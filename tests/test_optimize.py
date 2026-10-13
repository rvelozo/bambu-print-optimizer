"""The optimizer's planning, on measured-looking numbers (no slicing)."""

import unittest

from slicer_optimizer import optimize

BASE_METRICS = {"feature_s": {"Bridge": 700.0, "Top surface": 300.0, "Sparse infill": 900.0, "Outer wall": 1200.0,
                              "Internal solid infill": 2000.0},
                "flow_cap": 12.0, "near_cap_s": 0.0, "tolerance_s": 17.3}

CATALOG = [
    {"key": "bridge_speed", "label": "Bridge speed", "values": ["50", "75", "100"], "goals": ["time_s"], "needs": "Bridge", "current": "50"},
    {"key": "top_shell_layers", "label": "Top shells", "values": ["2", "3", "4", "5"], "goals": ["time_s", "grams", "stiffness"],
     "needs": "Top surface", "current": "5"},
    {"key": "bottom_shell_layers", "label": "Bottom shells", "values": ["2", "3"], "goals": ["time_s", "grams"],
     "needs": "Bottom surface", "current": "3"},
    {"key": "overhang_3_4_speed", "label": "Overhang", "values": ["30", "50"], "goals": ["time_s"], "needs": "Overhang wall", "current": "30"},
    {"key": "layer_height", "label": "Layer height", "values": ["0.16", "0.2", "0.28"], "goals": ["time_s"],
     "off_by_default": "Changes how slopes look.", "current": "0.2"},
    {"key": "filament_max_volumetric_speed", "label": "Flow cap", "values": ["16", "21"], "goals": ["time_s"],
     "needs": "@flow_cap", "current": "12"},
    {"key": "wall_loops", "label": "Walls", "values": ["2", "3"], "goals": ["grams", "stiffness"], "current": "2"},
]


class ChooseKnobs(unittest.TestCase):
    def test_relevance_comes_from_the_baseline_features(self):
        knobs, skipped = optimize.choose_knobs(CATALOG, BASE_METRICS, {"time_s": 3}, None)
        keys = [k["key"] for k in knobs]
        self.assertEqual(keys, ["bridge_speed", "top_shell_layers"])  # layer height only when asked for
        why = {s["key"]: s["why"] for s in skipped}
        self.assertEqual(why["bottom_shell_layers"], "this plate has no bottom surface")
        self.assertEqual(why["overhang_3_4_speed"], "this plate has no overhang wall")
        self.assertEqual(why["filament_max_volumetric_speed"], "nothing on this plate prints at the flow cap")
        self.assertEqual(why["wall_loops"], "doesn't affect the goals you chose")

    def test_current_values_are_not_measured_again_and_allowed_list_applies(self):
        knobs, skipped = optimize.choose_knobs(CATALOG, BASE_METRICS, {"time_s": 3},
                                               {"bridge_speed", "top_shell_layers"})
        self.assertEqual(knobs[0], {"key": "bridge_speed", "values": ["75", "100"]})
        self.assertEqual({s["key"]: s["why"] for s in skipped}["layer_height"], "Changes how slopes look.")

    def test_budget_trims_values_then_settings(self):
        knobs = [{"key": "a", "values": ["1", "2", "3", "4"]}, {"key": "b", "values": ["1", "2", "3"]}, {"key": "c", "values": ["1", "2"]}]
        same, notes = optimize.fit_measure_budget(knobs, 20)
        self.assertEqual((same, notes), (knobs, []))
        trimmed, notes = optimize.fit_measure_budget(knobs, 7)
        self.assertEqual([k["values"] for k in trimmed], [["1", "4"], ["1", "3"], ["1", "2"]])
        trimmed, notes = optimize.fit_measure_budget(knobs, 5)
        self.assertEqual([k["key"] for k in trimmed], ["a", "b"])
        self.assertIn("c was left out of the measurements.", notes)


