"""
Interactive compliance dashboard: side menu, stats, protocol explanations, and filtering.
Calls run_compliance_check() dynamically; no hard-coded compliance data.
Generates compliance_dashboard.html with collapsible sidebar, enhanced tooltips, and filters.
Keep neo4j_ops, ai_compliance, api unchanged; this module only reads from them.
"""
import json
import re
import webbrowser
from pathlib import Path

from pyvis.network import Network

from neo4j_config import NEO4J_DATABASE
from neo4j_connect import get_driver
from ai_compliance import run_compliance_check
from neo4j_ops import get_patients_with_diseases
from sepsis_compliance import sync_violations_to_neo4j
from protocol_explanations import get_explanation, get_why_recommended_better, PROTOCOL_EXPLANATIONS

OUTPUT_HTML = Path(__file__).resolve().parent / "compliance_dashboard.html"
NODE_LIMIT = 400
REL_LIMIT = 400

NODE_COLORS = {
    "Patient": "blue",
    "Doctor": "green",
    "Disease": "red",
    "Hospital": "purple",
    "Appointment": "orange",
    "Drug": "#ffaa00",
    "Procedure": "#00aacc",
    "FollowUp": "#888888",
    "ClinicalState": "#e67e22",
    "SepsisGuideline": "#9b59b6",
    "RecommendedAction": "#1abc9c",
    "LabCheck": "#3498db",
    "Violation": "#c53030",
}
DEFAULT_NODE_COLOR = "gray"
EDGE_COLOR_VIOLATION = "#cc0000"
EDGE_COLOR_COMPLIANT = "#00aa00"
EDGE_COLOR_DEFAULT = "#888888"
TREATMENT_REL_TYPES = {"HAS_DISEASE", "TREATED_WITH", "HAD_PROCEDURE", "RECOMMENDED_DRUG", "RECOMMENDED_PROCEDURE", "FOLLOW_UP"}

NODE_TYPES_LIST = ["Patient", "Doctor", "Disease", "Hospital", "Appointment", "Drug", "Procedure", "FollowUp"]
EDGE_TYPES_LIST = [
    "HAS_DISEASE", "TREATS", "VISITS", "HAS_APPOINTMENT", "AT_HOSPITAL",
    "RECOMMENDED_DRUG", "RECOMMENDED_PROCEDURE", "FOLLOW_UP", "TREATED_WITH", "HAD_PROCEDURE",
    "HAS_CLINICAL_STATE", "HAS_VIOLATION",
]


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
    violation_edges = set()
    violation_tooltips = {}
    why_better = get_why_recommended_better()
    for r in compliance_result.get("patients_with_violations", []):
        pid, did = r.get("patient_id"), r.get("disease_id")
        if not pid:
            continue
        violation_edges.add((pid, did, "HAS_DISEASE"))
        violation_tooltips[(pid, did, "HAS_DISEASE")] = (
            "Recommended: drug {}; procedure {} | Actual: {}; {} | {}"
            .format(
                r.get("recommended_drug_name") or "—", r.get("recommended_procedure_name") or "—",
                r.get("actual_drug_names") or "—", r.get("actual_procedure_names") or "—",
                why_better,
            )
        )
        for aid in r.get("actual_drug_ids") or []:
            if aid != r.get("recommended_drug_id"):
                violation_edges.add((pid, aid, "TREATED_WITH"))
                violation_tooltips[(pid, aid, "TREATED_WITH")] = (
                    "Recommended drug: {} | Actual: {} | {}"
                    .format(r.get("recommended_drug_name") or "—", r.get("actual_drug_names") or "—", why_better)
                )
        for aid in r.get("actual_procedure_ids") or []:
            if aid != r.get("recommended_procedure_id"):
                violation_edges.add((pid, aid, "HAD_PROCEDURE"))
                violation_tooltips[(pid, aid, "HAD_PROCEDURE")] = (
                    "Recommended procedure: {} | Actual: {} | {}"
                    .format(r.get("recommended_procedure_name") or "—", r.get("actual_procedure_names") or "—", why_better)
                )
    return violation_edges, violation_tooltips


def _patient_diseases_map():
    """patient_id -> list of disease names for tooltips."""
    rows = get_patients_with_diseases()
    out = {}
    for r in rows:
        pid, pname = r.get("patient_id"), r.get("patient_name")
        dname = r.get("disease_name")
        if not pid:
            continue
        if pid not in out:
            out[pid] = []
        if dname and dname not in out[pid]:
            out[pid].append(dname)
    return out


# Sepsis bundle node ids to exclude from the dashboard (show patients only, not the guideline graph)
_SEPSIS_BUNDLE_IDS = frozenset({
    "SEPSIS_1",
    "ACT_LACTATE", "ACT_ABX", "ACT_CULTURES", "ACT_FLUIDS", "ACT_VASO",
    "LAB_LACTATE", "LAB_CREAT",
    "DRUG_ABX", "DRUG_VASO",
    "PROC_CULTURES", "PROC_FLUIDS",
    "FU_REASSESS",
})


def _is_sepsis_bundle_node(label: str, props: dict) -> bool:
    """True if this node is part of the sepsis guideline bundle (exclude from graph)."""
    if label == "SepsisGuideline":
        return True
    if label == "RecommendedAction":
        return True
    if label == "LabCheck":
        return True
    if label == "Drug" and (props or {}).get("id") in _SEPSIS_BUNDLE_IDS:
        return True
    if label == "Procedure" and (props or {}).get("id") in _SEPSIS_BUNDLE_IDS:
        return True
    if label == "FollowUp" and (props or {}).get("id") in _SEPSIS_BUNDLE_IDS:
        return True
    return False


