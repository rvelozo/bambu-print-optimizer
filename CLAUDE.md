# bambu-print-optimizer (package `slicer_optimizer`, command `slicer-opt`)

Measures Bambu Studio print settings with the real slicer: reads a `.3mf`
project, applies setting changes to a copy, slices it with the Bambu Studio CLI,
and reports time, weight and time by feature against the current settings. The
plan for the whole tool (designer, previews, phases P1–P5) is `docs/plan.md`.
All five phases of the plan are built: P1 command-line core, P2 time model and web app, P3 designer, P4 section/seams/3D, P5 print log and calibration.

Reference project: a set of silica gel drawer panels (a flat lid, two rails and
a grid panel, "v7"; an earlier "v6" version too), printed on a Bambu Lab P2S
with Bambu Studio 02.08.04.57 on macOS. Numbers below called "the v7 lid / rails
/ panel" were measured on it. The integration tests use it when
`SLICER_OPT_V7` (and `SLICER_OPT_V6_DIR`) point at copies; otherwise they skip.

## Rules for working here

- Every number shown to the user comes from the real slicer or is labelled as
  an estimate. Never present a guessed effect as a measurement.
- Never write to the user's project file. Variants are new files, and a saved
  variant is byte-for-byte the file that was sliced.
- A change that has no effect gets a diagnosis (ignored per object vs no effect
  at all), never silence.

## Layout

| Path | What |
|---|---|
| `slicer_optimizer/project3mf.py` | Read/write print-wide (`Metadata/project_settings.config`, JSON) and per-object (`Metadata/model_settings.config`, XML) settings |
| `slicer_optimizer/presets.py` | System presets from the app bundle (`Contents/Resources/profiles/<vendor>/<kind>/*.json`, resolved through `inherits`) |
| `slicer_optimizer/paths.py` | Where Bambu Studio and the app's folders are per OS; `BAMBU_STUDIO_CLI/PROFILES/VERSION` and `SLICER_OPT_DATA/CACHE/LOG` override |
| `slicer_optimizer/bambu.py` | CLI runner and cache (`paths.cache_dir()`) |
| `slicer_optimizer/rules.py` | Project rules from `<project>.rules.json` or `slicer-rules.json` next to the project |
| `slicer_optimizer/trial.py` | Try changes on a plate, diagnose no-effect changes, save variants with provenance |
| `slicer_optimizer/gcode.py` | G-code parser and a port of Bambu Studio's time estimator (GCodeProcessor.cpp, tag v02.08.04.57) |
| `slicer_optimizer/layers.py` | Per-layer summaries and binary toolpaths for the browser, cached next to each slice |
| `slicer_optimizer/server.py` | Local web app (stdlib http.server): `Hub` (known and open projects, one shared `Worker`), `App` (one project file, its runs), routes |
| `slicer_optimizer/launcher.py` | `slicer-opt open`: find or start the app in the background, open files in it, show them in the browser |
| `macos/` | `make-app.sh` builds the "Slicer Optimizer" launcher app (AppleScript applet, 3mf Open With, icon from `render_icon.py`) |
| `slicer_optimizer/web/` | Frontend: vanilla ES modules (app, layer view, charts, designer, section, 3D, prints), no build step |
| `slicer_optimizer/catalog.json` | Goals and the settings the designer can search, with values and plain-language notes |
| `slicer_optimizer/metrics.py` | Goal metrics per run (time, weight, cost, stiffness proxy, time at the flow cap) |
| `slicer_optimizer/overview.py` | What a file is tuned for: its differences from the printer's default process (print-wide, objects, parts, filaments), the default rebuilt as a "reference" run, one-setting "revert" runs, the verdict |
| `slicer_optimizer/quality.py` | Quality proxy from the toolpaths (steps on slopes, outer wall speed, speed over air, top surface), and its score against the current settings |
| `slicer_optimizer/search.py` | Sensitivity and grid candidates, Pareto front, weighted-Chebyshev pick |
| `slicer_optimizer/designer.py` | Server side of the designer: searches whose candidates are ordinary runs |
| `slicer_optimizer/optimize.py` | Goal-first optimizer: picks settings from the catalog by goal and by the plate's features, measures each alone, combines what helped within a slicing budget, recommends (stages advance in a hub thread) |
| `slicer_optimizer/section.py` | Cross-sections through the toolpaths: spans, gaps, second moment of area |
| `slicer_optimizer/prints.py` | Print log per project and time/weight calibration per printer and filament |
| `slicer_optimizer/cli.py` | `slicer-opt info / check / slice / try / search / analyze / serve / open / log / prints / cache` |
| `tests/` | Unit tests (synthetic project) and integration tests (real CLI, real files) |
| `spike/` | Throwaway scripts from the planning spike (G-code parser, layer crops, lid sweep) |

