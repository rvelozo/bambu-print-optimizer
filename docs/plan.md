# Slicer optimizer: plan

A local app that opens a Bambu Studio `.3mf`, takes goals (time, weight,
stiffness, …) and hard rules, slices variants with the **real Bambu Studio CLI**,
and shows the trade-offs with toolpath previews drawn from the G-code. You pick
a point; it writes a new `.3mf`. It never modifies Bambu Studio and never
overwrites the input file.

Drafted 2026-10-12, Bambu Studio 02.08.04.57. Every number below was measured
in a spike on the reference project (silica gel drawer panels, v6) unless marked otherwise.

## 1. What the spike established

| Question | Measured answer | Consequence for the design |
|---|---|---|
| How long does one slice take? | Lid 0.8–0.9 s (36 runs). Rail ~1 s. Panel 55 s without supports, 95 s with them. | Lid: full grid search is cheap (500 variants ≈ 8 min). Panel: ~65 variants/hour, so sensitivity first, then a small focused search in the background. |
| Does the CLI give usable numbers? | `result.json` has `total_predication`, grams and time per feature type. Reproduces last session's lid numbers exactly: 2h20m22s / 89.67 g baseline, 1h37m52s / 68.64 g for 2+2 shells, 2h02m32s with bridge 100. | Time and weight come straight from the slicer. Nothing estimated. |
| Is the G-code enough for previews? | Has `; CHANGE_LAYER`, `; Z_HEIGHT`, `; LAYER_HEIGHT`, `; FEATURE`, `; LINE_WIDTH`, relative E (`M83`), no extrusion arcs. Spike parser total extrusion = header total exactly (lid 30,064.12 mm; panel 29,722.64 mm). 39 MB panel G-code parses in 2 s. | Layer view, section view, flow map and time per layer can all be built from the G-code. |
| Can the slicer render previews? | `--export-png` renders the **model only** (a grey part, identical for every variant) and refuses to run together with `--slice`. | Toolpath previews must be drawn by the app. PNG export only for plate thumbnails. |
| Can time per layer be computed as length ÷ feedrate? | Lid: within 0.2–4.5 % of the slicer per feature. Panel: up to 68 % low (short grid segments, acceleration dominates). | Needs an acceleration-aware time model, scaled so per-feature totals match the slicer. |
| Can flow headroom be computed? | Lid walls, solid and sparse infill run at 11.76 mm³/s (length-weighted p95) against the 12 mm³/s cap; bridge 6.2; first layer 9.4. Raw maxima up to 13.1 on the panel come from E rounding on tiny segments. | Flow map is real and explains why some speed changes do nothing. Use length-weighted percentiles, never the max. |
| Are the header units trustworthy? | No: `total filament volume [cm^3]` holds mm³ (83,659.65 for 103.74 g). | Validate every parsed total against an independent number. |

Findings on the real project (not part of the tool, but the tool must catch them):

- **Supports are generated on the panel.** A per-object `enable_support = 1`
  sits on the panel in three of the v6 project files (one also has it
  print-wide).
  With it: 6h40m57s / 101.47 g. Without it: 5h30m35s / 88.65 g. That's
  **1h10m22s and 12.8 g per panel**.
- **Fine-bar layers crawl at 10 mm/s.** On panel layers 5 and 7, 91 % and 99 %
  of the outer-wall time runs at 10 mm/s (`overhang_4_4_speed` /
  `overhang_totally_speed`). Setting both to 30 on the panel object: 4h47m51s,
  **−42m44s per panel**, same grams. Whether the bars still bridge cleanly at
  30 mm/s is **unknown**. It needs a test coupon before it goes into a batch.
- The rail in one v6 file stands upright (230 mm tall, 1,150 layers,
  2h52m41s, 22.24 g), not lying down as in a later copy of the project.

## 2. Goals

Grouped by how far a number can be trusted. The UI marks each goal with its
group, so a proxy never reads like a measurement.

**Measured by the slicer**

