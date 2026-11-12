"""
Aligning two runs of the same sample, and the solvent cut-off.

GCxGC-FID and GCxGC-MS runs with the same column set, oven programme and
modulation time agree in the first dimension (a compound elutes in the same
modulation), but the MS usually sees every compound a little later (the
transfer line), which shifts all peaks to a higher RT2. :func:`estimate_delay`
measures that delay by cross-correlating the 1D signals of both runs, and
suggests the phase shift that puts the source run (usually the MS) on the
target's RT2 axis. Apply it before peak detection, so peaks, masks and
reports are all in the target's coordinates::

    est = estimate_delay(ms, fid)
    ms.set_modulation(shift=est.suggested_shift)

:func:`suggest_solvent_cutoff` proposes a solvent cut-off from the 1D signal.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

import numpy as np
from scipy import ndimage


@dataclass
class Trace:
    """
    The 1D signal of a run and how it is folded.

    Attributes
    ----------
    times : np.ndarray
        Acquisition times (s).
    signal : np.ndarray
        Detector signal (TIC for MS runs).
    dt : float
        Sampling interval (s).
    modulation_time : float
        Modulation time (s).
    start : float
        Time (s) at which the modulations start, including the phase shift:
        a point at time ``t`` has RT2 ``(t - start) mod modulation_time``.
    shift : int
        Phase shift in samples (scans for MS runs).
    rt1_range : (float, float)
        RT1 of the first and last modulation (min) as the run reports them.
    kind : str
        ``"ms"`` or ``"fid"``.
    solvent_cutoff : float or None
        Solvent cut-off (min).
    """

    times: np.ndarray
    signal: np.ndarray
    dt: float
    modulation_time: float
    start: float
    shift: int
    rt1_range: tuple[float, float]
    kind: str
    solvent_cutoff: float | None = None


def trace_of(run) -> Trace:
    """:class:`Trace` of a :class:`~pyGCxGC.GCxGC_MS` or :class:`~pyGCxGC.GCxGC_FID`."""
    if isinstance(run, Trace):
        return run
    if hasattr(run, "tic_2d"):  # GCxGC_MS
        times = np.asarray(run.scan_times, dtype=np.float64)
        dt = float(run.sampling_interval)
        return Trace(
            times,
            run.raw.intensities.sum(axis=1, dtype=np.float64),
            dt,
            float(run.modulation_time),
            float(times[0] + run.shift * dt),
            int(run.shift),
            (float(run.rt1[0]), float(run.rt1[-1])),
            "ms",
            run.solvent_cutoff,
        )
    if hasattr(run, "chrom_1D"):  # GCxGC_FID
        frame = run.chrom_1D
        times = frame["Ret.Time[s]"].to_numpy(dtype=np.float64)
        signal = frame["Absolute Intensity"].to_numpy(dtype=np.float64)
        n = len(times)
        if n > 2:  # add_split pads the last modulation with copies of the last row
            last = np.flatnonzero(np.diff(times) > 0)
            n = int(last[-1]) + 2 if len(last) else n
        dt = float(run.sampling_interval) / 1000.0
        columns = np.asarray(run.chrom_2D.columns, dtype=np.float64)
        shift = int(run.shift or 0)
        return Trace(
            times[:n],
            signal[:n],
            dt,
            float(run.modulation_time),
            float(times[0] + shift * dt),
            shift,
            (float(columns[0]) / 60.0, float(columns[-1]) / 60.0),
            "fid",
            run.solvent_cutoff or None,
        )
    raise TypeError(f"Expected a GCxGC_MS or GCxGC_FID, not {type(run).__name__}")


@dataclass
class DelayEstimate:
    """
    Result of :func:`estimate_delay`.

    Attributes
    ----------
    delay_s : float or None
        How much later the source sees a compound than the target (s);
        ``None`` when no window correlated.
    delay_scans : float or None
        The delay in source samples (scans).
    rt2_offset_s : float or None
        How much higher RT2 is in the source than in the target, with the
        current phase shifts (s). Zero after the correction.
    suggested_shift : int or None
        Phase shift (samples) of the source that removes the offset.
    shift_change : int or None
        ``suggested_shift`` minus the current shift.
    residual_s : float or None
        RT2 offset left after rounding to whole source samples (s).
    windows : list of dict
        Per window: ``rt1_min`` (range), ``lag_s``, ``correlation``, ``used``.
    warnings : list of str
    """

    delay_s: float | None
    delay_scans: float | None
    rt2_offset_s: float | None
    suggested_shift: int | None
    shift_change: int | None
    residual_s: float | None
    source_shift: int
    source_dt: float
    target_dt: float
    windows: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def aligned(self) -> bool:
        """Whether the remaining RT2 offset is below half a pixel of the coarser run."""
        if self.rt2_offset_s is None:
            return False
        return abs(self.rt2_offset_s) < 0.5 * max(self.source_dt, self.target_dt)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["aligned"] = self.aligned
        return d


def _resample(times, signal, grid, dt):
    """Mean of the signal in bins of width ``dt`` centred on ``grid``."""
    cum = np.concatenate(
        [[0.0], np.cumsum(0.5 * (signal[1:] + signal[:-1]) * np.diff(times))]
    )
    hi = np.interp(grid + dt / 2, times, cum)
    lo = np.interp(grid - dt / 2, times, cum)
    return (hi - lo) / dt


def _lagged_correlation(a: np.ndarray, b: np.ndarray, n_lag: int) -> np.ndarray:
    """Pearson correlation of ``a[i + lag]`` with ``b[i]`` for lag in ±n_lag."""
    n = len(b) - 2 * n_lag
    ref = b[n_lag : n_lag + n]
    ref = ref - ref.mean()
    ref_norm = np.sqrt((ref**2).sum())
    out = np.zeros(2 * n_lag + 1)
    for k, lag in enumerate(range(-n_lag, n_lag + 1)):
        seg = a[n_lag + lag : n_lag + lag + n]
        seg = seg - seg.mean()
        denom = np.sqrt((seg**2).sum()) * ref_norm
        out[k] = float((seg * ref).sum() / denom) if denom > 0 else 0.0
    return out


def estimate_delay(
    source,
    target,
    window_modulations: int = 30,
    max_lag_s: float = 2.0,
    rt1_min: float | None = None,
) -> DelayEstimate:
    """
    Estimate the detector delay of ``source`` relative to ``target`` (spec ALN-1).

    The 1D signals of both runs are brought to a common time base (the
    coarser sampling interval), high-pass filtered (the modulated peaks stay,
    slow trends go) and cross-correlated in windows of
    ``window_modulations`` modulations for lags up to ``max_lag_s``. The delay
    is the median of the window lags, weighted by their correlation.

    Parameters
    ----------
    source, target : GCxGC_MS, GCxGC_FID or Trace
        Usually the MS run (source) and the FID run (target).
    window_modulations : int, default 30
        Window length in modulations.
    max_lag_s : float, default 2
        Largest delay searched (s).
    rt1_min : float, optional
        Ignore everything before this time (min). Defaults to the later of
        the two solvent cut-offs.

    Returns
    -------
    DelayEstimate
        The delay, the suggested phase shift of the source (positive when
        the source is later, as for an MS behind an FID) and per-window
        results with warnings (drift, poor correlation, differing
        modulation times or RT1 ranges).
    """
    src, tgt = trace_of(source), trace_of(target)
    warnings: list[str] = []
    period = tgt.modulation_time
    if abs(src.modulation_time - tgt.modulation_time) > 0.005 * period:
        warnings.append(
            f"The modulation times differ: {src.modulation_time:g} s (source) and "
            f"{tgt.modulation_time:g} s (target)"
        )
    if abs(src.rt1_range[0] - tgt.rt1_range[0]) > 0.5 * period / 60.0:
        warnings.append(
            f"The runs start at different RT1 ({src.rt1_range[0]:.3f} and "
            f"{tgt.rt1_range[0]:.3f} min): the same compound may get different RT1"
        )
    dt = max(src.dt, tgt.dt)
    cutoffs = [c for c in (src.solvent_cutoff, tgt.solvent_cutoff) if c]
    if rt1_min is None and cutoffs:
        rt1_min = max(cutoffs)
    lo = max(src.times[0], tgt.times[0], (rt1_min or 0.0) * 60.0)
    hi = min(src.times[-1], tgt.times[-1])
    grid = np.arange(lo + dt, hi - dt, dt)
    result = DelayEstimate(
        None, None, None, None, None, None, src.shift, src.dt, tgt.dt
    )
    n_lag = round(max_lag_s / dt)
    if len(grid) < 4 * n_lag + 10:
        result.warnings = [*warnings, "The runs overlap too little to estimate a delay"]
        return result
    width = max(round(period / dt), 3)
    signals = []
    for t in (src, tgt):
        x = _resample(t.times, t.signal, grid, dt)
        signals.append(x - ndimage.uniform_filter1d(x, width, mode="nearest"))
    a, b = signals
    step = max(round(window_modulations * period / dt), 4 * n_lag + 10)
    energies, windows = [], []
    for start in range(0, len(grid) - 2 * n_lag - 10, step):
        stop = min(start + step + 2 * n_lag, len(grid))
        if stop - start < 4 * n_lag + 10:
            break
        corr = _lagged_correlation(a[start:stop], b[start:stop], n_lag)
        k = int(np.argmax(corr))
        lag = float(k - n_lag)
        if 0 < k < len(corr) - 1:  # parabolic refinement between samples
            y0, y1, y2 = corr[k - 1], corr[k], corr[k + 1]
            denom = y0 - 2 * y1 + y2
            if denom < 0:
                lag += 0.5 * (y0 - y2) / denom
        segment = b[start + n_lag : stop - n_lag]
        energies.append(float((segment**2).sum()))
        windows.append(
            {
                "rt1_min": [
                    round(float(grid[start + n_lag]) / 60.0, 3),
                    round(float(grid[stop - n_lag - 1]) / 60.0, 3),
                ],
                "lag_s": round(lag * dt, 4),
                "correlation": round(float(corr[k]), 3),
                "used": False,
            }
        )
    # windows without compounds correlate by chance only
    top = max(energies, default=0.0)
    used = [
        w
        for w, e in zip(windows, energies, strict=True)
        if top > 0 and e > 1e-4 * top and w["correlation"] >= 0.3
    ]
    for w in used:
        w["used"] = True
    result.windows = windows
    if not used:
        result.warnings = [*warnings, "No window correlated: no delay estimate"]
        return result
    lags = np.array([w["lag_s"] for w in used])
    weights = np.array([w["correlation"] for w in used])
    order = np.argsort(lags)
    cum = np.cumsum(weights[order])
    delay = float(lags[order][np.searchsorted(cum, 0.5 * cum[-1])])
    poor = [w for w in used if w["correlation"] < 0.7]
    if poor:
        warnings.append(
            f"{len(poor)} of {len(used)} windows correlate below 0.7 "
            f"(RT1 {poor[0]['rt1_min'][0]:.1f} min, ...): check the result"
        )
    good = lags[weights >= 0.7] if np.any(weights >= 0.7) else lags
    if good.max() - good.min() > src.dt:
        warnings.append(
            f"The delay drifts from {good.min():.2f} to {good.max():.2f} s over the "
            "run (more than one source scan); a constant shift corrects the median"
        )
    n_per = src.modulation_time / src.dt
    # source RT2 = (t - start_s) mod P, target RT2 = (t - delay - start_t) mod P
    offset = (tgt.start + delay - src.start) / src.dt
    offset = (offset + n_per / 2) % n_per - n_per / 2
    change = round(offset)
    result.delay_s = round(delay, 4)
    result.delay_scans = round(delay / src.dt, 3)
    result.rt2_offset_s = round(offset * src.dt, 4)
    result.shift_change = int(change)
    result.suggested_shift = int(src.shift + change)
    result.residual_s = round((offset - change) * src.dt, 4)
    result.warnings = warnings
    return result


def suggest_solvent_cutoff(
    run=None,
    times=None,
    signal=None,
    modulation_time=None,
    skip_s: float = 0.0,
    min_prominence: float = 10.0,
):
    """
    Suggest a solvent cut-off (min) from the 1D signal (spec SOL-3).

    The signal is smoothed over one modulation. When its global maximum lies
    in the first 10 % of the run and stands out as a solvent peak (at least
    ``min_prominence`` times the median of the smoothed, non-zero signal in
    that part of the run), the
    suggestion is the first minimum after it; otherwise there is none. The
    prominence condition keeps runs without a solvent peak, whose early
    compounds can form the maximum, from getting a cut-off through their
    peaks (see ``docs/research/solvent-cutoff.md``).

    Parameters
    ----------
    run : GCxGC_MS or GCxGC_FID, optional
        The run; or give ``times`` (s), ``signal`` and ``modulation_time``.
        For an FID run processed with a cut-off, the zeroed signal is used,
        so pass its raw signal instead. For MS runs the first modulation (an
        injection artefact) is skipped.
    skip_s : float, default 0
        Ignore the first seconds of the signal.
    min_prominence : float, default 10
        How much the maximum must exceed the median of the smoothed, non-zero
        signal in the first 10 % of the run.

    Returns
    -------
    float or None
        The suggested cut-off in minutes.
    """
    if run is not None:
        t = trace_of(run)
        times, signal, modulation_time = t.times, t.signal, t.modulation_time
        if t.kind == "ms":
            skip_s = max(skip_s, t.modulation_time)
    times = np.asarray(times, dtype=np.float64)
    signal = np.asarray(signal, dtype=np.float64)
    keep = times >= times[0] + skip_s if len(times) else times > 0
    times, signal = times[keep], signal[keep]
    if len(times) < 10:
        return None
    dt = float(np.median(np.diff(times)))
    width = max(round((modulation_time or 5.0) / dt), 1)
    smooth = ndimage.uniform_filter1d(signal, width, mode="nearest")
    top = int(np.argmax(smooth))
    early = times - times[0] <= 0.1 * (times[-1] - times[0])
    if not early[top]:
        return None
    level = smooth[early & (smooth > 0)]
    if smooth[top] < min_prominence * float(np.median(level)):
        return None
    step = max(width // 2, 1)
    i = top
    while i + step < len(smooth) and smooth[i + step] < smooth[i]:
        i += step
    lo, hi = max(i - step, top), min(i + step + 1, len(smooth))
    j = lo + int(np.argmin(smooth[lo:hi]))
    if j <= top:
        return None
    return round(float(times[j]) / 60.0, 3)
