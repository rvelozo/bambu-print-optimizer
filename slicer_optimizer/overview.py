"""What a project is tuned for: its settings against the printer's default process.

The reference is the process preset Bambu Studio picks for this printer and
nozzle (the machine preset's default_print_profile, "0.20mm Standard @BBL P2S"
for a P2S with a 0.4 mm nozzle), with each filament at its system preset. Only
tuning goes back to it: the Quality, Strength and Speed pages of the Process
tab, and the filament settings that change how plastic is laid down
(temperatures, flow, cooling, volumetric speed, per-filament overrides). The
Support and Others pages (supports, bed adhesion, prime tower, special modes
like spiral vase or fuzzy skin, which filament prints what), the filament's
material facts (density, cost, type) and the printer's own settings are how
the model has to be printed, so the reference keeps them as the file has them.

The reference becomes an ordinary run (kind "reference"), sliced like any
other, and the project's own slice is compared with it. Tuning lives in four
places and all four go back: print-wide settings, per-object overrides,
per-part overrides (modifiers), and each used filament's changed settings.
"""

from __future__ import annotations

import re

from . import bambu, presets, search, settings
from .project3mf import Obj, Project, ProjectError

TUNING_PAGES = {"Quality", "Strength", "Speed"}
# Filament tab sections that change how plastic is laid down; the rest are facts about the
# material (density, cost, type) or how filament changes are handled (prime volume, ramming)
FILAMENT_TUNING_SECTIONS = {"Print temperature", "Volumetric speed limitation", "Filament scarf seam settings",
                            "Part cooling fan", "Auxiliary part cooling fan", "Exhaust fan"}
FILAMENT_TUNING_KEYS = {"filament_flow_ratio", "enable_pressure_advance", "pressure_advance"}
NUMBER = re.compile(r"^-?\d+(\.\d+)?%?$")
RECORD_GAPS = 5  # this many values off the preset without being recorded: the record is incomplete


def default_process_name(project: Project) -> str | None:
    machine = presets.resolve("machine", project.print_settings.get("printer_settings_id")) or {}
    name = machine.get("default_print_profile")
    return name if isinstance(name, str) and name else None


def system_preset(kind: str, name: str | None) -> dict:
    """A system preset's value for every setting: its own and its parents', then Bambu's built-in
    defaults for the settings no preset file names. Empty if it isn't a system preset."""
    resolved = presets.resolve(kind, name)
    return {**bambu.builtin_defaults(), **resolved} if resolved else {}


def _variant_text(project: Project, value) -> str | None:
    """One value for this printer from a stored value: a list, or "50,500,500" in an override."""
    if value is None:
        return None
    if isinstance(value, str) and "," in value:
        parts = value.split(",")
        n = len(project.print_settings.get("print_extruder_variant") or [])
        if n > 1 and len(parts) == n:
            return parts[project.variant_index()].strip()
        return value
    v = project.active(value)
    return ",".join(map(str, v)) if isinstance(v, list) else str(v)


def same(a, b, key: str | None = None) -> bool:
    """Equal values, as numbers where both are: presets write "100" or "45.0" where projects write
    "100%" or "45%" for a percentage setting."""
    if a is None or b is None:
        return a is b
    a, b = str(a).strip(), str(b).strip()
    if NUMBER.match(a) and NUMBER.match(b):
        percent = (settings.definition(key) or {}).get("type") in ("coPercent", "coPercents") if key else False
        if percent or a.endswith("%") == b.endswith("%"):
            return abs(float(a.rstrip("%")) - float(b.rstrip("%"))) < 1e-9
    return a == b


def _is_tuning(key: str, tab: str) -> bool:
    where = settings.place(key)
    if tab == "process":
        return bool(where) and where[0] == "Process" and where[1] in TUNING_PAGES
    if key in FILAMENT_TUNING_KEYS:
        return True
    if where and where[0] == "Filament":
        return where[2] in FILAMENT_TUNING_SECTIONS
    # per-filament overrides of process settings (filament_retraction_length, ...)
    return key.startswith("filament_") and settings.definition(key[len("filament_"):]) is not None


def _row(key: str, scope: str, where: str, now, default, tuning: bool, **extra) -> dict:
    place = settings.place(key)
    return {"key": key, "name": settings.display_name(key), "tab": place[0] if place else None,
            "page": place[1] if place else None, "section": place[2] if place else None,
            "scope": scope, "where": where, "now": settings.value_text(key, now),
            "default": None if default is None else settings.value_text(key, default),
            "tuning": tuning, **extra}


