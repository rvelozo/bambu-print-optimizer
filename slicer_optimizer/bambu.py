"""Slice with the Bambu Studio command line and read what it reports.

Each slice runs in its own temporary folder (the CLI writes result.json into
its working directory) on a private copy of the project, so the user's file is
never handed to the CLI. Results are cached by the exact bytes of the project,
the plate number and the Bambu Studio version.

Repeat slices of the same bytes are not always identical: on the v7 panel the
time estimate varied by 13.8 s over four runs (grams did not), while the lid and
rails were identical every time. So every real slice of the same bytes is kept
as a sample, and a result carries the spread of its samples.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import paths

APP = paths.studio_app()  # macOS app bundle; other systems set BAMBU_STUDIO_CLI (see paths.py)
CACHE = paths.cache_dir()


class SliceError(RuntimeError):
    pass


def cli_path() -> Path:
    return paths.studio_cli()


def studio_version() -> str:
    v = paths.studio_version()
    if v is None:
        raise SliceError(f"Bambu Studio not found at {cli_path()}: {paths.setup_hint()}")
    return v


@dataclass
class SliceResult:
    plate: int
    total_s: float  # what Bambu Studio shows as total time, including heating and calibration
    model_s: float  # printing the model only
    grams: float
    feature_s: dict[str, float]
    objects: list[dict]
    sha256: str
    wall_s: float  # how long slicing took
    cached: bool = False
    warning: str = ""
    folder: Path | None = field(default=None, repr=False)
    samples: int = 1
    noise_s: float | None = None  # max - min of total_s over samples; None with one sample
    filament_g: dict[int, float] = field(default_factory=dict)  # grams per filament slot (1-based)

    @property
    def support_s(self) -> float:
        return sum(v for k, v in self.feature_s.items() if k.startswith("Support"))

    @property
    def gcode_path(self) -> Path | None:
        if self.folder is None:
            return None
        p = self.folder / f"plate_{self.plate}.gcode.gz"
        return p if p.exists() else None

    @property
    def tolerance_s(self) -> float:
        """Time differences up to this are indistinguishable from repeat-slice noise."""
        if not self.noise_s:
            return 0.5
        return max(3 * self.noise_s, 0.002 * self.total_s)

    def same_as(self, other: "SliceResult", tol_s: float | None = None, tol_g: float = 0.005) -> bool:
        tol = max(self.tolerance_s, other.tolerance_s) if tol_s is None else tol_s
        if abs(self.total_s - other.total_s) > tol or abs(self.grams - other.grams) > tol_g:
            return False
        keys = set(self.feature_s) | set(other.feature_s)
        return all(abs(self.feature_s.get(k, 0) - other.feature_s.get(k, 0)) <= tol for k in keys)


def _plate_entry(result: dict, plate: int) -> dict:
    entry = next((p for p in result.get("sliced_plates", []) if p.get("id") == plate), None)
    if entry is None:
        raise SliceError(f"result.json has no plate {plate}: {result.get('error_string')}")
    return entry


def _sample(result: dict, plate: int, wall_s: float) -> dict:
    entry = _plate_entry(result, plate)
    return {"total_s": float(entry["total_predication"]),
            "grams": float(sum(f.get("total_used_g", 0.0) for f in entry.get("filaments", []))),
            "feature_s": {k: float(v) for k, v in entry.get("feature_type_times", {}).items()},
            "wall_s": round(wall_s, 2)}


def _from_result(result: dict, plate: int, sha: str, wall_s: float, cached: bool, folder: Path,
                 samples: list[dict]) -> SliceResult:
    entry = _plate_entry(result, plate)
    totals = [x["total_s"] for x in samples]
    return SliceResult(
        plate=plate,
        total_s=float(entry["total_predication"]),
        model_s=float(entry["main_predication"]),
        grams=float(sum(f.get("total_used_g", 0.0) for f in entry.get("filaments", []))),
        feature_s={k: float(v) for k, v in entry.get("feature_type_times", {}).items()},
        objects=[{"name": o.get("name"), "bbox": o.get("bbox")} for o in entry.get("objects", [])],
        sha256=sha,
        wall_s=wall_s,
        cached=cached,
        warning=entry.get("warning_message", "") or "",
        folder=folder,
        samples=len(samples),
        noise_s=(max(totals) - min(totals)) if len(totals) > 1 else None,
        filament_g={int(f["id"]): float(f.get("total_used_g", 0.0)) for f in entry.get("filaments", []) if "id" in f},
    )


_running: set[subprocess.Popen] = set()


def stop_all() -> int:
    """Stop slices in progress (the app is quitting). Returns how many were stopped."""
    procs = list(_running)
    for p in procs:
        p.terminate()
    for p in procs:
        try:
            p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            p.kill()
    return len(procs)


def _run_cli(data: bytes, plate: int, timeout: float) -> tuple[Path, dict, str, float]:
    """Slice in a fresh folder. Returns (folder, result.json, log, seconds)."""
    (CACHE / "slices").mkdir(parents=True, exist_ok=True)
    if not cli_path().exists():
        raise SliceError(f"Bambu Studio not found at {cli_path()}: {paths.setup_hint()}")
    work = Path(tempfile.mkdtemp(prefix="work-", dir=CACHE / "slices"))
    try:
        src = work / "input.3mf"
        src.write_bytes(data)
        t0 = time.monotonic()
        popen = subprocess.Popen(
            [str(cli_path()), "--slice", str(plate), "--outputdir", str(work), str(src)],
            cwd=work, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        _running.add(popen)
        try:
            out, err = popen.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as e:
            popen.kill()
            popen.communicate()
            raise SliceError(f"slicing plate {plate} took longer than {timeout:.0f} s") from e
        finally:
            _running.discard(popen)
        proc = subprocess.CompletedProcess(popen.args, popen.returncode, out, err)
        if proc.returncode < 0 and not (work / "result.json").exists():
            raise SliceError(f"slicing plate {plate} was stopped")
        wall = time.monotonic() - t0
        rpath = work / "result.json"
        if not rpath.exists():
            raise SliceError(f"Bambu Studio wrote no result (exit {proc.returncode}): {_tail(proc)}")
        result = json.loads(rpath.read_text())
        if result.get("return_code", proc.returncode) != 0:
            raise SliceError(f"Bambu Studio failed on plate {plate}: {result.get('error_string')} {_tail(proc)}")
        _plate_entry(result, plate)
        src.unlink()
        return work, result, (proc.stdout or "") + (proc.stderr or ""), wall
    except BaseException:
        shutil.rmtree(work, ignore_errors=True)
        raise


def slice_bytes(data: bytes, plate: int, *, use_cache: bool = True, repeats: int = 1,
                timeout: float = 1800) -> SliceResult:
    """Slice (or reuse) these exact bytes, making sure at least `repeats` real slices exist.

    use_cache=False forces one more real slice; earlier samples of the same bytes are kept.
    """
    sha = hashlib.sha256(data).hexdigest()
    version = studio_version()
    folder = CACHE / "slices" / f"{sha[:24]}-p{plate}-{version}"
    meta_path = folder / "meta.json"
    samples: list[dict] = []
    if folder.exists() and meta_path.exists():
        samples = json.loads(meta_path.read_text()).get("samples", [])
    cached = bool(samples) and use_cache and (folder / "result.json").exists()

    if not cached:
        work, result, log, wall = _run_cli(data, plate, timeout)
        gcode = work / f"plate_{plate}.gcode"
        if gcode.exists():
            with open(gcode, "rb") as fi, gzip.open(work / f"plate_{plate}.gcode.gz", "wb", compresslevel=6) as fo:
                shutil.copyfileobj(fi, fo)
            gcode.unlink()
        (work / "cli.log").write_text(log)
        samples.append(_sample(result, plate, wall))
        if folder.exists():
            shutil.rmtree(folder)
        os.replace(work, folder)
        first_wall = wall
    else:
        first_wall = samples[0]["wall_s"]

    while len(samples) < repeats:
        work, result_n, _, wall = _run_cli(data, plate, timeout)
        shutil.rmtree(work, ignore_errors=True)
        samples.append(_sample(result_n, plate, wall))

    meta_path.write_text(json.dumps({"sha256": sha, "plate": plate, "version": version,
                                     "created": time.time(), "samples": samples}))
    result = json.loads((folder / "result.json").read_text())
    return _from_result(result, plate, sha, round(first_wall, 2), cached, folder, samples)


def slice_file(path: str | os.PathLike, plate: int, **kw) -> SliceResult:
    return slice_bytes(Path(path).read_bytes(), plate, **kw)


def cleanup_work_dirs(max_age_s: float = 3600) -> int:
    """Remove slice folders left by a process killed mid-slice (only old ones: another process may be slicing)."""
    root = CACHE / "slices"
    if not root.exists():
        return 0
    n = 0
    for d in root.glob("work-*"):
        if d.is_dir() and time.time() - d.stat().st_mtime > max_age_s:
            shutil.rmtree(d, ignore_errors=True)
            n += 1
    return n


def cache_size() -> tuple[int, int]:
    root = CACHE / "slices"
    if not root.exists():
        return 0, 0
    n = sum(1 for d in root.iterdir() if d.is_dir() and not d.name.startswith("work-"))
    size = sum(f.stat().st_size for f in root.rglob("*") if f.is_file())
    return n, size


def clear_cache() -> None:
    shutil.rmtree(CACHE / "slices", ignore_errors=True)


def _tail(proc: subprocess.CompletedProcess, n: int = 400) -> str:
    text = (proc.stdout or "") + (proc.stderr or "")
    lines = [l for l in text.splitlines() if "Invalid T command (T65535)" not in l]
    return "\n".join(lines)[-n:]
