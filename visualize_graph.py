#!/usr/bin/env python3
"""
Neo4j healthcare graph visualization using pyvis.

Connects to Neo4j, retrieves nodes and relationships dynamically, and produces
an interactive HTML graph (graph.html) with color-coded nodes, shapes, edge labels,
and hover tooltips. Limits to 50 nodes and 50 relationships (configurable).
Suitable for university project presentation; run from terminal or VS Code.
"""
import webbrowser
from pathlib import Path

from pyvis.network import Network

from neo4j_config import NEO4J_DATABASE
from neo4j_connect import get_driver

# ---------------------------------------------------------------------------
# Configuration: limits and output path
# ---------------------------------------------------------------------------

# Maximum nodes and relationships to include (increase for larger graphs)
NODE_LIMIT = 150
REL_LIMIT = 150
OUTPUT_HTML = Path(__file__).resolve().parent / "graph.html"

# Node colors by label (pyvis accepts color names or hex)
NODE_COLORS = {
    "Patient": "blue",
    "Doctor": "green",
    "Disease": "red",
    "Hospital": "purple",
    "Appointment": "orange",
}
DEFAULT_NODE_COLOR = "gray"

# Node shapes by label (optional feature: circle, square, triangle, etc.)
NODE_SHAPES = {
    "Patient": "dot",        # circle
    "Doctor": "square",
    "Disease": "triangle",
    "Hospital": "diamond",
    "Appointment": "star",
}
DEFAULT_NODE_SHAPE = "dot"

# Property keys to show in hover tooltip (order preserved)
TOOLTIP_KEYS = ["id", "name", "age", "sex", "specialty", "date", "diagnosed_on", "icd10"]


# ---------------------------------------------------------------------------
# 1. connect_to_neo4j() – returns a driver; includes error handling
# ---------------------------------------------------------------------------

def connect_to_neo4j():
    """
    Connect to the Neo4j database using the official Python driver.

    Uses credentials from neo4j_config (env / .env). Verifies connectivity
    before returning. Caller must close the driver when done.

    :return: Neo4j Driver instance
    :raises: RuntimeError if connection fails (with details)
    """
    try:
        driver = get_driver()
        driver.verify_connectivity()
        return driver
    except Exception as e:
        raise RuntimeError(f"Neo4j connection failed: {e}") from e


# ---------------------------------------------------------------------------
# 2. get_graph_data() – retrieves nodes and relationships dynamically
# ---------------------------------------------------------------------------

def _run_query(driver, cypher: str, parameters: dict | None = None):
    """Run a Cypher query with the given driver; returns list of record dicts."""
    params = parameters or {}
    with driver.session(database=NEO4J_DATABASE) as session:
        result = session.run(cypher, params)
        return [dict(record) for record in result]


def _query_relationships(driver, limit: int):
    """
    Fetch up to `limit` relationships with full node data.
    Tries elementId() (Neo4j 5+) then id() (Neo4j 4.x) for compatibility.
    """
    params = {"limit": limit}
    cypher_v5 = """
    MATCH (a)-[r]->(b)
    RETURN
        elementId(a) AS src_id,
        coalesce(labels(a)[0], 'Unknown') AS src_label,
        properties(a) AS src_props,
        type(r) AS rel_type,
        elementId(b) AS tgt_id,
        coalesce(labels(b)[0], 'Unknown') AS tgt_label,
        properties(b) AS tgt_props
    LIMIT $limit
    """
    cypher_v4 = """
    MATCH (a)-[r]->(b)
    RETURN
        toString(id(a)) AS src_id,
        coalesce(labels(a)[0], 'Unknown') AS src_label,
        properties(a) AS src_props,
        type(r) AS rel_type,
        toString(id(b)) AS tgt_id,
        coalesce(labels(b)[0], 'Unknown') AS tgt_label,
        properties(b) AS tgt_props
    LIMIT $limit
    """
    try:
        return _run_query(driver, cypher_v5, params)
    except Exception:
        return _run_query(driver, cypher_v4, params)


