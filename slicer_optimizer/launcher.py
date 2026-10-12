"""Start the app if it isn't running, open projects in it, and show them in the browser.

Used by `slicer-opt open` and by the macOS launcher (macos/make-app.sh), which
runs without a terminal: then problems are shown in a macOS alert.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

from . import paths

LOG = paths.log_file()


class LaunchError(RuntimeError):
    pass


def _request(url: str, payload: dict | None = None, timeout: float = 30):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"} if data else {},
                                 method="POST" if data is not None else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def running(base: str) -> bool:
    """True if this app answers at `base`; LaunchError if something else holds the port."""
    try:
        info = _request(f"{base}/api/health", timeout=2)
    except urllib.error.HTTPError as e:
        raise LaunchError(f"another program is using {base}; start this app with --port") from e
    except (urllib.error.URLError, ConnectionError, TimeoutError, OSError):
        return False
    except ValueError as e:
        raise LaunchError(f"another program is using {base}; start this app with --port") from e
    if info.get("app") != "slicer-optimizer":
        raise LaunchError(f"another program is using {base}; start this app with --port")
    return True


def start_server(port: int) -> None:
    """Start `serve` in the background, logging to paths.log_file()."""
    LOG.parent.mkdir(parents=True, exist_ok=True)
    pkg_root = str(Path(__file__).resolve().parent.parent)
    env = {**os.environ, "PYTHONPATH": pkg_root + (os.pathsep + os.environ["PYTHONPATH"] if os.environ.get("PYTHONPATH") else "")}
    with open(LOG, "ab") as log:
        log.write(f"\n--- {time.strftime('%Y-%m-%d %H:%M:%S')} starting on port {port}\n".encode())
        detach = ({"creationflags": subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP}
                  if paths.WINDOWS else {"start_new_session": True})
        subprocess.Popen([sys.executable, "-m", "slicer_optimizer", "serve", "--port", str(port)],
                         stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                         cwd=str(Path.home()), env=env, **detach)


def ensure_running(port: int, wait_s: float = 20) -> str:
    from .server import running_instance
    other = running_instance()
    if other and other.get("port") and running(f"http://127.0.0.1:{other['port']}"):
        return f"http://127.0.0.1:{other['port']}"  # already running, maybe on another port
    base = f"http://127.0.0.1:{port}"
    if running(base):
        return base
    start_server(port)
    t0 = time.monotonic()
    while time.monotonic() - t0 < wait_s:
        time.sleep(0.25)
        if running(base):
            return base
    raise LaunchError(f"the app didn't start within {wait_s:.0f} s; see {LOG}")


def open_projects(files: list[str], port: int = 8765, browser: bool = True) -> list[str]:
    """Open each file in the app; returns the pages to show (the home page if no file)."""
    base = ensure_running(port)
    pages, problems = [], []
    for f in files:
        try:
            res = _request(f"{base}/api/projects", {"path": str(Path(f).expanduser().resolve())}, timeout=120)
            pages.append(f"{base}/p/{res['id']}/")
        except urllib.error.HTTPError as e:
            try:
                msg = json.loads(e.read()).get("error") or str(e)
            except ValueError:
                msg = str(e)
            problems.append(f"{Path(f).name}: {msg}")
    if not pages:
        pages = [f"{base}/"]
    if browser:
        for page in pages:
            webbrowser.open(page)
    if problems:
        raise LaunchError("\n".join(problems))
    return pages


def alert(message: str) -> None:
    """A macOS alert, for when there's no terminal to print to."""
    if not paths.MAC:
        return
    subprocess.run(["osascript", "-e", "on run argv", "-e",
                    'display alert "Slicer optimizer" message (item 1 of argv) as warning', "-e", "end run", message],
                   capture_output=True)
