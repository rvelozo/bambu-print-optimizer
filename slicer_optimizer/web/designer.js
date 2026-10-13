// The designer tab: pick settings to search, run them through the real slicer,
// and choose a point on the Pareto front with importance sliders and limits.
import { fmtTime, fmtDelta } from "./charts.js";

const NS = "http://www.w3.org/2000/svg";
const svgEl = (tag, attrs = {}, parent) => {
  const e = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  if (parent) parent.appendChild(e);
  return e;
};
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

const ui = {
  catalog: null, catalogPlate: null, searches: [], selected: null, picked: null, manual: false,
  weights: { time_s: 5, grams: 3, stiffness: 3, quality: 0, cost: 0, near_cap_s: 0 },
  limits: { maxTime: "", minStiffness: "" }, yGoal: "grams", checked: new Set(["bridge_speed", "top_shell_layers"]),
  values: {},
};

function goalValue(goal, v) {
  if (v == null) return "–";
  if (goal === "time_s" || goal === "near_cap_s") return fmtTime(v);
  if (goal === "grams") return `${v.toFixed(2)} g`;
  if (goal === "stiffness" || goal === "quality") return `${v.toFixed(1)}%`;
  if (goal === "cost") return v.toFixed(2);
  return String(v);
}

function goalDelta(goal, d, better) {
  if (Math.abs(d) < 1e-9) return { text: "same", cls: "" };
  const good = better === "higher" ? d > 0 : d < 0;
  const text = goal === "time_s" || goal === "near_cap_s" ? fmtDelta(d) :
    `${d < 0 ? "−" : "+"}${goal === "stiffness" || goal === "quality" ? Math.abs(d).toFixed(1) + " pt" : goal === "grams" ? Math.abs(d).toFixed(2) + " g" : Math.abs(d).toFixed(2)}`;
  return { text, cls: good ? "good" : "bad" };
}

function parseHM(text) {
  const m = String(text).trim().match(/^(\d+)\s*h\s*(\d+)?\s*m?$|^(\d+):(\d{1,2})$|^(\d+)\s*m(in)?$/i);
  if (!m) return null;
  if (m[1] != null) return (+m[1]) * 3600 + (+(m[2] || 0)) * 60;
  if (m[3] != null) return (+m[3]) * 3600 + (+m[4]) * 60;
  return (+m[5]) * 60;
}

function start(app) {
  const section = app.registerTab("designer", "Designer", () => render(app));
  section.innerHTML = `
    <div class="designer">
      <div class="d-setup">
        <h2>What to search</h2>
        <p class="muted small">Each setting is changed on the objects of this plate (or print-wide where noted) and sliced for real.</p>
        <div id="d-knobs" class="d-knobs"></div>
        <p id="d-count" class="muted small"></p>
        <div class="d-actions">
          <button type="button" class="ghost" id="d-sens">Measure each setting alone</button>
          <button type="button" class="primary" id="d-grid">Search all combinations</button>
        </div>
        <p id="d-error" class="error" role="alert"></p>
        <h2 class="d-h">Searches on this plate</h2>
        <ol id="d-searches" class="d-searches"></ol>
      </div>
      <div class="d-results" id="d-results"><p class="muted">Pick settings and start a search. Results appear as each slice finishes.</p></div>
    </div>`;
  section.querySelector("#d-sens").onclick = () => create(app, "sensitivity");
  section.querySelector("#d-grid").onclick = () => create(app, "grid");
  setInterval(() => { if (app.state.tab === "designer") refreshSearches(app); }, 2000);
}

async function loadCatalog(app) {
  if (ui.catalogPlate === app.state.plate && ui.catalog) return;
  ui.catalog = await app.api(`catalog?plate=${app.state.plate}`);
  ui.catalogPlate = app.state.plate;
  for (const k of ui.catalog.knobs) if (!(k.key in ui.values)) ui.values[k.key] = k.values.join(", ");
}

function knobList() {
  return ui.catalog.knobs.filter((k) => ui.checked.has(k.key)).map((k) => ({
    key: k.key, values: ui.values[k.key].split(",").map((v) => v.trim()).filter(Boolean),
  }));
}

