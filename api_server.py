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

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from ai_agent import ask_agent, ai_agent_query, patient_analysis_to_agent_response


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


@app.get("/")
def root():
    """Health and endpoint list."""
    return {
        "service": "Compliance Dashboard API",
        "endpoints": {
            "POST /ask-agent": "Natural language question -> AI analysis + highlight_query",
            "POST /analyze-patient": "patient_id -> patient protocol analysis + highlight_query",
        },
        "docs": "/docs",
    }
