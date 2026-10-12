import { call, enableDrop, escapeHtml, openPath, pickProject, projectUrl, quitApp, tildePath } from "./opener.js";

const $ = (id) => document.getElementById(id);
let data = { projects: [], waiting: 0, home: "" };
let signature = "";

const showError = (msg) => { $("open-error").textContent = msg; };

function ago(t) {
  const s = Date.now() / 1000 - t;
  if (s < 90) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} minutes ago`;
  if (s < 2 * 3600) return "an hour ago";
  if (s < 86400) return `${Math.round(s / 3600)} hours ago`;
  if (s < 2 * 86400) return "yesterday";
  return `on ${new Date(t * 1000).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" })}`;
}

function status(p) {
  if (!p.exists) return `<p class="bad small">The file was moved or deleted. Open it from its new place.</p>`;
  const bits = [];
  if (p.disk === "changed") bits.push(`<p class="small accent">Changed on disk since it was loaded</p>`);
  if (p.waiting) bits.push(`<p class="small accent">Slicing, ${p.waiting} waiting</p>`);
  else if (p.open) bits.push(`<p class="small muted">${p.plates} plate${p.plates === 1 ? "" : "s"}, ${p.done} run${p.done === 1 ? "" : "s"} measured</p>`);
  return bits.join("");
}

function render() {
  const sig = JSON.stringify(data.projects);
  if (sig === signature) return;
  signature = sig;
  const list = $("recent");
  if (!data.projects.length) {
    list.innerHTML = `<li class="recent-empty muted">No projects yet. Choose a .3mf that Bambu Studio saved; the app slices each plate and lets you compare settings.</li>`;
    return;
  }
  list.innerHTML = "";
  for (const p of data.projects) {
    const li = document.createElement("li");
    li.className = "proj" + (p.exists ? "" : " is-missing");
    const where = p.uploaded ? "A copy of a dropped file" : escapeHtml(tildePath(p.folder, data.home));
    const href = projectUrl(p.id);
    const plate = p.exists
      ? `<a class="proj-plate" href="${href}" tabindex="-1" aria-hidden="true"><img alt="" src="/api/projects/${p.id}/thumbnail" onerror="this.remove()"></a>`
      : `<div class="proj-plate" aria-hidden="true"></div>`;
    li.innerHTML = `${plate}
      <div class="proj-body">
        ${p.exists ? `<a class="proj-name" href="${href}">${escapeHtml(p.name)}</a>` : `<span class="proj-name">${escapeHtml(p.name)}</span>`}
        <p class="small muted proj-where" title="${escapeHtml(p.path)}">${where}</p>
        <p class="small muted">Opened ${ago(p.opened)}</p>
        ${status(p)}
        <button type="button" class="link proj-forget">Remove from list</button>
      </div>`;
    li.querySelector(".proj-forget").onclick = async () => {
      try { data = { ...data, ...(await call(`projects/${p.id}`, { method: "DELETE" })) }; render(); }
      catch (e) { showError(e.message); }
    };
    list.appendChild(li);
  }
}

async function refresh() {
  try {
    data = await call("projects");
  } catch (e) {
    return;
  }
  $("app-meta").textContent = data.bambu_studio
    ? `Measures Bambu Studio settings by slicing them, with Bambu Studio ${data.bambu_studio}.`
    : `Bambu Studio wasn't found at ${data.bambu_cli}, so nothing can be sliced: ${data.setup_hint}.`;
  $("pick").hidden = !data.dialogs;  // the native Open dialog is macOS only; paste or drop elsewhere
  render();
}

$("pick").onclick = async () => {
  showError("");
  const btn = $("pick");
  btn.disabled = true;
  try { await pickProject(); } catch (e) { showError(e.message); } finally { btn.disabled = false; }
};
$("path-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  showError("");
  const path = $("path").value.trim();
  if (!path) { showError("Paste the path of a .3mf file first."); return; }
  try { await openPath(path); } catch (err) { showError(err.message); }
});
$("quit").onclick = () => quitApp(data.waiting);
enableDrop(showError);
refresh();
setInterval(refresh, 3000);
