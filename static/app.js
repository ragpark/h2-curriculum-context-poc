/* H2 POC UI — plain JS, no build step. */
const S = { status: null, graph: null, labels: {}, page: "overview", pupil: "pupil:amara", cls: "10X", mode: null,
  sel: null, overlay: "none", visited: new Set(), items: [], pupils: [], scenarios: [], lastExplain: null, ctxDone: false, evalDone: false };

const $ = (s, r = document) => r.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const pct = (x) => (x == null ? "–" : Math.round(x * 100) + "%");
const lab = (id) => S.labels[id] || id;

async function api(path, opts = {}) {
  const o = { headers: { "content-type": "application/json" }, ...opts };
  if (o.body && typeof o.body !== "string") o.body = JSON.stringify(o.body);
  const r = await fetch(path, o);
  const t = await r.text();
  let d; try { d = JSON.parse(t); } catch { d = t; }
  if (!r.ok) throw new Error((d && d.detail) || r.statusText);
  return d;
}
function toast(msg) { const t = $("#toast"); t.textContent = msg; t.classList.add("show"); clearTimeout(t._h); t._h = setTimeout(() => t.classList.remove("show"), 3200); }
async function busy(btn, fn) {
  const html = btn.innerHTML; btn.disabled = true; btn.innerHTML = `<span class="spin"></span>${html}`;
  try { return await fn(); } catch (e) { toast(e.message); console.error(e); } finally { btn.disabled = false; btn.innerHTML = html; }
}
function tag(facet, id, conf, opts = {}) {
  const k = { concept: "concept", method: "method", representation: "rep", misconception: "misc" }[facet] || facet;
  const review = conf != null && conf < 0.5 ? " review" : "";
  const click = facet === "concept" && opts.click !== false ? ` clickable" data-node="${esc(id)}` : "";
  return `<span class="tag ${facet}${review}${click}" title="${esc(id)}${conf != null ? " · confidence " + conf : ""}"><span class="k">${k}</span>${esc(opts.label || lab(id))}${conf != null ? ` <span class="c">${conf.toFixed(2)}</span>` : ""}</span>`;
}
const badge = (s) => `<span class="badge ${esc(String(s).replace(/\s/g, "-"))}">${esc(s)}</span>`;
const bar = (v, color) => `<div class="bar"><i style="width:${Math.round((v || 0) * 100)}%;background:${color}"></i></div>`;
const statusColor = (s) => ({ secure: "var(--secure)", developing: "var(--developing)", gap: "var(--gap)", inferred: "var(--inferred)" }[s] || "var(--text-3)");

/* ------------------------------------------------------------------ boot & nav */
async function boot() {
  document.querySelectorAll("#nav button").forEach((b) => b.addEventListener("click", () => show(b.dataset.page)));
  document.addEventListener("click", (e) => {
    const n = e.target.closest("[data-node]");
    if (n) { S.sel = n.dataset.node; show("graph"); }
  });
  [S.items, S.pupils, S.scenarios] = await Promise.all([api("/api/items"), api("/api/pupils"), api("/api/scenarios")]);
  await loadGraph(); await refreshStatus();
  bindStatic();
  show((location.hash || "#overview").slice(1));
  window.addEventListener("hashchange", () => { const p = location.hash.slice(1); if (p && p !== S.page) show(p); });
}
async function loadGraph() {
  S.graph = await api("/api/graph");
  S.labels = {};
  for (const k of ["concepts", "misconceptions", "methods", "representations"]) for (const n of S.graph[k]) S.labels[n.id] = n.label;
}
async function refreshStatus() {
  S.status = await api("/api/status");
  const st = S.status, c = st.counts;
  $("#chips").innerHTML = [
    `<span class="chip ok"><span class="dot"></span>Graph ${esc(st.graph_version)}</span>`,
    `<span class="chip ${st.llm_available ? "ok" : "warn"}"><span class="dot"></span>Aligner: ${st.aligner_mode === "claude" ? "Claude" : "heuristic"}</span>`,
    `<span class="chip ${st.llm_available ? "ok" : "warn"}"><span class="dot"></span>${st.llm_available ? "Tutor: " + esc(st.model) : "No Anthropic key"}</span>`,
    `<span class="chip"><span class="dot"></span>${c.alignment} alignments · ${c.evidence} evidence</span>`,
  ].join("");
  renderArch(); renderSteps();
}
function show(page) {
  if (!document.getElementById("page-" + page)) page = "overview";
  S.page = page; S.visited.add(page); history.replaceState(null, "", "#" + page);
  document.querySelectorAll(".page").forEach((p) => p.classList.toggle("active", p.id === "page-" + page));
  document.querySelectorAll("#nav button").forEach((b) => b.classList.toggle("active", b.dataset.page === page));
  ({ overview: renderOverview, graph: renderGraphPage, materials: renderMaterials, evidence: renderEvidence, context: renderContextPage, evaluate: renderEvaluate, connect: renderConnect }[page])();
  window.scrollTo({ top: 0 });
}

