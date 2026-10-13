// The Optimize tab: say what should improve, the tool picks the settings, slices
// them for real (each alone, then combinations of what helped) and recommends one.
import { fmtTime, fmtDelta } from "./charts.js";
import { escapeHtml as esc } from "./opener.js";

const LEVELS = [["Off", 0], ["A little", 1], ["Some", 3], ["A lot", 6]];
const BUDGETS = [["About 10 minutes", 600], ["About 30 minutes", 1800], ["About an hour", 3600], ["About 3 hours", 10800]];
const ORDER = ["time_s", "grams", "stiffness", "cost", "near_cap_s"];
const BEST = { time_s: "Fastest", grams: "Lightest", stiffness: "Strongest", cost: "Cheapest", near_cap_s: "Most flow headroom" };

const ui = {
  goals: null, plate: null, list: [], selected: null, shown: null,
  weights: { time_s: 3, grams: 3, stiffness: 3, cost: 0, near_cap_s: 0 },
  limits: { strength_on: true, strength_pct: 90, max_time: "", max_grams: "" },
  allowed: null, budget_s: 1800, preview: null, previewTimer: null,
};

const $ = (id) => document.getElementById(id);

function value(goal, v) {
  if (v == null) return "–";
  if (goal === "time_s" || goal === "near_cap_s") return fmtTime(v);
  if (goal === "grams") return `${v.toFixed(2)} g`;
  if (goal === "stiffness") return `${v.toFixed(1)}%`;
  if (goal === "cost") return v.toFixed(2);
  return String(v);
}

function delta(goal, d) {
  const better = ui.goals[goal]?.better || "lower";
  const shown = { time_s: 0.5, near_cap_s: 0.5, grams: 0.005, cost: 0.005, stiffness: 0.05 }[goal] ?? 0.005;
  if (d == null || Math.abs(d) < shown) return { text: "same", cls: "" };  // below what's displayed
  const good = better === "higher" ? d > 0 : d < 0;
  const sign = d < 0 ? "−" : "+";
  const text = goal === "time_s" || goal === "near_cap_s" ? fmtDelta(d)
    : goal === "grams" ? `${sign}${Math.abs(d).toFixed(2)} g`
    : goal === "stiffness" ? `${sign}${Math.abs(d).toFixed(1)} pt` : `${sign}${Math.abs(d).toFixed(2)}`;
  return { text, cls: good ? "good" : "bad" };
}

function pct(d, base) {
  if (d == null || !base) return "";
  return ` (${d < 0 ? "−" : "+"}${Math.abs((100 * d) / base).toFixed(0)}%)`;
}

function parseHM(text) {
  const m = String(text).trim().match(/^(\d+)\s*h\s*(\d+)?\s*m?$|^(\d+):(\d{1,2})$|^(\d+)\s*m(in)?$/i);
  if (!m) return null;
  if (m[1] != null) return (+m[1]) * 3600 + (+(m[2] || 0)) * 60;
  if (m[3] != null) return (+m[3]) * 3600 + (+m[4]) * 60;
  return (+m[5]) * 60;
}

const baseline = (app) => app.state.runs.find((r) => r.plate === app.state.plate && r.kind === "baseline");
const activeWeights = () => Object.fromEntries(Object.entries(ui.weights).filter(([, w]) => w > 0));

function limitsBody() {
  return {
    strength_pct: ui.limits.strength_on && ui.limits.strength_pct !== "" ? +ui.limits.strength_pct : null,
    max_time_s: parseHM(ui.limits.max_time),
    max_grams: ui.limits.max_grams === "" ? null : +ui.limits.max_grams,
  };
}

// ---- layout -------------------------------------------------------------------------------

