"""
Sepsis clinical compliance: generate patient data with SOFA/clinical state and sepsis guideline graph.
Uses neo4j_connect.run_query for all Neo4j operations.
"""
import random
from neo4j_connect import run_query


def _run(cypher: str, params: dict | None = None):
    run_query(cypher, params or {})


def create_patient_sofa_nodes(clear_first: bool = True):
    """
    Create 20 patients (if clear_first) or attach ClinicalState to existing patients (if clear_first=False).
    Each patient has a ClinicalState node (HAS_CLINICAL_STATE) with:
    hours_from_icu_admit, sofa_score, sofa_delta_6h, map, gcs, creatinine, lactate,
    vasopressors_active, antibiotics_active, cultures_ordered; optional qsofa, esofa.
    When clear_first=False: does not delete or create Patient nodes; only MATCH existing Patient and CREATE ClinicalState.
    """
    if clear_first:
        _run("MATCH (n) DETACH DELETE n")

    # 10 patients with HIGH SOFA (e.g. 8-14) - at-risk sepsis patients
    # Some with violations: no abx (P3,P7,P10), no cultures with abx (P5), low MAP no vaso (P9)
    high_sofa_patients = [
        ("P1", "Alice Smith", 62, "F", 18, 10, 2, 58, 10, 2.1, 3.2, True, True, True, 2, 10),   # compliant
        ("P2", "Bob Jones", 71, "M", 12, 12, 3, 52, 8, 2.8, 4.1, True, True, True, 3, 12),   # compliant
        ("P3", "Carol White", 58, "F", 24, 9, 1, 55, 12, 1.9, 2.8, False, False, True, 2, 9), # VIOLATION: SOFA≥2, no antibiotics
        ("P4", "David Lee", 65, "M", 6, 11, 4, 50, 7, 3.2, 5.0, True, True, True, 3, 11),   # compliant
        ("P5", "Eva Martinez", 54, "F", 30, 8, 0, 60, 11, 1.8, 2.5, False, True, False, 2, 8), # VIOLATION: cultures not ordered with abx
        ("P6", "Frank Chen", 69, "M", 15, 13, 3, 48, 6, 3.5, 4.8, True, True, True, 3, 13),   # compliant
        ("P7", "Grace Kim", 45, "F", 9, 10, 2, 56, 9, 2.2, 3.0, False, False, False, 2, 10), # VIOLATION: SOFA≥2, no abx, no cultures
        ("P8", "Henry Wilson", 78, "M", 36, 14, 5, 45, 5, 4.0, 6.2, True, True, True, 3, 14), # compliant
        ("P9", "Iris Davis", 51, "F", 21, 9, 1, 52, 10, 2.0, 2.9, False, True, True, 2, 9),   # VIOLATION: MAP 52 < 65, no vasopressors
        ("P10", "Jack Brown", 67, "M", 27, 11, 2, 53, 8, 2.5, 3.8, True, False, False, 2, 11), # VIOLATION: SOFA≥2, no antibiotics
    ]
    # (id, name, age, sex, hours_from_icu_admit, sofa_score, sofa_delta_6h, map, gcs, creatinine, lactate,
    #  vasopressors_active, antibiotics_active, cultures_ordered, qsofa, esofa)

    # 10 patients with LOW SOFA (e.g. 0-4) - stable; some SOFA 2–4 trigger bundle (abx recommended)
    # P11,P12,P16,P18,P20: SOFA≥2 but no antibiotics = VIOLATION; P12 also lactate>2 = double violation
    low_sofa_patients = [
        ("P11", "Kate Moore", 44, "F", 48, 2, 0, 82, 15, 0.9, 1.0, False, False, True, 0, 2),   # VIOLATION: SOFA 2, no abx
        ("P12", "Leo Garcia", 55, "M", 72, 3, -1, 78, 15, 1.0, 2.6, False, False, True, 0, 3),  # VIOLATION: SOFA 3, lactate>2, no abx
        ("P13", "Mia Johnson", 39, "F", 24, 1, 0, 85, 15, 0.8, 0.8, False, False, False, 0, 1),  # compliant (low SOFA)
        ("P14", "Noah Williams", 61, "M", 96, 4, 0, 75, 14, 1.1, 1.4, False, True, True, 1, 4), # compliant (has abx)
        ("P15", "Olivia Clark", 47, "F", 12, 0, 0, 88, 15, 0.7, 0.7, False, False, False, 0, 0),  # compliant
        ("P16", "Paul Lewis", 52, "M", 60, 2, 0, 80, 15, 0.95, 1.0, False, False, True, 0, 2),   # VIOLATION: SOFA 2, no abx
        ("P17", "Quinn Taylor", 43, "F", 36, 1, 0, 84, 15, 0.85, 0.9, False, False, False, 0, 1), # compliant
        ("P18", "Ryan Adams", 59, "M", 84, 3, 0, 76, 15, 1.05, 1.1, False, False, True, 0, 3),   # VIOLATION: SOFA 3, no abx
        ("P19", "Sofia Hernandez", 41, "F", 18, 0, 0, 86, 15, 0.75, 0.8, False, False, False, 0, 0), # compliant
        ("P20", "Tom Anderson", 56, "M", 42, 2, 0, 79, 15, 1.0, 1.0, False, False, True, 0, 2),   # VIOLATION: SOFA 2, no abx
    ]

    count = 0
    for row in high_sofa_patients + low_sofa_patients:
        pid, name, age, sex = row[0], row[1], row[2], row[3]
        hours, sofa, sofa_delta = row[4], row[5], row[6]
        map_val, gcs_val = row[7], row[8]
        creat, lactate = row[9], row[10]
        vaso, abx, cultures = row[11], row[12], row[13]
        qsofa, esofa = row[14], row[15]
        cid = f"CS_{pid}"
        if clear_first:
            _run("""
            CREATE (p:Patient {id: $pid, name: $name, age: $age, sex: $sex})
            CREATE (c:ClinicalState {
                id: $cid,
                hours_from_icu_admit: $hours,
                sofa_score: $sofa,
                sofa_delta_6h: $sofa_delta,
                map: $map,
                gcs: $gcs,
                creatinine: $creatinine,
                lactate: $lactate,
                vasopressors_active: $vasopressors_active,
                antibiotics_active: $antibiotics_active,
                cultures_ordered: $cultures_ordered,
                qsofa: $qsofa,
                esofa: $esofa
            })
            CREATE (p)-[:HAS_CLINICAL_STATE]->(c)
            RETURN p.id
            """, {
                "pid": pid, "name": name, "age": age, "sex": sex,
                "cid": cid,
                "hours": hours, "sofa": sofa, "sofa_delta": sofa_delta,
                "map": map_val, "gcs": gcs_val,
                "creatinine": creat, "lactate": lactate,
                "vasopressors_active": vaso, "antibiotics_active": abx, "cultures_ordered": cultures,
                "qsofa": qsofa, "esofa": esofa,
            })
            count += 1
        else:
            # Add ClinicalState to existing Patient (e.g. after seed_data)
            rows = run_query("""
            MATCH (p:Patient {id: $pid})
            MERGE (c:ClinicalState {id: $cid})
            ON CREATE SET
                c.hours_from_icu_admit = $hours,
                c.sofa_score = $sofa,
                c.sofa_delta_6h = $sofa_delta,
                c.map = $map,
                c.gcs = $gcs,
                c.creatinine = $creatinine,
                c.lactate = $lactate,
                c.vasopressors_active = $vasopressors_active,
                c.antibiotics_active = $antibiotics_active,
                c.cultures_ordered = $cultures_ordered,
                c.qsofa = $qsofa,
                c.esofa = $esofa
            WITH p, c
            MERGE (p)-[:HAS_CLINICAL_STATE]->(c)
            RETURN p.id AS id
            """, {
                "pid": pid, "cid": cid,
                "hours": hours, "sofa": sofa, "sofa_delta": sofa_delta,
                "map": map_val, "gcs": gcs_val,
                "creatinine": creat, "lactate": lactate,
                "vasopressors_active": vaso, "antibiotics_active": abx, "cultures_ordered": cultures,
                "qsofa": qsofa, "esofa": esofa,
            })
            if rows:
                count += 1
    return count


