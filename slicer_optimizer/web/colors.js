// Feature colors from Bambu Studio 02.08.04.57's preview
// (src/slic3r/GUI/GCodeRenderer/BaseRenderer.cpp, Extrusion_Role_Colors), so the
// layer view reads like the slicer's own preview. Bambu Studio is copyright Bambu Lab and
// licensed under the GNU AGPL v3 (https://github.com/bambulab/BambuStudio), like this project.
const rgb = (r, g, b) => `rgb(${Math.round(r * 255)},${Math.round(g * 255)},${Math.round(b * 255)})`;

export const FEATURE_COLORS = {
  "Undefined": rgb(0.90, 0.70, 0.70),
  "Inner wall": rgb(1.00, 0.90, 0.30),
  "Outer wall": rgb(1.00, 0.49, 0.22),
  "Overhang wall": rgb(0.12, 0.12, 1.00),
  "Sparse infill": rgb(0.69, 0.19, 0.16),
  "Internal solid infill": rgb(0.59, 0.33, 0.80),
  "Floating vertical shell": rgb(0.90, 0.70, 0.70),
  "Top surface": rgb(0.94, 0.25, 0.25),
  "Bottom surface": rgb(0.40, 0.36, 0.78),
  "Ironing": rgb(1.00, 0.55, 0.41),
  "Bridge": rgb(0.30, 0.50, 0.73),
  "Gap infill": rgb(1.00, 1.00, 1.00),
  "Skirt": rgb(0.00, 0.53, 0.43),
  "Brim": rgb(0.00, 0.23, 0.43),
  "Support": rgb(0.00, 1.00, 0.00),
  "Support interface": rgb(0.00, 0.50, 0.00),
  "Support transition": rgb(0.00, 0.25, 0.00),
  "Support ironing": rgb(0.60, 1.00, 0.60),
  "Prime tower": rgb(0.70, 0.89, 0.67),
  "Custom": rgb(0.37, 0.82, 0.58),
  "Flush": rgb(0.85, 0.65, 0.95),
};
export const TRAVEL_COLOR = rgb(0.219, 0.282, 0.609);
export const featureColor = (name) => FEATURE_COLORS[name] || "rgb(200,200,200)";

// Ordered ramp for speed, flow and width (viridis stops): dark = low, bright = high.
const RAMP = ["#440154", "#482878", "#3e4a89", "#31688e", "#26828e", "#1f9e89", "#35b779", "#6ece58", "#b5de2b", "#fde725"];
const RAMP_RGB = RAMP.map((h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16)));
const CACHE = new Map();

export function rampColor(t) {
  const q = Math.max(0, Math.min(1, t));
  const key = Math.round(q * 63);
  let c = CACHE.get(key);
  if (!c) {
    const x = (key / 63) * (RAMP.length - 1);
    const i = Math.min(RAMP.length - 2, Math.floor(x));
    const f = x - i;
    const [a, b] = [RAMP_RGB[i], RAMP_RGB[i + 1]];
    c = `rgb(${Math.round(a[0] + (b[0] - a[0]) * f)},${Math.round(a[1] + (b[1] - a[1]) * f)},${Math.round(a[2] + (b[2] - a[2]) * f)})`;
    CACHE.set(key, c);
  }
  return c;
}
export const RAMP_CSS = `linear-gradient(90deg, ${RAMP.join(", ")})`;