function renderKnobs(app) {
  const box = document.getElementById("d-knobs");
  box.innerHTML = ui.catalog.knobs.map((k) => `
    <div class="d-knob" title="${esc(k.explain + (k.note ? " " + k.note : ""))}">
      <label class="check"><input type="checkbox" data-key="${k.key}" ${ui.checked.has(k.key) ? "checked" : ""}>
        ${esc(k.label)}${k.scope === "print-wide" ? ' <span class="muted small">(print-wide)</span>' : ""}</label>
      <input type="text" data-values="${k.key}" value="${esc(ui.values[k.key])}" aria-label="Values for ${esc(k.label)}">
      <span class="muted small">now ${esc(k.current ?? "varies")}</span>
    </div>`).join("");
  for (const cb of box.querySelectorAll("input[type=checkbox]")) cb.onchange = () => {
    cb.checked ? ui.checked.add(cb.dataset.key) : ui.checked.delete(cb.dataset.key);
    renderCount(app);
  };
  for (const inp of box.querySelectorAll("input[data-values]")) inp.oninput = () => { ui.values[inp.dataset.values] = inp.value; renderCount(app); };
  renderCount(app);
}

function renderCount(app) {
  const knobs = knobList();
  const alone = 1 + knobs.reduce((n, k) => n + k.values.length, 0);
  const all = knobs.reduce((n, k) => n * Math.max(1, k.values.length), 1);
  const base = app.state.runs.find((r) => r.plate === app.state.plate && r.kind === "baseline");
  const per = base && base.metrics ? base.metrics.wall_s : null;
  const est = (n) => (per ? ` (about ${fmtTime(n * per)} of slicing)` : "");
  document.getElementById("d-count").textContent = knobs.length
    ? `Each alone: ${alone} slices${est(alone)}. All combinations: ${all} slices${est(all)}. Slices already in the cache cost nothing.`
    : "Tick at least one setting.";
}

async function create(app, kind) {
  const err = document.getElementById("d-error");
  err.textContent = "";
  const knobs = knobList();
  if (!knobs.length) { err.textContent = "Tick at least one setting to search."; return; }
  if (knobs.some((k) => !k.values.length)) { err.textContent = "Every ticked setting needs at least one value."; return; }
  try {
    const s = await app.api("searches", { method: "POST", body: { plate: app.state.plate, kind, knobs } });
    ui.selected = s.id;
    ui.manual = false;
    await refreshSearches(app);
  } catch (e) {
    err.textContent = e.message;
  }
}

async function refreshSearches(app) {
  const data = await app.api(`searches?plate=${app.state.plate}`);
  ui.searches = data.searches;
  if (!ui.selected || !ui.searches.find((s) => s.id === ui.selected)) ui.selected = ui.searches[0]?.id || null;
  renderSearchList(app);
  renderResults(app);
}

function renderSearchList(app) {
  const list = document.getElementById("d-searches");
  if (!ui.searches.length) { list.innerHTML = `<li class="muted small">None yet.</li>`; return; }
  list.innerHTML = "";
  for (const s of ui.searches) {
    const li = document.createElement("li");
    li.className = "d-search" + (s.id === ui.selected ? " on" : "");
    const done = s.counts.done, total = s.total;
    const left = s.counts.queued + s.counts.active;
    const what = s.knobs.map((k) => k.key).join(", ");
    li.innerHTML = `<button type="button" class="link d-open">${s.kind === "grid" ? "All combinations" : "Each alone"} of ${esc(what)}${s.by ? " (from Optimize)" : ""}</button>
      <div class="muted small">${done} of ${total} sliced${left ? `, ${left} to go${s.eta_s ? `, about ${fmtTime(s.eta_s)}` : ""}` : ""}${s.counts.error ? `, ${s.counts.error} failed` : ""}${s.counts.cancelled ? `, ${s.counts.cancelled} stopped` : ""}</div>
      <div class="run-actions"></div>`;
    li.querySelector(".d-open").onclick = () => { ui.selected = s.id; ui.manual = false; renderSearchList(app); renderResults(app); };
    const act = li.querySelector(".run-actions");
    const btn = (text, fn) => { const b = document.createElement("button"); b.type = "button"; b.className = "link"; b.textContent = text; b.onclick = fn; act.appendChild(b); };
    if (left) btn("Stop", async () => { await app.api(`searches/${s.id}/cancel`, { method: "POST" }); refreshSearches(app); });
    if (s.counts.cancelled || s.counts.error) btn("Continue", async () => { await app.api(`searches/${s.id}/resume`, { method: "POST" }); refreshSearches(app); });
    btn("Delete", async () => { await app.api(`searches/${s.id}`, { method: "DELETE" }); if (ui.selected === s.id) ui.selected = null; refreshSearches(app); });
    list.appendChild(li);
  }
}