| Goal | Metric |
|---|---|
| Time | `total_predication`; later calibrated against real print times |
| Weight | filament grams |
| Cost | grams × R$/kg; plus energy (h × W × R$/kWh), marked as an estimate until the printer's draw is metered |
| Waste | grams of brim, supports, flush and prime line (G-code features) |
| Cleanup | support grams and time; "no supports" as a rule |

**Computed from the toolpaths** (exact geometry; a proxy for the physical property)

| Goal | Metric |
|---|---|
| Flow headroom | share of print time within 5 % of the filament's max volumetric speed: under-extrusion risk |
| Stiffness | bending stiffness from material per layer (spike) or per section (later), relative to a solid part; assumes perfect layer bonding |
| Fit | as-sliced width of fit-critical features (lid slot, rail channel) vs the model |
| Open area (this project) | open fraction of the grid as printed, vs the 23.3 % design value |
| Bar continuity (this project) | breaks in the fine bars, and the speed they print at |

**Heuristic** (scored by rules, labelled as estimates; validated only by test prints)

| Goal | Inputs |
|---|---|
| Surface quality | layer height on curves/chamfers, outer wall speed and acceleration, seam map |
| First-layer reliability | first-layer speeds, brim, bed temperature, footprint corners; becomes measured once outcomes are logged |
| Heat resistance | material property (PLA softens at 55–60 °C); a filter, not an optimization |

**Rules and constraints** (pass/fail, applied before anything is ranked)

Supports off; walls ≥ 2; finishes before a given time / fits overnight; total
batch hours and plate swaps for a set of plates (6 panels + 6 lids + 12 rails).

## 3. Optimal point designer

1. **Baseline.** Slice the file as is, compute all metrics, run the rules.
2. **Sensitivity sweep.** Each setting from the catalog that touches the chosen
   goals is varied alone (2–3 levels). Shown as a tornado chart per goal.
   A setting that changes nothing is flagged as either *ignored* (for example
   `initial_layer_infill_speed` set per object) or *capped* (the flow cap binds:
   bridge 100 → 200 on the lid).
3. **Combination search** over the settings that matter. Full grid when slices
   are cheap (lid). Latin hypercube plus refinement near the front when they are
   not (panel), running in the background with progress.
4. **Pareto filter** over the chosen goals. Lid spike: 11 of 36 variants are
   non-dominated, all with bridge 100.
5. **Preference.** Importance sliders per goal. Objectives are normalized to
   [0, 1] over the sliced set, and the pick is the point closest to the ideal by
   weighted Chebyshev distance (plus a 1 % sum term to break ties). Not a
   weighted sum: a weighted sum can never pick a point in a concave part of the
   front. Hard constraints filter before ranking. Clicking any point overrides
   the pick.
6. **Compare** the pick against the baseline: settings diff in plain language,
   with the measured effect of each change, and the previews side by side.
7. **Export** a new `.3mf` plus a provenance JSON (diff, metrics, Studio version,
   input hash). Re-slice the exported file and check that it gives the same numbers.
8. **Reality loop.** After printing, log the real time, the weight on a scale,
   and the outcome (adhesion, fit, surface notes). This calibrates the time
   estimate and turns heuristics into data.

Only sliced points can be recommended. If a surrogate model is added later to
propose combinations, its predictions are drawn hollow and must be sliced
before they can be exported.

## 4. Previews

| Preview | Source | What it answers | Status |
|---|---|---|---|
| Layer view: toolpaths at real line width, colored by feature, speed, commanded flow or width; layer slider; zoom; A\|B synced; diff overlay | G-code | "What changed on this layer? Where does it crawl?" | Rendered from the panel G-code in the spike (layers 1, 5, 7) |
| Section view: material rows per layer through a cut plane | G-code | "Where is the material? Skins vs core, wall count" | Per-layer material fraction computed in the spike |
| Time per layer | G-code, acceleration-aware, scaled to slicer feature totals | "Which layers eat the time?" Lid layer 8 (0.4 mm bridge) ≈ 38 min | Naive version done; acceleration model to build |
| Flow map | G-code | "Is this part flow-capped? Will a speed change help?" | Computed in the spike |
| Where the time goes: stacked bar per feature, A vs B | `result.json` | "Which feature to attack" | Data available |
| Pareto scatter, tornado, parallel coordinates | metrics | "What trades against what" | Lid sweep data exists |
| 3D toolpaths: layer range, level of detail | G-code | Orientation and overall shape | To build (three.js); panel needs lazy per-layer loading |
| Plate thumbnail | `--export-png` | Which plate am I looking at | Works (model only) |
| First layer and seam map | G-code | Reliability, seam visibility | To build |

