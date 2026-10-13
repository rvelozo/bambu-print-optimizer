"""Optimize a plate for goals: you say what matters, the tool picks the settings.

1. Wait for the plate's baseline slice: its feature times say which settings can
   matter at all (no bridges on the plate, no point in trying bridge speeds).
2. Measure: each relevant setting's catalog values, each on its own.
3. Combine: the settings that measurably helped a chosen goal, with their best
   values, in every combination the slicing budget allows.
4. Recommend the sliced candidate closest to the ideal for your priorities
   (search.pick), within your limits, plus the best one for each goal alone.

Every number comes from a real slice (each candidate is an ordinary designer
search run); nothing is predicted. Stages advance in the background (tick()),
so an optimization keeps going with the page closed and across restarts.
"""

from __future__ import annotations

import itertools
import json
import math
import threading
import time
import uuid

from . import search
from .project3mf import ProjectError

DEFAULT_WEIGHTS = {"time_s": 3, "grams": 3, "stiffness": 3, "quality": 0, "cost": 0, "near_cap_s": 0}
# time uses the plate's own repeat noise; quality's aspects moved by under 0.1% for settings that don't touch them
NOISE = {"grams": 0.01, "cost": 0.001, "stiffness": 0.05, "quality": 0.1, "near_cap_s": 1.0}
QUALITY_SLACK = 0.2  # points of quality a limit forgives, above that noise
MIN_EFFECT = 0.002  # a setting must move a goal by more than 0.2 % of its current value to be worth changing
MAX_VALUES_PER_KNOB = 2  # besides the current one, in the combination stage
MIN_BUDGET = 6
MAX_POINTS = 400  # a designer search holds at most this many candidates


# ---- pure planning functions (tested without slicing) ----------------------------------

def relevance(knob: dict, base: dict) -> str | None:
    """Why a catalog setting can't matter on this plate, or None if it can.

    `base` is the baseline run's metrics: feature_s (Bambu's time per feature)
    and flow_cap / near_cap_s.
    """
    need = knob.get("needs")
    if not need:
        return None
    if need == "@overhangs":
        air = (base.get("quality") or {}).get("air")
        if air is None:  # measured before quality: Bambu's own overhang feature is the best guess
            return None if base.get("feature_s", {}).get("Overhang wall", 0.0) >= 0.5 else "this plate has no overhangs"
        # any wall over air: Bambu's overhang classes don't match the measured share of the width
        # exactly (a lattice's bars print at the speed one class down), so every overhang speed is tried
        return None if air.get("walls_mm", 0.0) >= 1.0 else "no wall on this plate hangs over air"
    if need == "@flow_cap":
        if not base.get("flow_cap"):
            return "this plate's filaments don't share one flow cap"
        if not base.get("near_cap_s"):
            return "nothing on this plate prints at the flow cap"
        return None
    if base.get("feature_s", {}).get(need, 0.0) < 0.5:
        return f"this plate has no {need.lower()}"
    return None


def _number(v) -> float | None:
    try:
        return float(str(v).rstrip("%"))
    except (TypeError, ValueError):
        return None


def useful_values(knob: dict, goals: set[str]) -> list[str]:
    """The knob's catalog values worth slicing for these goals.

    A knob's "helps" says which way a goal improves: {"time_s": "higher"} means
    only values above the current one can make the print faster. A value is kept
    when some chosen goal can improve with it, or the direction is unknown.
    """
    cur = _number(knob.get("current"))
    out = []
    for v in knob["values"]:
        v = str(v)
        if v == str(knob.get("current")):
            continue
        x = _number(v)
        if cur is None or x is None:  # words, or objects that disagree: no direction to go by
            out.append(v)
            continue
        for g in goals & set(knob.get("goals", [])):
            way = knob.get("helps", {}).get(g)
            if way is None or (way == "higher" and x > cur) or (way == "lower" and x < cur):
                out.append(v)
                break
    return out


def quality_in_play(weights: dict, limits: dict | None) -> bool:
    return weights.get("quality", 0) > 0 or (limits or {}).get("quality_pct") not in (None, "")


