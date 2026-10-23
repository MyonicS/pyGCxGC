"""Tests for the GCxGC-MS functionality (synthetic data only)."""

from __future__ import annotations

import struct
from pathlib import Path

import numpy as np
import pytest

from pyGCxGC.ms import (
    GCxGC_MS,
    RawMSData,
    SearchParameters,
    SpectralLibrary,
    composite_scores,
    estimate_modulation_time,
    load_library,
    read_andi,
    search_spectrum,
)
from pyGCxGC.ms.nist import NAME_TOKENS, format_cas, read_nist_mainlib
from pyGCxGC.ms.peaks import detect_peaks, identify_peaks

LOCAL_NIST = Path(__file__).parents[1] / "local_data" / "MSSEARCH" / "MAINLIB"

SPECTRA = {
    "Toluene": ({91: 999, 92: 776, 65: 121, 63: 74, 51: 64, 50: 41}, "C7H8", 92),
    "Naphthalene": (
        {128: 999, 127: 130, 129: 110, 102: 80, 64: 50, 51: 60},
        "C10H8",
        128,
    ),
    "Decane": (
        {57: 999, 43: 900, 71: 520, 85: 330, 99: 80, 113: 40, 142: 60},
        "C10H22",
        142,
    ),
    "Heavy": ({57: 100, 300: 500, 301: 300, 820: 999}, "C60H100", 820),
}


def _lib_from_dict(spectra=SPECTRA) -> SpectralLibrary:
    names = list(spectra)
    pairs = [
        (np.array(list(p)), np.array(list(p.values()), float))
        for p, _, _ in spectra.values()
    ]
    metadata = {
        "name": names,
        "formula": [v[1] for v in spectra.values()],
        "mw": [v[2] for v in spectra.values()],
    }
    return SpectralLibrary.from_spectra(pairs, name="test", metadata=metadata)


