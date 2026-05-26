"""
In-memory graph sample aligned with seed_data.py ids (P1, P2, D1, D2, DRUG*, etc.).
Used when USE_GRAPH_DEMO=1 so the dashboard and API run without Neo4j.
"""
from __future__ import annotations

from typing import Any

# Patients exposed in GET /patients-sync and patient-scoped graph (subset of full seed).
DEMO_PATIENT_IDS = frozenset({"P1", "P2", "P3"})


def _nid(label: str, node_id: str) -> str:
    return f"demo:{label}:{node_id}"


def _row(
    sid: str,
    tid: str,
    rt: str,
    sl: str,
    tl: str,
    sp: dict[str, Any],
    tp: dict[str, Any],
) -> dict[str, Any]:
    return {
        "src_id": sid,
        "tgt_id": tid,
        "rel_type": rt,
        "src_label": sl,
        "tgt_label": tl,
        "src_props": sp,
        "tgt_props": tp,
    }


def demo_patient_exists(patient_id: str) -> bool:
    return (patient_id or "").strip() in DEMO_PATIENT_IDS


def demo_get_patients_with_diseases() -> list[dict[str, Any]]:
    return [
        {
            "patient_id": "P1",
            "patient_name": "Alice Smith",
            "patient_age": 34,
            "patient_sex": "F",
            "disease_id": "D1",
            "disease_name": "Hypertension",
            "disease_icd10": "I10",
            "diagnosed_on": "2023-06-01",
        },
        {
            "patient_id": "P2",
            "patient_name": "Bob Jones",
            "patient_age": 45,
            "patient_sex": "M",
            "disease_id": "D2",
            "disease_name": "Type 2 Diabetes",
            "disease_icd10": "E11",
            "diagnosed_on": "2023-08-10",
        },
        {
            "patient_id": "P3",
            "patient_name": "Carol White",
            "patient_age": 28,
            "patient_sex": "F",
            "disease_id": "D3",
            "disease_name": "Asthma",
            "disease_icd10": "J45",
            "diagnosed_on": "2023-09-15",
        },
    ]


def demo_get_patients_with_doctor() -> list[dict[str, Any]]:
    return [
        {"patient_id": "P1", "patient_name": "Alice Smith", "doctor_id": "DOC1", "doctor_name": "Dr. Patel"},
        {"patient_id": "P2", "patient_name": "Bob Jones", "doctor_id": "DOC2", "doctor_name": "Dr. Nguyen"},
        {"patient_id": "P3", "patient_name": "Carol White", "doctor_id": "DOC3", "doctor_name": "Dr. Kim"},
    ]


def demo_get_protocol_for_disease(disease_id: str) -> list[dict[str, Any]]:
    protocols: dict[str, dict[str, Any]] = {
        "D1": {
            "disease_id": "D1",
            "disease_name": "Hypertension",
            "drug_id": "DRUG1",
            "drug_name": "Lisinopril",
            "procedure_id": "PROC1",
            "procedure_name": "BP monitoring",
            "followup_id": "FU1",
            "followup_name": "3-month BP check",
        },
        "D2": {
            "disease_id": "D2",
            "disease_name": "Type 2 Diabetes",
            "drug_id": "DRUG2",
            "drug_name": "Metformin",
            "procedure_id": "PROC2",
            "procedure_name": "HbA1c test",
            "followup_id": "FU2",
            "followup_name": "Quarterly HbA1c",
        },
        "D3": {
            "disease_id": "D3",
            "disease_name": "Asthma",
            "drug_id": "DRUG3",
            "drug_name": "Inhaled corticosteroid",
            "procedure_id": "PROC3",
            "procedure_name": "Spirometry",
            "followup_id": "FU3",
            "followup_name": "Annual lung function",
        },
    }
    p = protocols.get(disease_id)
    return [dict(p)] if p else []


