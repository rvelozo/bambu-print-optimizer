import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from slicer_optimizer.project3mf import MODEL_SETTINGS, PROJECT_SETTINGS, Change, Project, ProjectError

from .fixtures import make_project


def entries(data_or_path):
    src = io.BytesIO(data_or_path) if isinstance(data_or_path, bytes) else data_or_path
    with zipfile.ZipFile(src) as z:
        return {i.filename: (i.CRC, i.compress_type, i.date_time, z.read(i.filename)) for i in z.infolist()}


class StubPresets:
    """Stands in for the app's system presets so the tests don't need Bambu Studio."""

    process = {"bridge_speed": ["50", "50", "50"], "top_shell_layers": "5", "enable_arc_fitting": "1",
               "sparse_infill_density": "15%", "enable_support": "0"}

    def group(self, key):
        return "process" if key in self.process else ("filament" if key.startswith("filament_") else None)

    def process_default(self, key):
        return self.process.get(key)


class ProjectTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = make_project(Path(self.tmp.name) / "p.3mf")
        self.pr = Project(self.path, presets=StubPresets())

    def tearDown(self):
        self.tmp.cleanup()

    def test_reads_objects_plates_and_overrides(self):
        self.assertEqual([o.id for o in self.pr.objects], ["6", "4", "7"])
        self.assertEqual([p.index for p in self.pr.plates], [1, 2])
        self.assertEqual([o.id for o in self.pr.objects_on_plate(2)], ["6", "7"])
        lid = self.pr.object("4")
        self.assertEqual(lid.overrides, {"sparse_infill_density": "12%"})
        self.assertEqual(lid.parts[0].overrides, {"small_perimeter_speed": "80%,nil,nil"})
        self.assertEqual([o.id for o in self.pr.find_objects("rail.stl")], ["6", "7"])
        self.assertEqual(self.pr.plate_of("7"), 2)
        self.assertEqual(self.pr.modified_vs_system(), {"process": ["enable_arc_fitting"]})

    def test_effective_value_and_source(self):
        lid = self.pr.object("4")
        self.assertEqual(self.pr.effective("sparse_infill_density", lid), ("12%", "object"))
        self.assertEqual(self.pr.effective("top_shell_layers", lid), ("5", "print-wide"))
        self.assertEqual(self.pr.effective("small_perimeter_speed", lid), ("80%,nil,nil", "part"))

    def test_no_changes_keeps_every_entry_identical(self):
        before, after = entries(self.path), entries(self.pr.build([]))
        self.assertEqual(before, after)

    def test_object_override_insert_is_alphabetical_and_isolated(self):
        data = self.pr.build([Change("bridge_speed", "100", "4"), Change("top_shell_layers", "3", "4")])
        before, after = entries(self.path), entries(data)
        for name in before:
            if name != MODEL_SETTINGS:
                self.assertEqual(before[name], after[name], name)
        xml = after[MODEL_SETTINGS][3].decode()
        lid_block = xml.split('<object id="4">')[1].split("<part")[0]
        keys = [line.split('key="')[1].split('"')[0] for line in lid_block.splitlines() if 'key="' in line]
        self.assertEqual(keys, ["name", "bridge_speed", "extruder", "sparse_infill_density", "top_shell_layers"])
        rail_block = xml.split('<object id="6">')[1].split("</object>")[0]
        self.assertNotIn("bridge_speed", rail_block)
        p2 = Project(self._write(data))
        self.assertEqual(p2.object("4").overrides["bridge_speed"], "100")
        self.assertEqual(p2.object("4").parts[0].overrides, {"small_perimeter_speed": "80%,nil,nil"})

    def test_replace_and_remove_override(self):
        p2 = Project(self._write(self.pr.build([Change("sparse_infill_density", "10%", "4")])))
        self.assertEqual(p2.object("4").overrides["sparse_infill_density"], "10%")
        p3 = Project(self._write(p2.build([Change("sparse_infill_density", None, "4")])))
        self.assertNotIn("sparse_infill_density", p3.object("4").overrides)
        # removing an override that isn't there changes nothing
        self.assertEqual(entries(p3.build([Change("sparse_infill_density", None, "4")])), entries(p3.path))

    def test_value_is_escaped(self):
        p2 = Project(self._write(self.pr.build([Change("sparse_infill_density", 'a"<&>', "4")])))
        self.assertEqual(p2.object("4").overrides["sparse_infill_density"], 'a"<&>')

    def test_print_wide_scalar_and_list(self):
        data = self.pr.build([Change("top_shell_layers", "3"), Change("bridge_speed", "100")])
        cfg = json.loads(entries(data)[PROJECT_SETTINGS][3])
        self.assertEqual(cfg["top_shell_layers"], "3")
        self.assertEqual(cfg["bridge_speed"], ["100", "100", "100"])
        self.assertEqual(cfg["different_settings_to_system"][0], "bridge_speed;enable_arc_fitting;top_shell_layers")
        self.assertEqual(self.pr.notes, [])
        untouched = {k: v for k, v in cfg.items() if k not in ("top_shell_layers", "bridge_speed", "different_settings_to_system")}
        orig = {k: v for k, v in self.pr.print_settings.items() if k in untouched}
        self.assertEqual(untouched, orig)
        self.assertEqual(entries(data)[MODEL_SETTINGS], entries(self.path)[MODEL_SETTINGS])

    def test_change_record_follows_system_defaults(self):
        changed = Project(self._write(self.pr.build([Change("top_shell_layers", "3")])), presets=StubPresets())
        back = json.loads(entries(changed.build([Change("top_shell_layers", "5")]))[PROJECT_SETTINGS][3])
        self.assertEqual(back["different_settings_to_system"][0], "enable_arc_fitting")
        self.pr.build([Change("filament_max_volumetric_speed", "20")])
        self.assertEqual(len(self.pr.notes), 1)
        self.assertTrue(self.pr.is_system_default("top_shell_layers", "5"))
        self.assertFalse(self.pr.is_system_default("top_shell_layers", "3"))
        self.assertIsNone(self.pr.is_system_default("filament_max_volumetric_speed", "16"))

    def test_build_is_deterministic(self):
        ch = [Change("bridge_speed", "100", "4"), Change("top_shell_layers", "3")]
        self.assertEqual(self.pr.build(ch), self.pr.build(ch))

    def test_refuses_bad_requests(self):
        with self.assertRaises(ProjectError):
            self.pr.build([Change("bridge_speed", "100", "99")])
        with self.assertRaises(ProjectError):
            self.pr.build([Change("name", "x", "4")])
        with self.assertRaises(ProjectError):
            self.pr.build([Change("no_such_key", "1")])
        with self.assertRaises(ProjectError):
            self.pr.save(self.path, [Change("bridge_speed", "100", "4")])
        other = Path(self.tmp.name) / "other.3mf"
        other.write_bytes(b"x")
        with self.assertRaises(ProjectError):
            self.pr.save(other, [])
        self.pr.save(other, [], overwrite=True)
        self.assertEqual(entries(other), entries(self.path))

    def _write(self, data: bytes) -> Path:
        p = Path(self.tmp.name) / f"v{len(list(Path(self.tmp.name).iterdir()))}.3mf"
        p.write_bytes(data)
        return p


