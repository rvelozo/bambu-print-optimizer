import json
import tempfile
import unittest
from pathlib import Path

from slicer_optimizer.bambu import SliceResult
from slicer_optimizer.project3mf import Change, Project, ProjectError
from slicer_optimizer.rules import Rules, check_settings, check_slice, load_rules
from slicer_optimizer.trial import plan_changes

from .fixtures import make_project
from .test_project3mf import StubPresets


def result(features):
    return SliceResult(plate=1, total_s=100, model_s=90, grams=10, feature_s=features, objects=[], sha256="x", wall_s=1)


class RulesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = make_project(Path(self.tmp.name) / "p.3mf")

    def tearDown(self):
        self.tmp.cleanup()

    def project_with(self, changes):
        p = Path(self.tmp.name) / "v.3mf"
        p.write_bytes(Project(self.path).build(changes))
        return Project(p)

    def test_clean_project_has_no_errors(self):
        f = check_settings(Project(self.path), Rules({"enable_support": "0"}))
        self.assertEqual([x for x in f if x.level == "error"], [])

    def test_object_and_print_wide_violations_are_located(self):
        pr = self.project_with([Change("enable_support", "1", "7"), Change("enable_support", "1")])
        errors = [x for x in check_settings(pr, Rules({"enable_support": "0"})) if x.level == "error"]
        self.assertEqual(sorted(e.where for e in errors), ["plate 2, object 7 (rail.stl)", "print-wide"])

    def test_supports_warned_without_a_rule(self):
        pr = self.project_with([Change("enable_support", "1", "4")])
        warn = [x for x in check_settings(pr, Rules()) if x.level == "warning"]
        self.assertEqual(len(warn), 1)
        self.assertIn("object 4", warn[0].where)

    def test_forbidden_feature_prefix(self):
        r = Rules(forbid_features=["Support"])
        self.assertEqual(check_slice(result({"Outer wall": 10, "Support interface": 0}), r), [])
        self.assertEqual(len(check_slice(result({"Outer wall": 10, "Support interface": 3.2}), r)), 1)

    def test_rules_file_next_to_project(self):
        (Path(self.tmp.name) / "slicer-rules.json").write_text(json.dumps({"require": {"enable_support": 0}}))
        self.assertEqual(load_rules(self.path).require, {"enable_support": "0"})
        (Path(self.tmp.name) / "p.rules.json").write_text(json.dumps({"forbid_features": ["Support"]}))
        self.assertEqual(load_rules(self.path).forbid_features, ["Support"])
        (Path(self.tmp.name) / "bad.json").write_text(json.dumps({"requir": {}}))
        with self.assertRaises(ValueError):
            load_rules(self.path, Path(self.tmp.name) / "bad.json")


class PlanningTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.pr = Project(make_project(Path(self.tmp.name) / "p.3mf"), presets=StubPresets())

    def tearDown(self):
        self.tmp.cleanup()

    def test_default_targets_every_object_on_the_plate(self):
        planned = plan_changes(self.pr, 2, [("top_shell_layers", "3")])
        self.assertEqual([p.change.object_id for p in planned], ["6", "7"])
        self.assertFalse(any(p.noop for p in planned))

    def test_value_already_in_effect_is_a_noop(self):
        planned = plan_changes(self.pr, 1, [("sparse_infill_density", "12%"), ("top_shell_layers", "5")])
        self.assertEqual([p.noop for p in planned], [True, True])
        planned = plan_changes(self.pr, 1, [("bridge_speed", "50")])
        self.assertTrue(planned[0].noop)

    def test_system_default_is_labelled(self):
        planned = plan_changes(self.pr, 2, [("top_shell_layers", "3")])
        self.assertEqual(planned[0].before, "5 (print-wide = system default)")
        planned = plan_changes(self.pr, 1, [("sparse_infill_density", "10%")])
        self.assertEqual(planned[0].before, "12% (object)")

    def test_part_level_value_is_flagged(self):
        planned = plan_changes(self.pr, 1, [("small_perimeter_speed", "80%")])
        self.assertFalse(planned[0].noop)
        self.assertIn("part-level value overrides", planned[0].before)

    def test_print_wide_and_unknown_keys(self):
        planned = plan_changes(self.pr, 1, [("top_shell_layers", "3")], print_wide=True)
        self.assertEqual(planned[0].change, Change("top_shell_layers", "3", None))
        with self.assertRaises(ProjectError):
            plan_changes(self.pr, 1, [("top_shel_layers", "3")])
        with self.assertRaises(ProjectError):
            plan_changes(self.pr, 9, [("top_shell_layers", "3")])

    def test_object_by_name(self):
        planned = plan_changes(self.pr, 2, [("top_shell_layers", "3")], object_refs=["rail.stl"])
        self.assertEqual([p.change.object_id for p in planned], ["6", "7"])


if __name__ == "__main__":
    unittest.main()
