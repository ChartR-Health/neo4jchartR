"""Plain-text edge tooltips for vis-network / pyvis (native title attribute does not render HTML)."""

from __future__ import annotations

import re

# Neo4j labels → short nouns (used in edge explanation text)
_TYPE_NOUN: dict[str, str] = {
    "Patient": "patient",
    "Doctor": "provider",
    "Disease": "condition",
    "Drug": "medication",
    "Procedure": "procedure",
    "Symptom": "symptom",
    "Hospital": "facility",
    "Appointment": "appointment",
    "Encounter": "encounter",
    "Lab": "lab result",
    "PatientNote": "clinical note",
    "ClinicalState": "clinical severity state",
    "Violation": "protocol issue",
    "FollowUp": "follow-up",
    "SepsisGuideline": "guideline reference",
    "RecommendedAction": "recommended action",
    "LabCheck": "lab check",
}


def _noun(label: str | None) -> str:
    if not label or label == "Unknown":
        return "record"
    return _TYPE_NOUN.get(label, label.replace("_", " ").lower())


def _strip_html(text: str | None) -> str:
    if not text:
        return ""
    s = re.sub(r"<[^>]+>", " ", str(text))
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _explain_edge(rel_type: str, sn: str, tn: str, st: str | None, tt: str | None) -> str:
    """
    One or two sentences: what this edge means for these endpoints (uses names + types).
    """
    a = _noun(st)
    b = _noun(tt)

    if rel_type == "HAS_NOTE":
        return (
            f"Ties written documentation (“{tn}”, {b}) to patient {sn}. "
            "Notes are free-text chart entries—visit summaries, clinician observations, or instructions—not structured orders."
        )
    if rel_type == "HAS_DISEASE":
        return (
            f"Records that patient {sn} has an active diagnosis: “{tn}” ({b}). "
            "This is the condition anchor used when comparing real treatment to protocol guidance."
        )
    if rel_type == "HAS_SYMPTOM":
        return f"Documents symptom “{tn}” for patient {sn}—a reported complaint or finding relevant to risk and workup."
    if rel_type == "HAS_VIOLATION":
        return (
            f"Highlights a structured protocol gap involving “{tn}” ({b}) for patient {sn}. "
            "Use the violation node for severity and rationale."
        )
    if rel_type == "HAS_CLINICAL_STATE":
        return (
            f"Attaches a severity snapshot (“{tn}”) to patient {sn}: scores such as SOFA, MAP, lactate, or GCS "
            "used for monitoring (e.g. sepsis trajectory)."
        )
    if rel_type == "TREATED_WITH":
        return (
            f"Shows medication “{tn}” on {sn}’s active treatment list. "
            "Edges from protocol paths indicate whether therapy aligns with guideline drugs for the diagnosis."
        )
    if rel_type == "HAD_PROCEDURE":
        return (
            f"Documents procedure “{tn}” performed or assigned for patient {sn}. "
            "Compared against recommended procedures for the relevant disease pathway."
        )
    if rel_type == "RECOMMENDED_DRUG":
        return (
            f"Protocol edge: for the linked disease context, “{tn}” is a guideline-recommended medication option "
            "(standard-of-care pathway, not necessarily what was dispensed)."
        )
    if rel_type == "RECOMMENDED_PROCEDURE":
        return (
            f"Protocol edge: “{tn}” is a recommended step or procedure in the disease pathway "
            "(ordering/labs/monitoring expected by the guideline graph)."
        )
    if rel_type == "FOLLOW_UP":
        return f"Follow-up step “{tn}” in a pathway—timing or modality expected after initial disease management."
    if rel_type == "FOLLOWED_UP_WITH":
        return f"Documents follow-up care “{tn}” actually recorded for patient {sn} (scheduled or completed)."
    if rel_type == "TREATS":
        if st == "Doctor" and tt == "Patient":
            return f"Provider {sn} is recorded as treating patient {tn} (assigned clinical responsibility)."
        if st == "Patient" and tt == "Doctor":
            return f"Patient {sn} is under care of provider {tn} (treatment relationship)."
        return f"Treatment responsibility link between “{sn}” ({a}) and “{tn}” ({b})."
    if rel_type == "VISITS":
        if st == "Patient" and tt == "Doctor":
            return f"Visit link: patient {sn} saw or is aligned with provider {tn} (face-to-face or documented encounter)."
        if st == "Doctor" and tt == "Patient":
            return f"Visit link: provider {sn} is connected to patient {tn} through a documented encounter."
        return f"Encounter-style link between “{sn}” ({a}) and “{tn}” ({b})."
    if rel_type == "HAS_APPOINTMENT":
        return f"Scheduled appointment “{tn}” tied to patient {sn} (reason, timing, or status live on the appointment node)."
    if rel_type == "AT_HOSPITAL":
        return f"Associates “{tn}” ({b}) with the appointment or visit—where services occurred."
    if rel_type == "HAS_ENCOUNTER":
        return f"Encounter episode “{tn}”: visit-level documentation that can bundle labs, meds, and procedures."
    if rel_type == "ORDERED_LAB":
        return (
            f"Lab “{tn}” ordered or resulted in context of the linked encounter or patient—vitals, chemistry, imaging labels, etc."
        )
    if rel_type == "PRESCRIBED":
        return f"Medication “{tn}” documented at encounter level (dose, duration, indication may appear on the drug node)."
    if rel_type == "INCLUDES_PROCEDURE":
        return f"Procedure “{tn}” performed or captured during the encounter (endoscopy, imaging, monitoring)."
    if rel_type == "PERFORMED_BY":
        return f"Clinician “{tn}” linked as performer for the encounter or activity on the left."
    # Default: still name types
    return (
        f"Connects a {a} (“{sn}”) to a {b} (“{tn}”). "
        "Use node tooltips for identifiers and properties; this edge shows how those records relate."
    )


def edge_tooltip_plain(
    rel_type: str,
    source_label: str,
    target_label: str,
    *,
    source_type: str | None = None,
    target_type: str | None = None,
    violation: bool = False,
    violation_detail: str | None = None,
    compliant_pathway: bool = False,
) -> str:
    """
    Readable tooltip for graph edges (plain text, no HTML).
    """
    sn = (source_label or "").strip() or "—"
    tn = (target_label or "").strip() or "—"
    st = source_type if source_type and source_type != "Unknown" else None
    tt = target_type if target_type and target_type != "Unknown" else None

    explain = _explain_edge(rel_type, sn, tn, st, tt)
    parts: list[str] = []

    if violation or violation_detail:
        vd = _strip_html(violation_detail) if violation_detail else ""
        parts.append("Protocol concern: this edge is flagged because care may diverge from the recommended pathway.")
        parts.append(explain)
        if vd:
            parts.append("")
            parts.append(vd)
    else:
        parts.append(explain)
        if compliant_pathway:
            parts.append("")
            parts.append("Along this disease/treatment chain, this step sits on the guideline-compliant path in the graph.")

    return "\n".join(parts)
