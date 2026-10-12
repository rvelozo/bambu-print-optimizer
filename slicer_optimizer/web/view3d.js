// 3D view of a run's toolpaths. three.js comes from cdn.jsdelivr.net and is only
// loaded when this tab opens, so the rest of the app works offline.
import { featureColor } from "./colors.js";

const ui = { three: null, scene: null, camera: null, renderer: null, controls: null, lines: null, seams: null,
             runId: null, layerStarts: [], lo: 1, hi: null, which: "A", showSeams: false };

function start(app) {
  const section = app.registerTab("view3d", "3D", () => render(app));
  section.innerHTML = `
    <div class="toolbar">
      <label class="field inline"><span>Show</span><select id="v3-run"><option value="A">A</option><option value="B">B</option></select></label>
      <label class="slider"><span>From layer</span><input type="range" id="v3-lo" min="1" max="1" value="1"><output id="v3-lo-out"></output></label>
      <label class="slider"><span>To layer</span><input type="range" id="v3-hi" min="1" max="1" value="1"><output id="v3-hi-out"></output></label>
      <label class="check"><input type="checkbox" id="v3-seams" aria-label="Show seams"> Seams</label>
    </div>
    <div class="v3-wrap"><canvas id="v3"></canvas><div class="v3-note" id="v3-note"></div></div>
    <p class="muted small">Drag to orbit, right-drag to pan, scroll to zoom. Lines follow the extrusion paths, colored by feature; white dots are where outer walls start (seams).</p>`;
  section.querySelector("#v3-run").onchange = (e) => { ui.which = e.target.value; ui.runId = null; render(app); };
  section.querySelector("#v3-lo").oninput = (e) => { ui.lo = +e.target.value; if (ui.lo > ui.hi) { ui.hi = ui.lo; section.querySelector("#v3-hi").value = ui.hi; } range(); };
  section.querySelector("#v3-hi").oninput = (e) => { ui.hi = +e.target.value; if (ui.hi < ui.lo) { ui.lo = ui.hi; section.querySelector("#v3-lo").value = ui.lo; } range(); };
  section.querySelector("#v3-seams").onchange = (e) => { ui.showSeams = e.target.checked; if (ui.seams) ui.seams.visible = ui.showSeams; frame(); };
}

async function loadThree() {
  if (ui.three) return true;
  try {
    const THREE = await import("three");
    const { OrbitControls } = await import("three/addons/controls/OrbitControls.js");
    ui.three = { THREE, OrbitControls };
    return true;
  } catch (e) {
    document.getElementById("v3-note").textContent = "The 3D view needs three.js from cdn.jsdelivr.net, which couldn't be reached.";
    return false;
  }
}

function setup() {
  const { THREE, OrbitControls } = ui.three;
  const canvas = document.getElementById("v3");
  ui.renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
  ui.renderer.setPixelRatio(window.devicePixelRatio || 1);
  ui.scene = new THREE.Scene();
  ui.scene.background = new THREE.Color("#1e1b16");
  ui.camera = new THREE.PerspectiveCamera(40, 1, 0.1, 5000);
  ui.camera.up.set(0, 0, 1);
  ui.controls = new OrbitControls(ui.camera, canvas);
  ui.controls.addEventListener("change", frame);
  new ResizeObserver(() => frame()).observe(canvas);
}

function frame() {
  if (!ui.renderer) return;
  const c = ui.renderer.domElement;
  const w = c.clientWidth, h = c.clientHeight;
  if (c.width !== Math.round(w * ui.renderer.getPixelRatio())) {
    ui.renderer.setSize(w, h, false);
    ui.camera.aspect = w / h;
    ui.camera.updateProjectionMatrix();
  }
  ui.renderer.render(ui.scene, ui.camera);
}

function range() {
  document.getElementById("v3-lo-out").textContent = String(ui.lo);
  document.getElementById("v3-hi-out").textContent = String(ui.hi);
  if (!ui.lines) return;
  const s = ui.layerStarts;
  const from = s[ui.lo] ?? 0, to = s[ui.hi + 1] ?? s[s.length - 1];
  ui.lines.geometry.setDrawRange(from, to - from);
  if (ui.seams) {
    const ss = ui.seamStarts;
    ui.seams.geometry.setDrawRange(ss[ui.lo] ?? 0, (ss[ui.hi + 1] ?? ss[ss.length - 1]) - (ss[ui.lo] ?? 0));
  }
  frame();
}

