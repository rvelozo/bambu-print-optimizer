"""Print-quality proxies, measured on the toolpaths of one slice.

Four aspects. Each is a number the settings move and that is known to relate to
how a print looks. None of them is a measurement of a printed part:

    slopes  stair-step height on sloped and curved sides
            (layer step x sine of the slope from vertical, the cusp height)
    walls   speed the outer walls actually print at, after acceleration,
            where they sit on the layer below
    air     speed of what prints over air: walls that hang out a quarter of
            their width or more past the layer below (where Bambu's overhang
            speeds start), overhang walls, and bridges over open space
    top     top surface speed, and the solid layers under the top surface

Slopes and overhangs come from comparing each layer's outer walls with the
layer below. The offset d between them gives the slope (tan from vertical =
d / layer step). The part of the wall's width beyond the lower wall, on the
side away from the part's own material, is the overhang. tests/test_quality.py
checks both on test shapes: against the analytic step height, and against the
overhang speeds Bambu Studio itself chose.

Layer thickness comes from gcode.Analysis.layer_heights(), which leaves out the
bridge lines' own height tag (0.4 mm for a thick bridge in a 0.2 mm layer).
"""

from __future__ import annotations

import math

VERSION = 3
CELL = 1.0             # mm, grid cell for finding toolpaths near a point
SAMPLE = 2.0           # mm between sample points along a wall
REACH = 3.0            # mm: an outer wall farther than this from the layer below's walls is unmatched
OVERHANG = 0.25        # share of the width past the layer below that counts as over air
SLOPED = 0.1           # a side counts as sloped when its step is at least this share of a full layer step
TOP_DEPTH = 3.0        # mm: stop counting solid layers under a top surface after this
VERTICAL = 0.005       # mm: offsets below this are a vertical wall
ACROSS = (-0.4, -0.2, 0.0, 0.2, 0.4)  # points across a line's width, as shares of it

WALLS = ("Outer wall", "Overhang wall")
SOLID = ("Internal solid infill", "Bridge", "Top surface", "Bottom surface", "Gap infill", "Floating vertical shell")
ASPECTS = ("slopes", "walls", "air", "top")
OVERHANG_CLASSES = ("25-50", "50-75", "75-100")  # % of the width over air: overhang_2_4 .. overhang_4_4 speeds