def create_sepsis_guideline_graph():
    """
    Create sepsis guideline graph: SepsisGuideline, RecommendedAction, LabCheck, Drug, Procedure, FollowUp
    with RECOMMENDS_ACTION, REQUIRES_LAB, RECOMMENDED_DRUG, RECOMMENDED_PROCEDURE, FOLLOW_UP.
    Includes thresholds for SOFA / sepsis criteria and interventions for high-risk patients.
    """
    # Guideline node with thresholds (SOFA >= 2 suggests sepsis; lactate > 2, etc.)
    _run("""
    MERGE (g:SepsisGuideline {id: 'SEPSIS_1', name: 'Sepsis-3 / Hour-1 Bundle'})
    SET g.sofa_threshold_high = 2,
        g.lactate_threshold_mmol = 2,
        g.map_threshold_mmhg = 65,
        g.description = 'SOFA increase >= 2, lactate > 2 mmol/L, or hypotension: initiate bundle within 1 hour'
    RETURN g.id
    """)

    # Recommended actions
    _run("MERGE (a:RecommendedAction {id: 'ACT_LACTATE', name: 'Measure lactate', threshold_desc: 'Lactate > 2 mmol/L'}) RETURN a.id")
    _run("MERGE (a:RecommendedAction {id: 'ACT_ABX', name: 'Broad-spectrum antibiotics', threshold_desc: 'Within 1 hour if sepsis suspected'}) RETURN a.id")
    _run("MERGE (a:RecommendedAction {id: 'ACT_CULTURES', name: 'Blood cultures before antibiotics', threshold_desc: 'Before or with first dose'}) RETURN a.id")
    _run("MERGE (a:RecommendedAction {id: 'ACT_FLUIDS', name: 'Fluid resuscitation', threshold_desc: 'If MAP < 65 or lactate >= 4'}) RETURN a.id")
    _run("MERGE (a:RecommendedAction {id: 'ACT_VASO', name: 'Vasopressors if refractory hypotension', threshold_desc: 'MAP < 65 despite fluids'}) RETURN a.id")

    # Lab checks
    _run("MERGE (l:LabCheck {id: 'LAB_LACTATE', name: 'Lactate', unit: 'mmol/L', threshold_high: 2}) RETURN l.id")
    _run("MERGE (l:LabCheck {id: 'LAB_CREAT', name: 'Creatinine', unit: 'mg/dL', threshold_high: 1.2}) RETURN l.id")

    # Drugs and procedures (for compatibility with existing graph style)
    _run("MERGE (d:Drug {id: 'DRUG_ABX', name: 'Broad-spectrum antibiotics'}) RETURN d.id")
    _run("MERGE (d:Drug {id: 'DRUG_VASO', name: 'Vasopressor'}) RETURN d.id")
    _run("MERGE (pr:Procedure {id: 'PROC_CULTURES', name: 'Blood cultures'}) RETURN pr.id")
    _run("MERGE (pr:Procedure {id: 'PROC_FLUIDS', name: 'IV fluid bolus'}) RETURN pr.id")
    _run("MERGE (f:FollowUp {id: 'FU_REASSESS', name: 'Reassess volume status and lactate in 2-4h'}) RETURN f.id")

    # Link guideline to actions and thresholds
    _run("MATCH (g:SepsisGuideline {id: 'SEPSIS_1'}), (a:RecommendedAction {id: 'ACT_LACTATE'}) MERGE (g)-[:RECOMMENDS_ACTION]->(a)")
    _run("MATCH (g:SepsisGuideline {id: 'SEPSIS_1'}), (a:RecommendedAction {id: 'ACT_ABX'}) MERGE (g)-[:RECOMMENDS_ACTION]->(a)")
    _run("MATCH (g:SepsisGuideline {id: 'SEPSIS_1'}), (a:RecommendedAction {id: 'ACT_CULTURES'}) MERGE (g)-[:RECOMMENDS_ACTION]->(a)")
    _run("MATCH (g:SepsisGuideline {id: 'SEPSIS_1'}), (a:RecommendedAction {id: 'ACT_FLUIDS'}) MERGE (g)-[:RECOMMENDS_ACTION]->(a)")
    _run("MATCH (g:SepsisGuideline {id: 'SEPSIS_1'}), (a:RecommendedAction {id: 'ACT_VASO'}) MERGE (g)-[:RECOMMENDS_ACTION]->(a)")

    _run("MATCH (g:SepsisGuideline {id: 'SEPSIS_1'}), (l:LabCheck {id: 'LAB_LACTATE'}) MERGE (g)-[:REQUIRES_LAB]->(l)")
    _run("MATCH (g:SepsisGuideline {id: 'SEPSIS_1'}), (l:LabCheck {id: 'LAB_CREAT'}) MERGE (g)-[:REQUIRES_LAB]->(l)")

    _run("MATCH (g:SepsisGuideline {id: 'SEPSIS_1'}), (d:Drug {id: 'DRUG_ABX'}) MERGE (g)-[:RECOMMENDED_DRUG]->(d)")
    _run("MATCH (g:SepsisGuideline {id: 'SEPSIS_1'}), (d:Drug {id: 'DRUG_VASO'}) MERGE (g)-[:RECOMMENDED_DRUG]->(d)")
    _run("MATCH (g:SepsisGuideline {id: 'SEPSIS_1'}), (pr:Procedure {id: 'PROC_CULTURES'}) MERGE (g)-[:RECOMMENDED_PROCEDURE]->(pr)")
    _run("MATCH (g:SepsisGuideline {id: 'SEPSIS_1'}), (pr:Procedure {id: 'PROC_FLUIDS'}) MERGE (g)-[:RECOMMENDED_PROCEDURE]->(pr)")
    _run("MATCH (g:SepsisGuideline {id: 'SEPSIS_1'}), (f:FollowUp {id: 'FU_REASSESS'}) MERGE (g)-[:FOLLOW_UP]->(f)")

    return True


