"""
Medical document upload and AI extraction service.

Extracts structured patient data (symptoms, diseases, clinical values) from
uploaded PDF or plain-text documents. Uses OpenAI for intelligent extraction
with a regex-based fallback when no API key is configured.

Kept separate from neo4j_ops and dashboard so the extraction logic is modular.
"""

import json
import os
import re
from typing import Any

try:
    from PyPDF2 import PdfReader
    _HAS_PYPDF2 = True
except ImportError:
    _HAS_PYPDF2 = False


# ---------------------------------------------------------------------------
# 1. Text extraction from file bytes
# ---------------------------------------------------------------------------

def extract_text_from_upload(content: bytes, filename: str) -> str:
    """Return plain text from an uploaded file (PDF or text)."""
    lower = filename.lower()
    if lower.endswith(".pdf"):
        if not _HAS_PYPDF2:
            raise ValueError(
                "PDF support requires PyPDF2. Install with: pip install PyPDF2"
            )
        import io
        reader = PdfReader(io.BytesIO(content))
        pages = [page.extract_text() or "" for page in reader.pages]
        text = "\n".join(pages).strip()
        if not text:
            raise ValueError(
                "Could not extract text from PDF. The file may be scanned/image-based."
            )
        return text
    # Treat everything else as plain text
    try:
        return content.decode("utf-8", errors="replace").strip()
    except Exception:
        raise ValueError(
            f"Unsupported file type: {filename}. Please upload a PDF or text file."
        )


# ---------------------------------------------------------------------------
# 2. Structured medical-data extraction
# ---------------------------------------------------------------------------

def extract_medical_data(text: str) -> dict[str, Any]:
    """
    Extract structured medical data from document text.

    Returns::

        {
            "patient_name": str | None,
            "age": int | None,
            "sex": str | None,
            "symptoms": [str, ...],
            "diseases": [str, ...],
            "clinical_values": { "MAP": float, ... }
        }

    Tries OpenAI first; falls back to regex extraction.
    """
    if not text or not text.strip():
        raise ValueError("Document is empty — nothing to extract.")
    api_key = os.environ.get("OPENAI_API_KEY")
    if api_key:
        try:
            return _extract_with_llm(text, api_key)
        except Exception:
            pass
    return _extract_with_regex(text)


# ---------------------------------------------------------------------------
# 2a. LLM-powered extraction
# ---------------------------------------------------------------------------

_LLM_PROMPT = """Extract structured medical data from this clinical document.
Return ONLY valid JSON (no markdown fences) with these fields:
{
  "patient_name": "string or null",
  "age": number or null,
  "sex": "M" or "F" or null,
  "symptoms": ["list of symptoms found"],
  "diseases": ["list of diseases/diagnoses found"],
  "clinical_values": {
    "MAP": number or null,
    "SOFA": number or null,
    "creatinine": number or null,
    "GCS": number or null,
    "lactate": number or null,
    "heart_rate": number or null,
    "temperature": number or null,
    "respiratory_rate": number or null
  }
}

Rules:
- Symptoms as simple lowercase terms (e.g. "fever", "hypotension")
- Diseases as proper medical names (e.g. "Sepsis", "Pneumonia")
- Only include clinical values explicitly mentioned with numeric values
- Use null / empty list when not found

Document:
"""


