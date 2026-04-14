"""
FastAPI backend that exposes the AI agent for the compliance dashboard.

Run with:
  uvicorn api_server:app --reload

Endpoints:
  POST /ask-agent     — Natural language question → AI agent analysis + highlight_query
  POST /analyze-patient — patient_id → analyze_patient_protocol → same response shape

The AI agent uses the existing Neo4j connection (neo4j_connect.run_query via neo4j_ops).
All graph data is retrieved through those modules; this API only orchestrates calls
and returns JSON for the dashboard to display the answer and highlight the graph.
"""

from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from ai_agent import ask_agent, ai_agent_query, patient_analysis_to_agent_response, ask_agent_with_context
from document_upload import extract_text_from_upload, extract_medical_data
from neo4j_ops import (
    create_patient_from_document,
    get_all_patients_graph_data,
    get_patients_for_comparison,
    get_patient_timeline_data,
)
from ai_compliance import check_patient_compliance


app = FastAPI(
    title="Compliance Dashboard API",
    description="AI agent endpoints for protocol compliance analysis and graph highlighting.",
)

# -------- CORS: allow the frontend dashboard to call this API --------
# When the dashboard is served (e.g. from another port or static file), the browser
# will send requests to this API; CORS must allow the dashboard origin.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# -------- Request/response models --------

class AskAgentRequest(BaseModel):
    question: str
    selected_patients: list[str] | None = None


class AnalyzePatientRequest(BaseModel):
    patient_id: str


class ConfirmPatientRequest(BaseModel):
    patient_name: str | None = None
    age: int | None = None
    sex: str | None = None
    symptoms: list[str] = []
    diseases: list[str] = []
    clinical_values: dict = {}


class CompareRequest(BaseModel):
    patient_ids: list[str]


# -------- POST /ask-agent --------
# How it works:
# 1. The dashboard sends a natural language question (e.g. "Did patient P001 follow the diabetes protocol?").
# 2. We call ask_agent(question), which:
#    - Queries Neo4j via neo4j_ops (get_protocol_guidelines, get_patients_with_diseases,
#      get_actual_patient_treatments, get_patient_context, etc.) — all use run_query().
#    - Compares actual treatment to protocol (Disease → Recommended Drug → Procedure → FollowUp).
#    - Optionally calls the LLM to produce a human-readable answer.
#    - Builds highlight_nodes, highlight_relationships, and highlight_query for the graph.
# 3. We return the same JSON the frontend expects: answer, violation, protocol_expected,
#    actual_treatment, highlight_nodes, highlight_relationships, highlight_query.
# How the dashboard uses the response:
# - answer → show in the AI answer panel.
# - violation → show a compliance warning (e.g. red badge).
# - highlight_nodes → select/highlight those nodes in the graph (e.g. by id_prop or label).
# - highlight_query → can be run in Neo4j Browser, or the dashboard can filter the current
#   graph to show only nodes/edges that match this path (e.g. filter to patient + diseases + drugs).
@app.post("/ask-agent")
def ask_agent_endpoint(body: AskAgentRequest):
    """
    Call the AI agent with a natural language question.
    Neo4j data is retrieved inside ask_agent() via neo4j_ops (which uses run_query()).
    Returns the structured response so the dashboard can display the answer and
    highlight the relevant nodes in the graph visualization.
    """
    question = (body.question or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="question is required")
    try:
        selected = body.selected_patients or []
        if selected:
            result = ask_agent_with_context(question, selected)
        else:
            result = ask_agent(question)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# -------- POST /analyze-patient --------
