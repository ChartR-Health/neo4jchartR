"""
Healthcare compliance dashboard AI agent: analyzes Neo4j patient data against clinical
guidelines and returns interactive, graph-ready responses.

Graph nodes: Patient, Doctor, Disease, Drug, Procedure, Hospital, Appointment, Encounter,
Lab, FollowUp, PatientNote, ClinicalState, Violation. (SepsisPatient = Patient with
ClinicalState and optionally Violation for sepsis-related queries.)
Relationships: HAS_DISEASE, TREATED_WITH, TREATS, VISITS, HAS_APPOINTMENT, HAS_ENCOUNTER,
AT_HOSPITAL, RECOMMENDED_DRUG, RECOMMENDED_PROCEDURE, FOLLOW_UP, HAD_PROCEDURE, HAS_NOTE,
HAS_CLINICAL_STATE, HAS_VIOLATION.

Input:
  - ask_agent(question): natural language (patient, doctor, disease, treatment, lab, clinical state)
  - analyze_patient(patient_id) / patient_analysis_to_agent_response(patient_id): single patient

Output JSON (required for dashboard):
  - answer: text summary of compliance or violation
  - violation: true/false if any violation exists
  - protocol_expected: list of recommended drugs, procedures, follow-ups
  - actual_treatment: list of actual treatments received
  - paths: array of path objects for visualization:
      nodes: e.g. ["Patient:P1","Disease:D2","Drug:DRUG2"]
      relationships: e.g. ["HAS_DISEASE","TREATED_WITH"]
      labels: human-readable names for nodes
      colors: { node_colors: [...], edge_colors: [...] } (red=violation, orange=minor, green=compliant, blue=patient, purple=doctor)
      hover_info: details per node/edge (notes, SOFA/lactate/MAP, guideline vs actual)
  - highlight_query: Cypher to reproduce the path in Neo4j for highlighting
  - highlight_nodes, highlight_relationships: for frontend node/edge filtering

Functionality:
  - Disease/clinical feature: highlight all matching patients (including sepsis clinical state)
  - Doctor: highlight all patients under that doctor and their compliance paths
  - Patient: highlight that patient's compliance paths
  - Patient notes and clinical state (SOFA, qSOFA, eSOFA, MAP, lactate, creatinine, vasopressors, antibiotics, cultures) in hover_info
  - Multiple paths when multiple violations or multiple patients match

Uses neo4j_ops (run_query); no hard-coded data. Set OPENAI_API_KEY for LLM explanations.
"""

# Response keys required by the dashboard (do not remove)
AGENT_RESPONSE_KEYS = (
    "answer",
    "violation",
    "protocol_expected",
    "actual_treatment",
    "paths",
    "highlight_query",
    "highlight_nodes",
    "highlight_relationships",
)

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

import json
import os
import re
from typing import Any

from neo4j_ops import (
    get_patient_notes,
    get_patient_full_journey,
    get_protocol_for_disease,
    get_protocol_guidelines,
    get_actual_patient_treatments,
    get_patients_with_diseases,
    get_patients_with_doctor,
    get_doctors_and_specialties,
    get_patients_with_clinical_state,
    get_sepsis_guidelines,
    get_patient_clinical_state,
    get_patients_for_comparison,
)
from ai_compliance import run_compliance_check, check_patient_compliance


# -------- LLM: graph-first reasoning & stable structure (prompts only) --------

_LLM_TEMPERATURE = 0.2

_STRUCTURED_SECTIONS_USER_SUFFIX = (
    "\n\nOutput format (required — use these exact headings, in order):\n"
    "## Conclusion\n"
    "One short paragraph grounded in the context above.\n\n"
    "## Evidence\n"
    "Bullet list. Each bullet MUST cite concrete graph-backed facts from the context "
    "(e.g. diseases/drugs/procedures/violations/symptoms/clinical values with relationship intent: "
    "HAS_DISEASE, HAS_SYMPTOM, HAS_VIOLATION, HAS_CLINICAL_STATE, TREATED_WITH, HAD_PROCEDURE). "
    "Do not list evidence that is only generic medical knowledge with no support in context.\n\n"
    "## Explanation\n"
    "Brief reasoning that connects Evidence to the question; name concrete gaps when supported by context "
    "(for example missing recommended drugs or tests such as spirometry), and avoid deferring to "
    "\"your clinician will explain\" instead of summarizing what the record shows.\n"
)


def _data_priority_preamble() -> list[str]:
    """Prepended to every ask_agent LLM context — establishes graph > notes > general knowledge."""
    return [
        "=== DATA PRIORITY (mandatory) ===",
        "1. PRIMARY: Neo4j graph facts in this context (relationships such as HAS_DISEASE, HAS_SYMPTOM, "
        "HAS_VIOLATION, HAS_CLINICAL_STATE, TREATED_WITH, HAD_PROCEDURE, VISITS, HAS_NOTE).",
        "2. SECONDARY: Verbatim patient notes / encounter text included below.",
        "3. GENERAL KNOWLEDGE: Only when it does not contradict (1) or (2). Never invent patient-specific facts.",
        "=== END DATA PRIORITY ===",
        "",
    ]


def _graph_grounding_prompt_lines(patient_ids: list[str]) -> list[str]:
    """
    Explicit graph edges for the LLM — improves coverage of symptoms, violations, and clinical state
    as stored in Neo4j (same shapes as the visualization graph).
    """
    ids = []
    for p in patient_ids or []:
        n = _normalize_patient_id(p) or p
        if n and n not in ids:
            ids.append(n)
    if not ids:
        return []
    try:
        rows = get_patients_for_comparison(ids)
    except Exception:
        return []
    lines: list[str] = [
        "Authoritative graph edges for selected patient(s) (use in Evidence; name the relationship type):",
    ]
    for row in rows:
        pid = row.get("patient_id")
        pname = row.get("patient_name") or pid
        lines.append(f"  Patient {pid} ({pname}):")
        for d in row.get("diseases") or []:
            did, dnm = d.get("id"), d.get("name")
            lines.append(f"    • (Patient)-[:HAS_DISEASE]->(Disease:{did}) — {dnm or did}")
        for s in row.get("symptoms") or []:
            sid, snm = s.get("id"), s.get("name")
            lines.append(f"    • (Patient)-[:HAS_SYMPTOM]->(Symptom:{sid}) — {snm or sid}")
        for v in row.get("violations") or []:
            lines.append(f"    • (Patient)-[:HAS_VIOLATION]->(Violation) — {v}")
        cs = row.get("clinical_state")
        if cs:
            lines.append(
                "    • (Patient)-[:HAS_CLINICAL_STATE]->(ClinicalState) — "
                f"SOFA={cs.get('sofa_score')}, MAP={cs.get('map')}, lactate={cs.get('lactate')}, "
                f"GCS={cs.get('gcs')}, creatinine={cs.get('creatinine')}"
            )
    lines.append("")
    return lines


def _locked_patient_context_header(patient_id: str, display_name: str) -> str:
    """Unmistakable scope header for a single patient’s LLM block (no cross-patient merge)."""
    pid = _normalize_patient_id(patient_id) or patient_id
    dn = display_name or pid
    return (
        "╔══════════════════════════════════════════════════════════════════════════════╗\n"
        f"║ SINGLE-PATIENT_SCOPE: patient_id={pid}  display_name={dn}\n"
        "║ RULE: Clinical facts below apply ONLY to this Patient node and its outgoing\n"
        "║       relationships (HAS_DISEASE, HAS_SYMPTOM, HAS_VIOLATION, HAS_CLINICAL_STATE,\n"
        "║       TREATED_WITH, HAD_PROCEDURE, HAS_NOTE, etc.).\n"
        "║ NEVER attribute diseases, drugs, symptoms, labs, notes, or violations from\n"
        "║ another patient id. If the question names another patient, respond using ONLY\n"
        f"║ data for patient_id={pid} and state that your context is scoped to {pid}.\n"
        "╚══════════════════════════════════════════════════════════════════════════════╝\n"
    )


def _comparison_context_header(patient_ids: list[str], name_by_pid: dict[str, str]) -> str:
    """Explicit allow-list of patients for comparison mode (prevents extra patients)."""
    lines = [
        "╔══════════════════════════════════════════════════════════════════════════════╗",
        "║ MULTI-PATIENT_COMPARISON_MODE",
        "║ You may ONLY discuss these patient ids with facts drawn from their sections below:",
    ]
    for p in patient_ids:
        pid = _normalize_patient_id(p) or p
        nm = name_by_pid.get(pid) or name_by_pid.get(p) or pid
        lines.append(f"║   • {pid} — {nm}")
    lines.extend(
        [
            "║ Do not add clinical facts for any other patient id. Do not merge timelines.",
            "╚══════════════════════════════════════════════════════════════════════════════╝",
            "",
        ]
    )
    return "\n".join(lines)