def get_graph_data(driver, node_limit: int = NODE_LIMIT, rel_limit: int = REL_LIMIT):
    """
    Retrieve nodes and relationships from the database dynamically (not hard-coded).

    Fetches up to `rel_limit` relationships and the nodes they connect. If
    unique nodes exceed `node_limit`, trims to the first `node_limit` and
    keeps only relationships whose endpoints are in that set.

    :param driver: Neo4j driver from connect_to_neo4j()
    :param node_limit: maximum nodes to include
    :param rel_limit: maximum relationships to fetch
    :return: tuple (nodes_list, relationships_list)
             nodes_list: list of dicts with keys id, label, name, props (for tooltip)
             relationships_list: list of dicts with keys src_id, tgt_id, type
    """
    rows = _query_relationships(driver, rel_limit)
    if not rows:
        return [], []

    # Build unique nodes with full properties (id, label, name, props)
    nodes_dict = {}
    for r in rows:
        for nid, nlabel, props in [
            (r.get("src_id"), r.get("src_label"), r.get("src_props") or {}),
            (r.get("tgt_id"), r.get("tgt_label"), r.get("tgt_props") or {}),
        ]:
            if nid and nid not in nodes_dict:
                name = (props or {}).get("name") or (props or {}).get("id") or str(nid)
                nodes_dict[nid] = {"id": nid, "label": nlabel or "Unknown", "name": str(name), "props": props or {}}

    # Optionally trim to node_limit (keep first N by insertion order)
    node_ids = list(nodes_dict.keys())
    if len(node_ids) > node_limit:
        keep_ids = set(node_ids[:node_limit])
        nodes_dict = {nid: nodes_dict[nid] for nid in keep_ids}

    # Build relationship list; only include edges between kept nodes
    keep_ids = set(nodes_dict.keys())
    relationships = []
    for r in rows:
        src, tgt = r.get("src_id"), r.get("tgt_id")
        if src and tgt and src in keep_ids and tgt in keep_ids:
            relationships.append({"src_id": src, "tgt_id": tgt, "type": r.get("rel_type") or ""})
            if len(relationships) >= rel_limit:
                break

    nodes_list = list(nodes_dict.values())
    return nodes_list, relationships


# ---------------------------------------------------------------------------
# 3. build_graph() – builds the pyvis network with colors, shapes, labels
# ---------------------------------------------------------------------------

def _tooltip_html(props: dict, node_label: str) -> str:
    """Build HTML tooltip string from node properties (age, specialty, date, icd10, etc.)."""
    parts = [f"<b>{node_label}</b>"]
    for key in TOOLTIP_KEYS:
        if key in props and props[key] is not None and props[key] != "":
            parts.append(f"{key}: {props[key]}")
    return "<br>".join(parts) if len(parts) > 1 else parts[0]


def _node_color(label: str) -> str:
    """Return color for a node label."""
    return NODE_COLORS.get(label, DEFAULT_NODE_COLOR)


def _node_shape(label: str) -> str:
    """Return shape for a node label (circle, square, triangle, etc.)."""
    return NODE_SHAPES.get(label, DEFAULT_NODE_SHAPE)


