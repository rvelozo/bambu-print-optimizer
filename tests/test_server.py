"""The local app's API against the real v7 project (slices come from the cache)."""

import json
import os
import struct
import tempfile
import threading
import time
import unittest
import urllib.request
from pathlib import Path

from slicer_optimizer import bambu
from . import MEASURED_WITH, measured_with_this_bambu

V7 = Path(os.environ.get("SLICER_OPT_V7", "/nonexistent/gaveta_painel_v7.3mf"))


@unittest.skipUnless(bambu.cli_path().exists() and V7.exists(), "needs Bambu Studio and the v7 project")
class ServerApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = tempfile.TemporaryDirectory()
        os.environ["SLICER_OPT_DATA"] = cls.data.name
        import importlib
        from slicer_optimizer import server
        importlib.reload(server)
        cls.server = server
        cls.httpd, cls.hub = server.make_server(port=0)
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        opened = cls.post_static(cls.base + "/api/projects", {"path": str(V7)})
        cls.pid = opened["id"]
        cls.api = f"/api/p/{cls.pid}"

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.data.cleanup()

    @staticmethod
    def post_static(url, payload):
        req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())

    def get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=30) as r:
            body = r.read()
            return json.loads(body) if "json" in r.headers.get("Content-Type", "") else body

    def post(self, path, payload):
        req = urllib.request.Request(self.base + path, data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())

    def wait_done(self, run_id, timeout=300):
        t0 = time.time()
        while time.time() - t0 < timeout:
            run = self.get(f"{self.api}/runs/{run_id}")
            if run["status"] in ("done", "error"):
                return run
            time.sleep(0.5)
        self.fail(f"run {run_id} not done after {timeout} s")

    @unittest.skipUnless(measured_with_this_bambu(), f"times measured with Bambu Studio {MEASURED_WITH}")
    def test_project_and_baseline_lid(self):
        info = self.get(f"{self.api}/project")
        self.assertEqual([p["index"] for p in info["plates"]], [1, 2, 3])
        self.assertTrue(all(p["thumbnail"] for p in info["plates"]))
        self.assertEqual(info["disk"], "same")
        runs = self.get(f"{self.api}/runs")["runs"]
        lid = next(r for r in runs if r["plate"] == 1 and r["kind"] == "baseline")
        lid = self.wait_done(lid["id"])
        self.assertEqual(lid["status"], "done")
        self.assertAlmostEqual(lid["metrics"]["total_s"], 8670.84, delta=1)
        self.assertEqual(lid["metrics"]["noise_s"], 0)
        analysis = self.get(f"{self.api}/runs/{lid['id']}/analysis")
        self.assertAlmostEqual(analysis["total_s"], lid["metrics"]["total_s"], delta=1)
        blob = self.get(f"{self.api}/runs/{lid['id']}/layer/7")
        n_ext, n_tr = struct.unpack("<II", blob[:8])
        self.assertEqual(len(blob), 8 + 4 * (10 * n_ext + 5 * n_tr))
        self.assertEqual(n_ext, analysis["layers"][7]["ext_count"])

    @unittest.skipUnless(measured_with_this_bambu(), f"times measured with Bambu Studio {MEASURED_WITH}")
    def test_trial_through_the_api(self):
        run = self.post(f"{self.api}/runs", {"plate": 1, "scope": "plate",
                                      "changes": [{"key": "bridge_speed", "value": "100", "object_id": None}]})
        run = self.wait_done(run["id"])
        self.assertAlmostEqual(run["metrics"]["total_s"], 8670.84 - 1130.0, delta=5)  # −18m50s, measured in P1
        again = self.post(f"{self.api}/runs", {"plate": 1, "scope": "plate",
                                        "changes": [{"key": "bridge_speed", "value": "100", "object_id": None}]})
        self.assertEqual(again["id"], run["id"])  # the same change is one run

    def test_print_log_and_calibration(self):
        runs = self.get(f"{self.api}/runs")["runs"]
        lid = self.wait_done(next(r for r in runs if r["plate"] == 1 and r["kind"] == "baseline")["id"])
        view = self.post(f"{self.api}/prints", {"run_id": lid["id"], "actual_s": lid["metrics"]["total_s"] * 1.05,
                                         "actual_g": 131.5, "outcome": "ok", "issues": ["adhesion"], "kind": "test"})
        self.assertEqual(len(view["prints"]), 1)
        cal = view["calibration"][0]
        self.assertAlmostEqual(cal["time"]["ratio"], 1.05, places=6)
        self.assertEqual(cal["filament"], "Bambu PLA Lite @BBL P2S 0.4 nozzle")
        req = urllib.request.Request(self.base + f"{self.api}/prints/{view['prints'][0]['id']}", method="DELETE")
        with urllib.request.urlopen(req, timeout=10) as r:
            self.assertEqual(json.loads(r.read())["prints"], [])

    def test_rejects_unknown_settings(self):
        req = urllib.request.Request(self.base + f"{self.api}/runs", method="POST",
                                     data=json.dumps({"plate": 1, "changes": [{"key": "brdige_speed", "value": "1"}]}).encode(),
                                     headers={"Content-Type": "application/json"})
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req, timeout=10)
        self.assertEqual(ctx.exception.code, 400)

    def test_static_files_and_traversal(self):
        self.assertIn(b"Recent projects", self.get("/"))
        self.assertIn(b"Try a change", self.get(f"/p/{self.pid}/"))
        self.assertEqual(self.get(f"/api/projects/{self.pid}/thumbnail")[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(self.get(f"{self.api}/thumbnail/3")[:8], b"\x89PNG\r\n\x1a\n")
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(self.base + "/web/../server.py", timeout=10)
        self.assertIn(ctx.exception.code, (403, 404))

    def test_save_through_the_dialog_route(self):
        # The macOS Save dialog itself needs a click; here it answers like osascript does
        # (a POSIX path, or None for Cancel) and the rest is the real route.
        import hashlib
        run = self.post(f"{self.api}/runs", {"plate": 1, "scope": "plate",
                                             "changes": [{"key": "bridge_speed", "value": "100", "object_id": None}]})
        run = self.wait_done(run["id"])
        original = self.server.choose_save_path
        try:
            with tempfile.TemporaryDirectory() as out:
                asked = {}

                def fake_dialog(start_dir, name):
                    asked.update(start_dir=start_dir, name=name)
                    return str(Path(out) / "lid variant")  # no extension typed

                self.server.choose_save_path = fake_dialog
                res = self.post(f"{self.api}/runs/{run['id']}/save", {"dialog": True})
                saved = Path(res["saved"])
                self.assertEqual(saved.name, "lid variant.3mf")
                self.assertEqual(hashlib.sha256(saved.read_bytes()).hexdigest(), run["sha"])
                self.assertEqual(asked["start_dir"], V7.parent)
                self.assertEqual(asked["name"], "gaveta_painel_v7 - Bridge 100.3mf")
                again = self.post(f"{self.api}/runs/{run['id']}/save", {"dialog": True})  # replace: the dialog asked
                self.assertEqual(again["saved"], res["saved"])
                self.server.choose_save_path = lambda start_dir, name: None
                self.assertEqual(self.post(f"{self.api}/runs/{run['id']}/save", {"dialog": True}), {"cancelled": True})
                self.server.choose_save_path = lambda start_dir, name: str(V7)
                req = urllib.request.Request(self.base + f"{self.api}/runs/{run['id']}/save", method="POST",
                                             data=b'{"dialog": true}', headers={"Content-Type": "application/json"})
                with self.assertRaises(urllib.error.HTTPError) as ctx:
                    urllib.request.urlopen(req, timeout=30)
                self.assertIn("refusing to overwrite the source project", json.loads(ctx.exception.read())["error"])
        finally:
            self.server.choose_save_path = original

    def test_projects_list_and_errors(self):
        listing = self.get("/api/projects")
        self.assertIn(self.pid, [p["id"] for p in listing["projects"]])
        self.assertEqual(self.get("/api/health")["app"], "slicer-optimizer")
        for path, code in (("/api/projects", 400), ("/api/p/0123456789abcdef/project", 404)):
            req = urllib.request.Request(self.base + path, method="POST" if path == "/api/projects" else "GET",
                                         data=json.dumps({"path": "/nowhere/x.3mf"}).encode() if path == "/api/projects" else None,
                                         headers={"Content-Type": "application/json"})
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(req, timeout=10)
            self.assertEqual(ctx.exception.code, code)

    def test_upload_through_the_api(self):
        data = V7.read_bytes()
        req = urllib.request.Request(self.base + "/api/projects/upload", data=data, method="POST",
                                     headers={"X-Filename": "v7%20copy.3mf", "Content-Type": "application/octet-stream"})
        with urllib.request.urlopen(req, timeout=60) as r:
            res = json.loads(r.read())
        self.assertNotEqual(res["id"], self.pid)
        info = self.get(f"/api/p/{res['id']}/project")
        self.assertEqual((info["name"], info["uploaded"]), ("v7 copy.3mf", True))
        # the copy is the same bytes, so its baselines come straight from the slice cache
        runs = self.get(f"/api/p/{res['id']}/runs")["runs"]
        self.assertEqual(sorted(r["plate"] for r in runs), [1, 2, 3])


if __name__ == "__main__":
    unittest.main()
