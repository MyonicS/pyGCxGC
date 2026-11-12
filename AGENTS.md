# AGENTS.md

Guidance for coding agents working on pyGCxGC, a Python package for GCxGC
(comprehensive two-dimensional gas chromatography) data: FID chromatograms,
masks/integration and GCxGC-MS with spectral library search.

## Setup and commands

```bash
uv sync --extra dev --extra app --extra mcp   # creates .venv (Python >= 3.10)
uv pip install -e .               # needed for the console scripts
.venv/bin/python -m pytest tests -q
.venv/bin/ruff check src/pyGCxGC/ms src/pyGCxGC/app src/pyGCxGC/masks.py \
    src/pyGCxGC/regions src/pyGCxGC/render.py \
    src/pyGCxGC/gui/ms_viewer.py tests/test_ms.py tests/test_masks.py \
    tests/test_app.py tests/test_notebook.py tests/test_structures.py tests/synthetic.py \
    tests/test_regions.py tests/test_labels.py src/pyGCxGC/mcp tests/test_mcp.py
.venv/bin/ruff format <files you touched>
.venv/bin/pygcxgc [file|project.gcxgc] [-l LIBRARY]   # = python -m pyGCxGC.app; opens a pywebview window or the browser
.venv/bin/pygcxgc --headless                        # serve only: prints READY <port> and the URL with the token
.venv/bin/pygcxgc mcp [files] [-l LIB] [--output DIR] [--allow-read DIR] [--gui]   # MCP server (stdio)
```

Frontend (`frontend/`, pnpm, Node >= 22):

```bash
cd frontend && pnpm install
pnpm build                      # -> src/pyGCxGC/app/static (served by the app server, not committed)
pnpm dev                        # with: python -m pyGCxGC.app --port 8765 --token dev; open localhost:5173/?token=dev
pnpm lint && pnpm typecheck
CHROMIUM_PATH=/usr/bin/chromium pnpm test      # Vitest: unit + shader tests in Chromium
CHROMIUM_PATH=/usr/bin/chromium pnpm test:e2e  # Playwright, needs `pnpm build`; --update-snapshots after UI changes
python scripts/export_openapi.py && pnpm gen:api   # after any API change (tests check both)
```

The older modules (`main.py`, `parsing.py`, `processing.py`, `gui/mask_creator.py`)
predate the ruff configuration and do not pass it. Lint and format only the
files you change, and do not reformat untouched legacy code.

## Layout