/* ------------------------------------------------------------------ overview */
function renderOverview() { renderArch(); renderSteps(); }
function renderSteps() {
  const c = S.status?.counts || {};
  const steps = [
    ["graph", "Explore the reference graph", "Concepts, misconceptions, methods, crosswalks", S.visited.has("graph")],
    ["materials", "Ingest teacher materials (H3)", "Align them to the graph and derive class coverage", c.content_unit > 0],
    ["evidence", "Record learner evidence (H1)", "Build a learner model tied to the same concepts", c.evidence > 0],
    ["context", "Compare tutor contexts", "Without H2 vs with H2, for the same pupil turn", S.ctxDone],
    ["evaluate", "Evaluate", "Alignment accuracy and the tutor A/B test", S.evalDone],
  ];
  $("#steps").innerHTML = steps.map(([p, t, d, done], i) => `<div class="step ${done ? "done" : ""}" data-go="${p}"><div class="n">${done ? "✓" : i + 1}</div><div><div class="t">${t}</div><div class="d">${d}</div></div></div>`).join("");
  document.querySelectorAll("[data-go]").forEach((el) => (el.onclick = () => show(el.dataset.go)));
}
function renderArch() {
  if (!S.status) return;
  const c = S.status.counts, st = S.status;
  const box = (x, y, w, h, title, sub, count, cls = "", go = "") =>
    `<g ${go ? `data-go="${go}"` : ""}><rect class="box ${cls} ${go ? "click" : ""}" x="${x}" y="${y}" width="${w}" height="${h}" rx="10"/>
     <text class="t-label" x="${x + 12}" y="${y + 22}">${title}</text>
     <text class="t-sub" x="${x + 12}" y="${y + 40}">${sub}</text>
     ${count ? `<text class="t-count" x="${x + 12}" y="${y + h - 12}">${count}</text>` : ""}</g>`;
  const arr = (x1, y1, x2, y2, cls = "", label = "", lx, ly) => {
    const mx = (x1 + x2) / 2;
    return `<path class="flow ${cls}" d="M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}" marker-end="url(#ah${cls ? "-r" : ""})"/>` +
      (label ? `<text class="t-sub" x="${lx ?? mx}" y="${ly ?? (y1 + y2) / 2 - 4}" text-anchor="middle">${label}</text>` : "");
  };
  const lbl = (x, y, t, a = "start") => `<text class="t-sub" x="${x}" y="${y}" text-anchor="${a}">${t}</text>`;
  const svg = `
  <defs>
    <marker id="ah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="var(--text-3)"/></marker>
    <marker id="ah-r" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="var(--accent)"/></marker>
  </defs>
  <rect class="frame" x="222" y="14" width="268" height="284" rx="14"/>
  <text class="t-group" x="238" y="36">H2 CAPABILITY</text>
  ${box(16, 50, 170, 64, "Curriculum teams", "+ shared public layer", "")}
  ${box(16, 202, 170, 64, "Teacher materials", "school LMS / drive", "", "", "materials")}
  ${box(16, 330, 170, 64, "Learner evidence", "tutors · homework · markbook", "", "", "evidence")}
  ${box(236, 50, 240, 88, "Reference graph", `version ${st.graph_version} · governed releases`, `${c.concepts} concepts · ${c.crosswalk} crosswalks`, "h2", "graph")}
  ${box(236, 190, 240, 88, "Alignment service", `mode: ${st.aligner_mode}`, `${c.alignment} alignments stored`, "h2", "materials")}
  ${box(540, 120, 200, 72, "Content index  (H3)", "vector + graph facets", `${c.content_unit} units`, "", "materials")}
  ${box(540, 310, 200, 64, "Evidence store  (H1)", "append-only", `${c.evidence} rows`, "", "evidence")}
  ${box(540, 404, 200, 64, "Learner state  (H1)", "projection, rebuildable", `${c.learner_state} concept states`, "", "evidence")}
  ${box(790, 200, 172, 88, "Context assembly", "one call per tutor turn", "REST + MCP", "h2", "context")}
  ${box(790, 362, 172, 64, "AI tutor(s)", "any vendor", "", "", "connect")}
  ${arr(186, 82, 236, 94)}
  <path class="flow" d="M356,138 L356,190" marker-end="url(#ah)"/>${lbl(364, 168, "constrains")}
  ${arr(186, 234, 236, 234)}
  ${arr(476, 212, 540, 156)}${lbl(496, 150, "tag units", "end")}
  ${arr(186, 362, 540, 342)}${lbl(300, 346, "items tagged at source", "middle")}
  ${arr(476, 256, 540, 326)}${lbl(420, 318, "untagged entries aligned", "middle")}
  <path class="flow" d="M640,374 L640,404" marker-end="url(#ah)"/>${lbl(648, 394, "project")}
  <path class="flow read" d="M476,70 C700,40 876,90 876,200" marker-end="url(#ah-r)"/>
  ${arr(740, 156, 790, 244, "read")}
  ${arr(740, 436, 790, 268, "read")}
  <path class="flow read" d="M876,288 L876,362" marker-end="url(#ah-r)"/>${lbl(884, 330, "context pack")}
  <text class="t-sub" x="16" y="484">Solid arrows: write paths. Dashed: read path for one tutor turn. Click a box to open that part.</text>`;
  const el = $("#arch"); el.innerHTML = svg;
  el.querySelectorAll("[data-go]").forEach((g) => (g.onclick = () => show(g.dataset.go)));
}

/* ------------------------------------------------------------------ graph renderer */
const NW = 150, NH = 48, CG = 34, RG = 12, PAD = 14;
function layout() {
  const cs = S.graph.concepts.filter((c) => c.status === "active");
  const byL = {};
  cs.forEach((c) => (byL[c.layer] ??= []).push(c));
  const pos = {};
  const prereqOf = {};
  S.graph.prerequisites.forEach((e) => (prereqOf[e.dst] ??= []).push(e.src));
  const layers = Object.keys(byL).map(Number).sort((a, b) => a - b);
  for (const L of layers) {
    const arr = byL[L];
    arr.forEach((c) => { const ps = (prereqOf[c.id] || []).filter((p) => pos[p]); c._bc = ps.length ? ps.reduce((s, p) => s + pos[p].row, 0) / ps.length : 99; });
    arr.sort((a, b) => a._bc - b._bc || a.label.localeCompare(b.label));
    arr.forEach((c, i) => (pos[c.id] = { row: i, col: L }));
  }
  const maxRows = Math.max(...layers.map((L) => byL[L].length));
  for (const L of layers) {  // centre shorter columns
    const off = (maxRows - byL[L].length) / 2;
    byL[L].forEach((c) => { const p = pos[c.id]; p.x = PAD + L * (NW + CG); p.y = PAD + (p.row + off) * (NH + RG); });
  }
  return { pos, w: PAD * 2 + layers.length * (NW + CG) - CG, h: PAD * 2 + maxRows * (NH + RG) - RG };
}
function wrap(s, n = 22) {
  const words = s.split(" "); const lines = []; let cur = "";
  for (const w of words) { if ((cur + " " + w).trim().length > n && cur) { lines.push(cur); cur = w; } else cur = (cur + " " + w).trim(); }
  if (cur) lines.push(cur);
  if (lines.length > 3) { lines.length = 3; lines[2] = lines[2].replace(/.{0,2}$/, "…"); }
  return lines;
}
function drawGraph(svg, o = {}) {
  const { pos, w, h } = layout();
  const sel = o.selected;
  const near = new Set();
  if (sel && o.dimOthers) {
    near.add(sel);
    S.graph.prerequisites.forEach((e) => { if (e.dst === sel) near.add(e.src); if (e.src === sel) near.add(e.dst); });
  }
  let out = "";
  for (const e of S.graph.prerequisites) {
    const a = pos[e.src], b = pos[e.dst]; if (!a || !b) continue;
    const x1 = a.x + NW, y1 = a.y + NH / 2, x2 = b.x, y2 = b.y + NH / 2, mx = (x1 + x2) / 2;
    const cls = sel && e.dst === sel ? "hl" : sel && e.src === sel ? "hl-out" : "";
    out += `<path class="gedge ${cls}" d="M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}" stroke-opacity="${0.4 + 0.6 * e.weight}"/>`;
  }
  for (const c of S.graph.concepts) {
    const p = pos[c.id]; if (!p) continue;
    const cls = [o.classFor ? o.classFor(c.id) : "", c.id === sel ? "sel" : "", near.size && !near.has(c.id) ? "dim" : ""].join(" ");
    const lines = wrap(c.label);
    const ty = p.y + NH / 2 - ((lines.length - 1) * 13) / 2 + 4;
    const dot = o.dots && o.dots[c.id] ? `<circle class="mcdot" cx="${p.x + NW - 9}" cy="${p.y + 9}" r="5"><title>${esc(o.dots[c.id])}</title></circle>` : "";
    const sub = o.sub && o.sub(c.id);
    out += `<g class="gnode ${cls}" data-id="${esc(c.id)}"><title>${esc(c.label)}\n${esc(c.id)}${sub ? "\n" + esc(sub) : ""}</title><rect x="${p.x}" y="${p.y}" width="${NW}" height="${NH}" rx="8"/>
      ${lines.map((l, i) => `<text x="${p.x + 10}" y="${ty + i * 13}">${esc(l)}</text>`).join("")}${dot}</g>`;
  }
  svg.setAttribute("viewBox", `0 0 ${w} ${h}`);
  svg.innerHTML = out;
  svg.querySelectorAll(".gnode").forEach((g) => (g.onclick = () => o.onClick && o.onClick(g.dataset.id)));
}
const LEGEND_MASTERY = `<span><i class="sw" style="background:var(--secure-soft);border-color:var(--secure)"></i>Secure</span><span><i class="sw" style="background:var(--developing-soft);border-color:var(--developing)"></i>Developing</span><span><i class="sw" style="background:var(--gap-soft);border-color:var(--gap)"></i>Gap</span><span><i class="sw" style="background:var(--inferred-soft);border-color:var(--inferred);border-style:dashed"></i>Inferred only</span><span><i class="sw" style="border-color:var(--border-strong)"></i>No evidence</span><span><svg width="12" height="12"><circle cx="6" cy="6" r="5" fill="var(--mc)"/></svg>Active misconception affects this concept</span>`;
const LEGEND_COVER = `<span><i class="sw" style="background:var(--method-soft);border-color:var(--method)"></i>Taught so far</span><span><i class="sw" style="border-color:var(--method);border-style:dashed"></i>Planned</span><span><i class="sw" style="border-color:var(--border-strong)"></i>Not in scheme yet</span>`;
const LEGEND_EDGES = `<span><svg width="26" height="8"><path d="M0,4 L26,4" stroke="var(--accent)" stroke-width="2.2"/></svg>Prerequisite of selected</span><span><svg width="26" height="8"><path d="M0,4 L26,4" stroke="var(--method)" stroke-width="2.2"/></svg>Leads to</span>`;

