"""
Neo4j graph operations: queries, relationship creation, updates, and deletes.
Graph model:
  Nodes: Patient, Doctor, Disease, Drug, Hospital, Appointment, Encounter, Lab, Procedure
  Relationships: HAS_DISEASE, TREATED_WITH, TREATS, VISITS, WITH_DOCTOR, HAS_APPOINTMENT,
                 HAS_ENCOUNTER, AT_HOSPITAL, INCLUDES_LAB, INCLUDES_PROCEDURE, PERFORMED_BY
  Properties: id, name, age, sex, specialty, date, diagnosed_on, icd10
"""
from neo4j_connect import run_query


# -------- Query (read) operations --------


def get_patients_with_diseases():
    """Retrieve all patients with their diseases (and optional diagnosed_on, icd10 on relationship)."""
    cypher = """
    MATCH (p:Patient)
    OPTIONAL MATCH (p)-[r:HAS_DISEASE]->(d:Disease)
    RETURN p.id AS patient_id, p.name AS patient_name, p.age AS patient_age, p.sex AS patient_sex,
           d.id AS disease_id, d.name AS disease_name, d.icd10 AS disease_icd10,
           r.diagnosed_on AS diagnosed_on
    ORDER BY p.id, d.id
    """
    return run_query(cypher)


def get_doctors_and_specialties():
    """Retrieve all doctors and their specialties."""
    cypher = """
    MATCH (d:Doctor)
    RETURN d.id AS doctor_id, d.name AS doctor_name, d.specialty AS specialty
    ORDER BY d.id
    """
    return run_query(cypher)


def get_doctors_treating_diseases():
    """Retrieve which doctors treat which diseases."""
    cypher = """
    MATCH (doc:Doctor)-[:TREATS]->(d:Disease)
    RETURN doc.id AS doctor_id, doc.name AS doctor_name, doc.specialty AS specialty,
           d.id AS disease_id, d.name AS disease_name, d.icd10 AS disease_icd10
    ORDER BY doc.id, d.id
    """
    return run_query(cypher)


def get_patient_appointments(patient_id: str):
    """Retrieve all appointments of a patient by patient id."""
    cypher = """
    MATCH (p:Patient {id: $patient_id})-[:HAS_APPOINTMENT]->(a:Appointment)
    RETURN a.id AS appointment_id, a.date AS appointment_date
    ORDER BY a.date
    """
    return run_query(cypher, {"patient_id": patient_id})


def get_hospitals_visited_by_patients():
    """Retrieve hospitals visited by patients (via appointments or encounters)."""
    cypher = """
    MATCH (p:Patient)-[:HAS_APPOINTMENT]->(a:Appointment)-[:AT_HOSPITAL]->(h:Hospital)
    RETURN p.id AS patient_id, p.name AS patient_name,
           a.id AS appointment_id, a.date AS appointment_date,
           h.id AS hospital_id, h.name AS hospital_name
    ORDER BY p.id, a.date
    """
    records = run_query(cypher)
    if records:
        return records
    # Fallback: encounters at hospital
    cypher2 = """
    MATCH (p:Patient)-[:HAS_ENCOUNTER]->(e:Encounter)-[:AT_HOSPITAL]->(h:Hospital)
    RETURN p.id AS patient_id, p.name AS patient_name,
           e.id AS encounter_id, h.id AS hospital_id, h.name AS hospital_name
    ORDER BY p.id
    """
    return run_query(cypher2)


# -------- Create relationship operations --------


def create_has_disease(patient_id: str, disease_id: str, diagnosed_on: str | None = None):
    """Connect a Patient to a Disease using HAS_DISEASE. Optionally set diagnosed_on on the relationship."""
    params = {"patient_id": patient_id, "disease_id": disease_id}
    if diagnosed_on:
        params["diagnosed_on"] = diagnosed_on
    cypher = """
    MATCH (p:Patient {id: $patient_id}), (d:Disease {id: $disease_id})
    MERGE (p)-[r:HAS_DISEASE]->(d)
    """
    if diagnosed_on:
        cypher += " SET r.diagnosed_on = $diagnosed_on"
    cypher += " RETURN p.id AS patient_id, d.id AS disease_id"
    result = run_query(cypher, params)
    return result


