"""
Short educational explanations for protocol recommendations (Disease → Drug → Procedure → FollowUp).
Used by the dashboard when a user clicks a node to show why each treatment is recommended.
Placeholder text; can be replaced with references to real guidelines (e.g. ESC, ICD-10).
"""
# Why this drug/procedure is recommended (evidence-based protocol, best practice, ICD10)
# Keys: node id (e.g. D1, DRUG1, PROC1, FU1)
PROTOCOL_EXPLANATIONS = {
    "D1": {
        "name": "Hypertension",
        "icd10": "I10",
        "why_drug": "ACE inhibitors (e.g. Lisinopril) are first-line for hypertension per ESC/ESH guidelines; they reduce cardiovascular risk and are evidence-based for I10.",
        "why_procedure": "BP monitoring is recommended to titrate therapy and confirm control (target <140/90 mmHg).",
        "references": "ESC/ESH Hypertension Guidelines; ICD-10 I10.",
    },
    "D2": {
        "name": "Type 2 Diabetes",
        "icd10": "E11",
        "why_drug": "Metformin is first-line per ADA/EASD consensus; improves insulin sensitivity and has strong evidence for E11.",
        "why_procedure": "HbA1c testing every 3 months is standard to monitor glycaemic control and adjust treatment.",
        "references": "ADA Standards of Care; ICD-10 E11.",
    },
    "D3": {
        "name": "Asthma",
        "icd10": "J45",
        "why_drug": "Inhaled corticosteroids are controller therapy per GINA guidelines; reduce exacerbations and inflammation in J45.",
        "why_procedure": "Spirometry confirms diagnosis and monitors lung function; recommended at diagnosis and periodically.",
        "references": "GINA Guidelines; ICD-10 J45.",
    },
    "D4": {
        "name": "Osteoarthritis",
        "icd10": "M17",
        "why_drug": "NSAIDs are first-line for pain and inflammation in osteoarthritis per NICE; use with gastroprotection when indicated.",
        "why_procedure": "Joint imaging (X-ray/MRI) supports diagnosis and severity; guides need for referral or surgery.",
        "references": "NICE Osteoarthritis; ICD-10 M17.",
    },
    "D5": {
        "name": "Anxiety",
        "icd10": "F41",
        "why_drug": "SSRIs are first-line pharmacological treatment per NICE for generalised anxiety (F41); evidence-based and well tolerated.",
        "why_procedure": "Psychological interventions (e.g. CBT) are recommended; counseling supports adherence and monitoring.",
        "references": "NICE Anxiety; ICD-10 F41.",
    },
    "DRUG1": "Lisinopril: ACE inhibitor; first-line for hypertension (I10). Evidence-based, reduces CV events (ESC/ESH).",
    "DRUG2": "Metformin: First-line for type 2 diabetes (E11). ADA/EASD consensus; improves glycaemic control and outcomes.",
    "DRUG3": "Inhaled corticosteroid: Controller for asthma (J45). GINA guidelines; reduces exacerbations and inflammation.",
    "DRUG4": "NSAIDs: First-line for osteoarthritis pain (M17). NICE; use with PPI if GI risk.",
    "DRUG5": "SSRI: First-line for anxiety (F41). NICE; evidence-based for GAD.",
    "PROC1": "BP monitoring: Standard for hypertension (I10). Titrate therapy to target; ESC/ESH.",
    "PROC2": "HbA1c test: Quarterly for diabetes (E11). ADA standard; monitors control.",
    "PROC3": "Spirometry: For asthma (J45). GINA; confirms diagnosis and monitors function.",
    "PROC4": "Joint imaging: For osteoarthritis (M17). NICE; severity and referral decisions.",
    "PROC5": "Counseling: For anxiety (F41). NICE; supports therapy and adherence.",
}

# Default message when no explanation is defined
DEFAULT_WHY_BETTER = (
    "The recommended treatment is based on evidence-based protocol, "
    "ICD-10 guidelines, and best practice. Following the protocol improves outcomes and reduces risk."
)


def get_explanation(node_id: str) -> dict:
    """Return explanation dict for a node (Disease, Drug, Procedure). For UI display on click."""
    if not node_id:
        return {"name": "", "text": DEFAULT_WHY_BETTER, "references": ""}
    raw = PROTOCOL_EXPLANATIONS.get(node_id)
    if isinstance(raw, dict):
        parts = []
        if raw.get("why_drug"):
            parts.append(f"<strong>Drug:</strong> {raw['why_drug']}")
        if raw.get("why_procedure"):
            parts.append(f"<strong>Procedure:</strong> {raw['why_procedure']}")
        text = "<br>".join(parts) if parts else raw.get("references", DEFAULT_WHY_BETTER)
        return {
            "name": raw.get("name", node_id),
            "icd10": raw.get("icd10", ""),
            "text": text,
            "references": raw.get("references", ""),
        }
    if isinstance(raw, str):
        return {"name": node_id, "text": raw, "references": ""}
    return {"name": node_id, "text": DEFAULT_WHY_BETTER, "references": ""}


def get_why_recommended_better() -> str:
    """Short line for violation edge tooltips: why the recommended option is better."""
    return "Why recommended is better: evidence-based protocol, ICD-10 guidelines, and best practice; improves outcomes and reduces risk."
