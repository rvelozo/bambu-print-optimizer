"""Quality proxies: measured on synthetic toolpaths, on test shapes sliced by Bambu Studio, and scored."""

import math
import tempfile
import unittest
from pathlib import Path

from slicer_optimizer import bambu, gcode, layers, quality

from . import shapes

H = 0.2


def square(half, feature, width=0.45, speed=100.0, cx=0.0, cy=0.0):
    c = [(cx - half, cy - half), (cx + half, cy - half), (cx + half, cy + half), (cx - half, cy + half)]
    return [(feature, *c[i], *c[(i + 1) % 4], width, speed) for i in range(4)]


def fake(layer_segments, heights=None):
    """A gcode.Analysis with these extrusions: one list of (feature, x0, y0, x1, y1, width, speed) per layer."""
    a = gcode.Analysis()
    z = 0.0
    for k, segs in enumerate(layer_segments, start=1):
        h = heights[k - 1] if heights else H
        z += h
        a.layers.append({"z": round(z, 4), "h": h})
        for feature, x0, y0, x1, y1, w, v in segs:
            length = math.hypot(x1 - x0, y1 - y0)
            a.x0.append(x0); a.y0.append(y0); a.x1.append(x1); a.y1.append(y1)
            a.width.append(w); a.height.append(0.4 if feature == "Bridge" else h)
            a.speed.append(v); a.flow.append(0.0); a.volume.append(0.0); a.time.append(length / v)
            a.feat.append(a._id(a.features, feature)); a.layer.append(k)
    return a


def walled(half, speed=100.0):
    """An outer wall with an inner wall inside it: the material is inward."""
    return square(half, "Outer wall", speed=speed) + square(half - 0.45, "Inner wall", speed=speed)


class Measure(unittest.TestCase):
    def test_vertical_walls_have_no_steps_and_nothing_over_air(self):
        q = quality.measure(fake([walled(10)] * 12))
        self.assertIsNone(q["slopes"]["step_um"])
        self.assertEqual(q["air"]["walls_mm"], 0.0)
        self.assertAlmostEqual(q["walls"]["speed"], 100.0, places=2)

    def test_a_45_degree_overhang(self):
        q = quality.measure(fake([walled(10 + H * k, speed=50.0) for k in range(12)]))
        self.assertAlmostEqual(q["slopes"]["step_um"], 1000 * H * math.sin(math.radians(45)), delta=0.5)
        # 0.2 mm out on a 0.45 mm line (0.407 mm spacing): 49% over air, Bambu's 25-50% class
        self.assertGreater(q["air"]["kinds"]["25-50"]["mm"], 0)
        self.assertEqual(q["air"]["kinds"]["50-75"]["mm"], 0.0)
        self.assertAlmostEqual(q["air"]["speed"], 50.0, places=2)

    def test_a_slope_leaning_in_is_supported(self):
        q = quality.measure(fake([walled(15 - H * k) for k in range(12)]))
        self.assertAlmostEqual(q["slopes"]["step_um"], 141.4, delta=0.5)
        self.assertEqual(q["air"]["walls_mm"], 0.0)

    def test_a_one_layer_ledge_is_not_a_slope(self):
        q = quality.measure(fake([walled(10)] * 6 + [walled(10.6)] * 6))
        self.assertIsNone(q["slopes"]["step_um"])
        self.assertGreater(q["air"]["walls_mm"], 0)  # the ledge itself hangs out 0.6 mm

    def test_bridges_over_open_space_and_over_infill(self):
        pillars = square(2, "Outer wall", cx=-10) + square(2, "Outer wall", cx=10)
        sparse = [("Sparse infill", x, -5, x, 5, 0.45, 100.0) for x in range(-6, 7, 3)]
        # lines anchored half a millimetre into each pillar, as Bambu ends them
        open_span = [("Bridge", -8.5, y / 2, 8.5, y / 2, 0.4, 50.0) for y in range(-3, 4)]
        over_infill = [("Bridge", -8.5, y / 2, 8.5, y / 2, 0.4, 50.0) for y in range(-3, 4)]
        q_open = quality.measure(fake([pillars] * 4 + [pillars + open_span]))
        q_hidden = quality.measure(fake([pillars] * 3 + [pillars + sparse] + [pillars + over_infill]))
        self.assertAlmostEqual(q_open["air"]["bridges_mm"], 119.0, places=1)
        self.assertEqual(q_open["air"]["hidden_bridges_mm"], 0.0)
        self.assertEqual(q_hidden["air"]["bridges_mm"], 0.0)
        self.assertAlmostEqual(q_hidden["air"]["hidden_bridges_mm"], 119.0, places=1)

    def test_solid_under_the_top_surface(self):
        fill = lambda feature: [(feature, -5, y / 2, 5, y / 2, 0.45, 100.0) for y in range(-10, 11)]
        stack = [walled(6) + fill("Sparse infill")] * 4 + [walled(6) + fill("Bridge")] + \
                [walled(6) + fill("Internal solid infill")] * 2 + [walled(6) + fill("Top surface")]
        q = quality.measure(fake(stack))
        self.assertAlmostEqual(q["top"]["solid_mm"], 4 * H, places=3)  # top, two solid layers, the bridge
        self.assertEqual(q["top"]["solid_layers"], 4)

    def test_layer_heights_leave_out_the_thick_bridge_tag(self):
        a = fake([walled(5)] * 3 + [walled(5) + [("Bridge", -4, 0, 4, 0, 0.4, 50.0)] * 5])
        a.layers[-1]["h"] = 0.4  # the tag a layer ends on can be the bridge's
        self.assertEqual(a.layer_heights()[1:], [H] * 4)


