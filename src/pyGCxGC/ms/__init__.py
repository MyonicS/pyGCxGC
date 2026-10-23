"""Mass spectrometry support: GCxGC-MS data, spectral libraries and search."""

from __future__ import annotations

from .data import GCxGC_MS, Spectrum, estimate_modulation_time, read_gcxgc_ms
from .io import RawMSData, read_andi, read_jcamp, read_ms_file, read_openchrom_csv
from .library import SpectralLibrary, download_mona_gcms, load_library
from .search import (
    Hit,
    SearchParameters,
    composite_scores,
    search_spectra,
    search_spectrum,
)

__all__ = [
    "GCxGC_MS",
    "Hit",
    "RawMSData",
    "SearchParameters",
    "SpectralLibrary",
    "Spectrum",
    "composite_scores",
    "download_mona_gcms",
    "estimate_modulation_time",
    "load_library",
    "read_andi",
    "read_gcxgc_ms",
    "read_jcamp",
    "read_ms_file",
    "read_openchrom_csv",
    "search_spectra",
    "search_spectrum",
]