function start(app) {
  const section = app.registerTab("optimize", "Optimize", () => render(app), { first: true });
  section.innerHTML = `
    <div class="opt">
      <form class="opt-form" id="opt-form" autocomplete="off">
        <h2>What should improve?</h2>
        <p class="muted small">Importance is relative: what counts is how the goals compare. Everything on "A lot" is the same as everything on "A little"; raise one goal above the others to favor it.</p>
        <div id="opt-goals" class="opt-goals"></div>
        <h3>Limits</h3>
        <div class="opt-limits">
          <label class="check"><input type="checkbox" id="opt-strength-on"> Keep strength at least
            <input type="text" id="opt-strength" class="num" inputmode="decimal" aria-label="Strength floor, percent of now"> % of now</label>
          <label class="field inline"><span>Finish within</span><input type="text" id="opt-maxtime" class="num" placeholder="2h30m"></label>
          <label class="field inline"><span>No heavier than</span><input type="text" id="opt-maxgrams" class="num" inputmode="decimal" placeholder="g"> g</label>
        </div>
        <details class="opt-allowed" id="opt-allowed-box">
          <summary id="opt-allowed-sum">Settings it may change</summary>
          <div id="opt-allowed"></div>
        </details>
        <label class="field"><span>Time to spend slicing</span><select id="opt-budget"></select></label>
        <p id="opt-plan" class="muted small"></p>
        <div class="d-actions">
          <button type="submit" class="primary" id="opt-go">Find the best settings</button>
        </div>
        <p id="opt-error" class="error" role="alert"></p>
        <div id="opt-history"></div>
      </form>
      <div class="opt-result" id="opt-result"></div>
    </div>`;
  $("opt-budget").innerHTML = BUDGETS.map(([l, s]) => `<option value="${s}">${l}</option>`).join("");
  $("opt-budget").value = String(ui.budget_s);
  $("opt-budget").onchange = (e) => { ui.budget_s = +e.target.value; schedulePreview(app); };
  $("opt-strength-on").onchange = (e) => { ui.limits.strength_on = e.target.checked; onPriorities(app); };
  $("opt-strength").oninput = (e) => { ui.limits.strength_pct = e.target.value; onPriorities(app); };
  $("opt-maxtime").oninput = (e) => { ui.limits.max_time = e.target.value; onPriorities(app); };
  $("opt-maxgrams").oninput = (e) => { ui.limits.max_grams = e.target.value; onPriorities(app); };
  $("opt-form").addEventListener("submit", (e) => { e.preventDefault(); create(app); });
  setInterval(() => { if (app.state.tab === "optimize") refresh(app); }, 2000);
  app.switchTab("optimize");
}

async function render(app) {
  if (!ui.goals) {
    const data = await app.api("optimize");
    ui.goals = data.goals;
  }
  if (ui.plate !== app.state.plate) {
    ui.plate = app.state.plate;
    ui.selected = null;
    ui.allowed = null;
    ui.preview = null;
    $("opt-result").innerHTML = "";
  }
  $("opt-strength-on").checked = ui.limits.strength_on;
  $("opt-strength").value = ui.limits.strength_pct;
  renderGoals(app);
  schedulePreview(app, 0);
  await refresh(app);
}

function renderGoals(app) {
  const base = baseline(app);
  const bg = base && base.goals;
  $("opt-goals").innerHTML = ORDER.filter((g) => ui.goals[g]).map((g) => {
    const meta = ui.goals[g];
    const unavailable = bg && bg[g] == null;
    const proxy = meta.source === "proxy" ? `<span class="tag" title="${esc(meta.explain || "")}">proxy</span>` : "";
    const now = bg ? `now ${value(g, bg[g])}` : "";
    const seg = LEVELS.map(([l, w]) => `<button type="button" role="radio" aria-checked="${ui.weights[g] === w}" data-goal="${g}" data-w="${w}" ${unavailable ? "disabled" : ""}>${l}</button>`).join("");
    return `<div class="opt-goal" title="${esc(meta.explain || "")}">
      <div class="opt-goal-name"><strong>${esc(meta.want)}</strong> ${proxy}<span class="muted small">${unavailable ? "can't be measured on this part" : esc(now)}</span></div>
      <div class="seg" role="radiogroup" aria-label="${esc(meta.want)}">${seg}</div></div>`;
  }).join("");
  for (const b of $("opt-goals").querySelectorAll("button[data-goal]")) b.onclick = () => {
    ui.weights[b.dataset.goal] = +b.dataset.w;
    renderGoals(app);
    onPriorities(app);
  };
}

