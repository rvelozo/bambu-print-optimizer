"""Make the demo project used for the README screenshots: 3DBenchy on a Bambu Lab P2S.

3DBenchy (by Creative Tools) ships with Bambu Studio. This loads it with Bambu's
P2S 0.4 nozzle machine, 0.20mm Standard process and Bambu PLA Basic presets
and exports a .3mf with Bambu Studio's command line:

    python3 tools/make_demo_project.py OUT_FOLDER

The filament preset is resolved through its `inherits` chain first: the CLI
loads a system filament file without its parents, which leaves the density and
cost at 0 (so the weight would read 0 g).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from slicer_optimizer import bambu, paths, presets  # noqa: E402

MACHINE = "Bambu Lab P2S 0.4 nozzle"
PROCESS = "0.20mm Standard @BBL P2S"
FILAMENT = "Bambu PLA Basic @BBL P2S"


def main(out_dir: str) -> None:
    out = Path(out_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)
    profiles = paths.studio_profiles() / "BBL"
    model = paths.studio_app() / "Contents" / "Resources" / "model" / "3DBenchy.stl"
    filament = {k: v for k, v in presets.resolve("filament", FILAMENT).items() if k != "inherits"}
    filament.update(name=FILAMENT, type="filament", **{"from": "system", "instantiation": "true"})
    fil_path = out / "filament-resolved.json"
    fil_path.write_text(json.dumps(filament, indent=1))
    cmd = [str(bambu.cli_path()), "--outputdir", str(out),
           "--load-settings", f"{profiles / 'machine' / (MACHINE + '.json')};{profiles / 'process' / (PROCESS + '.json')}",
           "--load-filaments", str(fil_path), "--arrange", "1", "--export-3mf", "3DBenchy.3mf", str(model)]
    subprocess.run(cmd, cwd=out, check=True, capture_output=True)  # the CLI writes result.json into its folder
    result = out / "3DBenchy.3mf"
    if not result.exists():
        raise SystemExit("Bambu Studio didn't export the project; run the command above with --debug 3")
    print(result)


if __name__ == "__main__":
    main(sys.argv[1])
