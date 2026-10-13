"""The designer's server side: searches over the settings catalog.

A search is a list of candidates; each candidate is an ordinary run (kind
"search"), so slicing, caching, persistence and restart behave like any other
run. Searches themselves are stored next to the runs in searches.json.
"""

from __future__ import annotations

import json
import statistics
import threading
import time
import uuid

from . import search
from .project3mf import ProjectError

MAX_CANDIDATES = 400


class Searches:
    def __init__(self, app):
        self.app = app
        self.file = app.runs.dir / "searches.json"
        self.lock = threading.RLock()
        self.items: dict[str, dict] = {}
        if self.file.exists():
            for item in json.loads(self.file.read_text()).get("searches", []):
                self.items[item["id"]] = item

    def save(self) -> None:
        with self.lock:
            tmp = self.file.with_suffix(".tmp")
            tmp.write_text(json.dumps({"searches": list(self.items.values())}, indent=1))
            tmp.replace(self.file)

    def create(self, plate: int, kind: str, knobs: list[dict], by: str | None = None) -> dict:
        pr = self.app.project
        pr.plate(plate)
        knobs = [{"key": str(k["key"]).strip(), "values": [str(v).strip() for v in k["values"] if str(v).strip()]}
                 for k in knobs]
        search.validate(pr, knobs)
        if kind == "sensitivity":
            assignments = search.sensitivity(knobs)
        elif kind == "grid":
            assignments = search.grid(knobs)
        else:
            raise ProjectError(f"unknown search kind {kind!r}")
        if len(assignments) > MAX_CANDIDATES:
            raise ProjectError(f"{len(assignments)} combinations is too many (limit {MAX_CANDIDATES}); "
                               "use fewer values or measure each setting alone first")
        candidates = []
        now = {k["key"]: search.current_value(pr, plate, k["key"], search.KNOBS.get(k["key"], {}).get("scope", "object"))
               for k in knobs}
        for a in assignments:
            changes = search.changes_for(pr, plate, a)
            run = self.app.runs.add(plate, changes, None, "search" if changes else "baseline")  # named by its changes
            if run["status"] in ("queued", "error", "cancelled"):
                self.app.enqueue(run["id"], 1)
            candidates.append({"assignment": a, "run_id": run["id"]})
        item = {"id": uuid.uuid4().hex[:10], "plate": plate, "kind": kind, "knobs": knobs,
                "created": time.time(), "candidates": candidates, "source_sha": self.app.runs.source_sha}
        if by:
            item["by"] = by  # the optimization that started it
        with self.lock:
            self.items[item["id"]] = item
            self.save()
        return self.view(item)

    def cancel(self, search_id: str) -> dict:
        item = self.items[search_id]
        for c in item["candidates"]:
            run = self.app.runs.runs.get(c["run_id"])
            if run and run["status"] == "queued" and run.get("kind") == "search":
                self.app.runs.update(run["id"], status="cancelled")
        return self.view(item)

    def resume(self, search_id: str) -> dict:
        item = self.items[search_id]
        for c in item["candidates"]:
            run = self.app.runs.runs.get(c["run_id"])
            if run and run["status"] in ("cancelled", "error"):
                self.app.enqueue(run["id"], 1)
        return self.view(item)

    def current(self) -> list[dict]:
        """Searches of the loaded version of the file, newest first."""
        def sha_of(item):
            if item.get("source_sha"):
                return item["source_sha"]
            first = self.app.runs.runs.get(item["candidates"][0]["run_id"]) if item["candidates"] else None
            return first and first["source_sha"]
        with self.lock:
            items = [i for i in self.items.values() if sha_of(i) == self.app.runs.source_sha]
        return sorted(items, key=lambda i: -i["created"])

    def view(self, item: dict) -> dict:
        runs = self.app.runs.runs
        cands, walls = [], []
        counts = {"done": 0, "queued": 0, "error": 0, "cancelled": 0, "active": 0}
        for c in item["candidates"]:
            run = runs.get(c["run_id"]) or {"status": "missing"}
            status = run.get("status")
            if status in ("slicing", "analysing"):
                counts["active"] += 1
            elif status in counts:
                counts[status] += 1
            m = run.get("metrics") or {}
            if status == "done" and m.get("samples") and m.get("wall_s"):
                walls.append(m["wall_s"])
            cands.append({"assignment": c["assignment"], "run_id": c["run_id"], "status": status,
                          "label": run.get("label"), "goals": run.get("goals"),
                          "error": run.get("error"), "pinned": run.get("pinned", False)})
        remaining = counts["queued"] + counts["active"]
        per = statistics.median(walls) if walls else None
        return {**{k: v for k, v in item.items() if k != "candidates"}, "candidates": cands,
                "counts": counts, "total": len(cands), "eta_s": per * remaining if per else None}


def register(app) -> None:
    store = Searches(app)
    app.searches = store

    def get_catalog(h, route, qs):
        plate = int(qs.get("plate", "1"))
        h._json({"knobs": search.catalog_for(app.project, plate), "goals": search.GOALS})

    def get_searches(h, route, qs):
        items = [store.view(i) for i in store.current()]
        if qs.get("plate"):
            items = [i for i in items if i["plate"] == int(qs["plate"])]
        if len(route) > 1:
            item = store.items.get(route[1])
            return h._json(store.view(item)) if item else h._error(404, "no such search")
        h._json({"searches": items})

    def post_searches(h, route, qs):
        if len(route) == 3 and route[2] == "cancel":
            return h._json(store.cancel(route[1]))
        if len(route) == 3 and route[2] == "resume":
            return h._json(store.resume(route[1]))
        body = h._body()
        h._json(store.create(int(body["plate"]), body.get("kind", "grid"), body.get("knobs", [])))

    def delete_searches(h, route, qs):
        if len(route) < 2 or route[1] not in store.items:
            return h._error(404, "no such search")
        store.cancel(route[1])
        with store.lock:
            store.items.pop(route[1])
            store.save()
        h._json({"deleted": route[1]})

    app.extensions.update({"GET catalog": get_catalog, "GET searches": get_searches,
                           "POST searches": post_searches, "DELETE searches": delete_searches})
