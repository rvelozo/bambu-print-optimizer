"""Try setting changes on one plate and measure the effect with the real slicer.

When a trial changes nothing, it finds out why with more slices: another
setting blocking it (top_shell_thickness sets a minimum that top_shell_layers
can't go below), a per-object value that Bambu Studio ignores (the same value
print-wide does change the plate), or no effect on this plate at all.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass, field
from pathlib import Path

from . import bambu
from .bambu import SliceResult
from .project3mf import Change, Project, ProjectError, write_bytes_atomic


# Settings that can block a change, the value that lifts the block, and why.
# Verified on the v7 lid: top_shell_layers 5 -> 3 alone changes nothing because
# top_shell_thickness = 1 mm already requires 5 layers at 0.2 mm.
BLOCKERS = {
    "top_shell_layers": ("top_shell_thickness", "0", "it sets a minimum top shell thickness"),
    "bottom_shell_layers": ("bottom_shell_thickness", "0", "it sets a minimum bottom shell thickness"),
}


@dataclass
class PlannedChange:
    change: Change
    target: str
    before: str
    noop: bool


@dataclass
class Trial:
    plate: int
    planned: list[PlannedChange]
    base: SliceResult
    variant: SliceResult | None = None
    variant_bytes: bytes | None = field(default=None, repr=False)
    diagnosis: str | None = None
    print_wide_check: SliceResult | None = None
    unblocked: SliceResult | None = None
    unblocked_by: list[Change] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def changes(self) -> list[Change]:
        return [p.change for p in self.planned if not p.noop]


def _norm(v) -> list[str]:
    if isinstance(v, list):
        return [str(x) for x in v]
    return [x for x in str(v).split(",") if x != "nil"] if v is not None else []


def _describe(project: Project, key: str, value, source: str) -> str:
    default = " = system default" if source == "print-wide" and project.is_system_default(key, value) else ""
    return f"{value} ({source}{default})"


def plan_changes(project: Project, plate: int, sets: list[tuple[str, str | None]],
                 object_refs: list[str] | None = None, print_wide: bool = False,
                 allow_unknown: bool = False) -> list[PlannedChange]:
    """Turn key=value requests into concrete changes.

    By default every object on the plate gets a per-object override, so other
    plates are untouched. print_wide edits the project-wide value instead.
    """
    if not project.plate(plate).object_ids:  # also raises for an unknown plate
        raise ProjectError(f"plate {plate} is empty: nothing to slice")
    for key, _ in sets:
        if not allow_unknown and not project.has_key(key):
            raise ProjectError(f"unknown setting {key!r} (check the spelling; --allow-unknown to force)")
    planned = []
    if print_wide:
        for key, value in sets:
            if value is None:
                raise ProjectError(f"cannot remove the print-wide value of {key}")
            before = project.print_settings.get(key)
            planned.append(PlannedChange(Change(key, value, None), "print-wide",
                                         _describe(project, key, before, "print-wide"),
                                         noop=all(v == value for v in _norm(project.active(before)))))
        return planned

    if object_refs:
        targets = []
        for ref in object_refs:
            found = project.find_objects(ref)
            if not found:
                raise ProjectError(f"no object with id or name {ref!r}")
            targets += [o for o in found if o not in targets]
    else:
        targets = project.objects_on_plate(plate)
    for o in targets:
        for key, value in sets:
            cur, source = project.effective(key, o)
            if value is None:
                noop = key not in o.overrides
            else:
                noop = source != "part" and cur is not None and all(v == value for v in _norm(project.active(cur)))
            note = ""
            if source == "part":
                note = " (a part-level value overrides the object; this change may not apply)"
            planned.append(PlannedChange(Change(key, value, o.id), o.label, _describe(project, key, cur, source) + note, noop))
    return planned


def run_trial(project: Project, plate: int, planned: list[PlannedChange], use_cache: bool = True) -> Trial:
    # Two real slices of the baseline tell us how much this plate's numbers move by themselves.
    base = bambu.slice_bytes(project.source_bytes(), plate, use_cache=use_cache, repeats=2)
    trial = Trial(plate, planned, base)
    changes = trial.changes
    if not changes:
        trial.diagnosis = "nothing to try: every requested value is already in effect"
        return trial
    trial.variant_bytes = project.build(changes)
    trial.notes = list(project.notes)
    trial.variant = bambu.slice_bytes(trial.variant_bytes, plate, use_cache=use_cache)
    if not trial.variant.same_as(base):
        return trial

    # 1. Is another setting blocking it? Lift the block in the same scope and slice again.
    keys = {c.key for c in changes}
    lifts, reasons = [], []
    for c in changes:
        if c.key in BLOCKERS and c.value is not None:
            partner, free, why = BLOCKERS[c.key]
            target = project.object(c.object_id) if c.object_id else None
            current, _ = project.effective(partner, target)
            if partner not in keys and current is not None and _norm(current) != [free]:
                lifts.append(Change(partner, free, c.object_id))
                reasons.append(f"{partner} = {current} ({why})")
    if lifts:
        lifted = changes + list(dict.fromkeys(lifts))
        check = bambu.slice_bytes(project.build(lifted), plate, use_cache=use_cache)
        if not check.same_as(base):
            trial.unblocked, trial.unblocked_by = check, list(dict.fromkeys(lifts))
            trial.diagnosis = ("blocked by " + "; ".join(dict.fromkeys(reasons)) + ". Changing "
                               + " and ".join(sorted({c.key for c in lifts})) + " too does change this plate (shown below).")
            return trial

    # 2. Ignored per object? Try the same values print-wide.
    per_object = [c for c in changes if c.object_id is not None and c.value is not None]
    if per_object:
        pw = list(dict.fromkeys(Change(c.key, c.value, None) for c in per_object))
        trial.print_wide_check = bambu.slice_bytes(project.build(pw), plate, use_cache=use_cache)
        if not trial.print_wide_check.same_as(base):
            trial.diagnosis = ("ignored per object: Bambu Studio did not apply this per-object value, "
                               "but the same value print-wide does change this plate (shown below). "
                               "Print-wide affects every plate.")
            return trial
    trial.diagnosis = ("no effect on this plate: another setting may take precedence, the value may be "
                       "capped (for example by the filament's max volumetric speed), or it controls "
                       "something this plate doesn't have")
    return trial


def save_variant(project: Project, trial: Trial, dest: Path, overwrite: bool = False) -> tuple[Path, Path]:
    """Write exactly the bytes that were sliced, plus a provenance file next to them."""
    if trial.variant_bytes is None or trial.variant is None:
        raise ProjectError("nothing to save: the trial made no changes")
    dest = Path(dest)
    if dest.exists() and dest.resolve() == project.path.resolve():
        raise ProjectError("refusing to overwrite the source project; pick another file name")
    if dest.exists() and not overwrite:
        raise ProjectError(f"{dest} already exists (use --overwrite)")
    write_bytes_atomic(dest, trial.variant_bytes)
    prov = dest.with_name(dest.stem + ".provenance.json")
    prov.write_text(json.dumps({
        "source": str(project.path.resolve()),
        "source_sha256": trial.base.sha256,
        "sha256": trial.variant.sha256,
        "plate": trial.plate,
        "bambu_studio": bambu.studio_version(),
        "changes": [{"key": p.change.key, "value": p.change.value, "target": p.target, "before": p.before}
                    for p in trial.planned if not p.noop],
        "before": {"total_s": round(trial.base.total_s), "grams": round(trial.base.grams, 2)},
        "after": {"total_s": round(trial.variant.total_s), "grams": round(trial.variant.grams, 2)},
        "created": dt.datetime.now().isoformat(timespec="seconds"),
    }, indent=2, ensure_ascii=False) + "\n")
    return dest, prov
