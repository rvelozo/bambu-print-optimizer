"""What a file is tuned for: its differences from the printer's default process, and the default rebuilt."""

import tempfile
import unittest
from pathlib import Path

from slicer_optimizer import bambu, overview, presets
from slicer_optimizer.project3mf import Change, Project

from . import shapes

HAVE_PROFILES = presets.resolve("process", shapes.PROCESS) is not None
HAVE_CLI = bambu.cli_path().exists()


def as_changes(diff):
    return [Change(c["key"], tuple(c["value"]) if isinstance(c["value"], list) else c["value"], c["object_id"], c.get("part_id"))
            for c in diff["changes"]]


class Values(unittest.TestCase):
    def test_numbers_and_percentages(self):
        self.assertTrue(overview.same("0.20", "0.2"))
        self.assertTrue(overview.same("100%", "100", "top_surface_density"))   # a percentage setting
        self.assertFalse(overview.same("50%", "50", "small_perimeter_speed"))  # % of the wall speed vs mm/s
        self.assertFalse(overview.same("grid", "gyroid"))

    def test_verdict(self):
        v = overview.verdict({"time_s": 600, "grams": 10.1, "quality": 90.0}, {"time_s": 1000, "grams": 10.0, "quality": 100.0})
        self.assertEqual((v["time_s"]["is"], v["time_s"]["change"]), ("better", -40.0))
        self.assertEqual(v["grams"]["is"], "same")    # +1 %: within 2 %
        self.assertEqual(v["quality"]["is"], "worse")  # −10 points


@unittest.skipUnless(HAVE_PROFILES, "needs Bambu Studio's profiles")
class Presets(unittest.TestCase):
    def test_a_preset_inherits_within_its_vendor(self):
        # fdm_process_common exists under many vendors; P2S's chain must use Bambu Lab's
        vendor, path = presets._find("process", "fdm_process_common", "BBL")
        self.assertEqual(path.parent.parent.name, "BBL")
        p = presets.resolve("process", shapes.PROCESS)
        self.assertEqual((p["line_width"], p["top_shell_layers"], p["sparse_infill_density"]), ("0.42", "5", "15%"))


@unittest.skipUnless(HAVE_PROFILES and HAVE_CLI, "needs Bambu Studio")
class AgainstTheDefault(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.TemporaryDirectory()
        path = shapes.project("box", shapes.frustum(10, 10, 10), Path(cls.dir.name))
        cls.default = Project(path)
        obj = cls.default.objects_on_plate(1)[0]
        cls.variant = Project("variant.3mf", data=cls.default.build([
            Change("sparse_infill_density", "5%"), Change("wall_loops", "4", obj.id), Change("filament_flow_ratio", "0.9"),
            Change("brim_type", "no_brim")]))

    @classmethod
    def tearDownClass(cls):
        cls.dir.cleanup()

    def test_a_project_made_from_the_default_has_no_differences(self):
        d = overview.differences(self.default, 1)
        self.assertEqual(d["reference"], shapes.PROCESS)
        self.assertEqual(d["rows"], [])
        self.assertEqual(d["changes"], [])

    def test_differences_where_they_are(self):
        d = overview.differences(self.variant, 1)
        rows = {(r["key"], r["scope"]): r for r in d["rows"]}
        self.assertEqual(set(rows), {("sparse_infill_density", "print-wide"), ("wall_loops", "object"),
                                     ("filament_flow_ratio", "filament"), ("brim_type", "print-wide")})
        self.assertEqual((rows["sparse_infill_density", "print-wide"]["now"], rows["sparse_infill_density", "print-wide"]["default"]), ("5%", "15%"))
        self.assertEqual(rows["wall_loops", "object"]["default"], "2")
        self.assertEqual(rows["filament_flow_ratio", "filament"]["default"], "0.98")
        self.assertFalse(rows["brim_type", "print-wide"]["tuning"])  # bed adhesion is how it has to print: kept

    def test_the_rebuilt_default_has_no_tuning_left_and_slices_like_the_default(self):
        d = overview.differences(self.variant, 1)
        rebuilt = Project("ref.3mf", data=self.variant.build(as_changes(d)))
        left = overview.differences(rebuilt, 1)
        self.assertEqual([r["key"] for r in left["rows"]], ["brim_type"])  # kept as the file has it
        same_brim = Project("b.3mf", data=self.default.build([Change("brim_type", "no_brim")]))
        a = bambu.slice_bytes(rebuilt.source_bytes(), 1)
        b = bambu.slice_bytes(same_brim.source_bytes(), 1)
        self.assertAlmostEqual(a.total_s, b.total_s, delta=0.01)
        self.assertAlmostEqual(a.grams, b.grams, delta=0.001)


if __name__ == "__main__":
    unittest.main()
