"""Bambu Studio G-code: per-layer toolpaths and the slicer's own time estimate.

The time model replays the G-code through a Python port of the estimator in
Bambu Studio 02.08.04.57 (src/libslic3r/GCode/GCodeProcessor.cpp): per-axis
speed and acceleration limits, Bambu's centripetal speed limit on gentle
corners, its vector-jerk junction speed, forward and reverse lookahead over a
64-block window, trapezoidal speed profiles, and its bookkeeping (travel booked
as "Undefined" outside custom G-code, dwells added to the next queued block,
G29 compensation inside M622 J1, filament load time as "Flush"). The tests
compare its totals with result.json from the real CLI.

Everything here is stdlib: segments are kept in typed arrays, not objects.

The estimator is a port of GCodeProcessor.cpp from Bambu Studio
(https://github.com/bambulab/BambuStudio), copyright Bambu Lab and the
PrusaSlicer authors, licensed under the GNU AGPL v3. This port is distributed
under the same license (see LICENSE).
"""

from __future__ import annotations

import gzip
import math
import struct
from array import array
from pathlib import Path

QUEUE = 64             # TimeProcessor::Planner::queue_size
REFRESH = 4 * QUEUE    # TimeProcessor::Planner::refresh_threshold
MIN_PREV_FEED = 1e-4   # PREVIOUS_FEEDRATE_THRESHOLD

# Move types (EMoveType subset).
EXTRUDE, TRAVEL, WIPE, RETRACT, UNRETRACT, TOOLCHANGE = range(6)
MOVE_NAMES = ["extrude", "travel", "wipe", "retract", "unretract", "tool change"]

# Feature tags Bambu books under another role name (GCodeProcessor::set_extrusion_role).
ROLE_ALIASES = {"Floating vertical shell": "Internal solid infill"}
KNOWN_ROLES = {"Inner wall", "Outer wall", "Overhang wall", "Sparse infill", "Internal solid infill",
               "Top surface", "Bottom surface", "Ironing", "Support ironing", "Bridge", "Gap infill",
               "Skirt", "Brim", "Support", "Support interface", "Support transition", "Prime tower",
               "Custom", "Multiple", "Flush"}


class _Block:
    __slots__ = ("dist", "accel", "cruise", "entry", "max_entry", "safe", "nominal", "recalc",
                 "acc_until", "dec_after", "cr", "role", "layer", "mtype", "prepare", "seg")

    def __init__(self):
        self.dist = self.accel = self.cruise = self.entry = self.max_entry = self.safe = 0.0
        self.acc_until = self.dec_after = self.cr = 0.0
        self.nominal = self.recalc = self.prepare = False
        self.role = 0
        self.layer = 1
        self.mtype = TOOLCHANGE
        self.seg = -1


def _trapezoid(b: _Block, entry: float, exit_: float) -> None:
    a = b.accel
    cruise = b.cruise
    d = b.dist
    if a != 0:
        acc_d = (cruise * cruise - entry * entry) / (2 * a)
        dec_d = (exit_ * exit_ - cruise * cruise) / (-2 * a)
    else:
        acc_d = dec_d = 0.0
    acc_d = acc_d if acc_d > 0 else 0.0
    dec_d = dec_d if dec_d > 0 else 0.0
    cruise_d = d - acc_d - dec_d
    cr = cruise
    if cruise_d < 0:
        acc_d = (2 * a * d - entry * entry + exit_ * exit_) / (4 * a) if a != 0 else 0.0
        acc_d = 0.0 if acc_d < 0 else (d if acc_d > d else acc_d)
        cruise_d = 0.0
        v2 = entry * entry + 2 * a * acc_d
        cr = math.sqrt(v2) if v2 > 0 else 0.0
    b.acc_until = acc_d
    b.dec_after = acc_d + cruise_d
    b.cr = cr


def _block_time(b: _Block) -> float:
    a = b.accel
    entry = b.entry
    cr = b.cr
    if a != 0:
        v2 = entry * entry + 2 * a * b.acc_until
        t_acc = ((math.sqrt(v2) if v2 > 0 else 0.0) - entry) / a
        v2 = cr * cr - 2 * a * (b.dist - b.dec_after)
        t_dec = ((math.sqrt(v2) if v2 > 0 else 0.0) - cr) / -a
    else:
        t_acc = t_dec = 0.0
    t_cruise = (b.dec_after - b.acc_until) / cr if cr != 0 else 0.0
    return t_acc + t_cruise + t_dec


