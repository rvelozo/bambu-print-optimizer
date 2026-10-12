import json
import math
import tempfile
import unittest
from pathlib import Path

from slicer_optimizer import gcode, layers, section

from .test_gcode import HEADER


def square_part(tmp: Path) -> tuple[Path, dict]:
    """Two layers: a 20 x 20 mm square loop (0.4 mm walls) and a diagonal line inside."""
    body = ["M83"]
    for i, z in enumerate((0.2, 0.4), start=1):
        body += ["; CHANGE_LAYER", f"; Z_HEIGHT: {z}", "; LAYER_HEIGHT: 0.2", "; FEATURE: Outer wall", "; LINE_WIDTH: 0.4",
                 f"G1 X0 Y0 Z{z} F6000", "G1 X20 Y0 E1", "G1 X20 Y20 E1", "G1 X0 Y20 E1", "G1 X0 Y0 E1",
                 "; FEATURE: Sparse infill", "; LINE_WIDTH: 0.4", "G1 X5 Y5", "G1 X15 Y15 E0.5"]
    gpath = tmp / "plate_1.gcode"
    gpath.write_text(HEADER + "\n".join(body) + "\n")
    import gzip
    import shutil
    with open(gpath, "rb") as fi, gzip.open(tmp / "plate_1.gcode.gz", "wb") as fo:
        shutil.copyfileobj(fi, fo)
    return tmp, layers.load_or_build(tmp, 1)


class SectionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder, self.an = square_part(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_cut_through_walls_and_diagonal(self):
        sec = section.cut(self.folder, 1, self.an, "x", 10.0)
        self.assertEqual(len(sec["layers"]), 2)
        spans = sec["layers"][0]["spans"]
        feats = [sec["features"][f] for _, _, f in spans]
        self.assertEqual(feats, ["Outer wall", "Sparse infill", "Outer wall"])
        a, b, _ = spans[0]
        self.assertAlmostEqual(b - a, 0.4, places=3)          # wall crossed at right angles: its width
        a, b, _ = spans[1]
        self.assertAlmostEqual(b - a, 0.4 * math.sqrt(2), places=3)  # 45° bead: width / cos 45°
        self.assertAlmostEqual((a + b) / 2, 10.0, places=3)

    def test_gaps_and_properties(self):
        sec = section.cut(self.folder, 1, self.an, "x", 10.0)
        g = [x for x in section.gaps(sec) if x["index"] == 1]
        self.assertEqual(len(g), 2)
        self.assertAlmostEqual(g[0]["width"], 10 - 0.2 - 0.2 * math.sqrt(2), places=3)
        expected_area = 2 * 0.2 * (0.4 + 0.4 + 0.4 * math.sqrt(2))
        self.assertAlmostEqual(sec["area_mm2"], expected_area, places=3)
        self.assertAlmostEqual(sec["centroid_z"], 0.2, places=4)

    def test_cut_along_a_wall(self):
        # x = 0 runs along the left wall: the whole wall length is material
        sec = section.cut(self.folder, 1, self.an, "x", 0.0)
        a, b, _ = sec["layers"][0]["spans"][0]
        self.assertAlmostEqual(a, -0.2, places=3)
        self.assertAlmostEqual(b, 20.2, places=3)


if __name__ == "__main__":
    unittest.main()
