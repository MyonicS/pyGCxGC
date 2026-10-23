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

## [0.0.1]

### Added

- The initial release!
