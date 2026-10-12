"""Where things live on each operating system, and the environment overrides."""

import os
import unittest
from pathlib import Path
from unittest import mock

from slicer_optimizer import paths


class PathsTest(unittest.TestCase):
    def setUp(self):
        keys = ["SLICER_OPT_DATA", "SLICER_OPT_CACHE", "SLICER_OPT_LOG", "BAMBU_STUDIO_APP", "BAMBU_STUDIO_CLI",
                "BAMBU_STUDIO_PROFILES", "BAMBU_STUDIO_VERSION", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME",
                "LOCALAPPDATA"]
        self.env = mock.patch.dict(os.environ, {k: "" for k in keys})
        self.env.start()
        for k in keys:
            os.environ.pop(k)

    def tearDown(self):
        self.env.stop()

    def on(self, mac=False, windows=False):
        return mock.patch.multiple(paths, MAC=mac, WINDOWS=windows)

    def test_defaults_per_system(self):
        home = Path.home()
        with self.on(mac=True):
            self.assertEqual(paths.data_dir(), home / "Library/Application Support/slicer-optimizer")
            self.assertEqual(paths.log_file(), home / "Library/Logs/slicer-optimizer.log")
        with self.on():
            self.assertEqual(paths.data_dir(), home / ".local/share/slicer-optimizer")
            self.assertEqual(paths.cache_dir(), home / ".cache/slicer-optimizer")
            os.environ["XDG_CACHE_HOME"] = "/tmp/xdg-cache"
            self.assertEqual(paths.cache_dir(), Path("/tmp/xdg-cache/slicer-optimizer"))
        with self.on(windows=True):
            os.environ["LOCALAPPDATA"] = "C:/Users/me/AppData/Local"
            self.assertEqual(paths.data_dir(), Path("C:/Users/me/AppData/Local/slicer-optimizer/data"))
            self.assertEqual(paths.log_file(), Path("C:/Users/me/AppData/Local/slicer-optimizer/logs/slicer-optimizer.log"))

    def test_overrides_win_everywhere(self):
        os.environ.update(SLICER_OPT_DATA="~/sd", BAMBU_STUDIO_CLI="/opt/bambu/bambu-studio",
                          BAMBU_STUDIO_PROFILES="/opt/bambu/resources/profiles", BAMBU_STUDIO_VERSION=" 02.08.04.57 ")
        for flags in ({"mac": True}, {}, {"windows": True}):
            with self.on(**flags):
                self.assertEqual(paths.data_dir(), Path.home() / "sd")
                self.assertEqual(paths.studio_cli(), Path("/opt/bambu/bambu-studio"))
                self.assertEqual(paths.studio_profiles(), Path("/opt/bambu/resources/profiles"))
                self.assertEqual(paths.studio_version(), "02.08.04.57")

    def test_no_version_without_the_mac_app(self):
        os.environ["BAMBU_STUDIO_APP"] = "/nonexistent/Bambu.app"
        self.assertIsNone(paths.studio_version())
        with self.on():
            self.assertIn("BAMBU_STUDIO_CLI", paths.setup_hint())

    def test_lock_is_exclusive(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "x.lock"
            a = os.open(f, os.O_RDWR | os.O_CREAT)
            b = os.open(f, os.O_RDWR)
            try:
                self.assertTrue(paths.try_lock(a))
                self.assertFalse(paths.try_lock(b))
                self.assertFalse(paths.try_lock(b, exclusive=False))
            finally:
                os.close(a)
                os.close(b)


if __name__ == "__main__":
    unittest.main()
