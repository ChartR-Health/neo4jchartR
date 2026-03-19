"""
AI-guided compliance checker: compares actual patient treatments with protocol guidelines.
Flags violations (wrong drug, missing procedure, etc.) and calculates compliance scores per doctor.
Uses existing neo4j_ops for all Neo4j reads (no hard-coded data).
"""
from neo4j_ops import (
    get_patients_with_diseases,
    get_protocol_for_disease,
    get_actual_patient_treatments,
    get_patients_with_doctor,
)


def check_patient_compliance(patient_id: str, patient_name: str, disease_id: str, disease_name: str):
    """
    Compare one patient's treatments for one disease against the protocol.
    Returns dict with: compliant (bool), violations (list of strings), recommended_*, actual_*.
    """
    protocol = get_protocol_for_disease(disease_id)
    if not protocol:
        return {
            "patient_id": patient_id,
            "patient_name": patient_name,
            "disease_id": disease_id,
            "disease_name": disease_name,
            "compliant": None,
            "violations": ["No protocol defined for this disease"],
            "recommended_drug_id": None,
            "recommended_drug_name": None,
            "recommended_procedure_id": None,
            "recommended_procedure_name": None,
            "actual_drug_ids": [],
            "actual_drug_names": [],
            "actual_procedure_ids": [],
            "actual_procedure_names": [],
        }
    p = protocol[0]
    rec_drug_id = p.get("drug_id")
    rec_drug_name = p.get("drug_name")
    rec_proc_id = p.get("procedure_id")
    rec_proc_name = p.get("procedure_name")
    actual = get_actual_patient_treatments(patient_id, disease_id)
    actual_drug_ids = actual.get("actual_drug_ids") or []
    actual_proc_ids = actual.get("actual_procedure_ids") or []
    violations = []
    if rec_drug_id and (not actual_drug_ids or rec_drug_id not in actual_drug_ids):
        violations.append(f"Wrong or missing drug: recommended '{rec_drug_name}' ({rec_drug_id}), actual {actual.get('actual_drug_names') or 'none'}")
    if rec_proc_id and (not actual_proc_ids or rec_proc_id not in actual_proc_ids):
        violations.append(f"Wrong or missing procedure: recommended '{rec_proc_name}' ({rec_proc_id}), actual {actual.get('actual_procedure_names') or 'none'}")
    compliant = len(violations) == 0
    return {
        "patient_id": patient_id,
        "patient_name": patient_name,
        "disease_id": disease_id,
        "disease_name": disease_name,
        "compliant": compliant,
        "violations": violations,
        "recommended_drug_id": rec_drug_id,
        "recommended_drug_name": rec_drug_name,
        "recommended_procedure_id": rec_proc_id,
        "recommended_procedure_name": rec_proc_name,
        "actual_drug_ids": actual_drug_ids,
        "actual_drug_names": actual.get("actual_drug_names") or [],
        "actual_procedure_ids": actual_proc_ids,
        "actual_procedure_names": actual.get("actual_procedure_names") or [],
    }


def run_compliance_check():
    """
    Run full compliance check: all patients and their diseases vs protocol.
    Returns dict with: patients_with_violations, doctor_compliance_scores, violated_relationships.
    """
    # All patient-disease pairs
    rows = get_patients_with_diseases()
    patient_diseases = []
    for r in rows:
        pid, pname = r.get("patient_id"), r.get("patient_name")
        did, dname = r.get("disease_id"), r.get("disease_name")
        if pid and did:
            patient_diseases.append((pid, pname or pid, did, dname or did))
    # Deduplicate (OPTIONAL MATCH can give multiple rows per patient)
    seen = set()
    unique = []
    for t in patient_diseases:
        if (t[0], t[2]) not in seen:
            seen.add((t[0], t[2]))
            unique.append(t)

    results = []
    for patient_id, patient_name, disease_id, disease_name in unique:
        results.append(check_patient_compliance(patient_id, patient_name, disease_id, disease_name))

    patients_with_violations = [r for r in results if r["compliant"] is False or (r["compliant"] is not True and r["violations"])]
    violated_relationships = []
    for r in patients_with_violations:
        for v in r["violations"]:
            violated_relationships.append({
                "patient_id": r["patient_id"],
                "patient_name": r["patient_name"],
                "disease_id": r["disease_id"],
                "disease_name": r["disease_name"],
                "violation": v,
                "recommended_drug": r.get("recommended_drug_name"),
                "recommended_procedure": r.get("recommended_procedure_name"),
                "actual_drugs": r.get("actual_drug_names"),
                "actual_procedures": r.get("actual_procedure_names"),
            })

    # Doctor compliance scores: which doctor(s) each patient visits
    patient_to_doctors = {}
    for r in get_patients_with_doctor():
        pid = r.get("patient_id")
        doc_id = r.get("doctor_id")
        doc_name = r.get("doctor_name")
        if pid not in patient_to_doctors:
            patient_to_doctors[pid] = []
        patient_to_doctors[pid].append({"doctor_id": doc_id, "doctor_name": doc_name})

    doctor_scores = {}
    for r in results:
        pid = r["patient_id"]
        compliant = r["compliant"] is True
        for doc in patient_to_doctors.get(pid, []):
            doc_id = doc["doctor_id"]
            if doc_id not in doctor_scores:
                doctor_scores[doc_id] = {"doctor_name": doc["doctor_name"], "compliant_count": 0, "total_count": 0}
            doctor_scores[doc_id]["total_count"] += 1
            if compliant:
                doctor_scores[doc_id]["compliant_count"] += 1

    doctor_compliance_scores = []
    for doc_id, data in doctor_scores.items():
        total = data["total_count"]
        comp = data["compliant_count"]
        score = (comp / total * 100) if total else 100.0
        doctor_compliance_scores.append({
            "doctor_id": doc_id,
            "doctor_name": data["doctor_name"],
            "compliance_score": round(score, 1),
            "compliant_cases": comp,
            "total_cases": total,
        })
    doctor_compliance_scores.sort(key=lambda x: -x["compliance_score"])

    return {
        "patients_with_violations": patients_with_violations,
        "doctor_compliance_scores": doctor_compliance_scores,
        "violated_relationships": violated_relationships,
        "all_checks": results,
    }
