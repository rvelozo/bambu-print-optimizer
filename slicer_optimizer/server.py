"""Local web app: open Bambu Studio projects, slice changes, compare runs.

    slicer-opt serve [PROJECT.3mf] [--port 8765]
    slicer-opt open [PROJECT.3mf ...]    # start the app if needed, open projects in the browser

The home page (/) lists recent projects and opens new ones: the macOS Open
dialog, a pasted path, or a file dropped on the page (dropped files are copied
into the app's data folder, because a browser doesn't reveal where a file came
from). Each project has its own page at /p/<id>/ and its API under /api/p/<id>/.

One worker thread slices for every open project, one slice at a time, so the
pages stay responsive and slices never compete for the CPU. Runs are stored per
project file in the app data folder, so they survive restarts; the numbers
themselves come from the slice cache (bambu.py). A project is read once into
memory; when the file changes on disk the page offers to reload it, and runs of
the earlier version are kept but hidden.
"""

from __future__ import annotations

import hashlib
import io
import json
import mimetypes
import os
import queue
import re
import subprocess
import tempfile
import threading
import time
import traceback
import uuid
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from . import __version__, bambu, layers, metrics, paths
from .prints import ISSUES, OUTCOMES, PrintLog
from .project3mf import Change, Project, ProjectError
from .trial import PlannedChange, Trial, save_variant

METRICS_VERSION = 6  # bump to recompute stored run metrics (from the cache) on startup
DATA = paths.data_dir()
WEB = Path(__file__).parent / "web"
MAX_UPLOAD = 1 << 30
PENDING = ("queued", "slicing", "analysing")


class NotFound(ProjectError):
    pass


def project_id(path: Path) -> str:
    return hashlib.sha1(str(Path(path).resolve()).encode()).hexdigest()[:16]


def project_dir(project_path: Path) -> Path:
    return DATA / "projects" / project_id(project_path)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class Runs:
    """Persisted runs of one project file. A run is one slice of one plate of one version of the file."""

    def __init__(self, folder: Path):
        self.dir = folder
        self.dir.mkdir(parents=True, exist_ok=True)
        self.file = self.dir / "runs.json"
        self.lock = threading.RLock()
        self.runs: dict[str, dict] = {}
        self.source_sha: str | None = None  # the version that's loaded (App.load sets it)
        if self.file.exists():
            for r in json.loads(self.file.read_text()).get("runs", []):
                if r.get("status") not in ("done", "error", "cancelled"):
                    r["status"] = "queued"
                self.runs[r["id"]] = r

    def save(self) -> None:
        with self.lock:
            tmp = self.file.with_suffix(".tmp")
            tmp.write_text(json.dumps({"runs": list(self.runs.values())}, indent=1))
            tmp.replace(self.file)

    def current(self) -> list[dict]:
        """Runs of the loaded version of the file, oldest first."""
        with self.lock:
            return sorted((r for r in self.runs.values() if r["source_sha"] == self.source_sha),
                          key=lambda r: r["created"])

    def older(self) -> int:
        with self.lock:
            return sum(1 for r in self.runs.values() if r["source_sha"] != self.source_sha)

    def find(self, plate: int, changes: list[dict]) -> dict | None:
        key = _changes_key(changes)
        with self.lock:
            for r in self.runs.values():
                if r["plate"] == plate and _changes_key(r["changes"]) == key and r["source_sha"] == self.source_sha:
                    return r
        return None

    def add(self, plate: int, changes: list[dict], label: str | None, kind: str) -> dict:
        with self.lock:
            existing = self.find(plate, changes)
            if existing:
                return existing
            run = {"id": uuid.uuid4().hex[:10], "plate": plate, "changes": changes, "kind": kind,
                   "label": label or _label(changes), "status": "queued", "created": time.time(),
                   "source_sha": self.source_sha}
            self.runs[run["id"]] = run
            self.save()
        return run

    def update(self, run_id: str, **fields) -> None:
        with self.lock:
            self.runs[run_id].update(fields)
            self.save()


def _changes_key(changes: list[dict]) -> str:
    return json.dumps(sorted((c["key"], c.get("value"), c.get("object_id")) for c in changes))


def _label(changes: list[dict]) -> str:
    if not changes:
        return "Current settings"
    parts = []
    for c in changes:
        scope = " (print-wide)" if c.get("object_id") is None else ""
        parts.append(f"{c['key']}={c['value']}{scope}" if c.get("value") is not None else f"remove {c['key']}")
    return ", ".join(dict.fromkeys(parts))


