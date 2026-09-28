/* Curriculum Map for AI Tutors — POC UI, plain JS, no build step. */
const S = { subject: "maths", status: null, graph: null, labels: {}, page: "overview", pupil: "pupil:amara", cls: "10X", mode: null,
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
  const k = { concept: "topic", method: "method", representation: "rep", misconception: "misc", section: "act" }[facet] || facet;
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
  let saved = null; try { saved = localStorage.getItem("cm-subject"); } catch {}
  const q = new URLSearchParams(location.search).get("subject");
  S.subject = q || saved || "maths";
  S.items = await api("/api/items");
  await loadSubject();
  await refreshStatus();
  bindStatic();
  $("#subject").value = S.subject;
  $("#subject").onchange = async () => { S.subject = $("#subject").value; try { localStorage.setItem("cm-subject", S.subject); } catch {} await loadSubject(); await refreshStatus(); show(S.page); };
  show((location.hash || "#overview").slice(1));
  window.addEventListener("hashchange", () => { const p = location.hash.slice(1); if (p && p !== S.page) show(p); });
}
const SUBTITLE = { maths: "Proof of concept · KS3–4 linear equations · synthetic data only", english: "Proof of concept · KS4 English Literature: Macbeth · synthetic data only" };
async function loadSubject() {
  const sub = S.subject;
  [S.pupils, S.scenarios, S.classes] = await Promise.all([api(`/api/pupils?subject=${sub}`), api(`/api/scenarios?subject=${sub}`), api(`/api/classes?subject=${sub}`)]);
  await loadGraph();
  if (!S.pupils.some((p) => p.id === S.pupil)) S.pupil = S.pupils[0].id;
  if (!S.classes.some((c) => c.id === S.cls)) S.cls = S.classes[0].id;
  S.sel = null; S.overlay = "none"; S.lastExplain = null; S.suiteLoaded = false;
  if (sub === "english") { S.evSet = "english"; S.suite = "english_heldout"; } else if (S.suite.startsWith("english")) { S.evSet = "heldout2"; S.suite = "heldout"; }
  const cp = $("#c-pupil"); if (cp) cp.innerHTML = ""; const cm = $("#c-msg"); if (cm) cm.value = "";
  $("#subtitle").textContent = SUBTITLE[sub] || "";
  document.body.dataset.subject = sub;
}
async function loadGraph() {
  S.graph = await api(`/api/graph?subject=${S.subject}`);
  S.labels = {};
  for (const k of ["concepts", "misconceptions", "methods", "representations", "sections"]) for (const n of S.graph[k] || []) S.labels[n.id] = n.label;
}
async function refreshStatus() {
  S.status = await api("/api/status");
  const st = S.status, c = st.counts;
  $("#chips").innerHTML = [
    `<span class="chip ok"><span class="dot"></span>Map ${esc(S.graph.version)}</span>`,
    `<span class="chip ${st.llm_available ? "ok" : "warn"}"><span class="dot"></span>Tagger: ${st.aligner_mode === "claude" ? "Claude" : "keyword"}</span>`,
    `<span class="chip ${st.llm_available ? "ok" : "warn"}"><span class="dot"></span>${st.llm_available ? "Tutor: " + esc(st.model) : "No Anthropic key"}</span>`,
    `<span class="chip"><span class="dot"></span>${c.alignment} tags · ${c.evidence} pupil answers</span>`,
  ].join("");
  renderArch(); renderSteps();
}
function show(page) {
  if (!document.getElementById("page-" + page)) page = "overview";
  S.page = page; S.visited.add(page); history.replaceState(null, "", "#" + page);
  document.querySelectorAll(".page").forEach((p) => p.classList.toggle("active", p.id === "page-" + page));
  document.querySelectorAll("#nav button").forEach((b) => b.classList.toggle("active", b.dataset.page === page));
  ({ overview: renderOverview, graph: renderGraphPage, materials: renderMaterials, evidence: renderEvidence, context: renderContextPage, evaluate: renderEvaluate, connect: renderConnect, help: renderHelp, theory: renderHelp, authoring: renderAuthoring }[page])();
  window.scrollTo({ top: 0 });
}

