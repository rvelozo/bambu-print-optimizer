"""Test shapes with known slopes and overhangs, and Bambu projects made from them.

Square frustums: a side that leans out by `a` degrees from vertical overhangs by
layer height x tan(a) per layer, and its stair steps are layer height x sin(a)
high. The arch has a 20 mm bridge. Projects use Bambu's P2S 0.4 nozzle,
0.20mm Standard and Bambu PLA Basic presets (tools/make_demo_project.py does the
same for 3DBenchy).
"""

from __future__ import annotations

import json
import math
import subprocess
from pathlib import Path

from slicer_optimizer import bambu, presets

MACHINE = "Bambu Lab P2S 0.4 nozzle"
PROCESS = "0.20mm Standard @BBL P2S"
FILAMENT = "Bambu PLA Basic @BBL P2S"


def write_stl(path: Path, tris) -> None:
    with open(path, "w") as f:
        f.write("solid s\n")
        for a, b, c in tris:
            u = [b[i] - a[i] for i in range(3)]
            v = [c[i] - a[i] for i in range(3)]
            n = (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0])
            length = math.sqrt(sum(x * x for x in n)) or 1.0
            f.write(f" facet normal {n[0] / length} {n[1] / length} {n[2] / length}\n  outer loop\n")
            for p in (a, b, c):
                f.write(f"   vertex {p[0]} {p[1]} {p[2]}\n")
            f.write("  endloop\n endfacet\n")
        f.write("endsolid s\n")


def frustum(bottom: float, top: float, height: float):
    """Square frustum with half-widths `bottom` and `top`, facets wound outward."""
    b = [(-bottom, -bottom, 0), (bottom, -bottom, 0), (bottom, bottom, 0), (-bottom, bottom, 0)]
    t = [(-top, -top, height), (top, -top, height), (top, top, height), (-top, top, height)]
    tris = [(b[0], b[2], b[1]), (b[0], b[3], b[2]), (t[0], t[1], t[2]), (t[0], t[2], t[3])]
    for i in range(4):
        j = (i + 1) % 4
        tris += [(b[i], b[j], t[j]), (b[i], t[j], t[i])]
    return tris


def leaning(angle: float, height: float, out: bool):
    """Sides leaning `angle` degrees from vertical, outward (an overhang) or inward (a slope)."""
    run = height * math.tan(math.radians(angle))
    return frustum(10, 10 + run, height) if out else frustum(15, 15 - run, height)


def arch():
    """Two 5 mm pillars, a 20 mm span and a 2 mm deck: a bridge over open space."""
    outline = [(0, 0), (5, 0), (5, 8), (25, 8), (25, 0), (30, 0), (30, 10), (0, 10)]
    front = {p: (p[0], 0, p[1]) for p in outline}
    back = {p: (p[0], 20, p[1]) for p in outline}
    caps = [((0, 0), (5, 0), (5, 8)), ((0, 0), (5, 8), (0, 10)), ((5, 8), (25, 8), (30, 10)),
            ((5, 8), (30, 10), (0, 10)), ((25, 8), (25, 0), (30, 0)), ((25, 8), (30, 0), (30, 10))]
    tris = []
    for a, b, c in caps:
        tris += [(front[a], front[c], front[b]), (back[a], back[b], back[c])]
    for i, p in enumerate(outline):
        q = outline[(i + 1) % len(outline)]
        tris += [(front[p], front[q], back[q]), (front[p], back[q], back[p])]
    return tris


def project(name: str, tris, folder: Path) -> Path:
    """A Bambu project with this shape on plate 1, exported by Bambu Studio's command line."""
    folder = folder.resolve()
    stl = folder / f"{name}.stl"
    write_stl(stl, tris)
    files = {}
    for kind, preset in (("machine", MACHINE), ("process", PROCESS), ("filament", FILAMENT)):
        files[kind] = folder / f"{kind}.json"
        files[kind].write_text(json.dumps(presets.standalone(kind, preset)))  # the CLI doesn't follow inherits
    cmd = [str(bambu.cli_path()), "--outputdir", str(folder), "--load-settings", f"{files['machine']};{files['process']}",
           "--load-filaments", str(files["filament"]), "--arrange", "1", "--export-3mf", f"{name}.3mf", str(stl)]
    subprocess.run(cmd, cwd=folder, check=True, capture_output=True)
    return folder / f"{name}.3mf"