function points(s) {
  return s.candidates.filter((c) => c.status === "done" && c.goals).map((c) => ({ ...c.goals, c }));
}

function pareto(pts, goals) {
  const better = (g) => ui.catalog.goals[g]?.better || "lower";
  const dom = (a, b) => {
    let strict = false;
    for (const g of goals) {
      let x = a[g], y = b[g];
      if (x == null || y == null) return false;
      if (better(g) === "higher") { x = -x; y = -y; }
      if (x > y) return false;
      if (x < y) strict = true;
    }
    return strict;
  };
  return pts.map((p, i) => !pts.some((q, j) => j !== i && dom(q, p)));
}

function feasibleIdx(pts) {
  const maxT = parseHM(ui.limits.maxTime);
  const minS = ui.limits.minStiffness === "" ? null : +ui.limits.minStiffness;
  return pts.map((p) => (maxT == null || p.time_s <= maxT) && (minS == null || (p.stiffness != null && p.stiffness >= minS)));
}

function pickIndex(pts) {
  const goals = Object.entries(ui.weights).filter(([g, w]) => w > 0 && pts.every((p) => p[g] != null)).map(([g]) => g);
  if (!goals.length) return null;
  const ok = feasibleIdx(pts);
  const cand = pts.map((p, i) => i).filter((i) => ok[i]);
  if (!cand.length) return null;
  const front = pareto(cand.map((i) => pts[i]), goals);
  const lo = {}, hi = {};
  for (const g of goals) { lo[g] = Math.min(...cand.map((i) => pts[i][g])); hi[g] = Math.max(...cand.map((i) => pts[i][g])); }
  let best = null, bestScore = Infinity;
  cand.forEach((i, k) => {
    if (!front[k]) return;
    const terms = goals.map((g) => {
      const span = hi[g] - lo[g], v = pts[i][g];
      const n = span === 0 ? 0 : (ui.catalog.goals[g].better === "higher" ? (hi[g] - v) / span : (v - lo[g]) / span);
      return ui.weights[g] * n;
    });
    const score = Math.max(...terms) + 0.01 * terms.reduce((a, b) => a + b, 0);
    if (score < bestScore) { bestScore = score; best = i; }
  });
  return best;
}

function renderResults(app) {
  const box = document.getElementById("d-results");
  const s = ui.searches.find((x) => x.id === ui.selected);
  if (!s) { box.innerHTML = `<p class="muted">Pick settings and start a search. Results appear as each slice finishes.</p>`; return; }
  const pts = points(s);
  const base = app.state.runs.find((r) => r.plate === s.plate && r.kind === "baseline");
  const baseGoals = base && base.goals;
  const goalsAvail = Object.keys(ui.catalog.goals).filter((g) => pts.some((p) => p[g] != null));
  const pct = Math.round((100 * s.counts.done) / s.total);
  if (!box.querySelector(".d-controls")) {
    box.innerHTML = `
      <div class="d-progress"><div class="bar"><span></span></div><span class="muted small" id="d-prog"></span></div>
      <div class="d-controls">
        <div id="d-weights" class="d-weights"></div>
        <div class="d-limits">
          <label class="field">Max print time <input type="text" id="d-maxtime" placeholder="2h30m" value="${esc(ui.limits.maxTime)}"></label>
          <label class="field">Min stiffness % <input type="text" id="d-minstiff" placeholder="85" value="${esc(ui.limits.minStiffness)}"></label>
          <label class="field">Up the chart <select id="d-y"></select></label>
        </div>
      </div>
      <div class="d-plot" id="d-plot"></div>
      <div id="d-picked" class="d-picked"></div>
      <div id="d-tornado"></div>`;
    box.querySelector("#d-maxtime").oninput = (e) => { ui.limits.maxTime = e.target.value; ui.manual = false; renderResults(app); };
    box.querySelector("#d-minstiff").oninput = (e) => { ui.limits.minStiffness = e.target.value; ui.manual = false; renderResults(app); };
    box.querySelector("#d-y").onchange = (e) => { ui.yGoal = e.target.value; renderResults(app); };
  }
  box.querySelector(".d-progress .bar span").style.width = `${pct}%`;
  box.querySelector("#d-prog").textContent = `${s.counts.done} of ${s.total} sliced` +
    (s.counts.queued + s.counts.active ? `, about ${s.eta_s ? fmtTime(s.eta_s) : "a moment"} to go` : "") +
    (s.counts.error ? `. ${s.counts.error} failed: ${esc(s.candidates.find((c) => c.status === "error")?.error || "")}` : "");
  const ysel = box.querySelector("#d-y");
  const yOpts = goalsAvail.filter((g) => g !== "time_s");
  if (ysel.options.length !== yOpts.length) {
    ysel.innerHTML = yOpts.map((g) => `<option value="${g}">${esc(ui.catalog.goals[g].label)}</option>`).join("");
  }
  if (!yOpts.includes(ui.yGoal)) ui.yGoal = yOpts[0];
  ysel.value = ui.yGoal;
  renderWeights(app, goalsAvail);
  if (!ui.manual) {
    const i = pickIndex(pts);
    ui.picked = i == null ? null : pts[i].c.run_id;
  }
  drawScatter(app, pts, baseGoals);
  renderPicked(app, pts, baseGoals);
  renderTornado(s, baseGoals);
}