/* ------------------------------------------------------------------ overview */
function renderOverview() { renderArch(); renderSteps(); }
function renderSteps() {
  const c = S.status?.counts || {};
  const steps = [
    ["graph", "Explore the curriculum map", "Topics, misconceptions, teaching methods, links to other curricula", S.visited.has("graph")],
    ["materials", "Tag the teacher's materials", "Tag them against the map and work out what the class has covered", c.content_unit > 0],
    ["evidence", "Record pupil progress", "Build a picture of each pupil's progress on the same topics", c.evidence > 0],
    ["context", "Compare tutor briefings", "Raw classroom data vs organised by the map, for the same pupil question", S.ctxDone],
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
  <text class="t-group" x="238" y="36">SHARED CURRICULUM MAP</text>
  ${box(16, 50, 170, 64, "Curriculum teams", "+ shared public layer", "")}
  ${box(16, 202, 170, 64, "Teacher's materials", "school LMS / drive", "", "", "materials")}
  ${box(16, 330, 170, 64, "Pupils' answers", "tutors · homework · markbook", "", "", "evidence")}
  ${box(236, 50, 240, 88, "Curriculum map", `version ${st.graph_version} · published releases`, `${c.concepts} topics · ${c.crosswalk} curriculum links`, "h2", "graph")}
  ${box(236, 190, 240, 88, "Tagging service", `tagger: ${st.aligner_mode === "claude" ? "Claude" : "keyword"}`, `${c.alignment} tags stored`, "h2", "materials")}
  ${box(540, 120, 200, 72, "Tagged materials", "searchable by text and topic", `${c.content_unit} teaching units`, "", "materials")}
  ${box(540, 310, 200, 64, "Pupil answers log", "only ever added to", `${c.evidence} answers`, "", "evidence")}
  ${box(540, 404, 200, 64, "Pupil progress", "rebuilt from the log", `${c.learner_state} topic estimates`, "", "evidence")}
  ${box(790, 200, 172, 88, "Tutor briefing", "one per pupil question", "REST + MCP", "h2", "context")}
  ${box(790, 362, 172, 64, "AI tutor(s)", "any vendor", "", "", "connect")}
  ${arr(186, 82, 236, 94)}
  <path class="flow" d="M356,138 L356,190" marker-end="url(#ah)"/>${lbl(364, 168, "constrains")}
  ${arr(186, 234, 236, 234)}
  ${arr(476, 212, 540, 156)}${lbl(496, 150, "tag", "end")}
  ${arr(186, 362, 540, 342)}${lbl(300, 346, "questions already tagged", "middle")}
  ${arr(476, 256, 540, 326)}${lbl(420, 318, "untagged entries tagged", "middle")}
  <path class="flow" d="M640,374 L640,404" marker-end="url(#ah)"/>${lbl(648, 394, "rebuild")}
  <path class="flow read" d="M476,70 C700,40 876,90 876,200" marker-end="url(#ah-r)"/>
  ${arr(740, 156, 790, 244, "read")}
  ${arr(740, 436, 790, 268, "read")}
  <path class="flow read" d="M876,288 L876,362" marker-end="url(#ah-r)"/>${lbl(884, 330, "briefing")}
  <text class="t-sub" x="16" y="484">Solid arrows: write paths. Dashed: read path for one tutor turn. Click a box to open that part.</text>`;
  const el = $("#arch"); el.innerHTML = svg;
  el.querySelectorAll("[data-go]").forEach((g) => (g.onclick = () => show(g.dataset.go)));
}

/* ------------------------------------------------------------------ graph renderer */
const NW = 150, NH = 48, CG = 34, RG = 12, PAD = 14;
function layoutStrands() {
  const cs = S.graph.concepts.filter((c) => c.status === "active");
  const strands = S.graph.strands.map((x) => x.id);
  const pos = {}; let maxRows = 0;
  strands.forEach((st, col) => {
    const arr = cs.filter((c) => c.strand === st).sort((a, b) => a.layer - b.layer || a.label.localeCompare(b.label));
    maxRows = Math.max(maxRows, arr.length);
    arr.forEach((c, row) => (pos[c.id] = { row, col, x: PAD + col * (NW + CG), y: PAD + 22 + row * (NH + RG) }));
  });
  const short = (l) => l.split(/[,(]/)[0].trim();
  return { pos, w: PAD * 2 + strands.length * (NW + CG) - CG + 60, h: PAD * 2 + 22 + maxRows * (NH + RG) - RG, heads: S.graph.strands.map((x, i) => ({ label: short(x.label), x: PAD + i * (NW + CG) })) };
}
function layout() {
  if (S.graph.layout === "strands") return layoutStrands();
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
  const { pos, w, h, heads } = layout();
  const sel = o.selected;
  const near = new Set();
  const related = S.graph.related || [];
  if (sel && o.dimOthers) {
    near.add(sel);
    S.graph.prerequisites.forEach((e) => { if (e.dst === sel) near.add(e.src); if (e.src === sel) near.add(e.dst); });
    related.forEach(([a, b]) => { if (a === sel) near.add(b); if (b === sel) near.add(a); });
  }
  let out = (heads || []).map((hd) => `<text class="strand-head" x="${hd.x + 2}" y="${PAD + 10}">${esc(hd.label)}</text>`).join("");
  // related links (web): only for the selected topic, otherwise the picture is a hairball
  if (sel) for (const [s1, s2] of related) {
    if (s1 !== sel && s2 !== sel) continue;
    const a = pos[s1], b = pos[s2]; if (!a || !b) continue;
    const [p, q2] = a.x <= b.x ? [a, b] : [b, a];
    const x1 = p.x + (p.col === q2.col ? NW : NW), y1 = p.y + NH / 2, x2 = p.col === q2.col ? q2.x + NW : q2.x, y2 = q2.y + NH / 2;
    const mx = p.col === q2.col ? x1 + 40 : (x1 + x2) / 2;
    out += `<path class="gedge rel" d="M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}"/>`;
  }
  for (const e of S.graph.prerequisites) {
    const a = pos[e.src], b = pos[e.dst]; if (!a || !b) continue;
    const cls = sel && e.dst === sel ? "hl" : sel && e.src === sel ? "hl-out" : "";
    if (a.col === b.col) {  // same column (English writing skills): arc out to the right
      const x = a.x + NW, y1 = a.y + NH / 2, y2 = b.y + NH / 2, bulge = 18 + 8 * Math.abs(a.row - b.row);
      out += `<path class="gedge ${cls}" d="M${x},${y1} C${x + bulge},${y1} ${x + bulge},${y2} ${x},${y2}" stroke-opacity="${0.4 + 0.6 * e.weight}"/>`;
      continue;
    }
    const x1 = a.x + NW, y1 = a.y + NH / 2, x2 = b.x, y2 = b.y + NH / 2, mx = (x1 + x2) / 2;
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
const LEGEND_REL = `<span><svg width="26" height="8"><path d="M0,4 L26,4" stroke="var(--method)" stroke-width="2" stroke-dasharray="4 3"/></svg>Related to selected (click a topic)</span>`;
const LEGEND_EDGES = `<span><svg width="26" height="8"><path d="M0,4 L26,4" stroke="var(--accent)" stroke-width="2.2"/></svg>Prerequisite of selected</span><span><svg width="26" height="8"><path d="M0,4 L26,4" stroke="var(--method)" stroke-width="2.2"/></svg>Leads to</span>`;

async function overlayData(key) {
  if (key.startsWith("class:")) {
    const cov = await api(`/api/classes/${key.slice(6)}/coverage`);
    const t = new Set(cov.taught.map((c) => c.id)), p = new Set(cov.planned.map((c) => c.id));
    return { classFor: (id) => (t.has(id) ? "taught" : p.has(id) ? "planned" : ""), legend: LEGEND_COVER, empty: !cov.taught.length && !cov.planned.length ? "Tag materials first to see coverage." : "" };
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
  $("#g-counts").textContent = `${active.length} topics · ${g.prerequisites.length} prerequisite links${g.related.length ? ` · ${g.related.length} related-topic links · ${g.quotations.length} key quotations · ${g.sections.length} acts` : ""} · ${g.misconceptions.length} misconceptions · ${g.methods.length} methods · ${g.representations.length} representations${dep.length ? ` · ${dep.length} deprecated` : ""}`;
  const ov = $("#g-overlay");
  ov.innerHTML = `<option value="none">Overlay: none</option>` + S.classes.map((c) => `<option value="class:${c.id}">Overlay: class ${c.id} coverage</option>`).join("") +
    S.pupils.map((p) => `<option value="${p.id}">Overlay: ${esc(p.name)} progress</option>`).join("");
  ov.value = S.overlay;
  ov.onchange = () => { S.overlay = ov.value; drawMain(); };
  await drawMain();
  renderBehaviourFramework();
  $("#g-others").innerHTML = `
    <h4 style="margin:4px 0 6px;font-size:12px;color:var(--text-3)">MISCONCEPTIONS → concepts they affect</h4>
    <div class="tbl-wrap"><table class="t">${g.misconceptions.map((m) => `<tr><td style="width:45%">${tag("misconception", m.id)}<div class="sub" style="margin:4px 0 0">${esc(m.description || "")}</div></td><td><div class="tags">${m.affects.map((c) => tag("concept", c)).join("")}</div></td></tr>`).join("")}</table></div>
    <h4 style="margin:14px 0 6px;font-size:12px;color:var(--text-3)">METHODS → concepts they teach</h4>
    <div class="tbl-wrap"><table class="t">${g.methods.map((m) => `<tr><td style="width:45%">${tag("method", m.id)}</td><td class="sub">${m.teaches.length} concepts</td></tr>`).join("")}</table></div>
    <h4 style="margin:14px 0 6px;font-size:12px;color:var(--text-3)">REPRESENTATIONS</h4>
    <div class="tags">${g.representations.map((r) => tag("representation", r.id)).join("")}</div>`;
  if (S.sel) inspect(S.sel);
  $("#btn-release").disabled = S.subject !== "maths" || g.version !== "2026.2";
}
async function drawMain() {
  const od = await overlayData(S.overlay);
  drawGraph($("#g-svg"), { ...od, selected: S.sel, dimOthers: !!S.sel && S.overlay === "none", onClick: (id) => { S.sel = id; drawMain(); inspect(id); } });
  $("#g-legend").innerHTML = (od.empty ? `<span class="sub">${od.empty}</span>` : od.legend) + LEGEND_EDGES + (S.graph.layout === "strands" ? LEGEND_REL : "");
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
      ${d.strand ? `<h4>Strand</h4><div class="sub">${esc((S.graph.strands.find((x) => x.id === d.strand) || {}).label || d.strand)}</div>` : ""}
      ${d.prerequisites.length || !d.strand ? `<h4>Prerequisites</h4>${chips(d.prerequisites)}` : ""}
      ${d.leads_to.length || !d.strand ? `<h4>Leads to</h4>${chips(d.leads_to)}` : ""}
      ${d.related && d.related.length ? `<h4>Related topics</h4>${chips(d.related)}` : ""}
      ${d.quotations && d.quotations.length ? `<h4>Key quotations</h4>${d.quotations.map((q) => `<div style="margin-bottom:8px"><p class="quote">“${esc(q.text)}”</p><div class="sub">${esc(q.speaker)}, ${esc(q.act)}</div></div>`).join("")}` : ""}
      <h4>Misconceptions that affect it</h4>${d.misconceptions.length ? d.misconceptions.map((m) => `<div style="margin-bottom:6px">${tag("misconception", m.id)}<div class="sub" style="margin:3px 0 0">${esc(m.description || "")}</div></div>`).join("") : `<div class="sub">None</div>`}
      <h4>Methods that teach it</h4>${chips(d.methods, "method")}
      ${d.case ? `<h4>CASE identity</h4><dl class="kv"><dt>Identifier</dt><dd class="mono" style="font-size:11px">${esc(d.case.identifier)}</dd><dt>Web address</dt><dd><a href="${esc(d.case.uri)}" target="_blank" style="font-size:11px;word-break:break-all">${esc(d.case.uri)}</a></dd><dt>Readable ID</dt><dd class="mono" style="font-size:11px">${esc(d.case.alias)}</dd></dl>` : ""}
      <h4>Links to other curricula</h4>${d.crosswalk.length ? `<table class="t">${d.crosswalk.map((x) => `<tr><td>${esc(x.scheme)}</td><td class="mono">${esc(x.id)}</td><td>${esc(x.match)}</td></tr>`).join("")}</table><div class="sub" style="margin-top:6px">Illustrative mappings. “shared-layer” IDs are placeholders for whichever shared public layer is adopted.</div>` : `<div class="sub">None</div>`}
      ${d.ancestors.length || !d.strand ? `<h4>Full prerequisite chain</h4><div class="sub">${d.ancestors.length} ancestor concepts, up to ${Math.max(0, ...d.ancestors.map((a) => a.depth))} steps back</div>` : ""}` : ""}
    ${d.affects ? `<h4>Affects</h4>${chips(d.affects)}` : ""}${d.teaches ? `<h4>Teaches</h4>${chips(d.teaches)}` : ""}
    <h4>Usage</h4><dl class="kv"><dt>Tags pointing here</dt><dd>${d.usage.alignments}</dd><dt>Pupil answers tagged</dt><dd>${d.usage.evidence}</dd></dl>`;
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
  const classes = S.classes;
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
        ${badge(m.status)} ${m.ingested ? `<span class="badge secure">tagged</span>` : `<span class="badge">not tagged yet</span>`}
        <button class="btn sm" data-ingest="${esc(m.id)}">${m.ingested ? "Re-tag" : "Read & tag"}</button></div>
      <div class="mat-body">${m.units.length ? m.units.map((u) => `
        <div class="unit"><div class="h">${esc(u.heading)} <span class="mono sub">#${u.idx}</span></div><div class="b">${esc(u.body)}</div>
          <div class="tags">${u.alignments.length ? ["concept", "method", "representation", "misconception", "section"].flatMap((f) => u.alignments.filter((a) => a.facet === f).map((a) => tag(f, a.id, a.confidence))).join("") : `<span class="sub">No tags above threshold</span>`}</div>
          ${u.alignments[0] ? `<div class="sub" style="margin-top:4px">tagged by ${esc(u.alignments[0].provenance)} · map v${esc(u.alignments[0].graph_version)}</div>` : ""}</div>`).join("")
        : `<div class="b" style="white-space:pre-wrap;color:var(--text-2);font-size:12.5px">${esc(m.body)}</div><div class="note" style="margin-top:8px">Not tagged yet. Click <b>Read & tag</b> to split it into units and align each one.</div>`}</div>
    </div>`).join("");
  document.querySelectorAll(".mat-head").forEach((h) => (h.onclick = (e) => {
    if (e.target.closest("[data-ingest]")) return;
    const id = h.parentElement.dataset.m; S.open.has(id) ? S.open.delete(id) : S.open.add(id); h.parentElement.classList.toggle("open");
  }));
  document.querySelectorAll("[data-ingest]").forEach((b) => (b.onclick = () => busy(b, async () => {
    await api("/api/materials/ingest", { method: "POST", body: { material_id: b.dataset.ingest, mode: S.mode } });
    S.open.add(b.dataset.ingest); await refreshStatus(); await renderMaterials(); toast("Tagged");
  })));
  // coverage
  const cov = await api(`/api/classes/${S.cls}/coverage`);
  const methods = {};
  mats.filter((m) => m.status === "taught").forEach((m) => m.units.forEach((u) => u.alignments.filter((a) => a.facet === "method" && a.confidence >= 0.5).forEach((a) => (methods[a.id] = (methods[a.id] || 0) + 1))));
  $("#m-coverage").innerHTML = !cov.taught.length && !cov.planned.length ? `<div class="empty">Tag materials to see what the class has covered</div>` : `
    <div class="sub" style="margin:0 0 6px"><b>Taught so far</b> (weeks 1–${cov.class.current_week})</div>
    <div class="tags">${cov.taught.map((c) => tag("concept", c.id, null, { label: `W${c.week} · ${c.label}` })).join("") || '<span class="sub">none</span>'}</div>
    <div class="sub" style="margin:12px 0 6px"><b>Planned</b></div>
    <div class="tags">${cov.planned.map((c) => tag("concept", c.id, null, { label: `W${c.week} · ${c.label}` })).join("") || '<span class="sub">none</span>'}</div>
    ${cov.sections_studied ? `<div class="sub" style="margin:12px 0 6px"><b>Parts of the play studied</b></div><div class="tags">${cov.sections_studied.map((x) => `<span class="tag plain">${esc(x.label)} · from W${x.week}</span>`).join("") || '<span class="sub">none</span>'}${cov.sections_coming.map((x) => `<span class="tag plain" style="border-style:dashed">${esc(x.label)} · ${x.week ? "W" + x.week : "not planned yet"}</span>`).join("")}</div>` : ""}
    <div class="sub" style="margin:12px 0 6px"><b>Teacher's methods in taught units</b></div>
    <div class="tags">${Object.entries(methods).sort((a, b) => b[1] - a[1]).map(([id, n]) => tag("method", id, null, { label: `${lab(id)} ×${n}` })).join("") || '<span class="sub">none</span>'}</div>
    <div style="margin-top:10px"><button class="btn sm" id="btn-cov-graph">View on graph</button></div>`;
  const cg = $("#btn-cov-graph"); if (cg) cg.onclick = () => { S.overlay = "class:" + S.cls; show("graph"); };
}
function renderAlignResult(r) {
  const tags = [...r.concepts.map((c) => tag("concept", c.id, c.confidence)), r.method ? tag("method", r.method.id, r.method.confidence) : "", r.representation ? tag("representation", r.representation.id, r.representation.confidence) : "", ...r.misconceptions.map((m) => tag("misconception", m.id, m.confidence))].join("");
  return `<div class="tags">${tags || '<span class="sub">No tags above threshold</span>'}</div>
    <div class="sub" style="margin-top:6px">tagged by ${esc(r.provenance)} · map v${esc(r.graph_version)}${r.candidates_considered ? ` · ${r.candidates_considered} candidate topics considered from the graph` : ""}</div>
    ${r.rationale ? `<div class="note" style="margin-top:6px">${esc(r.rationale)}</div>` : ""}
    ${r.fallback_reason ? `<div class="note warn" style="margin-top:6px">${esc(r.fallback_reason)}</div>` : ""}
    ${r.rejected_ids && r.rejected_ids.length ? `<div class="note warn" style="margin-top:6px">Rejected ${r.rejected_ids.length} label(s) not on the map. The map constrains the model.</div>` : ""}`;
}

