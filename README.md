# neo4jchartR

Python project that connects to your Neo4j instance **BarbaraTest** and performs CRUD operations and graph traversal on a healthcare model.

## Graph model

**Nodes:** `Patient`, `Doctor`, `Disease`, `Drug`, `Hospital`, `Appointment`, `Encounter`, `Lab`, `Procedure`

**Relationships:** `HAS_DISEASE`, `TREATED_WITH`, `TREATS`, `VISITS`, `HAS_APPOINTMENT`, `HAS_ENCOUNTER`, `AT_HOSPITAL`, `ORDERED_LAB`, `INCLUDES_PROCEDURE`, `PRESCRIBED`, `PERFORMED_BY`, `HAD_PROCEDURE`, `FOLLOWED_UP_WITH`

**Properties:** `id`, `name`, `age`, `sex`, `specialty`, `date`, `diagnosed_on`, `icd10`

**Protocol guideline graph:** `Disease` -[:RECOMMENDED_DRUG]-> `Drug` -[:RECOMMENDED_PROCEDURE]-> `Procedure` -[:FOLLOW_UP]-> `FollowUp`. Actual treatments: `Patient` -[:TREATED_WITH]-> `Drug`, `Patient` -[:HAD_PROCEDURE]-> `Procedure`.

**Richer care data (seed):** `Appointment` (date, reason, status) -[:AT_HOSPITAL]-> `Hospital`. `Encounter` (date, type, notes) -[:PERFORMED_BY]-> `Doctor`; Encounter -[:ORDERED_LAB]-> `Lab` (result_value, unit, normal_range, status, date); Encounter -[:PRESCRIBED]-> `Drug` (dose, frequency, duration, indication, guideline_based); Encounter -[:INCLUDES_PROCEDURE]-> `Procedure` (date, status). `Patient` -[:FOLLOWED_UP_WITH]-> `FollowUp` (date, status).

## Seed data and protocol compliance

The seed creates **actual clinical treatment data** (not only structure) so the AI compliance layer can compare real decisions to guidelines:

- **20 patients, 10 doctors, 8 diseases** (Hypertension, Type 2 Diabetes, Asthma, OA, Anxiety, Pneumonia, Anemia, COPD), **5 hospitals**, **32 appointments** (date, reason, status), **32 encounters** (date, type, notes) with performing doctor.
- **Labs:** 22 result records (e.g. HbA1c, glucose, CBC, BP, ferritin, chest X-ray) with `result_value`, `unit`, `normal_range`, `status`, `date`, linked to encounters via `ORDERED_LAB`.
- **Procedures:** 12 types (BP monitoring, HbA1c, spirometry, joint imaging, counseling, chest X-ray, CBC, iron studies, etc.); encounters link via `INCLUDES_PROCEDURE` (date, status); `Patient` -[:HAD_PROCEDURE]-> `Procedure` is kept for protocol checks.
- **Drugs:** 15 (protocol drugs + e.g. antibiotics, iron, SABA); `Encounter` -[:PRESCRIBED]-> `Drug` with dose/frequency/duration/indication/guideline_based; `Patient` -[:TREATED_WITH]-> `Drug` for compliance.
- **Clinically plausible cases:** e.g. diabetes patients with HbA1c/glucose labs; hypertension with BP and ACE inhibitor; pneumonia with CBC, chest X-ray, antibiotics; asthma with inhaler and spirometry; anemia with CBC and iron studies.
- **Some cases are intentionally non-compliant** (wrong drug, missing procedure, or missing drug) so the compliance checker has real violations to detect.

**New query helpers in `neo4j_ops.py`:**  
`get_patient_full_journey(patient_id)`, `get_patient_labs(patient_id)`, `get_patient_procedures(patient_id)`, `get_patient_drugs(patient_id)`, `get_patient_encounters(patient_id)`, `get_doctor_patient_cases(doctor_id)`.

## Setup

1. **Virtual environment and dependencies:**
   ```bash
   python3 -m venv venv
   source venv/bin/activate   # Windows: venv\Scripts\activate
   pip install -r requirements.txt
   ```

2. **Configure Neo4j:** copy `.env.example` to `.env` and set `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD`. Use `NEO4J_DATABASE=neo4j` (default database on your instance).

3. **Load sample data (including protocol + treatments):**
   ```bash
   python3 seed_data.py
   ```

---

## Running in VS Code