class Worker(threading.Thread):
    """Slices one run at a time for every open project. Priority 0 (your trials) goes before
    1 (search candidates), and both before 2 (runs recomputed after an update)."""

    def __init__(self, hub: "Hub"):
        super().__init__(daemon=True)
        self.hub = hub
        self.q: queue.PriorityQueue[tuple[int, int, str, str]] = queue.PriorityQueue()
        self.seq = 0
        self.lock = threading.Lock()
        self.current: tuple[str, str] | None = None  # (project id, run id)

    def put(self, project: str, run_id: str, priority: int) -> None:
        with self.lock:
            self.seq += 1
            self.q.put((priority, self.seq, project, run_id))

    def run(self) -> None:
        while True:
            _, _, pid, run_id = self.q.get()
            app = self.hub.apps.get(pid)
            run = app.runs.runs.get(run_id) if app else None
            if run is None or run.get("status") != "queued":
                continue  # removed, cancelled or already done
            self.current = (pid, run_id)
            try:
                app.process(run_id)
            except Exception as e:  # keep the worker alive; report on the run
                if self.hub.stopping:
                    return  # quitting stopped this slice; it stays pending and runs next time
                app.runs.update(run_id, status="error", error=f"{type(e).__name__}: {e}",
                                trace=traceback.format_exc()[-2000:])
            finally:
                self.current = None


