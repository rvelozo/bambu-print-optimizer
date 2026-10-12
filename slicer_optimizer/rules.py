"""Project rules: settings that must hold everywhere, and features a slice must not contain.

Rules come from a JSON file next to the project: either "<project>.rules.json"
or "slicer-rules.json" in the same folder, or one passed explicitly:

    {
      "require": {"enable_support": "0"},
      "forbid_features": ["Support"]
    }

"require" is checked against the print-wide value and every per-object and
per-part override. "forbid_features" is checked against a slice result; a name
matches any feature that starts with it ("Support" also catches
"Support interface").
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .bambu import SliceResult
from .project3mf import Project


@dataclass
class Rules:
    require: dict[str, str] = field(default_factory=dict)
    forbid_features: list[str] = field(default_factory=list)
    source: Path | None = None

    @property
    def empty(self) -> bool:
        return not self.require and not self.forbid_features


@dataclass
class Finding:
    level: str  # "error" breaks a rule; "warning" deserves a look; "info" is context
    where: str
    message: str


def find_rules_file(project_path: Path) -> Path | None:
    for cand in (project_path.with_suffix(".rules.json"), project_path.parent / "slicer-rules.json"):
        if cand.exists():
            return cand
    return None


def load_rules(project_path: Path, explicit: Path | None = None) -> Rules:
    path = explicit or find_rules_file(project_path)
    if path is None:
        return Rules()
    data = json.loads(Path(path).read_text())
    unknown = set(data) - {"require", "forbid_features"}
    if unknown:
        raise ValueError(f"{path}: unknown rule section(s) {', '.join(sorted(unknown))}")
    return Rules({k: str(v) for k, v in data.get("require", {}).items()},
                 list(data.get("forbid_features", [])), Path(path))


def _values(v) -> list[str]:
    """Comparable values: lists stay lists; per-object vectors look like "80%,nil,nil"."""
    if isinstance(v, list):
        return [str(x) for x in v]
    return [x for x in str(v).split(",") if x != "nil"]


def check_settings(project: Project, rules: Rules) -> list[Finding]:
    out: list[Finding] = []
    for key, want in rules.require.items():
        if not project.has_key(key):
            out.append(Finding("warning", "rules", f"{key} is not a setting in this project (typo?)"))
            continue
        got = project.print_settings.get(key)
        if any(v != want for v in _values(got)):
            out.append(Finding("error", "print-wide", f"{key} = {got} (rule: {want})"))
        for o in project.objects:
            where = f"plate {project.plate_of(o.id)}, {o.label}"
            if key in o.overrides and any(v != want for v in _values(o.overrides[key])):
                out.append(Finding("error", where, f"{key} = {o.overrides[key]} set on the object (rule: {want})"))
            for p in o.parts:
                if key in p.overrides and any(v != want for v in _values(p.overrides[key])):
                    out.append(Finding("error", f"{where}, part {p.id}",
                                       f"{key} = {p.overrides[key]} set on the part (rule: {want})"))

    # Not rules, but worth knowing about in any project.
    if "enable_support" not in rules.require:
        if project.print_settings.get("enable_support") == "1":
            out.append(Finding("warning", "print-wide", "supports are enabled"))
        for o in project.objects:
            if o.overrides.get("enable_support") == "1":
                out.append(Finding("warning", f"plate {project.plate_of(o.id)}, {o.label}",
                                   "supports are enabled on this object"))
    for o in project.objects:
        for p in o.parts:
            shadowed = sorted(set(p.overrides) & set(o.overrides))
            if shadowed:
                out.append(Finding("info", f"{o.label}, part {p.id}",
                                   f"part-level value wins over the object's for: {', '.join(shadowed)}"))
    return out


def check_slice(result: SliceResult, rules: Rules) -> list[Finding]:
    out = []
    for name in rules.forbid_features:
        hits = {k: v for k, v in result.feature_s.items() if k.startswith(name) and v > 0}
        if hits:
            detail = ", ".join(f"{k} {v:.0f} s" for k, v in hits.items())
            out.append(Finding("error", f"plate {result.plate}", f"slice contains {detail} (rule: no {name})"))
    return out
