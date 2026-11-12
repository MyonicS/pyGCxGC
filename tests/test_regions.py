"""Tests for pyGCxGC.regions and pyGCxGC.render on the synthetic GCxGC pair."""

from __future__ import annotations

import numpy as np
import pytest
from synthetic import (
    PAIR_MODULATION,
    _synthetic_pair_cached,
    pair_library,
    synthetic_pair,
)

import pyGCxGC as gcgc
from pyGCxGC.ms import GCxGC_MS
from pyGCxGC.ms.peaks import detect_peaks
from pyGCxGC.regions.alignment import estimate_delay, suggest_solvent_cutoff


def fid_of(frame, **kwargs):
    return gcgc.parse_2D_chromatogram(
        frame.copy(), modulation_time=PAIR_MODULATION, **kwargs
    )


# ------------------------------------------------------------------ solvent
def test_ms_solvent_cutoff_zeroes_the_solvent():
    raw, _, _ = _synthetic_pair_cached()
    plain = GCxGC_MS(raw, PAIR_MODULATION)
    for unset in (None, 0, 0.0):  # "not set" and 0 change nothing
        same = GCxGC_MS(raw, PAIR_MODULATION, solvent_cutoff=unset)
        np.testing.assert_array_equal(same.tic_2d, plain.tic_2d)
    cut = GCxGC_MS(raw, PAIR_MODULATION, solvent_cutoff=0.8)
    n = cut.n_solvent_scans
    assert n == int(np.searchsorted(raw.scan_times, 48.0, side="right"))
    tic = cut.tic_2d.T.ravel()  # scan order (shift 0)
    assert not tic[:n].any()
    np.testing.assert_array_equal(tic[n:], plain.tic_2d.T.ravel()[n:])
    eic = cut.eic_2d([49, 84]).T.ravel()
    assert not eic[:n].any()
    np.testing.assert_array_equal(cut.baseline.levels, plain.baseline.levels)
    assert not cut.spectrum(150, n_average=3).intensity.any()
    assert cut.spectrum(150, subtract_background=False).intensity.any()
    # peak detection: the solvent peaks are gone, the compounds stay
    before = detect_peaks(plain, snr=10)
    after = detect_peaks(cut, snr=10)
    assert before["rt1_min"].min() < 0.5
    assert after["rt1_min"].min() > 0.75
    assert len(before) - len(after) == int((before["rt1_min"] < 0.75).sum())


def test_solvent_cutoff_suggestion():
    raw, frame, truth = _synthetic_pair_cached()
    first = min(c["rt1_min"] for c in truth["compounds"].values())
    for suggestion in (
        suggest_solvent_cutoff(GCxGC_MS(raw, PAIR_MODULATION)),
        suggest_solvent_cutoff(fid_of(frame)),
    ):
        # after the solvent (0.25 min), before the first compound's rise
        assert 0.6 < suggestion < first - 0.1
    # no solvent peak, no suggestion
    t = frame["Ret.Time[s]"].to_numpy()
    y = frame["Absolute Intensity"].to_numpy().copy()
    y[t < 50] = 0.05
    assert suggest_solvent_cutoff(times=t, signal=y, modulation_time=6.0) is None
    # a maximum late in the run is not a solvent
    late = y.copy()
    late[(t > 500) & (t < 520)] = 1e4
    assert suggest_solvent_cutoff(times=t, signal=late, modulation_time=6.0) is None


# ------------------------------------------------------------------ delay
@pytest.mark.parametrize("delay", [0.0, 0.5, -0.5, 0.3])
def test_estimate_delay_recovers_known_delays(delay):
    raw, frame, _ = synthetic_pair(delay=delay)
    ms = GCxGC_MS(raw, PAIR_MODULATION)
    est = estimate_delay(ms, fid_of(frame))
    assert est.delay_s == pytest.approx(delay, abs=0.05)
    assert est.suggested_shift == round(delay / 0.1)
    assert est.warnings == []
    assert est.aligned == (abs(delay) < 0.05)
    # after the correction the runs are aligned
    ms.set_modulation(shift=est.suggested_shift)
    again = estimate_delay(ms, fid_of(frame))
    assert again.aligned
    assert again.shift_change == 0
    assert again.delay_s == pytest.approx(delay, abs=0.05)


def test_estimate_delay_accounts_for_phase_shifts():
    raw, frame, _ = _synthetic_pair_cached()
    est = estimate_delay(GCxGC_MS(raw, PAIR_MODULATION, shift=-3), fid_of(frame))
    assert est.delay_s == pytest.approx(0.5, abs=0.05)
    assert (est.shift_change, est.suggested_shift) == (8, 5)
    # the FID's shift (in its own samples) moves the target
    shifted = estimate_delay(GCxGC_MS(raw, PAIR_MODULATION), fid_of(frame, shift=10))
    assert shifted.rt2_offset_s == pytest.approx(0.5 + 10 * 0.02, abs=0.05)


def test_estimate_delay_warnings():
    raw, frame, _ = synthetic_pair(drift=0.4)
    est = estimate_delay(GCxGC_MS(raw, PAIR_MODULATION), fid_of(frame))
    assert any("drifts" in w for w in est.warnings)
    assert len(est.windows) >= 3
    # an unrelated target correlates poorly
    rng = np.random.default_rng(1)
    noisy = frame.copy()
    noisy["Absolute Intensity"] = rng.random(len(noisy)) + 0.2 * np.roll(
        noisy["Absolute Intensity"].to_numpy(), 4000
    )
    bad = estimate_delay(GCxGC_MS(raw, PAIR_MODULATION), fid_of(noisy))
    assert bad.delay_s is None or any("correlate" in w for w in bad.warnings)
    # differing modulation times are reported
    other = gcgc.parse_2D_chromatogram(frame.copy(), modulation_time=5.0)
    est = estimate_delay(GCxGC_MS(raw, PAIR_MODULATION), other)
    assert any("modulation times differ" in w for w in est.warnings)


def test_pair_library_has_structures():
    lib = pair_library()
    assert lib.structure_ids(lib.find("Toluene")[0])[0] == "Cc1ccccc1"