class App:
    """One project file: what's loaded from it, its runs, searches and print log."""

    def __init__(self, hub: "Hub", path: Path):
        self.hub = hub
        self.path = path
        self.id = project_id(path)
        self.dir = DATA / "projects" / self.id
        self.lock = threading.RLock()
        self.runs = Runs(self.dir)
        self.prints = PrintLog(self.dir)
        self.extensions: dict[str, callable] = {}  # the designer registers its API routes here
        self.sections: dict[tuple, dict] = {}       # recent cross-sections
        self.project: Project | None = None
        self.stat: tuple[int, int] | None = None
        self._disk: tuple[tuple[int, int], str] | None = None  # (stat, sha) of the file as last checked
        from . import designer, optimize
        designer.register(self)
        optimize.register(self)
        # Hub._create calls load() once the hub knows this project, so the worker can find
        # the runs load() queues.

    def load(self) -> None:
        """Read the file (again) and queue what this version still needs."""
        try:
            st = self.path.stat()  # before reading: a write after this shows up as a change
            data = self.path.read_bytes()
        except FileNotFoundError as e:
            raise ProjectError(f"{self.path} was moved or deleted") from e
        project = Project(self.path, data=data)
        sha = _sha(data)
        with self.lock:
            self.project = project
            self.stat = (st.st_mtime_ns, st.st_size)
            self._disk = (self.stat, sha)
            self.sections.clear()
            with self.runs.lock:
                self.runs.source_sha = sha
                for r in self.runs.runs.values():
                    if r["source_sha"] != sha and r["status"] == "queued":
                        r["status"] = "cancelled"
                        r["error"] = "the file changed before this was sliced"
                self.runs.save()
        for plate in project.plates:
            if plate.object_ids:  # an empty plate has nothing to slice
                self.runs.add(plate.index, [], "Current settings", "baseline")
        for r in self.runs.current():
            stale = r["status"] == "done" and (not self._folder_ok(r) or r.get("metrics", {}).get("v") != METRICS_VERSION)
            if r["status"] == "queued":
                self.enqueue(r["id"], 1 if r.get("kind") == "search" else 0)
            elif stale:  # recomputed from the cache after an update; your new trials go first
                self.enqueue(r["id"], 2)

    def disk_state(self) -> str:
        """'same', 'changed' (the file on disk differs from what's loaded) or 'missing'."""
        try:
            st = self.path.stat()
        except OSError:
            return "missing"
        key = (st.st_mtime_ns, st.st_size)
        if key == self.stat:
            return "same"
        if self._disk is None or self._disk[0] != key:  # read it once per new mtime/size, not on every poll
            try:
                self._disk = (key, _sha(self.path.read_bytes()))
            except OSError:
                return "missing"
        return "same" if self._disk[1] == self.runs.source_sha else "changed"

    def _folder_ok(self, run: dict) -> bool:
        folder = run.get("folder")
        return bool(folder) and (Path(folder) / "result.json").exists()

    def enqueue(self, run_id: str, priority: int = 0) -> None:
        self.runs.update(run_id, status="queued")
        self.hub.worker.put(self.id, run_id, priority)

    def changes_of(self, run: dict) -> list[Change]:
        return [Change(c["key"], c.get("value"), c.get("object_id")) for c in run["changes"]]

    def process(self, run_id: str) -> None:
        with self.lock:
            project, sha = self.project, self.runs.source_sha
        run = self.runs.runs[run_id]
        if run["source_sha"] != sha:
            self.runs.update(run_id, status="cancelled", error="the file changed before this was sliced")
            return
        data = project.build(self.changes_of(run)) if run["changes"] else project.source_bytes()
        self.runs.update(run_id, status="slicing", started=time.time(), error=None, trace=None)
        res = bambu.slice_bytes(data, run["plate"], repeats=2 if run["kind"] == "baseline" else 1)
        self.runs.update(run_id, status="analysing")
        t0 = time.monotonic()
        analysis = layers.load_or_build(res.folder, run["plate"])
        analysis_s = time.monotonic() - t0
        self.runs.update(run_id, status="done", folder=str(res.folder), sha=res.sha256, metrics={
            "v": METRICS_VERSION,
            "total_s": res.total_s, "model_s": res.model_s, "grams": res.grams,
            "support_s": round(res.support_s, 2), "noise_s": res.noise_s, "samples": res.samples,
            "tolerance_s": round(res.tolerance_s, 2), "wall_s": res.wall_s, "analysis_s": round(analysis_s, 2),
            "feature_s": {k: round(v, 2) for k, v in res.feature_s.items()},
            "near_cap_s": analysis["flow"]["near_cap_s"] if analysis["flow"]["cap"] else None,
            "flow_cap": analysis["flow"]["cap"],
            "cost": None if (c := metrics.filament_cost(res, analysis["filament"])) is None else round(c, 3),
            "filament": analysis["filament"].get("name"),
        }, goals=metrics.run_metrics(res, analysis))

    # ---- API data ------------------------------------------------------------------

    def project_info(self) -> dict:
        pr = self.project
        meta = self.hub.meta(self.id)
        plates = []
        for p in pr.plates:
            objs = []
            for o in pr.objects_on_plate(p.index):
                objs.append({"id": o.id, "name": o.name, "copies": p.object_ids.count(o.id),
                             "overrides": o.overrides, "filament": pr.filament_for(o),
                             "parts": [{"id": x.id, "name": x.name, "overrides": x.overrides} for x in o.parts if x.overrides]})
            plates.append({"index": p.index, "name": p.name, "objects": objs, "settings": p.settings,
                           "thumbnail": _thumbnail_name(pr.source_bytes(), p.index) is not None})
        uploaded = bool(meta.get("uploaded"))
        return {"id": self.id, "name": meta.get("name") or pr.path.name, "path": str(pr.path),
                "uploaded": uploaded, "save_dir": str(Path.home() / "Downloads") if uploaded else str(pr.path.parent),
                "studio_version": pr.studio_version, "bambu_studio": bambu.studio_version(), "dialogs": paths.MAC,
                "printer": pr.print_settings.get("printer_settings_id"),
                "process": pr.print_settings.get("print_settings_id"), "plates": plates,
                "modified": pr.modified_vs_system(), "disk": self.disk_state()}

    def runs_view(self) -> dict:
        runs = [self.run_view(r) for r in self.runs.current()]
        current = self.hub.worker.current
        return {"runs": runs, "older": self.runs.older(), "waiting": self.hub.waiting(),
                "active": current[1] if current and current[0] == self.id else None, "disk": self.disk_state()}

    def keys(self, q: str, plate: int | None) -> list[dict]:
        pr = self.project
        q = q.lower().strip()
        objs = pr.objects_on_plate(plate) if plate else []
        out = []
        for k in sorted(pr.print_settings):
            if q and q not in k.lower():
                continue
            v = pr.print_settings[k]
            if isinstance(v, list) and len(v) > 8:
                continue
            item = {"key": k, "print_wide": v, "group": pr.presets.group(k),
                    "default": pr.presets.process_default(k)}
            if objs:
                item["objects"] = {o.id: pr.effective(k, o) for o in objs}
            out.append(item)
            if len(out) >= 60:
                break
        return out

    def run_view(self, run: dict) -> dict:
        view = {k: v for k, v in run.items() if k not in ("trace",)}
        if self.hub.worker.current == (self.id, run["id"]):
            view["active"] = True
        return view

    def suggested_name(self, run: dict) -> str:
        name = self.hub.meta(self.id).get("name") or self.path.name
        stem = re.sub(r"\.3mf$", "", name, flags=re.I)
        safe = re.sub(r"[^\w.=,+-]+", " ", run["label"]).strip()[:60]
        return f"{stem} - {safe}.3mf"

    def save_run(self, run: dict, dest: str, overwrite: bool) -> dict:
        if not run["changes"]:
            raise ProjectError("this run has no changes to save")
        if run["source_sha"] != self.runs.source_sha:
            raise ProjectError("this run is from an earlier version of the file")
        dest_path = Path(os.path.expanduser(dest))
        if dest_path.suffix.lower() != ".3mf":
            dest_path = dest_path.with_name(dest_path.name + ".3mf")
        project = self.project
        data = project.build(self.changes_of(run))
        res = bambu.slice_bytes(data, run["plate"])  # from cache: same bytes, same numbers
        base = self.baseline(run["plate"])
        base_res = bambu.slice_bytes(project.source_bytes(), run["plate"])
        planned = [PlannedChange(c, "print-wide" if c.object_id is None else f"object {c.object_id}", "", False)
                   for c in self.changes_of(run)]
        trial = Trial(run["plate"], planned, base_res, res, data)
        path, prov = save_variant(project, trial, dest_path, overwrite=overwrite)
        return {"saved": str(path), "provenance": str(prov), "baseline_run": base["id"] if base else None}

    def baseline(self, plate: int) -> dict | None:
        return self.runs.find(plate, [])

    def thumbnail(self, plate: int, small: bool) -> bytes | None:
        data = self.project.source_bytes()
        name = _thumbnail_name(data, plate, small)
        if name is None:
            return None
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            return z.read(name)

    def log_print(self, body: dict) -> dict:
        run = self.runs.runs.get(body.get("run_id") or "")
        if run is None:
            raise ProjectError("pick the run you printed")
        if run.get("status") != "done":
            raise ProjectError("that run hasn't been sliced yet")
        m = run["metrics"]
        return self.prints.add({**body, "plate": run["plate"], "label": run["label"], "sha": run.get("sha"),
                                "printer": self.project.print_settings.get("printer_settings_id"),
                                "filament": m.get("filament"), "predicted_s": m["total_s"], "predicted_g": m["grams"]})

    def prints_view(self) -> dict:
        return {"prints": sorted(self.prints.items, key=lambda p: -p["created"]),
                "calibration": self.prints.calibration(), "issues": ISSUES, "outcomes": OUTCOMES,
                "printer": self.project.print_settings.get("printer_settings_id")}


