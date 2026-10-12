"""Where Bambu Studio and the app's own files live, per operating system.

macOS works out of the box with Bambu Studio in /Applications. On Windows and
Linux, tell the app where Bambu Studio is with environment variables:

    BAMBU_STUDIO_CLI       the Bambu Studio executable (the one that takes --slice)
    BAMBU_STUDIO_PROFILES  its resources/profiles folder (Bambu's system presets)
    BAMBU_STUDIO_VERSION   its version, for example 02.08.04.57 (macOS reads it
                           from the app; slices are cached per version)

The app's own folders can be moved with SLICER_OPT_DATA, SLICER_OPT_CACHE and
SLICER_OPT_LOG. Defaults:

    macOS    ~/Library/Application Support, ~/Library/Caches, ~/Library/Logs
    Linux    $XDG_DATA_HOME, $XDG_CACHE_HOME, $XDG_STATE_HOME (or ~/.local/share, ~/.cache, ~/.local/state)
    Windows  %LOCALAPPDATA%\\slicer-optimizer\\{data, cache, logs}
"""

from __future__ import annotations

import os
import plistlib
import sys
from pathlib import Path

NAME = "slicer-optimizer"
MAC = sys.platform == "darwin"
WINDOWS = sys.platform.startswith("win")


def _env(name: str) -> Path | None:
    v = os.environ.get(name)
    return Path(os.path.expanduser(v)) if v else None


def _local_appdata() -> Path:
    return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / NAME


def data_dir() -> Path:
    if (p := _env("SLICER_OPT_DATA")) is not None:
        return p
    if MAC:
        return Path.home() / "Library" / "Application Support" / NAME
    if WINDOWS:
        return _local_appdata() / "data"
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / NAME


def cache_dir() -> Path:
    if (p := _env("SLICER_OPT_CACHE")) is not None:
        return p
    if MAC:
        return Path.home() / "Library" / "Caches" / NAME
    if WINDOWS:
        return _local_appdata() / "cache"
    return Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / NAME


def log_file() -> Path:
    if (p := _env("SLICER_OPT_LOG")) is not None:
        return p
    if MAC:
        return Path.home() / "Library" / "Logs" / f"{NAME}.log"
    if WINDOWS:
        return _local_appdata() / "logs" / f"{NAME}.log"
    return Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state") / NAME / f"{NAME}.log"


# ---- Bambu Studio ----------------------------------------------------------------------

def studio_app() -> Path:
    """The macOS app bundle (BAMBU_STUDIO_APP overrides /Applications/BambuStudio.app)."""
    return _env("BAMBU_STUDIO_APP") or Path("/Applications/BambuStudio.app")


def studio_cli() -> Path:
    """The executable that slices: BAMBU_STUDIO_CLI, or the one inside the macOS app."""
    if (p := _env("BAMBU_STUDIO_CLI")) is not None:
        return p
    return studio_app() / "Contents" / "MacOS" / "BambuStudio"


def studio_profiles() -> Path:
    """Bambu's system presets: BAMBU_STUDIO_PROFILES, or the folder inside the macOS app."""
    if (p := _env("BAMBU_STUDIO_PROFILES")) is not None:
        return p
    return studio_app() / "Contents" / "Resources" / "profiles"


def studio_version() -> str | None:
    """BAMBU_STUDIO_VERSION, or the macOS app's version; None if neither is known."""
    if v := os.environ.get("BAMBU_STUDIO_VERSION"):
        return v.strip()
    try:
        with open(studio_app() / "Contents" / "Info.plist", "rb") as f:
            return plistlib.load(f).get("CFBundleShortVersionString") or None
    except OSError:
        return None


def setup_hint() -> str:
    if MAC:
        return "install Bambu Studio in /Applications, or set BAMBU_STUDIO_APP to where it is"
    return "set BAMBU_STUDIO_CLI, BAMBU_STUDIO_PROFILES and BAMBU_STUDIO_VERSION (see the README)"


# ---- one app per data folder -----------------------------------------------------------

def try_lock(fd: int, exclusive: bool = True) -> bool:
    """Lock an open file without waiting; False if another process holds it."""
    if WINDOWS:  # untested: no shared locks, and locked bytes can't be read, so lock one far past the content
        import msvcrt
        try:
            os.lseek(fd, 1 << 30, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False
        finally:
            os.lseek(fd, 0, os.SEEK_SET)
    import fcntl
    try:
        fcntl.flock(fd, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
        return True
    except BlockingIOError:
        return False
