"""
FastAPI API for the healthcare Neo4j project (BarbaraTest database).
Provides /check_compliance endpoint that queries Neo4j dynamically and returns
patients with violations, doctors' compliance scores, and violated relationship details.
Run with: uvicorn api:app --reload
"""
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from neo4j_connect import verify_connection
from ai_compliance import run_compliance_check

app = FastAPI(
    title="Healthcare Compliance API",
    description="API for protocol compliance checks against Neo4j BarbaraTest database.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def root():
    """Health/info."""
    return {"service": "Healthcare Compliance API", "docs": "/docs", "check_compliance": "/check_compliance"}


@app.get("/health")
def health():
    """Check Neo4j connectivity."""
    try:
        verify_connection()
        return {"status": "ok", "database": "connected"}
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Database connection failed: {e}")


@app.get("/check_compliance")
def check_compliance():
    """
    Compare actual patient treatments with protocol guidelines.
    Returns:
    - patients_with_violations: list of patients that have at least one protocol violation
    - doctor_compliance_scores: per-doctor compliance score (percentage) and counts
    - violated_relationships: details of each violation (patient, disease, recommended vs actual)
    All data is read dynamically from Neo4j (reflects current database state).
    """
    try:
        result = run_compliance_check()
        return {
            "patients_with_violations": result["patients_with_violations"],
            "doctor_compliance_scores": result["doctor_compliance_scores"],
            "violated_relationships": result["violated_relationships"],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