# -------- Helpers: normalize patient id (P001 vs P1) --------

def _normalize_patient_id(pid: str | None) -> str | None:
    """Map P001 -> P1, P02 -> P2, etc., for use with graph (ids are P1, P2, ...)."""
    if not pid:
        return None
    pid = (pid or "").strip().upper()
    m = re.match(r"P0*(\d+)", pid, re.IGNORECASE)
    if m:
        return f"P{m.group(1).lstrip('0') or '1'}"
    if pid.startswith("P") and pid[1:].isdigit():
        return pid
    return pid


# -------- 1. get_patient_context: Neo4j query step --------

def get_patient_context(patient_id: str) -> dict[str, Any]:
    """
    Retrieve full context for a patient from Neo4j: diseases, encounters, labs,
    procedures, drugs, appointments, and PatientNotes (HAS_NOTE). Uses run_query
    via neo4j_ops. Includes protocol (recommended drug/procedure/follow-up) per disease.
    """
    pid = _normalize_patient_id(patient_id) or patient_id
    context = {
        "patient_id": pid,
        "patient_name": None,
        "diseases": [],
        "protocol_per_disease": {},
        "actual_drugs": [],
        "actual_procedures": [],
        "labs": [],
        "encounters": [],
        "appointments": [],
        "notes": [],
    }
    # Patient diseases (from get_patients_with_diseases filtered by patient)
    rows = get_patients_with_diseases()
    for r in rows:
        if (r.get("patient_id") or "").strip() != pid:
            continue
        context["patient_name"] = context["patient_name"] or r.get("patient_name")
        did = r.get("disease_id")
        dname = r.get("disease_name")
        if did:
            context["diseases"].append({"disease_id": did, "disease_name": dname})
    # Protocol for each disease: Disease → Recommended Drug → Procedure → FollowUp
    for d in context["diseases"]:
        did = d["disease_id"]
        protocol = get_protocol_for_disease(did)
        if protocol:
            p = protocol[0]
            context["protocol_per_disease"][did] = {
                "recommended_drug_id": p.get("drug_id"),
                "recommended_drug_name": p.get("drug_name"),
                "recommended_procedure_id": p.get("procedure_id"),
                "recommended_procedure_name": p.get("procedure_name"),
                "followup_id": p.get("followup_id"),
                "followup_name": p.get("followup_name"),
            }
        else:
            context["protocol_per_disease"][did] = None
    # Actual treatments (drugs, procedures) — aggregate across all diseases for this patient
    seen_drug_ids = set()
    seen_proc_ids = set()
    for d in context["diseases"]:
        did = d["disease_id"]
        actual = get_actual_patient_treatments(pid, did)
        for x, y in zip(actual.get("actual_drug_ids") or [], actual.get("actual_drug_names") or []):
            if x and x not in seen_drug_ids:
                seen_drug_ids.add(x)
                context["actual_drugs"].append({"id": x, "name": y})
        for x, y in zip(actual.get("actual_procedure_ids") or [], actual.get("actual_procedure_names") or []):
            if x and x not in seen_proc_ids:
                seen_proc_ids.add(x)
                context["actual_procedures"].append({"id": x, "name": y})
    # Full journey: appointments, encounters, labs, procedures, drugs from encounters
    journey = get_patient_full_journey(pid)
    for r in journey:
        if r.get("appointment_id") and not any(a.get("id") == r.get("appointment_id") for a in (context.get("appointments") or [])):
            context["appointments"].append({
                "id": r.get("appointment_id"),
                "date": r.get("appointment_date"),
                "reason": r.get("appointment_reason"),
                "hospital_name": r.get("hospital_name"),
            })
        if r.get("encounter_id"):
            context["encounters"].append({
                "id": r.get("encounter_id"),
                "date": r.get("encounter_date"),
                "type": r.get("encounter_type"),
                "notes": r.get("encounter_notes"),
                "doctor_name": r.get("doctor_name"),
            })
        if r.get("lab_id"):
            context["labs"].append({
                "id": r.get("lab_id"),
                "name": r.get("lab_name"),
                "result_value": r.get("lab_result_value"),
                "unit": r.get("lab_unit"),
                "date": r.get("lab_date"),
            })
    # Dedupe encounters and labs by id
    seen = set()
    context["encounters"] = [e for e in context["encounters"] if (e.get("id") or "") not in seen and not seen.add(e.get("id") or "")]
    seen = set()
    context["labs"] = [l for l in context["labs"] if (l.get("id") or "") not in seen and not seen.add(l.get("id") or "")]
    # Patient notes: (Patient)-[:HAS_NOTE]->(PatientNote)
    notes = get_patient_notes(pid)
    context["notes"] = [{"id": n.get("id"), "text": n.get("text"), "date": n.get("date")} for n in notes]
    return context


# -------- 2. detect_protocol_violations: protocol comparison logic --------

def detect_protocol_violations() -> dict[str, Any]:
    """
    Run full compliance check: compare actual patient treatments vs protocol
    for all patients/diseases. Returns violations and doctor-level scores.
    """
    return run_compliance_check()


# -------- 3. analyze_patient_protocol --------

def analyze_patient_protocol(patient_id: str) -> dict[str, Any]:
    """
    Get patient context and compliance result for each of their diseases.
    Combines get_patient_context and per-disease check_patient_compliance.
    """
    pid = _normalize_patient_id(patient_id) or patient_id
    context = get_patient_context(pid)
    results = []
    for d in context.get("diseases") or []:
        did = d.get("disease_id")
        dname = d.get("disease_name")
        r = check_patient_compliance(pid, context.get("patient_name") or pid, did, dname or did)
        results.append(r)
    context["compliance_results"] = results
    context["has_violation"] = any(not (r.get("compliant") is True) for r in results)
    return context


def patient_analysis_to_agent_response(patient_id: str) -> dict[str, Any]:
    """
    Call analyze_patient_protocol(patient_id) and return the same structured response
    as ask_agent() so the dashboard can use answer, violation, highlight_nodes, highlight_query.
    Used by POST /analyze-patient in the API server.
    When the patient has a ClinicalState (sepsis data), uses run_sepsis_guidelines instead.
    """
    pid = _normalize_patient_id(patient_id) or patient_id
    if get_patient_clinical_state(pid) is not None:
        from sepsis_compliance import run_sepsis_guidelines
        result = run_sepsis_guidelines(pid)
        name = result.get("patient_name") or pid
        violations = result.get("violations") or []
        compliance = result.get("compliance", False)
        if compliance:
            answer = f"Patient {name} is compliant with sepsis guidelines."
        elif violations:
            answer = f"Patient {name} – sepsis guideline violation(s):\n" + "\n".join(f"  • {v}" for v in violations)
        else:
            answer = f"Patient {name}: sepsis guideline check completed (no violations)."
        protocol_expected = ["Antibiotics if SOFA≥2", "Blood cultures", "Vasopressors if MAP<65"]
        state = result.get("clinical_state") or {}
        actual = []
        if state.get("antibiotics_active"):
            actual.append("Antibiotics")
        if state.get("cultures_ordered"):
            actual.append("Cultures")
        if state.get("vasopressors_active"):
            actual.append("Vasopressors")
        return {
            "answer": answer,
            "violation": not compliance,
            "protocol_expected": protocol_expected,
            "actual_treatment": actual or ["None recorded"],
            "highlight_nodes": result.get("highlight_nodes") or [],
            "highlight_relationships": result.get("highlight_relationships") or [],
            "highlight_query": result.get("highlight_query") or "",
            "paths": result.get("paths") or [],
        }
    analysis = analyze_patient_protocol(patient_id)
    pid = analysis.get("patient_id") or patient_id
    results = analysis.get("compliance_results") or []
    violation = analysis.get("has_violation", False)
    protocol_expected = []
    actual_treatment = []
    disease_ids = []
    drug_ids = []
    procedure_ids = []
    for r in results:
        if r.get("recommended_drug_name"):
            protocol_expected.append(r["recommended_drug_name"])
        if r.get("recommended_procedure_name"):
            protocol_expected.append(r["recommended_procedure_name"])
        actual_treatment.extend(r.get("actual_drug_names") or [])
        actual_treatment.extend(r.get("actual_procedure_names") or [])
        if r.get("disease_id"):
            disease_ids.append(r["disease_id"])
        drug_ids.extend(r.get("actual_drug_ids") or [])
        if r.get("recommended_drug_id"):
            drug_ids.append(r["recommended_drug_id"])
        procedure_ids.extend(r.get("actual_procedure_ids") or [])
        if r.get("recommended_procedure_id"):
            procedure_ids.append(r["recommended_procedure_id"])
    protocol_expected = list(dict.fromkeys(x for x in protocol_expected if x))
    actual_treatment = list(dict.fromkeys(x for x in actual_treatment if x))
    drug_ids = list(dict.fromkeys(x for x in drug_ids if x))
    procedure_ids = list(dict.fromkeys(x for x in procedure_ids if x))
    highlight_nodes, highlight_relationships = _entities_to_highlight(
        patient_id=pid, disease_ids=disease_ids or None,
        drug_ids=drug_ids or None, procedure_ids=procedure_ids or None,
    )
    highlight_query = _build_highlight_query(patient_id=pid)
    # Build a short answer from compliance results (no LLM call for this endpoint)
    if not results:
        answer = f"Patient {analysis.get('patient_name') or pid} has no disease diagnoses in the graph."
    elif violation:
        parts = [f"Patient {analysis.get('patient_name') or pid} has protocol violation(s):"]
        for r in results:
            if not (r.get("compliant") is True) and (r.get("violations") or []):
                parts.append(f"  • {r.get('disease_name', '')}: {'; '.join(r['violations'])}")
        answer = "\n".join(parts)
    else:
        answer = f"Patient {analysis.get('patient_name') or pid} is compliant with protocol for all diagnosed diseases."
    # Paths for flowchart: nodes, relationships, colors, labels, hover_info (includes patient notes in reasoning)
    paths = _build_path_from_patient_analysis(analysis)
    return {
        "answer": answer,
        "violation": violation,
        "protocol_expected": protocol_expected,
        "actual_treatment": actual_treatment,
        "highlight_nodes": highlight_nodes,
        "highlight_relationships": highlight_relationships,
        "highlight_query": highlight_query,
        "paths": paths,
    }


