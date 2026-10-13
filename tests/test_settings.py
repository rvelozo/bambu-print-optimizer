"""The Process settings layout: extraction from Bambu Studio's source, values, names."""

import importlib.util
import tempfile
import unittest
from pathlib import Path

from slicer_optimizer import settings
from slicer_optimizer.project3mf import Project, ProjectError

from .fixtures import make_project
from .test_project3mf import StubPresets

TOOL = Path(__file__).resolve().parent.parent / "tools" / "extract_settings_layout.py"
spec = importlib.util.spec_from_file_location("extract_settings_layout", TOOL)
extract = importlib.util.module_from_spec(spec)
spec.loader.exec_module(extract)

TAB = '''
void TabPrint::build()
{
    auto page = add_options_page(L("Quality"), "empty");
        auto optgroup = page->new_optgroup(L("Layer height"), L"param_layer_height");
        optgroup->append_single_option_line("layer_height", "layer-height");
        // optgroup->append_single_option_line("commented_out");
    page = add_options_page(L("Speed"), "empty");
        optgroup = page->new_optgroup(L("Other layers speed"), L"param_speed", 15);
        optgroup->append_single_option_line("bridge_speed");
        Line line = { L("Overhang speed"), L("tooltip") };
        line.append_option(optgroup->get_option("overhang_1_4_speed", 0));
        optgroup->append_line(line);
#if 0
        optgroup->append_single_option_line("disabled");
#endif
        optgroup = page->new_optgroup(L("Notes"), "note");
        optgroup->append_single_option_line("process_notes");
}

void TabPrint::reload_config()
{
}
'''

CONFIG = '''
    def = this->add("layer_height", coFloat);
    def->label = L("Layer height");
    def->tooltip = L("Slicing height for each layer. "
                     "Smaller layer height means more accurate");
    def->sidetext = L("mm");
    def->min = 0;
    def = this->add("bridge_speed", coFloats);
    def->label = L("Bridge");
    def->sidetext = L("mm/s");
    def = this->add("overhang_1_4_speed", coFloats);
    def->label = "10%";
    //def->tooltip = L("ignored");
    def->sidetext = L("mm/s");
    auto def_top_fill_pattern = def = this->add("top_surface_pattern", coEnum);
    def->label = L("Top surface pattern");
    def->enum_values.push_back("monotonic");
    def->enum_values.emplace_back("zig-zag");
    def->enum_labels.push_back(L("Monotonic"));
    def->enum_labels.emplace_back(L("Rectilinear"));
    def = this->add("bottom_surface_pattern", coEnum);
    def->label = L("Bottom surface pattern");
    def->enum_values = def_top_fill_pattern->enum_values;
    def->enum_labels = def_top_fill_pattern->enum_labels;
'''


class Extractor(unittest.TestCase):
    def test_layout_follows_the_tab_code(self):
        pages = extract.layout(TAB)
        self.assertEqual([p["title"] for p in pages], ["Quality", "Speed"])
        self.assertEqual(pages[0]["groups"][0]["options"], [{"key": "layer_height"}])
        speed = pages[1]["groups"]
        self.assertEqual([g["title"] for g in speed], ["Other layers speed"])  # Notes: free text, left out
        self.assertEqual(speed[0]["options"], [{"key": "bridge_speed"}, {"key": "overhang_1_4_speed", "line": "Overhang speed"}])

    def test_definitions(self):
        d = extract.definitions(CONFIG)
        self.assertEqual(d["layer_height"]["tooltip"], "Slicing height for each layer. Smaller layer height means more accurate")
        self.assertEqual((d["layer_height"]["sidetext"], d["layer_height"]["min"]), ("mm", 0.0))
        self.assertEqual(d["overhang_1_4_speed"]["label"], "10%")
        self.assertNotIn("tooltip", d["overhang_1_4_speed"])
        self.assertEqual(d["top_surface_pattern"]["choices"], [["monotonic", "Monotonic"], ["zig-zag", "Rectilinear"]])
        self.assertEqual(d["bottom_surface_pattern"]["choices"], d["top_surface_pattern"]["choices"])


class Values(unittest.TestCase):
    def test_choices_bools_numbers(self):
        self.assertEqual(settings.check_value("sparse_infill_pattern", "Gyroid"), "gyroid")
        self.assertEqual(settings.check_value("enable_support", "on"), "1")
        self.assertEqual(settings.check_value("sparse_infill_density", "10"), "10%")
        self.assertEqual(settings.check_value("top_shell_layers", " 3 "), "3")
        for key, value, message in [("sparse_infill_pattern", "stars", "Sparse infill pattern: choose one of"),
                                    ("top_shell_layers", "3.5", "Top shell layers: a whole number"),
                                    ("overhang_3_4_speed", "fast", "Overhang speed 50%: a number"),
                                    ("wall_loops", "-1", "Wall loops: at least 0")]:
            with self.assertRaisesRegex(ProjectError, message):
                settings.check_value(key, value)

    def test_names(self):
        self.assertEqual(settings.display_name("outer_wall_speed"), "Outer wall (Other layers speed)")
        self.assertEqual(settings.display_name("bridge_speed"), "Bridge")
        self.assertEqual(settings.display_name("filament_max_volumetric_speed"), "Filament flow cap")
        label = settings.change_label([
            {"key": "bridge_speed", "value": "100", "object_id": "4"}, {"key": "bridge_speed", "value": "100", "object_id": "6"},
            {"key": "sparse_infill_pattern", "value": "gyroid", "object_id": "4"},
            {"key": "small_perimeter_speed", "value": "50%", "object_id": None}])
        self.assertEqual(label, "Bridge: 100 mm/s, Sparse infill pattern: Gyroid, Small perimeters: 50% (whole project)")
        self.assertEqual(settings.change_label([]), "Current settings")


class Picker(unittest.TestCase):
    def test_only_the_projects_settings_with_values(self):
        with tempfile.TemporaryDirectory() as d:
            pr = Project(make_project(Path(d) / "p.3mf"), presets=StubPresets())
            data = settings.picker(pr, 1)
            rows = {s["key"]: s for p in data["pages"] for g in p["groups"] for s in g["settings"]}
            self.assertEqual(set(rows), {k for k in rows if pr.has_key(k)})
            self.assertEqual((rows["sparse_infill_density"]["now"], rows["sparse_infill_density"]["source"]), ("12%", "object"))
            self.assertEqual((rows["bridge_speed"]["now"], rows["bridge_speed"]["source"]), ("50", "print-wide"))
            self.assertEqual(rows["bridge_speed"]["unit"], "mm/s")


if __name__ == "__main__":
    unittest.main()