def _thumbnail_name(data_or_path, plate: int, small: bool = False) -> str | None:
    """The plate picture Bambu Studio saved in the project, if any."""
    src = io.BytesIO(data_or_path) if isinstance(data_or_path, bytes) else data_or_path
    try:
        with zipfile.ZipFile(src) as z:
            names = set(z.namelist())
    except (OSError, zipfile.BadZipFile):
        return None
    order = [f"Metadata/plate_{plate}_small.png", f"Metadata/plate_{plate}.png"]
    for name in (order if small else order[::-1]):
        if name in names:
            return name
    return None


# ---- native dialogs ------------------------------------------------------------------

def _osascript(lines: list[str], args: list[str]) -> str | None:
    """Run an AppleScript dialog. Returns its result, or None if the user cancelled."""
    if not paths.MAC:
        raise ProjectError("file dialogs need macOS; paste the file's path or drop the file instead")
    cmd = ["osascript"]
    for line in lines:
        cmd += ["-e", line]
    try:
        proc = subprocess.run(cmd + args, capture_output=True, text=True, timeout=900)
    except FileNotFoundError as e:
        raise ProjectError("the file dialog needs macOS (osascript); paste the file's path instead") from e
    except subprocess.TimeoutExpired as e:
        raise ProjectError("the file dialog was left open too long") from e
    if proc.returncode != 0:
        if "-128" in proc.stderr:  # "User canceled."
            return None
        raise ProjectError(f"the file dialog failed: {proc.stderr.strip()}")
    return proc.stdout.strip() or None


def choose_project(start_dir: Path) -> str | None:
    return _osascript([
        "on run argv",
        "activate",
        'return POSIX path of (choose file with prompt "Open a Bambu Studio project" '
        'of type {"com.bambulab.3mf", "3mf"} default location (POSIX file (item 1 of argv) as alias))',
        "end run",
    ], [str(start_dir)])


def choose_save_path(start_dir: Path, name: str) -> str | None:
    return _osascript([
        "on run argv",
        "activate",
        'return POSIX path of (choose file name with prompt "Save this variant as" '
        "default name (item 2 of argv) default location (POSIX file (item 1 of argv) as alias))",
        "end run",
    ], [str(start_dir), name])


# ---- open projects -------------------------------------------------------------------

def _clean_path(text: str) -> Path:
    """A path as pasted: quotes from 'Copy as Pathname', backslashes from a Terminal drag, ~."""
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "'\"":
        text = text[1:-1]
    path = Path(os.path.expanduser(text))
    if not path.exists() and "\\" in text:
        path = Path(os.path.expanduser(re.sub(r"\\(.)", r"\1", text)))
    return path