def build_graph(nodes: list, relationships: list) -> Network:
    """
    Build the pyvis Network from node and relationship lists.

    - Nodes: labeled with `name`, color by type, shape by type, tooltip with
      properties (age, specialty, date, icd10, etc.)
    - Edges: labeled with relationship type
    - Interactivity: nodes draggable, zoom in/out (default in pyvis)
    """
    net = Network(
        height="95vh",
        width="100%",
        bgcolor="#f5f5f5",
        font_color="#222",
        directed=True,
    )
    # Larger canvas; bigger nodes/labels; physics tuned so graph is spread out and easy to see
    net.set_options("""
    var options = {
      "nodes": {
        "font": { "size": 20, "face": "arial" },
        "size": 28,
        "borderWidth": 2,
        "borderWidthSelected": 3,
        "shadow": true,
        "scaling": { "min": 20, "max": 40 }
      },
      "edges": {
        "font": { "size": 14, "align": "middle", "strokeWidth": 0 },
        "width": 1.5,
        "arrows": "to",
        "smooth": { "type": "cubicBezier" }
      },
      "physics": {
        "enabled": true,
        "repulsion": { "nodeDistance": 220, "centralGravity": 0.03, "springLength": 180 },
        "solver": "repulsion"
      },
      "interaction": { "dragNodes": true, "zoomView": true, "dragView": true, "hover": true }
    }
    """)

    for node in nodes:
        nid = node["id"]
        label = node["label"]
        name = node.get("name") or nid
        props = node.get("props") or {}
        title = _tooltip_html(props, label)
        net.add_node(
            nid,
            label=str(name),
            color=_node_color(label),
            shape=_node_shape(label),
            title=title,
            size=28,
        )

    for rel in relationships:
        net.add_edge(
            rel["src_id"],
            rel["tgt_id"],
            label=rel.get("type") or "",
            title=rel.get("type") or "",
        )

    return net


# ---------------------------------------------------------------------------
# 4. Table of contents / legend sidebar (injected into HTML)
# ---------------------------------------------------------------------------

def _legend_html() -> str:
    """Return HTML for the sidebar: title, node legend, relationship types, how to use."""
    return """
    <aside class="graph-legend">
      <h2>Healthcare Graph</h2>
      <nav class="toc">
        <h3>Table of contents</h3>
        <ul>
          <li><a href="#nodes">Node types</a></li>
          <li><a href="#relationships">Relationships</a></li>
          <li><a href="#howto">How to use</a></li>
        </ul>
      </nav>
      <section id="nodes">
        <h3>Node types</h3>
        <ul class="node-legend">
          <li><span class="swatch blue dot"></span> Patient</li>
          <li><span class="swatch green square"></span> Doctor</li>
          <li><span class="swatch red triangle"></span> Disease</li>
          <li><span class="swatch purple diamond"></span> Hospital</li>
          <li><span class="swatch orange star"></span> Appointment</li>
          <li><span class="swatch gray dot"></span> Other</li>
        </ul>
      </section>
      <section id="relationships">
        <h3>Relationships</h3>
        <ul class="rel-legend">
          <li>HAS_DISEASE</li>
          <li>TREATS</li>
          <li>VISITS</li>
          <li>HAS_APPOINTMENT</li>
          <li>AT_HOSPITAL</li>
        </ul>
      </section>
      <section id="howto">
        <h3>How to use</h3>
        <ul>
          <li><strong>Drag</strong> nodes to rearrange</li>
          <li><strong>Scroll</strong> to zoom in/out</li>
          <li><strong>Drag</strong> background to pan</li>
          <li><strong>Hover</strong> a node for details (age, specialty, date, etc.)</li>
        </ul>
      </section>
    </aside>
    """


