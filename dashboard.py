"""
Interactive compliance dashboard: side menu, stats, protocol explanations, and filtering.
Calls run_compliance_check() dynamically; no hard-coded compliance data.
Generates compliance_dashboard.html with collapsible sidebar, enhanced tooltips, and filters.
Keep neo4j_ops, ai_compliance, api unchanged; this module only reads from them.
"""
import json
import re
import webbrowser
from pathlib import Path

from pyvis.network import Network

from neo4j_config import NEO4J_DATABASE
from neo4j_connect import get_driver
from ai_compliance import run_compliance_check
from neo4j_ops import get_patients_with_diseases
from sepsis_compliance import sync_violations_to_neo4j
from protocol_explanations import get_explanation, get_why_recommended_better, PROTOCOL_EXPLANATIONS

OUTPUT_HTML = Path(__file__).resolve().parent / "compliance_dashboard.html"
NODE_LIMIT = 400
REL_LIMIT = 400

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
EDGE_COLOR_COMPLIANT = "#10b981"
EDGE_COLOR_DEFAULT = "#94a3b8"
TREATMENT_REL_TYPES = {"HAS_DISEASE", "TREATED_WITH", "HAD_PROCEDURE", "RECOMMENDED_DRUG", "RECOMMENDED_PROCEDURE", "FOLLOW_UP"}

# Visualization-only: node sizing / borders for clinical readability (does not affect Neo4j).
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
    "SepsisGuideline": 21,
    "RecommendedAction": 21,
    "LabCheck": 21,
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
    "SepsisGuideline": "#6b21a8",
    "RecommendedAction": "#0f766e",
    "LabCheck": "#0369a1",
}

# Semi-transparent edge colors by relationship type (direction arrows remain visible).
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
}


def _short_display_label(name: str, node_type: str, *, patient_max: int = 26, other_max: int = 20) -> str:
    """Truncate long labels on the canvas; full text remains in tooltip (title)."""
    n = (name or "").strip()
    if not n:
        return ""
    lim = patient_max if node_type == "Patient" else other_max
    if len(n) <= lim:
        return n
    return n[: max(1, lim - 1)] + "…"

NODE_TYPES_LIST = ["Patient", "Doctor", "Disease", "Hospital", "Appointment", "Drug", "Procedure", "FollowUp", "ClinicalState", "Violation", "Symptom"]
EDGE_TYPES_LIST = [
    "HAS_DISEASE", "TREATS", "VISITS", "HAS_APPOINTMENT", "AT_HOSPITAL",
    "RECOMMENDED_DRUG", "RECOMMENDED_PROCEDURE", "FOLLOW_UP", "TREATED_WITH", "HAD_PROCEDURE",
    "HAS_CLINICAL_STATE", "HAS_VIOLATION", "HAS_SYMPTOM",
]


def _run_query_driver(driver, cypher: str, params: dict | None = None):
    params = params or {}
    with driver.session(database=NEO4J_DATABASE) as session:
        result = session.run(cypher, params)
        return [dict(record) for record in result]


def _get_graph_rows(driver, limit: int):
    params = {"limit": limit}
    cypher_v5 = """
    MATCH (a)-[r]->(b)
    RETURN elementId(a) AS src_id, coalesce(labels(a)[0], 'Unknown') AS src_label, properties(a) AS src_props,
           type(r) AS rel_type,
           elementId(b) AS tgt_id, coalesce(labels(b)[0], 'Unknown') AS tgt_label, properties(b) AS tgt_props
    LIMIT $limit
    """
    cypher_v4 = """
    MATCH (a)-[r]->(b)
    RETURN toString(id(a)) AS src_id, coalesce(labels(a)[0], 'Unknown') AS src_label, properties(a) AS src_props,
           type(r) AS rel_type,
           toString(id(b)) AS tgt_id, coalesce(labels(b)[0], 'Unknown') AS tgt_label, properties(b) AS tgt_props
    LIMIT $limit
    """
    try:
        return _run_query_driver(driver, cypher_v5, params)
    except Exception:
        return _run_query_driver(driver, cypher_v4, params)


def _build_violation_set(compliance_result):
    violation_edges = set()
    violation_tooltips = {}
    why_better = get_why_recommended_better()
    for r in compliance_result.get("patients_with_violations", []):
        pid, did = r.get("patient_id"), r.get("disease_id")
        if not pid:
            continue
        violation_edges.add((pid, did, "HAS_DISEASE"))
        violation_tooltips[(pid, did, "HAS_DISEASE")] = (
            "Recommended: drug {}; procedure {} | Actual: {}; {} | {}"
            .format(
                r.get("recommended_drug_name") or "—", r.get("recommended_procedure_name") or "—",
                r.get("actual_drug_names") or "—", r.get("actual_procedure_names") or "—",
                why_better,
            )
        )
        for aid in r.get("actual_drug_ids") or []:
            if aid != r.get("recommended_drug_id"):
                violation_edges.add((pid, aid, "TREATED_WITH"))
                violation_tooltips[(pid, aid, "TREATED_WITH")] = (
                    "Recommended drug: {} | Actual: {} | {}"
                    .format(r.get("recommended_drug_name") or "—", r.get("actual_drug_names") or "—", why_better)
                )
        for aid in r.get("actual_procedure_ids") or []:
            if aid != r.get("recommended_procedure_id"):
                violation_edges.add((pid, aid, "HAD_PROCEDURE"))
                violation_tooltips[(pid, aid, "HAD_PROCEDURE")] = (
                    "Recommended procedure: {} | Actual: {} | {}"
                    .format(r.get("recommended_procedure_name") or "—", r.get("actual_procedure_names") or "—", why_better)
                )
    return violation_edges, violation_tooltips


def _patient_diseases_map():
    """patient_id -> list of disease names for tooltips."""
    rows = get_patients_with_diseases()
    out = {}
    for r in rows:
        pid, pname = r.get("patient_id"), r.get("patient_name")
        dname = r.get("disease_name")
        if not pid:
            continue
        if pid not in out:
            out[pid] = []
        if dname and dname not in out[pid]:
            out[pid].append(dname)
    return out


# Sepsis bundle node ids to exclude from the dashboard (show patients only, not the guideline graph)
_SEPSIS_BUNDLE_IDS = frozenset({
    "SEPSIS_1",
    "ACT_LACTATE", "ACT_ABX", "ACT_CULTURES", "ACT_FLUIDS", "ACT_VASO",
    "LAB_LACTATE", "LAB_CREAT",
    "DRUG_ABX", "DRUG_VASO",
    "PROC_CULTURES", "PROC_FLUIDS",
    "FU_REASSESS",
})


def _is_sepsis_bundle_node(label: str, props: dict) -> bool:
    """True if this node is part of the sepsis guideline bundle (exclude from graph)."""
    if label == "SepsisGuideline":
        return True
    if label == "RecommendedAction":
        return True
    if label == "LabCheck":
        return True
    if label == "Drug" and (props or {}).get("id") in _SEPSIS_BUNDLE_IDS:
        return True
    if label == "Procedure" and (props or {}).get("id") in _SEPSIS_BUNDLE_IDS:
        return True
    if label == "FollowUp" and (props or {}).get("id") in _SEPSIS_BUNDLE_IDS:
        return True
    return False


def build_dashboard_graph():
    """Build pyvis network with enhanced tooltips and node metadata (id_prop, node_type) for dashboard."""
    # Sync sepsis violations to Neo4j so Violation nodes appear in the graph when user asks "what patients have violations"
    try:
        sync_violations_to_neo4j()
    except Exception:
        pass
    driver = get_driver()
    try:
        rows = _get_graph_rows(driver, REL_LIMIT)
    finally:
        driver.close()
    if not rows:
        return None, None, None

    # Exclude sepsis bundle (guideline + actions/labs/drugs/procedures) so only patients and their data are shown
    rows = [
        r for r in rows
        if not _is_sepsis_bundle_node(r.get("src_label") or "", r.get("src_props") or {})
        and not _is_sepsis_bundle_node(r.get("tgt_label") or "", r.get("tgt_props") or {})
    ]

    compliance = run_compliance_check()
    violation_edges, violation_tooltips = _build_violation_set(compliance)
    doctor_scores = {x["doctor_id"]: x for x in compliance.get("doctor_compliance_scores", [])}
    patient_diseases = _patient_diseases_map()

    nodes_dict = {}
    for r in rows:
        for nid, nlabel, props in [
            (r.get("src_id"), r.get("src_label"), r.get("src_props") or {}),
            (r.get("tgt_id"), r.get("tgt_label"), r.get("tgt_props") or {}),
        ]:
            if nid and nid not in nodes_dict:
                props = props or {}
                id_prop = props.get("id")
                name = (props.get("name") or id_prop or str(nid))
                nodes_dict[nid] = {
                    "label": nlabel or "Unknown",
                    "name": str(name),
                    "id_prop": id_prop,
                    "props": props,
                    "patient_diseases": patient_diseases.get(id_prop, []) if nlabel == "Patient" else None,
                    "doctor_score": doctor_scores.get(id_prop) if nlabel == "Doctor" else None,
                }
    node_ids = list(nodes_dict.keys())
    if len(node_ids) > NODE_LIMIT:
        keep = set(node_ids[:NODE_LIMIT])
        nodes_dict = {k: v for k, v in nodes_dict.items() if k in keep}

    net = Network(height="100%", width="100%", bgcolor="#ffffff", font_color="#334155", directed=True)
    net.set_options("""{
      "nodes": {
        "font": {
          "size": 14,
          "face": "Inter, system-ui, sans-serif",
          "color": "#1e293b",
          "strokeWidth": 2,
          "strokeColor": "rgba(255,255,255,0.92)"
        },
        "borderWidth": 2,
        "scaling": { "label": { "enabled": true, "min": 11, "max": 20 } },
        "shadow": { "enabled": true, "size": 12, "x": 0, "y": 3, "color": "rgba(15,23,42,0.07)" }
      },
      "edges": {
        "font": { "size": 9, "face": "Inter, system-ui, sans-serif", "color": "#64748b", "strokeWidth": 0, "align": "middle" },
        "width": 1.35,
        "selectionWidth": 2,
        "arrows": { "to": { "enabled": true, "scaleFactor": 0.78 } },
        "smooth": { "type": "cubicBezier", "forceDirection": "none", "roundness": 0.52 }
      },
      "physics": {
        "enabled": true,
        "solver": "forceAtlas2Based",
        "forceAtlas2Based": {
          "theta": 0.55,
          "gravitationalConstant": -92,
          "centralGravity": 0.011,
          "springLength": 268,
          "springConstant": 0.058,
          "damping": 0.52,
          "avoidOverlap": 0.82
        },
        "maxVelocity": 42,
        "minVelocity": 2,
        "timestep": 0.52,
        "stabilization": { "enabled": true, "iterations": 280, "updateInterval": 25 }
      },
      "layout": { "improvedLayout": true },
      "interaction": {
        "dragNodes": true,
        "zoomView": true,
        "dragView": true,
        "hover": true,
        "tooltipDelay": 160,
        "hideEdgesOnDrag": false,
        "hideEdgesOnZoom": false
      }
    }""")

    def node_color(label):
        return NODE_COLORS.get(label, DEFAULT_NODE_COLOR)

    for nid, data in nodes_dict.items():
        label = data["label"]
        raw_name = str(data["name"])
        id_prop = data.get("id_prop") or ""
        node_color_override = None
        if label == "Patient":
            age = data["props"].get("age")
            sex = data["props"].get("sex")
            diseases = data.get("patient_diseases") or []
            title = f"<b>Patient: {raw_name}</b><br>Age: {age or '—'} | Sex: {sex or '—'}<br>Diseases: {', '.join(diseases) or '—'}"
            full_label = raw_name
        elif label == "Doctor":
            spec = data["props"].get("specialty")
            score_data = data.get("doctor_score")
            score = f"{score_data['compliance_score']}%" if score_data else "—"
            title = f"<b>Doctor: {raw_name}</b><br>Specialty: {spec or '—'}<br>Compliance score: {score}"
            full_label = raw_name
        elif label == "ClinicalState":
            p = data["props"]
            title = (f"<b>Clinical state: {raw_name}</b><br>SOFA: {p.get('sofa_score') or '—'} | "
                     f"Lactate: {p.get('lactate') or '—'} mmol/L | MAP: {p.get('map') or '—'} mmHg<br>"
                     f"GCS: {p.get('gcs') or '—'} | Creatinine: {p.get('creatinine') or '—'} mg/dL<br>"
                     f"Antibiotics: {p.get('antibiotics_active')} | Cultures: {p.get('cultures_ordered')} | Vasopressors: {p.get('vasopressors_active')}")
            full_label = raw_name
        elif label == "SepsisGuideline":
            p = data["props"]
            title = f"<b>Sepsis guideline: {raw_name}</b><br>{p.get('description') or '—'}<br>SOFA≥{p.get('sofa_threshold_high')} | Lactate>{p.get('lactate_threshold_mmol')} | MAP<{p.get('map_threshold_mmhg')}"
            full_label = raw_name
        elif label == "Violation":
            props = data["props"] or {}
            desc = props.get("description") or raw_name
            severity = (props.get("severity") or "warning").lower()
            reason = props.get("reason") or ""
            sev_label = severity.upper()
            title = (f"<b>Violation — <span style='color:{VIOLATION_SEVERITY_COLORS.get(severity, '#f59e0b')}'>"
                     f"{sev_label}</span></b><br>{desc}")
            if reason:
                title += f"<br><br><b>Reason:</b> {reason}"
            node_color_override = VIOLATION_SEVERITY_COLORS.get(severity, "#f59e0b")
            full_label = desc
        else:
            title = f"<b>{label}: {raw_name}</b>"
            if data["props"].get("icd10"):
                title += f"<br>ICD-10: {data['props']['icd10']}"
            full_label = raw_name

        bg = node_color_override if label == "Violation" and node_color_override else node_color(label)
        border = NODE_BORDER_BY_TYPE.get(label, "#475569")
        size = NODE_SIZE_BY_TYPE.get(label, 22)
        if label == "Violation":
            canvas_label = _short_display_label(full_label, "Violation", other_max=16)
        else:
            canvas_label = _short_display_label(raw_name, label)
        font_size = 17 if label == "Patient" else (15 if label in ("Doctor", "Disease") else 13)
        net.add_node(
            nid,
            label=canvas_label,
            title=title,
            id_prop=id_prop,
            node_type=label,
            full_label=full_label,
            short_label=canvas_label,
            size=size,
            borderWidth=2,
            color={
                "background": bg,
                "border": border,
                "highlight": {"background": bg, "border": "#0f172a"},
                "hover": {"background": bg, "border": "#0f172a"},
            },
            font={
                "size": font_size,
                "face": "Inter, system-ui, sans-serif",
                "color": "#0f172a",
                "strokeWidth": 2,
                "strokeColor": "rgba(255,255,255,0.9)",
            },
        )

    keep_ids = set(nodes_dict.keys())
    for r in rows:
        src, tgt = r.get("src_id"), r.get("tgt_id")
        if not src or not tgt or src not in keep_ids or tgt not in keep_ids:
            continue
        rel_type = r.get("rel_type") or ""
        src_id_prop = (nodes_dict.get(src) or {}).get("id_prop")
        tgt_id_prop = (nodes_dict.get(tgt) or {}).get("id_prop")
        key = (src_id_prop, tgt_id_prop, rel_type)
        is_violation = key in violation_edges
        sn = str((nodes_dict.get(src) or {}).get("name") or "")
        tn = str((nodes_dict.get(tgt) or {}).get("name") or "")
        if is_violation:
            vt = violation_tooltips.get(key, rel_type + " (VIOLATION)")
            title = f"<b>{rel_type}</b> — violation<br><b>{sn}</b> → <b>{tn}</b><br>{vt}"
            net.add_edge(
                src,
                tgt,
                label=rel_type,
                title=title,
                rel_type=rel_type,
                width=2.6,
                color={"color": EDGE_COLOR_VIOLATION, "highlight": "#991b1b"},
            )
            continue
        rgba = EDGE_REL_RGBA.get(rel_type, "rgba(148,163,184,0.42)")
        width = 1.75 if rel_type in ("HAS_DISEASE", "HAS_SYMPTOM", "TREATS", "RECOMMENDED_DRUG") else 1.4
        title = f"<b>{rel_type}</b><br>{sn} → {tn}"
        if rel_type in TREATMENT_REL_TYPES:
            title += "<br><small style='color:#059669'>Compliant pathway</small>"
        net.add_edge(
            src,
            tgt,
            label=rel_type,
            title=title,
            rel_type=rel_type,
            width=width,
            color={"color": rgba, "highlight": "#334155"},
        )

    # Stats and violations list for sidebar
    patients_with_diseases = get_patients_with_diseases()
    unique_patients = len(set(x.get("patient_id") for x in patients_with_diseases if x.get("patient_id")))
    violations_list = []
    for r in compliance.get("patients_with_violations", []):
        structured = r.get("violations_structured") or []
        plain = r.get("violations") or []
        items = []
        for i, vtext in enumerate(plain):
            sv = structured[i] if i < len(structured) else {}
            items.append({
                "text": vtext,
                "severity": sv.get("severity", "warning"),
                "reason": sv.get("reason", ""),
            })
        violations_list.append({
            "patient_id": r.get("patient_id"),
            "patient_name": r.get("patient_name"),
            "disease_id": r.get("disease_id"),
            "disease_name": r.get("disease_name"),
            "violations": plain,
            "violations_detail": items,
        })
    stats = {
        "total_patients": unique_patients,
        "total_violations": len(compliance.get("patients_with_violations", [])),
        "doctor_compliance_scores": compliance.get("doctor_compliance_scores", []),
        "violations_list": violations_list,
    }
    explanations = {k: get_explanation(k) for k in PROTOCOL_EXPLANATIONS}

    return net, stats, explanations


