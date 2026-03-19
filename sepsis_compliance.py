"""
Sepsis guideline evaluation: compare patient clinical state to sepsis guidelines.
Returns violations, compliance, and dashboard-ready paths (nodes, colors, hover_info, highlight_query).
Uses neo4j_ops and neo4j_connect.run_query.
"""
from typing import Any

from neo4j_ops import get_patient_clinical_state, get_sepsis_guidelines, get_patients_with_clinical_state
from neo4j_connect import run_query

PATH_COLORS = {
    "patient": "#3498db",
    "compliant": "#27ae60",
    "violation": "#e74c3c",
    "minor": "#e67e22",
    "neutral": "#95a5a6",
}


def run_sepsis_guidelines(patient_id: str) -> dict[str, Any]:
    """
    Compare patient clinical state to sepsis guideline thresholds.
    Returns: patient_id, patient_name, violations (list), compliance (bool),
             highlight_nodes, highlight_relationships, highlight_query, paths (for dashboard).
    """
    state = get_patient_clinical_state(patient_id)
    if not state:
        return {
            "patient_id": patient_id,
            "patient_name": None,
            "violations": ["No clinical state found for this patient."],
            "compliance": False,
            "highlight_nodes": [],
            "highlight_relationships": [],
            "highlight_query": "MATCH (p:Patient) RETURN p LIMIT 1",
            "paths": [],
        }

    guidelines = get_sepsis_guidelines()
    if not guidelines:
        return {
            "patient_id": patient_id,
            "patient_name": None,
            "violations": ["No sepsis guideline found in graph."],
            "compliance": False,
            "highlight_nodes": [],
            "highlight_relationships": [],
            "highlight_query": "MATCH (p:Patient)-[:HAS_CLINICAL_STATE]->(c:ClinicalState) WHERE p.id = $pid RETURN p, c",
            "paths": [],
        }

    # Get patient name
    rows = run_query("MATCH (p:Patient {id: $pid}) RETURN p.name AS name", {"pid": patient_id})
    patient_name = (rows[0].get("name") or patient_id) if rows else patient_id

    g = guidelines[0]
    sofa_threshold = g.get("sofa_threshold_high") or 2
    lactate_threshold = (g.get("lactate_threshold_mmol") or 2) if g.get("lactate_threshold_mmol") is not None else 2
    map_threshold = g.get("map_threshold_mmhg") or 65

    sofa = state.get("sofa_score")
    lactate = state.get("lactate")
    map_val = state.get("map")
    abx = state.get("antibiotics_active")
    cultures = state.get("cultures_ordered")
    vaso = state.get("vasopressors_active")

    violations = []
    # High SOFA (>= threshold) -> should have antibiotics, cultures, consider vasopressors if MAP low
    if sofa is not None and sofa >= sofa_threshold:
        if not abx:
            violations.append(f"SOFA {sofa} >= {sofa_threshold}: antibiotics not active (recommended within 1h).")
        if not cultures and abx:
            violations.append("Blood cultures should be ordered before or with first antibiotic dose.")
    if lactate is not None and lactate > lactate_threshold and not abx:
        violations.append(f"Lactate {lactate} > {lactate_threshold} mmol/L: antibiotics recommended.")
    if map_val is not None and map_val < map_threshold and not vaso:
        violations.append(f"MAP {map_val} < {map_threshold} mmHg: consider vasopressors if refractory to fluids.")

    compliance = len(violations) == 0

    # Build path for dashboard: Patient -> ClinicalState -> SepsisGuideline -> actions/labs/drugs
    state_id = state.get("state_id") or f"CS_{patient_id}"
    guideline_id = g.get("guideline_id") or "SEPSIS_1"

    nodes = [f"Patient:{patient_id}", f"ClinicalState:{state_id}", f"SepsisGuideline:{guideline_id}"]
    node_colors = [PATH_COLORS["patient"], PATH_COLORS["violation"] if not compliance else PATH_COLORS["neutral"], PATH_COLORS["neutral"]]
    labels = [f"{patient_name} (Patient)", "Clinical state", g.get("guideline_name") or "Sepsis guideline"]
    violations_str = "; ".join(violations) if violations else "None"
    state_hover = (f"Violations: {violations_str}. " if violations else "Compliant. ") + f"SOFA={sofa}, lactate={lactate}, MAP={map_val}. Thresholds: SOFA>={sofa_threshold}, lactate>{lactate_threshold}, MAP<{map_threshold}."
    node_hover = [
        f"Patient {patient_id}. " + (f"Violations: {violations_str}. " if violations else "Compliant. ") + f"SOFA={sofa}, lactate={lactate}, antibiotics={abx}, cultures={cultures}.",
        state_hover,
        f"Guideline: {g.get('guideline_name')}. Thresholds: SOFA>={sofa_threshold}, lactate>{lactate_threshold} mmol/L, MAP<{map_threshold} mmHg.",
    ]
    action_ids = [x for x in (g.get("action_ids") or []) if x]
    relationships = ["HAS_CLINICAL_STATE"]
    edge_hover = ["Current clinical state"]

    for aid in action_ids[:5]:
        nodes.append(f"RecommendedAction:{aid}")
        node_colors.append(PATH_COLORS["compliant"] if compliance else PATH_COLORS["violation"])
        labels.append(aid.replace("ACT_", "").replace("_", " ").title())
        node_hover.append(f"Guideline recommends: {aid}")
        relationships.append("RECOMMENDS_ACTION")
        edge_hover.append("RECOMMENDS_ACTION")
    hover_info = list(node_hover) + list(edge_hover)
    edge_colors = [PATH_COLORS["neutral"]] * len(relationships)

    highlight_query = (
        f"MATCH (p:Patient {{id: '{patient_id}'}})-[:HAS_CLINICAL_STATE]->(c:ClinicalState) "
        "OPTIONAL MATCH (g:SepsisGuideline) "
        "RETURN p, c, g"
    )

    highlight_nodes = [f"Patient:{patient_id}", f"ClinicalState:{state_id}", f"SepsisGuideline:{guideline_id}"] + [f"RecommendedAction:{a}" for a in action_ids[:5]]
    highlight_relationships = ["HAS_CLINICAL_STATE", "RECOMMENDS_ACTION"]

    path = {
        "nodes": nodes,
        "relationships": relationships,
        "colors": {"node_colors": node_colors, "edge_colors": edge_colors},
        "labels": labels,
        "hover_info": hover_info,
        "node_hover": node_hover,
        "edge_hover": edge_hover,
        "highlight_query": highlight_query,
    }

    # Include full guideline dict so agent can add LabCheck, Drug, Procedure, FollowUp to highlight_nodes
    guideline_export = dict(g) if g else {}
    return {
        "patient_id": patient_id,
        "patient_name": patient_name,
        "violations": violations,
        "compliance": compliance,
        "clinical_state": state,
        "guideline": guideline_export,
        "highlight_nodes": highlight_nodes,
        "highlight_relationships": highlight_relationships,
        "highlight_query": highlight_query,
        "paths": [path],
    }