// ---- what it would try ------------------------------------------------------------------

function schedulePreview(app, wait = 250) {
  clearTimeout(ui.previewTimer);
  ui.previewTimer = setTimeout(() => preview(app), wait);
}

async function preview(app) {
  const q = new URLSearchParams({ plate: app.state.plate, weights: JSON.stringify(activeWeights()), budget_s: ui.budget_s });
  if (ui.allowed) q.set("allowed", JSON.stringify([...ui.allowed]));
  try {
    ui.preview = await app.api(`optimize/preview?${q}`);
  } catch (e) {
    $("opt-plan").textContent = e.message;
    return;
  }
  const p = ui.preview;
  if (!ui.allowed) ui.allowed = new Set(p.catalog.filter((k) => !k.off_by_default).map((k) => k.key));
  renderAllowed(app);
  const per = p.per_slice_s;
  if (!Object.keys(activeWeights()).length) $("opt-plan").textContent = "Choose at least one goal.";
  else if (!p.ready) $("opt-plan").textContent = p.message;
  else if (!p.knobs.length) $("opt-plan").textContent = "None of the settings it may change can move these goals on this plate.";
  else $("opt-plan").textContent = `It will measure ${p.knobs.length} setting${p.knobs.length === 1 ? "" : "s"} alone (${p.measure_slices} slices), ` +
    `then combine what helps, up to ${p.budget_slices} slices in all` + (per ? `, about ${fmtTime(per)} each` : "") +
    `. Slices it has already made cost nothing.` + (p.notes.length ? ` ${p.notes.join(" ")}` : "");
}

function renderAllowed(app) {
  const p = ui.preview;
  const skipped = Object.fromEntries((p.skipped || []).map((s) => [s.key, s.why]));
  const tried = new Set((p.knobs || []).map((k) => k.key));
  $("opt-allowed-sum").textContent = `Settings it may change (${p.ready ? tried.size : ui.allowed.size} of ${p.catalog.length})`;
  $("opt-allowed").innerHTML = p.catalog.map((k) => {
    const why = skipped[k.key];
    const inapplicable = why && ui.allowed.has(k.key) && !tried.has(k.key);
    return `<label class="check opt-knob ${inapplicable ? "muted" : ""}" title="${esc(k.explain || "")}">
      <input type="checkbox" data-key="${k.key}" ${ui.allowed.has(k.key) ? "checked" : ""}>
      <span>${esc(k.label)}${k.scope === "print-wide" ? " (print-wide)" : ""}
      <span class="muted small">now ${esc(k.current ?? "varies")}${inapplicable ? `; ${esc(why)}` : ""}${!ui.allowed.has(k.key) && k.off_by_default ? `; ${esc(k.off_by_default)}` : ""}</span></span></label>`;
  }).join("");
  for (const cb of $("opt-allowed").querySelectorAll("input[type=checkbox]")) cb.onchange = () => {
    cb.checked ? ui.allowed.add(cb.dataset.key) : ui.allowed.delete(cb.dataset.key);
    schedulePreview(app);
  };
}

// ---- runs of the optimizer ----------------------------------------------------------------

async function create(app) {
  $("opt-error").textContent = "";
  if (!Object.keys(activeWeights()).length) { $("opt-error").textContent = "Choose at least one goal."; return; }
  if (ui.limits.max_time && parseHM(ui.limits.max_time) == null) { $("opt-error").textContent = "Write the time limit like 2h30m."; return; }
  $("opt-go").disabled = true;
  try {
    const o = await app.api("optimize", { method: "POST", body: {
      plate: app.state.plate, weights: ui.weights, limits: limitsBody(), allowed: [...ui.allowed], budget_s: ui.budget_s } });
    ui.selected = o.id;
    ui.rerankNote = "";
    await refresh(app);
  } catch (e) {
    $("opt-error").textContent = e.message;
  } finally {
    $("opt-go").disabled = false;
  }
}

