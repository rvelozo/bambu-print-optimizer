"""The print log: what really happened when a sliced file was printed.

Each entry links a print to the run that was sliced (its estimate) and records
what the printer and a scale said: actual time, actual weight, the outcome,
any issues, and whether it was a real part or a test piece (an adhesion test,
a coupon). The calibration turns finished prints into a correction per printer
and filament: "your prints take 4% longer than Bambu's estimate (3 prints,
+2% to +6%)". Only real entries count; nothing here is estimated.
"""

from __future__ import annotations

import json
import re
import statistics
import threading
import time
import uuid
from pathlib import Path

ISSUES = ["adhesion", "warping", "stringing", "under-extrusion", "over-extrusion", "layer shift",
          "fit too tight", "fit too loose", "bridges sag", "surface", "supports needed", "jam"]
OUTCOMES = ["ok", "partial", "failed"]


def parse_duration(text: str | float | int) -> float:
    """'2h10m', '2h 10m 30s', '2:10', '2:10:30', '130m', '7800s' or plain seconds."""
    if isinstance(text, (int, float)):
        return float(text)
    s = str(text).strip().lower().replace(" ", "")
    if not s:
        raise ValueError("empty duration")
    if ":" in s:
        parts = [float(p) for p in s.split(":")]
        if len(parts) == 2:
            return parts[0] * 3600 + parts[1] * 60
        if len(parts) == 3:
            return parts[0] * 3600 + parts[1] * 60 + parts[2]
        raise ValueError(f"can't read {text!r} as a duration")
    m = re.fullmatch(r"(?:(\d+(?:\.\d+)?)h)?(?:(\d+(?:\.\d+)?)m(?:in)?)?(?:(\d+(?:\.\d+)?)s)?", s)
    if m and any(m.groups()):
        h, mi, sec = (float(x) if x else 0.0 for x in m.groups())
        return h * 3600 + mi * 60 + sec
    try:
        return float(s)
    except ValueError:
        raise ValueError(f"can't read {text!r} as a duration (try 2h10m)") from None


class PrintLog:
    def __init__(self, folder: Path):
        self.file = Path(folder) / "prints.json"
        self.lock = threading.RLock()
        self.items: list[dict] = json.loads(self.file.read_text())["prints"] if self.file.exists() else []

    def save(self) -> None:
        with self.lock:
            self.file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.file.with_suffix(".tmp")
            tmp.write_text(json.dumps({"prints": self.items}, indent=1))
            tmp.replace(self.file)

    def add(self, entry: dict) -> dict:
        outcome = entry.get("outcome", "ok")
        if outcome not in OUTCOMES:
            raise ValueError(f"outcome must be one of {', '.join(OUTCOMES)}")
        issues = [i for i in entry.get("issues", []) if i]
        actual_s = parse_duration(entry["actual_s"]) if entry.get("actual_s") not in (None, "") else None
        actual_g = float(entry["actual_g"]) if entry.get("actual_g") not in (None, "") else None
        if actual_s is not None and actual_s <= 0:
            raise ValueError("actual time must be positive")
        item = {
            "id": uuid.uuid4().hex[:10], "created": time.time(),
            "date": entry.get("date") or time.strftime("%Y-%m-%d"),
            "kind": entry.get("kind", "part"), "plate": entry.get("plate"), "run_id": entry.get("run_id"),
            "label": entry.get("label", ""), "sha": entry.get("sha"),
            "printer": entry.get("printer"), "filament": entry.get("filament"),
            "predicted_s": entry.get("predicted_s"), "predicted_g": entry.get("predicted_g"),
            "actual_s": actual_s, "actual_g": actual_g,
            "outcome": outcome, "issues": issues, "note": entry.get("note", ""),
        }
        with self.lock:
            self.items.append(item)
            self.save()
        return item

    def remove(self, item_id: str) -> bool:
        with self.lock:
            n = len(self.items)
            self.items = [i for i in self.items if i["id"] != item_id]
            self.save()
            return len(self.items) < n

    def calibration(self) -> list[dict]:
        """Actual vs Bambu's estimate per printer and filament, from finished prints."""
        groups: dict[tuple, dict] = {}
        for p in self.items:
            if p["outcome"] == "failed":
                continue
            key = (p.get("printer"), p.get("filament"))
            g = groups.setdefault(key, {"printer": key[0], "filament": key[1], "time": [], "weight": []})
            if p.get("actual_s") and p.get("predicted_s"):
                g["time"].append(p["actual_s"] / p["predicted_s"])
            if p.get("actual_g") and p.get("predicted_g"):
                g["weight"].append(p["actual_g"] / p["predicted_g"])
        out = []
        for g in groups.values():
            row = {"printer": g["printer"], "filament": g["filament"]}
            for k in ("time", "weight"):
                v = g[k]
                row[k] = None if not v else {"ratio": statistics.median(v), "n": len(v), "lo": min(v), "hi": max(v)}
            if row["time"] or row["weight"]:
                out.append(row)
        return out

    def factor(self, printer: str | None, filament: str | None) -> dict | None:
        """The time calibration for this printer and filament, if any prints were logged."""
        for row in self.calibration():
            if row["printer"] == printer and row["filament"] == filament and row["time"]:
                return row["time"]
        return None
