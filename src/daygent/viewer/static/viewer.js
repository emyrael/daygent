(function () {
  "use strict";

  var TYPE_COLORS = {
    python_module: "#94a3b8",
    python_function: "#60a5fa",
    sql_table: "#fbbf24",
    dbt_model: "#34d399",
    dbt_source: "#2dd4bf",
    api_route: "#c084fc",
    api_client: "#a78bfa",
    service: "#22d3ee",
    langgraph_node: "#f472b6",
    agent: "#fb7185",
    llm: "#fb923c",
    embedding_model: "#38bdf8",
    vector_store: "#818cf8",
    vector_collection: "#c4b5fd",
    external_system: "#f87171",
    pipeline_dataset: "#5eead4",
    django_model: "#86efac",
    sqlalchemy_model: "#67e8f9"
  };

  var NODE_W = 188;
  var NODE_H = 52;
  var GAP_X = 86;
  var GAP_Y = 36;

  var raw = document.getElementById("daygent-data").textContent;
  var data = JSON.parse(raw);
  var nodes = data.nodes || [];
  var edges = (data.edges || []).filter(function (edge) {
    return edge && edge.source && edge.target;
  });

  var byId = {};
  nodes.forEach(function (node) {
    byId[node.id] = node;
  });

  var outgoing = {};
  var incoming = {};
  edges.forEach(function (edge) {
    if (!byId[edge.source] || !byId[edge.target]) return;
    (outgoing[edge.source] || (outgoing[edge.source] = [])).push(edge.target);
    (incoming[edge.target] || (incoming[edge.target] = [])).push(edge.source);
  });

  var svg = document.getElementById("canvas");
  var tooltip = document.getElementById("tooltip");
  var ns = "http://www.w3.org/2000/svg";
  var viewport = svgEl("g", { id: "viewport" });
  var edgeLayer = svgEl("g", { id: "edges" });
  var nodeLayer = svgEl("g", { id: "nodes" });
  viewport.appendChild(edgeLayer);
  viewport.appendChild(nodeLayer);
  svg.appendChild(defs());
  svg.appendChild(viewport);

  var transform = { x: 40, y: 40, k: 1 };
  var dragging = false;
  var dragOrigin = null;
  var panMoved = false;
  var nodeDrag = null;
  var selectedId = null;
  var selectedEdge = null;
  var hoverId = null;
  var highlight = null;
  var typeFilter = "";
  var hideFunctions = true;
  var query = "";
  var nodeEls = {};
  var edgeEls = [];
  var positions = {};

  if (data.includes_source) {
    var banner = document.getElementById("source-banner");
    if (banner) {
      banner.hidden = false;
      if (!banner.textContent) {
        banner.textContent = "This HTML contains source-code excerpts.";
      }
    }
  }

  initTheme();
  relayout();
  fillTypeFilter();
  fillLegend();
  fillStats();
  fit();
  bind();

  function isDark() {
    return document.documentElement.getAttribute("data-theme") !== "light";
  }

  function initTheme() {
    var theme = "dark";
    try {
      theme = localStorage.getItem("daygent-theme") || theme;
    } catch (err) { /* file:// may block storage */ }
    applyTheme(theme);
  }

  function applyTheme(theme) {
    theme = theme === "light" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", theme);
    var toggle = document.getElementById("theme-toggle");
    if (toggle) toggle.textContent = theme === "dark" ? "Light" : "Dark";
    try {
      localStorage.setItem("daygent-theme", theme);
    } catch (err) { /* ignore */ }
    if (Object.keys(nodeEls).length) draw();
  }

  function svgEl(name, attrs) {
    var el = document.createElementNS(ns, name);
    Object.keys(attrs || {}).forEach(function (key) {
      el.setAttribute(key, attrs[key]);
    });
    return el;
  }

  function defs() {
    var d = svgEl("defs");
    d.appendChild(arrow("arrow", "#64748b"));
    d.appendChild(arrow("arrow-hl", "#2dd4bf"));
    d.appendChild(glow("glow", 4, 0.45));
    d.appendChild(glow("glow-strong", 8, 0.8));
    return d;
  }

  function arrow(id, color) {
    var marker = svgEl("marker", {
      id: id,
      viewBox: "0 0 10 10",
      refX: "9",
      refY: "5",
      markerWidth: "8",
      markerHeight: "8",
      orient: "auto-start-reverse"
    });
    marker.appendChild(svgEl("path", { d: "M 0 0 L 10 5 L 0 10 z", fill: color }));
    return marker;
  }

  function glow(id, blur, opacity) {
    var filter = svgEl("filter", {
      id: id,
      x: "-40%",
      y: "-40%",
      width: "180%",
      height: "180%"
    });
    filter.appendChild(svgEl("feGaussianBlur", { stdDeviation: String(blur), result: "b" }));
    var merge = svgEl("feMerge");
    merge.appendChild(svgEl("feMergeNode", { in: "b" }));
    merge.appendChild(svgEl("feMergeNode", { in: "SourceGraphic" }));
    filter.appendChild(merge);
    filter.setAttribute("opacity", String(opacity));
    return filter;
  }

  function layout(nodeList, edgeList) {
    var rank = {};
    var indeg = {};
    var out = {};
    var inc = {};
    nodeList.forEach(function (node) {
      indeg[node.id] = 0;
      out[node.id] = [];
      inc[node.id] = [];
    });
    edgeList.forEach(function (edge) {
      if (indeg[edge.target] == null || indeg[edge.source] == null) return;
      indeg[edge.target] += 1;
      out[edge.source].push(edge.target);
      inc[edge.target].push(edge.source);
    });
    var queue = [];
    nodeList.forEach(function (node) {
      rank[node.id] = 0;
      if (!indeg[node.id]) queue.push(node.id);
    });
    var seen = {};
    while (queue.length) {
      var id = queue.shift();
      if (seen[id]) continue;
      seen[id] = true;
      (out[id] || []).forEach(function (next) {
        rank[next] = Math.max(rank[next] || 0, (rank[id] || 0) + 1);
        indeg[next] -= 1;
        if (indeg[next] <= 0) queue.push(next);
      });
    }
    var columns = {};
    nodeList.forEach(function (node) {
      var r = rank[node.id] || 0;
      (columns[r] || (columns[r] = [])).push(node.id);
    });
    Object.keys(columns).forEach(function (r) {
      columns[r].sort();
    });
    for (var pass = 0; pass < 2; pass += 1) {
      Object.keys(columns).sort(function (a, b) { return a - b; }).forEach(function (r) {
        columns[r].sort(function (a, b) {
          return neighborScore(a) - neighborScore(b) || (a < b ? -1 : 1);
        });
      });
    }
    var pos = {};
    Object.keys(columns).forEach(function (r) {
      columns[r].forEach(function (nid, index) {
        pos[nid] = {
          x: Number(r) * (NODE_W + GAP_X),
          y: index * (NODE_H + GAP_Y)
        };
      });
    });
    return pos;

    function neighborScore(nid) {
      var neighbors = (inc[nid] || []).concat(out[nid] || []);
      if (!neighbors.length) return 0;
      var sum = 0;
      neighbors.forEach(function (other) {
        var col = columns[rank[other] || 0] || [];
        sum += col.indexOf(other);
      });
      return sum / neighbors.length;
    }
  }

  function structuralVisible(node) {
    if (typeFilter && node.type !== typeFilter) return false;
    if (hideFunctions && node.type === "python_function" && typeFilter !== "python_function") {
      return false;
    }
    return true;
  }

  function structuralGraph() {
    var vis = nodes.filter(structuralVisible);
    var ids = {};
    vis.forEach(function (node) { ids[node.id] = true; });
    return {
      nodes: vis,
      edges: edges.filter(function (edge) {
        return ids[edge.source] && ids[edge.target];
      })
    };
  }

  function relayout() {
    var subset = structuralGraph();
    positions = layout(subset.nodes, subset.edges);
    draw();
    fillStats();
  }

  function edgeColor() {
    return isDark() ? "#64748b" : "#94a3b8";
  }

  function edgePath(edge) {
    var a = positions[edge.source];
    var b = positions[edge.target];
    if (!a || !b) return null;
    var x1 = a.x + NODE_W;
    var y1 = a.y + NODE_H / 2;
    var x2 = b.x;
    var y2 = b.y + NODE_H / 2;
    var mid = (x1 + x2) / 2;
    return {
      d: "M " + x1 + " " + y1 + " C " + mid + " " + y1 + ", " + mid + " " + y2 + ", " + x2 + " " + y2,
      mx: (x1 + x2) / 2,
      my: (y1 + y2) / 2 - 8
    };
  }

  function draw() {
    edgeLayer.textContent = "";
    nodeLayer.textContent = "";
    nodeEls = {};
    edgeEls = [];
    var stroke = edgeColor();
    edges.forEach(function (edge) {
      var geom = edgePath(edge);
      if (!geom) return;
      var g = svgEl("g", {
        class: "edge",
        "data-source": edge.source,
        "data-target": edge.target,
        "data-type": edge.type || ""
      });
      g.appendChild(svgEl("path", { class: "edge-hit", d: geom.d }));
      g.appendChild(svgEl("path", {
        class: "edge-line",
        d: geom.d,
        stroke: stroke,
        "marker-end": "url(#arrow)"
      }));
      var label = svgEl("text", {
        class: "edge-label",
        x: String(geom.mx),
        y: String(geom.my),
        "text-anchor": "middle"
      });
      label.textContent = edge.type || "";
      label.style.display = "none";
      g.appendChild(label);
      g.addEventListener("mouseenter", function () {
        hoverId = null;
        highlightPair(edge.source, edge.target);
      });
      g.addEventListener("mouseleave", function () {
        refreshVisibility();
      });
      g.addEventListener("click", function (event) {
        event.stopPropagation();
        selectEdge(edge);
      });
      edgeLayer.appendChild(g);
      edgeEls.push({ el: g, edge: edge, label: label });
    });
    nodes.forEach(function (node) {
      var p = positions[node.id];
      if (!p) return;
      var color = TYPE_COLORS[node.type] || "#64748b";
      var g = svgEl("g", { class: "node", "data-id": node.id });
      g.setAttribute("transform", "translate(" + p.x + "," + p.y + ")");
      g.appendChild(svgEl("rect", {
        class: "card",
        x: "0",
        y: "0",
        rx: "12",
        ry: "12",
        width: String(NODE_W),
        height: String(NODE_H),
        fill: isDark() ? "#111827" : "#ffffff",
        stroke: color,
        "stroke-width": "1.5"
      }));
      g.appendChild(svgEl("rect", {
        x: "0",
        y: "8",
        rx: "2",
        width: "5",
        height: String(NODE_H - 16),
        fill: color
      }));
      var label = svgEl("text", {
        x: "16",
        y: "22",
        fill: isDark() ? "#f8fafc" : "#0f172a",
        class: "node-label"
      });
      label.textContent = truncate(node.label || node.name || node.id, 22);
      g.appendChild(label);
      var sub = svgEl("text", {
        x: "16",
        y: "38",
        fill: isDark() ? "rgba(248,250,252,0.55)" : "#64748b",
        class: "node-label"
      });
      sub.textContent = truncate(typeLabel(node.type), 24);
      g.appendChild(sub);
      g.addEventListener("mousedown", function (event) {
        event.stopPropagation();
        var pt = graphPoint(event);
        nodeDrag = {
          id: node.id,
          dx: pt.x - p.x,
          dy: pt.y - p.y,
          moved: false
        };
        g.classList.add("dragging");
      });
      g.addEventListener("click", function (event) {
        event.stopPropagation();
        if (nodeDrag && nodeDrag.moved) return;
        selectNode(node.id);
      });
      g.addEventListener("dblclick", function (event) {
        event.stopPropagation();
        selectNode(node.id);
        setHighlight("impact");
        focusNode(node.id);
      });
      g.addEventListener("mouseenter", function (event) {
        hoverId = node.id;
        g.classList.add("hover");
        showTooltip(node, event);
        refreshVisibility();
      });
      g.addEventListener("mousemove", function (event) {
        placeTooltip(event);
      });
      g.addEventListener("mouseleave", function () {
        hoverId = null;
        g.classList.remove("hover");
        hideTooltip();
        refreshVisibility();
      });
      nodeLayer.appendChild(g);
      nodeEls[node.id] = g;
    });
    applyTransform();
    refreshVisibility();
  }

  function graphPoint(event) {
    var rect = svg.getBoundingClientRect();
    return {
      x: (event.clientX - rect.left - transform.x) / transform.k,
      y: (event.clientY - rect.top - transform.y) / transform.k
    };
  }

  function moveNode(id, x, y) {
    if (!positions[id]) return;
    positions[id] = { x: x, y: y };
    var el = nodeEls[id];
    if (el) el.setAttribute("transform", "translate(" + x + "," + y + ")");
    edgeEls.forEach(function (item) {
      if (item.edge.source !== id && item.edge.target !== id) return;
      var geom = edgePath(item.edge);
      if (!geom) return;
      item.el.querySelectorAll("path").forEach(function (path) {
        path.setAttribute("d", geom.d);
      });
      if (item.label) {
        item.label.setAttribute("x", String(geom.mx));
        item.label.setAttribute("y", String(geom.my));
      }
    });
  }

  function truncate(text, max) {
    text = String(text || "");
    return text.length > max ? text.slice(0, max - 1) + "…" : text;
  }

  function typeLabel(type) {
    return String(type || "").replace(/_/g, " ");
  }

  function applyTransform() {
    viewport.setAttribute(
      "transform",
      "translate(" + transform.x + "," + transform.y + ") scale(" + transform.k + ")"
    );
  }

  function bounds() {
    var ids = visibleIds();
    var minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    ids.forEach(function (id) {
      var p = positions[id];
      if (!p) return;
      minX = Math.min(minX, p.x);
      minY = Math.min(minY, p.y);
      maxX = Math.max(maxX, p.x + NODE_W);
      maxY = Math.max(maxY, p.y + NODE_H);
    });
    if (!isFinite(minX)) return { minX: 0, minY: 0, maxX: 400, maxY: 200 };
    return { minX: minX, minY: minY, maxX: maxX, maxY: maxY };
  }

  function fit() {
    var box = bounds();
    var width = svg.clientWidth || 800;
    var height = svg.clientHeight || 600;
    var gw = Math.max(1, box.maxX - box.minX);
    var gh = Math.max(1, box.maxY - box.minY);
    var k = Math.min((width - 80) / gw, (height - 80) / gh, 1.6);
    transform.k = k;
    transform.x = (width - gw * k) / 2 - box.minX * k;
    transform.y = (height - gh * k) / 2 - box.minY * k;
    applyTransform();
  }

  function resetView() {
    transform = { x: 40, y: 40, k: 1 };
    applyTransform();
  }

  function focusNode(id) {
    var p = positions[id];
    if (!p) return;
    var width = svg.clientWidth || 800;
    var height = svg.clientHeight || 600;
    transform.k = Math.max(transform.k, 1.15);
    transform.x = width / 2 - (p.x + NODE_W / 2) * transform.k;
    transform.y = height / 2 - (p.y + NODE_H / 2) * transform.k;
    applyTransform();
  }

  function visibleIds() {
    return nodes.filter(matchesFilters).map(function (node) { return node.id; });
  }

  function matchesFilters(node) {
    if (!structuralVisible(node)) return false;
    if (!query) return true;
    var hay = (node.name + " " + node.id + " " + node.type + " " + (node.label || "")).toLowerCase();
    return hay.indexOf(query) !== -1;
  }

  function highlightPair(a, b) {
    Object.keys(nodeEls).forEach(function (id) {
      var on = id === a || id === b;
      nodeEls[id].classList.toggle("dim", !on);
      nodeEls[id].classList.toggle("hl", on);
    });
    edgeEls.forEach(function (item) {
      var on = item.edge.source === a && item.edge.target === b;
      item.el.classList.toggle("dim", !on);
      item.el.classList.toggle("hl", on);
      styleEdge(item, on);
      toggleEdgeLabel(item, on);
    });
  }

  function styleEdge(item, on) {
    var line = item.el.querySelector(".edge-line");
    if (!line) return;
    line.classList.toggle("flow", !!on);
    line.setAttribute("stroke", on ? "#2dd4bf" : edgeColor());
    line.setAttribute("marker-end", on ? "url(#arrow-hl)" : "url(#arrow)");
    line.setAttribute("stroke-width", on ? "2.4" : "1.7");
  }

  function shouldShowEdgeLabel(edge) {
    if (selectedEdge) {
      return edge.source === selectedEdge.source
        && edge.target === selectedEdge.target
        && edge.type === selectedEdge.type;
    }
    if (selectedId) {
      return edge.source === selectedId || edge.target === selectedId;
    }
    return false;
  }

  function toggleEdgeLabel(item, on) {
    if (!item.label) return;
    item.label.style.display = on && shouldShowEdgeLabel(item.edge) ? "" : "none";
  }

  function refreshVisibility() {
    var visible = {};
    visibleIds().forEach(function (id) { visible[id] = true; });
    var related = highlightSet();
    Object.keys(nodeEls).forEach(function (id) {
      var el = nodeEls[id];
      var show = !!visible[id];
      el.style.display = show ? "" : "none";
      el.classList.remove("dim", "hl", "selected");
      if (!show) return;
      if (id === selectedId) el.classList.add("selected");
      if (related) {
        el.classList.add(related[id] ? "hl" : "dim");
      } else if (hoverId && id !== hoverId && !connected(hoverId, id)) {
        el.classList.add("dim");
      }
      var rect = el.querySelector("rect.card");
      if (rect) {
        var color = TYPE_COLORS[(byId[id] || {}).type] || "#64748b";
        rect.setAttribute("stroke-width", id === selectedId ? "2.6" : "1.5");
        rect.setAttribute("stroke", id === selectedId ? "#2dd4bf" : color);
      }
    });
    edgeEls.forEach(function (item) {
      var show = visible[item.edge.source] && visible[item.edge.target];
      item.el.style.display = show ? "" : "none";
      item.el.classList.remove("dim", "hl");
      if (!show) return;
      var on = false;
      if (related) {
        on = !!(related[item.edge.source] && related[item.edge.target]
          && (item.edge.source === selectedId || item.edge.target === selectedId
            || (selectedEdge && item.edge.source === selectedEdge.source
              && item.edge.target === selectedEdge.target)));
        if (selectedEdge && item.edge.source === selectedEdge.source
          && item.edge.target === selectedEdge.target && item.edge.type === selectedEdge.type) {
          on = true;
        }
        item.el.classList.add(on ? "hl" : "dim");
      } else if (hoverId) {
        on = item.edge.source === hoverId || item.edge.target === hoverId;
        if (!on) item.el.classList.add("dim");
      }
      styleEdge(item, on);
      toggleEdgeLabel(item, on);
    });
    syncActionButtons();
    fillStats();
  }

  function connected(a, b) {
    return (outgoing[a] || []).indexOf(b) !== -1 || (incoming[a] || []).indexOf(b) !== -1;
  }

  function highlightSet() {
    if (selectedEdge) {
      var pair = {};
      pair[selectedEdge.source] = true;
      pair[selectedEdge.target] = true;
      return pair;
    }
    if (!selectedId) return null;
    var set = {};
    set[selectedId] = true;
    if (highlight === "upstream") {
      (incoming[selectedId] || []).forEach(function (id) { set[id] = true; });
    } else if (highlight === "downstream") {
      (outgoing[selectedId] || []).forEach(function (id) { set[id] = true; });
    } else if (highlight === "impact") {
      var queue = [selectedId];
      while (queue.length) {
        var id = queue.shift();
        (outgoing[id] || []).forEach(function (next) {
          if (set[next]) return;
          set[next] = true;
          queue.push(next);
        });
      }
    } else {
      (incoming[selectedId] || []).forEach(function (id) { set[id] = true; });
      (outgoing[selectedId] || []).forEach(function (id) { set[id] = true; });
    }
    return set;
  }

  function selectNode(id) {
    selectedId = id;
    selectedEdge = null;
    showNodeDetails(byId[id]);
    refreshVisibility();
  }

  function selectEdge(edge) {
    selectedEdge = edge;
    selectedId = null;
    highlight = null;
    showEdgeDetails(edge);
    refreshVisibility();
  }

  function clearSelection() {
    selectedId = null;
    selectedEdge = null;
    highlight = null;
    showNodeDetails(null);
    refreshVisibility();
  }

  function setHighlight(mode) {
    if (!selectedId) return;
    highlight = highlight === mode ? null : mode;
    refreshVisibility();
  }

  function syncActionButtons() {
    ["upstream", "downstream", "impact"].forEach(function (mode) {
      var btn = document.getElementById("btn-" + (mode === "impact" ? "impact" : mode));
      if (btn) btn.classList.toggle("active", highlight === mode);
    });
    var inspect = document.getElementById("inspect-actions");
    if (inspect) inspect.style.display = selectedId ? "" : "none";
  }

  function nodeTitle(node) {
    return (node && (node.label || node.name || node.id)) || "";
  }

  function relationshipLabel(edge) {
    if (edge && edge.evidence) return typeLabel(edge.evidence);
    return typeLabel((edge && edge.type) || "") || "edge";
  }

  function prettyValue(value) {
    if (value === true) return "Yes";
    if (value === false) return "No";
    if (value == null) return "";
    if (typeof value === "string") {
      if (value === "high" || value === "medium" || value === "low") {
        return value.charAt(0).toUpperCase() + value.slice(1);
      }
      return value;
    }
    if (typeof value === "number") return String(value);
    return null;
  }

  function isSimpleMeta(value) {
    return prettyValue(value) !== null;
  }

  function showNodeDetails(node) {
    var empty = document.getElementById("details-empty");
    var body = document.getElementById("details-body");
    if (!node) {
      empty.hidden = false;
      body.hidden = true;
      return;
    }
    empty.hidden = true;
    body.hidden = false;
    var head = document.getElementById("details-head");
    head.textContent = "";
    var title = document.createElement("div");
    title.className = "inspect-title";
    title.textContent = nodeTitle(node);
    var kind = document.createElement("div");
    kind.className = "inspect-type";
    kind.textContent = typeLabel(node.type);
    head.appendChild(title);
    head.appendChild(kind);
    if (node.file_path) {
      var file = document.createElement("div");
      file.className = "inspect-file";
      file.textContent = node.file_path;
      head.appendChild(file);
    }
    if (node.line_number != null) {
      var line = document.createElement("div");
      line.className = "inspect-file";
      line.textContent = "Line " + node.line_number;
      head.appendChild(line);
    }
    var counts = document.createElement("div");
    counts.className = "inspect-counts";
    var up = (incoming[node.id] || []).length;
    var down = (outgoing[node.id] || []).length;
    counts.appendChild(countSpan(up, "upstream"));
    counts.appendChild(countSpan(down, "downstream"));
    head.appendChild(counts);

    renderConnections(node.id);
    renderSource(node);
    renderMetadata(node.metadata, ["file_path", "line_number", "reference", "evidence"]);
  }

  function countSpan(n, label) {
    var el = document.createElement("span");
    el.textContent = n + " " + label;
    return el;
  }

  function renderConnections(nodeId) {
    var list = document.getElementById("connections-list");
    list.textContent = "";
    var related = edges.filter(function (edge) {
      return edge.source === nodeId || edge.target === nodeId;
    });
    if (!related.length) {
      var none = document.createElement("p");
      none.className = "muted";
      none.textContent = "No connected edges.";
      list.appendChild(none);
      return;
    }
    related.forEach(function (edge) {
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "conn-row";
      btn.textContent = connectionLabel(edge);
      btn.addEventListener("click", function () {
        selectEdge(edge);
      });
      list.appendChild(btn);
    });
  }

  function connectionLabel(edge) {
    var src = nodeTitle(byId[edge.source]) || edge.source;
    var tgt = nodeTitle(byId[edge.target]) || edge.target;
    return src + " ── " + relationshipLabel(edge) + " ──→ " + tgt;
  }

  function showEdgeDetails(edge) {
    var empty = document.getElementById("details-empty");
    var body = document.getElementById("details-body");
    empty.hidden = true;
    body.hidden = false;
    var head = document.getElementById("details-head");
    head.textContent = "";
    var title = document.createElement("div");
    title.className = "inspect-title";
    title.textContent = (nodeTitle(byId[edge.source]) || edge.source)
      + " → "
      + (nodeTitle(byId[edge.target]) || edge.target);
    head.appendChild(title);
    var kv = document.createElement("div");
    kv.className = "kv";
    addKv(kv, "Relationship", relationshipLabel(edge) || "—");
    addKv(kv, "Confidence", prettyValue(edge.confidence) || "—");
    head.appendChild(kv);

    var list = document.getElementById("connections-list");
    list.textContent = "";
    var row = document.createElement("div");
    row.className = "conn-row";
    row.textContent = connectionLabel(edge);
    list.appendChild(row);

    renderSource(edge);
    renderMetadata(edge.metadata || {}, ["file_path", "line_number", "reference", "evidence"]);
  }

  function renderSource(item) {
    var list = document.getElementById("source-list");
    list.textContent = "";
    var meta = item.metadata || {};
    var filePath = item.file_path || meta.file_path || "";
    var lineNumber = item.line_number != null ? item.line_number : meta.line_number;
    var evidence = item.evidence || meta.evidence;
    var reference = meta.reference;
    var kv = document.createElement("div");
    kv.className = "kv";
    var any = false;
    if (evidence) {
      addKv(kv, "Evidence", String(evidence));
      any = true;
    }
    if (reference) {
      addKv(kv, "Reference", String(reference));
      any = true;
    }
    if (filePath) {
      var loc = filePath + (lineNumber != null ? ":" + lineNumber : "");
      addKv(kv, "Source", loc);
      any = true;
    } else if (lineNumber != null) {
      addKv(kv, "Line", String(lineNumber));
      any = true;
    }
    if (any) list.appendChild(kv);
    var excerpt = item.source_excerpt;
    if (excerpt && excerpt.text) {
      var pre = document.createElement("pre");
      pre.className = "excerpt";
      pre.textContent = excerpt.text;
      list.appendChild(pre);
    }
    if (!list.childNodes.length) {
      var none = document.createElement("p");
      none.className = "muted";
      none.textContent = "No static evidence for this item.";
      list.appendChild(none);
    }
  }

  function renderMetadata(meta, skip) {
    var list = document.getElementById("meta-list");
    list.textContent = "";
    meta = meta || {};
    skip = skip || [];
    var keys = Object.keys(meta).filter(function (key) {
      return skip.indexOf(key) === -1 && meta[key] != null && meta[key] !== "";
    });
    if (!keys.length) {
      var none = document.createElement("p");
      none.className = "muted";
      none.textContent = "No extra metadata.";
      list.appendChild(none);
      return;
    }
    var simple = document.createElement("div");
    simple.className = "kv";
    var complex = [];
    keys.forEach(function (key) {
      var value = meta[key];
      if (isSimpleMeta(value)) addKv(simple, key, prettyValue(value));
      else complex.push(key);
    });
    if (simple.childNodes.length) list.appendChild(simple);
    complex.forEach(function (key) {
      var label = document.createElement("div");
      label.className = "kv-key";
      label.textContent = key;
      var pre = document.createElement("pre");
      pre.className = "meta-json";
      try {
        pre.textContent = JSON.stringify(meta[key], null, 2);
      } catch (err) {
        pre.textContent = String(meta[key]);
      }
      list.appendChild(label);
      list.appendChild(pre);
    });
  }

  function addKv(root, key, value) {
    var k = document.createElement("div");
    k.className = "kv-key";
    k.textContent = key;
    var v = document.createElement("div");
    v.className = "kv-val";
    v.textContent = value;
    root.appendChild(k);
    root.appendChild(v);
  }

  function showTooltip(node, event) {
    tooltip.hidden = false;
    tooltip.textContent = "";
    var type = document.createElement("div");
    type.className = "tip-type";
    type.textContent = typeLabel(node.type);
    var name = document.createElement("div");
    name.textContent = nodeTitle(node);
    tooltip.appendChild(type);
    tooltip.appendChild(name);
    placeTooltip(event);
  }

  function placeTooltip(event) {
    var main = svg.parentElement.getBoundingClientRect();
    tooltip.style.left = event.clientX - main.left + 14 + "px";
    tooltip.style.top = event.clientY - main.top + 14 + "px";
  }

  function hideTooltip() {
    tooltip.hidden = true;
  }

  function fillTypeFilter() {
    var select = document.getElementById("type-filter");
    select.textContent = "";
    var types = uniqueTypes();
    var all = document.createElement("option");
    all.value = "";
    all.textContent = "All types";
    select.appendChild(all);
    types.forEach(function (type) {
      var opt = document.createElement("option");
      opt.value = type;
      opt.textContent = typeLabel(type);
      select.appendChild(opt);
    });
  }

  function uniqueTypes() {
    var types = [];
    nodes.forEach(function (node) {
      if (types.indexOf(node.type) === -1) types.push(node.type);
    });
    types.sort();
    return types;
  }

  function fillLegend() {
    var legend = document.getElementById("legend");
    legend.textContent = "";
    uniqueTypes().forEach(function (type) {
      var sw = document.createElement("span");
      sw.className = "legend-item";
      var swatch = document.createElement("span");
      swatch.className = "swatch";
      swatch.style.background = TYPE_COLORS[type] || "#64748b";
      swatch.style.color = TYPE_COLORS[type] || "#64748b";
      sw.appendChild(swatch);
      sw.appendChild(document.createTextNode(typeLabel(type)));
      legend.appendChild(sw);
    });
  }

  function fillStats() {
    var el = document.getElementById("stats");
    el.textContent = "";
    var shown = nodes.filter(matchesFilters).length;
    var nodesStat = document.createElement("span");
    var nodesN = document.createElement("strong");
    nodesN.textContent = String(shown);
    nodesStat.appendChild(nodesN);
    nodesStat.appendChild(document.createTextNode(" visible · "));
    var totalN = document.createElement("strong");
    totalN.textContent = String(nodes.length);
    nodesStat.appendChild(totalN);
    nodesStat.appendChild(document.createTextNode(" total"));
    el.appendChild(nodesStat);
  }

  function bind() {
    document.getElementById("theme-toggle").addEventListener("click", function () {
      applyTheme(isDark() ? "light" : "dark");
    });
    document.getElementById("search").addEventListener("input", function (event) {
      query = (event.target.value || "").trim().toLowerCase();
      refreshVisibility();
    });
    document.getElementById("type-filter").addEventListener("change", function (event) {
      typeFilter = event.target.value || "";
      relayout();
      fit();
    });
    document.getElementById("hide-functions").addEventListener("change", function (event) {
      hideFunctions = !!event.target.checked;
      relayout();
      fit();
    });
    document.getElementById("btn-fit").addEventListener("click", fit);
    document.getElementById("btn-reset-view").addEventListener("click", resetView);
    document.getElementById("btn-upstream").addEventListener("click", function () {
      setHighlight("upstream");
    });
    document.getElementById("btn-downstream").addEventListener("click", function () {
      setHighlight("downstream");
    });
    document.getElementById("btn-impact").addEventListener("click", function () {
      setHighlight("impact");
    });
    document.getElementById("btn-clear").addEventListener("click", clearSelection);

    svg.addEventListener("wheel", function (event) {
      event.preventDefault();
      var delta = event.deltaY < 0 ? 1.08 : 0.92;
      var next = Math.min(2.8, Math.max(0.2, transform.k * delta));
      var rect = svg.getBoundingClientRect();
      var mx = event.clientX - rect.left;
      var my = event.clientY - rect.top;
      var gx = (mx - transform.x) / transform.k;
      var gy = (my - transform.y) / transform.k;
      transform.k = next;
      transform.x = mx - gx * next;
      transform.y = my - gy * next;
      applyTransform();
    }, { passive: false });

    svg.addEventListener("mousedown", function (event) {
      if (nodeDrag) return;
      dragging = true;
      panMoved = false;
      dragOrigin = { x: event.clientX - transform.x, y: event.clientY - transform.y };
    });
    window.addEventListener("mousemove", function (event) {
      if (nodeDrag) {
        var pt = graphPoint(event);
        var nextX = pt.x - nodeDrag.dx;
        var nextY = pt.y - nodeDrag.dy;
        if (Math.abs(nextX - (positions[nodeDrag.id] || {}).x) > 2
          || Math.abs(nextY - (positions[nodeDrag.id] || {}).y) > 2) {
          nodeDrag.moved = true;
        }
        moveNode(nodeDrag.id, nextX, nextY);
        return;
      }
      if (!dragging) return;
      var nx = event.clientX - dragOrigin.x;
      var ny = event.clientY - dragOrigin.y;
      if (Math.abs(nx - transform.x) > 2 || Math.abs(ny - transform.y) > 2) panMoved = true;
      transform.x = nx;
      transform.y = ny;
      applyTransform();
    });
    window.addEventListener("mouseup", function () {
      if (nodeDrag && nodeEls[nodeDrag.id]) {
        nodeEls[nodeDrag.id].classList.remove("dragging");
      }
      nodeDrag = null;
      if (dragging && !panMoved && !nodeDrag) {
        /* empty-canvas click restores the full graph */
      }
      dragging = false;
    });
    svg.addEventListener("click", function () {
      if (panMoved) return;
      clearSelection();
    });
    window.addEventListener("keydown", function (event) {
      if (event.key === "Escape") clearSelection();
      if (event.key === "d" && (event.metaKey || event.ctrlKey)) return;
      if (event.key === "t" && !event.metaKey && !event.ctrlKey && event.target === document.body) {
        applyTheme(isDark() ? "light" : "dark");
      }
    });
  }
})();