function onPriorities(app) {
  schedulePreview(app);
  const o = ui.shown;
  const differs = o && (JSON.stringify(o.weights) !== JSON.stringify(normWeights()) ||
    JSON.stringify(normLimits(o.limits)) !== JSON.stringify(normLimits(limitsBody())));
  if (!differs || !Object.keys(activeWeights()).length) return;
  if (ui.limits.max_time && parseHM(ui.limits.max_time) == null) return;  // still typing
  clearTimeout(ui.rerankTimer);
  ui.rerankTimer = setTimeout(() => rerank(app), 350);
}

// limits without the empty ones, in a fixed order, so equal limits compare equal
const normLimits = (l) => Object.fromEntries(["strength_pct", "max_time_s", "max_grams"]
  .filter((k) => l && l[k] != null && l[k] !== "").map((k) => [k, +l[k]]));
const normWeights = () => Object.fromEntries(Object.keys(ui.goals).map((g) => [g, +(ui.weights[g] || 0)]));

// Re-rank what was already sliced for the new priorities; nothing is sliced again.
async function rerank(app) {
  if (!ui.shown) return;
  const before = ui.shown.pick && ui.shown.pick.run_id;
  ui.shown = await app.api(`optimize/${ui.shown.id}/priorities`, { method: "POST", body: { weights: ui.weights, limits: limitsBody() } });
  ui.altPick = null;
  const after = ui.shown.pick && ui.shown.pick.run_id;
  const n = ui.shown.points.length;
  ui.rerankNote = !after ? "" : after === before
    ? `Same recommendation: of the ${n} results already sliced, it's still the closest to these priorities.`
    : `Recommendation updated for these priorities, from the ${n} results already sliced.`;
  ui.resultSig = null;
  renderResult(app);
}

// The form shows the priorities of the optimization on screen, so a change re-ranks from there.
function loadForm(app, o) {
  ui.weights = { ...ui.weights, ...o.weights };
  const l = o.limits || {};
  ui.limits = {
    strength_on: l.strength_pct != null, strength_pct: l.strength_pct ?? ui.limits.strength_pct,
    max_time: l.max_time_s ? `${Math.floor(l.max_time_s / 3600)}h${String(Math.round((l.max_time_s % 3600) / 60)).padStart(2, "0")}m` : "",
    max_grams: l.max_grams ?? "",
  };
  $("opt-strength-on").checked = ui.limits.strength_on;
  $("opt-strength").value = ui.limits.strength_pct;
  $("opt-maxtime").value = ui.limits.max_time;
  $("opt-maxgrams").value = ui.limits.max_grams;
  renderGoals(app);
}

async function refresh(app) {
  let data;
  try {
    data = await app.api(`optimize?plate=${app.state.plate}`);
  } catch (e) {
    return;
  }
  ui.list = data.optimizations;
  if (!ui.selected || !ui.list.find((o) => o.id === ui.selected)) ui.selected = ui.list[0]?.id || null;
  ui.shown = ui.list.find((o) => o.id === ui.selected) || null;
  if (ui.shown && ui.shown.id !== ui.formFor) {
    ui.formFor = ui.shown.id;
    ui.rerankNote = "";
    loadForm(app, ui.shown);
  }
  renderHistory(app);
  renderResult(app);
}

function renderHistory(app) {
  const box = $("opt-history");
  if (ui.list.length < 2) { box.innerHTML = ""; return; }
  box.innerHTML = `<h3>Earlier on this plate</h3><ol class="d-searches">` + ui.list.map((o) => {
    const goals = ORDER.filter((g) => o.weights[g] > 0).map((g) => ui.goals[g].want.toLowerCase()).join(", ");
    const when = new Date(o.created * 1000).toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
    return `<li class="d-search ${o.id === ui.selected ? "on" : ""}"><button type="button" class="link" data-id="${o.id}">${esc(goals || "no goals")}</button>
      <div class="muted small">${when}, ${o.status === "done" ? `${o.counts.done} slices` : esc(o.status)}</div></li>`;
  }).join("") + `</ol>`;
  for (const b of box.querySelectorAll("button[data-id]")) b.onclick = () => { ui.selected = b.dataset.id; refresh(app); };
}

