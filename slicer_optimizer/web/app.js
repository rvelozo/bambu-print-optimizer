import { LayerView, parseLayer } from "./layerview.js";
import { featureColor, RAMP_CSS } from "./colors.js";
import { layerBars, layerLines, featureBars, fmtTime, fmtDelta } from "./charts.js";
import { enableDrop, escapeHtml, quitApp } from "./opener.js";
import { nameOf, openPicker, valueText } from "./settingpicker.js";

const $ = (id) => document.getElementById(id);
const PID = (location.pathname.match(/^\/p\/([0-9a-f]{16})/) || [])[1];  // this project's id, from /p/<id>/
const state = {
  project: null, runs: [], plate: null, A: null, B: null, tab: "layers",
  layer: null, colorBy: "feature", showTravel: false, showSeams: false, overlay: false, fitted: null,
};
const analyses = new Map();
const layerCache = new Map();
const tabs = new Map();   // extra tabs registered by later phases: name -> render(state, ctx)
const summaryHooks = [];  // extra summary stats: (state, runA, runB) -> html

async function api(path, opts = {}) {
  const res = await fetch(`/api/p/${PID}/${path}`, {
    method: opts.method || "GET",
    headers: opts.body ? { "Content-Type": "application/json" } : {},
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  const ctype = res.headers.get("Content-Type") || "";
  const data = ctype.includes("json") ? await res.json() : await res.arrayBuffer();
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

const view = new LayerView({
  canvasA: $("canvas-a"), canvasB: $("canvas-b"), tooltip: $("tooltip"),
  plate: { bg: "#1e1b16", grid: "#2c2820" },
});

// ---- project and plates -------------------------------------------------------------

function plateTitle(p) {
  const names = p.objects.map((o) => o.name.replace(/\.stl$/i, "") + (o.copies > 1 ? ` ×${o.copies}` : ""));
  return names.join(", ") || "empty";
}

function renderPlates() {
  const nav = $("plates");
  nav.innerHTML = "";
  for (const p of state.project.plates) {
    const b = document.createElement("button");
    b.setAttribute("aria-selected", String(p.index === state.plate));
    b.innerHTML = (p.thumbnail ? `<img class="plate-thumb" alt="" src="/api/p/${PID}/thumbnail/${p.index}?size=small">` : "") +
      `Plate ${p.index}<span>${escapeHtml(plateTitle(p))}</span>`;
    b.onclick = () => selectPlate(p.index);
    nav.appendChild(b);
  }
}

function selectPlate(index) {
  state.plate = index;
  const base = baselineRun();
  state.A = base ? base.id : null;
  state.B = null;
  state.layer = null;
  state.fitted = null;
  renderPlates();
  renderRuns();
  refreshView();
}

const plateRuns = () => state.runs.filter((r) => r.plate === state.plate);
const baselineRun = () => plateRuns().find((r) => r.kind === "baseline");
const runById = (id) => state.runs.find((r) => r.id === id);

// ---- runs ---------------------------------------------------------------------------

function deltaSpan(v, base, fmt, unit = "") {
  if (base == null || v == null) return "";
  const d = v - base;
  if (Math.abs(d) < 0.005 && unit) return ` <span>same</span>`;
  const cls = d < 0 ? "good" : "bad";
  return ` <span class="${cls}">${unit ? (d < 0 ? "−" : "+") + Math.abs(d).toFixed(2) + unit : fmt(d)}</span>`;
}

let runsSignature = "";
function renderRuns(force = true) {
  const sig = JSON.stringify([state.plate, state.A, state.B, plateRuns().map((r) => [r.id, r.status, r.label, r.metrics && r.metrics.total_s, r.error])]);
  if (!force && sig === runsSignature) return;
  runsSignature = sig;
  const list = $("runs");
  list.innerHTML = "";
  const base = baselineRun();
  const runs = plateRuns().filter((r) => r.kind !== "search" || r.pinned || r.id === state.A || r.id === state.B)
    .sort((a, b) => (a.kind === "baseline" ? -1 : b.kind === "baseline" ? 1 : a.created - b.created));
  for (const r of runs) {
    const li = document.createElement("li");
    li.className = "run" + (r.id === state.A ? " is-a" : "") + (r.id === state.B ? " is-b" : "");
    const m = r.metrics;
    let numbers = "";
    if (r.status === "done" && m) {
      const bm = base && base.metrics && r.id !== base.id ? base.metrics : null;
      numbers = `${fmtTime(m.total_s)}${bm ? deltaSpan(m.total_s, bm.total_s, fmtDelta) : ""}, ` +
                `${m.grams.toFixed(2)} g${bm ? deltaSpan(m.grams, bm.grams, null, " g") : ""}`;
      if (m.support_s > 0) numbers += `, <span class="bad">supports ${fmtTime(m.support_s)}</span>`;
    }
    const status = r.status === "done" ? "" :
      r.status === "error" ? `<div class="run-status error">${escapeHtml(r.error || "failed")}</div>` :
      `<div class="run-status">${{ queued: "Waiting to slice", slicing: "Slicing…", analysing: "Reading the G-code…" }[r.status] || r.status}</div>`;
    li.innerHTML = `
      <div class="run-label">${escapeHtml(r.label)}</div>
      <div class="run-pick">
        <button type="button" title="Show as A" class="${r.id === state.A ? "on-a" : ""}" data-pick="A">A</button>
        <button type="button" title="Compare as B" class="${r.id === state.B ? "on-b" : ""}" data-pick="B">B</button>
      </div>
      <div class="run-numbers">${numbers}</div>
      ${status}
      <div class="run-actions"></div>`;
    li.querySelector('[data-pick="A"]').onclick = () => { state.A = r.id; if (state.B === r.id) state.B = null; renderRuns(); refreshView(); };
    li.querySelector('[data-pick="B"]').onclick = () => { state.B = state.B === r.id ? null : r.id; if (state.A === r.id) state.A = baselineRun()?.id; renderRuns(); refreshView(); };
    const actions = li.querySelector(".run-actions");
    if (r.kind !== "baseline" && r.status === "done") actions.appendChild(actionButton("Save as .3mf", () => saveRun(r)));
    if (r.kind === "search" && r.pinned) actions.appendChild(actionButton("Unpin", async () => {
      await api(`runs/${r.id}/pin`, { method: "POST", body: { pinned: false } });
      if (state.B === r.id) state.B = null;
      await poll();
    }));
    if (r.status === "error") actions.appendChild(actionButton("Try again", async () => { await api(`runs/${r.id}/retry`, { method: "POST" }); poll(); }));
    if (r.kind === "trial") actions.appendChild(actionButton("Remove", async () => {
      await api(`runs/${r.id}`, { method: "DELETE" });
      if (state.B === r.id) state.B = null;
      if (state.A === r.id) state.A = baselineRun()?.id;
      await poll();
    }));
    list.appendChild(li);
  }
}

function actionButton(text, fn) {
  const b = document.createElement("button");
  b.type = "button";
  b.className = "link";
  b.textContent = text;
  b.onclick = fn;
  return b;
}

async function saveRun(r) {
  if (!state.project.dialogs) return savePrompt(r);
  // The macOS Save dialog asks for the name and place, and about replacing a file.
  try {
    const res = await api(`runs/${r.id}/save`, { method: "POST", body: { dialog: true } });
    if (!res.cancelled) window.alert(`Saved ${res.saved}\nIt is byte-for-byte the file that was sliced.`);
  } catch (e) {
    window.alert(e.message);
  }
}

async function savePrompt(r) {
  // Without a native dialog (Windows, Linux): ask for a path in the page.
  const dir = state.project.save_dir.replace(/[\\/]*$/, "/");
  const stem = state.project.name.replace(/\.3mf$/i, "");
  const safe = r.label.replace(/[^\w.=,+-]+/g, " ").trim().slice(0, 60);
  const dest = window.prompt("Save this variant as", `${dir}${stem} - ${safe}.3mf`);
  if (!dest) return;
  try {
    const res = await api(`runs/${r.id}/save`, { method: "POST", body: { path: dest } });
    window.alert(`Saved ${res.saved}\nIt is byte-for-byte the file that was sliced.`);
  } catch (e) {
    if (String(e.message).includes("already exists") && window.confirm(`${dest} already exists. Replace it?`)) {
      const res = await api(`runs/${r.id}/save`, { method: "POST", body: { path: dest, overwrite: true } });
      window.alert(`Saved ${res.saved}`);
    } else {
      window.alert(e.message);
    }
  }
}

// ---- the file on disk -----------------------------------------------------------------

let bannerState = "";
function renderBanner(disk) {
  const box = $("banner");
  if (disk === bannerState) return;
  bannerState = disk;
  if (disk === "changed") {
    box.className = "banner";
    box.innerHTML = `<span>${escapeHtml(state.project.name)} changed on disk since it was loaded. These results are for the earlier version.</span>
      <button type="button" class="ghost" id="reload">Load the new version</button>`;
    $("reload").onclick = async () => {
      $("reload").disabled = true;
      try { await api("reload", { method: "POST" }); location.reload(); }
      catch (e) { $("reload").disabled = false; window.alert(e.message); }
    };
  } else if (disk === "missing") {
    box.className = "banner bad";
    box.innerHTML = `<span>${escapeHtml(state.project.name)} was moved or deleted. Its results stay here; to slice again, open the file from its new place.</span>`;
  }
  box.hidden = disk !== "changed" && disk !== "missing";
}

function renderOlder(n) {
  const p = $("runs-older");
  p.hidden = !n;
  p.textContent = n ? `${n} run${n === 1 ? "" : "s"} of earlier versions of this file ${n === 1 ? "is" : "are"} hidden.` : "";
}

async function poll() {
  try {
    const data = await api("runs");
    state.runs = data.runs;
    state.waiting = data.waiting;
    renderBanner(data.disk);
    renderOlder(data.older);
  } catch (e) {
    return;
  }
  if (state.plate != null && !runById(state.A)) state.A = baselineRun()?.id;
  renderRuns(false);
  const a = runById(state.A), b = runById(state.B);
  const ready = (r) => r && r.status === "done" && !analyses.has(r.id);
  if (ready(a) || ready(b)) refreshView();
  else renderSummary();
}

// ---- analysis and layers ----------------------------------------------------------

async function analysisOf(run) {
  if (!run || run.status !== "done") return null;
  if (!analyses.has(run.id)) analyses.set(run.id, await api(`runs/${run.id}/analysis`));
  return analyses.get(run.id);
}

async function layerOf(run, index) {
  const key = `${run.id}:${index}`;
  if (!layerCache.has(key)) {
    if (layerCache.size > 40) layerCache.delete(layerCache.keys().next().value);
    layerCache.set(key, parseLayer(await api(`runs/${run.id}/layer/${index}`)));
  }
  return layerCache.get(key);
}

function stat(label, value, delta, key) {
  return `<div class="stat"><div class="label">${key ? `<span class="key" style="background:var(--${key})"></span>` : ""}${label}</div>
          <div class="value">${value}</div>${delta ? `<div class="delta ${delta.cls}">${delta.text}</div>` : ""}</div>`;
}

function renderSummary() {
  const a = runById(state.A), b = runById(state.B);
  const box = $("summary");
  const plate = state.project && state.project.plates.find((p) => p.index === state.plate);
  if (plate && !plate.objects.length) { box.innerHTML = `<p class="muted">Plate ${plate.index} is empty in this project: nothing to slice.</p>`; return; }
  if (!a) { box.innerHTML = ""; return; }
  if (a.status === "error") { box.innerHTML = `<p class="error">Plate ${a.plate} couldn't be sliced: ${a.error}</p>`; return; }
  if (a.status !== "done") { box.innerHTML = `<p class="muted">Slicing plate ${a.plate}. Times and previews appear when it's done.</p>`; return; }
  const ma = a.metrics, mb = b && b.status === "done" ? b.metrics : null;
  const d = (x, y, fmt, lowerIsBetter = true) => {
    if (y == null) return null;
    const diff = y - x;
    if (Math.abs(diff) < 1e-9) return { cls: "", text: "same in B" };
    return { cls: (diff < 0) === lowerIsBetter ? "good" : "bad", text: `B ${fmt(diff)}` };
  };
  const gfmt = (v) => `${v < 0 ? "−" : "+"}${Math.abs(v).toFixed(2)} g`;
  const cfmt = (v) => `${v < 0 ? "−" : "+"}${Math.abs(v).toFixed(2)}`;
  let html = stat("Print time", fmtTime(ma.total_s), mb && d(ma.total_s, mb.total_s, (v) => (Math.abs(v) <= Math.max(ma.tolerance_s || 0, mb.tolerance_s || 0) ? fmtDelta(v) + " (within noise)" : fmtDelta(v))), "a");
  html += stat("Weight", `${ma.grams.toFixed(2)} g`, mb && d(ma.grams, mb.grams, gfmt));
  if (ma.cost != null) html += stat("Filament cost", ma.cost.toFixed(2), mb && mb.cost != null && d(ma.cost, mb.cost, cfmt));
  html += stat("Supports", ma.support_s ? fmtTime(ma.support_s) : "none", mb && d(ma.support_s, mb.support_s, fmtDelta));
  if (ma.flow_cap) html += stat(`Time near the ${ma.flow_cap} mm³/s cap`, fmtTime(ma.near_cap_s), mb && d(ma.near_cap_s, mb.near_cap_s, fmtDelta));
  if (mb) html += stat("B", fmtTime(mb.total_s), null, "b");
  for (const hook of summaryHooks) html += hook(state, a, b) || "";
  box.innerHTML = html;
}

async function refreshView() {
  renderSummary();
  const a = runById(state.A), b = runById(state.B);
  const [aa, ab] = await Promise.all([analysisOf(a), analysisOf(b)]);
  const tab = state.tab;
  if (tab === "layers") await renderLayers(a, b, aa, ab);
  else if (tab === "time") renderTime(aa, ab);
  else if (tab === "flow") renderFlow(aa, ab);
  else if (tab === "features") renderFeatures(a, aa, ab);
  else if (tabs.has(tab)) tabs.get(tab)(state, { api, runById, analysisOf, layerOf, plateRuns, baselineRun, refreshView, poll });
}

function firstPrintedLayer(an) {
  const i = an.layers.findIndex((l) => l.ext_count > 0);
  return i < 0 ? 0 : i;
}

async function renderLayers(a, b, aa, ab) {
  const single = !ab || state.overlay;
  $("canvases").classList.toggle("single", single);
  $("pane-b").hidden = single;
  if (!aa) { view.set({}); $("legend").innerHTML = ""; return; }
  const n = aa.layers.length;
  const slider = $("layer");
  slider.max = String(Math.max(0, n - 1));
  if (state.layer == null || state.layer >= n) state.layer = firstPrintedLayer(aa);
  slider.value = String(state.layer);
  const la = aa.layers[state.layer];
  const lb = ab && ab.layers[state.layer];
  $("layer-out").textContent = state.layer === 0 ? "Start G-code" : `Layer ${state.layer} of ${n - 1}, z ${la.z.toFixed(2)} mm`;
  const [da, db] = await Promise.all([layerOf(a, state.layer), lb ? layerOf(b, state.layer) : null]);
  const fitKey = `${a.id}|${single ? "1" : "2"}`;
  if (state.fitted !== fitKey) {
    const bb = ab ? [Math.min(aa.bbox[0], ab.bbox[0]), Math.min(aa.bbox[1], ab.bbox[1]), Math.max(aa.bbox[2], ab.bbox[2]), Math.max(aa.bbox[3], ab.bbox[3])] : aa.bbox;
    await new Promise(requestAnimationFrame);  // let the panes take their new size first
    view.fit(bb);
    state.fitted = fitKey;
  }
  view.set({ a: da, b: db, featuresA: aa.features, featuresB: ab ? ab.features : [], colorBy: state.colorBy,
             showTravel: state.showTravel, overlay: state.overlay && !!db, cap: aa.flow.cap,
             seamsA: la.seams, seamsB: lb ? lb.seams : [], showSeams: state.showSeams });
  $("cap-a").textContent = state.overlay && db ? `A (blue) and B (orange), ${fmtTime(la.time_s)} vs ${fmtTime(lb.time_s)}` : `A  ${fmtTime(la.time_s)} on this layer`;
  $("cap-b").textContent = lb ? `B  ${fmtTime(lb.time_s)} on this layer` : "";
  renderLegend(aa, la, ab && lb);
}

function renderLegend(aa, la, lb) {
  const box = $("legend");
  if (state.overlay && lb) { box.innerHTML = `<span><span class="sw" style="background:var(--a)"></span>A</span><span><span class="sw" style="background:var(--b)"></span>B</span>`; return; }
  if (state.colorBy === "feature") {
    const roles = Object.entries(la.time_by_role).filter(([k]) => k !== "Undefined").sort((x, y) => y[1] - x[1]);
    const travel = la.time_by_role["Undefined"];
    box.innerHTML = roles.map(([k, t]) => `<span><span class="sw" style="background:${featureColor(k)}"></span>${k} ${fmtTime(t)}</span>`).join("") +
      (travel ? `<span class="muted">Travel ${fmtTime(travel)}</span>` : "");
    return;
  }
  const r = view.range;
  const unit = { speed: "mm/s", flow: "mm³/s", width: "mm" }[state.colorBy];
  const fmt = (v) => (state.colorBy === "width" ? v.toFixed(2) : v.toFixed(state.colorBy === "flow" ? 1 : 0));
  box.innerHTML = `<span>${fmt(r.min)} ${unit}<span class="ramp" style="background:${RAMP_CSS}"></span>${fmt(r.max)} ${unit}</span>` +
    (state.colorBy === "flow" && aa.flow.cap ? `<span class="muted">The filament's cap is ${aa.flow.cap} mm³/s</span>` : "");
}

function layerTip(aa, ab, i) {
  const l = aa.layers[i];
  const top = Object.entries(l.time_by_role).sort((x, y) => y[1] - x[1]).slice(0, 3)
    .map(([k, t]) => `${k === "Undefined" ? "Travel" : k} ${fmtTime(t)}`).join("<br>");
  const lb = ab && ab.layers[i];
  return `<strong>${i === 0 ? "Start G-code" : `Layer ${i}, z ${l.z.toFixed(2)}`}</strong><br>A ${fmtTime(l.time_s)}` +
    (lb ? `, B ${fmtTime(lb.time_s)} (${fmtDelta(lb.time_s - l.time_s)})` : "") + `<br><span class="muted">${top}</span>`;
}

function renderTime(aa, ab) {
  if (!aa) return;
  const n = aa.layers.length;
  layerBars($("time-chart"), {
    a: aa.layers.map((l) => l.time_s), b: ab ? Array.from({ length: n }, (_, i) => ab.layers[i]?.time_s || 0) : null,
    labels: (i) => (i === 0 ? "start" : String(i)), time: true, selected: state.layer, aria: "Time per layer",
    tip: (i) => layerTip(aa, ab, i), onPick: (i) => { state.layer = i; switchTab("layers"); },
  });
  const total = aa.total_s;
  const slow = aa.layers.map((l, i) => ({ ...l, i })).filter((l) => l.i > 0).sort((x, y) => y.time_s - x.time_s).slice(0, 5);
  $("time-slowest").innerHTML = `<h3>Slowest layers in A</h3><table class="table"><tr><th>Layer</th><th>Time</th><th>Share</th><th>Mostly</th></tr>` +
    slow.map((l) => {
      const [k] = Object.entries(l.time_by_role).sort((x, y) => y[1] - x[1])[0] || ["–"];
      return `<tr><td><button class="link" data-layer="${l.i}">Layer ${l.i}, z ${l.z.toFixed(2)}</button></td><td>${fmtTime(l.time_s)}</td><td>${(100 * l.time_s / total).toFixed(1)}%</td><td>${k === "Undefined" ? "Travel" : k}</td></tr>`;
    }).join("") + `</table><p class="muted small">Start G-code (heating, leveling, purge) takes ${fmtTime(aa.layers[0]?.time_s || 0)} before layer 1.</p>`;
  for (const btn of $("time-slowest").querySelectorAll("[data-layer]")) btn.onclick = () => { state.layer = +btn.dataset.layer; switchTab("layers"); };
}

function renderFlow(aa, ab) {
  if (!aa) return;
  const n = aa.layers.length;
  const series = [
    { values: aa.layers.map((l) => l.p95_flow), color: "var(--a)", label: "A p95" },
    { values: aa.layers.map((l) => l.max_flow), color: "var(--a)", dash: "3 3", label: "A max" },
  ];
  if (ab) series.push({ values: Array.from({ length: n }, (_, i) => ab.layers[i]?.p95_flow || 0), color: "var(--b)", label: "B p95" });
  layerLines($("flow-chart"), {
    series, n, cap: aa.flow.cap, labels: (i) => (i === 0 ? "start" : String(i)), fmt: (v) => `${+v.toFixed(1)}`, aria: "Flow per layer",
    tip: (i) => {
      const l = aa.layers[i];
      const lb = ab && ab.layers[i];
      return `<strong>Layer ${i}</strong><br>A: 95% of extrusion at or below ${l.p95_flow.toFixed(1)} mm³/s, peak ${l.max_flow.toFixed(1)}<br>` +
        `${fmtTime(l.near_cap_s)} near the cap` + (lb ? `<br>B: ${lb.p95_flow.toFixed(1)} mm³/s, ${fmtTime(lb.near_cap_s)} near the cap` : "");
    },
  });
  const share = (an) => (100 * an.flow.near_cap_s / an.model_s).toFixed(0);
  if (!aa.flow.cap) {
    const caps = (aa.filament.filaments || []).map((f) => `${f.max_flow ?? "none"} (filament ${f.slot})`).join(", ");
    $("flow-notes").innerHTML = aa.filament.caps_differ
      ? `<p>The filaments on this plate have different flow caps: ${escapeHtml(caps)} mm³/s. Time near the cap isn't measured, because which filament prints each line isn't tracked yet.</p>`
      : `<p>The filament profile sets no max volumetric speed, so there is no cap to compare against.</p>`;
    return;
  }
  $("flow-notes").innerHTML = `<p>A spends <strong>${fmtTime(aa.flow.near_cap_s)}</strong> (${share(aa)}% of the model time) printing at 95% or more of the ${aa.flow.cap} mm³/s cap` +
    (ab ? `; B spends ${fmtTime(ab.flow.near_cap_s)} (${share(ab)}%)` : "") + `. In those stretches only a higher cap (a faster filament profile, or a flow calibration) shortens the print.</p>` +
    `<p class="muted small">Solid line: 95% of the extrusion length is at or below this flow. Dashed: the layer's peak.</p>`;
}

function renderFeatures(a, aa, ab) {
  if (!aa) return;
  const fa = aa.time_by_feature, fb = ab ? ab.time_by_feature : {};
  const names = Object.keys(fa).filter((k) => k !== "Travel" && fa[k] > 0.5);
  for (const k of Object.keys(fb)) if (!names.includes(k) && k !== "Travel" && fb[k] > 0.5) names.push(k);
  names.sort((x, y) => (fa[y] || 0) - (fa[x] || 0));
  const rows = names.map((k) => ({ label: k === "Undefined" ? "Travel" : k, a: fa[k] || 0, b: fb[k] || 0, color: k === "Undefined" ? "#38489b" : featureColor(k) }));
  featureBars($("feature-chart"), rows, { hasB: !!ab });
  const slicer = a.metrics.feature_s;
  const tot = aa.bambu_total_s ?? aa.total_s;
  const fs = aa.bambu_time_by_feature || fa;  // added up as Bambu Studio does, to compare like with like
  const keys = Object.keys(slicer).filter((k) => slicer[k] >= 0.5).sort((x, y) => slicer[y] - slicer[x]);
  $("feature-check").innerHTML = `<table class="table"><tr><th>Feature</th><th>Bambu Studio</th><th>This tool</th><th>Difference</th></tr>` +
    keys.map((k) => {
      const ours = fs[k] ?? 0;
      const pct = slicer[k] ? (100 * (ours - slicer[k]) / slicer[k]) : 0;
      return `<tr><td>${k}</td><td>${slicer[k].toFixed(1)} s</td><td>${ours.toFixed(1)} s</td><td>${pct >= 0 ? "+" : "−"}${Math.abs(pct).toFixed(2)}%</td></tr>`;
    }).join("") +
    `<tr><td>Total</td><td>${a.metrics.total_s.toFixed(1)} s</td><td>${tot.toFixed(1)} s</td><td>${((d) => `${d >= 0 ? "+" : "−"}${Math.abs(d).toFixed(2)}%`)((100 * (tot - a.metrics.total_s)) / a.metrics.total_s)}</td></tr></table>` +
    `<p class="muted small">Bambu Studio adds these times up in single precision, which loses seconds on plates with many tiny moves, so this table adds them up the same way. The exact total is ${fmtTime(aa.total_s)}; the other views use exact sums.</p>`;
}

// ---- tabs and controls --------------------------------------------------------------

function switchTab(tab) {
  state.tab = tab;
  for (const b of document.querySelectorAll(".tabs button")) b.setAttribute("aria-selected", String(b.dataset.tab === tab));
  for (const s of document.querySelectorAll(".tab")) s.hidden = s.id !== `tab-${tab}`;
  refreshView();
}

export function registerTab(name, label, render, { first = false } = {}) {
  tabs.set(name, render);
  const b = document.createElement("button");
  b.setAttribute("role", "tab");
  b.dataset.tab = name;
  b.textContent = label;
  b.setAttribute("aria-selected", "false");
  b.onclick = () => switchTab(name);
  const nav = document.querySelector(".tabs");
  if (first) nav.prepend(b); else nav.appendChild(b);
  const section = document.createElement("section");
  section.id = `tab-${name}`;
  section.className = "tab";
  section.hidden = true;
  document.querySelector(".main").appendChild(section);
  return section;
}

for (const b of document.querySelectorAll(".tabs button")) b.onclick = () => switchTab(b.dataset.tab);
$("layer").addEventListener("input", (e) => { state.layer = +e.target.value; refreshView(); });
$("color-mode").addEventListener("change", (e) => { state.colorBy = e.target.value; refreshView(); });
$("show-travel").addEventListener("change", (e) => { state.showTravel = e.target.checked; refreshView(); });
$("overlay").addEventListener("change", (e) => { state.overlay = e.target.checked; refreshView(); });
$("show-seams").addEventListener("change", (e) => { state.showSeams = e.target.checked; refreshView(); });
$("first-layer").addEventListener("click", () => {
  state.layer = 1;
  state.colorBy = "speed";
  $("color-mode").value = "speed";
  refreshView();
});
$("fit").addEventListener("click", () => { state.fitted = null; refreshView(); });
document.addEventListener("keydown", (e) => {
  if (state.tab !== "layers" || e.target.matches("input[type=text], select")) return;
  const slider = $("layer");
  if (e.key === "ArrowUp" || e.key === "PageUp") { state.layer = Math.min(+slider.max, (state.layer || 0) + 1); refreshView(); e.preventDefault(); }
  if (e.key === "ArrowDown" || e.key === "PageDown") { state.layer = Math.max(0, (state.layer || 0) - 1); refreshView(); e.preventDefault(); }
});

// ---- try a change -------------------------------------------------------------------
// Settings are picked from Bambu Studio's Process tab arrangement (settingpicker.js);
// the value field follows the setting: a list for choices, on/off, or a number with its unit.

let settingsData = null;
async function loadSettings() {
  if (!settingsData || settingsData.plate !== state.plate) {
    settingsData = { ...(await api(`settings?plate=${state.plate}`)), plate: state.plate };
  }
  return settingsData;
}

function sourceText(src) {
  return { "print-wide": "for the whole project", object: "set on this plate's objects", part: "set on a part", varies: "differs between objects" }[src] || src;
}

function setRowSetting(row, s) {
  row.dataset.key = s.key;
  row.querySelector(".ps-name").textContent = nameOf(s);
  row.querySelector(".ps-path").textContent = `${s.page} › ${s.group}`;
  const wrap = row.querySelector(".v-wrap");
  const now = s.now;
  if (s.choices || s.type === "coBool" || s.type === "coBools") {
    const opts = s.choices || [["1", "On"], ["0", "Off"]];
    wrap.innerHTML = `<select class="v" aria-label="New value">` +
      opts.map(([v, l]) => `<option value="${escapeHtml(v)}" ${v === now ? "selected" : ""}>${escapeHtml(l)}${v === now ? " (now)" : ""}</option>`).join("") + `</select>`;
  } else {
    const shown = now == null ? "" : String(now).replace(/%$/, s.unit === "%" ? "" : "%");
    wrap.innerHTML = `<input type="text" class="v" aria-label="New value" inputmode="decimal" placeholder="${escapeHtml(shown)}">` +
      (s.unit ? `<span class="unit">${escapeHtml(s.unit)}</span>` : "");
  }
  row.querySelector(".now").textContent = `Now ${valueText(s)}, ${sourceText(s.source)}` +
    (s.default != null && s.default !== now ? `. Bambu's default: ${valueText(s, s.default)}` : "");
  row.querySelector(".now").title = s.tooltip || "";
}

function addTryRow() {
  const row = document.createElement("div");
  row.className = "try-row";
  row.innerHTML = `
    <div class="field"><span>Setting</span>
      <button type="button" class="pick-setting" aria-haspopup="dialog" aria-expanded="false">
        <span class="ps-name">Choose a setting</span><span class="ps-path"></span></button></div>
    <div class="field"><span>New value</span><span class="v-wrap"><input type="text" class="v" aria-label="New value" disabled></span></div>
    <button type="button" class="icon" aria-label="Remove this setting">✕</button>
    <div class="now"></div>`;
  const btn = row.querySelector(".pick-setting");
  btn.onclick = async () => {
    try {
      const data = await loadSettings();
      openPicker(btn, data, row.dataset.key, (s) => { setRowSetting(row, s); row.querySelector(".v").focus(); });
    } catch (e) {
      $("try-error").textContent = e.message;
    }
  };
  row.querySelector(".icon").onclick = () => { row.remove(); if (!$("try-rows").children.length) addTryRow(); };
  $("try-rows").appendChild(row);
}

$("try-add").onclick = () => addTryRow();
$("try-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const err = $("try-error");
  err.textContent = "";
  const changes = [];
  for (const row of $("try-rows").children) {
    const key = row.dataset.key || "";
    const value = (row.querySelector(".v").value || "").trim();
    if (!key && !value) continue;
    if (!key) { err.textContent = "Choose a setting for each row, or remove the row."; return; }
    if (!value) { err.textContent = `Give ${row.querySelector(".ps-name").textContent} a new value.`; return; }
    changes.push({ key, value, object_id: null });
  }
  if (!changes.length) { err.textContent = "Add a setting to change first."; return; }
  const btn = $("try-submit");
  btn.disabled = true;
  try {
    const run = await api("runs", { method: "POST", body: { plate: state.plate, changes, scope: $("try-scope").value } });
    state.B = run.id;
    await poll();
    refreshView();
  } catch (ex) {
    err.textContent = ex.message;
  } finally {
    btn.disabled = false;
  }
});

// ---- start --------------------------------------------------------------------------

async function start() {
  if (!PID) { location.replace("/"); return; }
  state.project = await api("project");
  $("project-name").textContent = state.project.name;
  $("project-meta").textContent = `${state.project.printer}, ${state.project.process}. Saved by Bambu Studio ${state.project.studio_version}; slicing with ${state.project.bambu_studio}.`;
  document.title = `${state.project.name} – slicer optimizer`;
  $("quit").onclick = () => quitApp(state.waiting || 0);
  enableDrop((msg) => window.alert(msg));
  addTryRow();
  await poll();
  selectPlate(state.project.plates[0].index);
  setInterval(poll, 2000);
  window.slicerApp = { state, api, registerTab, refreshView, runById, analysisOf, layerOf, switchTab, poll, saveRun, renderRuns, summaryHooks };
  window.dispatchEvent(new CustomEvent("slicer:ready", { detail: window.slicerApp }));
}

start().catch((e) => {
  $("project-name").textContent = "Couldn't open this project";
  $("project-meta").textContent = `${e.message}. Go to All projects to open it again.`;
  $("quit").onclick = () => quitApp(0);
});
