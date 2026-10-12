// Opening projects from any page: the macOS Open dialog, a pasted path, or a dropped file.
// Also the Quit button, which stops the whole app (every open project).

export async function call(path, opts = {}) {
  const res = await fetch(`/api/${path}`, {
    method: opts.method || "GET",
    headers: opts.body ? { "Content-Type": "application/json" } : {},
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

export const projectUrl = (id) => `/p/${id}/`;

export async function pickProject() {
  const res = await call("projects/pick", { method: "POST" });
  if (!res.cancelled) location.href = projectUrl(res.id);
}

export async function openPath(path) {
  const res = await call("projects", { method: "POST", body: { path } });
  location.href = projectUrl(res.id);
}

async function uploadFile(file) {
  const res = await fetch("/api/projects/upload", {
    method: "POST",
    headers: { "X-Filename": encodeURIComponent(file.name), "Content-Type": "application/octet-stream" },
    body: file,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  location.href = projectUrl(data.id);
}

// The whole window takes a dropped .3mf. A browser doesn't say where a dropped file
// lives, so the app keeps a copy; the overlay says so before the drop.
export function enableDrop(onError) {
  const overlay = document.createElement("div");
  overlay.className = "drop-overlay";
  overlay.hidden = true;
  overlay.innerHTML = `<div class="drop-card"><strong>Drop the .3mf to open a copy of it</strong>
    <span>Later edits in Bambu Studio won't show in a copy. To keep working on the file itself, use Choose a .3mf file.</span></div>`;
  document.body.appendChild(overlay);
  let depth = 0;
  const hasFiles = (e) => [...(e.dataTransfer?.types || [])].includes("Files");
  window.addEventListener("dragenter", (e) => { if (!hasFiles(e)) return; depth++; overlay.hidden = false; e.preventDefault(); });
  window.addEventListener("dragover", (e) => { if (hasFiles(e)) e.preventDefault(); });
  window.addEventListener("dragleave", (e) => { if (!hasFiles(e)) return; depth = Math.max(0, depth - 1); if (!depth) overlay.hidden = true; });
  window.addEventListener("drop", async (e) => {
    if (!hasFiles(e)) return;
    e.preventDefault();
    depth = 0;
    const files = [...e.dataTransfer.files];
    const file = files.find((f) => /\.3mf$/i.test(f.name));
    if (!file) { overlay.hidden = true; onError(files.length ? `${files[0].name} is not a .3mf project.` : "Nothing to open."); return; }
    overlay.querySelector(".drop-card").innerHTML = `<strong>Copying ${escapeHtml(file.name)}</strong><span>${(file.size / 1e6).toFixed(1)} MB</span>`;
    try {
      await uploadFile(file);
    } catch (err) {
      overlay.hidden = true;
      onError(err.message);
    }
  });
}

export async function quitApp(waiting) {
  if (waiting > 0 && !window.confirm(`${waiting} slice${waiting === 1 ? " is" : "s are"} still waiting. ` +
      "They'll continue the next time the app starts. Quit now?")) return;
  try { await call("quit", { method: "POST" }); } catch (e) { /* the server may close the connection first */ }
  document.body.innerHTML = `<main class="stopped"><h1>Slicer optimizer has stopped</h1>
    <p class="muted">To start it again, open the Slicer Optimizer app, or run <code>slicer-opt open</code>.</p></main>`;
  document.title = "Slicer optimizer (stopped)";
}

export function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

export function tildePath(p, home) {
  return home && p.startsWith(home + "/") ? "~" + p.slice(home.length) : p;
}