Run: `./slicer-opt <command>` (no install needed; stdlib only, Python ≥ 3.10).
Tests: `python3 -m unittest discover -s tests -t .`; add `SLICER_OPT_SLOW=1` to
include the panel plates (~1.5 min each).

## The app (server.py)

- Pages: `/` home (recent projects, open), `/p/<id>/` a project. API:
  `/api/projects[...]` (list, open by path, `pick` = macOS Open dialog,
  `upload` = dropped file, thumbnail, DELETE = hide from the list),
  `/api/p/<id>/...` (everything per project), `/api/health`, `/api/quit`.
- Project id = sha1 of the resolved path, first 16 hex; its data folder is
  `DATA/projects/<id>/` (`project.json`, `runs.json`, `searches.json`,
  `prints.json`). Dropped files are copied to `DATA/uploads/<sha12>/`.
- `App.load()` reads the file once into `Project(path, data=bytes)`; slices and
  builds use that snapshot. Every run and search carries `source_sha`; only
  the loaded version's are shown, queued ones of other versions are cancelled.
- `Hub._create` registers an App before `load()` queues its runs: the shared
  worker looks runs up through the hub (a test covers this race).
- `DATA/server.lock` (flock) keeps one server per data folder; it holds
  `{"pid", "port"}` for `slicer-opt open`.
- Native dialogs run `osascript` from the server. The dialog window opens at
  window layer 8, above app windows (measured with CGWindowList), but focus
  stays in the browser until it's clicked. Quitting stops a running slice
  and leaves it queued for the next start.

## Bambu Studio facts (verified 2026-10-12, 02.08.04.57)

- `--slice N --outputdir DIR file.3mf` slices plate N (plate N = `plater_id` N)
  and writes `result.json` (`total_predication`, `main_predication`,
  `filaments[].total_used_g`, `feature_type_times`) and `plate_N.gcode`.
- Any CLI run, even `--help`, writes `result.json` into the working directory.
  Run it from a scratch folder.
- `--export-png` renders the model only (no toolpaths) and refuses to run
  together with `--slice`.
- The line "Invalid T command (T65535)" in the log is harmless.
- Repeat slices of the same bytes: lid and rails identical every time; the v7
  panel's time varied 14230.56–14244.35 s over four runs (Travel/Undefined
  move most; grams identical). `bambu.py` keeps every real slice as a sample,
  trials slice the baseline twice, and differences inside the measured noise
  (3× the spread, at least 0.2 % of the time) count as no change.
- `project_settings.config` re-serializes byte-for-byte as
  `json.dumps(cfg, indent=4, ensure_ascii=False) + "\n"`.
- `different_settings_to_system` is a list: [process keys, one entry per
  filament slot, printer keys], each a `;`-joined sorted list.
- Per-object overrides are `<metadata key=... value=.../>` lines in the
  object's header, in alphabetical order after `name`. Vector values look like
  `80%,nil,nil`.
- A per-object `initial_layer_infill_speed` is silently ignored; it only works
  print-wide.
- `top_shell_thickness` is a minimum that `top_shell_layers` can't go below (0
  turns it off); same for the bottom pair. On the v7 lid (1 mm, 0.2 mm layers)
  top shells 5 → 3 alone changes nothing; with thickness 0 too it saves 19m39s
  and 25.30 g. `trial.BLOCKERS` lists these pairs and the tool tests them.
- The G-code header's "total filament volume [cm^3]" is actually mm³.
- Presets inherit within their vendor folder: `fdm_process_common`,
  `fdm_filament_common` and `fdm_machine_common` exist under a dozen vendors
  with different values (`presets.resolve` used Anker's until 2026-10-17).
- `--load-settings`/`--load-filaments` load a preset file without what it
  inherits; everything else comes out as built-in defaults (0.20mm Standard
  @BBL P2S exported with 0.4 mm lines and 4 top shells instead of 0.42 and 5).
  `presets.standalone()` folds the parents in first.
- Exporting a model with no presets at all writes Bambu's built-in defaults
  (PrintConfig.cpp), 555 settings: `bambu.builtin_defaults()`, cached per
  version. System presets don't name every setting (`top_one_wall_type`,
  `internal_solid_infill_pattern`, ...), and those take these values.
- `different_settings_to_system` is exact: on four GUI-saved projects the
  print-wide differences from the resolved preset (plus built-in defaults)
  were the recorded keys, no more and no fewer. Comparing values directly is
  not: GUI-saved P2S projects all have travel 1000 mm/s where every preset
  file says 600, and Bambu doesn't count it as a change.
- The machine preset's `default_print_profile` names the printer's default
  process ("0.20mm Standard @BBL P2S", "0.30mm Standard @BBL P2S 0.6 nozzle").

