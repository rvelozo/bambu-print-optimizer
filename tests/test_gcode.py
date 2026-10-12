import gzip
import math
import tempfile
import unittest
from pathlib import Path

from slicer_optimizer import bambu, gcode, layers

HEADER = """; HEADER_BLOCK_START
; total filament length [mm] : 10.0
; filament: 1
; HEADER_BLOCK_END
; CONFIG_BLOCK_START
; machine_max_speed_x = 1000,1000
; machine_max_speed_y = 1000,1000
; machine_max_speed_z = 20,20
; machine_max_speed_e = 30,30
; machine_max_acceleration_x = 20000,20000
; machine_max_acceleration_y = 20000,20000
; machine_max_acceleration_z = 500,500
; machine_max_acceleration_e = 5000,5000
; machine_max_acceleration_extruding = 20000,20000
; machine_max_acceleration_retracting = 5000,5000
; machine_max_acceleration_travel = 10000,10000
; machine_max_jerk_x = 9,9
; machine_max_jerk_y = 9,9
; machine_max_jerk_z = 3,3
; machine_max_jerk_e = 2.5,2.5
; filament_diameter = 1.75
; filament_max_volumetric_speed = 16
; machine_load_filament_time = 26
; machine_prepare_compensation_time = 370
; CONFIG_BLOCK_END
"""


def write(tmp, body: str) -> Path:
    p = Path(tmp) / "t.gcode"
    p.write_text(HEADER + body)
    return p


class PlannerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_single_long_move_is_a_trapezoid(self):
        # 100 mm at 100 mm/s with 1000 mm/s² starting and ending at the 9 mm/s jerk speed
        body = "M83\nM204 S1000\n; CHANGE_LAYER\n; FEATURE: Outer wall\nG1 X0 Y0 F6000\nG1 X100 Y0 E1 F6000\nG1 X100 Y0.0001 F6000\n"
        a = gcode.analyze(write(self.tmp.name, body))
        v, j, acc = 100.0, 9.0, 1000.0
        expected = 2 * (v - j) / acc + (100 - 2 * (v * v - j * j) / (2 * acc)) / v
        self.assertAlmostEqual(a.time_by_feature()["Outer wall"], expected, places=3)

    def test_travel_is_booked_as_undefined_and_travel(self):
        body = "M83\n; CHANGE_LAYER\n; FEATURE: Outer wall\nG1 X10 Y0 F6000\nG1 X20 Y0 E0.5\nG1 X40 Y0\nG1 X40 Y1\n"
        a = gcode.analyze(write(self.tmp.name, body))
        t = a.time_by_feature()
        self.assertGreater(t["Undefined"], 0)
        self.assertAlmostEqual(t["Travel"], t["Undefined"], places=6)
        self.assertGreater(t["Outer wall"], 0)

    def test_dwell_needs_two_queued_blocks(self):
        # A dwell with fewer than two blocks queued waits for later blocks; at the
        # end of the file it is dropped, as in Bambu Studio.
        a = gcode.analyze(write(self.tmp.name, "M83\nG1 X10 F6000\nM400 S180\n"))
        self.assertLess(a.total_s, 1.0)
        b = gcode.analyze(write(self.tmp.name, "M83\nG1 X10 F6000\nG1 X20\nG1 X30\nM400 S180\nG1 X40\nG1 X50\n"))
        self.assertGreater(b.total_s, 180)

    def test_g29_counts_only_inside_m622_j1(self):
        outside = gcode.analyze(write(self.tmp.name, "M83\nG1 X10 F6000\nG1 X20\nG29 A1\nG1 X30\nG1 X40\n"))
        inside = gcode.analyze(write(self.tmp.name, "M83\nG1 X10 F6000\nG1 X20\nM622 J1\nG29 A1\nM623\nG1 X30\nG1 X40\n"))
        # the dwell also flushes the planner queue, so the move before it ends at a stop (+8 ms)
        self.assertAlmostEqual(inside.total_s - outside.total_s, 370, delta=0.05)

    def test_first_filament_load_is_flush(self):
        a = gcode.analyze(write(self.tmp.name, "M83\nG1 X10 F6000\nG1 X20\nT0\nT0\nG1 X30\nG1 X40\nT65535\n"))
        self.assertAlmostEqual(a.time_by_feature().get("Flush", 0), 26, places=3)

    def test_floating_vertical_shell_counts_as_internal_solid_infill(self):
        body = "M83\n; CHANGE_LAYER\n; FEATURE: Floating vertical shell\nG1 X0 Y0 F6000\nG1 X10 Y0 E0.2\nG1 X10 Y1\n"
        a = gcode.analyze(write(self.tmp.name, body))
        self.assertIn("Internal solid infill", a.time_by_feature())
        self.assertIn("Floating vertical shell", a.features)

    def test_layer_data_preserves_time_and_merges_collinear(self):
        body = ("M83\n; CHANGE_LAYER\n; Z_HEIGHT: 0.2\n; LAYER_HEIGHT: 0.2\n; FEATURE: Outer wall\n; LINE_WIDTH: 0.45\n"
                "G1 X0 Y0 F6000\nG1 X5 Y0 E0.2\nG1 X10 Y0 E0.2\nG1 X10 Y5 E0.2\nG1 X10 Y5.1\n")
        a = gcode.analyze(write(self.tmp.name, body))
        an, blob = layers.build(a)
        self.assertEqual(an["segments"]["raw"], 3)
        self.assertEqual(an["segments"]["merged"], 2)
        self.assertAlmostEqual(sum(l["time_s"] for l in an["layers"]), an["total_s"], places=3)
        self.assertEqual(len(blob) % 4, 0)