# How it works:
# 1. The dashboard sends a patient_id (e.g. "P001" or "P1").
# 2. We call analyze_patient_protocol(patient_id), which:
#    - Uses get_patient_context(patient_id) to load from Neo4j: diseases, protocol per disease,
#      actual drugs/procedures, encounters, labs, patient notes (all via neo4j_ops/run_query).
#    - Runs check_patient_compliance for each disease of that patient (protocol comparison).
# 3. We convert the analysis to the same response shape as ask_agent via
#    patient_analysis_to_agent_response(), so the dashboard can use the same UI:
#    answer, violation, protocol_expected, actual_treatment, highlight_nodes,
#    highlight_relationships, highlight_query.
# How the dashboard uses the response:
# - Same as /ask-agent: display answer, show violation warning, highlight nodes,
#   and use highlight_query to visualize the patient's treatment path in the graph.
@app.post("/analyze-patient")
def analyze_patient_endpoint(body: AnalyzePatientRequest):
    """
    Analyze protocol compliance for a single patient.
    Neo4j data is retrieved inside analyze_patient_protocol() via neo4j_ops (run_query).
    Returns the same structured response as /ask-agent so the dashboard can
    display the result and highlight the graph (answer, violation, highlight_nodes, highlight_query).
    """
    patient_id = (body.patient_id or "").strip()
    if not patient_id:
        raise HTTPException(status_code=400, detail="patient_id is required")
    try:
        result = patient_analysis_to_agent_response(patient_id)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/upload-document")