async function overlayData(key) {
  if (key.startsWith("class:")) {
    const cov = await api(`/api/classes/${key.slice(6)}/coverage`);
    const t = new Set(cov.taught.map((c) => c.id)), p = new Set(cov.planned.map((c) => c.id));
    return { classFor: (id) => (t.has(id) ? "taught" : p.has(id) ? "planned" : ""), legend: LEGEND_COVER, empty: !cov.taught.length && !cov.planned.length ? "Ingest materials first to see coverage." : "" };
  }
  if (key.startsWith("pupil:")) {
    const lv = await api(`/api/learners/${key}`);
    const m = Object.fromEntries(lv.state.map((s) => [s.id, s]));
    const dots = {};
    lv.misconceptions.filter((x) => x.active).forEach((mc) => mc.affects.forEach((c) => (dots[c.id] = (dots[c.id] ? dots[c.id] + "; " : "") + mc.label)));
    return { classFor: (id) => (m[id] ? m[id].status : ""), dots, sub: (id) => (m[id] ? `${m[id].status} · mastery ${pct(m[id].mastery)}` : "no evidence"), legend: LEGEND_MASTERY, empty: !lv.state.length ? "No evidence yet for this pupil. Record some on the Learner evidence page." : "" };
  }
  return { legend: "" };
}

/* ------------------------------------------------------------------ graph page */
async function renderGraphPage() {
  const g = S.graph;
  $("#g-version").textContent = "v" + g.version;
  const active = g.concepts.filter((c) => c.status === "active");
  const dep = g.concepts.filter((c) => c.status !== "active");
  $("#g-counts").textContent = `${active.length} concepts · ${g.prerequisites.length} prerequisite edges · ${g.misconceptions.length} misconceptions · ${g.methods.length} methods · ${g.representations.length} representations${dep.length ? ` · ${dep.length} deprecated` : ""}`;
  const ov = $("#g-overlay");
  ov.innerHTML = `<option value="none">Overlay: none</option><option value="class:10X">Overlay: class 10X coverage</option><option value="class:10Y">Overlay: class 10Y coverage</option>` +
    S.pupils.map((p) => `<option value="${p.id}">Overlay: ${esc(p.name)} mastery</option>`).join("");
  ov.value = S.overlay;
  ov.onchange = () => { S.overlay = ov.value; drawMain(); };
  await drawMain();
  $("#g-others").innerHTML = `
    <h4 style="margin:4px 0 6px;font-size:12px;color:var(--text-3)">MISCONCEPTIONS → concepts they affect</h4>
    <div class="tbl-wrap"><table class="t">${g.misconceptions.map((m) => `<tr><td style="width:45%">${tag("misconception", m.id)}<div class="sub" style="margin:4px 0 0">${esc(m.description || "")}</div></td><td><div class="tags">${m.affects.map((c) => tag("concept", c)).join("")}</div></td></tr>`).join("")}</table></div>
    <h4 style="margin:14px 0 6px;font-size:12px;color:var(--text-3)">METHODS → concepts they teach</h4>
    <div class="tbl-wrap"><table class="t">${g.methods.map((m) => `<tr><td style="width:45%">${tag("method", m.id)}</td><td class="sub">${m.teaches.length} concepts</td></tr>`).join("")}</table></div>
    <h4 style="margin:14px 0 6px;font-size:12px;color:var(--text-3)">REPRESENTATIONS</h4>
    <div class="tags">${g.representations.map((r) => tag("representation", r.id)).join("")}</div>`;
  if (S.sel) inspect(S.sel);
  $("#btn-release").disabled = g.version !== "2026.2";
}
async function drawMain() {
  const od = await overlayData(S.overlay);
  drawGraph($("#g-svg"), { ...od, selected: S.sel, dimOthers: !!S.sel && S.overlay === "none", onClick: (id) => { S.sel = id; drawMain(); inspect(id); } });
  $("#g-legend").innerHTML = (od.empty ? `<span class="sub">${od.empty}</span>` : od.legend) + LEGEND_EDGES;
}
async function inspect(id) {
  const d = await api(`/api/graph/node?id=${encodeURIComponent(id)}`);
  const el = $("#g-inspector");
  const chips = (arr, f = "concept") => (arr && arr.length ? `<div class="tags">${arr.map((x) => tag(f, x.id, null, { label: x.label })).join("")}</div>` : `<div class="sub">None</div>`);
  el.innerHTML = `
    <div class="eyebrow">${esc(d.type)}</div>
    <p class="title">${esc(d.label)}</p>
    <div class="mono sub" style="margin:2px 0 8px">${esc(d.id)}</div>
    <div class="tags">${(d.key_stage || []).map((k) => `<span class="tag plain">${esc(k)}</span>`).join("")}<span class="tag plain">v${esc(d.graph_version)}</span>${d.status !== "active" ? `<span class="tag misconception">deprecated → ${esc(d.replaced_by)}</span>` : ""}</div>
    ${d.type === "concept" ? `
      <h4>Prerequisites</h4>${chips(d.prerequisites)}
      <h4>Leads to</h4>${chips(d.leads_to)}
      <h4>Misconceptions that affect it</h4>${d.misconceptions.length ? d.misconceptions.map((m) => `<div style="margin-bottom:6px">${tag("misconception", m.id)}<div class="sub" style="margin:3px 0 0">${esc(m.description || "")}</div></div>`).join("") : `<div class="sub">None</div>`}
      <h4>Methods that teach it</h4>${chips(d.methods, "method")}
      <h4>Crosswalks</h4>${d.crosswalk.length ? `<table class="t">${d.crosswalk.map((x) => `<tr><td>${esc(x.scheme)}</td><td class="mono">${esc(x.id)}</td><td>${esc(x.match)}</td></tr>`).join("")}</table><div class="sub" style="margin-top:6px">Illustrative mappings. “shared-layer” IDs are placeholders for whichever shared public layer is adopted.</div>` : `<div class="sub">None</div>`}
      <h4>Full prerequisite chain</h4><div class="sub">${d.ancestors.length} ancestor concepts, up to ${Math.max(0, ...d.ancestors.map((a) => a.depth))} steps back</div>` : ""}
    ${d.affects ? `<h4>Affects</h4>${chips(d.affects)}` : ""}${d.teaches ? `<h4>Teaches</h4>${chips(d.teaches)}` : ""}
    <h4>Usage</h4><dl class="kv"><dt>Alignments pointing here</dt><dd>${d.usage.alignments}</dd><dt>Evidence rows tagged</dt><dd>${d.usage.evidence}</dd></dl>`;
}