// ---- results ------------------------------------------------------------------------------

function renderResult(app) {
  const box = $("opt-result");
  const o = ui.shown;
  const sig = JSON.stringify([o, ui.altPick, app.state.plate, ui.rerankNote]);
  if (sig === ui.resultSig) return;  // polling: redraw only when something changed
  ui.resultSig = sig;
  if (!o) { box.innerHTML = emptyState(app); return; }
  const chosen = ORDER.filter((g) => o.weights[g] > 0);
  let html = progress(o);
  if (o.error) html += `<p class="error">${esc(o.error)}</p>`;
  if (ui.rerankNote) html += `<p class="opt-rerank-note" role="status">${esc(ui.rerankNote)}</p>`;
  if (o.pick) html += recommendation(o, o.pick, chosen);
  else if (o.status === "done" && o.counts.done) html += `<p>No sliced candidate meets your limits. Loosen a limit and re-rank, or start again with more settings.</p>`;
  html += alternatives(o, chosen);
  html += effectsTable(o, chosen);
  html += notTried(o);
  box.innerHTML = html;
  const skippedBox = box.querySelector(".opt-skipped");
  if (skippedBox) skippedBox.ontoggle = () => { ui.skippedOpen = skippedBox.open; };
  for (const b of box.querySelectorAll("[data-alt]")) b.onclick = () => { ui.altPick = b.dataset.alt || null; renderResult(app); };
  const shownRun = currentPick(o);
  const cmp = box.querySelector("#opt-compare"), save = box.querySelector("#opt-save");
  if (cmp && shownRun) cmp.onclick = () => compare(app, shownRun.run_id);
  if (save && shownRun) save.onclick = async () => {
    await app.poll();
    const run = app.state.runs.find((r) => r.id === shownRun.run_id) || { id: shownRun.run_id, label: shownRun.label };
    await app.saveRun(run);
  };
  for (const b of box.querySelectorAll("[data-act]")) b.onclick = async () => {
    await app.api(`optimize/${o.id}/${b.dataset.act}`, { method: b.dataset.act === "delete" ? "DELETE" : "POST" });
    if (b.dataset.act === "delete") ui.selected = null;
    refresh(app);
  };
  onPriorities(app);
}

function emptyState(app) {
  const base = baseline(app);
  const plate = app.state.project.plates.find((p) => p.index === app.state.plate);
  const pic = plate && plate.thumbnail ? `<img alt="" src="/api/p/${location.pathname.split("/")[2]}/thumbnail/${plate.index}">` : "";
  return `<div class="opt-empty">
    <div class="proj-plate opt-plate">${pic}</div>
    <div>
      <h2>Let the slicer find settings for your goals</h2>
      <ol class="opt-steps">
        <li>It slices this plate with each relevant setting changed on its own, to see what actually moves your goals here.</li>
        <li>It combines the settings that helped, as many combinations as the time you give it allows.</li>
        <li>It recommends the result closest to your priorities, within your limits, and shows the best one for each goal.</li>
      </ol>
      <p class="muted small">Every number is from a real slice with Bambu Studio${base && base.metrics ? `; this plate slices in about ${fmtTime(base.metrics.wall_s)}` : ""}. Strength is a proxy for flat parts and surface quality isn't measured, so print a test piece before a batch.</p>
    </div></div>`;
}