MULTI_HEADER = HEADER.replace("; total filament length [mm] : 10.0\n; filament: 1\n",
                              "; total filament length [mm] : 24332.61,1454.35,1599.20\n"
                              "; total filament weight [g] : 72.57,4.34,4.77\n; filament: 1,6,7\n").replace(
    "; filament_max_volumetric_speed = 16\n",
    "; filament_max_volumetric_speed = 12,12,12,12,12,18,12\n; filament_cost = 20,20,20,20,20,30,25\n"
    '; filament_settings_id = "PLA A";"PLA A";"PLA A";"PLA A";"PLA A";"PETG B";"PLA A"\n'
    "; filament_type = PLA;PLA;PLA;PLA;PLA;PETG;PLA\n; machine_unload_filament_time = 31\n")

CHANGE = """T5
;VG1 E4 F299
; VFLUSH_START
;VG1 E41.5 F299
SYNC T10
; VFLUSH_END
"""


class MultiFilamentTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def run_body(self, body):
        p = Path(self.tmp.name) / "m.gcode"
        p.write_text(MULTI_HEADER + body)
        return gcode.analyze(p)

    def walls(self, middle=""):
        return ("M83\n; CHANGE_LAYER\n; FEATURE: Outer wall\nG1 X0 Y0 F6000\nG1 X10 Y0 E0.5\n" + middle +
                "G1 X20 Y0 E0.5 F6000\nG1 X30 Y0 E0.5\nG1 X30 Y1\n")  # F: a ;VG1 F299 is modal, as in Bambu

    def test_header_lists_one_value_per_filament(self):
        a = self.run_body(self.walls())
        self.assertAlmostEqual(a.header["filament_mm"], 24332.61 + 1454.35 + 1599.20, places=6)
        self.assertEqual(a.header["filament_g_each"], [72.57, 4.34, 4.77])

    def test_tool_change_flush_sync_and_virtual_moves(self):
        base = self.run_body(self.walls("T0\n"))     # the first filament: load time only
        changed = self.run_body(self.walls("T0\n" + CHANGE))
        f0 = base.time_by_feature()["Flush"]
        f1 = changed.time_by_feature()["Flush"]
        flush_move = 41.5 / (299 / 60)                  # E-only, starts and ends at the E jerk speed
        self.assertAlmostEqual(f0, 26, places=3)
        self.assertAlmostEqual(f1 - f0, 31 + 26 + 10 + flush_move, delta=0.01)
        # the de-retract before VFLUSH_START belongs to the feature being printed
        wall_extra = changed.time_by_feature()["Outer wall"] - base.time_by_feature()["Outer wall"]
        self.assertAlmostEqual(wall_extra, 4 / (299 / 60), delta=0.01)

    def test_sync_with_another_role_is_not_flush(self):
        a = self.run_body(self.walls("SYNC T7 R0\n"))
        b = self.run_body(self.walls())
        self.assertAlmostEqual(a.total_s - b.total_s, 7, delta=0.01)
        self.assertEqual(a.time_by_feature().get("Flush", 0.0), 0.0)

    def test_filament_info_and_cost_per_filament(self):
        from slicer_optimizer import metrics
        a = self.run_body(self.walls())
        fil = layers.filament_info(a)
        self.assertEqual([f["slot"] for f in fil["filaments"]], [1, 6, 7])
        self.assertTrue(fil["caps_differ"])
        self.assertIsNone(fil["max_flow"])              # no single cap to compare against
        self.assertEqual(fil["name"], "PLA A + PETG B")

        class Res:
            grams = 72.57 + 4.34 + 4.77
            filament_g = {1: 72.57, 6: 4.34, 7: 4.77}
        self.assertAlmostEqual(metrics.filament_cost(Res, fil), (72.57 * 20 + 4.34 * 30 + 4.77 * 25) / 1000, places=9)
        an, _ = layers.build(a)
        self.assertIsNone(an["flow"]["cap"])
        self.assertEqual(an["flow"]["near_cap_s"], 0)