def _sidebar_and_script(stats: dict, explanations: dict) -> str:
    """HTML for collapsible sidebar (legend, stats) + script for filters, click-to-explain, and stats injection."""
    stats_json = json.dumps(stats, default=str)
    expl_json = json.dumps(explanations, default=str)
    return """
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: 'Inter', system-ui, -apple-system, sans-serif !important; background: #f1f5f9 !important;
    color: #0f172a; display: flex !important; flex-direction: column !important; height: 100vh !important; overflow: hidden !important; }
  #loadingBar { display: none !important; opacity: 0 !important; visibility: hidden !important; pointer-events: none !important; }

  /* ===== HEADER ===== */
  .app-header { background: #ffffff; border-bottom: 1px solid #e2e8f0; padding: 0 1.5rem; height: 64px;
    display: flex; align-items: center; box-shadow: 0 1px 3px rgba(0,0,0,0.04); z-index: 20; flex-shrink: 0; }
  .app-header-inner { display: flex; align-items: center; width: 100%; gap: 1rem; }
  .app-brand { display: flex; align-items: center; gap: 0.625rem; flex-shrink: 0; }
  .app-brand svg { width: 26px; height: 26px; color: #3b82f6; }
  .app-brand h1 { font-size: 1.05rem; font-weight: 700; color: #0f172a; white-space: nowrap; letter-spacing: -0.02em; }

  /* ===== AI SEARCH BAR ===== */
  .ai-search-wrapper { flex: 1; max-width: 900px; }
  .ai-search-box { display: flex; align-items: center; background: #f8fafc; border: 1.5px solid #e2e8f0;
    border-radius: 12px; padding: 0.25rem 0.25rem 0.25rem 0.75rem; transition: all 0.2s ease; }
  .ai-search-box:focus-within { border-color: #3b82f6; box-shadow: 0 0 0 3px rgba(59,130,246,0.1); background: #fff; }
  .ai-search-box .search-icon { width: 18px; height: 18px; color: #94a3b8; flex-shrink: 0; margin-right: 0.5rem; }
  .ai-search-box textarea { flex: 1; border: none; background: transparent; font-size: 0.875rem; color: #0f172a;
    outline: none; resize: none; font-family: inherit; line-height: 1.5; padding: 0.375rem 0; min-height: 22px; max-height: 60px; }
  .ai-search-box textarea::placeholder { color: #94a3b8; }
  .ai-search-box button { padding: 0.5rem 1rem; background: #3b82f6; color: #fff; border: none; border-radius: 8px;
    font-size: 0.8125rem; font-weight: 600; cursor: pointer; transition: all 0.15s ease; white-space: nowrap; font-family: inherit; }
  .ai-search-box button:hover { background: #2563eb; transform: translateY(-1px); box-shadow: 0 2px 4px rgba(37,99,235,0.3); }
  .ai-search-box button:disabled { opacity: 0.5; cursor: not-allowed; transform: none; box-shadow: none; }
  .ai-loading { font-size: 0.8125rem; color: #64748b; padding: 0.5rem 0 0; display: flex; align-items: center; gap: 0.5rem; }
  .loading-spinner { display: inline-block; width: 14px; height: 14px; border: 2px solid #e2e8f0;
    border-top-color: #3b82f6; border-radius: 50%; animation: spin 0.6s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }

  /* ===== AI RESULT BANNER ===== */
  .ai-result-banner { background: #ffffff; border-bottom: 1px solid #e2e8f0; padding: 0.75rem 1.5rem;
    flex-shrink: 0; animation: slideDown 0.3s ease; max-height: min(42vh, 380px); overflow-y: auto; }
  @keyframes slideDown { from { opacity: 0; transform: translateY(-8px); } to { opacity: 1; transform: translateY(0); } }
  .ai-result-inner { max-width: 900px; display: flex; flex-wrap: wrap; align-items: flex-start; gap: 0.75rem; }
  .ai-result-inner .answer { flex: 1; min-width: 200px; font-size: 0.8125rem; color: #334155; line-height: 1.6; }
  .ai-result-inner .violation-badge { display: inline-flex; align-items: center; padding: 0.25rem 0.75rem;
    border-radius: 100px; font-size: 0.6875rem; font-weight: 600; letter-spacing: 0.025em; text-transform: uppercase; flex-shrink: 0; }
  .ai-result-inner .violation-badge.yes { background: #fef2f2; color: #dc2626; border: 1px solid #fecaca; }
  .ai-result-inner .violation-badge.no { background: #f0fdf4; color: #16a34a; border: 1px solid #bbf7d0; }
  .ai-result-inner .meta { width: 100%; font-size: 0.75rem; color: #64748b; }
  .ai-result-inner .error { color: #dc2626; }
  .ai-result-inner button { padding: 0.375rem 0.875rem; background: #10b981; color: #fff; border: none; border-radius: 8px;
    font-size: 0.75rem; font-weight: 600; cursor: pointer; transition: all 0.15s ease; font-family: inherit; }
  .ai-result-inner button:hover { background: #059669; }
  .ai-result-inner .ai-new-question-btn { background: #3b82f6; margin-left: 0.5rem; }
  .ai-result-inner .ai-new-question-btn:hover { background: #2563eb; }
  .ai-result-actions { display: flex; gap: 0.5rem; align-items: center; width: 100%; margin-top: 0.25rem; }
  .ai-structured { width: 100%; display: flex; flex-direction: column; gap: 0.5rem; }
  .ai-clinical-response .ai-response-card {
    background: #ffffff; border: 1px solid #e2e8f0; border-radius: 10px; padding: 0.625rem 0.75rem;
    box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04); }
  .ai-clinical-response .ai-response-card > .ai-section-label { margin-bottom: 0.35rem; }
  .ai-clinical-response .ai-conclusion { margin-bottom: 0; }
  .ai-insufficient-card { background: linear-gradient(180deg, #fffbeb 0%, #fff 100%); border-color: #fde68a; }
  .ai-insufficient-title { font-size: 0.8125rem; font-weight: 700; color: #92400e; line-height: 1.45; margin: 0 0 0.35rem; }
  .ai-insufficient-detail { font-size: 0.6875rem; color: #b45309; line-height: 1.5; margin: 0; opacity: 0.95; }
  .ai-confidence { display: inline-flex; align-items: center; gap: 0.3rem; padding: 0.15rem 0.6rem;
    border-radius: 100px; font-size: 0.625rem; font-weight: 700; letter-spacing: 0.03em;
    text-transform: uppercase; flex-shrink: 0; vertical-align: middle; margin-left: 0.5rem; }
  .ai-confidence.high { background: #f0fdf4; color: #16a34a; border: 1px solid #bbf7d0; }
  .ai-confidence.medium { background: #fffbeb; color: #b45309; border: 1px solid #fde68a; }
  .ai-confidence.low { background: #fef2f2; color: #dc2626; border: 1px solid #fecaca; }
  .ai-confidence .conf-dot { width: 6px; height: 6px; border-radius: 50%; }
  .ai-confidence.high .conf-dot { background: #16a34a; }
  .ai-confidence.medium .conf-dot { background: #f59e0b; }
  .ai-confidence.low .conf-dot { background: #dc2626; }
  .ai-section { margin-bottom: 0; }
  .ai-section-label { font-size: 0.625rem; font-weight: 700; color: #64748b; text-transform: uppercase;
    letter-spacing: 0.05em; margin: 0 0 0.2rem; display: flex; align-items: center; gap: 0.375rem; flex-wrap: wrap; }
  .ai-section-label .section-icon { font-size: 0.75rem; }
  .ai-conclusion { font-size: 0.8125rem; font-weight: 600; color: #0f172a; line-height: 1.55;
    padding: 0.5rem 0.625rem; background: #f0f9ff; border-left: 3px solid #3b82f6;
    border-radius: 0 8px 8px 0; margin-bottom: 0.5rem; }
  .ai-evidence { padding: 0; margin: 0; list-style: none; }
  .ai-evidence li { font-size: 0.75rem; color: #334155; line-height: 1.5; padding: 0.2rem 0 0.2rem 1rem;
    position: relative; }
  .ai-evidence li::before { content: ''; position: absolute; left: 0.25rem; top: 0.55rem;
    width: 5px; height: 5px; border-radius: 50%; background: #3b82f6; }
  .ai-explanation { font-size: 0.75rem; color: #475569; line-height: 1.55; white-space: pre-wrap; }
  .ai-insufficient { padding: 0.625rem 0.75rem; background: #fffbeb; border: 1px solid #fde68a;
    border-radius: 8px; font-size: 0.8125rem; color: #92400e; line-height: 1.5; text-align: left; }
  .ai-comparison-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 0.5rem; }
  @media (max-width: 560px) {
    .ai-comparison-grid { grid-template-columns: 1fr; }
  }
  .ai-comparison-col { padding: 0.5rem 0.625rem; border-radius: 8px; font-size: 0.75rem; line-height: 1.5; }
  .ai-comparison-col.common { background: #f0fdf4; border: 1px solid #bbf7d0; }
  .ai-comparison-col.diff { background: #fef2f2; border: 1px solid #fecaca; }
  .ai-comparison-col h6 { font-size: 0.625rem; font-weight: 700; color: #64748b; text-transform: uppercase;
    letter-spacing: 0.04em; margin: 0 0 0.25rem; }
  .ai-placeholder { font-size: 0.6875rem; color: #94a3b8; font-style: italic; line-height: 1.45; display: block; }
  .hl-toast { position: absolute; top: 12px; left: 50%; transform: translateX(-50%); z-index: 999;
    background: #1e40af; color: #fff; padding: 8px 20px; border-radius: 8px; font-size: 13px; font-weight: 600;
    box-shadow: 0 4px 16px rgba(30,64,175,0.3); pointer-events: none; transition: opacity 0.5s; }

  /* ===== PATIENT SELECTOR OVERLAY ===== */
  .patient-selector-overlay { position: fixed; top: 0; left: 0; right: 0; bottom: 0; z-index: 2000;
    background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%); display: flex; align-items: center;
    justify-content: center; animation: fadeIn 0.3s ease; }
  .patient-selector-card { background: #fff; border-radius: 20px; width: 440px; max-width: 92vw;
    box-shadow: 0 24px 80px rgba(0,0,0,0.35); animation: scaleIn 0.3s ease; overflow: hidden; }
  .ps-header { padding: 2rem 2rem 0; text-align: center; }
  .ps-header svg { width: 40px; height: 40px; color: #3b82f6; margin-bottom: 0.75rem; }
  .ps-header h2 { font-size: 1.25rem; font-weight: 700; color: #0f172a; margin: 0 0 0.25rem; }
  .ps-header p { font-size: 0.8125rem; color: #64748b; margin: 0; }
  .ps-body { padding: 1.5rem 2rem 2rem; }
  .ps-search { width: 100%; padding: 0.625rem 0.875rem; border: 1.5px solid #e2e8f0; border-radius: 10px;
    font-size: 0.875rem; color: #0f172a; font-family: inherit; outline: none; margin-bottom: 0.75rem;
    transition: border-color 0.15s; }
  .ps-search:focus { border-color: #3b82f6; box-shadow: 0 0 0 3px rgba(59,130,246,0.1); }
  .ps-list { max-height: 240px; overflow-y: auto; display: flex; flex-direction: column; gap: 0.25rem; }
  .ps-group-label { font-size: 0.625rem; font-weight: 700; color: #94a3b8; text-transform: uppercase;
    letter-spacing: 0.04em; padding: 0.45rem 0.5rem 0.15rem; }
  .compare-patient-list .ps-group-label { padding-left: 0.25rem; }
  .ps-item { display: flex; align-items: center; gap: 0.75rem; padding: 0.625rem 0.75rem; border-radius: 10px;
    cursor: pointer; transition: all 0.12s; border: 1.5px solid transparent; }
  .ps-item:hover { background: #eff6ff; border-color: #bfdbfe; }
  .ps-item .ps-dot { width: 10px; height: 10px; border-radius: 50%; background: #3b82f6; flex-shrink: 0; }
  .ps-item.mimic-sample .ps-dot { background: #a855f7; }
  .ps-item .ps-name { font-weight: 600; color: #0f172a; font-size: 0.875rem; }
  .ps-item .ps-id { color: #94a3b8; font-size: 0.75rem; margin-left: auto; }
  .ps-footer { padding: 0 2rem 1.5rem; display: flex; justify-content: center; }
  .ps-skip { background: none; border: none; color: #94a3b8; font-size: 0.8125rem; cursor: pointer;
    font-family: inherit; text-decoration: underline; transition: color 0.15s; }
  .ps-skip:hover { color: #3b82f6; }

  /* ===== VIEWING BAR ===== */
  .viewing-bar { background: #eff6ff; border-bottom: 1px solid #bfdbfe; padding: 0.375rem 1.5rem;
    display: flex; align-items: center; gap: 0.75rem; font-size: 0.8125rem; flex-shrink: 0; }
  .viewing-bar:empty, .viewing-bar.hidden { display: none; }
  .viewing-label { color: #1e40af; font-weight: 600; }
  .viewing-patient { color: #3b82f6; font-weight: 700; }
  .viewing-change { background: none; border: none; color: #64748b; font-size: 0.75rem; cursor: pointer;
    text-decoration: underline; font-family: inherit; margin-left: auto; }
  .viewing-change:hover { color: #3b82f6; }

  /* ===== EMPTY STATE ===== */
  .empty-state { display: flex; flex-direction: column; align-items: center; justify-content: center;
    flex: 1; color: #94a3b8; text-align: center; padding: 2rem; }
  .empty-state svg { width: 48px; height: 48px; color: #cbd5e1; margin-bottom: 1rem; }
  .empty-state p { font-size: 0.9375rem; font-weight: 500; margin: 0; }
  .empty-state .empty-hint { font-size: 0.8125rem; margin-top: 0.375rem; color: #cbd5e1; }

  /* ===== PATIENT SUMMARY CARD ===== */
  .patient-summary-card { display: none; }
  .patient-summary-card.active { display: block; }
  .psc-name { font-size: 0.9375rem; font-weight: 700; color: #0f172a; margin: 0 0 0.125rem; display: flex; align-items: center; gap: 0.5rem; }
  .psc-name .psc-dot { width: 8px; height: 8px; border-radius: 50%; background: #3b82f6; flex-shrink: 0; }
  .psc-meta { font-size: 0.6875rem; color: #94a3b8; margin: 0 0 0.625rem; }
  .psc-section { margin-bottom: 0.5rem; }
  .psc-section:last-child { margin-bottom: 0; }
  .psc-section-label { font-size: 0.625rem; font-weight: 700; color: #64748b; text-transform: uppercase;
    letter-spacing: 0.05em; margin: 0 0 0.3rem; }
  .psc-tags { display: flex; flex-wrap: wrap; gap: 0.25rem; }
  .psc-tag { display: inline-flex; padding: 0.15rem 0.5rem; border-radius: 100px; font-size: 0.6875rem; font-weight: 500; }
  .psc-tag.disease { background: #fef2f2; color: #dc2626; border: 1px solid #fecaca; }
  .psc-tag.symptom { background: #eff6ff; color: #1e40af; border: 1px solid #bfdbfe; }
  .psc-tag.violation-critical { background: #fef2f2; color: #dc2626; border: 1px solid #fecaca; font-weight: 600; }
  .psc-tag.violation-warning { background: #fffbeb; color: #92400e; border: 1px solid #fde68a; }
  .psc-tag.violation-normal { background: #f0fdf4; color: #16a34a; border: 1px solid #bbf7d0; }
  .psc-tag.drug { background: #faf5ff; color: #7c3aed; border: 1px solid #ddd6fe; }
  .psc-none { font-size: 0.75rem; color: #cbd5e1; font-style: italic; }
  .psc-clinical-summary { font-size: 0.8125rem; color: #334155; line-height: 1.55; margin: 0 0 0.625rem;
    padding: 0.5rem 0.625rem; background: linear-gradient(135deg, #f0f9ff 0%, #faf5ff 100%);
    border-left: 3px solid #3b82f6; border-radius: 0 8px 8px 0; font-style: italic; }

  /* ===== INSIGHT PANEL ===== */
  .insight-panel { display: none; }
  .insight-panel.active { display: block; }
  .insight-list { display: flex; flex-direction: column; gap: 0.375rem; }
  .insight-item { display: flex; align-items: flex-start; gap: 0.5rem; padding: 0.5rem 0.625rem;
    background: #fffbeb; border: 1px solid #fef3c7; border-radius: 8px; font-size: 0.75rem; line-height: 1.45; color: #92400e; }
  .insight-item.critical { background: #fef2f2; border-color: #fecaca; color: #991b1b; }
  .insight-item.info { background: #eff6ff; border-color: #bfdbfe; color: #1e40af; }
  .insight-item.good { background: #f0fdf4; border-color: #bbf7d0; color: #166534; }
  .insight-icon { flex-shrink: 0; font-size: 0.8125rem; line-height: 1; margin-top: 1px; }
  .insight-text { flex: 1; }
  .insight-text strong { font-weight: 600; }
  .insight-why-toggle { display: inline-block; font-size: 0.625rem; font-weight: 600; color: inherit; opacity: 0.65;
    cursor: pointer; margin-left: 0.375rem; padding: 0.05rem 0.375rem; border-radius: 4px; background: rgba(0,0,0,0.06);
    transition: opacity 0.15s; vertical-align: middle; user-select: none; }
  .insight-why-toggle:hover { opacity: 1; }
  .insight-reasons { display: none; margin-top: 0.375rem; padding: 0.375rem 0.5rem; background: rgba(0,0,0,0.04);
    border-radius: 6px; font-size: 0.6875rem; line-height: 1.5; }
  .insight-reasons.open { display: block; }
  .insight-reasons ul { margin: 0; padding-left: 1rem; list-style: disc; }
  .insight-reasons li { margin: 0.1rem 0; }
  .insight-reasons li .reason-val { font-weight: 600; }

  /* ===== AI BASED-ON SECTION ===== */
  .ai-based-on { margin-top: 0.5rem; width: 100%; }
  .ai-based-on-toggle { display: inline-flex; align-items: center; gap: 0.3rem; font-size: 0.6875rem; font-weight: 600;
    color: #64748b; cursor: pointer; padding: 0.2rem 0.5rem; border-radius: 6px; background: #f1f5f9;
    border: 1px solid #e2e8f0; transition: all 0.15s; user-select: none; }
  .ai-based-on-toggle:hover { background: #e2e8f0; color: #334155; }
  .ai-based-on-toggle .toggle-arrow { font-size: 0.5rem; transition: transform 0.2s; }
  .ai-based-on-toggle.open .toggle-arrow { transform: rotate(90deg); }
  .ai-based-on-content { display: none; margin-top: 0.375rem; padding: 0.5rem 0.625rem; background: #f8fafc;
    border: 1px solid #e2e8f0; border-radius: 8px; font-size: 0.6875rem; line-height: 1.5; color: #475569; }
  .ai-based-on-content.open { display: block; }
  .ai-based-on-content .abo-section { margin-bottom: 0.375rem; }
  .ai-based-on-content .abo-section:last-child { margin-bottom: 0; }
  .ai-based-on-content .abo-label { font-weight: 700; font-size: 0.625rem; color: #64748b; text-transform: uppercase;
    letter-spacing: 0.04em; margin-bottom: 0.125rem; }
  .ai-based-on-content .abo-tags { display: flex; flex-wrap: wrap; gap: 0.25rem; }
  .ai-based-on-content .abo-tag { display: inline-flex; padding: 0.1rem 0.4rem; border-radius: 100px;
    font-size: 0.625rem; font-weight: 500; }
  .ai-based-on-content .abo-tag.patient { background: #dbeafe; color: #1e40af; }
  .ai-based-on-content .abo-tag.disease { background: #fef2f2; color: #dc2626; }
  .ai-based-on-content .abo-tag.symptom { background: #eff6ff; color: #1e40af; }
  .ai-based-on-content .abo-tag.violation { background: #fef2f2; color: #991b1b; }
  .ai-based-on-content .abo-tag.drug { background: #faf5ff; color: #7c3aed; }
  .ai-based-on-content .abo-tag.clinical { background: #f0fdf4; color: #166534; }
  .ai-based-on-content .abo-tag.other { background: #f1f5f9; color: #475569; }

  /* ===== DASHBOARD LAYOUT ===== */
  .dashboard-wrapper { display: flex; flex: 1; min-height: 0; overflow: hidden; position: relative; }

  /* ===== SIDEBAR ===== */
  .dashboard-sidebar { width: 272px; min-width: 272px; max-width: 272px; background: #ffffff; border-right: 1px solid #e2e8f0;
    padding: 0.875rem; overflow-y: auto; transition: margin-left 0.3s cubic-bezier(0.4,0,0.2,1); font-size: 0.8125rem; }
  .dashboard-sidebar.collapsed { margin-left: -252px; }
  .dashboard-sidebar h2 { display: none; }
  .sidebar-card { background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 10px; padding: 0.875rem; margin-bottom: 0.625rem; }
  .sidebar-card h3 { font-size: 0.6875rem; font-weight: 700; color: #64748b; text-transform: uppercase;
    letter-spacing: 0.05em; margin: 0 0 0.625rem; }
  .sidebar-card h4 { font-size: 0.625rem; font-weight: 600; color: #94a3b8; text-transform: uppercase;
    letter-spacing: 0.05em; margin: 0.625rem 0 0.375rem; }
  .sidebar-card h4:first-of-type { margin-top: 0; }
  .sidebar-card p { margin: 0.25rem 0; color: #334155; font-size: 0.8125rem; }
  .sidebar-card ul { margin: 0; padding-left: 0; list-style: none; }
  .sidebar-card li { margin: 0.2rem 0; color: #475569; font-size: 0.8125rem; }
  .node-legend li { display: flex; align-items: center; gap: 0.5rem; padding: 0.1rem 0; }
  .node-legend span { display: inline-block; width: 10px; height: 10px; border-radius: 50%; flex-shrink: 0; }
  .edge-legend li { font-size: 0.75rem; color: #64748b; padding: 0.1rem 0; }
  .color-coding { display: flex; flex-wrap: wrap; gap: 0.75rem; align-items: center; font-size: 0.75rem; color: #64748b; }
  .color-dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 0.25rem; vertical-align: middle; }
  #violationsList li { padding: 0.375rem 0; border-bottom: 1px solid #f1f5f9; font-size: 0.75rem; line-height: 1.5; }
  #violationsList li:last-child { border-bottom: none; }
  .stat-doctors { font-size: 0.75rem; color: #64748b; margin-top: 0.375rem !important; }

  /* ===== TOGGLE ===== */
  .toggle-sidebar { position: absolute; left: 272px; top: 10px; z-index: 10; width: 24px; height: 24px;
    display: flex; align-items: center; justify-content: center; background: #ffffff; border: 1px solid #e2e8f0;
    border-radius: 6px; color: #64748b; cursor: pointer; font-size: 10px;
    transition: all 0.3s cubic-bezier(0.4,0,0.2,1); box-shadow: 0 1px 2px rgba(0,0,0,0.05); }
  .toggle-sidebar:hover { background: #f1f5f9; color: #0f172a; }
  .toggle-sidebar.collapsed { left: 20px; }

  /* ===== MAIN AREA ===== */
  .dashboard-main { flex: 1; display: flex; flex-direction: column; min-width: 0; position: relative; min-height: 0; overflow: hidden; background: #f8fafc; }
  .dashboard-main #mynetwork { flex: 1; min-height: 0; height: 100%; background: #ffffff !important; border: none !important; }

  /* ===== FILTER BAR ===== */
  .filter-bar { padding: 0.5rem 1rem; background: #ffffff; border-bottom: 1px solid #e2e8f0;
    display: flex; flex-wrap: wrap; gap: 0.5rem; align-items: center; font-size: 0.8125rem; }
  .filter-bar label { font-weight: 600; color: #475569; font-size: 0.75rem; margin-right: 0.125rem; }
  .filter-bar select { padding: 0.3rem 0.5rem; border: 1px solid #e2e8f0; border-radius: 6px; font-size: 0.75rem;
    color: #334155; background: #f8fafc; outline: none; transition: all 0.15s ease; font-family: inherit; }
  .filter-bar select:focus { border-color: #3b82f6; box-shadow: 0 0 0 2px rgba(59,130,246,0.1); }
  .filter-bar button { padding: 0.3rem 0.75rem; border: 1px solid #e2e8f0; border-radius: 6px; background: #ffffff;
    color: #475569; font-size: 0.75rem; font-weight: 500; cursor: pointer; transition: all 0.15s ease; font-family: inherit; }
  .filter-bar button:hover { background: #f1f5f9; border-color: #cbd5e1; }
  .filter-bar #filterLabel { margin-left: auto; font-weight: 600; color: #3b82f6; font-size: 0.75rem; }

  /* ===== EXPLANATION PANEL ===== */
  .explain-panel { position: absolute; bottom: 0; left: 0; right: 0; max-height: 200px; overflow-y: auto;
    background: #ffffff; border-top: 1px solid #e2e8f0; padding: 0.875rem 1.25rem; font-size: 0.8125rem;
    box-shadow: 0 -4px 12px rgba(0,0,0,0.04); animation: slideUp 0.2s ease; }
  @keyframes slideUp { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: translateY(0); } }
  .explain-panel h4 { margin: 0 0 0.5rem; font-size: 0.875rem; font-weight: 600; color: #0f172a; }
  .explain-panel.empty { display: none; }

  /* Upload Document */
  .upload-doc-btn { display: flex; align-items: center; gap: 0.5rem; padding: 0.5rem 1rem;
    background: #10b981; color: #fff; border: none; border-radius: 8px; font-size: 0.8125rem;
    font-weight: 600; cursor: pointer; transition: all 0.15s ease; white-space: nowrap; font-family: inherit;
    flex-shrink: 0; align-self: center; }
  .upload-doc-btn:hover { background: #059669; transform: translateY(-1px); box-shadow: 0 2px 4px rgba(5,150,105,0.3); }
  .upload-doc-btn svg { width: 16px; height: 16px; }
  .upload-modal { position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: rgba(0,0,0,0.5);
    z-index: 1000; display: flex; align-items: center; justify-content: center; animation: fadeIn 0.2s ease; }
  @keyframes fadeIn { from { opacity: 0; } to { opacity: 1; } }
  .upload-panel { background: #fff; border-radius: 16px; width: 560px; max-width: 90vw; max-height: 85vh;
    overflow-y: auto; box-shadow: 0 20px 60px rgba(0,0,0,0.15); animation: scaleIn 0.2s ease; }
  @keyframes scaleIn { from { transform: scale(0.95); opacity: 0; } to { transform: scale(1); opacity: 1; } }
  .upload-panel-header { padding: 1.25rem 1.5rem; border-bottom: 1px solid #e2e8f0; display: flex;
    align-items: center; justify-content: space-between; }
  .upload-panel-header h3 { font-size: 1rem; font-weight: 700; color: #0f172a; }
  .upload-panel-close { background: none; border: none; color: #94a3b8; cursor: pointer; font-size: 1.5rem;
    padding: 0.25rem; border-radius: 6px; line-height: 1; transition: all 0.15s; }
  .upload-panel-close:hover { background: #f1f5f9; color: #0f172a; }
  .upload-panel-body { padding: 1.5rem; }
  .upload-dropzone { border: 2px dashed #cbd5e1; border-radius: 12px; padding: 2rem; text-align: center;
    cursor: pointer; transition: all 0.2s; background: #f8fafc; }
  .upload-dropzone:hover, .upload-dropzone.dragover { border-color: #3b82f6; background: #eff6ff; }
  .upload-dropzone svg { width: 40px; height: 40px; color: #94a3b8; margin-bottom: 0.75rem; }
  .upload-dropzone p { color: #64748b; font-size: 0.875rem; margin: 0; }
  .upload-dropzone .hint { font-size: 0.75rem; color: #94a3b8; margin-top: 0.5rem; }
  .upload-file-input { display: none; }
  .upload-loading { text-align: center; padding: 2rem; color: #64748b; font-size: 0.875rem; }
  .upload-loading .loading-spinner { width: 24px; height: 24px; margin: 0 auto 1rem; display: block; }
  .upload-preview h4 { font-size: 0.75rem; font-weight: 700; color: #64748b; text-transform: uppercase;
    letter-spacing: 0.05em; margin: 1rem 0 0.5rem; }
  .upload-preview h4:first-child { margin-top: 0; }
  .upload-preview .tag-list { display: flex; flex-wrap: wrap; gap: 0.375rem; }
  .upload-preview .tag { display: inline-flex; padding: 0.25rem 0.625rem; background: #eff6ff; color: #1e40af;
    border-radius: 100px; font-size: 0.75rem; font-weight: 500; border: 1px solid #bfdbfe; }
  .upload-preview .tag.disease { background: #fef2f2; color: #dc2626; border-color: #fecaca; }
  .upload-preview .clinical-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(120px, 1fr)); gap: 0.5rem; }
  .upload-preview .clinical-item { background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px; padding: 0.5rem 0.75rem; }
  .upload-preview .clinical-item .cv-label { font-size: 0.6875rem; color: #94a3b8; text-transform: uppercase; letter-spacing: 0.05em; }
  .upload-preview .clinical-item .cv-value { font-size: 1rem; font-weight: 700; color: #0f172a; margin-top: 0.125rem; }
  .upload-name-input { width: 100%; padding: 0.5rem 0.75rem; border: 1.5px solid #e2e8f0; border-radius: 8px;
    font-size: 0.875rem; color: #0f172a; font-family: inherit; outline: none; transition: border-color 0.15s; }
  .upload-name-input:focus { border-color: #3b82f6; box-shadow: 0 0 0 3px rgba(59,130,246,0.1); }
  .upload-actions { padding: 1rem 1.5rem; border-top: 1px solid #e2e8f0; display: flex; gap: 0.75rem; justify-content: flex-end; }
  .upload-actions button { padding: 0.5rem 1.25rem; border-radius: 8px; font-size: 0.8125rem; font-weight: 600;
    cursor: pointer; transition: all 0.15s; font-family: inherit; }
  .upload-actions .cancel-btn { background: #f1f5f9; color: #475569; border: 1px solid #e2e8f0; }
  .upload-actions .cancel-btn:hover { background: #e2e8f0; }
  .upload-actions .confirm-btn { background: #3b82f6; color: #fff; border: none; }
  .upload-actions .confirm-btn:hover { background: #2563eb; }
  .upload-actions .confirm-btn:disabled { opacity: 0.5; cursor: not-allowed; }
  .upload-error { color: #dc2626; font-size: 0.8125rem; padding: 0.75rem; background: #fef2f2;
    border-radius: 8px; margin-top: 1rem; border: 1px solid #fecaca; }
  .upload-success { color: #16a34a; font-size: 0.8125rem; padding: 0.75rem; background: #f0fdf4;
    border-radius: 8px; border: 1px solid #bbf7d0; text-align: center; }
  .upload-success strong { display: block; font-size: 0.875rem; margin-bottom: 0.25rem; }

  /* Compare Patients */
  .filter-bar button.compare-btn { padding: 0.3rem 0.75rem; border: none; border-radius: 6px; background: #7c3aed;
    color: #fff; font-size: 0.75rem; font-weight: 600; cursor: pointer; transition: all 0.15s; font-family: inherit; white-space: nowrap; }
  .filter-bar button.compare-btn:hover { background: #6d28d9; transform: translateY(-1px); box-shadow: 0 2px 4px rgba(109,40,217,0.3); }
  .compare-modal { position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: rgba(0,0,0,0.5);
    z-index: 1000; display: flex; align-items: center; justify-content: center; animation: fadeIn 0.2s ease; }
  .compare-panel { background: #fff; border-radius: 16px; width: 480px; max-width: 90vw; max-height: 80vh;
    display: flex; flex-direction: column; box-shadow: 0 20px 60px rgba(0,0,0,0.15); animation: scaleIn 0.2s ease; }
  .compare-panel-header { padding: 1.25rem 1.5rem; border-bottom: 1px solid #e2e8f0; display: flex;
    align-items: center; justify-content: space-between; flex-shrink: 0; }
  .compare-panel-header h3 { font-size: 1rem; font-weight: 700; color: #0f172a; }
  .compare-panel-body { padding: 1rem 1.5rem; overflow-y: auto; flex: 1; min-height: 0; }
  .compare-hint { font-size: 0.8125rem; color: #64748b; margin: 0 0 0.75rem; }
  .compare-patient-list { display: flex; flex-direction: column; gap: 0.25rem; }
  .compare-patient-item { display: flex; align-items: center; gap: 0.625rem; padding: 0.5rem 0.75rem;
    border-radius: 8px; cursor: pointer; transition: background 0.1s; font-size: 0.8125rem; }
  .compare-patient-item:hover { background: #f1f5f9; }
  .compare-patient-item input[type="checkbox"] { width: 16px; height: 16px; accent-color: #7c3aed; cursor: pointer; flex-shrink: 0; }
  .compare-patient-item .cp-name { font-weight: 500; color: #0f172a; }
  .compare-patient-item .cp-id { color: #94a3b8; font-size: 0.75rem; margin-left: auto; }
  .compare-panel-actions { padding: 1rem 1.5rem; border-top: 1px solid #e2e8f0; display: flex; gap: 0.75rem;
    justify-content: flex-end; flex-shrink: 0; }
  .compare-panel-actions button { padding: 0.5rem 1.25rem; border-radius: 8px; font-size: 0.8125rem; font-weight: 600;
    cursor: pointer; transition: all 0.15s; font-family: inherit; }
  .compare-panel-actions .cancel-btn { background: #f1f5f9; color: #475569; border: 1px solid #e2e8f0; }
  .compare-panel-actions .cancel-btn:hover { background: #e2e8f0; }
  .compare-panel-actions .confirm-btn { background: #7c3aed; color: #fff; border: none; }
  .compare-panel-actions .confirm-btn:hover { background: #6d28d9; }
  .compare-panel-actions .confirm-btn:disabled { opacity: 0.45; cursor: not-allowed; }
  .compare-results-panel { position: absolute; bottom: 0; left: 0; right: 0; max-height: 260px; overflow-y: auto;
    background: #fff; border-top: 1px solid #e2e8f0; padding: 0.875rem 1.25rem; font-size: 0.8125rem;
    box-shadow: 0 -4px 12px rgba(0,0,0,0.06); animation: slideUp 0.2s ease; z-index: 5; }
  .compare-results-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 0.75rem; }
  .compare-results-header h4 { font-size: 0.875rem; font-weight: 700; color: #0f172a; margin: 0; }
  .compare-results-header button { padding: 0.25rem 0.75rem; border-radius: 6px; background: #f1f5f9; color: #475569;
    border: 1px solid #e2e8f0; font-size: 0.75rem; font-weight: 500; cursor: pointer; font-family: inherit; }
  .compare-results-header button:hover { background: #e2e8f0; }
  .compare-patients-row { display: flex; gap: 0.5rem; flex-wrap: wrap; margin-bottom: 0.75rem; }
  .compare-patient-card { background: #eff6ff; border: 1px solid #bfdbfe; border-radius: 8px; padding: 0.5rem 0.75rem;
    font-size: 0.75rem; color: #1e40af; line-height: 1.5; }
  .compare-patient-card strong { font-weight: 600; }
  .compare-section { margin-bottom: 0.625rem; }
  .compare-section h5 { font-size: 0.6875rem; font-weight: 700; color: #64748b; text-transform: uppercase;
    letter-spacing: 0.05em; margin: 0 0 0.375rem; }
  .compare-section .tag-list { display: flex; flex-wrap: wrap; gap: 0.3rem; }
  .compare-section .tag { display: inline-flex; padding: 0.2rem 0.5rem; border-radius: 100px;
    font-size: 0.6875rem; font-weight: 500; }
  .compare-section .tag.shared { background: #fef3c7; color: #92400e; border: 1px solid #fde68a; }
  .compare-section .tag.unique { background: #f1f5f9; color: #64748b; border: 1px solid #e2e8f0; }
  .compare-section .tag.violation { background: #fef2f2; color: #dc2626; border: 1px solid #fecaca; }
  .compare-none { color: #94a3b8; font-size: 0.75rem; font-style: italic; margin: 0; }

  /* Benchmark Results */
  .filter-bar button.benchmark-btn { padding: 0.3rem 0.75rem; border: none; border-radius: 6px; background: #0ea5e9;
    color: #fff; font-size: 0.75rem; font-weight: 600; cursor: pointer; transition: all 0.15s; font-family: inherit; white-space: nowrap; }
  .filter-bar button.benchmark-btn:hover { background: #0284c7; transform: translateY(-1px); box-shadow: 0 2px 4px rgba(14,165,233,0.35); }
  .filter-bar button.benchmark-btn:disabled { opacity: 0.55; cursor: not-allowed; transform: none; box-shadow: none; }
  .benchmark-modal { position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: rgba(15,23,42,0.55);
    z-index: 1050; display: none; align-items: center; justify-content: center; animation: fadeIn 0.2s ease; padding: 1rem; }
  .benchmark-modal.open { display: flex; }
  .benchmark-panel { background: #fff; border-radius: 16px; width: 960px; max-width: 100%; max-height: 92vh;
    display: flex; flex-direction: column; box-shadow: 0 24px 80px rgba(0,0,0,0.18); overflow: hidden; border: 1px solid #e2e8f0; }
  .benchmark-panel-header { padding: 1rem 1.25rem; border-bottom: 1px solid #e2e8f0; display: flex; align-items: center; justify-content: space-between; flex-shrink: 0; background: linear-gradient(180deg, #f8fafc 0%, #fff 100%); }
  .benchmark-panel-header h3 { font-size: 1rem; font-weight: 700; color: #0f172a; margin: 0; letter-spacing: -0.02em; }
  .benchmark-panel-header .benchmark-sub { font-size: 0.6875rem; color: #64748b; margin: 0.25rem 0 0; }
  .benchmark-panel-body { padding: 1rem 1.25rem 1.25rem; overflow-y: auto; flex: 1; min-height: 0; }
  .benchmark-loading { display: none; flex-direction: column; align-items: center; justify-content: center; gap: 0.75rem; padding: 3rem 2rem; color: #64748b; font-size: 0.875rem; }
  .benchmark-loading.visible { display: flex; }
  .benchmark-loading .loading-spinner { width: 28px; height: 28px; border-width: 3px; }
  .benchmark-content { display: none; }
  .benchmark-content.visible { display: block; }
  .benchmark-hero { display: flex; flex-wrap: wrap; align-items: stretch; gap: 1rem; margin-bottom: 1rem; }
  .benchmark-score-block { flex: 1; min-width: 200px; background: linear-gradient(135deg, #eff6ff 0%, #f8fafc 100%);
    border: 1px solid #bfdbfe; border-radius: 12px; padding: 1rem 1.25rem; display: flex; align-items: center; gap: 1.25rem; }
  .benchmark-score-num { font-size: 2.75rem; font-weight: 800; color: #1e40af; line-height: 1; letter-spacing: -0.03em; }
  .benchmark-score-num span { font-size: 1rem; font-weight: 600; color: #64748b; vertical-align: super; margin-left: 0.125rem; }
  .benchmark-score-meta { flex: 1; }
  .benchmark-score-meta .label { font-size: 0.625rem; font-weight: 700; color: #64748b; text-transform: uppercase; letter-spacing: 0.06em; margin-bottom: 0.35rem; }
  .benchmark-status-pill { display: inline-flex; align-items: center; padding: 0.35rem 0.85rem; border-radius: 100px; font-size: 0.75rem; font-weight: 700;
    letter-spacing: 0.02em; }
  .benchmark-status-pill.good { background: #f0fdf4; color: #15803d; border: 1px solid #bbf7d0; }
  .benchmark-status-pill.moderate { background: #fffbeb; color: #b45309; border: 1px solid #fde68a; }
  .benchmark-status-pill.needs_improvement { background: #fef2f2; color: #b91c1c; border: 1px solid #fecaca; }
  .benchmark-run-meta { font-size: 0.6875rem; color: #94a3b8; margin-top: 0.35rem; }
  .benchmark-metrics-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 0.625rem; margin-bottom: 1rem; }
  .benchmark-metric-card { background: #fff; border: 1px solid #e2e8f0; border-radius: 10px; padding: 0.75rem 0.875rem;
    box-shadow: 0 1px 2px rgba(15,23,42,0.04); }
  .benchmark-metric-card .bm-label { font-size: 0.6875rem; font-weight: 600; color: #64748b; text-transform: uppercase; letter-spacing: 0.04em; margin-bottom: 0.35rem; line-height: 1.35; }
  .benchmark-metric-card .bm-value { font-size: 1.375rem; font-weight: 700; color: #0f172a; }
  .benchmark-metric-card .bm-bar { height: 6px; background: #f1f5f9; border-radius: 100px; margin-top: 0.5rem; overflow: hidden; }
  .benchmark-metric-card .bm-bar > i { display: block; height: 100%; border-radius: 100px; background: linear-gradient(90deg, #3b82f6, #0ea5e9); }
  .benchmark-charts-row { display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; margin-bottom: 1rem; }
  @media (max-width: 720px) { .benchmark-charts-row { grid-template-columns: 1fr; } }
  .benchmark-chart-card { background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 12px; padding: 0.75rem 0.75rem 0.5rem; }
  .benchmark-chart-card h4 { font-size: 0.6875rem; font-weight: 700; color: #64748b; text-transform: uppercase; letter-spacing: 0.05em; margin: 0 0 0.5rem; }
  .benchmark-chart-card canvas { max-height: 220px !important; }
  .benchmark-experiment-row { display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; margin-bottom: 1rem; }
  @media (max-width: 900px) { .benchmark-experiment-row { grid-template-columns: 1fr; } }
  .benchmark-exp-note { font-size: 0.625rem; color: #64748b; line-height: 1.45; margin: 0 0 0.5rem; }
  .benchmark-interpretation { font-size: 0.6875rem; color: #334155; line-height: 1.5; margin: 0 0 1rem; padding: 0.6rem 0.75rem; background: #f0fdf4; border: 1px solid #bbf7d0; border-radius: 10px; }
  .benchmark-table-wrap { border: 1px solid #e2e8f0; border-radius: 12px; overflow: hidden; background: #fff; }
  .benchmark-table-wrap h4 { font-size: 0.6875rem; font-weight: 700; color: #64748b; text-transform: uppercase; letter-spacing: 0.05em;
    padding: 0.625rem 0.875rem; margin: 0; background: #f8fafc; border-bottom: 1px solid #e2e8f0; }
  .benchmark-table { width: 100%; border-collapse: collapse; font-size: 0.75rem; }
  .benchmark-table th { text-align: left; padding: 0.5rem 0.75rem; background: #f1f5f9; color: #475569; font-weight: 600; border-bottom: 1px solid #e2e8f0; }
  .benchmark-table td { padding: 0.5rem 0.75rem; border-bottom: 1px solid #f1f5f9; color: #334155; vertical-align: top; line-height: 1.45; }
  .benchmark-table tr:last-child td { border-bottom: none; }
  .benchmark-table .tc-score { font-weight: 700; color: #1e40af; white-space: nowrap; }
  .benchmark-table .mono { font-family: ui-monospace, monospace; font-size: 0.6875rem; color: #475569; }
  .benchmark-note { font-size: 0.6875rem; color: #94a3b8; margin-top: 0.75rem; padding: 0.5rem 0.75rem; background: #f8fafc; border-radius: 8px; border: 1px dashed #e2e8f0; }
  .benchmark-gi-details { margin: 0.75rem 0 0.5rem; border: 1px solid #e2e8f0; border-radius: 10px; background: #fff; font-size: 0.75rem; }
  .benchmark-gi-details > summary { padding: 0.5rem 0.75rem; cursor: pointer; font-weight: 600; color: #0f172a; list-style: none; }
  .benchmark-gi-details > summary::-webkit-details-marker { display: none; }
  .benchmark-gi-details[open] > summary { border-bottom: 1px solid #e2e8f0; }
  .benchmark-gi-body { padding: 0.75rem; color: #475569; line-height: 1.5; }
  .benchmark-gi-body table { width: 100%; font-size: 0.6875rem; border-collapse: collapse; }
  .benchmark-gi-body th, .benchmark-gi-body td { text-align: left; padding: 0.35rem 0.5rem; border-bottom: 1px solid #f1f5f9; }
  .benchmark-gi-body th { color: #64748b; font-weight: 600; }
  .benchmark-compare-bar { display: flex; align-items: center; gap: 0.5rem; margin: 0.5rem 0 0.75rem; font-size: 0.75rem; color: #475569; }
  .benchmark-compare-bar input { accent-color: #0ea5e9; width: 16px; height: 16px; cursor: pointer; }
  .benchmark-compare-strip { display: none; flex-wrap: wrap; gap: 0.75rem; margin-bottom: 0.75rem; align-items: stretch; }
  .benchmark-compare-strip.visible { display: flex; }
  .bc-item { flex: 1; min-width: 160px; background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 10px; padding: 0.5rem 0.75rem; }
  .bc-item.bc-muted { background: #fff7ed; border-color: #fed7aa; }
  .bc-item.bc-delta { background: #ecfdf5; border-color: #a7f3d0; }
  .benchmark-compare-disclaimer { flex: 1 1 100%; margin: 0; padding: 0.5rem 0 0; font-size: 0.625rem; color: #64748b; line-height: 1.45; border-top: 1px solid #e2e8f0; white-space: pre-line; }
  .bc-lab { display: block; font-size: 0.625rem; font-weight: 700; color: #64748b; text-transform: uppercase; letter-spacing: 0.04em; margin-bottom: 0.25rem; }
  .bc-val { font-size: 1.25rem; font-weight: 800; color: #1e40af; }

  /* Patient-Aware AI context chips */
  .ai-context-bar { display: flex; align-items: center; gap: 0.375rem; flex-wrap: wrap; padding: 0.35rem 0; font-size: 0.6875rem; min-height: 0; }
  .ai-context-bar:empty { display: none; }
  .ai-ctx-label { color: #64748b; font-weight: 600; white-space: nowrap; }
  .ai-ctx-chip { display: inline-flex; align-items: center; gap: 0.25rem; padding: 0.125rem 0.5rem; border-radius: 100px;
    background: #eff6ff; color: #1d4ed8; border: 1px solid #bfdbfe; font-size: 0.6875rem; font-weight: 500; cursor: default; }
  .ai-ctx-chip .ctx-remove { cursor: pointer; font-size: 0.75rem; color: #93c5fd; margin-left: 0.125rem; line-height: 1; }
  .ai-ctx-chip .ctx-remove:hover { color: #dc2626; }
  .ai-ctx-clear { color: #94a3b8; cursor: pointer; font-size: 0.625rem; text-decoration: underline; margin-left: 0.25rem; }
  .ai-ctx-clear:hover { color: #dc2626; }
  .ai-ctx-hint { color: #94a3b8; font-size: 0.625rem; font-style: italic; }
  .ai-answer-smart { white-space: pre-wrap; line-height: 1.65; }
  .ai-answer-smart b, .ai-answer-smart strong { font-weight: 600; }

  /* Clinical Timeline */
  .timeline-modal { position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: rgba(0,0,0,0.55);
    z-index: 1100; display: flex; align-items: center; justify-content: center; animation: fadeIn 0.2s ease; }
  .timeline-panel { background: #fff; border-radius: 16px; width: 920px; max-width: 95vw; max-height: 90vh;
    display: flex; flex-direction: column; box-shadow: 0 20px 60px rgba(0,0,0,0.18); animation: scaleIn 0.2s ease; }
  .timeline-header { padding: 1rem 1.5rem; border-bottom: 1px solid #e2e8f0; display: flex; align-items: center; justify-content: space-between; flex-shrink: 0; }
  .timeline-header h3 { font-size: 1rem; font-weight: 700; color: #0f172a; margin: 0; }
  .timeline-header .tl-patient-info { font-size: 0.75rem; color: #64748b; margin-left: 0.75rem; }
  .timeline-header-left { display: flex; align-items: center; }
  .timeline-body { padding: 1rem 1.5rem; overflow-y: auto; flex: 1; min-height: 0; }
  .tl-charts-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; margin-bottom: 1rem; }
  .tl-chart-card { background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 12px; padding: 0.75rem; }
  .tl-chart-title { display: flex; align-items: center; justify-content: space-between; margin-bottom: 0.5rem; }
  .tl-chart-title span { font-size: 0.75rem; font-weight: 600; color: #0f172a; }
  .tl-trend { font-size: 0.625rem; font-weight: 600; padding: 0.125rem 0.5rem; border-radius: 100px; }
  .tl-trend.rising { background: #fef2f2; color: #dc2626; }
  .tl-trend.falling { background: #f0fdf4; color: #16a34a; }
  .tl-trend.stable { background: #f1f5f9; color: #64748b; }
  .tl-trend.good { background: #f0fdf4; color: #16a34a; }
  .tl-trend.bad { background: #fef2f2; color: #dc2626; }
  .tl-chart-card canvas { width: 100% !important; height: 160px !important; }
  .tl-events-section { margin-top: 0.5rem; }
  .tl-events-title { font-size: 0.8125rem; font-weight: 700; color: #0f172a; margin: 0 0 0.5rem; }
  .tl-events-list { display: flex; flex-direction: column; gap: 0.375rem; }
  .tl-event { display: flex; align-items: flex-start; gap: 0.625rem; font-size: 0.75rem; color: #334155; padding: 0.375rem 0.625rem; border-radius: 8px; background: #f8fafc; }
  .tl-event-dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; margin-top: 0.25rem; }
  .tl-event-dot.encounter { background: #3b82f6; }
  .tl-event-dot.drug { background: #10b981; }
  .tl-event-dot.procedure { background: #f59e0b; }
  .tl-event-dot.lab { background: #8b5cf6; }
  .tl-event-dot.treatment { background: #ef4444; }
  .tl-event-date { color: #94a3b8; font-size: 0.6875rem; min-width: 60px; flex-shrink: 0; }
  .tl-no-events { color: #94a3b8; font-size: 0.75rem; font-style: italic; }
  .view-timeline-btn { display: inline-flex; align-items: center; gap: 0.375rem; padding: 0.375rem 0.75rem;
    background: #7c3aed; color: #fff; border: none; border-radius: 6px; font-size: 0.6875rem;
    font-weight: 600; cursor: pointer; transition: all 0.15s; font-family: inherit; margin-top: 0.5rem; }
  .view-timeline-btn:hover { background: #6d28d9; }
  .view-timeline-btn svg { width: 14px; height: 14px; }

  /* Violation severity badges */
  .sev-badge { display: inline-block; padding: 0.1rem 0.4rem; border-radius: 100px; font-size: 0.5625rem;
    font-weight: 700; letter-spacing: 0.04em; text-transform: uppercase; vertical-align: middle; margin-right: 0.25rem; }
  .sev-badge.critical { background: #fef2f2; color: #dc2626; border: 1px solid #fecaca; }
  .sev-badge.warning { background: #fffbeb; color: #d97706; border: 1px solid #fde68a; }
  .sev-badge.normal { background: #f0fdf4; color: #16a34a; border: 1px solid #bbf7d0; }
  .violation-reason { display: block; font-size: 0.6875rem; color: #64748b; margin-top: 0.125rem; line-height: 1.4; font-style: italic; }
</style>
<div id="patientSelectorOverlay" class="patient-selector-overlay">
  <div class="patient-selector-card">
    <div class="ps-header">
      <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 12h-4l-3 9L9 3l-3 9H2"/></svg>
      <h2>Clinical Dashboard</h2>
      <p>Select a patient to begin, or skip to view all data</p>
    </div>
    <div class="ps-body">
      <input type="text" class="ps-search" id="psSearch" placeholder="Search patients..." oninput="filterPsPatients(this.value)">
      <div class="ps-list" id="psList"></div>
    </div>
    <div class="ps-footer">
      <button class="ps-skip" onclick="dismissPatientSelector()">Skip &mdash; view all patients</button>
    </div>
  </div>
</div>
<div id="viewingBar" class="viewing-bar hidden"></div>
<header class="app-header">
  <div class="app-header-inner">
    <div class="app-brand">
      <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 12h-4l-3 9L9 3l-3 9H2"/></svg>
      <h1>Clinical Dashboard</h1>
    </div>
    <div class="ai-search-wrapper">
      <div class="ai-search-box">
        <svg class="search-icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/></svg>
        <textarea id="aiQuestion" placeholder="Ask AI — click patient nodes for context, e.g. 'What violations does this patient have?'" rows="1"></textarea>
        <input type="hidden" id="aiApiUrl" value="http://localhost:8000" />
        <button type="button" id="aiAskBtn" onclick="askAi()">Ask AI</button>
      </div>
      <div id="aiLoading" class="ai-loading" style="display:none;"><span class="loading-spinner"></span> Analyzing...</div>
    </div>
    <button type="button" class="upload-doc-btn" onclick="openUploadModal()">
      <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>
      Upload Document
    </button>
  </div>
</header>
<div id="aiContextBar" class="ai-context-bar" style="padding:0.25rem 1.5rem;border-bottom:1px solid #e2e8f0;background:#f8fafc;"></div>
<div id="uploadModal" class="upload-modal" style="display:none;" onclick="if(event.target===this)closeUploadModal()">
  <div class="upload-panel">
    <div class="upload-panel-header">
      <h3>Upload Medical Document</h3>
      <button class="upload-panel-close" onclick="closeUploadModal()">&times;</button>
    </div>
    <div class="upload-panel-body">
      <div id="uploadDropzone" class="upload-dropzone">
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/><polyline points="10 9 9 9 8 9"/></svg>
        <p>Drag &amp; drop a medical document here</p>
        <p class="hint">PDF or text file &mdash; or click to browse</p>
        <input type="file" id="uploadFileInput" class="upload-file-input" accept=".pdf,.txt,.text,.md" onchange="handleFileSelect(this.files[0])">
      </div>
      <div id="uploadLoading" class="upload-loading" style="display:none;">
        <div class="loading-spinner"></div>
        <p>Extracting medical data&hellip;</p>
      </div>
      <div id="uploadPreview" class="upload-preview" style="display:none;"></div>
      <div id="uploadError" class="upload-error" style="display:none;"></div>
    </div>
    <div id="uploadActions" class="upload-actions" style="display:none;">
      <button type="button" class="cancel-btn" onclick="resetUploadUI()">Cancel</button>
      <button type="button" class="confirm-btn" id="confirmPatientBtn" onclick="confirmCreatePatient()">Confirm &amp; Create Patient</button>
    </div>
  </div>
</div>
<div id="compareModal" style="display:none;" class="compare-modal" onclick="if(event.target===this)closeCompareModal()">
  <div class="compare-panel">
    <div class="compare-panel-header">
      <h3>Compare Patients</h3>
      <button class="upload-panel-close" onclick="closeCompareModal()">&times;</button>
    </div>
    <div class="compare-panel-body">
      <p class="compare-hint">Select 2 or more patients to compare their diseases, symptoms, and violations.</p>
      <div class="compare-patient-list" id="comparePatientList"></div>
    </div>
    <div class="compare-panel-actions">
      <button class="cancel-btn" onclick="closeCompareModal()">Cancel</button>
      <button class="confirm-btn" id="compareRunBtn" onclick="runComparison()" disabled>Compare</button>
    </div>
  </div>
</div>
<div id="timelineModal" style="display:none;" class="timeline-modal" onclick="if(event.target===this)closeTimeline()">
  <div class="timeline-panel">
    <div class="timeline-header">
      <div class="timeline-header-left">
        <h3 id="timelineTitle">Clinical Timeline</h3>
        <span class="tl-patient-info" id="timelineInfo"></span>
      </div>
      <button class="upload-panel-close" onclick="closeTimeline()">&times;</button>
    </div>
    <div class="timeline-body" id="timelineBody">
      <div class="tl-charts-grid">
        <div class="tl-chart-card">
          <div class="tl-chart-title"><span>SOFA Score</span><span class="tl-trend" id="trendSofa"></span></div>
          <canvas id="chartSofa"></canvas>
        </div>
        <div class="tl-chart-card">
          <div class="tl-chart-title"><span>MAP (mmHg)</span><span class="tl-trend" id="trendMap"></span></div>
          <canvas id="chartMap"></canvas>
        </div>
        <div class="tl-chart-card">
          <div class="tl-chart-title"><span>Creatinine (mg/dL)</span><span class="tl-trend" id="trendCreat"></span></div>
          <canvas id="chartCreat"></canvas>
        </div>
        <div class="tl-chart-card">
          <div class="tl-chart-title"><span>GCS</span><span class="tl-trend" id="trendGcs"></span></div>
          <canvas id="chartGcs"></canvas>
        </div>
      </div>
      <div class="tl-chart-card" style="margin-bottom:1rem;">
        <div class="tl-chart-title"><span>Lactate (mmol/L)</span><span class="tl-trend" id="trendLactate"></span></div>
        <canvas id="chartLactate"></canvas>
      </div>
      <div class="tl-events-section">
        <p class="tl-events-title">Clinical Events</p>
        <div class="tl-events-list" id="timelineEvents"></div>
      </div>
    </div>
  </div>
</div>
<div id="benchmarkModal" class="benchmark-modal" onclick="if(event.target===this)closeBenchmarkModal()" aria-hidden="true">
  <div class="benchmark-panel" onclick="event.stopPropagation()">
    <div class="benchmark-panel-header">
      <div>
        <h3>Benchmark Results</h3>
        <p class="benchmark-sub">Evaluation summary &mdash; clinical AI and graph-grounding performance</p>
      </div>
      <button type="button" class="upload-panel-close" onclick="closeBenchmarkModal()" aria-label="Close">&times;</button>
    </div>
    <div class="benchmark-panel-body">
      <div id="benchmarkLoading" class="benchmark-loading">
        <div class="loading-spinner"></div>
        <span>Running benchmark suite&hellip;</span>
      </div>
      <div id="benchmarkContent" class="benchmark-content">
        <div class="benchmark-hero">
          <div class="benchmark-score-block">
            <div class="benchmark-score-num" id="benchmarkOverallNum">&mdash;<span>/100</span></div>
            <div class="benchmark-score-meta">
              <div class="label">Overall score</div>
              <span id="benchmarkStatusPill" class="benchmark-status-pill moderate">Moderate</span>
              <p class="benchmark-run-meta" id="benchmarkRunMeta"></p>
            </div>
          </div>
        </div>
        <div class="benchmark-metrics-grid" id="benchmarkMetricsGrid"></div>
        <details class="benchmark-gi-details" id="benchmarkGIDetails">
          <summary>Graph Impact — detailed breakdown</summary>
          <div id="benchmarkGIBreakdownBody" class="benchmark-gi-body"></div>
        </details>
        <div class="benchmark-compare-bar">
          <label style="display:flex;align-items:center;gap:0.5rem;cursor:pointer;margin:0;">
            <input type="checkbox" id="benchmarkCompareGraphToggle" />
            <span>Show graph-augmented vs. simulated baseline (see disclaimer)</span>
          </label>
        </div>
        <div class="benchmark-compare-strip" id="benchmarkGraphCompareRow">
          <div class="bc-item">
            <span class="bc-lab">Graph-augmented (measured)</span>
            <span class="bc-val" id="bcWithG">&mdash;</span>
          </div>
          <div class="bc-item bc-muted">
            <span class="bc-lab" id="bcBaselineLab">LLM Baseline (heuristic simulation, not rerun model)</span>
            <span class="bc-val" id="bcWithoutG">&mdash;</span>
          </div>
          <div class="bc-item bc-delta">
            <span class="bc-lab">Graph Influence Delta (non-causal estimate)</span>
            <span class="bc-val" id="bcDeltaG">&mdash;</span>
          </div>
          <p class="benchmark-compare-disclaimer" id="benchmarkCompareDisclaimer"></p>
        </div>
        <div class="benchmark-charts-row">
          <div class="benchmark-chart-card">
            <h4>Metric profile (radar)</h4>
            <canvas id="benchmarkRadarCanvas" height="220"></canvas>
          </div>
          <div class="benchmark-chart-card">
            <h4>Scores by dimension (bar)</h4>
            <canvas id="benchmarkBarCanvas" height="220"></canvas>
          </div>
        </div>
        <div class="benchmark-experiment-row" id="benchmarkExperimentSection" style="display:none;">
          <div class="benchmark-chart-card">
            <h4>Paired experiment: With Graph vs Without Graph</h4>
            <p class="benchmark-exp-note" id="benchmarkExperimentNote"></p>
            <canvas id="benchmarkModeCompareCanvas" height="300"></canvas>
          </div>
          <div class="benchmark-chart-card">
            <h4>Graph Improvement (percentage points)</h4>
            <p class="benchmark-exp-note" style="margin-bottom:0.35rem;">Positive = graph arm higher on the same 0–100 heuristic scales (includes grounding, traceability, hallucination proxy).</p>
            <canvas id="benchmarkImprovementCanvas" height="300"></canvas>
          </div>
        </div>
        <p class="benchmark-interpretation" id="benchmarkAugmentationInterpretation" style="display:none;" aria-live="polite"></p>
        <div class="benchmark-table-wrap">
          <h4>Test cases</h4>
          <table class="benchmark-table">
            <thead>
              <tr>
                <th>Patient</th>
                <th>Query</th>
                <th>Expected</th>
                <th>Actual</th>
                <th>Score</th>
              </tr>
            </thead>
            <tbody id="benchmarkTestCasesBody"></tbody>
          </table>
        </div>
        <p class="benchmark-note" id="benchmarkFootnote"></p>
      </div>
    </div>
  </div>
</div>
<div id="aiResult" class="ai-result-banner" style="display:none;">
  <div class="ai-result-inner">
    <span id="aiViolationBadge" class="violation-badge"></span>
    <div id="aiAnswer" class="answer"></div>
    <div id="aiMeta" class="meta"></div>
    <div id="aiBasedOn" class="ai-based-on" style="display:none;"></div>
    <div class="ai-result-actions">
      <button type="button" class="ai-new-question-btn" onclick="resetAiQuestion()">New Question</button>
    </div>
  </div>
</div>
<div class="dashboard-wrapper">
  <aside class="dashboard-sidebar" id="sidebar">
    <div class="sidebar-card patient-summary-card" id="patientSummaryCard">
      <h3>Patient Summary</h3>
      <div id="pscContent"></div>
    </div>
    <div class="sidebar-card insight-panel" id="insightPanel">
      <h3>Key Insights</h3>
      <div class="insight-list" id="insightList"></div>
    </div>
    <div class="sidebar-card" id="statsPanel">
      <h3>Overview</h3>
      <p id="statPatients">Total patients: &mdash;</p>
      <p id="statViolations">Violations: &mdash;</p>
      <p id="statDoctors" class="stat-doctors">Doctor compliance: &mdash;</p>
    </div>
    <div class="sidebar-card" id="violationsPanel">
      <h3>Protocol Violations</h3>
      <ul id="violationsList"></ul>
    </div>
    <div class="sidebar-card">
      <h3>Legend</h3>
      <h4>Nodes</h4>
      <ul class="node-legend" id="nodeLegend"></ul>
      <h4>Edges</h4>
      <ul class="edge-legend" id="edgeLegend"></ul>
      <h4>Violation Severity</h4>
      <div class="color-coding">
        <span><span class="color-dot" style="background:#dc2626"></span> Critical</span>
        <span><span class="color-dot" style="background:#f59e0b"></span> Warning</span>
        <span><span class="color-dot" style="background:#10b981"></span> Normal / Compliant</span>
      </div>
      <h4>Edge Colors</h4>
      <p style="font-size:0.7rem;color:#64748b;margin:0.15rem 0 0.4rem;line-height:1.4">Hues follow relationship type (hover an edge for full detail). Violations stay solid red.</p>
      <div class="color-coding">
        <span><span class="color-dot" style="background:#dc2626"></span> Violation</span>
        <span><span class="color-dot" style="background:#eab308"></span> Drugs / treatment</span>
        <span><span class="color-dot" style="background:#94a3b8"></span> Other</span>
      </div>
    </div>
  </aside>
  <button class="toggle-sidebar" id="toggleSidebar" onclick="document.querySelector('.dashboard-sidebar').classList.toggle('collapsed'); this.classList.toggle('collapsed');">&#9666;</button>
  <div class="dashboard-main">
    <div class="filter-bar">
      <label>Doctor</label><select id="filterDoctor" onchange="applyFilter()"><option value="">All</option></select>
      <select id="filterPatient" style="display:none;"><option value="">All</option></select>
      <label>Disease</label><select id="filterDisease" onchange="applyFilter()"><option value="">All</option></select>
      <label>Compliance</label><select id="filterCompliance" onchange="applyFilter()"><option value="">All</option><option value="violation">Violations only</option><option value="compliant">Compliant only</option></select>
      <label>Hospital</label><select id="filterHospital" onchange="applyFilter()"><option value="">All</option></select>
      <button type="button" onclick="applyFilter()">Apply</button>
      <button type="button" onclick="resetFilter()">Reset</button>
      <button type="button" class="compare-btn" onclick="openCompareModal()">Compare Patients</button>
      <button type="button" class="benchmark-btn" id="benchmarkRunBtn" onclick="runBenchmark()">Run Benchmark</button>
      <span id="filterLabel">Showing: All</span>
    </div>
    MYNETWORK_PLACEHOLDER
    <div class="explain-panel empty" id="explainPanel">
      <h4 id="explainTitle">Protocol explanation</h4>
      <div id="explainContent"></div>
    </div>
    <div class="compare-results-panel" id="compareResults" style="display:none;">
      <div class="compare-results-header">
        <h4>Patient Comparison</h4>
        <button onclick="closeComparison()">Close Comparison</button>
      </div>
      <div class="compare-results-body" id="compareResultsBody"></div>
    </div>
  </div>
</div>
<script>
  var DASHBOARD_STATS = """ + stats_json + """;
  var EXPLANATIONS = """ + expl_json + """;
  function initDashboard() {
    document.getElementById('statPatients').textContent = 'Total patients: ' + (DASHBOARD_STATS.total_patients || 0);
    document.getElementById('statViolations').textContent = 'Violations: ' + (DASHBOARD_STATS.total_violations || 0);
    var docLines = (DASHBOARD_STATS.doctor_compliance_scores || []).map(function(d) { return d.doctor_name + ' ' + d.compliance_score + '%'; });
    document.getElementById('statDoctors').textContent = 'Doctor compliance: ' + (docLines.length ? docLines.join('; ') : '—');
    var vList = document.getElementById('violationsList');
    vList.innerHTML = '';
    (DASHBOARD_STATS.violations_list || []).forEach(function(v) {
      var detail = v.violations_detail || [];
      var li = document.createElement('li');
      li.style.marginBottom = '0.625rem';
      var html = '<strong>' + (v.patient_name || v.patient_id) + '</strong> / ' + (v.disease_name || v.disease_id) + ':';
      if (detail.length) {
        detail.forEach(function(d) {
          var sev = d.severity || 'warning';
          html += '<br><span class="sev-badge ' + sev + '">' + sev + '</span>' + (d.text || '');
          if (d.reason) html += '<span class="violation-reason">' + d.reason + '</span>';
        });
      } else if (v.violations && v.violations.length) {
        html += ' ' + v.violations.join('; ');
      } else {
        html += ' —';
      }
      li.innerHTML = html;
      vList.appendChild(li);
    });
    if (!(DASHBOARD_STATS.violations_list || []).length) {
      var li = document.createElement('li');
      li.textContent = 'None';
      vList.appendChild(li);
    }
    var nodeTypes = """ + json.dumps(NODE_TYPES_LIST) + """;
    var nodeColors = """ + json.dumps(NODE_COLORS) + """;
    var ul = document.getElementById('nodeLegend');
    nodeTypes.forEach(function(t) { var li = document.createElement('li'); li.innerHTML = '<span style="background:' + (nodeColors[t] || '#888') + '"></span>' + t; ul.appendChild(li); });
    var edgeTypes = """ + json.dumps(EDGE_TYPES_LIST) + """;
    var ulE = document.getElementById('edgeLegend');
    edgeTypes.forEach(function(t) { var li = document.createElement('li'); li.textContent = t; ulE.appendChild(li); });
  }
  function getNet() {
    var n = (typeof window !== 'undefined' && window.network && window.network.body) ? window.network : (typeof network !== 'undefined' && network && network.body ? network : null);
    return n || null;
  }
  function showExplanation(nodeId) {
    var net = getNet(); if (!net) return;
    var nodeData = net.body.data.nodes.get(nodeId);
    if (!nodeData) { document.getElementById('explainPanel').classList.add('empty'); return; }
    var displayName = nodeData.full_label || nodeData.label;
    var expl = nodeData.id_prop ? EXPLANATIONS[nodeData.id_prop] : null;
    var panel = document.getElementById('explainPanel');
    var title = document.getElementById('explainTitle');
    var content = document.getElementById('explainContent');
    panel.classList.remove('empty');
    if (expl && (expl.text || expl.name)) {
      title.textContent = (expl.name || displayName) + ' — Protocol explanation';
      var html = (expl.text || '').replace(/\\n/g, '<br>');
      if (expl.references) html += '<br><small>Refs: ' + expl.references + '</small>';
      content.innerHTML = html || 'No explanation available.';
    } else {
      title.textContent = displayName + ' — Info';
      content.innerHTML = 'Node type: <strong>' + (nodeData.node_type || '') + '</strong>. Click a Disease, Drug, or Procedure node for protocol explanation (why recommended).';
    }
    if (nodeData.node_type === 'Patient' && nodeData.id_prop) {
      content.innerHTML += '<button type="button" class="view-timeline-btn" onclick="openTimeline(\\'' + nodeData.id_prop + '\\')">'
        + '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/></svg>'
        + 'View Timeline</button>';
      renderPatientSummary(nodeData.id_prop);
      renderInsightPanel(nodeData.id_prop);
    }
  }
  function getConnectedNodeIds(startId) {
    var net = getNet(); if (!net) return [];
    var seen = {}; var stack = [startId]; seen[startId] = true;
    var edges = net.body.data.edges.get();
    while (stack.length) {
      var id = stack.pop();
      edges.forEach(function(e) {
        var other = e.from === id ? e.to : (e.to === id ? e.from : null);
        if (other && !seen[other]) { seen[other] = true; stack.push(other); }
      });
    }
    return Object.keys(seen);
  }
  function neighborIdSet(centerId) {
    var net = getNet(); if (!net) return {};
    var keep = {}; keep[centerId] = true;
    net.body.data.edges.get().forEach(function(e) {
      if (e.from === centerId) keep[e.to] = true;
      else if (e.to === centerId) keep[e.from] = true;
    });
    return keep;
  }
  function applyEgoHighlight(centerId) {
    var net = getNet(); if (!net || centerId == null) return;
    var keep = neighborIdSet(centerId);
    net.body.data.nodes.getIds().forEach(function(id) {
      net.body.data.nodes.update({ id: id, opacity: keep[id] ? 1 : 0.14 });
    });
    net.body.data.edges.get().forEach(function(e) {
      var lit = !!(keep[e.from] && keep[e.to]);
      net.body.data.edges.update({ id: e.id, opacity: lit ? 0.9 : 0.06 });
    });
    window._egoActive = true;
    updatePatientSelectionVisuals();
  }
  function clearEgoHighlight() {
    var net = getNet(); if (!net || !window._egoActive) return;
    net.body.data.nodes.getIds().forEach(function(id) {
      net.body.data.nodes.update({ id: id, opacity: 1 });
    });
    net.body.data.edges.get().forEach(function(e) {
      net.body.data.edges.update({ id: e.id, opacity: 1 });
    });
    window._egoActive = false;
    updatePatientSelectionVisuals();
  }
  var _zoomDetailTimer = null;
  function applyZoomProgressiveDetail(scale) {
    var net = getNet(); if (!net || !net.body.data.nodes) return;
    var detailed = scale >= 0.72;
    var hideEdgeLbl = scale < 0.32;
    net.body.data.nodes.get().forEach(function(nd) {
      var full = nd.full_label != null ? nd.full_label : nd.label;
      var short = nd.short_label != null ? nd.short_label : nd.label;
      var want = detailed ? full : short;
      var pt = nd.node_type === 'Patient';
      var fs = detailed ? (pt ? 18 : 14) : (pt ? 16 : 12);
      if (want !== nd.label || !(nd.font && nd.font.size === fs)) {
        net.body.data.nodes.update({
          id: nd.id,
          label: want,
          font: Object.assign({}, nd.font || {}, { size: fs, face: 'Inter, system-ui, sans-serif', strokeWidth: 2, strokeColor: 'rgba(255,255,255,0.9)' })
        });
      }
    });
    try {
      net.setOptions({
        edges: {
          font: {
            size: hideEdgeLbl ? 0 : Math.max(8, Math.min(11, Math.round(6 + scale * 8))),
            color: '#64748b',
            face: 'Inter, system-ui, sans-serif'
          }
        }
      });
    } catch (e) {}
  }
  function onGraphZoom(params) {
    var sc = params && params.scale != null ? params.scale : 1;
    if (_zoomDetailTimer) clearTimeout(_zoomDetailTimer);
    _zoomDetailTimer = setTimeout(function() { applyZoomProgressiveDetail(sc); }, 100);
  }
  function toNodeArray(raw) { return Array.isArray(raw) ? raw : (raw ? Object.keys(raw).map(function(k) { return raw[k]; }) : []); }
  function applyFilter() {
    var net = getNet();
    if (!net || !net.body || !net.body.data) {
      alert('Graph not ready. Please refresh the page.');
      return;
    }
    try { clearEgoHighlight(); } catch (e) {}
    if (!window._allNodes || !window._allNodes.length) {
      window._allNodes = toNodeArray(net.body.data.nodes.get());
      window._allEdges = toNodeArray(net.body.data.edges.get());
    }
    var allNodes = window._allNodes;
    var allEdges = window._allEdges;
    if (!allNodes || !allNodes.length) return;
    var doctor = document.getElementById('filterDoctor').value;
    var patient = document.getElementById('filterPatient').value;
    var disease = document.getElementById('filterDisease').value;
    var compliance = document.getElementById('filterCompliance').value;
    var hospital = document.getElementById('filterHospital').value;
    var nodeMap = {};
    allNodes.forEach(function(n) { nodeMap[n.id] = n; });
    var visibleIds = {};
    if (!doctor && !patient && !disease && !compliance && !hospital) {
      allNodes.forEach(function(n) { visibleIds[n.id] = true; });
    } else {
      var seedIds = [];
      allNodes.forEach(function(n) {
        if (doctor && n.id_prop === doctor) seedIds.push(n.id);
        if (patient && n.id_prop === patient) seedIds.push(n.id);
        if (disease && n.id_prop === disease) seedIds.push(n.id);
        if (hospital && n.id_prop === hospital) seedIds.push(n.id);
      });
      seedIds = seedIds.filter(function(id, i, a) { return a.indexOf(id) === i; });
      if (seedIds.length) {
        var edges = window._allEdges || toNodeArray(net.body.data.edges.get());
        // When filtering by doctor: only include that doctor and their patients (don't follow into other doctors' patients)
        var allowPatient = null;
        if (doctor && !patient && !disease && !hospital) {
          var doctorId = seedIds[0];
          allowPatient = {};
          edges.forEach(function(e) {
            var a = e.from === doctorId ? e.to : (e.to === doctorId ? e.from : null);
            if (a && nodeMap[a] && nodeMap[a].node_type === 'Patient') allowPatient[a] = true;
          });
          seedIds = [doctorId].concat(Object.keys(allowPatient));
        }
        // When filtering by patient: only that patient's subgraph (don't add other patients)
        if (patient && !doctor && !disease && !hospital) {
          allowPatient = {}; allowPatient[seedIds[0]] = true;
        }
        // When filtering by hospital: only this hospital's subgraph (don't add other hospitals)
        var allowHospital = null;
        if (hospital && !doctor && !patient && !disease) {
          allowHospital = {}; seedIds.forEach(function(id) { allowHospital[id] = true; });
        }
        // When filtering by disease: only this disease's subgraph (don't add other diseases)
        var allowDisease = null;
        if (disease && !doctor && !patient && !hospital) {
          allowDisease = {}; seedIds.forEach(function(id) { allowDisease[id] = true; });
        }
        seedIds.forEach(function(startId) {
          var seen = {}; var stack = [startId]; seen[startId] = true;
          while (stack.length) {
            var cur = stack.pop();
            edges.forEach(function(e) {
              var other = e.from === cur ? e.to : (e.to === cur ? e.from : null);
              if (!other || seen[other]) return;
              var node = nodeMap[other];
              if (allowPatient !== null && node && node.node_type === 'Patient' && !allowPatient[other]) return;
              if (allowHospital !== null && node && node.node_type === 'Hospital' && !allowHospital[other]) return;
              if (allowDisease !== null && node && node.node_type === 'Disease' && !allowDisease[other]) return;
              seen[other] = true; stack.push(other);
            });
          }
          Object.keys(seen).forEach(function(x) { visibleIds[x] = true; });
        });
      } else {
        allNodes.forEach(function(n) { visibleIds[n.id] = true; });
      }
    }
    function isRedEdge(e) {
      var c = e.color;
      var s = (typeof c === 'string') ? c : (c && c.color);
      if (!s) return false;
      if (s === '#dc2626' || s === '#cc0000') return true;
      return typeof s === 'string' && (s.indexOf('220,38,38') >= 0 || s.indexOf('239,68,68') >= 0);
    }
    allNodes.forEach(function(n) {
      var show = !!visibleIds[n.id];
      if (compliance === 'violation') {
        var hasViolation = allEdges.some(function(e) { return (e.from === n.id || e.to === n.id) && isRedEdge(e); });
        if (!hasViolation) show = false;
      } else if (compliance === 'compliant') {
        var hasViolation = allEdges.some(function(e) { return (e.from === n.id || e.to === n.id) && isRedEdge(e); });
        if (hasViolation) show = false;
      }
      if (!show) delete visibleIds[n.id];
    });
    var filteredNodes = allNodes.filter(function(n) { return visibleIds[n.id]; });
    var filteredEdges = allEdges.filter(function(e) { return visibleIds[e.from] && visibleIds[e.to]; });
    try {
      var lb = document.getElementById('loadingBar');
      if (lb) { lb.style.display = 'none'; lb.style.opacity = '0'; }
      var newNodes = new vis.DataSet(filteredNodes);
      var newEdges = new vis.DataSet(filteredEdges);
      net.setData({ nodes: newNodes, edges: newEdges });
      window._currentNodes = newNodes;
      window._currentEdges = newEdges;
      var lab = document.getElementById('filterLabel');
      if (lab) {
        var parts = [];
        var d = document.getElementById('filterDoctor');
        if (doctor && d && d.options[d.selectedIndex]) parts.push(d.options[d.selectedIndex].text);
        var p = document.getElementById('filterPatient');
        if (patient && p && p.options[p.selectedIndex]) parts.push(p.options[p.selectedIndex].text);
        var dis = document.getElementById('filterDisease');
        if (disease && dis && dis.options[dis.selectedIndex]) parts.push(dis.options[dis.selectedIndex].text);
        var h = document.getElementById('filterHospital');
        if (hospital && h && h.options[h.selectedIndex]) parts.push(h.options[h.selectedIndex].text);
        if (compliance === 'violation') parts.push('Violations only');
        if (compliance === 'compliant') parts.push('Compliant only');
        lab.textContent = parts.length ? 'Showing: ' + parts.join(', ') : 'Showing: All';
      }
      // Re-enable physics so the filtered subgraph lays out correctly (nodes spread out)
      try {
        net.setOptions({
          physics: {
            enabled: true,
            solver: 'forceAtlas2Based',
            forceAtlas2Based: {
              theta: 0.55,
              gravitationalConstant: -92,
              centralGravity: 0.011,
              springLength: 268,
              springConstant: 0.058,
              damping: 0.52,
              avoidOverlap: 0.82
            },
            maxVelocity: 42,
            minVelocity: 2,
            timestep: 0.52,
            stabilization: { enabled: true, iterations: 220, updateInterval: 25 }
          }
        });
      } catch(e) {}
      function onStabilized() {
        net.off('stabilizationIterationsDone', onStabilized);
        try { net.setOptions({ physics: { enabled: false } }); } catch(e) {}
        try { if (net.fit) net.fit({ animation: { duration: 300 } }); } catch(e) {}
      }
      net.once('stabilizationIterationsDone', onStabilized);
      setTimeout(function() { try { if (net.fit) net.fit({ animation: { duration: 300 } }); } catch(e) {} }, 500);
    } catch(err) {
      console.error('Filter error', err);
      alert('Filter failed: ' + (err.message || err));
    }
  }
  function resetFilter() {
    document.getElementById('filterDoctor').value = '';
    document.getElementById('filterPatient').value = '';
    document.getElementById('filterDisease').value = '';
    document.getElementById('filterCompliance').value = '';
    document.getElementById('filterHospital').value = '';
    var lab = document.getElementById('filterLabel');
    if (lab) lab.textContent = 'Showing: All';
    var net = getNet();
    if (!net || !window._allNodes || !window._allEdges) return;
    try { clearEgoHighlight(); } catch (e) {}
    try {
      var lb = document.getElementById('loadingBar');
      if (lb) { lb.style.display = 'none'; lb.style.opacity = '0'; }
      var fullNodes = new vis.DataSet(window._allNodes);
      var fullEdges = new vis.DataSet(window._allEdges);
      net.setData({ nodes: fullNodes, edges: fullEdges });
      window._currentNodes = fullNodes;
      window._currentEdges = fullEdges;
      // Re-enable physics so the full graph lays out correctly
      try {
        net.setOptions({
          physics: {
            enabled: true,
            solver: 'forceAtlas2Based',
            forceAtlas2Based: {
              theta: 0.55,
              gravitationalConstant: -92,
              centralGravity: 0.011,
              springLength: 268,
              springConstant: 0.058,
              damping: 0.52,
              avoidOverlap: 0.82
            },
            maxVelocity: 42,
            minVelocity: 2,
            timestep: 0.52,
            stabilization: { enabled: true, iterations: 220, updateInterval: 25 }
          }
        });
      } catch(e) {}
      function onStabilized() {
        net.off('stabilizationIterationsDone', onStabilized);
        try { net.setOptions({ physics: { enabled: false } }); } catch(e) {}
        try { if (net.fit) net.fit({ animation: { duration: 300 } }); } catch(e) {}
      }
      net.once('stabilizationIterationsDone', onStabilized);
      setTimeout(function() { try { if (net.fit) net.fit({ animation: { duration: 300 } }); } catch(e) {} }, 500);
    } catch(err) {
      console.error('Reset error', err);
    }
  }
  function populateFilterOptions() {
    var net = getNet();
    if (!net || !net.body || !net.body.data) return;
    var nodes = window._allNodes || toNodeArray(net.body.data.nodes.get());
    var doctors = {}, patients = {}, diseases = {}, hospitals = {};
    nodes.forEach(function(n) {
      var disp = n.full_label || n.label;
      if (n.node_type === 'Doctor') doctors[n.id_prop] = disp;
      if (n.node_type === 'Patient') patients[n.id_prop] = disp;
      if (n.node_type === 'Disease') diseases[n.id_prop] = disp;
      if (n.node_type === 'Hospital') hospitals[n.id_prop] = disp;
    });
    function fillSelect(id, map) {
      var sel = document.getElementById(id);
      while (sel.options.length > 1) sel.remove(1);
      Object.keys(map).sort().forEach(function(k) { var o = document.createElement('option'); o.value = k; o.textContent = map[k]; sel.appendChild(o); });
    }
    fillSelect('filterDoctor', doctors);
    fillSelect('filterPatient', patients);
    fillSelect('filterDisease', diseases);
    fillSelect('filterHospital', hospitals);
  }
  /* ---- Patient-Aware AI: selection tracking ---- */
  var _aiSelectedPatients = [];

  function togglePatientSelection(nodeId) {
    var allN = window._allNodes || [];
    var nodeData = null;
    allN.forEach(function(n) { if (n.id === nodeId) nodeData = n; });
    if (!nodeData || nodeData.node_type !== 'Patient') return false;
    var pid = nodeData.id_prop || nodeData.id;
    var nm = nodeData.full_label || nodeData.label || pid;
    var idx = -1;
    _aiSelectedPatients.forEach(function(p, i) { if (p.pid === pid) idx = i; });
    if (idx >= 0) {
      _aiSelectedPatients.splice(idx, 1);
    } else {
      _aiSelectedPatients.push({ pid: pid, name: nm, visId: nodeId });
    }
    renderAiContextBar();
    updatePatientSelectionVisuals();
    return true;
  }

  function removePatientFromAi(pid) {
    _aiSelectedPatients = _aiSelectedPatients.filter(function(p) { return p.pid !== pid; });
    renderAiContextBar();
    updatePatientSelectionVisuals();
  }

  function clearAiPatients() {
    _aiSelectedPatients = [];
    renderAiContextBar();
    updatePatientSelectionVisuals();
    resetAiQuestion();
  }

  function renderAiContextBar() {
    var bar = document.getElementById('aiContextBar');
    if (!bar) return;
    if (!_aiSelectedPatients.length) {
      bar.innerHTML = '<span class="ai-ctx-hint">Click a patient node to add context for AI</span>';
      return;
    }
    var html = '<span class="ai-ctx-label">AI context:</span>';
    _aiSelectedPatients.forEach(function(p) {
      html += '<span class="ai-ctx-chip">' + p.name + ' (' + p.pid + ')';
      html += '<span class="ctx-remove" onclick="event.stopPropagation();removePatientFromAi(\\'' + p.pid + '\\')">&times;</span></span>';
    });
    html += '<span class="ai-ctx-clear" onclick="clearAiPatients()">clear all</span>';
    bar.innerHTML = html;
  }

  function updatePatientSelectionVisuals() {
    var net = getNet();
    if (!net || !net.body || !net.body.data) return;
    var nodes = net.body.data.nodes;
    var selectedPids = {};
    _aiSelectedPatients.forEach(function(p) { selectedPids[p.pid] = true; });
    var updates = [];
    (window._allNodes || []).forEach(function(n) {
      if (n.node_type !== 'Patient') return;
      var pid = n.id_prop || n.id;
      var existing = nodes.get(n.id);
      if (!existing) return;
      if (selectedPids[pid]) {
        updates.push({ id: n.id, borderWidth: 3, shapeProperties: { borderDashes: false },
          color: { background: existing.color && existing.color.background || '#60a5fa', border: '#2563eb' } });
      } else {
        var orig = null;
        (window._allNodes || []).forEach(function(o) { if (o.id === n.id) orig = o; });
        if (orig) updates.push({ id: n.id, borderWidth: orig.borderWidth || 1, color: orig.color });
      }
    });
    if (updates.length) nodes.update(updates);
  }

  window.removePatientFromAi = removePatientFromAi;
  window.clearAiPatients = clearAiPatients;

  var lastAiResponse = null;
  function askAi() {
    var q = (document.getElementById('aiQuestion') && document.getElementById('aiQuestion').value || '').trim();
    if (!q) {
      alert('Please type a question (e.g. "Did patient P1 follow the diabetes protocol?")');
      return;
    }
    var apiUrl = (document.getElementById('aiApiUrl') && document.getElementById('aiApiUrl').value || 'http://localhost:8000').replace(/\\/$/, '');
    var resultEl = document.getElementById('aiResult');
    var loadingEl = document.getElementById('aiLoading');
    var btn = document.getElementById('aiAskBtn');
    resultEl.style.display = 'none';
    loadingEl.style.display = 'block';
    if (btn) btn.disabled = true;
    var payload = { question: q };
    if (_aiSelectedPatients.length) {
      payload.selected_patients = _aiSelectedPatients.map(function(p) { return p.pid; });
    }
    fetch(apiUrl + '/ask-agent', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    }).then(function(r) {
      if (!r.ok) throw new Error('API error: ' + r.status);
      return r.json();
    }).then(function(data) {
      lastAiResponse = data;
      var answerEl = document.getElementById('aiAnswer');
      var badgeEl = document.getElementById('aiViolationBadge');
      var metaEl = document.getElementById('aiMeta');
      var highlightBtn = null;
      if (answerEl) {
        answerEl.classList.remove('error');
        var raw = data.answer || 'No answer.';
        answerEl.innerHTML = buildStructuredAiHtml(raw, data);
        answerEl.classList.add('ai-answer-smart');
      }
      if (metaEl) metaEl.classList.remove('error');
      if (badgeEl) {
        badgeEl.style.display = 'inline-flex';
        badgeEl.textContent = data.violation ? 'Protocol violation' : 'Compliant';
        badgeEl.className = 'violation-badge ' + (data.violation ? 'yes' : 'no');
      }
      var meta = [];
      if ((data.protocol_expected || []).length) meta.push('Expected: ' + data.protocol_expected.join(', '));
      if ((data.actual_treatment || []).length) meta.push('Actual: ' + data.actual_treatment.join(', '));
      if (metaEl) metaEl.innerHTML = meta.join('<br/>');
      if (highlightBtn) highlightBtn.style.display = ((data.highlight_nodes || []).length || (data.paths || []).length) ? 'inline-block' : 'none';
      renderAiBasedOn(data);
      resultEl.style.display = 'block';
      if ((data.highlight_nodes || []).length || (data.paths || []).length) highlightFromAi();
    }).catch(function(err) {
      lastAiResponse = null;
      var answerEl = document.getElementById('aiAnswer');
      if (answerEl) { answerEl.textContent = 'Could not reach the AI. Is the API running? Start it with: uvicorn api_server:app --reload'; answerEl.classList.add('error'); }
      document.getElementById('aiViolationBadge').style.display = 'none';
      document.getElementById('aiMeta').textContent = err.message || 'Check the API URL (e.g. http://localhost:8000) and try again.';
      document.getElementById('aiMeta').classList.add('error');
      try { document.getElementById('aiHighlightBtn') && (document.getElementById('aiHighlightBtn').style.display = 'none'); } catch(e) {}
      resultEl.style.display = 'block';
    }).then(function() {
      loadingEl.style.display = 'none';
      if (btn) btn.disabled = false;
    });
  }

  function resetAiQuestion() {
    document.getElementById('aiResult').style.display = 'none';
    var abo = document.getElementById('aiBasedOn');
    if (abo) { abo.style.display = 'none'; abo.innerHTML = ''; }
    var answerEl = document.getElementById('aiAnswer');
    if (answerEl) {
      answerEl.innerHTML = '';
      answerEl.classList.remove('error', 'ai-answer-smart');
    }
    var badgeEl = document.getElementById('aiViolationBadge');
    if (badgeEl) {
      badgeEl.style.display = 'none';
      badgeEl.textContent = '';
      badgeEl.className = 'violation-badge';
    }
    var metaEl = document.getElementById('aiMeta');
    if (metaEl) {
      metaEl.innerHTML = '';
      metaEl.classList.remove('error');
    }
    var textarea = document.getElementById('aiQuestion');
    if (textarea) { textarea.value = ''; textarea.focus(); }
    lastAiResponse = null;
    clearAiHighlight();
    filterGraphToPatient();
  }
  window.resetAiQuestion = resetAiQuestion;

  function renderAiBasedOn(data) {
    var el = document.getElementById('aiBasedOn');
    if (!el) return;
    var hNodes = data.highlight_nodes || [];
    if (!hNodes.length && (data.paths || []).length) {
      var seen = {};
      (data.paths || []).forEach(function(p) { (p.nodes || []).forEach(function(n) { seen[n] = true; }); });
      hNodes = Object.keys(seen);
    }
    if (!hNodes.length && !(data.protocol_expected || []).length && !(data.actual_treatment || []).length) {
      el.style.display = 'none'; return;
    }
    var groups = { Patient: [], Disease: [], Symptom: [], Violation: [], Drug: [], ClinicalState: [], other: [] };
    var typeMap = { patient: 'Patient', disease: 'Disease', symptom: 'Symptom', violation: 'Violation', drug: 'Drug', clinicalstate: 'ClinicalState', procedure: 'other', recommendedaction: 'other', sepsisguideline: 'other' };
    var allN = window._allNodes || [];
    hNodes.forEach(function(s) {
      var idx = s.indexOf(':');
      var type = idx >= 0 ? s.substring(0, idx) : '';
      var idProp = idx >= 0 ? s.substring(idx + 1) : s;
      var label = idProp;
      allN.forEach(function(n) { if (n.id_prop === idProp && (!type || (n.node_type || '').toLowerCase() === type.toLowerCase())) label = n.full_label || n.label || idProp; });
      var gk = typeMap[(type || '').toLowerCase()] || 'other';
      groups[gk].push(label);
    });
    var labelMap = { Patient: 'Patients', Disease: 'Diseases', Symptom: 'Symptoms', Violation: 'Violations', Drug: 'Drugs', ClinicalState: 'Clinical States', other: 'Other' };
    var clsMap = { Patient: 'patient', Disease: 'disease', Symptom: 'symptom', Violation: 'violation', Drug: 'drug', ClinicalState: 'clinical', other: 'other' };
    var sections = [];
    Object.keys(groups).forEach(function(k) {
      var items = groups[k];
      items = items.filter(function(v, i, a) { return a.indexOf(v) === i; });
      if (!items.length) return;
      var h = '<div class="abo-section"><div class="abo-label">' + labelMap[k] + '</div><div class="abo-tags">';
      items.forEach(function(lbl) { h += '<span class="abo-tag ' + clsMap[k] + '">' + lbl + '</span>'; });
      h += '</div></div>';
      sections.push(h);
    });
    if ((data.protocol_expected || []).length) {
      var h = '<div class="abo-section"><div class="abo-label">Expected Protocol</div><div class="abo-tags">';
      data.protocol_expected.forEach(function(x) { h += '<span class="abo-tag other">' + x + '</span>'; });
      h += '</div></div>';
      sections.push(h);
    }
    if ((data.actual_treatment || []).length) {
      var h = '<div class="abo-section"><div class="abo-label">Actual Treatment</div><div class="abo-tags">';
      data.actual_treatment.forEach(function(x) { h += '<span class="abo-tag drug">' + x + '</span>'; });
      h += '</div></div>';
      sections.push(h);
    }
    if (!sections.length) { el.style.display = 'none'; return; }
    var uid = 'aboContent';
    el.innerHTML = '<span class="ai-based-on-toggle" onclick="var c=document.getElementById(\\'' + uid + '\\');c.classList.toggle(\\'open\\');this.classList.toggle(\\'open\\')">'
      + '<span class="toggle-arrow">\\u25b6</span> Based on ' + hNodes.length + ' data points</span>'
      + '<div class="ai-based-on-content" id="' + uid + '">' + sections.join('') + '</div>';
    el.style.display = 'block';
  }

  function _escHtml(t) { return (t || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); }
  function _mdInline(s) {
    s = s.replace(/\\*\\*(.+?)\\*\\*/g, '<strong>$1</strong>');
    s = s.replace(/^### (.+)$/gm, '<strong>$1</strong>');
    s = s.replace(/^## (.+)$/gm, '<strong>$1</strong>');
    return s;
  }

  function _countMatchingDataPoints(data) {
    var seen = {};
    (data.highlight_nodes || []).forEach(function(s) { if (s) seen[String(s)] = true; });
    (data.paths || []).forEach(function(p) {
      (p && p.nodes ? p.nodes : []).forEach(function(n) { if (n) seen[String(n)] = true; });
    });
    var graphCount = Object.keys(seen).length;
    var clinicalFacts = (data.protocol_expected || []).length + (data.actual_treatment || []).length;
    return graphCount + clinicalFacts;
  }

  function _computeConfidence(data) {
    var pts = _countMatchingDataPoints(data);
    if (pts >= 10) return { level: 'high', label: 'High', score: pts };
    if (pts >= 4) return { level: 'medium', label: 'Medium', score: pts };
    if (pts >= 1) return { level: 'low', label: 'Low', score: pts };
    return { level: 'low', label: 'Low', score: 0 };
  }

  function _isComparisonResponse(text) {
    var lower = (text || '').toLowerCase();
    return (lower.indexOf('common finding') >= 0 || lower.indexOf('both patients') >= 0 || lower.indexOf('shared') >= 0)
      && (lower.indexOf('differ') >= 0 || lower.indexOf('versus') >= 0 || lower.indexOf('unique') >= 0 || lower.indexOf('contrast') >= 0);
  }

  function _detectSectionHeader(line) {
    var t = line.replace(/^#+\\s*/, '').replace(/^\\*{1,2}\\s*/, '').replace(/\\*{1,2}$/g, '').trim();
    var core = t.toLowerCase().replace(/\\s*[：:]\\s*$/, '').replace(/^[*•]\\s*/, '').trim();
    if (/^conclusion\\b/.test(core)) return 'conclusion';
    if (/^evidence\\b/.test(core)) return 'evidence';
    if (/^explanation\\b/.test(core) || /^rationale\\b/.test(core)) return 'explanation';
    if (/^common\\s+findings?\\b/.test(core) || /^similarities\\b/.test(core)) return 'common';
    if (/^differences?\\b/.test(core) || /^contrasts?\\b/.test(core)) return 'diff';
    return null;
  }

  function _parseStructured(rawText) {
    var lines = rawText.split('\\n');
    var conclusion = '';
    var evidence = [];
    var explanation = [];
    var commonFindings = [];
    var differences = [];
    var phase = 'scan';

    lines.forEach(function(line) {
      var t = line.trim();
      if (!t) return;
      var hdr = _detectSectionHeader(t);
      if (hdr) { phase = hdr; return; }

      var lower = t.toLowerCase();
      if (phase === 'scan') {
        if ((/^common\\b/i.test(t) && t.length < 96 && (lower.indexOf('finding') >= 0 || lower.indexOf('ality') >= 0))
          || /^similarities\\b/i.test(t)) {
          phase = 'common'; return;
        }
        if (/^differences?\\b/i.test(t) && t.length < 96) { phase = 'diff'; return; }

        var isBullet = /^[•\\-*]/.test(t) || /^\\d+[.)]\\s/.test(t);
        if (isBullet) {
          var eb = t.replace(/^[•\\-*]+\\s*/, '').replace(/^\\d+[.)]\\s*/, '').trim();
          if (eb) evidence.push(eb);
        } else if (!conclusion && t.length > 12 && !lower.startsWith('##') && !/^based\\s+on\\b/.test(lower)) {
          conclusion = t;
        } else {
          explanation.push(t);
        }
        return;
      }

      if (phase === 'conclusion') {
        conclusion = conclusion ? conclusion + ' ' + t : t;
        return;
      }
      if (phase === 'evidence') {
        var evb = t.replace(/^[•\\-*\\d.)]+\\s*/, '').trim();
        if (evb) evidence.push(evb);
        return;
      }
      if (phase === 'explanation') {
        explanation.push(t);
        return;
      }
      if (phase === 'common') {
        var cb = t.replace(/^[•\\-*\\d.)]+\\s*/, '').trim();
        if (cb) commonFindings.push(cb);
        return;
      }
      if (phase === 'diff') {
        var db = t.replace(/^[•\\-*\\d.)]+\\s*/, '').trim();
        if (db) differences.push(db);
        return;
      }
    });

    if (!conclusion && evidence.length) conclusion = evidence.shift();
    if (!conclusion && explanation.length) conclusion = explanation.shift();

    return {
      conclusion: conclusion || '',
      evidence: evidence,
      explanation: explanation.join('\\n').trim(),
      commonFindings: commonFindings,
      differences: differences
    };
  }

  function _buildEvidenceList(parsed, data) {
    var ev = parsed.evidence.slice();
    var seen = {};
    ev.forEach(function(e) { seen[String(e).toLowerCase()] = true; });
    (data.protocol_expected || []).forEach(function(x) {
      var line = 'Protocol expectation: ' + x;
      if (!seen[line.toLowerCase()]) { ev.push(line); seen[line.toLowerCase()] = true; }
    });
    (data.actual_treatment || []).forEach(function(x) {
      var line = 'Documentation / actual: ' + x;
      if (!seen[line.toLowerCase()]) { ev.push(line); seen[line.toLowerCase()] = true; }
    });
    var pts = _countMatchingDataPoints(data);
    if (!ev.length) {
      if (pts > 0) {
        ev.push('Retrieved context includes ' + pts + ' matching data point(s) from the clinical graph (nodes, paths, or protocol artifacts).');
      } else if (parsed.conclusion) {
        ev.push('No discrete graph-backed items were enumerated; synthesis reflects the narrative reply only.');
      } else {
        ev.push('No structured evidence lines were available for this response.');
      }
    }
    return ev;
  }

  function buildStructuredAiHtml(rawText, data) {
    var parsed = _parseStructured(_escHtml(rawText || ''));
    var dataPoints = _countMatchingDataPoints(data);
    var conf = _computeConfidence(data);
    var displayConf = dataPoints === 0 ? { level: 'low', label: 'Low', score: 0 } : conf;
    var evidenceList = _buildEvidenceList(parsed, data);
    var explanationText = parsed.explanation && parsed.explanation.trim();
    if (!explanationText) {
      explanationText = 'Interpretation synthesizes the conclusion and evidence above in line with standard clinical documentation review.';
    }

    var multiPatient = data.selected_patients && data.selected_patients.length > 1;
    var isComp = multiPatient || _isComparisonResponse(rawText || '')
      || parsed.commonFindings.length || parsed.differences.length;

    var conclusionBody = parsed.conclusion || (rawText ? _escHtml(rawText).replace(/\\n/g, ' ').trim().substring(0, 800) : '');
    if (!conclusionBody) conclusionBody = 'No narrative conclusion was returned.';

    var html = '<div class="ai-structured ai-clinical-response">';

    if (dataPoints === 0) {
      html += '<div class="ai-response-card ai-insufficient-card">';
      html += '<div class="ai-insufficient-title">Insufficient data to provide a confident conclusion</div>';
      html += '<p class="ai-insufficient-detail">No matching clinical data points were retrieved for this query. Select patients on the graph or narrow the question for a data-grounded assessment.</p>';
      html += '</div>';
    }

    html += '<div class="ai-response-card">';
    html += '<div class="ai-section-label"><span class="section-icon">\\u2192</span> Conclusion';
    html += '<span class="ai-confidence ' + displayConf.level + '"><span class="conf-dot"></span>' + displayConf.label + '</span>';
    html += '</div>';
    html += '<div class="ai-section"><div class="ai-conclusion">' + _mdInline(conclusionBody) + '</div></div>';
    html += '</div>';

    if (isComp) {
      html += '<div class="ai-response-card">';
      html += '<div class="ai-section-label"><span class="section-icon">\\u2194</span> Multi-patient comparison</div>';
      html += '<div class="ai-comparison-grid">';
      html += '<div class="ai-comparison-col common"><h6>Common Findings</h6>';
      if (parsed.commonFindings.length) {
        parsed.commonFindings.forEach(function(f) { html += '&bull; ' + _mdInline(f) + '<br>'; });
      } else {
        html += '<span class="ai-placeholder">None parsed from the reply' + (multiPatient ? '; see conclusion and explanation.' : '.') + '</span>';
      }
      html += '</div>';
      html += '<div class="ai-comparison-col diff"><h6>Differences</h6>';
      if (parsed.differences.length) {
        parsed.differences.forEach(function(f) { html += '&bull; ' + _mdInline(f) + '<br>'; });
      } else {
        html += '<span class="ai-placeholder">None parsed from the reply' + (multiPatient ? '; see conclusion and explanation.' : '.') + '</span>';
      }
      html += '</div></div></div>';
    }

    html += '<div class="ai-response-card">';
    html += '<div class="ai-section-label"><span class="section-icon">\\u2022</span> Evidence</div>';
    html += '<div class="ai-section"><ul class="ai-evidence">';
    evidenceList.forEach(function(e) { html += '<li>' + _mdInline(e) + '</li>'; });
    html += '</ul></div></div>';

    html += '<div class="ai-response-card">';
    html += '<div class="ai-section-label"><span class="section-icon">\\u24d8</span> Explanation</div>';
    html += '<div class="ai-section"><div class="ai-explanation">' + _mdInline(explanationText) + '</div></div>';
    html += '</div>';

    html += '</div>';
    return html;
  }
  var _aiHighlightActive = false;

  function highlightFromAi() {
    if (!lastAiResponse) return;
    var net = getNet();
    if (!net || !net.body || !net.body.data) return;
    var nodeDS = net.body.data.nodes;
    var edgeDS = net.body.data.edges;
    if (!nodeDS || !edgeDS) return;
    var allNodes = window._allNodes;
    var allEdges = window._allEdges;
    if (!allNodes || !allNodes.length) {
      allNodes = toNodeArray(nodeDS.get());
      allEdges = toNodeArray(edgeDS.get());
      window._allNodes = allNodes;
      window._allEdges = allEdges;
    }

    var hNodes = lastAiResponse.highlight_nodes || [];
    if (!hNodes.length && (lastAiResponse.paths || []).length) {
      var seen = {};
      (lastAiResponse.paths || []).forEach(function(p) {
        (p.nodes || []).forEach(function(n) { seen[n] = true; });
      });
      hNodes = Object.keys(seen);
    }
    if (!hNodes.length) return;

    var matchedIds = {};
    hNodes.forEach(function(s) {
      var idx = s.indexOf(':');
      var type = idx >= 0 ? s.substring(0, idx) : null;
      var idProp = idx >= 0 ? s.substring(idx + 1) : s;
      allNodes.forEach(function(n) {
        if (n.id_prop === idProp && (!type || (n.node_type || '').toLowerCase() === (type || '').toLowerCase())) matchedIds[n.id] = true;
      });
    });
    var matchCount = Object.keys(matchedIds).length;
    if (matchCount === 0) return;

    var neighborIds = {};
    allEdges.forEach(function(e) {
      if (matchedIds[e.from] || matchedIds[e.to]) {
        neighborIds[e.from] = true; neighborIds[e.to] = true;
      }
    });

    var nodeUpdates = [];
    allNodes.forEach(function(n) {
      if (matchedIds[n.id]) {
        var bg = (n.color && typeof n.color === 'object') ? n.color.background : (n.color || '#3b82f6');
        nodeUpdates.push({
          id: n.id,
          color: { background: bg, border: '#1e40af', highlight: { background: bg, border: '#1e40af' } },
          font: { color: '#0f172a', size: 14, bold: true },
          size: Math.max((n.size || 16), 22),
          borderWidth: 3,
          shadow: { enabled: true, color: 'rgba(59,130,246,0.4)', size: 14, x: 0, y: 0 }
        });
      } else if (neighborIds[n.id]) {
        nodeUpdates.push({
          id: n.id,
          color: { background: '#e2e8f0', border: '#cbd5e1', highlight: { background: '#e2e8f0', border: '#cbd5e1' } },
          font: { color: '#94a3b8', size: 10 },
          size: Math.max(8, (n.size || 16) * 0.65),
          borderWidth: 0.5, shadow: false
        });
      } else {
        nodeUpdates.push({
          id: n.id,
          color: { background: '#f1f5f9', border: '#e2e8f0', highlight: { background: '#f1f5f9', border: '#e2e8f0' } },
          font: { color: '#cbd5e1', size: 8 },
          size: Math.max(6, (n.size || 16) * 0.5),
          borderWidth: 0, shadow: false
        });
      }
    });

    var edgeUpdates = [];
    allEdges.forEach(function(e) {
      if (matchedIds[e.from] && matchedIds[e.to]) {
        var ec = (e.color && typeof e.color === 'string') ? e.color : ((e.color && e.color.color) || '#3b82f6');
        edgeUpdates.push({ id: e.id, color: { color: ec, highlight: ec }, width: Math.max((e.width || 1) * 1.5, 2) });
      } else if (matchedIds[e.from] || matchedIds[e.to]) {
        edgeUpdates.push({ id: e.id, color: { color: '#cbd5e1', highlight: '#cbd5e1' }, width: 0.5 });
      } else {
        edgeUpdates.push({ id: e.id, color: { color: '#f1f5f9', highlight: '#f1f5f9' }, width: 0.3 });
      }
    });

    var preAiNodes = JSON.parse(JSON.stringify(window._allNodes || []));
    var preAiEdges = JSON.parse(JSON.stringify(window._allEdges || []));

    try {
      nodeDS.update(nodeUpdates);
      edgeDS.update(edgeUpdates);
      net.redraw();
      _aiHighlightActive = true;
      window._aiGraphSnapshotBeforeHighlight = { nodes: preAiNodes, edges: preAiEdges };
      document.getElementById('filterLabel').textContent = 'Showing: AI highlight (' + matchCount + ' nodes)';
      var btn = document.getElementById('aiHighlightBtn');
      if (btn) btn.textContent = 'Reset Graph';
      var graphBox = document.getElementById('mynetwork');
      if (graphBox) {
        graphBox.style.position = 'relative';
        var toast = document.createElement('div');
        toast.className = 'hl-toast';
        toast.textContent = 'Highlighted ' + matchCount + ' nodes';
        graphBox.appendChild(toast);
        setTimeout(function() { toast.style.opacity = '0'; }, 2500);
        setTimeout(function() { if (toast.parentNode) toast.parentNode.removeChild(toast); }, 3200);
      }
    } catch(e) {
      console.error('highlightFromAi:', e);
      window._aiGraphSnapshotBeforeHighlight = null;
    }
  }

  function clearAiHighlight() {
    var fullSnap = window._aiGraphSnapshotBeforeHighlight;
    var shouldRestore = _aiHighlightActive || fullSnap;
    if (!shouldRestore) return;

    if (fullSnap && fullSnap.nodes && fullSnap.edges) {
      window._allNodes = JSON.parse(JSON.stringify(fullSnap.nodes));
      window._allEdges = JSON.parse(JSON.stringify(fullSnap.edges));
      window._aiGraphSnapshotBeforeHighlight = null;
    }

    _aiHighlightActive = false;

    try {
      filterGraphToPatient();
    } catch(e) { console.error('clearAiHighlight filterGraphToPatient:', e); }

    try {
      if (!_patientContext || !_patientContext.selected) {
        document.getElementById('filterLabel').textContent = 'Showing: All';
      }
    } catch(e2) {}

    var btn = document.getElementById('aiHighlightBtn');
    if (btn) btn.textContent = 'Highlight in Graph';
  }

  function toggleAiHighlight() {
    if (_aiHighlightActive) {
      clearAiHighlight();
    } else {
      highlightFromAi();
      var btn = document.getElementById('aiHighlightBtn');
      if (btn) btn.textContent = 'Reset Graph';
    }
  }
  /* ===== Document Upload ===== */
  var _uploadExtractedData = null;
  function openUploadModal() {
    document.getElementById('uploadModal').style.display = 'flex';
    resetUploadUI();
  }
  function closeUploadModal() {
    document.getElementById('uploadModal').style.display = 'none';
    _uploadExtractedData = null;
  }
  function resetUploadUI() {
    _uploadExtractedData = null;
    document.getElementById('uploadDropzone').style.display = 'block';
    document.getElementById('uploadLoading').style.display = 'none';
    document.getElementById('uploadPreview').style.display = 'none';
    document.getElementById('uploadPreview').innerHTML = '';
    document.getElementById('uploadError').style.display = 'none';
    document.getElementById('uploadActions').style.display = 'none';
    var fi = document.getElementById('uploadFileInput');
    if (fi) fi.value = '';
  }
  function handleFileSelect(file) {
    if (!file) return;
    var ext = (file.name.split('.').pop() || '').toLowerCase();
    if (['pdf','txt','text','md'].indexOf(ext) === -1) {
      showUploadError('Unsupported file type. Please upload a PDF or text file.');
      return;
    }
    if (file.size > 10 * 1024 * 1024) {
      showUploadError('File too large. Maximum size is 10 MB.');
      return;
    }
    document.getElementById('uploadDropzone').style.display = 'none';
    document.getElementById('uploadLoading').style.display = 'block';
    document.getElementById('uploadError').style.display = 'none';
    var apiUrl = (document.getElementById('aiApiUrl') && document.getElementById('aiApiUrl').value || 'http://localhost:8000').replace(/\\/$/, '');
    var formData = new FormData();
    formData.append('file', file);
    fetch(apiUrl + '/upload-document', { method: 'POST', body: formData })
      .then(function(r) {
        if (!r.ok) return r.json().then(function(e) { throw new Error(e.detail || 'Upload failed'); });
        return r.json();
      })
      .then(function(data) {
        _uploadExtractedData = data;
        showUploadPreview(data);
      })
      .catch(function(err) {
        showUploadError(err.message || 'Failed to process document. Is the API running?');
        document.getElementById('uploadDropzone').style.display = 'block';
      })
      .finally(function() {
        document.getElementById('uploadLoading').style.display = 'none';
      });
  }
  function showUploadError(msg) {
    var el = document.getElementById('uploadError');
    el.textContent = msg;
    el.style.display = 'block';
  }
  function showUploadPreview(data) {
    var html = '<h4>Patient Name</h4>';
    html += '<input type="text" class="upload-name-input" id="uploadPatientName" value="' + ((data.patient_name || '').replace(/"/g, '&quot;')) + '" placeholder="Enter patient name">';
    if (data.age || data.sex) {
      html += '<h4>Demographics</h4><div class="tag-list">';
      if (data.age) html += '<span class="tag">Age: ' + data.age + '</span>';
      if (data.sex) html += '<span class="tag">Sex: ' + data.sex + '</span>';
      html += '</div>';
    }
    if (data.symptoms && data.symptoms.length) {
      html += '<h4>Symptoms (' + data.symptoms.length + ')</h4><div class="tag-list">';
      data.symptoms.forEach(function(s) { html += '<span class="tag">' + s + '</span>'; });
      html += '</div>';
    }
    if (data.diseases && data.diseases.length) {
      html += '<h4>Diseases / Diagnoses (' + data.diseases.length + ')</h4><div class="tag-list">';
      data.diseases.forEach(function(d) { html += '<span class="tag disease">' + d + '</span>'; });
      html += '</div>';
    }
    var cv = data.clinical_values || {};
    var keys = Object.keys(cv);
    if (keys.length) {
      html += '<h4>Clinical Values</h4><div class="clinical-grid">';
      keys.forEach(function(k) {
        html += '<div class="clinical-item"><div class="cv-label">' + k + '</div><div class="cv-value">' + cv[k] + '</div></div>';
      });
      html += '</div>';
    }
    if (!(data.symptoms || []).length && !(data.diseases || []).length && !keys.length) {
      html += '<p style="color:#94a3b8;text-align:center;padding:1rem 0;">No medical data could be extracted. Try a different document.</p>';
    }
    document.getElementById('uploadPreview').innerHTML = html;
    document.getElementById('uploadPreview').style.display = 'block';
    document.getElementById('uploadActions').style.display = 'flex';
  }
  function confirmCreatePatient() {
    if (!_uploadExtractedData) return;
    var btn = document.getElementById('confirmPatientBtn');
    btn.disabled = true;
    btn.textContent = 'Creating...';
    document.getElementById('uploadError').style.display = 'none';
    var apiUrl = (document.getElementById('aiApiUrl') && document.getElementById('aiApiUrl').value || 'http://localhost:8000').replace(/\\/$/, '');
    var payload = JSON.parse(JSON.stringify(_uploadExtractedData));
    var nameInput = document.getElementById('uploadPatientName');
    if (nameInput && nameInput.value.trim()) payload.patient_name = nameInput.value.trim();
    fetch(apiUrl + '/confirm-patient', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    })
    .then(function(r) {
      if (!r.ok) return r.json().then(function(e) { throw new Error(e.detail || 'Creation failed'); });
      return r.json();
    })
    .then(function(result) {
      addUploadedPatientToGraph(result);
      populateFilterOptions();
      syncPatientsFromBackend();
      document.getElementById('uploadPreview').innerHTML = '<div class="upload-success"><strong>Patient ' + (result.patient_name || result.patient_id) + ' (' + result.patient_id + ') created!</strong>The patient has been added to the graph.</div>';
      document.getElementById('uploadActions').style.display = 'none';
      setTimeout(closeUploadModal, 2200);
    })
    .catch(function(err) {
      showUploadError(err.message || 'Failed to create patient.');
      btn.disabled = false;
      btn.textContent = 'Confirm & Create Patient';
    });
  }
  function addUploadedPatientToGraph(patient) {
    var net = getNet();
    if (!net || !net.body || !net.body.data) return;
    var nodes = net.body.data.nodes;
    var edges = net.body.data.edges;
    var pid = patient.patient_id;
    var pName = patient.patient_name || pid;
    var nodeId = 'up_' + pid;
    nodes.add({
      id: nodeId, label: pName,
      color: { background: '#22d3ee', border: '#0891b2', highlight: { background: '#22d3ee', border: '#0891b2' } },
      title: '<b>Patient: ' + pName + '</b><br>Source: Document upload',
      id_prop: pid, node_type: 'Patient', size: 30, borderWidth: 3,
      shadow: { enabled: true, size: 15, color: 'rgba(34,211,238,0.4)' },
      font: { size: 14, color: '#0f172a' }
    });
    (patient.symptoms || []).forEach(function(s, i) {
      var sId = 'up_sym_' + i + '_' + pid;
      nodes.add({ id: sId, label: s, color: '#f472b6',
        title: '<b>Symptom: ' + s + '</b>', node_type: 'Symptom', size: 16 });
      edges.add({ from: nodeId, to: sId, label: 'HAS_SYMPTOM', color: '#f472b6' });
    });
    (patient.diseases || []).forEach(function(d, i) {
      var existing = null;
      var allN = window._allNodes || [];
      for (var j = 0; j < allN.length; j++) {
        if (allN[j].node_type === 'Disease' && allN[j].label && allN[j].label.toLowerCase() === d.toLowerCase()) {
          existing = allN[j].id; break;
        }
      }
      if (existing) {
        edges.add({ from: nodeId, to: existing, label: 'HAS_DISEASE', color: '#10b981' });
      } else {
        var dId = 'up_dis_' + i + '_' + pid;
        nodes.add({ id: dId, label: d, color: '#ef4444',
          title: '<b>Disease: ' + d + '</b>', node_type: 'Disease', size: 18 });
        edges.add({ from: nodeId, to: dId, label: 'HAS_DISEASE', color: '#10b981' });
      }
    });
    try {
      window._allNodes = nodes.get();
      window._allEdges = edges.get();
    } catch(e) {}
    try { net.focus(nodeId, { scale: 1.2, animation: { duration: 500, easingFunction: 'easeInOutQuad' } }); } catch(e) {}
    populateFilterOptions();
    setTimeout(function() {
      try {
        nodes.update({ id: nodeId, color: '#3b82f6', size: 22, borderWidth: 2,
          shadow: { enabled: true, size: 8, x: 0, y: 2, color: 'rgba(0,0,0,0.08)' } });
        window._allNodes = toNodeArray(nodes.get());
      } catch(e) {}
    }, 5000);
  }
  /* ===== Backend sync: keep graph + filters in sync with Neo4j ===== */
  function syncPatientsFromBackend(callback) {
    var apiUrl = (document.getElementById('aiApiUrl') && document.getElementById('aiApiUrl').value || 'http://localhost:8000').replace(/\\/$/, '');
    var net = getNet();
    if (!net || !net.body || !net.body.data) { if (callback) callback(); return; }
    fetch(apiUrl + '/patients-sync')
      .then(function(r) { return r.ok ? r.json() : []; })
      .then(function(patients) {
        if (!patients) return;
        window._patientSourceById = window._patientSourceById || {};
        patients.forEach(function(p) {
          if (p.patient_id) window._patientSourceById[p.patient_id] = p.source || null;
        });
        var nodes = net.body.data.nodes;
        var edges = net.body.data.edges;
        var allN = toNodeArray(nodes.get());
        var allE = toNodeArray(edges.get());
        var changed = false;

        var existingPatients = {};
        var existingDiseases = {};
        var existingSymptoms = {};
        allN.forEach(function(n) {
          if (n.node_type === 'Patient'  && n.id_prop) existingPatients[n.id_prop] = n.id;
          if (n.node_type === 'Disease'  && n.id_prop) existingDiseases[n.id_prop] = n.id;
          if (n.node_type === 'Symptom'  && n.id_prop) existingSymptoms[n.id_prop] = n.id;
        });

        /* --- REMOVE patients deleted from Neo4j --- */
        var validPids = {};
        patients.forEach(function(p) { if (p.patient_id) validPids[p.patient_id] = true; });
        var removeNodeIds = [];
        allN.forEach(function(n) {
          if (n.node_type === 'Patient' && n.id_prop && !validPids[n.id_prop]) removeNodeIds.push(n.id);
        });
        if (removeNodeIds.length) {
          changed = true;
          var removeSet = {};
          removeNodeIds.forEach(function(nid) { removeSet[nid] = true; });
          var edgesToDrop = [];
          var symptomCandidates = {};
          allE.forEach(function(e) {
            if (removeSet[e.from] || removeSet[e.to]) {
              edgesToDrop.push(e.id);
              var other = removeSet[e.from] ? e.to : e.from;
              allN.forEach(function(n) { if (n.id === other && n.node_type === 'Symptom') symptomCandidates[other] = true; });
            }
          });
          edgesToDrop.forEach(function(eid) { try { edges.remove(eid); } catch(x) {} });
          removeNodeIds.forEach(function(nid) { try { nodes.remove(nid); } catch(x) {} });
          var remainEdges = toNodeArray(edges.get());
          Object.keys(symptomCandidates).forEach(function(symId) {
            var stillConnected = remainEdges.some(function(e) { return e.from === symId || e.to === symId; });
            if (!stillConnected) { try { nodes.remove(symId); } catch(x) {} }
          });
          removeNodeIds.forEach(function(nid) {
            var pid = null;
            allN.forEach(function(n) { if (n.id === nid) pid = n.id_prop; });
            if (pid) delete existingPatients[pid];
          });
          allN = toNodeArray(nodes.get());
          allE = toNodeArray(edges.get());
        }

        /* --- ADD patients that are in Neo4j but not in graph --- */
        patients.forEach(function(p) {
          var pid = p.patient_id;
          if (!pid || existingPatients[pid]) return;
          changed = true;
          var nodeId = 'sync_' + pid;
          var age = p.age; var sex = p.sex;
          var title = '<b>Patient: ' + (p.patient_name || pid) + '</b>';
          if (age || sex) title += '<br>Age: ' + (age || '\u2014') + ' | Sex: ' + (sex || '\u2014');
          var dNames = (p.diseases || []).map(function(d) { return d.name; }).filter(Boolean);
          if (dNames.length) title += '<br>Diseases: ' + dNames.join(', ');
          nodes.add({
            id: nodeId, label: p.patient_name || pid,
            color: '#3b82f6', title: title,
            id_prop: pid, node_type: 'Patient', patient_source: p.source || null, size: 22, borderWidth: 2,
            shadow: { enabled: true, size: 8, x: 0, y: 2, color: 'rgba(0,0,0,0.08)' },
            font: { size: 14, color: '#334155' }
          });
          existingPatients[pid] = nodeId;
          (p.diseases || []).forEach(function(d) {
            if (!d.id) return;
            var target = existingDiseases[d.id];
            if (!target) {
              target = 'sync_d_' + d.id;
              nodes.add({ id: target, label: d.name || d.id, color: '#ef4444',
                title: '<b>Disease: ' + (d.name || d.id) + '</b>',
                id_prop: d.id, node_type: 'Disease', size: 18 });
              existingDiseases[d.id] = target;
            }
            edges.add({ from: nodeId, to: target, label: 'HAS_DISEASE', color: '#10b981' });
          });
          (p.symptoms || []).forEach(function(s) {
            if (!s.id) return;
            var target = existingSymptoms[s.id];
            if (!target) {
              target = 'sync_s_' + s.id;
              nodes.add({ id: target, label: s.name || s.id, color: '#f472b6',
                title: '<b>Symptom: ' + (s.name || s.id) + '</b>',
                id_prop: s.id, node_type: 'Symptom', size: 16 });
              existingSymptoms[s.id] = target;
            }
            edges.add({ from: nodeId, to: target, label: 'HAS_SYMPTOM', color: '#f472b6' });
          });
        });

        if (changed) {
          try {
            if (typeof clearAiHighlight === 'function' && (_aiHighlightActive || window._aiGraphSnapshotBeforeHighlight)) {
              clearAiHighlight();
            }
            window._allNodes = toNodeArray(nodes.get());
            window._allEdges = toNodeArray(edges.get());
          } catch(e) {}
          populateFilterOptions();
          try {
            net.setOptions({ physics: { enabled: true, solver: 'repulsion',
              repulsion: { nodeDistance: 220, centralGravity: 0.03, springLength: 180, springConstant: 0.05 },
              stabilization: { enabled: true, iterations: 100 } } });
            net.once('stabilizationIterationsDone', function() {
              try { net.setOptions({ physics: { enabled: false } }); } catch(e) {}
            });
          } catch(e) {}
        }
      })
      .catch(function(err) { console.log('Patient sync skipped:', err.message); })
      .finally(function() { if (callback) callback(); });
  }
  /* ---- Compare Patients ---- */
  var _compareOriginalNodes = null;
  var _compareOriginalEdges = null;

  function _patientNodeSource(n) {
    if (n.patient_source) return n.patient_source;
    if (n.id_prop && window._patientSourceById) return window._patientSourceById[n.id_prop];
    return null;
  }

  function openCompareModal() {
    var allN = window._allNodes || [];
    var pts = allN.filter(function(n) { return n.node_type === 'Patient'; });
    pts.sort(function(a, b) { return (a.label || '').localeCompare(b.label || ''); });
    var preSelected = {};
    if (_patientContext.selected) preSelected[_patientContext.selected.pid] = true;
    _patientContext.selectedMulti.forEach(function(p) { preSelected[p.pid] = true; });
    var real = []; var sample = [];
    pts.forEach(function(p) {
      if (_patientSourceIsMimic(_patientNodeSource(p))) sample.push(p);
      else real.push(p);
    });
    function row(p) {
      var checked = preSelected[p.id_prop] ? ' checked' : '';
      var h = '<label class="compare-patient-item">';
      h += '<input type="checkbox" value="' + (p.id_prop || p.id) + '"' + checked + ' onchange="updateCompareBtn()">';
      h += '<span class="cp-name">' + (p.label || p.id_prop || '') + '</span>';
      h += '<span class="cp-id">' + (p.id_prop || '') + '</span>';
      h += '</label>';
      return h;
    }
    var html = '';
    var twoGroups = real.length > 0 && sample.length > 0;
    var onlySample = sample.length > 0 && real.length === 0;
    if (twoGroups) {
      html += '<div class="ps-group-label">Real patients</div>';
      real.forEach(function(p) { html += row(p); });
      html += '<div class="ps-group-label">Sample patients (MIMIC)</div>';
      sample.forEach(function(p) { html += row(p); });
    } else {
      if (onlySample) html += '<div class="ps-group-label">Sample patients (MIMIC)</div>';
      real.forEach(function(p) { html += row(p); });
      sample.forEach(function(p) { html += row(p); });
    }
    if (!pts.length) html = '<p class="compare-none">No patients found in the graph.</p>';
    document.getElementById('comparePatientList').innerHTML = html;
    updateCompareBtn();
    document.getElementById('compareModal').style.display = 'flex';
  }

  function closeCompareModal() { document.getElementById('compareModal').style.display = 'none'; }

  function updateCompareBtn() {
    var cb = document.querySelectorAll('#comparePatientList input[type="checkbox"]:checked');
    var btn = document.getElementById('compareRunBtn');
    btn.disabled = cb.length < 2;
    btn.textContent = cb.length >= 2 ? 'Compare (' + cb.length + ')' : 'Compare';
  }

  function runComparison() {
    var cbs = document.querySelectorAll('#comparePatientList input[type="checkbox"]:checked');
    var ids = [];
    cbs.forEach(function(c) { ids.push(c.value); });
    if (ids.length < 2) return;
    closeCompareModal();
    document.getElementById('compareResults').style.display = 'block';
    document.getElementById('compareResultsBody').innerHTML = '<p style="color:#64748b;">Loading comparison...</p>';
    var apiUrl = (document.getElementById('aiApiUrl') && document.getElementById('aiApiUrl').value || 'http://localhost:8000').replace(/\\/$/, '');
    fetch(apiUrl + '/compare-patients', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ patient_ids: ids })
    })
    .then(function(r) { if (!r.ok) throw new Error('Server returned ' + r.status); return r.json(); })
    .then(function(data) { showCompareResults(data); highlightCompareInGraph(data); })
    .catch(function(err) {
      document.getElementById('compareResultsBody').innerHTML =
        '<p style="color:#dc2626;">Comparison failed: ' + err.message + '</p>';
    });
  }

  function showCompareResults(data) {
    var pts = data.patients || [];
    var common = data.common || {};
    var html = '<div class="compare-patients-row">';
    pts.forEach(function(p) {
      html += '<div class="compare-patient-card"><strong>' + (p.patient_name || p.patient_id) + '</strong> (' + p.patient_id + ')';
      if (p.age || p.sex) html += '<br>Age: ' + (p.age || '\u2014') + ' &middot; Sex: ' + (p.sex || '\u2014');
      html += '<br>Diseases: ' + p.diseases.length + ' &middot; Symptoms: ' + p.symptoms.length + ' &middot; Violations: ' + p.violations.length;
      html += '</div>';
    });
    html += '</div>';
    function section(title, items, cls) {
      html += '<div class="compare-section"><h5>' + title + ' (' + items.length + ')</h5>';
      if (items.length) {
        html += '<div class="tag-list">';
        items.forEach(function(it) {
          var label = typeof it === 'string' ? it : (it.name || it.id || it);
          html += '<span class="tag ' + cls + '">' + label + '</span>';
        });
        html += '</div>';
      } else { html += '<p class="compare-none">None in common</p>'; }
      html += '</div>';
    }
    section('Common Diseases', common.diseases || [], 'shared');
    section('Common Symptoms', common.symptoms || [], 'shared');
    section('Common Violations', common.violations || [], 'violation');

    pts.forEach(function(p) {
      var cdi = new Set((common.diseases || []).map(function(d) { return d.id; }));
      var csi = new Set((common.symptoms || []).map(function(s) { return s.id; }));
      var cvi = new Set(common.violations || []);
      var ud = p.diseases.filter(function(d) { return !cdi.has(d.id); });
      var us = p.symptoms.filter(function(s) { return !csi.has(s.id); });
      var uv = p.violations.filter(function(v) { return !cvi.has(v); });
      if (ud.length || us.length || uv.length) {
        html += '<div class="compare-section"><h5>Unique to ' + (p.patient_name || p.patient_id) + '</h5>';
        html += '<div class="tag-list">';
        ud.forEach(function(d) { html += '<span class="tag unique">' + d.name + '</span>'; });
        us.forEach(function(s) { html += '<span class="tag unique">' + s.name + '</span>'; });
        uv.forEach(function(v) { html += '<span class="tag unique">' + v + '</span>'; });
        html += '</div></div>';
      }
    });
    document.getElementById('compareResultsBody').innerHTML = html;
    document.getElementById('compareResults').style.display = 'block';
  }

  function highlightCompareInGraph(data) {
    var net = (typeof getNet === 'function') ? getNet() : null;
    if (!net || !window._allNodes || !window._allEdges) return;
    _compareOriginalNodes = window._allNodes.slice();
    _compareOriginalEdges = window._allEdges.slice();

    var commonSet = {};
    (data.common_node_ids || []).forEach(function(s) {
      var id = s.split(':').slice(1).join(':');
      commonSet[id] = true;
    });

    var patientIdProps = {};
    (data.patients || []).forEach(function(p) { patientIdProps[p.patient_id] = true; });

    var patientVisIds = {};
    var nodeById = {};
    window._allNodes.forEach(function(n) {
      nodeById[n.id] = n;
      if (n.node_type === 'Patient' && patientIdProps[n.id_prop]) patientVisIds[n.id] = true;
    });

    var visibleIds = {};
    Object.keys(patientVisIds).forEach(function(id) { visibleIds[id] = true; });
    var relevantEdges = [];
    window._allEdges.forEach(function(e) {
      if (patientVisIds[e.from] || patientVisIds[e.to]) {
        relevantEdges.push(e);
        visibleIds[e.from] = true;
        visibleIds[e.to] = true;
      }
    });

    var filteredNodes = [];
    window._allNodes.forEach(function(n) {
      if (!visibleIds[n.id]) return;
      var c = JSON.parse(JSON.stringify(n));
      var ip = n.id_prop || n.id;
      if (n.node_type === 'Patient' && patientIdProps[ip]) {
        c.color = { background: '#3b82f6', border: '#2563eb' }; c.size = 30;
        c.font = { color: '#fff', size: 14, bold: true };
      } else if (commonSet[ip]) {
        c.color = { background: '#f59e0b', border: '#d97706' }; c.size = 24;
        c.font = { color: '#78350f', size: 13, bold: true };
      }
      filteredNodes.push(c);
    });

    var filteredEdges = relevantEdges.filter(function(e) { return visibleIds[e.from] && visibleIds[e.to]; });

    net.setData({
      nodes: new vis.DataSet(filteredNodes),
      edges: new vis.DataSet(filteredEdges)
    });
    net.setOptions({ physics: { enabled: true } });
    net.once('stabilized', function() { net.setOptions({ physics: { enabled: false } }); net.fit({ animation: true }); });
    document.getElementById('filterLabel').textContent = 'Showing: Patient comparison';
  }

  function closeComparison() {
    document.getElementById('compareResults').style.display = 'none';
    if (_compareOriginalNodes && _compareOriginalEdges) {
      var net = (typeof getNet === 'function') ? getNet() : null;
      if (net) {
        net.setData({
          nodes: new vis.DataSet(_compareOriginalNodes),
          edges: new vis.DataSet(_compareOriginalEdges)
        });
        net.setOptions({ physics: { enabled: true } });
        net.once('stabilized', function() { net.setOptions({ physics: { enabled: false } }); net.fit({ animation: true }); });
      }
    }
    _compareOriginalNodes = null;
    _compareOriginalEdges = null;
    document.getElementById('filterLabel').textContent = 'Showing: All';
  }

  window.openCompareModal = openCompareModal;
  window.closeCompareModal = closeCompareModal;
  window.updateCompareBtn = updateCompareBtn;
  window.runComparison = runComparison;
  window.closeComparison = closeComparison;

  /* ---- Benchmark Results UI (display layer only; optional GET /benchmark) ---- */
  var _benchmarkCharts = { radar: null, bar: null, modeCompare: null, improvement: null };
  var DEMO_BENCHMARK_PAYLOAD = {
    overall_score: 78,
    status_label: 'moderate',
    metrics: {
      ai_clinical_accuracy: 82,
      graph_coverage_score: 74,
      patient_context_accuracy: 88,
      response_consistency: 71,
      graph_impact_score: 76
    },
    graph_impact: {
      score: 76,
      breakdown: {
        avg_nodes_used_per_case: 11.4,
        avg_relationships_used_per_case: 9.2,
        unique_node_labels_seen: ['Patient', 'Disease', 'Drug', 'Procedure', 'ClinicalState'],
        unique_relationship_types_seen: ['HAS_DISEASE', 'HAS_VIOLATION', 'HAS_CLINICAL_STATE', 'TREATED_WITH'],
        percent_graph_based_reasoning: 63.5,
        percent_non_graph_reasoning: 36.5
      },
      comparison: {
        graph_measured_score: 76,
        llm_baseline_simulated_score: 49,
        graph_influence_delta: 27,
        graph_improvement_score: 27,
        llm_baseline_descriptor: 'LLM Baseline (heuristic simulation, not rerun model)',
        non_causal_interpretation_note: 'This metric does not represent causal performance difference due to evaluation constraints.',
        disclaimer: 'Baseline is not directly comparable due to different evaluation constraints.',
        interpretation_note: 'Graph-augmented values are measured on this system; LLM baseline is a bounded heuristic on the same outputs—not a paired rerun without Neo4j.',
        experiment_structure: {
          graph_enabled_run: true,
          graph_disabled_run: null,
          graph_disabled_run_note: 'True isolation mode placeholder — reserved for future graph-disabled paired benchmark.'
        },
        with_graph_score: 76,
        without_graph_estimated_score: 49,
        note: 'Demo: LLM baseline is simulated from the same responses under a no-graph heuristic.'
      }
    },
    paired_comparison: {
      methodology: 'Demo: paired WITH_GRAPH (Neo4j-backed) vs WITHOUT_GRAPH (LLM-only arm), same prompts per case. Extra metrics capture grounding and traceability beyond raw accuracy.',
      with_graph: {
        ai_clinical_accuracy: 82, response_consistency: 71, patient_context_accuracy: 88,
        graph_grounding_score: 79, reasoning_traceability_score: 76, hallucination_reduction_score: 74
      },
      without_graph: {
        ai_clinical_accuracy: 68, response_consistency: 64, patient_context_accuracy: 72,
        graph_grounding_score: 38, reasoning_traceability_score: 52, hallucination_reduction_score: 49
      },
      graph_improvement_pct: {
        ai_clinical_accuracy: 14, response_consistency: 7, patient_context_accuracy: 16,
        graph_grounding_score: 41, reasoning_traceability_score: 24, hallucination_reduction_score: 25
      },
      without_graph_llm_arm_executed: true,
      interpretation_layer: {
        graph_augmentation_note: 'Graph augmentation may not significantly change final answer accuracy, but improves clinical grounding, reasoning structure, and traceability of AI outputs.'
      }
    },
    experiment_modes: {
      WITH_GRAPH: { label: 'WITH_GRAPH', description: 'Neo4j context.', metrics: {
        ai_clinical_accuracy: 82, response_consistency: 71, patient_context_accuracy: 88,
        graph_grounding_score: 79, reasoning_traceability_score: 76, hallucination_reduction_score: 74
      } },
      WITHOUT_GRAPH: { label: 'WITHOUT_GRAPH', description: 'No Neo4j injection.', metrics: {
        ai_clinical_accuracy: 68, response_consistency: 64, patient_context_accuracy: 72,
        graph_grounding_score: 38, reasoning_traceability_score: 52, hallucination_reduction_score: 49
      } }
    },
    test_cases: [
      { patient_id: 'P23', query: 'Does this patient meet oral anticoagulation safety criteria given their last INR?', expected: 'Contraindicated: recent GI bleed documented; hold anticoagulation and reassess.', actual: 'Flags active GI bleed in notes; recommends holding anticoagulant and gastroenterology review.', score: 92 },
      { patient_id: 'P12', query: 'Summarize Type 2 diabetes protocol adherence.', expected: 'HbA1c monitoring and metformin as first-line; gap in quarterly labs.', actual: 'Identifies Metformin; notes missing HbA1c interval vs protocol.', score: 81 },
      { patient_id: 'P4', query: 'Which follow-up appointments are overdue?', expected: 'Cardiology within 90 days per CHF pathway.', actual: 'Lists overdue PCP visit; misses cardiology interval from graph edge.', score: 64 },
      { patient_id: 'M10006', query: 'Compare sepsis bundle completion for this encounter.', expected: 'Lactate ordered; fluids and antibiotics timing per hour-1 bundle.', actual: 'Cites lactate and antibiotics; fluid bolus timing partially aligned.', score: 73 }
    ],
    generated_at: new Date().toISOString(),
    source: 'demo'
  };

  function destroyBenchmarkCharts() {
    if (_benchmarkCharts.radar) {
      try { _benchmarkCharts.radar.destroy(); } catch (e) {}
      _benchmarkCharts.radar = null;
    }
    if (_benchmarkCharts.bar) {
      try { _benchmarkCharts.bar.destroy(); } catch (e) {}
      _benchmarkCharts.bar = null;
    }
    if (_benchmarkCharts.modeCompare) {
      try { _benchmarkCharts.modeCompare.destroy(); } catch (e) {}
      _benchmarkCharts.modeCompare = null;
    }
    if (_benchmarkCharts.improvement) {
      try { _benchmarkCharts.improvement.destroy(); } catch (e) {}
      _benchmarkCharts.improvement = null;
    }
  }

  function benchmarkStatusFromScore(score) {
    if (score >= 80) return { key: 'good', label: 'Good' };
    if (score >= 60) return { key: 'moderate', label: 'Moderate' };
    return { key: 'needs_improvement', label: 'Needs Improvement' };
  }

  var BENCHMARK_BASELINE_DISCLAIMER = 'Baseline is not directly comparable due to different evaluation constraints.';
  var BENCHMARK_NON_CAUSAL_NOTE = 'This metric does not represent causal performance difference due to evaluation constraints.';
  var BENCHMARK_GRAPH_AUGMENTATION_NOTE = 'Graph augmentation may not significantly change final answer accuracy, but improves clinical grounding, reasoning structure, and traceability of AI outputs.';
  var BENCHMARK_EXPERIMENT_PLACEHOLDER = {
    graph_enabled_run: true,
    graph_disabled_run: null,
    graph_disabled_run_note: 'True isolation mode placeholder — reserved for future graph-disabled paired benchmark.'
  };

  function enrichGraphImpactComparison(gi, fallbackScore) {
    if (!gi) return null;
    var c = gi.comparison || {};
    var gm = c.graph_measured_score != null ? Number(c.graph_measured_score)
      : (c.with_graph_score != null ? Number(c.with_graph_score)
        : (gi.score != null ? Number(gi.score) : (fallbackScore != null ? Number(fallbackScore) : NaN)));
    var lb = c.llm_baseline_simulated_score != null ? Number(c.llm_baseline_simulated_score)
      : (c.without_graph_estimated_score != null ? Number(c.without_graph_estimated_score) : NaN);
    if (isNaN(lb) && !isNaN(gm)) {
      lb = Math.max(22, Math.min(71, Math.round(62 - gm * 0.35)));
    }
    var delta = c.graph_influence_delta != null ? Number(c.graph_influence_delta)
      : (c.graph_improvement_score != null ? Number(c.graph_improvement_score) : NaN);
    if (isNaN(delta) && !isNaN(gm) && !isNaN(lb)) delta = Math.round(gm - lb);
    var exp = c.experiment_structure && typeof c.experiment_structure === 'object' ? c.experiment_structure : BENCHMARK_EXPERIMENT_PLACEHOLDER;
    gi.comparison = Object.assign({}, c, {
      graph_measured_score: isNaN(gm) ? null : gm,
      llm_baseline_simulated_score: isNaN(lb) ? null : lb,
      graph_influence_delta: isNaN(delta) ? null : delta,
      graph_improvement_score: isNaN(delta) ? null : delta,
      experiment_structure: exp,
      non_causal_interpretation_note: (c.non_causal_interpretation_note && String(c.non_causal_interpretation_note).trim())
        ? c.non_causal_interpretation_note : BENCHMARK_NON_CAUSAL_NOTE,
      disclaimer: (c.disclaimer && String(c.disclaimer).trim()) ? c.disclaimer : BENCHMARK_BASELINE_DISCLAIMER
    });
    return gi;
  }

  function normalizeBenchmarkPayload(raw) {
    var o = raw || {};
    var overall = o.overall_score != null ? Number(o.overall_score) : (o.overall != null ? Number(o.overall) : NaN);
    if (isNaN(overall)) overall = 0;
    overall = Math.max(0, Math.min(100, Math.round(overall)));
    var m = o.metrics || {};
    function pick(a, b, c) {
      var v = m[a];
      if (v == null) v = m[b];
      if (v == null) v = c;
      return Math.max(0, Math.min(100, Math.round(Number(v || 0))));
    }
    var metrics = {
      ai_clinical_accuracy: pick('ai_clinical_accuracy', 'aiClinicalAccuracy', overall),
      graph_coverage_score: pick('graph_coverage_score', 'graphCoverageScore', overall),
      patient_context_accuracy: pick('patient_context_accuracy', 'patientContextAccuracy', overall),
      response_consistency: pick('response_consistency', 'responseConsistency', overall),
      graph_impact_score: pick('graph_impact_score', 'graphImpactScore', overall)
    };
    var cases = Array.isArray(o.test_cases) ? o.test_cases : (Array.isArray(o.cases) ? o.cases : []);
    var gi = o.graph_impact;
    if (!gi && metrics.graph_impact_score != null) {
      gi = { score: metrics.graph_impact_score, breakdown: {}, comparison: {} };
    }
    if (gi) enrichGraphImpactComparison(gi, metrics.graph_impact_score);
    return {
      overall_score: overall,
      status_label: o.status_label || o.status || '',
      metrics: metrics,
      graph_impact: gi || null,
      paired_comparison: o.paired_comparison || null,
      experiment_modes: o.experiment_modes || null,
      test_cases: cases,
      generated_at: o.generated_at || o.run_at || '',
      source: o.source || 'api',
      note: o.note || ''
    };
  }

  function renderBenchmarkResults(data) {
    destroyBenchmarkCharts();
    function formatBenchmarkDelta(d) {
      if (d == null || isNaN(Number(d))) return '\\u2014';
      var n = Math.round(Number(d));
      return (n > 0 ? '+' : '') + n;
    }
    var overall = Math.max(0, Math.min(100, Math.round(data.overall_score)));
    var st = benchmarkStatusFromScore(overall);
    var raw = (data.status_label || data.status || '').toString().trim();
    if (raw) {
      var sl = raw.toLowerCase().replace(/\\s+/g, '_');
      if (sl === 'good') st = { key: 'good', label: 'Good' };
      else if (sl === 'moderate') st = { key: 'moderate', label: 'Moderate' };
      else if (sl === 'needs_improvement' || raw.toLowerCase().indexOf('needs') === 0) st = { key: 'needs_improvement', label: 'Needs Improvement' };
      else st = { key: st.key, label: raw };
    }
    var pill = document.getElementById('benchmarkStatusPill');
    if (pill) {
      pill.className = 'benchmark-status-pill ' + st.key;
      pill.textContent = st.label;
    }
    var numEl = document.getElementById('benchmarkOverallNum');
    if (numEl) numEl.innerHTML = overall + '<span>/100</span>';

    var meta = document.getElementById('benchmarkRunMeta');
    if (meta) {
      var parts = [];
      if (data.generated_at) {
        try { parts.push('Run: ' + new Date(data.generated_at).toLocaleString()); } catch (e) { parts.push('Run: ' + data.generated_at); }
      }
      if (data.source === 'demo') parts.push('Sample data');
      meta.textContent = parts.join(' \\u2014 ');
    }

    var grid = document.getElementById('benchmarkMetricsGrid');
    if (grid) {
      var cards = [
        { k: 'ai_clinical_accuracy', title: 'AI Clinical Accuracy' },
        { k: 'graph_coverage_score', title: 'Graph Coverage Score' },
        { k: 'patient_context_accuracy', title: 'Patient Context Accuracy' },
        { k: 'response_consistency', title: 'Response Consistency' },
        { k: 'graph_impact_score', title: 'Graph Impact Score (0\u2013100)' }
      ];
      grid.innerHTML = cards.map(function(c) {
        var v = data.metrics[c.k] != null ? data.metrics[c.k] : 0;
        var w = Math.max(0, Math.min(100, v));
        return '<div class="benchmark-metric-card"><div class="bm-label">' + c.title + '</div><div class="bm-value">' + v + '</div><div class="bm-bar"><i style="width:' + w + '%"></i></div></div>';
      }).join('');
    }

    var giDetails = document.getElementById('benchmarkGIDetails');
    var giBody = document.getElementById('benchmarkGIBreakdownBody');
    var giRow = document.getElementById('benchmarkGraphCompareRow');
    var withEl = document.getElementById('bcWithG');
    var withoutEl = document.getElementById('bcWithoutG');
    var deltaEl = document.getElementById('bcDeltaG');
    var discStripEl = document.getElementById('benchmarkCompareDisclaimer');
    var toggleEl = document.getElementById('benchmarkCompareGraphToggle');
    var gix = data.graph_impact;
    if (gix) enrichGraphImpactComparison(gix, data.metrics && data.metrics.graph_impact_score);
    else if (data.metrics && data.metrics.graph_impact_score != null) {
      gix = { score: data.metrics.graph_impact_score, breakdown: {}, comparison: {} };
      enrichGraphImpactComparison(gix, data.metrics.graph_impact_score);
    }
    if (giDetails) giDetails.style.display = gix ? '' : 'none';
    if (giBody && gix) {
      var br = gix.breakdown || {};
      var cmpPre = gix.comparison || {};
      var gmPre = cmpPre.graph_measured_score != null ? cmpPre.graph_measured_score : (cmpPre.with_graph_score != null ? cmpPre.with_graph_score : gix.score);
      var lbPre = cmpPre.llm_baseline_simulated_score != null ? cmpPre.llm_baseline_simulated_score : cmpPre.without_graph_estimated_score;
      var deltaPre = cmpPre.graph_influence_delta != null ? cmpPre.graph_influence_delta
        : (cmpPre.graph_improvement_score != null ? cmpPre.graph_improvement_score : (gmPre != null && lbPre != null ? Math.round(gmPre - lbPre) : null));
      var nodesL = (br.unique_node_labels_seen || []).join(', ') || '\\u2014';
      var relsL = (br.unique_relationship_types_seen || []).join(', ') || '\\u2014';
      giBody.innerHTML = '<table><tbody>'
        + '<tr><th>Avg. nodes used / case</th><td>' + (br.avg_nodes_used_per_case != null ? br.avg_nodes_used_per_case : '\\u2014') + '</td></tr>'
        + '<tr><th>Avg. relationships used / case</th><td>' + (br.avg_relationships_used_per_case != null ? br.avg_relationships_used_per_case : '\\u2014') + '</td></tr>'
        + '<tr><th>Node labels (union)</th><td style="word-break:break-word;">' + nodesL + '</td></tr>'
        + '<tr><th>Relationship types (union)</th><td style="word-break:break-word;">' + relsL + '</td></tr>'
        + '<tr><th>% graph-based reasoning (est.)</th><td>' + (br.percent_graph_based_reasoning != null ? br.percent_graph_based_reasoning + '%' : '\\u2014') + '</td></tr>'
        + '<tr><th>% non-graph reasoning (est.)</th><td>' + (br.percent_non_graph_reasoning != null ? br.percent_non_graph_reasoning + '%' : '\\u2014') + '</td></tr>'
        + '<tr><th>Graph Influence Delta (non-causal estimate)</th><td>' + formatBenchmarkDelta(deltaPre) + '</td></tr>'
        + '</tbody></table>'
        + (cmpPre.interpretation_note ? '<p style="margin-top:0.5rem;font-size:0.625rem;color:#64748b;line-height:1.45;">' + String(cmpPre.interpretation_note).replace(/</g, '&lt;') + '</p>' : '')
        + (cmpPre.non_causal_interpretation_note ? '<p style="margin-top:0.35rem;font-size:0.625rem;color:#475569;font-weight:500;">' + String(cmpPre.non_causal_interpretation_note).replace(/</g, '&lt;') + '</p>' : '')
        + (function() {
            var es = cmpPre.experiment_structure;
            if (!es) return '';
            return '<p style="margin-top:0.5rem;font-size:0.625rem;color:#64748b;"><strong>Future baseline structure:</strong> graph_enabled_run=' + String(es.graph_enabled_run)
              + '; graph_disabled_run=' + (es.graph_disabled_run != null ? String(es.graph_disabled_run) : 'null')
              + (es.graph_disabled_run_note ? '. ' + String(es.graph_disabled_run_note).replace(/</g, '&lt;') : '') + '</p>';
          })()
        + (cmpPre.note ? '<p style="margin-top:0.35rem;font-size:0.625rem;color:#94a3b8;">' + String(cmpPre.note).replace(/</g, '&lt;') + '</p>' : '')
        + (cmpPre.disclaimer ? '<p style="margin-top:0.35rem;font-size:0.625rem;font-weight:600;color:#64748b;">' + String(cmpPre.disclaimer).replace(/</g, '&lt;') + '</p>' : '');
    } else if (giBody) giBody.innerHTML = '';

    var cmp = gix && gix.comparison ? gix.comparison : {};
    var gm = cmp.graph_measured_score != null ? cmp.graph_measured_score : (cmp.with_graph_score != null ? cmp.with_graph_score : (gix && gix.score != null ? gix.score : null));
    var lb = cmp.llm_baseline_simulated_score != null ? cmp.llm_baseline_simulated_score : cmp.without_graph_estimated_score;
    var dlt = cmp.graph_influence_delta != null ? cmp.graph_influence_delta
      : (cmp.graph_improvement_score != null ? cmp.graph_improvement_score : (gm != null && lb != null ? Math.round(gm - lb) : null));
    if (withEl) withEl.textContent = gm != null ? gm : '\\u2014';
    if (withoutEl) withoutEl.textContent = lb != null ? lb : '\\u2014';
    if (deltaEl) deltaEl.textContent = formatBenchmarkDelta(dlt);
    var baselineLabEl = document.getElementById('bcBaselineLab');
    if (baselineLabEl) baselineLabEl.textContent = (cmp.llm_baseline_descriptor && String(cmp.llm_baseline_descriptor).trim())
      ? cmp.llm_baseline_descriptor : 'LLM Baseline (heuristic simulation, not rerun model)';
    if (discStripEl) {
      var p1 = (cmp.disclaimer && String(cmp.disclaimer).trim()) ? cmp.disclaimer : BENCHMARK_BASELINE_DISCLAIMER;
      var p2 = (cmp.non_causal_interpretation_note && String(cmp.non_causal_interpretation_note).trim())
        ? cmp.non_causal_interpretation_note : BENCHMARK_NON_CAUSAL_NOTE;
      discStripEl.textContent = p1 + '\\n\\n' + p2;
    }
    if (toggleEl) {
      toggleEl.checked = false;
      if (giRow) giRow.classList.remove('visible');
      toggleEl.onchange = function() {
        if (giRow) giRow.classList.toggle('visible', toggleEl.checked);
      };
    }

    var labels = ['AI Clinical Accuracy', 'Graph Coverage', 'Patient Context', 'Response Consistency', 'Graph Impact'];
    var vals = [
      data.metrics.ai_clinical_accuracy,
      data.metrics.graph_coverage_score,
      data.metrics.patient_context_accuracy,
      data.metrics.response_consistency,
      data.metrics.graph_impact_score != null ? data.metrics.graph_impact_score : (gix && gix.score != null ? gix.score : 0)
    ];

    var chartOpts = {
      responsive: true,
      maintainAspectRatio: false,
      scales: {
        r: {
          min: 0,
          max: 100,
          ticks: { stepSize: 20, color: '#94a3b8', font: { size: 10 } },
          grid: { color: '#e2e8f0' },
          pointLabels: { color: '#64748b', font: { size: 10, family: 'Inter,system-ui,sans-serif' } }
        }
      },
      plugins: { legend: { display: false } }
    };

    var radarEl = document.getElementById('benchmarkRadarCanvas');
    if (radarEl && typeof Chart !== 'undefined') {
      _benchmarkCharts.radar = new Chart(radarEl.getContext('2d'), {
        type: 'radar',
        data: {
          labels: labels,
          datasets: [{
            label: 'Score',
            data: vals,
            borderColor: '#3b82f6',
            backgroundColor: 'rgba(59,130,246,0.22)',
            borderWidth: 2,
            pointBackgroundColor: '#1d4ed8'
          }]
        },
        options: chartOpts
      });
    }

    var barEl = document.getElementById('benchmarkBarCanvas');
    if (barEl && typeof Chart !== 'undefined') {
      _benchmarkCharts.bar = new Chart(barEl.getContext('2d'), {
        type: 'bar',
        data: {
          labels: labels,
          datasets: [{
            label: 'Score',
            data: vals,
            backgroundColor: ['rgba(59,130,246,0.85)', 'rgba(14,165,233,0.85)', 'rgba(16,185,129,0.85)', 'rgba(139,92,246,0.85)', 'rgba(234,88,12,0.85)'],
            borderRadius: 6
          }]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          indexAxis: 'y',
          scales: {
            x: { min: 0, max: 100, grid: { color: '#f1f5f9' }, ticks: { color: '#64748b', font: { size: 10 } } },
            y: { grid: { display: false }, ticks: { color: '#475569', font: { size: 10 } } }
          },
          plugins: { legend: { display: false } }
        }
      });
    }

    var expSec = document.getElementById('benchmarkExperimentSection');
    var expNote = document.getElementById('benchmarkExperimentNote');
    var interpAug = document.getElementById('benchmarkAugmentationInterpretation');
    var pc = data.paired_comparison;
    if (expSec) {
      if (pc && pc.with_graph && pc.without_graph && typeof Chart !== 'undefined') {
        expSec.style.display = '';
        if (expNote) expNote.textContent = pc.methodology || '';
        if (interpAug) {
          interpAug.style.display = '';
          interpAug.textContent = (pc.interpretation_layer && pc.interpretation_layer.graph_augmentation_note)
            ? pc.interpretation_layer.graph_augmentation_note : BENCHMARK_GRAPH_AUGMENTATION_NOTE;
        }
        var pv = function(o, k) { return o && o[k] != null ? Number(o[k]) : null; };
        var modeLabels = [
          'AI clinical acc.', 'Response consistency', 'Patient context',
          'Graph grounding', 'Traceability', 'Hallucination reduction'
        ];
        var metricKeys = [
          'ai_clinical_accuracy', 'response_consistency', 'patient_context_accuracy',
          'graph_grounding_score', 'reasoning_traceability_score', 'hallucination_reduction_score'
        ];
        var wg = pc.with_graph;
        var ng = pc.without_graph;
        var valsWG = metricKeys.map(function(k) { var v = pv(wg, k); return v != null ? v : 0; });
        var valsNG = metricKeys.map(function(k) { var v = pv(ng, k); return v != null ? v : 0; });
        var modeEl = document.getElementById('benchmarkModeCompareCanvas');
        if (modeEl) {
          _benchmarkCharts.modeCompare = new Chart(modeEl.getContext('2d'), {
            type: 'bar',
            data: {
              labels: modeLabels,
              datasets: [
                { label: 'With Graph', data: valsWG, backgroundColor: 'rgba(59,130,246,0.85)', borderRadius: 5 },
                { label: 'Without Graph', data: valsNG, backgroundColor: 'rgba(148,163,184,0.85)', borderRadius: 5 }
              ]
            },
            options: {
              responsive: true,
              maintainAspectRatio: false,
              scales: {
                y: { min: 0, max: 100, grid: { color: '#f1f5f9' }, ticks: { color: '#64748b', font: { size: 10 } } },
                x: { grid: { display: false }, ticks: { color: '#475569', font: { size: 8 }, maxRotation: 55, minRotation: 35 } }
              },
              plugins: { legend: { position: 'bottom', labels: { boxWidth: 12, font: { size: 10 } } } }
            }
          });
        }
        var imp = pc.graph_improvement_pct || {};
        var impVals = metricKeys.map(function(k, i) {
          var iv = pv(imp, k);
          return iv != null ? iv : (valsWG[i] - valsNG[i]);
        });
        var impColors = impVals.map(function(v) {
          if (v > 0) return 'rgba(16,185,129,0.9)';
          if (v < 0) return 'rgba(239,68,68,0.9)';
          return 'rgba(148,163,184,0.85)';
        });
        var impEl = document.getElementById('benchmarkImprovementCanvas');
        if (impEl) {
          var vmin = Math.min.apply(null, impVals.concat([0]));
          var vmax = Math.max.apply(null, impVals.concat([0]));
          var pad = Math.max(5, Math.abs(vmax - vmin) * 0.15);
          _benchmarkCharts.improvement = new Chart(impEl.getContext('2d'), {
            type: 'bar',
            data: {
              labels: modeLabels,
              datasets: [{
                label: 'Delta (pp)',
                data: impVals,
                backgroundColor: impColors,
                borderRadius: 5
              }]
            },
            options: {
              responsive: true,
              maintainAspectRatio: false,
              indexAxis: 'y',
              scales: {
                x: {
                  grid: { color: '#f1f5f9' },
                  ticks: { color: '#64748b', font: { size: 9 } },
                  suggestedMin: vmin - pad,
                  suggestedMax: vmax + pad
                },
                y: { grid: { display: false }, ticks: { color: '#475569', font: { size: 8 } } }
              },
              plugins: { legend: { display: false } }
            }
          });
        }
      } else {
        expSec.style.display = 'none';
        if (interpAug) { interpAug.style.display = 'none'; interpAug.textContent = ''; }
      }
    }

    var tbody = document.getElementById('benchmarkTestCasesBody');
    if (tbody) {
      var rows = (data.test_cases || []).length ? data.test_cases : DEMO_BENCHMARK_PAYLOAD.test_cases;
      tbody.innerHTML = rows.map(function(tc) {
        var pid = tc.patient_id || tc.patientId || '\\u2014';
        var q = tc.query || tc.question || '\\u2014';
        var ex = tc.expected || tc.expected_result || '\\u2014';
        var ac = tc.actual || tc.actual_result || '\\u2014';
        var sc = tc.score != null ? tc.score : '\\u2014';
        return '<tr><td class="mono">' + String(pid).replace(/</g, '&lt;') + '</td><td>' + String(q).replace(/</g, '&lt;') + '</td><td>' + String(ex).replace(/</g, '&lt;') + '</td><td>' + String(ac).replace(/</g, '&lt;') + '</td><td class="tc-score">' + sc + (typeof sc === 'number' ? '%' : '') + '</td></tr>';
      }).join('');
      if (!rows.length) tbody.innerHTML = '<tr><td colspan="5" style="color:#94a3b8;font-style:italic;">No test cases in payload.</td></tr>';
    }

    var foot = document.getElementById('benchmarkFootnote');
    if (foot) {
      if (data.note) foot.textContent = data.note;
      else if (data.source === 'demo') foot.textContent = 'Sample benchmark payload for UI review. Implement GET /benchmark on your API to return live evaluation JSON in the same shape.';
      else foot.textContent = 'Scores are illustrative of evaluation dimensions; wire your benchmark runner to populate this panel.';
    }
  }

  function openBenchmarkModal() {
    var m = document.getElementById('benchmarkModal');
    if (m) { m.classList.add('open'); m.setAttribute('aria-hidden', 'false'); }
  }

  function closeBenchmarkModal() {
    destroyBenchmarkCharts();
    var modal = document.getElementById('benchmarkModal');
    if (modal) { modal.classList.remove('open'); modal.setAttribute('aria-hidden', 'true'); }
  }

  function runBenchmark() {
    var btn = document.getElementById('benchmarkRunBtn');
    if (btn) btn.disabled = true;
    openBenchmarkModal();
    var loadEl = document.getElementById('benchmarkLoading');
    var contentEl = document.getElementById('benchmarkContent');
    if (loadEl) loadEl.classList.add('visible');
    if (contentEl) contentEl.classList.remove('visible');

    var apiUrl = (document.getElementById('aiApiUrl') && document.getElementById('aiApiUrl').value || 'http://localhost:8000').replace(/\\/$/, '');
    var done = function(payload, isDemo) {
      renderBenchmarkResults(payload);
      if (isDemo) {
        var foot = document.getElementById('benchmarkFootnote');
        if (foot) foot.textContent = 'Showing sample benchmark data (no GET /benchmark response). Connect your evaluation endpoint to display live runs.';
      }
      if (loadEl) loadEl.classList.remove('visible');
      if (contentEl) contentEl.classList.add('visible');
      if (btn) btn.disabled = false;
    };

    fetch(apiUrl + '/benchmark', { method: 'GET', headers: { Accept: 'application/json' } })
      .then(function(r) {
        if (!r.ok) throw new Error('benchmark unavailable');
        return r.json();
      })
      .then(function(json) {
        done(normalizeBenchmarkPayload(json), false);
      })
      .catch(function() {
        done(normalizeBenchmarkPayload(DEMO_BENCHMARK_PAYLOAD), true);
      });
  }

  window.runBenchmark = runBenchmark;
  window.closeBenchmarkModal = closeBenchmarkModal;

  /* ---- Clinical Timeline ---- */
  var _tlCharts = {};

  function openTimeline(patientId) {
    document.getElementById('timelineModal').style.display = 'flex';
    document.getElementById('timelineTitle').textContent = 'Clinical Timeline';
    document.getElementById('timelineInfo').textContent = 'Loading...';
    document.getElementById('timelineEvents').innerHTML = '';
    Object.keys(_tlCharts).forEach(function(k) { _tlCharts[k].destroy(); });
    _tlCharts = {};
    var apiUrl = (document.getElementById('aiApiUrl') && document.getElementById('aiApiUrl').value || 'http://localhost:8000').replace(/\\/$/, '');
    fetch(apiUrl + '/patient-timeline/' + encodeURIComponent(patientId))
    .then(function(r) { if (!r.ok) throw new Error('API error ' + r.status); return r.json(); })
    .then(function(data) { renderTimeline(data); })
    .catch(function(err) {
      document.getElementById('timelineInfo').textContent = 'Error: ' + err.message;
    });
  }

  function closeTimeline() {
    document.getElementById('timelineModal').style.display = 'none';
    Object.keys(_tlCharts).forEach(function(k) { _tlCharts[k].destroy(); });
    _tlCharts = {};
  }

  function renderTimeline(data) {
    var name = data.patient_name || data.patient_id;
    var info = name + ' (' + data.patient_id + ')';
    if (data.age) info += ' · Age ' + data.age;
    if (data.sex) info += ' · ' + data.sex;
    if (data.diseases && data.diseases.length) info += ' · ' + data.diseases.map(function(d) { return d.name || d.id; }).join(', ');
    document.getElementById('timelineTitle').textContent = 'Clinical Timeline — ' + name;
    document.getElementById('timelineInfo').textContent = info;
    if (!data.has_clinical_state) {
      document.getElementById('timelineInfo').textContent += ' (simulated from baseline)';
    }

    var labels = data.labels || [];
    var v = data.vitals || {};

    function trendLabel(vital, key) {
      var t = vital.trend || 'stable';
      var higher_is_worse = !!vital.critical_above;
      var cls, text;
      if (t === 'stable') { cls = 'stable'; text = 'Stable'; }
      else if (t === 'rising') {
        if (higher_is_worse) { cls = 'bad'; text = 'Worsening \\u2191'; }
        else { cls = 'good'; text = 'Improving \\u2191'; }
      } else {
        if (higher_is_worse) { cls = 'good'; text = 'Improving \\u2193'; }
        else { cls = 'bad'; text = 'Worsening \\u2193'; }
      }
      var el = document.getElementById('trend' + key);
      if (el) { el.textContent = text; el.className = 'tl-trend ' + cls; }
    }

    trendLabel(v.sofa || {}, 'Sofa');
    trendLabel(v.map || {}, 'Map');
    trendLabel(v.creatinine || {}, 'Creat');
    trendLabel(v.gcs || {}, 'Gcs');
    trendLabel(v.lactate || {}, 'Lactate');

    function makeChart(canvasId, label, values, color, critVal, critType) {
      var ctx = document.getElementById(canvasId);
      if (!ctx) return;
      var datasets = [{
        label: label, data: values, borderColor: color, backgroundColor: color + '22',
        borderWidth: 2, pointRadius: 4, pointBackgroundColor: color, fill: true, tension: 0.3
      }];
      var annotations = {};
      if (critVal != null) {
        annotations.critLine = {
          type: 'line', yMin: critVal, yMax: critVal, borderColor: '#ef444480',
          borderWidth: 1.5, borderDash: [5, 3],
          label: { content: (critType === 'above' ? '\\u2265 ' : '\\u2264 ') + critVal, enabled: true, position: 'end', font: { size: 9 }, color: '#ef4444' }
        };
      }
      _tlCharts[canvasId] = new Chart(ctx, {
        type: 'line',
        data: { labels: labels, datasets: datasets },
        options: {
          responsive: true, maintainAspectRatio: false,
          scales: { y: { beginAtZero: false, grid: { color: '#e2e8f022' }, ticks: { font: { size: 10 } } }, x: { grid: { display: false }, ticks: { font: { size: 10 } } } },
          plugins: { legend: { display: false }, tooltip: { callbacks: { label: function(c) { return label + ': ' + c.parsed.y; } } } }
        }
      });
    }

    makeChart('chartSofa', 'SOFA', (v.sofa || {}).values || [], '#ef4444', 2, 'above');
    makeChart('chartMap', 'MAP', (v.map || {}).values || [], '#3b82f6', 65, 'below');
    makeChart('chartCreat', 'Creatinine', (v.creatinine || {}).values || [], '#f59e0b', 1.5, 'above');
    makeChart('chartGcs', 'GCS', (v.gcs || {}).values || [], '#10b981', 13, 'below');
    makeChart('chartLactate', 'Lactate', (v.lactate || {}).values || [], '#8b5cf6', 2.0, 'above');

    var eventsHtml = '';
    var events = data.events || [];
    if (events.length) {
      events.forEach(function(ev) {
        eventsHtml += '<div class="tl-event">';
        eventsHtml += '<span class="tl-event-dot ' + (ev.type || 'encounter') + '"></span>';
        eventsHtml += '<span class="tl-event-date">' + (ev.date || '') + '</span>';
        eventsHtml += '<span>' + (ev.label || '') + '</span>';
        eventsHtml += '</div>';
      });
    } else {
      eventsHtml = '<p class="tl-no-events">No clinical events recorded.</p>';
    }
    document.getElementById('timelineEvents').innerHTML = eventsHtml;
  }

  window.openTimeline = openTimeline;
  window.closeTimeline = closeTimeline;

  /* ===== Patient Summary Card & Insights ===== */
  function _getConnectedByType(pid) {
    var allN = window._allNodes || [];
    var allE = window._allEdges || [];
    var visId = null;
    allN.forEach(function(n) { if (n.node_type === 'Patient' && n.id_prop === pid) visId = n.id; });
    if (!visId) return { diseases: [], symptoms: [], violations: [], drugs: [], clinical: null, name: '', age: '', sex: '' };
    var pNode = null;
    allN.forEach(function(n) { if (n.id === visId) pNode = n; });
    var connIds = {};
    allE.forEach(function(e) {
      if (e.from === visId) connIds[e.to] = e.label || '';
      if (e.to === visId) connIds[e.from] = e.label || '';
    });
    var diseases = [], symptoms = [], violations = [], drugs = [], clinical = null;
    allN.forEach(function(n) {
      if (!connIds.hasOwnProperty(n.id)) return;
      if (n.node_type === 'Disease') diseases.push(n.label || n.id_prop);
      else if (n.node_type === 'Symptom') symptoms.push(n.label || n.id_prop);
      else if (n.node_type === 'Violation') {
        var sev = 'warning';
        var t = n.title || '';
        if (t.indexOf('critical') >= 0 || t.indexOf('Critical') >= 0) sev = 'critical';
        else if (t.indexOf('normal') >= 0 || t.indexOf('Normal') >= 0 || t.indexOf('Compliant') >= 0) sev = 'normal';
        violations.push({ text: n.label || n.id_prop, severity: sev, title: t });
      }
      else if (n.node_type === 'Drug') drugs.push(n.label || n.id_prop);
      else if (n.node_type === 'ClinicalState') {
        var tp = n.title || '';
        var extract = function(key) { var m = tp.match(new RegExp(key + ':\\\\s*([\\\\d.]+)')); return m ? parseFloat(m[1]) : null; };
        clinical = { sofa: extract('SOFA'), lactate: extract('Lactate'), map: extract('MAP'), gcs: extract('GCS'), creatinine: extract('Creatinine') };
      }
    });
    var title = (pNode && pNode.title) || '';
    var ageM = title.match(/Age:\\s*([^|<]+)/);
    var sexM = title.match(/Sex:\\s*([^<]+)/);
    return {
      diseases: diseases, symptoms: symptoms, violations: violations, drugs: drugs, clinical: clinical,
      name: (pNode && pNode.label) || pid,
      age: ageM ? ageM[1].trim() : '',
      sex: sexM ? sexM[1].trim() : ''
    };
  }

  function renderPatientSummary(pid) {
    var card = document.getElementById('patientSummaryCard');
    var content = document.getElementById('pscContent');
    if (!card || !content) return;
    if (!pid) { card.classList.remove('active'); return; }
    var d = _getConnectedByType(pid);
    var html = '<p class="psc-name"><span class="psc-dot"></span>' + d.name + '</p>';
    html += '<p class="psc-meta">' + pid;
    if (d.age && d.age !== '—') html += ' &middot; Age ' + d.age;
    if (d.sex && d.sex !== '—') html += ' &middot; ' + d.sex;
    html += '</p>';
    var summaryParts = [];
    var c = d.clinical;
    var hasSepsis = d.diseases.some(function(x) { return x.toLowerCase().indexOf('sepsis') >= 0; });
    var hasFever = d.symptoms.some(function(x) { return x.toLowerCase().indexOf('fever') >= 0; });
    var hasHypotension = d.symptoms.some(function(x) { return x.toLowerCase().indexOf('hypotension') >= 0; });
    var critCount = d.violations.filter(function(v) { return v.severity === 'critical'; }).length;
    if (hasSepsis) summaryParts.push('diagnosed with sepsis');
    else if (d.diseases.length === 1) summaryParts.push('diagnosed with ' + d.diseases[0].toLowerCase());
    else if (d.diseases.length > 1) summaryParts.push(d.diseases.length + ' active diagnoses');
    if (c && c.map != null && c.map < 65) summaryParts.push('hypotensive (MAP ' + c.map + ')');
    else if (hasHypotension) summaryParts.push('signs of hypotension');
    if (c && c.sofa != null && c.sofa >= 8) summaryParts.push('severe organ dysfunction (SOFA ' + c.sofa + ')');
    else if (c && c.sofa != null && c.sofa >= 2) summaryParts.push('elevated SOFA (' + c.sofa + ')');
    if (c && c.lactate != null && c.lactate > 4) summaryParts.push('high lactate (' + c.lactate + ')');
    if (hasFever && !hasSepsis && c && c.sofa != null && c.sofa >= 2) summaryParts.push('possible early sepsis pattern');
    if (critCount) summaryParts.push(critCount + ' critical protocol violation' + (critCount > 1 ? 's' : ''));
    else if (!d.violations.length && d.diseases.length) summaryParts.push('protocol-compliant');
    var summaryText = '';
    if (summaryParts.length) {
      summaryText = 'Patient presents with ' + summaryParts[0];
      if (summaryParts.length === 2) summaryText += ' and ' + summaryParts[1];
      else if (summaryParts.length > 2) {
        for (var si = 1; si < summaryParts.length - 1; si++) summaryText += ', ' + summaryParts[si];
        summaryText += ', and ' + summaryParts[summaryParts.length - 1];
      }
      summaryText += '.';
    } else {
      summaryText = 'No significant clinical findings for this patient.';
    }
    html += '<p class="psc-clinical-summary">' + summaryText + '</p>';
    html += '<div class="psc-section"><p class="psc-section-label">Diseases</p><div class="psc-tags">';
    if (d.diseases.length) d.diseases.forEach(function(x) { html += '<span class="psc-tag disease">' + x + '</span>'; });
    else html += '<span class="psc-none">None</span>';
    html += '</div></div>';
    html += '<div class="psc-section"><p class="psc-section-label">Symptoms</p><div class="psc-tags">';
    if (d.symptoms.length) d.symptoms.forEach(function(x) { html += '<span class="psc-tag symptom">' + x + '</span>'; });
    else html += '<span class="psc-none">None</span>';
    html += '</div></div>';
    html += '<div class="psc-section"><p class="psc-section-label">Medications</p><div class="psc-tags">';
    if (d.drugs.length) d.drugs.forEach(function(x) { html += '<span class="psc-tag drug">' + x + '</span>'; });
    else html += '<span class="psc-none">None</span>';
    html += '</div></div>';
    if (d.violations.length) {
      html += '<div class="psc-section"><p class="psc-section-label">Violations</p><div class="psc-tags">';
      d.violations.forEach(function(v) { html += '<span class="psc-tag violation-' + v.severity + '">' + v.text + '</span>'; });
      html += '</div></div>';
    }
    if (d.clinical) {
      html += '<div class="psc-section"><p class="psc-section-label">Vitals</p><div class="psc-tags">';
      if (d.clinical.sofa != null) html += '<span class="psc-tag symptom">SOFA ' + d.clinical.sofa + '</span>';
      if (d.clinical.map != null) html += '<span class="psc-tag symptom">MAP ' + d.clinical.map + '</span>';
      if (d.clinical.lactate != null) html += '<span class="psc-tag symptom">Lactate ' + d.clinical.lactate + '</span>';
      if (d.clinical.gcs != null) html += '<span class="psc-tag symptom">GCS ' + d.clinical.gcs + '</span>';
      if (d.clinical.creatinine != null) html += '<span class="psc-tag symptom">Creatinine ' + d.clinical.creatinine + '</span>';
      html += '</div></div>';
    }
    content.innerHTML = html;
    card.classList.add('active');
  }

  var _insightCounter = 0;
  function renderInsightPanel(pid) {
    var panel = document.getElementById('insightPanel');
    var list = document.getElementById('insightList');
    if (!panel || !list) return;
    if (!pid) { panel.classList.remove('active'); list.innerHTML = ''; return; }
    var d = _getConnectedByType(pid);
    var insights = [];
    var c = d.clinical;
    if (c) {
      if (c.map != null && c.map < 65) insights.push({ icon: '\\u26a0', text: '<strong>Low MAP (' + c.map + ' mmHg)</strong> &mdash; below 65 mmHg threshold', cls: 'critical',
        reasons: ['MAP reading is <span class="reason-val">' + c.map + ' mmHg</span>', 'Clinical threshold is 65 mmHg (Surviving Sepsis Campaign)', 'Low MAP suggests inadequate tissue perfusion', 'Consider vasopressor therapy if fluid-unresponsive'] });
      if (c.sofa != null && c.sofa >= 8) insights.push({ icon: '\\u26a0', text: '<strong>High SOFA score (' + c.sofa + ')</strong> &mdash; significant organ dysfunction', cls: 'critical',
        reasons: ['SOFA score is <span class="reason-val">' + c.sofa + '</span> (normal &lt; 2)', 'Score \\u2265 8 indicates multi-organ failure risk', 'Each point increase above 2 raises mortality risk'] });
      else if (c.sofa != null && c.sofa >= 2) insights.push({ icon: '\\u25b2', text: '<strong>Elevated SOFA (' + c.sofa + ')</strong> &mdash; monitor closely', cls: '',
        reasons: ['SOFA score is <span class="reason-val">' + c.sofa + '</span> (baseline is 0\\u20131)', 'Score \\u2265 2 indicates possible organ dysfunction', 'Trend monitoring recommended'] });
      if (c.lactate != null && c.lactate > 4) insights.push({ icon: '\\u26a0', text: '<strong>High lactate (' + c.lactate + ' mmol/L)</strong> &mdash; tissue hypoperfusion', cls: 'critical',
        reasons: ['Lactate is <span class="reason-val">' + c.lactate + ' mmol/L</span>', 'Level &gt; 4 mmol/L is a sepsis severity marker', 'Indicates anaerobic metabolism from poor perfusion', 'Repeat measurement in 2\\u20134 hours recommended'] });
      else if (c.lactate != null && c.lactate > 2) insights.push({ icon: '\\u25b2', text: '<strong>Elevated lactate (' + c.lactate + ' mmol/L)</strong> &mdash; recheck recommended', cls: '',
        reasons: ['Lactate is <span class="reason-val">' + c.lactate + ' mmol/L</span> (normal &lt; 2)', 'Intermediate elevation may indicate early perfusion deficit'] });
      if (c.gcs != null && c.gcs < 13) insights.push({ icon: '\\u26a0', text: '<strong>Low GCS (' + c.gcs + ')</strong> &mdash; altered mental status', cls: 'critical',
        reasons: ['GCS is <span class="reason-val">' + c.gcs + '</span> (normal 15)', 'Score &lt; 13 suggests moderate to severe impairment', 'May indicate CNS involvement or metabolic encephalopathy'] });
      if (c.creatinine != null && c.creatinine > 2.0) insights.push({ icon: '\\u26a0', text: '<strong>Elevated creatinine (' + c.creatinine + ')</strong> &mdash; possible renal impairment', cls: 'critical',
        reasons: ['Creatinine is <span class="reason-val">' + c.creatinine + ' mg/dL</span>', 'Level &gt; 2.0 mg/dL suggests acute kidney injury', 'May contribute to SOFA score elevation', 'Monitor urine output and consider nephrology consult'] });
      else if (c.creatinine != null && c.creatinine > 1.2) insights.push({ icon: '\\u25b2', text: '<strong>Creatinine slightly elevated (' + c.creatinine + ')</strong>', cls: '',
        reasons: ['Creatinine is <span class="reason-val">' + c.creatinine + ' mg/dL</span> (normal 0.6\\u20131.2)', 'Monitor for further increase'] });
    }
    var hasSepsis = d.diseases.some(function(x) { return x.toLowerCase().indexOf('sepsis') >= 0; });
    var hasFever = d.symptoms.some(function(x) { return x.toLowerCase().indexOf('fever') >= 0; });
    var hasHypotension = d.symptoms.some(function(x) { return x.toLowerCase().indexOf('hypotension') >= 0; });
    if (hasSepsis) {
      var sepsisReasons = ['Patient has a <span class="reason-val">sepsis</span> diagnosis'];
      if (c && c.sofa != null) sepsisReasons.push('SOFA score: <span class="reason-val">' + c.sofa + '</span>');
      if (c && c.lactate != null) sepsisReasons.push('Lactate: <span class="reason-val">' + c.lactate + ' mmol/L</span>');
      if (c && c.map != null) sepsisReasons.push('MAP: <span class="reason-val">' + c.map + ' mmHg</span>');
      sepsisReasons.push('Sepsis-3 bundle: antibiotics within 1h, blood cultures, 30 mL/kg crystalloid if hypotensive');
      insights.push({ icon: '\\u26a0', text: '<strong>Sepsis diagnosis present</strong> &mdash; ensure bundle compliance', cls: 'critical', reasons: sepsisReasons });
    } else if (hasFever && (c && c.sofa != null && c.sofa >= 2)) {
      insights.push({ icon: '\\u2139', text: '<strong>Possible sepsis pattern</strong> &mdash; fever + elevated SOFA', cls: '',
        reasons: ['Symptom: <span class="reason-val">fever</span> is present', 'SOFA score: <span class="reason-val">' + c.sofa + '</span> (\\u2265 2)', 'Combination suggests possible infection-driven organ dysfunction', 'Consider blood cultures and empiric antibiotics if clinical suspicion is high'] });
    }
    if (hasHypotension && (!c || c.map == null || c.map >= 65)) insights.push({ icon: '\\u2139', text: '<strong>Hypotension symptom noted</strong> &mdash; verify current MAP', cls: 'info',
      reasons: ['Symptom: <span class="reason-val">hypotension</span> documented', c && c.map != null ? 'Current MAP reading: <span class="reason-val">' + c.map + ' mmHg</span>' : 'No current MAP reading available', 'Verify latest vitals and reassess fluid status'] });
    var critViolations = d.violations.filter(function(v) { return v.severity === 'critical'; });
    if (critViolations.length) {
      var vReasons = critViolations.map(function(v) { return '<span class="reason-val">' + v.text + '</span>'; });
      vReasons.push('Critical violations indicate missed mandatory protocol steps');
      insights.push({ icon: '\\u26d4', text: '<strong>' + critViolations.length + ' critical violation' + (critViolations.length > 1 ? 's' : '') + '</strong> &mdash; review protocol', cls: 'critical', reasons: vReasons });
    }
    if (!d.violations.length && d.diseases.length) insights.push({ icon: '\\u2705', text: '<strong>No violations detected</strong> &mdash; protocol-compliant', cls: 'good',
      reasons: ['All ' + d.diseases.length + ' disease protocol(s) checked', 'No missing treatments or procedures identified', 'Patient is following recommended clinical pathways'] });
    if (!d.drugs.length && d.diseases.length) insights.push({ icon: '\\u2139', text: '<strong>No medications recorded</strong> &mdash; verify treatment plan', cls: 'info',
      reasons: ['Patient has ' + d.diseases.length + ' disease(s) but no drugs in the graph', 'Medications may not have been documented', 'Review pharmacy records or treatment orders'] });
    if (!insights.length) insights.push({ icon: '\\u2139', text: 'No notable clinical insights for this patient', cls: 'info', reasons: [] });
    var html = '';
    insights.forEach(function(ins, idx) {
      var uid = 'insR' + (++_insightCounter);
      html += '<div class="insight-item ' + ins.cls + '"><span class="insight-icon">' + ins.icon + '</span><span class="insight-text">' + ins.text;
      if (ins.reasons && ins.reasons.length) {
        html += '<span class="insight-why-toggle" onclick="event.stopPropagation();var r=document.getElementById(\\'' + uid + '\\');r.classList.toggle(\\'open\\');this.textContent=r.classList.contains(\\'open\\')?\\'\u25b4 Hide\\':\\'\u25be Why?\\'">\\u25be Why?</span>';
        html += '<div class="insight-reasons" id="' + uid + '"><ul>';
        ins.reasons.forEach(function(r) { html += '<li>' + r + '</li>'; });
        html += '</ul></div>';
      }
      html += '</span></div>';
    });
    list.innerHTML = html;
    panel.classList.add('active');
  }

  /* ===== Global Patient Context ===== */
  var _patientContext = { selected: null, selectedMulti: [] };

  function _getAllPatientNodes() {
    var nodes = window._allNodes || [];
    var pts = [];
    nodes.forEach(function(n) {
      if (n.node_type === 'Patient' && n.id_prop) {
        var src = n.patient_source;
        if (src == null && window._patientSourceById) src = window._patientSourceById[n.id_prop];
        pts.push({ pid: n.id_prop, name: n.label || n.id_prop, visId: n.id, patient_source: src });
      }
    });
    pts.sort(function(a, b) { return a.name.localeCompare(b.name); });
    return pts;
  }

  function _patientSourceIsMimic(src) {
    return src === 'mimic_sample';
  }

  function _buildPsList(filter) {
    var el = document.getElementById('psList');
    if (!el) return;
    var pts = _getAllPatientNodes();
    var q = (filter || '').toLowerCase();
    function matches(p) {
      if (!q) return true;
      return p.name.toLowerCase().indexOf(q) >= 0 || p.pid.toLowerCase().indexOf(q) >= 0;
    }
    var real = [];
    var sample = [];
    pts.forEach(function(p) {
      if (!matches(p)) return;
      if (_patientSourceIsMimic(p.patient_source)) sample.push(p);
      else real.push(p);
    });
    el.innerHTML = '';
    function addRow(p) {
      var div = document.createElement('div');
      div.className = 'ps-item' + (_patientSourceIsMimic(p.patient_source) ? ' mimic-sample' : '');
      div.innerHTML = '<span class="ps-dot"></span><span class="ps-name">' + p.name + '</span><span class="ps-id">' + p.pid + '</span>';
      div.onclick = function() { selectGlobalPatient(p.pid, p.name, p.visId); };
      el.appendChild(div);
    }
    var twoGroups = real.length > 0 && sample.length > 0;
    var onlySampleGroup = sample.length > 0 && real.length === 0;
    if (twoGroups) {
      var hr = document.createElement('div');
      hr.className = 'ps-group-label';
      hr.textContent = 'Real patients';
      el.appendChild(hr);
      real.forEach(addRow);
      var hs = document.createElement('div');
      hs.className = 'ps-group-label';
      hs.textContent = 'Sample patients (MIMIC)';
      el.appendChild(hs);
      sample.forEach(addRow);
    } else {
      if (onlySampleGroup) {
        var hx = document.createElement('div');
        hx.className = 'ps-group-label';
        hx.textContent = 'Sample patients (MIMIC)';
        el.appendChild(hx);
      }
      real.forEach(addRow);
      sample.forEach(addRow);
    }
    if (!el.querySelector('.ps-item')) el.innerHTML = '<p style="color:#94a3b8;font-size:0.8125rem;text-align:center;padding:1rem;">No patients found</p>';
  }
  window.filterPsPatients = function(val) { _buildPsList(val); };

  function selectGlobalPatient(pid, name, visId) {
    _patientContext.selected = { pid: pid, name: name, visId: visId };
    _patientContext.selectedMulti = [{ pid: pid, name: name, visId: visId }];
    dismissPatientSelector();
    applyPatientContext();
  }

  function dismissPatientSelector() {
    var overlay = document.getElementById('patientSelectorOverlay');
    if (overlay) overlay.style.display = 'none';
  }
  window.dismissPatientSelector = dismissPatientSelector;

  function changePatient() {
    var overlay = document.getElementById('patientSelectorOverlay');
    if (overlay) {
      overlay.style.display = 'flex';
      _buildPsList('');
      var search = document.getElementById('psSearch');
      if (search) { search.value = ''; search.focus(); }
    }
  }
  window.changePatient = changePatient;

  function clearGlobalPatient() {
    _patientContext.selected = null;
    _patientContext.selectedMulti = [];
    applyPatientContext();
  }
  window.clearGlobalPatient = clearGlobalPatient;

  function renderViewingBar() {
    var bar = document.getElementById('viewingBar');
    if (!bar) return;
    bar.className = 'viewing-bar';
    if (!_patientContext.selected) {
      bar.innerHTML = '<span class="viewing-label">Viewing:</span>'
        + '<span class="viewing-patient">All patients</span>'
        + '<button class="viewing-change" onclick="changePatient()">Select a patient</button>';
      return;
    }
    bar.innerHTML = '<span class="viewing-label">Viewing:</span>'
      + '<span class="viewing-patient">' + _patientContext.selected.name + ' (' + _patientContext.selected.pid + ')</span>'
      + '<button class="viewing-change" onclick="openTimeline(\\'' + _patientContext.selected.pid + '\\')" style="color:#3b82f6;text-decoration:none;font-weight:600;">View Timeline</button>'
      + '<button class="viewing-change" onclick="clearGlobalPatient()">Show all patients</button>'
      + '<button class="viewing-change" onclick="changePatient()">Change patient</button>';
  }

  function filterGraphToPatient() {
    var net = getNet();
    if (!net || !net.body || !net.body.data) return;
    try { clearEgoHighlight(); } catch (e) {}
    var allNodes = window._allNodes;
    var allEdges = window._allEdges;
    if (!allNodes || !allNodes.length) return;

    if (!_patientContext.selected) {
      net.body.data.nodes.update(allNodes);
      net.body.data.edges.update(allEdges);
      try {
        var fullN = new vis.DataSet(allNodes);
        var fullE = new vis.DataSet(allEdges);
        net.setData({ nodes: fullN, edges: fullE });
      } catch(e) {}
      document.getElementById('filterLabel').textContent = 'Showing: All';
      var es = document.getElementById('graphEmptyState');
      if (es) es.style.display = 'none';
      return;
    }

    var pid = _patientContext.selected.pid;
    var nodeMap = {};
    allNodes.forEach(function(n) { nodeMap[n.id] = n; });
    var patientVisId = null;
    allNodes.forEach(function(n) { if (n.node_type === 'Patient' && n.id_prop === pid) patientVisId = n.id; });

    if (!patientVisId) return;

    var connectedIds = {};
    connectedIds[patientVisId] = true;
    allEdges.forEach(function(e) {
      if (e.from === patientVisId) connectedIds[e.to] = true;
      if (e.to === patientVisId) connectedIds[e.from] = true;
    });
    var secondLevel = {};
    allEdges.forEach(function(e) {
      if (connectedIds[e.from] && !connectedIds[e.to]) secondLevel[e.to] = true;
      if (connectedIds[e.to] && !connectedIds[e.from]) secondLevel[e.from] = true;
    });
    Object.keys(secondLevel).forEach(function(id) { connectedIds[id] = true; });

    var filteredN = allNodes.filter(function(n) { return connectedIds[n.id]; });
    var filteredE = allEdges.filter(function(e) { return connectedIds[e.from] && connectedIds[e.to]; });

    try {
      net.setData({ nodes: new vis.DataSet(filteredN), edges: new vis.DataSet(filteredE) });
      document.getElementById('filterLabel').textContent = 'Viewing: ' + _patientContext.selected.name;
    } catch(e) {}
  }

  function applyPatientContext() {
    renderViewingBar();
    filterGraphToPatient();
    var pid = _patientContext.selected ? _patientContext.selected.pid : null;
    renderPatientSummary(pid);
    renderInsightPanel(pid);
    var sp = document.getElementById('statsPanel');
    var vp = document.getElementById('violationsPanel');
    if (sp) sp.style.display = pid ? 'none' : '';
    if (vp) vp.style.display = pid ? 'none' : '';
    if (_patientContext.selected) {
      _aiSelectedPatients = [{ pid: _patientContext.selected.pid, name: _patientContext.selected.name, visId: _patientContext.selected.visId }];
    } else {
      _aiSelectedPatients = [];
    }
    renderAiContextBar();
    updatePatientSelectionVisuals();
    var sel = document.getElementById('filterPatient');
    if (sel) sel.value = _patientContext.selected ? _patientContext.selected.pid : '';
  }

  document.addEventListener('DOMContentLoaded', function() {
    initDashboard();
    var dz = document.getElementById('uploadDropzone');
    if (dz) {
      dz.addEventListener('click', function() { document.getElementById('uploadFileInput').click(); });
      dz.addEventListener('dragover', function(e) { e.preventDefault(); dz.classList.add('dragover'); });
      dz.addEventListener('dragleave', function() { dz.classList.remove('dragover'); });
      dz.addEventListener('drop', function(e) {
        e.preventDefault(); dz.classList.remove('dragover');
        if (e.dataTransfer.files.length) handleFileSelect(e.dataTransfer.files[0]);
      });
    }
    function attachToGraph() {
      var net = getNet();
      if (net) {
        if (!window._graphHooksDone) {
          window._graphHooksDone = true;
          net.on('click', function(params) {
            if (params.nodes && params.nodes.length) {
              var clickedId = params.nodes[0];
              applyEgoHighlight(clickedId);
              togglePatientSelection(clickedId);
              showExplanation(clickedId);
            } else {
              clearEgoHighlight();
            }
          });
          net.on('zoom', function(p) { onGraphZoom(p); });
        }
        try {
          var sc = typeof net.getScale === 'function' ? net.getScale() : 1;
          applyZoomProgressiveDetail(sc);
        } catch (e) {}
        populateFilterOptions();
        syncPatientsFromBackend(function() {
          _buildPsList('');
        });
        renderAiContextBar();
      } else {
        setTimeout(attachToGraph, 100);
      }
    }
    setTimeout(attachToGraph, 100);
  });
</script>
"""