class Analysis:
    """Result of analyze(): toolpath segments, per-layer data and time totals."""

    def __init__(self):
        self.config: dict[str, str] = {}
        self.header: dict[str, float | str] = {}
        self.features: list[str] = []   # raw FEATURE tags, ids used by segments
        self.roles: list[str] = []      # time roles as Bambu names them
        self.layers: list[dict] = [{"z": 0.0, "h": 0.0}]  # index 0 = before the first layer change
        # extrusion segments (structure of arrays)
        self.x0, self.y0, self.x1, self.y1 = array("f"), array("f"), array("f"), array("f")
        self.width, self.height = array("f"), array("f")
        self.speed, self.flow, self.time, self.volume = array("f"), array("f"), array("f"), array("f")
        self.feat = array("B")
        self.layer = array("H")
        # travel and wipe moves with XY motion, for display
        self.tx0, self.ty0, self.tx1, self.ty1 = array("f"), array("f"), array("f"), array("f")
        self.ttime = array("f")
        self.tlayer = array("H")
        # seams: where an outer wall loop starts after a travel or another feature
        self.seam_x, self.seam_y = array("f"), array("f")
        self.seam_layer = array("H")
        # bookkeeping, as GCodeProcessor::TimeMachine keeps it
        self.total_s = 0.0
        # The same sums in single precision, as Bambu Studio adds them up. Block times
        # below a float's resolution are partly lost: 6 s of a 3.5 h plate of short arc
        # segments, 26 s of the v7 panel's outer walls.
        self.bambu_total_s = 0.0
        self.bambu_roles_time: dict[int, float] = {}
        self.bambu_moves_time: dict[int, float] = {}
        self.prepare_s = 0.0
        self.roles_time: dict[int, float] = {}
        self.moves_time: dict[int, float] = {}
        self.layer_time: dict[int, float] = {}   # by layer index (0 = start G-code)
        self.layer_role_time: dict[tuple[int, int], float] = {}
        self.extruded_mm = 0.0  # filament fed by extrusion moves (what the header totals)

    def _id(self, table: list[str], name: str) -> int:
        try:
            return table.index(name)
        except ValueError:
            table.append(name)
            return len(table) - 1

    def time_by_feature(self, bambu: bool = False) -> dict[str, float]:
        """Seconds per feature, keyed like result.json (roles plus "Travel").

        bambu=True gives the sums as Bambu Studio adds them (single precision),
        to compare with its numbers; the default exact sums are the better
        estimate of the print itself.
        """
        roles, moves = (self.bambu_roles_time, self.bambu_moves_time) if bambu else (self.roles_time, self.moves_time)
        out = {self.roles[r]: t for r, t in roles.items()}
        out["Travel"] = moves.get(TRAVEL, 0.0)
        return out

    def layer_heights(self) -> list[float]:
        """Each layer's thickness: the LAYER_HEIGHT its toolpaths carry, most of them by length.

        Bridge lines are left out: Bambu tags a thick bridge with its line's height
        (0.4 mm in a 0.2 mm layer). The tag a layer ends on can be that one, so
        layers[i]["h"] is not always the layer's thickness. A layer with nothing
        else printed gets its Z step.
        """
        bridge = {i for i, name in enumerate(self.features) if name == "Bridge"}
        votes: list[dict[float, float]] = [{} for _ in self.layers]
        for i, layer in enumerate(self.layer):
            if self.feat[i] in bridge:
                continue
            h = round(self.height[i], 4)
            v = votes[layer]
            v[h] = v.get(h, 0.0) + math.hypot(self.x1[i] - self.x0[i], self.y1[i] - self.y0[i])
        out = []
        for k, layer in enumerate(self.layers):
            if votes[k]:
                out.append(max(votes[k].items(), key=lambda kv: kv[1])[0])
            else:
                out.append(round(layer["z"] - (self.layers[k - 1]["z"] if k > 1 else 0.0), 4) if k else 0.0)
        return out

    @property
    def model_s(self) -> float:
        """Bambu's main_predication: total minus start-up (prepare) and flush time."""
        flush = self.roles_time.get(self.roles.index("Flush"), 0.0) if "Flush" in self.roles else 0.0
        return self.total_s - self.prepare_s - flush

    @property
    def bambu_model_s(self) -> float:
        flush = self.roles_time.get(self.roles.index("Flush"), 0.0) if "Flush" in self.roles else 0.0
        return self.bambu_total_s - self.prepare_s - flush


