"""Cross-sections through the toolpaths: material, gaps, and bending stiffness.

A vertical cut plane (x = const or y = const) crosses each layer's extrusion
segments. A bead of width w along a segment occupies a strip of half-width w/2;
its intersection with the cut line is an interval. Each layer gives a list of
intervals (spans) at its height; together they draw the section: walls, shells
and infill as they were sliced. The open space between spans is a gap: a slot,
a channel, a clearance, measured as sliced (bead centres plus half widths),
not as printed.
"""

from __future__ import annotations

import math
from array import array
from pathlib import Path

from . import layers as layers_mod

EXT = len(layers_mod.EXT_FIELDS)


def _layer_floats(folder: Path, plate: int, analysis: dict, index: int) -> array:
    blob = layers_mod.layer_blob(folder, plate, analysis, index)
    data = array("f")
    data.frombytes(blob[8:])
    return data


def cut(folder: Path, plate: int, analysis: dict, axis: str, at: float,
        lo: float | None = None, hi: float | None = None) -> dict:
    """Spans per layer where the plane `axis` = `at` crosses material.

    axis "x": plane x = at, spans along y. axis "y": plane y = at, spans along x.
    lo/hi limit the spans to part of the cut line.
    """
    if axis not in ("x", "y"):
        raise ValueError("axis must be x or y")
    out_layers = []
    for layer in analysis["layers"]:
        if layer["index"] == 0 or layer["ext_count"] == 0:
            continue
        data = _layer_floats(folder, plate, analysis, layer["index"])
        spans = []
        for i in range(layer["ext_count"]):
            o = i * EXT
            x0, y0, x1, y1, w = data[o], data[o + 1], data[o + 2], data[o + 3], data[o + 4]
            if axis == "y":  # swap so the plane is always "u = at", spans along v
                x0, y0, x1, y1 = y0, x0, y1, x1
            du, dv = x1 - x0, y1 - y0
            length = math.hypot(du, dv)
            half = w / 2
            if length == 0:
                continue
            if min(x0, x1) - half > at or max(x0, x1) + half < at:
                continue
            if abs(du) < 1e-6 * length:
                # bead runs along the cut plane: it is cut if the plane is within its half width
                if abs(x0 - at) <= half:
                    a, b = min(y0, y1), max(y0, y1)
                    spans.append((a - half, b + half, int(data[o + 9])))
                continue
            t = (at - x0) / du
            # parameter range where the strip still meets the plane near the segment's ends
            slack = half * abs(dv) / (abs(du) * length) if length else 0
            if t < -slack or t > 1 + slack:
                continue
            v = y0 + t * dv
            ext = half * length / abs(du)  # half length of the strip's intersection with the plane
            a = max(v - ext, min(y0, y1) - half)  # a bead nearly along the plane: no longer than the bead
            b = min(v + ext, max(y0, y1) + half)
            if b > a:
                spans.append((a, b, int(data[o + 9])))
        if lo is not None or hi is not None:
            spans = [(max(a, lo if lo is not None else a), min(b, hi if hi is not None else b), f)
                     for a, b, f in spans if (hi is None or a < hi) and (lo is None or b > lo)]
        spans.sort()
        merged: list[list] = []
        for a, b, f in spans:
            if merged and a <= merged[-1][1] + 1e-6 and merged[-1][2] == f:
                merged[-1][1] = max(merged[-1][1], b)
            else:
                merged.append([a, b, f])
        out_layers.append({"index": layer["index"], "z": layer["z"], "h": layer["h"],
                           "spans": [[round(a, 3), round(b, 3), f] for a, b, f in merged]})
    return {"axis": axis, "at": at, "features": analysis["features"], "layers": out_layers,
            **properties(out_layers)}


def _union(spans: list[list]) -> list[tuple[float, float]]:
    out: list[list[float]] = []
    for a, b, _ in sorted(spans):
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


def properties(layers: list[dict]) -> dict:
    """Area, centroid height and second moment of area of the cut material.

    Bending about the horizontal axis in the cut plane (the plate bending out
    of its plane). Perfect bonding between beads and layers is assumed, so it
    is an upper bound useful for comparing variants, not a strength value.
    """
    area = 0.0
    first = 0.0
    rows = []
    for l in layers:
        width = sum(b - a for a, b in _union(l["spans"]))
        h = l["h"]
        zc = l["z"] - h / 2
        rows.append((width, h, zc))
        area += width * h
        first += width * h * zc
    if area <= 0:
        return {"area_mm2": 0.0, "centroid_z": None, "i_mm4": 0.0}
    zbar = first / area
    inertia = sum(w * (h ** 3 / 12 + h * (zc - zbar) ** 2) for w, h, zc in rows)
    return {"area_mm2": round(area, 3), "centroid_z": round(zbar, 4), "i_mm4": round(inertia, 3)}


def gaps(section: dict, min_width: float = 0.05) -> list[dict]:
    """Open spans between material on each layer (slots, channels, clearances), as sliced."""
    out = []
    for l in section["layers"]:
        solid = _union(l["spans"])
        for (a0, b0), (a1, b1) in zip(solid, solid[1:]):
            width = a1 - b0
            if width >= min_width:
                out.append({"index": l["index"], "z": l["z"], "from": round(b0, 3), "to": round(a1, 3),
                            "width": round(width, 3)})
    return out
