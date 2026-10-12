import tempfile
import unittest
from pathlib import Path

from slicer_optimizer.prints import PrintLog, parse_duration


class DurationTest(unittest.TestCase):
    def test_formats(self):
        self.assertEqual(parse_duration("2h10m"), 7800)
        self.assertEqual(parse_duration("2h 10m 30s"), 7830)
        self.assertEqual(parse_duration("2:10"), 7800)
        self.assertEqual(parse_duration("2:10:30"), 7830)
        self.assertEqual(parse_duration("130m"), 7800)
        self.assertEqual(parse_duration("45min"), 2700)
        self.assertEqual(parse_duration("7800"), 7800)
        self.assertEqual(parse_duration(90), 90)
        with self.assertRaises(ValueError):
            parse_duration("soon")


class PrintLogTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.log = PrintLog(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def entry(self, predicted, actual, outcome="ok", **kw):
        return self.log.add({"printer": "P2S", "filament": "PLA Lite", "predicted_s": predicted, "actual_s": actual,
                             "predicted_g": 100, "actual_g": kw.pop("grams", 101), "outcome": outcome, **kw})

    def test_calibration_uses_finished_prints_only(self):
        self.entry(3600, "1h02m")       # +3.3 %
        self.entry(7200, "2h06m")       # +5 %
        self.entry(3600, "1h30m", outcome="failed")
        cal = self.log.calibration()
        self.assertEqual(len(cal), 1)
        t = cal[0]["time"]
        self.assertEqual(t["n"], 2)
        self.assertAlmostEqual(t["ratio"], (62 / 60 + 126 / 120) / 2, places=6)
        self.assertAlmostEqual(cal[0]["weight"]["ratio"], 1.01, places=6)
        self.assertEqual(self.log.factor("P2S", "PLA Lite")["n"], 2)
        self.assertIsNone(self.log.factor("P2S", "PETG"))

    def test_persists_and_removes(self):
        e = self.entry(3600, "1h", kind="test", issues=["adhesion", ""], note="front right corner lifted")
        again = PrintLog(Path(self.tmp.name))
        self.assertEqual(again.items[0]["issues"], ["adhesion"])
        self.assertEqual(again.items[0]["kind"], "test")
        self.assertTrue(again.remove(e["id"]))
        self.assertEqual(PrintLog(Path(self.tmp.name)).items, [])

    def test_rejects_bad_input(self):
        with self.assertRaises(ValueError):
            self.entry(3600, "1h", outcome="great")
        with self.assertRaises(ValueError):
            self.entry(3600, "0")


if __name__ == "__main__":
    unittest.main()