class _Grid:
    """Segments of one layer, bucketed by the cells their bounding box or line touches."""

    __slots__ = ("cells", "a")

    def __init__(self, a, segs):
        self.a = a
        self.cells: dict[tuple[int, int], list[int]] = {}
        for i in segs:
            self.add(i)

    def add(self, i: int) -> None:
        a, cells = self.a, self.cells
        x0, y0, x1, y1 = a.x0[i], a.y0[i], a.x1[i], a.y1[i]
        cx0, cx1 = math.floor(min(x0, x1) / CELL), math.floor(max(x0, x1) / CELL)
        cy0, cy1 = math.floor(min(y0, y1) / CELL), math.floor(max(y0, y1) / CELL)
        if (cx1 - cx0 + 1) * (cy1 - cy0 + 1) <= 4:
            for cx in range(cx0, cx1 + 1):
                for cy in range(cy0, cy1 + 1):
                    cells.setdefault((cx, cy), []).append(i)
            return
        n = int(math.hypot(x1 - x0, y1 - y0) / (CELL * 0.5)) + 1
        last = None
        for k in range(n + 1):
            t = k / n
            c = (math.floor((x0 + (x1 - x0) * t) / CELL), math.floor((y0 + (y1 - y0) * t) / CELL))
            if c != last:
                bucket = cells.setdefault(c, [])
                if not bucket or bucket[-1] != i:
                    bucket.append(i)
                last = c

    def nearest(self, px: float, py: float, reach: float):
        """(distance, qx, qy) to the closest segment within reach, or None."""
        a, cells = self.a, self.cells
        cx, cy = math.floor(px / CELL), math.floor(py / CELL)
        # distance from p to the edge of its own cell: nothing in ring r can be closer than (r - 1) cells plus this
        edge = min(px - cx * CELL, (cx + 1) * CELL - px, py - cy * CELL, (cy + 1) * CELL - py)
        best, bx, by = math.inf, 0.0, 0.0
        for r in range(int(math.ceil(reach / CELL)) + 1):
            if r and best <= (r - 1) * CELL + edge:
                break
            for dx in range(-r, r + 1):
                for dy in ((-r, r) if abs(dx) != r else range(-r, r + 1)):
                    for i in cells.get((cx + dx, cy + dy), ()):
                        x0, y0 = a.x0[i], a.y0[i]
                        vx, vy = a.x1[i] - x0, a.y1[i] - y0
                        l2 = vx * vx + vy * vy
                        t = 0.0 if l2 == 0 else ((px - x0) * vx + (py - y0) * vy) / l2
                        t = 0.0 if t < 0 else 1.0 if t > 1 else t
                        qx, qy = x0 + t * vx, y0 + t * vy
                        d = math.hypot(px - qx, py - qy)
                        if d < best:
                            best, bx, by = d, qx, qy
        return (best, bx, by) if best <= reach else None

    def within(self, px: float, py: float, r: float) -> bool:
        """Whether any segment passes within r of the point (r up to one cell)."""
        a, cells = self.a, self.cells
        cx, cy = math.floor(px / CELL), math.floor(py / CELL)
        fx, fy = px - cx * CELL, py - cy * CELL
        xs = (0, -1) if fx < r else (0, 1) if CELL - fx < r else (0,)
        ys = (0, -1) if fy < r else (0, 1) if CELL - fy < r else (0,)
        r2 = r * r
        for dx in xs:
            for dy in ys:
                for i in cells.get((cx + dx, cy + dy), ()):
                    x0, y0 = a.x0[i], a.y0[i]
                    vx, vy = a.x1[i] - x0, a.y1[i] - y0
                    l2 = vx * vx + vy * vy
                    t = 0.0 if l2 == 0 else ((px - x0) * vx + (py - y0) * vy) / l2
                    t = 0.0 if t < 0 else 1.0 if t > 1 else t
                    ex, ey = px - x0 - t * vx, py - y0 - t * vy
                    if ex * ex + ey * ey <= r2:
                        return True
        return False

    def crosses(self, x0: float, y0: float, x1: float, y1: float, r: float) -> bool:
        """Whether any segment comes within r of the segment from (x0, y0) to (x1, y1)."""
        a, cells = self.a, self.cells
        n = int(math.hypot(x1 - x0, y1 - y0) / (CELL * 0.5)) + 1
        seen: set[int] = set()
        for k in range(n + 1):
            t = k / n
            cx, cy = math.floor((x0 + (x1 - x0) * t) / CELL), math.floor((y0 + (y1 - y0) * t) / CELL)
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for i in cells.get((cx + dx, cy + dy), ()):
                        if i in seen:
                            continue
                        seen.add(i)
                        if _segment_distance(x0, y0, x1, y1, a.x0[i], a.y0[i], a.x1[i], a.y1[i]) <= r:
                            return True
        return False

    def occupied(self, px: float, py: float) -> bool:
        return (math.floor(px / CELL), math.floor(py / CELL)) in self.cells


def _point_segment(px, py, x0, y0, x1, y1) -> float:
    vx, vy = x1 - x0, y1 - y0
    l2 = vx * vx + vy * vy
    t = 0.0 if l2 == 0 else max(0.0, min(1.0, ((px - x0) * vx + (py - y0) * vy) / l2))
    return math.hypot(px - x0 - t * vx, py - y0 - t * vy)


def _segment_distance(ax, ay, bx, by, cx, cy, dx, dy) -> float:
    """Shortest distance between segments AB and CD (0 when they cross)."""
    def side(px, py, qx, qy, rx, ry):
        return (qx - px) * (ry - py) - (qy - py) * (rx - px)
    d1, d2 = side(cx, cy, dx, dy, ax, ay), side(cx, cy, dx, dy, bx, by)
    d3, d4 = side(ax, ay, bx, by, cx, cy), side(ax, ay, bx, by, dx, dy)
    if ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0)) and d1 and d2 and d3 and d4:
        return 0.0
    return min(_point_segment(ax, ay, cx, cy, dx, dy), _point_segment(bx, by, cx, cy, dx, dy),
               _point_segment(cx, cy, ax, ay, bx, by), _point_segment(dx, dy, ax, ay, bx, by))


