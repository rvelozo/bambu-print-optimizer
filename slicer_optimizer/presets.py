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


@lru_cache(maxsize=None)
def _index(kind: str) -> dict[str, Path]:
    out: dict[str, Path] = {}
    root = profiles_root()
    if root.is_dir():
        for vendor in sorted(root.iterdir()):
            folder = vendor / kind
            if folder.is_dir():
                for f in folder.glob("*.json"):
                    out.setdefault(f.stem, f)
    return out


@lru_cache(maxsize=None)
def resolve(kind: str, name: str | None) -> dict | None:
    """Merged settings of a system preset and its ancestors, or None if it isn't a system preset."""
    if not name:
        return None
    merged: dict = {}
    seen = set()
    current = name
    while current and current not in seen:
        seen.add(current)
        path = _index(kind).get(current)
        if path is None:
            if current == name:
                return None
            break
        data = json.loads(path.read_text())
        for k, v in data.items():
            merged.setdefault(k, v)
        current = data.get("inherits")
    return {k: v for k, v in merged.items() if k not in META_KEYS}


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
        return None

    def process_default(self, key: str):
        return self.process.get(key)