def build_dashboard_graph():
    """Build pyvis network with enhanced tooltips and node metadata (id_prop, node_type) for dashboard."""
    # Sync sepsis violations to Neo4j so Violation nodes appear in the graph when user asks "what patients have violations"
    try:
        sync_violations_to_neo4j()
    except Exception:
        pass
    driver = get_driver()
    try:
        rows = _get_graph_rows(driver, REL_LIMIT)
    finally:
        driver.close()
    if not rows:
        return None, None, None

    # Exclude sepsis bundle (guideline + actions/labs/drugs/procedures) so only patients and their data are shown
    rows = [
        r for r in rows
        if not _is_sepsis_bundle_node(r.get("src_label") or "", r.get("src_props") or {})
        and not _is_sepsis_bundle_node(r.get("tgt_label") or "", r.get("tgt_props") or {})
    ]

    compliance = run_compliance_check()
    violation_edges, violation_tooltips = _build_violation_set(compliance)
    doctor_scores = {x["doctor_id"]: x for x in compliance.get("doctor_compliance_scores", [])}
    patient_diseases = _patient_diseases_map()

    nodes_dict = {}
    for r in rows:
        for nid, nlabel, props in [
            (r.get("src_id"), r.get("src_label"), r.get("src_props") or {}),
            (r.get("tgt_id"), r.get("tgt_label"), r.get("tgt_props") or {}),
        ]:
            if nid and nid not in nodes_dict:
                props = props or {}
                id_prop = props.get("id")
                name = (props.get("name") or id_prop or str(nid))
                nodes_dict[nid] = {
                    "label": nlabel or "Unknown",
                    "name": str(name),
                    "id_prop": id_prop,
                    "props": props,
                    "patient_diseases": patient_diseases.get(id_prop, []) if nlabel == "Patient" else None,
                    "doctor_score": doctor_scores.get(id_prop) if nlabel == "Doctor" else None,
                }
    node_ids = list(nodes_dict.keys())
    if len(node_ids) > NODE_LIMIT:
        keep = set(node_ids[:NODE_LIMIT])
        nodes_dict = {k: v for k, v in nodes_dict.items() if k in keep}

    net = Network(height="100%", width="100%", bgcolor="#f5f5f5", font_color="#222", directed=True)
    net.set_options("""{
      "nodes": { "font": { "size": 18 }, "size": 24, "borderWidth": 2, "shadow": true },
      "edges": { "font": { "size": 12 }, "width": 2, "arrows": "to", "smooth": { "type": "cubicBezier" } },
      "physics": {
        "enabled": true,
        "solver": "repulsion",
        "repulsion": {
          "nodeDistance": 220,
          "centralGravity": 0.03,
          "springLength": 180,
          "springConstant": 0.05
        },
        "stabilization": { "enabled": true, "iterations": 150 }
      },
      "layout": { "improvedLayout": true },
      "interaction": { "dragNodes": true, "zoomView": true, "dragView": true }
    }""")

    def node_color(label):
        return NODE_COLORS.get(label, DEFAULT_NODE_COLOR)

    for nid, data in nodes_dict.items():
        label = data["label"]
        name = data["name"]
        id_prop = data.get("id_prop") or ""
        # Enhanced tooltip HTML
        if label == "Patient":
            age = data["props"].get("age")
            sex = data["props"].get("sex")
            diseases = data.get("patient_diseases") or []
            title = f"<b>Patient: {name}</b><br>Age: {age or '—'} | Sex: {sex or '—'}<br>Diseases: {', '.join(diseases) or '—'}"
        elif label == "Doctor":
            spec = data["props"].get("specialty")
            score_data = data.get("doctor_score")
            score = f"{score_data['compliance_score']}%" if score_data else "—"
            title = f"<b>Doctor: {name}</b><br>Specialty: {spec or '—'}<br>Compliance score: {score}"
        elif label == "ClinicalState":
            p = data["props"]
            title = (f"<b>Clinical state: {name}</b><br>SOFA: {p.get('sofa_score') or '—'} | "
                     f"Lactate: {p.get('lactate') or '—'} mmol/L | MAP: {p.get('map') or '—'} mmHg<br>"
                     f"GCS: {p.get('gcs') or '—'} | Creatinine: {p.get('creatinine') or '—'} mg/dL<br>"
                     f"Antibiotics: {p.get('antibiotics_active')} | Cultures: {p.get('cultures_ordered')} | Vasopressors: {p.get('vasopressors_active')}")
        elif label == "SepsisGuideline":
            p = data["props"]
            title = f"<b>Sepsis guideline: {name}</b><br>{p.get('description') or '—'}<br>SOFA≥{p.get('sofa_threshold_high')} | Lactate>{p.get('lactate_threshold_mmol')} | MAP<{p.get('map_threshold_mmhg')}"
        elif label == "Violation":
            desc = (data["props"] or {}).get("description") or name
            name = (desc[:80] + "…") if len(desc) > 80 else desc  # show violation text as node label
            title = f"<b>Violation</b><br>{desc}"
        else:
            title = f"<b>{label}: {name}</b>"
            if data["props"].get("icd10"):
                title += f"<br>ICD-10: {data['props']['icd10']}"
        net.add_node(
            nid,
            label=name,
            color=node_color(label),
            title=title,
            id_prop=id_prop,
            node_type=label,
        )

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
            title = violation_tooltips.get(key, rel_type + " (VIOLATION)")
            # Slightly thicker so violations stand out
            net.add_edge(src, tgt, label=rel_type, color=color, title=title, width=2.5)
            continue
        elif rel_type in TREATMENT_REL_TYPES:
            color = EDGE_COLOR_COMPLIANT
            title = f"{rel_type} (compliant)"
        else:
            color = EDGE_COLOR_DEFAULT
            title = rel_type
        net.add_edge(src, tgt, label=rel_type, color=color, title=title)

    # Stats and violations list for sidebar
    patients_with_diseases = get_patients_with_diseases()
    unique_patients = len(set(x.get("patient_id") for x in patients_with_diseases if x.get("patient_id")))
    violations_list = [
        {
            "patient_id": r.get("patient_id"),
            "patient_name": r.get("patient_name"),
            "disease_id": r.get("disease_id"),
            "disease_name": r.get("disease_name"),
            "violations": r.get("violations") or [],
        }
        for r in compliance.get("patients_with_violations", [])
    ]
    stats = {
        "total_patients": unique_patients,
        "total_violations": len(compliance.get("patients_with_violations", [])),
        "doctor_compliance_scores": compliance.get("doctor_compliance_scores", []),
        "violations_list": violations_list,
    }
    explanations = {k: get_explanation(k) for k in PROTOCOL_EXPLANATIONS}

    return net, stats, explanations