/* ------------------------------------------------------------------ evidence (H1) */
async function renderEvidence() {
  $("#e-pupils").innerHTML = S.pupils.map((p) => { const sc = S.scenarios.find((s) => s.pupil === p.id); return `<button class="pupil ${p.id === S.pupil ? "on" : ""}" data-p="${p.id}"><b>${esc(p.name)}</b><span>Class ${esc(p.class)} · ${esc(sc ? sc.title : "")}</span></button>`; }).join("");
  document.querySelectorAll("[data-p]").forEach((b) => (b.onclick = () => { S.pupil = b.dataset.p; S.lastExplain = null; renderEvidence(); }));
  const sc = S.scenarios.find((s) => s.pupil === S.pupil);
  $("#e-scenario").innerHTML = sc ? `<h3>Preset scenario</h3><p class="sub">${esc(sc.title)}</p><p style="margin:0 0 10px;font-size:13px;color:var(--text-2)">${esc(sc.story)}</p>
    <div class="sub">${sc.events.filter((e) => e.concepts && e.concepts.length).length ? `${sc.events.length} pieces of teacher-marked work` : `${sc.events.filter((e) => e.item).length} item answers · ${sc.events.filter((e) => e.activity).length} markbook entries`}</div>
    <button class="btn primary" id="btn-sc" style="margin-top:10px">Run scenario</button><div class="sub" style="margin-top:6px">Replaces this pupil's evidence with the scenario.</div>` : "";
  const b = $("#btn-sc"); if (b) b.onclick = () => busy(b, async () => {
    const r = await api(`/api/scenarios/${sc.id}/run`, { method: "POST" });
    if (r.recorded.every((x) => x.concept_provenance === "teacher")) {
      const nMc = r.recorded.filter((x) => x.misconception).length;
      S.lastExplain = `<b>Recorded ${r.recorded.length} pieces of marked work</b><ul><li>Each came with the topics the teacher said it covered, and a mark</li><li>${nMc} had a misconception recorded by the teacher. Where the mark was low, only the topics that misconception affects were marked down (usually a writing skill, not the character or theme)</li><li>Progress was rebuilt from all the work</li></ul>`;
      await refreshStatus(); await renderEvidence(); return;
    }
    const nItem = r.recorded.filter((x) => x.concept_provenance === "source").length, nAct = r.recorded.length - nItem;
    const diag = r.recorded.filter((x) => x.misconception).length;
    const acts = sc.events.map((ev, i) => [ev, r.recorded[i]]).filter(([ev]) => ev.activity);
    const actTxt = acts.map(([ev, rec]) => `“${esc(ev.activity)}” as ${rec.concepts.map((c) => `<b>${esc(c.label)}</b>`).join(", ") || "nothing recognisable"} (confidence ${rec.confidence})`).join("; ");
    S.lastExplain = `<b>Recorded ${r.recorded.length} answers</b><ul><li>${nItem} answers were to questions already tagged to the map</li>${nAct ? `<li>${nAct} markbook entr${nAct > 1 ? "ies" : "y"} had no topics attached. The tagger interpreted ${actTxt}. Those entries count for less, in proportion to the confidence</li>` : ""}<li>${diag} wrong answers matched a known distractor, so a misconception was diagnosed</li><li>Progress was rebuilt from all the answers, spreading evidence across the map</li>${r.recorded.some((x) => x.behaviour && x.behaviour.stored) ? `<li>How they worked was recorded for ${r.recorded.filter((x) => x.behaviour && x.behaviour.stored).length} answers and turned into learning-behaviour patterns${r.recorded.some((x) => x.behaviour && x.behaviour.discarded_session_only) ? "; emotional signals were discarded (session only)" : ""}</li>` : ""}</ul>`;
    await refreshStatus(); await renderEvidence();
  });
  // forms: maths has tagged items; English has teacher-marked written work
  const eng = S.subject === "english";
  $("#e-card-item").hidden = eng; $("#e-card-act").hidden = eng; $("#e-card-marked").hidden = !eng;
  if (eng && !$("#em-topics").children.length) {
    $("#em-topics").innerHTML = S.graph.strands.map((st) => `<div class="grp">${esc(st.label)}</div>` + S.graph.concepts.filter((c) => c.strand === st.id).map((c) => `<label><input type="checkbox" value="${esc(c.id)}"${["cc:english/macbeth/th-guilt", "cc:english/macbeth/sk-language"].includes(c.id) ? " checked" : ""}>${esc(c.label)}</label>`).join("")).join("");
    $("#em-mc").innerHTML = `<option value="">None seen</option>` + S.graph.misconceptions.map((m) => `<option value="${esc(m.id)}">${esc(m.label)}</option>`).join("");
    $("#em-score").oninput = () => ($("#em-score-v").textContent = $("#em-score").value + "%");
  }
  if (!eng) $("#em-topics").innerHTML = "";
  const it = $("#e-item");
  if (!it.options.length) {
    it.innerHTML = S.items.map((i) => `<option value="${i.id}">${esc(i.prompt)}</option>`).join("");
    it.onchange = fillResp; fillResp();
    $("#e-score").oninput = () => ($("#e-score-v").textContent = $("#e-score").value + "%");
  }
  const lv = await api(`/api/learners/${S.pupil}`);
  $("#e-sub").textContent = `${lv.pupil.name} · ${lv.evidence.length} answers recorded · ${lv.state.length} topics with an estimate`;
  $("#e-explain").innerHTML = S.lastExplain ? `<div class="explain">${S.lastExplain}</div>` : lv.evidence.length ? "" : `<div class="note">No evidence yet. Run the preset scenario or record answers on the right.</div>`;
  const od = await overlayData(S.pupil);
  drawGraph($("#e-svg"), { ...od, onClick: (id) => { S.sel = id; S.overlay = S.pupil; show("graph"); } });
  $("#e-legend").innerHTML = LEGEND_MASTERY;
  $("#e-states").innerHTML = lv.state.length ? `<table class="t">${lv.state.map((s) => `<tr><td>${esc(s.label)}</td><td>${badge(s.status)}</td><td style="width:90px">${bar(s.mastery, statusColor(s.status))}<div class="sub">${pct(s.mastery)}</div></td><td class="sub" title="direct / inferred evidence">${s.n_direct}d · ${s.n_inferred}i</td></tr>`).join("")}</table>` : `<div class="empty">No estimates yet</div>`;
  renderBehaviour(await api(`/api/learners/${S.pupil}/behaviour`));
  $("#e-mcs").innerHTML = lv.misconceptions.length ? lv.misconceptions.map((m) => `<div style="margin-bottom:12px">${tag("misconception", m.id)} ${m.active ? badge("gap").replace(">gap<", ">active<") : badge("faded")}
      <div class="row" style="align-items:center;margin-top:6px"><div style="flex:1">${bar(m.strength, "var(--mc)")}</div><span class="sub">strength ${pct(m.strength)} · seen ${m.count}×</span></div>
      <div class="sub" style="margin-top:4px">Affects: ${m.affects.map((c) => esc(c.label)).join(", ")}</div></div>`).join("") : `<div class="empty">None diagnosed</div>`;
  $("#e-log").innerHTML = lv.evidence.length ? `<table class="t"><tr><th>#</th><th>Source</th><th>Evidence</th><th>Outcome</th><th>Concepts (how tagged)</th><th>Misconception</th></tr>${lv.evidence.map((e) => `<tr>
    <td class="mono">${e.id}</td><td>${esc(e.source)}</td>
    <td>${e.prompt ? `${esc(e.prompt)}<div class="sub">→ “${esc(e.response)}”</div>` : `<i>${esc(e.activity)}</i>`}${e.process ? `<div class="sub" style="margin-top:3px">How: ${esc(e.process)}</div>` : ""}</td>
    <td>${e.item ? (e.outcome >= 0.5 ? '<span class="ok">✓</span>' : '<span class="bad">✗</span>') : pct(e.outcome)}</td>
    <td><div class="tags">${e.concepts.map((c) => tag("concept", c.id, null, { label: c.label })).join("")}</div><div class="sub">${e.concept_provenance === "source" ? "tagged at source" : e.concept_provenance === "teacher" ? "topics given by the teacher" : "aligned: " + esc(e.concept_provenance) + " · conf " + e.confidence} · v${esc(e.graph_version)}</div></td>
    <td>${e.misconception ? tag("misconception", e.misconception.id) : ""}</td></tr>`).join("")}</table>` : `<div class="empty">Empty</div>`;
}
const BSTAT = { support: ["gap", "pattern to support"], strength: ["secure", "strength"], mixed: ["developing", "mixed"], "too little evidence": ["", "too little evidence"] };
function renderBehaviour(b) {
  const el = $("#e-beh");
  const pats = b.patterns;
  el.innerHTML = `<div class="card-head"><div><h3>Learning behaviour</h3><p class="sub">How this pupil learns, worked out from what tools observed during each answer. Recent patterns, not fixed traits.</p></div></div>
    ${pats.length ? `<div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:10px">${pats.map((p) => `<div class="sec">
      <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap"><b>${esc(p.label)}</b> <span class="badge ${BSTAT[p.status][0]}">${BSTAT[p.status][1]}</span></div>
      <div class="sub" style="margin:4px 0">${esc(p.summary)} · from ${p.n} observation${p.n > 1 ? "s" : ""}</div>
      ${p.status === "support" ? `<div style="font-size:12.5px"><b>How to help:</b> ${esc(p.support)}</div>` : ""}
      ${p.status === "support" || p.status === "strength" ? `<button class="btn sm" style="margin-top:8px" data-confirm="${esc(p.construct)}" data-v="${p.teacher_confirmed ? "0" : "1"}">${p.teacher_confirmed ? "✓ Teacher confirmed · undo" : "Teacher: confirm this pattern"}</button>` : ""}
    </div>`).join("")}</div>` : `<div class="empty">No learning-behaviour data yet. Run a preset scenario (Isla and Jay have it), or record an answer with "How did they work?".</div>`}
    ${b.indicators.length ? `<details style="margin-top:10px"><summary>What was observed, question by question (${b.indicators.length})</summary><table class="t" style="margin-top:6px">${b.indicators.map((i) => `<tr><td class="${i.polarity > 0 ? "ok" : "bad"}">${i.polarity > 0 ? "+" : "−"}</td><td>${esc(i.label)}<div class="sub">${esc(i.detail)}</div></td><td class="sub">${esc(i.construct)}</td><td class="sub">${esc(i.topic || "")}</td></tr>`).join("")}</table></details>` : ""}
    <div class="note" style="margin-top:10px"><b>What is kept, and who sees it.</b> Emotional signals such as frustration: this session only, never stored. Patterns: recent, and fade if not seen again. Shared beyond the tutoring tool: only patterns a teacher has confirmed (${b.shared.length} for this pupil).</div>`;
  el.querySelectorAll("[data-confirm]").forEach((btn) => (btn.onclick = () => busy(btn, async () => {
    renderBehaviour(await api(`/api/learners/${S.pupil}/behaviour/${btn.dataset.confirm}/confirm`, { method: "POST", body: { confirmed: btn.dataset.v === "1" } }));
  })));
}
async function renderBehaviourFramework() {
  const f = await api("/api/behaviour/framework");
  const byC = {}; f.indicators.forEach((i) => (byC[i.construct] ??= []).push(i));
  $("#g-behaviour").innerHTML = `<h3>Learning-behaviour framework</h3><p class="sub">A second, small framework alongside the map: how pupils learn, not what they know. Every observation is still recorded against a topic on the map, because behaviour only means something relative to what the pupil already knows.</p>
    <div class="tbl-wrap"><table class="t">${f.constructs.map((c) => `<tr><td style="width:30%"><b>${esc(c.label)}</b><div class="sub">${esc(c.description)}</div></td><td>${(byC[c.id] || []).map((i) => `<div class="sub"><span class="${i.polarity > 0 ? "ok" : "bad"}">${i.polarity > 0 ? "+" : "−"}</span> ${esc(i.label)}</div>`).join("")}</td><td class="sub" style="width:34%"><i>${esc(c.support)}</i></td></tr>`).join("")}</table></div>`;
}
function processFor(item, response) {
  const v = $("#e-proc").value, conf = $("#e-conf").value, ok = response === item.answer;
  const P = [];
  const att = (t, ans, c) => P.push({ type: "attempt", t, answer: ans, correct: c });
  if (v === "tried" || v === "" && conf) att(30, response, ok);
  if (v === "hint_first") { P.push({ type: "hint_requested", t: 3 }); att(40, response, ok); }
  if (v === "gave_up") { att(40, response, ok); P.push({ type: "abandoned", t: 50 }); }
  if (v === "repeated") { att(30, response, ok); att(70, response, ok); }
  if (v === "self_corrected") { P.push({ type: "answer_revised", t: 35 }); att(40, response, ok); }
  if (v === "checked") { att(30, response, ok); P.push({ type: "checked", t: 45 }); }
  if (v === "planned") { P.push({ type: "plan_stated", t: 5 }); att(35, response, ok); }
  if (v === "frustrated") { att(40, response, ok); P.push({ type: "affect", t: 45, signal: "frustration" }); }
  if (conf) P.push({ type: "confidence", t: 60, rating: +conf });
  return P.length ? P : null;
}
function fillResp() {
  const i = S.items.find((x) => x.id === $("#e-item").value);
  $("#e-resp").innerHTML = `<option value="${esc(i.answer)}">${esc(i.answer)}  (correct)</option>` + (i.distractors || []).map((d) => `<option value="${esc(d.response)}">${esc(d.response)}  (${d.misconception ? "reveals: " + esc(lab(d.misconception)) : "wrong, no known misconception"})</option>`).join("");
}
function explainRecord(r, prompt) {
  const g = S.graph; const pre = {};
  g.prerequisites.forEach((e) => (pre[e.dst] ??= []).push(e.src));
  const parts = [];
  if (r.concept_provenance === "teacher") parts.push(`The teacher said the work covered ${r.concepts.map((c) => `<b>${esc(c.label)}</b>`).join(", ")}${r.misconception ? `, and recorded the misconception <b>${esc(r.misconception.label)}</b>` : ""}`);
  else if (r.concept_provenance === "source") parts.push(`The item arrived tagged with ${r.concepts.map((c) => `<b>${esc(c.label)}</b>`).join(", ")}`);
  else parts.push(`No topics were supplied. The tagger (${esc(r.concept_provenance)}) interpreted “${esc(prompt)}” as ${r.concepts.map((c) => `<b>${esc(c.label)}</b>`).join(", ") || "<b>nothing recognisable</b>"} with confidence ${r.confidence}. The evidence is weighted by that confidence`);
  if (r.outcome >= 0.5) {
    const ps = [...new Set(r.concepts.flatMap((c) => pre[c.id] || []))];
    parts.push(`Outcome ${r.concept_provenance === "source" ? "correct" : pct(r.outcome)}. Because the graph knows the prerequisites, it added weak evidence to ${ps.map((p) => `<b>${esc(lab(p))}</b>`).join(", ") || "none"}`);
  } else if (r.misconception && r.concept_provenance === "teacher") {
    parts.push(`Mark ${pct(r.outcome)}. Only the topics the misconception affects were marked down (graph-based credit assignment)`);
  } else if (r.misconception) {
    parts.push(`Wrong answer matched a known distractor, so it was diagnosed as <b>${esc(r.misconception.label)}</b>. Only concepts that misconception affects were marked down (graph-based credit assignment)`);
  } else parts.push(`Outcome ${pct(r.outcome)}`);
  parts.push("The pupil's progress was rebuilt from all their answers");
  const bh = r.behaviour || {};
  if (bh.indicators && bh.indicators.length) parts.push(`How they worked: ${bh.indicators.map((i) => `<b>${esc(i.label.toLowerCase())}</b> (${esc(i.construct)})`).join("; ")}`);
  else if (bh.stored) parts.push("How they worked was recorded, but nothing in it counts for or against a learning behaviour (for example, an early hint on a brand-new topic is reasonable)");
  if (bh.discarded_session_only) parts.push(`${bh.discarded_session_only} emotional signal discarded: session only, never stored`);
  return `<b>What the map did with this answer</b><ul>${parts.map((p) => `<li>${p}</li>`).join("")}</ul>`;
}

/* ------------------------------------------------------------------ context & tutor */
const SUGGEST = {
  "pupil:amara": ["I got 5x + 3 = 2x + 12 wrong again, I got x = 5. Can you help?", "How do I solve 7x − 2 = 3x + 10?", "What should I practise next?"],
  "pupil:ben": ["Can you help me with 3(x + 2) = 21?", "I don't get how to expand −2(x − 3).", "What should I practise next?"],
  "pupil:chloe": ["What's an inverse operation?", "Can you help me solve x + 9 = 14?", "What should I practise next?"],
  "pupil:dev": ["I keep getting 5x + 3 = 2x + 12 wrong. Can you help?", "How do I solve 7x − 2 = 3x + 10?", "Can you give me a question to practise?"],
  "pupil:isla": ["Can you help me with 6x + 1 = 2x + 9?", "Give me a question to practise.", "I got it wrong again."],
  "pupil:jay": ["Can you help me with 6x + 1 = 2x + 9?", "Give me a question to practise.", "I got it wrong again."],
  "pupil:gabriel": ["Can you check my working for 3x − 4 = 17? I divided by 3 first.", "What should I work on?", "Can we skip to the harder ones?"],
  "pupil:hana": ["I don't get why there are x's on both sides.", "Give me a quick quiz.", "Can you help with my homework?"],
  "pupil:priya": ["Can you look at my paragraph on 'Out, damned spot'? I said it shows she feels guilty.", "How can I get a higher grade?", "What does 'vaulting ambition' mean?"],
  "pupil:tom": ["Why does Macbeth kill Duncan?", "What should I say about the witches?", "What happens to Lady Macbeth at the end?"],
  "pupil:leah": ["Can you check my quotation for the blood imagery paragraph?", "How do I get my essay to a grade 7?", "What does 'full of scorpions is my mind' show?"],
  "pupil:owen": ["Why do people in the play care so much about the king?", "Is Macbeth a good tragic hero?", "Can you check my PETAL paragraph on Lady Macbeth?"],
  "pupil:zainab": ["Is Lady Macbeth a villain?", "Can you help me plan an essay on ambition?", "What should I work on?"],
  "pupil:farah": ["I've finished all my homework. What's next?", "Is x = 4 right for 4x + 5 = x − 7?", "Can we do something harder?"],
};
function renderContextPage() {
  const ps = $("#c-pupil");
  if (!ps.options.length) {
    ps.innerHTML = S.pupils.map((p) => `<option value="${p.id}">${esc(p.name)}</option>`).join("");
    ps.onchange = () => { S.pupil = ps.value; $("#c-msg").value = SUGGEST[S.pupil][0]; sugg(); };
  }
  ps.value = S.pupil;
  if (!$("#c-msg").value) $("#c-msg").value = SUGGEST[S.pupil][0];
  if (!SUGGEST[S.pupil]) SUGGEST[S.pupil] = ["What should I work on?"];
  if (!$("#c-msg").value) $("#c-msg").value = SUGGEST[S.pupil][0];
  $("#c-concept").innerHTML = `<option value="">Auto: align from the message</option>` + S.graph.concepts.filter((c) => c.status === "active").map((c) => `<option value="${c.id}">${esc(c.label)}</option>`).join("");
  sugg();
  const c = S.status.counts;
  const warns = [];
  if (!c.content_unit || !c.evidence) warns.push(`<div class="note warn">This works best once materials are tagged and the pupils have some answers recorded. <button class="btn sm" id="btn-c-prep">Prepare demo data</button></div>`);
  if (!S.status.llm_available) warns.push(`<div class="note" style="margin-top:8px">No <code>ANTHROPIC_API_KEY</code> set, so both briefings are shown without live tutor replies. Add the key in Railway to generate a tutor reply from each.</div>`);
  $("#c-warn").innerHTML = warns.join("");
  const pb = $("#btn-c-prep"); if (pb) pb.onclick = () => busy(pb, async () => { await api("/api/demo/prepare", { method: "POST" }); await refreshStatus(); renderContextPage(); toast("Demo data ready"); });
}
function sugg() { $("#c-suggest").innerHTML = SUGGEST[S.pupil].map((m) => `<button>${esc(m)}</button>`).join(""); $("#c-suggest").querySelectorAll("button").forEach((b) => (b.onclick = () => { $("#c-msg").value = b.textContent; })); }
function renderCompare(r) {
  const a = r.without_h2.context, b = r.with_h2.context;
  const reply = (x) => x.response ? `<div class="sec"><div class="st">Tutor reply (${esc(r.model)})</div><div class="reply">${esc(x.response)}</div></div>` : x.error ? `<div class="note warn">${esc(x.error)}</div>` : r.llm ? "" : `<div class="sec"><div class="st">Tutor reply</div><div class="sub">Needs ANTHROPIC_API_KEY</div></div>`;
  const left = `<div class="cmp-col">
    <div class="cmp-title"><span class="pill no">RAW CLASSROOM DATA</span><span class="sub">~${a.approx_tokens} tokens of context</span></div>
    ${reply(r.without_h2)}
    <div class="sec"><div class="st">Recent activity (raw log)</div>${a.recent_activity.length ? `<ul>${a.recent_activity.map((l) => `<li>${esc(l)}</li>`).join("")}</ul>` : '<div class="sub">No activity</div>'}</div>
    <div class="sec"><div class="st">Materials by text similarity</div>${a.materials.length ? `<ul>${a.materials.map((m) => `<li><b>${esc(m.heading)}</b> <span class="sub">W${m.week} · ${esc(m.why)}</span></li>`).join("")}</ul>` : '<div class="sub">None (ingest materials first)</div>'}</div>
    <div class="note">${esc(a.note)}</div>
    <details><summary>Raw context JSON</summary><pre class="json">${esc(JSON.stringify(a, null, 2))}</pre></details></div>`;
  let right;
  if (b.error) right = `<div class="cmp-col"><div class="cmp-title"><span class="pill yes">ORGANISED BY THE MAP</span></div><div class="note warn">${esc(b.error)}</div></div>`;
  else {
    const L = b.learner, C = b.class, G = b.guidance;
    right = `<div class="cmp-col">
    <div class="cmp-title"><span class="pill yes">ORGANISED BY THE MAP</span><span class="sub">~${b.approx_tokens} tokens · map v${esc(b.graph_version)}</span></div>
    ${reply(r.with_h2)}
    <div class="sec"><div class="st">Focus topic</div>${tag("concept", b.focus.id, null, { label: b.focus.label })}
      <div class="sub" style="margin-top:4px">Request read as <b>${esc((b.request.intent || "other").replace("_", " "))}</b>${b.request.message_topic ? ` about ${esc(b.request.message_topic)}` : ""}${b.request.mentions && b.request.mentions.parts_of_text.length ? `, mentioning ${esc(b.request.mentions.parts_of_text.join(", "))}` : ""}. Focus chosen because: ${esc(b.focus.chosen_because.how)}</div>
      <div class="tags" style="margin-top:6px">${b.focus.crosswalk.map((x) => `<span class="tag plain">${esc(x.scheme)}: ${esc(x.id)}</span>`).join("")}</div></div>
    <div class="sec"><div class="st">Pupil progress</div>${badge(L.status)} <span class="sub">mastery ${pct(L.mastery)} · learning edge: ${esc(L.learning_edge)}</span>
      ${L.active_misconceptions.length ? `<div style="margin-top:8px">${L.active_misconceptions.map((m) => `${tag("misconception", m.id)}<div class="sub" style="margin:3px 0 0">${esc(m.description)}</div>`).join("")}</div>` : '<div class="sub" style="margin-top:6px">No active misconception on this concept</div>'}
      <div class="sub" style="margin:8px 0 4px">Overview</div><div class="tags">${L.overview.map((o) => `<span class="tag plain">${esc(o.topic)} ${badge(o.status)}</span>`).join("")}</div></div>
    <div class="sec"><div class="st">Class ${esc(C.id)}</div>${C.focus_taught ? `Focus taught in week ${C.focus_week}` : `<b>Focus not yet taught to this class</b>${C.focus_week ? ` (planned week ${C.focus_week})` : ""}`} · ${esc(C.teacher)}
      <div class="tags" style="margin-top:6px">${C.preferred_method ? tag("method", C.preferred_method.id) : ""}${C.preferred_representation ? tag("representation", C.preferred_representation.id) : ""}</div>
      <div class="sub" style="margin-top:6px">Taught: ${esc(C.taught_so_far.join(" · "))}</div><div class="sub">Not yet: ${esc(C.not_yet_taught.join(" · ") || "none")}</div>
      ${C.text_studied_so_far ? `<div class="sub" style="margin-top:6px"><b>Play studied:</b> ${esc(C.text_studied_so_far.join(", ") || "none")}${C.text_coming_up.length ? ` · <b>coming up:</b> ${esc(C.text_coming_up.join(", "))}` : ""}</div>` : ""}</div>
    ${b.key_quotations && b.key_quotations.length ? `<div class="sec"><div class="st">Key quotations the class has studied</div>${b.key_quotations.map((q) => `<p class="quote">“${esc(q.text)}” <span class="sub" style="font-style:normal">${esc(q.speaker)}, ${esc(q.act)}</span></p>`).join("")}</div>` : ""}
    <div class="sec"><div class="st">Teacher's materials (filtered by concept, ranked by method + misconception)</div>${b.materials.length ? `<ul>${b.materials.map((m) => `<li><b>${esc(m.heading)}</b> <span class="sub">W${m.week}: ${esc(m.why)}</span></li>`).join("")}</ul>` : '<div class="sub">No aligned materials for this concept in taught weeks</div>'}</div>
    ${b.how_to_support && (b.how_to_support.patterns_to_support.length || b.how_to_support.strengths.length) ? `<div class="sec"><div class="st">How to support (learning behaviour, ${esc(b.how_to_support.based_on)})</div><ul>${b.how_to_support.patterns_to_support.map((p) => `<li><b>${esc(p.area)}:</b> ${esc(p.what_we_saw)}. <i>${esc(p.how_to_help)}</i></li>`).join("")}${b.how_to_support.strengths.map((p) => `<li><b>Strength, ${esc(p.area.toLowerCase())}:</b> ${esc(p.what_we_saw)}</li>`).join("")}</ul><div class="sub" style="margin-top:4px">${esc(b.how_to_support.rule)}</div></div>` : ""}
    <div class="sec guidance"><div class="st">Guidance for the tutor</div><ul>
      <li>${esc(G.diagnosis)}</li>
      ${G.teach_with.method ? `<li>Teach with <b>${esc(G.teach_with.method.label)}</b>${G.teach_with.representation ? ` using a <b>${esc(G.teach_with.representation.label)}</b>` : ""}</li>` : ""}
      ${G.avoid_methods.length ? `<li>Avoid: ${G.avoid_methods.map((m) => esc(m.label)).join(", ")}</li>` : ""}
      ${G.check_prerequisites_first.length ? `<li>Check first: ${G.check_prerequisites_first.map((p) => `${esc(p.label)} (${esc(p.status)})`).join(", ")}</li>` : ""}
      ${G.next_step ? `<li>Next step: <b>${esc(G.next_step.action.replaceAll("_", " "))}</b>, ${esc((G.next_step.concept || G.next_step.link_back_to || {}).label || G.next_step.part_of_text || "")}${G.next_step.part_of_text ? ` (${esc(G.next_step.part_of_text)})` : ""}: ${esc(G.next_step.why)}${(G.next_step.check_first || []).length ? `. Check first: ${G.next_step.check_first.map((x) => esc(x.label)).join(", ")}` : ""}</li>` : ""}
      <li>${esc(G.scope_rule)}</li></ul></div>
    <details><summary>Raw context JSON</summary><pre class="json">${esc(JSON.stringify(b, null, 2))}</pre></details></div>`;
  }
  $("#c-out").innerHTML = left + right;
}

/* ------------------------------------------------------------------ evaluate */
S.evMode = "claude"; S.evSet = "heldout2"; S.suite = "heldout"; S.evTutor = "builtin"; S.tutors = [];
function renderEvaluate() {
  const llm = S.status.llm_available;
  if (!llm && S.evMode === "claude") S.evMode = "heuristic";
  const seg = (el, opts, key, after) => {
    el.innerHTML = opts.map(([v, l, dis]) => `<button data-v="${v}" class="${S[key] === v ? "on" : ""}" ${dis ? 'disabled title="Set ANTHROPIC_API_KEY to enable"' : ""}>${l}</button>`).join("");
    el.querySelectorAll("button").forEach((b) => (b.onclick = () => { S[key] = b.dataset.v; renderEvaluate(); }));
  };
  seg($("#ev-set"), S.subject === "english" ? [["english", "English: Macbeth (12)"]] : [["heldout2", "Unseen 2 (12)"], ["heldout", "Unseen 1 (14)"], ["tuning", "Practice (14)"]], "evSet");
  seg($("#ev-suite"), S.subject === "english" ? [["english_heldout", "Unseen (10)"], ["english", "First set (10)"]] : [["heldout", "Unseen"], ["dev", "Development"], ["behaviour", "Learning behaviour"]], "suite");
  $("#ev-suite").querySelectorAll("button").forEach((btn) => btn.addEventListener("click", () => { S.suiteLoaded = false; renderEvaluate(); }));
  seg($("#ev-mode"), [["heuristic", "Keyword tagger"], ["claude", "Claude tagger", !llm]], "evMode");
  $("#ev-align-sub").textContent = { heldout2: "Unseen set 2: written and locked before the latest tagger change, like an exam paper the candidate hasn't seen. The honest figure.", heldout: "Unseen set 1: its misses were used to diagnose the latest tagger change, so it is no longer truly unseen.", tuning: "Practice set: the 14 units the tagger was adjusted against. Expect flattering numbers.", english: "English (Macbeth), unseen: 12 units written before any English tagging was run, including two that are not about Macbeth and should get no topics. Never used to adjust anything." }[S.evSet];
  $("#btn-ev-tutor").disabled = !llm;
  const ak = S.evSet + ":" + S.evMode;
  if (S.alignLoaded !== ak) {
    S.alignLoaded = ak;
    api(`/api/eval/alignment/latest?set=${S.evSet}&mode=${S.evMode}`).then((d) => { if (d && d.summary) renderAlignEval(d); else $("#ev-align").innerHTML = `<div class="empty">No run yet for this set. Click Run.</div>`; });
  }
  renderTutorPicker();
  if (!S.suiteLoaded) {
    S.suiteLoaded = true;
    api(`/api/eval/tutor/latest?suite=${S.suite}&tutor=${encodeURIComponent(S.evTutor)}`).then((d) => { if (d && d.summary) renderTutorEval(d); else if (!llm) $("#ev-tutor").innerHTML = `<div class="note">Needs <code>ANTHROPIC_API_KEY</code>.</div>`; else $("#ev-tutor").innerHTML = `<div class="empty">No run yet for this set and tutor. Click Run${S.tutors.find((t) => t.id === S.evTutor)?.kind === "offline" ? ", or upload a replies file" : ""}.</div>`; });
    renderTutorCompare(); renderTutorList();
  }
}
async function renderTutorPicker() {
  if (!S.tutors.length) S.tutors = await api("/api/tutors");
  const sel = $("#ev-tutor-pick");
  sel.innerHTML = S.tutors.map((t) => `<option value="${esc(t.id)}">${esc(t.label)}</option>`).join("");
  sel.value = S.evTutor;
  sel.onchange = () => { S.evTutor = sel.value; S.suiteLoaded = false; renderEvaluate(); };
  const t = S.tutors.find((x) => x.id === S.evTutor) || S.tutors[0];
  const off = $("#ev-tutor-offline");
  if (t && t.kind === "offline") {
    off.innerHTML = `<div class="note" style="margin-bottom:10px"><b>Offline tutor.</b> 1. <a href="/api/eval/tutor/export?suite=${esc(S.suite)}" target="_blank">Download the turns for this set</a> (every scenario × 3 arms, with the context each arm gets). 2. Run your tutor on them. 3. Upload the replies: <input type="file" id="ev-replies" accept="application/json" style="font-size:12px"> <button class="btn sm" id="btn-ev-upload">Upload and score</button></div>`;
    $("#btn-ev-tutor").disabled = true; $("#btn-ev-tutor").title = "Offline tutors are scored from an uploaded replies file";
    $("#btn-ev-upload").onclick = async (e) => {
      const f = $("#ev-replies").files[0]; if (!f) { toast("Choose the replies file first"); return; }
      let body; try { body = JSON.parse(await f.text()); } catch { toast("That file is not valid JSON"); return; }
      await runTutorSuite(e.currentTarget, body);
    };
  } else { off.innerHTML = ""; $("#btn-ev-tutor").disabled = !S.status.llm_available; $("#btn-ev-tutor").title = ""; }
}
async function renderTutorCompare() {
  const d = await api(`/api/eval/tutor/compare?suite=${S.suite}`);
  const arms = ["none", "raw", "h2"];
  $("#ev-compare").innerHTML = d.tutors.length ? `<div class="tbl-wrap"><table class="t"><tr><th>Tutor</th><th>Last run</th>${arms.map((a) => `<th>${ARM_SHORT[a]}</th>`).join("")}<th>Map − raw</th><th>Map vs raw (W–T–L)</th></tr>${d.tutors.map((t) => { const dlt = +(t.by_arm.h2 - t.by_arm.raw).toFixed(2); const h = t.head_to_head; return `<tr><td><b>${esc(t.label)}</b><div class="sub">${esc(t.kind)}</div></td><td class="sub">${esc(t.finished || "")}</td>${arms.map((a) => `<td style="color:${ARM_COL[a]}"><b>${t.by_arm[a]}</b><span class="sub"> / ${t.max_total}</span></td>`).join("")}<td class="${dlt > 0.25 ? "ok" : dlt < -0.25 ? "bad" : ""}"><b>${dlt > 0 ? "+" : ""}${dlt}</b></td><td>${h.win}–${h.tie}–${h.loss}</td></tr>`; }).join("")}</table></div><div class="sub" style="margin-top:6px">Same scenarios, ground truth and judge for every row (${esc(d.suite_label || d.suite)}). Compare each tutor with and without the map before comparing tutors with each other: the ground truth encodes one pedagogy, and a tutor with a different one can score lower for reasons unrelated to the map.</div>` : `<div class="empty">No results yet on this set. Run the built-in tutor, or register and run another.</div>`;
}
async function renderTutorList() {
  S.tutors = await api("/api/tutors");
  $("#tutor-list").innerHTML = S.tutors.map((t) => `<div class="sec" style="margin-bottom:8px"><div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap"><b>${esc(t.label)}</b><span class="badge">${esc(t.kind)}</span>${t.has_secret ? '<span class="badge secure">secret set</span>' : ""}</div>${t.url ? `<div class="mono sub" style="margin-top:3px;word-break:break-all">${esc(t.url)}</div>` : ""}${t.notes ? `<div class="sub" style="margin-top:3px">${esc(t.notes)}</div>` : ""}${t.builtin ? "" : `<div style="margin-top:6px;display:flex;gap:6px">${t.kind === "webhook" ? `<button class="btn sm" data-ping="${esc(t.id)}">Test connection</button>` : ""}<button class="btn sm" data-rm="${esc(t.id)}">Remove</button></div>`}</div>`).join("");
  $("#tutor-list").querySelectorAll("[data-ping]").forEach((b) => (b.onclick = () => busy(b, async () => { const r = await api(`/api/tutors/${b.dataset.ping}/ping`, { method: "POST" }); toast(r.ok ? "Connected. Reply: " + (r.reply || "").slice(0, 80) : "Failed: " + r.error); })));
  $("#tutor-list").querySelectorAll("[data-rm]").forEach((b) => (b.onclick = () => busy(b, async () => { await api(`/api/tutors/${b.dataset.rm}`, { method: "DELETE" }); if (S.evTutor === b.dataset.rm) S.evTutor = "builtin"; S.tutors = []; S.suiteLoaded = false; renderEvaluate(); })));
  const kind = $("#tr-kind"); const sync = () => { const w = kind.value === "webhook"; $("#tr-url-f").hidden = !w; $("#tr-secret-f").hidden = !w; }; kind.onchange = sync; sync();
}
function renderAlignEval(d) {
  const s = d.summary;
  const k = (v, l) => `<div class="kpi"><div class="v">${v == null ? "–" : Math.round(v * 100) + "%"}</div><div class="l">${l}</div></div>`;
  const f = (x) => (x == null ? "—" : Array.isArray(x) ? (x.length ? x.map(lab).join(", ") : "none") : lab(x));
  const cell = (o, multi) => {
    let ok;
    if (multi && o.acceptable) { const good = new Set([...o.expected, ...o.acceptable]); ok = o.got.every((g) => good.has(g)) && o.expected.every((e) => o.got.includes(e)); }
    else ok = multi ? JSON.stringify([...o.expected].sort()) === JSON.stringify([...o.got].sort()) : o.expected === o.got;
    return `<td><span class="${ok ? "ok" : "bad"}">${ok ? "✓" : "✗"}</span> ${ok ? `<span class="sub">${esc(f(o.got))}</span>` : `<div class="sub">expected: ${esc(f(o.expected))}${o.acceptable && o.acceptable.length ? ` (also OK: ${esc(f(o.acceptable))})` : ""}</div><div class="sub">got: ${esc(f(o.got))}</div>`}</td>`;
  };
  $("#ev-align").innerHTML = `<div class="sub" style="margin-bottom:8px"><b>${({ heldout: "Unseen set 1", heldout2: "Unseen set 2", tuning: "Practice set", english: "English (Macbeth) unseen set" })[d.set] || d.set}</b> · ${d.mode === "claude" ? "Claude tagger" : "keyword tagger"} · ${d.units} units${s.offstrand_correctly_untagged ? ` · off-topic units correctly left untagged: <b>${s.offstrand_correctly_untagged}</b>` : ""}</div>
    <div class="kpis">${k(s.concepts.precision, "Concept tags correct")}${k(s.concepts.recall, "Expected concepts found")}${k(s.misconceptions.precision, "Misconception tags correct")}${k(s.misconceptions.recall, "Misconceptions found")}${k(s.method_accuracy, "Method right")}${k(s.representation_accuracy, "Representation right")}</div>
    <details style="margin-top:12px"><summary>Per-unit results</summary><div class="tbl-wrap"><table class="t" style="margin-top:8px"><tr><th>Unit</th><th>Concepts</th><th>Method</th><th>Representation</th><th>Misconceptions</th></tr>
    ${d.rows.map((r) => `<tr><td><b>${esc(r.heading)}</b><div class="mono sub">${esc(r.unit)}</div>${r.rationale ? `<div class="sub" style="margin-top:4px"><i>${esc(r.rationale)}</i></div>` : ""}</td>${cell(r.concepts, true)}${cell(r.method)}${cell(r.representation)}${cell(r.misconceptions, true)}</tr>`).join("")}</table></div></details>`;
}
const ARM_COL = { none: "var(--text-3)", raw: "var(--developing)", h2: "var(--accent)" };
const ARM_SHORT = { none: "No context", raw: "Raw data", h2: "Map" };
const CAT = { specific: "Asks about something specific", gives_up: "Isla: asks before trying, stops after errors", repeats_method: "Jay: keeps going, repeats the same method", method_conflict: "Teacher uses a different method (10Y / 11F)", non_revealing: "Request doesn't reveal the problem", jump_ahead: "Temptation to jump ahead", control: "Controls" };
const CRIT = { support: "Responds to how the pupil learns", diagnosis: "Diagnosis", method: "Teacher's method", scope: "Respects what's been taught", next_step: "Next step", grounding: "True to pupil & class" };
function renderTutorEval(d) {
  if (d.error) { $("#ev-tutor").innerHTML = `<div class="note warn">${esc(d.error)}</div>`; return; }
  const sm = d.summary, arms = ["none", "raw", "h2"], max = sm.max_total;
  const tiles = arms.map((a) => `<div class="kpi"><div class="v" style="color:${ARM_COL[a]}">${sm.by_arm[a].total}<span class="sub" style="font-size:13px"> / ${max}</span></div><div class="l">${esc(d.arms[a])}</div></div>`).join("");
  const crit = `<table class="t"><tr><th>Criterion (0–3)</th>${arms.map((a) => `<th>${ARM_SHORT[a]}</th>`).join("")}</tr>${d.criteria.map((c) => `<tr><td>${CRIT[c]}</td>${arms.map((a) => `<td style="width:22%">${bar(sm.by_arm[a][c] / 3, ARM_COL[a])}<span class="sub">${sm.by_arm[a][c]}</span></td>`).join("")}</tr>`).join("")}</table>`;
  const bf = d.before_fix;
  const cats = `<table class="t"><tr><th>Scenario group</th><th>n</th>${arms.map((a) => `<th>${ARM_SHORT[a]}</th>`).join("")}${bf ? "<th>Map before fix</th>" : ""}<th>Map − raw</th></tr>${Object.entries(sm.by_category).map(([c, v]) => { const dlt = +(v.h2 - v.raw).toFixed(2); return `<tr><td>${CAT[c] || c}</td><td>${v.n}</td>${arms.map((a) => `<td>${v[a]}</td>`).join("")}${bf ? `<td class="sub">${bf.by_category[c] ? bf.by_category[c].h2 : "–"}</td>` : ""}<td class="${dlt > 0.25 ? "ok" : dlt < -0.25 ? "bad" : ""}"><b>${dlt > 0 ? "+" : ""}${dlt}</b></td></tr>`; }).join("")}</table>`;
  const h = sm.head_to_head.h2_vs_raw;
  const rows = d.results.map((r) => {
    if (arms.some((a) => !r.arms[a] || r.arms[a].error)) return `<div class="note warn">${esc(r.id)}: ${esc(arms.map((a) => r.arms[a]?.error).filter(Boolean).join("; "))}</div>`;
    return `<details class="sec" style="margin-top:8px"><summary><b>${esc(r.pupil)}</b> (${esc(r.class)}): “${esc(r.message)}” — ${arms.map((a) => `<span style="color:${ARM_COL[a]}">${ARM_SHORT[a]} ${r.arms[a].total}</span>`).join(" · ")}</summary>
      <div class="sub" style="margin:8px 0"><b>Actual need (hand-written):</b> ${esc(r.need)}</div>
      <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(240px,1fr))">${arms.map((a) => `<div><div class="sub"><b style="color:${ARM_COL[a]}">${esc(d.arms[a])}</b> · ${r.arms[a].total}/15 · ${Object.entries(r.arms[a].scores).map(([k, v]) => `${CRIT[k].split(" ")[0]} ${v}`).join(", ")}</div><div class="reply" style="margin-top:6px;font-size:12.5px">${esc(r.arms[a].reply)}</div><div class="sub" style="margin-top:6px"><i>Judge: ${esc(r.arms[a].why[0])}</i></div></div>`).join("")}</div></details>`;
  }).join("");
  $("#ev-tutor").innerHTML = `<div class="sub" style="margin-bottom:8px"><b>${esc(d.suite_label || "Development scenarios")}</b>${d.tutor && !d.tutor.builtin ? ` · tutor: <b>${esc(d.tutor.label)}</b> (${esc(d.tutor.kind)})` : ""} · last run ${esc(d.finished)} · ${esc(d.model)} · ${d.seconds}s · ${sm.scored_scenarios} scenarios${bf ? ` · the map on the same scenarios before the fix: <b>${bf.by_arm.h2.total}</b> / ${max}` : ""}</div>
    <div class="note" style="margin-bottom:10px">Scores vary by about ±1 point between identical runs, so differences smaller than that are noise.</div>
    <div class="kpis" style="margin-bottom:12px">${tiles}<div class="kpi"><div class="v">${h.win}–${h.tie}–${h.loss}</div><div class="l">Map vs raw data: wins–ties–losses</div></div></div>
    <div class="grid g2"><div>${crit}</div><div>${cats}</div></div>
    <h4 style="margin:16px 0 4px;font-size:12px;color:var(--text-3);text-transform:uppercase;letter-spacing:.05em">Every scenario, with all three replies</h4>${rows}`;
}
async function runTutorSuite(btn, replies) {
  btn.disabled = true;
  const prog = $("#ev-tutor-progress");
  try {
    let j = replies ? await api(`/api/eval/tutor/import?suite=${S.suite}&tutor=${encodeURIComponent(S.evTutor)}`, { method: "POST", body: replies })
                    : await api(`/api/eval/tutor?suite=${S.suite}&tutor=${encodeURIComponent(S.evTutor)}`, { method: "POST" });
    while (j.status === "running") {
      prog.innerHTML = `<div class="note accent" style="margin-bottom:10px"><span class="spin" style="display:inline-block;width:10px;height:10px;border:2px solid currentColor;border-right-color:transparent;border-radius:50%;animation:spin .7s linear infinite;vertical-align:-1px"></span> Running… ${j.done}/${j.total || 12} scenarios (3 replies and 6 judgements each)</div>`;
      await new Promise((r) => setTimeout(r, 4000));
      j = await api(`/api/eval/tutor/${j.id}`);
    }
    prog.innerHTML = "";
    if (j.status === "error") throw new Error(j.error);
    S.suiteLoaded = false; renderEvaluate(); S.evalDone = true; renderSteps();
  } catch (e) { prog.innerHTML = `<div class="note warn">${esc(e.message)}</div>`; }
  finally { btn.disabled = false; }
}