function renderWeights(app, goalsAvail) {
  const box = document.getElementById("d-weights");
  const key = goalsAvail.join(",");
  if (box.dataset.key === key) return;
  box.dataset.key = key;
  box.innerHTML = goalsAvail.map((g) => {
    const meta = ui.catalog.goals[g];
    const tag = meta.source === "proxy" && !/proxy/i.test(meta.label) ? " (proxy)" : "";
    return `<label class="field d-weight" title="${esc(meta.explain || "")}">${esc(meta.label)}${tag}: matters
      <input type="range" min="0" max="10" step="1" value="${ui.weights[g] ?? 0}" data-goal="${g}"><output>${ui.weights[g] ?? 0}</output></label>`;
  }).join("");
  for (const r of box.querySelectorAll("input[type=range]")) r.oninput = () => {
    ui.weights[r.dataset.goal] = +r.value;
    r.nextElementSibling.textContent = r.value;
    ui.manual = false;
    renderResults(app);
  };
}

function drawScatter(app, pts, baseGoals) {
  const box = document.getElementById("d-plot");
  box.innerHTML = "";
  if (!pts.length) { box.innerHTML = `<p class="muted">Waiting for the first slices.</p>`; return; }
  const yg = ui.yGoal;
  const width = Math.max(box.clientWidth || 640, 320), height = 340;
  const m = { l: 64, r: 16, t: 14, b: 40 };
  const iw = width - m.l - m.r, ih = height - m.t - m.b;
  const xs = pts.map((p) => p.time_s), ys = pts.map((p) => p[yg]).filter((v) => v != null);
  if (baseGoals) { xs.push(baseGoals.time_s); if (baseGoals[yg] != null) ys.push(baseGoals[yg]); }
  const pad = (lo, hi) => { const d = (hi - lo) || Math.abs(hi) * 0.05 || 1; return [lo - d * 0.08, hi + d * 0.08]; };
  const [x0, x1] = pad(Math.min(...xs), Math.max(...xs));
  const [y0, y1] = pad(Math.min(...ys), Math.max(...ys));
  const X = (v) => m.l + ((v - x0) / (x1 - x0)) * iw;
  const Y = (v) => m.t + ih - ((v - y0) / (y1 - y0)) * ih;
  const svg = svgEl("svg", { width, height, role: "img", "aria-label": "Candidates: print time against the chosen goal" }, box);
  for (let k = 0; k <= 4; k++) {
    const v = y0 + ((y1 - y0) * k) / 4;
    svgEl("line", { x1: m.l, x2: m.l + iw, y1: Y(v), y2: Y(v), class: "axis" }, svg);
    svgEl("text", { x: m.l - 6, y: Y(v) + 4, "text-anchor": "end" }, svg).textContent = goalValue(yg, v);
    const t = x0 + ((x1 - x0) * k) / 4;
    svgEl("text", { x: X(t), y: height - 22, "text-anchor": "middle" }, svg).textContent = fmtTime(t);
  }
  svgEl("text", { x: m.l + iw / 2, y: height - 4, "text-anchor": "middle" }, svg).textContent = "Print time";
  const goals = Object.entries(ui.weights).filter(([g, w]) => w > 0 && pts.every((p) => p[g] != null)).map(([g]) => g);
  const front = pareto(pts, goals.length ? goals : ["time_s", yg]);
  const ok = feasibleIdx(pts);
  const ordered = pts.map((p, i) => i).sort((a, b) => front[a] - front[b]);
  for (const i of ordered) {
    const p = pts[i];
    if (p[yg] == null) continue;
    const isFront = front[i] && ok[i];
    const c = svgEl("circle", { cx: X(p.time_s), cy: Y(p[yg]), r: isFront ? 5.5 : 4,
      fill: isFront ? "var(--a)" : "var(--muted)", "fill-opacity": isFront ? 1 : 0.45, style: "cursor:pointer" }, svg);
    const tip = document.getElementById("tooltip");
    c.addEventListener("mouseenter", (ev) => {
      tip.innerHTML = `<strong>${esc(p.c.label)}</strong><br>${goalValue("time_s", p.time_s)}, ${goalValue("grams", p.grams)}` +
        (p.stiffness != null ? `, stiffness ${p.stiffness.toFixed(1)}%` : "") + (isFront ? "" : `<br><span class="muted">${ok[i] ? "dominated" : "outside your limits"}</span>`);
      tip.style.left = `${ev.clientX + 14}px`; tip.style.top = `${ev.clientY + 14}px`; tip.hidden = false;
    });
    c.addEventListener("mouseleave", () => { tip.hidden = true; });
    c.addEventListener("click", () => { ui.picked = p.c.run_id; ui.manual = true; renderResults(app); });
  }
  if (baseGoals && baseGoals[yg] != null) {
    svgEl("circle", { cx: X(baseGoals.time_s), cy: Y(baseGoals[yg]), r: 9, fill: "none", stroke: "var(--ink)", "stroke-width": 1.5 }, svg);
    svgEl("text", { x: X(baseGoals.time_s), y: Y(baseGoals[yg]) + 22, "text-anchor": "middle" }, svg).textContent = "Current";
  }
  const pk = pts.find((p) => p.c.run_id === ui.picked);
  if (pk && pk[yg] != null) {
    svgEl("circle", { cx: X(pk.time_s), cy: Y(pk[yg]), r: 11, fill: "none", stroke: "var(--accent)", "stroke-width": 3 }, svg);
  }
}