def _median(pairs: list[tuple[float, float]]):
    """Weighted median of (value, weight) pairs; None when there are none.

    The solid layers under a top surface are counted per sample, and the edges of
    solid regions shift a little with unrelated settings (infill density moved the
    mean by 1-2 % on 3DBenchy); the typical count doesn't.
    """
    total = sum(w for _, w in pairs)
    if not total:
        return None
    acc = 0.0
    for v, w in sorted(pairs):
        acc += w
        if acc >= total / 2:
            return v
    return pairs[-1][0]


def _key(a, i) -> tuple:
    return (round(a.x0[i], 2), round(a.y0[i], 2), round(a.x1[i], 2), round(a.y1[i], 2))


def measure(a, trace=None) -> dict:
    """The four aspects of a gcode.Analysis, with the surface area each one describes (mm²).

    trace(segment, length, offset, hung), if given, sees every wall sample that was
    checked for overhang (for tests and diagnostics).
    """
    ids = {name: i for i, name in enumerate(a.features)}
    pick = lambda names: {ids[n] for n in names if n in ids}
    wall_f, ovh_f = pick(WALLS), pick(("Overhang wall",))
    bridge_f, top_f, solid_f = pick(("Bridge",)), pick(("Top surface",)), pick(SOLID)

    n_layers = len(a.layers)
    by_layer: list[list[int]] = [[] for _ in range(n_layers)]
    for i, layer in enumerate(a.layer):
        by_layer[layer].append(i)
    z = [l["z"] for l in a.layers]
    step = a.layer_heights()

    slope_area = slope_cusp = side_area = 0.0
    wall_len = wall_t = wall_area = 0.0
    air_len = air_t = air_area = 0.0
    air_walls = air_bridges = inner_bridges = 0.0
    # length and time over air by kind: walls by share of width (as Bambu's overhang speeds), and bridges
    air_kind = {c: [0.0, 0.0] for c in OVERHANG_CLASSES + ("bridges",)}
    top_len = top_t = top_area = 0.0
    top_solid: list[tuple[float, float]] = []

    walls_below: list[_Grid | None] = [None] * n_layers  # outer walls per layer, kept for two layers
    keys_below: set = set()
    other_grid: dict[int, _Grid] = {}   # everything but outer walls, per layer
    solid_grid: dict[int, _Grid] = {}

    def others(k: int) -> _Grid:
        if k not in other_grid:
            other_grid[k] = _Grid(a, [i for i in by_layer[k] if a.feat[i] not in wall_f])
        return other_grid[k]

    def printed(k: int, px: float, py: float, r: float) -> bool:
        """Whether layer k has anything printed within r of the point."""
        return walls_below[k].within(px, py, r) or others(k).within(px, py, r)

    def solid(k: int) -> _Grid:
        if k not in solid_grid:
            solid_grid[k] = _Grid(a, [i for i in by_layer[k] if a.feat[i] in solid_f])
        return solid_grid[k]

    for L in range(1, n_layers):
        segs = by_layer[L]
        h = step[L]
        # the first layer is inset by elephant foot compensation, so the second layer would look like
        # it hangs over it all round: compare from the third layer on
        below = walls_below[L - 1] if L >= 3 else None
        below2 = walls_below[L - 2] if L >= 4 else None
        keys_here = set()
        for i in segs:
            f = a.feat[i]
            x0, y0, x1, y1 = a.x0[i], a.y0[i], a.x1[i], a.y1[i]
            ln = math.hypot(x1 - x0, y1 - y0)
            if ln == 0:
                continue
            t = a.time[i]
            w = a.width[i]
            if f in wall_f:
                k = _key(a, i)
                keys_here.add(k)
                if f not in ovh_f and (below is None or k in keys_below or (k[2], k[3], k[0], k[1]) in keys_below):
                    # first layer, or the same wall as the layer below: vertical and supported
                    wall_len += ln; wall_t += t; wall_area += ln * h; side_area += ln * h
                    continue
                out = None   # unit normal pointing away from this layer's own material
                side_done = False
                n = max(1, math.ceil(ln / SAMPLE))
                part = ln / n
                for s in range(n):
                    u = (s + 0.5) / n
                    px, py = x0 + (x1 - x0) * u, y0 + (y1 - y0) * u
                    hit = below.nearest(px, py, REACH)
                    d = hit[0] if hit else None
                    # slopes: a step that continues from the layer below is a slope; a one-layer step is a ledge
                    if d is None or d < VERTICAL:
                        side_area += part * h
                    else:
                        cusp = 0.0
                        if below2 is not None:
                            hit2 = below2.nearest(hit[1], hit[2], REACH)
                            if hit2 and 0.5 * d <= hit2[0] <= 2 * d and \
                                    (hit[1] - px) * (hit2[1] - hit[1]) + (hit[2] - py) * (hit2[2] - hit[2]) > 0.5 * d * hit2[0]:
                                cusp = h * d / math.hypot(d, h)
                        area = part * (math.hypot(d, h) if cusp else h)
                        side_area += area
                        if cusp >= SLOPED * h:
                            slope_area += area
                            slope_cusp += cusp * area
                    # overhang: how much of the width is past the layer below
                    if f in ovh_f:
                        hung = 1.0
                    elif d is not None and d < OVERHANG * w:
                        hung = 0.0
                    else:
                        if not side_done:
                            side_done = True
                            lx, ly = -(y1 - y0) / ln, (x1 - x0) / ln
                            mx, my = (x0 + x1) / 2, (y0 + y1) / 2
                            left = others(L).within(mx + lx * w, my + ly * w, 0.8 * w)
                            right = others(L).within(mx - lx * w, my - ly * w, 0.8 * w)
                            out = (-lx, -ly) if left and not right else (lx, ly) if right and not left else None
                        if d is not None and out is not None:
                            hung = ((px - hit[1]) * out[0] + (py - hit[2]) * out[1]) / w
                        else:
                            # no wall below nearby, or a thin feature with nothing beside it on this
                            # layer: the share of the line's width with nothing printed under it
                            lx, ly = -(y1 - y0) / ln, (x1 - x0) / ln
                            hung = sum(not printed(L - 1, px + lx * o * w, py + ly * o * w, 0.5 * w)
                                       for o in ACROSS) / len(ACROSS)
                    if trace is not None:
                        trace(i, part, d, hung)
                    if hung >= OVERHANG:
                        air_len += part; air_t += t * part / ln; air_area += part * w; air_walls += part
                        kind = air_kind["75-100" if hung >= 0.75 else "50-75" if hung >= 0.5 else "25-50"]
                        kind[0] += part; kind[1] += t * part / ln
                    else:
                        wall_len += part; wall_t += t * part / ln; wall_area += part * h
            elif f in bridge_f:
                # over open space, or over sparse infill inside the part (hidden): anything printed on
                # the layer below under the line, leaving out a millimetre at each end where it's anchored
                inner = ln <= 2.0
                if not inner:
                    ux, uy = (x1 - x0) / ln, (y1 - y0) / ln
                    sx0, sy0, sx1, sy1 = x0 + ux, y0 + uy, x1 - ux, y1 - uy
                    inner = walls_below[L - 1].crosses(sx0, sy0, sx1, sy1, 0.5 * w) or \
                        others(L - 1).crosses(sx0, sy0, sx1, sy1, 0.5 * w)
                if inner:
                    inner_bridges += ln
                else:
                    air_len += ln; air_t += t; air_area += ln * w; air_bridges += ln
                    air_kind["bridges"][0] += ln; air_kind["bridges"][1] += t
            elif f in top_f:
                top_len += ln; top_t += t; top_area += ln * w
                n = max(1, math.ceil(ln / SAMPLE))
                for s in range(n):
                    u = (s + 0.5) / n
                    px, py = x0 + (x1 - x0) * u, y0 + (y1 - y0) * u
                    thick, count, j = h, 1, L - 1
                    while j >= 1 and thick < TOP_DEPTH and solid(j).occupied(px, py):
                        thick += step[j]
                        count += 1
                        j -= 1
                    top_solid.append((thick, count, ln / n))
        walls_below[L] = _Grid(a, [i for i in segs if a.feat[i] in wall_f])
        if L >= 2:
            walls_below[L - 2] = None
        other_grid.pop(L - 2, None)
        for k in [k for k in solid_grid if z[L] - z[k] > TOP_DEPTH + 1.0]:
            del solid_grid[k]
        keys_below = keys_here
    weight = sum(wt for _, _, wt in top_solid)

    def r(x, nd=3):
        return None if x is None else round(x, nd)

    return {
        "v": VERSION,
        "slopes": {"step_um": r(1000 * slope_cusp / slope_area if slope_area else None, 1),
                   "area_mm2": round(slope_area, 1), "share": r(slope_area / side_area if side_area else None)},
        "walls": {"speed": r(wall_len / wall_t if wall_t else None, 2), "area_mm2": round(wall_area, 1),
                  "length_mm": round(wall_len, 1)},
        "air": {"speed": r(air_len / air_t if air_t else None, 2), "area_mm2": round(air_area, 1),
                "walls_mm": round(air_walls, 1), "bridges_mm": round(air_bridges, 1),
                "kinds": {c: {"mm": round(ln, 1), "speed": r(ln / t if t else None, 2)} for c, (ln, t) in air_kind.items()},
                "hidden_bridges_mm": round(inner_bridges, 1)},
        "top": {"speed": r(top_len / top_t if top_t else None, 2), "area_mm2": round(top_area, 1),
                "solid_mm": r(sum(v * wt for v, _, wt in top_solid) / weight if weight else None),
                "solid_layers": _median([(c, wt) for _, c, wt in top_solid])},
    }


