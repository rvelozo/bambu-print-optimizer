// The prints tab: log what really happened (time from the printer, weight from a
// scale, outcome, issues, test pieces) and see how Bambu's estimates compare.
import { fmtTime } from "./charts.js";

const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const ui = { data: null };

function parseDuration(text) {
  const s = String(text).trim().toLowerCase().replace(/\s+/g, "");
  if (!s) return null;
  if (s.includes(":")) {
    const p = s.split(":").map(Number);
    if (p.some(isNaN)) return null;
    return p.length === 2 ? p[0] * 3600 + p[1] * 60 : p.length === 3 ? p[0] * 3600 + p[1] * 60 + p[2] : null;
  }
  const m = s.match(/^(?:(\d+(?:\.\d+)?)h)?(?:(\d+(?:\.\d+)?)m(?:in)?)?(?:(\d+(?:\.\d+)?)s)?$/);
  if (m && (m[1] || m[2] || m[3])) return (+m[1] || 0) * 3600 + (+m[2] || 0) * 60 + (+m[3] || 0);
  return isNaN(+s) ? null : +s;
}

function pct(r) { return `${r >= 1 ? "+" : "−"}${Math.abs(100 * (r - 1)).toFixed(1)}%`; }

function start(app) {
  const section = app.registerTab("prints", "Prints", () => render(app));
  section.innerHTML = `
    <div class="p-grid">
      <form id="p-form" class="p-form" autocomplete="off">
        <h2>Log a print</h2>
        <p class="muted small">After printing, record what the printer and a scale say. Bambu's estimate for the run you pick is filled in for you.</p>
        <label class="field">Which run did you print? <select id="p-run"></select></label>
        <label class="field">Print time on the printer <input type="text" id="p-time" placeholder="2h10m"></label>
        <label class="field">Weight on a scale, grams (optional) <input type="text" id="p-weight" placeholder="129.5"></label>
        <fieldset class="p-outcome"><legend class="muted small">How did it go?</legend>
          <label class="check"><input type="radio" name="p-outcome" value="ok" checked> Worked</label>
          <label class="check"><input type="radio" name="p-outcome" value="partial"> Partly</label>
          <label class="check"><input type="radio" name="p-outcome" value="failed"> Failed</label>
        </fieldset>
        <fieldset><legend class="muted small">Anything wrong?</legend><div id="p-issues" class="p-issues"></div></fieldset>
        <label class="check"><input type="checkbox" id="p-test"> A test piece (adhesion test, coupon), not a real part</label>
        <label class="field">Note <textarea id="p-note" rows="2" placeholder="Front right corner lifted after layer 3"></textarea></label>
        <button type="submit" class="primary">Log this print</button>
        <p id="p-error" class="error" role="alert"></p>
      </form>
      <div>
        <h2>How Bambu's estimates compare</h2>
        <div id="p-cal"></div>
        <h2 class="d-h">Logged prints</h2>
        <div id="p-list"></div>
      </div>
    </div>`;
  section.querySelector("#p-form").addEventListener("submit", (e) => { e.preventDefault(); submit(app); });
  app.summaryHooks.push((state, a) => calibratedStat(app, a));
  load(app).then(() => app.refreshView()).catch(() => {});
}

function calibratedStat(app, run) {
  if (!ui.data || !run || run.status !== "done") return "";
  const cal = ui.data.calibration.find((c) => c.printer === ui.data.printer && c.filament === run.metrics.filament && c.time);
  if (!cal) return "";
  const t = cal.time;
  return `<div class="stat"><div class="label">On your printer</div><div class="value">about ${fmtTime(run.metrics.total_s * t.ratio)}</div>
          <div class="delta">${pct(t.ratio)} from ${t.n} logged print${t.n === 1 ? "" : "s"}</div></div>`;
}

async function load(app) {
  ui.data = await app.api("prints");
}

async function render(app) {
  await load(app);
  const sel = document.getElementById("p-run");
  const runs = app.state.runs.filter((r) => r.status === "done" && (r.kind !== "search" || r.pinned))
    .sort((a, b) => a.plate - b.plate || (a.kind === "baseline" ? -1 : 1));
  const keep = sel.value;
  sel.innerHTML = runs.map((r) => `<option value="${r.id}">Plate ${r.plate}: ${esc(r.label)} (Bambu says ${fmtTime(r.metrics.total_s)})</option>`).join("");
  sel.value = keep && runs.find((r) => r.id === keep) ? keep : (runs.find((r) => r.id === app.state.A)?.id || runs[0]?.id || "");
  const issues = document.getElementById("p-issues");
  if (!issues.children.length) {
    issues.innerHTML = ui.data.issues.map((i) => `<label class="check"><input type="checkbox" value="${esc(i)}"> ${esc(i)}</label>`).join("");
  }
  renderCalibration();
  renderList(app);
}

