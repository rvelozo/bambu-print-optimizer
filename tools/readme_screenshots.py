"""Take the README screenshots with headless Chrome (no packages needed).

Make the demo project and run a separate copy of the app for it, so your own
projects don't show:

    python3 tools/make_demo_project.py /tmp/demo
    SLICER_OPT_DATA=/tmp/demo/data ./slicer-opt serve --port 8790

In that app, drop /tmp/demo/3DBenchy.3mf on the home page and optimize plate 1
(the README's images: Faster "A lot", Lighter "Some", about 10 minutes). Then:

    python3 tools/readme_screenshots.py http://127.0.0.1:8790 PROJECT_ID docs/images

PROJECT_ID is the demo project's id (its page is /p/PROJECT_ID/). Plate 1 should
have a finished optimization. Images are written as PNG at twice the CSS
size, in the dark theme. Set CHROME to Chrome's executable if it isn't in
/Applications.
"""

from __future__ import annotations

import base64
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

CHROME = os.environ.get("CHROME", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
PORT = 9333


class Tab:
    """A minimal Chrome DevTools Protocol client over a websocket (stdlib only)."""

    def __init__(self, ws_url: str):
        host_port, path = ws_url[len("ws://"):].split("/", 1)
        host, port = host_port.split(":")
        self.sock = socket.create_connection((host, int(port)))
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((f"GET /{path} HTTP/1.1\r\nHost: {host_port}\r\nUpgrade: websocket\r\n"
                           f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        response = b""
        while b"\r\n\r\n" not in response:
            response += self.sock.recv(1)
        if b" 101 " not in response.split(b"\r\n")[0]:
            raise RuntimeError(f"websocket handshake failed: {response[:200]!r}")
        self.next_id = 0

    def _recv_exact(self, n: int) -> bytes:
        data = b""
        while len(data) < n:
            chunk = self.sock.recv(n - len(data))
            if not chunk:
                raise ConnectionError("Chrome closed the connection")
            data += chunk
        return data

    def _recv_message(self) -> str:
        parts = []
        while True:
            b0, b1 = self._recv_exact(2)
            length = b1 & 0x7F
            if length == 126:
                length = int.from_bytes(self._recv_exact(2), "big")
            elif length == 127:
                length = int.from_bytes(self._recv_exact(8), "big")
            payload = self._recv_exact(length)
            opcode = b0 & 0x0F
            if opcode == 9:  # ping
                self._send_frame(payload, opcode=10)
                continue
            parts.append(payload)
            if b0 & 0x80:
                return b"".join(parts).decode()

    def _send_frame(self, data: bytes, opcode: int = 1) -> None:
        mask = os.urandom(4)
        n = len(data)
        header = bytes([0x80 | opcode])
        if n < 126:
            header += bytes([0x80 | n])
        elif n < 65536:
            header += bytes([0x80 | 126]) + n.to_bytes(2, "big")
        else:
            header += bytes([0x80 | 127]) + n.to_bytes(8, "big")
        self.sock.sendall(header + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def call(self, method: str, **params):
        self.next_id += 1
        self._send_frame(json.dumps({"id": self.next_id, "method": method, "params": params}).encode())
        while True:
            msg = json.loads(self._recv_message())
            if msg.get("id") == self.next_id:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})

    def js(self, expression: str):
        r = self.call("Runtime.evaluate", expression=expression, awaitPromise=True, returnByValue=True)
        if "exceptionDetails" in r:
            raise RuntimeError(f"page script failed: {r['exceptionDetails'].get('exception', {}).get('description')}")
        return r.get("result", {}).get("value")

    def wait_for(self, expression: str, timeout: float = 60) -> None:
        t0 = time.time()
        while not self.js(expression):
            if time.time() - t0 > timeout:
                raise TimeoutError(f"page never satisfied: {expression}")
            time.sleep(0.25)

    def shot(self, path: Path, selector: str | None = None, pad: int = 0) -> None:
        clip = None
        if selector:
            r = self.js(f"(() => {{ const r = document.querySelector({json.dumps(selector)}).getBoundingClientRect();"
                        f" return [r.left + scrollX, r.top + scrollY, r.width, r.height]; }})()")
            clip = {"x": max(0, r[0] - pad), "y": max(0, r[1] - pad), "width": r[2] + 2 * pad, "height": r[3] + 2 * pad, "scale": 1}
        params = {"format": "png", "captureBeyondViewport": True}
        if clip:
            params["clip"] = clip
        data = self.call("Page.captureScreenshot", **params)["data"]
        path.write_bytes(base64.b64decode(data))
        print(f"wrote {path} ({path.stat().st_size // 1024} KB)")


def open_tab(width: int, height: int) -> tuple[subprocess.Popen, Tab]:
    profile = tempfile.mkdtemp(prefix="readme-shots-")
    chrome = subprocess.Popen([CHROME, "--headless=new", f"--remote-debugging-port={PORT}", f"--user-data-dir={profile}",
                               "--hide-scrollbars", "--no-first-run", "--no-default-browser-check", "about:blank"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(60):
        try:
            pages = json.load(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/list", timeout=1))
            page = next(p for p in pages if p.get("type") == "page")
            break
        except Exception:
            time.sleep(0.25)
    else:
        chrome.kill()
        raise RuntimeError("headless Chrome didn't start")
    tab = Tab(page["webSocketDebuggerUrl"])
    tab.call("Emulation.setDeviceMetricsOverride", width=width, height=height, deviceScaleFactor=2, mobile=False)
    tab.call("Emulation.setEmulatedMedia", features=[{"name": "prefers-color-scheme", "value": "dark"}])
    return chrome, tab


def goto(tab: Tab, url: str) -> None:
    tab.call("Page.enable")
    tab.call("Page.navigate", url=url)
    tab.wait_for("document.readyState === 'complete' && !!window.slicerApp")
    tab.js("document.fonts.ready.then(() => true)")


def frame(tab: Tab, selectors: list[str], bottom_of: list[str] | None = None) -> None:
    """An invisible box around several elements, to capture them as one image."""
    tab.js(f"""(() => {{
        const rect = (q) => document.querySelector(q).getBoundingClientRect();
        const rs = {json.dumps(selectors)}.map(rect);
        const bottoms = {json.dumps(bottom_of or [])}.map(rect).map((r) => r.bottom);
        const left = Math.min(...rs.map((r) => r.left)), top = Math.min(...rs.map((r) => r.top));
        const right = Math.max(...rs.map((r) => r.right));
        const bottom = bottoms.length ? Math.max(...bottoms) : Math.max(...rs.map((r) => r.bottom));
        document.getElementById('shot-frame')?.remove();
        const box = document.createElement('div');
        box.id = 'shot-frame';
        Object.assign(box.style, {{position: 'absolute', pointerEvents: 'none', left: left + scrollX + 'px',
          top: top + scrollY + 'px', width: right - left + 'px', height: bottom - top + 'px'}});
        document.body.appendChild(box);
        return true; }})()""")


def main(base: str, project: str, out_dir: str) -> None:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    page = f"{base}/p/{project}/"
    chrome, tab = open_tab(1360, 940)
    try:
        # 0. The home page: open a project, recent projects
        tab.call("Page.enable")
        tab.call("Page.navigate", url=f"{base}/")
        tab.wait_for("document.readyState === 'complete' && !!document.querySelector('#recent .proj img')")
        tab.js("document.fonts.ready.then(() => true)")
        time.sleep(0.5)
        tab.shot(out / "home.png", ".home", pad=0)

        # 1. Optimize: goals on the left, the recommendation on the right
        goto(tab, page)
        tab.wait_for("!!document.querySelector('.opt-card') && !!document.querySelector('.opt-alts')")
        time.sleep(0.5)
        frame(tab, [".opt"], bottom_of=[".opt-form .d-actions", ".opt-alts"])
        tab.shot(out / "optimize.png", "#shot-frame", pad=12)

        # 2. What each setting did on its own
        tab.shot(out / "settings-effects.png", ".opt-table", pad=12)

        # 3. Compare the recommendation with the current settings, layer by layer
        tab.js("document.getElementById('opt-compare').click(); true")
        tab.wait_for("window.slicerApp.state.tab === 'layers' && !!window.slicerApp.state.B")
        layer = tab.js("""(async () => {  // where A and B differ most in plastic laid down
            const app = window.slicerApp;
            const [a, b] = await Promise.all([app.analysisOf(app.runById(app.state.A)), app.analysisOf(app.runById(app.state.B))]);
            let best = 1, most = -1;
            const n = Math.min(a.layers.length, b.layers.length);
            for (let i = Math.round(n * 0.1); i < Math.round(n * 0.9); i++) {
              const d = Math.abs(a.layers[i].volume_mm3 - b.layers[i].volume_mm3);
              if (d > most) { most = d; best = i; }
            }
            return best;
        })()""")
        tab.js(f"window.slicerApp.state.layer = {layer}; window.slicerApp.refreshView(); true")
        time.sleep(2.0)
        tab.shot(out / "compare.png", ".main", pad=0)

        # 4. Try a change: settings arranged like Bambu Studio's Process tab
        tab.call("Emulation.setDeviceMetricsOverride", width=1360, height=1500, deviceScaleFactor=2, mobile=False)
        goto(tab, page)
        tab.js("document.querySelector('.main').style.visibility = 'hidden'; true")  # only the form and the dropdown
        tab.js("document.querySelector('.pick-setting').click(); true")
        tab.wait_for("!!document.querySelector('.sp-row')")
        tab.js("[...document.querySelectorAll('.sp-tabs button')].find(b => b.textContent === 'Strength').click(); true")
        time.sleep(0.4)
        frame(tab, [".try", ".sp"])
        tab.shot(out / "setting-picker.png", "#shot-frame", pad=12)
    finally:
        chrome.kill()


if __name__ == "__main__":
    main(*sys.argv[1:4])
