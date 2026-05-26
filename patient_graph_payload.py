"""
Build vis-network JSON (nodes + relationships) from Neo4j subgraph rows.
Visualization constants mirror dashboard.py (same clinical styling).
"""
from __future__ import annotations

from typing import Any

from ai_compliance import run_compliance_check
from graph_edge_tooltips import edge_tooltip_plain
from graph_node_tooltips import format_node_tooltip_plain, protocol_catalog_node_ids

NODE_COLORS = {
    "Patient": "#3b82f6",
    "Doctor": "#10b981",
    "Disease": "#ef4444",
    "Hospital": "#8b5cf6",
    "Appointment": "#f59e0b",
    "Drug": "#eab308",
    "Procedure": "#06b6d4",
    "FollowUp": "#64748b",
    "ClinicalState": "#f97316",
    "SepsisGuideline": "#a855f7",
    "RecommendedAction": "#14b8a6",
    "LabCheck": "#0ea5e9",
    "Lab": "#0ea5e9",
    "Encounter": "#f97316",
    "PatientNote": "#14b8a6",
    "Violation": "#dc2626",
    "Symptom": "#f472b6",
}
VIOLATION_SEVERITY_COLORS = {
    "critical": "#dc2626",
    "warning": "#f59e0b",
    "normal": "#10b981",
}
DEFAULT_NODE_COLOR = "#94a3b8"
EDGE_COLOR_VIOLATION = "#dc2626"
EDGE_REL_RGBA = {
    "HAS_DISEASE": "rgba(239,68,68,0.58)",
    "HAS_SYMPTOM": "rgba(244,114,182,0.58)",
    "HAS_VIOLATION": "rgba(220,38,38,0.62)",
    "HAS_CLINICAL_STATE": "rgba(249,115,22,0.56)",
    "HAS_NOTE": "rgba(14,165,233,0.52)",
    "TREATS": "rgba(16,185,129,0.52)",
    "VISITS": "rgba(16,185,129,0.48)",
    "TREATED_WITH": "rgba(234,179,8,0.58)",
    "RECOMMENDED_DRUG": "rgba(234,179,8,0.56)",
    "RECOMMENDED_PROCEDURE": "rgba(6,182,212,0.54)",
    "HAD_PROCEDURE": "rgba(6,182,212,0.54)",
    "FOLLOW_UP": "rgba(100,116,139,0.52)",
    "HAS_APPOINTMENT": "rgba(245,158,11,0.52)",
    "AT_HOSPITAL": "rgba(139,92,246,0.48)",
    "PERFORMED_BY": "rgba(16,185,129,0.45)",
    "ORDERED_LAB": "rgba(14,165,233,0.48)",
    "PRESCRIBED": "rgba(234,179,8,0.50)",
    "INCLUDES_PROCEDURE": "rgba(6,182,212,0.48)",
    "FOLLOWED_UP_WITH": "rgba(100,116,139,0.52)",
}

NODE_SIZE_BY_TYPE = {
    "Patient": 30,
    "Doctor": 26,
    "Disease": 24,
    "Drug": 23,
    "Symptom": 22,
    "ClinicalState": 23,
    "Violation": 23,
    "Hospital": 22,
    "Appointment": 21,
    "Procedure": 22,
    "FollowUp": 21,
    "Encounter": 22,
    "Lab": 20,
    "PatientNote": 20,
}
NODE_BORDER_BY_TYPE = {
    "Patient": "#1e40af",
    "Doctor": "#047857",
    "Disease": "#991b1b",
    "Drug": "#a16207",
    "Symptom": "#be185d",
    "ClinicalState": "#c2410c",
    "Violation": "#7f1d1d",
    "Hospital": "#5b21b6",
    "Appointment": "#b45309",
    "Procedure": "#0e7490",
    "FollowUp": "#475569",
    "Encounter": "#c2410c",
    "Lab": "#0369a1",
    "PatientNote": "#0f766e",
}

TREATMENT_REL_TYPES = frozenset(
    {"HAS_DISEASE", "TREATED_WITH", "HAD_PROCEDURE", "RECOMMENDED_DRUG", "RECOMMENDED_PROCEDURE", "FOLLOW_UP"}
)


