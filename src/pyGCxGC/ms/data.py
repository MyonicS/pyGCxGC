"""
GCxGC-MS data container.

The raw GC-MS signal (one mass spectrum per scan) is folded into a 2D
chromatogram using the modulation period. Every pixel of the 2D chromatogram
corresponds to one scan, so the mass spectrum of any point can be extracted
directly.

Background handling
-------------------
GC-MS data contain a per-ion background (detector offset, column bleed, air)
that drifts over the run. By default, for every modulation and every m/z
channel a baseline (a low percentile of that channel within the modulation)
and a noise level (scaled median absolute deviation) are estimated. Spectra
are baseline corrected per ion and channels below ``snr * noise`` are removed,
which gives clean spectra for library searching without manual background
selection. Other models are in :mod:`pyGCxGC.ms.baseline`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from pyGCxGC.ms.baseline import BaselineModel, make_baseline
from pyGCxGC.ms.io import RawMSData, read_ms_file


@dataclass
class Spectrum:
    """A mass spectrum extracted from the data."""

    mz: np.ndarray
    intensity: np.ndarray
    rt1: float = float("nan")
    rt2: float = float("nan")
    scan: int = -1
    label: str = ""

    def normalized(self, top: float = 999.0) -> Spectrum:
        """Copy with the base peak scaled to ``top``."""
        peak = self.intensity.max() if len(self.intensity) else 0
        scale = top / peak if peak > 0 else 1.0
        return Spectrum(
            self.mz, self.intensity * scale, self.rt1, self.rt2, self.scan, self.label
        )

    def nonzero(self) -> Spectrum:
        """Copy containing only peaks with a positive intensity."""
        keep = self.intensity > 0
        return Spectrum(
            self.mz[keep],
            self.intensity[keep],
            self.rt1,
            self.rt2,
            self.scan,
            self.label,
        )


class GCxGC_MS:
    """
    Comprehensive two-dimensional GC with mass spectrometric detection.

    Parameters
    ----------
    raw : RawMSData
        The GC-MS data (scans x nominal m/z).
    modulation_time : float
        Modulation period in seconds.
    shift : int, default 0
        Phase shift in scans. Each modulation starts ``shift`` scans later, so a
        positive value moves peaks to lower second dimension retention times
        (same direction as :func:`pyGCxGC.shift_phase`).
    baseline_percentile : float, default 25
        Percentile of every ion within a modulation used as its baseline
        (used when ``baseline`` is not given).
    name : str, optional
        Name of the chromatogram.
    baseline : BaselineModel, str or dict, optional
        Baseline model (see :mod:`pyGCxGC.ms.baseline`), as a model, a mode
        name or ``{"mode": name, **params}``. Defaults to
        ``modulation_percentile`` with ``baseline_percentile``.

    Attributes
    ----------
    baseline : BaselineModel
        The fitted baseline model.
    tic_2d : np.ndarray
        Baseline corrected TIC as a ``(n_rt2, n_modulations)`` array; row 0 is
        the start of the modulation (``origin="lower"`` when plotting).
    chrom_2D : pandas.DataFrame
        TIC in the layout used by :class:`pyGCxGC.GCxGC_FID` (rows are second
        dimension retention times in descending order), so it can be used with
        the masking and integration functions.
    limits : list
        ``[rt1_min (min), rt1_max (min), rt2_min (s), rt2_max (s)]``.
    """

    def __init__(
        self,
        raw: RawMSData,
        modulation_time: float,
        shift: int = 0,
        baseline_percentile: float = 25.0,
        name: str | None = None,
        baseline: BaselineModel | str | dict | None = None,
    ):
        self.raw = raw
        self.name = name or raw.metadata.get("source", "GCxGC-MS")
        self.baseline_percentile = baseline_percentile
        if baseline is None:
            baseline = {
                "mode": "modulation_percentile",
                "percentile": baseline_percentile,
            }
        self.baseline = make_baseline(baseline)
        self.sampling_interval = float(np.median(np.diff(raw.scan_times)))
        self._configure(modulation_time, shift)

    # ------------------------------------------------------------ properties
    @property
    def mz(self) -> np.ndarray:
        """Nominal m/z axis."""
        return self.raw.mz

    @property
    def intensities(self) -> np.ndarray:
        """Raw ``(n_scans, n_mz)`` intensity matrix."""
        return self.raw.intensities

    @property
    def scan_times(self) -> np.ndarray:
        """Acquisition time of each scan (s)."""
        return self.raw.scan_times

    @property
    def n_scans(self) -> int:
        return self.raw.intensities.shape[0]

    @property
    def mass_range(self) -> tuple[int, int]:
        """Acquired m/z range."""
        return int(self.mz[0]), int(self.mz[-1])

    def __repr__(self) -> str:
        return (
            f"GCxGC_MS(name={self.name!r}, scans={self.n_scans}, "
            f"m/z={self.mass_range[0]}-{self.mass_range[1]}, "
            f"modulation={self.modulation_time} s, modulations={self.n_modulations}, "
            f"baseline={self.baseline.describe()})"
        )

    # ------------------------------------------------------------ folding
    def set_modulation(
        self, modulation_time: float | None = None, shift: int | None = None
    ):
        """Change modulation time and/or phase shift and refold the data."""
        self._configure(
            self.modulation_time if modulation_time is None else modulation_time,
            self.shift if shift is None else shift,
        )

    def set_baseline(self, baseline: BaselineModel | str | dict | None, **params):
        """
        Change the baseline model and recompute the background corrected TIC.

        Parameters
        ----------
        baseline : BaselineModel, str or dict
            See :func:`pyGCxGC.ms.baseline.make_baseline`.
        **params
            Parameters of the model, when ``baseline`` is a mode name.
        """
        self.baseline = make_baseline(baseline, **params)
        self._estimate_background()

    def _configure(self, modulation_time: float, shift: int):
        n = round(modulation_time / self.sampling_interval)
        if n < 2:
            raise ValueError("modulation_time must span at least two scans")
        if (
            abs(n * self.sampling_interval - modulation_time)
            > 0.01 * self.sampling_interval
        ):
            raise ValueError(
                f"Modulation time {modulation_time} s is not an integer multiple "
                f"of the sampling interval {self.sampling_interval:.4g} s"
            )
        self.modulation_time = float(modulation_time)
        self.shift = int(shift)
        self.n_rt2 = n
        self.n_modulations = int(np.ceil((self.n_scans - self.shift) / n))
        self._scan_grid = (
            np.arange(self.n_modulations)[None, :] * n
            + np.arange(n)[:, None]
            + self.shift
        )
        self._valid = (self._scan_grid >= 0) & (self._scan_grid < self.n_scans)
        self._estimate_background()
        t0 = self.scan_times[0]
        dt = self.sampling_interval
        self.rt1 = (t0 + (np.arange(self.n_modulations) * n + self.shift) * dt) / 60.0
        self.rt2 = np.arange(n) * dt
        self.limits = [
            float(self.rt1[0]),
            float(self.rt1[-1] + self.modulation_time / 60.0),
            0.0,
            float(self.modulation_time),
        ]

    def fold(self, signal: np.ndarray) -> np.ndarray:
        """
        Fold a per-scan signal into a ``(n_rt2, n_modulations)`` array.

        Scans outside the acquisition (because of the phase shift or an
        incomplete last modulation) are set to zero.
        """
        out = np.zeros(self._scan_grid.shape, dtype=np.float64)
        out[self._valid] = np.asarray(signal)[self._scan_grid[self._valid]]
        return out

    def scan_at(self, rt2_index: int, mod_index: int) -> int:
        """Scan number of a 2D pixel (``-1`` if outside the data)."""
        if not (0 <= rt2_index < self.n_rt2 and 0 <= mod_index < self.n_modulations):
            return -1
        scan = int(self._scan_grid[rt2_index, mod_index])
        return scan if 0 <= scan < self.n_scans else -1

    def pixel_of_scan(self, scan: int) -> tuple[int, int]:
        """``(rt2_index, mod_index)`` of a scan."""
        k = scan - self.shift
        return int(k % self.n_rt2), int(k // self.n_rt2)

    def pixel_at(self, rt1_min: float, rt2_s: float) -> tuple[int, int]:
        """Nearest ``(rt2_index, mod_index)`` of a retention time pair."""
        mod = int(np.floor((rt1_min - self.rt1[0]) * 60.0 / self.modulation_time))
        j = int(np.round(rt2_s / self.sampling_interval))
        return (
            int(np.clip(j, 0, self.n_rt2 - 1)),
            int(np.clip(mod, 0, self.n_modulations - 1)),
        )

    def retention_times(self, scan: int) -> tuple[float, float]:
        """``(rt1 in min, rt2 in s)`` of a scan."""
        j, i = self.pixel_of_scan(scan)
        return float(self.rt1[i]), float(self.rt2[j])

    @property
    def chrom_2D(self) -> pd.DataFrame:
        """TIC in the :class:`pyGCxGC.GCxGC_FID` layout."""
        chrom = pd.DataFrame(
            self.tic_2d,
            index=np.round(self.rt2, 3),
            columns=np.round(self.rt1 * 60.0, 3),
        )
        return chrom.iloc[::-1]

    # ------------------------------------------------------------ background
    def _estimate_background(self):
        """Fit the baseline model and compute the corrected TIC."""
        self.baseline.fit(self)
        tic = self.raw.intensities.sum(axis=1) - self.baseline.summed_baseline()
        self.tic_2d = self.fold(tic)

    def baseline_of_scans(self, scans: np.ndarray) -> np.ndarray:
        """Baseline spectra (``(len(scans), n_mz)``) of the given scans."""
        return self.baseline.baseline_of_scans(scans)

    # ------------------------------------------------------------ spectra
    def spectrum(
        self,
        scan: int,
        n_average: int = 1,
        subtract_background: bool = True,
        snr: float = 3.0,
        baseline: BaselineModel | None = None,
    ) -> Spectrum:
        """
        Mass spectrum of a scan.

        Parameters
        ----------
        scan : int
            Scan number (see :meth:`scan_at`).
        n_average : int, default 1
            Number of scans (centred on ``scan``) that are summed.
        subtract_background : bool, default True
            Subtract the baseline and remove channels that are not
            significantly above the noise.
        snr : float, default 3
            Signal to noise threshold used when ``subtract_background`` is set
            (``0`` keeps all channels). Only the percentile baseline models
            estimate a noise level; the others never zero channels.
        baseline : BaselineModel, optional
            A fitted model to use instead of :attr:`baseline` (e.g.
            ``chrom.baseline.image_model``).

        Returns
        -------
        Spectrum
        """
        half = max(n_average, 1) // 2
        lo = max(scan - half, 0)
        hi = min(lo + max(n_average, 1), self.n_scans)
        scans = np.arange(lo, hi)
        summed = self.raw.intensities[scans].astype(np.float64).sum(axis=0)
        if subtract_background:
            model = self.baseline if baseline is None else baseline
            summed -= model.spectrum_baseline(self, scans)
            noise = model.noise_of_scans(scans)
            if noise is not None and snr > 0:
                threshold = snr * noise.mean(axis=0) * np.sqrt(len(scans))
                summed[summed < threshold] = 0.0
        summed = np.clip(summed, 0, None)
        rt1, rt2 = self.retention_times(scan)
        return Spectrum(self.mz.copy(), summed, rt1, rt2, scan)

    def spectrum_at(self, rt1_min: float, rt2_s: float, **kwargs) -> Spectrum:
        """Mass spectrum at a retention time pair (see :meth:`spectrum`)."""
        j, i = self.pixel_at(rt1_min, rt2_s)
        return self.spectrum(self.scan_at(j, i), **kwargs)

    def region_spectrum(
        self,
        mask: np.ndarray,
        subtract_background: bool = True,
        snr: float = 3.0,
        label: str = "",
    ) -> Spectrum:
        """
        Summed spectrum of a 2D region (e.g. a mask).

        Parameters
        ----------
        mask : np.ndarray
            ``(n_rt2, n_modulations)`` array; pixels with a value > 0 are
            summed (a raster of :class:`pyGCxGC.MaskSet` with
            ``origin="lower"``).
        subtract_background : bool, default True
            Subtract the image baseline (:attr:`BaselineModel.image_model`)
            and zero channels below ``snr`` times the noise of the sum.
        snr : float, default 3
            Channel zeroing threshold (``0`` keeps all channels).
        label : str, optional
            Stored on the spectrum (e.g. the mask name).

        Returns
        -------
        Spectrum
            ``rt1``/``rt2`` are the intensity-weighted centre of the region,
            ``scan`` is ``-1``.
        """
        mask = np.asarray(mask)
        if mask.shape != self._scan_grid.shape:
            raise ValueError(
                f"The region has shape {mask.shape}, the data {self._scan_grid.shape}"
            )
        inside = (mask > 0) & self._valid
        scans = self._scan_grid[inside]
        summed = self.raw.intensities[scans].astype(np.float64).sum(axis=0)
        if subtract_background and len(scans):
            model = self.baseline.image_model
            summed -= model.spectrum_baseline(self, scans)
            noise = model.noise_of_scans(scans)
            if noise is not None and snr > 0:
                # independent noise per scan adds in quadrature
                threshold = snr * np.sqrt((noise.astype(np.float64) ** 2).sum(axis=0))
                summed[summed < threshold] = 0.0
        summed = np.clip(summed, 0, None)
        rt1 = rt2 = float("nan")
        weights = np.clip(self.tic_2d[inside], 0, None)
        if weights.sum() > 0:
            rows, cols = np.nonzero(inside)
            rt1 = float(np.average(self.rt1[cols], weights=weights))
            rt2 = float(np.average(self.rt2[rows], weights=weights))
        return Spectrum(self.mz.copy(), summed, rt1, rt2, -1, label)

    def find_apex(self, scan: int, rt2_window: int = 3, mod_window: int = 1) -> int:
        """
        Scan with the highest TIC in a window around ``scan``.

        Parameters
        ----------
        scan : int
            Starting scan.
        rt2_window : int, default 3
            Half width of the window along the second dimension (scans).
        mod_window : int, default 1
            Half width of the window along the first dimension (modulations).
        """
        j, i = self.pixel_of_scan(scan)
        j0, j1 = max(j - rt2_window, 0), min(j + rt2_window + 1, self.n_rt2)
        i0, i1 = max(i - mod_window, 0), min(i + mod_window + 1, self.n_modulations)
        window = self.tic_2d[j0:j1, i0:i1]
        jj, ii = np.unravel_index(np.argmax(window), window.shape)
        apex = self.scan_at(j0 + jj, i0 + ii)
        return apex if apex >= 0 else scan

    def eic_2d(self, mz_values, subtract_background: bool = True) -> np.ndarray:
        """
        2D extracted ion chromatogram (sum of the given m/z values).

        Parameters
        ----------
        mz_values : int or sequence of int
            Nominal masses to sum.
        subtract_background : bool, default True
            Subtract the per-ion baseline.
        """
        mz_values = np.atleast_1d(mz_values).astype(int)
        cols = mz_values - int(self.mz[0])
        cols = cols[(cols >= 0) & (cols < len(self.mz))]
        if len(cols) == 0:
            return np.zeros_like(self.tic_2d)
        signal = self.raw.intensities[:, cols].astype(np.float64).sum(axis=1)
        if subtract_background:
            signal -= self.baseline.summed_baseline(cols)
        return self.fold(signal)


def read_gcxgc_ms(
    path: str | Path,
    modulation_time: float,
    shift: int = 0,
    baseline_percentile: float = 25.0,
    name: str | None = None,
    baseline: BaselineModel | str | dict | None = None,
) -> GCxGC_MS:
    """
    Read a GCxGC-MS data file.

    Parameters
    ----------
    path : str or Path
        ANDI/AIA netCDF (``.cdf``), OpenChrom ``.csv`` or JCAMP-DX file.
    modulation_time : float
        Modulation period in seconds.
    shift : int, default 0
        Phase shift in scans (see :class:`GCxGC_MS`).
    baseline_percentile : float, default 25
        Percentile used for the per-ion baseline.
    name : str, optional
        Name of the chromatogram, defaults to the file name.
    baseline : BaselineModel, str or dict, optional
        Baseline model (see :mod:`pyGCxGC.ms.baseline`).

    Returns
    -------
    GCxGC_MS

    Examples
    --------
    >>> import pyGCxGC as gcgc
    >>> chrom = gcgc.read_gcxgc_ms("run.cdf", modulation_time=20)  # doctest: +SKIP
    >>> spec = chrom.spectrum_at(25.3, 7.4)  # doctest: +SKIP
    """
    raw = read_ms_file(path)
    return GCxGC_MS(
        raw,
        modulation_time=modulation_time,
        shift=shift,
        baseline_percentile=baseline_percentile,
        name=name or Path(path).name,
        baseline=baseline,
    )


def estimate_modulation_time(
    raw: RawMSData, min_s: float = 2.0, max_s: float = 30.0
) -> float:
    """
    Estimate the modulation period from the periodicity of the TIC.

    The autocorrelation of the (detrended) TIC is evaluated for lags between
    ``min_s`` and ``max_s`` seconds. The strongest lag is refined with its
    higher harmonics (which pin down the period to a fraction of a scan) and
    rounded to a whole number of scans.
    """
    tic = raw.intensities.sum(axis=1).astype(np.float64)
    dt = float(np.median(np.diff(raw.scan_times)))
    k = max(int(2 * max_s / dt), 3)  # detrend over two maximal periods
    trend = np.convolve(tic, np.ones(k) / k, mode="same")
    x = tic - trend
    x = x[k // 2 : len(x) - k // 2] if len(x) > 4 * k else x
    lo, hi = max(int(min_s / dt), 2), min(int(max_s / dt), len(x) // 2)
    if hi <= lo:
        raise ValueError("Data too short to estimate the modulation time")
    f = np.fft.rfft(x - x.mean(), n=2 * len(x))
    ac = np.fft.irfft(f * np.conj(f))[: len(x) // 2]
    # skip the central lobe: start after the autocorrelation first turns negative
    negative = np.flatnonzero(ac[lo : hi + 1] < 0)
    start = lo + (int(negative[0]) if len(negative) else 0)
    lag = start + int(np.argmax(ac[start : hi + 1]))
    estimates = [float(lag)]
    for m in range(2, 11):
        centre = m * lag
        w = m + 2
        if centre + w >= len(ac):
            break
        seg = ac[centre - w : centre + w + 1]
        if seg.max() < 0.25 * ac[lag]:
            break  # harmonic too weak to refine the period reliably
        estimates.append((centre - w + int(np.argmax(seg))) / m)
    n_scans = round(estimates[-1])
    return round(n_scans * dt, 6)