/* ------------------------------------------------------------------ materials (H3) */
S.open = new Set(["mat:10x-w4-both-sides"]);
function modeSeg(el, onChange, includeAuto = true) {
  const llm = S.status.llm_available;
  const opts = [...(includeAuto ? [["", "Auto"]] : []), ["heuristic", "Heuristic"], ["claude", "Claude"]];
  el.innerHTML = opts.map(([v, l]) => `<button data-v="${v}" ${v === "claude" && !llm ? 'disabled title="Set ANTHROPIC_API_KEY to enable"' : ""} class="${(S.mode || "") === v ? "on" : ""}">${l}</button>`).join("");
  el.querySelectorAll("button").forEach((b) => (b.onclick = () => { S.mode = b.dataset.v || null; modeSeg(el, onChange, includeAuto); onChange && onChange(); }));
}
async function renderMaterials() {
  const classes = await api("/api/classes");
  const seg = $("#m-class");
  seg.innerHTML = classes.map((c) => `<button data-c="${c.id}" class="${c.id === S.cls ? "on" : ""}">${c.id}</button>`).join("");
  seg.querySelectorAll("button").forEach((b) => (b.onclick = () => { S.cls = b.dataset.c; renderMaterials(); }));
  const cls = classes.find((c) => c.id === S.cls);
  $("#m-sub").innerHTML = `${esc(cls.teacher)} · ${esc(cls.scheme)} · currently in <b>week ${cls.current_week}</b>`;
  let ms = $("#m-mode");
  if (!ms) { ms = document.createElement("div"); ms.className = "seg"; ms.id = "m-mode"; $("#btn-ingest-all").before(ms); }
  modeSeg(ms);
  const mats = await api(`/api/materials?class_id=${S.cls}`);
  $("#m-list").innerHTML = mats.map((m) => `
    <div class="mat ${S.open.has(m.id) ? "open" : ""}" data-m="${esc(m.id)}">
      <div class="mat-head"><span class="w">W${m.week}</span><span class="ttl">${esc(m.title)}</span>
        ${badge(m.status)} ${m.ingested ? `<span class="badge secure">aligned</span>` : `<span class="badge">not ingested</span>`}
        <button class="btn sm" data-ingest="${esc(m.id)}">${m.ingested ? "Re-align" : "Ingest"}</button></div>
      <div class="mat-body">${m.units.length ? m.units.map((u) => `
        <div class="unit"><div class="h">${esc(u.heading)} <span class="mono sub">#${u.idx}</span></div><div class="b">${esc(u.body)}</div>
          <div class="tags">${u.alignments.length ? ["concept", "method", "representation", "misconception"].flatMap((f) => u.alignments.filter((a) => a.facet === f).map((a) => tag(f, a.id, a.confidence))).join("") : `<span class="sub">No tags above threshold</span>`}</div>
          ${u.alignments[0] ? `<div class="sub" style="margin-top:4px">provenance ${esc(u.alignments[0].provenance)} · graph v${esc(u.alignments[0].graph_version)}</div>` : ""}</div>`).join("")
        : `<div class="b" style="white-space:pre-wrap;color:var(--text-2);font-size:12.5px">${esc(m.body)}</div><div class="note" style="margin-top:8px">Not ingested yet. Click <b>Ingest</b> to split it into units and align each one.</div>`}</div>
    </div>`).join("");
  document.querySelectorAll(".mat-head").forEach((h) => (h.onclick = (e) => {
    if (e.target.closest("[data-ingest]")) return;
    const id = h.parentElement.dataset.m; S.open.has(id) ? S.open.delete(id) : S.open.add(id); h.parentElement.classList.toggle("open");
  }));
  document.querySelectorAll("[data-ingest]").forEach((b) => (b.onclick = () => busy(b, async () => {
    await api("/api/materials/ingest", { method: "POST", body: { material_id: b.dataset.ingest, mode: S.mode } });
    S.open.add(b.dataset.ingest); await refreshStatus(); await renderMaterials(); toast("Aligned");
  })));
  // coverage
  const cov = await api(`/api/classes/${S.cls}/coverage`);
  const methods = {};
  mats.filter((m) => m.status === "taught").forEach((m) => m.units.forEach((u) => u.alignments.filter((a) => a.facet === "method" && a.confidence >= 0.5).forEach((a) => (methods[a.id] = (methods[a.id] || 0) + 1))));
  $("#m-coverage").innerHTML = !cov.taught.length && !cov.planned.length ? `<div class="empty">Ingest materials to derive coverage</div>` : `
    <div class="sub" style="margin:0 0 6px"><b>Taught so far</b> (weeks 1–${cov.class.current_week})</div>
    <div class="tags">${cov.taught.map((c) => tag("concept", c.id, null, { label: `W${c.week} · ${c.label}` })).join("") || '<span class="sub">none</span>'}</div>
    <div class="sub" style="margin:12px 0 6px"><b>Planned</b></div>
    <div class="tags">${cov.planned.map((c) => tag("concept", c.id, null, { label: `W${c.week} · ${c.label}` })).join("") || '<span class="sub">none</span>'}</div>
    <div class="sub" style="margin:12px 0 6px"><b>Teacher's methods in taught units</b></div>
    <div class="tags">${Object.entries(methods).sort((a, b) => b[1] - a[1]).map(([id, n]) => tag("method", id, null, { label: `${lab(id)} ×${n}` })).join("") || '<span class="sub">none</span>'}</div>
    <div style="margin-top:10px"><button class="btn sm" id="btn-cov-graph">View on graph</button></div>`;
  const cg = $("#btn-cov-graph"); if (cg) cg.onclick = () => { S.overlay = "class:" + S.cls; show("graph"); };
}
function renderAlignResult(r) {
  const tags = [...r.concepts.map((c) => tag("concept", c.id, c.confidence)), r.method ? tag("method", r.method.id, r.method.confidence) : "", r.representation ? tag("representation", r.representation.id, r.representation.confidence) : "", ...r.misconceptions.map((m) => tag("misconception", m.id, m.confidence))].join("");
  return `<div class="tags">${tags || '<span class="sub">No tags above threshold</span>'}</div>
    <div class="sub" style="margin-top:6px">${esc(r.provenance)} · graph v${esc(r.graph_version)}${r.candidates_considered ? ` · ${r.candidates_considered} candidate concepts retrieved from the graph` : ""}</div>
    ${r.rationale ? `<div class="note" style="margin-top:6px">${esc(r.rationale)}</div>` : ""}
    ${r.fallback_reason ? `<div class="note warn" style="margin-top:6px">${esc(r.fallback_reason)}</div>` : ""}
    ${r.rejected_ids && r.rejected_ids.length ? `<div class="note warn" style="margin-top:6px">Rejected ${r.rejected_ids.length} ID(s) not in the graph. The ontology constrains the model.</div>` : ""}`;
}