def _sidebar_and_script(stats: dict, explanations: dict) -> str:
    """HTML for collapsible sidebar (legend, stats) + script for filters, click-to-explain, and stats injection."""
    stats_json = json.dumps(stats, default=str)
    expl_json = json.dumps(explanations, default=str)
    return """
<style>
  #loadingBar { display: none !important; opacity: 0 !important; visibility: hidden !important; pointer-events: none !important; }
  .dashboard-wrapper { display: flex; height: 100vh; min-height: 100vh; overflow: hidden; }
  .dashboard-sidebar {
    width: 300px; min-width: 300px; max-width: 300px; padding: 1rem; background: #2d3748; color: #e2e8f0;
    font-family: system-ui, sans-serif; font-size: 13px; overflow-y: auto; transition: margin 0.2s;
  }
  .dashboard-sidebar.collapsed { margin-left: -280px; }
  .dashboard-sidebar h2 { margin: 0 0 0.75rem; font-size: 1.1rem; color: #fff; }
  .dashboard-sidebar h3 { margin: 0.75rem 0 0.35rem; font-size: 0.85rem; color: #a0aec0; }
  .dashboard-sidebar ul { margin: 0; padding-left: 1.1rem; }
  .dashboard-sidebar li { margin: 0.25rem 0; }
  .dashboard-stats { background: #1a202c; padding: 0.5rem 0.75rem; border-radius: 6px; margin: 0.5rem 0; }
  .dashboard-stats p { margin: 0.25rem 0; }
  .dashboard-main { flex: 1; display: flex; flex-direction: column; min-width: 0; position: relative; min-height: 0; overflow: hidden; }
  .dashboard-main #mynetwork { flex: 1; min-height: 0; height: 100%; }
  .filter-bar { padding: 0.5rem 1rem; background: #edf2f7; display: flex; flex-wrap: wrap; gap: 0.5rem; align-items: center; }
  .filter-bar label { font-weight: 600; margin-right: 0.25rem; }
  .filter-bar select { padding: 0.25rem 0.5rem; border-radius: 4px; }
  .explain-panel {
    position: absolute; bottom: 0; left: 0; right: 0; max-height: 200px; overflow-y: auto;
    background: #fff; border-top: 2px solid #cbd5e0; padding: 0.75rem 1rem; font-size: 12px; box-shadow: 0 -2px 8px rgba(0,0,0,0.08);
  }
  .explain-panel h4 { margin: 0 0 0.5rem; color: #2d3748; }
  .explain-panel.empty { display: none; }
  .toggle-sidebar { position: absolute; left: 300px; top: 8px; z-index: 10; padding: 4px 8px; border-radius: 4px; background: #4a5568; color: #fff; cursor: pointer; font-size: 12px; }
  .toggle-sidebar.collapsed { left: 0; }
  .node-legend span { display: inline-block; width: 12px; height: 12px; border-radius: 50%; margin-right: 6px; vertical-align: middle; }
  .ai-panel { background: #1a202c; padding: 0.75rem; border-radius: 6px; margin: 0.5rem 0; }
  .ai-panel h3 { margin: 0 0 0.5rem; font-size: 0.9rem; color: #a0aec0; }
  .ai-panel input[type="text"], .ai-panel textarea { width: 100%; padding: 0.4rem 0.5rem; border-radius: 4px; border: 1px solid #4a5568; background: #2d3748; color: #e2e8f0; font-size: 12px; box-sizing: border-box; margin-bottom: 0.4rem; }
  .ai-panel textarea { min-height: 52px; resize: vertical; }
  .ai-panel button { padding: 0.4rem 0.75rem; border-radius: 4px; border: none; font-weight: 600; font-size: 12px; cursor: pointer; margin-right: 0.25rem; margin-bottom: 0.25rem; }
  .ai-panel .btn-ask { background: #3182ce; color: #fff; }
  .ai-panel .btn-ask:hover { background: #2c5282; }
  .ai-panel .btn-ask:disabled { opacity: 0.6; cursor: not-allowed; }
  .ai-panel .btn-highlight { background: #38a169; color: #fff; }
  .ai-panel .btn-highlight:hover { background: #276749; }
  .ai-result { margin-top: 0.5rem; padding: 0.5rem; background: #2d3748; border-radius: 4px; font-size: 12px; max-height: 200px; overflow-y: auto; }
  .ai-result .answer { color: #e2e8f0; margin-bottom: 0.5rem; line-height: 1.4; }
  .ai-result .violation-badge { display: inline-block; padding: 0.2rem 0.5rem; border-radius: 4px; font-size: 11px; font-weight: 600; margin-bottom: 0.4rem; }
  .ai-result .violation-badge.yes { background: #c53030; color: #fff; }
  .ai-result .violation-badge.no { background: #276749; color: #fff; }
  .ai-result .meta { color: #a0aec0; font-size: 11px; margin-top: 0.4rem; }
  .ai-result .error { color: #fc8181; }
  .ai-loading { color: #a0aec0; font-size: 12px; }
</style>
<div class="dashboard-wrapper">
  <button class="toggle-sidebar" id="toggleSidebar" onclick="document.querySelector('.dashboard-sidebar').classList.toggle('collapsed'); this.classList.toggle('collapsed');">◀ Sidebar</button>
  <aside class="dashboard-sidebar" id="sidebar">
    <h2>Compliance Dashboard</h2>
    <section class="dashboard-stats" id="statsPanel">
      <h3>Statistics</h3>
      <p id="statPatients">Total patients: —</p>
      <p id="statViolations">Violations: —</p>
      <p id="statDoctors">Doctor compliance: —</p>
    </section>
    <section class="dashboard-stats" id="violationsPanel">
      <h3>Protocol violations</h3>
      <ul id="violationsList"></ul>
    </section>
    <h3>Node types</h3>
    <ul class="node-legend" id="nodeLegend"></ul>
    <h3>Edge types</h3>
    <ul id="edgeLegend"></ul>
    <h3>Color coding</h3>
    <p>Edges: <span style="color:#cc0000">●</span> Violation &nbsp; <span style="color:#00aa00">●</span> Compliant &nbsp; <span style="color:#888">●</span> Other</p>
    <section class="ai-panel">
      <h3>Ask AI</h3>
      <input type="text" id="aiApiUrl" placeholder="API: http://localhost:8000" value="http://localhost:8000" style="font-size:11px;margin-bottom:4px;" title="API server address (must be running)" />
      <textarea id="aiQuestion" placeholder="e.g. Did patient P1 follow the diabetes protocol? Or: Which doctor has the most violations?" rows="2"></textarea>
      <button type="button" class="btn-ask" id="aiAskBtn" onclick="askAi()">Ask AI</button>
      <div id="aiResult" class="ai-result" style="display:none;">
        <div id="aiAnswer" class="answer"></div>
        <span id="aiViolationBadge" class="violation-badge"></span>
        <div id="aiMeta" class="meta"></div>
        <button type="button" class="btn-highlight" id="aiHighlightBtn" onclick="highlightFromAi()" style="display:none;margin-top:6px;">Highlight in graph</button>
      </div>
      <div id="aiLoading" class="ai-loading" style="display:none;">Asking AI…</div>
    </section>
  </aside>
  <div class="dashboard-main">
    <div class="filter-bar">
      <label>Doctor</label><select id="filterDoctor" onchange="applyFilter()"><option value="">All</option></select>
      <label>Patient</label><select id="filterPatient" onchange="applyFilter()"><option value="">All</option></select>
      <label>Disease</label><select id="filterDisease" onchange="applyFilter()"><option value="">All</option></select>
      <label>Compliance</label><select id="filterCompliance" onchange="applyFilter()"><option value="">All</option><option value="violation">Violations only</option><option value="compliant">Compliant only</option></select>
      <label>Hospital</label><select id="filterHospital" onchange="applyFilter()"><option value="">All</option></select>
      <button type="button" onclick="applyFilter()">Apply</button>
      <button type="button" onclick="resetFilter()">Reset</button>
      <span id="filterLabel" style="margin-left:0.5rem;font-weight:600;color:#2d3748;">Showing: All</span>
    </div>
    MYNETWORK_PLACEHOLDER
    <div class="explain-panel empty" id="explainPanel">
      <h4 id="explainTitle">Protocol explanation</h4>
      <div id="explainContent"></div>
    </div>
  </div>
</div>
<script>
  var DASHBOARD_STATS = """ + stats_json + """;
  var EXPLANATIONS = """ + expl_json + """;
  function initDashboard() {
    document.getElementById('statPatients').textContent = 'Total patients: ' + (DASHBOARD_STATS.total_patients || 0);
    document.getElementById('statViolations').textContent = 'Violations: ' + (DASHBOARD_STATS.total_violations || 0);
    var docLines = (DASHBOARD_STATS.doctor_compliance_scores || []).map(function(d) { return d.doctor_name + ' ' + d.compliance_score + '%'; });
    document.getElementById('statDoctors').textContent = 'Doctor compliance: ' + (docLines.length ? docLines.join('; ') : '—');
    var vList = document.getElementById('violationsList');
    vList.innerHTML = '';
    (DASHBOARD_STATS.violations_list || []).forEach(function(v) {
      var li = document.createElement('li');
      li.style.marginBottom = '0.5rem';
      li.innerHTML = '<strong>' + (v.patient_name || v.patient_id) + '</strong> / ' + (v.disease_name || v.disease_id) + ': ' + (v.violations && v.violations.length ? v.violations.join('; ') : '—');
      vList.appendChild(li);
    });
    if (!(DASHBOARD_STATS.violations_list || []).length) {
      var li = document.createElement('li');
      li.textContent = 'None';
      vList.appendChild(li);
    }
    var nodeTypes = """ + json.dumps(NODE_TYPES_LIST) + """;
    var nodeColors = """ + json.dumps(NODE_COLORS) + """;
    var ul = document.getElementById('nodeLegend');
    nodeTypes.forEach(function(t) { var li = document.createElement('li'); li.innerHTML = '<span style="background:' + (nodeColors[t] || '#888') + '"></span>' + t; ul.appendChild(li); });
    var edgeTypes = """ + json.dumps(EDGE_TYPES_LIST) + """;
    var ulE = document.getElementById('edgeLegend');
    edgeTypes.forEach(function(t) { var li = document.createElement('li'); li.textContent = t; ulE.appendChild(li); });
  }
  function getNet() {
    var n = (typeof window !== 'undefined' && window.network && window.network.body) ? window.network : (typeof network !== 'undefined' && network && network.body ? network : null);
    return n || null;
  }
  function showExplanation(nodeId) {
    var net = getNet(); if (!net) return;
    var nodeData = net.body.data.nodes.get(nodeId);
    if (!nodeData) { document.getElementById('explainPanel').classList.add('empty'); return; }
    var expl = nodeData.id_prop ? EXPLANATIONS[nodeData.id_prop] : null;
    var panel = document.getElementById('explainPanel');
    var title = document.getElementById('explainTitle');
    var content = document.getElementById('explainContent');
    panel.classList.remove('empty');
    if (expl && (expl.text || expl.name)) {
      title.textContent = (expl.name || nodeData.label) + ' — Protocol explanation';
      var html = (expl.text || '').replace(/\\n/g, '<br>');
      if (expl.references) html += '<br><small>Refs: ' + expl.references + '</small>';
      content.innerHTML = html || 'No explanation available.';
    } else {
      title.textContent = nodeData.label + ' — Info';
      content.innerHTML = 'Node type: <strong>' + (nodeData.node_type || '') + '</strong>. Click a Disease, Drug, or Procedure node for protocol explanation (why recommended).';
    }
  }
  function getConnectedNodeIds(startId) {
    var net = getNet(); if (!net) return [];
    var seen = {}; var stack = [startId]; seen[startId] = true;
    var edges = net.body.data.edges.get();
    while (stack.length) {
      var id = stack.pop();
      edges.forEach(function(e) {
        var other = e.from === id ? e.to : (e.to === id ? e.from : null);
        if (other && !seen[other]) { seen[other] = true; stack.push(other); }
      });
    }
    return Object.keys(seen);
  }
  function toNodeArray(raw) { return Array.isArray(raw) ? raw : (raw ? Object.keys(raw).map(function(k) { return raw[k]; }) : []); }
  function applyFilter() {
    var net = getNet();
    if (!net || !net.body || !net.body.data) {
      alert('Graph not ready. Please refresh the page.');
      return;
    }
    if (!window._allNodes || !window._allNodes.length) {
      window._allNodes = toNodeArray(net.body.data.nodes.get());
      window._allEdges = toNodeArray(net.body.data.edges.get());
    }
    var allNodes = window._allNodes;
    var allEdges = window._allEdges;
    if (!allNodes || !allNodes.length) return;
    var doctor = document.getElementById('filterDoctor').value;
    var patient = document.getElementById('filterPatient').value;
    var disease = document.getElementById('filterDisease').value;
    var compliance = document.getElementById('filterCompliance').value;
    var hospital = document.getElementById('filterHospital').value;
    var nodeMap = {};
    allNodes.forEach(function(n) { nodeMap[n.id] = n; });
    var visibleIds = {};
    if (!doctor && !patient && !disease && !compliance && !hospital) {
      allNodes.forEach(function(n) { visibleIds[n.id] = true; });
    } else {
      var seedIds = [];
      allNodes.forEach(function(n) {
        if (doctor && n.id_prop === doctor) seedIds.push(n.id);
        if (patient && n.id_prop === patient) seedIds.push(n.id);
        if (disease && n.id_prop === disease) seedIds.push(n.id);
        if (hospital && n.id_prop === hospital) seedIds.push(n.id);
      });
      seedIds = seedIds.filter(function(id, i, a) { return a.indexOf(id) === i; });
      if (seedIds.length) {
        var edges = window._allEdges || toNodeArray(net.body.data.edges.get());
        // When filtering by doctor: only include that doctor and their patients (don't follow into other doctors' patients)
        var allowPatient = null;
        if (doctor && !patient && !disease && !hospital) {
          var doctorId = seedIds[0];
          allowPatient = {};
          edges.forEach(function(e) {
            var a = e.from === doctorId ? e.to : (e.to === doctorId ? e.from : null);
            if (a && nodeMap[a] && nodeMap[a].node_type === 'Patient') allowPatient[a] = true;
          });
          seedIds = [doctorId].concat(Object.keys(allowPatient));
        }
        // When filtering by patient: only that patient's subgraph (don't add other patients)
        if (patient && !doctor && !disease && !hospital) {
          allowPatient = {}; allowPatient[seedIds[0]] = true;
        }
        // When filtering by hospital: only this hospital's subgraph (don't add other hospitals)
        var allowHospital = null;
        if (hospital && !doctor && !patient && !disease) {
          allowHospital = {}; seedIds.forEach(function(id) { allowHospital[id] = true; });
        }
        // When filtering by disease: only this disease's subgraph (don't add other diseases)
        var allowDisease = null;
        if (disease && !doctor && !patient && !hospital) {
          allowDisease = {}; seedIds.forEach(function(id) { allowDisease[id] = true; });
        }
        seedIds.forEach(function(startId) {
          var seen = {}; var stack = [startId]; seen[startId] = true;
          while (stack.length) {
            var cur = stack.pop();
            edges.forEach(function(e) {
              var other = e.from === cur ? e.to : (e.to === cur ? e.from : null);
              if (!other || seen[other]) return;
              var node = nodeMap[other];
              if (allowPatient !== null && node && node.node_type === 'Patient' && !allowPatient[other]) return;
              if (allowHospital !== null && node && node.node_type === 'Hospital' && !allowHospital[other]) return;
              if (allowDisease !== null && node && node.node_type === 'Disease' && !allowDisease[other]) return;
              seen[other] = true; stack.push(other);
            });
          }
          Object.keys(seen).forEach(function(x) { visibleIds[x] = true; });
        });
      } else {
        allNodes.forEach(function(n) { visibleIds[n.id] = true; });
      }
    }
    function isRedEdge(e) {
      var c = e.color;
      return c === '#cc0000' || (c && (typeof c === 'string' ? c : c.color) === '#cc0000');
    }
    allNodes.forEach(function(n) {
      var show = !!visibleIds[n.id];
      if (compliance === 'violation') {
        var hasViolation = allEdges.some(function(e) { return (e.from === n.id || e.to === n.id) && isRedEdge(e); });
        if (!hasViolation) show = false;
      } else if (compliance === 'compliant') {
        var hasViolation = allEdges.some(function(e) { return (e.from === n.id || e.to === n.id) && isRedEdge(e); });
        if (hasViolation) show = false;
      }
      if (!show) delete visibleIds[n.id];
    });
    var filteredNodes = allNodes.filter(function(n) { return visibleIds[n.id]; });
    var filteredEdges = allEdges.filter(function(e) { return visibleIds[e.from] && visibleIds[e.to]; });
    try {
      var lb = document.getElementById('loadingBar');
      if (lb) { lb.style.display = 'none'; lb.style.opacity = '0'; }
      var newNodes = new vis.DataSet(filteredNodes);
      var newEdges = new vis.DataSet(filteredEdges);
      net.setData({ nodes: newNodes, edges: newEdges });
      window._currentNodes = newNodes;
      window._currentEdges = newEdges;
      var lab = document.getElementById('filterLabel');
      if (lab) {
        var parts = [];
        var d = document.getElementById('filterDoctor');
        if (doctor && d && d.options[d.selectedIndex]) parts.push(d.options[d.selectedIndex].text);
        var p = document.getElementById('filterPatient');
        if (patient && p && p.options[p.selectedIndex]) parts.push(p.options[p.selectedIndex].text);
        var dis = document.getElementById('filterDisease');
        if (disease && dis && dis.options[dis.selectedIndex]) parts.push(dis.options[dis.selectedIndex].text);
        var h = document.getElementById('filterHospital');
        if (hospital && h && h.options[h.selectedIndex]) parts.push(h.options[h.selectedIndex].text);
        if (compliance === 'violation') parts.push('Violations only');
        if (compliance === 'compliant') parts.push('Compliant only');
        lab.textContent = parts.length ? 'Showing: ' + parts.join(', ') : 'Showing: All';
      }
      // Re-enable physics so the filtered subgraph lays out correctly (nodes spread out)
      try {
        net.setOptions({ physics: { enabled: true, solver: 'repulsion', repulsion: { nodeDistance: 220, centralGravity: 0.03, springLength: 180, springConstant: 0.05 }, stabilization: { enabled: true, iterations: 150 } } });
      } catch(e) {}
      function onStabilized() {
        net.off('stabilizationIterationsDone', onStabilized);
        try { net.setOptions({ physics: { enabled: false } }); } catch(e) {}
        try { if (net.fit) net.fit({ animation: { duration: 300 } }); } catch(e) {}
      }
      net.once('stabilizationIterationsDone', onStabilized);
      setTimeout(function() { try { if (net.fit) net.fit({ animation: { duration: 300 } }); } catch(e) {} }, 500);
    } catch(err) {
      console.error('Filter error', err);
      alert('Filter failed: ' + (err.message || err));
    }
  }
  function resetFilter() {
    document.getElementById('filterDoctor').value = '';
    document.getElementById('filterPatient').value = '';
    document.getElementById('filterDisease').value = '';
    document.getElementById('filterCompliance').value = '';
    document.getElementById('filterHospital').value = '';
    var lab = document.getElementById('filterLabel');
    if (lab) lab.textContent = 'Showing: All';
    var net = getNet();
    if (!net || !window._allNodes || !window._allEdges) return;
    try {
      var lb = document.getElementById('loadingBar');
      if (lb) { lb.style.display = 'none'; lb.style.opacity = '0'; }
      var fullNodes = new vis.DataSet(window._allNodes);
      var fullEdges = new vis.DataSet(window._allEdges);
      net.setData({ nodes: fullNodes, edges: fullEdges });
      window._currentNodes = fullNodes;
      window._currentEdges = fullEdges;
      // Re-enable physics so the full graph lays out correctly
      try {
        net.setOptions({ physics: { enabled: true, solver: 'repulsion', repulsion: { nodeDistance: 220, centralGravity: 0.03, springLength: 180, springConstant: 0.05 }, stabilization: { enabled: true, iterations: 150 } } });
      } catch(e) {}
      function onStabilized() {
        net.off('stabilizationIterationsDone', onStabilized);
        try { net.setOptions({ physics: { enabled: false } }); } catch(e) {}
        try { if (net.fit) net.fit({ animation: { duration: 300 } }); } catch(e) {}
      }
      net.once('stabilizationIterationsDone', onStabilized);
      setTimeout(function() { try { if (net.fit) net.fit({ animation: { duration: 300 } }); } catch(e) {} }, 500);
    } catch(err) {
      console.error('Reset error', err);
    }
  }
  function populateFilterOptions() {
    var net = getNet();
    if (!net || !net.body || !net.body.data) return;
    var nodes = window._allNodes || toNodeArray(net.body.data.nodes.get());
    var doctors = {}, patients = {}, diseases = {}, hospitals = {};
    nodes.forEach(function(n) {
      if (n.node_type === 'Doctor') doctors[n.id_prop] = n.label;
      if (n.node_type === 'Patient') patients[n.id_prop] = n.label;
      if (n.node_type === 'Disease') diseases[n.id_prop] = n.label;
      if (n.node_type === 'Hospital') hospitals[n.id_prop] = n.label;
    });
    function fillSelect(id, map) {
      var sel = document.getElementById(id);
      while (sel.options.length > 1) sel.remove(1);
      Object.keys(map).sort().forEach(function(k) { var o = document.createElement('option'); o.value = k; o.textContent = map[k]; sel.appendChild(o); });
    }
    fillSelect('filterDoctor', doctors);
    fillSelect('filterPatient', patients);
    fillSelect('filterDisease', diseases);
    fillSelect('filterHospital', hospitals);
  }
  var lastAiResponse = null;
  function askAi() {
    var q = (document.getElementById('aiQuestion') && document.getElementById('aiQuestion').value || '').trim();
    if (!q) {
      alert('Please type a question (e.g. "Did patient P1 follow the diabetes protocol?")');
      return;
    }
    var apiUrl = (document.getElementById('aiApiUrl') && document.getElementById('aiApiUrl').value || 'http://localhost:8000').replace(/\\/$/, '');
    var resultEl = document.getElementById('aiResult');
    var loadingEl = document.getElementById('aiLoading');
    var btn = document.getElementById('aiAskBtn');
    resultEl.style.display = 'none';
    loadingEl.style.display = 'block';
    if (btn) btn.disabled = true;
    fetch(apiUrl + '/ask-agent', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question: q })
    }).then(function(r) {
      if (!r.ok) throw new Error('API error: ' + r.status);
      return r.json();
    }).then(function(data) {
      lastAiResponse = data;
      var answerEl = document.getElementById('aiAnswer');
      var badgeEl = document.getElementById('aiViolationBadge');
      var metaEl = document.getElementById('aiMeta');
      var highlightBtn = document.getElementById('aiHighlightBtn');
      if (answerEl) { answerEl.textContent = data.answer || 'No answer.'; answerEl.classList.remove('error'); }
      if (metaEl) metaEl.classList.remove('error');
      if (badgeEl) {
        badgeEl.textContent = data.violation ? 'Protocol violation' : 'Compliant';
        badgeEl.className = 'violation-badge ' + (data.violation ? 'yes' : 'no');
      }
      var meta = [];
      if ((data.protocol_expected || []).length) meta.push('Expected: ' + data.protocol_expected.join(', '));
      if ((data.actual_treatment || []).length) meta.push('Actual: ' + data.actual_treatment.join(', '));
      if (metaEl) metaEl.innerHTML = meta.join('<br/>');
      if (highlightBtn) highlightBtn.style.display = ((data.highlight_nodes || []).length || (data.paths || []).length) ? 'inline-block' : 'none';
      resultEl.style.display = 'block';
    }).catch(function(err) {
      lastAiResponse = null;
      var answerEl = document.getElementById('aiAnswer');
      if (answerEl) { answerEl.textContent = 'Could not reach the AI. Is the API running? Start it with: uvicorn api_server:app --reload'; answerEl.classList.add('error'); }
      document.getElementById('aiViolationBadge').style.display = 'none';
      document.getElementById('aiMeta').textContent = err.message || 'Check the API URL (e.g. http://localhost:8000) and try again.';
      document.getElementById('aiMeta').classList.add('error');
      document.getElementById('aiHighlightBtn').style.display = 'none';
      resultEl.style.display = 'block';
    }).then(function() {
      loadingEl.style.display = 'none';
      if (btn) btn.disabled = false;
    });
  }
  function highlightFromAi() {
    if (!lastAiResponse) return;
    var net = getNet();
    if (!net || !window._allNodes || !window._allEdges) { alert('Graph not ready. Wait for it to load.'); return; }
    var nodes = lastAiResponse.highlight_nodes || [];
    if (!nodes.length && (lastAiResponse.paths || []).length) {
      var seen = {};
      (lastAiResponse.paths[0].nodes || []).forEach(function(n) { seen[n] = true; });
      nodes = Object.keys(seen);
    }
    if (!nodes.length) { alert('No nodes to highlight.'); return; }
    var allNodes = window._allNodes;
    var allEdges = window._allEdges;
    var visibleIds = {};
    nodes.forEach(function(s) {
      var parts = s.indexOf(':') >= 0 ? s.split(':') : [null, s];
      var type = parts[0];
      var idProp = parts[1];
      allNodes.forEach(function(n) {
        if (n.id_prop === idProp && (!type || (n.node_type || '').toLowerCase() === (type || '').toLowerCase())) visibleIds[n.id] = true;
      });
    });
    var filteredNodes = allNodes.filter(function(n) { return visibleIds[n.id]; });
    var filteredEdges = allEdges.filter(function(e) { return visibleIds[e.from] && visibleIds[e.to]; });
    try {
      net.setOptions({ physics: { enabled: false } });
      net.setData({ nodes: new vis.DataSet(filteredNodes), edges: new vis.DataSet(filteredEdges) });
      window._currentNodes = new vis.DataSet(filteredNodes);
      window._currentEdges = new vis.DataSet(filteredEdges);
      net.setOptions({ physics: { enabled: true, solver: 'repulsion', repulsion: { nodeDistance: 220, centralGravity: 0.03, springLength: 180, springConstant: 0.05 }, stabilization: { enabled: true, iterations: 150 } } });
      net.once('stabilizationIterationsDone', function() {
        net.off('stabilizationIterationsDone', arguments.callee);
        net.setOptions({ physics: { enabled: false } });
        try { if (net.fit) net.fit({ animation: { duration: 300 } }); } catch(e) {}
      });
      setTimeout(function() { try { if (net.fit) net.fit({ animation: { duration: 300 } }); } catch(e) {} }, 400);
      document.getElementById('filterLabel').textContent = 'Showing: AI highlight';
    } catch(e) { console.error(e); alert('Highlight failed: ' + (e.message || e)); }
  }
  document.addEventListener('DOMContentLoaded', function() {
    initDashboard();
    function attachToGraph() {
      var net = getNet();
      if (net) {
        net.on('click', function(params) {
          if (params.nodes && params.nodes.length) showExplanation(params.nodes[0]);
        });
        populateFilterOptions();
      } else {
        setTimeout(attachToGraph, 100);
      }
    }
    setTimeout(attachToGraph, 100);
  });
</script>
"""