## 5. Architecture

- **Local web app.** Python backend, static HTML/JS frontend, no build step.
  Runs on localhost; opens in the browser pane or any browser.
- **Backend modules** (package `slicer_optimizer/`; P1 ones exist):
  - `project3mf.py` (P1): read and write print-wide (`project_settings.config`)
    and per-object (`model_settings.config`) settings. Every other zip entry is
    copied with its content, date and compression intact.
  - `presets.py` (P1): Bambu's system presets from the app bundle, to classify
    keys and know system defaults.
  - `bambu.py` (P1): CLI runner, one slice at a time (each run is multithreaded),
    cache keyed by the project's exact bytes + plate + Studio version.
  - `rules.py` (P1): project rules (supports off, …) from a JSON file next to
    the project.
  - `trial.py` (P1): try changes on a plate, diagnose "no effect", save the
    exact sliced bytes plus a provenance file.
  - `gcode.py` (P2): parser → per-layer binary arrays (x0 y0 x1 y1 width feature
    feed E); totals checked against the header.
  - `metrics.py` (P2–P4): the goals in section 2.
  - `catalog.json` (P3): settings catalog: key, scope (print-wide or per-object),
    allowed range, goals it touches, a plain-language explanation, and known
    traps (`initial_layer_infill_speed` is print-wide only).
  - `search.py` (P3): sweeps, Pareto, scoring.
  - `server.py` (P2): HTTP API plus static files.
- **Frontend:** Canvas 2D for the layer and section views (hundreds of
  thousands of segments), D3 for scatter, tornado and parallel coordinates,
  three.js for 3D.
- **Storage:** one folder per variant: `variant.json`, `result.json`,
  compressed G-code, `layers.bin`, `metrics.json`.

## 6. Phases

Each phase ends with a check that has to pass, not a feature list.

**P1: core loop and rules (CLI).** Open, show settings and per-object overrides,
check rules, apply a diff, slice, print a metrics table, cache.
*Done when:* it reproduces the spike (lid 8422 s / 89.67 g; 2+2 → 5872 s /
68.64 g); the support rule flags the three files above; a per-object
`initial_layer_infill_speed` is reported as ignored; an exported file opens in
Bambu Studio (checked by hand) and re-slices to the same numbers.
*Status (2026-10-12):* built. Every check above passes in
`tests/test_integration.py` except the Bambu Studio GUI check, which is
still to be done by hand. Added along the way: repeat-slice noise measurement (the
panel's time moves by up to 14 s between identical slices), blocked-setting
diagnosis (`top_shell_thickness` vs `top_shell_layers`), system preset defaults.

**P2: G-code previews in a local page.** Parser, layer view (feature/speed/flow),
time per layer, flow map, where the time goes, A|B compare.
*Done when:* parsed extrusion equals the header on lid, rail and panel; per-feature
times from the acceleration model are within 5 % of the slicer on all three;
the layer view matches Bambu Studio's preview of the same layer (screenshots
side by side).

*P2 status (2026-10-12):* built. `gcode.py` is a port of Bambu Studio
02.08.04.57's estimator (GCodeProcessor.cpp at tag v02.08.04.57): per feature
within 0.005 % on the lid and rails and 0.36 % on the panel (later traced
to Bambu adding times up in single precision; repeating that, every plate
matches to 0.004 %, see CLAUDE.md); filament length equals the header. `serve` gives the layer view
(Bambu's feature colors, speed/flow/width ramps, travel, A|B side by side or
overlaid), time per layer, flow per layer and where the time goes. Still to do:
a side-by-side screenshot against Bambu Studio's own preview.