| Path | Purpose |
| --- | --- |
| `src/pyGCxGC/main.py`, `parsing.py`, `processing.py` | FID workflow: `parse_2D_chromatogram` -> `GCxGC_FID`, masks, integration |
| `src/pyGCxGC/gui/mask_creator.py` | Tk mask creator (`pygcxgc-maskcreator`) |
| `src/pyGCxGC/masks.py` | `MaskSet`/`Mask`/`Grid`: masks as add/subtract shapes in RT units, rasteriser (pixel centres, even-odd), TIFF and `.gcxgc-masks.json` I/O, integration identical to `integrate_masks` |
| `src/pyGCxGC/ms/io.py` | GC-MS readers -> `RawMSData` (dense scans x nominal m/z float32 matrix) |
| `src/pyGCxGC/vendor.py` | Vendor raw files via the `chromparser` dependency: `vendor_kind` (FID or MS), `read_signal`/`from_signal` (`Signal` -> FID DataFrame, used by `parsing.read_chromatogram`). MS runs: `ms.io.read_raw_ms`/`from_msdata` |
| `src/pyGCxGC/ms/data.py` | `GCxGC_MS`: folding, spectra, EIC, apex search, modulation estimate |
| `src/pyGCxGC/ms/baseline.py` | Baseline models: `none`, `modulation_percentile` (default), `rolling_percentile`, spectra-only `local` and `reference` |
| `src/pyGCxGC/ms/library.py` | `SpectralLibrary` (CSR-like peaks + metadata), MSP I/O, `.npz` cache, `load_library`, MoNA download |
| `src/pyGCxGC/ms/nist.py` | Reader for NIST MS Search `MAINLIB/nist.db`, the structures in `struct.db`/`struct.in6` and the retention indices in `ri.dat` (formats documented in the module docstring) |
| `src/pyGCxGC/ms/search.py` | Composite match factor / reverse match (Stein & Scott), sparse prescreen; `delta_match`, match `probability` and compound identity (`annotate_hits`, calibration in `ms/match_probability.json`) |
| `src/pyGCxGC/ms/peaks.py` | 2D peak detection (local maxima + nearest-apex blob volumes) and batch identification |
| `src/pyGCxGC/regions/` | `classes` (labels, rule language, `ClassScheme`, built-in `schemes/hydrocarbon-group-type.json`, evidence cards), `from_peaks` (band/hull masks and their report), `alignment` (RT2 delay, solvent cut-off suggestion), `grade` (exploratory/reportable) |
| `src/pyGCxGC/render.py` | `render_chromatogram`: deterministic matplotlib (Agg) PNG with masks, peaks and a text caption |
| `src/pyGCxGC/mcp/` | MCP server (`pygcxgc mcp`, `mcp` extra): `server.py` (the 37 tools, instructions, guides as resources, the `group-type-area` prompt, `Gui`), `guard.py` (read/write roots, output limits, library redaction), `__main__.py` (command line), `guides/*.md` |
| `scripts/calibrate_match_probability.py` | Fits the temperature of the match probability on distorted library spectra (NIST if present); prints aggregates only |
| `src/pyGCxGC/ms/structures.py` | Structure resolver: library identifiers, then opt-in PubChem (`PubChem`, rate limited), SQLite `StructureCache`, RDKit `depict_svg` |
| `src/pyGCxGC/gui/ms_viewer.py` | Tk GCxGC-MS viewer (`pygcxgc-msviewer`, `pyGCxGC.launch_ms_viewer`); drawing only, all logic goes through a `Session` |
| `src/pyGCxGC/app/session.py` | `Session`: documents, jobs, events, libraries, selection/search, peaks, masks with undo, export. Core dependencies only |
| `src/pyGCxGC/app/session.py` (projects) | `save_project`/`open_project`/`Session.load`: `*.gcxgc` JSON with relative paths, never data |
| `src/pyGCxGC/app/server.py`, `schemas.py`, `arrays.py` | FastAPI routes and WebSocket, pydantic API models, binary array format (`app` extra); `ServerThread` runs the server in a background thread |
| `src/pyGCxGC/app/viewer.py` | `gcgc.view` for notebooks: `Viewer` (live state, `select`, `set_signal`, `on`) and `LiveMasks` |
| `src/pyGCxGC/app/paths.py` | Config/cache/log folders (platformdirs), `ms_viewer.json` migration, rotating log file (core dependencies only) |
| `tests/test_ms.py` | Synthetic-data tests for everything in `ms/` (no local data needed) |
| `tests/test_vendor.py` | chromparser adapters on synthetic `MSData`/`Signal`, ANDI and HDF5 files, plus the Shimadzu files in `local_data/Data/` when present |
| `tests/test_masks.py`, `tests/test_app.py` | `MaskSet`; `Session` and the API (FastAPI `TestClient`) |
| `tests/test_notebook.py` | Milestone 4: projects, settings migration and logs, active document, view state, the viewer (real server thread) and `pygcxgc --headless` |
| `tests/test_structures.py` | Structure resolver and cache with a stubbed PubChem (`pubchem()` is reused by `test_app.py` and `e2e/backend.py`) |
| `tests/synthetic.py` | Synthetic GCxGC-MS run, test library and ANDI writer shared by the tests; `synthetic_pair()`: MS + FID run of one sample (alkanes, a monoaromatic band, diaromatics, phenol, solvent, MS 0.5 s later), `pair_library()` with SMILES, `write_fid_csv` |
| `tests/test_mcp.py` | Every tool through the SDK's in-memory client, the scripted workflow (WF-1), the guards and the library-leak test, and `pygcxgc mcp` over stdio |
| `tests/test_regions.py`, `tests/test_labels.py` | Core of `regions` and `render` on the synthetic pair; the `Session` methods for labels, review, rules, masks from peaks, delay, grade and projects |
| `docs/example_docs/gui/ms_viewer.md` | User documentation of the MS features |
| `frontend/src/api/` | Transport (HTTP + WebSocket, spec NB-4), typed client, binary array decoder; `schema.d.ts` is generated |
| `frontend/src/store/` | Zustand: `session` (mirror of the backend, refreshed by events; per-document mask edit queue), `view` (display, zoom, tool, active mask, selected shape), `images` (overview + detail layers), `masks.ts` (mask commands), `viewSync.ts` (view state to/from the backend for projects) |
| `frontend/src/chromatogram/` | WebGL2 renderer (`renderer.ts`, all display maths in the shader), view, axes, colour bar, overlays, Display popover |
| `frontend/src/shell/`, `panels/` | Top bar, status bar, dockview workspace and layouts (compact for notebooks), file browser, open and path dialogs (also "save as"), project commands (`project.ts`), settings, command palette; Processing, Masks, Integration, Spectrum, Hits, Match, Peaks and Libraries panels |
| `frontend/src/ms/`, `store/ms.ts` | Stick plot (spectrum, head-to-tail), structure card, library picker; MS commands (select and search, EIC, peaks, libraries) and view state (selected hit and peak), spectrum/structure fetching |
| `docs/example_docs/gui/app.md` | User documentation of the app |
| `frontend/e2e/` | Playwright tests; `backend.py` serves a session with the example FID and a synthetic MS run |
| `scripts/export_*.py` | Generated frontend inputs: colour maps, OpenAPI snapshot, matplotlib interpolation reference |
| `specs/app.md` | Specification of the new desktop/web app (React frontend on a FastAPI session layer). Read it before working on `frontend/`, `desktop/` or `pyGCxGC.app` |
| `specs/mcp.md` | Specification of the MCP server (agent interface), peak classification, masks from peaks and rendering (milestones 1 and 2 implemented) |
| `docs/roadmap.md` | Planned features beyond the app: group-type/region classification and the MCP server |