# -------- 4. highlight_query generation --------

def _build_highlight_query(
    patient_id: str | None = None,
    disease_ids: list[str] | None = None,
    drug_ids: list[str] | None = None,
    procedure_ids: list[str] | None = None,
    doctor_id: str | None = None,
) -> str:
    """
    Build an executable Cypher query that returns the nodes/relationships to highlight
    in Neo4j Browser or Bloom. Uses node labels and id property (e.g. Patient {id:'P1'}).
    """
    if patient_id:
        pid = _normalize_patient_id(patient_id) or patient_id
        pid_s = pid.replace("\\", "\\\\").replace("'", "\\'")
        return (
            f"MATCH (p:Patient {{id: '{pid_s}'}})"
            " OPTIONAL MATCH (p)-[:HAS_DISEASE]->(d:Disease)"
            " OPTIONAL MATCH (p)-[:TREATED_WITH]->(drug:Drug)"
            " OPTIONAL MATCH (p)-[:HAD_PROCEDURE]->(proc:Procedure)"
            " RETURN p, d, drug, proc"
        )
    if disease_ids:
        dids = ", ".join(f"'{str(d).replace(chr(39), chr(92) + chr(39))}'" for d in disease_ids[:10])
        return (
            f"MATCH (p:Patient)-[:HAS_DISEASE]->(d:Disease) WHERE d.id IN [{dids}]"
            " OPTIONAL MATCH (p)-[:TREATED_WITH]->(drug:Drug)"
            " OPTIONAL MATCH (p)-[:HAD_PROCEDURE]->(proc:Procedure)"
            " RETURN p, d, drug, proc"
        )
    if doctor_id:
        doc_s = doctor_id.replace("\\", "\\\\").replace("'", "\\'")
        return (
            f"MATCH (doc:Doctor {{id: '{doc_s}'}})"
            " OPTIONAL MATCH (p:Patient)-[:VISITS]->(doc)"
            " OPTIONAL MATCH (p)-[:HAS_DISEASE]->(d:Disease)"
            " RETURN doc, p, d"
        )
    return "MATCH (p:Patient) RETURN p LIMIT 25"


def _diseases_for_patient_ids(patient_ids: set[str]) -> set[tuple[str, str]]:
    """Return set of (patient_id, disease_id) for all given patient IDs (symptoms/HAS_DISEASE)."""
    out = set()
    for r in get_patients_with_diseases():
        pid = r.get("patient_id")
        did = r.get("disease_id")
        if pid and did and pid in patient_ids:
            out.add((pid, did))
    return out


def _entities_to_highlight(
    patient_id: str | None = None,
    disease_ids: list[str] | None = None,
    disease_names: list[str] | None = None,
    drug_ids: list[str] | None = None,
    drug_names: list[str] | None = None,
    procedure_ids: list[str] | None = None,
    doctor_id: str | None = None,
    patient_ids: set[str] | None = None,
) -> tuple[list[str], list[str]]:
    """Build highlight_nodes and highlight_relationships lists for the response.
    Include HAS_DISEASE and Disease nodes so symptoms/diagnoses are shown alongside violations."""
    nodes = []
    rels = []
    if patient_id:
        pid = _normalize_patient_id(patient_id) or patient_id
        nodes.append(f"Patient:{pid}")
        if disease_ids:
            for did in disease_ids:
                nodes.append(f"Disease:{did}")
            rels.append("HAS_DISEASE")
        if drug_ids:
            for x in drug_ids:
                nodes.append(f"Drug:{x}")
            rels.append("TREATED_WITH")
        if procedure_ids:
            for x in procedure_ids:
                nodes.append(f"Procedure:{x}")
            rels.append("HAD_PROCEDURE")
    elif patient_ids:
        for pid in patient_ids:
            nodes.append(f"Patient:{pid}")
        for pid, did in _diseases_for_patient_ids(patient_ids):
            nodes.append(f"Disease:{did}")
        if any(n.startswith("Disease:") for n in nodes):
            rels.append("HAS_DISEASE")
    if doctor_id:
        nodes.append(f"Doctor:{doctor_id}")
        rels.append("VISITS")
    return nodes, rels


# -------- Path building for flowchart/dashboard visualization --------
# Colors: dashboard uses these to highlight nodes/edges (red = violation, green = compliant,
# blue = patient, purple = doctor, orange = minor issue). The paths array feeds the graph UI
# so it can render each path with the right colors, labels, and hover tooltips.

PATH_COLORS = {
    "patient": "#3498db",    # blue
    "doctor": "#9b59b6",     # purple
    "compliant": "#27ae60",  # green
    "violation": "#e74c3c",  # red (major)
    "minor": "#e67e22",      # orange
    "neutral": "#95a5a6",   # gray (e.g. disease node)
}


