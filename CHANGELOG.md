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

### Changed

- The GCxGC-MS viewer runs on the app `Session`.
- The per-ion baseline is estimated in parallel; opening a run is about 2 s faster.

### Fixed

- The mask creator could not load chromatograms or load and save masks (`pathlib.Path` was shadowed by `matplotlib.path.Path`).

## [0.0.1]

### Added

- The initial release!
