"""Goal metrics for a run: measured by the slicer, computed from the toolpaths, or proxies.

Every metric says where it comes from (catalog.json, "source"), so a proxy is
never shown as a measurement.
"""

from __future__ import annotations


SLAB_AREA = 0.5  # layers whose footprint is at least this share of the largest one form the slab


def stiffness_proxy(analysis: dict) -> float | None:
    """Bending stiffness of the part's main slab, relative to a solid slab.

    The slab is the layers whose footprint (bounding box) is at least half the
    largest layer's, so a tall rib or tab on a flat part (the v7 lid's front
    bar) doesn't count as part of the plate. Material fraction per layer is the
    extruded volume per mm of height relative to a solid slab layer. Beam
    theory with perfect layer bonding, about the slab's own neutral axis:
    meaningful for flat parts, and only to compare variants of the same part.
    """
    layers = [l for l in analysis["layers"] if l["index"] > 0 and l["ext_count"] > 0 and l["h"] > 0 and l.get("bbox")]
    if not layers:
        return None
    area = lambda l: (l["bbox"][2] - l["bbox"][0]) * (l["bbox"][3] - l["bbox"][1])
    biggest = max(area(l) for l in layers)
    rows = [(l["z"], l["h"], l["volume_mm3"] / l["h"]) for l in layers if area(l) >= SLAB_AREA * biggest]
    if not rows:
        return None
    # Reference "solid" density: the median of the densest half of the slab layers.
    # Not the maximum: Bambu's thick-bridge layer extrudes ~1.4x a solid layer
    # (round 0.4 mm lines in a 0.2 mm layer) and would make real solid layers look 70% full.
    dens = sorted((v for _, _, v in rows), reverse=True)
    top = dens[: max(1, (len(dens) + 1) // 2)]
    ref = top[len(top) // 2]
    if ref <= 0:
        return None
    parts = [(z - h / 2, h, min(v / ref, 1.0)) for z, h, v in rows]
    mass = sum(f * h for _, h, f in parts)
    zbar = sum(f * h * zc for zc, h, f in parts) / mass
    inertia = sum(f * (h ** 3 / 12 + h * (zc - zbar) ** 2) for zc, h, f in parts)
    height = max(z for z, _, _ in rows) - min(z - h for z, h, _ in rows)
    return inertia / (height ** 3 / 12) if height > 0 else None


def filament_cost(res, fil: dict) -> float | None:
    """Each filament's grams (from Bambu's result) at its own price; None if a price is missing."""
    prices = {f["slot"]: f.get("cost_per_kg") for f in fil.get("filaments", [])}
    grams = getattr(res, "filament_g", None) or {}
    if grams and prices:
        if any(prices.get(slot) is None for slot in grams):
            return None
        return sum(g * prices[slot] / 1000.0 for slot, g in grams.items())
    c = fil.get("cost_per_kg")
    return res.grams * c / 1000.0 if c else None


def run_metrics(res, analysis: dict) -> dict:
    fil = analysis.get("filament") or {}
    stiff = stiffness_proxy(analysis)
    return {
        "time_s": res.total_s,
        "grams": res.grams,
        "cost": filament_cost(res, fil),
        "stiffness": None if stiff is None else round(stiff * 100, 2),
        "near_cap_s": analysis["flow"]["near_cap_s"] if analysis["flow"]["cap"] else None,
    }