def choose_knobs(catalog: list[dict], base: dict, weights: dict, allowed: set[str] | None,
                 limits: dict | None = None) -> tuple[list[dict], list[dict]]:
    """The settings to measure, and the ones left out with the reason."""
    goals = {g for g, w in weights.items() if w > 0}
    chosen, skipped = [], []
    for k in catalog:
        why = None
        off = k.get("off_by_default")
        if off and k.get("on_with_quality") and quality_in_play(weights, limits):
            off = None  # its effect on looks is measured now
        if not goals & set(k.get("goals", [])):
            why = "doesn't affect the goals you chose"
        elif (r := relevance(k, base)) is not None:
            why = r
        elif allowed is not None and k["key"] not in allowed:
            why = off or "you left it out"
        elif allowed is None and off:
            why = off  # only when asked for
        if why:
            skipped.append({"key": k["key"], "label": k.get("label", k["key"]), "why": why})
            continue
        values = useful_values(k, goals)
        if values:
            chosen.append({"key": k["key"], "values": values})
        else:
            others = [str(v) for v in k["values"] if str(v) != str(k.get("current"))]
            why = "no catalog value can improve the goals you chose" if others else "every catalog value is the current one"
            skipped.append({"key": k["key"], "label": k.get("label", k["key"]), "why": why})
    return chosen, skipped


def _name(key: str) -> str:
    return search.KNOBS.get(key, {}).get("label") or key