def _slots_on_plate(project: Project, objs: list[Obj]) -> list[int]:
    """Filament slots (0-based) the plate's objects and parts print with."""
    slots = set()
    for o in objs:
        slots.add(int(o.extruder or "1") - 1)
        for p in o.parts:
            if p.overrides.get("extruder"):
                slots.add(int(p.overrides["extruder"]) - 1)
    for key in ("wall_filament", "sparse_infill_filament", "solid_infill_filament", "support_filament",
                "support_interface_filament"):
        v = str(project.active(project.print_settings.get(key)) or "0")
        if v.isdigit() and int(v) > 0:
            slots.add(int(v) - 1)
    return sorted(slots)


def differences(project: Project, plate: int) -> dict:
    """Every setting on this plate that differs from the printer's default process, and the
    changes (run form) that put the tuning ones back to it."""
    ref_name = default_process_name(project)
    ref = system_preset("process", ref_name)
    out = {"reference": ref_name, "found": bool(ref), "process": project.print_settings.get("print_settings_id"),
           "rows": [], "changes": [], "unrecorded": []}
    if not ref:
        return out
    ps = project.print_settings
    rows, changes = out["rows"], out["changes"]

    # Print-wide process settings, everything the Process tab shows. A setting differs when the
    # project changed it from its preset (Bambu Studio's own record), or when the project's
    # preset isn't the default and the two presets disagree on it. The project's values aren't
    # compared with the default directly: Bambu writes some it doesn't count as changes
    # (travel 1000 mm/s on a P2S whose presets say 600). Files saved by Bambu Studio's window
    # have one or two such values; a file with many has an incomplete record (its command
    # line writes none), and then those are compared by value too.
    own = system_preset("process", out["process"]) or None
    recorded = set(project.modified_vs_system().get("process", []))
    keys = [o["key"] for p in settings.layout()["pages"] for g in p["groups"] for o in g["options"]
            if o["key"] in ps and o["key"] in ref]
    if own is not None:
        out["unrecorded"] = [k for k in keys if k not in recorded and not same(
            _variant_text(project, ps[k]), _variant_text(project, own.get(k)), k)]
        if len(out["unrecorded"]) >= RECORD_GAPS:
            recorded |= set(out["unrecorded"])
    for key in keys:
        now, default = _variant_text(project, ps[key]), _variant_text(project, ref[key])
        if key in recorded or own is None:
            differs = not same(now, default, key)
            source = "this file" if own is not None else "this file's preset"
        elif out["process"] != ref_name:
            differs = key in own and not same(_variant_text(project, own[key]), default, key)
            source = "this file's preset"
        else:
            differs = False
        if not differs:
            continue
        tuning = _is_tuning(key, "process")
        rows.append(_row(key, "print-wide", "whole plate", now, default, tuning, source=source))
        if tuning:
            changes.append({"key": key, "value": default, "object_id": None})
    # process settings the project changed that the Process tab doesn't show: listed, kept
    shown = {o["key"] for p in settings.layout()["pages"] for g in p["groups"] for o in g["options"]}
    for key in project.modified_vs_system().get("process", []):
        if key not in shown and key in ps:
            rows.append(_row(key, "print-wide", "whole plate", _variant_text(project, ps[key]),
                             _variant_text(project, ref.get(key)), False))

    # per-object and per-part overrides
    objs = project.objects_on_plate(plate)
    for o in objs:
        for key, value in sorted(o.overrides.items()):
            default = _variant_text(project, ref.get(key)) if key in ref else None
            now = _variant_text(project, value)
            if default is not None and same(now, default, key):
                continue
            tuning = _is_tuning(key, "process") and default is not None
            rows.append(_row(key, "object", o.name, now, default, tuning, object_id=o.id))
            if tuning:
                changes.append({"key": key, "value": None, "object_id": o.id})
        for part in o.parts:
            for key, value in sorted(part.overrides.items()):
                if key == "extruder":
                    continue
                default = _variant_text(project, ref.get(key)) if key in ref else None
                now = _variant_text(project, value)
                if default is not None and same(now, default, key):
                    continue
                tuning = _is_tuning(key, "process") and default is not None
                rows.append(_row(key, "part", f"{o.name}: {part.name}", now, default, tuning,
                                 object_id=o.id, part_id=part.id))
                if tuning:
                    changes.append({"key": key, "value": None, "object_id": o.id, "part_id": part.id})

    # filaments used on the plate: the settings the project changed from each one's system preset
    names = ps.get("filament_settings_id") or []
    nvar = len(ps.get("print_extruder_variant") or []) or 1
    changed = project.modified_vs_system()
    lists: dict[str, list] = {}
    for slot in _slots_on_plate(project, objs):
        if slot >= len(names):
            continue
        system = system_preset("filament", names[slot])
        for key in changed.get(f"filament {slot + 1}", []):
            value = ps.get(key)
            if not isinstance(value, list):
                continue
            idx = [slot * nvar + v for v in range(nvar)] if len(value) == len(names) * nvar else \
                [slot] if len(value) == len(names) else []
            if not idx:
                continue
            now = str(value[idx[min(project.variant_index(), len(idx) - 1)]])
            sys_value = (system or {}).get(key)
            sys_list = [str(x) for x in sys_value] if isinstance(sys_value, list) else \
                [str(sys_value)] if sys_value is not None else []
            default = None
            if sys_list:
                default = sys_list[project.variant_index()] if len(sys_list) == nvar and len(idx) == nvar else sys_list[0]
            if default is not None and same(now, default, key):
                continue
            tuning = _is_tuning(key, "filament") and default is not None
            rows.append(_row(key, "filament", f"filament {slot + 1}: {names[slot]}", now, default, tuning, slot=slot + 1))
            if tuning:
                new = lists.setdefault(key, [str(x) for x in value])
                for j, i in enumerate(idx):
                    new[i] = sys_list[j] if len(sys_list) == len(idx) else sys_list[0]
    for key, value in sorted(lists.items()):
        changes.append({"key": key, "value": value, "object_id": None})

    # the printer's own settings: listed, kept
    for key in changed.get("printer", []):
        rows.append(_row(key, "printer", "printer", _variant_text(project, ps.get(key)), None, False))
    return out


