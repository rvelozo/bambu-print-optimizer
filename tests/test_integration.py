"""Acceptance checks against the real Bambu Studio CLI and real project files.

Expected numbers were measured on 2026-10-12 with Bambu Studio 02.08.04.57
(docs/plan.md). Skipped when Bambu Studio or the files are missing. The panel
plates take 1-1.5 min each to slice, so they only run with SLICER_OPT_SLOW=1.
"""

import hashlib
import os
import re
import tempfile
import unittest
import zipfile
from pathlib import Path

from slicer_optimizer import bambu
from slicer_optimizer.project3mf import MODEL_SETTINGS, Project
from slicer_optimizer.rules import Rules, check_settings
from slicer_optimizer.trial import plan_changes, run_trial, save_variant

# The reference project (see CLAUDE.md); set these to run the checks, they skip otherwise.
V6_DIR = Path(os.environ.get("SLICER_OPT_V6_DIR", "/nonexistent"))
V6 = V6_DIR / "painel silica.3mf"
V7 = Path(os.environ.get("SLICER_OPT_V7", "/nonexistent/gaveta_painel_v7.3mf"))
HAVE_CLI = bambu.cli_path().exists()
SLOW = os.environ.get("SLICER_OPT_SLOW") == "1"
NO_SUPPORTS = Rules({"enable_support": "0"})


def errors(project):
    return [f for f in check_settings(project, NO_SUPPORTS) if f.level == "error"]


@unittest.skipUnless(HAVE_CLI and V6.exists(), "needs Bambu Studio and the v6 project")
class V6Acceptance(unittest.TestCase):
    def test_lid_baseline(self):
        r = bambu.slice_file(V6, 2)
        self.assertAlmostEqual(r.total_s, 8422, delta=1)
        self.assertAlmostEqual(r.grams, 89.67, delta=0.01)

    def test_lid_two_plus_two_with_bridge_100(self):
        pr = Project(V6)
        planned = plan_changes(pr, 2, [("top_shell_layers", "2"), ("bottom_shell_layers", "2"), ("bridge_speed", "100")])
        t = run_trial(pr, 2, planned)
        self.assertAlmostEqual(t.variant.total_s, 5872, delta=1)
        self.assertAlmostEqual(t.variant.grams, 68.64, delta=0.01)
        self.assertIsNone(t.diagnosis)

    def test_per_object_initial_layer_infill_speed_is_reported_ignored(self):
        pr = Project(V6)
        t = run_trial(pr, 2, plan_changes(pr, 2, [("initial_layer_infill_speed", "35")]))
        self.assertTrue(t.variant.same_as(t.base))
        self.assertTrue(t.diagnosis.startswith("ignored per object"), t.diagnosis)
        self.assertGreater(t.print_wide_check.total_s - t.base.total_s, 60)

    def test_support_rule_flags_the_three_v6_files(self):
        for name in ("painel silica.3mf", "painel silica - tampa otimizada.3mf", "painel silica - tampa 2+2.3mf"):
            found = errors(Project(V6_DIR / name))
            self.assertTrue(any("gaveta_painel_v6.stl" in f.where for f in found), (name, found))
        two_plus_two = errors(Project(V6_DIR / "painel silica - tampa 2+2.3mf"))
        self.assertIn("print-wide", [f.where for f in two_plus_two])
        self.assertEqual(sum("gaveta_trilho_v6.stl" in f.where for f in two_plus_two), 2)

    def test_saved_variant_is_the_sliced_file(self):
        pr = Project(V6)
        t = run_trial(pr, 2, plan_changes(pr, 2, [("sparse_infill_density", "10%")]))
        with tempfile.TemporaryDirectory() as d:
            dest, prov = save_variant(pr, t, Path(d) / "lid-10.3mf")
            self.assertEqual(hashlib.sha256(dest.read_bytes()).hexdigest(), t.variant.sha256)
            self.assertTrue(prov.exists())
            with zipfile.ZipFile(V6) as a, zipfile.ZipFile(dest) as b:
                self.assertEqual(a.namelist(), b.namelist())
                for i in a.infolist():
                    if i.filename != MODEL_SETTINGS:
                        j = b.getinfo(i.filename)
                        self.assertEqual((i.CRC, i.compress_type, i.date_time), (j.CRC, j.compress_type, j.date_time))
            lid = next(o for o in Project(dest).objects if o.name == "gaveta_tampa_v6.stl")
            self.assertEqual(lid.overrides["sparse_infill_density"], "10%")
            again = bambu.slice_file(dest, 2, use_cache=False)  # a real slice of the saved file
            self.assertFalse(again.cached)
            self.assertTrue(again.same_as(t.variant))