function renderPicked(app, pts, baseGoals) {
  const box = document.getElementById("d-picked");
  const p = pts.find((x) => x.c.run_id === ui.picked);
  if (!p) { box.innerHTML = `<p class="muted">${pts.length ? "No candidate meets your limits; loosen them or give a goal some importance." : ""}</p>`; return; }
  const goals = Object.keys(ui.catalog.goals).filter((g) => p[g] != null);
  const stats = goals.map((g) => {
    const meta = ui.catalog.goals[g];
    const d = baseGoals && baseGoals[g] != null ? goalDelta(g, p[g] - baseGoals[g], meta.better) : null;
    return `<div class="stat"><div class="label">${esc(meta.label)}${meta.source === "proxy" && !/proxy/i.test(meta.label) ? " (proxy)" : ""}</div>
      <div class="value">${goalValue(g, p[g])}</div>${d ? `<div class="delta ${d.cls}">${d.text} vs current</div>` : ""}</div>`;
  }).join("");
  const assign = Object.entries(p.c.assignment);
  const knobs = Object.fromEntries(ui.catalog.knobs.map((k) => [k.key, k]));
  box.innerHTML = `<h3>${ui.manual ? "Your pick" : "Best match for your priorities"}</h3>
    <p>${assign.length ? assign.map(([k, v]) => `${esc(knobs[k]?.label || k)} <strong>${esc(v)}</strong> <span class="muted">(now ${esc(knobs[k]?.current ?? "?")})</span>`).join("; ") : "Your current settings"}</p>
    <div class="summary">${stats}</div>
    <div class="d-actions">
      <button type="button" class="primary" id="d-compare">Compare with current, layer by layer</button>
      <button type="button" class="ghost" id="d-save">Save as .3mf</button>
    </div>
    <p class="muted small">Stiffness is a proxy and surface quality isn't measured: print a test piece before a batch.</p>`;
  box.querySelector("#d-compare").onclick = async () => {
    await app.api(`runs/${p.c.run_id}/pin`, { method: "POST", body: { pinned: true } });
    await app.poll();
    const base = app.state.runs.find((r) => r.plate === app.state.plate && r.kind === "baseline");
    app.state.A = base ? base.id : app.state.A;
    app.state.B = p.c.run_id;
    app.renderRuns();
    app.switchTab("layers");
  };
  box.querySelector("#d-save").onclick = async () => {
    const run = app.state.runs.find((r) => r.id === p.c.run_id) || { id: p.c.run_id, label: p.c.label };
    if (!assign.length) { window.alert("These are your current settings; there is nothing new to save."); return; }
    await app.saveRun(run);
  };
}