def _coerce_float(val: Any) -> float | None:
    if val is None or val == "":
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _collect_has_disease_names(rows: list[dict]) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for row in rows:
        if row.get("rel_type") != "HAS_DISEASE":
            continue
        tp = row.get("tgt_props") or {}
        sp = row.get("src_props") or {}
        if row.get("tgt_label") == "Disease":
            dn = str(tp.get("name") or tp.get("id") or "").strip()
        elif row.get("src_label") == "Disease":
            dn = str(sp.get("name") or sp.get("id") or "").strip()
        else:
            continue
        if dn and dn not in seen:
            seen.add(dn)
            names.append(dn)
    return names


def _short_display_label(name: str, node_type: str, *, patient_max: int = 26, other_max: int = 20) -> str:
    n = (name or "").strip()
    if not n:
        return ""
    lim = patient_max if node_type == "Patient" else other_max
    if len(n) <= lim:
        return n
    return n[: max(1, lim - 1)] + "…"


def _violation_edge_keys_for_patient(compliance_result: dict, patient_id: str) -> set[tuple[str, str, str]]:
    """Build (src_id_prop, tgt_id_prop, rel_type) keys for violation styling — same logic as dashboard._build_violation_set."""
    violation_edges: set[tuple[str, str, str]] = set()
    for r in compliance_result.get("patients_with_violations", []):
        pid, did = r.get("patient_id"), r.get("disease_id")
        if pid != patient_id or not pid:
            continue
        if did:
            violation_edges.add((pid, did, "HAS_DISEASE"))
        for aid in r.get("actual_drug_ids") or []:
            if aid != r.get("recommended_drug_id"):
                violation_edges.add((pid, aid, "TREATED_WITH"))
        for aid in r.get("actual_procedure_ids") or []:
            if aid != r.get("recommended_procedure_id"):
                violation_edges.add((pid, aid, "HAD_PROCEDURE"))
    return violation_edges


