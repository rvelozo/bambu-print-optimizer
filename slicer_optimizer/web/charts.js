// Small SVG charts: per-layer bars with an optional B line, per-layer lines, and
// grouped horizontal bars. No dependencies.
const NS = "http://www.w3.org/2000/svg";

function el(tag, attrs = {}, parent) {
  const e = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  if (parent) parent.appendChild(e);
  return e;
}

export function fmtTime(s) {
  if (s == null || !isFinite(s)) return "–";
  const t = Math.round(s);
  const h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), sec = t % 60;
  if (h) return `${h}h${String(m).padStart(2, "0")}m${String(sec).padStart(2, "0")}s`;
  if (m) return `${m}m${String(sec).padStart(2, "0")}s`;
  return `${sec}s`;
}

export function fmtDelta(s) {
  if (Math.abs(s) < 0.5) return "same";
  return (s < 0 ? "−" : "+") + fmtTime(Math.abs(s));
}

function niceStep(max, target = 5) {
  const raw = max / target;
  const mag = Math.pow(10, Math.floor(Math.log10(raw || 1)));
  for (const f of [1, 2, 2.5, 5, 10]) if (raw <= f * mag) return f * mag;
  return 10 * mag;
}

function timeStep(max) {
  for (const s of [5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600]) if (max / s <= 6) return s;
  return 7200;
}

// Bars per layer for A, a step line for B. opts: {a, b, labels, onPick, tip, selected, time}
export function layerBars(container, opts) {
  container.innerHTML = "";
  const n = opts.a.length;
  const width = Math.max(container.clientWidth || 800, 320);
  const height = 300;
  const m = { l: 56, r: 12, t: 12, b: 30 };
  const iw = width - m.l - m.r, ih = height - m.t - m.b;
  const max = Math.max(1e-9, ...opts.a, ...(opts.b || []));
  const step = opts.time ? timeStep(max) : niceStep(max);
  const top = Math.ceil(max / step) * step;
  const svg = el("svg", { width, height, role: "img", "aria-label": opts.aria || "chart" }, container);
  const x = (i) => m.l + (i / n) * iw;
  const y = (v) => m.t + ih - (v / top) * ih;
  for (let v = 0; v <= top + 1e-9; v += step) {
    el("line", { x1: m.l, x2: m.l + iw, y1: y(v), y2: y(v), class: "axis" }, svg);
    const t = el("text", { x: m.l - 6, y: y(v) + 4, "text-anchor": "end" }, svg);
    t.textContent = opts.time ? fmtTime(v) : (opts.fmt ? opts.fmt(v) : String(+v.toFixed(2)));
  }
  const tickEvery = Math.max(1, Math.ceil(n / 12));
  for (let i = 0; i < n; i += tickEvery) {
    const t = el("text", { x: x(i + 0.5), y: height - 10, "text-anchor": "middle" }, svg);
    t.textContent = opts.labels ? opts.labels(i) : String(i);
  }
  const bw = Math.max(1, iw / n - (n < 60 ? 2 : 0.5));
  for (let i = 0; i < n; i++) {
    el("rect", { x: x(i), y: y(opts.a[i]), width: bw, height: Math.max(0, ih + m.t - y(opts.a[i])),
                 fill: opts.colorA || "var(--a)", opacity: i === opts.selected ? 1 : 0.75 }, svg);
  }
  if (opts.selected != null && opts.selected < n) {
    // the layer open in the Layers view: a marker under the axis, not a recolored bar
    const cx = x(opts.selected) + bw / 2, by = m.t + ih + 2;
    el("path", { d: `M${cx - 5},${by + 8}L${cx + 5},${by + 8}L${cx},${by}Z`, fill: "var(--accent)" }, svg);
  }
  if (opts.b) {
    let d = "";
    for (let i = 0; i < n; i++) d += `${i ? "L" : "M"}${x(i)},${y(opts.b[i] || 0)}H${x(i + 1)}`;
    el("path", { d, fill: "none", stroke: "var(--b)", "stroke-width": 2 }, svg);
  }
  if (opts.cap != null) {
    el("line", { x1: m.l, x2: m.l + iw, y1: y(opts.cap), y2: y(opts.cap), stroke: "var(--bad)", "stroke-dasharray": "4 3" }, svg);
  }
  const hit = el("rect", { x: m.l, y: m.t, width: iw, height: ih, fill: "transparent", style: "cursor:pointer" }, svg);
  const tip = document.getElementById("tooltip");
  hit.addEventListener("mousemove", (ev) => {
    const r = svg.getBoundingClientRect();
    const i = Math.min(n - 1, Math.max(0, Math.floor(((ev.clientX - r.left - m.l) / iw) * n)));
    tip.innerHTML = opts.tip(i);
    tip.style.left = `${ev.clientX + 14}px`;
    tip.style.top = `${ev.clientY + 14}px`;
    tip.hidden = false;
  });
  hit.addEventListener("mouseleave", () => { tip.hidden = true; });
  hit.addEventListener("click", (ev) => {
    const r = svg.getBoundingClientRect();
    const i = Math.min(n - 1, Math.max(0, Math.floor(((ev.clientX - r.left - m.l) / iw) * n)));
    tip.hidden = true;
    opts.onPick && opts.onPick(i);
  });
}

