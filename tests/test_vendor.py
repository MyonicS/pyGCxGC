"""Tests for the chromparser adapters (synthetic data and local vendor files)."""

from __future__ import annotations

from pathlib import Path

import chromparser
import numpy as np
import pytest
from synthetic import synthetic_run, write_andi

from pyGCxGC.app.session import detect_kind
from pyGCxGC.ms.io import from_msdata, read_ms_file
from pyGCxGC.parsing import parse_2D_chromatogram, read_chromatogram
from pyGCxGC.vendor import from_signal, read_signal, vendor_format, vendor_kind

LOCAL_DATA = Path(__file__).parents[1] / "local_data" / "Data"
LOCAL_QGD = LOCAL_DATA / "Clean PP_016_MS.qgd"
LOCAL_GCD = LOCAL_DATA / "CleanPP_016 _FID.gcd"
EXAMPLE_FID = (
    Path(__file__).parents[1]
    / "example_data"
    / "example_chromatograms"
    / "Example_FID.csv"
)


def _msdata(raw, offset=0.05):
    """`raw` as a chromparser MSData with centroids next to the nominal masses."""
    rows, cols = np.nonzero(raw.intensities)
    return chromparser.MSData(
        name="MS",
        scan_number=np.arange(len(raw.scan_times), dtype=np.int32),
        retention_time=raw.scan_times / 60.0,
        tic=raw.intensities.sum(axis=1, dtype=np.float64),
        base_peak_mz=None,
        base_peak_intensity=None,
        filters=None,
        mz=raw.mz[cols] + offset,
        intensity=raw.intensities[rows, cols],
        peak_offset=np.concatenate(
            [[0], np.cumsum(np.bincount(rows, minlength=len(raw.scan_times)))]
        ),
    )


def _signal(n=1000, interval=0.004, channel="FID"):
    return chromparser.Signal(
        channel=channel,
        time=np.arange(n) * interval / 60.0,
        values=np.sin(np.arange(n) / 50.0) + 2.0,
        unit="pA",
        sampling_interval=interval,
    )


def test_from_msdata_matches_nominal_matrix():
    raw, _ = synthetic_run()
    back = from_msdata(_msdata(raw), {"sample_name": "x"})
    np.testing.assert_array_equal(back.mz, raw.mz[np.any(raw.intensities, axis=0)])
    columns = np.searchsorted(raw.mz, back.mz)
    np.testing.assert_array_equal(back.intensities, raw.intensities[:, columns])
    np.testing.assert_allclose(back.scan_times, raw.scan_times, rtol=0, atol=1e-9)
    assert back.metadata == {"sample_name": "x", "intensity_units": "a.u."}


@pytest.mark.parametrize(
    ("stored", "short"),
    [
        (None, "a.u."),
        ("", "a.u."),
        ("Total Counts", "counts"),
        ("Arbitrary Intensity Units", "a.u."),
        ("ions/s", "ions/s"),
    ],
)
def test_intensity_units(stored, short):
    raw, _ = synthetic_run()
    ms = _msdata(raw)
    if stored is not None:
        ms.metadata["intensity_unit"] = stored
    assert from_msdata(ms).metadata["intensity_units"] == short


def test_text_formats_default_to_arbitrary_units(tmp_path):
    from pyGCxGC.ms.io import read_jcamp

    path = tmp_path / "run.jdx"
    path.write_text("##RETENTION_TIME= 1.0\n##XYDATA= (XY..XY)\n45, 10\n46, 20\n")
    assert read_jcamp(path).metadata["intensity_units"] == "a.u."


def test_from_signal_columns_and_attrs():
    chrom = from_signal(_signal(), {"sample_name": "x"})
    assert list(chrom.columns) == ["Ret.Time", "Ret.Time[s]", "Absolute Intensity"]
    np.testing.assert_array_equal(chrom["Ret.Time[s]"][:4], [0, 0.004, 0.008, 0.012])
    np.testing.assert_allclose(chrom["Ret.Time"], chrom["Ret.Time[s]"] / 60)
    assert chrom.attrs == {
        "sample_name": "x",
        "channel": "FID",
        "unit": "pA",
        "sampling_interval": 0.004,
    }


def test_andi_files_through_chromparser(tmp_path):
    raw, _ = synthetic_run()
    ms_path = write_andi(raw, tmp_path / "run.cdf")
    assert vendor_format(ms_path) == "andi-ms"
    assert vendor_kind(ms_path) == detect_kind(ms_path) == "ms"
    back = read_ms_file(ms_path)
    np.testing.assert_array_equal(back.intensities, raw.intensities)

    injection = chromparser.Injection("fid", signals={"FID": _signal(n=5000)})
    (fid_path,) = chromparser.write_andi(injection, tmp_path)
    assert vendor_kind(fid_path) == detect_kind(fid_path) == "fid"
    chrom = read_chromatogram(fid_path)
    assert len(chrom) == 5000
    assert chrom.attrs["channel"] == "FID"
    folded = parse_2D_chromatogram(fid_path, 4)
    expected = parse_2D_chromatogram(from_signal(_signal(n=5000)), 4)
    np.testing.assert_array_equal(folded.chrom_2D, expected.chrom_2D)


def test_channel_and_injection_choice(tmp_path):
    two = chromparser.Injection(
        "two", signals={"A": _signal(channel="A"), "B": _signal(channel="B")}
    )
    path = chromparser.write_hdf5([two, two], tmp_path / "two.h5")
    assert vendor_kind(path) == "fid"
    with pytest.raises(ValueError, match="2 injections"):
        read_signal(path)
    with pytest.raises(ValueError, match="several channels"):
        read_signal(path, injection=1)
    with pytest.raises(ValueError, match="no channel 'C'"):
        read_signal(path, channel="C", injection=1)
    assert read_signal(path, channel="B", injection=1).attrs["channel"] == "B"


def test_csv_files_are_not_vendor_files():
    assert vendor_format(EXAMPLE_FID) is None
    assert detect_kind(EXAMPLE_FID) == "fid"


@pytest.mark.skipif(not LOCAL_QGD.exists(), reason="no local Shimadzu data")
def test_local_shimadzu_files():
    from pyGCxGC.ms.data import estimate_modulation_time

    assert detect_kind(LOCAL_QGD) == "ms"
    assert detect_kind(LOCAL_GCD) == "fid"
    raw = read_ms_file(LOCAL_QGD)
    assert raw.intensities.shape == (63600, 456)
    assert (raw.mz[0], raw.mz[-1]) == (45, 500)
    assert raw.metadata["sample_name"] == "Clean PP_016_MS"
    (ms,) = chromparser.read_one(LOCAL_QGD).ms.values()
    np.testing.assert_array_equal(raw.intensities.sum(axis=1, dtype=np.float64), ms.tic)
    assert estimate_modulation_time(raw) == pytest.approx(20.0)

    chrom = read_chromatogram(LOCAL_GCD)
    assert len(chrom) == 1_590_000
    assert chrom.attrs["sampling_interval"] == 0.004
    assert chrom["Ret.Time[s]"].iloc[-1] == 6359.996