def create_treats(doctor_id: str, disease_id: str):
    """Connect a Doctor to a Disease using TREATS."""
    cypher = """
    MATCH (doc:Doctor {id: $doctor_id}), (d:Disease {id: $disease_id})
    MERGE (doc)-[:TREATS]->(d)
    RETURN doc.id AS doctor_id, d.id AS disease_id
    """
    return run_query(cypher, {"doctor_id": doctor_id, "disease_id": disease_id})


def create_visits(patient_id: str, doctor_id: str):
    """Connect a Patient to a Doctor using VISITS."""
    cypher = """
    MATCH (p:Patient {id: $patient_id}), (doc:Doctor {id: $doctor_id})
    MERGE (p)-[:VISITS]->(doc)
    RETURN p.id AS patient_id, doc.id AS doctor_id
    """
    return run_query(cypher, {"patient_id": patient_id, "doctor_id": doctor_id})


def create_at_hospital(appointment_id: str, hospital_id: str):
    """Connect an Appointment to a Hospital using AT_HOSPITAL."""
    cypher = """
    MATCH (a:Appointment {id: $appointment_id}), (h:Hospital {id: $hospital_id})
    MERGE (a)-[:AT_HOSPITAL]->(h)
    RETURN a.id AS appointment_id, h.id AS hospital_id
    """
    return run_query(cypher, {"appointment_id": appointment_id, "hospital_id": hospital_id})


# -------- Update operations --------


def update_patient_age(patient_id: str, age: int):
    """Update a patient's age."""
    cypher = """
    MATCH (p:Patient {id: $patient_id})
    SET p.age = $age
    RETURN p.id AS patient_id, p.age AS age
    """
    return run_query(cypher, {"patient_id": patient_id, "age": age})


def update_doctor_specialty(doctor_id: str, specialty: str):
    """Update a doctor's specialty."""
    cypher = """
    MATCH (d:Doctor {id: $doctor_id})
    SET d.specialty = $specialty
    RETURN d.id AS doctor_id, d.specialty AS specialty
    """
    return run_query(cypher, {"doctor_id": doctor_id, "specialty": specialty})


def update_diagnosed_on(patient_id: str, disease_id: str, diagnosed_on: str):
    """Update the diagnosed_on date for a HAS_DISEASE relationship."""
    cypher = """
    MATCH (p:Patient {id: $patient_id})-[r:HAS_DISEASE]->(d:Disease {id: $disease_id})
    SET r.diagnosed_on = $diagnosed_on
    RETURN p.id AS patient_id, d.id AS disease_id, r.diagnosed_on AS diagnosed_on
    """
    return run_query(cypher, {"patient_id": patient_id, "disease_id": disease_id, "diagnosed_on": diagnosed_on})


# -------- Delete operations --------


def delete_patient(patient_id: str):
    """Delete a patient and all their relationships (DETACH DELETE)."""
    cypher = """
    MATCH (p:Patient {id: $patient_id})
    DETACH DELETE p
    """
    run_query(cypher, {"patient_id": patient_id})
    return True


def delete_patient_disease_relationship(patient_id: str, disease_id: str):
    """Delete the HAS_DISEASE relationship between a Patient and a Disease."""
    cypher = """
    MATCH (p:Patient {id: $patient_id})-[r:HAS_DISEASE]->(d:Disease {id: $disease_id})
    DELETE r
    """
    run_query(cypher, {"patient_id": patient_id, "disease_id": disease_id})
    return True


# -------- Protocol guideline graph (Disease → Drug → Procedure → Follow-up) --------


