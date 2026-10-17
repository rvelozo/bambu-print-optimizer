// The Overview tab: what this file is and what it's tuned for. The file is sliced as it is and
// with its tuning put back to Bambu's default process for the printer; the two are compared.
import { fmtTime, fmtDelta } from "./charts.js";
import { escapeHtml as esc } from "./opener.js";

const ui = { data: null, plate: null, timer: null, sig: null, keptOpen: false };
const $ = (id) => document.getElementById(id);

const GOALS = {
  time_s: { label: "Print time", noun: "speed", value: (v) => fmtTime(v) },
  grams: { label: "Weight", noun: "less plastic", value: (v) => `${v.toFixed(2)} g` },
  cost: { label: "Filament cost", noun: "lower cost", value: (v) => v.toFixed(2) },
  stiffness: { label: "Strength (flat-part proxy)", noun: "strength", value: (v) => `${v.toFixed(1)}%` },
  quality: { label: "Quality (proxy)", noun: "quality", value: (v) => `${v.toFixed(1)}%` },
};
const ASPECTS = {
  slopes: { label: "Steps on slopes", show: (a) => `${a.step_um.toFixed(0)} µm` },
  walls: { label: "Outer walls", show: (a) => `${a.speed.toFixed(0)} mm/s` },
  air: { label: "Over air (overhangs, bridges)", show: (a) => `${a.speed.toFixed(0)} mm/s` },
  top: { label: "Top surface", show: (a) => `${a.speed.toFixed(0)} mm/s` + (a.solid_layers != null ? `, ${a.solid_layers} solid layers` : "") },
};
const KEY_SETTINGS = [
  ["layer_height", "Layer height"], ["wall_loops", "Wall loops"], ["top_shell_layers", "Top shells"],
  ["bottom_shell_layers", "Bottom shells"], ["sparse_infill_density", "Infill"], ["sparse_infill_pattern", "Infill pattern"],
  ["outer_wall_speed", "Outer wall speed"], ["enable_support", "Supports"], ["brim_type", "Brim"],
];
const SPECIAL = [["spiral_mode", "Spiral vase"], ["fuzzy_skin", "Fuzzy skin"], ["ironing_type", "Ironing"], ["enable_prime_tower", "Prime tower"]];

function start(app) {
  const section = app.registerTab("overview", "Overview", () => render(app), { first: true });
  section.innerHTML = `<div class="ov" id="ov"></div>`;
  setInterval(() => { if (app.state.tab === "overview" && pending()) load(app); }, 2000);
  app.switchTab("overview");
}

function pending() {
  const d = ui.data;
  if (!d) return true;
  const busy = (s) => s === "queued" || s === "slicing" || s === "analysing";
  return busy(d.base.status) || busy(d.ref.status) || d.groups.some((g) => busy(g.status));
}

async function render(app) {
  if (ui.plate !== app.state.plate) { ui.plate = app.state.plate; ui.data = null; ui.sig = null; }
  await load(app);
}

async function load(app) {
  try {
    ui.data = await app.api(`overview?plate=${app.state.plate}`);
  } catch (e) {
    $("ov").innerHTML = `<p class="error">${esc(e.message)}</p>`;
    return;
  }
  draw(app);
}

function draw(app) {
  const d = ui.data;
  const sig = JSON.stringify(d);
  if (sig === ui.sig) return;  // polling: redraw only when something changed
  ui.sig = sig;
  $("ov").innerHTML = recordWarning(d) + verdictCard(d) + aboutFile(d) + differences(d) + kept(d);
  const cmp = $("ov-compare");
  if (cmp) cmp.onclick = () => compare(app, d.comparison.ref_run);
  const go = $("ov-explain");
  if (go) go.onclick = async () => {
    go.disabled = true;
    ui.data = await app.api(`overview/explain?plate=${app.state.plate}`, { method: "POST" });
    draw(app);
  };
  const box = document.querySelector(".ov-kept");
  if (box) box.ontoggle = () => { ui.keptOpen = box.open; };
}

// Bambu Studio records which settings a project changed from its preset; files saved by its
// window carry an exact record. Files made other ways (its command line, other tools) may not.
function recordWarning(d) {
  const n = d.unrecorded.length;
  if (n < 5) return "";
  return `<p class="ov-warn" role="note">${n} settings in this file differ from ${esc(d.process || "its preset")} without being recorded as changes
    (${esc(d.unrecorded.slice(0, 4).join(", "))}${n > 4 ? ", …" : ""}). It was probably not saved by Bambu Studio's window, so they're compared by value below,
    which can also count a setting or two that Bambu Studio itself wouldn't.</p>`;
}

