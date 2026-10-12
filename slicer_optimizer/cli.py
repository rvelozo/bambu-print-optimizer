"""Command line: slicer-opt info | check | slice | try | search | analyze | serve | open | log | prints | cache."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import bambu, fmt
from .bambu import SliceError, SliceResult
from .project3mf import Project, ProjectError
from .rules import check_settings, check_slice, load_rules
from .trial import PlannedChange, plan_changes, run_trial, save_variant


def _plates(project: Project, arg: str | None) -> list[int]:
    if arg in (None, "all"):
        return [p.index for p in project.plates if p.object_ids]
    plates = [int(x) for x in arg.split(",")]
    for n in plates:
        if not project.plate(n).object_ids:
            raise ProjectError(f"plate {n} is empty: nothing to slice")
    return plates


def _plate_title(project: Project, plate: int) -> str:
    objs = project.objects_on_plate(plate)
    counts = {}
    for i in project.plate(plate).object_ids:
        counts[i] = counts.get(i, 0) + 1
    names = {}
    for o in objs:
        names[o.name] = names.get(o.name, 0) + counts.get(o.id, 1)
    return ", ".join(n + (f" ×{c}" if c > 1 else "") for n, c in names.items())


# ---- info ----------------------------------------------------------------------

def cmd_info(args) -> int:
    pr = Project(args.project)
    ps = pr.print_settings
    print(f"{pr.path.name} · saved by Bambu Studio {pr.studio_version}")
    print(f"Printer: {ps.get('printer_settings_id')} · Process: {ps.get('print_settings_id')}")
    for plate in pr.plates:
        print(f"\nPlate {plate.index}" + (f" · {plate.name}" if plate.name else ""))
        if plate.settings:
            print("  plate settings: " + ", ".join(f"{k}={v}" for k, v in plate.settings.items()))
        for o in pr.objects_on_plate(plate.index):
            fil = pr.filament_for(o)
            copies = plate.object_ids.count(o.id)
            print(f"  object {o.id}  {o.name}" + (f" ×{copies}" if copies > 1 else "")
                  + f" · filament {fil['slot']}: {fil['name']} (max flow {fil['max_flow']} mm³/s)")
            if o.overrides:
                print("      per-object: " + ", ".join(f"{k}={v}" for k, v in sorted(o.overrides.items())))
            for p in o.parts:
                if p.overrides:
                    print(f"      part {p.id} ({p.name}): " + ", ".join(f"{k}={v}" for k, v in sorted(p.overrides.items())))
            for key in args.key or []:
                val, src = pr.effective(key, o)
                print(f"      {key} = {pr.active(val)} ({src})")
    unplaced = [o for o in pr.objects if pr.plate_of(o.id) is None]
    if unplaced:
        print("\nNot on any plate: " + ", ".join(o.label for o in unplaced))
    mod = pr.modified_vs_system()
    if mod:
        print("\nChanged from the system presets")
        for group, keys in mod.items():
            print(f"  {group}: {', '.join(keys)}")
    return 0


# ---- check ---------------------------------------------------------------------

def _print_findings(findings) -> int:
    errors = 0
    for f in findings:
        errors += f.level == "error"
        print(f"  {f.level:<7} {f.where}: {f.message}")
    return errors


def cmd_check(args) -> int:
    pr = Project(args.project)
    rules = load_rules(pr.path, Path(args.rules) if args.rules else None)
    if rules.empty:
        print("No rules file (looked for <project>.rules.json and slicer-rules.json next to the project).")
    else:
        parts = [f"{k}={v}" for k, v in rules.require.items()] + [f"no {f}" for f in rules.forbid_features]
        print(f"Rules from {rules.source}: " + "; ".join(parts))
    findings = check_settings(pr, rules)
    print("\nSettings")
    errors = _print_findings(findings) if findings else 0
    if not findings:
        print("  no problems")
    if args.slice:
        print("\nSlices")
        for plate in _plates(pr, args.plate):
            res = bambu.slice_file(pr.path, plate, use_cache=not args.no_cache)
            sf = check_slice(res, rules)
            if sf:
                errors += _print_findings(sf)
            else:
                sup = f", supports {fmt.dur(res.support_s)}" if res.support_s else ", no supports"
                print(f"  ok      plate {plate}: {fmt.dur(res.total_s)}, {fmt.grams(res.grams)}{sup}")
    return 1 if errors else 0


# ---- slice ---------------------------------------------------------------------

def _how(res: SliceResult) -> str:
    return "from cache" if res.cached else f"sliced in {res.wall_s:.1f} s"


def _noise(res: SliceResult) -> str:
    if res.noise_s is None:
        return ""
    if res.noise_s == 0:
        return f"identical in {res.samples} slices"
    if res.noise_s < 0.5:
        return f"within a second over {res.samples} slices"
    return f"varies by up to {fmt.dur(res.noise_s)} over {res.samples} slices"


def cmd_slice(args) -> int:
    pr = Project(args.project)
    for plate in _plates(pr, args.plate):
        res = bambu.slice_file(pr.path, plate, use_cache=not args.no_cache, repeats=args.repeat)
        print(f"Plate {plate} · {_plate_title(pr, plate)}")
        noise = f" · time {_noise(res)}" if res.noise_s is not None else ""
        print(f"  {fmt.dur(res.total_s)} · {fmt.grams(res.grams)} · {_how(res)}{noise}")
        printing = {k: v for k, v in res.feature_s.items() if v > 0 and k not in ("Custom", "Undefined", "Travel", "Flush")}
        top = sorted(printing.items(), key=lambda kv: -kv[1])[: args.features]
        print("  " + " · ".join(f"{k} {fmt.dur(v)} ({fmt.pct(v, res.model_s)})" for k, v in top))
        print(f"  supports: {fmt.dur(res.support_s) if res.support_s else 'none'}"
              + (f" · warning: {res.warning}" if res.warning else ""))
    return 0


# ---- try -----------------------------------------------------------------------

def _parse_sets(items: list[str], removes: list[str]) -> list[tuple[str, str | None]]:
    out = []
    for item in items or []:
        if "=" not in item:
            raise ProjectError(f"--set needs key=value, got {item!r}")
        k, v = item.split("=", 1)
        out.append((k.strip(), v.strip()))
    out += [(k.strip(), None) for k in removes or []]
    if not out:
        raise ProjectError("nothing to try: give at least one --set key=value or --remove key")
    return out


def _print_trial(trial, title: str, min_feature_s: float = 1.0) -> None:
    print(f"\n{title}")
    for p in trial.planned:
        new = "remove override" if p.change.value is None else p.change.value
        state = "  (already in effect, skipped)" if p.noop else ""
        where = p.target if p.target == "print-wide" else f"on {p.target}"
        print(f"  {p.change.key}: {p.before} → {new} {where}{state}")
    b, v = trial.base, trial.variant
    if v is None:
        print(f"  {trial.diagnosis}")
        return
    tol = max(b.tolerance_s, v.tolerance_s)

    def dt(d: float) -> str:
        return fmt.dur_delta(d) + (" (within noise)" if 0.5 < abs(d) <= tol else "")

    print(fmt.table([
        ["", "Current", "Trial", "Change"],
        ["Time", fmt.dur(b.total_s), fmt.dur(v.total_s), dt(v.total_s - b.total_s)],
        ["Weight", fmt.grams(b.grams), fmt.grams(v.grams), fmt.grams_delta(v.grams - b.grams)],
    ], right={1, 2, 3}))
    keys = set(b.feature_s) | set(v.feature_s)
    moved = sorted(((k, b.feature_s.get(k, 0), v.feature_s.get(k, 0)) for k in keys),
                   key=lambda t: -abs(t[2] - t[1]))
    moved = [t for t in moved if abs(t[2] - t[1]) >= min_feature_s]
    if moved:
        print("\n" + fmt.table([["Time by feature", "Current", "Trial", "Change"]]
                               + [[k, fmt.dur(x), fmt.dur(y), dt(y - x)] for k, x, y in moved],
                               right={1, 2, 3}))
    for note in trial.notes:
        print(f"\n  note: {note}")
    if trial.diagnosis:
        print(f"\n  {trial.diagnosis}")
        ub = trial.unblocked
        if ub is not None:
            extra = ", ".join(f"{c.key}={c.value}" for c in trial.unblocked_by)
            print(f"  with {extra} too: {fmt.dur(ub.total_s)} ({fmt.dur_delta(ub.total_s - b.total_s)}), "
                  f"{fmt.grams(ub.grams)} ({fmt.grams_delta(ub.grams - b.grams)})")
        pw = trial.print_wide_check
        if pw is not None and not pw.same_as(b):
            print(f"  print-wide: {fmt.dur(pw.total_s)} ({fmt.dur_delta(pw.total_s - b.total_s)}), "
                  f"{fmt.grams(pw.grams)} ({fmt.grams_delta(pw.grams - b.grams)})")
    if b.noise_s:
        print(f"\n  The current file's time {_noise(b)} of the same file, so time changes up to "
              f"±{fmt.dur(tol)} count as no change.")
    print(f"\n  {_how(b).capitalize()} / {_how(v)}.")


def cmd_try(args) -> int:
    pr = Project(args.project)
    sets = _parse_sets(args.set, args.remove)
    planned = plan_changes(pr, args.plate, sets, args.object, args.print_wide, args.allow_unknown)
    header = f"Plate {args.plate} · {_plate_title(pr, args.plate)}"
    if args.each and len(sets) > 1:
        for key, _ in sets:
            sub = [p for p in planned if p.change.key == key]
            _print_trial(run_trial(pr, args.plate, sub, use_cache=not args.no_cache), f"{header} · {key} alone")
        header += " · all together"
    trial = run_trial(pr, args.plate, planned, use_cache=not args.no_cache)
    _print_trial(trial, header)
    if args.save:
        dest, prov = save_variant(pr, trial, Path(args.save), overwrite=args.overwrite)
        print(f"\nSaved {dest} (the exact file that was sliced) and {prov.name}.")
    elif trial.variant is not None:
        print("  Not saved; add --save NEW.3mf to write this variant.")
    return 0


# ---- analyze -------------------------------------------------------------------

def cmd_analyze(args) -> int:
    from . import layers
    pr = Project(args.project)
    for plate in _plates(pr, args.plate):
        res = bambu.slice_file(pr.path, plate, use_cache=not args.no_cache)
        an = layers.load_or_build(res.folder, plate)
        print(f"Plate {plate} · {_plate_title(pr, plate)}")
        ours = an["bambu_total_s"]  # added up in single precision, as Bambu Studio does
        print(f"  Bambu Studio {fmt.dur(res.total_s)} · this tool {fmt.dur(ours)} "
              f"({100 * (ours - res.total_s) / res.total_s:+.3f}%; exact sum {fmt.dur(an['total_s'])})")
        rows = [["Feature", "Bambu Studio", "This tool", "Difference"]]
        for k, v in sorted(res.feature_s.items(), key=lambda kv: -kv[1]):
            if v < 0.5:
                continue
            ours = an["bambu_time_by_feature"].get(k, 0.0)
            rows.append([k, f"{v:.1f} s", f"{ours:.1f} s", f"{100 * (ours - v) / v:+.2f}%"])
        print(fmt.table(rows, right={1, 2, 3}))
        slow = sorted((l for l in an["layers"] if l["index"] > 0), key=lambda l: -l["time_s"])[:5]
        print("  Slowest layers: " + ", ".join(f"{l['index']} ({fmt.dur(l['time_s'])})" for l in slow))
        flow = an["flow"]
        if flow["cap"]:
            print(f"  Flow: {fmt.dur(flow['near_cap_s'])} of printing at ≥95% of the {flow['cap']} mm³/s cap "
                  f"({100 * flow['near_cap_s'] / an['model_s']:.0f}% of model time)")
    return 0


def cmd_search(args) -> int:
    import time as _time
    from . import layers, metrics, search
    from .project3mf import Change
    pr = Project(args.project)
    knobs = []
    for item in args.knob:
        if "=" not in item:
            raise ProjectError(f"--knob needs key=v1,v2,..., got {item!r}")
        k, v = item.split("=", 1)
        knobs.append({"key": k.strip(), "values": [x.strip() for x in v.split(",") if x.strip()]})
    search.validate(pr, knobs)
    assignments = search.sensitivity(knobs) if args.each else search.grid(knobs)
    weights = {}
    for item in (args.weights or "time_s=5,grams=3,stiffness=3").split(","):
        g, _, w = item.partition("=")
        weights[g.strip()] = float(w)
    limits = {}
    if args.max_time:
        limits["time_s"] = ("<=", float(args.max_time) * 60)
    if args.min_stiffness:
        limits["stiffness"] = (">=", float(args.min_stiffness))
    t0 = _time.monotonic()
    pts = []
    for n, a in enumerate(assignments, 1):
        changes = search.changes_for(pr, args.plate, a)
        data = pr.build([Change(c["key"], c["value"], c["object_id"]) for c in changes]) if changes else pr.path.read_bytes()
        res = bambu.slice_bytes(data, args.plate)
        an = layers.load_or_build(res.folder, args.plate)
        pts.append({**metrics.run_metrics(res, an), "label": search.label_for(a)})
        print(f"  {n}/{len(assignments)} {search.label_for(a)}: {fmt.dur(res.total_s)}, {res.grams:.2f} g"
              f"{' (cached)' if res.cached else ''}", flush=True)
    goals = [g for g, w in weights.items() if w > 0]
    front = set(search.pareto(pts, goals))
    best = search.pick(pts, weights, limits)
    print(f"\n{len(pts)} candidates in {_time.monotonic() - t0:.0f} s. Pareto front over {', '.join(goals)}:")
    rows = [["", "Settings", "Time", "Weight", "Stiffness", "At flow cap"]]
    for i in sorted(range(len(pts)), key=lambda i: pts[i]["time_s"]):
        if i not in front and not args.all:
            continue
        p = pts[i]
        mark = "pick" if i == best else ("front" if i in front else "")
        stiff = f"{p['stiffness']:.1f}%" if p.get("stiffness") is not None else "-"
        rows.append([mark, p["label"], fmt.dur(p["time_s"]), fmt.grams(p["grams"]), stiff, fmt.dur(p["near_cap_s"])])
    print(fmt.table(rows, right={2, 3, 4, 5}))
    print("  Stiffness is a beam-theory proxy for flat parts; surface quality is not measured.")
    return 0


def _print_log(pr: Project):
    from .prints import PrintLog
    from .server import project_dir
    return PrintLog(project_dir(pr.path))


def cmd_log(args) -> int:
    from . import layers
    pr = Project(args.project)
    res = bambu.slice_file(pr.path, args.plate)
    an = layers.load_or_build(res.folder, args.plate)
    item = _print_log(pr).add({
        "plate": args.plate, "label": "Current settings", "sha": res.sha256,
        "printer": pr.print_settings.get("printer_settings_id"), "filament": an["filament"].get("name"),
        "predicted_s": res.total_s, "predicted_g": res.grams, "actual_s": args.time, "actual_g": args.weight,
        "outcome": args.outcome, "issues": args.issue or [], "note": args.note or "",
        "kind": "test" if args.test else "part", "date": args.date,
    })
    diff = ""
    if item["actual_s"]:
        diff = f", {100 * (item['actual_s'] / item['predicted_s'] - 1):+.1f}% vs Bambu's {fmt.dur(item['predicted_s'])}"
    print(f"Logged plate {args.plate}: {fmt.dur(item['actual_s']) if item['actual_s'] else 'no time'}{diff}, {item['outcome']}.")
    return 0


def cmd_prints(args) -> int:
    pr = Project(args.project)
    log = _print_log(pr)
    if not log.items:
        print("No prints logged yet. After a print: slicer-opt log PROJECT --plate N --time 2h10m --weight 129")
        return 0
    rows = [["Date", "Plate", "What", "Bambu", "Actual", "Diff", "Outcome", "Issues"]]
    for p in sorted(log.items, key=lambda p: p["created"]):
        diff = f"{100 * (p['actual_s'] / p['predicted_s'] - 1):+.1f}%" if p.get("actual_s") and p.get("predicted_s") else ""
        rows.append([p["date"], str(p["plate"]), ("test: " if p["kind"] == "test" else "") + p["label"][:40],
                     fmt.dur(p["predicted_s"]) if p.get("predicted_s") else "", fmt.dur(p["actual_s"]) if p.get("actual_s") else "",
                     diff, p["outcome"], ", ".join(p["issues"])])
    print(fmt.table(rows, right={3, 4, 5}))
    for c in log.calibration():
        t = c["time"]
        if t:
            print(f"\n  {c['printer']} with {c['filament']}: prints take {100 * (t['ratio'] - 1):+.1f}% vs Bambu's estimate "
                  f"({t['n']} print{'s' if t['n'] != 1 else ''}, {100 * (t['lo'] - 1):+.1f}% to {100 * (t['hi'] - 1):+.1f}%)")
    return 0


def cmd_serve(args) -> int:
    from .server import serve
    serve(Path(args.project) if args.project else None, port=args.port)
    return 0


def cmd_open(args) -> int:
    from .launcher import LaunchError, alert, open_projects
    try:
        pages = open_projects(args.files, port=args.port, browser=not args.no_browser)
    except LaunchError as e:
        if args.alert:
            alert(str(e))
        print(f"slicer-opt: {e}", file=sys.stderr)
        return 2
    for page in pages:
        print(page)
    return 0


# ---- cache ---------------------------------------------------------------------

def cmd_cache(args) -> int:
    if args.clear:
        bambu.clear_cache()
        print("Cache cleared.")
        return 0
    n, size = bambu.cache_size()
    print(f"{n} cached slices, {size / 1e6:.1f} MB in {bambu.CACHE / 'slices'}")
    return 0


# ---- main ----------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="slicer-opt", description="Measure Bambu Studio settings with the real slicer.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("info", help="plates, objects, overrides and changed presets")
    p.add_argument("project")
    p.add_argument("--key", action="append", help="also show the value of this setting for every object")
    p.set_defaults(func=cmd_info)

    p = sub.add_parser("check", help="check the project's rules")
    p.add_argument("project")
    p.add_argument("--rules", help="rules JSON (default: <project>.rules.json or slicer-rules.json next to it)")
    p.add_argument("--slice", action="store_true", help="also slice and check forbidden features")
    p.add_argument("--plate", help="plate number(s), comma-separated, or 'all' (default)")
    p.add_argument("--no-cache", action="store_true")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("slice", help="time, weight and time by feature for each plate")
    p.add_argument("project")
    p.add_argument("--plate", help="plate number(s), comma-separated, or 'all' (default)")
    p.add_argument("--features", type=int, default=5, help="how many features to list (default 5)")
    p.add_argument("--repeat", type=int, default=1, help="slice N times to measure repeat noise (default 1)")
    p.add_argument("--no-cache", action="store_true")
    p.set_defaults(func=cmd_slice)

    p = sub.add_parser("try", help="slice a variant with changed settings and compare")
    p.add_argument("project")
    p.add_argument("--plate", type=int, required=True)
    p.add_argument("--set", action="append", metavar="KEY=VALUE", help="setting to change (repeatable)")
    p.add_argument("--remove", action="append", metavar="KEY", help="per-object override to remove (repeatable)")
    p.add_argument("--object", action="append", metavar="ID_OR_NAME",
                   help="only these objects (default: every object on the plate)")
    p.add_argument("--print-wide", action="store_true", help="change the print-wide value (affects every plate)")
    p.add_argument("--each", action="store_true", help="also try each setting on its own")
    p.add_argument("--save", metavar="NEW.3mf", help="write the variant (never the source file)")
    p.add_argument("--overwrite", action="store_true", help="allow --save to replace an existing file")
    p.add_argument("--allow-unknown", action="store_true", help="allow keys this project doesn't have")
    p.add_argument("--no-cache", action="store_true")
    p.set_defaults(func=cmd_try)

    p = sub.add_parser("search", help="slice combinations of settings and show the Pareto front")
    p.add_argument("project")
    p.add_argument("--plate", type=int, required=True)
    p.add_argument("--knob", action="append", required=True, metavar="KEY=V1,V2,...", help="setting and values to try (repeatable)")
    p.add_argument("--each", action="store_true", help="each value on its own instead of every combination")
    p.add_argument("--weights", help="importance per goal, e.g. time_s=5,grams=3,stiffness=3 (default)")
    p.add_argument("--max-time", type=float, metavar="MINUTES", help="only consider candidates up to this long")
    p.add_argument("--min-stiffness", type=float, metavar="PERCENT", help="only consider candidates at least this stiff")
    p.add_argument("--all", action="store_true", help="list every candidate, not just the front")
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("analyze", help="time model check, slowest layers and flow headroom per plate")
    p.add_argument("project")
    p.add_argument("--plate", help="plate number(s), comma-separated, or 'all' (default)")
    p.add_argument("--no-cache", action="store_true")
    p.set_defaults(func=cmd_analyze)

    p = sub.add_parser("serve", help="run the local app in this terminal (previews, comparisons, designer)")
    p.add_argument("project", nargs="?", help="also open this project")
    p.add_argument("--port", type=int, default=8765)
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("open", help="start the app in the background if needed and open projects in the browser")
    p.add_argument("files", nargs="*", metavar="PROJECT.3mf")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--no-browser", action="store_true", help="print the pages instead of opening them")
    p.add_argument("--alert", action="store_true", help="report problems in a macOS alert (the launcher app uses this)")
    p.set_defaults(func=cmd_open)

    p = sub.add_parser("log", help="record a finished print of the current file (time from the printer, weight from a scale)")
    p.add_argument("project")
    p.add_argument("--plate", type=int, required=True)
    p.add_argument("--time", help="actual print time: 2h10m, 2:10 or seconds")
    p.add_argument("--weight", type=float, help="actual weight in grams, part only")
    p.add_argument("--outcome", choices=["ok", "partial", "failed"], default="ok")
    p.add_argument("--issue", action="append", help="adhesion, warping, stringing, fit too tight, … (repeatable)")
    p.add_argument("--note")
    p.add_argument("--test", action="store_true", help="a test piece, not a real part")
    p.add_argument("--date", help="YYYY-MM-DD (default today)")
    p.set_defaults(func=cmd_log)

    p = sub.add_parser("prints", help="logged prints and how Bambu's estimates compare")
    p.add_argument("project")
    p.set_defaults(func=cmd_prints)

    p = sub.add_parser("cache", help="show or clear the slice cache")
    p.add_argument("--clear", action="store_true")
    p.set_defaults(func=cmd_cache)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (ProjectError, SliceError, ValueError, FileNotFoundError) as e:
        print(f"slicer-opt: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