LIMIT_SHARE = 0.01  # aspects covering less of the measured surface than this don't count for the quality limit


def score(run: dict | None, base: dict | None) -> dict | None:
    """A run's quality aspects against the current settings' (both from measure()).

    Each aspect scores 1 when it is as good as now and above 1 when better: the
    current step height or speed over the run's (lower is better for both).

    Over air, speeds are compared kind by kind (each overhang class, and
    bridges), weighted by the current lengths. Otherwise thicker layers, which
    push the same overhang into a steeper class that Bambu prints slower, would
    look like a better finish. Top surfaces also scale by the number of solid
    layers under them, which counts only when there are fewer than now: a thin
    top shows the infill through, but nothing says more layers than now look
    better.

    The index is the geometric mean of the scores, each weighted by the surface
    its aspect covers on the current slice. "worst" is the lowest score among
    aspects covering at least LIMIT_SHARE of that surface, for a limit that no
    aspect may break.
    """
    if not run or not base or run.get("v") != base.get("v"):
        return None
    aspects = {}
    for name in ASPECTS:
        now, then = base.get(name) or {}, run.get(name) or {}
        area = now.get("area_mm2") or 0.0
        key = "step_um" if name == "slopes" else "speed"
        if area <= 0 or not now.get(key) or not then.get(key):
            continue
        s = now[key] / then[key]
        if name == "air":
            pairs = [(k["mm"], k["speed"], (then.get("kinds") or {}).get(c, {}).get("speed"))
                     for c, k in (now.get("kinds") or {}).items() if k["mm"] >= 1.0 and k["speed"]]
            pairs = [(mm, v0, v1) for mm, v0, v1 in pairs if v1]
            if pairs:
                s = math.exp(sum(mm * math.log(v0 / v1) for mm, v0, v1 in pairs) / sum(mm for mm, _, _ in pairs))
        if name == "top" and now.get("solid_layers") and then.get("solid_layers") is not None:
            s *= min(1.0, then["solid_layers"] / now["solid_layers"])
        aspects[name] = {"score": s, "area_mm2": area, "now": now, "then": then}
    if not aspects:
        return None
    total = sum(x["area_mm2"] for x in aspects.values())
    index = math.exp(sum(x["area_mm2"] * math.log(x["score"]) for x in aspects.values()) / total)
    counted = [x["score"] for x in aspects.values() if x["area_mm2"] >= LIMIT_SHARE * total]
    for x in aspects.values():
        x["weight"] = round(x["area_mm2"] / total, 4)
        x["score"] = round(100 * x["score"], 2)
    return {"index": round(100 * index, 2), "worst": round(100 * min(counted), 2), "aspects": aspects}