class Hub:
    """The projects the app knows about, the ones that are open, and the one worker that slices for all."""

    def __init__(self, start_worker: bool = True):
        self.apps: dict[str, App] = {}
        self.lock = threading.RLock()
        self.dialog = threading.Lock()  # one native dialog at a time
        self.quit = None  # set by make_server()
        self.stopping = False
        self.worker = Worker(self)
        if start_worker:
            self.worker.start()
            threading.Thread(target=self._tick, daemon=True).start()

    def _tick(self) -> None:
        """Advance optimizations (measure, then combine) without anyone watching the page."""
        while not self.stopping:
            for app in list(self.apps.values()):
                try:
                    app.optimizations.tick()
                except Exception:  # a broken optimization must not stop the others
                    traceback.print_exc()
            time.sleep(2)

    # -- project metadata (DATA/projects/<id>/project.json) --

    def meta(self, pid: str) -> dict:
        f = DATA / "projects" / pid / "project.json"
        try:
            return json.loads(f.read_text())
        except (OSError, ValueError):
            return {}

    def _write_meta(self, pid: str, **fields) -> None:
        d = DATA / "projects" / pid
        d.mkdir(parents=True, exist_ok=True)
        meta = {**self.meta(pid), **fields}
        tmp = d / "project.json.tmp"
        tmp.write_text(json.dumps(meta, indent=1))
        tmp.replace(d / "project.json")

    # -- opening --

    def open(self, path, *, name: str | None = None, uploaded: bool = False) -> App:
        path = _clean_path(str(path)) if not isinstance(path, Path) else path
        if not path.exists():
            raise ProjectError(f"no file at {path}")
        if path.is_dir():
            raise ProjectError(f"{path} is a folder; pick a .3mf file")
        if path.suffix.lower() != ".3mf":
            raise ProjectError(f"{path.name} is not a .3mf project")
        path = path.resolve()
        pid = project_id(path)
        with self.lock:
            app = self.apps.get(pid)
            if app is None:
                app = self._create(path)
            elif app.disk_state() == "changed":
                app.load()
            fields = {"path": str(path), "opened": time.time(), "hidden": False}
            if uploaded:
                fields.update(uploaded=True, name=name or path.name)
            self._write_meta(pid, **fields)
        return app

    def get(self, pid: str) -> App:
        if not re.fullmatch(r"[0-9a-f]{16}", pid or ""):
            raise NotFound("no such project")
        app = self.apps.get(pid)
        if app is not None:
            return app
        meta = self.meta(pid)
        if not meta.get("path"):
            raise NotFound("this project isn't in the app's list; open the file again")
        path = Path(meta["path"])
        if not path.exists():
            raise NotFound(f"{path} was moved or deleted; open it from its new place")
        with self.lock:
            app = self.apps.get(pid)
            if app is None:
                app = self._create(path)
        return app

    def _create(self, path: Path) -> App:
        app = App(self, path)
        self.apps[app.id] = app  # before load(): the worker looks runs up through the hub
        try:
            app.load()
        except BaseException:
            self.apps.pop(app.id, None)
            raise
        return app

    def upload(self, name: str, stream, length: int) -> App:
        name = Path(name or "").name.strip()
        if not name.lower().endswith(".3mf"):
            raise ProjectError(f"{name or 'that file'} is not a .3mf project")
        if length <= 0:
            raise ProjectError("the file is empty")
        if length > MAX_UPLOAD:
            raise ProjectError("the file is larger than 1 GB")
        (DATA / "uploads").mkdir(parents=True, exist_ok=True)
        h = hashlib.sha256()
        with tempfile.NamedTemporaryFile(dir=DATA / "uploads", suffix=".part", delete=False) as tmp:
            left = length
            while left > 0:
                chunk = stream.read(min(left, 1 << 20))
                if not chunk:
                    break
                h.update(chunk)
                tmp.write(chunk)
                left -= len(chunk)
        tmp_path = Path(tmp.name)
        if left:
            tmp_path.unlink(missing_ok=True)
            raise ProjectError("the upload stopped before the whole file arrived")
        try:
            Project(tmp_path)  # refuse anything that isn't a Bambu Studio project before keeping it
        except ProjectError as e:
            tmp_path.unlink(missing_ok=True)
            raise ProjectError(str(e).replace(tmp_path.name, name)) from e
        dest = DATA / "uploads" / h.hexdigest()[:12] / re.sub(r"[\x00-\x1f/]", "_", name)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            tmp_path.unlink()  # the same file was dropped before: same project, same runs
        else:
            os.replace(tmp_path, dest)
        return self.open(dest, name=name, uploaded=True)

    def pick(self) -> App | None:
        if not self.dialog.acquire(blocking=False):
            raise ProjectError("an Open dialog is already showing")
        try:
            start = next((Path(p["path"]).parent for p in self.recent() if not p.get("uploaded") and p["exists"]),
                         Path.home() / "Downloads")
            if not start.is_dir():
                start = Path.home()
            chosen = choose_project(start)
        finally:
            self.dialog.release()
        return self.open(Path(chosen)) if chosen else None

    def forget(self, pid: str) -> None:
        """Take a project off the home page. Its runs and print log stay in the data folder."""
        if not (DATA / "projects" / pid / "project.json").exists():
            raise NotFound("no such project")
        self._write_meta(pid, hidden=True)

    # -- views --

    def waiting(self) -> int:
        """Runs waiting or being sliced, over every open project."""
        n = 0
        for app in list(self.apps.values()):
            with app.runs.lock:
                n += sum(1 for r in app.runs.runs.values() if r["status"] in PENDING and r["source_sha"] == app.runs.source_sha)
        return n

    def recent(self) -> list[dict]:
        items = []
        root = DATA / "projects"
        for f in root.glob("*/project.json") if root.exists() else []:
            pid = f.parent.name
            meta = self.meta(pid)
            if not meta.get("path") or meta.get("hidden"):
                continue
            path = Path(meta["path"])
            app = self.apps.get(pid)
            item = {"id": pid, "path": str(path), "name": meta.get("name") or path.name,
                    "folder": str(path.parent), "opened": meta.get("opened") or f.stat().st_mtime,
                    "uploaded": bool(meta.get("uploaded")), "exists": path.exists(), "open": app is not None}
            if app is not None:
                runs = app.runs.current()
                item.update(plates=sum(1 for p in app.project.plates if p.object_ids),
                            done=sum(1 for r in runs if r["status"] == "done"),
                            waiting=sum(1 for r in runs if r["status"] in PENDING),
                            disk=app.disk_state())
            items.append(item)
        return sorted(items, key=lambda i: -i["opened"])

    def resume_pending(self) -> list[str]:
        """Open the projects that still had slices waiting when the app last stopped."""
        opened = []
        root = DATA / "projects"
        for f in root.glob("*/runs.json") if root.exists() else []:
            try:
                runs = json.loads(f.read_text()).get("runs", [])
            except ValueError:
                continue
            if any(r.get("status") in PENDING for r in runs):
                try:
                    opened.append(self.get(f.parent.name).path.name)
                except ProjectError:
                    pass  # the file is gone; its runs stay as they are
        return opened

    def thumbnail(self, pid: str) -> bytes | None:
        """The first plate's picture (512 px), from the open project or the file on disk."""
        app = self.apps.get(pid)
        if app is not None:
            first = next((p.index for p in app.project.plates if p.object_ids), 1)
            return app.thumbnail(first, small=False)
        path = Path(self.meta(pid).get("path") or "")
        if not path.is_file():
            return None
        for plate in range(1, 9):
            name = _thumbnail_name(path, plate, small=False)
            if name:
                with zipfile.ZipFile(path) as z:
                    return z.read(name)
        return None


