"""
Masks from peak sets (spec REG-1…6).

:func:`mask_from_peaks` turns the peaks of a class into an ordinary polygon
mask, which can be edited in the app, saved and copied to other runs:

* ``band`` (default) follows a class that forms a band across RT1, such as
  the monoaromatics between the n-alkane line and the diaromatics;
* ``hull`` is the convex hull of the peaks, for compact clusters.

:func:`mask_report` checks a mask against the labelled peak table: coverage
and purity, the label sources, the unreviewed peaks in the mask's
neighbourhood, the largest intruders and a wrap-around warning.

Examples
--------
>>> mono = table[table["category"] == "monoaromatics"]  # doctest: +SKIP
>>> mask = mask_from_peaks(mono, grid, name="Monoaromatics")  # doctest: +SKIP
>>> report = mask_report(mask, table, grid, "monoaromatics")  # doctest: +SKIP
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from scipy import ndimage

from pyGCxGC.masks import Grid, Mask, Polygon, simplify_polygon

DEFAULT_MARGIN_RT1_MODULATIONS = 2
MIN_MARGIN_RT2_PIXELS = 2


def default_margins(peaks: pd.DataFrame | None, grid: Grid) -> tuple[float, float]:
    """
    Default margins ``(margin_rt2_s, margin_rt1_min)`` (spec REG-1).

    RT2: the median half-width of the peaks' footprints in RT2
    (``rt2_width_s / 2``), at least 2 pixels. RT1: 2 modulations.
    """
    floor = MIN_MARGIN_RT2_PIXELS * grid.sampling_interval
    margin_rt2 = floor
    if peaks is not None and len(peaks) and "rt2_width_s" in peaks:
        widths = pd.to_numeric(peaks["rt2_width_s"], errors="coerce").dropna()
        if len(widths):
            margin_rt2 = max(float(np.median(widths)) / 2.0, floor)
    margin_rt1 = DEFAULT_MARGIN_RT1_MODULATIONS * grid.modulation_time / 60.0
    return round(margin_rt2, 4), round(margin_rt1, 5)


def _windows(rt1: np.ndarray, max_width: float, min_peaks: int = 3) -> list[np.ndarray]:
    """Split sorted RT1 values into windows of at least ``min_peaks`` peaks."""
    order = np.argsort(rt1, kind="stable")
    windows: list[list[int]] = [[]]
    for i in order:
        current = windows[-1]
        if len(current) >= min_peaks and rt1[i] - rt1[current[0]] > max_width:
            windows.append([])
        windows[-1].append(int(i))
    if len(windows) > 1 and len(windows[-1]) < min_peaks:
        windows[-2].extend(windows.pop())
    return [np.array(w) for w in windows if w]


def _trimmed(values: np.ndarray, trim: float) -> np.ndarray:
    """Drop the ``trim`` fraction of outliers (both ends, at least none)."""
    n_drop = int(np.floor(trim * len(values) / 2.0))
    if n_drop <= 0 or len(values) - 2 * n_drop < 1:
        return values
    s = np.sort(values)
    return s[n_drop : len(s) - n_drop]


def _clip(vertices: np.ndarray, grid: Grid) -> np.ndarray:
    lo1, hi1 = grid.rt1_start, grid.rt1_start + grid.n_mod * grid.modulation_time / 60
    lo2, hi2 = grid.rt2_start, grid.rt2_start + grid.n_rt2 * grid.sampling_interval
    out = vertices.copy()
    out[:, 0] = np.clip(out[:, 0], lo1, hi1)
    out[:, 1] = np.clip(out[:, 1], lo2, hi2)
    return out


def _simplified(vertices: np.ndarray, grid: Grid, max_vertices: int) -> np.ndarray:
    tolerance = 0.25
    out = simplify_polygon(vertices, tolerance, scale=grid.pixel_size)
    while len(out) > max_vertices and tolerance < 64:
        tolerance *= 1.5
        out = simplify_polygon(vertices, tolerance, scale=grid.pixel_size)
    return out


def band_polygon(
    rt1: np.ndarray,
    rt2: np.ndarray,
    grid: Grid,
    margin_rt2_s: float,
    margin_rt1_min: float,
    trim: float = 0.02,
    window_rt1_min: float | None = None,
    max_vertices: int = 60,
) -> np.ndarray:
    """
    Vertices of a band polygon around peaks (spec REG-1).

    RT1 is split into windows of at least three peaks (at most
    ``window_rt1_min`` wide unless more are needed). Per window the lowest
    and highest RT2 (after dropping ``trim`` of the outliers) are smoothed
    with the neighbouring windows (never cutting the window's own peaks),
    widened by ``margin_rt2_s`` and extended by ``margin_rt1_min`` at both
    ends.
    """
    rt1, rt2 = np.asarray(rt1, float), np.asarray(rt2, float)
    if window_rt1_min is None:
        window_rt1_min = 0.05 * grid.n_mod * grid.modulation_time / 60.0
    windows = _windows(rt1, window_rt1_min)
    lows, highs, starts, ends = [], [], [], []
    for w in windows:
        values = _trimmed(rt2[w], trim)
        lows.append(values.min())
        highs.append(values.max())
        starts.append(rt1[w].min())
        ends.append(rt1[w].max())
    lows, highs = np.array(lows), np.array(highs)
    if len(windows) >= 3:
        kernel = np.ones(3) / 3.0
        smooth_lo = np.convolve(np.pad(lows, 1, mode="edge"), kernel, "valid")
        smooth_hi = np.convolve(np.pad(highs, 1, mode="edge"), kernel, "valid")
        lows, highs = np.minimum(lows, smooth_lo), np.maximum(highs, smooth_hi)
    lows, highs = lows - margin_rt2_s, highs + margin_rt2_s
    starts[0] -= margin_rt1_min
    ends[-1] += margin_rt1_min
    upper, lower = [], []
    for a, b, lo, hi in zip(starts, ends, lows, highs, strict=True):
        upper += [(a, hi), (b, hi)]
        lower += [(a, lo), (b, lo)]
    vertices = np.array(upper + lower[::-1], dtype=np.float64)
    vertices = _clip(vertices, grid)
    return _simplified(vertices, grid, max_vertices)


def hull_polygon(
    rt1: np.ndarray,
    rt2: np.ndarray,
    grid: Grid,
    margin_rt2_s: float,
    margin_rt1_min: float,
    max_vertices: int = 60,
) -> np.ndarray:
    """Convex hull of the peaks widened by the margins (spec REG-2)."""
    from scipy.spatial import ConvexHull

    rt1, rt2 = np.asarray(rt1, float), np.asarray(rt2, float)
    corners = np.concatenate(
        [
            np.column_stack([rt1 + dx, rt2 + dy])
            for dx in (-margin_rt1_min, margin_rt1_min)
            for dy in (-margin_rt2_s, margin_rt2_s)
        ]
    )
    scaled = corners / np.asarray(grid.pixel_size)
    hull = ConvexHull(scaled)
    vertices = _clip(corners[hull.vertices], grid)
    return _simplified(vertices, grid, max_vertices)


def mask_from_peaks(
    peaks: pd.DataFrame,
    grid,
    method: str = "band",
    margin_rt2_s: float | None = None,
    margin_rt1_min: float | None = None,
    trim: float = 0.02,
    window_rt1_min: float | None = None,
    name: str | None = None,
    color: str | None = None,
    category: str | None = None,
    max_vertices: int = 60,
) -> Mask:
    """
    A polygon mask around a set of peaks (spec REG-1/2).

    Parameters
    ----------
    peaks : pandas.DataFrame
        The peaks (``rt1_min``, ``rt2_s``; ``rt2_width_s`` for the default
        RT2 margin), e.g. the rows of one class.
    grid : Grid, GCxGC_MS or GCxGC_FID
        The chromatogram the mask is drawn on (for clipping and pixel sizes).
    method : {"band", "hull"}
    margin_rt2_s, margin_rt1_min : float, optional
        Widening in RT2 (s) and RT1 (min); see :func:`default_margins`.
    trim : float, default 0.02
        Fraction of RT2 outliers dropped per window (``band``).
    window_rt1_min : float, optional
        Largest RT1 window (min, ``band``); default 5 % of the run.
    name, color, category : str, optional
        Of the new mask (the name defaults to the category).
    max_vertices : int, default 60
        The polygon is simplified to about this many vertices.

    Returns
    -------
    Mask
        One ``add`` polygon; ``mask.meta`` records the method and margins,
        which define the mask's neighbourhood (spec GRD-1).
    """
    grid = Grid.from_chromatogram(grid)
    if peaks is None or not len(peaks):
        raise ValueError("No peaks to draw a mask around")
    if method not in ("band", "hull"):
        raise ValueError(f"Unknown method {method!r}; use 'band' or 'hull'")
    d_rt2, d_rt1 = default_margins(peaks, grid)
    margin_rt2_s = d_rt2 if margin_rt2_s is None else float(margin_rt2_s)
    margin_rt1_min = d_rt1 if margin_rt1_min is None else float(margin_rt1_min)
    if margin_rt2_s < 0 or margin_rt1_min < 0:
        raise ValueError("Margins must not be negative")
    rt1 = peaks["rt1_min"].to_numpy(dtype=float)
    rt2 = peaks["rt2_s"].to_numpy(dtype=float)
    if method == "band":
        vertices = band_polygon(
            rt1,
            rt2,
            grid,
            margin_rt2_s,
            margin_rt1_min,
            trim,
            window_rt1_min,
            max_vertices,
        )
    else:
        vertices = hull_polygon(
            rt1, rt2, grid, margin_rt2_s, margin_rt1_min, max_vertices
        )
    mask = Mask(name=name or category or "Peaks", category=category)
    if color:
        mask.color = color
    mask.add(Polygon(tuple(map(tuple, np.round(vertices, 6)))))
    mask.meta = {
        "source": "peaks",
        "method": method,
        "margin_rt2_s": margin_rt2_s,
        "margin_rt1_min": margin_rt1_min,
        "trim": trim,
        "n_peaks": len(peaks),
    }
    if "peak_id" in peaks:
        mask.meta["peak_ids"] = [int(p) for p in peaks["peak_id"]]
    return mask


# ------------------------------------------------------------------ report
def peak_pixels(table: pd.DataFrame, grid: Grid) -> tuple[np.ndarray, np.ndarray]:
    """``(rt2_index, mod_index)`` of every peak apex on ``grid``."""
    # peak positions are pixel starts: allow for rounding
    rows = np.floor(
        (table["rt2_s"].to_numpy(float) - grid.rt2_start) / grid.sampling_interval
        + 1e-6
    )
    cols = np.floor(
        (table["rt1_min"].to_numpy(float) - grid.rt1_start)
        / (grid.modulation_time / 60.0)
        + 1e-6
    )
    return rows.astype(int), cols.astype(int)


def _lookup(raster: np.ndarray, rows: np.ndarray, cols: np.ndarray) -> np.ndarray:
    inside = (
        (rows >= 0) & (rows < raster.shape[0]) & (cols >= 0) & (cols < raster.shape[1])
    )
    out = np.zeros(len(rows), dtype=bool)
    out[inside] = raster[rows[inside], cols[inside]]
    return out


def neighbourhood_raster(
    mask: Mask, grid: Grid, margin_rt2_s: float, margin_rt1_min: float
) -> np.ndarray:
    """The mask (``origin="lower"``) dilated by the margins."""
    inside = mask.rasterize(grid, "lower") > 0
    r = int(np.ceil(margin_rt2_s / grid.sampling_interval - 1e-9))
    c = int(np.ceil(margin_rt1_min / (grid.modulation_time / 60.0) - 1e-9))
    if r <= 0 and c <= 0:
        return inside
    structure = np.ones((2 * max(r, 0) + 1, 2 * max(c, 0) + 1), dtype=bool)
    return ndimage.binary_dilation(inside, structure=structure)


def mask_margins(
    mask: Mask, peaks: pd.DataFrame | None, grid: Grid
) -> tuple[float, float]:
    """The mask's own margins, or the defaults of REG-1 (masks drawn by hand)."""
    d_rt2, d_rt1 = default_margins(peaks, grid)
    return (
        float(mask.meta.get("margin_rt2_s", d_rt2)),
        float(mask.meta.get("margin_rt1_min", d_rt1)),
    )


def source_kind(source: str) -> str:
    """``rule``, ``agent``, ``user`` or ``""`` of a ``category_source``."""
    source = str(source or "")
    return "rule" if source.startswith("rule:") else source


def mask_report(
    mask: Mask,
    table: pd.DataFrame,
    grid,
    category: str | None = None,
    subclass: str | None = None,
    margins: tuple[float, float] | None = None,
    n_intruders: int = 10,
) -> dict[str, Any]:
    """
    How well a mask fits a class of the labelled peak table (spec REG-4).

    Parameters
    ----------
    mask : Mask
    table : pandas.DataFrame
        The full peak table with the label columns.
    grid : Grid, GCxGC_MS or GCxGC_FID
        The chromatogram the peaks belong to.
    category, subclass : str, optional
        The class (default: the mask's category).
    margins : (margin_rt2_s, margin_rt1_min), optional
        The neighbourhood; default: :func:`mask_margins`.

    Returns
    -------
    dict
        ``coverage`` (class peaks and volume share inside), ``purity`` (the
        class's share of the labelled volume inside), ``sources`` (class
        volume inside per label source, and the unreviewed share),
        ``neighbourhood`` (peaks inside or within the margins that are not
        reviewed), ``intruders`` (largest peaks of other classes inside),
        ``n_unknown``/``n_unlabelled`` inside, ``wrap_around`` and
        ``warnings``.
    """
    grid = Grid.from_chromatogram(grid)
    category = category if category is not None else mask.category
    table = table.reset_index(drop=True)
    for col, default in (
        ("category", ""),
        ("subclass", ""),
        ("category_source", ""),
        ("category_confidence", ""),
        ("reviewed", False),
        ("name", ""),
        ("match", 0),
    ):
        if col not in table:
            table[col] = default
    if margins is None:
        member = table[table["category"] == category] if category else None
        margins = mask_margins(mask, member, grid)
    rows, cols = peak_pixels(table, grid)
    inside = _lookup(mask.rasterize(grid, "lower") > 0, rows, cols)
    near = _lookup(neighbourhood_raster(mask, grid, *margins), rows, cols)
    volume = table["volume"].to_numpy(float)
    is_class = (
        (table["category"] == category).to_numpy()
        if category
        else np.zeros(len(table), bool)
    )
    if subclass:
        is_class &= (table["subclass"] == subclass).to_numpy()
    labelled = (table["category"] != "").to_numpy()
    reviewed = table["reviewed"].fillna(False).astype(bool).to_numpy()
    warnings: list[str] = []

    def share(a, b):
        return round(float(a) / float(b), 4) if b else None

    class_volume = volume[is_class].sum()
    in_class = inside & is_class
    coverage = {
        "n_peaks": int(is_class.sum()),
        "n_inside": int(in_class.sum()),
        "volume_share": share(volume[in_class].sum(), class_volume),
    }
    purity = share(volume[in_class].sum(), volume[inside & labelled].sum())
    sources: dict[str, Any] = {}
    kinds = table["category_source"].map(source_kind).to_numpy()
    for kind in ("rule", "agent", "user"):
        sources[kind] = share(
            volume[in_class & (kinds == kind)].sum(), volume[in_class].sum()
        )
    sources["unreviewed"] = share(
        volume[in_class & ~reviewed].sum(), volume[in_class].sum()
    )
    unreviewed = near & ~reviewed
    neighbourhood = {
        "margin_rt2_s": margins[0],
        "margin_rt1_min": margins[1],
        "n_peaks": int(near.sum()),
        "n_unreviewed": int(unreviewed.sum()),
        "unreviewed_peak_ids": [int(p) for p in table.loc[unreviewed, "peak_id"]][:200],
    }
    if neighbourhood["n_unreviewed"]:
        warnings.append(
            f"{neighbourhood['n_unreviewed']} peaks inside the mask or within its "
            "margin are not reviewed"
        )
    others = table[
        inside & labelled & ~is_class & (table["category"] != "unknown").to_numpy()
    ]
    others = others.sort_values("volume", ascending=False).head(n_intruders)
    intruders = [
        {
            "peak_id": int(r["peak_id"]),
            "category": r["category"],
            "name": r["name"],
            "match": round(float(r["match"] or 0)),
            "volume_share": share(r["volume"], volume[inside].sum()),
        }
        for _, r in others.iterrows()
    ]
    n_unknown = int((inside & (table["category"] == "unknown").to_numpy()).sum())
    n_unlabelled = int((inside & ~labelled).sum())
    period = grid.modulation_time
    rt2 = table["rt2_s"].to_numpy(float)
    edge = 0.05 * period
    wrap = bool(
        np.any(is_class & (rt2 - grid.rt2_start < edge))
        and np.any(is_class & (grid.rt2_start + period - rt2 < edge))
    )
    if wrap:
        warnings.append(
            "Class peaks lie near both ends of the modulation (RT2 near 0 and near "
            f"{period:g} s): the band wraps around; correct the phase shift"
        )
    if coverage["volume_share"] is not None and coverage["volume_share"] < 0.95:
        warnings.append(
            f"The mask holds {coverage['volume_share']:.0%} of the class volume"
        )
    return {
        "category": category,
        "subclass": subclass,
        "coverage": coverage,
        "purity": purity,
        "sources": sources,
        "neighbourhood": neighbourhood,
        "intruders": intruders,
        "n_unknown": n_unknown,
        "n_unlabelled": n_unlabelled,
        "wrap_around": wrap,
        "warnings": warnings,
    }


__all__ = [
    "band_polygon",
    "default_margins",
    "hull_polygon",
    "mask_from_peaks",
    "mask_margins",
    "mask_report",
    "neighbourhood_raster",
    "peak_pixels",
    "source_kind",
]