def characteristics(project: Project, plate: int) -> dict:
    """What the file prints and how, as Bambu Studio would show it."""
    ps = project.print_settings
    objs = project.objects_on_plate(plate)
    names = ps.get("filament_settings_id") or []
    colours = ps.get("filament_colour") or []
    types = ps.get("filament_type") or []
    filaments = [{"slot": s + 1, "name": names[s] if s < len(names) else None,
                  "type": types[s] if s < len(types) else None, "colour": colours[s] if s < len(colours) else None}
                 for s in _slots_on_plate(project, objs)]

    def now(key):
        return settings.value_text(key, _variant_text(project, ps.get(key))) if key in ps else None

    pl = project.plate(plate)
    return {
        "printer": ps.get("printer_settings_id"), "nozzle": _variant_text(project, ps.get("nozzle_diameter")),
        "bed": now("curr_bed_type"), "process": ps.get("print_settings_id"),
        "default_process": default_process_name(project), "filaments": filaments,
        "objects": [{"id": o.id, "name": o.name, "copies": pl.object_ids.count(o.id),
                     "overrides": len(o.overrides), "parts": len(o.parts),
                     "modifiers": sum(1 for p in o.parts if p.overrides)} for o in objs],
        "settings": {key: now(key) for key in (
            "layer_height", "initial_layer_print_height", "wall_loops", "top_shell_layers", "bottom_shell_layers",
            "sparse_infill_density", "sparse_infill_pattern", "outer_wall_speed", "enable_support", "support_type",
            "brim_type", "spiral_mode", "fuzzy_skin", "enable_prime_tower", "ironing_type", "seam_position")
            if key in ps},
    }


def revert_groups(diff: dict) -> list[dict]:
    """The reference changes grouped by setting, to put back one setting at a time."""
    groups: dict[str, list[dict]] = {}
    for c in diff["changes"]:
        groups.setdefault(c["key"], []).append(c)
    return [{"key": k, "name": settings.display_name(k), "changes": v} for k, v in sorted(groups.items())]


def check(project: Project, plate: int) -> None:
    project.plate(plate)
    if not project.objects_on_plate(plate):
        raise ProjectError(f"plate {plate} is empty")


GOAL_ORDER = ("time_s", "grams", "cost", "stiffness", "quality")
SAME_PCT = 2.0  # within this many percent (or points, for proxies in %) of the default counts as the same


def verdict(goals: dict, ref_goals: dict) -> dict:
    """What the file is tuned for, from its measured goals against the default's.

    Returns, per goal, the change and whether it's better, worse or about the
    same (within SAME_PCT % of the default, or SAME_PCT points for stiffness and
    quality, which are already percentages).
    """
    out = {}
    for g in GOAL_ORDER:
        a, b = goals.get(g), ref_goals.get(g)
        if a is None or b is None:
            continue
        better_high = search.GOALS.get(g, {}).get("better") == "higher"
        if g in ("stiffness", "quality"):
            change, size = a - b, abs(a - b)
        else:
            change = 100.0 * (a - b) / b if b else 0.0
            size = abs(change)
        gain = change if better_high else -change
        out[g] = {"now": a, "default": b, "change": round(change, 2),
                  "is": "same" if size < SAME_PCT else ("better" if gain > 0 else "worse")}
    return out