/* ------------------------------------------------------------------ evidence (H1) */
async function renderEvidence() {
  $("#e-pupils").innerHTML = S.pupils.map((p) => { const sc = S.scenarios.find((s) => s.pupil === p.id); return `<button class="pupil ${p.id === S.pupil ? "on" : ""}" data-p="${p.id}"><b>${esc(p.name)}</b><span>Class ${esc(p.class)} · ${esc(sc ? sc.title : "")}</span></button>`; }).join("");
  document.querySelectorAll("[data-p]").forEach((b) => (b.onclick = () => { S.pupil = b.dataset.p; S.lastExplain = null; renderEvidence(); }));
  const sc = S.scenarios.find((s) => s.pupil === S.pupil);
  $("#e-scenario").innerHTML = sc ? `<h3>Preset scenario</h3><p class="sub">${esc(sc.title)}</p><p style="margin:0 0 10px;font-size:13px;color:var(--text-2)">${esc(sc.story)}</p>
    <div class="sub">${sc.events.filter((e) => e.item).length} item answers · ${sc.events.filter((e) => e.activity).length} markbook entries</div>
    <button class="btn primary" id="btn-sc" style="margin-top:10px">Run scenario</button><div class="sub" style="margin-top:6px">Replaces this pupil's evidence with the scenario.</div>` : "";
  const b = $("#btn-sc"); if (b) b.onclick = () => busy(b, async () => {
    const r = await api(`/api/scenarios/${sc.id}/run`, { method: "POST" });
    const nItem = r.recorded.filter((x) => x.concept_provenance === "source").length, nAct = r.recorded.length - nItem;
    const diag = r.recorded.filter((x) => x.misconception).length;
    const acts = sc.events.map((ev, i) => [ev, r.recorded[i]]).filter(([ev]) => ev.activity);
    const actTxt = acts.map(([ev, rec]) => `“${esc(ev.activity)}” as ${rec.concepts.map((c) => `<b>${esc(c.label)}</b>`).join(", ") || "nothing recognisable"} (confidence ${rec.confidence})`).join("; ");
    S.lastExplain = `<b>Replayed ${r.recorded.length} events</b><ul><li>${nItem} item answers arrived already tagged with concept IDs</li>${nAct ? `<li>${nAct} markbook entr${nAct > 1 ? "ies" : "y"} had no IDs. The aligner interpreted ${actTxt}. Those entries count for less, in proportion to the confidence</li>` : ""}<li>${diag} wrong answers matched a known distractor, so a misconception was diagnosed</li><li>The learner model was rebuilt from the whole log, spreading evidence across the graph</li></ul>`;
    await refreshStatus(); await renderEvidence();
  });
  // item form
  const it = $("#e-item");
  if (!it.options.length) {
    it.innerHTML = S.items.map((i) => `<option value="${i.id}">${esc(i.prompt)}</option>`).join("");
    it.onchange = fillResp; fillResp();
    $("#e-score").oninput = () => ($("#e-score-v").textContent = $("#e-score").value + "%");
  }
  const lv = await api(`/api/learners/${S.pupil}`);
  $("#e-sub").textContent = `${lv.pupil.name} · ${lv.evidence.length} evidence rows · ${lv.state.length} concepts with estimates`;
  $("#e-explain").innerHTML = S.lastExplain ? `<div class="explain">${S.lastExplain}</div>` : lv.evidence.length ? "" : `<div class="note">No evidence yet. Run the preset scenario or record answers on the right.</div>`;
  const od = await overlayData(S.pupil);
  drawGraph($("#e-svg"), { ...od, onClick: (id) => { S.sel = id; S.overlay = S.pupil; show("graph"); } });
  $("#e-legend").innerHTML = LEGEND_MASTERY;
  $("#e-states").innerHTML = lv.state.length ? `<table class="t">${lv.state.map((s) => `<tr><td>${esc(s.label)}</td><td>${badge(s.status)}</td><td style="width:90px">${bar(s.mastery, statusColor(s.status))}<div class="sub">${pct(s.mastery)}</div></td><td class="sub" title="direct / inferred evidence">${s.n_direct}d · ${s.n_inferred}i</td></tr>`).join("")}</table>` : `<div class="empty">No estimates yet</div>`;
  $("#e-mcs").innerHTML = lv.misconceptions.length ? lv.misconceptions.map((m) => `<div style="margin-bottom:12px">${tag("misconception", m.id)} ${m.active ? badge("gap").replace(">gap<", ">active<") : badge("faded")}
      <div class="row" style="align-items:center;margin-top:6px"><div style="flex:1">${bar(m.strength, "var(--mc)")}</div><span class="sub">strength ${pct(m.strength)} · seen ${m.count}×</span></div>
      <div class="sub" style="margin-top:4px">Affects: ${m.affects.map((c) => esc(c.label)).join(", ")}</div></div>`).join("") : `<div class="empty">None diagnosed</div>`;
  $("#e-log").innerHTML = lv.evidence.length ? `<table class="t"><tr><th>#</th><th>Source</th><th>Evidence</th><th>Outcome</th><th>Concepts (how tagged)</th><th>Misconception</th></tr>${lv.evidence.map((e) => `<tr>
    <td class="mono">${e.id}</td><td>${esc(e.source)}</td>
    <td>${e.prompt ? `${esc(e.prompt)}<div class="sub">→ “${esc(e.response)}”</div>` : `<i>${esc(e.activity)}</i>`}</td>
    <td>${e.item ? (e.outcome >= 0.5 ? '<span class="ok">✓</span>' : '<span class="bad">✗</span>') : pct(e.outcome)}</td>
    <td><div class="tags">${e.concepts.map((c) => tag("concept", c.id, null, { label: c.label })).join("")}</div><div class="sub">${e.concept_provenance === "source" ? "tagged at source" : "aligned: " + esc(e.concept_provenance) + " · conf " + e.confidence} · v${esc(e.graph_version)}</div></td>
    <td>${e.misconception ? tag("misconception", e.misconception.id) : ""}</td></tr>`).join("")}</table>` : `<div class="empty">Empty</div>`;
}
function fillResp() {
  const i = S.items.find((x) => x.id === $("#e-item").value);
  $("#e-resp").innerHTML = `<option value="${esc(i.answer)}">${esc(i.answer)}  (correct)</option>` + (i.distractors || []).map((d) => `<option value="${esc(d.response)}">${esc(d.response)}  (${d.misconception ? "reveals: " + esc(lab(d.misconception)) : "wrong, no known misconception"})</option>`).join("");
}
function explainRecord(r, prompt) {
  const g = S.graph; const pre = {};
  g.prerequisites.forEach((e) => (pre[e.dst] ??= []).push(e.src));
  const parts = [];
  if (r.concept_provenance === "source") parts.push(`The item arrived tagged with ${r.concepts.map((c) => `<b>${esc(c.label)}</b>`).join(", ")}`);
  else parts.push(`No concept IDs were supplied. The aligner (${esc(r.concept_provenance)}) interpreted “${esc(prompt)}” as ${r.concepts.map((c) => `<b>${esc(c.label)}</b>`).join(", ") || "<b>nothing recognisable</b>"} with confidence ${r.confidence}. The evidence is weighted by that confidence`);
  if (r.outcome >= 0.5) {
    const ps = [...new Set(r.concepts.flatMap((c) => pre[c.id] || []))];
    parts.push(`Outcome ${r.concept_provenance === "source" ? "correct" : pct(r.outcome)}. Because the graph knows the prerequisites, it added weak evidence to ${ps.map((p) => `<b>${esc(lab(p))}</b>`).join(", ") || "none"}`);
  } else if (r.misconception) {
    parts.push(`Wrong answer matched a known distractor, so it was diagnosed as <b>${esc(r.misconception.label)}</b>. Only concepts that misconception affects were marked down (graph-based credit assignment)`);
  } else parts.push(`Outcome ${pct(r.outcome)}`);
  parts.push("The learner state was rebuilt from the full evidence log");
  return `<b>What H2 did with this evidence</b><ul>${parts.map((p) => `<li>${p}</li>`).join("")}</ul>`;
}

