"""
Create rich healthcare graph data for protocol compliance and AI analysis.
Run once to populate the database with patients, doctors, diseases, appointments,
encounters, labs, procedures, drugs, and actual treatment journeys.

Structure:
  Patient -> HAS_APPOINTMENT -> Appointment -> AT_HOSPITAL -> Hospital
  Patient -> HAS_ENCOUNTER -> Encounter -> PERFORMED_BY -> Doctor
  Encounter -> ORDERED_LAB -> Lab (result_value, unit, normal_range, date)
  Encounter -> PRESCRIBED -> Drug (dose, frequency, duration); Patient -> TREATED_WITH -> Drug
  Encounter -> INCLUDES_PROCEDURE -> Procedure (date, status); Patient -> HAD_PROCEDURE -> Procedure
  Patient -> HAS_DISEASE -> Disease; Disease -> RECOMMENDED_DRUG -> Drug -> RECOMMENDED_PROCEDURE -> Procedure -> FOLLOW_UP -> FollowUp
  Patient -> FOLLOWED_UP_WITH -> FollowUp (date, status)
"""
from neo4j_connect import run_query


def _run(cypher: str, params: dict | None = None):
    """Run a single Cypher statement."""
    run_query(cypher, params or {})


def seed(light: bool = False):
    """
    Create full healthcare graph. If light=True, use fewer appointments/encounters so the graph
    is less cluttered and sepsis patients stand out when sepsis is added on top.
    """
    # -------------------------------------------------------------------------
    # 1. Clear existing data
    # -------------------------------------------------------------------------
    _run("MATCH (n) DETACH DELETE n")

    # -------------------------------------------------------------------------
    # 2. Patients (20): diverse ages and sexes for realistic case mix
    # -------------------------------------------------------------------------
    _run("""
    CREATE
      (p1:Patient {id: 'P1', name: 'Alice Smith', age: 34, sex: 'F'}),
      (p2:Patient {id: 'P2', name: 'Bob Jones', age: 45, sex: 'M'}),
      (p3:Patient {id: 'P3', name: 'Carol White', age: 28, sex: 'F'}),
      (p4:Patient {id: 'P4', name: 'David Lee', age: 52, sex: 'M'}),
      (p5:Patient {id: 'P5', name: 'Eva Martinez', age: 41, sex: 'F'}),
      (p6:Patient {id: 'P6', name: 'Frank Chen', age: 39, sex: 'M'}),
      (p7:Patient {id: 'P7', name: 'Grace Kim', age: 33, sex: 'F'}),
      (p8:Patient {id: 'P8', name: 'Henry Wilson', age: 61, sex: 'M'}),
      (p9:Patient {id: 'P9', name: 'Iris Davis', age: 29, sex: 'F'}),
      (p10:Patient {id: 'P10', name: 'Jack Brown', age: 47, sex: 'M'}),
      (p11:Patient {id: 'P11', name: 'Kate Moore', age: 55, sex: 'F'}),
      (p12:Patient {id: 'P12', name: 'Leo Garcia', age: 42, sex: 'M'}),
      (p13:Patient {id: 'P13', name: 'Mia Johnson', age: 38, sex: 'F'}),
      (p14:Patient {id: 'P14', name: 'Noah Williams', age: 67, sex: 'M'}),
      (p15:Patient {id: 'P15', name: 'Olivia Clark', age: 31, sex: 'F'}),
      (p16:Patient {id: 'P16', name: 'Paul Lewis', age: 44, sex: 'M'}),
      (p17:Patient {id: 'P17', name: 'Quinn Taylor', age: 50, sex: 'F'}),
      (p18:Patient {id: 'P18', name: 'Ryan Adams', age: 36, sex: 'M'}),
      (p19:Patient {id: 'P19', name: 'Sofia Hernandez', age: 59, sex: 'F'}),
      (p20:Patient {id: 'P20', name: 'Tom Anderson', age: 48, sex: 'M'})
    RETURN 1
    """)

    # -------------------------------------------------------------------------
    # 3. Doctors (10): various specialties for protocol attribution
    # -------------------------------------------------------------------------
    _run("""
    CREATE
      (doc1:Doctor {id: 'DOC1', name: 'Dr. Evans', specialty: 'Cardiology'}),
      (doc2:Doctor {id: 'DOC2', name: 'Dr. Brown', specialty: 'General'}),
      (doc3:Doctor {id: 'DOC3', name: 'Dr. Patel', specialty: 'Endocrinology'}),
      (doc4:Doctor {id: 'DOC4', name: 'Dr. Nguyen', specialty: 'Pulmonology'}),
      (doc5:Doctor {id: 'DOC5', name: 'Dr. Taylor', specialty: 'Orthopedics'}),
      (doc6:Doctor {id: 'DOC6', name: 'Dr. Wright', specialty: 'Infectious Disease'}),
      (doc7:Doctor {id: 'DOC7', name: 'Dr. King', specialty: 'Hematology'}),
      (doc8:Doctor {id: 'DOC8', name: 'Dr. Scott', specialty: 'Psychiatry'}),
      (doc9:Doctor {id: 'DOC9', name: 'Dr. Green', specialty: 'Internal Medicine'}),
      (doc10:Doctor {id: 'DOC10', name: 'Dr. Hall', specialty: 'Radiology'})
    RETURN 1
    """)

    # -------------------------------------------------------------------------
    # 4. Diseases (8): hypertension, diabetes, asthma, OA, anxiety, pneumonia, anemia, COPD
    # -------------------------------------------------------------------------
    _run("""
    CREATE
      (dis1:Disease {id: 'D1', name: 'Hypertension', icd10: 'I10'}),
      (dis2:Disease {id: 'D2', name: 'Type 2 Diabetes', icd10: 'E11'}),
      (dis3:Disease {id: 'D3', name: 'Asthma', icd10: 'J45'}),
      (dis4:Disease {id: 'D4', name: 'Osteoarthritis', icd10: 'M17'}),
      (dis5:Disease {id: 'D5', name: 'Anxiety', icd10: 'F41'}),
      (dis6:Disease {id: 'D6', name: 'Pneumonia', icd10: 'J18'}),
      (dis7:Disease {id: 'D7', name: 'Anemia', icd10: 'D64'}),
      (dis8:Disease {id: 'D8', name: 'COPD', icd10: 'J44'})
    RETURN 1
    """)

    # -------------------------------------------------------------------------
    # 5. Hospitals (5)
    # -------------------------------------------------------------------------
    _run("""
    CREATE
      (h1:Hospital {id: 'H1', name: 'City General'}),
      (h2:Hospital {id: 'H2', name: 'Central Clinic'}),
      (h3:Hospital {id: 'H3', name: 'North Medical Center'}),
      (h4:Hospital {id: 'H4', name: 'South Health Campus'}),
      (h5:Hospital {id: 'H5', name: 'East Valley Hospital'})
    RETURN 1
    """)

    # -------------------------------------------------------------------------
    # 6. Appointments: multiple per patient, with date, reason, status
    # -------------------------------------------------------------------------
    appointments = [
        ("A1", "2024-01-10", "Routine BP check", "completed", "P1", "H1"),
        ("A2", "2024-02-15", "Follow-up hypertension", "completed", "P1", "H1"),
        ("A3", "2024-04-01", "Quarterly diabetes review", "completed", "P2", "H2"),
        ("A4", "2024-05-12", "HbA1c and medication review", "completed", "P2", "H2"),
        ("A5", "2024-03-05", "Asthma control", "completed", "P3", "H1"),
        ("A6", "2024-06-10", "Spirometry follow-up", "completed", "P3", "H4"),
        ("A7", "2024-01-20", "Hypertension + diabetes", "completed", "P4", "H3"),
        ("A8", "2024-04-18", "BP and glucose", "completed", "P4", "H3"),
        ("A9", "2024-02-01", "Diabetes new diagnosis", "completed", "P5", "H2"),
        ("A10", "2024-05-20", "Medication non-compliance", "completed", "P5", "H2"),
        ("A11", "2024-03-12", "Asthma exacerbation", "completed", "P6", "H4"),
        ("A12", "2024-07-01", "Annual lung function", "completed", "P6", "H4"),
        ("A13", "2024-01-08", "Anxiety assessment", "completed", "P7", "H2"),
        ("A14", "2024-04-22", "Therapy review", "completed", "P7", "H2"),
        ("A15", "2024-02-10", "Hypertension, knee pain", "completed", "P8", "H1"),
        ("A16", "2024-06-05", "BP and joint review", "completed", "P8", "H1"),
        ("A17", "2024-03-18", "Anxiety - no meds yet", "completed", "P9", "H2"),
        ("A18", "2024-01-25", "Diabetes annual", "completed", "P10", "H2"),
        ("A19", "2024-05-08", "HbA1c in range", "completed", "P10", "H2"),
        ("A20", "2024-02-28", "Hypertension follow-up", "completed", "P11", "H1"),
        ("A21", "2024-04-14", "Cough and fever", "completed", "P12", "H3"),
        ("A22", "2024-03-01", "Fatigue, possible anemia", "completed", "P13", "H4"),
        ("A23", "2024-06-20", "CBC and iron studies", "completed", "P13", "H4"),
        ("A24", "2024-01-15", "COPD exacerbation", "completed", "P14", "H4"),
        ("A25", "2024-05-30", "Breathing difficulty", "completed", "P15", "H1"),
        ("A26", "2024-04-08", "Pneumonia follow-up", "completed", "P12", "H3"),
        ("A27", "2024-07-10", "Anemia recheck", "completed", "P13", "H4"),
        ("A28", "2024-02-20", "Diabetes screening", "completed", "P16", "H2"),
        ("A29", "2024-06-15", "Asthma control", "completed", "P17", "H4"),
        ("A30", "2024-03-25", "Hypertension", "completed", "P18", "H1"),
        ("A31", "2024-05-05", "Osteoarthritis knee", "completed", "P19", "H5"),
        ("A32", "2024-04-28", "Anxiety and insomnia", "completed", "P20", "H2"),
    ]
    if light:
        appointments = appointments[:16]  # fewer so sepsis patients stand out
    for aid, date, reason, status, pid, hid in appointments:
        _run("""
        MATCH (p:Patient {id: $pid}), (h:Hospital {id: $hid})
        CREATE (a:Appointment {id: $aid, date: $date, reason: $reason, status: $status})
        CREATE (p)-[:HAS_APPOINTMENT]->(a)
        CREATE (a)-[:AT_HOSPITAL]->(h)
        RETURN 1
        """, {"aid": aid, "date": date, "reason": reason, "status": status, "pid": pid, "hid": hid})

    # -------------------------------------------------------------------------
    # 7. Encounters: clinical visits linked to doctor and (optionally) appointment
    # -------------------------------------------------------------------------
    encounters = [
        ("E1", "2024-01-10", "outpatient", "BP elevated, started Lisinopril", "P1", "DOC1"),
        ("E2", "2024-02-15", "outpatient", "BP controlled on Lisinopril", "P1", "DOC1"),
        ("E3", "2024-04-01", "outpatient", "Diabetes review, Metformin continued", "P2", "DOC3"),
        ("E4", "2024-05-12", "outpatient", "HbA1c 7.2, diet advice", "P2", "DOC3"),
        ("E5", "2024-03-05", "outpatient", "Asthma stable on ICS", "P3", "DOC4"),
        ("E6", "2024-06-10", "outpatient", "Spirometry normal", "P3", "DOC4"),
        ("E7", "2024-01-20", "outpatient", "HTN and DM, dual treatment", "P4", "DOC1"),
        ("E8", "2024-04-18", "outpatient", "BP and glucose check", "P4", "DOC3"),
        ("E9", "2024-02-01", "outpatient", "New T2DM, started wrong drug", "P5", "DOC2"),
        ("E10", "2024-05-20", "outpatient", "Switched to Metformin", "P5", "DOC3"),
        ("E11", "2024-03-12", "outpatient", "Asthma, ICS prescribed", "P6", "DOC4"),
        ("E12", "2024-07-01", "outpatient", "Spirometry done", "P6", "DOC4"),
        ("E13", "2024-01-08", "outpatient", "Anxiety, SSRI started", "P7", "DOC2"),
        ("E14", "2024-04-22", "outpatient", "Counseling and med review", "P7", "DOC2"),
        ("E15", "2024-02-10", "outpatient", "HTN and OA, Lisinopril only", "P8", "DOC1"),
        ("E16", "2024-06-05", "outpatient", "BP check, no joint imaging", "P8", "DOC5"),
        ("E17", "2024-03-18", "outpatient", "Anxiety, counseling only", "P9", "DOC2"),
        ("E18", "2024-01-25", "outpatient", "Diabetes, Metformin", "P10", "DOC3"),
        ("E19", "2024-05-08", "outpatient", "HbA1c on target", "P10", "DOC3"),
        ("E20", "2024-02-28", "outpatient", "Hypertension stable", "P11", "DOC1"),
        ("E21", "2024-04-14", "outpatient", "Pneumonia, antibiotics", "P12", "DOC6"),
        ("E22", "2024-03-01", "outpatient", "Fatigue, ordered CBC", "P13", "DOC7"),
        ("E23", "2024-06-20", "outpatient", "Anemia, iron started", "P13", "DOC7"),
        ("E24", "2024-01-15", "outpatient", "COPD, inhaler", "P14", "DOC4"),
        ("E25", "2024-05-30", "outpatient", "Asthma exacerbation", "P15", "DOC4"),
        ("E26", "2024-04-08", "outpatient", "Pneumonia resolved", "P12", "DOC6"),
        ("E27", "2024-07-10", "outpatient", "Ferritin improved", "P13", "DOC7"),
        ("E28", "2024-02-20", "outpatient", "Prediabetes, lifestyle", "P16", "DOC3"),
        ("E29", "2024-06-15", "outpatient", "Asthma control", "P17", "DOC4"),
        ("E30", "2024-03-25", "outpatient", "BP follow-up", "P18", "DOC1"),
        ("E31", "2024-05-05", "outpatient", "Knee OA, NSAIDs", "P19", "DOC5"),
        ("E32", "2024-04-28", "outpatient", "Anxiety, started SSRI", "P20", "DOC8"),
    ]
    if light:
        encounters = encounters[:16]
    for eid, date, etype, notes, pid, doc_id in encounters:
        _run("""
        MATCH (p:Patient {id: $pid}), (doc:Doctor {id: $doc_id})
        CREATE (e:Encounter {id: $eid, date: $date, type: $etype, notes: $notes})
        CREATE (p)-[:HAS_ENCOUNTER]->(e)
        CREATE (e)-[:PERFORMED_BY]->(doc)
        RETURN 1
        """, {"eid": eid, "date": date, "etype": etype, "notes": notes, "pid": pid, "doc_id": doc_id})

    # -------------------------------------------------------------------------
    # 8. VISITS: Patient -> Doctor (for compliance attribution)
    # -------------------------------------------------------------------------
    visits = [
        ("P1", "DOC1"), ("P2", "DOC3"), ("P3", "DOC4"), ("P4", "DOC1"), ("P4", "DOC3"),
        ("P5", "DOC2"), ("P5", "DOC3"), ("P6", "DOC4"), ("P7", "DOC2"), ("P8", "DOC1"), ("P8", "DOC5"),
        ("P9", "DOC2"), ("P10", "DOC3"), ("P11", "DOC1"), ("P12", "DOC6"), ("P13", "DOC7"),
        ("P14", "DOC4"), ("P15", "DOC4"), ("P16", "DOC3"), ("P17", "DOC4"), ("P18", "DOC1"),
        ("P19", "DOC5"), ("P20", "DOC8"),
    ]
    for pid, doc_id in visits:
        _run("MATCH (p:Patient {id: $pid}), (d:Doctor {id: $doc_id}) MERGE (p)-[:VISITS]->(d) RETURN 1", {"pid": pid, "doc_id": doc_id})

    # -------------------------------------------------------------------------
    # 9. HAS_DISEASE: patient diagnoses with diagnosed_on
    # -------------------------------------------------------------------------
    has_disease = [
        ("P1", "D1", "2023-06-01"), ("P2", "D2", "2023-08-10"), ("P3", "D3", "2023-09-15"),
        ("P4", "D1", "2022-01-20"), ("P4", "D2", "2023-04-01"), ("P5", "D2", "2023-11-01"),
        ("P6", "D3", "2022-05-10"), ("P7", "D5", "2024-01-08"), ("P8", "D1", "2020-03-12"), ("P8", "D4", "2021-07-22"),
        ("P9", "D5", "2023-12-01"), ("P10", "D2", "2023-02-14"), ("P11", "D1", "2022-09-01"),
        ("P12", "D6", "2024-04-14"), ("P13", "D7", "2024-03-01"), ("P14", "D8", "2020-05-10"),
        ("P15", "D3", "2023-01-20"), ("P16", "D2", "2024-02-20"), ("P17", "D3", "2022-11-01"),
        ("P18", "D1", "2023-03-25"), ("P19", "D4", "2022-08-15"), ("P20", "D5", "2024-04-28"),
    ]
    for pid, did, diagnosed_on in has_disease:
        _run("""
        MATCH (p:Patient {id: $pid}), (d:Disease {id: $did})
        MERGE (p)-[r:HAS_DISEASE]->(d) SET r.diagnosed_on = $diagnosed_on
        RETURN 1
        """, {"pid": pid, "did": did, "diagnosed_on": diagnosed_on})

    # -------------------------------------------------------------------------
    # 10. TREATS: which doctors treat which diseases
    # -------------------------------------------------------------------------
    treats = [
        ("DOC1", "D1"), ("DOC2", "D2"), ("DOC2", "D1"), ("DOC2", "D5"), ("DOC3", "D2"),
        ("DOC4", "D3"), ("DOC4", "D8"), ("DOC5", "D4"), ("DOC6", "D6"), ("DOC7", "D7"),
        ("DOC8", "D5"), ("DOC9", "D1"), ("DOC9", "D2"),
    ]
    for doc_id, did in treats:
        _run("MATCH (doc:Doctor {id: $doc_id}), (d:Disease {id: $did}) MERGE (doc)-[:TREATS]->(d) RETURN 1", {"doc_id": doc_id, "did": did})

    # -------------------------------------------------------------------------
    # 11. Drugs (15): protocol + antibiotics, iron, SABA, etc.
    # -------------------------------------------------------------------------
    _run("""
    CREATE
      (drug1:Drug {id: 'DRUG1', name: 'Lisinopril'}),
      (drug2:Drug {id: 'DRUG2', name: 'Metformin'}),
      (drug3:Drug {id: 'DRUG3', name: 'Inhaled corticosteroid'}),
      (drug4:Drug {id: 'DRUG4', name: 'NSAIDs'}),
      (drug5:Drug {id: 'DRUG5', name: 'SSRI'}),
      (drug6:Drug {id: 'DRUG6', name: 'Amoxicillin'}),
      (drug7:Drug {id: 'DRUG7', name: 'Iron supplement'}),
      (drug8:Drug {id: 'DRUG8', name: 'Short-acting beta-agonist'}),
      (drug9:Drug {id: 'DRUG9', name: 'Azithromycin'}),
      (drug10:Drug {id: 'DRUG10', name: 'Ferrous sulfate'}),
      (drug11:Drug {id: 'DRUG11', name: 'LABA/ICS combination'}),
      (drug12:Drug {id: 'DRUG12', name: 'Amlodipine'}),
      (drug13:Drug {id: 'DRUG13', name: 'Sertraline'}),
      (drug14:Drug {id: 'DRUG14', name: 'Ibuprofen'}),
      (drug15:Drug {id: 'DRUG15', name: 'Omeprazole'})
    RETURN 1
    """)

    # -------------------------------------------------------------------------
    # 12. Procedures (12): BP, HbA1c, spirometry, imaging, counseling, CBC, chest X-ray, etc.
    # -------------------------------------------------------------------------
    _run("""
    CREATE
      (proc1:Procedure {id: 'PROC1', name: 'BP monitoring'}),
      (proc2:Procedure {id: 'PROC2', name: 'HbA1c test'}),
      (proc3:Procedure {id: 'PROC3', name: 'Spirometry'}),
      (proc4:Procedure {id: 'PROC4', name: 'Joint imaging'}),
      (proc5:Procedure {id: 'PROC5', name: 'Counseling'}),
      (proc6:Procedure {id: 'PROC6', name: 'Chest X-ray'}),
      (proc7:Procedure {id: 'PROC7', name: 'CBC draw'}),
      (proc8:Procedure {id: 'PROC8', name: 'Iron studies'}),
      (proc9:Procedure {id: 'PROC9', name: 'Blood glucose test'}),
      (proc10:Procedure {id: 'PROC10', name: 'Urinalysis'}),
      (proc11:Procedure {id: 'PROC11', name: 'Peak flow measurement'}),
      (proc12:Procedure {id: 'PROC12', name: 'CT chest'})
    RETURN 1
    """)

    # -------------------------------------------------------------------------
    # 13. Follow-ups (8): one per disease for protocol chain
    # -------------------------------------------------------------------------
    _run("""
    CREATE
      (f1:FollowUp {id: 'FU1', name: '3-month BP check'}),
      (f2:FollowUp {id: 'FU2', name: 'Quarterly HbA1c'}),
      (f3:FollowUp {id: 'FU3', name: 'Annual lung function'}),
      (f4:FollowUp {id: 'FU4', name: '6-month joint review'}),
      (f5:FollowUp {id: 'FU5', name: 'Monthly therapy review'}),
      (f6:FollowUp {id: 'FU6', name: 'Post-pneumonia follow-up'}),
      (f7:FollowUp {id: 'FU7', name: 'Anemia recheck in 3 months'}),
      (f8:FollowUp {id: 'FU8', name: 'COPD annual review'})
    RETURN 1
    """)

    # -------------------------------------------------------------------------
    # 14. Protocol: Disease -> RECOMMENDED_DRUG -> Drug -> RECOMMENDED_PROCEDURE -> Procedure -> FOLLOW_UP -> FollowUp
    # -------------------------------------------------------------------------
    _run("MATCH (d:Disease {id: 'D1'}), (drug:Drug {id: 'DRUG1'}) CREATE (d)-[:RECOMMENDED_DRUG]->(drug)")
    _run("MATCH (d:Drug {id: 'DRUG1'}), (p:Procedure {id: 'PROC1'}) CREATE (d)-[:RECOMMENDED_PROCEDURE]->(p)")
    _run("MATCH (p:Procedure {id: 'PROC1'}), (f:FollowUp {id: 'FU1'}) CREATE (p)-[:FOLLOW_UP]->(f)")
    _run("MATCH (d:Disease {id: 'D2'}), (drug:Drug {id: 'DRUG2'}) CREATE (d)-[:RECOMMENDED_DRUG]->(drug)")
    _run("MATCH (d:Drug {id: 'DRUG2'}), (p:Procedure {id: 'PROC2'}) CREATE (d)-[:RECOMMENDED_PROCEDURE]->(p)")
    _run("MATCH (p:Procedure {id: 'PROC2'}), (f:FollowUp {id: 'FU2'}) CREATE (p)-[:FOLLOW_UP]->(f)")
    _run("MATCH (d:Disease {id: 'D3'}), (drug:Drug {id: 'DRUG3'}) CREATE (d)-[:RECOMMENDED_DRUG]->(drug)")
    _run("MATCH (d:Drug {id: 'DRUG3'}), (p:Procedure {id: 'PROC3'}) CREATE (d)-[:RECOMMENDED_PROCEDURE]->(p)")
    _run("MATCH (p:Procedure {id: 'PROC3'}), (f:FollowUp {id: 'FU3'}) CREATE (p)-[:FOLLOW_UP]->(f)")
    _run("MATCH (d:Disease {id: 'D4'}), (drug:Drug {id: 'DRUG4'}) CREATE (d)-[:RECOMMENDED_DRUG]->(drug)")
    _run("MATCH (d:Drug {id: 'DRUG4'}), (p:Procedure {id: 'PROC4'}) CREATE (d)-[:RECOMMENDED_PROCEDURE]->(p)")
    _run("MATCH (p:Procedure {id: 'PROC4'}), (f:FollowUp {id: 'FU4'}) CREATE (p)-[:FOLLOW_UP]->(f)")
    _run("MATCH (d:Disease {id: 'D5'}), (drug:Drug {id: 'DRUG5'}) CREATE (d)-[:RECOMMENDED_DRUG]->(drug)")
    _run("MATCH (d:Drug {id: 'DRUG5'}), (p:Procedure {id: 'PROC5'}) CREATE (d)-[:RECOMMENDED_PROCEDURE]->(p)")
    _run("MATCH (p:Procedure {id: 'PROC5'}), (f:FollowUp {id: 'FU5'}) CREATE (p)-[:FOLLOW_UP]->(f)")
    _run("MATCH (d:Disease {id: 'D6'}), (drug:Drug {id: 'DRUG6'}) CREATE (d)-[:RECOMMENDED_DRUG]->(drug)")
    _run("MATCH (d:Drug {id: 'DRUG6'}), (p:Procedure {id: 'PROC6'}) CREATE (d)-[:RECOMMENDED_PROCEDURE]->(p)")
    _run("MATCH (p:Procedure {id: 'PROC6'}), (f:FollowUp {id: 'FU6'}) CREATE (p)-[:FOLLOW_UP]->(f)")
    _run("MATCH (d:Disease {id: 'D7'}), (drug:Drug {id: 'DRUG7'}) CREATE (d)-[:RECOMMENDED_DRUG]->(drug)")
    _run("MATCH (d:Drug {id: 'DRUG7'}), (p:Procedure {id: 'PROC8'}) CREATE (d)-[:RECOMMENDED_PROCEDURE]->(p)")
    _run("MATCH (p:Procedure {id: 'PROC8'}), (f:FollowUp {id: 'FU7'}) CREATE (p)-[:FOLLOW_UP]->(f)")
    _run("MATCH (d:Disease {id: 'D8'}), (drug:Drug {id: 'DRUG11'}) CREATE (d)-[:RECOMMENDED_DRUG]->(drug)")
    _run("MATCH (d:Drug {id: 'DRUG11'}), (p:Procedure {id: 'PROC3'}) CREATE (d)-[:RECOMMENDED_PROCEDURE]->(p)")
    _run("MATCH (p:Procedure {id: 'PROC3'}), (f:FollowUp {id: 'FU8'}) CREATE (p)-[:FOLLOW_UP]->(f)")

    # -------------------------------------------------------------------------
    # 15. Labs: result instances with name, result_value, unit, normal_range, status, date
    #    Linked to encounters via ORDERED_LAB (Encounter -> Lab)
    # -------------------------------------------------------------------------
    labs = [
        ("L1", "HbA1c", "7.1", "%", "4-6", "final", "2024-04-01", "E3"),
        ("L2", "Fasting glucose", "128", "mg/dL", "70-100", "final", "2024-04-01", "E3"),
        ("L3", "HbA1c", "7.2", "%", "4-6", "final", "2024-05-12", "E4"),
        ("L4", "BP systolic", "142", "mmHg", "<140", "final", "2024-01-10", "E1"),
        ("L5", "BP diastolic", "88", "mmHg", "<90", "final", "2024-01-10", "E1"),
        ("L6", "BP systolic", "132", "mmHg", "<140", "final", "2024-02-15", "E2"),
        ("L7", "BP diastolic", "82", "mmHg", "<90", "final", "2024-02-15", "E2"),
        ("L8", "Spirometry FEV1", "92", "% predicted", ">80", "final", "2024-06-10", "E6"),
        ("L9", "HbA1c", "8.0", "%", "4-6", "final", "2024-02-01", "E9"),
        ("L10", "HbA1c", "7.4", "%", "4-6", "final", "2024-05-20", "E10"),
        ("L11", "CBC Hemoglobin", "10.2", "g/dL", "12-16", "final", "2024-03-01", "E22"),
        ("L12", "CBC WBC", "4.5", "K/uL", "4-11", "final", "2024-03-01", "E22"),
        ("L13", "Serum iron", "35", "mcg/dL", "60-170", "final", "2024-03-01", "E22"),
        ("L14", "Ferritin", "18", "ng/mL", "12-150", "final", "2024-03-01", "E22"),
        ("L15", "Ferritin", "45", "ng/mL", "12-150", "final", "2024-06-20", "E23"),
        ("L16", "CBC Hemoglobin", "11.8", "g/dL", "12-16", "final", "2024-06-20", "E23"),
        ("L17", "Chest X-ray", "Infiltrate RLL", "finding", "clear", "final", "2024-04-14", "E21"),
        ("L18", "CBC WBC", "14.2", "K/uL", "4-11", "final", "2024-04-14", "E21"),
        ("L19", "Peak flow", "380", "L/min", ">400", "final", "2024-05-30", "E25"),
        ("L20", "BP systolic", "138", "mmHg", "<140", "final", "2024-02-28", "E20"),
        ("L21", "HbA1c", "6.8", "%", "4-6", "final", "2024-05-08", "E19"),
        ("L22", "Fasting glucose", "98", "mg/dL", "70-100", "final", "2024-01-25", "E18"),
    ]
    for lid, name, result_value, unit, normal_range, status, date, eid in labs:
        _run("""
        MATCH (e:Encounter {id: $eid})
        CREATE (l:Lab {id: $lid, name: $name, result_value: $result_value, unit: $unit, normal_range: $normal_range, status: $status, date: $date})
        CREATE (e)-[:ORDERED_LAB]->(l)
        RETURN 1
        """, {"lid": lid, "name": name, "result_value": result_value, "unit": unit, "normal_range": normal_range, "status": status, "date": date, "eid": eid})

    # -------------------------------------------------------------------------
    # 16. Encounter -> INCLUDES_PROCEDURE -> Procedure (date, status); Patient -> HAD_PROCEDURE -> Procedure
    # -------------------------------------------------------------------------
    encounter_procedures = [
        ("E1", "PROC1", "2024-01-10", "completed"),
        ("E2", "PROC1", "2024-02-15", "completed"),
        ("E3", "PROC2", "2024-04-01", "completed"),
        ("E4", "PROC2", "2024-05-12", "completed"),
        ("E5", "PROC3", "2024-03-05", "completed"),
        ("E6", "PROC3", "2024-06-10", "completed"),
        ("E7", "PROC1", "2024-01-20", "completed"),
        ("E8", "PROC1", "2024-04-18", "completed"),
        ("E9", "PROC2", "2024-02-01", "completed"),
        ("E10", "PROC2", "2024-05-20", "completed"),
        ("E11", "PROC3", "2024-03-12", "completed"),
        ("E12", "PROC3", "2024-07-01", "completed"),
        ("E13", "PROC5", "2024-01-08", "completed"),
        ("E14", "PROC5", "2024-04-22", "completed"),
        ("E15", "PROC1", "2024-02-10", "completed"),
        ("E16", "PROC1", "2024-06-05", "completed"),
        ("E17", "PROC5", "2024-03-18", "completed"),
        ("E18", "PROC2", "2024-01-25", "completed"),
        ("E19", "PROC2", "2024-05-08", "completed"),
        ("E20", "PROC1", "2024-02-28", "completed"),
        ("E21", "PROC6", "2024-04-14", "completed"),
        ("E21", "PROC7", "2024-04-14", "completed"),
        ("E22", "PROC7", "2024-03-01", "completed"),
        ("E23", "PROC8", "2024-06-20", "completed"),
        ("E23", "PROC7", "2024-06-20", "completed"),
        ("E24", "PROC3", "2024-01-15", "completed"),
        ("E25", "PROC11", "2024-05-30", "completed"),
        ("E26", "PROC6", "2024-04-08", "completed"),
        ("E27", "PROC7", "2024-07-10", "completed"),
        ("E28", "PROC2", "2024-02-20", "completed"),
        ("E29", "PROC3", "2024-06-15", "completed"),
        ("E30", "PROC1", "2024-03-25", "completed"),
        ("E31", "PROC4", "2024-05-05", "completed"),
        ("E32", "PROC5", "2024-04-28", "completed"),
    ]
    for eid, proc_id, pdate, pstatus in encounter_procedures:
        _run("""
        MATCH (e:Encounter {id: $eid}), (proc:Procedure {id: $proc_id})
        MATCH (e)<-[:HAS_ENCOUNTER]-(p:Patient)
        MERGE (e)-[r:INCLUDES_PROCEDURE]->(proc) SET r.date = $pdate, r.status = $pstatus
        MERGE (p)-[:HAD_PROCEDURE]->(proc)
        RETURN 1
        """, {"eid": eid, "proc_id": proc_id, "pdate": pdate, "pstatus": pstatus})

    # -------------------------------------------------------------------------
    # 17. Encounter -> PRESCRIBED -> Drug (prescribed_on, dose, frequency, duration, indication, guideline_based)
    #     and Patient -> TREATED_WITH -> Drug (for compliance checker)
    # -------------------------------------------------------------------------
    prescriptions = [
        ("E1", "DRUG1", "2024-01-10", "10mg", "once daily", "ongoing", "hypertension", True, "P1"),
        ("E3", "DRUG2", "2024-04-01", "500mg", "twice daily", "ongoing", "Type 2 diabetes", True, "P2"),
        ("E5", "DRUG3", "2024-03-05", "250mcg", "twice daily", "ongoing", "Asthma", True, "P3"),
        ("E7", "DRUG1", "2024-01-20", "10mg", "once daily", "ongoing", "Hypertension", True, "P4"),
        ("E7", "DRUG2", "2024-01-20", "500mg", "twice daily", "ongoing", "Type 2 diabetes", True, "P4"),
        ("E9", "DRUG1", "2024-02-01", "10mg", "once daily", "ongoing", "Diabetes", False, "P5"),
        ("E10", "DRUG2", "2024-05-20", "1000mg", "twice daily", "ongoing", "Type 2 diabetes", True, "P5"),
        ("E11", "DRUG3", "2024-03-12", "250mcg", "twice daily", "ongoing", "Asthma", True, "P6"),
        ("E13", "DRUG5", "2024-01-08", "50mg", "once daily", "ongoing", "Anxiety", True, "P7"),
        ("E15", "DRUG1", "2024-02-10", "20mg", "once daily", "ongoing", "Hypertension", True, "P8"),
        # P9: anxiety — counseling only (E17), no drug prescribed → compliance violation
        ("E18", "DRUG2", "2024-01-25", "500mg", "twice daily", "ongoing", "Type 2 diabetes", True, "P10"),
        ("E20", "DRUG1", "2024-02-28", "10mg", "once daily", "ongoing", "Hypertension", True, "P11"),
        ("E21", "DRUG6", "2024-04-14", "500mg", "three times daily", "7 days", "Pneumonia", True, "P12"),
        ("E22", "DRUG7", "2024-06-20", "325mg", "once daily", "3 months", "Anemia", True, "P13"),
        ("E23", "DRUG7", "2024-06-20", "325mg", "once daily", "3 months", "Anemia", True, "P13"),
        ("E24", "DRUG11", "2024-01-15", "1 puff", "twice daily", "ongoing", "COPD", True, "P14"),
        ("E25", "DRUG3", "2024-05-30", "500mcg", "twice daily", "ongoing", "Asthma", True, "P15"),
        ("E28", "DRUG2", "2024-02-20", "500mg", "twice daily", "ongoing", "Prediabetes", True, "P16"),
        ("E29", "DRUG3", "2024-06-15", "250mcg", "twice daily", "ongoing", "Asthma", True, "P17"),
        ("E30", "DRUG1", "2024-03-25", "10mg", "once daily", "ongoing", "Hypertension", True, "P18"),
        ("E31", "DRUG4", "2024-05-05", "400mg", "as needed", "ongoing", "Osteoarthritis", True, "P19"),
        ("E32", "DRUG5", "2024-04-28", "50mg", "once daily", "ongoing", "Anxiety", True, "P20"),
    ]
    for eid, drug_id, prescribed_on, dose, frequency, duration, indication, guideline_based, pid in prescriptions:
        _run("""
        MATCH (e:Encounter {id: $eid}), (drug:Drug {id: $drug_id}), (p:Patient {id: $pid})
        MERGE (e)-[r:PRESCRIBED]->(drug) SET r.prescribed_on = $prescribed_on, r.dose = $dose, r.frequency = $frequency, r.duration = $duration, r.indication = $indication, r.guideline_based = $guideline_based
        MERGE (p)-[:TREATED_WITH]->(drug)
        RETURN 1
        """, {"eid": eid, "drug_id": drug_id, "prescribed_on": prescribed_on, "dose": dose, "frequency": frequency, "duration": duration, "indication": indication, "guideline_based": guideline_based, "pid": pid})

    # P9: anxiety, counseling only — no TREATED_WITH so compliance will flag missing drug
    # P8: has D4 (OA) but only PROC1 (BP) — missing PROC4 (joint imaging) for OA

    # -------------------------------------------------------------------------
    # 18. Patient -> FOLLOWED_UP_WITH -> FollowUp (date, status)
    # -------------------------------------------------------------------------
    follow_ups = [
        ("P1", "FU1", "2024-05-10", "completed"), ("P2", "FU2", "2024-07-01", "scheduled"),
        ("P3", "FU3", "2025-03-05", "scheduled"), ("P4", "FU1", "2024-05-18", "completed"),
        ("P4", "FU2", "2024-07-18", "scheduled"), ("P5", "FU2", "2024-08-20", "scheduled"),
        ("P6", "FU3", "2025-03-12", "scheduled"), ("P7", "FU5", "2024-05-22", "completed"),
        ("P8", "FU1", "2024-09-05", "scheduled"), ("P10", "FU2", "2024-08-08", "scheduled"),
        ("P11", "FU1", "2024-05-28", "completed"), ("P12", "FU6", "2024-05-14", "completed"),
        ("P13", "FU7", "2024-09-20", "scheduled"), ("P14", "FU8", "2025-01-15", "scheduled"),
        ("P15", "FU3", "2025-05-30", "scheduled"), ("P16", "FU2", "2024-08-20", "scheduled"),
        ("P17", "FU3", "2025-06-15", "scheduled"), ("P18", "FU1", "2024-06-25", "completed"),
        ("P19", "FU4", "2024-11-05", "scheduled"), ("P20", "FU5", "2024-05-28", "scheduled"),
    ]
    for pid, fu_id, date, status in follow_ups:
        _run("""
        MATCH (p:Patient {id: $pid}), (f:FollowUp {id: $fu_id})
        MERGE (p)-[r:FOLLOWED_UP_WITH]->(f) SET r.date = $date, r.status = $status
        RETURN 1
        """, {"pid": pid, "fu_id": fu_id, "date": date, "status": status})

    # -------------------------------------------------------------------------
    # 17. Patient notes: (Patient)-[:HAS_NOTE]->(PatientNote {id, text, date})
    # -------------------------------------------------------------------------
    patient_notes = [
        ("N1", "P1", "2024-01-10", "BP elevated at visit; started on lifestyle advice."),
        ("N2", "P1", "2024-02-15", "BP improved. Continue current medication."),
        ("N3", "P2", "2024-04-01", "HbA1c 7.2%; discuss metformin adherence."),
        ("N4", "P5", "2024-05-20", "Patient reports difficulty with metformin; considering alternative."),
        ("N5", "P6", "2024-03-12", "Asthma control suboptimal; step up ICS."),
        ("N6", "P10", "2024-06-01", "Diabetes follow-up; insulin added due to persistent elevation."),
    ]
    for note_id, pid, date, text in patient_notes:
        _run("""
        CREATE (n:PatientNote {id: $note_id, text: $text, date: $date})
        WITH n
        MATCH (p:Patient {id: $pid})
        CREATE (p)-[:HAS_NOTE]->(n)
        RETURN 1
        """, {"note_id": note_id, "pid": pid, "date": date, "text": text})

    print(
        "Seed complete: 20 patients, 10 doctors, 8 diseases, 5 hospitals, 32 appointments, 32 encounters, "
        "22 labs, 15 drugs, 12 procedures, 8 follow-ups, PatientNotes (HAS_NOTE). "
        "Protocol D1–D8 with RECOMMENDED_DRUG/PROCEDURE/FOLLOW_UP. "
        "Actual care: PRESCRIBED, ORDERED_LAB, INCLUDES_PROCEDURE, HAD_PROCEDURE, TREATED_WITH, FOLLOWED_UP_WITH. "
        "Some cases compliant, some intentionally non-compliant for AI checker."
    )


if __name__ == "__main__":
    try:
        seed()
    except Exception as e:
        print(f"Error: {e}")
        raise
