// A dropdown of Bambu Studio's Process settings, arranged like the app's Process tab
// (Quality, Strength, Speed, Support, Others, with their sections), so a setting is
// picked by the name the slicer shows, not by its key.
import { escapeHtml as esc } from "./opener.js";

let panel = null;
let current = null;  // { data, onPick, anchor }
let tab = 0;

export function nameOf(s) {
  return s.line ? `${s.line} ${s.label}` : s.label;
}

export function valueText(s, v = s.now) {
  if (v == null) return "varies";
  if (s.choices) return (s.choices.find((c) => c[0] === v) || [v, v])[1];
  if (s.type === "coBool" || s.type === "coBools") return v === "1" ? "On" : "Off";
  const text = String(v);
  if (!s.unit || text.endsWith(s.unit)) return text;
  if (/ or %$/.test(s.unit)) return text.endsWith("%") ? text : `${text} ${s.unit.replace(/ or %$/, "")}`;  // "mm/s or %"
  return `${text} ${s.unit}`;
}

function build() {
  panel = document.createElement("div");
  panel.className = "sp";
  panel.setAttribute("role", "dialog");
  panel.setAttribute("aria-label", "Choose a setting");
  panel.hidden = true;
  panel.innerHTML = `<input type="search" class="sp-search" placeholder="Search settings" aria-label="Search settings">
    <nav class="sp-tabs" role="tablist"></nav><div class="sp-list"></div>`;
  document.body.appendChild(panel);
  panel.querySelector(".sp-search").addEventListener("input", render);
  panel.querySelector(".sp-search").addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); panel.querySelector(".sp-row")?.click(); }
  });
  document.addEventListener("mousedown", (e) => {
    if (!panel.hidden && !panel.contains(e.target) && !current?.anchor.contains(e.target)) close();
  });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !panel.hidden) { close(); current?.anchor.focus(); } });
  window.addEventListener("resize", () => { if (!panel.hidden) place(); });
}

function place() {
  const r = current.anchor.getBoundingClientRect();
  const width = Math.min(560, window.innerWidth - 32);
  const left = Math.max(16, Math.min(r.left, window.innerWidth - width - 16));
  const below = window.innerHeight - r.bottom - 16;
  const above = r.top - 16;
  panel.style.width = `${width}px`;
  panel.style.left = `${left}px`;
  if (below >= 320 || below >= above) {
    panel.style.top = `${r.bottom + 4}px`;
    panel.style.bottom = "";
    panel.style.maxHeight = `${Math.max(240, below)}px`;
  } else {
    panel.style.top = "";
    panel.style.bottom = `${window.innerHeight - r.top + 4}px`;
    panel.style.maxHeight = `${above}px`;
  }
}

function row(s, path) {
  return `<button type="button" class="sp-row" data-key="${s.key}" title="${esc(s.tooltip || "")}">
    <span>${esc(nameOf(s))}${path ? `<span class="sp-path">${esc(path)}</span>` : ""}</span>
    <span class="sp-now">${esc(valueText(s))}</span></button>`;
}

function render() {
  const { data, selected } = current;
  const q = panel.querySelector(".sp-search").value.trim().toLowerCase();
  const tabs = panel.querySelector(".sp-tabs");
  const list = panel.querySelector(".sp-list");
  tabs.hidden = !!q;
  tabs.innerHTML = data.pages.map((p, i) => `<button type="button" role="tab" aria-selected="${i === tab}" data-i="${i}">${esc(p.title)}</button>`).join("");
  for (const b of tabs.querySelectorAll("button")) b.onclick = () => { tab = +b.dataset.i; render(); };
  let html = "";
  if (q) {
    const hits = [];
    for (const p of data.pages) for (const g of p.groups) for (const s of g.settings) {
      const hay = `${nameOf(s)} ${s.key} ${g.title}`.toLowerCase();
      if (q.split(/\s+/).every((w) => hay.includes(w))) hits.push(row(s, `${p.title} › ${g.title}`));
    }
    html = hits.length ? hits.join("") : `<p class="muted small sp-empty">No setting matches "${esc(q)}".</p>`;
  } else {
    const page = data.pages[tab] || data.pages[0];
    html = page.groups.map((g) => `<h4>${esc(g.title)}</h4>` + g.settings.map((s) => row(s)).join("")).join("");
  }
  list.innerHTML = html;
  for (const b of list.querySelectorAll(".sp-row")) {
    if (b.dataset.key === selected) b.classList.add("on");
    b.onclick = () => {
      const s = findSetting(data, b.dataset.key);
      close();
      current.onPick(s);
      current.anchor.focus();
    };
  }
}

export function findSetting(data, key) {
  for (const p of data.pages) for (const g of p.groups) for (const s of g.settings) if (s.key === key) return { ...s, page: p.title, group: g.title };
  return null;
}

export function openPicker(anchor, data, selected, onPick) {
  if (!panel) build();
  current = { data, onPick, anchor, selected };
  if (selected) {  // open on the page that holds the chosen setting
    const i = data.pages.findIndex((p) => p.groups.some((g) => g.settings.some((s) => s.key === selected)));
    if (i >= 0) tab = i;
  }
  panel.querySelector(".sp-search").value = "";
  panel.hidden = false;
  anchor.setAttribute("aria-expanded", "true");
  place();
  render();
  panel.querySelector(".sp-row.on")?.scrollIntoView({ block: "center" });
  panel.querySelector(".sp-search").focus();
}

function close() {
  if (!panel) return;
  panel.hidden = true;
  current?.anchor.setAttribute("aria-expanded", "false");
}