# measured on the v7 lid (Bambu Studio 02.08.04.57); stiffness is the flat-part proxy
LID_BASE = {"time_s": 8670.84, "grams": 130.71, "stiffness": 94.6, "cost": 2.48, "near_cap_s": 4516.0}
LID_SOLO = {
    ("bridge_speed", "75"): {"time_s": 8670.84 - 754, "grams": 130.71, "stiffness": 94.6},
    ("bridge_speed", "100"): {"time_s": 8670.84 - 1130, "grams": 130.71, "stiffness": 94.6},
    ("top_shell_layers", "3"): {"time_s": 8670.84 - 1179, "grams": 130.71 - 25.30, "stiffness": 93.2},
    ("top_shell_layers", "4"): {"time_s": 8670.84 - 585, "grams": 130.71 - 12.6, "stiffness": 94.0},
    ("outer_wall_speed", "250"): {"time_s": 8670.84 - 3.0, "grams": 130.71, "stiffness": 94.6},  # inside the noise
}
LID_KNOBS = [{"key": "bridge_speed", "values": ["75", "100"]}, {"key": "top_shell_layers", "values": ["3", "4"]},
             {"key": "outer_wall_speed", "values": ["250"]}]
TOL = {**optimize.NOISE, "time_s": 17.3}


class EffectsAndCombinations(unittest.TestCase):
    def test_noise_decides_what_matters(self):
        eff = {e["key"]: e for e in optimize.effects(LID_KNOBS, LID_SOLO, LID_BASE, {"time_s": 3, "grams": 3}, TOL)}
        self.assertTrue(eff["bridge_speed"]["matters"])
        self.assertTrue(eff["top_shell_layers"]["matters"])
        self.assertFalse(eff["outer_wall_speed"]["matters"])   # −3 s is inside the 17 s repeat noise
        top3 = next(r for r in eff["top_shell_layers"]["values"] if r["value"] == "3")
        self.assertEqual(sorted(top3["helps"]), ["grams", "time_s"])
        self.assertAlmostEqual(top3["deltas"]["grams"], -25.30, places=6)

    def test_tiny_effects_on_a_deterministic_plate_dont_count(self):
        # the lid slices identically every time (tolerance 0.5 s), but −3 s is still not worth a change
        eff = {e["key"]: e for e in optimize.effects(LID_KNOBS, LID_SOLO, LID_BASE, {"time_s": 3}, {**optimize.NOISE, "time_s": 0.5})}
        self.assertFalse(eff["outer_wall_speed"]["matters"])
        self.assertTrue(eff["bridge_speed"]["matters"])
        self.assertAlmostEqual(optimize.thresholds(LID_BASE, {"time_s": 0.5})["time_s"], 0.002 * 8670.84)

    def test_combinations_skip_what_was_measured_and_fit_the_budget(self):
        eff = optimize.effects(LID_KNOBS, LID_SOLO, LID_BASE, {"time_s": 3, "grams": 3}, TOL)
        current = {"bridge_speed": "50", "top_shell_layers": "5", "outer_wall_speed": "200"}
        knobs, notes = optimize.plan_combinations(eff, current, 10)
        # top shells first (it saves time and weight), then bridges; both with two values that helped
        self.assertEqual(knobs, [{"key": "top_shell_layers", "values": ["5", "3", "4"]},
                                 {"key": "bridge_speed", "values": ["50", "100", "75"]}])
        knobs, notes = optimize.plan_combinations(eff, current, 1)   # room for one new slice only
        self.assertEqual(knobs, [{"key": "top_shell_layers", "values": ["5", "3"]}, {"key": "bridge_speed", "values": ["50", "100"]}])
        self.assertEqual(notes, ["To fit the time you gave it, only the best value of Top shell layers and Bridge speed "
                                 "went into the combinations."])
        knobs, notes = optimize.plan_combinations(eff, current, 0)
        self.assertEqual(knobs, [])
        self.assertEqual(notes, ["To fit the time you gave it, Bridge speed helped alone but was left out."])

    def test_one_setting_needs_no_combination_stage(self):
        eff = optimize.effects(LID_KNOBS[:1], LID_SOLO, LID_BASE, {"time_s": 3}, TOL)
        self.assertEqual(optimize.plan_combinations(eff, {"bridge_speed": "50"}, 50), ([], []))