def _join(names: list[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def fit_measure_budget(knobs: list[dict], budget: int) -> tuple[list[dict], list[str]]:
    """Trim the measuring stage to the budget: first to each knob's extreme values, then whole knobs."""
    notes = []
    count = lambda ks: 1 + sum(len(k["values"]) for k in ks)
    if count(knobs) <= budget:
        return knobs, notes
    trimmed = [{**k, "values": [k["values"][0], k["values"][-1]] if len(k["values"]) > 2 else k["values"]} for k in knobs]
    notes.append("To fit the time you gave it, only the lowest and highest value of each setting were measured.")
    dropped = []
    while count(trimmed) > budget and len(trimmed) > 1:
        dropped.insert(0, _name(trimmed.pop()["key"]))
    if dropped:
        notes.append(f"{_join(dropped)} {'was' if len(dropped) == 1 else 'were'} left out of the measurements.")
    return trimmed, notes


def _gain(goal: str, base: float, x: float) -> float:
    """Improvement of x over base for this goal (positive is better)."""
    return (x - base) if search.GOALS.get(goal, {}).get("better") == "higher" else (base - x)


def score(goals: dict, base: dict, weights: dict) -> float:
    """Weighted relative improvement over the baseline (higher is better)."""
    s = 0.0
    for g, w in weights.items():
        if w <= 0 or goals.get(g) is None or base.get(g) is None:
            continue
        s += w * _gain(g, base[g], goals[g]) / max(abs(base[g]), 1e-9)
    return s


def thresholds(base: dict, tolerance: dict[str, float]) -> dict[str, float]:
    """The smallest change per goal that counts: above the repeat noise and above 0.2 %."""
    return {g: max(tolerance.get(g, 0.0), MIN_EFFECT * abs(v)) for g, v in base.items() if v is not None}


def effects(knobs: list[dict], solo: dict[tuple[str, str], dict], base: dict, weights: dict,
            tolerance: dict[str, float]) -> list[dict]:
    """What each measured value did, alone, against the baseline.

    `solo` maps (key, value) to that run's goals. A value "helps" when it beats
    the baseline on a chosen goal by more than the goal's threshold (noise, and
    0.2 % of the current value: a deterministic plate has no noise, but 3 s off
    a 2.5 h print isn't worth a setting change).
    """
    limit = thresholds(base, tolerance)
    out = []
    for k in knobs:
        rows = []
        for v in k["values"]:
            g = solo.get((k["key"], v))
            if g is None:
                rows.append({"value": v, "measured": False})
                continue
            deltas = {name: (g[name] - base[name]) for name in g if g.get(name) is not None and base.get(name) is not None}
            helps = [name for name, w in weights.items() if w > 0 and name in deltas
                     and _gain(name, base[name], g[name]) > limit.get(name, 0.0)]
            rows.append({"value": v, "measured": True, "deltas": deltas, "helps": helps,
                         "score": score(g, base, weights)})
        measured = [r for r in rows if r["measured"]]
        best = max((r["score"] for r in measured if r["helps"]), default=None)
        out.append({"key": k["key"], "values": rows, "best": best,
                    "matters": any(r["helps"] for r in measured)})
    return out


def plan_combinations(effect_rows: list[dict], current: dict[str, str], budget: int,
                      max_points: int = MAX_POINTS) -> tuple[list[dict], list[str]]:
    """Knobs and values for the combination stage, within `budget` new slices.

    Settings that helped come in order of their best weighted gain; each brings
    its current value plus up to two values that helped. Grid points that are
    the baseline or a single change were already sliced in the measuring stage.
    """
    notes = []
    ranked = sorted((e for e in effect_rows if e["matters"]), key=lambda e: -(e["best"] or -math.inf))

    def as_knobs(picked):
        out = []
        for key, used, _ in picked:
            cur = current.get(key)
            out.append({"key": key, "values": ([cur] if cur is not None else []) + used})
        return out

    def new_slices(ks):
        # combinations with two or more settings off their current value; the rest were measured alone
        if math.prod(len(k["values"]) for k in ks) > max_points:
            return math.inf
        n = 0
        for combo in itertools.product(*(k["values"] for k in ks)):
            changed = sum(1 for k, v in zip(ks, combo) if v != current.get(k["key"]))
            n += changed >= 2
        return n

    left_out: list[str] = []   # helped alone, no room in the combinations
    best_only: list[str] = []  # combined with its best value only
    # first every setting that helped, with its best value; then second-best values while there's room
    picked: list[tuple[str, list[str], list[str]]] = []  # (key, values in use, helping values by score)
    for e in ranked:
        helping = [r["value"] for r in sorted((r for r in e["values"] if r.get("helps")), key=lambda r: -r["score"])]
        trial = picked + [(e["key"], helping[:1], helping)]
        if new_slices(as_knobs(trial)) <= budget:
            picked = trial
        else:
            left_out.append(_name(e["key"]))
    for i, (key, used, helping) in enumerate(picked):
        for extra in helping[len(used):MAX_VALUES_PER_KNOB]:
            trial = picked[:i] + [(key, used + [extra], helping)] + picked[i + 1:]
            if new_slices(as_knobs(trial)) <= budget:
                picked, used = trial, used + [extra]
            else:
                best_only.append(_name(key))
                break
    knobs = as_knobs(picked)
    parts = []
    if best_only:
        parts.append(f"only the best value of {_join(best_only)} went into the combinations")
    if left_out:
        parts.append(f"{_join(left_out)} helped alone but {'was' if len(left_out) == 1 else 'were'} left out")
    if parts:
        notes.append("To fit the time you gave it, " + "; ".join(parts) + ".")
    if len(knobs) < 2:
        return [], notes  # one setting: its values were all measured already
    return knobs, notes


def limits_for(raw: dict, base: dict) -> dict[str, tuple[str, float]]:
    """The user's limits in search.feasible form; strength and quality are relative to the current part.

    The quality limit holds every quality aspect (quality.score's "worst"), so a
    better finish in one place can't pay for a worse one elsewhere.
    """
    out: dict[str, tuple[str, float]] = {}
    if raw.get("strength_pct") not in (None, "") and base.get("stiffness") is not None:
        out["stiffness"] = (">=", base["stiffness"] * float(raw["strength_pct"]) / 100.0 - 1e-9)
    if raw.get("max_time_s") not in (None, ""):
        out["time_s"] = ("<=", float(raw["max_time_s"]))
    if raw.get("max_grams") not in (None, ""):
        out["grams"] = ("<=", float(raw["max_grams"]))
    if raw.get("quality_pct") not in (None, ""):
        out["quality_worst"] = (">=", float(raw["quality_pct"]) - QUALITY_SLACK)
    return out


def recommend(points: list[dict], weights: dict, limits: dict) -> dict:
    """The pick for these priorities and the best feasible point for each goal alone.

    `points` are dicts of goals plus "run_id". Returns run ids.
    """
    goals = [g for g, w in weights.items() if w > 0]
    i = search.pick(points, weights, limits)
    out = {"pick": points[i]["run_id"] if i is not None else None, "best_for": {}}
    for g in goals:
        j = search.pick(points, {g: 1.0}, limits)
        if j is not None:
            out["best_for"][g] = points[j]["run_id"]
    ok = [p for p in points if search.feasible(p, limits) and all(p.get(g) is not None for g in goals)]
    out["front"] = [ok[x]["run_id"] for x in search.pareto(ok, goals)] if goals else []
    return out


# ---- the store ---------------------------------------------------------------------------

class Optimizations:
    def __init__(self, app):
        self.app = app
        self.file = app.runs.dir / "optimizations.json"
        self.lock = threading.RLock()
        self.items: dict[str, dict] = {}
        if self.file.exists():
            for item in json.loads(self.file.read_text()).get("optimizations", []):
                self.items[item["id"]] = item

    def save(self) -> None:
        with self.lock:
            tmp = self.file.with_suffix(".tmp")
            tmp.write_text(json.dumps({"optimizations": list(self.items.values())}, indent=1))
            tmp.replace(self.file)

    def current(self, plate: int | None = None) -> list[dict]:
        sha = self.app.runs.source_sha
        with self.lock:
            items = [i for i in self.items.values() if i["source_sha"] == sha and (plate is None or i["plate"] == plate)]
        return sorted(items, key=lambda i: -i["created"])

    # -- inputs --

    def _baseline(self, plate: int) -> dict | None:
        return self.app.baseline(plate)

    def per_slice_s(self, plate: int) -> float | None:
        """Seconds one candidate takes: Bambu's slicing plus reading its G-code."""
        b = self._baseline(plate)
        m = (b or {}).get("metrics") or {}
        if not m.get("wall_s"):
            return None
        return m["wall_s"] + (m["analysis_s"] if m.get("analysis_s") is not None else m["wall_s"])

    def preview(self, plate: int, weights: dict, allowed: list[str] | None, budget_s: float | None,
                limits: dict | None = None) -> dict:
        """What an optimization with these choices would measure, before starting it."""
        project = self.app.project
        project.plate(plate)
        cat = search.catalog_for(project, plate)
        base = self._baseline(plate)
        if not base or base.get("status") != "done":
            return {"ready": False, "catalog": cat, "message": "The plate's current settings are still being sliced."}
        knobs, skipped = choose_knobs(cat, base["metrics"], weights, set(allowed) if allowed is not None else None, limits)
        budget = self.budget_slices(plate, budget_s)
        measured, notes = fit_measure_budget(knobs, budget)
        return {"ready": True, "catalog": cat, "knobs": measured, "skipped": skipped, "notes": notes,
                "budget_slices": budget, "measure_slices": 1 + sum(len(k["values"]) for k in measured),
                "per_slice_s": self.per_slice_s(plate), "base_goals": base.get("goals")}

    def budget_slices(self, plate: int, budget_s: float | None) -> int:
        per = self.per_slice_s(plate) or 30.0
        if not budget_s:
            return 60
        return max(MIN_BUDGET, int(budget_s // max(per, 0.5)))

    # -- lifecycle --

    def create(self, plate: int, weights: dict, limits: dict, allowed: list[str] | None, budget_s: float | None) -> dict:
        self.app.project.plate(plate)
        if not self.app.project.plate(plate).object_ids:
            raise ProjectError(f"plate {plate} is empty: nothing to optimize")
        weights = {g: float(weights.get(g, 0) or 0) for g in search.GOALS}
        if not any(w > 0 for w in weights.values()):
            raise ProjectError("choose at least one goal")
        item = {"id": uuid.uuid4().hex[:10], "plate": plate, "created": time.time(), "weights": weights,
                "limits": limits or {}, "allowed": allowed, "budget_s": budget_s, "stage": "baseline",
                "searches": {}, "skipped": [], "notes": [], "source_sha": self.app.runs.source_sha}
        with self.lock:
            self.items[item["id"]] = item
            self.save()
        self.tick()
        return self.view(item)

    def update(self, oid: str, weights: dict | None, limits: dict | None) -> dict:
        """New priorities or limits re-rank what was already sliced; nothing is re-sliced."""
        item = self.items[oid]
        with self.lock:
            if weights is not None:
                item["weights"] = {g: float(weights.get(g, 0) or 0) for g in search.GOALS}
            if limits is not None:
                item["limits"] = limits
            self.save()
        return self.view(item)

    def stop(self, oid: str) -> dict:
        item = self.items[oid]
        for sid in item["searches"].values():
            if sid in self.app.searches.items:
                self.app.searches.cancel(sid)
        with self.lock:
            item["stopped"] = True
            self.save()
        return self.view(item)

    def resume(self, oid: str) -> dict:
        item = self.items[oid]
        for sid in item["searches"].values():
            if sid in self.app.searches.items:
                self.app.searches.resume(sid)
        with self.lock:
            item["stopped"] = False
            self.save()
        self.tick()
        return self.view(item)

    def delete(self, oid: str) -> None:
        self.stop(oid)
        with self.lock:
            self.items.pop(oid, None)
            self.save()

    def _search_done(self, sid: str) -> bool:
        s = self.app.searches.items.get(sid)
        if s is None:
            return True
        v = self.app.searches.view(s)
        return v["counts"]["queued"] + v["counts"]["active"] == 0

    def tick(self) -> None:
        """Move every unfinished optimization on to its next stage when it can."""
        for item in self.current():
            if item.get("stopped") or item["stage"] == "done":
                continue
            try:
                self._advance(item)
            except ProjectError as e:
                with self.lock:
                    item["stage"], item["error"] = "done", str(e)
                    self.save()

    def _advance(self, item: dict) -> None:
        plate = item["plate"]
        if item["stage"] == "baseline":
            base = self._baseline(plate)
            if not base or base.get("status") in ("queued", "slicing", "analysing"):
                return
            if base.get("status") != "done":
                raise ProjectError("the plate's current settings couldn't be sliced")
            cat = search.catalog_for(self.app.project, plate)
            knobs, skipped = choose_knobs(cat, base["metrics"], item["weights"],
                                          set(item["allowed"]) if item.get("allowed") is not None else None,
                                          item.get("limits"))
            budget = self.budget_slices(plate, item.get("budget_s"))
            knobs, notes = fit_measure_budget(knobs, budget)
            with self.lock:
                item.update(skipped=skipped, notes=notes, budget_slices=budget, knobs=knobs,
                            current={k["key"]: k.get("current") for k in cat})
                if not knobs:
                    item["stage"] = "done"
                    item["notes"].append("No setting in the catalog can move these goals on this plate.")
                else:
                    s = self.app.searches.create(plate, "sensitivity", knobs, by=item["id"])
                    item["searches"]["measure"] = s["id"]
                    item["stage"] = "measure"
                self.save()
        elif item["stage"] == "measure":
            if not self._search_done(item["searches"]["measure"]):
                return
            data = self._data(item)
            used = data["measure_slices"]
            combos, notes = plan_combinations(data["effects"], item.get("current", {}),
                                              max(0, item.get("budget_slices", 60) - used))
            with self.lock:
                item["notes"] += notes
                item["combine_knobs"] = combos
                if combos:
                    s = self.app.searches.create(plate, "grid", combos, by=item["id"])
                    item["searches"]["combine"] = s["id"]
                    item["stage"] = "combine"
                else:
                    item["stage"] = "done"
                self.save()
        elif item["stage"] == "combine":
            if self._search_done(item["searches"]["combine"]):
                with self.lock:
                    item["stage"] = "done"
                    self.save()

    # -- results --

    def _data(self, item: dict) -> dict:
        """Baseline, every sliced candidate and the measured effects of single changes."""
        runs = self.app.runs.runs
        base = self._baseline(item["plate"])
        base_goals = (self.app.goals_of(base, base) if base else None) or {}
        tol = {**NOISE, "time_s": ((base or {}).get("metrics") or {}).get("tolerance_s") or 0.0}
        points, seen, solo = [], set(), {}
        counts = {"done": 0, "total": 0, "left": 0, "error": 0}
        measure_slices = 0
        for stage in ("measure", "combine"):
            sid = item["searches"].get(stage)
            s = self.app.searches.items.get(sid) if sid else None
            if s is None:
                continue
            for c in s["candidates"]:
                run = runs.get(c["run_id"]) or {}
                if stage == "measure":
                    measure_slices += 1
                if c["run_id"] in seen:
                    continue
                seen.add(c["run_id"])
                counts["total"] += 1
                st = run.get("status")
                if st == "done" and run.get("goals"):
                    counts["done"] += 1
                    goals = self.app.goals_of(run, base)
                    points.append({**goals, "run_id": c["run_id"], "assignment": c["assignment"],
                                   "label": run.get("label")})
                    if len(c["assignment"]) == 1:
                        (k, v), = c["assignment"].items()
                        solo[(k, v)] = goals
                elif st in ("queued", "slicing", "analysing"):
                    counts["left"] += 1
                elif st == "error":
                    counts["error"] += 1
        if base and base.get("status") == "done" and base["id"] not in seen and base.get("goals"):
            points.append({**base_goals, "run_id": base["id"], "assignment": {}, "label": "Current settings"})
        eff = effects(item.get("knobs", []), solo, base_goals, item["weights"], tol) if base_goals else []
        return {"base": base, "base_goals": base_goals, "points": points, "effects": eff, "counts": counts,
                "measure_slices": measure_slices, "tolerance": thresholds(base_goals, tol)}

    def view(self, item: dict) -> dict:
        data = self._data(item)
        base_goals = data["base_goals"]
        limits = limits_for(item.get("limits", {}), base_goals)
        rec = recommend(data["points"], item["weights"], limits) if data["points"] else {"pick": None, "best_for": {}, "front": []}
        catalog = {k["key"]: k for k in search.catalog_for(self.app.project, item["plate"])}
        solo = {(e["key"], r["value"]): r for e in data["effects"] for r in e["values"] if r.get("measured")}

        def describe(run_id):
            p = next((x for x in data["points"] if x["run_id"] == run_id), None)
            if p is None:
                return None
            changes = []
            for key, value in sorted(p["assignment"].items()):
                k = catalog.get(key, {})
                if str(value) == str(k.get("current")):
                    continue
                r = solo.get((key, str(value)))
                changes.append({"key": key, "label": k.get("label", key), "from": k.get("current"), "to": value,
                                "scope": k.get("scope", "object"), "explain": k.get("explain"), "note": k.get("note"),
                                "alone": r["deltas"] if r else None})
            goals = {g: p.get(g) for g in search.GOALS}
            run = self.app.runs.runs.get(run_id)
            q = self.app.quality_of(run, data["base"]) if run else None
            return {"run_id": run_id, "label": p.get("label"), "goals": goals, "changes": changes,
                    "quality": q and {"worst": q["worst"], "aspects": q["aspects"]},
                    "deltas": {g: goals[g] - base_goals[g] for g in goals
                               if goals[g] is not None and base_goals.get(g) is not None}}

        stages = []
        for name in ("measure", "combine"):
            sid = item["searches"].get(name)
            s = self.app.searches.items.get(sid) if sid else None
            if s is not None:
                v = self.app.searches.view(s)
                stages.append({"name": name, "search_id": sid, "counts": v["counts"], "total": v["total"], "eta_s": v["eta_s"]})
        effects_out = []
        for e in data["effects"]:
            k = catalog.get(e["key"], {})
            effects_out.append({**e, "label": k.get("label", e["key"]), "current": k.get("current"),
                                "explain": k.get("explain"), "scope": k.get("scope")})
        status = item["stage"]
        if item.get("stopped") and status != "done":
            status = "stopped"
        return {**{k: v for k, v in item.items() if k not in ("current",)}, "status": status, "stages": stages,
                "counts": data["counts"], "base_goals": base_goals, "limits_abs": {g: list(v) for g, v in limits.items()},
                "effects": effects_out, "front": rec["front"],
                "pick": describe(rec["pick"]) if rec["pick"] else None,
                "best_for": {g: describe(r) for g, r in rec["best_for"].items()},
                "points": [{"run_id": p["run_id"], "label": p["label"], "assignment": p["assignment"],
                            **{g: p.get(g) for g in search.GOALS}} for p in data["points"]],
                "tolerance": data["tolerance"]}


def register(app) -> None:
    store = Optimizations(app)
    app.optimizations = store

    def get_optimize(h, route, qs):
        if len(route) > 1 and route[1] == "preview":
            weights = json.loads(qs.get("weights") or "{}") or DEFAULT_WEIGHTS
            allowed = json.loads(qs["allowed"]) if qs.get("allowed") else None
            return h._json(store.preview(int(qs.get("plate", "1")), weights, allowed,
                                         float(qs["budget_s"]) if qs.get("budget_s") else None,
                                         json.loads(qs["limits"]) if qs.get("limits") else None))
        if len(route) > 1:
            item = store.items.get(route[1])
            return h._json(store.view(item)) if item else h._error(404, "no such optimization")
        plate = int(qs["plate"]) if qs.get("plate") else None
        h._json({"optimizations": [store.view(i) for i in store.current(plate)], "goals": search.GOALS,
                 "defaults": DEFAULT_WEIGHTS})

    def post_optimize(h, route, qs):
        body = h._body()
        if len(route) == 3 and route[1] in store.items:
            action = route[2]
            if action == "stop":
                return h._json(store.stop(route[1]))
            if action == "continue":
                return h._json(store.resume(route[1]))
            if action == "priorities":
                return h._json(store.update(route[1], body.get("weights"), body.get("limits")))
            return h._error(404, "unknown action")
        h._json(store.create(int(body["plate"]), body.get("weights") or DEFAULT_WEIGHTS, body.get("limits") or {},
                             body.get("allowed"), body.get("budget_s")))

    def delete_optimize(h, route, qs):
        if len(route) < 2 or route[1] not in store.items:
            return h._error(404, "no such optimization")
        store.delete(route[1])
        h._json({"deleted": route[1]})

    app.extensions.update({"GET optimize": get_optimize, "POST optimize": post_optimize,
                           "DELETE optimize": delete_optimize})