def get_protocol_guidelines():
    """
    Retrieve the protocol guideline graph: Disease → Recommended Drug → Recommended Procedure → Follow-up.
    Returns list of dicts with disease_id, disease_name, drug_id, drug_name, procedure_id, procedure_name,
    followup_id, followup_name.
    """
    cypher = """
    MATCH (d:Disease)-[:RECOMMENDED_DRUG]->(drug:Drug)-[:RECOMMENDED_PROCEDURE]->(proc:Procedure)-[:FOLLOW_UP]->(f:FollowUp)
    RETURN d.id AS disease_id, d.name AS disease_name, d.icd10 AS disease_icd10,
           drug.id AS drug_id, drug.name AS drug_name,
           proc.id AS procedure_id, proc.name AS procedure_name,
           f.id AS followup_id, f.name AS followup_name
    ORDER BY d.id
    """
    return run_query(cypher)


def get_protocol_for_disease(disease_id: str):
    """Get the recommended drug, procedure, and follow-up for a single disease."""
    cypher = """
    MATCH (d:Disease {id: $disease_id})-[:RECOMMENDED_DRUG]->(drug:Drug)-[:RECOMMENDED_PROCEDURE]->(proc:Procedure)-[:FOLLOW_UP]->(f:FollowUp)
    RETURN d.id AS disease_id, d.name AS disease_name,
           drug.id AS drug_id, drug.name AS drug_name,
           proc.id AS procedure_id, proc.name AS procedure_name,
           f.id AS followup_id, f.name AS followup_name
    """
    return run_query(cypher, {"disease_id": disease_id})


def get_actual_patient_treatments(patient_id: str, disease_id: str):
    """
    Get actual treatments for a patient (for comparison with protocol for the given disease).
    Returns dict with lists actual_drug_ids, actual_drug_names, actual_procedure_ids, actual_procedure_names.
    """
    cypher_drugs = """
    MATCH (p:Patient {id: $patient_id})-[:HAS_DISEASE]->(d:Disease {id: $disease_id})
    OPTIONAL MATCH (p)-[:TREATED_WITH]->(drug:Drug)
    RETURN collect(DISTINCT drug.id) AS drug_ids, collect(DISTINCT drug.name) AS drug_names
    """
    cypher_procs = """
    MATCH (p:Patient {id: $patient_id})-[:HAS_DISEASE]->(d:Disease {id: $disease_id})
    OPTIONAL MATCH (p)-[:HAD_PROCEDURE]->(proc:Procedure)
    RETURN collect(DISTINCT proc.id) AS procedure_ids, collect(DISTINCT proc.name) AS procedure_names
    """
    drugs = run_query(cypher_drugs, {"patient_id": patient_id, "disease_id": disease_id})
    procs = run_query(cypher_procs, {"patient_id": patient_id, "disease_id": disease_id})
    drug_ids = [x for x in (drugs[0].get("drug_ids") or []) if x]
    drug_names = [x for x in (drugs[0].get("drug_names") or []) if x]
    proc_ids = [x for x in (procs[0].get("procedure_ids") or []) if x]
    proc_names = [x for x in (procs[0].get("procedure_names") or []) if x]
    return {"actual_drug_ids": drug_ids, "actual_drug_names": drug_names,
            "actual_procedure_ids": proc_ids, "actual_procedure_names": proc_names}


def create_treated_with(patient_id: str, drug_id: str):
    """Record that a patient was treated with a drug (actual treatment)."""
    cypher = """
    MATCH (p:Patient {id: $patient_id}), (drug:Drug {id: $drug_id})
    MERGE (p)-[:TREATED_WITH]->(drug)
    RETURN p.id AS patient_id, drug.id AS drug_id
    """
    return run_query(cypher, {"patient_id": patient_id, "drug_id": drug_id})


