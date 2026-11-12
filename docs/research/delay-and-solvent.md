# Research: RT2 delay between FID and MS runs, and the solvent cut-off

Measurement-dependent defaults of `pyGCxGC.regions.alignment` (2026-11-12).

## RT2 delay

`estimate_delay(source, target)` brings the 1D signals of both runs (raw MS
TIC, FID signal) to the coarser sampling interval (bin averages), removes
slow trends with a moving average over one modulation, and cross-correlates
windows of 30 modulations for lags up to ±2 s, refined with a parabola
between samples. The delay is the correlation-weighted median of the window
lags. Windows are used when their correlation is at least 0.3 and their
signal energy at least 10⁻⁴ of the strongest window (windows without
compounds only correlate by chance). Warnings: lags of the windows with
correlation ≥ 0.7 spreading by more than one source scan (drift), windows
below 0.7, modulation times differing by more than 0.5 %, and RT1 starts
differing by more than half a modulation.

The suggested phase shift follows from the folding of both runs: a point at
time `t` has RT2 `(t - start) mod P`, with `start` the first acquisition
time plus the phase shift (in samples) times the sampling interval. The
source gets the target's RT2 when `start_source = start_target + delay`
(mod P), rounded to whole source scans. The FID's `shift` (a cyclic roll of
the rows) moves the target the same way.

Clean PP_016 (local, Shimadzu FID `.gcd` at 250 Hz and MS `.qgd` at 10 Hz,
20 s modulation):

| RT1 window (min) | Lag (s) | Correlation |
|---|---|---|
| 0-10 | 0.55 | 0.91 |
| 10-20 | 0.61 | 0.91 |
| 20-30 | 0.64 | 0.90 |
| 30-40 | 0.59 | 0.94 |
| 40-50 | 0.53 | 0.95 |
| 50-60 | 0.49 | 0.97 |
| 60-70 | 0.47 | 0.97 |
| 70-80 | 0.47 | 0.96 |
| 80-90 | 0.48 | 0.89 |
| 90-100 | 0.32 | 0.72 |
| 100-106 | 0.21 | 0.80 |

Estimate 0.53 s (5.3 scans), suggested shift +5 scans, with a drift warning
(0.47-0.64 s among the well-correlated windows). The peak-based measurement
(46 peaks) gave 0.5 s with a drift to 0.3-0.4 s after 90 min, which the
late windows reproduce. The 1D lags of the early windows are about 0.05 s
higher than the peak positions; this may be where the MS reports a scan's
time within the scan, and is below the rounding to whole scans.

On the synthetic pair (`tests/synthetic.py`) delays of 0, ±0.3 and ±0.5 s
are recovered to within 0.05 s, and a drift of 0.4 s over the run is
reported.

## Solvent cut-off suggestion

`suggest_solvent_cutoff` smooths the 1D signal over one modulation; when its
global maximum lies in the first 10 % of the run, the suggestion is the
first minimum after it. MS runs skip the first modulation, because scan 0 of
Shimadzu runs is an injection artefact (it otherwise is the maximum, and the
suggestion was 1.03 min on Clean PP_016).

Added to the specified rule (decided with the user on 2026-11-12): the
maximum must also stand out as a solvent peak, at least **10 times** the
median of the smoothed, non-zero signal in the first 10 % of the run
(`min_prominence`). Without it, Clean PP_016, which has no solvent peak (the
signal starts at about 1.5 min), got a suggestion of 2.83 min from the FID:
its maximum is a dense cluster of compounds at 2.3 min, and the cut-off
would have removed real peaks between 1.6 and 2.8 min. Measured ratios:

| Run | Maximum / median (first 10 %) | Suggestion |
|---|---|---|
| Clean PP_016 FID | 5.6 | none |
| Clean PP_016 MS | 2.5 | none |
| PY_010 MS | 1.8 | none |
| Synthetic pair (solvent peak) | 24 | 0.96-0.97 min |

A run without a suggestion still needs an explicit cut-off for a
reportable result; 0 means "checked, none needed".
