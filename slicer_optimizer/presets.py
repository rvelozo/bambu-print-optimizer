"""Bambu Studio's system presets, read from the app bundle.

Presets live in Contents/Resources/profiles/<vendor>/<process|filament|machine>/<name>.json
and inherit from each other through "inherits". Resolving the chain gives the
system value of every setting, and tells process, filament and printer keys apart.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from . import paths

META_KEYS = {"name", "inherits", "from", "type", "setting_id", "instantiation", "description",
             "renamed_from", "version", "is_custom_defined"}


def profiles_root() -> Path:
    return paths.studio_profiles()


VENDOR = "BBL"  # Bambu Lab's own folder, searched first


@lru_cache(maxsize=None)
def _vendors(kind: str) -> dict[str, dict[str, Path]]:
    """Each vendor's presets of this kind by name, Bambu Lab's first."""
    out: dict[str, dict[str, Path]] = {}
    root = profiles_root()
    if root.is_dir():
        for vendor in sorted(root.iterdir(), key=lambda v: (v.name != VENDOR, v.name)):
            folder = vendor / kind
            if folder.is_dir():
                out[vendor.name] = {f.stem: f for f in folder.glob("*.json")}
    return out


def _find(kind: str, name: str, vendor: str | None = None) -> tuple[str, Path] | None:
    """A preset file: in this vendor's folder when given, else the first vendor that has it."""
    vendors = _vendors(kind)
    if vendor in vendors and name in vendors[vendor]:
        return vendor, vendors[vendor][name]
    for v, presets in vendors.items():
        if name in presets:
            return v, presets[name]
    return None


@lru_cache(maxsize=None)
def resolve(kind: str, name: str | None) -> dict | None:
    """Merged settings of a system preset and its ancestors, or None if it isn't a system preset.

    A preset inherits within its own vendor's folder, as Bambu Studio resolves it:
    base presets such as fdm_process_common exist under many vendors with
    different values (looked up by name alone, Anker's was found first).
    """
    if not name:
        return None
    merged: dict = {}
    seen = set()
    current = name
    vendor = None
    while current and current not in seen:
        seen.add(current)
        found = _find(kind, current, vendor)
        if found is None:
            if current == name:
                return None
            break
        vendor, path = found
        data = json.loads(path.read_text())
        for k, v in data.items():
            merged.setdefault(k, v)
        current = data.get("inherits")
    return {k: v for k, v in merged.items() if k not in META_KEYS}


def standalone(kind: str, name: str) -> dict:
    """A system preset with its parents folded in, as a preset file Bambu Studio's command line can load.

    Given a preset file, the command line doesn't load the presets it inherits
    from: settings the file itself doesn't name come out as Bambu's built-in
    defaults (0.20mm Standard @BBL P2S exported with 0.4 mm lines and 4 top
    shells instead of 0.42 mm and 5; Bambu PLA Basic with no density, so 0 g).
    """
    resolved = resolve(kind, name)
    if resolved is None:
        raise ValueError(f"no system {kind} preset named {name!r}")
    out = {k: v for k, v in resolved.items() if k != "inherits"}
    out.update(name=name, type=kind, **{"from": "system", "instantiation": "true"})
    return out


class SystemPresets:
    """The system presets a project was built on (process, printer, one filament per slot)."""

    def __init__(self, print_settings: dict):
        self.process = resolve("process", print_settings.get("print_settings_id")) or {}
        self.printer = resolve("machine", print_settings.get("printer_settings_id")) or {}
        self.filaments = [resolve("filament", n) or {} for n in print_settings.get("filament_settings_id") or []]

    @property
    def found(self) -> bool:
        return bool(self.process)

    def group(self, key: str) -> str | None:
        if key in self.process:
            return "process"
        if any(key in f for f in self.filaments):
            return "filament"
        if key in self.printer:
            return "printer"
        from . import settings  # settings no preset file names: by the tab Bambu shows them in
        place = settings.place(key)
        return {"Process": "process", "Filament": "filament"}.get(place[0]) if place else None

    def process_default(self, key: str):
        """The process preset's value, or Bambu's built-in default when no preset file names it."""
        if key in self.process or not self.found:
            return self.process.get(key)
        from . import bambu
        return bambu.builtin_defaults().get(key)

    def filament_default(self, slot: int, key: str):
        """The system value for this filament slot (0-based), or None if its preset isn't a system one."""
        if slot >= len(self.filaments) or not self.filaments[slot]:
            return None
        if key in self.filaments[slot]:
            return self.filaments[slot][key]
        from . import bambu
        return bambu.builtin_defaults().get(key)