def inject_dashboard_into_html(html: str, sidebar_and_script: str) -> str:
    """Inject sidebar layout and script. Put #mynetwork inside .dashboard-main (between filter and explain) so the graph sits to the right of the sidebar in one view."""
    # Inject Google Fonts and viewport meta
    if "<head>" in html:
        font_tags = (
            '<link rel="preconnect" href="https://fonts.googleapis.com">'
            '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
            '<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">'
            '<meta name="viewport" content="width=device-width, initial-scale=1.0">'
            '<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js"></script>'
        )
        html = html.replace("<head>", "<head>\n" + font_tags, 1)
    # Extract the mynetwork div from pyvis output (card contains it)
    mynetwork_pattern = re.compile(r'<div id="mynetwork"[^>]*></div>', re.IGNORECASE)
    match = mynetwork_pattern.search(html)
    mynetwork_div = match.group(0) if match else '<div id="mynetwork" class="card-body"></div>'
    # Put mynetwork inside the right-hand column (between filter and explain)
    sidebar_with_graph = sidebar_and_script.replace("MYNETWORK_PLACEHOLDER", mynetwork_div)
    if "<body>" in html:
        html = html.replace("<body>", "<body>\n" + sidebar_with_graph, 1)
    # Remove the original card block (so we don't have a second mynetwork below the wrapper)
    card_pattern = re.compile(
        r'<div class="card"[^>]*>\s*<div id="mynetwork"[^>]*></div>\s*</div>',
        re.DOTALL | re.IGNORECASE,
    )
    html = card_pattern.sub("", html, count=1)
    # After drawGraph(): expose network and store full nodes/edges for filter reset
    html = re.sub(
        r'(\s+drawGraph\(\)\s*;)',
        r'''\1 try {
          if (typeof network !== "undefined" && network && network.body && network.body.data) {
            window.network = network;
            var _nr = network.body.data.nodes.get();
            var _er = network.body.data.edges.get();
            window._allNodes = Array.isArray(_nr) ? _nr : (_nr ? Object.keys(_nr).map(function(k){ return _nr[k]; }) : []);
            window._allEdges = Array.isArray(_er) ? _er : (_er ? Object.keys(_er).map(function(k){ return _er[k]; }) : []);
          }
        } catch(e) {}''',
        html,
        count=1,
    )
    # When stabilization finishes, store nodes/edges and hide loading bar (backup)
    html = re.sub(
        r"(setTimeout\(function\s*\(\)\s*\{document\.getElementById\('loadingBar'\)\.style\.display\s*=\s*'none';\}, 500\);)",
        r'''\1
                          try { var _r = network.body.data.nodes.get(); var _s = network.body.data.edges.get();
                            window._allNodes = Array.isArray(_r) ? _r : (_r ? Object.keys(_r).map(function(k){ return _r[k]; }) : []);
                            window._allEdges = Array.isArray(_s) ? _s : (_s ? Object.keys(_s).map(function(k){ return _s[k]; }) : []);
                          } catch(e) {}
                          var _lb = document.getElementById('loadingBar'); if (_lb) { _lb.style.display = 'none'; _lb.style.opacity = '0'; }''',
        html,
        count=1,
    )
    # Force-hide loading bar after 4s so it never stays stuck at 100%
    html = re.sub(
        r'(\s+drawGraph\(\)\s*;)',
        r'''\1
          setTimeout(function() {
            var lb = document.getElementById('loadingBar');
            if (lb) { lb.style.display = 'none'; lb.style.opacity = '0'; }
          }, 4000);''',
        html,
        count=1,
    )
    html = html.replace("</body>", "</body>", 1)
    return html


def run_dashboard():
    """Build graph, inject dashboard UI, save to compliance_dashboard.html, open in browser."""
    net, stats, explanations = build_dashboard_graph()
    if net is None:
        print("No graph data. Run seed_data.py and ensure Neo4j is populated.")
        return
    path = Path(OUTPUT_HTML)
    net.save_graph(str(path))
    html = path.read_text(encoding="utf-8")
    sidebar = _sidebar_and_script(stats or {}, explanations or {})
    html = inject_dashboard_into_html(html, sidebar)
    path.write_text(html, encoding="utf-8")
    webbrowser.open(path.as_uri())
    print(f"Dashboard saved: {path}")
    print("Use the sidebar for legend and stats; click a node for protocol explanation; use filters to explore.")


if __name__ == "__main__":
    print("Running compliance check and building dashboard...")
    run_dashboard()