# ---- in the app --------------------------------------------------------------------------

def _effect(app, base: dict, other: dict) -> dict:
    """What the file's settings do against another run of the same plate: base minus other,
    and quality as the file's against the other's (100 = the same)."""
    gb, go = base.get("goals") or {}, other.get("goals") or {}
    out = {g: gb[g] - go[g] for g in ("time_s", "grams", "cost", "stiffness") if gb.get(g) is not None and go.get(g) is not None}
    from . import quality
    q = quality.score((base.get("metrics") or {}).get("quality"), (other.get("metrics") or {}).get("quality"))
    if q:
        out["quality"] = round(q["index"] - 100, 2)
    return out


def _state(run: dict | None) -> str | None:
    return None if run is None else run.get("status")


class Overviews:
    def __init__(self, app):
        self.app = app

    def reference_run(self, plate: int, diff: dict) -> dict | None:
        """The plate with its tuning back to the printer's default (created and queued once)."""
        if not diff["found"] or not diff["changes"]:
            return None
        runs = self.app.runs
        run = runs.find(plate, diff["changes"])
        if run is None or run["status"] == "cancelled":
            if run is None:
                run = runs.add(plate, diff["changes"], f"Bambu default ({diff['reference']})", "reference")
            self.app.enqueue(run["id"], 0)  # what the Overview tab shows first: ahead of searches, like a trial
        return run

    def explain(self, plate: int) -> dict:
        """Slice the file with one setting at a time put back to the default."""
        project = self.app.project
        check(project, plate)
        diff = differences(project, plate)
        for g in revert_groups(diff):
            run = self.app.runs.find(plate, g["changes"])
            if run is None or run["status"] == "cancelled":
                if run is None:
                    run = self.app.runs.add(plate, g["changes"], f"{g['name']} at the default", "revert")
                self.app.enqueue(run["id"], 1)
        return self.view(plate)

    def view(self, plate: int) -> dict:
        project = self.app.project
        check(project, plate)
        diff = differences(project, plate)
        base = self.app.baseline(plate)
        ref = self.reference_run(plate, diff)
        done = lambda r: r is not None and r.get("status") == "done" and r.get("goals")
        comparison = None
        if done(base) and (done(ref) or (diff["found"] and not diff["changes"])):
            other = ref if ref is not None else base
            effect = _effect(self.app, base, other)
            goals = dict(base["goals"])
            ref_goals = dict(other["goals"])
            if "quality" in effect:
                goals["quality"], ref_goals["quality"] = 100 + effect["quality"], 100.0
            from . import quality
            comparison = {"verdict": verdict(goals, ref_goals), "ref_run": other["id"],
                          "quality": quality.score((base.get("metrics") or {}).get("quality"),
                                                   (other.get("metrics") or {}).get("quality"))}
        groups = []
        per = self.app.optimizations.per_slice_s(plate) if hasattr(self.app, "optimizations") else None
        for g in revert_groups(diff):
            run = self.app.runs.find(plate, g["changes"])
            groups.append({"key": g["key"], "name": g["name"], "run_id": run["id"] if run else None,
                           "status": _state(run), "error": (run or {}).get("error"),
                           "effect": _effect(self.app, base, run) if done(base) and done(run) else None})
        base_metrics = (base or {}).get("metrics") or {}
        return {"plate": plate, "characteristics": characteristics(project, plate), "reference": diff["reference"],
                "found": diff["found"], "process": diff["process"], "rows": diff["rows"],
                "unrecorded": [settings.display_name(k) for k in diff["unrecorded"]],
                "tuning_changes": len(diff["changes"]), "groups": groups, "per_slice_s": per,
                "base": {"run_id": (base or {}).get("id"), "status": _state(base), "goals": (base or {}).get("goals"),
                         "quality": base_metrics.get("quality"), "filament": base_metrics.get("filament")},
                "ref": {"run_id": (ref or {}).get("id"), "status": _state(ref), "error": (ref or {}).get("error"),
                        "goals": (ref or {}).get("goals")},
                "comparison": comparison}


def register(app) -> None:
    store = Overviews(app)
    app.overviews = store

    def get_overview(h, route, qs):
        h._json(store.view(int(qs.get("plate", "1"))))

    def post_overview(h, route, qs):
        if len(route) == 2 and route[1] == "explain":
            return h._json(store.explain(int(qs.get("plate", "1"))))
        return h._error(404, "unknown action")

    app.extensions.update({"GET overview": get_overview, "POST overview": post_overview})