def demo_get_actual_patient_treatments(patient_id: str, disease_id: str) -> dict[str, Any]:
    # P2 + D2: wrong drug / wrong procedure vs protocol (DRUG2 / PROC2).
    if patient_id == "P2" and disease_id == "D2":
        return {
            "actual_drug_ids": ["DRUG14"],
            "actual_drug_names": ["Ibuprofen"],
            "actual_procedure_ids": ["PROC4"],
            "actual_procedure_names": ["Joint injection"],
        }
    if patient_id == "P1" and disease_id == "D1":
        return {
            "actual_drug_ids": ["DRUG1"],
            "actual_drug_names": ["Lisinopril"],
            "actual_procedure_ids": ["PROC1"],
            "actual_procedure_names": ["BP monitoring"],
        }
    if patient_id == "P3" and disease_id == "D3":
        return {
            "actual_drug_ids": ["DRUG3"],
            "actual_drug_names": ["Inhaled corticosteroid"],
            "actual_procedure_ids": ["PROC3"],
            "actual_procedure_names": ["Spirometry"],
        }
    return {
        "actual_drug_ids": [],
        "actual_drug_names": [],
        "actual_procedure_ids": [],
        "actual_procedure_names": [],
    }


def demo_get_all_patients_graph_data() -> list[dict[str, Any]]:
    return [
        {
            "patient_id": "P1",
            "patient_name": "Alice Smith",
            "age": 34,
            "sex": "F",
            "source": "demo",
            "diseases": [{"id": "D1", "name": "Hypertension"}],
            "symptoms": [{"id": "SYM_fatigue", "name": "Fatigue"}],
            "clinical_state": {
                "sofa_score": 2,
                "map": 78,
                "gcs": 15,
                "creatinine": 0.9,
                "lactate": 1.2,
            },
        },
        {
            "patient_id": "P2",
            "patient_name": "Bob Jones",
            "age": 45,
            "sex": "M",
            "source": "demo",
            "diseases": [{"id": "D2", "name": "Type 2 Diabetes"}],
            "symptoms": [{"id": "SYM_thirst", "name": "Polydipsia"}],
            "clinical_state": {
                "sofa_score": 4,
                "map": 68,
                "gcs": 14,
                "creatinine": 1.2,
                "lactate": 2.4,
            },
        },
        {
            "patient_id": "P3",
            "patient_name": "Carol White",
            "age": 28,
            "sex": "F",
            "source": "demo",
            "diseases": [{"id": "D3", "name": "Asthma"}],
            "symptoms": [{"id": "SYM_wheeze", "name": "Wheezing"}],
            "clinical_state": None,
        },
    ]


def demo_get_patients_for_comparison(patient_ids: list[str]) -> list[dict[str, Any]]:
    by_id = {r["patient_id"]: r for r in demo_get_all_patients_graph_data()}
    out: list[dict[str, Any]] = []
    for pid in patient_ids:
        base = by_id.get(pid)
        if not base:
            continue
        viol: list[str] = []
        if pid == "P2":
            viol = ["Wrong or missing drug for Type 2 Diabetes", "Wrong or missing procedure for Type 2 Diabetes"]
        out.append(
            {
                "patient_id": base["patient_id"],
                "patient_name": base["patient_name"],
                "age": base["age"],
                "sex": base["sex"],
                "diseases": list(base["diseases"]),
                "symptoms": list(base["symptoms"]),
                "violations": viol,
                "clinical_state": base.get("clinical_state"),
            }
        )
    return out


def demo_get_patients_with_clinical_state() -> list[dict[str, Any]]:
    rows = []
    for r in demo_get_all_patients_graph_data():
        cs = r.get("clinical_state")
        if not cs:
            continue
        pid = r["patient_id"]
        rows.append(
            {
                "patient_id": pid,
                "patient_name": r["patient_name"],
                "patient_age": r["age"],
                "patient_sex": r["sex"],
                "sofa_score": cs.get("sofa_score"),
                "lactate": cs.get("lactate"),
                "map": cs.get("map"),
                "antibiotics_active": pid == "P2",
                "cultures_ordered": True,
            }
        )
    return rows


def demo_get_patient_clinical_state(patient_id: str) -> dict[str, Any] | None:
    for r in demo_get_all_patients_graph_data():
        if r["patient_id"] == patient_id and r.get("clinical_state"):
            cs = dict(r["clinical_state"])
            cs["state_id"] = f"CS_{patient_id}"
            cs["antibiotics_active"] = patient_id == "P2"
            cs["vasopressors_active"] = False
            cs["cultures_ordered"] = True
            return cs
    return None