class OneLineModelSettingsTest(unittest.TestCase):
    """model_settings.config written on one line, as some generators do (custom-...-ge-circle-15cm.3mf)."""

    XML = ('<?xml version="1.0" encoding="UTF-8"?>\n<config><object id="10"><metadata key="name" value="Terrain"/>'
           '<part id="2" subtype="normal_part"><metadata key="name" value="Terrain"/><metadata key="extruder" value="1"/></part>'
           '</object><object id="8"><metadata key="name" value="Trail lines"/><metadata key="extruder" value="4"/></object>'
           '<plate><metadata key="plater_id" value="1"/><model_instance><metadata key="object_id" value="10"/></model_instance>'
           '<model_instance><metadata key="object_id" value="8"/></model_instance></plate></config>')

    def test_insert_replace_remove_on_one_line(self):
        from slicer_optimizer.project3mf import _parse_model, _set_object_setting
        x = _set_object_setting(self.XML, "8", "outer_wall_speed", "60")
        self.assertIn('<metadata key="extruder" value="4"/><metadata key="outer_wall_speed" value="60"/></object>', x)
        x = _set_object_setting(x, "8", "bridge_speed", "100")  # alphabetical: before extruder
        self.assertIn('value="Trail lines"/><metadata key="bridge_speed" value="100"/><metadata key="extruder"', x)
        x = _set_object_setting(x, "10", "outer_wall_speed", "70")  # object metadata, not the part's
        self.assertIn('value="Terrain"/><metadata key="outer_wall_speed" value="70"/><part id="2"', x)
        x = _set_object_setting(x, "8", "outer_wall_speed", "65")
        x = _set_object_setting(x, "8", "bridge_speed", None)
        objs = {o.id: o for o in _parse_model(x)[0]}
        self.assertEqual(objs["8"].overrides, {"outer_wall_speed": "65"})
        self.assertEqual(objs["10"].overrides, {"outer_wall_speed": "70"})
        self.assertEqual(objs["10"].parts[0].overrides, {})
        self.assertNotIn("\n<metadata", x)  # still one line


if __name__ == "__main__":
    unittest.main()