## Conventions

* numpy-style docstrings, `from __future__ import annotations`, `pathlib`,
  `X | None` typing.
* `src/pyGCxGC/__init__.py` uses CRLF line endings. Keep them when editing,
  because Python's `read_text`/`write_text` silently converts them to LF.
* Retention times: RT1 in minutes, RT2 in seconds. 2D arrays in `ms/` are
  `(n_rt2, n_modulations)` with row 0 = start of the modulation (plot with
  `origin="lower"`). `GCxGC_MS.chrom_2D` mirrors the reversed FID layout so
  that masks work.
* Phase shift (`shift`) is in scans. A modulation starts `shift` scans later,
  and positive values move peaks to lower RT2, the same direction as
  `shift_phase` for FID data.
* GUI work runs in threads via `MSViewer._run` (or session jobs via
  `MSViewer._run_job`), and results come back through a queue polled with
  `after()`. Never touch Tk widgets from a worker thread.
* App state belongs in the `Session` (spec `ARCH-1`); every UI action is a
  `Session` method. Long work is a job: `session.submit(kind, label, func)`,
  where `func(job)` calls `job.report(fraction)` (raises `JobCancelled` once
  cancelled) and returns a small JSON dict such as `{"document_id": ...}`.

## Data and licensing (important)

* Local data lives in `local_data/` (git-ignored, as are `*.zip`, `*.cdf`,
  `*.qgd`): `local_data/gcxgc-ms_data/` (Shimadzu GCMS-QP runs, 10 Hz,
  m/z 45-500, 20 s modulation) and `local_data/MSSEARCH/` (a licensed **NIST 20** library).