def demo_get_patient_notes(patient_id: str) -> list[dict[str, Any]]:
    if patient_id == "P1":
        return [{"id": "N_P1_1", "text": "Patient reports good adherence to antihypertensive regimen.", "date": "2024-05-01"}]
    return []


def demo_get_patient_full_journey(patient_id: str) -> list[dict[str, Any]]:
    if patient_id not in DEMO_PATIENT_IDS:
        return []
    return [
        {
            "patient_id": patient_id,
            "patient_name": next(x["patient_name"] for x in demo_get_all_patients_graph_data() if x["patient_id"] == patient_id),
            "age": next(x["age"] for x in demo_get_all_patients_graph_data() if x["patient_id"] == patient_id),
            "sex": next(x["sex"] for x in demo_get_all_patients_graph_data() if x["patient_id"] == patient_id),
            "encounter_id": f"E_{patient_id}_1",
            "encounter_date": "2024-06-01",
            "encounter_type": "Inpatient",
            "encounter_notes": "Assessment and treatment plan documented.",
            "doctor_name": "Dr. Patel",
            "lab_id": f"L_{patient_id}_1",
            "lab_name": "Basic metabolic panel",
            "lab_result_value": "1.0",
            "lab_unit": "mg/dL",
            "lab_date": "2024-06-01",
            "drug_id": "DRUG2" if patient_id == "P2" else "DRUG1",
            "drug_name": "Metformin" if patient_id == "P2" else "Lisinopril",
            "prescribed_on": "2024-06-01",
            "dose": "500mg",
            "procedure_id": "PROC2" if patient_id == "P2" else "PROC1",
            "procedure_name": "HbA1c test" if patient_id == "P2" else "BP check",
            "procedure_date": "2024-06-02",
        }
    ]


def demo_get_patient_timeline_data(patient_id: str) -> dict[str, Any]:
    pid = (patient_id or "").strip()
    rows_pd = demo_get_patients_with_diseases()
    pname, age, sex = pid, None, None
    diseases: list[dict[str, Any]] = []
    for r in rows_pd:
        if r.get("patient_id") == pid:
            pname = r.get("patient_name") or pid
            age = r.get("patient_age")
            sex = r.get("patient_sex")
            if r.get("disease_id"):
                diseases.append({"id": r["disease_id"], "name": r.get("disease_name")})
    journey = demo_get_patient_full_journey(pid)
    encounters, labs, drugs, procedures = [], [], [], []
    seen_e, seen_l, seen_d, seen_p = set(), set(), set(), set()
    for r in journey:
        eid = r.get("encounter_id")
        if eid and eid not in seen_e:
            seen_e.add(eid)
            encounters.append(
                {
                    "id": eid,
                    "date": r.get("encounter_date"),
                    "type": r.get("encounter_type"),
                    "notes": r.get("encounter_notes") or "",
                    "doctor": r.get("doctor_name"),
                }
            )
        lid = r.get("lab_id")
        if lid and lid not in seen_l:
            seen_l.add(lid)
            labs.append(
                {
                    "id": lid,
                    "name": r.get("lab_name"),
                    "value": r.get("lab_result_value"),
                    "unit": r.get("lab_unit"),
                    "date": r.get("lab_date"),
                }
            )
        did = r.get("drug_id")
        if did and did not in seen_d:
            seen_d.add(did)
            drugs.append({"id": did, "name": r.get("drug_name"), "date": r.get("prescribed_on"), "dose": r.get("dose")})
        prid = r.get("procedure_id")
        if prid and prid not in seen_p:
            seen_p.add(prid)
            procedures.append({"id": prid, "name": r.get("procedure_name"), "date": r.get("procedure_date")})
    return {
        "patient_id": pid,
        "patient_name": pname,
        "age": age,
        "sex": sex,
        "diseases": diseases,
        "clinical_state": demo_get_patient_clinical_state(pid) or {},
        "encounters": encounters,
        "labs": labs,
        "drugs": drugs,
        "procedures": procedures,
        "notes": demo_get_patient_notes(pid),
    }