## Time model notes

`gcode.py` follows Bambu's estimator, not PrusaSlicer's: vector-jerk junctions,
centripetal speed limit for direction changes under ~29°, arcs as single
blocks along tangents (the arc's safe speed and E axis speed carry over from the
previous move), travel booked as "Undefined" except in custom G-code, dwells
(G4, M400 S) added to the next queued block and dropped when fewer than two
blocks are queued (so the end G-code's 2 × M400 S180 never count), G29 adds
`machine_prepare_compensation_time` only inside `M622 J1`, the first `T<n>`
adds `machine_load_filament_time` as "Flush". If a new Bambu version changes
the estimate, diff GCodeProcessor.cpp between tags first.

Multi-filament (AMS) plates, checked on custom-mv473g59-ge-circle-15cm.3mf
(plates with 3 and 2 filaments): every comment line starting `;V` is a command
for the estimator, so `;VG1 E… F…` (de-retract and the virtual flush between
`; VFLUSH_START` and `; VFLUSH_END`) is timed like a G1 move and its F is modal;
`SYNC T<s>` adds s seconds as Flush (another `R` value: any role); `FLUSH_START`
and `VFLUSH_START` switch the role to Flush until their END tag. The header's
`total filament length/weight` and `filament:` lines list one value per
filament used, and the length includes flushed filament, so it no longer equals
the G1 extrusion.

Bambu adds block times up in single-precision floats (the total and each
role). Block times below a float's resolution are partly lost: 6 s of a 3.5 h
plate of short arcs, 26 s of the v7 panel's outer walls. `Analysis.bambu_total_s`
and `time_by_feature(bambu=True)` repeat that rounding and match result.json to
0.004 % (total) and 0.03 % (per feature); the exact sums (`total_s`,
`time_by_feature()`) are what the views use. The panel's earlier "0.36 %" gap
was this rounding, not slicer noise.

Projects not saved by the Bambu Studio window exist: the ge-circle file
(generated, dated 2024-01-01) has no plate thumbnails and writes
model_settings.config on one line, with extruders on modifier parts.
`_set_object_setting` edits <metadata> elements wherever the line breaks are;
an override written there slices the same as the print-wide value.

## Quality proxy notes (verified 2026-10-13)

- `quality.measure()` runs inside `layers.build()`, so it's in every analysis
  and run's metrics (`metrics["quality"]`). The goal value is relative: the
  server scores each run against its plate's current-settings run when it's
  read (`App.goals_of`, `quality.score`), so it is never stored in `goals`.
- Slopes and overhangs come from each layer's outer walls against the layer
  below's. Test shapes sliced by Bambu (`tests/test_quality.py`): steps match
  layer height × sin(slope) to 0.5 µm; walls over air fall in the class whose
  overhang speed Bambu used (30°: 25–50 % at 50 mm/s, 45°: 50–75 % at 30,
  60°: 75–100 % at 10), 100 % of the length.
- The material side of a wall comes from the same layer's inner walls and
  infill (wall direction isn't consistent: 80–100 % of outer wall length has
  the material on the right, never all of it). Thin features with nothing
  beside them use the share of the line's width with nothing printed below.
- The first layer is inset by elephant foot compensation, so comparisons start
  at layer 3; a one-layer offset is a ledge, not a slope.
- On a lattice (the v7 panel's grid) about half of what measures 75–100 %
  over air prints at Bambu's 50–75 % speed, so every overhang speed is tried
  when any wall hangs over air (`"needs": "@overhangs"`).
- Speeds over air are compared overhang class by class (and bridges apart):
  thicker layers push the same overhang into a steeper class that Bambu prints
  slower, and a plain mean speed read that as a better finish (+23 % "over
  air" on 3DBenchy at 0.28 mm, cancelling the bigger slope steps). The top
  surface counts solid layers, not mm, for the same kind of reason: 0.12 mm
  layers with a 0.6 mm minimum gave a thinner but more-layered top.
- Bambu tags a thick bridge with the bridge line's height (0.4 mm in a 0.2 mm
  layer), and a layer's last LAYER_HEIGHT tag can be that one.
  `Analysis.layer_heights()` leaves bridge lines out; `layers.build` uses it
  for each layer's `h` (the stiffness proxy read 86.1 % instead of 83.8 % on
  the v7 lid with 2 top shells before this).

## Per-variant values

Process speeds are lists with one entry per extruder variant (P2S:
`print_extruder_variant` = Standard, High Flow, E3D High Flow); the active one
is "<extruder_type> <nozzle_volume_type>" (index 0 here). Filament lists have
slots × variants entries. `Project.active()` and `Project.filament_value()`
resolve them; compare values through them, never through the raw list.
