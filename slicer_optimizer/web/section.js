// The section tab: cut the toolpaths with a vertical plane and look at the walls,
// shells and infill layer by layer. Gaps between material (slots, channels,
// clearances) are measured as sliced; stiffness is compared between A and B.
import { featureColor } from "./colors.js";
import { parseLayer } from "./layerview.js";

const ui = { axis: "x", at: null, exag: "auto", plate: null, view: null, data: {} };

function fmtMm(v) { return `${v.toFixed(2)} mm`; }

function start(app) {
  const section = app.registerTab("section", "Section", () => render(app));
  section.innerHTML = `
    <div class="toolbar">
      <label class="field inline"><span>Cut</span>
        <select id="s-axis"><option value="x">Front to back (x fixed)</option><option value="y">Left to right (y fixed)</option></select></label>
      <label class="slider"><span>Position</span><input type="range" id="s-pos" min="0" max="1" step="0.05"><output id="s-pos-out"></output></label>
      <label class="field inline"><span>Height ×</span>
        <select id="s-exag"><option value="auto" selected>Auto</option><option>1</option><option>2</option><option>4</option><option>8</option><option>16</option></select></label>
    </div>
    <div class="s-top">
      <canvas id="s-map" class="s-map" aria-label="Top view: click to place the cut"></canvas>
      <div id="s-props" class="s-props"></div>
    </div>
    <div id="s-panes"></div>
    <p class="muted small">Spans are beads cut by the plane at their sliced width (centre ± half the line width). Gaps between them are clearances as sliced, not as printed; real lines spread a little.</p>`;
  section.querySelector("#s-axis").onchange = (e) => { ui.axis = e.target.value; ui.at = null; render(app); };
  section.querySelector("#s-exag").onchange = (e) => { ui.exag = e.target.value === "auto" ? "auto" : +e.target.value; repaint(); };
  const pos = section.querySelector("#s-pos");
  let t = null;
  pos.oninput = () => {
    ui.at = +pos.value;
    section.querySelector("#s-pos-out").textContent = `${ui.axis} = ${ui.at.toFixed(2)} mm`;
    drawMap(app);
    clearTimeout(t);
    t = setTimeout(() => load(app), 150);
  };
  section.querySelector("#s-map").addEventListener("click", (ev) => {
    if (!ui.map) return;
    const r = ev.target.getBoundingClientRect();
    const { x, y } = ui.map.toWorld(ev.clientX - r.left, ev.clientY - r.top);
    ui.at = Math.round((ui.axis === "x" ? x : y) * 20) / 20;
    pos.value = String(ui.at);
    pos.oninput();
  });
}

async function render(app) {
  const a = app.runById(app.state.A);
  const aa = await app.analysisOf(a);
  if (!aa) return;
  const bb = aa.bbox;
  const [lo, hi] = ui.axis === "x" ? [bb[0], bb[2]] : [bb[1], bb[3]];
  const pos = document.getElementById("s-pos");
  pos.min = String(Math.floor(lo)); pos.max = String(Math.ceil(hi));
  if (ui.at == null || ui.plate !== app.state.plate || ui.at < lo || ui.at > hi) ui.at = Math.round(((lo + hi) / 2) * 20) / 20;
  ui.plate = app.state.plate;
  pos.value = String(ui.at);
  document.getElementById("s-pos-out").textContent = `${ui.axis} = ${ui.at.toFixed(2)} mm`;
  // top view of the biggest layer, to place the cut
  const big = aa.layers.reduce((best, l) => (l.bbox && (!best || area(l) > area(best)) ? l : best), null);
  ui.mapLayer = big ? parseLayer(await app.api(`runs/${a.id}/layer/${big.index}`)) : null;
  ui.mapFeatures = aa.features;
  ui.mapBbox = bb;
  drawMap(app);
  await load(app);
}

const area = (l) => (l.bbox[2] - l.bbox[0]) * (l.bbox[3] - l.bbox[1]);

