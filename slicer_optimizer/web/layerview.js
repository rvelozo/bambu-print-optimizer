// Draws one layer's toolpaths for run A (and B) on canvases that share one view,
// so panning or zooming either pane moves both.
import { featureColor, rampColor, TRAVEL_COLOR } from "./colors.js";

const EXT = 10;   // x0 y0 x1 y1 width height speed flow time feature
const TRAV = 5;   // x0 y0 x1 y1 time

export function parseLayer(buffer) {
  const head = new Uint32Array(buffer, 0, 2);
  const nExt = head[0], nTrav = head[1];
  const floats = new Float32Array(buffer, 8);
  return { nExt, nTrav, ext: floats.subarray(0, nExt * EXT), trav: floats.subarray(nExt * EXT, nExt * EXT + nTrav * TRAV) };
}

export class LayerView {
  constructor({ canvasA, canvasB, tooltip, plate }) {
    this.canvases = [canvasA, canvasB];
    this.tooltip = tooltip;
    this.plate = plate;
    this.view = { cx: 128, cy: 128, scale: 3 };
    this.data = [null, null];       // parsed layers for A and B
    this.features = [[], []];       // feature names per run (analysis.features)
    this.colorBy = "feature";
    this.showTravel = false;
    this.overlay = false;
    this.range = { min: 0, max: 1 };
    this.cap = null;
    this.pending = false;
    for (const [i, c] of this.canvases.entries()) this.attach(c, i);
    new ResizeObserver(() => this.redraw()).observe(canvasA);
  }

  set({ a, b, featuresA, featuresB, colorBy, showTravel, overlay, cap, seamsA, seamsB, showSeams }) {
    this.data = [a || null, b || null];
    this.seams = [seamsA || [], seamsB || []];
    this.showSeams = !!showSeams;
    this.features = [featuresA || [], featuresB || []];
    this.colorBy = colorBy;
    this.showTravel = showTravel;
    this.overlay = overlay;
    this.cap = cap;
    const css = getComputedStyle(document.documentElement);
    this.overlayColors = [css.getPropertyValue("--a").trim() || "#3d7dff", css.getPropertyValue("--b").trim() || "#ff9e2c"];
    this.range = this.computeRange();
    this.redraw();
  }

  computeRange() {
    if (this.colorBy === "feature") return null;
    const field = { speed: 6, flow: 7, width: 4 }[this.colorBy];
    let lo = Infinity, hi = -Infinity;
    for (const d of this.data) {
      if (!d) continue;
      for (let i = 0; i < d.nExt; i++) {
        const v = d.ext[i * EXT + field];
        if (v < lo) lo = v;
        if (v > hi) hi = v;
      }
    }
    if (this.colorBy === "flow" && this.cap) hi = Math.max(hi, this.cap);
    if (!isFinite(lo)) return { min: 0, max: 1, field };
    if (hi - lo < 1e-6) hi = lo + 1;
    return { min: this.colorBy === "flow" ? 0 : lo, max: hi, field };
  }

  fit(bbox) {
    const c = this.canvases[0];
    const w = c.clientWidth || 600, h = c.clientHeight || 400;
    const [x0, y0, x1, y1] = bbox;
    const pad = 1.06;
    this.view.cx = (x0 + x1) / 2;
    this.view.cy = (y0 + y1) / 2;
    this.view.scale = Math.min(w / ((x1 - x0) * pad || 1), h / ((y1 - y0) * pad || 1));
    this.redraw();
  }

  redraw() {
    if (this.pending) return;
    this.pending = true;
    requestAnimationFrame(() => {
      this.pending = false;
      this.draw(0);
      if (!this.overlay) this.draw(1);
    });
  }

  toScreen(x, y, w, h) {
    const v = this.view;
    return [(x - v.cx) * v.scale + w / 2, h / 2 - (y - v.cy) * v.scale];
  }

  toWorld(sx, sy, w, h) {
    const v = this.view;
    return [(sx - w / 2) / v.scale + v.cx, v.cy - (sy - h / 2) / v.scale];
  }

  colorOf(run, i) {
    const d = this.data[run];
    if (this.overlay) return this.overlayColors[run];
    if (this.colorBy === "feature") return featureColor(this.features[run][d.ext[i * EXT + 9]]);
    const r = this.range;
    return rampColor((d.ext[i * EXT + r.field] - r.min) / (r.max - r.min));
  }