function progress(o) {
  const steps = [
    ["Measure each setting alone", "measure"],
    ["Combine what helped", "combine"],
  ];
  const order = ["baseline", "measure", "combine", "done"];
  const at = order.indexOf(o.stage);
  const rows = steps.map(([label, name], i) => {
    const st = o.stages.find((s) => s.name === name);
    const state = at > i + 1 || o.stage === "done" ? "done" : at === i + 1 ? "now" : "later";
    let detail = "";
    if (st) {
      const left = st.counts.queued + st.counts.active;
      detail = `${st.counts.done} of ${st.total} sliced` + (left && o.status !== "stopped" ? `, about ${st.eta_s ? fmtTime(st.eta_s) : "a moment"} to go` : "") +
        (st.counts.error ? `, ${st.counts.error} failed` : "");
    } else if (name === "combine" && o.stage === "done") detail = o.combine_knobs && o.combine_knobs.length ? "" : "not needed: fewer than two settings helped, or no budget left";
    else if (o.stage === "baseline" && i === 0) detail = "waiting for the current settings to be sliced";
    const w = st ? Math.round((100 * st.counts.done) / Math.max(1, st.total)) : state === "done" ? 100 : 0;
    return `<li class="opt-step ${state}"><span>${label}</span><span class="bar"><span style="width:${w}%"></span></span><span class="muted small">${detail}</span></li>`;
  }).join("");
  const running = o.status !== "done" && o.status !== "stopped";
  const actions = running ? `<button type="button" class="link" data-act="stop">Stop</button>`
    : o.status === "stopped" ? `<button type="button" class="link" data-act="continue">Continue</button>` : "";
  return `<div class="opt-progress"><ol>${rows}</ol><div class="run-actions">${actions}<button type="button" class="link" data-act="delete">Delete</button></div>
    ${o.notes.length ? `<p class="muted small">${o.notes.map(esc).join(" ")}</p>` : ""}</div>`;
}

function currentPick(o) {
  if (ui.altPick) {
    const alt = Object.values(o.best_for).find((b) => b && b.run_id === ui.altPick);
    if (alt) return alt;
    ui.altPick = null;
  }
  return o.pick;
}

function recommendation(o, _pick, chosen) {
  const p = currentPick(o);
  const isPick = !ui.altPick;
  const goals = [...new Set([...chosen, "time_s", "grams"])].filter((g) => p.goals[g] != null);
  const rows = goals.map((g) => {
    const d = p.deltas[g];
    const dd = delta(g, d);
    return `<div class="opt-stat"><div class="label">${esc(ui.goals[g].label)}</div>
      <div class="value"><span class="muted">${value(g, o.base_goals[g])}</span> → ${value(g, p.goals[g])}</div>
      <div class="delta ${dd.cls}">${dd.text}${dd.text !== "same" && g !== "stiffness" ? pct(d, o.base_goals[g]) : ""}</div></div>`;
  }).join("");
  const changes = p.changes.length ? `<ul class="opt-changes">` + p.changes.map((c) => {
    const alone = c.alone ? goals.map((g) => (c.alone[g] != null ? delta(g, c.alone[g]) : null)).filter((x) => x && x.text !== "same").map((x) => `<span class="${x.cls}">${x.text}</span>`).join(", ") : "";
    return `<li><strong>${esc(c.label)}</strong> ${esc(c.from ?? "?")} → <strong>${esc(c.to)}</strong>${c.scope === "print-wide" ? ' <span class="tag">print-wide</span>' : ""}
      ${alone ? `<span class="small">alone: ${alone}</span>` : ""}
      <div class="muted small">${esc(c.explain || "")}${c.note ? " " + esc(c.note) : ""}</div></li>`;
  }).join("") + `</ul>` : `<p>Your current settings are already the best match among what was sliced.</p>`;
  const altGoal = isPick ? null : Object.keys(o.best_for).find((g) => o.best_for[g] && o.best_for[g].run_id === p.run_id);
  return `<section class="opt-card">
    <h2>${isPick ? (o.status === "done" ? "Recommended" : "Best so far") : esc(BEST[altGoal] || "Alternative")} for plate ${o.plate}</h2>
    <div class="opt-stats">${rows}</div>
    ${changes}
    ${p.changes.length ? `<div class="d-actions">
      <button type="button" class="primary" id="opt-compare">Compare with current, layer by layer</button>
      <button type="button" class="ghost" id="opt-save">Save as .3mf</button></div>` : ""}
    <p class="muted small">Measured by slicing. Strength is a flat-part proxy, and faster bridges or overhangs, fewer shells and other changes can affect surface quality, which isn't measured: print a test piece first.</p>
  </section>`;
}