* Never commit, upload or paste the NIST library, its converted cache
  (`~/.cache/pyGCxGC/library_*.npz`) or spectra extracted from it. Tests that
  need it must `skipif` when it is absent.
* The open fallback is MoNA GC-MS (`download_mona_gcms`). Its records are
  mostly CC BY / CC BY-SA.

## Non-obvious facts

* **Names.** The app (frontend, desktop binary) is **gc-squared**, shown as
  "GC²" (`²` only in displayed text, never in identifiers); the library,
  `Session`, API and notebook viewer stay **pyGCxGC**. `gc-squared` and
  `pygcxgc` are the same command. Internal ids (`pygcxgc.token`, the array
  media type, the dockview theme, the settings folder) keep the old name.
  Distribution plans: `specs/distribution-todo.md`.
* **NIST `nist.db`** is a FairCom c-tree variable-length file. The record
  layout, the 128-entry name token table (extracted from `nistms32.dll`) and
  the delta/run mass and two-byte abundance encodings are documented in
  `ms/nist.py`. The decoding routine in `nistms32.dll` is at VA `0x1002f600`.
  When only one consecutive mass is left, the encoder may write a "run of 2"
  (`0xFF`), so the mass list must be truncated to the stored peak count.
  `peak.db` is only a search index. Synonyms are in `synonym.db`, whose records
  are not aligned with `nist.db` and are not decoded yet. Other MAINLIB files:
  `ichi.dat` is a c-tree file with 306,866 records of NIST-compressed InChI
  strings (e.g. `CH2N2/cAB1A/hA2`, not decoded). `struct.db` holds the
  structures as `uint16`-length-prefixed 2D drawings, not c-tree records
  (`iter_ctree_records` finds nothing), and `struct.in6` maps `nist.db` record
  ids to them (`read_nist_structures`; validation in
  `docs/research/ms-class-prediction.md`, section F). Stereo is not decoded.
  `ri.dat` is a c-tree file with 447,285 tab-separated text records
  (`read_nist_ri`, `ri_by_compound`; section G), keyed by `C<CAS>`,
  `U<NIST number>` or `R<id>` (no spectrum). Phase types come in 15 spellings
  (normalised), n-alkanes are stored once with phase type "All column types",
  and `Lee RI` values are on the PAH scale, so never mix them with alkane RIs.
* **Vendor files** (ANDI `.cdf`, Shimadzu `.qgd`/`.gcd`, Agilent,
  Chromeleon, Thermo) are read by `chromparser` (separate package, same
  author); fix or extend format support there, not here. pyGCxGC only adapts
  its model: `MSData` centroids are rounded to nominal m/z (`np.rint`, as
  before) and summed per scan; retention times come in minutes and are
  rounded to µs after conversion to seconds. `read_ms_file` and
  `read_chromatogram` try chromparser first (content sniffing) and fall back
  to the in-repo OpenChrom CSV/JCAMP-DX readers, which chromparser does not
  cover. chromparser refuses truncated ANDI exports (`CorruptFile`) instead
  of recovering the complete scans. Never read chromConverter's source
  (GPL-3.0).
* **Search mass range** must be the acquisition range (e.g. 45-500).
  Otherwise library peaks at m/z < 45 penalise every match.
  `SpectralLibrary.search_matrix` caches the sparse matrices per
  `(mz_min, mz_max, weights)`, and building them takes about 1.5 s for NIST.
* **Weighting**: the defaults are `(m/z)^0.5 * I^0.5`. NIST's `m^3 I^0.6`
  misidentified the n-alkane series in real data and recovered only 75 %
  (vs 97 %) of simulated distorted NIST spectra. Ratio term `F_R` is averaged
  over the N-1 neighbouring pairs, so identical spectra score 1000.
