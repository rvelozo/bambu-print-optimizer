import tempfile
import unittest
from pathlib import Path

from slicer_optimizer import search
from slicer_optimizer.project3mf import Project

from .fixtures import make_project
from .test_project3mf import StubPresets


class SearchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.pr = Project(make_project(Path(self.tmp.name) / "p.3mf"), presets=StubPresets())

    def tearDown(self):
        self.tmp.cleanup()

    def test_current_values(self):
        self.assertEqual(search.current_value(self.pr, 1, "sparse_infill_density", "object"), "12%")
        self.assertEqual(search.current_value(self.pr, 1, "bridge_speed", "object"), "50")
        self.assertEqual(search.current_value(self.pr, 2, "top_shell_layers", "object"), "5")

    def test_current_value_makes_no_change(self):
        self.assertEqual(search.changes_for(self.pr, 1, {"bridge_speed": "50", "sparse_infill_density": "12%"}), [])

    def test_changes_cover_every_object_and_partner(self):
        ch = search.changes_for(self.pr, 2, {"top_shell_layers": "3"})
        keys = sorted((c["key"], c["object_id"]) for c in ch)
        # top_shell_thickness is not in the fixture, so only the knob itself changes here
        self.assertEqual(keys, [("top_shell_layers", "6"), ("top_shell_layers", "7")])

    def test_sensitivity_and_grid(self):
        knobs = [{"key": "bridge_speed", "values": ["50", "100"]}, {"key": "top_shell_layers", "values": ["3", "4", "5"]}]
        self.assertEqual(len(search.sensitivity(knobs)), 1 + 2 + 3)
        g = search.grid(knobs)
        self.assertEqual(len(g), 6)
        self.assertIn({"bridge_speed": "100", "top_shell_layers": "3"}, g)

    def test_pareto_and_pick(self):
        pts = [
            {"time_s": 100, "grams": 10, "stiffness": 80},   # fast, light, weak
            {"time_s": 200, "grams": 20, "stiffness": 95},   # slow, heavy, stiff
            {"time_s": 150, "grams": 15, "stiffness": 90},   # middle
            {"time_s": 210, "grams": 21, "stiffness": 94},   # dominated by #1
        ]
        goals = ["time_s", "grams", "stiffness"]
        self.assertEqual(search.pareto(pts, goals), [0, 1, 2])
        self.assertEqual(search.pick(pts, {"time_s": 10}), 0)
        self.assertEqual(search.pick(pts, {"stiffness": 10}), 1)
        self.assertEqual(search.pick(pts, {"time_s": 5, "stiffness": 5}), 2)
        self.assertEqual(search.pick(pts, {"time_s": 10}, {"stiffness": (">=", 90)}), 2)
        self.assertIsNone(search.pick(pts, {}))

    def test_chebyshev_reaches_concave_front_points(self):
        # (0,1), (0.6,0.6), (1,0): the middle point is in a concave part of the front;
        # a weighted sum with equal weights prefers an end point, Chebyshev picks the middle.
        pts = [{"time_s": 0, "grams": 1}, {"time_s": 0.6, "grams": 0.6}, {"time_s": 1, "grams": 0}]
        self.assertEqual(search.pick(pts, {"time_s": 1, "grams": 1}), 1)


if __name__ == "__main__":
    unittest.main()
