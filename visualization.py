"""
Interactive compliance visualization: pyvis graph with violations in red, compliant in green.
Reuses neo4j_connect and ai_compliance; hover shows actual vs recommended treatment.
Run from terminal or VS Code: python3 visualization.py
Output: compliance_graph.html (opens in browser).
"""
import webbrowser
from pathlib import Path

from pyvis.network import Network

from neo4j_config import NEO4J_DATABASE
from neo4j_connect import get_driver, run_query
from ai_compliance import run_compliance_check

OUTPUT_HTML = Path(__file__).resolve().parent / "compliance_graph.html"
NODE_LIMIT = 150
REL_LIMIT = 150

NODE_COLORS = {
    "Patient": "blue",
    "Doctor": "green",
    "Disease": "red",
    "Hospital": "purple",
    "Appointment": "orange",
    "Drug": "#ffaa00",
    "Procedure": "#00aacc",
    "FollowUp": "#888888",
}
DEFAULT_NODE_COLOR = "gray"

EDGE_COLOR_VIOLATION = "#cc0000"
EDGE_COLOR_COMPLIANT = "#00aa00"
EDGE_COLOR_DEFAULT = "#888888"

TREATMENT_REL_TYPES = {"HAS_DISEASE", "TREATED_WITH", "HAD_PROCEDURE", "RECOMMENDED_DRUG", "RECOMMENDED_PROCEDURE", "FOLLOW_UP"}


def _run_query_driver(driver, cypher: str, params: dict | None = None):
    params = params or {}
    with driver.session(database=NEO4J_DATABASE) as session:
        result = session.run(cypher, params)
        return [dict(record) for record in result]


def _get_graph_rows(driver, limit: int):
    params = {"limit": limit}
    cypher_v5 = """
    MATCH (a)-[r]->(b)
    RETURN elementId(a) AS src_id, coalesce(labels(a)[0], 'Unknown') AS src_label, properties(a) AS src_props,
           type(r) AS rel_type,
           elementId(b) AS tgt_id, coalesce(labels(b)[0], 'Unknown') AS tgt_label, properties(b) AS tgt_props
    LIMIT $limit
    """
    cypher_v4 = """
    MATCH (a)-[r]->(b)
    RETURN toString(id(a)) AS src_id, coalesce(labels(a)[0], 'Unknown') AS src_label, properties(a) AS src_props,
           type(r) AS rel_type,
           toString(id(b)) AS tgt_id, coalesce(labels(b)[0], 'Unknown') AS tgt_label, properties(b) AS tgt_props
    LIMIT $limit
    """
    try:
        return _run_query_driver(driver, cypher_v5, params)
    except Exception:
        return _run_query_driver(driver, cypher_v4, params)


def _build_violation_set(compliance_result):
    """Build set of (src_id, tgt_id, rel_type) and a map of (pid, did/rel_type) -> tooltip text for violations."""
    violation_edges = set()
    violation_tooltips = {}
    for r in compliance_result.get("patients_with_violations", []):
        pid, did = r.get("patient_id"), r.get("disease_id")
        if not pid:
            continue
        violation_edges.add((pid, did, "HAS_DISEASE"))
        violation_tooltips[(pid, did, "HAS_DISEASE")] = "Recommended drug: {}; procedure: {} | Actual: {}; {}".format(
            r.get("recommended_drug_name") or "—", r.get("recommended_procedure_name") or "—",
            r.get("actual_drug_names") or "—", r.get("actual_procedure_names") or "—"
        )
        for aid in r.get("actual_drug_ids") or []:
            if aid != r.get("recommended_drug_id"):
                violation_edges.add((pid, aid, "TREATED_WITH"))
                violation_tooltips[(pid, aid, "TREATED_WITH")] = "Recommended drug: {} | Actual: {}".format(
                    r.get("recommended_drug_name") or "—", r.get("actual_drug_names") or "—"
                )
        for aid in r.get("actual_procedure_ids") or []:
            if aid != r.get("recommended_procedure_id"):
                violation_edges.add((pid, aid, "HAD_PROCEDURE"))
                violation_tooltips[(pid, aid, "HAD_PROCEDURE")] = "Recommended procedure: {} | Actual: {}".format(
                    r.get("recommended_procedure_name") or "—", r.get("actual_procedure_names") or "—"
                )
    return violation_edges, violation_tooltips


