"""
Plain-text, clinically framed node tooltips for vis-network (native `title` — no HTML).

Structure:
  Line 1: Type — Name (title role)
  Blank
  1–2 sentence description (patient context)
  Blank
  Key details (compact labeled lines)
"""

from __future__ import annotations

from typing import Any

MISSING = "—"


def _clean_str(val: Any) -> str:
    if val is None or val == "":
        return ""
    return str(val).strip()


def _human_key(key: str) -> str:
    """Turn snake_case keys into short labels for detail lines."""
    if not key:
        return ""
    kl = key.lower()
    if kl in ("id", "uuid") or kl.endswith("_id"):
        return ""
    k = key.replace("_", " ").strip()
    parts = k.split()
    out = []
    for p in parts:
        out.append(p[:1].upper() + p[1:] if len(p) > 1 else p.upper())
    return " ".join(out)


def _detail_line(label: str, value: Any) -> str | None:
    v = _clean_str(value)
    if not v or v.lower() in ("none", "null", "undefined"):
        return None
    return f"{label}: {v}"


def _truncate(text: str, max_len: int = 220) -> str:
    t = text.strip()
    if len(t) <= max_len:
        return t
    return t[: max(1, max_len - 1)] + "…"


def format_node_tooltip_plain(
    neo4j_label: str,
    display_name: str,
    props: dict[str, Any] | None,
    *,
    patient_diseases: list[str] | None = None,
    doctor_compliance_pct: str | None = None,
    recommended_catalog: bool = False,
) -> str:
    """
    Build standardized multiline plain-text tooltip. Does not mutate props.
    Omit internal Neo4j/technical dumps; favor clinical readability.
    """
    props = props or {}
    name = _clean_str(display_name) or _clean_str(props.get("name")) or MISSING
    parts: list[str] = []

    def block(title_line: str, description: str, detail_lines: list[str | None]) -> None:
        parts.append(title_line)
        parts.append("")
        parts.append(description)
        details = [d for d in detail_lines if d]
        if details:
            parts.append("")
            parts.extend(details)

    # --- Patient ---
    if neo4j_label == "Patient":
        age = props.get("age")
        sex = props.get("sex")
        dis = patient_diseases if patient_diseases is not None else []
        dis_txt = ", ".join(dis) if dis else MISSING
        extra: list[str | None] = []
        if age not in (None, ""):
            extra.append(_detail_line("Age", age))
        if sex not in (None, ""):
            extra.append(_detail_line("Sex", sex))
        extra.append(_detail_line("Recorded conditions", dis_txt))
        pid = _clean_str(props.get("id"))
        if pid and pid != name:
            extra.append(_detail_line("Patient identifier", pid))
        block(
            f"Patient — {name}",
            "Primary subject of this clinical graph; other nodes describe care linked to this person.",
            extra,
        )
        return "\n".join(parts)

    # --- Disease ---
    if neo4j_label == "Disease":
        icd = props.get("icd10")
        extra = [
            _detail_line("ICD-10", icd) if icd else None,
        ]
        block(
            f"Disease — {name}",
            "Condition diagnosed or tracked for this patient; used when comparing treatment to protocol pathways.",
            extra,
        )
        return "\n".join(parts)

    # --- Symptom ---
    if neo4j_label == "Symptom":
        block(
            f"Symptom — {name}",
            "Observed or reported symptom tied to this patient for assessment and documentation.",
            [],
        )
        return "\n".join(parts)

    # --- Drug ---
    if neo4j_label == "Drug":
        dose = props.get("dose") or props.get("dose_strength")
        freq = props.get("frequency")
        ind = props.get("indication")
        lines: list[str | None] = [
            _detail_line("Dose", dose),
            _detail_line("Frequency", freq),
            _detail_line("Indication", ind),
        ]
        desc = (
            "Medication relevant to this patient’s care (ordered, prescribed, or on the treatment plan)."
        )
        if recommended_catalog:
            desc = (
                "Medication appearing on a guideline pathway in this graph. "
                "This represents standard-of-care recommendation from linked disease protocols—not confirmed administration by itself."
            )
            lines.insert(0, "Note: Clinical recommendation from pathway logic, not a proof of dispensing.")
        block(f"Drug — {name}", desc, lines)
        return "\n".join(parts)

    # --- Procedure ---
    if neo4j_label == "Procedure":
        desc = "Procedure tied to evaluation or treatment for this patient."
        lines: list[str | None] = []
        if recommended_catalog:
            desc = (
                "Procedure suggested by linked guideline pathways in this graph. "
                "Indicates recommended standard-of-care step—not necessarily a completed procedure record."
            )
            lines.append("Note: Pathway recommendation, not confirmation the procedure was performed.")
        block(f"Procedure — {name}", desc, lines)
        return "\n".join(parts)

    # --- Doctor ---
    if neo4j_label == "Doctor":
        spec = props.get("specialty")
        lines: list[str | None] = [_detail_line("Specialty", spec)]
        if doctor_compliance_pct:
            lines.append(_detail_line("Pathway adherence score", doctor_compliance_pct))
        block(
            f"Doctor — {name}",
            "Healthcare provider connected to this patient’s visits, treatment decisions, or encounters.",
            lines,
        )
        return "\n".join(parts)

    # --- Encounter ---
    if neo4j_label == "Encounter":
        lines = [
            _detail_line("Encounter type", props.get("type")),
            _detail_line("Date", props.get("date")),
            _detail_line("Notes summary", _truncate(_clean_str(props.get("notes")), 120) or None),
        ]
        block(
            f"Encounter — {name}",
            "Clinical interaction (visit, admission episode, or documented encounter) where orders and findings attach.",
            lines,
        )
        return "\n".join(parts)

    # --- Appointment ---
    if neo4j_label == "Appointment":
        lines = [
            _detail_line("Date", props.get("date")),
            _detail_line("Reason", props.get("reason")),
            _detail_line("Status", props.get("status")),
        ]
        block(
            f"Appointment — {name}",
            "Scheduled visit or booking linked to this patient’s care timeline.",
            lines,
        )
        return "\n".join(parts)

    # --- Hospital ---
    if neo4j_label == "Hospital":
        block(
            f"Hospital — {name}",
            "Facility where care was delivered or scheduled for this patient.",
            [],
        )
        return "\n".join(parts)

    # --- Lab ---
    if neo4j_label == "Lab":
        lines = [
            _detail_line("Result", props.get("result_value")),
            _detail_line("Unit", props.get("unit")),
            _detail_line("Status", props.get("status")),
            _detail_line("Date", props.get("date")),
        ]
        block(
            f"Lab — {name}",
            "Laboratory result or study ordered in linked encounters and tied to clinical decisions.",
            lines,
        )
        return "\n".join(parts)

    # --- Violation ---
    if neo4j_label == "Violation":
        sev = _clean_str(props.get("severity")).upper() or "WARNING"
        desc_text = _clean_str(props.get("description")) or name
        reason = _clean_str(props.get("reason"))
        parts.append("PROTOCOL OR CARE PATH FINDING")
        parts.append("")
        parts.append(f"Violation — {sev}")
        parts.append("")
        parts.append(desc_text)
        if reason:
            parts.append("")
            parts.append(f"Reason: {reason}")
        parts.append("")
        parts.append(
            "Explains where documented care may diverge from pathway expectations for this patient."
        )
        return "\n".join(parts)

    # --- ClinicalState ---
    if neo4j_label == "ClinicalState":
        p = props
        lines = [
            _detail_line("SOFA", p.get("sofa_score")),
            _detail_line("MAP (mmHg)", p.get("map")),
            _detail_line("Lactate (mmol/L)", p.get("lactate")),
            _detail_line("GCS", p.get("gcs")),
            _detail_line("Creatinine (mg/dL)", p.get("creatinine")),
            _detail_line("Antibiotics active", p.get("antibiotics_active")),
            _detail_line("Cultures ordered", p.get("cultures_ordered")),
            _detail_line("Vasopressors", p.get("vasopressors_active")),
        ]
        block(
            f"Clinical state — {name}",
            "Structured severity or monitoring snapshot (e.g. sepsis-related metrics) for trend review.",
            lines,
        )
        return "\n".join(parts)

    # --- PatientNote ---
    if neo4j_label == "PatientNote":
        txt = _clean_str(props.get("text"))
        preview = _truncate(txt, 280) if txt else MISSING
        block(
            f"Clinical note — {name}",
            "Free-text documentation in the chart (progress notes, assessments, instructions).",
            [_detail_line("Preview", preview)],
        )
        return "\n".join(parts)

    # --- FollowUp ---
    if neo4j_label == "FollowUp":
        lines = [
            _detail_line("Date", props.get("date")),
            _detail_line("Status", props.get("status")),
        ]
        block(
            f"Follow-up — {name}",
            "Recommended or scheduled follow-up step in a disease pathway.",
            lines,
        )
        return "\n".join(parts)

    # --- SepsisGuideline ---
    if neo4j_label == "SepsisGuideline":
        desc = _clean_str(props.get("description")) or MISSING
        lines = [
            _detail_line("SOFA threshold", props.get("sofa_threshold_high")),
            _detail_line("Lactate threshold (mmol/L)", props.get("lactate_threshold_mmol")),
            _detail_line("MAP threshold (mmHg)", props.get("map_threshold_mmhg")),
        ]
        parts.append(f"Sepsis guideline — {name}")
        parts.append("")
        parts.append(
            "Reference criteria from clinical sepsis bundles. "
            "This is guidance logic in the graph—not a patient-specific measurement."
        )
        parts.append("")
        parts.append(_truncate(desc, 300))
        for L in lines:
            if L:
                parts.append(L)
        return "\n".join(parts)

    # --- RecommendedAction ---
    if neo4j_label == "RecommendedAction":
        block(
            f"Recommended action — {name}",
            "Suggested bundle step from a guideline (e.g. sepsis care). "
            "Not confirmed as completed for the patient unless linked to actual encounter data.",
            [],
        )
        return "\n".join(parts)

    # --- LabCheck (guideline-style) ---
    if neo4j_label == "LabCheck":
        block(
            f"Lab check — {name}",
            "Guideline-defined laboratory threshold or monitoring concept—not a specific patient lab result row.",
            [],
        )
        return "\n".join(parts)

    # --- Default: other labels ---
    extra_lines: list[str | None] = []
    for k, v in sorted(props.items()):
        if k in ("name",) or str(k).lower() in ("embedding", "embedding_vector"):
            continue
        hk = _human_key(k)
        if not hk:
            continue
        if isinstance(v, (dict, list)):
            continue
        line = _detail_line(hk, v)
        if line:
            extra_lines.append(line)
    title = f"{neo4j_label.replace('_', ' ')} — {name}"
    parts.append(title)
    parts.append("")
    parts.append(
        "Entity in this patient’s care graph; hover edges to see how it connects to other records."
    )
    if extra_lines[:8]:
        parts.append("")
        parts.extend(extra_lines[:8])
    return "\n".join(parts)


def protocol_catalog_node_ids(rows: list[dict]) -> tuple[set[str], set[str]]:
    """Node internal ids reached by RECOMMENDED_DRUG / RECOMMENDED_PROCEDURE edges."""
    drugs: set[str] = set()
    procs: set[str] = set()
    for row in rows:
        rt = row.get("rel_type") or ""
        tid = row.get("tgt_id")
        if not tid:
            continue
        if rt == "RECOMMENDED_DRUG":
            drugs.add(tid)
        elif rt == "RECOMMENDED_PROCEDURE":
            procs.add(tid)
    return drugs, procs