* Spectra are background-corrected per ion: baseline = 25th percentile of each
  ion within its modulation, and channels below `3 * noise` (scaled MAD) are
  zeroed. Scan 0 of Shimadzu runs is an injection artefact, so peak detection
  skips the first modulation.
* **Labels** live in the peak table (`regions.classes.LABEL_COLUMNS`:
  `category`, `subclass`, `category_source` = `rule:<scheme>/<class>`,
  `agent` or `user`, `category_reason`, `category_confidence`, `reviewed`,
  and the built-in proposal). `Session.set_categories(channel=...)` is the
  one entry point: the app and Python label as `user`, the MCP server will
  pass `channel="agent"`. User labels win (conflicts are recorded in
  `peaks_info["conflicts"]`); `detect_peaks` refuses to drop reviewed labels
  without `discard_labels=True`. Project class schemes are saved as
  `<project>.classes.json` next to the project; projects also store the top
  10 hits per peak (metadata only) and the delay estimates.
* **MCP server.** The SDK is `mcp` 2.x: FastMCP is `MCPServer`
  (`mcp.server.mcpserver`); tests use `mcp.client.Client(server)` in memory or
  `Client(StdioServerParameters(...))`. The SDK imports uvicorn and starlette
  itself, so MCP-3 means pyGCxGC never imports FastAPI or `app.server`
  without the GUI. While serving stdio the SDK points file descriptor 1 at
  stderr (the legacy FID parser prints), so start-up work runs in the
  server's lifespan and `sys.stdout` must never be replaced. `server.py` does
  not use `from __future__ import annotations` (the SDK reads the annotations
  for the schemas). Every tool returns `ToolResult` (`CallToolResult` with a
  `dict` output schema): structured content plus a text block with a summary
  line and the JSON, because clients differ in which one they show.
  Non-redistributable libraries are redacted to `guard.HIT_FIELDS`, also in
  the peak CSVs the server writes (the agent can read the output folder).
  Agent identity edits are `assigned_by = "agent"` (re-identification keeps
  user and agent edits); labels always go through `set_categories`.
  `close_document` refuses while `Session.unsaved_changes` is not empty (a
  digest of masks, labels and delays, stored when a project is saved or
  opened).
* `Session.edit_peak(hit_rank=)` takes the peak's own hits by default; the
  HTTP route passes `hits="selection"` (the default of `PeakEdit.hits`),
  because the app's "Assign to peak" button sends the rank shown in the Hits
  panel.
* **Match probability** is calibrated for `(m/z)^0.5·I^0.5` only (T = 11.6
  on NIST, `docs/research/match-probability.md`); other exponents give
  `None` and a `probability_note`. Tests stub `match_calibration`. Search
  keeps 20 hits per library for the competition before cutting to `n_hits`.
* **Solvent cut-offs**: `None` = not set (treated as 0), `0` = checked. MS
  runs zero the background-corrected signal up to the cut-off (the baseline
  is fitted on all scans, so `None` keeps the default baseline
  bit-identical). Project format version 2 converts version 1's `0` to
  `None`. The cut-off suggestion needs a dominant early maximum (10× the
  median, `docs/research/delay-and-solvent.md`): Clean PP_016 has no solvent
  and gets none.
* **Delay**: on Clean PP_016 the MS is 0.53 s (+5 scans) behind the FID;
  `estimate_delay` must stay within 0.5 ± 0.1 s there (`test_regions.py`).
  Masks copied with `import_masks(from_document=)` record `meta["copied_from"]`
  with a fingerprint of their operations, for the grade. `Mask.meta` is
  written to files only when not empty.
* Legacy FID fixes: `parse_2D_chromatogram` compared the modulation time (s)
  with the sampling interval (ms), and `add_split` divided by a non-integer
  row count when the inferred interval was a hair below the nominal one; it
  is rounded now (identical splits for data that worked before). The OpenChrom
  CSV reader uses the first data line as header, so `write_fid_csv` writes a
  dummy line.
* `estimate_modulation_time` uses the TIC autocorrelation, refined with
  harmonics. It must return 20.0 s for the PY_010 run.
