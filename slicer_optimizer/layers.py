"""Per-layer data for the browser: summaries plus compact binary toolpaths.

Built once per slice from its G-code (gcode.analyze) and stored next to the
slice in the cache as analysis-v{VERSION}.json and layers-v{VERSION}.bin.

Binary layout: for each layer, extrusion segments then travel moves, float32
little-endian. An extrusion segment is EXT_FIELDS (10 floats); a travel move is
TRAVEL_FIELDS (5 floats). analysis["layers"][i] gives offsets and counts.
Consecutive collinear segments with the same feature and height, and width and
speed within MERGE_WIDTH_MM / MERGE_SPEED_REL, are merged: time and volume add
up, width and speed become length-weighted averages. Bambu splits overhang
walls into many tiny segments with slightly different speeds; merging them
keeps the panel's ~500k raw segments browser-sized without changing totals.
"""

from __future__ import annotations

import json
import math
from array import array
from pathlib import Path

from . import gcode

VERSION = 6
MERGE_WIDTH_MM = 0.02
MERGE_SPEED_REL = 0.05
EXT_FIELDS = ["x0", "y0", "x1", "y1", "width", "height", "speed", "flow", "time", "feature"]
TRAVEL_FIELDS = ["x0", "y0", "x1", "y1", "time"]


def _list(cfg: dict, key: str) -> list[str]:
    raw = cfg.get(key, "")
    sep = ";" if ";" in raw else ","
    return [x.strip().strip('"') for x in raw.split(sep) if x.strip()]


def filament_info(a: gcode.Analysis) -> dict:
    """The filaments the plate uses (the header's "filament: 1,6,7" lists their slots).

    Plate-level values (flow cap, cost, density) are given only when every filament
    on the plate shares them; "filaments" has each one's own values.
    """
    cfg = a.config
    try:
        slots = [int(x) - 1 for x in str(a.header.get("filament", "1")).split(",") if x.strip()] or [0]
    except ValueError:
        slots = [0]

    def pick(key, slot, cast=float):
        vals = _list(cfg, key)
        if not vals:
            return None
        try:
            return cast(vals[slot if slot < len(vals) else 0])
        except ValueError:
            return None

    fils = [{"slot": s + 1, "name": pick("filament_settings_id", s, str),
             "max_flow": pick("filament_max_volumetric_speed", s), "density": pick("filament_density", s),
             "cost_per_kg": pick("filament_cost", s), "type": pick("filament_type", s, str)} for s in slots]

    def shared(key):
        vals = {f[key] for f in fils}
        return vals.pop() if len(vals) == 1 else None

    names = list(dict.fromkeys(f["name"] for f in fils if f["name"]))
    types = list(dict.fromkeys(f["type"] for f in fils if f["type"]))
    return {"slot": fils[0]["slot"], "name": " + ".join(names) or None, "type": " + ".join(types) or None,
            "max_flow": shared("max_flow"), "density": shared("density"), "cost_per_kg": shared("cost_per_kg"),
            "caps_differ": len({f["max_flow"] for f in fils}) > 1, "filaments": fils}


def _weighted_percentile(values: list[tuple[float, float]], q: float) -> float:
    if not values:
        return 0.0
    values.sort()
    total = sum(w for _, w in values)
    acc = 0.0
    for v, w in values:
        acc += w
        if acc >= q * total:
            return v
    return values[-1][0]