def _build_path_from_patient_analysis(
    analysis: dict[str, Any],
) -> list[dict[str, Any]]:
    """
    Build one path object for the flowchart from analyze_patient_protocol output.
    Protocol violations are detected from compliance_results (wrong/missing drug or procedure).
    Colors: patient=blue, disease=neutral or red if violation, drug/procedure=green if compliant else red/orange.
    hover_info is constructed from protocol expected vs actual and from patient notes (HAS_NOTE).
    """
    pid = analysis.get("patient_id") or ""
    patient_name = analysis.get("patient_name") or pid
    results = analysis.get("compliance_results") or []
    notes = analysis.get("notes") or []
    notes_text = " | ".join((n.get("text") or "")[:80] for n in notes[:3]) if notes else ""
    nodes = [f"Patient:{pid}"]
    relationships: list[str] = []
    node_colors = [PATH_COLORS["patient"]]
    labels = [f"{patient_name} (Patient)"]
    node_hover = [f"Patient {pid}. " + (f"Notes: {notes_text}" if notes_text else "No notes in graph.")]
    edge_hover: list[str] = []
    for r in results:
        did = r.get("disease_id")
        dname = r.get("disease_name") or did
        compliant = r.get("compliant") is True
        violations = r.get("violations") or []
        rec_drug = r.get("recommended_drug_name")
        rec_proc = r.get("recommended_procedure_name")
        actual_drugs = r.get("actual_drug_names") or []
        actual_procs = r.get("actual_procedure_names") or []
        disease_color = PATH_COLORS["violation"] if not compliant else PATH_COLORS["neutral"]
        nodes.append(f"Disease:{did}")
        node_colors.append(disease_color)
        labels.append(dname)
        node_hover.append(
            f"Disease: {dname}. Recommended: {rec_drug or '—'}, {rec_proc or '—'}. "
            + (f"Actual: {', '.join(actual_drugs) or '—'}; {', '.join(actual_procs) or '—'}. "
               + (f"Violations: {'; '.join(violations)}" if violations else "Compliant.")
            )
        )
        relationships.append("HAS_DISEASE")
        edge_hover.append(f"HAS_DISEASE: diagnosis / symptom — {dname}")
        for drug_id, drug_name in zip(
            r.get("actual_drug_ids") or [],
            r.get("actual_drug_names") or [],
        ):
            nodes.append(f"Drug:{drug_id}")
            rec_id = r.get("recommended_drug_id")
            is_rec = drug_id == rec_id
            node_colors.append(PATH_COLORS["compliant"] if is_rec else PATH_COLORS["violation"])
            labels.append(drug_name or drug_id)
            node_hover.append(
                f"Recommended Drug: {rec_drug or '—'} | Actual: {drug_name or drug_id}"
                + (" (compliant)" if is_rec else " (not recommended)")
            )
            relationships.append("TREATED_WITH")
            edge_hover.append("TREATED_WITH: prescribed" + (" (protocol)" if is_rec else " (deviation)"))
        if r.get("recommended_drug_id") and not (r.get("actual_drug_ids") or []):
            nodes.append(f"Drug:{r['recommended_drug_id']}")
            node_colors.append(PATH_COLORS["violation"])
            labels.append(r.get("recommended_drug_name") or r["recommended_drug_id"])
            node_hover.append(f"Recommended Drug: {r.get('recommended_drug_name')} (missing from actual treatment)")
            relationships.append("TREATED_WITH")
            edge_hover.append("TREATED_WITH: missing (violation)")
        for proc_id, proc_name in zip(
            r.get("actual_procedure_ids") or [],
            r.get("actual_procedure_names") or [],
        ):
            nodes.append(f"Procedure:{proc_id}")
            rec_pid = r.get("recommended_procedure_id")
            is_rec = proc_id == rec_pid
            node_colors.append(PATH_COLORS["compliant"] if is_rec else PATH_COLORS["violation"])
            labels.append(proc_name or proc_id)
            node_hover.append(
                f"Recommended Procedure: {rec_proc or '—'} | Actual: {proc_name or proc_id}"
                + (" (compliant)" if is_rec else " (not recommended)")
            )
            relationships.append("HAD_PROCEDURE")
            edge_hover.append("HAD_PROCEDURE: performed" + (" (protocol)" if is_rec else " (deviation)"))
        if r.get("recommended_procedure_id") and not (r.get("actual_procedure_ids") or []):
            nodes.append(f"Procedure:{r['recommended_procedure_id']}")
            node_colors.append(PATH_COLORS["violation"])
            labels.append(r.get("recommended_procedure_name") or r["recommended_procedure_id"])
            node_hover.append(f"Recommended Procedure: {r.get('recommended_procedure_name')} (missing)")
            relationships.append("HAD_PROCEDURE")
            edge_hover.append("HAD_PROCEDURE: missing (violation)")
    highlight_query = _build_highlight_query(patient_id=pid)
    edge_colors = [PATH_COLORS["violation"] if "violation" in h.lower() or "missing" in h.lower() else PATH_COLORS["compliant"] for h in edge_hover]
    # hover_info: optional string per node then per edge (for dashboard tooltips)
    hover_info = list(node_hover) + list(edge_hover)
    return [{
        "nodes": nodes,
        "relationships": relationships,
        "colors": {"node_colors": node_colors, "edge_colors": edge_colors},
        "labels": labels,
        "hover_info": hover_info,
        "node_hover": node_hover,
        "edge_hover": edge_hover,
        "highlight_query": highlight_query,
    }]


def _build_paths_for_doctor(
    doctor_id: str,
    violated_relationships: list[dict[str, Any]],
    patient_to_doctors: dict[str, list[str]],
    all_checks: list[dict[str, Any]],
    doctor_name: str | None = None,
) -> list[dict[str, Any]]:
    """
    For doctor-level questions: build one path per patient treated by this doctor who has
    a protocol violation. Each path shows Doctor -> Patient -> Disease -> Drug/Procedure
    so the dashboard can show the doctor and each patient's violations.
    """
    if not doctor_name:
        for r in get_doctors_and_specialties():
            if r.get("doctor_id") == doctor_id:
                doctor_name = r.get("doctor_name") or doctor_id
                break
        doctor_name = doctor_name or doctor_id
    patient_ids = set()
    for pid, doc_list in (patient_to_doctors or {}).items():
        if doctor_id in (doc_list or []):
            for v in violated_relationships or []:
                if v.get("patient_id") == pid:
                    patient_ids.add(pid)
                    break
    paths = []
    for pid in patient_ids:
        analysis = analyze_patient_protocol(pid)
        for path in _build_path_from_patient_analysis(analysis):
            # Prepend Doctor and VISITS so path is Doctor -> Patient -> violations
            path_nodes = path.get("nodes") or []
            path_rels = path.get("relationships") or []
            path_node_colors = path.get("colors", {}).get("node_colors") or []
            path_edge_colors = path.get("colors", {}).get("edge_colors") or []
            path_labels = path.get("labels") or []
            path_node_hover = path.get("node_hover") or []
            path_edge_hover = path.get("edge_hover") or []
            paths.append({
                "nodes": [f"Doctor:{doctor_id}"] + path_nodes,
                "relationships": ["VISITS"] + path_rels,
                "colors": {
                    "node_colors": [PATH_COLORS["doctor"]] + path_node_colors,
                    "edge_colors": [PATH_COLORS["neutral"]] + path_edge_colors,
                },
                "labels": [f"{doctor_name} (Doctor)"] + path_labels,
                "node_hover": [f"Doctor with most violations. Patients below had protocol deviations."] + path_node_hover,
                "edge_hover": ["VISITS: patient under this doctor"] + path_edge_hover,
                "hover_info": [f"Doctor with most violations. Patients below had protocol deviations."] + path_node_hover + ["VISITS: patient under this doctor"] + path_edge_hover,
                "highlight_query": _build_highlight_query(doctor_id=doctor_id),
            })
    return paths


def _build_paths_for_ask_agent(
    entity_patient: str | None,
    entity_doctor: str | None,
    entity_disease_ids: list[str],
    entity_drug_ids: list[str],
    entity_procedure_ids: list[str],
    violated_relationships: list[dict[str, Any]],
    patient_to_doctors: dict[str, list[str]],
    compliance_all_checks: list[dict[str, Any]] | None,
    entity_doctor_name: str | None = None,
) -> list[dict[str, Any]]:
    """
    Build paths array for ask_agent response. If entity_patient is set, one path for that patient.
    If entity_doctor is set, one path per patient under that doctor with violations (each path: Doctor -> Patient -> ...).
    Otherwise a single generic path from first few violated relationships if any.
    """
    if entity_patient:
        analysis = analyze_patient_protocol(entity_patient)
        return _build_path_from_patient_analysis(analysis)
    if entity_doctor:
        return _build_paths_for_doctor(
            entity_doctor,
            violated_relationships or [],
            patient_to_doctors or {},
            compliance_all_checks or [],
            doctor_name=entity_doctor_name,
        )
    paths = []
    seen_patient = set()
    for v in (violated_relationships or [])[:5]:
        pid = v.get("patient_id")
        if pid and pid not in seen_patient:
            seen_patient.add(pid)
            analysis = analyze_patient_protocol(pid)
            paths.extend(_build_path_from_patient_analysis(analysis))
    return paths


# -------- 5. LLM reasoning step --------

def _call_llm(question: str, context_for_llm: str) -> str:
    """Call OpenAI to generate a human-readable answer from the question and graph context."""
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return (
            "No OPENAI_API_KEY set. Set it in your environment or .env to get LLM-generated explanations. "
            "Here is the retrieved context: " + (context_for_llm[:1500] or "No context.")
        )
    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a healthcare compliance analyst for a Neo4j-backed clinical graph. "
                        "You MUST prioritize graph-connected facts (violations, diseases, symptoms, drugs, procedures, "
                        "clinical state) over generic clinical prose. Reference relationship types when relevant "
                        "(HAS_DISEASE, HAS_SYMPTOM, HAS_VIOLATION, HAS_CLINICAL_STATE, TREATED_WITH, HAD_PROCEDURE). "
                        "Do not answer from general knowledge alone when the context contains graph data — tie every "
                        "clinical claim to items in the provided context. "
                        "Use stable, clinical wording; avoid filler. "
                        "If protocol violations exist, state them explicitly (missing/wrong drug or procedure)."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Context:\n{context_for_llm}\n\nQuestion: {question}"
                        + _STRUCTURED_SECTIONS_USER_SUFFIX
                    ),
                },
            ],
            max_tokens=900,
            temperature=_LLM_TEMPERATURE,
        )
        return (response.choices[0].message.content or "").strip()
    except Exception as e:
        return f"LLM error: {e}. Context summary: {context_for_llm[:1000]}"


# -------- 6. ask_agent: main entry --------

# Map disease keywords (from question) to graph disease_id (D1-D8 in seed data)
_DISEASE_KEYWORDS_TO_ID: dict[str, str] = {
    "hypertension": "D1",
    "type 2 diabetes": "D2",
    "diabetes type 2": "D2",
    "type 2 diabetic": "D2",
    "diabetes": "D2",
    "asthma": "D3",
    "osteoarthritis": "D4",
    "anxiety": "D5",
    "pneumonia": "D6",
    "anemia": "D7",
    "copd": "D8",
}