# ------------------------------------------------------------------ NIST
def _encode_masses(masses: list[int]) -> bytes:
    out = bytearray(b"\xff" * (masses[0] // 255))
    out.append(masses[0] % 255)
    i = 1
    while i < len(masses):
        run = 0
        while (
            i + run < len(masses)
            and masses[i + run] == masses[i + run - 1] + 1
            and run < 50
        ):
            run += 1
        if run >= 2:
            out.append(0x101 - run)
            i += run
            continue
        gap = masses[i] - masses[i - 1]
        if gap < 0xCC:
            out.append(gap)
        else:
            out.extend(b"\xcc" * (gap // 0xCC))
            out.append(gap % 0xCC)
        i += 1
    return bytes(out)


def _encode_abundances(values: list[int]) -> bytes:
    out = bytearray()
    for v in values:
        if v < 0xFA:
            out.append(v)
        else:
            q, r = divmod(v, 5)
            out += bytes([0xFF - r, q])
    return bytes(out)


def _nist_record(
    name: bytes, formula: str, cas: int, nist_no: int, mw: int, peaks: dict
) -> bytes:
    masses = sorted(peaks)
    body = struct.pack("<HIIIHH", 0, cas, nist_no, 0, mw, 1)
    body += name + b"\0" + formula.encode() + b"\0"
    body += struct.pack("<h", len(masses))
    body += _encode_masses(masses) + _encode_abundances([peaks[m] for m in masses])
    return b"\xfa\xfa" + struct.pack("<II", len(body) + 10, len(body)) + body


def test_nist_reader_roundtrip(tmp_path):
    peaks_a = {12: 38, 13: 106, 14: 204, 15: 887, 16: 999, 17: 16}
    peaks_b = {45: 5, 50: 300, 51: 251, 300: 999, 512: 77, 513: 2}
    token = bytes([0x80 + NAME_TOKENS.index("methyl")])
    records = _nist_record(b"Methane", "CH4", 74828, 61313, 16, peaks_a)
    records += _nist_record(b"Di" + token + b"thing", "C2H6X", 0, 7, 513, peaks_b)
    header = bytearray(b"\xff" * 512)
    header[:128] = b"\0" * 128
    resource = b"\xfe\xfe" + struct.pack("<II", 31, 9) + b"\0" * 21
    mainlib = tmp_path / "MAINLIB"
    mainlib.mkdir()
    (mainlib / "nist.db").write_bytes(bytes(header) + resource + records + b"\xff" * 64)

    data = read_nist_mainlib(tmp_path)
    assert data["name"] == ["Methane", "Dimethylthing"]
    assert data["cas"] == ["74-82-8", ""]
    assert data["nist_no"] == [61313, 7]
    assert data["mw"] == [16, 513]
    first = slice(data["offsets"][0], data["offsets"][1])
    second = slice(data["offsets"][1], data["offsets"][2])
    assert data["mz"][first].tolist() == sorted(peaks_a)
    assert data["intensity"][first].tolist() == [peaks_a[m] for m in sorted(peaks_a)]
    assert data["mz"][second].tolist() == sorted(peaks_b)
    assert data["intensity"][second].tolist() == [peaks_b[m] for m in sorted(peaks_b)]

    lib = load_library(mainlib, cache_dir=tmp_path / "cache")
    assert len(lib) == 2
    assert list((tmp_path / "cache").glob("*.npz"))
    assert len(load_library(mainlib, cache_dir=tmp_path / "cache")) == 2


def test_format_cas():
    assert format_cas(71432) == "71-43-2"
    assert format_cas(0) == ""


# ------------------------------------------------------------------ library
def test_msp_roundtrip(tmp_path):
    lib = _lib_from_dict()
    path = tmp_path / "lib.msp"
    lib.to_msp(path)
    back = SpectralLibrary.from_msp(path)
    assert len(back) == len(lib)
    for i in range(len(lib)):
        a, b = lib[i], back[i]
        assert a.name == b.name
        assert a.mz.tolist() == b.mz.tolist()
        np.testing.assert_allclose(a.intensity, b.intensity, atol=0.6)


def test_library_save_load_merge(tmp_path):
    lib = _lib_from_dict()
    lib.save(tmp_path / "lib.npz")
    back = SpectralLibrary.load(tmp_path / "lib.npz")
    assert back.compound_name.tolist() == lib.compound_name.tolist()
    np.testing.assert_array_equal(back.mz, lib.mz)
    merged = lib.merge(back)
    assert len(merged) == 2 * len(lib)
    assert merged[len(lib)].name == lib[0].name


def test_msp_skips_non_ei(tmp_path):
    path = tmp_path / "mixed.msp"
    path.write_text(
        "Name: A\nSpectrum_type: MS1\nInstrument_type: EI-B\nNum Peaks: 2\n50 10\n60 100\n\n"
        "Name: B\nSpectrum_type: MS2\nPrecursorMZ: 200\nNum Peaks: 1\n50 10\n\n"
        "Name: C\nInstrument_type: CI-B\nNum Peaks: 1\n50 10\n"
    )
    lib = SpectralLibrary.from_msp(path)
    assert lib.compound_name.tolist() == ["A"]


# ------------------------------------------------------------------ search
def test_composite_scores_identical():
    mz = np.array([50, 51, 63, 65, 91, 92])
    it = np.array([41, 64, 74, 121, 999, 776], float)
    mf, rmf = composite_scores(mz, it, mz, it)
    assert mf == pytest.approx(1000)
    assert rmf == pytest.approx(1000)


def test_reverse_match_ignores_extra_peaks():
    mz = np.array([91, 92, 65])
    it = np.array([999, 776, 121], float)
    mz_u = np.append(mz, [150, 151])
    it_u = np.append(it, [600, 500])
    mf, rmf = composite_scores(mz_u, it_u, mz, it)
    assert rmf == pytest.approx(1000)
    assert mf < 900


def test_search_finds_correct_compound():
    lib = _lib_from_dict()
    peaks = SPECTRA["Naphthalene"][0]
    mz = np.array(list(peaks))
    it = np.array(list(peaks.values()), float) * np.linspace(0.9, 1.1, len(peaks))
    hits = search_spectrum(mz, it, lib, mz_min=45, mz_max=500)
    assert hits[0].name == "Naphthalene"
    assert hits[0].match > 900
    assert [h.rank for h in hits] == list(range(1, len(hits) + 1))


def test_search_respects_mass_range():
    lib = _lib_from_dict()
    peaks = {k: v for k, v in SPECTRA["Decane"][0].items() if k >= 45}
    hits = search_spectrum(
        np.array(list(peaks)),
        np.array(list(peaks.values()), float),
        lib,
        SearchParameters(mz_min=45, mz_max=500),
    )
    assert hits[0].name == "Decane"
    assert hits[0].match > 950  # m/z 43 of the library spectrum is ignored


# ------------------------------------------------------------------ data
def _synthetic_run(modulation=4.0, dt=0.05, n_mod=40, seed=0):
    """GCxGC-MS run with three compounds and a per-ion baseline."""
    rng = np.random.default_rng(seed)
    n_per = round(modulation / dt)
    n_scans = n_per * n_mod
    mz = np.arange(45, 201)
    data = 50.0 + 5.0 * rng.standard_normal((n_scans, len(mz)))
    data += np.linspace(0, 30, len(mz))[None, :]  # ion dependent background
    truth = []
    for name, rt1_mod, rt2 in (
        ("Toluene", 10, 1.0),
        ("Naphthalene", 25, 2.5),
        ("Decane", 18, 1.6),
    ):
        peaks = SPECTRA[name][0]
        spec = np.zeros(len(mz))
        for m, v in peaks.items():
            if 45 <= m <= 200:
                spec[m - 45] = v
        for mod in range(rt1_mod - 2, rt1_mod + 3):
            amp = 4e3 * np.exp(-0.5 * ((mod - rt1_mod) / 1.0) ** 2)
            centre = mod * n_per + rt2 / dt
            idx = np.arange(mod * n_per, (mod + 1) * n_per)
            profile = amp * np.exp(-0.5 * ((idx - centre) / 2.0) ** 2)
            data[idx] += profile[:, None] * spec[None, :] / 999
        truth.append((name, rt1_mod, rt2))
    raw = RawMSData(
        np.arange(n_scans) * dt, mz, data.astype(np.float32), {"source": "synthetic"}
    )
    return raw, truth


def test_fold_and_spectrum():
    raw, truth = _synthetic_run()
    chrom = GCxGC_MS(raw, modulation_time=4.0)
    assert chrom.tic_2d.shape == (80, 40)
    assert chrom.chrom_2D.shape == (80, 40)
    _, mod, rt2 = truth[0]  # toluene
    j = round(rt2 / 0.05)
    scan = chrom.find_apex(chrom.scan_at(j - 2, mod))
    assert chrom.pixel_of_scan(scan) == (j, mod)
    spec = chrom.spectrum(scan, n_average=3).nonzero()
    top = spec.mz[np.argsort(-spec.intensity)[:2]].tolist()
    assert top == [91, 92]
    # background removed: no signal at an m/z the compound does not have
    assert spec.intensity[spec.mz == 150].sum() == 0
    rt1, rt2_found = chrom.retention_times(scan)
    assert rt2_found == pytest.approx(rt2)
    assert rt1 == pytest.approx(mod * 4.0 / 60)


def test_phase_shift_moves_peaks_down():
    raw, truth = _synthetic_run()
    a = GCxGC_MS(raw, modulation_time=4.0)
    b = GCxGC_MS(raw, modulation_time=4.0, shift=5)
    ja = np.argmax(a.tic_2d[:, truth[0][1]])
    jb = np.argmax(b.tic_2d[:, truth[0][1]])
    assert jb == ja - 5


def test_eic_and_modulation_estimate():
    raw, truth = _synthetic_run()
    chrom = GCxGC_MS(raw, modulation_time=4.0)
    eic = chrom.eic_2d(128)
    j, i = np.unravel_index(np.argmax(eic), eic.shape)
    assert i == truth[1][1]
    assert estimate_modulation_time(raw, min_s=1, max_s=10) == pytest.approx(4.0)


def test_modulation_must_be_multiple_of_sampling():
    raw, _ = _synthetic_run()
    with pytest.raises(ValueError, match="integer multiple"):
        GCxGC_MS(raw, modulation_time=4.03)


def test_detect_and_identify_peaks():
    raw, truth = _synthetic_run()
    chrom = GCxGC_MS(raw, modulation_time=4.0)
    peaks = detect_peaks(chrom, snr=20)
    assert len(peaks) == 3
    table, hits = identify_peaks(chrom, peaks, _lib_from_dict())
    found = {
        row["name"]: (row["mod_index"], row["rt2_s"]) for _, row in table.iterrows()
    }
    for name, mod, rt2 in truth:
        assert found[name][0] == mod
        assert found[name][1] == pytest.approx(rt2, abs=0.06)
    assert (table["match"] > 850).all()
    assert set(hits) == set(table["peak_id"])


def test_read_andi_roundtrip(tmp_path):
    from scipy.io import netcdf_file

    raw, _ = _synthetic_run()
    path = tmp_path / "run.cdf"
    scans, n_mz = raw.intensities.shape
    with netcdf_file(path, "w") as f:
        f.createDimension("scan_number", scans)
        f.createDimension("point_number", scans * n_mz)
        t = f.createVariable("scan_acquisition_time", "d", ("scan_number",))
        t[:] = raw.scan_times
        idx = f.createVariable("scan_index", "i", ("scan_number",))
        idx[:] = np.arange(scans) * n_mz
        cnt = f.createVariable("point_count", "i", ("scan_number",))
        cnt[:] = n_mz
        mv = f.createVariable("mass_values", "f", ("point_number",))
        mv[:] = np.tile(raw.mz + 0.05, scans)
        iv = f.createVariable("intensity_values", "f", ("point_number",))
        iv[:] = raw.intensities.ravel()
    back = read_andi(path)
    np.testing.assert_array_equal(back.mz, raw.mz)
    np.testing.assert_allclose(back.intensities, raw.intensities, rtol=1e-6)
    np.testing.assert_allclose(back.scan_times, raw.scan_times)


@pytest.mark.skipif(not LOCAL_NIST.exists(), reason="no local NIST library")
def test_local_nist_library():
    lib = load_library(LOCAL_NIST)
    assert len(lib) > 1000
    idx = [i for i, n in enumerate(lib.compound_name) if n == "Toluene"]
    assert idx
    entry = lib[idx[0]]
    assert entry.intensity.max() == 999
    assert entry.mz[np.argmax(entry.intensity)] == 91