def rows_to_vis_payload(rows: list[dict], patient_id: str) -> dict[str, Any]:
    """
    rows: list of dicts with src_id, tgt_id, rel_type, src_label, tgt_label, src_props, tgt_props
    Returns { nodes: [...], relationships: [...] } for vis.js DataSet.
    """
    compliance = run_compliance_check()
    violation_keys = _violation_edge_keys_for_patient(compliance, patient_id)

    nodes_dict: dict[str, dict[str, Any]] = {}
    tooltip_props_by_nid: dict[str, dict[str, Any]] = {}
    edge_seen: set[tuple[str, str, str]] = set()
    relationships: list[dict[str, Any]] = []
    ei = 0

    def upsert_node(nid: str, label: str, props: dict[str, Any]) -> None:
        if nid in nodes_dict:
            return
        raw_name = str(props.get("name") or props.get("id") or nid)
        id_prop = props.get("id")
        tooltip_props_by_nid[nid] = dict(props)
        bg = NODE_COLORS.get(label, DEFAULT_NODE_COLOR)
        border = NODE_BORDER_BY_TYPE.get(label, "#475569")
        size = NODE_SIZE_BY_TYPE.get(label, 22)
        canvas_label = _short_display_label(raw_name, label)
        clinical_metrics: dict[str, Any] | None = None

        if label == "ClinicalState":
            p = props
            sofa = _coerce_float(p.get("sofa_score"))
            lac = _coerce_float(p.get("lactate"))
            mmap = _coerce_float(p.get("map"))
            gcs = _coerce_float(p.get("gcs"))
            creat = _coerce_float(p.get("creatinine"))
            clinical_metrics = {
                "sofa": sofa,
                "lactate": lac,
                "map": mmap,
                "gcs": gcs,
                "creatinine": creat,
            }
        elif label == "Violation":
            sev = (props.get("severity") or "warning").lower()
            bg = VIOLATION_SEVERITY_COLORS.get(sev, "#f59e0b")
            desc = props.get("description") or raw_name
            canvas_label = _short_display_label(desc, "Violation", other_max=16)

        # Match legacy pyvis dashboard: circular nodes (dot), soft shadow, label stroke (readable on color fills).
        node_entry: dict[str, Any] = {
            "id": nid,
            "shape": "dot",
            "label": canvas_label,
            "title": "",
            "id_prop": id_prop,
            "node_type": label,
            "full_label": raw_name,
            "short_label": canvas_label,
            "patient_id": patient_id if label == "Patient" and id_prop == patient_id else None,
            "size": size,
            "borderWidth": 2,
            "shadow": {
                "enabled": True,
                "size": 12,
                "x": 0,
                "y": 3,
                "color": "rgba(15,23,42,0.07)",
            },
            "color": {
                "background": bg,
                "border": border,
                "highlight": {"background": bg, "border": "#0f172a"},
                "hover": {"background": bg, "border": "#0f172a"},
            },
            "font": {
                "size": 17 if label == "Patient" else (15 if label in ("Doctor", "Disease") else 13),
                "face": "Inter, system-ui, sans-serif",
                "color": "#0f172a",
                "strokeWidth": 2,
                "strokeColor": "rgba(255,255,255,0.9)",
            },
        }
        if label == "Patient":
            node_entry["patient_age"] = props.get("age")
            node_entry["patient_sex"] = props.get("sex")
        if label == "Doctor":
            node_entry["doctor_specialty"] = props.get("specialty")
        if label == "Violation":
            node_entry["violation_reason"] = props.get("reason")
        if label == "PatientNote":
            node_entry["note_preview"] = str(props.get("text") or "")[:400]
        if clinical_metrics is not None:
            node_entry["clinical_metrics"] = clinical_metrics
        nodes_dict[nid] = node_entry

    for row in rows:
        sid = row.get("src_id")
        tid = row.get("tgt_id")
        rt = row.get("rel_type") or ""
        if not sid or not tid:
            continue
        dedup_key = (sid, tid, rt)
        if dedup_key in edge_seen:
            continue
        edge_seen.add(dedup_key)

        upsert_node(sid, row.get("src_label") or "Unknown", row.get("src_props") or {})
        upsert_node(tid, row.get("tgt_label") or "Unknown", row.get("tgt_props") or {})

        sn = str((nodes_dict.get(sid) or {}).get("full_label") or "")
        tn = str((nodes_dict.get(tid) or {}).get("full_label") or "")
        sl = (nodes_dict.get(sid) or {}).get("node_type")
        tl = (nodes_dict.get(tid) or {}).get("node_type")
        sip = nodes_dict.get(sid, {}).get("id_prop")
        tip = nodes_dict.get(tid, {}).get("id_prop")
        key_tuple = (sip, tip, rt) if sip is not None and tip is not None else None
        is_violation = bool(key_tuple and key_tuple in violation_keys)

        if is_violation:
            etitle = edge_tooltip_plain(rt, sn, tn, source_type=sl, target_type=tl, violation=True)
            edge_obj = {
                "id": "e_" + str(ei),
                "from": sid,
                "to": tid,
                "label": rt,
                "title": etitle,
                "rel_type": rt,
                "is_violation": True,
                "width": 2.6,
                "color": {"color": EDGE_COLOR_VIOLATION, "highlight": "#991b1b"},
                "arrows": "to",
            }
        else:
            rgba = EDGE_REL_RGBA.get(rt, "rgba(148,163,184,0.42)")
            width = 1.75 if rt in ("HAS_DISEASE", "HAS_SYMPTOM", "TREATS", "RECOMMENDED_DRUG") else 1.4
            etitle = edge_tooltip_plain(
                rt, sn, tn, source_type=sl, target_type=tl, compliant_pathway=(rt in TREATMENT_REL_TYPES)
            )
            edge_obj = {
                "id": "e_" + str(ei),
                "from": sid,
                "to": tid,
                "label": rt,
                "title": etitle,
                "rel_type": rt,
                "width": width,
                "color": {"color": rgba, "highlight": "#334155"},
                "arrows": "to",
            }
        ei += 1
        relationships.append(edge_obj)

    rec_drugs, rec_procs = protocol_catalog_node_ids(rows)
    disease_names = _collect_has_disease_names(rows)
    for nid, node in nodes_dict.items():
        nt = node.get("node_type") or "Unknown"
        props = tooltip_props_by_nid.get(nid, {})
        full_label = str(node.get("full_label") or "")
        rc = (nt == "Drug" and nid in rec_drugs) or (nt == "Procedure" and nid in rec_procs)
        pd_list = (
            disease_names
            if nt == "Patient" and str(node.get("id_prop")) == str(patient_id)
            else None
        )
        node["title"] = format_node_tooltip_plain(
            nt,
            full_label,
            props,
            patient_diseases=pd_list,
            recommended_catalog=rc,
        )

    return {"nodes": list(nodes_dict.values()), "relationships": relationships}
