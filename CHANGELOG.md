# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `pyGCxGC.ms` subpackage for GCxGC-MS data: ANDI/AIA netCDF (including truncated exports), OpenChrom CSV and JCAMP-DX readers, `GCxGC_MS` with modulation folding, per-ion background correction, spectrum extraction, EICs and modulation time estimation.
- Spectral libraries (`SpectralLibrary`, `load_library`): MSP import/export, a reader for NIST MS Search `MAINLIB` libraries, `.npz` caching and download of the open MoNA GC-MS library.
- Library search with composite match and reverse match factors (`search_spectrum`, `search_spectra`).
- 2D peak detection and batch identification (`pyGCxGC.ms.peaks`).
- GCxGC-MS viewer GUI (`pygcxgc-msviewer`, `launch_ms_viewer`).
- `MaskSet` (`pyGCxGC.masks`): masks stored as add/subtract shapes in retention time units, rasterised on any chromatogram, saved as `*.gcxgc-masks.json` or TIFFs, integrated exactly like `integrate_masks`.
- Selectable GCxGC-MS baseline models (`pyGCxGC.ms.baseline`): `none`, `modulation_percentile` (default, unchanged results), `rolling_percentile`, and the spectra-only `local` and `reference`; `GCxGC_MS(baseline=...)` and `set_baseline`.
- Library entries keep structure identifiers from MSP files (`smiles`, `inchi`, `inchikey`, `pubchem_cid`); library cache version 2.
- App backend (`pyGCxGC.app`, extra `app`): a `Session` with documents, jobs and events, and a local HTTP/WebSocket API with token security and binary arrays (`python -m pyGCxGC.app`).
- App frontend, milestone 1 (`frontend/`, React + TypeScript, served by `python -m pyGCxGC.app --open`): light/dark/system themes, dockable FID and MS layouts, document tabs, file browser, status bar with cursor readout and jobs, error toasts with tracebacks, command palette and shortcuts; a WebGL2 chromatogram view with matplotlib colour maps, linear/square root/cube root/log scaling, contrast histogram, nearest/bilinear/bicubic interpolation (as matplotlib), zoom and pan at 60 fps, and overlays; the FID Processing panel with a live phase-shift preview.
- API: `GET /api/fs/probe` and a `region` parameter for images.
- App milestone 2: rectangle, lasso and polygon tools (add or subtract), shape editing, undo/redo, the Masks panel (colour, visibility, rename, reorder, duplicate, category, import of TIFF folders, mask sets and other documents, exports) and the Integration panel (volumes, % of total, unassigned, overlap warnings, CSV, batch integration of several files and "Copy as Python").
- API: `POST /api/documents/{id}/batch`; mask responses include the integration.
- App milestone 3 (GCxGC-MS): EIC from an m/z list, click to search with snap to apex and scan averaging, the Spectrum panel (corrected/raw, relative/absolute, labelled peaks, copy as text, save as MSP), the Hits panel, the Match panel (head-to-tail plot and structure), the Peaks panel (detect & identify with progress, filter, sort, assign hit, rename, delete, CSV export, stale tables and re-identify keeping edits), the Libraries panel (add NIST/MSP/npz, download MoNA, several active libraries, search settings including the mass range), spectra-only baselines (local, reference point via "Set as background") and the summed spectrum of a mask.
- Molecule structures (`pyGCxGC.ms.structures`): from library identifiers or an opt-in, cached PubChem lookup, drawn with RDKit in light or dark colours.
- `GCxGC_MS.region_spectrum` (summed, background corrected spectrum of a mask).
- `pygcxgc` command (the app).
- API: structure info and SVG depictions, peak re-identification, region selection.

### Changed

- The GCxGC-MS viewer runs on the app `Session`.
- The per-ion baseline is estimated in parallel; opening a run is about 2 s faster.
- The app integrates only masks that changed, so an edit takes about the same time with any number of masks.
- `pygcxgc-msviewer` and `pygcxgc-maskcreator` are deprecated and start the app; `--legacy` runs the Tk tools.
- The app searches the selected point again when libraries or search settings change, and after re-processing.

### Fixed

- The mask creator could not load chromatograms or load and save masks (`pathlib.Path` was shadowed by `matplotlib.path.Path`).
- `document.updated` events carried the previous document revision.
- Masks on an EIC were integrated on the float32 display image instead of the float64 signal.

## [0.0.1]

### Added

- The initial release!