// ---- what it's tuned for --------------------------------------------------------------------

function change(goal, v) {
  if (goal === "stiffness" || goal === "quality") return `${v.is === "same" ? "" : v.change > 0 ? "+" : "−"}${Math.abs(v.change).toFixed(1)} pt`;
  return `${v.change > 0 ? "+" : "−"}${Math.abs(v.change).toFixed(0)}%`;
}

function phrase(goal, v) {
  const pct = Math.abs(v.change).toFixed(0);
  if (v.is === "same") return `${goal === "time_s" ? "print time" : GOALS[goal].label.toLowerCase().replace(/ \(proxy\)/, "")} about the same`;
  if (goal === "time_s") return `${pct}% ${v.change < 0 ? "faster" : "slower"}`;
  if (goal === "grams") return `${pct}% ${v.change < 0 ? "lighter" : "heavier"}`;
  if (goal === "cost") return `${pct}% ${v.change < 0 ? "cheaper" : "dearer"}`;
  if (goal === "quality") return `quality (proxy) ${v.now.toFixed(0)}% of the default's`;
  return `strength (flat-part proxy) ${v.change > 0 ? "+" : "−"}${Math.abs(v.change).toFixed(1)} pt`;
}

function joinWords(words) {
  return words.length < 2 ? words.join("") : `${words.slice(0, -1).join(", ")} and ${words[words.length - 1]}`;
}

function verdictCard(d) {
  const ref = esc(d.reference || "?");
  if (!d.found) return `<section class="opt-card ov-verdict"><h2>No default to compare with</h2>
    <p>Bambu Studio has no default process for ${esc(d.characteristics.printer || "this printer")}, so the file's settings can't be measured against one.</p></section>`;
  if (!d.tuning_changes) return `<section class="opt-card ov-verdict"><h2>Bambu's default settings</h2>
    <p>This file prints with ${ref}, the default for this printer, without changes to how it prints. It isn't tuned for anything in particular.</p></section>`;
  const c = d.comparison;
  if (!c) {
    const st = (s, err) => (s === "done" ? "sliced" : s === "error" ? `failed: ${esc(err || "")}` : s ? `${esc(s)}…` : "waiting…");
    return `<section class="opt-card ov-verdict"><h2>Measuring what it's tuned for</h2>
      <p>Slicing the file as it is, and with its ${d.tuning_changes} tuning change${d.tuning_changes === 1 ? "" : "s"} put back to ${ref}, the default for this printer.</p>
      <ul class="small muted ov-status"><li>This file: ${st(d.base.status)}</li><li>Bambu's default: ${st(d.ref.status, d.ref.error)}</li></ul></section>`;
  }
  const v = c.verdict;
  const order = Object.keys(GOALS).filter((g) => v[g]);
  const size = (g) => (g === "stiffness" || g === "quality" ? Math.abs(v[g].change) : Math.abs(v[g].change));
  const better = order.filter((g) => v[g].is === "better").sort((a, b) => size(b) - size(a));
  const worse = order.filter((g) => v[g].is === "worse").sort((a, b) => size(b) - size(a));
  const same = order.filter((g) => v[g].is === "same");
  // strength is a flat-part proxy: reported, but not enough to call a file "tuned for strength"
  // cost follows weight unless filaments differ in price: say "less plastic" once
  const titled = better.filter((g) => g !== "stiffness" && !(g === "cost" && better.includes("grams")));
  const title = titled.length ? `Tuned for ${joinWords(titled.map((g) => GOALS[g].noun))}`
    : worse.length || better.length ? "Changed from the default, with no clear gain" : "Prints much like Bambu's default";
  const parts = [...better, ...worse, ...same].filter((g) => g !== "cost" || v[g].is !== "same").map((g) => phrase(g, v[g]));
  const stats = order.map((g) => `<div class="opt-stat"><div class="label">${GOALS[g].label}</div>
      <div class="value">${GOALS[g].value(v[g].now)} <span class="muted ov-vs">vs ${GOALS[g].value(v[g].default)}</span></div>
      <div class="delta ${v[g].is === "better" ? "good" : v[g].is === "worse" ? "bad" : ""}">${v[g].is === "same" ? "about the same" : change(g, v[g])}</div></div>`).join("");
  return `<section class="opt-card ov-verdict">
    <h2>${title}</h2>
    <p>Against ${ref}, Bambu's default for this printer, with the same model, supports and filament: ${esc(joinWords(parts))}.</p>
    <div class="opt-stats">${stats}</div>
    ${qualityTable(c.quality)}
    <div class="d-actions"><button type="button" class="primary" id="ov-compare">Compare with the default, layer by layer</button></div>
    <p class="muted small">Both measured by slicing. Within 2% (or 2 points for the proxies) counts as about the same. Strength and quality are proxies worked out from the toolpaths.</p>
  </section>`;
}