def inject_dashboard_into_html(html: str, sidebar_and_script: str) -> str:
    """Inject sidebar layout and script. Put #mynetwork inside .dashboard-main (between filter and explain) so the graph sits to the right of the sidebar in one view."""
    # Extract the mynetwork div from pyvis output (card contains it)
    mynetwork_pattern = re.compile(r'<div id="mynetwork"[^>]*></div>', re.IGNORECASE)
    match = mynetwork_pattern.search(html)
    mynetwork_div = match.group(0) if match else '<div id="mynetwork" class="card-body"></div>'
    # Put mynetwork inside the right-hand column (between filter and explain)
    sidebar_with_graph = sidebar_and_script.replace("MYNETWORK_PLACEHOLDER", mynetwork_div)
    if "<body>" in html:
        html = html.replace("<body>", "<body>\n" + sidebar_with_graph, 1)
    # Remove the original card block (so we don't have a second mynetwork below the wrapper)
    card_pattern = re.compile(
        r'<div class="card"[^>]*>\s*<div id="mynetwork"[^>]*></div>\s*</div>',
        re.DOTALL | re.IGNORECASE,
    )
    html = card_pattern.sub("", html, count=1)
    # After drawGraph(): expose network and store full nodes/edges for filter reset
    html = re.sub(
        r'(\s+drawGraph\(\)\s*;)',
        r'''\1 try {
          if (typeof network !== "undefined" && network && network.body && network.body.data) {
            window.network = network;
            var _nr = network.body.data.nodes.get();
            var _er = network.body.data.edges.get();
            window._allNodes = Array.isArray(_nr) ? _nr : (_nr ? Object.keys(_nr).map(function(k){ return _nr[k]; }) : []);
            window._allEdges = Array.isArray(_er) ? _er : (_er ? Object.keys(_er).map(function(k){ return _er[k]; }) : []);
          }
        } catch(e) {}''',
        html,
        count=1,
    )
    # When stabilization finishes, store nodes/edges and hide loading bar (backup)
    html = re.sub(
        r"(setTimeout\(function\s*\(\)\s*\{document\.getElementById\('loadingBar'\)\.style\.display\s*=\s*'none';\}, 500\);)",
        r'''\1
                          try { var _r = network.body.data.nodes.get(); var _s = network.body.data.edges.get();
                            window._allNodes = Array.isArray(_r) ? _r : (_r ? Object.keys(_r).map(function(k){ return _r[k]; }) : []);
                            window._allEdges = Array.isArray(_s) ? _s : (_s ? Object.keys(_s).map(function(k){ return _s[k]; }) : []);
                          } catch(e) {}
                          var _lb = document.getElementById('loadingBar'); if (_lb) { _lb.style.display = 'none'; _lb.style.opacity = '0'; }''',
        html,
        count=1,
    )
    # Force-hide loading bar after 4s so it never stays stuck at 100%
    html = re.sub(
        r'(\s+drawGraph\(\)\s*;)',
        r'''\1
          setTimeout(function() {
            var lb = document.getElementById('loadingBar');
            if (lb) { lb.style.display = 'none'; lb.style.opacity = '0'; }
          }, 4000);''',
        html,
        count=1,
    )
    html = html.replace("</body>", "</body>", 1)
    return html


def run_dashboard():
    """Build graph, inject dashboard UI, save to compliance_dashboard.html, open in browser."""
    net, stats, explanations = build_dashboard_graph()
    if net is None:
        print("No graph data. Run seed_data.py and ensure Neo4j is populated.")
        return
    path = Path(OUTPUT_HTML)
    net.save_graph(str(path))
    html = path.read_text(encoding="utf-8")
    sidebar = _sidebar_and_script(stats or {}, explanations or {})
    html = inject_dashboard_into_html(html, sidebar)
    path.write_text(html, encoding="utf-8")
    webbrowser.open(path.as_uri())
    print(f"Dashboard saved: {path}")
    print("Use the sidebar for legend and stats; click a node for protocol explanation; use filters to explore.")


if __name__ == "__main__":
    print("Running compliance check and building dashboard...")
    run_dashboard()
