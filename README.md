# bambu-print-optimizer

Make a Bambu Studio project faster, lighter, stronger or cheaper, and see how
each print setting changes your part. Every number comes from slicing the
project with Bambu Studio itself, so what you see is what the slicer would do.

You say what matters, for example "faster and lighter, but keep at least 90 %
of the strength". The tool picks the settings worth trying on that plate,
slices each one on its own to show what it does, combines the ones that help,
and recommends a result with the reason for every change. A table shows what
each setting did alone, so you learn which parameters actually move your part
and which don't.

It runs on your own computer as a small web app. Your project file is never
changed: variants are new files, byte for byte what was sliced.

Not affiliated with Bambu Lab.

## What you can do

- **Optimize** a plate for what matters to you: print time, weight, strength
  (a proxy for flat parts), filament cost, or headroom below the filament's
  flow limit. Set how much each one matters and any limits, and choose how
  long it may slice. You get a recommendation, the fastest, lightest and
  strongest alternatives, and what each setting did on its own.
- **Compare** any two variants layer by layer: toolpaths at their real line
  widths, colored by feature, speed, flow or width; time per layer; flow
  against the filament's limit; where the time goes; a cross-section; 3D.
- **Try a change** by hand, or search combinations of settings you choose
  (Designer), with the Pareto front over your goals.
- **Save** any variant as a new `.3mf` to open in Bambu Studio.
- **Log real prints** (time, weight, outcome) and see how far Bambu's
  estimates are from your printer.

Strength is a beam-theory proxy that ranks variants of the same flat part; it
doesn't predict breaking loads. Surface quality isn't measured at all. Print a
test piece before a batch.

## Requirements

- Bambu Studio. Tested with 02.08.04.57.
- Python 3.10 or newer. No other packages.
- macOS works out of the box. Windows and Linux need three settings (below)
  and haven't been tested yet; reports and fixes are welcome.

## Install and run

```bash
git clone https://github.com/rvelozo/bambu-print-optimizer.git
cd bambu-print-optimizer
./slicer-opt open
```

`open` starts the app in the background and opens it in your browser at
http://127.0.0.1:8765. Open a `.3mf` there (choose it, paste its path, or drop
it on the page), pick a plate, and start on the Optimize tab. The Quit button
stops the app.

To install the `slicer-opt` command instead of running it from the checkout:

```bash
pipx install .
```

### macOS app

```bash
macos/make-app.sh
```

builds `~/Applications/Slicer Optimizer.app`. Open it to get the app, drop
`.3mf` files on its icon, or choose it in Finder's Open With menu. It runs this
checkout, so rebuild it if you move the folder. The first time, macOS may ask
whether it can read files in Downloads or Documents.

### Windows and Linux

Tell the app where Bambu Studio is with three environment variables:

| Variable | What it is |
|---|---|
| `BAMBU_STUDIO_CLI` | The Bambu Studio executable, the one that accepts `--slice` |
| `BAMBU_STUDIO_PROFILES` | Bambu Studio's `resources/profiles` folder (its system presets) |
| `BAMBU_STUDIO_VERSION` | Its version, for example `02.08.04.57` (slices are cached per version) |

Where these are depends on how Bambu Studio was installed; check your own
install. Typical places:

- **Windows:** the executable in the Bambu Studio install folder (for example
  `C:\Program Files\Bambu Studio`), with `resources\profiles` next to it. Run
  with `python -m slicer_optimizer open` if `./slicer-opt` doesn't run.
- **Linux AppImage:** extract it with `--appimage-extract`; the executable and
  `resources/profiles` are inside `squashfs-root`. For Flatpak, point
  `BAMBU_STUDIO_CLI` at a small script that runs the Flatpak with its arguments.

On these systems the Open and Save dialogs aren't available: paste paths
instead, or drop files on the page.

The app keeps its data in `~/.local/share`, `~/.cache` and `~/.local/state`
on Linux (or the XDG folders), and in `%LOCALAPPDATA%\slicer-optimizer` on
Windows. `SLICER_OPT_DATA`, `SLICER_OPT_CACHE` and `SLICER_OPT_LOG` move them.

## How it measures