function drawMap(app) {
  const c = document.getElementById("s-map");
  if (!ui.mapLayer || !c) return;
  const dpr = window.devicePixelRatio || 1;
  const w = c.clientWidth, h = c.clientHeight;
  c.width = w * dpr; c.height = h * dpr;
  const ctx = c.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.fillStyle = "#1e1b16";
  ctx.fillRect(0, 0, w, h);
  const [x0, y0, x1, y1] = ui.mapBbox;
  const s = Math.min(w / ((x1 - x0) * 1.08), h / ((y1 - y0) * 1.08));
  const ox = w / 2 - ((x0 + x1) / 2) * s, oy = h / 2 + ((y0 + y1) / 2) * s;
  ui.map = { toWorld: (sx, sy) => ({ x: (sx - ox) / s, y: (oy - sy) / s }) };
  const d = ui.mapLayer;
  ctx.lineWidth = 1;
  for (let i = 0; i < d.nExt; i++) {
    const o = i * 10;
    ctx.strokeStyle = featureColor(ui.mapFeatures[d.ext[o + 9]]);
    ctx.beginPath();
    ctx.moveTo(d.ext[o] * s + ox, oy - d.ext[o + 1] * s);
    ctx.lineTo(d.ext[o + 2] * s + ox, oy - d.ext[o + 3] * s);
    ctx.stroke();
  }
  ctx.strokeStyle = "#c8a04a";
  ctx.lineWidth = 2;
  ctx.beginPath();
  if (ui.axis === "x") { const sx = ui.at * s + ox; ctx.moveTo(sx, 0); ctx.lineTo(sx, h); }
  else { const sy = oy - ui.at * s; ctx.moveTo(0, sy); ctx.lineTo(w, sy); }
  ctx.stroke();
}

async function load(app) {
  const runs = [app.runById(app.state.A), app.runById(app.state.B)].filter((r) => r && r.status === "done");
  ui.data = {};
  for (const r of runs) ui.data[r.id] = await app.api(`runs/${r.id}/section?axis=${ui.axis}&at=${ui.at}`);
  ui.view = null;
  draw(app);
}

function union(spans) {
  const out = [];
  for (const [a, b] of spans.map((s) => [s[0], s[1]]).sort((p, q) => p[0] - q[0])) {
    if (out.length && a <= out[out.length - 1][1]) out[out.length - 1][1] = Math.max(out[out.length - 1][1], b);
    else out.push([a, b]);
  }
  return out;
}

function draw(app) {
  const panes = document.getElementById("s-panes");
  const runs = [app.runById(app.state.A), app.runById(app.state.B)].filter((r) => r && ui.data[r.id]);
  panes.innerHTML = runs.map((r, k) => `<figure class="s-pane"><figcaption>${k ? "B" : "A"}: ${r.label}</figcaption><canvas data-run="${r.id}"></canvas></figure>`).join("");
  const all = runs.map((r) => ui.data[r.id]);
  let lo = Infinity, hi = -Infinity, ztop = 0;
  for (const s of all) for (const l of s.layers) {
    for (const sp of l.spans) { lo = Math.min(lo, sp[0]); hi = Math.max(hi, sp[1]); }
    ztop = Math.max(ztop, l.z);
  }
  if (!isFinite(lo)) { panes.innerHTML = `<p class="muted">The cut misses the part here; move it.</p>`; props(app, runs); return; }
  if (!ui.view) ui.view = { lo: lo - 1, hi: hi + 1 };
  ui.ztop = ztop;
  for (const canvas of panes.querySelectorAll("canvas")) {
    const sec = ui.data[canvas.dataset.run];
    paint(canvas, sec, ztop);
    attach(canvas, sec, ztop, app);
  }
  props(app, runs);
}

function repaint() {
  for (const canvas of document.querySelectorAll("#s-panes canvas")) paint(canvas, ui.data[canvas.dataset.run], ui.ztop);
}

function paint(canvas, sec, ztop) {
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth;
  const v = ui.view;
  const sx = w / (v.hi - v.lo);
  // Auto: stretch height so the section is about 220 px tall (flat parts are thin)
  const exag = ui.exag === "auto" ? Math.max(1, Math.min(40, 220 / (ztop * sx))) : ui.exag;
  const h = Math.max(80, Math.min(520, ztop * sx * exag + 30));
  canvas.style.height = `${h}px`;
  canvas.width = w * dpr; canvas.height = h * dpr;
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.fillStyle = "#1e1b16";
  ctx.fillRect(0, 0, w, h);
  const sz = (h - 24) / ztop;
  for (const l of sec.layers) {
    const y = h - 12 - l.z * sz, hh = Math.max(1, l.h * sz - (l.h * sz > 4 ? 1 : 0));
    for (const [a, b, f] of l.spans) {
      if (b < v.lo || a > v.hi) continue;
      ctx.fillStyle = featureColor(sec.features[f]);
      ctx.fillRect((a - v.lo) * sx, y, Math.max(1, (b - a) * sx), hh);
    }
  }
  canvas._geom = { sx, sz, h };
}

