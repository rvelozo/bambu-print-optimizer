"""Opening projects in the app: paths, uploads, the recent list, and files that change on disk.

No slicing here: the hub runs without its worker, so runs stay queued.
"""

import importlib
import io
import json
import os
import tempfile
import time
import unittest
import zipfile
from pathlib import Path

from slicer_optimizer.project3mf import ProjectError

from .fixtures import PRINT_SETTINGS, make_project


def rewrite_setting(path: Path, key: str, value: str) -> None:
    """Save the project again with one print-wide value changed, as Bambu Studio would."""
    with zipfile.ZipFile(path) as z:
        entries = [(i, z.read(i.filename)) for i in z.infolist()]
    with zipfile.ZipFile(path, "w") as z:
        for info, data in entries:
            if info.filename == "Metadata/project_settings.config":
                data = (json.dumps({**PRINT_SETTINGS, key: value}, indent=4) + "\n").encode()
            z.writestr(info, data)


class HubTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.old_data = os.environ.get("SLICER_OPT_DATA")
        os.environ["SLICER_OPT_DATA"] = str(root / "data")
        from slicer_optimizer import server
        self.server = importlib.reload(server)
        self.hub = self.server.Hub(start_worker=False)
        (root / "work").mkdir()
        self.file = make_project(root / "work" / "part one.3mf")

    def tearDown(self):
        if self.old_data is None:
            os.environ.pop("SLICER_OPT_DATA", None)
        else:
            os.environ["SLICER_OPT_DATA"] = self.old_data
        importlib.reload(self.server)
        self.tmp.cleanup()

    def test_open_queues_a_baseline_per_plate(self):
        app = self.hub.open(str(self.file))
        runs = app.runs.current()
        self.assertEqual(sorted(r["plate"] for r in runs), [1, 2])
        self.assertTrue(all(r["kind"] == "baseline" and r["status"] == "queued" for r in runs))
        self.assertEqual(app.disk_state(), "same")
        self.assertIs(self.hub.open(self.file), app)  # opening again is the same project
        self.assertEqual(self.hub.waiting(), 2)

    def test_the_worker_takes_runs_queued_while_opening(self):
        # The baselines are queued while the project is being opened; the worker must find them.
        done = []
        original = self.server.App.process

        def fake_process(app, run_id):  # no slicing: just record it
            done.append(run_id)
            app.runs.update(run_id, status="done")

        self.server.App.process = fake_process
        try:
            hub = self.server.Hub(start_worker=True)
            app = hub.open(self.file)
            t0 = time.time()
            while len(done) < 2 and time.time() - t0 < 5:
                time.sleep(0.02)
        finally:
            self.server.App.process = original
        self.assertEqual(sorted(done), sorted(r["id"] for r in app.runs.current()))

    def test_pasted_paths(self):
        quoted = f"'{self.file}'"
        escaped = str(self.file).replace(" ", "\\ ")
        self.assertEqual(self.hub.open(quoted).path, self.file.resolve())
        self.assertEqual(self.hub.open(escaped).path, self.file.resolve())
        with self.assertRaisesRegex(ProjectError, "no file"):
            self.hub.open(str(self.file.with_name("nope.3mf")))
        other = self.file.with_suffix(".stl")
        other.write_bytes(b"solid")
        with self.assertRaisesRegex(ProjectError, "not a .3mf"):
            self.hub.open(str(other))
        bad = self.file.with_name("broken.3mf")
        bad.write_bytes(b"not a zip")
        with self.assertRaisesRegex(ProjectError, "not a .3mf"):
            self.hub.open(str(bad))

    def test_changed_file_hides_runs_of_the_earlier_version(self):
        app = self.hub.open(self.file)
        first = {r["id"] for r in app.runs.current()}
        old_sha = app.runs.source_sha
        trial = app.runs.add(1, [{"key": "bridge_speed", "value": "80", "object_id": "4"}], None, "trial")

        os.utime(self.file, (time.time() + 5, time.time() + 5))  # touched, same bytes
        self.assertEqual(app.disk_state(), "same")

        rewrite_setting(self.file, "sparse_infill_density", "20%")
        self.assertEqual(app.disk_state(), "changed")
        app.load()
        self.assertEqual(app.disk_state(), "same")
        self.assertNotEqual(app.runs.source_sha, old_sha)
        now = {r["id"] for r in app.runs.current()}
        self.assertFalse(now & first)                  # new baselines for the new version
        self.assertEqual(app.runs.older(), 3)          # two old baselines and the trial
        self.assertEqual(app.runs.runs[trial["id"]]["status"], "cancelled")
        self.assertEqual(app.project.print_settings["sparse_infill_density"], "20%")

    def test_a_run_of_an_earlier_version_is_never_sliced_against_the_new_one(self):
        app = self.hub.open(self.file)
        run = app.runs.current()[0]
        rewrite_setting(self.file, "top_shell_layers", "3")
        app.load()
        app.runs.update(run["id"], status="queued")  # as if it had been queued before the reload
        app.process(run["id"])                        # returns before slicing anything
        self.assertEqual(app.runs.runs[run["id"]]["status"], "cancelled")

    def test_the_loaded_snapshot_is_what_gets_built(self):
        app = self.hub.open(self.file)
        before = app.project.source_bytes()
        rewrite_setting(self.file, "top_shell_layers", "9")
        self.assertEqual(app.project.source_bytes(), before)
        built = app.project.build([])
        with zipfile.ZipFile(io.BytesIO(built)) as z:
            cfg = json.loads(z.read("Metadata/project_settings.config"))
        self.assertEqual(cfg["top_shell_layers"], "5")

    def test_missing_file(self):
        app = self.hub.open(self.file)
        self.file.unlink()
        self.assertEqual(app.disk_state(), "missing")
        fresh = self.server.Hub(start_worker=False)  # after a restart the project can't load
        with self.assertRaises(self.server.NotFound):
            fresh.get(app.id)
        self.assertFalse(fresh.recent()[0]["exists"])

    def test_recent_list_and_forget(self):
        a = self.hub.open(self.file)
        second = make_project(self.file.with_name("second.3mf"))
        b = self.hub.open(second)
        self.assertEqual([p["id"] for p in self.hub.recent()], [b.id, a.id])
        self.hub.forget(a.id)
        self.assertEqual([p["id"] for p in self.hub.recent()], [b.id])
        self.hub.open(self.file)
        self.assertEqual([p["id"] for p in self.hub.recent()], [a.id, b.id])
        fresh = self.server.Hub(start_worker=False)  # the list survives a restart, unloaded
        item = fresh.recent()[0]
        self.assertEqual((item["name"], item["open"], item["exists"]), ("part one.3mf", False, True))
        self.assertIs(fresh.get(a.id).path, fresh.apps[a.id].path)
        with self.assertRaises(self.server.NotFound):
            fresh.get("../../etc")

    def test_upload_keeps_a_copy(self):
        data = self.file.read_bytes()
        app = self.hub.upload("dropped part.3mf", io.BytesIO(data), len(data))
        self.assertTrue(str(app.path).startswith(str(self.server.DATA.resolve() / "uploads")))
        self.assertEqual(app.path.read_bytes(), data)
        again = self.hub.upload("dropped part.3mf", io.BytesIO(data), len(data))
        self.assertEqual(again.id, app.id)
        item = next(p for p in self.hub.recent() if p["id"] == app.id)
        self.assertTrue(item["uploaded"])
        self.assertEqual(item["name"], "dropped part.3mf")
        with self.assertRaisesRegex(ProjectError, "not a .3mf"):
            self.hub.upload("notes.txt", io.BytesIO(b"x"), 1)
        with self.assertRaisesRegex(ProjectError, "junk.3mf is not a .3mf"):
            self.hub.upload("junk.3mf", io.BytesIO(b"garbage"), 7)
        with self.assertRaisesRegex(ProjectError, "stopped before"):
            self.hub.upload("short.3mf", io.BytesIO(data[:100]), len(data))
        leftovers = [p.name for p in (self.server.DATA / "uploads").iterdir() if p.is_file()]
        self.assertEqual(leftovers, [])

    def test_thumbnails(self):
        app = self.hub.open(self.file)
        png = app.thumbnail(1, small=True)               # no plate_1_small.png: falls back to plate_1.png
        self.assertEqual(png, bytes(range(256)) * 8)   # the fixture's plate_1.png
        self.assertIsNone(app.thumbnail(2, small=False))
        fresh = self.server.Hub(start_worker=False)
        self.assertEqual(fresh.thumbnail(app.id), png)  # read from the file on disk, project not loaded

    def test_pending_work_resumes_after_restart(self):
        app = self.hub.open(self.file)
        self.assertTrue(all(r["status"] == "queued" for r in app.runs.current()))
        fresh = self.server.Hub(start_worker=False)
        self.assertEqual(fresh.resume_pending(), ["part one.3mf"])
        self.assertEqual(fresh.worker.q.qsize(), 2)


if __name__ == "__main__":
    unittest.main()