/* ------------------------------------------------------------------ help */
function renderHelp() {
  document.querySelectorAll("[data-scroll]").forEach((a) => (a.onclick = (e) => {
    e.preventDefault();
    const t = document.getElementById(a.dataset.scroll);
    if (t) window.scrollTo({ top: t.getBoundingClientRect().top + window.scrollY - 72, behavior: "smooth" });
  }));
}

/* ------------------------------------------------------------------ authoring */
function renderAuthoring() {
  renderHelp();
  const out = $("#val-out"), ta = $("#val-text");
  const load = (name) => api(`/api/authoring/${name}`).then((t) => { ta.value = typeof t === "string" ? t : JSON.stringify(t); out.innerHTML = ""; });
  $("#btn-val-example").onclick = () => load("example");
  $("#btn-val-template").onclick = () => load("template");
  $("#val-file").onchange = async () => { const f = $("#val-file").files[0]; if (f) { ta.value = await f.text(); out.innerHTML = ""; } };
  $("#btn-validate").onclick = (e) => busy(e.currentTarget, async () => {
    if (!ta.value.trim()) { toast("Paste or load a map first"); return; }
    const r = await api("/api/validate", { method: "POST", body: { yaml_text: ta.value } });
    const s = r.summary || {};
    const row = (x, cls) => `<tr><td class="${cls}">${cls === "bad" ? "error" : "warning"}</td><td class="mono" style="font-size:11.5px">${esc(x.code)}</td><td class="mono" style="font-size:11.5px;word-break:break-all">${esc(x.where)}</td><td>${esc(x.message)}</td></tr>`;
    out.innerHTML = `<div class="note ${r.ok ? "" : "warn"}" style="margin-bottom:8px"><b>${r.ok ? "Well-formed." : "Refused."}</b> ${r.counts.errors} error${r.counts.errors === 1 ? "" : "s"}, ${r.counts.warnings} warning${r.counts.warnings === 1 ? "" : "s"}${s.subject ? ` · ${esc(s.subject)} ${esc(s.graph_version || "")} · ${esc(s.layout)} · ${s.concepts || 0} concepts, ${s.misconceptions || 0} misconceptions, ${s.methods || 0} methods, ${s.prerequisites || 0} prerequisites${s.related ? `, ${s.related} related links` : ""}${s.quotations ? `, ${s.quotations} quotations` : ""}` : ""}. ${r.ok ? "This means the file is structurally sound; whether the map is right is the reviewer's judgement." : "Fix the errors and check again."}</div>
      ${r.errors.length || r.warnings.length ? `<div class="tbl-wrap"><table class="t"><tr><th></th><th>Check</th><th>Where</th><th>Finding</th></tr>${r.errors.map((x) => row(x, "bad")).join("")}${r.warnings.map((x) => row(x, "")).join("")}</table></div>` : ""}`;
  });
}