async function render(app) {
  const run = app.runById(ui.which === "B" ? app.state.B : app.state.A);
  const note = document.getElementById("v3-note");
  if (!run || run.status !== "done") { note.textContent = ui.which === "B" ? "Pick a run as B to see it here." : ""; return; }
  if (!(await loadThree())) return;
  if (!ui.renderer) setup();
  if (ui.runId === run.id) { frame(); return; }
  ui.runId = run.id;
  note.textContent = "Loading toolpaths…";
  const [buf, an] = await Promise.all([app.api(`runs/${run.id}/bundle`), app.analysisOf(run)]);
  const { THREE } = ui.three;
  const head = new DataView(buf);
  const nLayers = head.getUint32(0, true);
  let off = 4, total = 0;
  const layers = [];
  for (let i = 0; i < nLayers; i++) {
    const z = head.getFloat32(off, true), n = head.getUint32(off + 8, true);
    layers.push({ z, n, at: off + 12 });
    total += n;
    off += 12 + n * 40;
  }
  const pos = new Float32Array(total * 6), col = new Float32Array(total * 6);
  const starts = [];
  let v = 0;
  const color = new THREE.Color();
  const cache = new Map();
  for (const [i, l] of layers.entries()) {
    starts[i] = v * 2;
    const f = new Float32Array(buf, l.at, l.n * 10);
    for (let k = 0; k < l.n; k++) {
      const o = k * 10;
      const name = an.features[f[o + 9]];
      let c = cache.get(name);
      if (!c) { color.set(featureColor(name)); c = [color.r, color.g, color.b]; cache.set(name, c); }
      const p = v * 6;
      pos[p] = f[o]; pos[p + 1] = f[o + 1]; pos[p + 2] = l.z;
      pos[p + 3] = f[o + 2]; pos[p + 4] = f[o + 3]; pos[p + 5] = l.z;
      col[p] = col[p + 3] = c[0]; col[p + 1] = col[p + 4] = c[1]; col[p + 2] = col[p + 5] = c[2];
      v++;
    }
  }
  starts[nLayers] = v * 2;
  ui.layerStarts = starts;
  if (ui.lines) { ui.scene.remove(ui.lines); ui.lines.geometry.dispose(); }
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.BufferAttribute(pos, 3));
  g.setAttribute("color", new THREE.BufferAttribute(col, 3));
  ui.lines = new THREE.LineSegments(g, new THREE.LineBasicMaterial({ vertexColors: true }));
  ui.scene.add(ui.lines);
  // seams
  if (ui.seams) { ui.scene.remove(ui.seams); ui.seams.geometry.dispose(); ui.seams = null; }
  const seamPts = [], seamStarts = [];
  for (const l of an.layers) {
    seamStarts[l.index] = seamPts.length / 3;
    for (const [x, y] of l.seams || []) seamPts.push(x, y, l.z + 0.05);
  }
  seamStarts[an.layers.length] = seamPts.length / 3;
  ui.seamStarts = seamStarts;
  if (seamPts.length) {
    const sg = new THREE.BufferGeometry();
    sg.setAttribute("position", new THREE.BufferAttribute(new Float32Array(seamPts), 3));
    ui.seams = new THREE.Points(sg, new THREE.PointsMaterial({ color: 0xffffff, size: 3, sizeAttenuation: false }));
    ui.seams.visible = ui.showSeams;
    ui.scene.add(ui.seams);
  }
  // camera framing
  const [x0, y0, x1, y1] = an.bbox;
  const ztop = an.layers[an.layers.length - 1].z;
  const cx = (x0 + x1) / 2, cy = (y0 + y1) / 2, size = Math.max(x1 - x0, y1 - y0, ztop);
  ui.controls.target.set(cx, cy, ztop / 2);
  ui.camera.position.set(cx + size * 0.9, cy - size * 1.1, ztop + size * 0.8);
  ui.controls.update();
  const lo = document.getElementById("v3-lo"), hi = document.getElementById("v3-hi");
  lo.max = hi.max = String(nLayers - 1);
  ui.lo = 1; ui.hi = nLayers - 1;
  lo.value = "1"; hi.value = String(ui.hi);
  note.textContent = `${run.label}: ${total.toLocaleString()} extrusion segments in ${nLayers - 1} layers`;
  range();
}

if (window.slicerApp) start(window.slicerApp);
else window.addEventListener("slicer:ready", (e) => start(e.detail), { once: true });