// Lines per layer. series: [{values, color, dash, label}]
export function layerLines(container, { series, n, labels, cap, tip, fmt, aria }) {
  container.innerHTML = "";
  const width = Math.max(container.clientWidth || 800, 320);
  const height = 280;
  const m = { l: 56, r: 12, t: 12, b: 30 };
  const iw = width - m.l - m.r, ih = height - m.t - m.b;
  const max = Math.max(1e-9, cap || 0, ...series.flatMap((s) => s.values));
  const step = niceStep(max);
  const top = Math.ceil((max * 1.05) / step) * step;
  const svg = el("svg", { width, height, role: "img", "aria-label": aria || "chart" }, container);
  const x = (i) => m.l + ((i + 0.5) / n) * iw;
  const y = (v) => m.t + ih - (v / top) * ih;
  for (let v = 0; v <= top + 1e-9; v += step) {
    el("line", { x1: m.l, x2: m.l + iw, y1: y(v), y2: y(v), class: "axis" }, svg);
    const t = el("text", { x: m.l - 6, y: y(v) + 4, "text-anchor": "end" }, svg);
    t.textContent = fmt ? fmt(v) : String(+v.toFixed(2));
  }
  const tickEvery = Math.max(1, Math.ceil(n / 12));
  for (let i = 0; i < n; i += tickEvery) {
    const t = el("text", { x: x(i), y: height - 10, "text-anchor": "middle" }, svg);
    t.textContent = labels ? labels(i) : String(i);
  }
  if (cap != null) {
    el("line", { x1: m.l, x2: m.l + iw, y1: y(cap), y2: y(cap), stroke: "var(--bad)", "stroke-dasharray": "5 4" }, svg);
    const t = el("text", { x: m.l + iw - 4, y: y(cap) - 5, "text-anchor": "end" }, svg);
    t.textContent = `filament cap ${cap} mm³/s`;
  }
  for (const s of series) {
    let d = "";
    s.values.forEach((v, i) => { d += `${i ? "L" : "M"}${x(i)},${y(v)}`; });
    el("path", { d, fill: "none", stroke: s.color, "stroke-width": 2, "stroke-dasharray": s.dash || "" }, svg);
  }
  const hit = el("rect", { x: m.l, y: m.t, width: iw, height: ih, fill: "transparent" }, svg);
  const tipEl = document.getElementById("tooltip");
  hit.addEventListener("mousemove", (ev) => {
    const r = svg.getBoundingClientRect();
    const i = Math.min(n - 1, Math.max(0, Math.floor(((ev.clientX - r.left - m.l) / iw) * n)));
    tipEl.innerHTML = tip(i);
    tipEl.style.left = `${ev.clientX + 14}px`;
    tipEl.style.top = `${ev.clientY + 14}px`;
    tipEl.hidden = false;
  });
  hit.addEventListener("mouseleave", () => { tipEl.hidden = true; });
}

// Grouped horizontal bars: rows [{label, a, b, color}]
export function featureBars(container, rows, { hasB }) {
  container.innerHTML = "";
  const width = Math.max(container.clientWidth || 800, 320);
  const rowH = hasB ? 34 : 24;
  const m = { l: 170, r: 150, t: 6, b: 6 };
  const height = m.t + m.b + rows.length * rowH;
  const iw = width - m.l - m.r;
  const max = Math.max(1e-9, ...rows.map((r) => Math.max(r.a || 0, r.b || 0)));
  const svg = el("svg", { width, height, role: "img", "aria-label": "Time by feature" }, container);
  rows.forEach((r, k) => {
    const y0 = m.t + k * rowH;
    const label = el("text", { x: m.l - 8, y: y0 + (hasB ? 16 : 14), "text-anchor": "end" }, svg);
    label.textContent = r.label;
    el("rect", { x: m.l, y: y0 + 3, width: Math.max(1, (r.a / max) * iw), height: hasB ? 12 : 14, fill: r.color, rx: 2 }, svg);
    if (hasB) el("rect", { x: m.l, y: y0 + 17, width: Math.max(1, ((r.b || 0) / max) * iw), height: 12, fill: r.color, opacity: 0.45, rx: 2 }, svg);
    const v = el("text", { x: m.l + iw + 8, y: y0 + (hasB ? 16 : 14) }, svg);
    v.textContent = hasB ? `${fmtTime(r.a)} → ${fmtTime(r.b || 0)}` : fmtTime(r.a);
  });
}