async def upload_document_endpoint(file: UploadFile = File(...)):
    """
    Upload a medical document (PDF or text), extract structured patient data.
    Returns JSON with patient_name, age, sex, symptoms, diseases, clinical_values.
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="No file provided.")
    content = await file.read()
    if not content or len(content) == 0:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File too large (max 10 MB).")
    try:
        text = extract_text_from_upload(content, file.filename)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    try:
        data = extract_medical_data(text)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Extraction failed: {e}"
        )
    return data


@app.post("/confirm-patient")
def confirm_patient_endpoint(body: ConfirmPatientRequest):
    """
    Confirm extracted data and create the Patient (+ Symptom, Disease,
    ClinicalState) nodes in Neo4j.  Returns the created patient info.
    """
    if not body.symptoms and not body.diseases and not body.clinical_values:
        raise HTTPException(
            status_code=400,
            detail="No medical data to create — upload a document first.",
        )
    try:
        result = create_patient_from_document(body.model_dump())
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/compare-patients")
def compare_patients_endpoint(body: CompareRequest):
    """
    Compare 2+ patients: return per-patient data and the intersection of
    diseases, symptoms, and violations shared by all selected patients.
    """
    ids = list(dict.fromkeys(body.patient_ids or []))
    if len(ids) < 2:
        raise HTTPException(status_code=400, detail="Select at least 2 patients.")
    try:
        patients = get_patients_for_comparison(ids)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    if len(patients) < 2:
        raise HTTPException(status_code=404, detail="Could not find 2+ of the requested patients.")

    for p in patients:
        for d in p["diseases"]:
            try:
                r = check_patient_compliance(
                    p["patient_id"], p["patient_name"] or p["patient_id"],
                    d["id"], d["name"] or d["id"],
                )
                for v in r.get("violations") or []:
                    if v not in p["violations"]:
                        p["violations"].append(v)
            except Exception:
                pass

    disease_sets = [set(d["id"] for d in p["diseases"]) for p in patients]
    symptom_sets = [set(s["id"] for s in p["symptoms"]) for p in patients]
    violation_sets = [set(p["violations"]) for p in patients]

    common_disease_ids = disease_sets[0].intersection(*disease_sets[1:])
    common_symptom_ids = symptom_sets[0].intersection(*symptom_sets[1:]) if all(symptom_sets) else set()
    common_violations = violation_sets[0].intersection(*violation_sets[1:]) if all(violation_sets) else set()

    d_map: dict = {}
    s_map: dict = {}
    for p in patients:
        for d in p["diseases"]:
            d_map[d["id"]] = d
        for s in p["symptoms"]:
            s_map[s["id"]] = s

    common = {
        "diseases": [d_map[x] for x in sorted(common_disease_ids) if x in d_map],
        "symptoms": [s_map[x] for x in sorted(common_symptom_ids) if x in s_map],
        "violations": sorted(common_violations),
    }

    highlight_nodes: list[str] = []
    common_node_ids: list[str] = []
    for p in patients:
        highlight_nodes.append(f"Patient:{p['patient_id']}")
        for d in p["diseases"]:
            highlight_nodes.append(f"Disease:{d['id']}")
        for s in p["symptoms"]:
            highlight_nodes.append(f"Symptom:{s['id']}")
    for d in common["diseases"]:
        common_node_ids.append(f"Disease:{d['id']}")
    for s in common["symptoms"]:
        common_node_ids.append(f"Symptom:{s['id']}")

    return {
        "patients": patients,
        "common": common,
        "highlight_nodes": list(dict.fromkeys(highlight_nodes)),
        "common_node_ids": sorted(set(common_node_ids)),
    }


@app.get("/patient-timeline/{patient_id}")
def patient_timeline_endpoint(patient_id: str):
    """
    Return timeline data for a patient: clinical vitals over simulated time
    points plus clinical events (encounters, labs, drugs, procedures).
    """
    import random, math

    pid = patient_id.strip()
    if not pid:
        raise HTTPException(status_code=400, detail="patient_id is required")
    try:
        raw = get_patient_timeline_data(pid)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    cs = raw.get("clinical_state") or {}
    hours = [0, 2, 6, 12, 24, 48]
    labels = ["Admission", "2 h", "6 h", "12 h", "24 h", "48 h"]

    sofa_now = cs.get("sofa_score")
    map_now = cs.get("map")
    creat_now = cs.get("creatinine")
    gcs_now = cs.get("gcs")
    lactate_now = cs.get("lactate")

    has_cs = sofa_now is not None or map_now is not None

    sofa_now = sofa_now if sofa_now is not None else 3
    map_now = map_now if map_now is not None else 72
    creat_now = creat_now if creat_now is not None else 1.1
    gcs_now = gcs_now if gcs_now is not None else 14
    lactate_now = lactate_now if lactate_now is not None else 1.8

    treated = bool(cs.get("antibiotics_active") or cs.get("vasopressors_active"))

    random.seed(hash(pid) & 0xFFFFFFFF)

    def _jitter(v, pct=0.05):
        return round(v * (1 + random.uniform(-pct, pct)), 2)

    sofa_vals, map_vals, creat_vals, gcs_vals, lactate_vals = [], [], [], [], []
    for i, h in enumerate(hours):
        if h == 0:
            sofa_vals.append(max(0, sofa_now - 3))
            map_vals.append(min(95, map_now + 18))
            creat_vals.append(round(max(0.5, creat_now - 0.4), 2))
            gcs_vals.append(min(15, gcs_now + 2))
            lactate_vals.append(round(max(0.5, lactate_now - 0.8), 2))
        elif h <= 6:
            t = h / 6.0
            sofa_vals.append(_jitter(max(0, sofa_now - 3 + int(round(3 * t)))))
            map_vals.append(_jitter(min(95, map_now + 18 - int(round(18 * t)))))
            creat_vals.append(_jitter(round(max(0.5, creat_now - 0.4 + 0.4 * t), 2)))
            gcs_vals.append(max(3, min(15, int(round(gcs_now + 2 - 2 * t)))))
            lactate_vals.append(_jitter(round(max(0.5, lactate_now - 0.8 + 0.8 * t), 2)))
        else:
            rt = (h - 6) / 42.0
            if treated:
                sofa_vals.append(_jitter(max(0, sofa_now - int(round(sofa_now * 0.5 * rt)))))
                map_vals.append(_jitter(map_now + int(round(15 * rt))))
                creat_vals.append(_jitter(round(max(0.5, creat_now - creat_now * 0.25 * rt), 2)))
                gcs_vals.append(max(3, min(15, gcs_now + int(round(2 * rt)))))
                lactate_vals.append(_jitter(round(max(0.5, lactate_now - lactate_now * 0.3 * rt), 2)))
            else:
                sofa_vals.append(_jitter(sofa_now + int(round(2 * rt))))
                map_vals.append(_jitter(max(50, map_now - int(round(8 * rt)))))
                creat_vals.append(_jitter(round(creat_now + 0.3 * rt, 2)))
                gcs_vals.append(max(3, min(15, gcs_now - int(round(1 * rt)))))
                lactate_vals.append(_jitter(round(lactate_now + 0.5 * rt, 2)))

    def _trend(vals):
        if len(vals) < 2:
            return "stable"
        diff = vals[-1] - vals[-2]
        if abs(diff) < 0.3:
            return "stable"
        return "rising" if diff > 0 else "falling"

    events = []
    for enc in raw.get("encounters") or []:
        events.append({"type": "encounter", "label": (enc.get("type") or "Encounter") + (": " + enc["notes"][:60] if enc.get("notes") else ""), "date": enc.get("date"), "doctor": enc.get("doctor")})
    for drug in raw.get("drugs") or []:
        events.append({"type": "drug", "label": "Rx: " + (drug.get("name") or drug["id"]) + (" " + drug["dose"] if drug.get("dose") else ""), "date": drug.get("date")})
    for proc in raw.get("procedures") or []:
        events.append({"type": "procedure", "label": "Procedure: " + (proc.get("name") or proc["id"]), "date": proc.get("date")})
    for lab in raw.get("labs") or []:
        events.append({"type": "lab", "label": (lab.get("name") or lab["id"]) + ": " + str(lab.get("value") or "—") + " " + (lab.get("unit") or ""), "date": lab.get("date")})
    if cs.get("antibiotics_active"):
        events.append({"type": "treatment", "label": "Antibiotics started", "date": None})
    if cs.get("vasopressors_active"):
        events.append({"type": "treatment", "label": "Vasopressors started", "date": None})
    if cs.get("cultures_ordered"):
        events.append({"type": "treatment", "label": "Blood cultures ordered", "date": None})

    return {
        "patient_id": pid,
        "patient_name": raw.get("patient_name") or pid,
        "age": raw.get("age"),
        "sex": raw.get("sex"),
        "diseases": raw.get("diseases") or [],
        "has_clinical_state": has_cs,
        "treated": treated,
        "hours": hours,
        "labels": labels,
        "vitals": {
            "sofa": {"values": sofa_vals, "trend": _trend(sofa_vals), "unit": "", "critical_above": 2},
            "map": {"values": map_vals, "trend": _trend(map_vals), "unit": "mmHg", "critical_below": 65},
            "creatinine": {"values": creat_vals, "trend": _trend(creat_vals), "unit": "mg/dL", "critical_above": 1.5},
            "gcs": {"values": gcs_vals, "trend": _trend(gcs_vals), "unit": "", "critical_below": 13},
            "lactate": {"values": lactate_vals, "trend": _trend(lactate_vals), "unit": "mmol/L", "critical_above": 2.0},
        },
        "events": events,
    }


@app.get("/patients-sync")
def patients_sync_endpoint():
    """
    Return every patient with diseases, symptoms, and clinical state.
    The frontend calls this on page load (and after patient creation) so that
    patients created after the static dashboard HTML was generated still appear
    in the graph and filter dropdowns.
    """
    try:
        return get_all_patients_graph_data()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/")
def root():
    """Health and endpoint list."""
    return {
        "service": "Compliance Dashboard API",
        "endpoints": {
            "POST /ask-agent": "Natural language question -> AI analysis + highlight_query",
            "POST /analyze-patient": "patient_id -> patient protocol analysis + highlight_query",
            "POST /upload-document": "Upload medical document -> extracted data preview",
            "POST /confirm-patient": "Confirm extracted data -> create Patient in Neo4j",
            "GET  /patients-sync": "All patients + relationships for graph/filter sync",
        },
        "docs": "/docs",
    }