function renderTornado(s, baseGoals) {
  const box = document.getElementById("d-tornado");
  if (s.kind !== "sensitivity" || !baseGoals) { box.innerHTML = ""; return; }
  const rows = [];
  for (const k of s.knobs) {
    const pts = s.candidates.filter((c) => c.status === "done" && c.goals && Object.keys(c.assignment).length === 1 && k.key in c.assignment);
    if (!pts.length) continue;
    const dt = pts.map((c) => ({ v: c.assignment[k.key], dt: c.goals.time_s - baseGoals.time_s, dg: c.goals.grams - baseGoals.grams }));
    rows.push({ key: k.key, dt, span: Math.max(...dt.map((d) => Math.abs(d.dt))) });
  }
  rows.sort((a, b) => b.span - a.span);
  if (!rows.length) { box.innerHTML = ""; return; }
  const max = Math.max(1, ...rows.map((r) => r.span));
  const width = Math.max(box.clientWidth || 640, 320), rowH = 30, m = { l: 190, r: Math.min(340, Math.max(200, width * 0.42)) };
  const iw = width - m.l - m.r;
  box.innerHTML = `<h3>What each setting does on its own</h3><p class="muted small">Change in print time against the current settings, for each value tried. Zero width means no effect: it may be capped or blocked by another setting.</p>`;
  const svg = svgEl("svg", { width, height: rows.length * rowH + 10, role: "img", "aria-label": "Effect of each setting on print time" }, box);
  const zero = m.l + iw / 2;
  svgEl("line", { x1: zero, x2: zero, y1: 0, y2: rows.length * rowH + 10, class: "axis" }, svg);
  rows.forEach((r, k) => {
    const y = 6 + k * rowH;
    svgEl("text", { x: m.l - 8, y: y + 15, "text-anchor": "end" }, svg).textContent = r.key;
    const lo = Math.min(0, ...r.dt.map((d) => d.dt)), hi = Math.max(0, ...r.dt.map((d) => d.dt));
    const sx = (v) => zero + (v / max) * (iw / 2);
    svgEl("rect", { x: sx(lo), y: y + 4, width: Math.max(1, sx(hi) - sx(lo)), height: 16, fill: "var(--a)", opacity: 0.35, rx: 2 }, svg);
    for (const d of r.dt) svgEl("circle", { cx: sx(d.dt), cy: y + 12, r: 4, fill: "var(--a)" }, svg);
    const label = svgEl("text", { x: m.l + iw + 8, y: y + 15 }, svg);
    label.textContent = r.dt.map((d) => `${d.v}: ${fmtDelta(d.dt)}`).join("  ");
    svgEl("title", {}, label).textContent = label.textContent;
  });
}

async function render(app) {
  await loadCatalog(app);
  if (document.getElementById("d-knobs").dataset.plate !== String(app.state.plate)) {
    document.getElementById("d-knobs").dataset.plate = String(app.state.plate);
    renderKnobs(app);
    ui.selected = null;
    document.getElementById("d-results").innerHTML = "";
  }
  await refreshSearches(app);
}

if (window.slicerApp) start(window.slicerApp);
else window.addEventListener("slicer:ready", (e) => start(e.detail), { once: true });