function qualityTable(q) {
  if (!q || !q.aspects) return "";
  const rows = Object.entries(q.aspects).filter(([k]) => ASPECTS[k]).sort((a, b) => b[1].weight - a[1].weight).map(([k, a]) => {
    const meta = ASPECTS[k];
    const now = meta.show(a.then), def = meta.show(a.now);  // the file is "then" against the default
    const dd = a.score - 100;
    return `<tr><td>${esc(meta.label)}</td><td class="num">${(100 * a.weight).toFixed(0)}%</td><td>${esc(now)}</td><td>${now === def ? '<span class="muted">same</span>' : esc(def)}</td>
      <td class="num ${Math.abs(dd) < 0.5 ? "" : dd > 0 ? "good" : "bad"}">${Math.abs(dd) < 0.5 ? "same" : `${a.score.toFixed(0)}%`}</td></tr>`;
  }).join("");
  return `<details class="opt-quality"><summary class="small">Quality aspects</summary>
    <table class="table opt-qtable"><tr><th>Aspect</th><th class="num">Counts</th><th>This file</th><th>Default</th><th class="num">vs default</th></tr>${rows}</table></details>`;
}

// ---- what the file is -----------------------------------------------------------------------

function aboutFile(d) {
  const ch = d.characteristics;
  const g = d.base.goals;
  const fil = ch.filaments.map((f) => `<span class="ov-fil"><span class="ov-swatch" style="background:${esc(f.colour || "transparent")}"></span>${esc(f.name || `slot ${f.slot}`)}</span>`).join(" ");
  const objs = ch.objects.map((o) => `${esc(o.name)}${o.copies > 1 ? ` ×${o.copies}` : ""}${o.modifiers ? ` <span class="muted">(${o.modifiers} part${o.modifiers === 1 ? "" : "s"} with own settings)</span>` : ""}`).join(", ");
  const preset = ch.process === ch.default_process ? `${esc(ch.process)} <span class="muted">(the printer's default)</span>`
    : `${esc(ch.process || "?")} <span class="muted">(the printer's default is ${esc(ch.default_process || "?")})</span>`;
  const off = (v) => !v || /^(off|none|no ironing|disable)/i.test(v);
  const special = SPECIAL.filter(([k]) => !off(ch.settings[k]) && (k !== "enable_prime_tower" || ch.filaments.length > 1))
    .map(([k, l]) => `${l}${ch.settings[k] === "On" ? "" : `: ${esc(ch.settings[k])}`}`);
  const rows = [
    ["Printer", `${esc(ch.printer || "?")}${ch.bed ? `, ${esc(ch.bed)}` : ""}`],
    ["Process", preset],
    ["Filament", fil || "–"],
    ["On this plate", objs || "–"],
    ...KEY_SETTINGS.filter(([k]) => ch.settings[k] != null).map(([k, l]) => [l, esc(ch.settings[k])]),
    ...(special.length ? [["Special", special.join(", ")]] : []),
    ...(g ? [["Sliced", `${fmtTime(g.time_s)}, ${g.grams.toFixed(2)} g`]] : []),
  ];
  return `<section class="ov-about"><h3>This file</h3><dl class="ov-facts">${rows.map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join("")}</dl></section>`;
}

// ---- how it differs from the default ----------------------------------------------------------

function effectText(e) {
  if (!e) return "";
  const out = [];
  if (e.time_s != null && Math.abs(e.time_s) >= 0.5) out.push([fmtDelta(e.time_s), e.time_s < 0]);
  if (e.grams != null && Math.abs(e.grams) >= 0.005) out.push([`${e.grams < 0 ? "−" : "+"}${Math.abs(e.grams).toFixed(2)} g`, e.grams < 0]);
  if (e.quality != null && Math.abs(e.quality) >= 0.1) out.push([`quality ${e.quality > 0 ? "+" : "−"}${Math.abs(e.quality).toFixed(1)} pt`, e.quality > 0]);
  if (e.stiffness != null && Math.abs(e.stiffness) >= 0.05) out.push([`strength ${e.stiffness > 0 ? "+" : "−"}${Math.abs(e.stiffness).toFixed(1)} pt`, e.stiffness > 0]);
  if (!out.length) return '<span class="muted">no measurable effect</span>';
  return out.map(([t, good]) => `<span class="${good ? "good" : "bad"}">${esc(t)}</span>`).join(", ");
}

