"""
Peak detection and identification in 2D chromatograms.

Peaks (blobs) are found as local maxima of the lightly smoothed, baseline
corrected 2D TIC. Each pixel above a low threshold is assigned to the nearest
maximum of its connected region (a simple watershed), which gives the blob
volume. The apex spectrum of every peak can then be searched against a
library with :func:`identify_peaks`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import pandas as pd
from scipy import ndimage

from pyGCxGC.ms.search import SearchParameters, search_spectra

if TYPE_CHECKING:
    from pyGCxGC.ms.data import GCxGC_MS
    from pyGCxGC.ms.library import SpectralLibrary


def detect_peaks(
    data: GCxGC_MS,
    snr: float = 20.0,
    min_height: float = 0.0,
    rt2_window: int = 4,
    mod_window: int = 1,
    rt1_min: float | None = None,
    max_peaks: int = 1000,
    image: np.ndarray | None = None,
) -> pd.DataFrame:
    """
    Find peaks in the 2D TIC of a :class:`GCxGC_MS`.

    Parameters
    ----------
    data : GCxGC_MS
        The chromatogram.
    snr : float, default 20
        Minimum apex height in units of the TIC noise (robust standard
        deviation of the background pixels).
    min_height : float, default 0
        Additional absolute minimum apex height.
    rt2_window, mod_window : int
        Half widths (scans, modulations) of the neighbourhood in which a peak
        must be the maximum. A compound eluting over several modulations is
        reported once, at its most intense modulation.
    rt1_min : float, optional
        Ignore everything before this first dimension time (min). Defaults to
        skipping the first modulation (injection artefacts) and, when the data
        have a solvent cut-off, everything before the modulation containing
        it.
    max_peaks : int, default 1000
        Keep only the most intense peaks.
    image : np.ndarray, optional
        Use this 2D signal (e.g. an EIC from :meth:`GCxGC_MS.eic_2d`) instead of
        the TIC.

    Returns
    -------
    pandas.DataFrame
        One row per peak with ``peak_id``, ``rt1_min``, ``rt2_s``, ``scan``,
        ``rt2_index``, ``mod_index``, ``height``, ``volume``, ``n_pixels``,
        ``snr`` (height over the TIC noise) and ``rt2_width_s`` (extent of the
        peak's footprint in its apex modulation), sorted by first then second
        dimension retention time.
    """
    tic = np.clip(data.tic_2d if image is None else image, 0, None)
    smooth = ndimage.gaussian_filter(tic, sigma=(1.0, 0.0))
    background = smooth[smooth > 0]
    if background.size == 0:
        return _empty_peak_table()
    med = np.median(background)
    noise = 1.4826 * np.median(np.abs(background - med)) or 1.0
    threshold = max(snr * noise, min_height)

    footprint = np.ones((2 * rt2_window + 1, 2 * mod_window + 1), dtype=bool)
    is_max = (smooth == ndimage.maximum_filter(smooth, footprint=footprint)) & (
        smooth > threshold
    )
    first_mod = 1 if rt1_min is None else int(np.searchsorted(data.rt1, rt1_min))
    cutoff = getattr(data, "solvent_cutoff", None)
    if rt1_min is None and cutoff:
        first_mod = max(first_mod, int(np.searchsorted(data.rt1, cutoff, "right")) - 1)
    is_max[:, :first_mod] = False
    rows, cols = np.nonzero(is_max)
    heights = tic[rows, cols]
    order = np.argsort(-heights)[:max_peaks]
    rows, cols, heights = rows[order], cols[order], heights[order]

    volumes, sizes, widths = _blob_volumes(
        tic, smooth > 3 * noise, rows, cols, rt2_window, mod_window
    )
    scans = [data.scan_at(int(r), int(c)) for r, c in zip(rows, cols, strict=False)]
    table = pd.DataFrame(
        {
            "rt1_min": data.rt1[cols],
            "rt2_s": data.rt2[rows],
            "scan": scans,
            "rt2_index": rows,
            "mod_index": cols,
            "height": heights,
            "volume": volumes,
            "n_pixels": sizes,
            "snr": heights / noise,
            "rt2_width_s": widths * data.sampling_interval,
        }
    )
    table = table.sort_values(["rt1_min", "rt2_s"]).reset_index(drop=True)
    table.insert(0, "peak_id", np.arange(1, len(table) + 1))
    return table


def identify_peaks(
    data: GCxGC_MS,
    peaks: pd.DataFrame,
    library: SpectralLibrary,
    params: SearchParameters | None = None,
    n_average: int = 3,
    progress=None,
) -> tuple[pd.DataFrame, dict]:
    """
    Search the apex spectrum of every peak against a library.

    Parameters
    ----------
    data : GCxGC_MS
        The chromatogram.
    peaks : pandas.DataFrame
        Output of :func:`detect_peaks`.
    library : SpectralLibrary
        Reference library.
    params : SearchParameters, optional
        Search settings. The mass range defaults to the acquired range.
    n_average : int, default 3
        Number of scans around the apex that are summed.
    progress : callable, optional
        ``progress(fraction)`` callback.

    Returns
    -------
    table : pandas.DataFrame
        ``peaks`` with the columns ``name``, ``match``, ``reverse_match``,
        ``formula``, ``mw``, ``cas``, ``nist_no``, ``delta_match`` and
        ``probability`` (percent, ``None`` without a calibration for the
        search settings) of the best hit.
    hits : dict
        ``peak_id`` -> list of :class:`~pyGCxGC.ms.search.Hit`.
    """
    params = params or SearchParameters()
    lo, hi = data.mass_range
    if params.mz_min is None:
        params.mz_min = lo
    if params.mz_max is None:
        params.mz_max = hi
    # spectra-only baseline models (local, reference) do not apply to peaks
    model = data.baseline.image_model
    spectra = []
    for scan in peaks["scan"]:
        s = data.spectrum(int(scan), n_average=n_average, baseline=model)
        spectra.append((s.mz, s.intensity))
    results = search_spectra(spectra, library, params, progress=progress)
    table = peaks.copy()
    best = [r[0] if r else None for r in results]
    table["name"] = [h.name if h else "" for h in best]
    table["match"] = [round(h.match) if h else 0 for h in best]
    table["reverse_match"] = [round(h.reverse_match) if h else 0 for h in best]
    table["formula"] = [h.formula if h else "" for h in best]
    table["mw"] = [h.mw if h else 0 for h in best]
    table["cas"] = [h.cas if h else "" for h in best]
    table["nist_no"] = [h.nist_no if h else 0 for h in best]
    table["delta_match"] = [
        round(h.delta_match) if h and h.delta_match is not None else None for h in best
    ]
    table["probability"] = [
        round(h.probability, 1) if h and h.probability is not None else None
        for h in best
    ]
    hits = dict(zip(table["peak_id"].tolist(), results, strict=False))
    return table, hits


def _blob_volumes(tic, mask, rows, cols, rt2_scale, mod_scale):
    """
    Volume of each peak: pixels of its region assigned to the nearest apex.

    Also returns the number of pixels and the number of pixels in the apex
    modulation (the footprint's extent along RT2).
    """
    volumes = np.zeros(len(rows))
    sizes = np.zeros(len(rows), dtype=np.int64)
    widths = np.ones(len(rows), dtype=np.int64)
    if len(rows) == 0:
        return volumes, sizes, widths
    labels, _ = ndimage.label(mask)
    peak_labels = labels[rows, cols]
    objects = ndimage.find_objects(labels)
    for lab in np.unique(peak_labels):
        members = np.flatnonzero(peak_labels == lab)
        if lab == 0:
            volumes[members] = tic[rows[members], cols[members]]
            sizes[members] = 1
            continue
        sl = objects[lab - 1]
        pr, pc = np.nonzero(labels[sl] == lab)
        pr = pr + sl[0].start
        pc = pc + sl[1].start
        values = tic[pr, pc]
        if len(members) == 1:
            owner = np.zeros(len(pr), dtype=np.int64)
        else:
            d = ((pr[:, None] - rows[members][None, :]) / max(rt2_scale, 1)) ** 2 + (
                (pc[:, None] - cols[members][None, :]) / max(mod_scale, 1)
            ) ** 2
            owner = np.argmin(d, axis=1)
        volumes[members] = np.bincount(owner, weights=values, minlength=len(members))
        sizes[members] = np.bincount(owner, minlength=len(members))
        apex_col = pc == cols[members][owner]
        widths[members] = np.bincount(owner[apex_col], minlength=len(members))
    return volumes, sizes, widths


def _empty_peak_table() -> pd.DataFrame:
    cols = [
        "peak_id",
        "rt1_min",
        "rt2_s",
        "scan",
        "rt2_index",
        "mod_index",
        "height",
        "volume",
        "n_pixels",
        "snr",
        "rt2_width_s",
    ]
    return pd.DataFrame({c: [] for c in cols})
