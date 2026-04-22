"""
Load MIMIC-style sample patients from data/mimic_sample_patients.json into Neo4j.

- Idempotent: each record is keyed by integer Patient.mimic_subject_id; re-run skips existing.
- Does not clear or alter other data. Expects seed_data (or equivalent) to have Doctor DOC1, Hospital H1, Disease D1–D8.
- Sets Patient.source = "mimic_sample" and stores mimic_subject_id for deduplication and filtering.

Cypher to list only MIMIC samples:  MATCH (p:Patient) WHERE p.source = "mimic_sample" RETURN p
To remove samples:            MATCH (p:Patient) WHERE p.source = "mimic_sample" DETACH DELETE p
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from neo4j_ops import get_next_patient_id, run_query

DATA_FILE = Path(__file__).resolve().parent / "data" / "mimic_sample_patients.json"
SOURCE_TAG = "mimic_sample"
DEFAULT_DOCTOR = "DOC1"
DEFAULT_HOSPITAL = "H1"


def _load_json(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _already_imported(mimic_subject_id: int) -> bool:
    rows = run_query(
        "MATCH (p:Patient {mimic_subject_id: $mid}) RETURN p.id AS id LIMIT 1",
        {"mid": mimic_subject_id},
    )
    return bool(rows)


def _require_seed_graph():
    ok = run_query(
        """
        OPTIONAL MATCH (d:Doctor {id: $doc})
        OPTIONAL MATCH (h:Hospital {id: $hid})
        RETURN d IS NOT NULL AS has_doc, h IS NOT NULL AS has_hosp
        """,
        {"doc": DEFAULT_DOCTOR, "hid": DEFAULT_HOSPITAL},
    )
    if not ok or not (ok[0].get("has_doc") and ok[0].get("has_hosp")):
        raise RuntimeError(
            "Missing Doctor DOC1 or Hospital H1. Run seed_data.seed() (or your usual seed) before importing MIMIC samples."
        )


def _merge_symptom(pid: str, name: str) -> None:
    sym_id = "SYM_" + "".join(c if c.isalnum() else "_" for c in name.lower())
    run_query(
        "MERGE (s:Symptom {id: $sym_id}) SET s.name = $name "
        "WITH s MATCH (p:Patient {id: $pid}) MERGE (p)-[:HAS_SYMPTOM]->(s)",
        {"sym_id": sym_id, "name": name, "pid": pid},
    )


def _merge_disease(pid: str, disease_id: str, diagnosed_on: str | None) -> None:
    run_query(
        """
        MATCH (p:Patient {id: $pid}), (d:Disease {id: $did})
        MERGE (p)-[r:HAS_DISEASE]->(d)
        SET r.diagnosed_on = coalesce($dod, r.diagnosed_on)
        RETURN 1
        """,
        {"pid": pid, "did": disease_id, "dod": diagnosed_on},
    )


def _create_clinical_state(pid: str, mimic_subject_id: int, cs: dict) -> None:
    if not cs:
        return
    cs_id = f"CS_MIMIC_{mimic_subject_id}"
    keys = ("sofa_score", "map", "gcs", "creatinine", "lactate")
    set_parts = []
    params: dict = {"cs_id": cs_id, "pid": pid}
    for k in keys:
        if cs.get(k) is not None:
            set_parts.append(f"c.{k} = ${k}")
            params[k] = cs[k]
    if not set_parts:
        return
    run_query(
        f"CREATE (c:ClinicalState {{id: $cs_id}}) SET {', '.join(set_parts)} "
        f"WITH c MATCH (p:Patient {{id: $pid}}) CREATE (p)-[:HAS_CLINICAL_STATE]->(c)",
        params,
    )


def import_record(rec: dict) -> tuple[str, str]:
    """
    Insert one dataset record. Returns (status, patient_id or message).
    status is 'skipped' | 'created' | 'error'.
    """
    mid = rec["mimic_subject_id"]
    try:
        if _already_imported(mid):
            rows = run_query(
                "MATCH (p:Patient {mimic_subject_id: $mid}) RETURN p.id AS id LIMIT 1",
                {"mid": mid},
            )
            pid = rows[0]["id"] if rows else "?"
            return "skipped", pid

        pid = get_next_patient_id()
        name = rec.get("display_name") or f"MIMIC subject {mid}"
        age = rec.get("anchor_age")
        sex = rec.get("gender")

        run_query(
            """
            CREATE (p:Patient {
              id: $pid,
              name: $name,
              age: $age,
              sex: $sex,
              source: $src,
              mimic_subject_id: $mid
            })
            RETURN p.id AS id
            """,
            {"pid": pid, "name": name, "age": age, "sex": sex, "src": SOURCE_TAG, "mid": mid},
        )

        for d in rec.get("diseases") or []:
            did = d.get("disease_id")
            if not did:
                continue
            _merge_disease(pid, did, d.get("diagnosed_on"))

        for s in rec.get("symptoms") or []:
            if s:
                _merge_symptom(pid, str(s))

        run_query(
            """
            MATCH (p:Patient {id: $pid}), (doc:Doctor {id: $doc})
            MERGE (p)-[:VISITS]->(doc)
            RETURN 1
            """,
            {"pid": pid, "doc": DEFAULT_DOCTOR},
        )

        appt_id = f"AMIMIC{mid}"
        appt_date = (rec.get("encounter") or {}).get("date") or "2132-01-01"
        run_query(
            """
            MATCH (p:Patient {id: $pid}), (h:Hospital {id: $hid})
            MERGE (a:Appointment {id: $aid})
            SET a.date = $adate,
                a.reason = $reason,
                a.status = 'completed'
            MERGE (p)-[:HAS_APPOINTMENT]->(a)
            MERGE (a)-[:AT_HOSPITAL]->(h)
            RETURN 1
            """,
            {
                "pid": pid,
                "hid": DEFAULT_HOSPITAL,
                "aid": appt_id,
                "adate": appt_date,
                "reason": "MIMIC sample follow-up",
            },
        )

        enc = rec.get("encounter") or {}
        eid = f"EMIMIC{mid}"
        run_query(
            """
            MATCH (p:Patient {id: $pid}), (doc:Doctor {id: $doc})
            CREATE (e:Encounter {id: $eid, date: $edate, type: $etype, notes: $enotes})
            CREATE (p)-[:HAS_ENCOUNTER]->(e)
            CREATE (e)-[:PERFORMED_BY]->(doc)
            RETURN 1
            """,
            {
                "pid": pid,
                "doc": DEFAULT_DOCTOR,
                "eid": eid,
                "edate": enc.get("date") or appt_date,
                "etype": enc.get("type") or "outpatient",
                "enotes": enc.get("notes") or "",
            },
        )

        for i, lab in enumerate(rec.get("labs") or []):
            lid = f"LABMIMIC{mid}_{i}"
            run_query(
                """
                MATCH (e:Encounter {id: $eid})
                CREATE (l:Lab {
                  id: $lid,
                  name: $name,
                  result_value: $rv,
                  unit: $unit,
                  normal_range: $nr,
                  status: $st,
                  date: $dt
                })
                CREATE (e)-[:ORDERED_LAB]->(l)
                RETURN 1
                """,
                {
                    "eid": eid,
                    "lid": lid,
                    "name": lab.get("name") or "Lab",
                    "rv": str(lab.get("result_value") or ""),
                    "unit": lab.get("unit") or "",
                    "nr": lab.get("normal_range") or "",
                    "st": lab.get("status") or "final",
                    "dt": lab.get("date") or enc.get("date") or appt_date,
                },
            )

        _create_clinical_state(pid, mid, rec.get("clinical_state") or {})

        return "created", pid
    except Exception as e:
        return "error", str(e)


def import_mimic_samples(path: Path | None = None, dry_run: bool = False) -> dict:
    """
    Import all records from JSON. Returns summary counts.
    """
    path = path or DATA_FILE
    data = _load_json(path)
    records = data.get("records") or []
    summary = {"created": 0, "skipped": 0, "errors": 0, "details": []}

    if dry_run:
        summary["details"].append(f"DRY RUN: would process {len(records)} record(s) from {path}")
        return summary

    _require_seed_graph()

    for rec in records:
        status, info = import_record(rec)
        summary["details"].append({"mimic_subject_id": rec.get("mimic_subject_id"), "status": status, "info": info})
        if status == "created":
            summary["created"] += 1
        elif status == "skipped":
            summary["skipped"] += 1
        else:
            summary["errors"] += 1

    return summary


def main():
    parser = argparse.ArgumentParser(description="Import MIMIC sample patients from JSON into Neo4j.")
    parser.add_argument(
        "--file",
        type=Path,
        default=DATA_FILE,
        help=f"Path to mimic_sample_patients JSON (default: {DATA_FILE})",
    )
    parser.add_argument("--dry-run", action="store_true", help="Validate file and print counts only.")
    args = parser.parse_args()
    if not args.file.is_file():
        raise SystemExit(f"File not found: {args.file}")

    summary = import_mimic_samples(path=args.file, dry_run=args.dry_run)
    print(json.dumps(summary, indent=2))
    if summary.get("errors"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