def _match_disease_in_question(question: str) -> str | None:
    """Return disease_id (e.g. D2) if the question mentions a disease by name."""
    q = (question or "").strip().lower()
    for keyword, did in sorted(_DISEASE_KEYWORDS_TO_ID.items(), key=lambda x: -len(x[0])):
        if keyword in q:
            return did
    return None


def _extract_question_intent(question: str) -> dict[str, Any]:
    """Simple extraction: patient id (P001, P1), doctor, disease, question type."""
    q = (question or "").strip().lower()
    intent = {"patient_id": None, "doctor_id": None, "disease_id": None, "type": "general"}
    # Patient: P001, P1, patient P1, patient P001
    m = re.search(r"patient\s*(?:id\s*)?[:\s]*([Pp]0*\d+)|([Pp]0*\d+)", question or "", re.IGNORECASE)
    if m:
        intent["patient_id"] = (m.group(1) or m.group(2) or "").strip()
    # Doctor: Dr. X, doctor DOC1, DOC1
    m = re.search(r"doctor\s+([A-Za-z0-9]+)|([Dd][Oo][Cc]\d+)", question or "", re.IGNORECASE)
    if m:
        intent["doctor_id"] = (m.group(1) or m.group(2) or "").strip()
    if "violation" in q or "violations" in q:
        intent["type"] = "violations"
    # "What/which patients have violations" -> sepsis guideline violations (list all with compliance=False)
    if ("patient" in q or "patients" in q) and ("violation" in q or "violations" in q):
        intent["type"] = "sepsis"
    if "follow" in q and "protocol" in q:
        intent["type"] = "patient_protocol"
    if "drugs not recommended" in q or "wrong drug" in q:
        intent["type"] = "drug_violations"
    if "most protocol violations" in q or "most violations" in q:
        intent["type"] = "doctor_most_violations"
    # "Which/what patient had [disease]" -> filter by disease
    disease_id = _match_disease_in_question(question)
    if disease_id and ("patient" in q or "who" in q or "which" in q or "what" in q):
        intent["disease_id"] = disease_id
        intent["type"] = "patients_by_disease"
    # Sepsis / SOFA / clinical state questions
    if any(k in q for k in ("sepsis", "sofa", "antibiotics", "lactate", "vasopressor", "urgent", "clinical state", "icu")):
        intent["type"] = "sepsis"
    return intent