def _extract_with_llm(text: str, api_key: str) -> dict[str, Any]:
    from openai import OpenAI

    client = OpenAI(api_key=api_key)
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a medical data extraction system. "
                    "Return only valid JSON, no markdown."
                ),
            },
            {"role": "user", "content": _LLM_PROMPT + text[:4000]},
        ],
        max_tokens=600,
        temperature=0.1,
    )
    raw = (response.choices[0].message.content or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    data = json.loads(raw)
    return _normalize(data)


# ---------------------------------------------------------------------------
# 2b. Regex fallback extraction
# ---------------------------------------------------------------------------

_SYMPTOM_PATTERNS = [
    "fever", "hypotension", "tachycardia", "dyspnea", "cough",
    "fatigue", "nausea", "vomiting", "headache", "chest pain",
    "shortness of breath", "confusion", "dizziness", "chills",
    "abdominal pain", "diarrhea", "oliguria", "altered mental status",
    "wheezing", "sore throat", "myalgia", "malaise", "edema",
]

_DISEASE_PATTERNS: dict[str, str] = {
    "sepsis": "Sepsis",
    "pneumonia": "Pneumonia",
    "type 2 diabetes": "Type 2 Diabetes",
    "diabetes mellitus": "Type 2 Diabetes",
    "diabetes": "Type 2 Diabetes",
    "hypertension": "Hypertension",
    "asthma": "Asthma",
    "copd": "COPD",
    "anemia": "Anemia",
    "osteoarthritis": "Osteoarthritis",
    "anxiety": "Anxiety",
    "meningitis": "Meningitis",
    "urinary tract infection": "Urinary Tract Infection",
    "uti": "Urinary Tract Infection",
    "acute kidney injury": "Acute Kidney Injury",
    "heart failure": "Heart Failure",
    "atrial fibrillation": "Atrial Fibrillation",
}


def _extract_with_regex(text: str) -> dict[str, Any]:
    text_lower = text.lower()

    symptoms = [s for s in _SYMPTOM_PATTERNS if s in text_lower]

    seen_diseases: set[str] = set()
    diseases: list[str] = []
    for pattern, name in sorted(
        _DISEASE_PATTERNS.items(), key=lambda x: -len(x[0])
    ):
        if pattern in text_lower and name not in seen_diseases:
            diseases.append(name)
            seen_diseases.add(name)

    cv: dict[str, float] = {}
    for label, key in [
        (r"(?:MAP|mean arterial pressure)", "MAP"),
        (r"SOFA", "SOFA"),
        (r"creatinine", "creatinine"),
        (r"(?:GCS|glasgow coma scale?)", "GCS"),
        (r"lactate", "lactate"),
        (r"(?:heart rate|HR|pulse)", "heart_rate"),
        (r"(?:temperature|temp)", "temperature"),
        (r"(?:respiratory rate|RR)", "respiratory_rate"),
    ]:
        m = re.search(
            rf"{label}[:\s]*(\d+(?:\.\d+)?)", text, re.IGNORECASE
        )
        if m:
            cv[key] = float(m.group(1))

    patient_name = None
    m = re.search(
        r"(?:patient(?:\s+name)?|name)[:\s]+([A-Z][a-z]+ [A-Z][a-z]+)", text
    )
    if m:
        patient_name = m.group(1)

    age = None
    m = re.search(r"(\d{1,3})\s*[-–]?\s*(?:year|yr|y/?o)", text, re.IGNORECASE)
    if m:
        age = int(m.group(1))
    elif re.search(r"age", text, re.IGNORECASE):
        m2 = re.search(r"age[:\s]*(\d{1,3})", text, re.IGNORECASE)
        if m2:
            age = int(m2.group(1))

    sex = None
    if re.search(r"\b(?:male|man)\b", text_lower):
        sex = "M"
    elif re.search(r"\b(?:female|woman)\b", text_lower):
        sex = "F"

    return _normalize({
        "patient_name": patient_name,
        "age": age,
        "sex": sex,
        "symptoms": symptoms,
        "diseases": diseases,
        "clinical_values": cv,
    })


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalize(data: dict) -> dict[str, Any]:
    return {
        "patient_name": data.get("patient_name"),
        "age": data.get("age"),
        "sex": data.get("sex"),
        "symptoms": [
            s.lower().strip() for s in (data.get("symptoms") or []) if s
        ],
        "diseases": [d.strip() for d in (data.get("diseases") or []) if d],
        "clinical_values": {
            k: v
            for k, v in (data.get("clinical_values") or {}).items()
            if v is not None
        },
    }