/* ------------------------------------------------------------------ context & tutor */
const SUGGEST = {
  "pupil:amara": ["I got 5x + 3 = 2x + 12 wrong again, I got x = 5. Can you help?", "How do I solve 7x − 2 = 3x + 10?", "What should I practise next?"],
  "pupil:ben": ["Can you help me with 3(x + 2) = 21?", "I don't get how to expand −2(x − 3).", "What should I practise next?"],
  "pupil:chloe": ["What's an inverse operation?", "Can you help me solve x + 9 = 14?", "What should I practise next?"],
};
function renderContextPage() {
  const ps = $("#c-pupil");
  if (!ps.options.length) {
    ps.innerHTML = S.pupils.map((p) => `<option value="${p.id}">${esc(p.name)}</option>`).join("");
    ps.onchange = () => { S.pupil = ps.value; $("#c-msg").value = SUGGEST[S.pupil][0]; sugg(); };
  }
  ps.value = S.pupil;
  if (!$("#c-msg").value) $("#c-msg").value = SUGGEST[S.pupil][0];
  $("#c-concept").innerHTML = `<option value="">Auto: align from the message</option>` + S.graph.concepts.filter((c) => c.status === "active").map((c) => `<option value="${c.id}">${esc(c.label)}</option>`).join("");
  sugg();
  const c = S.status.counts;
  const warns = [];
  if (!c.content_unit || !c.evidence) warns.push(`<div class="note warn">This works best once materials are ingested and scenario evidence exists. <button class="btn sm" id="btn-c-prep">Prepare demo data</button></div>`);
  if (!S.status.llm_available) warns.push(`<div class="note" style="margin-top:8px">No <code>ANTHROPIC_API_KEY</code> set, so both context packs are shown without live tutor replies. Add the key in Railway to generate replies from each context.</div>`);
  $("#c-warn").innerHTML = warns.join("");
  const pb = $("#btn-c-prep"); if (pb) pb.onclick = () => busy(pb, async () => { await api("/api/demo/prepare", { method: "POST" }); await refreshStatus(); renderContextPage(); toast("Demo data ready"); });
}
function sugg() { $("#c-suggest").innerHTML = SUGGEST[S.pupil].map((m) => `<button>${esc(m)}</button>`).join(""); $("#c-suggest").querySelectorAll("button").forEach((b) => (b.onclick = () => { $("#c-msg").value = b.textContent; })); }
function renderCompare(r) {
  const a = r.without_h2.context, b = r.with_h2.context;
  const reply = (x) => x.response ? `<div class="sec"><div class="st">Tutor reply (${esc(r.model)})</div><div class="reply">${esc(x.response)}</div></div>` : x.error ? `<div class="note warn">${esc(x.error)}</div>` : r.llm ? "" : `<div class="sec"><div class="st">Tutor reply</div><div class="sub">Needs ANTHROPIC_API_KEY</div></div>`;
  const left = `<div class="cmp-col">
    <div class="cmp-title"><span class="pill no">WITHOUT H2</span><span class="sub">~${a.approx_tokens} tokens of context</span></div>
    ${reply(r.without_h2)}
    <div class="sec"><div class="st">Recent activity (raw log)</div>${a.recent_activity.length ? `<ul>${a.recent_activity.map((l) => `<li>${esc(l)}</li>`).join("")}</ul>` : '<div class="sub">No activity</div>'}</div>
    <div class="sec"><div class="st">Materials by text similarity</div>${a.materials.length ? `<ul>${a.materials.map((m) => `<li><b>${esc(m.heading)}</b> <span class="sub">W${m.week} · ${esc(m.why)}</span></li>`).join("")}</ul>` : '<div class="sub">None (ingest materials first)</div>'}</div>
    <div class="note">${esc(a.note)}</div>
    <details><summary>Raw context JSON</summary><pre class="json">${esc(JSON.stringify(a, null, 2))}</pre></details></div>`;
  let right;
  if (b.error) right = `<div class="cmp-col"><div class="cmp-title"><span class="pill yes">WITH H2</span></div><div class="note warn">${esc(b.error)}</div></div>`;
  else {
    const L = b.learner, C = b.class, G = b.guidance;
    right = `<div class="cmp-col">
    <div class="cmp-title"><span class="pill yes">WITH H2</span><span class="sub">~${b.approx_tokens} tokens · graph v${esc(b.graph_version)}</span></div>
    ${reply(r.with_h2)}
    <div class="sec"><div class="st">Focus concept</div>${tag("concept", b.focus.id, null, { label: b.focus.label })}
      <div class="sub" style="margin-top:4px">${esc(b.focus.resolved.how)}${b.focus.resolved.confidence ? " · confidence " + b.focus.resolved.confidence : ""}</div>
      <div class="tags" style="margin-top:6px">${b.focus.crosswalk.map((x) => `<span class="tag plain">${esc(x.scheme)}: ${esc(x.id)}</span>`).join("")}</div></div>
    <div class="sec"><div class="st">Learner (H1)</div>${badge(L.status)} <span class="sub">mastery ${pct(L.mastery)}</span>
      ${L.active_misconceptions.length ? `<div style="margin-top:8px">${L.active_misconceptions.map((m) => `${tag("misconception", m.id)}<div class="sub" style="margin:3px 0 0">${esc(m.description)}</div>`).join("")}</div>` : '<div class="sub" style="margin-top:6px">No active misconception on this concept</div>'}
      <div class="sub" style="margin:8px 0 4px">Prerequisites</div><ul>${L.prerequisites.map((p) => `<li>${esc(p.label)} ${badge(p.status)}</li>`).join("")}</ul></div>
    <div class="sec"><div class="st">Class ${esc(C.id)} (H3)</div>${C.focus_taught ? `Taught in week ${C.focus_taught_week}` : "<b>Not yet taught to this class</b>"} · ${esc(C.teacher)}
      <div class="tags" style="margin-top:6px">${C.preferred_method ? tag("method", C.preferred_method.id) : ""}${C.preferred_representation ? tag("representation", C.preferred_representation.id) : ""}</div>
      <div class="sub" style="margin-top:4px">Teacher's preference, from aligned materials (${esc(C.preference_scope || "n/a")} level)</div></div>
    <div class="sec"><div class="st">Teacher's materials (filtered by concept, ranked by method + misconception)</div>${b.materials.length ? `<ul>${b.materials.map((m) => `<li><b>${esc(m.heading)}</b> <span class="sub">W${m.week}: ${esc(m.why)}</span></li>`).join("")}</ul>` : '<div class="sub">No aligned materials for this concept in taught weeks</div>'}</div>
    <div class="sec guidance"><div class="st">Guidance for the tutor</div><ul>
      ${G.target_misconception ? `<li>Target: <b>${esc(G.target_misconception.label)}</b></li>` : ""}
      ${G.reteach_with.method ? `<li>Reteach with <b>${esc(G.reteach_with.method.label)}</b>${G.reteach_with.representation ? ` using a <b>${esc(G.reteach_with.representation.label)}</b>` : ""}</li>` : ""}
      ${G.avoid_methods.length ? `<li>Avoid: ${G.avoid_methods.map((m) => esc(m.label)).join(", ")}</li>` : ""}
      ${G.check_prerequisites.length ? `<li>Check prerequisites first: ${G.check_prerequisites.map((p) => `${esc(p.label)} (${esc(p.status)})`).join(", ")}</li>` : ""}
      ${G.next_step ? `<li>Next step: <b>${esc(G.next_step.label)}</b>, ${esc(G.next_step_note)}</li>` : ""}</ul></div>
    <details><summary>Raw context JSON</summary><pre class="json">${esc(JSON.stringify(b, null, 2))}</pre></details></div>`;
  }
  $("#c-out").innerHTML = left + right;
}