def ask_agent(question: str) -> dict[str, Any]:
    """
    Answer a natural language question and return dashboard-ready JSON.

    Returns dict with:
      answer (str), violation (bool), protocol_expected (list), actual_treatment (list),
      paths (list of {nodes, relationships, labels, colors: {node_colors, edge_colors}, hover_info, highlight_query?}),
      highlight_query (str), highlight_nodes (list), highlight_relationships (list).
    Paths support multiple patients and multiple violations; colors indicate severity (red/orange/green/blue/purple).
    """
    question = (question or "").strip()
    if not question:
        return {
            "answer": "Please ask a question.",
            "violation": False,
            "protocol_expected": [],
            "actual_treatment": [],
            "highlight_nodes": [],
            "highlight_relationships": [],
            "highlight_query": "MATCH (n) RETURN n LIMIT 1",
            "paths": [],
        }

    intent = _extract_question_intent(question)
    patient_id = _normalize_patient_id(intent.get("patient_id")) if intent.get("patient_id") else None
    doctor_id = intent.get("doctor_id")
    context_parts = _data_priority_preamble()
    protocol_expected = []
    actual_treatment = []
    violation = False
    highlight_nodes = []
    highlight_relationships = []
    entity_patient = None
    entity_disease_ids = []
    entity_drug_ids = []
    entity_procedure_ids = []
    entity_doctor = None
    entity_doctor_name: str | None = None
    violated_rels: list[dict[str, Any]] = []
    patient_to_doctors_map: dict[str, list[str]] = {}
    compliance_all: list[dict[str, Any]] | None = None
    paths_precomputed: list[dict[str, Any]] | None = None  # set in patients_by_disease branch
    prebuilt_answer: str | None = None  # e.g. violation-focused answer for "what patients have violations"

    # -------- Neo4j query step: gather protocol guidelines and relevant data --------
    guidelines = get_protocol_guidelines()
    context_parts.append("Protocol guidelines (Disease -> Recommended Drug -> Procedure -> FollowUp):")
    context_parts.append(json.dumps([{k: v for k, v in g.items()} for g in guidelines], indent=2, default=str))

    if intent["type"] == "sepsis":
        # Sepsis: compare patient clinical state to sepsis guidelines (SOFA, lactate, antibiotics, etc.)
        from sepsis_compliance import run_sepsis_guidelines, run_sepsis_guidelines_all
        patients_with_state = get_patients_with_clinical_state()
        sepsis_guidelines = get_sepsis_guidelines()
        if not patients_with_state:
            context_parts.append("No sepsis clinical state data in graph. Run: python -m sepsis_data (seed_sepsis).")
            paths_precomputed = []
            highlight_nodes = []
            highlight_relationships = []
            highlight_query = "MATCH (p:Patient)-[:HAS_CLINICAL_STATE]->(c:ClinicalState) RETURN p, c LIMIT 1"
        else:
            if patient_id:
                results = [run_sepsis_guidelines(patient_id)]
            else:
                results = run_sepsis_guidelines_all()
                q_lower = (question or "").lower()
                # If question asks who needs urgent antibiotics, keep only patients with antibiotics-related violations
                if "antibiotic" in q_lower and any(k in q_lower for k in ("urgent", "need", "require", "who", "which patient", "which patients", "must", "has to", "have to")):
                    def _needs_urgent_abx(r):
                        v = r.get("violations") or []
                        return any("antibiotic" in (vitem or "").lower() for vitem in v)
                    results = [r for r in results if _needs_urgent_abx(r)]
                    if results:
                        context_parts.append("Only patients who need urgent antibiotics (SOFA≥2 or lactate>2 but antibiotics not active):")
                # If question asks what/which patients have violations, keep only non-compliant patients (all violators)
                elif ("violation" in q_lower or "violations" in q_lower) and ("patient" in q_lower or "patients" in q_lower):
                    results = [r for r in results if not r.get("compliance")]
                    if results:
                        context_parts.append("Only patients with at least one sepsis guideline violation:")
                        # Build violation-focused answer: sepsis + disease protocol violators
                        parts = ["Patients with sepsis guideline violations:"]
                        for r in results:
                            name = r.get("patient_name") or r.get("patient_id")
                            vlist = r.get("violations") or []
                            parts.append(f"  • {name}: {'; '.join(vlist)}")
                        # Add non-sepsis (disease protocol) violators
                        _protocol_violators = run_compliance_check().get("patients_with_violations") or []
                        if _protocol_violators:
                            parts.append("")
                            parts.append("Patients with disease protocol violations (wrong/missing drug or procedure):")
                            for r in _protocol_violators:
                                name = r.get("patient_name") or r.get("patient_id")
                                dname = r.get("disease_name") or r.get("disease_id")
                                vlist = r.get("violations") or []
                                parts.append(f"  • {name} ({dname}): {'; '.join(vlist)}")
                        prebuilt_answer = "\n".join(parts)
            context_parts.append("Sepsis guidelines: " + json.dumps(sepsis_guidelines[:1], indent=2, default=str))
            for r in results:
                context_parts.append(
                    f"Patient {r.get('patient_id')} ({r.get('patient_name')}): "
                    f"compliance={r.get('compliance')}, violations={r.get('violations')}, "
                    f"state SOFA={r.get('clinical_state', {}).get('sofa_score')}, "
                    f"lactate={r.get('clinical_state', {}).get('lactate')}, antibiotics={r.get('clinical_state', {}).get('antibiotics_active')}"
                )
            _sepsis_pids = list({r.get("patient_id") for r in results if r.get("patient_id")})
            context_parts.extend(_graph_grounding_prompt_lines(_sepsis_pids))
            violation = any(not r.get("compliance") for r in results)
            protocol_expected = ["Broad-spectrum antibiotics within 1h", "Blood cultures", "Lactate", "Vasopressors if MAP<65"]
            actual_treatment = []
            for r in results:
                s = r.get("clinical_state") or {}
                if s.get("antibiotics_active"):
                    actual_treatment.append("Antibiotics active")
                if s.get("cultures_ordered"):
                    actual_treatment.append("Cultures ordered")
            paths_precomputed = []
            for r in results:
                paths_precomputed.extend(r.get("paths") or [])
            # Highlight patients, ClinicalState, and Violation nodes (so graph shows violations, not just patients)
            seen_hn = set()
            for r in results:
                for n in r.get("highlight_nodes") or []:
                    if n.startswith("Patient:") or n.startswith("ClinicalState:"):
                        seen_hn.add(n)
                # Add Violation node ids (V_{pid}_{i}) so "what patients have violations" highlights violation nodes
                if not r.get("compliance"):
                    for i, _ in enumerate(r.get("violations") or []):
                        seen_hn.add(f"Violation:V_{r.get('patient_id')}_{i}")
            # Include disease protocol violators (non-sepsis) when question is "what patients have violations"
            if ("violation" in q_lower or "violations" in q_lower) and ("patient" in q_lower or "patients" in q_lower):
                for r in (run_compliance_check().get("patients_with_violations") or []):
                    pid = r.get("patient_id")
                    did = r.get("disease_id")
                    if pid:
                        seen_hn.add(f"Patient:{pid}")
                    if did:
                        seen_hn.add(f"Disease:{did}")
                    for i, _ in enumerate(r.get("violations") or []):
                        seen_hn.add(f"Violation:V_D_{pid}_{did}_{i}")
            # Add HAS_DISEASE / Disease for all patients in scope so symptoms and diagnoses are shown
            sepsis_pids = {n.split(":", 1)[1] for n in seen_hn if n.startswith("Patient:")}
            for _pid, did in _diseases_for_patient_ids(sepsis_pids):
                seen_hn.add(f"Disease:{did}")
            highlight_nodes = list(seen_hn)
            highlight_relationships = ["HAS_CLINICAL_STATE", "HAS_VIOLATION", "HAS_DISEASE"]
            # Cypher: patients, their diseases (symptoms), clinical state, and violations
            highlight_query = (
                "MATCH (p:Patient) "
                "OPTIONAL MATCH (p)-[:HAS_DISEASE]->(d:Disease) "
                "OPTIONAL MATCH (p)-[:HAS_CLINICAL_STATE]->(c:ClinicalState) "
                "OPTIONAL MATCH (p)-[:HAS_VIOLATION]->(v:Violation) "
                "RETURN p, d, c, v LIMIT 200"
            ) if results else "MATCH (n) RETURN n LIMIT 1"

    elif intent["type"] == "doctor_most_violations" or (doctor_id and "violation" in (question or "").lower()):
        # Which doctor has the most protocol violations?
        compliance = detect_protocol_violations()
        doctor_scores = compliance.get("doctor_compliance_scores") or []
        violated = compliance.get("violated_relationships") or []
        # Count violations per doctor (via patient -> doctor)
        patient_to_doctors = {}
        for r in get_patients_with_doctor():
            pid = r.get("patient_id")
            doc_id = r.get("doctor_id")
            if pid not in patient_to_doctors:
                patient_to_doctors[pid] = []
            patient_to_doctors[pid].append(doc_id)
        doc_violation_count = {}
        for v in violated:
            pid = v.get("patient_id")
            for doc_id in patient_to_doctors.get(pid, []):
                doc_violation_count[doc_id] = doc_violation_count.get(doc_id, 0) + 1
        worst = sorted(doc_violation_count.items(), key=lambda x: -x[1])[:1]
        if worst:
            entity_doctor = worst[0][0]
            entity_doctor_name = next(
                (d.get("doctor_name") for d in (compliance.get("doctor_compliance_scores") or []) if d.get("doctor_id") == entity_doctor),
                entity_doctor,
            )
            context_parts.append(f"Doctor with most violations: {entity_doctor} ({worst[0][1]} violations).")
        context_parts.append("Doctor compliance scores: " + json.dumps(doctor_scores[:15], indent=2, default=str))
        context_parts.append("Violations: " + json.dumps(violated[:30], indent=2, default=str))
        _doc_viol_pids = list({v.get("patient_id") for v in violated[:35] if v.get("patient_id")})
        context_parts.extend(_graph_grounding_prompt_lines(_doc_viol_pids))
        violation = len(violated) > 0
        violated_rels = violated
        patient_to_doctors_map = patient_to_doctors
        compliance_all = compliance.get("all_checks")

    elif intent["type"] == "patients_by_disease" and intent.get("disease_id"):
        # "What patient had diabetes type 2?" -> only patients with this disease (from Neo4j)
        disease_id = intent["disease_id"]
        rows = get_patients_with_diseases()
        patient_ids_with_disease = []
        disease_name = None
        for r in rows:
            if r.get("disease_id") == disease_id:
                pid = r.get("patient_id")
                if pid and pid not in patient_ids_with_disease:
                    patient_ids_with_disease.append(pid)
                disease_name = disease_name or r.get("disease_name")
        disease_name = disease_name or disease_id
        context_parts.append(f"Patients with disease: {disease_name} ({disease_id}). From Neo4j HAS_DISEASE.")
        context_parts.append("Patient IDs: " + json.dumps(patient_ids_with_disease, default=str))
        context_parts.extend(_graph_grounding_prompt_lines(patient_ids_with_disease))
        for pid in patient_ids_with_disease:
            analysis = analyze_patient_protocol(pid)
            context_parts.append(f"Patient {pid} ({analysis.get('patient_name', pid)}): " + json.dumps({
                "diseases": analysis.get("diseases"),
                "compliance_results": analysis.get("compliance_results"),
                "notes": analysis.get("notes"),
            }, indent=2, default=str))
            if analysis.get("has_violation"):
                violation = True
            for r in analysis.get("compliance_results") or []:
                if r.get("disease_id") == disease_id:
                    if r.get("recommended_drug_name"):
                        protocol_expected.append(r["recommended_drug_name"])
                    if r.get("recommended_procedure_name"):
                        protocol_expected.append(r["recommended_procedure_name"])
                    actual_treatment.extend(r.get("actual_drug_names") or [])
                    actual_treatment.extend(r.get("actual_procedure_names") or [])
                    entity_disease_ids.append(disease_id)
                    entity_drug_ids.extend(r.get("actual_drug_ids") or [])
                    if r.get("recommended_drug_id"):
                        entity_drug_ids.append(r["recommended_drug_id"])
                    entity_procedure_ids.extend(r.get("actual_procedure_ids") or [])
                    if r.get("recommended_procedure_id"):
                        entity_procedure_ids.append(r["recommended_procedure_id"])
        # Paths and highlight: only patients with this disease
        paths_precomputed = []
        seen_path_nodes = set()
        seen_path_rels = set()
        for pid in patient_ids_with_disease:
            analysis = analyze_patient_protocol(pid)
            for path in _build_path_from_patient_analysis(analysis):
                paths_precomputed.append(path)
                for n in path.get("nodes") or []:
                    seen_path_nodes.add(n)
                for rel in path.get("relationships") or []:
                    seen_path_rels.add(rel)
        highlight_nodes = [f"Disease:{disease_id}"] + [n for n in seen_path_nodes if n != f"Disease:{disease_id}"]
        highlight_relationships = list(seen_path_rels)
        highlight_query = _build_highlight_query(patient_id=None, disease_ids=[disease_id])

    elif patient_id or intent["type"] == "patient_protocol":
        # Patient-specific: did patient X follow protocol?
        pid = patient_id or intent.get("patient_id")
        if pid:
            pid = _normalize_patient_id(pid) or pid
        if not pid:
            # Try to find a patient from "which patients received drugs not recommended"
            compliance = detect_protocol_violations()
            violated = compliance.get("violated_relationships") or []
            context_parts.append("Patients with protocol violations (e.g. wrong/missing drug):")
            context_parts.append(json.dumps(violated[:40], indent=2, default=str))
            context_parts.extend(
                _graph_grounding_prompt_lines(list({x.get("patient_id") for x in violated[:25] if x.get("patient_id")}))
            )
            for v in violated[:5]:
                protocol_expected.extend(v.get("recommended_drug") or [])
                actual_treatment.extend(v.get("actual_drugs") or [])
            violation = len(violated) > 0
            violated_rels = violated
            compliance_all = compliance.get("all_checks")
            for r in get_patients_with_doctor():
                p = r.get("patient_id")
                doc_id = r.get("doctor_id")
                if p not in patient_to_doctors_map:
                    patient_to_doctors_map[p] = []
                patient_to_doctors_map[p].append(doc_id)
        else:
            analysis = analyze_patient_protocol(pid)
            context_parts.extend(_graph_grounding_prompt_lines([pid]))
            context_parts.append("Patient context: " + json.dumps({
                "patient_id": analysis.get("patient_id"),
                "patient_name": analysis.get("patient_name"),
                "diseases": analysis.get("diseases"),
                "protocol_per_disease": analysis.get("protocol_per_disease"),
                "actual_drugs": analysis.get("actual_drugs"),
                "actual_procedures": analysis.get("actual_procedures"),
                "notes": analysis.get("notes"),
            }, indent=2, default=str))
            context_parts.append("Compliance results: " + json.dumps(analysis.get("compliance_results", []), indent=2, default=str))
            entity_patient = pid
            violation = analysis.get("has_violation", False)
            for r in analysis.get("compliance_results") or []:
                if r.get("recommended_drug_name"):
                    protocol_expected.append(r["recommended_drug_name"])
                if r.get("recommended_procedure_name"):
                    protocol_expected.append(r["recommended_procedure_name"])
                actual_treatment.extend(r.get("actual_drug_names") or [])
                actual_treatment.extend(r.get("actual_procedure_names") or [])
                if r.get("disease_id"):
                    entity_disease_ids.append(r["disease_id"])
                entity_drug_ids.extend(r.get("actual_drug_ids") or [])
                if r.get("recommended_drug_id"):
                    entity_drug_ids.append(r["recommended_drug_id"])
                entity_procedure_ids.extend(r.get("actual_procedure_ids") or [])
                if r.get("recommended_procedure_id"):
                    entity_procedure_ids.append(r["recommended_procedure_id"])
    else:
        # General: which patients received drugs not recommended, etc.
        compliance = detect_protocol_violations()
        violated = compliance.get("violated_relationships") or []
        context_parts.append("Violations: " + json.dumps(violated[:50], indent=2, default=str))
        _gen_viol_pids = list({v.get("patient_id") for v in violated[:40] if v.get("patient_id")})
        context_parts.extend(_graph_grounding_prompt_lines(_gen_viol_pids))
        violation = len(violated) > 0
        violated_rels = violated
        compliance_all = compliance.get("all_checks")
        for r in get_patients_with_doctor():
            p = r.get("patient_id")
            doc_id = r.get("doctor_id")
            if p not in patient_to_doctors_map:
                patient_to_doctors_map[p] = []
            patient_to_doctors_map[p].append(doc_id)
        for v in violated[:3]:
            protocol_expected.extend([v.get("recommended_drug"), v.get("recommended_procedure")])
            actual_treatment.extend(v.get("actual_drugs") or [])
            actual_treatment.extend(v.get("actual_procedures") or [])

    protocol_expected = [x for x in protocol_expected if x]
    actual_treatment = list(dict.fromkeys(x for x in actual_treatment if x))

    # -------- LLM reasoning step --------
    context_for_llm = "\n".join(context_parts)
    if prebuilt_answer is not None:
        answer = prebuilt_answer
    else:
        answer = _call_llm(question, context_for_llm)

    # -------- Graph highlight query generation (skip if already set e.g. patients_by_disease) --------
    if paths_precomputed is None:
        highlight_nodes, highlight_relationships = _entities_to_highlight(
            patient_id=entity_patient,
            disease_ids=entity_disease_ids or None,
            drug_ids=entity_drug_ids or None,
            procedure_ids=entity_procedure_ids or None,
            doctor_id=entity_doctor,
        )
        highlight_query = _build_highlight_query(
            patient_id=entity_patient,
            disease_ids=entity_disease_ids or None,
            drug_ids=entity_drug_ids or None,
            procedure_ids=entity_procedure_ids or None,
            doctor_id=entity_doctor,
        )

    # Build paths array for dashboard (or use precomputed for "patients by disease" questions)
    if paths_precomputed is not None:
        paths = paths_precomputed
    else:
        paths = _build_paths_for_ask_agent(
            entity_patient,
            entity_doctor,
            entity_disease_ids,
            entity_drug_ids,
            entity_procedure_ids,
            violated_rels,
            patient_to_doctors_map,
            compliance_all,
            entity_doctor_name=entity_doctor_name,
        )

    # For "which doctor has most violations": include doctor and all path nodes so highlight shows doctor + violations
    if entity_doctor and paths:
        seen_nodes = {f"Doctor:{entity_doctor}"}
        seen_rels = set()
        for p in paths:
            for n in p.get("nodes") or []:
                seen_nodes.add(n)
            for r in p.get("relationships") or []:
                seen_rels.add(r)
        highlight_nodes = list(seen_nodes)
        highlight_relationships = ["VISITS"] + [x for x in seen_rels if x != "VISITS"]

    return {
        "answer": answer,
        "violation": violation,
        "protocol_expected": protocol_expected,
        "actual_treatment": actual_treatment,
        "highlight_nodes": highlight_nodes,
        "highlight_relationships": highlight_relationships,
        "highlight_query": highlight_query,
        "paths": paths,
    }