- **Terminal:** Open integrated terminal (`Ctrl+`` ` or View → Terminal), activate venv: `source venv/bin/activate` (macOS/Linux) or `venv\Scripts\activate` (Windows).
- **Run API:** In terminal, `uvicorn api:app --reload` then open http://127.0.0.1:8000/docs for Swagger.
- **Run compliance dashboard:** `python3 dashboard.py` — opens `compliance_dashboard.html` with side menu, stats, explanations, and filters.
- **Run compliance graph:** In terminal, `python3 visualization.py` — opens `compliance_graph.html` in the browser (red = violations, green = compliant).
- **Run main graph:** `python3 visualize_graph.py` — opens `graph.html`.
- **Trigger compliance check:** Call the API `GET /check_compliance` or run `python3 -c "from ai_compliance import run_compliance_check; print(run_compliance_check())"`.

---

## API (FastAPI)

Start the API server:

```bash
uvicorn api:app --reload
```

- **Docs:** http://127.0.0.1:8000/docs  
- **Health:** `GET /health` — checks Neo4j connection.  
- **Compliance:** `GET /check_compliance` — returns:
  - `patients_with_violations`: list of patients with at least one protocol violation
  - `doctor_compliance_scores`: per-doctor compliance score (%), compliant/total cases
  - `violated_relationships`: details of each violation (recommended vs actual)

All data is read dynamically from Neo4j (reflects current database state).

---

## Compliance checker (`ai_compliance.py`)

- **`run_compliance_check()`** — compares every patient’s treatments (per disease) with the protocol; flags wrong/missing drug or procedure; computes doctor compliance scores.
- **Use from API:** `api.py` calls `run_compliance_check()` for `/check_compliance`.
- **Use from visualization:** `visualization.py` calls it to colour edges (red = violation, green = compliant).
- **Use from code or terminal:**
  ```bash
  python3 -c "from ai_compliance import run_compliance_check; import json; print(json.dumps(run_compliance_check(), indent=2))"
  ```

---

## Compliance visualization (`visualization.py`)

Interactive graph with **violations in red**, **compliant treatments in green**:

```bash
python3 visualization.py
```

- **Output:** `compliance_graph.html` (opens in browser).
- **Node colors:** Patient = blue, Doctor = green, Disease = red, Hospital = purple, Appointment = orange, Drug/Procedure/FollowUp = distinct colors.
- **Edges:** Red = protocol violation; green = compliant treatment; hover shows “Recommended vs Actual” where applicable.

---

## Interactive compliance dashboard (`dashboard.py`)

Full interactive dashboard with **side menu**, **statistics**, **protocol explanations**, and **filtering**:

```bash
python3 dashboard.py
```

- **Output:** `compliance_dashboard.html` (opens in browser).

### Side menu (collapsible)
- **Statistics:** Total patients, number of violations, doctor compliance scores (from `run_compliance_check()`; data is read from Neo4j when you run the script).
- **Node types:** Patient, Doctor, Disease, Hospital, Appointment, Drug, Procedure, FollowUp (with color legend).
- **Edge types:** HAS_DISEASE, TREATS, VISITS, HAS_APPOINTMENT, AT_HOSPITAL, RECOMMENDED_DRUG, RECOMMENDED_PROCEDURE, FOLLOW_UP, TREATED_WITH, HAD_PROCEDURE.
- **Color coding:** Red = violation, green = compliant, gray = other. Use the **◀ Sidebar** button to collapse or expand the panel.

### Enhanced hover and tooltips
- **Patient nodes:** Age, sex, list of diseases.
- **Doctor nodes:** Specialty and compliance score.
- **Violation edges:** Recommended vs actual treatment plus a short line on *why the recommended treatment is better* (evidence-based protocol, ICD-10 guidelines, best practice).

### Click for protocol explanations
- Click a **Disease**, **Drug**, or **Procedure** node to show an explanation panel at the bottom.
- The panel explains why each drug/procedure in the protocol is recommended (short text and optional references; content is from `protocol_explanations.py`).

### Filtering
- **Doctor / Patient / Disease / Hospital:** Dropdowns are filled from the graph. Choose one to focus on that entity and its connections (only connected nodes stay visible).
- **Compliance:** "Violations only" or "Compliant only" to highlight by compliance status.
- **Apply filter** runs the selection; **Reset** clears filters.