def create_had_procedure(patient_id: str, procedure_id: str):
    """Record that a patient had a procedure (actual treatment)."""
    cypher = """
    MATCH (p:Patient {id: $patient_id}), (proc:Procedure {id: $procedure_id})
    MERGE (p)-[:HAD_PROCEDURE]->(proc)
    RETURN p.id AS patient_id, proc.id AS procedure_id
    """
    return run_query(cypher, {"patient_id": patient_id, "procedure_id": procedure_id})


def get_patients_with_doctor():
    """Get each patient and the doctor they visit (for attributing compliance to doctors)."""
    cypher = """
    MATCH (p:Patient)-[:VISITS]->(doc:Doctor)
    RETURN p.id AS patient_id, p.name AS patient_name, doc.id AS doctor_id, doc.name AS doctor_name
    ORDER BY doc.id, p.id
    """
    return run_query(cypher)


# -------- Rich data: patient journey, labs, procedures, drugs, encounters, doctor cases --------


def get_patient_full_journey(patient_id: str):
    """
    Get full treatment journey for a patient: appointments (with hospital), encounters (with doctor),
    labs (with results), procedures, and drugs (prescriptions). Ordered by date where available.
    """
    cypher = """
    MATCH (p:Patient {id: $patient_id})
    OPTIONAL MATCH (p)-[:HAS_APPOINTMENT]->(a:Appointment)-[:AT_HOSPITAL]->(h:Hospital)
    OPTIONAL MATCH (p)-[:HAS_ENCOUNTER]->(e:Encounter)-[:PERFORMED_BY]->(doc:Doctor)
    OPTIONAL MATCH (e)-[:ORDERED_LAB]->(l:Lab)
    OPTIONAL MATCH (e)-[ip:INCLUDES_PROCEDURE]->(proc:Procedure)
    OPTIONAL MATCH (e)-[pr:PRESCRIBED]->(drug:Drug)
    RETURN p.id AS patient_id, p.name AS patient_name, p.age AS age, p.sex AS sex,
           a.id AS appointment_id, a.date AS appointment_date, a.reason AS appointment_reason, a.status AS appointment_status,
           h.id AS hospital_id, h.name AS hospital_name,
           e.id AS encounter_id, e.date AS encounter_date, e.type AS encounter_type, e.notes AS encounter_notes,
           doc.id AS doctor_id, doc.name AS doctor_name, doc.specialty AS doctor_specialty,
           l.id AS lab_id, l.name AS lab_name, l.result_value AS lab_result_value, l.unit AS lab_unit, l.normal_range AS lab_normal_range, l.status AS lab_status, l.date AS lab_date,
           proc.id AS procedure_id, proc.name AS procedure_name, ip.date AS procedure_date, ip.status AS procedure_status,
           drug.id AS drug_id, drug.name AS drug_name, pr.prescribed_on AS prescribed_on, pr.dose AS dose, pr.frequency AS frequency, pr.duration AS duration, pr.indication AS indication
    ORDER BY a.date, e.date, l.date
    """
    return run_query(cypher, {"patient_id": patient_id})


def get_patient_labs(patient_id: str):
    """Get all lab results for a patient (via encounters that ordered labs)."""
    cypher = """
    MATCH (p:Patient {id: $patient_id})-[:HAS_ENCOUNTER]->(e:Encounter)-[:ORDERED_LAB]->(l:Lab)
    RETURN l.id AS lab_id, l.name AS lab_name, l.result_value AS result_value, l.unit AS unit,
           l.normal_range AS normal_range, l.status AS status, l.date AS date,
           e.id AS encounter_id, e.date AS encounter_date
    ORDER BY l.date
    """
    return run_query(cypher, {"patient_id": patient_id})


def get_patient_procedures(patient_id: str):
    """Get all procedures for a patient (from encounters and HAD_PROCEDURE)."""
    cypher = """
    MATCH (p:Patient {id: $patient_id})-[:HAS_ENCOUNTER]->(e:Encounter)-[ip:INCLUDES_PROCEDURE]->(proc:Procedure)
    RETURN proc.id AS procedure_id, proc.name AS procedure_name,
           ip.date AS procedure_date, ip.status AS procedure_status,
           e.id AS encounter_id, e.date AS encounter_date
    ORDER BY ip.date
    """
    return run_query(cypher, {"patient_id": patient_id})