function attach(canvas, sec, ztop, app) {
  const tip = document.getElementById("tooltip");
  canvas.addEventListener("mousemove", (ev) => {
    const r = canvas.getBoundingClientRect();
    const { sx, sz, h } = canvas._geom;
    const u = ui.view.lo + (ev.clientX - r.left) / sx;
    const z = (h - 12 - (ev.clientY - r.top)) / sz;
    const layer = sec.layers.find((l) => z <= l.z && z > l.z - l.h);
    if (!layer) { tip.hidden = true; return; }
    const hit = layer.spans.find(([a, b]) => u >= a && u <= b);
    if (hit) {
      tip.innerHTML = `<strong>${sec.features[hit[2]]}</strong><br>layer ${layer.index}, z ${layer.z.toFixed(2)}<br>${fmtMm(hit[1] - hit[0])} wide, from ${hit[0].toFixed(2)} to ${hit[1].toFixed(2)}`;
    } else {
      const solid = union(layer.spans);
      const left = solid.filter(([, b]) => b <= u).pop();
      const right = solid.find(([a]) => a >= u);
      if (!left || !right) { tip.hidden = true; return; }
      tip.innerHTML = `<strong>Gap ${fmtMm(right[0] - left[1])}</strong> as sliced<br>layer ${layer.index}, z ${layer.z.toFixed(2)}<br>from ${left[1].toFixed(2)} to ${right[0].toFixed(2)}`;
    }
    tip.style.left = `${ev.clientX + 14}px`; tip.style.top = `${ev.clientY + 14}px`; tip.hidden = false;
  });
  canvas.addEventListener("mouseleave", () => { tip.hidden = true; });
  canvas.addEventListener("wheel", (ev) => {
    ev.preventDefault();
    const r = canvas.getBoundingClientRect();
    const u = ui.view.lo + (ev.clientX - r.left) / canvas._geom.sx;
    const k = Math.exp(ev.deltaY * 0.0015);
    ui.view = { lo: u - (u - ui.view.lo) * k, hi: u + (ui.view.hi - u) * k };
    repaint();
  }, { passive: false });
  let drag = null;
  canvas.addEventListener("pointerdown", (ev) => { drag = { x: ev.clientX, lo: ui.view.lo, hi: ui.view.hi }; canvas.setPointerCapture(ev.pointerId); });
  canvas.addEventListener("pointermove", (ev) => {
    if (!drag) return;
    const d = (ev.clientX - drag.x) / canvas._geom.sx;
    ui.view = { lo: drag.lo - d, hi: drag.hi - d };
    repaint();
  });
  canvas.addEventListener("pointerup", () => { drag = null; });
}

function props(app, runs) {
  const box = document.getElementById("s-props");
  if (!runs.length) { box.innerHTML = ""; return; }
  const rows = runs.map((r, k) => {
    const s = ui.data[r.id];
    return `<tr><td>${k ? "B" : "A"}</td><td>${s.area_mm2.toFixed(1)} mm²</td><td>${s.centroid_z == null ? "–" : s.centroid_z.toFixed(2) + " mm"}</td><td>${s.i_mm4.toFixed(1)} mm⁴</td></tr>`;
  }).join("");
  let ratio = "";
  if (runs.length === 2) {
    const [a, b] = runs.map((r) => ui.data[r.id].i_mm4);
    if (a > 0) ratio = `<p>B is <strong>${((100 * b) / a).toFixed(1)}%</strong> as stiff as A in this section (bending out of the plate), assuming perfect layer bonding.</p>`;
  }
  box.innerHTML = `<table class="table"><tr><th></th><th>Material</th><th>Centroid height</th><th>Bending stiffness I</th></tr>${rows}</table>${ratio}
    <p class="muted small">Hover the section for line widths and gaps. Scroll to zoom, drag to pan.</p>`;
}

if (window.slicerApp) start(window.slicerApp);
else window.addEventListener("slicer:ready", (e) => start(e.detail), { once: true });