/* ------------------------------------------------------------------ evaluate */
S.evMode = "heuristic";
function renderEvaluate() {
  const el = $("#ev-mode"), llm = S.status.llm_available;
  el.innerHTML = [["heuristic", "Heuristic"], ["claude", "Claude"]].map(([v, l]) => `<button data-v="${v}" class="${S.evMode === v ? "on" : ""}" ${v === "claude" && !llm ? 'disabled title="Set ANTHROPIC_API_KEY to enable"' : ""}>${l}</button>`).join("");
  el.querySelectorAll("button").forEach((b) => (b.onclick = () => { S.evMode = b.dataset.v; renderEvaluate(); }));
  if (!llm) $("#ev-tutor").innerHTML = $("#ev-tutor").innerHTML || `<div class="note">Needs <code>ANTHROPIC_API_KEY</code>. Set it on the Railway service and this runs 5 pupil turns through both arms, then scores them blind.</div>`;
  $("#btn-ev-tutor").disabled = !llm;
}
function renderAlignEval(d) {
  const s = d.summary;
  const k = (v, l) => `<div class="kpi"><div class="v">${v == null ? "–" : Math.round(v * 100) + "%"}</div><div class="l">${l}</div></div>`;
  const cell = (o, multi) => {
    const ok = multi ? JSON.stringify([...o.expected].sort()) === JSON.stringify([...o.got].sort()) : o.expected === o.got;
    const f = (x) => (x == null ? "—" : Array.isArray(x) ? (x.length ? x.map(lab).join(", ") : "—") : lab(x));
    return `<td><span class="${ok ? "ok" : "bad"}">${ok ? "✓" : "✗"}</span> ${ok ? `<span class="sub">${esc(f(o.got))}</span>` : `<div class="sub">expected: ${esc(f(o.expected))}</div><div class="sub">got: ${esc(f(o.got))}</div>`}</td>`;
  };
  $("#ev-align").innerHTML = `<div class="sub" style="margin-bottom:8px">Mode: <b>${esc(d.mode)}</b> · ${d.units} units</div>
    <div class="kpis">${k(s.concepts.precision, "Concept precision")}${k(s.concepts.recall, "Concept recall")}${k(s.misconceptions.precision, "Misconception precision")}${k(s.misconceptions.recall, "Misconception recall")}${k(s.method_accuracy, "Method accuracy")}${k(s.representation_accuracy, "Representation accuracy")}</div>
    ${d.rows.find((r) => r.fallback_reason) ? `<div class="note warn" style="margin-top:8px">${esc(d.rows.find((r) => r.fallback_reason).fallback_reason)}</div>` : ""}
    <details style="margin-top:12px"><summary>Per-unit results</summary><div class="tbl-wrap"><table class="t" style="margin-top:8px"><tr><th>Unit</th><th>Concepts</th><th>Method</th><th>Representation</th><th>Misconceptions</th></tr>
    ${d.rows.map((r) => `<tr><td><b>${esc(r.heading)}</b><div class="mono sub">${esc(r.unit)}</div></td>${cell(r.concepts, true)}${cell(r.method)}${cell(r.representation)}${cell(r.misconceptions, true)}</tr>`).join("")}</table></div></details>`;
}
function renderTutorEval(d) {
  if (d.error) { $("#ev-tutor").innerHTML = `<div class="note warn">${esc(d.error)}</div>`; return; }
  const names = { targets_misconception: "Targets the misconception", method_consistency: "Uses the teacher's method", within_scope: "Stays within taught scope", next_step: "Sensible next step" };
  const rows = d.criteria.map((c) => `<tr><td>${names[c]}</td><td style="width:32%">${bar(d.summary.without_h2[c] / 2, "var(--text-3)")}<span class="sub">${d.summary.without_h2[c]} / 2</span></td><td style="width:32%">${bar(d.summary.with_h2[c] / 2, "var(--accent)")}<span class="sub">${d.summary.with_h2[c]} / 2</span></td></tr>`).join("");
  $("#ev-tutor").innerHTML = `<div class="kpis" style="margin-bottom:12px"><div class="kpi"><div class="v">${d.summary.without_h2.total_of_8}</div><div class="l">Without H2 · mean score out of 8</div></div><div class="kpi"><div class="v" style="color:var(--accent-text)">${d.summary.with_h2.total_of_8}</div><div class="l">With H2 · mean score out of 8</div></div></div>
    <table class="t"><tr><th>Criterion</th><th>Without H2</th><th>With H2</th></tr>${rows}</table>
    <details style="margin-top:12px"><summary>Per-scenario replies and judge notes</summary>${d.results.map((r) => `<div class="sec" style="margin-top:8px"><div class="st">${esc((S.pupils.find((p) => p.id === r.learner) || {}).name || r.learner)}</div><b>“${esc(r.message)}”</b>
      ${r.error ? `<div class="note warn">${esc(r.error)}</div>` : `<div class="sub">Focus: ${esc(r.focus)} · judge: ${esc(r.notes || "")}</div>
      <div class="grid g2" style="margin-top:8px"><div><div class="sub"><b>Without H2</b> · ${Object.values(r.scores.without_h2).reduce((a, b) => a + b, 0)}/8</div><div class="reply">${esc(r.responses.without_h2)}</div></div>
      <div><div class="sub"><b>With H2</b> · ${Object.values(r.scores.with_h2).reduce((a, b) => a + b, 0)}/8</div><div class="reply">${esc(r.responses.with_h2)}</div></div></div>`}</div>`).join("")}</details>`;
}