def get_patient_drugs(patient_id: str):
    """Get all drugs a patient is treated with (from TREATED_WITH and PRESCRIBED details if available)."""
    cypher = """
    MATCH (p:Patient {id: $patient_id})-[:TREATED_WITH]->(drug:Drug)
    OPTIONAL MATCH (p)-[:HAS_ENCOUNTER]->(e:Encounter)-[pr:PRESCRIBED]->(drug)
    RETURN drug.id AS drug_id, drug.name AS drug_name,
           pr.prescribed_on AS prescribed_on, pr.dose AS dose, pr.frequency AS frequency,
           pr.duration AS duration, pr.indication AS indication, pr.guideline_based AS guideline_based
    ORDER BY pr.prescribed_on
    """
    return run_query(cypher, {"patient_id": patient_id})


def get_patient_encounters(patient_id: str):
    """Get all encounters for a patient with performing doctor."""
    cypher = """
    MATCH (p:Patient {id: $patient_id})-[:HAS_ENCOUNTER]->(e:Encounter)-[:PERFORMED_BY]->(doc:Doctor)
    RETURN e.id AS encounter_id, e.date AS encounter_date, e.type AS encounter_type, e.notes AS encounter_notes,
           doc.id AS doctor_id, doc.name AS doctor_name, doc.specialty AS doctor_specialty
    ORDER BY e.date
    """
    return run_query(cypher, {"patient_id": patient_id})


def get_doctor_patient_cases(doctor_id: str):
    """
    Get all patient cases (diseases and encounters) for a doctor.
    Includes patients linked via VISITS or via encounters PERFORMED_BY this doctor.
    """
    cypher = """
    MATCH (doc:Doctor {id: $doctor_id})
    OPTIONAL MATCH (p:Patient)-[:VISITS]->(doc)
    WITH doc, collect(DISTINCT p) AS visit_patients
    UNWIND visit_patients AS p
    WITH doc, p WHERE p IS NOT NULL
    OPTIONAL MATCH (p)-[:HAS_DISEASE]->(d:Disease)
    OPTIONAL MATCH (p)-[:HAS_ENCOUNTER]->(e:Encounter)-[:PERFORMED_BY]->(doc)
    RETURN DISTINCT p.id AS patient_id, p.name AS patient_name, p.age AS patient_age, p.sex AS patient_sex,
           d.id AS disease_id, d.name AS disease_name, d.icd10 AS disease_icd10,
           e.id AS encounter_id, e.date AS encounter_date, e.type AS encounter_type, e.notes AS encounter_notes
    ORDER BY p.id, e.date
    """
    result_via_visits = run_query(cypher, {"doctor_id": doctor_id})
    # Also include patients who had encounters with this doctor but may not have VISITS
    cypher2 = """
    MATCH (doc:Doctor {id: $doctor_id})<-[:PERFORMED_BY]-(e:Encounter)<-[:HAS_ENCOUNTER]-(p:Patient)
    OPTIONAL MATCH (p)-[:HAS_DISEASE]->(d:Disease)
    RETURN DISTINCT p.id AS patient_id, p.name AS patient_name, p.age AS patient_age, p.sex AS patient_sex,
           d.id AS disease_id, d.name AS disease_name, d.icd10 AS disease_icd10,
           e.id AS encounter_id, e.date AS encounter_date, e.type AS encounter_type, e.notes AS encounter_notes
    ORDER BY p.id, e.date
    """
    result_via_encounters = run_query(cypher2, {"doctor_id": doctor_id})
    seen = set()
    out = []
    for r in result_via_visits + result_via_encounters:
        key = (r.get("patient_id"), r.get("encounter_id"))
        if key not in seen:
            seen.add(key)
            out.append(r)
    out.sort(key=lambda x: (x.get("patient_id") or "", x.get("encounter_date") or ""))
    return out