### Integration
- Dashboard calls `run_compliance_check()` when you run `python3 dashboard.py`; no hard-coded compliance data. Stats and violation highlighting reflect the current Neo4j database.

---

## Graph visualization (pyvis)

Generate an interactive HTML graph from the database:

```bash
python3 visualize_graph.py
```

- **Output:** `graph.html` (saved in the project folder and opened in your default browser).
- **Data:** Nodes and relationships are read dynamically from Neo4j (not hard-coded).
- **Limits:** First 50 nodes and 50 relationships (change `NODE_LIMIT` and `REL_LIMIT` at the top of `visualize_graph.py` to adjust).
- **Node colors:** Patient = blue, Doctor = green, Disease = red, Hospital = purple, Appointment = orange, others = gray.
- **Node shapes:** Patient = circle, Doctor = square, Disease = triangle, Hospital = diamond, Appointment = star.
- **Interactivity:** Drag nodes, zoom in/out, hover to see properties (age, specialty, date, icd10, etc.). Edge labels show relationship type.

**Script structure:** `connect_to_neo4j()` → `get_graph_data()` → `build_graph()` → `show_graph()`; `main()` runs the full workflow with status messages. Suitable for running in VS Code or the terminal.

## Run the console app

```bash
python3 main.py
```

**Menu options:**

| Option | Action |
|--------|--------|
| 1 | Show all patients and their diseases |
| 2 | Show doctors and specialties |
| 3 | Show which doctors treat which diseases |
| 4 | Show appointments for a patient (by patient id) |
| 5 | Add relationship Patient → Disease (HAS_DISEASE) |
| 6 | Update a patient's age |
| 7 | Delete a patient (DETACH DELETE) |
| 8 | Exit |

## Module overview

- **`neo4j_config.py`** – Loads connection settings from environment / `.env`.
- **`neo4j_connect.py`** – Driver and `run_query()`; used by all operations.
- **`neo4j_ops.py`** – All graph operations:
  - **Queries:** `get_patients_with_diseases`, `get_doctors_and_specialties`, `get_doctors_treating_diseases`, `get_patient_appointments`, `get_hospitals_visited_by_patients`
  - **Create relationships:** `create_has_disease`, `create_treats`, `create_visits`, `create_at_hospital`
  - **Updates:** `update_patient_age`, `update_doctor_specialty`, `update_diagnosed_on`
  - **Deletes:** `delete_patient`, `delete_patient_disease_relationship`
- **`main.py`** – Console UI that calls the above.
- **`ai_compliance.py`** – Compares actual treatments with protocol; flags violations; computes doctor compliance scores. Used by API and visualization.
- **`api.py`** – FastAPI app: `GET /check_compliance` returns violations and doctor scores (queries Neo4j dynamically).
- **`dashboard.py`** – Interactive compliance dashboard: side menu (stats, node/edge legend, color coding), enhanced tooltips (patient age/sex/diseases, doctor compliance), click-to-show protocol explanations, and filters (doctor, patient, disease, compliance, hospital). Calls `run_compliance_check()` when run. Output: `compliance_dashboard.html`.
- **`protocol_explanations.py`** – Short text explanations for why each protocol drug/procedure is recommended (Disease, Drug, Procedure keys); used by the dashboard on node click.
- **`visualization.py`** – Compliance-focused pyvis graph: red edges = violations, green = compliant; hover shows actual vs recommended. Output: `compliance_graph.html`.
- **`visualize_graph.py`** – General pyvis graph (colors, shapes, tooltips). Output: `graph.html`.
- **`seed_data.py`** – Creates sample nodes, protocol graph (Disease→Drug→Procedure→FollowUp), and actual treatments (some with violations).

## Using the API in code

```python
from neo4j_ops import (
    get_patients_with_diseases,
    get_doctors_treating_diseases,
    create_has_disease,
    update_patient_age,
    delete_patient,
)

# Queries
for r in get_patients_with_diseases():
    print(r)
for r in get_doctors_treating_diseases():
    print(r)

# Create relationship
create_has_disease("P1", "D1", diagnosed_on="2024-01-01")

# Update
update_patient_age("P1", 35)

# Delete
delete_patient("P1")
```

## Instance vs database

**BarbaraTest** is the name of your Neo4j *instance*. This project uses the default database **neo4j** on that instance. To use another database (Neo4j 4.0+), set `NEO4J_DATABASE` in `.env`.