class Score(unittest.TestCase):
    BASE = {"v": quality.VERSION, "slopes": {"step_um": 100.0, "area_mm2": 100.0},
            "walls": {"speed": 100.0, "area_mm2": 300.0}, "air": {"speed": 40.0, "area_mm2": 0.0},
            "top": {"speed": 200.0, "area_mm2": 600.0, "solid_mm": 1.0, "solid_layers": 5}}

    def run_with(self, **changes):
        run = {k: dict(v) if isinstance(v, dict) else v for k, v in self.BASE.items()}
        for path, value in changes.items():
            aspect, key = path.split("__")
            run[aspect][key] = value
        return run

    def test_the_current_settings_score_100(self):
        s = quality.score(self.BASE, self.BASE)
        self.assertEqual((s["index"], s["worst"]), (100.0, 100.0))
        self.assertNotIn("air", s["aspects"])  # nothing over air on this part

    def test_aspects_count_by_the_surface_they_cover(self):
        s = quality.score(self.run_with(walls__speed=50.0), self.BASE)  # walls: half the speed, twice as good
        self.assertEqual(s["aspects"]["walls"]["score"], 200.0)
        self.assertAlmostEqual(s["index"], 100 * 2 ** 0.3, places=1)  # walls cover 30% of the surface
        self.assertEqual(s["worst"], 100.0)

    def test_less_solid_under_the_top_counts_more_does_not(self):
        thinner = quality.score(self.run_with(top__solid_layers=3), self.BASE)
        thicker = quality.score(self.run_with(top__solid_layers=7), self.BASE)
        self.assertEqual(thinner["aspects"]["top"]["score"], 60.0)
        self.assertEqual(thinner["worst"], 60.0)
        self.assertEqual(thicker["index"], 100.0)

    def test_over_air_compares_like_with_like(self):
        # thicker layers push the same overhang into a steeper class that prints slower: not a better finish
        kinds = lambda a, b: {"25-50": {"mm": 100.0, "speed": a}, "50-75": {"mm": 50.0, "speed": b}, "75-100": {"mm": 0.0, "speed": None},
                              "bridges": {"mm": 0.0, "speed": None}}
        base = {**self.BASE, "air": {"speed": 43.3, "area_mm2": 100.0, "kinds": kinds(50.0, 30.0)}}
        shifted = {**self.BASE, "air": {"speed": 36.0, "area_mm2": 100.0, "kinds": kinds(50.0, 30.0)}}
        slower = {**self.BASE, "air": {"speed": 34.0, "area_mm2": 100.0, "kinds": kinds(40.0, 30.0)}}
        self.assertEqual(quality.score(shifted, base)["aspects"]["air"]["score"], 100.0)
        self.assertAlmostEqual(quality.score(slower, base)["aspects"]["air"]["score"], 100 * 1.25 ** (2 / 3), places=1)

    def test_different_measure_versions_dont_compare(self):
        self.assertIsNone(quality.score({**self.BASE, "v": quality.VERSION - 1}, self.BASE))