def _f32(x: float) -> float:
    return struct.unpack("f", struct.pack("f", x))[0]


def _first(cfg: dict, key: str, default: float = 0.0) -> float:
    v = cfg.get(key)
    if v is None:
        return default
    try:
        return float(str(v).split(",")[0].strip().rstrip("%"))
    except ValueError:
        return default


def open_text(path: str | Path):
    path = Path(path)
    if path.suffix == ".gz":
        return gzip.open(path, "rt", encoding="utf-8", errors="replace")
    return open(path, encoding="utf-8", errors="replace")


def _norm3(v):
    n = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])
    return (v[0] / n, v[1] / n, v[2] / n) if n > 0 else (0.0, 0.0, 0.0)


def analyze(path: str | Path) -> Analysis:
    res = Analysis()
    cfg = res.config
    undefined = res._id(res.roles, "Undefined")
    custom_role = res._id(res.roles, "Custom")
    flush_role = res._id(res.roles, "Flush")

    # ---- machine ---------------------------------------------------------------------
    max_v = [0.0] * 4
    max_a = [0.0] * 4
    jerk = [0.0] * 4
    min_extruding = min_travel = 0.0
    max_accel = max_travel = max_retract = 0.0
    accel = travel_accel = retract_accel = 0.0
    fil_area = math.pi * (1.75 / 2) ** 2
    load_time = unload_time = prepare_comp = 0.0

    def load_config():
        nonlocal min_extruding, min_travel, max_accel, max_travel, max_retract
        nonlocal accel, travel_accel, retract_accel, fil_area, load_time, unload_time, prepare_comp
        for i, ax in enumerate("xyze"):
            max_v[i] = _first(cfg, f"machine_max_speed_{ax}")
            max_a[i] = _first(cfg, f"machine_max_acceleration_{ax}")
            jerk[i] = _first(cfg, f"machine_max_jerk_{ax}")
        min_extruding = _first(cfg, "machine_min_extruding_rate")
        min_travel = _first(cfg, "machine_min_travel_rate")
        max_accel = _first(cfg, "machine_max_acceleration_extruding")
        max_retract = _first(cfg, "machine_max_acceleration_retracting")
        max_travel = _first(cfg, "machine_max_acceleration_travel")
        accel = max_accel if max_accel > 0 else 1500.0
        retract_accel = max_retract if max_retract > 0 else 1500.0
        travel_accel = max_travel if max_travel > 0 else 1250.0
        d = _first(cfg, "filament_diameter", 1.75)
        fil_area = math.pi * (d / 2) ** 2
        load_time = _first(cfg, "machine_load_filament_time")
        unload_time = _first(cfg, "machine_unload_filament_time")
        prepare_comp = _first(cfg, "machine_prepare_compensation_time")

    # ---- interpreter state -----------------------------------------------------------
    sx = sy = sz = se = 0.0          # start position of the next move
    feedrate = 0.0                   # m_feedrate, mm/s
    rel_xyz = False                  # G91
    rel_e = False                    # M83
    feat = res._id(res.features, "Undefined")
    role = undefined
    prepare = False
    wiping = False
    layer_id = 0
    g1_line_id = 0
    measure_g29 = False
    filament = -1
    prev_role, prev_feat = undefined, feat  # role before a FLUSH_START / VFLUSH_START tag
    width = height = 0.0
    config_done = False
    seam_pending = True
    outer_wall = res._id(res.features, "Outer wall")

    # TimeMachine::State curr / prev
    c_feed = c_safe = 0.0
    c_axis = [0.0] * 4
    p_feed = p_safe = 0.0
    p_exit = (0.0, 0.0, 0.0)

    blocks: list[_Block] = []
    pending: list[list] = []         # m_additional_time_buffer: [role or None, seconds]
    roles_time = res.roles_time
    moves_time = res.moves_time
    layer_time = res.layer_time
    layer_role_time = res.layer_role_time
    seg_time = res.time
    tr_time = res.ttime

    def calculate_time(keep: int, additional: float = 0.0, target: int | None = None) -> None:
        nonlocal pending
        if len(blocks) < 2:
            if additional > 0:
                pending.append([target, additional])
            return
        buf = pending + ([[target, additional]] if additional > 0 else [])
        merged: list[list] = []
        for item in buf:
            if merged and merged[-1][0] == item[0]:
                merged[-1][1] += item[1]
            else:
                merged.append(list(item))
        n = len(blocks)
        for i in range(n - 1):
            prev = blocks[i]
            cur = blocks[i + 1]
            if not prev.nominal and prev.entry < cur.entry:
                v2 = prev.entry * prev.entry + 2 * prev.accel * prev.dist
                lim = math.sqrt(v2) if v2 > 0 else 0.0
                e = cur.entry if cur.entry < lim else lim
                if cur.entry != e:
                    cur.entry = e
                    cur.recalc = True
        for i in range(n - 1, 0, -1):
            cur = blocks[i - 1]
            nxt = blocks[i]
            if cur.entry != cur.max_entry:
                if not cur.nominal and cur.max_entry > nxt.entry:
                    v2 = nxt.entry * nxt.entry + 2 * cur.accel * cur.dist
                    lim = math.sqrt(v2) if v2 > 0 else 0.0
                    cur.entry = cur.max_entry if cur.max_entry < lim else lim
                else:
                    cur.entry = cur.max_entry
                cur.recalc = True
        for i in range(1, n):
            cur = blocks[i - 1]
            nxt = blocks[i]
            if cur.recalc or nxt.recalc:
                _trapezoid(cur, cur.entry, nxt.entry)
                cur.recalc = False
        last = blocks[-1]
        _trapezoid(last, last.entry, last.safe)
        last.recalc = False
        m = n - keep
        idx = 0
        for i in range(m):
            b = blocks[i]
            t = _block_time(b)
            move_t = t
            if idx < len(merged):
                r, extra = merged[idx]
                if r is None or r == b.role:
                    t += extra
                    idx += 1
            res.total_s += t
            t32 = _f32(t)
            res.bambu_total_s = _f32(res.bambu_total_s + t32)
            res.bambu_roles_time[b.role] = _f32(res.bambu_roles_time.get(b.role, 0.0) + t32)
            if not (b.prepare and b.mtype == TRAVEL):
                moves_time[b.mtype] = moves_time.get(b.mtype, 0.0) + t
                res.bambu_moves_time[b.mtype] = _f32(res.bambu_moves_time.get(b.mtype, 0.0) + t32)
            roles_time[b.role] = roles_time.get(b.role, 0.0) + t
            layer_time[b.layer] = layer_time.get(b.layer, 0.0) + t
            lr = (b.layer, b.role)
            layer_role_time[lr] = layer_role_time.get(lr, 0.0) + t
            if b.prepare:
                res.prepare_s += t
            if b.seg >= 0:
                if b.mtype == EXTRUDE:
                    seg_time[b.seg] += move_t
                else:
                    tr_time[b.seg] += move_t
        pending = merged[idx:]
        del blocks[:m]

    def push(b: _Block) -> None:
        blocks.append(b)
        if len(blocks) > REFRESH:
            calculate_time(QUEUE)

    def junction(enter, cruise: float, safe: float) -> float:
        vmax = safe
        if blocks and p_feed > MIN_PREV_FEED:
            vmax = p_feed if p_feed < cruise else cruise
            eu = _norm3(p_exit)
            nu = _norm3(enter)
            limited = False
            kmin = 10000.0
            for i in range(3):
                jv = abs(nu[i] - eu[i])
                if jv > 0:
                    limited = True
                    k = jerk[i] / jv
                    if k < kmin:
                        kmin = k
            if limited:
                vmax = kmin
            thr = vmax * 0.99
            if p_safe > thr and safe > thr:
                vmax = safe
        return vmax

    def time_role(mtype: int) -> int:
        return role if (mtype != TRAVEL or role == custom_role) else undefined

    def g1(ex: float, ey: float, ez: float, ee: float, seg_kind: bool) -> None:
        """process_G1 from the start position to (ex, ey, ez, ee)."""
        nonlocal sx, sy, sz, se, c_feed, c_safe, p_feed, p_safe, p_exit, seam_pending
        dx, dy, dz, de = ex - sx, ey - sy, ez - sz, ee - se
        if dx == 0 and dy == 0 and dz == 0 and de == 0:
            return
        if wiping:
            mtype = WIPE
        elif de < 0:
            mtype = TRAVEL if (dx != 0 or dy != 0 or dz != 0) else RETRACT
        elif de > 0:
            if dx == 0 and dy == 0:
                mtype = UNRETRACT if dz == 0 else TRAVEL
            else:
                mtype = EXTRUDE
        else:
            mtype = TRAVEL
        sq = dx * dx + dy * dy + dz * dz
        e_only = sq == 0
        dist = math.sqrt(sq) if not e_only else abs(de)
        inv = 1.0 / dist

        seg = -1
        if mtype == EXTRUDE and feat == outer_wall:
            if seam_pending and seg_kind:
                res.seam_x.append(sx); res.seam_y.append(sy); res.seam_layer.append(min(layer_id, 65535))
            seam_pending = False
        elif mtype != EXTRUDE:
            seam_pending = True
        if seg_kind:
            if mtype == EXTRUDE:
                seg = len(res.x0)
                res.x0.append(sx); res.y0.append(sy); res.x1.append(ex); res.y1.append(ey)
                res.width.append(width); res.height.append(height)
                vol = de * fil_area
                res.volume.append(vol)
                res.speed.append(feedrate)
                res.flow.append(vol * feedrate * inv)
                res.time.append(0.0)
                res.feat.append(feat)
                res.layer.append(min(layer_id, 65535))
                res.extruded_mm += de
            elif (mtype == TRAVEL or mtype == WIPE) and (dx != 0 or dy != 0):
                seg = len(res.tx0)
                res.tx0.append(sx); res.ty0.append(sy); res.tx1.append(ex); res.ty1.append(ey)
                res.ttime.append(0.0)
                res.tlayer.append(min(layer_id, 65535))

        f = (min_travel if feedrate < min_travel else feedrate) if de == 0 else \
            (min_extruding if feedrate < min_extruding else feedrate)
        enter = (0.0, 0.0, 0.0) if e_only else (dx * inv, dy * inv, dz * inv)
        if mtype == TRAVEL:
            acc = travel_accel
        elif e_only:
            acc = retract_accel
        else:
            acc = accel
        # Bambu: limit speed by centripetal acceleration on gentle corners
        if (p_exit[0] != 0 or p_exit[1] != 0) and (enter[0] != 0 or enter[1] != 0) and not e_only:
            l1 = math.hypot(p_exit[0], p_exit[1])
            l2 = math.hypot(enter[0], enter[1])
            v1x, v1y = p_exit[0] / l1, p_exit[1] / l1
            v2x, v2y = enter[0] / l2, enter[1] / l2
            nd = math.hypot(v2x - v1x, v2y - v1y)
            if 0.00001 < nd < 0.5:
                ang = math.atan2(v1x * v2y - v1y * v2x, v1x * v2x + v1y * v2y)
                s2 = math.sqrt((1.0 - math.cos(ang)) * 0.5)
                r = math.sqrt(dx * dx + dy * dy) * 0.5 / s2
                lim = math.sqrt(acc * r)
                if lim < f:
                    f = lim
        axis = [f * dx * inv, f * dy * inv, f * dz * inv, f * de * inv]
        factor = 1.0
        for i in range(4):
            av = abs(axis[i])
            if av != 0 and max_v[i] != 0:
                q = max_v[i] / av
                if q < factor:
                    factor = q
        f *= factor
        cruise = f
        if factor < 1.0:
            for i in range(4):
                axis[i] *= factor
        d4 = (dx, dy, dz, de)
        for i in range(4):
            comp = abs(d4[i]) * inv
            if acc * comp > max_a[i]:
                acc = max_a[i] / comp
        safe = cruise
        for i in range(4):
            if abs(axis[i]) > jerk[i] and jerk[i] < safe:
                safe = jerk[i]
        vmax = junction(enter, cruise, safe)
        v2 = safe * safe + 2 * acc * dist
        v_allow = math.sqrt(v2) if v2 > 0 else 0.0
        b = _Block()
        b.dist = dist
        b.accel = acc
        b.cruise = cruise
        b.entry = vmax if vmax < v_allow else v_allow
        b.max_entry = vmax
        b.safe = safe
        b.nominal = cruise <= v_allow
        b.recalc = True
        b.role = time_role(mtype)
        b.layer = layer_id
        b.mtype = mtype
        b.prepare = prepare
        b.seg = seg
        _trapezoid(b, b.entry, safe)
        c_feed, c_safe = f, safe
        c_axis[:] = axis
        p_feed, p_safe, p_exit = f, safe, enter
        sx, sy, sz, se = ex, ey, ez, ee
        push(b)

    def arc(ex, ey, ez, ee, ci, cj, ccw: bool, p_mode: bool) -> None:
        """process_G2_G3: one block along the arc, tangents as entry/exit directions."""
        nonlocal sx, sy, sz, se, c_feed, c_safe, p_feed, p_safe, p_exit
        cx, cy = sx + ci, sy + cj
        radius = math.hypot(sx - cx, sy - cy)
        if p_mode:
            length = 2 * math.pi * radius
        else:
            a0 = math.atan2(sy - cy, sx - cx)
            a1 = math.atan2(ey - cy, ex - cx)
            sweep = (a1 - a0) if ccw else (a0 - a1)
            while sweep < 0:
                sweep += 2 * math.pi
            if sweep == 0:
                sweep = 2 * math.pi
            length = radius * sweep

        def tangent(px, py):
            rx, ry = px - cx, py - cy
            n = math.hypot(rx, ry)
            if n == 0:
                return (0.0, 0.0, 0.0)
            return ((-ry / n, rx / n, 0.0) if ccw else (ry / n, -rx / n, 0.0))

        start_dir = tangent(sx, sy)
        end_dir = tangent(ex, ey)
        dz, de = ez - sz, ee - se
        if length == 0 and dz == 0:
            return
        mtype = TRAVEL if de == 0 else EXTRUDE
        dist = math.sqrt(length * length + dz * dz)
        inv = 1.0 / dist
        f = (min_travel if feedrate < min_travel else feedrate) if mtype == TRAVEL else \
            (min_extruding if feedrate < min_extruding else feedrate)
        lim = math.sqrt(accel * radius) / (length * inv) if length > 0 else f
        if lim < f:
            f = lim
        axis = [f * length * inv, f * length * inv, f * dz * inv, c_axis[3]]
        factor = 1.0
        for i in range(4):
            av = abs(axis[i])
            if av != 0 and max_v[i] != 0:
                q = max_v[i] / av
                if q < factor:
                    factor = q
        f *= factor
        cruise = f
        if factor < 1.0:
            for i in range(4):
                axis[i] *= factor
        acc = travel_accel if mtype == TRAVEL else accel
        acc_factor = 1.0
        for i, comp in ((0, length * inv), (1, length * inv), (2, abs(dz) * inv)):
            aa = acc * comp
            if aa != 0 and max_a[i] != 0 and aa > max_a[i]:
                q = max_a[i] / aa
                if q < acc_factor:
                    acc_factor = q
        safe = c_safe   # not reset for arcs in Bambu's estimator
        for i in range(4):
            if abs(axis[i]) > jerk[i] and jerk[i] < safe:
                safe = jerk[i]
        vmax = junction(start_dir, cruise, safe)
        v2 = safe * safe + 2 * acc * dist
        v_allow = math.sqrt(v2) if v2 > 0 else 0.0
        b = _Block()
        b.dist = dist
        b.accel = acc * acc_factor
        b.cruise = cruise
        b.entry = vmax if vmax < v_allow else v_allow
        b.max_entry = vmax
        b.safe = safe
        b.nominal = cruise <= v_allow
        b.recalc = True
        b.role = time_role(mtype)
        b.layer = layer_id
        b.mtype = mtype
        b.prepare = prepare
        _trapezoid(b, b.entry, safe)
        c_feed, c_safe = f, safe
        c_axis[:] = axis
        p_feed, p_safe, p_exit = f, safe, end_dir
        sx, sy, sz, se = ex, ey, ez, ee
        push(b)

    def target(tok_axes: dict) -> tuple[float, float, float, float]:
        ex = (sx + tok_axes["X"] if rel_xyz else tok_axes["X"]) if "X" in tok_axes else sx
        ey = (sy + tok_axes["Y"] if rel_xyz else tok_axes["Y"]) if "Y" in tok_axes else sy
        ez = (sz + tok_axes["Z"] if rel_xyz else tok_axes["Z"]) if "Z" in tok_axes else sz
        if "E" in tok_axes:
            ee = se + tok_axes["E"] if (rel_e or rel_xyz) else tok_axes["E"]
        else:
            ee = se
        return ex, ey, ez, ee

    lines = open_text(path)
    for raw in lines:
        if raw[:4] == ";VG1":
            # A "virtual" move the AMS makes while changing filament (de-retract, flush).
            # Bambu's estimator times it like a G1 move; it never moves the nozzle.
            g1_line_id += 1
            ax = {}
            for tok in raw[1:].split(";", 1)[0].split()[1:]:
                k = tok[0]
                if k in "XYZE":
                    ax[k] = float(tok[1:])
                elif k == "F":
                    feedrate = float(tok[1:]) / 60.0
            g1(*target(ax), True)
            continue
        if raw[:1] == ";":
            s = raw[1:].strip()
            if s in ("FLUSH_START", "VFLUSH_START"):
                prev_role, prev_feat = role, feat
                role = flush_role
                feat = res._id(res.features, "Flush")
                continue
            if s in ("FLUSH_END", "VFLUSH_END"):
                role, feat = prev_role, prev_feat
                continue
            if not s:
                continue
            if not config_done:
                if s == "CONFIG_BLOCK_END":
                    load_config()
                    config_done = True
                    continue
                if " = " in s and not s.startswith("FEATURE"):
                    k, _, val = s.partition(" = ")
                    cfg[k.strip()] = val.strip()
                    continue
                # one value per filament the plate uses ("filament: 1,6,7" says which slots)
                if s.startswith("total filament length [mm] :"):
                    each = [float(x) for x in s.split(":")[1].split(",")]
                    res.header["filament_mm"] = sum(each)
                    res.header["filament_mm_each"] = each
                elif s.startswith("total filament weight [g] :"):
                    each = [float(x) for x in s.split(":")[1].split(",")]
                    res.header["filament_g"] = sum(each)
                    res.header["filament_g_each"] = each
                elif s.startswith("filament:") and "filament" not in res.header:
                    res.header["filament"] = s.split(":", 1)[1].strip()
            if s.startswith("FEATURE:"):
                name = s[8:].strip()
                new_feat = res._id(res.features, name)
                if new_feat != feat:
                    seam_pending = True
                feat = new_feat
                rname = ROLE_ALIASES.get(name, name)
                role = res._id(res.roles, rname if rname in KNOWN_ROLES else "Undefined")
                prepare = (rname == "Custom" and g1_line_id == 0)
            elif s.startswith("LINE_WIDTH:"):
                width = float(s[11:])
            elif s.startswith("LAYER_HEIGHT:"):
                height = float(s[13:])
                res.layers[-1]["h"] = height
            elif s.startswith("Z_HEIGHT:"):
                res.layers[-1]["z"] = float(s[9:])
            elif s == "CHANGE_LAYER":
                layer_id += 1
                res.layers.append({"z": 0.0, "h": height})
            elif s.startswith("WIPE_START"):
                wiping = True
            elif s.startswith("WIPE_END"):
                wiping = False
            continue

        code = raw.split(";", 1)[0].split()
        if not code:
            continue
        cmd = code[0]
        c1 = cmd[:1]
        if cmd == "G1" or cmd == "G0":
            g1_line_id += 1
            ax = {}
            for tok in code[1:]:
                k = tok[0]
                if k in "XYZE":
                    ax[k] = float(tok[1:])
                elif k == "F":
                    feedrate = float(tok[1:]) / 60.0
            g1(*target(ax), True)
        elif cmd == "G2" or cmd == "G3":
            g1_line_id += 1
            ax = {}
            ci = cj = None
            p = None
            for tok in code[1:]:
                k = tok[0]
                if k in "XYZE":
                    ax[k] = float(tok[1:])
                elif k == "I":
                    ci = float(tok[1:])
                elif k == "J":
                    cj = float(tok[1:])
                elif k == "P":
                    p = float(tok[1:])
                elif k == "F":
                    feedrate = float(tok[1:]) / 60.0
            ex, ey, ez, ee = target(ax)
            if ci is None and cj is None:
                continue
            if p is not None and (ex != sx or ey != sy or int(p) != 1):
                continue
            arc(ex, ey, ez, ee, ci or 0.0, cj or 0.0, cmd == "G3", p is not None)
        elif cmd == "G28":
            ax = {k: 0.0 for k in "XYZ" if any(t[0] == k for t in code[1:])} or {"X": 0.0, "Y": 0.0, "Z": 0.0}
            g1(*target(ax), False)
        elif cmd == "G90":
            rel_xyz = False
        elif cmd == "G91":
            rel_xyz = True
        elif cmd == "M82":
            rel_e = False
        elif cmd == "M83":
            rel_e = True
        elif cmd == "G92":
            for tok in code[1:]:
                k = tok[0]
                if k == "X":
                    sx = float(tok[1:])
                elif k == "Y":
                    sy = float(tok[1:])
                elif k == "Z":
                    sz = float(tok[1:])
                elif k == "E":
                    se = float(tok[1:])
        elif cmd == "M204":
            vals = {}
            for tok in code[1:]:
                if len(tok) > 1 and tok[0] in "SPRT":
                    try:
                        vals[tok[0]] = float(tok[1:])
                    except ValueError:
                        pass
            if "S" in vals:
                accel = min(vals["S"], max_accel) if max_accel else vals["S"]
                travel_accel = min(vals["S"], max_travel) if max_travel else vals["S"]
                if "T" in vals:
                    retract_accel = min(vals["T"], max_retract) if max_retract else vals["T"]
            else:
                if "P" in vals:
                    accel = min(vals["P"], max_accel) if max_accel else vals["P"]
                if "R" in vals:
                    retract_accel = min(vals["R"], max_retract) if max_retract else vals["R"]
                if "T" in vals:
                    travel_accel = min(vals["T"], max_travel) if max_travel else vals["T"]
        elif cmd == "M201" or cmd == "M203":
            dest = max_a if cmd == "M201" else max_v
            for tok in code[1:]:
                if len(tok) > 1 and tok[0] in "XYZE":
                    dest["XYZE".index(tok[0])] = float(tok[1:])
        elif cmd == "M205":
            for tok in code[1:]:
                k = tok[0]
                if len(tok) < 2:
                    continue
                v = float(tok[1:])
                if k == "X":
                    jerk[0] = jerk[1] = v
                elif k == "Y":
                    jerk[1] = v
                elif k == "Z":
                    jerk[2] = v
                elif k == "E":
                    jerk[3] = v
                elif k == "S":
                    min_extruding = v
                elif k == "T":
                    min_travel = v
        elif cmd == "G4" or cmd == "M400":
            secs = 0.0
            found = False
            for tok in code[1:]:
                if len(tok) > 1 and tok[0] in "SP":
                    found = True
                    secs += float(tok[1:]) * (0.001 if tok[0] == "P" else 1.0)
            if found:
                calculate_time(0, secs, None)
        elif cmd == "G29":
            if measure_g29:
                calculate_time(0, prepare_comp, None)
        elif cmd == "M622":
            for tok in code[1:]:
                if tok[0] == "J" and round(float(tok[1:])) == 1:
                    measure_g29 = True
        elif cmd == "M623":
            measure_g29 = False
        elif cmd == "SYNC":
            # "SYNC T<seconds>": time the AMS spends flushing (R1 or no R) or other machine time
            secs = None
            r = 1
            for tok in code[1:]:
                if tok[:1] == "T" and len(tok) > 1:
                    secs = float(tok[1:])
                elif tok[:1] == "R" and len(tok) > 1:
                    r = round(float(tok[1:]))
            if secs is not None:
                calculate_time(0, secs, flush_role if r == 1 else None)
        elif c1 == "T":
            try:
                n = int(cmd[1:])
            except ValueError:
                continue
            if 0 <= n <= 254 and n != filament:
                extra = (unload_time if filament >= 0 else 0.0) + load_time
                filament = n
                b = _Block()
                b.role = flush_role
                b.layer = layer_id
                b.prepare = prepare
                blocks.append(b)
                calculate_time(0, extra, flush_role)
    lines.close()
    calculate_time(0)
    if not config_done:
        load_config()
    return res
