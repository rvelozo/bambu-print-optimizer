"""Bambu Studio's Process settings, arranged as the app shows them (Develop mode on).

settings_layout.json is extracted from the Bambu Studio source by
tools/extract_settings_layout.py: pages (Quality, Strength, Speed, Support,
Others), their sections, and each setting's label, unit, tooltip and choices.
Here it is combined with a project: only settings the project has, with their
current value on a plate, so a change can be picked by name instead of by key.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

from . import search
from .project3mf import Project, ProjectError

TEXT_TYPES = {"coString", "coStrings", "coPoints", "coPoint"}  # nothing to try with free text or coordinates
NUMBER = re.compile(r"^-?\d+(\.\d+)?$")


@lru_cache(maxsize=1)
def layout() -> dict:
    return json.loads((Path(__file__).parent / "settings_layout.json").read_text())


def definition(key: str) -> dict | None:
    return layout()["settings"].get(key)


@lru_cache(maxsize=2)
def _names(pages: str = "pages") -> dict[str, str]:
    """Bambu's label, plus the section when the label alone repeats ("Outer wall" is in four)."""
    base, section = {}, {}
    for page in layout().get(pages, []):
        for group in page["groups"]:
            for opt in group["options"]:
                label = (definition(opt["key"]) or {}).get("label") or opt["key"]
                if opt.get("line"):  # "Overhang speed 10%"; filament lines read "Linear ramp up (Fan speed)"
                    label = f"{opt['line']} ({label})" if pages == "filament_pages" else f"{opt['line']} {label}"
                base[opt["key"]] = label
                section[opt["key"]] = group["title"]
    count: dict[str, int] = {}
    for name in base.values():
        count[name] = count.get(name, 0) + 1
    return {k: (f"{n} ({section[k]})" if count[n] > 1 else n) for k, n in base.items()}


@lru_cache(maxsize=1)
def _places() -> dict[str, tuple[str, str, str]]:
    """Where each setting sits in Bambu Studio: (tab, page, section)."""
    out = {}
    for tab, pages in (("Filament", layout().get("filament_pages", [])), ("Process", layout()["pages"])):
        for page in pages:
            for group in page["groups"]:
                for opt in group["options"]:
                    out[opt["key"]] = (tab, page["title"], group["title"])
    return out


def place(key: str) -> tuple[str, str, str] | None:
    """("Process", "Speed", "Other layers speed") for outer_wall_speed; None if Bambu doesn't show it."""
    return _places().get(key)


def display_name(key: str) -> str:
    """The setting's name outside the picker: "Overhang speed 50%", "Outer wall (Line width)"."""
    if key in _names():
        return _names()[key]
    if key in search.KNOBS:
        return search.KNOBS[key].get("label") or key
    if key in _names("filament_pages"):
        return _names("filament_pages")[key]
    # filament settings outside the tab's pages; per-filament overrides of process settings
    d = definition(key) or (definition(key[len("filament_"):]) if key.startswith("filament_") else None) or {}
    return d.get("full_label") or d.get("label") or key


def value_text(key: str, value: str | None) -> str:
    """A value as the app shows it: the choice's name, On/Off, or the number with its unit."""
    if value is None:
        return "removed"
    d = definition(key) or {}
    v = str(value)
    if d.get("choices"):
        return next((c[1] for c in d["choices"] if c[0] == v), v)
    if d.get("type") in ("coBool", "coBools"):
        return "On" if v in ("1", "true") else "Off"
    unit = d.get("sidetext")
    if not unit or v.endswith(unit):
        return v
    if unit.endswith(" or %"):
        return v if v.endswith("%") else f"{v} {unit[:-5]}"
    return f"{v} {unit}"


def file_label(changes: list[dict]) -> str:
    """A short name for a saved variant's file: setting names and plain values ("Bridge 100")."""
    seen: list[str] = []
    for c in changes:
        v = c.get("value")
        value = "removed" if v is None else (value_text(c["key"], v) if (definition(c["key"]) or {}).get("choices") else str(v))
        item = f"{display_name(c['key'])} {value}"
        if item not in seen:
            seen.append(item)
    return re.sub(r"[^\w.%=,+() -]+", "-", ", ".join(seen)).strip()