@unittest.skipUnless(bambu.cli_path().exists(), "needs Bambu Studio")
class OnBambuSlices(unittest.TestCase):
    """The shapes sliced by Bambu Studio (P2S, 0.20mm Standard: overhang speeds 50, 30 and 10 mm/s)."""

    WALL_SPEED = 200.0  # outer wall speed of 0.20mm Standard @BBL P2S; overhang speeds 50, 30 and 10

    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.TemporaryDirectory()
        cls.made = {}

    @classmethod
    def tearDownClass(cls):
        cls.dir.cleanup()

    def slice(self, name, tris):
        if name not in self.made:
            path = shapes.project(name, tris, Path(self.dir.name))
            res = bambu.slice_bytes(path.read_bytes(), 1)
            self.made[name] = res.folder
        return self.made[name]

    def classes(self, folder):
        """Length and Bambu's mean commanded speed of the walls measured over air, per class."""
        a = gcode.analyze(folder / "plate_1.gcode.gz")
        out = {}

        def trace(i, part, d, hung):
            if hung >= quality.OVERHANG:
                c = "75-100" if hung >= 0.75 else "50-75" if hung >= 0.5 else "25-50"
                row = out.setdefault(c, [0.0, 0.0])
                row[0] += part
                row[1] += part * a.speed[i]
        quality.measure(a, trace)
        return {c: (length, lv / length) for c, (length, lv) in out.items()}

    def test_steps_match_layer_height_times_sine(self):
        for angle, height, out in [(30, 10, True), (45, 10, True), (60, 5, True), (45, 10, False), (80, 2, False)]:
            with self.subTest(angle=angle, overhang=out):
                folder = self.slice(f"lean{angle}{'out' if out else 'in'}", shapes.leaning(angle, height, out))
                q = layers.load_or_build(folder, 1)["quality"]  # the path a run's metrics take
                self.assertAlmostEqual(q["slopes"]["step_um"], 1000 * H * math.sin(math.radians(angle)), delta=1.0)

    def test_overhang_classes_follow_bambus_overhang_speeds(self):
        # Bambu smooths overhang speeds between its classes, so each class is checked by order: the
        # steeper the class, the slower Bambu printed it, all slower than a plain outer wall; the
        # steepest class runs at exactly its 10 mm/s
        speeds = []
        for angle, height, cls in [(30, 10, "25-50"), (45, 10, "50-75"), (60, 5, "75-100")]:
            with self.subTest(angle=angle):
                got = self.classes(self.slice(f"lean{angle}out", shapes.leaning(angle, height, True)))
                self.assertEqual(set(got), {cls})
                speeds.append(got[cls][1])
                self.assertLess(got[cls][1], self.WALL_SPEED)
        self.assertEqual(speeds, sorted(speeds, reverse=True))
        self.assertAlmostEqual(speeds[-1], 10.0, delta=0.5)

    def test_slopes_leaning_in_print_at_wall_speed(self):
        q = layers.load_or_build(self.slice("lean45in", shapes.leaning(45, 10, False)), 1)["quality"]
        self.assertEqual(q["air"]["walls_mm"], 0.0)

    def test_the_arch_bridge_is_over_open_space(self):
        q = layers.load_or_build(self.slice("arch", shapes.arch()), 1)["quality"]
        self.assertGreater(q["air"]["bridges_mm"], 800)  # 20 mm deep, 0.4 mm lines over a 20 mm span


if __name__ == "__main__":
    unittest.main()