class Handler(BaseHTTPRequestHandler):
    hub: Hub = None  # set by serve()

    def log_message(self, fmt, *args):  # quiet unless SLICER_OPT_LOG_REQUESTS=1 (for debugging)
        if os.environ.get("SLICER_OPT_LOG_REQUESTS"):
            print(f"{time.strftime('%H:%M:%S')} {self.headers.get('User-Agent', '-')[:60]!r} {fmt % args}", flush=True)

    def _send(self, code: int, body: bytes, ctype: str, cache: str = "no-store") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200) -> None:
        self._send(code, json.dumps(obj).encode(), "application/json")

    def _error(self, code: int, message: str) -> None:
        self._json({"error": message}, code)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def _handle(self, method: str) -> None:
        try:
            self._route(method)
        except NotFound as e:
            self._error(404, str(e))
        except (ProjectError, ValueError, KeyError, FileNotFoundError, bambu.SliceError) as e:
            self._error(400, str(e))
        except Exception as e:
            self._error(500, f"{type(e).__name__}: {e}")

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def do_DELETE(self):
        self._handle("DELETE")

    def _route(self, method: str) -> None:
        hub = self.hub
        url = urlparse(self.path)
        parts = [unquote(p) for p in url.path.strip("/").split("/") if p]
        qs = {k: v[0] for k, v in parse_qs(url.query).items()}

        if method == "GET" and (not parts or parts == ["index.html"]):
            return self._static("home.html")
        if method == "GET" and parts[0] == "p" and len(parts) == 2:
            return self._static("index.html")  # the project page; it reads its id from the URL
        if method == "GET" and parts[0] == "web":
            return self._static("/".join(parts[1:]))
        if method == "GET" and parts == ["favicon.ico"]:
            return self._static("icon.svg")
        if parts[0] != "api":
            return self._error(404, "not found")
        route = parts[1:]

        if route == ["health"] and method == "GET":
            return self._json({"app": "slicer-optimizer", "version": __version__, "pid": os.getpid()})
        if route == ["quit"] and method == "POST":
            self._json({"quitting": True, "stopped_waiting": hub.waiting()})
            if hub.quit:
                threading.Thread(target=hub.quit, daemon=True).start()
            return None
        if route[:1] == ["projects"]:
            return self._projects(method, route[1:])
        if len(route) >= 2 and route[0] == "p":
            return self._project(hub.get(route[1]), method, route[2:], qs)
        return self._error(404, "unknown API route")

    def _projects(self, method: str, route: list[str]) -> None:
        hub = self.hub
        if not route and method == "GET":
            try:
                studio = bambu.studio_version()
            except bambu.SliceError:
                studio = None
            return self._json({"projects": hub.recent(), "waiting": hub.waiting(), "home": str(Path.home()),
                               "bambu_studio": studio, "bambu_cli": str(bambu.cli_path()),
                               "setup_hint": paths.setup_hint(), "dialogs": paths.MAC})
        if not route and method == "POST":
            app = hub.open(str(self._body().get("path") or ""))
            return self._json({"id": app.id, "name": app.path.name})
        if route == ["pick"] and method == "POST":
            app = hub.pick()
            return self._json({"id": app.id, "name": app.path.name} if app else {"cancelled": True})
        if route == ["upload"] and method == "POST":
            name = unquote(self.headers.get("X-Filename") or "")
            app = hub.upload(name, self.rfile, int(self.headers.get("Content-Length") or 0))
            return self._json({"id": app.id, "name": name})
        if len(route) == 2 and route[1] == "thumbnail" and method == "GET":
            png = hub.thumbnail(route[0]) if re.fullmatch(r"[0-9a-f]{16}", route[0]) else None
            return self._send(200, png, "image/png", "max-age=60") if png else self._error(404, "no picture")
        if len(route) == 1 and method == "DELETE":
            hub.forget(route[0])
            return self._json({"projects": hub.recent()})
        return self._error(404, "unknown API route")

    def _project(self, app: App, method: str, route: list[str], qs: dict) -> None:
        if route == ["project"] and method == "GET":
            return self._json(app.project_info())
        if route == ["reload"] and method == "POST":
            app.load()
            return self._json(app.project_info())
        if len(route) == 2 and route[0] == "thumbnail" and method == "GET":
            png = app.thumbnail(int(route[1]), small=qs.get("size") == "small")
            return self._send(200, png, "image/png", "max-age=60") if png else self._error(404, "no picture")
        if route == ["keys"] and method == "GET":
            return self._json(app.keys(qs.get("q", ""), int(qs["plate"]) if qs.get("plate") else None))
        if route == ["runs"] and method == "GET":
            return self._json(app.runs_view())
        if route == ["runs"] and method == "POST":
            body = self._body()
            plate = int(body["plate"])
            if not app.project.plate(plate).object_ids:
                raise ProjectError(f"plate {plate} is empty: nothing to slice")
            changes = []
            for c in body.get("changes", []):
                key = str(c["key"]).strip()
                if not app.project.has_key(key):
                    raise ProjectError(f"unknown setting {key!r}")
                obj = c.get("object_id")
                if obj is not None:
                    app.project.object(str(obj))
                changes.append({"key": key, "value": None if c.get("value") is None else str(c["value"]).strip(),
                                "object_id": None if obj is None else str(obj)})
            if body.get("scope") == "plate" and changes:
                expanded = []
                for c in changes:
                    for o in app.project.objects_on_plate(plate):
                        expanded.append({**c, "object_id": o.id})
                changes = expanded
            run = app.runs.add(plate, changes, body.get("label"), "trial" if changes else "baseline")
            if run["status"] in ("queued", "error", "cancelled"):
                app.enqueue(run["id"])
            return self._json(app.run_view(run))
        if len(route) >= 2 and route[0] == "runs":
            run = app.runs.runs.get(route[1])
            if run is None:
                return self._error(404, "no such run")
            if len(route) == 2 and method == "DELETE":
                with app.runs.lock:
                    app.runs.runs.pop(run["id"], None)
                    app.runs.save()
                return self._json({"deleted": run["id"]})
            if len(route) == 2 and method == "GET":
                return self._json(app.run_view(run))
            if route[2:] == ["retry"] and method == "POST":
                if run["source_sha"] != app.runs.source_sha:
                    raise ProjectError("this run is from an earlier version of the file")
                app.enqueue(run["id"])
                return self._json(app.run_view(run))
            if route[2:] == ["pin"] and method == "POST":
                app.runs.update(run["id"], pinned=bool(self._body().get("pinned", True)))
                return self._json(app.run_view(run))
            if run.get("status") != "done":
                return self._error(409, f"run is {run.get('status')}")
            folder = Path(run["folder"])
            if route[2:] == ["analysis"]:
                return self._json(layers.load_or_build(folder, run["plate"]))
            if len(route) == 4 and route[2] == "layer":
                analysis = layers.load_or_build(folder, run["plate"])
                return self._send(200, layers.layer_blob(folder, run["plate"], analysis, int(route[3])),
                                  "application/octet-stream")
            if route[2:] == ["section"]:
                from . import section
                analysis = layers.load_or_build(folder, run["plate"])
                key = (run["id"], qs.get("axis", "x"), round(float(qs["at"]), 3))
                cached = app.sections.get(key)
                if cached is None:
                    cached = section.cut(folder, run["plate"], analysis, key[1], key[2])
                    app.sections[key] = cached
                    if len(app.sections) > 24:
                        app.sections.pop(next(iter(app.sections)))
                return self._json(cached)
            if route[2:] == ["bundle"]:
                analysis = layers.load_or_build(folder, run["plate"])
                return self._send(200, layers.bundle_blob(folder, run["plate"], analysis), "application/octet-stream")
            if route[2:] == ["save"] and method == "POST":
                body = self._body()
                if body.get("dialog"):
                    return self._save_with_dialog(app, run)
                return self._json(app.save_run(run, body["path"], bool(body.get("overwrite"))))
        if route[:1] == ["prints"]:
            if method == "GET":
                return self._json(app.prints_view())
            if method == "POST":
                app.log_print(self._body())
                return self._json(app.prints_view())
            if method == "DELETE" and len(route) == 2:
                app.prints.remove(route[1])
                return self._json(app.prints_view())
        handler = app.extensions.get(f"{method} {route[0]}" if route else "")
        if handler is not None:
            return handler(self, route, qs)
        return self._error(404, "unknown API route")

    def _save_with_dialog(self, app: App, run: dict) -> None:
        hub = self.hub
        if not hub.dialog.acquire(blocking=False):
            raise ProjectError("a file dialog is already showing")
        try:
            info_dir = Path(app.project_info()["save_dir"])
            chosen = choose_save_path(info_dir if info_dir.is_dir() else Path.home(), app.suggested_name(run))
        finally:
            hub.dialog.release()
        if not chosen:
            return self._json({"cancelled": True})
        # The Save dialog has already asked about replacing an existing file.
        return self._json(app.save_run(run, chosen, overwrite=True))

    def _static(self, rel: str) -> None:
        root = WEB.resolve()
        path = (root / rel).resolve()
        if not str(path).startswith(str(root) + os.sep):
            return self._error(403, "forbidden")
        if not path.is_file():
            return self._error(404, "not found")
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if path.suffix == ".js":
            ctype = "text/javascript"
        self._send(200, path.read_bytes(), ctype)