def _inject_legend_into_html(path: Path) -> None:
    """Read graph.html, inject sidebar + layout, write back."""
    path = Path(path)
    html = path.read_text(encoding="utf-8")
    legend = _legend_html()
    # Inject layout styles into <head> (before </head>)
    layout_css = """
        <style>
          .graph-wrapper { display: flex; min-height: 100vh; }
          .graph-legend {
            width: 280px; min-width: 280px; padding: 1rem 1.25rem; background: #2d3748; color: #e2e8f0;
            font-family: system-ui, -apple-system, sans-serif; font-size: 14px; overflow-y: auto;
          }
          .graph-legend h2 { margin: 0 0 1rem; font-size: 1.25rem; color: #fff; }
          .graph-legend h3 { margin: 1rem 0 0.5rem; font-size: 0.9rem; color: #a0aec0; }
          .graph-legend ul { margin: 0; padding-left: 1.25rem; }
          .graph-legend li { margin: 0.35rem 0; }
          .graph-legend a { color: #90cdf4; text-decoration: none; }
          .graph-legend a:hover { text-decoration: underline; }
          .node-legend { list-style: none; padding-left: 0; }
          .node-legend li { display: flex; align-items: center; gap: 0.5rem; }
          .swatch { display: inline-block; width: 14px; height: 14px; border-radius: 50%; border: 1px solid rgba(255,255,255,0.3); }
          .swatch.blue { background: blue; }
          .swatch.green { background: green; }
          .swatch.red { background: red; }
          .swatch.purple { background: purple; }
          .swatch.orange { background: orange; }
          .swatch.gray { background: gray; }
          .swatch.square { border-radius: 2px; }
          .swatch.triangle { border-radius: 0; clip-path: polygon(50% 0%, 100% 100%, 0% 100%); }
          .swatch.diamond { border-radius: 0; clip-path: polygon(50% 0%, 100% 50%, 50% 100%, 0% 50%); }
          .swatch.star { border-radius: 0; clip-path: polygon(50% 0%, 61% 35%, 98% 35%, 68% 57%, 79% 91%, 50% 70%, 21% 91%, 32% 57%, 2% 35%, 39% 35%); }
          .rel-legend { font-size: 0.85rem; color: #cbd5e0; }
          .graph-main { flex: 1; display: flex; flex-direction: column; min-width: 0; }
          .graph-main .card { flex: 1; margin: 0; }
          .graph-main #mynetwork { height: 100vh !important; }
        </style>
    """
    if "</head>" in html:
        html = html.replace("</head>", layout_css + "\n    </head>", 1)
    # Wrap body content: add sidebar and wrap existing content in .graph-main
    if "<body>" in html and "</body>" in html:
        html = html.replace("<body>", "<body>\n        <div class=\"graph-wrapper\">\n            " + legend + "\n            <div class=\"graph-main\">", 1)
        html = html.replace("</body>", "            </div>\n        </div>\n    </body>", 1)
    path.write_text(html, encoding="utf-8")


# ---------------------------------------------------------------------------
# 5. show_graph() – saves HTML and opens in default browser
# ---------------------------------------------------------------------------

def show_graph(net: Network, path: Path | None = None) -> None:
    """
    Save the pyvis network to an HTML file and open it in the default browser.
    Injects a table-of-contents sidebar (legend, relationships, how to use).
    """
    path = path or OUTPUT_HTML
    path = Path(path)
    net.save_graph(str(path))
    _inject_legend_into_html(path)
    url = path.as_uri()
    webbrowser.open(url)
    print(f"Graph saved as: {path}")
    print(f"Opened in browser: {url}")


# ---------------------------------------------------------------------------
# main() – run full workflow with status messages
# ---------------------------------------------------------------------------

def main():
    """
    Run the complete graph visualization workflow:
    connect → retrieve data → build graph → save and open in browser.
    Prints status messages for each step (suitable for VS Code / terminal).
    """
    print("Connecting to Neo4j database...")
    try:
        driver = connect_to_neo4j()
    except RuntimeError as e:
        print(f"Error: {e}")
        return
    print("Connected to database.")

    print("Retrieving nodes and relationships...")
    try:
        nodes, relationships = get_graph_data(driver, node_limit=NODE_LIMIT, rel_limit=REL_LIMIT)
    finally:
        driver.close()
    print(f"Retrieved {len(nodes)} nodes and {len(relationships)} relationships.")

    if not nodes and not relationships:
        print("No data in the database. Add data (e.g. run seed_data.py) and try again.")
        return

    print("Building interactive graph (colors, shapes, labels, tooltips)...")
    net = build_graph(nodes, relationships)
    print("Saving visualization and opening in browser...")
    show_graph(net)
    print("Done. You can zoom, drag nodes, and hover for details.")


if __name__ == "__main__":
    main()
