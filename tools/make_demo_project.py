"""Make the demo project used for the README screenshots: 3DBenchy on a Bambu Lab P2S.

3DBenchy (by Creative Tools) ships with Bambu Studio. This loads it with Bambu's
P2S 0.4 nozzle machine, 0.20mm Standard process and Bambu PLA Basic presets
and exports a .3mf with Bambu Studio's command line:

    python3 tools/make_demo_project.py OUT_FOLDER

Each preset is resolved through its `inherits` chain first: the CLI loads a
preset file without its parents, which leaves the rest at Bambu's built-in
defaults (a filament with no density reads 0 g; the process gets 0.4 mm lines
and 4 top shells instead of 0.20mm Standard's 0.42 mm and 5).
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
    model = paths.studio_app() / "Contents" / "Resources" / "model" / "3DBenchy.stl"
    files = {}
    for kind, name in (("machine", MACHINE), ("process", PROCESS), ("filament", FILAMENT)):
        files[kind] = out / f"{kind}-resolved.json"
        files[kind].write_text(json.dumps(presets.standalone(kind, name), indent=1))
    cmd = [str(bambu.cli_path()), "--outputdir", str(out),
           "--load-settings", f"{files['machine']};{files['process']}",
           "--load-filaments", str(files["filament"]), "--arrange", "1", "--export-3mf", "3DBenchy.3mf", str(model)]
    subprocess.run(cmd, cwd=out, check=True, capture_output=True)  # the CLI writes result.json into its folder
    result = out / "3DBenchy.3mf"
    if not result.exists():
        raise SystemExit("Bambu Studio didn't export the project; run the command above with --debug 3")
    print(result)


if __name__ == "__main__":
    main(sys.argv[1])