* **Exact integration.** `MaskSet.integrate` multiplies the `chrom_2D`
  *DataFrame*, as `integrate_masks` does. Multiplying a NumPy copy changes the
  memory order, hence the summation order, and the last digit.
* **Default baseline is frozen.** `modulation_percentile` must stay
  bit-identical to the original algorithm (`test_default_baseline_unchanged`).
  `fit` runs the modulations in a thread pool (NumPy releases the GIL while
  partitioning), which cut PY_010 from 2.2 s to 0.4 s.
* **Library cache version 2** adds `smiles`, `inchi`, `inchikey` and
  `pubchem_cid`; **version 3** adds `nist_structure` (base64 `struct.db`
  record). Older `.npz` files still load; converted caches are keyed by the
  version, so NIST is re-converted once (about 10-20 s). NIST SMILES/InChIKeys
  are not stored: `SpectralLibrary.structure_ids` (used by `entry()`) converts
  the drawing with RDKit on first access (about 1 ms, memoised per library) and
  leaves them empty without RDKit, so installing RDKit later needs no
  re-conversion. Converting all of NIST eagerly would add 2-4 minutes.
* `pyGCxGC.app.session` must not import FastAPI or pydantic, because the Tk
  viewer uses it without the `app` extra (`test_session_needs_no_web_dependencies`).
* **API security.** `/api` needs `Authorization: Bearer <token>` (WebSocket:
  `?token=`) and a `Host` of `127.0.0.1:<port>` or `localhost:<port>`.
  Starlette's `TestClient` always sends `Host: testserver` for WebSockets, so
  tests pass `headers={"host": ...}`. uvicorn needs the `websockets` package to
  serve `/api/events`.
* The mask creator imported `matplotlib.path.Path` over `pathlib.Path`, which
  broke loading and saving files; it is now imported as `MplPath`.
* **Frontend facts.** The root `.gitignore` rules `lib/`/`lib64/` are anchored
  (`/lib/`) because they also hid `frontend/src/lib/` from git and from
  Tailwind's class scanning. dockview's React bindings are `dockview-react` (v8
  `dockview` is core only). shadcn's `cn` import is the `cn` npm package by
  shadcn. `CommandDialog` needs a `<Command>` inside. Store selectors must not
  return fresh objects/arrays (zustand v5 loops), use module constants.
* **Shader fidelity.** matplotlib's `bicubic` is Agg's cubic **B-spline**
  (smoothing, not Catmull-Rom); `renderer.browser.test.ts` compares the shader
  with `matplotlib._image.resample` output. matplotlib's `gray` table truncates
  (`(i/255*255).astype(uint8)`), so entry `i` can be `i - 1`.
* **Revisions.** `document.updated` payloads carry the new revision (fixed in
  `Session.emit`; the info used to be taken before the bump). The frontend
  refetches an image when `doc.revision` changes.
* **Mask edits.** Every edit response (`MaskSetInfo`) carries the integration,
  so the panels update with one request. `Session._integrate` caches, per mask,
  volume, pixel count and the weights inside its bounding box, keyed by the
  identity of its (immutable, shared) operations; an edit integrates only the
  changed mask and the union, bit-identical to `MaskSet.integration`. EICs are
  integrated in float64 (the image is float32 for display). The frontend runs
  a document's mask edits one after another (`queueMaskEdit`), so "New mask"
  then drawing goes into the new mask, and ignores `masks.updated` events of
  its own edits in flight.
* **Projects and live state.** The *active document* is session state: the
  frontend reports tab switches (`PUT /api/session/active`) and follows
  `document.updated` events with `change: "activated"`. View state (display,
  zoom) stays frontend-owned but is copied to `doc.view` (debounced `PUT
  .../view`, flushed before saving) so projects saved from Python include
  it; a document arriving with a `view` starts with it (`viewSync.ts`). Every
  settings change emits `settings.updated` (`Settings.on_change`), and the
  frontend's `updateSettings` ignores the PUT response, because a response
  can be older than an event (e.g. recent files written by a save). Opening a
  project writes its layouts into the settings, then emits `project.updated`
  (`opened`), on which the workspace reloads its layout.