def ai_agent_query(question: str) -> dict[str, Any]:
    """Alias for ask_agent: natural language question -> compliance/violation analysis and paths for visualization."""
    return ask_agent(question)


# ---------------------------------------------------------------------------
# Patient-Aware "Smart" AI  (extends ask_agent — existing logic untouched)
# ---------------------------------------------------------------------------

def _build_patient_summary(pid: str) -> dict[str, Any]:
    """Build a rich text summary + structured data for one patient."""
    pid = _normalize_patient_id(pid) or pid
    ctx = get_patient_context(pid)
    analysis = analyze_patient_protocol(pid)

    graph_compare: list[dict[str, Any]] = []
    try:
        graph_compare = get_patients_for_comparison([pid])
    except Exception:
        graph_compare = []

    clinical_state = get_patient_clinical_state(pid)
    sepsis_info = None
    if clinical_state is not None:
        from sepsis_compliance import run_sepsis_guidelines
        sepsis_info = run_sepsis_guidelines(pid)

    pname = ctx.get("patient_name") or pid
    summary_parts = [
        f"PATIENT_DATA_SCOPE patient_id={pid} patient_name={pname}",
        f"All rows below are Neo4j facts for patient_id={pid} only — do not apply them to any other patient.",
        f"Patient {pname} ({pid}):",
    ]
    if ctx.get("diseases"):
        summary_parts.append(
            "  Diseases: " + ", ".join(d["disease_name"] or d["disease_id"] for d in ctx["diseases"])
        )
    if ctx.get("actual_drugs"):
        summary_parts.append(
            "  Drugs received: " + ", ".join(d["name"] or d["id"] for d in ctx["actual_drugs"])
        )
    if ctx.get("actual_procedures"):
        summary_parts.append(
            "  Procedures: " + ", ".join(p["name"] or p["id"] for p in ctx["actual_procedures"])
        )
    if ctx.get("notes"):
        summary_parts.append(
            "  Clinical notes: " + " | ".join((n.get("text") or "")[:120] for n in ctx["notes"][:5])
        )
    if clinical_state:
        cs = clinical_state
        summary_parts.append(
            f"  Clinical state: SOFA={cs.get('sofa_score')}, MAP={cs.get('map')}, "
            f"lactate={cs.get('lactate')}, GCS={cs.get('gcs')}, creatinine={cs.get('creatinine')}, "
            f"antibiotics={cs.get('antibiotics_active')}, vasopressors={cs.get('vasopressors_active')}, "
            f"cultures={cs.get('cultures_ordered')}"
        )
    comp = analysis.get("compliance_results") or []
    violations = []
    for r in comp:
        for v in r.get("violations") or []:
            violations.append(v)
    if sepsis_info and not sepsis_info.get("compliance"):
        for v in sepsis_info.get("violations") or []:
            if v not in violations:
                violations.append(v)
    if violations:
        summary_parts.append("  Violations: " + "; ".join(violations))
    else:
        summary_parts.append("  Compliance: No violations found.")

    if graph_compare:
        gr = graph_compare[0]
        syms = gr.get("symptoms") or []
        if syms:
            summary_parts.append(
                "  Graph symptoms (HAS_SYMPTOM): "
                + ", ".join((s.get("name") or s.get("id")) for s in syms if s)
            )
        gv = gr.get("violations") or []
        if gv:
            summary_parts.append(
                "  Graph violation nodes (HAS_VIOLATION): " + "; ".join(str(x) for x in gv[:12])
            )

    summary_parts.extend([ln for ln in _graph_grounding_prompt_lines([pid]) if ln.strip()])

    return {
        "pid": pid,
        "name": ctx.get("patient_name") or pid,
        "context": ctx,
        "analysis": analysis,
        "clinical_state": clinical_state,
        "sepsis_info": sepsis_info,
        "violations": violations,
        "summary_text": "\n".join(summary_parts),
    }