def build(a: gcode.Analysis) -> tuple[dict, bytes]:
    fil = filament_info(a)
    cap = fil["max_flow"]
    n_layers = len(a.layers)
    ext_by_layer: list[list[int]] = [[] for _ in range(n_layers)]
    for i, layer in enumerate(a.layer):
        ext_by_layer[layer].append(i)
    tr_by_layer: list[list[int]] = [[] for _ in range(n_layers)]
    for i, layer in enumerate(a.tlayer):
        tr_by_layer[layer].append(i)
    seams_by_layer: list[list[list[float]]] = [[] for _ in range(n_layers)]
    for i, layer in enumerate(a.seam_layer):
        seams_by_layer[layer].append([round(a.seam_x[i], 2), round(a.seam_y[i], 2)])

    out = array("f")
    layers = []
    feature_totals: dict[int, list[float]] = {}  # feature -> [time, volume, length]
    raw_segments = merged_segments = 0
    for li in range(n_layers):
        ext_off = len(out)
        flows: list[tuple[float, float]] = []
        near_cap_s = 0.0
        volume = length = 0.0
        merged = 0
        bx0 = by0 = math.inf
        bx1 = by1 = -math.inf
        cur = None  # [x0,y0,x1,y1,w,h,speed,flow,time,feat, vol]
        for i in ext_by_layer[li]:
            x0, y0, x1, y1 = a.x0[i], a.y0[i], a.x1[i], a.y1[i]
            w, h, v, q, t, f, vol = a.width[i], a.height[i], a.speed[i], a.flow[i], a.time[i], a.feat[i], a.volume[i]
            seg_len = math.hypot(x1 - x0, y1 - y0)
            raw_segments += 1
            bx0 = min(bx0, x0, x1); by0 = min(by0, y0, y1)
            bx1 = max(bx1, x0, x1); by1 = max(by1, y0, y1)
            volume += vol
            length += seg_len
            flows.append((q, seg_len))
            if cap and q >= 0.95 * cap:
                near_cap_s += t
            ft = feature_totals.setdefault(f, [0.0, 0.0, 0.0])
            ft[0] += t
            ft[1] += vol
            ft[2] += seg_len
            if (cur is not None and cur[9] == f and abs(cur[4] - w) < MERGE_WIDTH_MM and abs(cur[5] - h) < 1e-3
                    and abs(cur[6] - v) <= MERGE_SPEED_REL * max(cur[6], v) and cur[2] == x0 and cur[3] == y0):
                dx1, dy1 = cur[2] - cur[0], cur[3] - cur[1]
                dx2, dy2 = x1 - x0, y1 - y0
                if abs(dx1 * dy2 - dy1 * dx2) <= 1e-4 * (abs(dx1) + abs(dy1)) * (abs(dx2) + abs(dy2)) \
                        and dx1 * dx2 + dy1 * dy2 >= 0:
                    l1 = math.hypot(dx1, dy1)
                    tot = l1 + seg_len
                    if tot > 0:
                        cur[4] = (cur[4] * l1 + w * seg_len) / tot
                        cur[6] = (cur[6] * l1 + v * seg_len) / tot
                    cur[2], cur[3] = x1, y1
                    cur[8] += t
                    cur[10] += vol
                    continue
            if cur is not None:
                out.extend(_finish(cur))
                merged += 1
            cur = [x0, y0, x1, y1, w, h, v, q, t, f, vol]
        if cur is not None:
            out.extend(_finish(cur))
            merged += 1
        merged_segments += merged
        tr_off = len(out)
        for i in tr_by_layer[li]:
            out.extend((a.tx0[i], a.ty0[i], a.tx1[i], a.ty1[i], a.ttime[i]))
        info = a.layers[li]
        roles = {a.roles[r]: round(t, 3) for (l, r), t in a.layer_role_time.items() if l == li and t > 0}
        layers.append({
            "index": li, "z": info["z"], "h": info["h"],
            "time_s": round(a.layer_time.get(li, 0.0), 3), "time_by_role": roles,
            "volume_mm3": round(volume, 2), "length_mm": round(length, 1),
            "max_flow": round(max((q for q, _ in flows), default=0.0), 3),
            "p95_flow": round(_weighted_percentile(flows, 0.95), 3),
            "near_cap_s": round(near_cap_s, 2),
            "bbox": [round(bx0, 2), round(by0, 2), round(bx1, 2), round(by1, 2)] if merged else None,
            "seams": seams_by_layer[li],
            "ext_offset": ext_off, "ext_count": merged,
            "travel_offset": tr_off, "travel_count": len(tr_by_layer[li]),
        })

    xs = [v for arr in (a.x0, a.x1) for v in arr]
    ys = [v for arr in (a.y0, a.y1) for v in arr]
    bbox = [min(xs), min(ys), max(xs), max(ys)] if xs else [0, 0, 1, 1]
    all_flow = sorted(zip(a.flow, (math.hypot(a.x1[i] - a.x0[i], a.y1[i] - a.y0[i]) for i in range(len(a.flow)))))
    analysis = {
        "version": VERSION,
        "ext_fields": EXT_FIELDS, "travel_fields": TRAVEL_FIELDS,
        "features": a.features,
        "filament": fil,
        "bbox": [round(v, 3) for v in bbox],
        "total_s": round(a.total_s, 3),
        "model_s": round(a.model_s, 3),
        "bambu_total_s": round(a.bambu_total_s, 3),
        "bambu_model_s": round(a.bambu_model_s, 3),
        "prepare_s": round(a.prepare_s, 3),
        "time_by_feature": {k: round(v, 3) for k, v in a.time_by_feature().items()},
        "bambu_time_by_feature": {k: round(v, 3) for k, v in a.time_by_feature(bambu=True).items()},
        "feature_totals": {a.features[f]: {"time_s": round(t, 3), "volume_mm3": round(vol, 2), "length_mm": round(ln, 1)}
                           for f, (t, vol, ln) in feature_totals.items()},
        "extruded_mm": round(a.extruded_mm, 3),
        "header_filament_mm": a.header.get("filament_mm"),
        "flow": {"cap": cap, "p95": round(_weighted_percentile(list(all_flow), 0.95), 3),
                 "max": round(max(a.flow, default=0.0), 3),
                 "near_cap_s": round(sum(l["near_cap_s"] for l in layers), 2)},
        "segments": {"raw": raw_segments, "merged": merged_segments, "travel": len(a.tx0)},
        "layers": layers,
    }
    return analysis, out.tobytes()