**P3: the designer.** Catalog, sensitivity sweep and tornado, combination
search, Pareto scatter with importance sliders and constraints, export.
*Done when:* the lid search runs in under 2 minutes and its front contains the
hand-found variants; a ~30-variant panel search runs unattended with progress
and survives a restart.

*P3 status (2026-10-12):* built. A 36-candidate lid search (bridge speed ×
top shells × infill) takes 60 s cold, and its front contains the hand-found
variant (bridge 100 + top shells 3 with thickness 0: 1h45m58s, 105.42 g). A
27-candidate panel search runs in the background and resumes after the
server is restarted mid-slice. The stiffness proxy measures the part's main
slab (a footprint ≥ half the largest layer) against a robust solid reference
(the v7 lid's bar and its thick-bridge layer had skewed the first version).

**P4: strength and fit.** Section view, stiffness by section, as-sliced
dimensions of the lid slot and rail channel, 3D view, seam and first-layer views.

*P4 status (2026-10-12):* built. Section view: spans per layer at their
sliced width, gaps as sliced, area/centroid/second moment for A and B. On the
v7 panel it shows the lid channel as designed: wall 3.2 mm, 1.6 mm deep
channel, 45° wedge ceiling, 0.6 mm top chamfer. Seams are detected (one per
layer on the lid, aligned) and drawn in the layer and 3D views; "First layer
by speed" jumps to layer 1 colored by speed; 3D view with a layer range.

**P5: reality loop.** Log prints (time, weight, outcome), calibrate time per
printer and filament, track adhesion tests and coupons.
*P5 status (2026-10-12):* built. `prints.py` keeps the log per project
(prints.json next to the runs); each entry carries Bambu's estimate for the
run that was printed, so the calibration is actual/estimated per printer and
filament (median, count, range; failed prints left out). Shown in the Prints
tab, in the summary ("On your printer, about …") and by `slicer-opt prints`.
Test pieces and issues (adhesion, warping, fit…) are logged the same way.
Waiting on real prints: the log is empty until a print is logged.

*Goal-first optimizer (2026-10-12):* the designer started from settings, so
step 2 of section 3 ("each setting that touches the chosen goals") now has its
own flow, the Optimize tab (`optimize.py`): goals and limits in, settings
chosen from the catalog by goal and by the plate's baseline features, each
measured alone, those that beat noise and 0.2 % combined within a slicing
budget, then the pick. Not done yet: the Latin hypercube / refinement near the
front for expensive plates (combinations are a budgeted grid of the best values).

## 7. Risks

- **Panel slices take ~55 s.** Mitigation: sensitivity first, background search,
  cache, and test coupons (a 30 × 30 mm piece of grid) for quality questions.
- **Setting names and behavior change between Studio versions.** Read keys from
  the file, store the Studio version with every result, re-run the baseline after
  an update.
- **Per-object overrides that are silently ignored.** Detected as "no change"
  and recorded in the catalog.
- **Proxies can mislead.** Stiffness, open area and fit rank variants of the
  same part and material only. A real answer needs a coupon or test print.
- **Browser load.** The panel G-code is 39 MB, so use binary per-layer data,
  lazy loading, and decimation for 3D.
- **Writing the `.3mf`.** Must keep every other entry byte-identical. Verified
  only through the CLI so far; Bambu Studio's GUI still has to open one.

## 8. Decisions (2026-10-12)

1. Reference project: the v7 version of the drawer panels (assembly information
   added, no supports anywhere). The v6 files stay as regression fixtures for
   the numbers above.
2. The tool lives in its own repository, separate from the project files.
3. Interface language: English (code and docs too).
4. Generic core, this project as the test case.

v7 baseline (CLI, 02.08.04.57): lid 2h24m31s / 130.71 g; two rails 1h23m38s /
56.85 g; panel 3h57m16s / 116.92 g; no supports on any plate.

The throwaway spike scripts that produced section 1 are not part of the
repository; everything they measured is reproduced by the tool and its tests.