function renderCalibration() {
  const box = document.getElementById("p-cal");
  const rows = ui.data.calibration;
  if (!rows.length) {
    box.innerHTML = `<p class="muted">Nothing logged yet. Each finished print with its real time shows how far Bambu's estimate is from your printer, per filament.</p>`;
    return;
  }
  box.innerHTML = rows.map((c) => {
    const t = c.time, w = c.weight;
    return `<p><strong>${esc(c.filament || "Unknown filament")}</strong> on ${esc(c.printer || "your printer")}: ` +
      (t ? `prints take <strong>${pct(t.ratio)}</strong> against Bambu's estimate (${t.n} print${t.n === 1 ? "" : "s"}, ${pct(t.lo)} to ${pct(t.hi)})` : "no times logged") +
      (w ? `; parts weigh ${pct(w.ratio)} against the slicer (${w.n})` : "") + `.</p>`;
  }).join("") + `<p class="muted small">Failed prints are left out. One print is a hint, several are a measurement.</p>`;
}

function renderList(app) {
  const box = document.getElementById("p-list");
  const items = ui.data.prints;
  if (!items.length) { box.innerHTML = `<p class="muted small">None yet.</p>`; return; }
  box.innerHTML = `<table class="table"><tr><th>Date</th><th>What</th><th>Bambu</th><th>Actual</th><th>Diff</th><th>Outcome</th><th>Notes</th><th></th></tr>` +
    items.map((p) => {
      const diff = p.actual_s && p.predicted_s ? pct(p.actual_s / p.predicted_s) : "";
      const notes = [p.issues.join(", "), p.note].filter(Boolean).join(". ");
      return `<tr><td>${esc(p.date)}</td><td>${p.kind === "test" ? "Test: " : ""}Plate ${p.plate}, ${esc(p.label)}</td>
        <td>${p.predicted_s ? fmtTime(p.predicted_s) : ""}</td><td>${p.actual_s ? fmtTime(p.actual_s) : ""}</td><td>${diff}</td>
        <td class="${p.outcome === "failed" ? "bad" : p.outcome === "ok" ? "good" : ""}">${{ ok: "Worked", partial: "Partly", failed: "Failed" }[p.outcome]}</td>
        <td>${esc(notes)}</td><td><button type="button" class="link" data-del="${p.id}">Remove</button></td></tr>`;
    }).join("") + `</table>`;
  for (const b of box.querySelectorAll("[data-del]")) b.onclick = async () => {
    if (!window.confirm("Remove this print from the log?")) return;
    ui.data = await app.api(`prints/${b.dataset.del}`, { method: "DELETE" });
    renderCalibration(); renderList(app);
  };
}

async function submit(app) {
  const err = document.getElementById("p-error");
  err.textContent = "";
  const runId = document.getElementById("p-run").value;
  const timeText = document.getElementById("p-time").value;
  const outcome = document.querySelector('input[name="p-outcome"]:checked').value;
  const seconds = timeText.trim() ? parseDuration(timeText) : null;
  if (!runId) { err.textContent = "Pick the run you printed."; return; }
  if (timeText.trim() && (seconds == null || seconds <= 0)) { err.textContent = "Write the time like 2h10m or 2:10."; return; }
  if (!timeText.trim() && outcome !== "failed") { err.textContent = "Add the print time from the printer (or mark the print as failed)."; return; }
  const weightText = document.getElementById("p-weight").value.trim();
  if (weightText && !(+weightText > 0)) { err.textContent = "Weight is a number of grams, like 129.5."; return; }
  try {
    ui.data = await app.api("prints", { method: "POST", body: {
      run_id: runId, actual_s: seconds, actual_g: weightText ? +weightText : null, outcome,
      issues: [...document.querySelectorAll("#p-issues input:checked")].map((i) => i.value),
      kind: document.getElementById("p-test").checked ? "test" : "part",
      note: document.getElementById("p-note").value.trim(),
    } });
    document.getElementById("p-form").reset();
    renderCalibration(); renderList(app);
  } catch (e) {
    err.textContent = e.message;
  }
}

if (window.slicerApp) start(window.slicerApp);
else window.addEventListener("slicer:ready", (e) => start(e.detail), { once: true });