* **Figures.** `lib/figure.ts` clones a plot's SVG with computed styles
  inlined (elements marked `data-export-skip` are dropped: crosshair,
  drafts, hover marks); `ChromView` registers an exporter per document that
  adds the WebGL image drawn at 2× (`ChromRenderer.snapshot`, which needs
  `preserveDrawingBuffer`). PNGs embed the Latin `@font-face` files, because
  an SVG drawn as an image cannot load fonts. The figure theme is applied
  by `withTheme` (toggles the root `dark` class with transitions disabled,
  reads the styles and restores it in the same task, so nothing flickers).
  A Radix menu that is still closing swallows a click on its trigger, so
  e2e tests wait for `getByRole('menu')` to be gone before reopening. DOM
  tests must be named `*.browser.test.ts` (the `unit` project has no DOM).
* **Notebook viewer.** `ServerThread` binds and `listen()`s before
  uvicorn starts (also in `serve`, so `READY` means connectable). One viewer
  per process (`viewer._current`); `view(path)` opens in the background and
  the viewer's properties `wait()` for it. IPython is optional: tests patch
  `Viewer.show`. `gcgc.app` is imported lazily by `pyGCxGC.__getattr__`.
* **UI pitfalls.** `TooltipTrigger asChild` overwrites the child's
  `data-state`, so toggle styles also key on `aria-checked`/`aria-pressed`. A
  native `<input type="color">` overflows small swatches unless sized and
  clipped. AG Grid registers only the modules in use (`AllCommunityModule`
  doubled the bundle) and never loads Google fonts.
* **MS selection.** A click is two requests: `PUT .../selection` (the
  spectrum shows at once) then `POST /api/search` (document only, searches the
  current selection and emits `selection.changed` only if it is still
  current). The frontend relies on the events, not the responses. The backend
  re-searches selections that were searched before (`hits is not None`) when
  libraries or search settings change (`Session._research`, a `search` job).
  `Selection.id` identifies the point across re-searches; the selected hit
  resets when the hit list changes (`hitsKey`), the selected peak when the
  selection id changes.
* **Structures.** PubChem's property is `SMILES` (requesting `IsomericSMILES`
  still works but returns `SMILES`); names and CAS numbers are POSTed to
  `/compound/name/property/...` so special characters need no escaping, and
  404 means "not found". Tests stub it with `session.pubchem = pubchem({...})`
  from `tests/test_structures.py`. The e2e backend clears its structure cache
  at start, but light and dark projects share one backend, so only the first
  project sees the "Fetch from PubChem?" prompt.
* AG Grid: sorting needs no module in v36 (there is no exported
  `SortModule`); `rowClassRules` needs `RowStyleModule`, editable cells
  `TextEditorModule`, `tooltipField` the `TooltipModule` (not
  registered; `DataGrid` turns a column's `headerTooltip` into the app's Radix
  tooltip, because AG Grid's did not show in the user's browsers). The current row (selected hit or peak) is styled with
  `.row-current` in `index.css`.
* Playwright `--update-snapshots` only rewrites failing screenshots; use
  `--update-snapshots=all` after intended UI changes. The e2e tests call
  Python (`helpers.python`) to check results against `integrate_masks`.
* Screenshots and frame timing: headless Chromium can use the real GPU with
  `--use-angle=vulkan --enable-features=Vulkan` (or `gl-egl`); the tests use
  SwiftShader for determinism.
* Smoke-testing the GUI: create `tk.Tk()`, construct `MSViewer(root, data,
  library, 20.0)`, drive it with `root.after(...)` callbacks (e.g.
  `select_scan`, `find_peaks`) and `root.destroy()` when done. A display is
  available on the development machine.