/* ------------------------------------------------------------------ connect */
function renderConnect() {
  const url = location.origin + "/mcp/";
  $("#mcp-url").value = url;
  $("#btn-copy").onclick = () => { navigator.clipboard?.writeText(url); toast("Copied"); };
  $("#curl-ex").textContent = `curl -X POST ${location.origin}/api/context \\
  -H 'content-type: application/json' \\
  -d '{"learner":"pupil:amara",
       "message":"I got 5x + 3 = 2x + 12 wrong"}'`;
}

/* ------------------------------------------------------------------ static bindings */
function bindStatic() {
  $("#btn-prepare").onclick = (e) => busy(e.currentTarget, async () => { await api("/api/demo/prepare", { method: "POST" }); await refreshStatus(); toast("Materials ingested and scenarios recorded"); show("context"); });
  $("#btn-reset").onclick = (e) => busy(e.currentTarget, async () => { await api("/api/admin/reset", { method: "POST" }); await loadGraph(); S.sel = null; S.overlay = "none"; S.lastExplain = null; S.ctxDone = S.evalDone = false; await refreshStatus(); toast("Demo reset to graph 2026.2 with no alignments or evidence"); show("overview"); });
  $("#btn-release").onclick = (e) => busy(e.currentTarget, async () => {
    const r = await api("/api/admin/release", { method: "POST" });
    await loadGraph(); await refreshStatus();
    $("#release-out").className = "explain";
    $("#release-out").innerHTML = `<b>Released ${esc(r.from)} → ${esc(r.to)}</b><ul>
      <li><b>Reference graph:</b> ${r.graph_changes.map(esc).join("; ")}</li>
      <li><b>Alignments:</b> ${r.alignments_migrated} migrated to the new ID and re-stamped v${esc(r.to)}</li>
      <li><b>Evidence:</b> ${r.evidence_rows_referencing_deprecated_ids} rows reference deprecated IDs; <b>${r.evidence_rows_modified} modified</b></li>
      <li><b>Learner state:</b> ${r.learners_reprojected} learners rebuilt. On the new concept: ${r.learner_state_new_concept.map((s) => `${esc(s.learner)} ${esc(s.status)} (${pct(s.mastery)})`).join(", ") || "no evidence yet"}</li></ul>
      <div class="sub" style="margin-top:6px">${esc(r.note)} Use Reset on the Overview to return to 2026.2.</div>`;
    S.sel = "cc:maths/alg/lin-eq-simple"; renderGraphPage();
  });
  $("#btn-ingest-all").onclick = (e) => busy(e.currentTarget, async () => { await api("/api/materials/ingest", { method: "POST", body: { mode: S.mode } }); await refreshStatus(); S.open.add("mat:10x-w4-both-sides"); await renderMaterials(); toast("All materials aligned"); });
  $("#btn-try").onclick = (e) => busy(e.currentTarget, async () => { const r = await api("/api/align", { method: "POST", body: { text: $("#try-text").value, mode: S.mode } }); $("#try-out").innerHTML = renderAlignResult(r); });
  $("#btn-add-mat").onclick = (e) => busy(e.currentTarget, async () => {
    const t = $("#try-text").value.trim(); if (!t) return;
    const cls = (await api("/api/classes")).find((c) => c.id === S.cls);
    const r = await api("/api/materials", { method: "POST", body: { class_id: S.cls, week: cls.current_week, title: "Added: " + t.slice(0, 48) + (t.length > 48 ? "…" : ""), body: "## Added unit\n" + t, mode: S.mode } });
    S.open.add(r.material); await refreshStatus(); await renderMaterials(); toast("Added and aligned");
  });
  $("#btn-e-item").onclick = (e) => busy(e.currentTarget, async () => {
    const i = S.items.find((x) => x.id === $("#e-item").value);
    const r = await api("/api/evidence", { method: "POST", body: { learner: S.pupil, source: $("#e-src").value, item: i.id, response: $("#e-resp").value, mode: S.mode } });
    S.lastExplain = explainRecord(r.recorded, i.prompt); await refreshStatus(); await renderEvidence();
  });
  $("#btn-e-act").onclick = (e) => busy(e.currentTarget, async () => {
    const act = $("#e-act").value;
    const r = await api("/api/evidence", { method: "POST", body: { learner: S.pupil, source: "Teacher markbook", activity: act, score: $("#e-score").value / 100, mode: S.mode } });
    S.lastExplain = explainRecord(r.recorded, act); await refreshStatus(); await renderEvidence();
  });
  $("#btn-e-clear").onclick = (e) => busy(e.currentTarget, async () => { await api(`/api/learners/${S.pupil}/clear`, { method: "POST" }); S.lastExplain = null; await refreshStatus(); await renderEvidence(); });
  $("#btn-ctx").onclick = (e) => busy(e.currentTarget, async () => {
    S.pupil = $("#c-pupil").value;
    const r = await api("/api/tutor", { method: "POST", body: { learner: S.pupil, message: $("#c-msg").value, concept: $("#c-concept").value || null, mode: S.mode } });
    renderCompare(r); S.ctxDone = true; renderSteps();
  });
  $("#btn-ev-align").onclick = (e) => busy(e.currentTarget, async () => { const d = await api(`/api/eval/alignment?mode=${S.evMode}`, { method: "POST" }); renderAlignEval(d); S.evalDone = true; renderSteps(); });
  $("#btn-ev-tutor").onclick = (e) => busy(e.currentTarget, async () => { const d = await api("/api/eval/tutor", { method: "POST" }); renderTutorEval(d); S.evalDone = true; await refreshStatus(); });
}

boot().catch((e) => { document.body.insertAdjacentHTML("beforeend", `<div class="toast show">Failed to load: ${esc(e.message)}</div>`); });