def _patient_rows(pid: str) -> list[dict[str, Any]]:
    p = _nid("Patient", pid)
    meta = next(x for x in demo_get_all_patients_graph_data() if x["patient_id"] == pid)
    prow = {"id": pid, "name": meta["patient_name"], "age": meta["age"], "sex": meta["sex"]}
    rows: list[dict[str, Any]] = []

    for d in meta["diseases"]:
        did = d["id"]
        dn = _nid("Disease", did)
        rows.append(_row(p, dn, "HAS_DISEASE", "Patient", "Disease", prow, {"id": did, "name": d["name"]}))
        proto = demo_get_protocol_for_disease(did)
        if proto:
            pr = proto[0]
            drugn = _nid("Drug", pr["drug_id"])
            procn = _nid("Procedure", pr["procedure_id"])
            fun = _nid("FollowUp", pr["followup_id"])
            rows.append(_row(dn, drugn, "RECOMMENDED_DRUG", "Disease", "Drug", {"id": did, "name": d["name"]}, {"id": pr["drug_id"], "name": pr["drug_name"]}))
            rows.append(
                _row(
                    drugn,
                    procn,
                    "RECOMMENDED_PROCEDURE",
                    "Drug",
                    "Procedure",
                    {"id": pr["drug_id"], "name": pr["drug_name"]},
                    {"id": pr["procedure_id"], "name": pr["procedure_name"]},
                )
            )
            rows.append(
                _row(
                    procn,
                    fun,
                    "FOLLOW_UP",
                    "Procedure",
                    "FollowUp",
                    {"id": pr["procedure_id"], "name": pr["procedure_name"]},
                    {"id": pr["followup_id"], "name": pr["followup_name"]},
                )
            )

    for s in meta.get("symptoms") or []:
        sid = s["id"]
        sn = _nid("Symptom", sid)
        rows.append(_row(p, sn, "HAS_SYMPTOM", "Patient", "Symptom", prow, {"id": sid, "name": s["name"]}))

    doc = next((x for x in demo_get_patients_with_doctor() if x["patient_id"] == pid), None)
    if doc:
        dnid = _nid("Doctor", doc["doctor_id"])
        rows.append(
            _row(
                p,
                dnid,
                "VISITS",
                "Patient",
                "Doctor",
                prow,
                {"id": doc["doctor_id"], "name": doc["doctor_name"], "specialty": "General"},
            )
        )

    cs = meta.get("clinical_state")
    if cs:
        cn = _nid("ClinicalState", f"CS_{pid}")
        cprops = {
            "id": f"CS_{pid}",
            "sofa_score": cs.get("sofa_score"),
            "map": cs.get("map"),
            "gcs": cs.get("gcs"),
            "creatinine": cs.get("creatinine"),
            "lactate": cs.get("lactate"),
            "antibiotics_active": pid == "P2",
            "vasopressors_active": False,
            "cultures_ordered": True,
        }
        rows.append(_row(p, cn, "HAS_CLINICAL_STATE", "Patient", "ClinicalState", prow, cprops))

    act = demo_get_actual_patient_treatments(pid, meta["diseases"][0]["id"])
    for drug_id, drug_name in zip(act["actual_drug_ids"], act["actual_drug_names"]):
        gn = _nid("Drug", drug_id)
        rows.append(_row(p, gn, "TREATED_WITH", "Patient", "Drug", prow, {"id": drug_id, "name": drug_name}))
    for proc_id, proc_name in zip(act["actual_procedure_ids"], act["actual_procedure_names"]):
        pn = _nid("Procedure", proc_id)
        rows.append(_row(p, pn, "HAD_PROCEDURE", "Patient", "Procedure", prow, {"id": proc_id, "name": proc_name}))

    return rows


def demo_collect_patient_scoped_graph_rows(patient_id: str) -> list[dict[str, Any]]:
    pid = (patient_id or "").strip()
    if pid not in DEMO_PATIENT_IDS:
        return []
    return _patient_rows(pid)
