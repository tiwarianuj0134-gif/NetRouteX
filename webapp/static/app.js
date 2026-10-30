(() => {
  "use strict";

  const state = {
    topologies: [],
    topology: null,
    topologyName: "",
    result: null,
    comparison: [],
    failureBaseline: null,
    failureResult: null,
    failureState: null,
    selectedElement: null,
    selectedRoute: null,
    graphTransform: { x: 0, y: 0, scale: 1 },
    panStart: null,
    animationFrames: new Set(),
    busy: false,
  };

  const byId = (id) => document.getElementById(id);
  const html = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[char]);
  const pairKey = (a, b) => [String(a), String(b)].sort().join("\u0000");
  const number = (value, places = 2) =>
    value === null || value === undefined || !Number.isFinite(Number(value))
      ? "Not measured"
      : Number(value).toFixed(places).replace(/\.?0+$/, "");

  async function api(url, options = {}) {
    const response = await fetch(url, options);
    const type = response.headers.get("content-type") || "";
    const body = type.includes("application/json")
      ? await response.json()
      : await response.text();
    if (!response.ok) {
      const message = body && typeof body === "object" ? body.error : body;
      throw new Error(message || `Request failed (${response.status}).`);
    }
    return body;
  }

  function postJson(url, body) {
    return api(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  }

  function toast(message, error = false) {
    const item = document.createElement("div");
    item.className = `toast${error ? " error" : ""}`;
    item.textContent = message;
    byId("toast-region").append(item);
    window.setTimeout(() => item.remove(), 5000);
  }

  function setPage(page) {
    document.querySelectorAll(".page").forEach((section) => {
      section.classList.toggle("active", section.id === `page-${page}`);
    });
    document.querySelectorAll(".nav-item").forEach((button) => {
      button.classList.toggle("active", button.dataset.page === page);
    });
    const active = document.querySelector(`.nav-item[data-page="${page}"]`);
    byId("page-crumb").textContent = active ? active.textContent.trim() : "Overview";
    if (page === "topology") renderTopology();
    if (page === "metrics") renderMetrics();
  }

  function populateTopologySelectors() {
    const selectors = [byId("topology-select"), byId("simulation-topology")];
    selectors.forEach((select) => {
      const current = state.topologyName;
      select.replaceChildren();
      state.topologies.filter((entry) => entry.valid).forEach((entry) => {
        const option = document.createElement("option");
        option.value = entry.name;
        option.textContent = entry.name;
        select.append(option);
      });
      if (state.topologyName && !state.topologies.some((entry) => entry.name === current)) {
        const option = document.createElement("option");
        option.value = current;
        option.textContent = current;
        select.append(option);
      }
      if (state.topologyName) select.value = state.topologyName;
    });
  }

  async function loadTopology(name) {
    const result = await api(`/api/topologies/${encodeURIComponent(name)}`);
    acceptTopology(result.name, result.topology);
  }

  function acceptTopology(name, config) {
    state.topology = config;
    state.topologyName = name;
    state.result = null;
    state.comparison = [];
    state.failureBaseline = null;
    state.failureResult = null;
    state.failureState = null;
    state.selectedElement = null;
    state.selectedRoute = null;
    populateTopologySelectors();
    updateCounts();
    populateClientSelectors();
    populateFailureLinks();
    setRunStatus("Idle", "Topology loaded; no run yet");
    renderSimulationResult(null);
    renderComparison([]);
    renderFailureResult();
    renderEvents([], "event-list");
    renderEvents([], "failure-events");
    renderConfiguredEvents(config.changes || []);
    renderTopology();
    renderMetrics();
    toast(`Loaded and validated ${name}.`);
  }

  function updateCounts() {
    const config = state.topology;
    const count = config
      ? { routers: config.routers.length, clients: config.clients.length, links: allLinkRows(config).length }
      : { routers: null, clients: null, links: null };
    byId("router-count").textContent = count.routers ?? "—";
    byId("client-count").textContent = count.clients ?? "—";
    byId("link-count").textContent = count.links ?? "—";
    byId("snapshot-topology").textContent = state.topologyName || "No topology loaded";
    byId("topology-size").textContent = config
      ? `${count.routers} routers · ${count.clients} clients · ${count.links} links`
      : "—";
    byId("topology-validity").innerHTML = config
      ? "<i></i>Validated"
      : "<i></i>No topology";
    byId("topology-validity").classList.toggle("error", !config);
    byId("map-state").textContent = config ? "TOPOLOGY LOADED" : "Awaiting topology";
    byId("link-count-note").textContent = state.result
      ? `${state.result.activeLinks.length} active after last run`
      : "Configured connections";
  }

  function setRunStatus(status, note = "") {
    byId("run-status").textContent = status;
    byId("run-status-note").textContent = note;
    const badge = byId("sim-status-badge");
    badge.querySelector("span").textContent = status;
    badge.classList.toggle("error", status === "Error");
  }

  function populateClientSelectors() {
    const clients = state.topology?.clients || [];
    for (const id of ["source-select", "destination-select", "failure-source", "failure-destination"]) {
      const select = byId(id);
      const prior = select.value;
      select.replaceChildren();
      if (!clients.length) {
        const option = document.createElement("option");
        option.value = "";
        option.textContent = "No clients";
        select.append(option);
        select.disabled = true;
        continue;
      }
      select.disabled = false;
      clients.forEach((client) => {
        const option = document.createElement("option");
        option.value = client;
        option.textContent = client;
        select.append(option);
      });
      select.value = clients.includes(prior) ? prior : clients[0];
    }
    if (clients.length > 1) {
      byId("destination-select").value = clients[1];
      byId("failure-destination").value = clients[1];
    }
  }

  function allLinkRows(config = state.topology) {
    if (!config) return [];
    const map = new Map();
    (config.links || []).forEach((row) => map.set(pairKey(row[0], row[1]), row));
    (config.changes || []).forEach((event) => {
      if (event[2] === "up" && Array.isArray(event[1]) && event[1].length === 6) {
        map.set(pairKey(event[1][0], event[1][1]), event[1]);
      }
    });
    return [...map.values()];
  }

  function currentActiveLinks() {
    if (state.result?.activeLinks) return state.result.activeLinks;
    return (state.topology?.links || []).map((row) => ({
      node1: row[0], node2: row[1], cost12: row[4], cost21: row[5],
    }));
  }

  function populateFailureLinks() {
    const select = byId("failure-link");
    select.replaceChildren();
    const rows = (state.topology?.links || []).filter((row) =>
      state.topology.routers.includes(row[0]) && state.topology.routers.includes(row[1]));
    rows.forEach((row) => {
      const option = document.createElement("option");
      option.value = JSON.stringify([row[0], row[1]]);
      option.textContent = `${row[0]} — ${row[1]}`;
      select.append(option);
    });
    select.disabled = !rows.length;
    byId("failure-action").disabled = !rows.length;
    updateFailureLinkInfo();
  }

  function selectedFailureLink() {
    try { return JSON.parse(byId("failure-link").value); } catch { return null; }
  }

  function updateFailureLinkInfo() {
    const [a, b] = selectedFailureLink() || [];
    const row = (state.topology?.links || []).find((link) =>
      (link[0] === a && link[1] === b) || (link[0] === b && link[1] === a));
    if (!row) {
      byId("failure-link-info").textContent = "Load a topology with at least one router-to-router link.";
      return;
    }
    const active = currentActiveLinks().some((link) => pairKey(link.node1, link.node2) === pairKey(a, b));
    const action = state.failureState === "down" ? "up" : "down";
    byId("failure-link-info").innerHTML =
      `<div class="detail-row"><span>Current run state</span><b>${active ? "Active" : "Down"}</b></div>` +
      `<div class="detail-row"><span>Directional costs</span><b>${html(row[0])} → ${html(row[1])}: ${html(row[4])} · ${html(row[1])} → ${html(row[0])}: ${html(row[5])}</b></div>` +
      `<div class="detail-row"><span>Next experiment</span><b>Link ${action}</b></div>`;
    byId("failure-action").textContent = action === "down" ? "Take link down" : "Restore link";
    byId("failure-state-label").innerHTML =
      `<i></i>${active ? "Link active" : "Link down"}${state.failureState ? ` · last event ${state.failureState}` : ""}`;
  }

  function svgEl(tag, attrs = {}) {
    const el = document.createElementNS("http://www.w3.org/2000/svg", tag);
    Object.entries(attrs).forEach(([key, value]) => el.setAttribute(key, String(value)));
    return el;
  }

  function nodePositions(config, width = 1000, height = 600) {
    const nodes = [...config.routers, ...config.clients];
    const locations = config.visualize?.locations || {};
    const available = nodes.filter((node) => Array.isArray(locations[node]));
    if (available.length === nodes.length && nodes.length) {
      const xs = available.map((node) => Number(locations[node][0]));
      const ys = available.map((node) => Number(locations[node][1]));
      const minX = Math.min(...xs), maxX = Math.max(...xs);
      const minY = Math.min(...ys), maxY = Math.max(...ys);
      return new Map(nodes.map((node) => {
        const [gx, gy] = locations[node].map(Number);
        return [node, {
          x: minX === maxX ? width / 2 : 110 + ((gx - minX) / (maxX - minX)) * (width - 220),
          y: minY === maxY ? height / 2 : 85 + ((gy - minY) / (maxY - minY)) * (height - 170),
        }];
      }));
    }
    const radius = Math.min(width, height) * .34;
    return new Map(nodes.map((node, index) => {
      const angle = -Math.PI / 2 + (Math.PI * 2 * index) / Math.max(1, nodes.length);
      return [node, { x: width / 2 + Math.cos(angle) * radius, y: height / 2 + Math.sin(angle) * radius }];
    }));
  }

  function resultRoute() {
    if (!state.result) return null;
    return state.result.routes.find((route) =>
      route.source === byId("source-select").value &&
      route.destination === byId("destination-select").value) || null;
  }

  function renderGraph(svg, interactive) {
    svg.replaceChildren();
    if (!state.topology) return;
    const width = 1000, height = 600;
    svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
    svg.setAttribute("aria-label", interactive
      ? "Interactive network topology"
      : "Network topology with the selected observed route");
    const transform = interactive ? state.graphTransform : { x: 0, y: 0, scale: .66 };
    const scene = svgEl("g", { transform: `translate(${transform.x} ${transform.y}) scale(${transform.scale})` });
    const positions = nodePositions(state.topology, width, height);
    const active = new Set(currentActiveLinks().map((edge) => pairKey(edge.node1, edge.node2)));
    const path = resultRoute()?.path || state.selectedRoute || [];
    const pathEdges = new Set(path.slice(1).map((node, index) => pairKey(path[index], node)));

    allLinkRows().forEach((row) => {
      const [a, b] = row;
      const pa = positions.get(a), pb = positions.get(b);
      if (!pa || !pb) return;
      const key = pairKey(a, b);
      const isActive = active.has(key);
      const edgeClass = `graph-edge ${isActive ? "active" : "down"}${pathEdges.has(key) ? " route" : ""}${state.selectedElement?.type === "link" && state.selectedElement.key === key ? " selected" : ""}`;
      const hit = svgEl("line", { x1: pa.x, y1: pa.y, x2: pb.x, y2: pb.y, class: "edge-hit" });
      const line = svgEl("line", { x1: pa.x, y1: pa.y, x2: pb.x, y2: pb.y, class: edgeClass });
      const linkGroup = svgEl("g", { class: "edge-group" });
      linkGroup.append(hit, line);
      const text = `${row[4]} / ${row[5]}`;
      const midX = (pa.x + pb.x) / 2, midY = (pa.y + pb.y) / 2;
      const labelBg = svgEl("rect", { x: midX - 27, y: midY - 10, width: 54, height: 20, rx: 6, class: "edge-label-bg" });
      const label = svgEl("text", { x: midX, y: midY + .5, class: "edge-label" });
      label.textContent = text;
      linkGroup.append(labelBg, label);
      if (interactive) {
        linkGroup.style.cursor = "pointer";
        linkGroup.addEventListener("click", (event) => {
          event.stopPropagation();
          state.selectedElement = { type: "link", key, endpoints: [a, b] };
          renderInspector();
          renderTopology();
        });
      }
      scene.append(linkGroup);
    });

    [...state.topology.routers, ...state.topology.clients].forEach((node) => {
      const point = positions.get(node);
      if (!point) return;
      const isRouter = state.topology.routers.includes(node);
      const group = svgEl("g", {
        class: `graph-node ${isRouter ? "router" : "client"}${state.selectedElement?.type === "node" && state.selectedElement.name === node ? " selected" : ""}`,
        transform: `translate(${point.x} ${point.y})`,
      });
      group.append(svgEl("circle", { r: isRouter ? 24 : 20, class: "node-ring" }));
      const label = svgEl("text", { x: 0, y: 1, class: "node-label" });
      label.textContent = node;
      group.append(label);
      const subtitle = svgEl("text", { x: 0, y: isRouter ? 38 : 34, class: "node-sub" });
      subtitle.textContent = isRouter ? "ROUTER" : "CLIENT";
      group.append(subtitle);
      if (interactive) {
        group.addEventListener("click", (event) => {
          event.stopPropagation();
          state.selectedElement = { type: "node", name: node };
          renderInspector();
          renderTopology();
        });
      }
      scene.append(group);
    });
    svg.append(scene);

    if (interactive && !svg.dataset.panWired) {
      svg.dataset.panWired = "true";
      svg.addEventListener("pointerdown", (event) => {
        if (event.target.closest(".graph-node, .edge-group")) return;
        state.panStart = { x: event.clientX, y: event.clientY, tx: state.graphTransform.x, ty: state.graphTransform.y };
        svg.setPointerCapture(event.pointerId);
      });
      svg.addEventListener("pointermove", (event) => {
        if (!state.panStart) return;
        const bounds = svg.getBoundingClientRect();
        const factor = 1000 / Math.max(1, bounds.width);
        state.graphTransform.x = state.panStart.tx + (event.clientX - state.panStart.x) * factor;
        state.graphTransform.y = state.panStart.ty + (event.clientY - state.panStart.y) * factor;
        const activeScene = svg.querySelector("g");
        if (activeScene) activeScene.setAttribute("transform", `translate(${state.graphTransform.x} ${state.graphTransform.y}) scale(${state.graphTransform.scale})`);
      });
      const endPan = () => { state.panStart = null; };
      svg.addEventListener("pointerup", endPan);
      svg.addEventListener("pointercancel", endPan);
      svg.addEventListener("wheel", (event) => {
        event.preventDefault();
        zoom(event.deltaY < 0 ? 1.1 : .9);
      }, { passive: false });
    }
  }

  function renderTopology() {
    stopPacketAnimations();
    renderGraph(byId("network-svg"), true);
    const preview = byId("dashboard-graph");
    if (state.topology) {
      preview.innerHTML = '<svg aria-hidden="true"></svg>';
      renderGraph(preview.querySelector("svg"), false);
    } else {
      preview.innerHTML = '<div class="empty-state">Load a topology to view the network.</div>';
    }
    if (state.topology) renderGraph(byId("simulation-svg"), false);
    byId("graph-empty").hidden = Boolean(state.topology);
    updateFailureLinkInfo();
    updateCounts();
  }

  function zoom(factor) {
    state.graphTransform.scale = Math.min(2.2, Math.max(.45, state.graphTransform.scale * factor));
    const scene = byId("network-svg").querySelector("g");
    if (scene) scene.setAttribute("transform", `translate(${state.graphTransform.x} ${state.graphTransform.y}) scale(${state.graphTransform.scale})`);
  }

  function renderInspector() {
    const container = byId("inspector-content");
    const selection = state.selectedElement;
    if (!selection || !state.topology) {
      byId("inspector-type").textContent = "NETWORK";
      container.innerHTML = '<span class="empty-icon">⌖</span><b>Select a node or link</b><p>Details from the loaded topology and most recent run appear here.</p>';
      return;
    }
    if (selection.type === "link") {
      byId("inspector-type").textContent = "LINK";
      const [a, b] = selection.endpoints;
      const row = allLinkRows().find((link) => pairKey(link[0], link[1]) === selection.key);
      const active = currentActiveLinks().some((link) => pairKey(link.node1, link.node2) === selection.key);
      if (!row) return;
      container.innerHTML = `<h2>${html(a)} ↔ ${html(b)}</h2><div class="detail-list">
        <div class="detail-row"><span>Status</span><b>${active ? "Active" : "Down"}</b></div>
        <div class="detail-row"><span>${html(row[0])} → ${html(row[1])}</span><b>Cost ${html(row[4])}</b></div>
        <div class="detail-row"><span>${html(row[1])} → ${html(row[0])}</span><b>Cost ${html(row[5])}</b></div>
        <div class="detail-row"><span>Link ports</span><b>${html(row[2])} / ${html(row[3])}</b></div></div>
        <p class="muted">Directional costs are read from the topology. A failure experiment runs the engine on an isolated copy.</p>`;
      return;
    }
    const name = selection.name;
    const isRouter = state.topology.routers.includes(name);
    byId("inspector-type").textContent = isRouter ? "ROUTER" : "CLIENT";
    const adjacent = currentActiveLinks().filter((link) => link.node1 === name || link.node2 === name);
    const adjacencyText = adjacent.length
      ? adjacent.map((link) => link.node1 === name ? link.node2 : link.node1).join(", ")
      : "No active neighbors";
    const details = state.result?.routerStates?.[name];
    if (isRouter) {
      container.innerHTML = `<h2>Router ${html(name)}</h2><div class="detail-list">
        <div class="detail-row"><span>Active neighbors</span><b>${html(adjacencyText)}</b></div>
        <div class="detail-row"><span>Routing mode</span><b>${html(state.result?.algorithm || "Not run")}</b></div>
      </div>${details
        ? `<div class="eyebrow">ENGINE DEBUG STATE</div><pre class="debug-block">${html(details)}</pre>`
        : '<p class="muted">Run a simulation to inspect this router’s actual routing and forwarding state.</p>'}`;
    } else {
      const clientRoute = state.result?.routes?.filter((route) => route.source === name) || [];
      container.innerHTML = `<h2>Client ${html(name)}</h2><div class="detail-list">
        <div class="detail-row"><span>Connected neighbor</span><b>${html(adjacencyText)}</b></div>
        <div class="detail-row"><span>Observed destinations</span><b>${clientRoute.length || "Not run"}</b></div>
      </div><p class="muted">Clients generate traceroute packets through their attached router.</p>`;
    }
  }

  function selectedRouteRecord(result = state.result) {
    if (!result) return null;
    const source = byId("source-select").value;
    const destination = byId("destination-select").value;
    return result.routes.find((route) => route.source === source && route.destination === destination) || null;
  }

  function pathMarkup(path) {
    if (!path?.length) return '<span class="muted">No route observed</span>';
    return `<div class="path-display">${path.map((node, index) =>
      `${index ? '<span class="path-arrow">→</span>' : ""}<span class="path-node">${html(node)}</span>`
    ).join("")}</div>`;
  }

  function stopPacketAnimations() {
    state.animationFrames.forEach((frame) => window.cancelAnimationFrame(frame));
    state.animationFrames.clear();
  }

  function animatePacketRoute(route) {
    stopPacketAnimations();
    const note = byId("packet-animation-note");
    if (!route?.delivered || !Array.isArray(route.path) || route.path.length < 2) {
      note.textContent = route
        ? "No packet animation: the simulator did not observe a delivered route for this pair."
        : "Run a simulation to animate its observed packet route here.";
      return;
    }
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      note.textContent = "Reduced-motion preference is on; the selected observed route is highlighted without animation.";
      return;
    }

    const positions = nodePositions(state.topology);
    const points = route.path.map((node) => positions.get(node)).filter(Boolean);
    if (points.length !== route.path.length) {
      note.textContent = "The observed route contains a node without a display position.";
      return;
    }

    const svgs = [
      byId("simulation-svg"),
      byId("network-svg"),
      byId("dashboard-graph").querySelector("svg"),
    ].filter(Boolean);
    const packets = svgs.map((svg) => {
      const scene = svg.querySelector("g");
      if (!scene) return null;
      const halo = svgEl("circle", { r: 13, class: "packet-halo", "aria-hidden": "true" });
      const orb = svgEl("circle", { r: 7, class: "packet-orb", "aria-hidden": "true" });
      const title = svgEl("title");
      title.textContent = `Packet on observed route ${route.path.join(" to ")}`;
      orb.append(title);
      scene.append(halo, orb);
      return { halo, orb };
    }).filter(Boolean);
    if (!packets.length) return;

    const duration = Math.max(900, Math.min(2600, (points.length - 1) * 620));
    note.textContent = `Animating observed traceroute: ${route.path.join(" → ")}.`;
    let startedAt;
    const tick = (timestamp) => {
      if (startedAt === undefined) startedAt = timestamp;
      const progress = Math.min(1, (timestamp - startedAt) / duration);
      const segmentPosition = progress * (points.length - 1);
      const segment = Math.min(points.length - 2, Math.floor(segmentPosition));
      const fraction = progress === 1 ? 1 : segmentPosition - segment;
      const from = points[segment];
      const to = points[Math.min(segment + 1, points.length - 1)];
      const transform = `translate(${from.x + (to.x - from.x) * fraction} ${from.y + (to.y - from.y) * fraction})`;
      packets.forEach(({ halo, orb }) => {
        halo.setAttribute("transform", transform);
        orb.setAttribute("transform", transform);
      });
      if (progress < 1) {
        const frame = window.requestAnimationFrame(tick);
        state.animationFrames.add(frame);
      } else {
        packets.forEach(({ halo, orb }) => { halo.remove(); orb.remove(); });
        state.animationFrames.clear();
        note.textContent = `Animation complete. The displayed path is the simulator's observed traceroute: ${route.path.join(" → ")}.`;
      }
    };
    const frame = window.requestAnimationFrame(tick);
    state.animationFrames.add(frame);
  }

  function renderLatestResult(result, route) {
    const container = byId("latest-result");
    if (!result || !route) {
      container.className = "result-empty";
      container.innerHTML = '<span class="empty-icon">⌁</span><b>Ready when you are</b><p>Run the network to see observed routes and measured metrics.</p><button class="button secondary small" data-nav="simulation">Open simulation controls</button>';
      container.querySelector("[data-nav]")?.addEventListener("click", () => setPage("simulation"));
      return;
    }
    container.className = "latest-result-content";
    container.innerHTML = `<span class="result-chip ${route.delivered ? "success" : "failed"}">${route.delivered ? "PACKET DELIVERED" : "UNREACHABLE"}</span>
      <div class="eyebrow">${html(result.algorithm)} · ${html(route.source)} → ${html(route.destination)}</div>
      ${pathMarkup(route.path)}
      <div class="detail-row"><span>Observed route cost</span><b>${number(route.routeCost)}</b></div>`;
  }

  function renderSimulationResult(result) {
    const container = byId("simulation-result");
    const route = selectedRouteRecord(result);
    if (!result || !route) {
      container.className = "result-empty large";
      container.innerHTML = '<span class="empty-icon">⇢</span><b>No packet result yet</b><p>Start a simulation to see the selected source/destination route.</p>';
      byId("result-chip").textContent = "NO RUN";
      byId("result-chip").className = "result-chip";
      byId("simulation-metrics").replaceChildren();
      renderLatestResult(result, route);
      animatePacketRoute(route);
      return;
    }
    container.className = "";
    const status = route.delivered ? "DELIVERED" : "UNREACHABLE";
    byId("result-chip").textContent = status;
    byId("result-chip").className = `result-chip ${route.delivered ? "success" : "failed"}`;
    const pathNodes = route.path || [];
    const explanation = route.delivered
      ? `The actual traceroute reached ${html(route.destination)} through ${Math.max(0, pathNodes.length - 2)} intermediate node(s). Its configured directional link costs total ${number(route.routeCost)}.`
      : `No traceroute reached ${html(route.destination)} from ${html(route.source)} in the final observation.`;
    container.innerHTML = `<div class="eyebrow">${html(route.source)} → ${html(route.destination)} · ${html(result.algorithm)}</div>
      ${pathMarkup(route.path)}
      <div class="route-explanation">${explanation}</div>
      <div class="route-detail-grid">
        <div class="route-detail"><small>Delivery</small><b>${route.delivered ? "Success" : "Not delivered"}</b></div>
        <div class="route-detail"><small>Hops</small><b>${route.hopCount ?? "Not measured"}</b></div>
        <div class="route-detail"><small>Route cost</small><b>${number(route.routeCost)}</b></div>
      </div>`;
    const metrics = result.metrics;
    byId("simulation-metrics").innerHTML = `<div class="detail-list">
      <div class="detail-row"><span>Observed client pairs</span><b>${metrics.observedClientPairs}</b></div>
      <div class="detail-row"><span>Other measured values</span><b>Available on Metrics page</b></div>
    </div>`;
    state.selectedRoute = route.path;
    renderTopology();
    renderLatestResult(result, route);
    animatePacketRoute(route);
  }

  function renderEvents(events, elementId) {
    const container = byId(elementId);
    if (!events?.length) {
      container.className = "timeline-empty";
      container.textContent = "No recorded events for this result.";
      return;
    }
    container.className = "timeline";
    container.innerHTML = events.map((event) => {
      const target = event.target;
      const link = Array.isArray(target) ? target.slice(0, 2).join(" — ") : String(target);
      const action = event.change === "down" ? "Link down" : "Link up";
      return `<div class="timeline-item"><span class="timeline-time">wall +${number(event.wallSeconds, 3)}s</span><i class="timeline-dot"></i><span class="timeline-copy"><b>${action}: ${html(link)}</b><small>Callback recorded by the running simulation engine</small></span></div>`;
    }).join("");
  }

  function renderConfiguredEvents(events) {
    const container = byId("configured-events");
    if (!events.length) {
      container.className = "timeline-empty";
      container.textContent = "No scheduled link events in this topology.";
      return;
    }
    container.className = "timeline";
    container.innerHTML = [...events]
      .sort((first, second) => first[0] - second[0])
      .map(([time, target, change]) => {
        const link = target.slice(0, 2).join(" — ");
        const action = change === "down" ? "Link down" : "Link up";
        const costDetails = change === "up"
          ? ` · directional costs ${html(target[0])} → ${html(target[1])}: ${html(target[4])}, ${html(target[1])} → ${html(target[0])}: ${html(target[5])}`
          : "";
        return `<div class="timeline-item"><span class="timeline-time">t = ${number(time)}</span><i class="timeline-dot"></i><span class="timeline-copy"><b>${action}: ${html(link)}</b><small>Configured event${costDetails}</small></span></div>`;
      }).join("");
  }

  function pairFromResult(result, source, destination) {
    return result?.routes?.find((route) => route.source === source && route.destination === destination) || null;
  }

  async function runSimulation() {
    if (!state.topology || state.busy) return;
    const algorithm = byId("algorithm-select").value;
    state.busy = true;
    byId("run-button").disabled = true;
    byId("run-progress").hidden = false;
    setRunStatus("Running", "Python routing engine is active");
    try {
      const response = await postJson("/api/simulate", {
        topologyName: state.topologyName,
        topology: state.topology,
        algorithm,
      });
      state.result = response.result;
      setRunStatus("Completed", `${algorithm} · ${number(state.result.wallSeconds, 2)}s wall time`);
      byId("link-count-note").textContent = `${state.result.activeLinks.length} active after run`;
      renderSimulationResult(state.result);
      renderEvents(state.result.events, "event-list");
      renderMetrics();
      renderInspector();
      toast(`${algorithm} simulation completed using the Python routing engine.`);
    } catch (error) {
      setRunStatus("Error", "Simulation did not complete");
      toast(error.message, true);
    } finally {
      state.busy = false;
      byId("run-button").disabled = false;
      byId("run-progress").hidden = true;
    }
  }

  function renderComparison(results) {
    const container = byId("comparison-results");
    if (!results?.length) {
      container.innerHTML = '<article class="comparison-card empty-comparison"><span class="empty-icon">⇄</span><b>Comparison not run</b><p>Choose a topology and run both algorithms to compare real output.</p></article>';
      return;
    }
    const source = results[0].selectedPair.source;
    const destination = results[0].selectedPair.destination;
    container.innerHTML = results.map((result) => {
      const route = pairFromResult(result, source, destination);
      const metrics = result.metrics;
      return `<article class="comparison-card ${result.algorithm.toLowerCase()}">
        <div class="compare-title"><h2>${result.algorithm === "DV" ? "Distance Vector" : "Link State"}</h2><span>${result.algorithm}</span></div>
        <div class="compare-path">${route?.path?.length ? route.path.map(html).join(" → ") : "No route observed"}</div>
        <span class="compare-state ${route?.delivered ? "" : "bad"}">${route?.delivered ? "PACKET DELIVERED" : "UNREACHABLE"}</span>
        <div class="compare-metrics">
          <div><small>HOPS</small><b>${route?.hopCount ?? "Not measured"}</b></div>
          <div><small>ROUTE COST</small><b>${number(route?.routeCost)}</b></div>
          <div><small>WALL RUNTIME</small><b>${number(result.wallSeconds, 2)}s</b></div>
          <div><small>DELIVERY RATIO</small><b>${metrics.deliveryRatio === null ? "Not measured" : `${number(metrics.deliveryRatio * 100, 1)}%`}</b></div>
          <div><small>SIM LATENCY</small><b>Not measured</b></div>
          <div><small>CONVERGENCE</small><b>Not measured</b></div>
        </div>
        <button class="button secondary small export-one" data-result-id="${html(result.resultId)}">Export ${result.algorithm} report</button>
      </article>`;
    }).join("");
    container.querySelectorAll(".export-one").forEach((button) =>
      button.addEventListener("click", () => exportReport(button.dataset.resultId, "json")));
  }

  async function runComparison() {
    if (!state.topology || state.busy) return;
    const button = byId("compare-button");
    state.busy = true;
    button.disabled = true;
    button.textContent = "Running both algorithms…";
    try {
      const response = await postJson("/api/compare", {
        topologyName: state.topologyName,
        topology: state.topology,
        source: byId("source-select").value,
        destination: byId("destination-select").value,
      });
      state.comparison = response.results;
      renderComparison(state.comparison);
      state.result = state.comparison[1];
      renderSimulationResult(state.result);
      renderMetrics();
      renderInspector();
      toast("DV and LS completed independently on the same topology.");
    } catch (error) {
      toast(error.message, true);
    } finally {
      state.busy = false;
      button.disabled = false;
      button.innerHTML = "<span>⇄</span> Run comparison";
    }
  }

  async function runFailureBaseline() {
    if (!state.topology || state.busy) return;
    const button = byId("failure-baseline");
    state.busy = true;
    button.disabled = true;
    byId("failure-progress").hidden = false;
    try {
      const response = await postJson("/api/simulate", {
        topologyName: state.topologyName,
        topology: state.topology,
        algorithm: byId("failure-algorithm").value,
      });
      state.failureBaseline = response.result;
      state.failureResult = null;
      state.failureState = null;
      state.result = state.failureBaseline;
      renderFailureResult();
      renderEvents(state.failureBaseline.events, "failure-events");
      byId("source-select").value = byId("failure-source").value;
      byId("destination-select").value = byId("failure-destination").value;
      renderSimulationResult(state.failureBaseline);
      renderMetrics();
      renderInspector();
      toast("Baseline route recorded from the actual engine.");
    } catch (error) {
      toast(error.message, true);
    } finally {
      state.busy = false;
      button.disabled = false;
      byId("failure-progress").hidden = true;
      updateFailureLinkInfo();
    }
  }

  async function runFailureAction() {
    if (!state.topology || state.busy) return;
    if (!state.failureBaseline) {
      toast("Run a baseline before injecting a link event.", true);
      return;
    }
    const link = selectedFailureLink();
    if (!link) {
      toast("Choose an available router link.", true);
      return;
    }
    const action = state.failureState === "down" ? "up" : "down";
    const button = byId("failure-action");
    state.busy = true;
    button.disabled = true;
    byId("failure-progress").hidden = false;
    try {
      const response = await postJson("/api/failure", {
        topologyName: state.topologyName,
        topology: state.topology,
        algorithm: byId("failure-algorithm").value,
        source: byId("failure-source").value,
        destination: byId("failure-destination").value,
        link,
        action,
      });
      state.failureResult = response.result;
      state.failureState = action;
      state.result = response.result;
      renderFailureResult();
      renderEvents(response.result.events, "failure-events");
      byId("source-select").value = byId("failure-source").value;
      byId("destination-select").value = byId("failure-destination").value;
      renderMetrics();
      renderSimulationResult(response.result);
      toast(`Link-${action} experiment completed by the routing engine.`);
    } catch (error) {
      toast(error.message, true);
    } finally {
      state.busy = false;
      button.disabled = false;
      byId("failure-progress").hidden = true;
      updateFailureLinkInfo();
    }
  }

  function renderFailureResult() {
    const container = byId("failure-comparison");
    if (!state.failureBaseline) {
      container.className = "result-empty";
      container.innerHTML = '<span class="empty-icon">⌁</span><b>Baseline needed</b><p>Run a baseline, then take a link down or restore it.</p>';
      return;
    }
    const source = byId("failure-source").value;
    const destination = byId("failure-destination").value;
    const before = pairFromResult(state.failureBaseline, source, destination);
    const after = pairFromResult(state.failureResult, source, destination);
    if (!after) {
      container.className = "result-empty";
      container.innerHTML = '<span class="empty-icon">⌁</span><b>Run an event experiment</b><p>Choose a link and trigger link down to compare with the baseline.</p>';
      return;
    }
    const same = JSON.stringify(before?.path || []) === JSON.stringify(after.path || []);
    container.className = "";
    container.innerHTML = `<div class="before-after">
      <div><small>BASELINE · ${html(state.failureBaseline.algorithm)}</small><b>${before?.delivered ? "Delivered" : "Unreachable"}</b><code>${html(before?.path?.join(" → ") || "No route")}</code><div class="detail-row"><span>Cost</span><b>${number(before?.routeCost)}</b></div></div>
      <div><small>AFTER LINK-${html(state.failureState || "event").toUpperCase()}</small><b>${after.delivered ? "Delivered" : "Unreachable"}</b><code>${html(after.path?.join(" → ") || "No route")}</code><div class="detail-row"><span>Cost</span><b>${number(after.routeCost)}</b></div></div>
    </div><p class="changed-note">${same ? "The observed path did not change for this selected pair." : "The actual observed path changed after the engine processed the link event."}</p>`;
  }

  function updateFailureSelection() {
    const result = state.failureResult || state.failureBaseline;
    renderFailureResult();
    if (!result) return;
    byId("source-select").value = byId("failure-source").value;
    byId("destination-select").value = byId("failure-destination").value;
    state.result = result;
    renderSimulationResult(result);
    renderMetrics();
    renderInspector();
  }

  function renderMetrics() {
    const container = byId("metrics-content");
    const result = state.result;
    if (!result) {
      container.innerHTML = '<div class="panel empty-metrics"><span class="empty-icon">▥</span><b>No simulation report yet</b><p>Run a simulation before viewing its measured results.</p><button class="button secondary small" data-nav="simulation">Go to simulation</button></div>';
      container.querySelector("[data-nav]")?.addEventListener("click", () => setPage("simulation"));
      return;
    }
    const m = result.metrics;
    const percent = m.deliveryRatio === null ? null : m.deliveryRatio * 100;
    const deliveredWidth = percent === null ? 0 : percent;
    const failedWidth = percent === null ? 0 : 100 - percent;
    container.innerHTML = `<div class="metrics-cards">
      <article class="metric-card"><small>OBSERVED CLIENT PAIRS</small><b>${m.observedClientPairs}</b><span>Final route observations</span></article>
      <article class="metric-card"><small>DELIVERED</small><b>${m.deliveredPairs}</b><span>Traceroute reached destination</span></article>
      <article class="metric-card"><small>UNREACHABLE</small><b>${m.unreachablePairs}</b><span>No completed path observed</span></article>
      <article class="metric-card"><small>DELIVERY RATIO</small><b>${percent === null ? "Not measured" : `${number(percent, 1)}%`}</b><span>Delivered ÷ observed pairs</span></article>
    </div>
    <div class="metrics-report-grid">
      <article class="panel"><div class="panel-heading"><div><h2>Observed delivery</h2><p>${html(result.topology)} · ${html(result.algorithm)}</p></div></div>
        <div class="chart-bars">
          <div class="chart-row"><span>Delivered</span><div class="bar-track"><div class="bar-fill" style="width:${deliveredWidth}%"></div></div><b>${m.deliveredPairs}</b></div>
          <div class="chart-row"><span>Unreachable</span><div class="bar-track"><div class="bar-fill failed" style="width:${failedWidth}%"></div></div><b>${m.unreachablePairs}</b></div>
        </div>
      </article>
      <article class="panel"><div class="panel-heading"><div><h2>Path summary</h2><p>Delivered observations only where noted</p></div></div>
        <div class="metric-list">
          <div class="detail-row"><span>Average hop count</span><b>${number(m.averageHopsDelivered)}</b></div>
          <div class="detail-row"><span>Average route cost</span><b>${number(m.averageRouteCostDelivered)}</b></div>
          <div class="detail-row"><span>Wall runtime</span><b>${number(result.wallSeconds, 2)} s</b></div>
          <div class="detail-row"><span>Simulated packet latency</span><b>Not measured</b></div>
          <div class="detail-row"><span>Convergence / control counts</span><b>Not measured</b></div>
        </div>
      </article>
    </div>`;
  }

  function downloadBlob(blob, filename) {
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = filename;
    anchor.hidden = true;
    document.body.append(anchor);
    anchor.click();
    anchor.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  async function exportReport(resultId, format) {
    if (!resultId) {
      toast("Run a simulation before exporting a report.", true);
      return;
    }
    try {
      const response = await fetch("/api/export", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ resultId, format }),
      });
      if (!response.ok) {
        const error = await response.json();
        throw new Error(error.error || "Could not export report.");
      }
      const blob = await response.blob();
      const disposition = response.headers.get("content-disposition") || "";
      const match = disposition.match(/filename="([^"]+)"/);
      const name = match ? match[1] : `netroute-report.${format}`;
      downloadBlob(blob, name);
      toast(`${format.toUpperCase()} report downloaded.`);
    } catch (error) {
      toast(error.message, true);
    }
  }

  function exportTopology() {
    if (!state.topology) return;
    const blob = new Blob([JSON.stringify(state.topology, null, 2)], { type: "application/json" });
    const filename = state.topologyName.replace(/[^a-zA-Z0-9._-]/g, "_") || "topology.json";
    downloadBlob(blob, filename);
    toast("Topology JSON exported without changing the original file.");
  }

  function clearResultView() {
    state.result = null;
    state.comparison = [];
    state.selectedRoute = null;
    state.selectedElement = null;
    stopPacketAnimations();
    setRunStatus("Idle", "View reset; no new simulation was run");
    renderSimulationResult(null);
    renderComparison([]);
    renderTopology();
    renderMetrics();
    renderInspector();
    toast("Result panels reset. No simulation state was changed.");
  }

  function wireEvents() {
    document.querySelectorAll(".nav-item[data-page]").forEach((button) =>
      button.addEventListener("click", () => setPage(button.dataset.page)));
    document.querySelectorAll("[data-nav]").forEach((button) =>
      button.addEventListener("click", () => setPage(button.dataset.nav)));
    byId("notice-close").addEventListener("click", (event) => event.currentTarget.closest(".notice-strip").remove());
    byId("topology-select").addEventListener("change", (event) => loadTopology(event.target.value).catch((error) => toast(error.message, true)));
    byId("simulation-topology").addEventListener("change", (event) => loadTopology(event.target.value).catch((error) => toast(error.message, true)));
    byId("load-demo").addEventListener("click", () => loadTopology("demo_topology.json").catch((error) => toast(error.message, true)));
    byId("reload-topology").addEventListener("click", () => {
      if (!state.topologyName || state.topologyName.startsWith("Uploaded:")) {
        toast("The current uploaded topology is held in memory; upload it again to reload.");
        return;
      }
      loadTopology(state.topologyName).catch((error) => toast(error.message, true));
    });
    byId("topology-file").addEventListener("change", async (event) => {
      const file = event.target.files?.[0];
      if (!file) return;
      const body = new FormData();
      body.append("file", file);
      try {
        const response = await api("/api/topologies/upload", { method: "POST", body });
        acceptTopology(`Uploaded: ${response.name}`, response.topology);
      } catch (error) {
        toast(error.message, true);
      } finally {
        event.target.value = "";
      }
    });
    byId("save-topology").addEventListener("click", exportTopology);
    byId("zoom-in").addEventListener("click", () => zoom(1.14));
    byId("zoom-out").addEventListener("click", () => zoom(.88));
    byId("fit-graph").addEventListener("click", () => {
      state.graphTransform = { x: 0, y: 0, scale: 1 };
      renderTopology();
    });
    byId("run-button").addEventListener("click", runSimulation);
    byId("reset-button").addEventListener("click", clearResultView);
    byId("source-select").addEventListener("change", () => renderSimulationResult(state.result));
    byId("destination-select").addEventListener("change", () => renderSimulationResult(state.result));
    byId("compare-button").addEventListener("click", runComparison);
    byId("failure-baseline").addEventListener("click", runFailureBaseline);
    byId("failure-action").addEventListener("click", runFailureAction);
    byId("failure-link").addEventListener("change", updateFailureLinkInfo);
    byId("failure-source").addEventListener("change", updateFailureSelection);
    byId("failure-destination").addEventListener("change", updateFailureSelection);
    byId("failure-algorithm").addEventListener("change", () => {
      state.failureBaseline = null;
      state.failureResult = null;
      state.failureState = null;
      renderFailureResult();
      updateFailureLinkInfo();
    });
    byId("export-json").addEventListener("click", () => exportReport(state.result?.resultId, "json"));
    byId("export-csv").addEventListener("click", () => exportReport(state.result?.resultId, "csv"));
  }

  async function initialize() {
    wireEvents();
    try {
      const health = await api("/api/health");
      byId("engine-label").textContent = `${health.engine} connected`;
      state.topologies = (await api("/api/topologies")).topologies;
      populateTopologySelectors();
      const demo = state.topologies.find((entry) => entry.name === "demo_topology.json" && entry.valid);
      const initial = demo || state.topologies.find((entry) => entry.valid);
      if (!initial) throw new Error("No valid JSON topology is available in the project folder.");
      await loadTopology(initial.name);
    } catch (error) {
      byId("engine-label").textContent = "Backend unavailable";
      byId("engine-label").previousElementSibling?.classList.add("offline");
      toast(error.message, true);
    }
  }

  initialize();
})();