function alternatives(o, chosen) {
  const entries = chosen.map((g) => [g, o.best_for[g]]).filter(([, b]) => b);
  if (!o.pick || !entries.length) return "";
  const shown = [...new Set(["time_s", "grams", ...chosen])];  // your goals, plus time and weight
  const chip = (label, b, id) => {
    const on = ui.altPick === id;
    const stats = shown.filter((g) => b.goals[g] != null).map((g) => value(g, b.goals[g])).join(", ");
    return `<button type="button" class="opt-alt ${on ? "on" : ""}" data-alt="${id}"><strong>${label}</strong><span class="small">${stats}</span></button>`;
  };
  return `<div class="opt-alts"><span class="muted small">Other good points</span>` +
    `<button type="button" class="opt-alt ${!ui.altPick ? "on" : ""}" data-alt=""><strong>Recommended</strong><span class="small">${shown.filter((g) => o.pick.goals[g] != null).map((g) => value(g, o.pick.goals[g])).join(", ")}</span></button>` +
    entries.map(([g, b]) => chip(BEST[g], b, b.run_id)).join("") + `</div>`;
}

function effectsTable(o, chosen) {
  const rows = o.effects.filter((e) => e.values.some((v) => v.measured));
  if (!rows.length) return "";
  const goals = [...new Set(["time_s", ...chosen])].filter((g) => o.base_goals[g] != null);
  const head = `<tr><th>Setting</th><th>Value</th>${goals.map((g) => `<th>${esc(ui.goals[g].label.replace(/ \(.*\)$/, ""))}</th>`).join("")}</tr>`;
  const body = rows.map((e) => e.values.filter((v) => v.measured).map((v, i, arr) => {
    const cells = goals.map((g) => {
      const dd = delta(g, v.deltas[g]);
      const small = Math.abs(v.deltas[g] || 0) <= (o.tolerance[g] || 0) && dd.text !== "same";
      return `<td class="${small ? "muted" : dd.cls}">${small ? `${dd.text} (too small)` : dd.text}</td>`;
    }).join("");
    return `<tr class="${e.matters ? "" : "muted"}">${i === 0 ? `<td class="opt-k" rowspan="${arr.length}" title="${esc(e.explain || "")}">${esc(e.label)}<div class="muted small">now ${esc(e.current ?? "?")}</div></td>` : ""}<td class="opt-v">${esc(v.value)}</td>${cells}</tr>`;
  }).join("")).join("");
  return `<h3>What each setting did on its own</h3>
    <p class="muted small">Change against the current settings on this plate. Changes smaller than the slicer's repeat noise or 0.2% of the current value (${fmtTime(o.tolerance.time_s || 0)} of print time here) count as no change; greyed settings didn't help any goal you chose.</p>
    <div class="chart"><table class="table opt-table">${head}${body}</table></div>`;
}

function notTried(o) {
  if (!o.skipped || !o.skipped.length) return "";
  return `<details class="opt-skipped" ${ui.skippedOpen ? "open" : ""}><summary class="muted small">Not tried (${o.skipped.length})</summary><ul class="small">` +
    o.skipped.map((s) => `<li>${esc(s.label)}: <span class="muted">${esc(s.why)}</span></li>`).join("") + `</ul></details>`;
}

async function compare(app, runId) {
  await app.api(`runs/${runId}/pin`, { method: "POST", body: { pinned: true } });
  await app.poll();
  const base = baseline(app);
  app.state.A = base ? base.id : app.state.A;
  app.state.B = runId;
  app.renderRuns();
  app.switchTab("layers");
}

if (window.slicerApp) start(window.slicerApp);
else window.addEventListener("slicer:ready", (e) => start(e.detail), { once: true });