  draw(pane) {
    const canvas = this.canvases[pane];
    if (!canvas || canvas.offsetParent === null) return;
    const dpr = window.devicePixelRatio || 1;
    const w = canvas.clientWidth, h = canvas.clientHeight;
    if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
      canvas.width = Math.round(w * dpr);
      canvas.height = Math.round(h * dpr);
    }
    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.fillStyle = this.plate.bg;
    ctx.fillRect(0, 0, w, h);
    this.drawGrid(ctx, w, h);
    const runs = this.overlay ? [0, 1] : [pane];
    for (const run of runs) {
      const d = this.data[run];
      if (!d) continue;
      if (this.overlay) ctx.globalAlpha = run === 0 ? 0.9 : 0.65;
      if (this.showTravel) this.drawTravel(ctx, d, w, h);
      this.drawExtrusion(ctx, run, d, w, h);
      if (this.showSeams) this.drawSeams(ctx, run, w, h);
      ctx.globalAlpha = 1;
    }
    this.drawScale(ctx, w, h);
  }

  drawGrid(ctx, w, h) {
    // 10 mm grid, like a build plate
    const v = this.view;
    const step = v.scale * 10 < 12 ? 50 : 10;
    const [wx0, wy1] = this.toWorld(0, 0, w, h);
    const [wx1, wy0] = this.toWorld(w, h, w, h);
    ctx.strokeStyle = this.plate.grid;
    ctx.lineWidth = 1;
    ctx.beginPath();
    for (let x = Math.ceil(wx0 / step) * step; x <= wx1; x += step) {
      const [sx] = this.toScreen(x, 0, w, h);
      ctx.moveTo(Math.round(sx) + 0.5, 0);
      ctx.lineTo(Math.round(sx) + 0.5, h);
    }
    for (let y = Math.ceil(wy0 / step) * step; y <= wy1; y += step) {
      const [, sy] = this.toScreen(0, y, w, h);
      ctx.moveTo(0, Math.round(sy) + 0.5);
      ctx.lineTo(w, Math.round(sy) + 0.5);
    }
    ctx.stroke();
  }

  drawScale(ctx, w, h) {
    const v = this.view;
    const candidates = [0.5, 1, 2, 5, 10, 20, 50, 100];
    const mm = candidates.find((m) => m * v.scale >= 60) || 100;
    const px = mm * v.scale;
    ctx.strokeStyle = "#e9e1cf";
    ctx.fillStyle = "#e9e1cf";
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(w - 16 - px, h - 16);
    ctx.lineTo(w - 16, h - 16);
    ctx.stroke();
    ctx.font = "12px Barlow, system-ui, sans-serif";
    ctx.textAlign = "right";
    ctx.fillText(`${mm} mm`, w - 16, h - 22);
  }

  drawSeams(ctx, run, w, h) {
    ctx.fillStyle = "#ffffff";
    ctx.strokeStyle = "#1e1b16";
    ctx.lineWidth = 1;
    const r = Math.max(2, Math.min(5, this.view.scale * 0.25));
    for (const [x, y] of this.seams[run]) {
      const [sx, sy] = this.toScreen(x, y, w, h);
      if (sx < -r || sy < -r || sx > w + r || sy > h + r) continue;
      ctx.beginPath();
      ctx.arc(sx, sy, r, 0, Math.PI * 2);
      ctx.fill();
      ctx.stroke();
    }
  }

  drawTravel(ctx, d, w, h) {
    ctx.strokeStyle = TRAVEL_COLOR;
    ctx.lineWidth = 1;
    ctx.beginPath();
    const t = d.trav;
    for (let i = 0; i < d.nTrav; i++) {
      const o = i * TRAV;
      const [ax, ay] = this.toScreen(t[o], t[o + 1], w, h);
      const [bx, by] = this.toScreen(t[o + 2], t[o + 3], w, h);
      ctx.moveTo(ax, ay);
      ctx.lineTo(bx, by);
    }
    ctx.stroke();
  }

  drawExtrusion(ctx, run, d, w, h) {
    const v = this.view;
    const e = d.ext;
    const groups = new Map();
    const margin = 20;
    for (let i = 0; i < d.nExt; i++) {
      const o = i * EXT;
      const [ax, ay] = this.toScreen(e[o], e[o + 1], w, h);
      const [bx, by] = this.toScreen(e[o + 2], e[o + 3], w, h);
      if ((ax < -margin && bx < -margin) || (ax > w + margin && bx > w + margin) ||
          (ay < -margin && by < -margin) || (ay > h + margin && by > h + margin)) continue;
      const lw = Math.max(0.75, e[o + 4] * v.scale);
      const key = this.colorOf(run, i) + "|" + (Math.round(lw * 4) / 4);
      let g = groups.get(key);
      if (!g) { g = []; groups.set(key, g); }
      g.push(ax, ay, bx, by);
    }
    ctx.lineCap = v.scale * 0.45 > 3 ? "round" : "butt";
    for (const [key, pts] of groups) {
      const [color, lw] = key.split("|");
      ctx.strokeStyle = color;
      ctx.lineWidth = parseFloat(lw);
      ctx.beginPath();
      for (let k = 0; k < pts.length; k += 4) {
        ctx.moveTo(pts[k], pts[k + 1]);
        ctx.lineTo(pts[k + 2], pts[k + 3]);
      }
      ctx.stroke();
    }
  }

  nearest(pane, sx, sy) {
    const canvas = this.canvases[pane];
    const w = canvas.clientWidth, h = canvas.clientHeight;
    const [wx, wy] = this.toWorld(sx, sy, w, h);
    const limit = 8 / this.view.scale;
    let best = null;
    const runs = this.overlay ? [0, 1] : [pane];
    for (const run of runs) {
      const d = this.data[run];
      if (!d) continue;
      const e = d.ext;
      for (let i = 0; i < d.nExt; i++) {
        const o = i * EXT;
        const x0 = e[o], y0 = e[o + 1], x1 = e[o + 2], y1 = e[o + 3];
        const dx = x1 - x0, dy = y1 - y0;
        const l2 = dx * dx + dy * dy;
        let t = l2 > 0 ? ((wx - x0) * dx + (wy - y0) * dy) / l2 : 0;
        t = t < 0 ? 0 : t > 1 ? 1 : t;
        const px = x0 + t * dx - wx, py = y0 + t * dy - wy;
        const dist = Math.sqrt(px * px + py * py) - e[o + 4] / 2;
        if (dist < limit && (!best || dist < best.dist)) best = { run, i, dist };
      }
    }
    if (!best) return null;
    const e = this.data[best.run].ext, o = best.i * EXT;
    const len = Math.hypot(e[o + 2] - e[o], e[o + 3] - e[o + 1]);
    return {
      run: best.run, feature: this.features[best.run][e[o + 9]], width: e[o + 4], height: e[o + 5],
      speed: e[o + 6], flow: e[o + 7], time: e[o + 8], length: len, x: (e[o] + e[o + 2]) / 2, y: (e[o + 1] + e[o + 3]) / 2,
    };
  }

  attach(canvas, pane) {
    let drag = null;
    canvas.addEventListener("pointerdown", (ev) => {
      canvas.setPointerCapture(ev.pointerId);
      drag = { x: ev.clientX, y: ev.clientY, cx: this.view.cx, cy: this.view.cy };
      this.tooltip.hidden = true;
    });
    canvas.addEventListener("pointermove", (ev) => {
      if (drag) {
        this.view.cx = drag.cx - (ev.clientX - drag.x) / this.view.scale;
        this.view.cy = drag.cy + (ev.clientY - drag.y) / this.view.scale;
        this.redraw();
        return;
      }
      this.hover(canvas, pane, ev);
    });
    canvas.addEventListener("pointerup", () => { drag = null; });
    canvas.addEventListener("pointerleave", () => { this.tooltip.hidden = true; });
    canvas.addEventListener("wheel", (ev) => {
      ev.preventDefault();
      const r = canvas.getBoundingClientRect();
      const sx = ev.clientX - r.left, sy = ev.clientY - r.top;
      const w = canvas.clientWidth, h = canvas.clientHeight;
      const [wx, wy] = this.toWorld(sx, sy, w, h);
      const k = Math.exp(-ev.deltaY * 0.0015);
      this.view.scale = Math.max(0.5, Math.min(400, this.view.scale * k));
      const [nx, ny] = this.toWorld(sx, sy, w, h);
      this.view.cx += wx - nx;
      this.view.cy += wy - ny;
      this.redraw();
    }, { passive: false });
  }

  hover(canvas, pane, ev) {
    if (this.hoverPending) return;
    this.hoverPending = true;
    requestAnimationFrame(() => {
      this.hoverPending = false;
      const r = canvas.getBoundingClientRect();
      const hit = this.nearest(pane, ev.clientX - r.left, ev.clientY - r.top);
      if (!hit) { this.tooltip.hidden = true; return; }
      const run = hit.run === 0 ? "A" : "B";
      this.tooltip.innerHTML =
        `<strong>${hit.feature}</strong> <span class="muted">(${run})</span><br>` +
        `${hit.speed.toFixed(0)} mm/s, ${hit.flow.toFixed(1)} mm³/s<br>` +
        `line ${hit.width.toFixed(2)} × ${hit.height.toFixed(2)} mm, ${hit.length.toFixed(2)} mm long<br>` +
        `${hit.time < 1 ? (hit.time * 1000).toFixed(0) + " ms" : hit.time.toFixed(1) + " s"} at X ${hit.x.toFixed(1)} Y ${hit.y.toFixed(1)}`;
      this.tooltip.style.left = `${ev.clientX + 14}px`;
      this.tooltip.style.top = `${ev.clientY + 14}px`;
      this.tooltip.hidden = false;
    });
  }
}