def run_sepsis_guidelines_all() -> list[dict[str, Any]]:
    """Run sepsis guideline evaluation for all patients with clinical state."""
    patients = get_patients_with_clinical_state()
    pid_list = list(dict.fromkeys(r.get("patient_id") for r in patients if r.get("patient_id")))
    return [run_sepsis_guidelines(pid) for pid in pid_list]


def sync_violations_to_neo4j() -> int:
    """
    Write current sepsis and disease protocol violations to Neo4j as Violation nodes and (Patient)-[:HAS_VIOLATION]->(Violation).
    Sepsis ids: V_{patient_id}_{index}. Disease ids: V_D_{patient_id}_{disease_id}_{index}.
    Returns count of violation nodes created.
    """
    run_query("MATCH (v:Violation) DETACH DELETE v")
    count = 0
    # Sepsis guideline violations
    results = run_sepsis_guidelines_all()
    for r in results:
        if r.get("compliance"):
            continue
        pid = r.get("patient_id")
        violations = r.get("violations") or []
        for i, vtext in enumerate(violations):
            vid = f"V_{pid}_{i}"
            run_query(
                """
                MATCH (p:Patient {id: $pid})
                CREATE (v:Violation {id: $vid, description: $description, source: 'sepsis'})
                CREATE (p)-[:HAS_VIOLATION]->(v)
                RETURN v.id
                """,
                {"pid": pid, "vid": vid, "description": (vtext or "")[:500]},
            )
            count += 1
    # Disease protocol violations (wrong/missing drug or procedure)
    from ai_compliance import run_compliance_check
    compliance = run_compliance_check()
    for r in compliance.get("patients_with_violations") or []:
        pid = r.get("patient_id")
        did = r.get("disease_id")
        dname = r.get("disease_name") or did
        violations = r.get("violations") or []
        for i, vtext in enumerate(violations):
            vid = f"V_D_{pid}_{did}_{i}"
            desc = (vtext or "")[:500]
            run_query(
                """
                MATCH (p:Patient {id: $pid})
                CREATE (v:Violation {id: $vid, description: $description, source: 'protocol', disease_id: $did, disease_name: $dname})
                CREATE (p)-[:HAS_VIOLATION]->(v)
                RETURN v.id
                """,
                {"pid": pid, "vid": vid, "description": desc, "did": did, "dname": dname},
            )
            count += 1
    return count
