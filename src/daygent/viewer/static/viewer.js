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
    temp_view: "#a3a3a3",
    django_model: "#86efac",
    sqlalchemy_model: "#67e8f9"
  };

  /* Layer names are read off canonical asset names only; they never imply an edge. */
  var NAMESPACES = ["bronze", "silver", "gold"];

  var NODE_W = 188;
  var NODE_H = 52;
  var GAP_X = 86;
  var GAP_Y = 36;
  var COMPONENT_GAP_X = 120;
  var COMPONENT_GAP_Y = 90;
  /* Wrap component rows instead of growing one endless vertical tower. */
  var MAX_ROW_HEIGHT = 1600;
  var MAX_ROW_WIDTH = 5200;

  var raw = document.getElementById("daygent-data").textContent;
  var data = JSON.parse(raw);
  var nodes = data.nodes || [];
  var detailedEdges = (data.edges || []).filter(function (edge) {
    return edge && edge.source && edge.target;
  });
  var assetEdges = (data.asset_edges || []).filter(function (edge) {
    return edge && edge.source && edge.target;
  });
  var assetTypes = {};
  (data.asset_types || []).forEach(function (type) {
    assetTypes[type] = true;
  });

  var byId = {};
  nodes.forEach(function (node) {
    byId[node.id] = node;
  });

  var edges = detailedEdges;
  var outgoing = {};
  var incoming = {};

  function rebuildAdjacency(edgeList) {
    outgoing = {};
    incoming = {};
    edgeList.forEach(function (edge) {
      if (!byId[edge.source] || !byId[edge.target]) return;
      (outgoing[edge.source] || (outgoing[edge.source] = [])).push(edge.target);
      (incoming[edge.target] || (incoming[edge.target] = [])).push(edge.source);
    });
  }

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
  var showDetails = false;
  var filterMode = "context";
  var focusId = null;
  var query = "";
  var searchMatches = [];
  var searchCursor = 0;
  var nodeEls = {};
  var edgeEls = [];
  var positions = {};
  var drawnNodes = [];
  var drawnEdges = [];

  /* Asset lineage is the default read: implementation nodes stay in the data
     but are contracted into `asset_edges` until the user asks for detail. */
  var hasAssetView = assetEdges.length > 0
    || nodes.some(function (node) { return assetTypes[node.type]; });

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

  function adjacencyOf(nodeList, edgeList) {
    var out = {};
    var inc = {};
    var present = {};
    nodeList.forEach(function (node) {
      present[node.id] = true;
      out[node.id] = [];
      inc[node.id] = [];
    });
    edgeList.forEach(function (edge) {
      if (!present[edge.source] || !present[edge.target]) return;
      if (edge.source === edge.target) return;
      out[edge.source].push(edge.target);
      inc[edge.target].push(edge.source);
    });
    return { out: out, inc: inc };
  }

  /* Weakly connected components, so unrelated pipelines are laid out apart
     instead of all piling into rank 0 of one shared column. */
  function components(nodeList, adj) {
    var seen = {};
    var groups = [];
    nodeList.forEach(function (node) {
      if (seen[node.id]) return;
      var group = [];
      var stack = [node.id];
      while (stack.length) {
        var id = stack.pop();
        if (seen[id]) continue;
        seen[id] = true;
        group.push(id);
        (adj.out[id] || []).forEach(function (next) {
          if (!seen[next]) stack.push(next);
        });
        (adj.inc[id] || []).forEach(function (prev) {
          if (!seen[prev]) stack.push(prev);
        });
      }
      group.sort();
      groups.push(group);
    });
    return groups;
  }

  /* Longest-path layering. Cycles are broken by the visit guard, so a
     temp-view loop or mutual import cannot hang the layout. */
  function rankComponent(group, adj) {
    var rank = {};
    var inGroup = {};
    group.forEach(function (id) {
      rank[id] = 0;
      inGroup[id] = true;
    });
    var roots = group.filter(function (id) {
      return !(adj.inc[id] || []).some(function (prev) { return inGroup[prev]; });
    });
    if (!roots.length) roots = [group[0]];
    var guard = group.length * group.length + group.length;
    var queue = roots.slice();
    while (queue.length && guard > 0) {
      guard -= 1;
      var id = queue.shift();
      (adj.out[id] || []).forEach(function (next) {
        if (!inGroup[next]) return;
        var candidate = rank[id] + 1;
        if (candidate > rank[next]) {
          rank[next] = candidate;
          queue.push(next);
        }
      });
    }
    return rank;
  }

  function orderColumns(group, adj, rank) {
    var columns = {};
    group.forEach(function (id) {
      var r = rank[id] || 0;
      (columns[r] || (columns[r] = [])).push(id);
    });
    var order = Object.keys(columns).map(Number).sort(function (a, b) { return a - b; });
    order.forEach(function (r) {
      columns[r].sort(function (a, b) {
        return namespaceRank(a) - namespaceRank(b) || (a < b ? -1 : 1);
      });
    });
    var index = {};
    order.forEach(function (r) {
      columns[r].forEach(function (id, i) { index[id] = i; });
    });
    /* Barycenter sweeps: pull each node next to its neighbours to cut crossings. */
    for (var pass = 0; pass < 3; pass += 1) {
      var forward = pass % 2 === 0;
      var sweep = forward ? order : order.slice().reverse();
      sweep.forEach(function (r) {
        columns[r].sort(function (a, b) {
          return barycenter(a, forward) - barycenter(b, forward)
            || namespaceRank(a) - namespaceRank(b)
            || (a < b ? -1 : 1);
        });
        columns[r].forEach(function (id, i) { index[id] = i; });
      });
    }
    return { columns: columns, order: order };

    function barycenter(id, forward) {
      var side = forward ? (adj.inc[id] || []) : (adj.out[id] || []);
      var neighbors = side.length ? side : (adj.inc[id] || []).concat(adj.out[id] || []);
      if (!neighbors.length) return index[id] != null ? index[id] : 0;
      var sum = 0;
      var count = 0;
      neighbors.forEach(function (other) {
        if (index[other] == null) return;
        sum += index[other];
        count += 1;
      });
      return count ? sum / count : (index[id] || 0);
    }
  }

  function layoutComponent(group, adj) {
    var rank = rankComponent(group, adj);
    var laid = orderColumns(group, adj, rank);
    var local = {};
    var width = 0;
    var height = 0;
    laid.order.forEach(function (r, column) {
      laid.columns[r].forEach(function (id, row) {
        var x = column * (NODE_W + GAP_X);
        var y = row * (NODE_H + GAP_Y);
        local[id] = { x: x, y: y };
        width = Math.max(width, x + NODE_W);
        height = Math.max(height, y + NODE_H);
      });
    });
    return { positions: local, width: width, height: height, size: group.length };
  }

  function layout(nodeList, edgeList) {
    var adj = adjacencyOf(nodeList, edgeList);
    var groups = components(nodeList, adj);
    var singles = [];
    var blocks = [];
    groups.forEach(function (group) {
      if (group.length === 1) {
        singles.push(group[0]);
        return;
      }
      blocks.push(layoutComponent(group, adj));
    });
    /* Bigger lineage chains first; the reader should meet them before strays. */
    blocks.sort(function (a, b) {
      return b.size - a.size || b.width - a.width;
    });
    if (singles.length) blocks.push(gridBlock(singles));

    var pos = {};
    var rowTop = 0;
    var rowLeft = 0;
    var rowHeight = 0;
    blocks.forEach(function (block) {
      var wrapWidth = rowLeft > 0 && rowLeft + block.width > MAX_ROW_WIDTH;
      var wrapHeight = rowLeft > 0 && rowHeight > 0
        && Math.max(rowHeight, block.height) > MAX_ROW_HEIGHT;
      if (wrapWidth || wrapHeight) {
        rowTop += rowHeight + COMPONENT_GAP_Y;
        rowLeft = 0;
        rowHeight = 0;
      }
      Object.keys(block.positions).forEach(function (id) {
        pos[id] = {
          x: block.positions[id].x + rowLeft,
          y: block.positions[id].y + rowTop
        };
      });
      rowLeft += block.width + COMPONENT_GAP_X;
      rowHeight = Math.max(rowHeight, block.height);
    });
    return pos;
  }

  /* Disconnected nodes carry no direction, so they pack as a block rather than
     stretching the canvas into a single column. */
  function gridBlock(ids) {
    var perColumn = Math.max(1, Math.floor(MAX_ROW_HEIGHT / (NODE_H + GAP_Y)));
    var columns = Math.max(1, Math.ceil(ids.length / perColumn));
    var rows = Math.ceil(ids.length / columns);
    var local = {};
    var width = 0;
    var height = 0;
    ids.slice().sort().forEach(function (id, i) {
      var column = Math.floor(i / rows);
      var row = i % rows;
      var x = column * (NODE_W + GAP_X);
      var y = row * (NODE_H + GAP_Y);
      local[id] = { x: x, y: y };
      width = Math.max(width, x + NODE_W);
      height = Math.max(height, y + NODE_H);
    });
    return { positions: local, width: width, height: height, size: 1 };
  }

  function namespaceOf(node) {
    if (!node) return "";
    var id = String(node.id || "");
    var cut = id.indexOf(":");
    var qualified = (cut === -1 ? id : id.slice(cut + 1)).toLowerCase();
    for (var i = 0; i < NAMESPACES.length; i += 1) {
      var layer = NAMESPACES[i];
      if (qualified.indexOf(layer + ".") === 0 || qualified.indexOf("." + layer + ".") !== -1) {
        return layer;
      }
    }
    return "";
  }

  function namespaceRank(id) {
    var layer = namespaceOf(byId[id]);
    var at = NAMESPACES.indexOf(layer);
    return at === -1 ? NAMESPACES.length : at;
  }

  function inViewMode(node) {
    if (showDetails || !hasAssetView) return true;
    return !!assetTypes[node.type];
  }

  function activeEdges() {
    return showDetails || !hasAssetView ? detailedEdges : assetEdges;
  }

  function contextOf(ids, edgeList) {
    var set = {};
    Object.keys(ids).forEach(function (id) { set[id] = true; });
    edgeList.forEach(function (edge) {
      if (ids[edge.source]) set[edge.target] = true;
      if (ids[edge.target]) set[edge.source] = true;
    });
    return set;
  }

  function componentIds(startId, nodeList, edgeList) {
    var adj = adjacencyOf(nodeList, edgeList);
    var seen = {};
    var stack = [startId];
    while (stack.length) {
      var id = stack.pop();
      if (seen[id]) continue;
      seen[id] = true;
      (adj.out[id] || []).forEach(function (next) { if (!seen[next]) stack.push(next); });
      (adj.inc[id] || []).forEach(function (prev) { if (!seen[prev]) stack.push(prev); });
    }
    return seen;
  }

  function structuralGraph() {
    var edgeList = activeEdges();
    var vis = nodes.filter(inViewMode);
    if (focusId && byId[focusId]) {
      var keep = componentIds(focusId, vis, edgeList);
      vis = vis.filter(function (node) { return keep[node.id]; });
    }
    if (typeFilter) {
      var matching = {};
      vis.forEach(function (node) {
        if (node.type === typeFilter) matching[node.id] = true;
      });
      var allowed = filterMode === "only" ? matching : contextOf(matching, edgeList);
      vis = vis.filter(function (node) { return allowed[node.id]; });
    }
    var ids = {};
    vis.forEach(function (node) { ids[node.id] = true; });
    return {
      nodes: vis,
      edges: edgeList.filter(function (edge) {
        return ids[edge.source] && ids[edge.target];
      })
    };
  }

  function relayout() {
    var subset = structuralGraph();
    drawnNodes = subset.nodes;
    drawnEdges = subset.edges;
    edges = drawnEdges;
    rebuildAdjacency(drawnEdges);
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
    drawnEdges.forEach(function (edge) {
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
    drawnNodes.forEach(function (node) {
      var p = positions[node.id];
      if (!p) return;
      var color = TYPE_COLORS[node.type] || "#64748b";
      var layer = namespaceOf(node);
      var g = svgEl("g", {
        class: "node",
        "data-id": node.id,
        "data-namespace": layer || "other"
      });
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
      if (layer) {
        var badge = svgEl("text", {
          x: String(NODE_W - 12),
          y: "38",
          "text-anchor": "end",
          fill: color,
          class: "node-namespace"
        });
        badge.textContent = layer;
        g.appendChild(badge);
      }
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
    return drawnNodes.map(function (node) { return node.id; });
  }

  /* Search never deletes the graph: a query marks matches and their immediate
     lineage, and everything else is dimmed so the context stays readable. */
  function matchesQuery(node) {
    if (!query) return false;
    var hay = (node.name + " " + node.id + " " + node.type + " " + (node.label || "")).toLowerCase();
    return hay.indexOf(query) !== -1;
  }

  function isContextNode(node) {
    if (typeFilter && node.type !== typeFilter) return true;
    return false;
  }

  function refreshSearchMatches() {
    searchMatches = query
      ? drawnNodes.filter(matchesQuery).map(function (node) { return node.id; })
      : [];
    if (searchCursor >= searchMatches.length) searchCursor = 0;
  }

  function searchSet() {
    if (!searchMatches.length) return null;
    var set = {};
    searchMatches.forEach(function (id) {
      set[id] = true;
      (incoming[id] || []).forEach(function (prev) { set[prev] = true; });
      (outgoing[id] || []).forEach(function (next) { set[next] = true; });
    });
    return set;
  }

  function stepSearch(delta) {
    if (!searchMatches.length) return;
    searchCursor = (searchCursor + delta + searchMatches.length) % searchMatches.length;
    var id = searchMatches[searchCursor];
    selectNode(id);
    focusNode(id);
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
    var matched = searchSet();
    Object.keys(nodeEls).forEach(function (id) {
      var el = nodeEls[id];
      var show = !!visible[id];
      el.style.display = show ? "" : "none";
      el.classList.remove("dim", "hl", "selected", "context");
      if (!show) return;
      if (id === selectedId) el.classList.add("selected");
      if (isContextNode(byId[id] || {})) el.classList.add("context");
      if (matched) {
        var isMatch = searchMatches.indexOf(id) !== -1;
        el.classList.add(isMatch ? "hl" : (matched[id] ? "context" : "dim"));
      } else if (related) {
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
      if (matched) {
        on = searchMatches.indexOf(item.edge.source) !== -1
          || searchMatches.indexOf(item.edge.target) !== -1;
        if (!on) item.el.classList.add("dim");
      } else if (related) {
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

  /* Focus lineage reduces the canvas to the one connected flow a developer is
     reading, which is what makes a large repository usable. */
  function focusLineage(id) {
    if (!id || !byId[id]) return;
    focusId = id;
    relayout();
    if (!positions[id]) {
      focusId = null;
      relayout();
      return;
    }
    selectedId = id;
    refreshVisibility();
    fit();
  }

  function clearFocus() {
    if (!focusId) return;
    focusId = null;
    relayout();
    refreshVisibility();
    fit();
  }

  function syncActionButtons() {
    ["upstream", "downstream", "impact"].forEach(function (mode) {
      var btn = document.getElementById("btn-" + mode);
      if (btn) btn.classList.toggle("active", highlight === mode);
    });
    var focusBtn = document.getElementById("btn-focus");
    if (focusBtn) focusBtn.classList.toggle("active", !!focusId);
    var clearFocusBtn = document.getElementById("btn-clear-focus");
    if (clearFocusBtn) clearFocusBtn.hidden = !focusId;
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
    renderViaPath(edge, list);

    renderSource(edge);
    renderMetadata(edge.metadata || {}, ["file_path", "line_number", "reference", "evidence"]);
  }

  /* A contracted asset edge hides real hops. Show them so the visual
     simplification never costs the reader the actual path. */
  function renderViaPath(edge, list) {
    var meta = edge.metadata || {};
    var via = meta.via;
    if (!via || !via.length) return;
    var heading = document.createElement("div");
    heading.className = "kv-key";
    heading.textContent = "Path (" + via.length + " hidden hop"
      + (via.length === 1 ? "" : "s") + ")";
    list.appendChild(heading);
    var path = document.createElement("div");
    path.className = "via-path";
    var chain = [edge.source].concat(via).concat([edge.target]);
    chain.forEach(function (id, at) {
      var step = document.createElement("div");
      step.className = "via-step";
      var node = byId[id];
      step.textContent = (at === 0 ? "" : "↓ ") + (nodeTitle(node) || id);
      if (node && at > 0 && at < chain.length - 1) {
        step.textContent += "  (" + typeLabel(node.type) + ")";
      }
      path.appendChild(step);
    });
    list.appendChild(path);
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
    var types = uniqueTypes();
    if (typeFilter && types.indexOf(typeFilter) === -1) typeFilter = "";
    select.textContent = "";
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
    select.value = typeFilter;
  }

  function uniqueTypes() {
    var types = [];
    nodes.forEach(function (node) {
      if (!inViewMode(node)) return;
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
    var layers = NAMESPACES.filter(function (layer) {
      return nodes.some(function (node) { return namespaceOf(node) === layer; });
    });
    layers.forEach(function (layer) {
      var item = document.createElement("span");
      item.className = "legend-item legend-namespace";
      item.textContent = layer + ".*";
      legend.appendChild(item);
    });
  }

  function fillStats() {
    var el = document.getElementById("stats");
    el.textContent = "";
    var nodesStat = document.createElement("span");
    var nodesN = document.createElement("strong");
    nodesN.textContent = String(drawnNodes.length);
    nodesStat.appendChild(nodesN);
    nodesStat.appendChild(document.createTextNode(" visible · "));
    var totalN = document.createElement("strong");
    totalN.textContent = String(nodes.length);
    nodesStat.appendChild(totalN);
    nodesStat.appendChild(document.createTextNode(" total"));
    if (!showDetails && hasAssetView) {
      nodesStat.appendChild(document.createTextNode(" · asset lineage"));
    }
    if (focusId && byId[focusId]) {
      nodesStat.appendChild(document.createTextNode(" · focused on " + nodeTitle(byId[focusId])));
    }
    el.appendChild(nodesStat);
  }

  function bind() {
    document.getElementById("theme-toggle").addEventListener("click", function () {
      applyTheme(isDark() ? "light" : "dark");
    });
    var search = document.getElementById("search");
    search.addEventListener("input", function (event) {
      query = (event.target.value || "").trim().toLowerCase();
      searchCursor = 0;
      refreshSearchMatches();
      refreshVisibility();
      if (searchMatches.length) {
        selectNode(searchMatches[0]);
        focusNode(searchMatches[0]);
      }
    });
    search.addEventListener("keydown", function (event) {
      if (event.key !== "Enter") return;
      event.preventDefault();
      stepSearch(event.shiftKey ? -1 : 1);
    });
    document.getElementById("type-filter").addEventListener("change", function (event) {
      typeFilter = event.target.value || "";
      relayout();
      refreshSearchMatches();
      refreshVisibility();
      fit();
    });
    document.getElementById("filter-mode").addEventListener("change", function (event) {
      filterMode = event.target.value === "only" ? "only" : "context";
      relayout();
      refreshSearchMatches();
      refreshVisibility();
      fit();
    });
    document.getElementById("show-details").addEventListener("change", function (event) {
      showDetails = !!event.target.checked;
      relayout();
      fillTypeFilter();
      refreshSearchMatches();
      refreshVisibility();
      fit();
    });
    document.getElementById("btn-fit").addEventListener("click", fit);
    document.getElementById("btn-reset-view").addEventListener("click", resetView);
    document.getElementById("btn-focus").addEventListener("click", function () {
      if (focusId) clearFocus();
      else if (selectedId) focusLineage(selectedId);
    });
    document.getElementById("btn-clear-focus").addEventListener("click", clearFocus);
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