/* ------------------------------------------------------------------ connect */
function renderConnect() {
  api("/ims/case/v1p1/CFDocuments").then((d) => {
    $("#case-docs").innerHTML = `<tr><th>Framework</th><th>Version</th><th>Identifier</th><th></th></tr>` + d.CFDocuments.map((x) => `<tr><td>${esc(x.title)}</td><td>${esc(x.version)}</td><td class="mono" style="font-size:11px">${esc(x.identifier)}</td><td><a href="${esc(x.CFPackageURI.uri)}" target="_blank">Open package</a></td></tr>`).join("");
  });
  const url = location.origin + "/mcp";
  $("#mcp-url").value = url;
  $("#btn-copy").onclick = () => { navigator.clipboard?.writeText(url); toast("Copied"); };
  $("#curl-ex").textContent = `curl -X POST ${location.origin}/api/context \\
  -H 'content-type: application/json' \\
  -d '{"learner":"pupil:amara",
       "message":"I got 5x + 3 = 2x + 12 wrong"}'`;
}

/* ------------------------------------------------------------------ static bindings */
function bindStatic() {
  $("#btn-help-top").onclick = () => show("help");
  document.querySelectorAll("[data-page-link]").forEach((a) => (a.onclick = (e) => { e.preventDefault(); show(a.dataset.pageLink); }));
  document.querySelectorAll("[data-go-help]").forEach((a) => (a.onclick = (e) => { e.preventDefault(); show("help"); }));
  $("#btn-prepare").onclick = (e) => busy(e.currentTarget, async () => { await api("/api/demo/prepare", { method: "POST" }); await refreshStatus(); toast("Materials tagged and scenarios recorded"); show("context"); });
  $("#btn-reset").onclick = (e) => busy(e.currentTarget, async () => { await api("/api/admin/reset", { method: "POST" }); await loadGraph(); S.sel = null; S.overlay = "none"; S.lastExplain = null; S.ctxDone = S.evalDone = false; await refreshStatus(); toast("Demo reset to graph 2026.2 with no alignments or evidence"); show("overview"); });
  $("#btn-release").onclick = (e) => busy(e.currentTarget, async () => {
    const r = await api("/api/admin/release", { method: "POST" });
    await loadGraph(); await refreshStatus();
    $("#release-out").className = "explain";
    $("#release-out").innerHTML = `<b>Released ${esc(r.from)} → ${esc(r.to)}</b><ul>
      <li><b>Curriculum map:</b> ${r.graph_changes.map(esc).join("; ")}</li>
      <li><b>Tags:</b> ${r.alignments_migrated} moved to the new topic and marked v${esc(r.to)}</li>
      <li><b>Pupil answers log:</b> ${r.evidence_rows_referencing_deprecated_ids} answers refer to retired topics; <b>${r.evidence_rows_modified} changed</b></li>
      <li><b>Pupil progress:</b> rebuilt for ${r.learners_reprojected} pupils. On the new topic: ${r.learner_state_new_concept.map((s) => `${esc(s.learner)} ${esc(s.status)} (${pct(s.mastery)})`).join(", ") || "no evidence yet"}</li></ul>
      <div class="sub" style="margin-top:6px">${esc(r.note)} Use Reset on the Overview to return to 2026.2.</div>`;
    S.sel = "cc:maths/alg/lin-eq-simple"; renderGraphPage();
  });
  $("#btn-ingest-all").onclick = (e) => busy(e.currentTarget, async () => { await api("/api/materials/ingest", { method: "POST", body: { mode: S.mode } }); await refreshStatus(); S.open.add("mat:10x-w4-both-sides"); await renderMaterials(); toast("All materials aligned"); });
  $("#btn-try").onclick = (e) => busy(e.currentTarget, async () => { const r = await api("/api/align", { method: "POST", body: { text: $("#try-text").value, mode: S.mode } }); $("#try-out").innerHTML = renderAlignResult(r); });
  $("#btn-add-mat").onclick = (e) => busy(e.currentTarget, async () => {
    const t = $("#try-text").value.trim(); if (!t) return;
    const cls = (await api("/api/classes")).find((c) => c.id === S.cls);
    const r = await api("/api/materials", { method: "POST", body: { class_id: S.cls, week: cls.current_week, title: "Added: " + t.slice(0, 48) + (t.length > 48 ? "…" : ""), body: "## Added unit\n" + t, mode: S.mode } });
    S.open.add(r.material); await refreshStatus(); await renderMaterials(); toast("Added and tagged");
  });
  $("#btn-e-item").onclick = (e) => busy(e.currentTarget, async () => {
    const i = S.items.find((x) => x.id === $("#e-item").value);
    const r = await api("/api/evidence", { method: "POST", body: { learner: S.pupil, source: $("#e-src").value, item: i.id, response: $("#e-resp").value, mode: S.mode, process: processFor(i, $("#e-resp").value) } });
    S.lastExplain = explainRecord(r.recorded, i.prompt); await refreshStatus(); await renderEvidence();
  });
  $("#btn-em").onclick = (e) => busy(e.currentTarget, async () => {
    const concepts = [...document.querySelectorAll("#em-topics input:checked")].map((x) => x.value);
    if (!concepts.length) throw new Error("Tick at least one topic");
    const act = $("#em-act").value;
    const r = await api("/api/evidence", { method: "POST", body: { learner: S.pupil, source: "Teacher marking", activity: act, score: $("#em-score").value / 100, concepts, misconception: $("#em-mc").value || null } });
    S.lastExplain = explainRecord(r.recorded, act); await refreshStatus(); await renderEvidence();
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
  $("#btn-tr-add").onclick = (e) => busy(e.currentTarget, async () => {
    const kind = $("#tr-kind").value;
    const t = await api("/api/tutors", { method: "POST", body: { label: $("#tr-label").value, kind, url: kind === "webhook" ? $("#tr-url").value : null, secret: $("#tr-secret").value || null, notes: $("#tr-notes").value || null } });
    $("#tr-label").value = ""; $("#tr-url").value = ""; $("#tr-secret").value = ""; $("#tr-notes").value = "";
    S.tutors = []; S.evTutor = t.id; S.suiteLoaded = false; toast("Registered " + t.label); renderEvaluate();
  });
  document.querySelectorAll("[data-then-scroll]").forEach((a) => (a.onclick = (e) => { e.preventDefault(); show(a.dataset.pageLink); setTimeout(() => { const t = document.getElementById(a.dataset.thenScroll); if (t) window.scrollTo({ top: t.getBoundingClientRect().top + window.scrollY - 72, behavior: "smooth" }); }, 50); }));
  $("#btn-ev-align").onclick = (e) => busy(e.currentTarget, async () => { const d = await api(`/api/eval/alignment?mode=${S.evMode}&set=${S.evSet}`, { method: "POST" }); renderAlignEval(d); S.evalDone = true; renderSteps(); });
  $("#btn-ev-tutor").onclick = (e) => runTutorSuite(e.currentTarget);
}

boot().catch((e) => { document.body.insertAdjacentHTML("beforeend", `<div class="toast show">Failed to load: ${esc(e.message)}</div>`); });
