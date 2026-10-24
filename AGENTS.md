# AGENTS.md

Guidance for coding agents working on pyGCxGC, a Python package for GCxGC
(comprehensive two-dimensional gas chromatography) data: FID chromatograms,
masks/integration and GCxGC-MS with spectral library search.

## Setup and commands

```bash
uv sync --extra dev --extra app   # creates .venv (Python >= 3.10)
uv pip install -e .               # needed for the console scripts
.venv/bin/python -m pytest tests -q
.venv/bin/ruff check src/pyGCxGC/ms src/pyGCxGC/app src/pyGCxGC/masks.py \
    src/pyGCxGC/gui/ms_viewer.py tests/test_ms.py tests/test_masks.py \
    tests/test_app.py tests/test_structures.py tests/synthetic.py
.venv/bin/ruff format <files you touched>
.venv/bin/pygcxgc [file] [-l LIBRARY] [--open]   # = python -m pyGCxGC.app; prints READY <port> and the URL with the token
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
| `src/pyGCxGC/ms/data.py` | `GCxGC_MS`: folding, spectra, EIC, apex search, modulation estimate |
| `src/pyGCxGC/ms/baseline.py` | Baseline models: `none`, `modulation_percentile` (default), `rolling_percentile`, spectra-only `local` and `reference` |
| `src/pyGCxGC/ms/library.py` | `SpectralLibrary` (CSR-like peaks + metadata), MSP I/O, `.npz` cache, `load_library`, MoNA download |
| `src/pyGCxGC/ms/nist.py` | Reader for NIST MS Search `MAINLIB/nist.db` (format documented in the module docstring) |
| `src/pyGCxGC/ms/search.py` | Composite match factor / reverse match (Stein & Scott), sparse prescreen |
| `src/pyGCxGC/ms/peaks.py` | 2D peak detection (local maxima + nearest-apex blob volumes) and batch identification |
| `src/pyGCxGC/ms/structures.py` | Structure resolver: library identifiers, then opt-in PubChem (`PubChem`, rate limited), SQLite `StructureCache`, RDKit `depict_svg` |
| `src/pyGCxGC/gui/ms_viewer.py` | Tk GCxGC-MS viewer (`pygcxgc-msviewer`, `pyGCxGC.launch_ms_viewer`); drawing only, all logic goes through a `Session` |
| `src/pyGCxGC/app/session.py` | `Session`: documents, jobs, events, libraries, selection/search, peaks, masks with undo, export. Core dependencies only |
| `src/pyGCxGC/app/server.py`, `schemas.py`, `arrays.py` | FastAPI routes and WebSocket, pydantic API models, binary array format (`app` extra) |
| `tests/test_ms.py` | Synthetic-data tests for everything in `ms/` (no local data needed) |
| `tests/test_masks.py`, `tests/test_app.py` | `MaskSet`; `Session` and the API (FastAPI `TestClient`) |
| `tests/test_structures.py` | Structure resolver and cache with a stubbed PubChem (`pubchem()` is reused by `test_app.py` and `e2e/backend.py`) |
| `tests/synthetic.py` | Synthetic GCxGC-MS run, test library and ANDI writer shared by the tests |
| `docs/example_docs/gui/ms_viewer.md` | User documentation of the MS features |
| `frontend/src/api/` | Transport (HTTP + WebSocket, spec NB-4), typed client, binary array decoder; `schema.d.ts` is generated |
| `frontend/src/store/` | Zustand: `session` (mirror of the backend, refreshed by events; per-document mask edit queue), `view` (display, zoom, tool, active mask, selected shape), `images` (overview + detail layers), `masks.ts` (mask commands) |
| `frontend/src/chromatogram/` | WebGL2 renderer (`renderer.ts`, all display maths in the shader), view, axes, colour bar, overlays, Display popover |
| `frontend/src/shell/`, `panels/` | Top bar, status bar, dockview workspace and layouts, file browser, open and path dialogs, settings, command palette; Processing, Masks, Integration, Spectrum, Hits, Match, Peaks and Libraries panels |
| `frontend/src/ms/`, `store/ms.ts` | Stick plot (spectrum, head-to-tail), structure card, library picker; MS commands (select and search, EIC, peaks, libraries) and view state (selected hit and peak), spectrum/structure fetching |
| `docs/example_docs/gui/app.md` | User documentation of the app |
| `frontend/e2e/` | Playwright tests; `backend.py` serves a session with the example FID and a synthetic MS run |
| `scripts/export_*.py` | Generated frontend inputs: colour maps, OpenAPI snapshot, matplotlib interpolation reference |
| `specs/app.md` | Specification of the new desktop/web app (React frontend on a FastAPI session layer). Read it before working on `frontend/`, `desktop/` or `pyGCxGC.app` |
| `docs/roadmap.md` | Planned features beyond the app: group-type/region classification, agent skills and MCP server |

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
  m/z 45-500, 20 s modulation; `SR_2DGC_001_PY_007.CDF` is a truncated
  export) and `local_data/MSSEARCH/` (a licensed **NIST 20** library).
* Never commit, upload or paste the NIST library, its converted cache
  (`~/.cache/pyGCxGC/library_*.npz`) or spectra extracted from it. Tests that
  need it must `skipif` when it is absent.
* The open fallback is MoNA GC-MS (`download_mona_gcms`). Its records are
  mostly CC BY / CC BY-SA.

## Non-obvious facts

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
  structures in a packed binary format that is *not* a c-tree record file
  (`iter_ctree_records` finds nothing). `ri.dat` is a c-tree file with 447,285
  plain-text, tab-separated retention-index records keyed by `C<CAS digits>`.
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
* `estimate_modulation_time` uses the TIC autocorrelation, refined with
  harmonics. It must return 20.0 s for the PY_010 and PY_007 runs.
* **Exact integration.** `MaskSet.integrate` multiplies the `chrom_2D`
  *DataFrame*, as `integrate_masks` does. Multiplying a NumPy copy changes the
  memory order, hence the summation order, and the last digit.
* **Default baseline is frozen.** `modulation_percentile` must stay
  bit-identical to the original algorithm (`test_default_baseline_unchanged`).
  `fit` runs the modulations in a thread pool (NumPy releases the GIL while
  partitioning), which cut PY_010 from 2.2 s to 0.4 s.
* **Library cache version 2** adds `smiles`, `inchi`, `inchikey` and
  `pubchem_cid`. Version 1 `.npz` files still load; converted caches are keyed
  by the version, so NIST is re-converted once (about 10 s).
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
  `TextEditorModule`, `tooltipField`/`headerTooltip` the `TooltipModule` (not
  registered). The current row (selected hit or peak) is styled with
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