def seed_sepsis(clear_first: bool = True):
    """Create sepsis patients and guideline graph. Set clear_first=False to add to existing graph."""
    n = create_patient_sofa_nodes(clear_first=clear_first)
    create_sepsis_guideline_graph()
    print(f"Sepsis seed complete: {n} patients with ClinicalState, SepsisGuideline + actions/labs/drugs/procedures.")
    return n


def seed_full_with_sepsis(light: bool = True):
    """
    Build full healthcare graph (patients, diseases, doctors, hospitals, labs, notes) then add sepsis
    (ClinicalState per patient + SepsisGuideline). Use this to show both protocol compliance and sepsis in one graph.
    If light=True (default), seed uses fewer appointments/encounters so sepsis patients stand out.
    """
    from seed_data import seed
    seed(light=light)
    n = create_patient_sofa_nodes(clear_first=False)
    create_sepsis_guideline_graph()
    print(f"Full graph + sepsis: {n} patients with ClinicalState, SepsisGuideline + actions. Run dashboard.py to refresh.")
    return n


if __name__ == "__main__":
    import sys
    if "full" in (sys.argv[1:] or [""]) or "--full" in (sys.argv[1:] or []):
        seed_full_with_sepsis()
    elif "add" in (sys.argv[1:] or [""]) or "--add" in (sys.argv[1:] or []) or "add-to-existing" in (sys.argv[1:] or []):
        seed_sepsis(clear_first=False)
    else:
        seed_sepsis(clear_first=True)