def build_compliance_graph():
    """
    Build pyvis network: nodes color-coded by type; edges red (violation) or green (compliant).
    Hover on treatment edges shows actual vs recommended where applicable.
    """
    driver = get_driver()
    try:
        rows = _get_graph_rows(driver, REL_LIMIT)
    finally:
        driver.close()
    if not rows:
        return None, set()

    compliance = run_compliance_check()
    violation_edges, violation_tooltips = _build_violation_set(compliance)

    nodes_dict = {}
    for r in rows:
        for nid, nlabel, props in [
            (r.get("src_id"), r.get("src_label"), r.get("src_props") or {}),
            (r.get("tgt_id"), r.get("tgt_label"), r.get("tgt_props") or {}),
        ]:
            if nid and nid not in nodes_dict:
                props = props or {}
                nodes_dict[nid] = {"label": nlabel or "Unknown", "name": (props.get("name") or props.get("id") or str(nid)), "id_prop": props.get("id")}
    node_ids = list(nodes_dict.keys())
    if len(node_ids) > NODE_LIMIT:
        keep = set(node_ids[:NODE_LIMIT])
        nodes_dict = {k: v for k, v in nodes_dict.items() if k in keep}

    net = Network(height="95vh", width="100%", bgcolor="#f5f5f5", font_color="#222", directed=True)
    net.set_options("""
    var options = {
      "nodes": { "font": { "size": 18 }, "size": 24, "borderWidth": 2, "shadow": true },
      "edges": { "font": { "size": 12 }, "width": 1.5, "arrows": "to", "smooth": { "type": "cubicBezier" } },
      "physics": { "enabled": true, "repulsion": { "nodeDistance": 200 }, "solver": "repulsion" },
      "interaction": { "dragNodes": true, "zoomView": true, "dragView": true }
    }
    """)

    def node_color(label):
        return NODE_COLORS.get(label, DEFAULT_NODE_COLOR)

    for nid, data in nodes_dict.items():
        net.add_node(nid, label=data["name"], color=node_color(data["label"]), title=f"{data['label']}: {data['name']}")

    keep_ids = set(nodes_dict.keys())
    for r in rows:
        src, tgt = r.get("src_id"), r.get("tgt_id")
        if not src or not tgt or src not in keep_ids or tgt not in keep_ids:
            continue
        rel_type = r.get("rel_type") or ""
        src_id_prop = (nodes_dict.get(src) or {}).get("id_prop")
        tgt_id_prop = (nodes_dict.get(tgt) or {}).get("id_prop")
        key = (src_id_prop, tgt_id_prop, rel_type)
        is_violation = key in violation_edges
        if is_violation:
            color = EDGE_COLOR_VIOLATION
            title = violation_tooltips.get(key, f"{rel_type} (VIOLATION)")
        elif rel_type in TREATMENT_REL_TYPES:
            color = EDGE_COLOR_COMPLIANT
            title = f"{rel_type} (compliant)"
        else:
            color = EDGE_COLOR_DEFAULT
            title = rel_type
        net.add_edge(src, tgt, label=rel_type, color=color, title=title)

    return net, violation_edges


def show_compliance_graph():
    """Build graph, save to compliance_graph.html, open in browser."""
    net, _ = build_compliance_graph()
    if net is None:
        print("No graph data. Run seed_data.py and ensure Neo4j is populated.")
        return
    path = Path(OUTPUT_HTML)
    net.save_graph(str(path))
    webbrowser.open(path.as_uri())
    print(f"Compliance graph saved: {path}")
    print("Red edges = violations; green = compliant treatments. Hover for details.")


if __name__ == "__main__":
    print("Running compliance check and building visualization...")
    show_compliance_graph()