def get_patient_notes(patient_id: str):
    """
    Retrieve all PatientNote nodes linked to a patient via HAS_NOTE.
    Returns list of dicts with id, text, date.
    """
    cypher = """
    MATCH (p:Patient {id: $patient_id})-[:HAS_NOTE]->(n:PatientNote)
    RETURN n.id AS id, n.text AS text, n.date AS date
    ORDER BY n.date
    """
    return run_query(cypher, {"patient_id": patient_id})


# -------- Sepsis: ClinicalState and SepsisGuideline --------


def get_patient_clinical_state(patient_id: str):
    """
    Get current clinical state for a patient (HAS_CLINICAL_STATE -> ClinicalState).
    Returns single dict or None if not found.
    """
    cypher = """
    MATCH (p:Patient {id: $patient_id})-[:HAS_CLINICAL_STATE]->(c:ClinicalState)
    RETURN c.id AS state_id,
           c.hours_from_icu_admit AS hours_from_icu_admit,
           c.sofa_score AS sofa_score,
           c.sofa_delta_6h AS sofa_delta_6h,
           c.map AS map,
           c.gcs AS gcs,
           c.creatinine AS creatinine,
           c.lactate AS lactate,
           c.vasopressors_active AS vasopressors_active,
           c.antibiotics_active AS antibiotics_active,
           c.cultures_ordered AS cultures_ordered,
           c.qsofa AS qsofa,
           c.esofa AS esofa
    """
    rows = run_query(cypher, {"patient_id": patient_id})
    return rows[0] if rows else None


def get_patients_with_clinical_state():
    """All patients that have a ClinicalState node (for sepsis dashboard)."""
    cypher = """
    MATCH (p:Patient)-[:HAS_CLINICAL_STATE]->(c:ClinicalState)
    RETURN p.id AS patient_id, p.name AS patient_name, p.age AS patient_age, p.sex AS patient_sex,
           c.sofa_score AS sofa_score, c.lactate AS lactate, c.map AS map,
           c.antibiotics_active AS antibiotics_active, c.cultures_ordered AS cultures_ordered
    ORDER BY c.sofa_score DESC, p.id
    """
    return run_query(cypher)


def get_sepsis_guidelines():
    """
    Get sepsis guideline with recommended actions, labs, drugs, procedures, follow-up.
    Returns list of dicts with guideline and related node ids/names and thresholds.
    """
    cypher = """
    MATCH (g:SepsisGuideline)
    OPTIONAL MATCH (g)-[:RECOMMENDS_ACTION]->(a:RecommendedAction)
    OPTIONAL MATCH (g)-[:REQUIRES_LAB]->(lab:LabCheck)
    OPTIONAL MATCH (g)-[:RECOMMENDED_DRUG]->(d:Drug)
    OPTIONAL MATCH (g)-[:RECOMMENDED_PROCEDURE]->(pr:Procedure)
    OPTIONAL MATCH (g)-[:FOLLOW_UP]->(f:FollowUp)
    RETURN g.id AS guideline_id, g.name AS guideline_name,
           g.sofa_threshold_high AS sofa_threshold, g.lactate_threshold_mmol AS lactate_threshold,
           g.map_threshold_mmhg AS map_threshold,
           collect(DISTINCT a.id) AS action_ids, collect(DISTINCT a.name) AS action_names,
           collect(DISTINCT lab.id) AS lab_ids, collect(DISTINCT lab.name) AS lab_names,
           collect(DISTINCT d.id) AS drug_ids, collect(DISTINCT d.name) AS drug_names,
           collect(DISTINCT pr.id) AS procedure_ids, collect(DISTINCT pr.name) AS procedure_names,
           collect(DISTINCT f.id) AS followup_ids, collect(DISTINCT f.name) AS followup_names
    """
    return run_query(cypher)