def _finish(cur: list) -> tuple:
    x0, y0, x1, y1, w, h, v, q, t, f, vol = cur
    seg_len = math.hypot(x1 - x0, y1 - y0)
    flow = vol * v / seg_len if seg_len > 0 else q
    return (x0, y0, x1, y1, w, h, v, flow, t, float(f))


def paths(folder: Path, plate: int) -> tuple[Path, Path, Path]:
    return (folder / f"plate_{plate}.gcode.gz", folder / f"analysis-v{VERSION}.json", folder / f"layers-v{VERSION}.bin")


def load_or_build(folder: Path, plate: int) -> dict:
    gz, ajson, abin = paths(folder, plate)
    if ajson.exists() and abin.exists():
        return json.loads(ajson.read_text())
    if not gz.exists():
        raise FileNotFoundError(f"no G-code for plate {plate} in {folder}")
    analysis, blob = build(gcode.analyze(gz))
    tmp = abin.with_suffix(".tmp")
    tmp.write_bytes(blob)
    tmp.replace(abin)
    ajson.write_text(json.dumps(analysis))
    return analysis


def layer_blob(folder: Path, plate: int, analysis: dict, index: int) -> bytes:
    """One layer's segments: uint32 ext_count, uint32 travel_count, then the floats."""
    _, _, abin = paths(folder, plate)
    layer = analysis["layers"][index]
    n_ext, n_tr = layer["ext_count"], layer["travel_count"]
    start = layer["ext_offset"] * 4
    size = (n_ext * len(EXT_FIELDS) + n_tr * len(TRAVEL_FIELDS)) * 4
    with open(abin, "rb") as f:
        f.seek(start)
        body = f.read(size)
    return array("I", [n_ext, n_tr]).tobytes() + body


def bundle_blob(folder: Path, plate: int, analysis: dict) -> bytes:
    """Every layer's extrusion segments for the 3D view.

    uint32 layer count, then per layer: float32 z, float32 h, uint32 count,
    and that many extrusion segments (EXT_FIELDS floats each). Travel is left out.
    """
    _, _, abin = paths(folder, plate)
    parts = [array("I", [len(analysis["layers"])]).tobytes()]
    with open(abin, "rb") as f:
        for layer in analysis["layers"]:
            n = layer["ext_count"]
            parts.append(array("f", [layer["z"], layer["h"]]).tobytes() + array("I", [n]).tobytes())
            f.seek(layer["ext_offset"] * 4)
            parts.append(f.read(n * len(EXT_FIELDS) * 4))
    return b"".join(parts)
