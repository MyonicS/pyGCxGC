# AGENTS.md

Guidance for coding agents working on pyGCxGC, a Python package for GCxGC
(comprehensive two-dimensional gas chromatography) data: FID chromatograms,
masks/integration and GCxGC-MS with spectral library search.

## Setup and commands

```bash
uv sync --extra dev            # creates .venv (Python >= 3.10)
uv pip install -e .            # needed for the console scripts
.venv/bin/python -m pytest tests -q
.venv/bin/ruff check src/pyGCxGC/ms src/pyGCxGC/gui/ms_viewer.py tests/test_ms.py
.venv/bin/ruff format <files you touched>
```

The older modules (`main.py`, `parsing.py`, `processing.py`, `gui/mask_creator.py`)
predate the ruff configuration and do not pass it. Lint and format only the
files you change, and do not reformat untouched legacy code.

## Layout

| Path | Purpose |
| --- | --- |
| `src/pyGCxGC/main.py`, `parsing.py`, `processing.py` | FID workflow: `parse_2D_chromatogram` -> `GCxGC_FID`, masks, integration |
| `src/pyGCxGC/gui/mask_creator.py` | Tk mask creator (`pygcxgc-maskcreator`) |
| `src/pyGCxGC/ms/io.py` | GC-MS readers -> `RawMSData` (dense scans x nominal m/z float32 matrix) |
| `src/pyGCxGC/ms/data.py` | `GCxGC_MS`: folding, per-ion baseline/noise, spectra, EIC, apex search, modulation estimate |
| `src/pyGCxGC/ms/library.py` | `SpectralLibrary` (CSR-like peaks + metadata), MSP I/O, `.npz` cache, `load_library`, MoNA download |
| `src/pyGCxGC/ms/nist.py` | Reader for NIST MS Search `MAINLIB/nist.db` (format documented in the module docstring) |
| `src/pyGCxGC/ms/search.py` | Composite match factor / reverse match (Stein & Scott), sparse prescreen |
| `src/pyGCxGC/ms/peaks.py` | 2D peak detection (local maxima + nearest-apex blob volumes) and batch identification |
| `src/pyGCxGC/gui/ms_viewer.py` | Tk GCxGC-MS viewer (`pygcxgc-msviewer`, `pyGCxGC.launch_ms_viewer`) |
| `tests/test_ms.py` | Synthetic-data tests for everything in `ms/` (no local data needed) |
| `docs/example_docs/gui/ms_viewer.md` | User documentation of the MS features |
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
* GUI work runs in threads via `MSViewer._run`, and results come back
  through a queue polled with `after()`. Never touch Tk widgets from a
  worker thread.

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
* Smoke-testing the GUI: create `tk.Tk()`, construct `MSViewer(root, data,
  library, 20.0)`, drive it with `root.after(...)` callbacks (e.g.
  `select_scan`, `find_peaks`) and `root.destroy()` when done. A display is
  available on the development machine.