def ask_agent_with_context(
    question: str,
    selected_patient_ids: list[str] | None = None,
) -> dict[str, Any]:
    """
    Patient-aware AI: if selected_patient_ids are provided, the LLM receives
    each patient's full Neo4j context (diseases, drugs, procedures, notes,
    clinical state, compliance results).  For multi-patient selections the
    prompt also asks the LLM to compare.  Falls back to the original
    ask_agent() when no patients are selected.
    """
    question = (question or "").strip()
    ids = [_normalize_patient_id(p) for p in (selected_patient_ids or []) if p]
    ids = [p for p in ids if p]
    ids = list(dict.fromkeys(ids))

    if not ids:
        return ask_agent(question)

    summaries = [_build_patient_summary(pid) for pid in ids]

    name_by_pid = {s["pid"]: (s.get("name") or s["pid"]) for s in summaries}

    if len(ids) == 1:
        scope_top = _locked_patient_context_header(ids[0], summaries[0].get("name") or ids[0])
        patient_blocks = "\n\n".join(s["summary_text"] for s in summaries)
        context_block = (
            scope_top
            + "\n".join(_data_priority_preamble())
            + "\n\n"
            + patient_blocks
        )
    else:
        scope_top = _comparison_context_header(ids, name_by_pid)
        patient_blocks = "\n\n".join(
            "▶ SECTION_START patient_id=" + s["pid"] + "\n" + s["summary_text"] + "\n▶ SECTION_END patient_id=" + s["pid"]
            for s in summaries
        )
        context_block = scope_top + "\n".join(_data_priority_preamble()) + "\n\n" + patient_blocks

    guidelines = get_protocol_guidelines()
    context_block += (
        "\n\n--- Reference: protocol templates (disease-level; map ONLY to diseases listed "
        "under each patient section above — do not assume an unlisted disease belongs to a patient) ---\n"
        "Protocol guidelines:\n"
        + json.dumps([{k: v for k, v in g.items()} for g in guidelines], indent=2, default=str)
    )

    sepsis_gl = get_sepsis_guidelines()
    if sepsis_gl:
        context_block += (
            "\n\n--- Reference: sepsis guideline thresholds (apply only when this patient's section "
            "includes HAS_CLINICAL_STATE / sepsis-related data) ---\nSepsis guidelines:\n"
            + json.dumps(sepsis_gl[:1], indent=2, default=str)
        )

    if len(ids) > 1:
        system_prompt = (
            "You assist clinicians using a Neo4j patient graph. MULTIPLE patients are selected — COMPARISON MODE.\n"
            "Hard rules: (1) You may ONLY reason about patient ids explicitly listed in the MULTI-PATIENT_COMPARISON_MODE "
            "banner and their SECTION_START/END blocks. Never introduce a third patient’s clinical facts.\n"
            "(2) Answer ONLY from those sections plus protocol JSON — graph relationships "
            "(HAS_DISEASE, HAS_SYMPTOM, HAS_VIOLATION, HAS_CLINICAL_STATE, TREATED_WITH, HAD_PROCEDURE) "
            "take priority over generic medical knowledge.\n"
            "(3) Compare only attributes present in the data; label which patient each fact belongs to.\n"
            "(4) Do not merge one patient’s diseases, drugs, or violations onto another.\n"
            "(5) Evidence bullets must name the patient_id each fact refers to."
        )
    else:
        pid0 = ids[0]
        system_prompt = (
            f"You assist clinicians using a Neo4j patient graph. Exactly ONE patient is in scope: patient_id={pid0}.\n"
            "You may ONLY reason using data from the currently selected patient context for that id. "
            "Every clinical claim must be traceable to the PATIENT_DATA_SCOPE / SINGLE-PATIENT_SCOPE block or its "
            "graph edges (HAS_DISEASE, HAS_SYMPTOM, HAS_VIOLATION, HAS_CLINICAL_STATE, TREATED_WITH, HAD_PROCEDURE, "
            "HAS_NOTE). "
            "Do not use another patient’s diseases, drugs, symptoms, notes, or violations. "
            "If the user’s question references a different patient id, explain that your context is locked to "
            f"patient_id={pid0} and answer only from data for {pid0}. "
            "Protocol guidelines are reference templates — tie them only to diseases that appear for this patient. "
            "If data is missing, say so; do not invent or borrow from other patients."
        )

    if len(ids) == 1:
        user_prefix = (
            f"ACTIVE_PATIENT_ID: {ids[0]}\n"
            f"ACTIVE_PATIENT_NAME: {summaries[0].get('name') or ids[0]}\n"
            "MANDATORY_ISOLATION: Use only facts for ACTIVE_PATIENT_ID. Cross-patient reasoning is forbidden.\n\n"
        )
    else:
        user_prefix = (
            "COMPARISON_ALLOWLIST_PATIENT_IDS: "
            + ", ".join(ids)
            + "\nMANDATORY: Attribute each clinical fact to the correct patient_id from this list only.\n\n"
        )

    answer = _call_llm_smart(question, context_block, system_prompt, user_prefix=user_prefix)

    all_violation = any(s["violations"] for s in summaries)
    protocol_expected: list[str] = []
    actual_treatment: list[str] = []
    highlight_nodes: list[str] = []
    highlight_relationships: list[str] = []
    paths: list[dict] = []

    for s in summaries:
        pid = s["pid"]
        highlight_nodes.append(f"Patient:{pid}")
        analysis = s["analysis"]
        for r in analysis.get("compliance_results") or []:
            if r.get("disease_id"):
                highlight_nodes.append(f"Disease:{r['disease_id']}")
                highlight_relationships.append("HAS_DISEASE")
            if r.get("recommended_drug_name"):
                protocol_expected.append(r["recommended_drug_name"])
            if r.get("recommended_procedure_name"):
                protocol_expected.append(r["recommended_procedure_name"])
            actual_treatment.extend(r.get("actual_drug_names") or [])
            actual_treatment.extend(r.get("actual_procedure_names") or [])
            for did in r.get("actual_drug_ids") or []:
                highlight_nodes.append(f"Drug:{did}")
                highlight_relationships.append("TREATED_WITH")
            for pid2 in r.get("actual_procedure_ids") or []:
                highlight_nodes.append(f"Procedure:{pid2}")
                highlight_relationships.append("HAD_PROCEDURE")
        if s["clinical_state"]:
            highlight_relationships.append("HAS_CLINICAL_STATE")
        if s["violations"]:
            highlight_relationships.append("HAS_VIOLATION")
        for p in _build_path_from_patient_analysis(analysis):
            paths.append(p)

    highlight_nodes = list(dict.fromkeys(highlight_nodes))
    highlight_relationships = list(dict.fromkeys(highlight_relationships))
    protocol_expected = list(dict.fromkeys(x for x in protocol_expected if x))
    actual_treatment = list(dict.fromkeys(x for x in actual_treatment if x))

    pids_str = ", ".join(f"'{p}'" for p in ids)
    highlight_query = (
        f"MATCH (p:Patient) WHERE p.id IN [{pids_str}]"
        " OPTIONAL MATCH (p)-[:HAS_DISEASE]->(d:Disease)"
        " OPTIONAL MATCH (p)-[:TREATED_WITH]->(drug:Drug)"
        " OPTIONAL MATCH (p)-[:HAD_PROCEDURE]->(proc:Procedure)"
        " OPTIONAL MATCH (p)-[:HAS_CLINICAL_STATE]->(c:ClinicalState)"
        " OPTIONAL MATCH (p)-[:HAS_VIOLATION]->(v:Violation)"
        " RETURN p, d, drug, proc, c, v"
    )

    return {
        "answer": answer,
        "violation": all_violation,
        "protocol_expected": protocol_expected,
        "actual_treatment": actual_treatment,
        "highlight_nodes": highlight_nodes,
        "highlight_relationships": highlight_relationships,
        "highlight_query": highlight_query,
        "paths": paths,
        "selected_patients": [{"pid": s["pid"], "name": s["name"]} for s in summaries],
    }


def _call_llm_smart(
    question: str,
    context: str,
    system_prompt: str,
    *,
    user_prefix: str = "",
) -> str:
    """Call OpenAI with a custom system prompt for patient-aware answers."""
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return (
            "No OPENAI_API_KEY set. Here is the retrieved patient context:\n\n"
            + (user_prefix or "")
            + (context[:2000] or "No context.")
        )
    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": (
                        user_prefix
                        + f"Patient data:\n{context}\n\nQuestion: {question}"
                        + _STRUCTURED_SECTIONS_USER_SUFFIX
                    ),
                },
            ],
            max_tokens=1200,
            temperature=_LLM_TEMPERATURE,
        )
        return (response.choices[0].message.content or "").strip()
    except Exception as e:
        return f"LLM error: {e}. Context summary:\n{context[:1000]}"


if __name__ == "__main__":
    import sys
    q = sys.argv[1] if len(sys.argv) > 1 else "Did patient P1 follow the diabetes treatment protocol?"
    print("Question:", q)
    result = ask_agent(q)
    print("Answer:", result.get("answer"))
    print("Violation:", result.get("violation"))
    print("Protocol expected:", result.get("protocol_expected"))
    print("Actual treatment:", result.get("actual_treatment"))
    print("Highlight query:\n", result.get("highlight_query"))