- **Real slices.** Each candidate is your project with the changed settings,
  sliced by Bambu Studio's command line. Time, weight and time per feature come
  from the slicer's own report. Results are cached by the exact file, plate and
  Bambu Studio version, so asking again costs nothing.
- **Noise.** Some plates slice to slightly different times when sliced twice
  (seconds on a 4-hour plate). The current settings are sliced twice, and
  changes smaller than that noise, or than 0.2 %, count as no change.
- **Settings that do nothing.** When a change has no effect, the tool slices
  more variants to tell why: another setting blocking it (a minimum shell
  thickness overriding the shell count, for example), Bambu Studio ignoring it
  per object, or no effect on that plate at all.
- **Previews and time per layer** come from the G-code, through a port of Bambu
  Studio's own time estimator. It matches the slicer's totals to within 0.004 %,
  per feature to 0.03 %, on single- and multi-color plates.

## Command line

The app's features are also commands:

```bash
./slicer-opt info PROJECT.3mf [--key SETTING ...]
./slicer-opt check PROJECT.3mf [--rules RULES.json] [--slice]
./slicer-opt slice PROJECT.3mf [--plate N]
./slicer-opt try PROJECT.3mf --plate N --set KEY=VALUE [--set ...] [--each] [--save NEW.3mf]
./slicer-opt search PROJECT.3mf --plate N --knob KEY=V1,V2 [--knob ...] [--each] [--weights time_s=5,grams=3,stiffness=3]
./slicer-opt analyze PROJECT.3mf [--plate N]
./slicer-opt open [PROJECT.3mf ...] [--port 8765]
./slicer-opt serve [PROJECT.3mf] [--port 8765]
./slicer-opt log PROJECT.3mf --plate N --time 2h10m [--weight 129.5] [--outcome ok|partial|failed] [--issue ...] [--test]
./slicer-opt prints PROJECT.3mf
./slicer-opt cache [--clear]
```

- **info** lists plates, objects, per-object and per-part overrides, each
  object's filament, and the settings changed from Bambu's system presets.
  `--key` shows where a setting's value comes from.
- **check** applies rules from `PROJECT.rules.json` or `slicer-rules.json`
  next to the project, for example
  `{"require": {"enable_support": "0"}, "forbid_features": ["Support"]}`.
  `--slice` also slices and checks the forbidden features.
- **slice** shows time, weight, where the time goes, and supports, per plate.
- **try** changes settings on one plate (per object by default, `--print-wide`
  for the whole project) and compares with the current settings; `--save`
  writes the variant and a `.provenance.json` with what changed.
- **search** slices combinations of values and prints the Pareto front for
  the goals in `--weights` (`time_s`, `grams`, `cost`, `stiffness`,
  `near_cap_s`), with `--max-time` and `--min-stiffness` limits.
- **analyze** checks the time model against Bambu Studio per plate and lists
  the slowest layers and the time spent at the filament's flow limit.
- **open** starts the app in the background if needed and opens projects in
  the browser; **serve** runs it in the terminal instead. One copy of the app
  runs per data folder.
- **log** and **prints** record real prints and compare them with Bambu's
  estimates, per printer and filament.
- **cache** shows or clears the slice cache.

## Data and privacy

Everything stays on your computer. The app listens only on 127.0.0.1 and has
no login, so don't expose it to a network. It reads your `.3mf` files and
never writes to them. A file dropped on the page is copied into the app's data
folder. The 3D view loads three.js from cdn.jsdelivr.net.

## Tests

```bash
python3 -m unittest discover -s tests -t .
```

Integration tests slice a reference project and compare against numbers
measured with Bambu Studio 02.08.04.57; they run when `SLICER_OPT_V7` points at
it and skip otherwise. `SLICER_OPT_SLOW=1` adds the slow plates.

## License

GNU Affero General Public License v3 (see `LICENSE`). The time estimator in
`slicer_optimizer/gcode.py` is a port of `GCodeProcessor.cpp` from
[Bambu Studio](https://github.com/bambulab/BambuStudio), and the preview uses
Bambu Studio's feature colors; Bambu Studio is AGPL-3.0, so this project is too.
