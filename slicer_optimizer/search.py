"""Searches over settings: each setting on its own (sensitivity) or every
combination (grid), the Pareto front over the chosen goals, and the pick.

A candidate is a set of values for some knobs from catalog.json. It becomes a
list of changes (per object on the plate, or print-wide), and from there an
ordinary slice: same cache, same numbers as any trial. Values equal to what the
project already uses produce no change, so the "all current" candidate is the
baseline itself.
"""

from __future__ import annotations

import itertools
import json
import math
from pathlib import Path

from .project3mf import Project, ProjectError

CATALOG = json.loads((Path(__file__).parent / "catalog.json").read_text())
GOALS = CATALOG["goals"]
KNOBS = {k["key"]: k for k in CATALOG["knobs"]}


def _norm(v) -> str | None:
    if v is None:
        return None
    if isinstance(v, list):
        return str(v[0]).strip() if len(set(map(str, v))) == 1 else ",".join(map(str, v))
    parts = [p for p in str(v).split(",") if p != "nil"]
    return parts[0].strip() if parts else str(v).strip()


def current_value(project: Project, plate: int, key: str, scope: str) -> str | None:
    """The value now in effect for the knob on this plate (None if objects disagree)."""
    if scope == "print-wide":
        if key.startswith("filament_"):
            values = {_norm(project.filament_value(key, o)) for o in project.objects_on_plate(plate)}
            return values.pop() if len(values) == 1 else None
        return _norm(project.active(project.print_settings.get(key)))
    values = {_norm(project.active(project.effective(key, o)[0])) for o in project.objects_on_plate(plate)}
    return values.pop() if len(values) == 1 else None


def catalog_for(project: Project, plate: int) -> list[dict]:
    out = []
    for k in CATALOG["knobs"]:
        if not project.has_key(k["key"]):
            continue
        out.append({**k, "current": current_value(project, plate, k["key"], k["scope"]),
                    "default": _norm(project.active(project.presets.process_default(k["key"])))})
    return out


def changes_for(project: Project, plate: int, assignment: dict[str, str]) -> list[dict]:
    """Changes (as the runs API takes them) that make the plate use these knob values."""
    objs = [o.id for o in project.objects_on_plate(plate)]
    changes: list[dict] = []
    for key, value in sorted(assignment.items()):
        knob = KNOBS.get(key, {"scope": "object"})
        scope = knob["scope"]
        value = str(value).strip()
        if current_value(project, plate, key, scope) == value:
            continue
        targets = objs if scope == "object" else [None]
        changes += [{"key": key, "value": value, "object_id": t} for t in targets]
        for pk, pv in knob.get("with", {}).items():
            if project.has_key(pk) and current_value(project, plate, pk, scope) != pv:
                changes += [{"key": pk, "value": pv, "object_id": t} for t in targets]
    seen, unique = set(), []
    for c in changes:
        k = (c["key"], c["object_id"])
        if k not in seen:
            seen.add(k)
            unique.append(c)
    return unique


def label_for(assignment: dict[str, str]) -> str:
    if not assignment:
        return "Current settings"
    return ", ".join(f"{k}={v}" for k, v in sorted(assignment.items()))


def sensitivity(knobs: list[dict]) -> list[dict[str, str]]:
    """The current settings plus each knob value on its own."""
    out = [{}]
    for k in knobs:
        for v in k["values"]:
            out.append({k["key"]: str(v)})
    return out


def grid(knobs: list[dict]) -> list[dict[str, str]]:
    keys = [k["key"] for k in knobs]
    return [dict(zip(keys, map(str, combo))) for combo in itertools.product(*(k["values"] for k in knobs))]


def validate(project: Project, knobs: list[dict]) -> None:
    for k in knobs:
        if not project.has_key(k["key"]):
            raise ProjectError(f"unknown setting {k['key']!r}")
        if not k.get("values"):
            raise ProjectError(f"{k['key']}: give at least one value")


# ---- Pareto front and the pick ----------------------------------------------------------

def _better(goal: str) -> str:
    return GOALS.get(goal, {}).get("better", "lower")


def dominates(a: dict, b: dict, goals: list[str]) -> bool:
    strictly = False
    for g in goals:
        x, y = a.get(g), b.get(g)
        if x is None or y is None:
            return False
        if _better(g) == "higher":
            x, y = -x, -y
        if x > y:
            return False
        if x < y:
            strictly = True
    return strictly


def pareto(points: list[dict], goals: list[str]) -> list[int]:
    """Indices of points no other point beats on every goal."""
    return [i for i, p in enumerate(points) if not any(dominates(q, p, goals) for j, q in enumerate(points) if j != i)]


def feasible(p: dict, limits: dict[str, tuple[str, float]]) -> bool:
    for g, (op, v) in limits.items():
        x = p.get(g)
        if x is None:
            return False
        if op == "<=" and x > v:
            return False
        if op == ">=" and x < v:
            return False
    return True


def pick(points: list[dict], weights: dict[str, float], limits: dict[str, tuple[str, float]] | None = None) -> int | None:
    """Index of the point closest to the ideal by weighted Chebyshev distance.

    Objectives are normalized to [0, 1] over the feasible points (0 = best seen).
    A plain weighted sum can never pick a point in a concave part of the front;
    the Chebyshev distance can. A 1% sum term breaks ties.
    """
    goals = [g for g, w in weights.items() if w > 0]
    if not goals:
        return None
    ok = [i for i, p in enumerate(points) if feasible(p, limits or {}) and all(p.get(g) is not None for g in goals)]
    if not ok:
        return None
    front = [ok[i] for i in pareto([points[i] for i in ok], goals)]
    lo = {g: min(points[i][g] for i in ok) for g in goals}
    hi = {g: max(points[i][g] for i in ok) for g in goals}
    best, best_score = None, math.inf
    for i in front:
        terms = []
        for g in goals:
            span = hi[g] - lo[g]
            v = points[i][g]
            norm = 0.0 if span == 0 else ((v - lo[g]) / span if _better(g) == "lower" else (hi[g] - v) / span)
            terms.append(weights[g] * norm)
        score = max(terms) + 0.01 * sum(terms)
        if score < best_score:
            best, best_score = i, score
    return best