def change_label(changes: list[dict]) -> str:
    """A run's name from its changes, by setting name ("Bridge 100 mm/s, Top shell layers 3")."""
    seen: list[tuple] = []
    for c in changes:
        item = (c["key"], c.get("value"), c.get("object_id") is None)
        if item not in seen:
            seen.append(item)
    if not seen:
        return "Current settings"
    return ", ".join(f"{display_name(k)}: {value_text(k, v)}" + (" (whole project)" if pw else "") for k, v, pw in seen)


def picker(project: Project, plate: int) -> dict:
    """The Process tab for this project, with each setting's value now on the plate."""
    objs = project.objects_on_plate(plate)
    pages = []
    for page in layout()["pages"]:
        groups = []
        for group in page["groups"]:
            rows = []
            for opt in group["options"]:
                key = opt["key"]
                d = definition(key) or {}
                if not project.has_key(key) or d.get("type") in TEXT_TYPES:
                    continue
                sources = {project.effective(key, o)[1] for o in objs} if objs else {"print-wide"}
                rows.append({
                    "key": key, "label": d.get("label") or key, "line": opt.get("line"),
                    "unit": d.get("sidetext"), "tooltip": d.get("tooltip"), "type": d.get("type"),
                    "choices": d.get("choices"), "min": d.get("min"), "max": d.get("max"),
                    "now": search.current_value(project, plate, key, "object") if objs else
                    search.current_value(project, plate, key, "print-wide"),
                    "source": sources.pop() if len(sources) == 1 else "varies",
                    "default": search._norm(project.active(project.presets.process_default(key))),
                })
            if rows:
                groups.append({"title": group["title"], "settings": rows})
        if groups:
            pages.append({"title": page["title"], "groups": groups})
    return {"bambu_studio": layout()["bambu_studio"], "pages": pages}


def check_value(key: str, value: str) -> str:
    """The value as Bambu Studio stores it, or ProjectError naming what's wrong (by its label)."""
    d = definition(key)
    if d is None:
        return value  # not in the Process tab: passed through as typed
    label = display_name(key)
    t = d.get("type", "")
    v = value.strip()
    if d.get("choices"):
        allowed = {c[0]: c[1] for c in d["choices"]}
        by_label = {c[1].lower(): c[0] for c in d["choices"]}
        if v in allowed:
            return v
        if v.lower() in by_label:
            return by_label[v.lower()]
        raise ProjectError(f"{label}: choose one of {', '.join(allowed.values())}")
    if t in ("coBool", "coBools"):
        b = {"1": "1", "0": "0", "true": "1", "false": "0", "on": "1", "off": "0", "yes": "1", "no": "0"}.get(v.lower())
        if b is None:
            raise ProjectError(f"{label}: on or off")
        return b
    if t in ("coInt", "coInts"):
        if not re.fullmatch(r"-?\d+", v):
            raise ProjectError(f"{label}: a whole number")
        _limits(d, float(v), label)
        return v
    if t in ("coFloat", "coFloats"):
        if not NUMBER.match(v):
            raise ProjectError(f"{label}: a number")
        _limits(d, float(v), label)
        return v
    if t in ("coPercent", "coPercents"):
        n = v[:-1] if v.endswith("%") else v
        if not NUMBER.match(n):
            raise ProjectError(f"{label}: a percentage")
        _limits(d, float(n), label)
        return f"{n}%"
    if t in ("coFloatOrPercent", "coFloatsOrPercents"):
        n = v[:-1] if v.endswith("%") else v
        if not NUMBER.match(n):
            raise ProjectError(f"{label}: a number, or a percentage like 50%")
        return v
    return v


def _limits(d: dict, x: float, label: str) -> None:
    if d.get("min") is not None and x < d["min"]:
        raise ProjectError(f"{label}: at least {d['min']:g}")
    if d.get("max") is not None and x > d["max"]:
        raise ProjectError(f"{label}: at most {d['max']:g}")
