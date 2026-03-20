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

from ai_agent import ask_agent, ai_agent_query, patient_analysis_to_agent_response
from document_upload import extract_text_from_upload, extract_medical_data
from neo4j_ops import (
    create_patient_from_document,
    get_all_patients_graph_data,
    get_patients_for_comparison,
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