function differences(d) {
  const tuning = d.rows.filter((r) => r.tuning);
  if (!tuning.length) return "";
  const groups = Object.fromEntries(d.groups.map((g) => [g.key, g]));
  const measured = d.groups.some((g) => g.effect);
  const busy = d.groups.some((g) => g.status === "queued" || g.status === "slicing" || g.status === "analysing");
  const failed = d.groups.filter((g) => g.status === "error").length;
  const n = d.groups.length;
  const sections = [];
  for (const r of tuning) {
    const head = `${r.tab || "Other"} › ${r.page || ""}${r.section ? ` › ${r.section}` : ""}`;
    let s = sections.find((x) => x.head === head);
    if (!s) sections.push((s = { head, rows: [] }));
    s.rows.push(r);
  }
  const seen = new Set();
  const body = sections.map((s) => `<tr class="ov-head"><th colspan="${measured ? 4 : 3}">${esc(s.head)}</th></tr>` + s.rows.map((r) => {
    const first = !seen.has(r.key);
    seen.add(r.key);
    const g = groups[r.key];
    const eff = measured ? `<td class="ov-eff">${first && g ? (g.effect ? effectText(g.effect) : g.status === "error" ? '<span class="bad">failed</span>' : g.status ? '<span class="muted">slicing…</span>' : "") : ""}</td>` : "";
    return `<tr><td>${esc(r.name)}${r.scope !== "print-wide" ? `<div class="muted small">${esc(r.scope === "part" ? `part ${r.where}` : r.where)}</div>` : ""}</td>
      <td>${esc(r.now)}</td><td class="muted">${esc(r.default ?? "?")}</td>${eff}</tr>`;
  }).join("")).join("");
  const per = d.per_slice_s;
  const todo = d.groups.filter((g) => !g.status).length;
  const button = todo ? `<button type="button" class="ghost" id="ov-explain">Measure what each setting does</button>
      <span class="muted small">${todo} slice${todo === 1 ? "" : "s"}${per ? `, about ${fmtTime(per * todo)}` : ""}</span>` : "";
  const progress = busy ? `<span class="muted small">${d.groups.filter((g) => g.effect).length} of ${n} measured…</span>` : "";
  return `<section class="ov-diff">
    <h3>What it changes from the default</h3>
    <p class="muted small">${tuning.length} setting${tuning.length === 1 ? "" : "s"} on Bambu's Quality, Strength and Speed pages, and filament settings, differ from ${esc(d.reference)}.
      ${measured ? "“On its own” is the file against itself with only that setting at the default: what this setting does here. A setting changed in several places is measured with all of them at the default, on its first row. Effects don't add up exactly when settings interact; strength is a flat-part proxy." : "Measure them to see which ones make the difference."}</p>
    <div class="d-actions">${button}${progress}${failed ? ` <span class="bad small">${failed} failed to slice</span>` : ""}</div>
    <div class="chart"><table class="table ov-table"><tr><th>Setting</th><th>This file</th><th>Default</th>${measured ? "<th>On its own</th>" : ""}</tr>${body}</table></div>
  </section>`;
}

function kept(d) {
  const rows = d.rows.filter((r) => !r.tuning);
  if (!rows.length) return "";
  return `<details class="ov-kept opt-skipped" ${ui.keptOpen ? "open" : ""}><summary class="muted small">Kept as the file has them (${rows.length})</summary>
    <p class="muted small">Supports, bed adhesion, the prime tower, special modes, which filament prints what, material facts and the printer's own settings are how this model has to be printed, so the default keeps them too.</p>
    <ul class="small">${rows.map((r) => `<li>${esc(r.name)}${r.scope !== "print-wide" ? ` <span class="muted">(${esc(r.where)})</span>` : ""}: ${esc(r.now)}${r.default != null ? ` <span class="muted">(default ${esc(r.default)})</span>` : ""}</li>`).join("")}</ul></details>`;
}

async function compare(app, runId) {
  await app.api(`runs/${runId}/pin`, { method: "POST", body: { pinned: true } });
  await app.poll();
  const base = app.state.runs.find((r) => r.plate === app.state.plate && r.kind === "baseline");
  app.state.A = base ? base.id : app.state.A;
  app.state.B = runId;
  app.renderRuns();
  app.switchTab("layers");
}

if (window.slicerApp) start(window.slicerApp);
else window.addEventListener("slicer:ready", (e) => start(e.detail), { once: true });