@unittest.skipUnless(HAVE_CLI and V7.exists(), "needs Bambu Studio and the v7 project")
class V7Acceptance(unittest.TestCase):
    def test_no_supports_anywhere(self):
        self.assertEqual(errors(Project(V7)), [])

    def test_lid_repeat_slices_are_identical(self):
        lid = bambu.slice_file(V7, 1, repeats=2)
        self.assertGreaterEqual(lid.samples, 2)
        self.assertEqual(lid.noise_s, 0)
        self.assertEqual(lid.tolerance_s, 0.5)

    def test_top_shell_layers_blocked_by_thickness(self):
        # top_shell_thickness = 1 mm already needs 5 layers, so 3 layers alone does nothing.
        pr = Project(V7)
        t = run_trial(pr, 1, plan_changes(pr, 1, [("top_shell_layers", "3")]))
        self.assertTrue(t.variant.same_as(t.base))
        self.assertTrue(t.diagnosis.startswith("blocked by top_shell_thickness = 1"), t.diagnosis)
        self.assertAlmostEqual(t.unblocked.total_s - t.base.total_s, -1179, delta=1)
        self.assertAlmostEqual(t.unblocked.grams - t.base.grams, -25.30, delta=0.01)

    def test_lid_and_rails_baseline(self):
        lid = bambu.slice_file(V7, 1)
        self.assertAlmostEqual(lid.total_s, 8671, delta=1)
        self.assertAlmostEqual(lid.grams, 130.71, delta=0.01)
        rails = bambu.slice_file(V7, 2)
        self.assertAlmostEqual(rails.total_s, 5018, delta=1)
        self.assertAlmostEqual(rails.grams, 56.85, delta=0.01)
        self.assertEqual(lid.support_s + rails.support_s, 0)

    @unittest.skipUnless(SLOW, "set SLICER_OPT_SLOW=1 to slice the panel")
    def test_panel_baseline(self):
        # The panel's time estimate is not repeatable: 14230.56-14244.35 s over four
        # slices of the same bytes (grams identical), hence the wider tolerance.
        panel = bambu.slice_file(V7, 3)
        self.assertAlmostEqual(panel.total_s, 14237, delta=30)
        self.assertAlmostEqual(panel.grams, 116.92, delta=0.01)
        self.assertEqual(panel.support_s, 0)

    def test_assembly_information_survives_a_change(self):
        pr = Project(V7)
        data = pr.build([p.change for p in plan_changes(pr, 1, [("top_shell_layers", "3")])])
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "v.3mf"
            out.write_bytes(data)
            with zipfile.ZipFile(V7) as a, zipfile.ZipFile(out) as b:
                for name in ("Metadata/assembly_step.json", "Metadata/assembly_model.json", "3D/3dmodel.model"):
                    self.assertEqual(a.getinfo(name).CRC, b.getinfo(name).CRC, name)
                assemble = re.compile(r"<assemble>.*?</assemble>", re.S)
                self.assertEqual(assemble.search(a.read(MODEL_SETTINGS).decode()).group(0),
                                 assemble.search(b.read(MODEL_SETTINGS).decode()).group(0))


if __name__ == "__main__":
    unittest.main()