def make_server(port: int = 8765, host: str = "127.0.0.1", start_worker: bool = True) -> tuple[ThreadingHTTPServer, Hub]:
    hub = Hub(start_worker=start_worker)

    class BoundHandler(Handler):
        pass

    BoundHandler.hub = hub
    httpd = ThreadingHTTPServer((host, port), BoundHandler)
    httpd.daemon_threads = True
    def quit():
        hub.stopping = True
        httpd.shutdown()

    hub.quit = quit
    return httpd, hub


def running_instance() -> dict | None:
    """The app already serving this data folder ({"pid", "port"}), if any."""
    lock = DATA / "server.lock"
    try:
        fd = os.open(lock, os.O_RDONLY)
    except OSError:
        return None
    try:
        if not paths.try_lock(fd, exclusive=False):
            try:
                return json.loads(lock.read_text())
            except (OSError, ValueError):
                return {}
    finally:
        os.close(fd)
    return None  # nobody holds the lock: a stale file from an app that stopped


def _take_lock(port: int):
    """One app per data folder: two would both slice the same queued runs."""
    DATA.mkdir(parents=True, exist_ok=True)
    fd = os.open(DATA / "server.lock", os.O_RDWR | os.O_CREAT, 0o644)
    if not paths.try_lock(fd):
        os.close(fd)
        other = running_instance() or {}
        raise ProjectError(f"the app is already running (port {other.get('port', '?')}, process {other.get('pid', '?')}); "
                           "use slicer-opt open, or press Quit in it first") from None
    os.ftruncate(fd, 0)
    os.lseek(fd, 0, os.SEEK_SET)
    os.write(fd, json.dumps({"pid": os.getpid(), "port": port}).encode())
    return fd  # held open (and locked) until the process exits


def serve(project_path: Path | None = None, port: int = 8765, host: str = "127.0.0.1") -> None:
    lock = _take_lock(port)
    bambu.cleanup_work_dirs()
    try:
        httpd, hub = make_server(port, host)
    except OSError as e:
        if e.errno == 48:  # EADDRINUSE on macOS
            raise ProjectError(f"port {port} is in use: the app may already be running "
                               f"(slicer-opt open), or pick another with --port") from e
        raise
    url = f"http://{host}:{httpd.server_address[1]}"
    resumed = hub.resume_pending()
    if resumed:
        print(f"slicer-optimizer: continuing slices for {', '.join(resumed)}", flush=True)
    if project_path is not None:
        app = hub.open(project_path)
        print(f"slicer-optimizer: {app.path.name} at {url}/p/{app.id}/", flush=True)
    print(f"slicer-optimizer: {url}  (Ctrl-C or the Quit button to stop)", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        hub.stopping = True
        stopped = bambu.stop_all()
        httpd.server_close()
        os.close(lock)
        print("slicer-optimizer: stopped" + (f" (stopped {stopped} slice in progress; it continues next time)"
                                              if stopped else ""), flush=True)