class Recommend(unittest.TestCase):
    POINTS = [
        {"run_id": "base", "time_s": 8670, "grams": 130.7, "stiffness": 94.6},
        {"run_id": "fast", "time_s": 6200, "grams": 105.0, "stiffness": 88.0},
        {"run_id": "mid", "time_s": 6290, "grams": 104.0, "stiffness": 91.8},
        {"run_id": "stiff", "time_s": 8100, "grams": 130.0, "stiffness": 96.0},  # beats base on all three
    ]

    def test_pick_limits_and_best_for_each_goal(self):
        w = {"time_s": 5, "grams": 3, "stiffness": 3}
        r = optimize.recommend(self.POINTS, w, {})
        self.assertEqual(r["pick"], "mid")
        self.assertEqual(r["best_for"], {"time_s": "fast", "grams": "mid", "stiffness": "stiff"})
        self.assertEqual(sorted(r["front"]), ["fast", "mid", "stiff"])
        limits = optimize.limits_for({"strength_pct": 99}, {"stiffness": 94.6})
        r = optimize.recommend(self.POINTS, w, limits)
        self.assertEqual(r["pick"], "stiff")          # only the base and "stiff" keep 99 % of the strength
        limits = optimize.limits_for({"max_time_s": 6000}, {})
        self.assertIsNone(optimize.recommend(self.POINTS, w, limits)["pick"])


if __name__ == "__main__":
    unittest.main()


class OptimizeTheLid(unittest.TestCase):
    """A whole optimization through the API on the v7 lid: goals in, recommendation out.

    Slow without the slice cache (about 250 slices); run with SLICER_OPT_SLOW=1.
    """

    @classmethod
    def setUpClass(cls):
        import os
        import tempfile
        import threading
        from pathlib import Path

        from slicer_optimizer import bambu
        v7 = Path(os.environ.get("SLICER_OPT_V7", "/nonexistent/gaveta_painel_v7.3mf"))
        if not (os.environ.get("SLICER_OPT_SLOW") and bambu.cli_path().exists() and v7.exists()):
            raise unittest.SkipTest("needs SLICER_OPT_SLOW=1, Bambu Studio and the v7 project")
        cls.data = tempfile.TemporaryDirectory()
        cls.old = os.environ.get("SLICER_OPT_DATA")
        os.environ["SLICER_OPT_DATA"] = cls.data.name
        import importlib
        from slicer_optimizer import server
        cls.server = importlib.reload(server)
        cls.httpd, cls.hub = cls.server.make_server(port=0)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.app = cls.hub.open(v7)

    @classmethod
    def tearDownClass(cls):
        import os
        cls.hub.stopping = True
        cls.httpd.shutdown()
        if cls.old is None:
            os.environ.pop("SLICER_OPT_DATA", None)
        else:
            os.environ["SLICER_OPT_DATA"] = cls.old
        cls.data.cleanup()

    def test_finds_at_least_the_hand_found_variant(self):
        import time
        store = self.app.optimizations
        o = store.create(1, {"time_s": 3, "grams": 3, "stiffness": 3}, {"strength_pct": 90}, None, 600)
        t0 = time.time()
        while store.items[o["id"]]["stage"] != "done":
            self.assertLess(time.time() - t0, 3600, "optimization didn't finish")
            time.sleep(1)
        v = store.view(store.items[o["id"]])
        skipped = {s["key"] for s in v["skipped"]}
        self.assertIn("overhang_3_4_speed", skipped)       # the lid has no overhang walls
        self.assertIn("layer_height", skipped)              # off unless asked for
        pick = v["pick"]
        base = v["base_goals"]
        self.assertGreaterEqual(pick["goals"]["stiffness"], 0.9 * base["stiffness"] - 1e-6)
        self.assertIn(pick["run_id"], v["front"])
        # found by a hand-made search on 2026-10-12: bridge 100, top shells 3, infill 10 %
        # → 6286 s, 103.99 g, 91.8 %. Some sliced candidate must match or beat it on all three.
        hand = {"time_s": 6286.0 + 1, "grams": 103.99 + 0.005, "stiffness": 91.8 - 0.05}
        goals = ["time_s", "grams", "stiffness"]
        self.assertTrue(any(p["time_s"] <= hand["time_s"] and p["grams"] <= hand["grams"] and p["stiffness"] >= hand["stiffness"]
                            for p in v["points"]), "nothing as good as the hand-found variant was sliced")
        self.assertNotIn("outer_wall_speed", [k["key"] for k in v.get("combine_knobs") or []])  # −3 s: too small