V7_SHA = "9c772856dc04c56a67e880d5"
# custom-mv473g59-ge-circle-15cm.3mf: plates 1 and 2 print in 3 and 2 filaments (AMS changes and flushes)
CIRCLE_SHA = "ebf17d74ce3dedbd73664f62"


def _cached(plate, sha=V7_SHA):
    folders = sorted((bambu.CACHE / "slices").glob(f"{sha}-p{plate}-*")) if (bambu.CACHE / "slices").exists() else []
    return folders[0] if folders and (folders[0] / f"plate_{plate}.gcode.gz").exists() else None


class MatchesBambuStudio(unittest.TestCase):
    """The ported estimator against result.json for the same G-code.

    Bambu Studio adds block times up in single precision; compared that way
    (bambu_total_s, time_by_feature(bambu=True)) the model matches it to within
    0.004 % of the total and 0.03 % per feature on every plate measured. The
    exact sums differ from Bambu's by up to 26 s (v7 panel outer walls).
    """

    def check(self, plate, sha=V7_SHA, single_filament=True):
        import json
        folder = _cached(plate, sha)
        if folder is None:
            self.skipTest("slices not in the cache (run the integration tests or open the project in the app first)")
        entry = json.loads((folder / "result.json").read_text())["sliced_plates"][0]
        a = gcode.analyze(folder / f"plate_{plate}.gcode.gz")
        self.assertAlmostEqual(a.bambu_total_s, entry["total_predication"], delta=0.00005 * entry["total_predication"])
        self.assertAlmostEqual(a.bambu_model_s, entry["main_predication"], delta=0.00005 * entry["main_predication"])
        ours = a.time_by_feature(bambu=True)
        for name, t in entry["feature_type_times"].items():
            if t >= 1:
                self.assertAlmostEqual(ours.get(name, 0.0), t, delta=max(0.05, 0.0003 * t), msg=name)
        if single_filament:  # with several filaments the header also counts flushed filament
            self.assertAlmostEqual(a.extruded_mm, a.header["filament_mm"], delta=0.02)
        return a, entry

    def test_lid(self):
        self.check(1)

    def test_rails(self):
        self.check(2)

    def test_panel(self):
        a, entry = self.check(3)
        # the exact sum is larger: Bambu's single-precision sums drop part of the tiny grid moves
        self.assertGreater(a.time_by_feature()["Outer wall"] - entry["feature_type_times"]["Outer wall"], 20)

    def test_three_filaments(self):
        a, entry = self.check(1, CIRCLE_SHA, single_filament=False)
        self.assertEqual(a.header["filament"], "1,6,7")
        self.assertEqual(len(a.header["filament_mm_each"]), 3)

    def test_two_filaments_with_flushes(self):
        # each change: 26 s load + 31 s unload, 116.41 mm virtual flush at F299 and SYNC T10
        a, entry = self.check(2, CIRCLE_SHA, single_filament=False)
        self.assertAlmostEqual(a.time_by_feature(bambu=True)["Flush"], entry["feature_type_times"]["Flush"], delta=0.05)


if __name__ == "__main__":
    unittest.main()
