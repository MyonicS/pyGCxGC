"""Tests for the GCxGC-MS functionality (synthetic data only)."""

from __future__ import annotations

import struct
from pathlib import Path

import numpy as np
import pytest
from synthetic import SPECTRA
from synthetic import lib_from_dict as _lib_from_dict
from synthetic import synthetic_run as _synthetic_run

from pyGCxGC.ms import (
    GCxGC_MS,
    SearchParameters,
    SpectralLibrary,
    composite_scores,
    estimate_modulation_time,
    load_library,
    read_andi,
    search_spectrum,
)
from pyGCxGC.ms.nist import (
    BOND_AROMATIC,
    NAME_TOKENS,
    decode_structure,
    format_cas,
    read_nist_mainlib,
    read_nist_ri,
    ri_by_compound,
)
from pyGCxGC.ms.peaks import detect_peaks, identify_peaks
from pyGCxGC.ms.structures import rdkit_available

LOCAL_NIST = Path(__file__).parents[1] / "local_data" / "MSSEARCH" / "MAINLIB"


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
    name: bytes,
    formula: str,
    cas: int,
    nist_no: int,
    mw: int,
    peaks: dict,
    rid: bytes = b"\0\0\0\0",
) -> bytes:
    masses = sorted(peaks)
    body = struct.pack("<HIIIHH", 0, cas, nist_no, 0, mw, 1)
    body = rid[:2] + body[2:10] + rid[2:] + body[12:]
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


def _coord(v: int) -> bytes:
    out = bytearray()
    while v > 126:
        out.append(0x7F)
        v -= 127
    while v < -126:
        out.append(0x81)
        v += 127
    out.append(v & 0xFF)
    return bytes(out)


def _endpoint(e: int) -> bytes:
    out = bytearray()
    while e >= 0x7F:
        out.append(0x7F)
        e -= 127
    out.append(e)
    return bytes(out)


def _struct_payload(seeds, chain=(), rings=(), labels=(), circles=(), orders=None):
    """Encode a drawing as in ``struct.db`` (see ``pyGCxGC.ms.nist``)."""
    n_bonds = len(seeds) + len(chain) + len(rings)
    orders = orders or [1] * n_bonds
    mask = sum((o - 1) << (2 * i) for i, o in enumerate(orders))
    out = bytearray([len(seeds), len(chain), len(rings), len(labels), len(circles)])
    out += mask.to_bytes((2 * n_bonds + 7) // 8, "little")
    for (x1, y1), (x2, y2) in seeds:
        out += _coord(x1) + _coord(y1) + _coord(x2) + _coord(y2)
    for (x, y), parent in chain:
        out += _coord(x) + _coord(y) + _endpoint(parent)
    for a, b in rings:
        out += _endpoint(a) + _endpoint(b)
    for target, code in labels:
        if isinstance(target, tuple):  # label without an atom
            out += b"\xff" + _coord(target[0]) + _coord(target[1])
        else:
            out += _endpoint(target)
        out += code.encode() + b"\0" if isinstance(code, str) else bytes([code])
    for x, y in circles:
        out += _coord(x) + _coord(y)
    return bytes(out)


# Own drawings (bond length 20), not NIST data.
ETHANOL = _struct_payload([((0, 0), (17, -10))], [((34, 0), 1)], labels=[(2, 2)])
BENZENE = _struct_payload(
    [((0, -20), (17, -10))],
    [((17, 10), 1), ((0, 20), 2), ((-17, 10), 4), ((-17, -10), 6)],
    rings=[(8, 0)],
    circles=[(0, 0)],
    orders=[BOND_AROMATIC] * 6,
)
SALT = _struct_payload(  # tetramethylammonium bromide, far from the origin
    [((200, 0), (220, 0))],
    [((180, 0), 0), ((200, 20), 0), ((200, -20), 0)],
    labels=[(0, "N+"), ((240, -150), "Br-")],
)
CHAIN = _struct_payload(  # C70: endpoints above 127 need the escape byte
    [((0, 0), (17, -10))],
    [((17 * i, -10 * (i % 2)), 1 if i == 2 else 2 * (i - 2)) for i in range(2, 70)],
)


def _write_mainlib(root: Path, drawings: dict[bytes, bytes], records: bytes) -> Path:
    """Write ``nist.db``, ``struct.db`` and ``struct.in6`` into ``root/MAINLIB``."""
    mainlib = root / "MAINLIB"
    mainlib.mkdir()
    header = bytearray(b"\xff" * 512)
    header[:128] = b"\0" * 128
    resource = b"\xfe\xfe" + struct.pack("<II", 31, 9) + b"\0" * 21
    (mainlib / "nist.db").write_bytes(bytes(header) + resource + records + b"\xff" * 64)
    db, entries = bytearray(b"\0"), []
    for rid, payload in drawings.items():
        entries.append(struct.pack("<I", len(db)) + rid)
        db += struct.pack("<H", len(payload)) + payload
    (mainlib / "struct.db").write_bytes(bytes(db))

    def node(items):
        raw = struct.pack("<IIHH", 0, 0, len(items), 8 * len(items)) + b"\0" * 6
        return (raw + b"".join(items)).ljust(0x200, b"\0")

    decoy = [struct.pack("<I", 12345) + rid for rid in drawings]  # not a leaf
    index = b"\xff" * 0x400 + node(entries) + node(decoy)
    (mainlib / "struct.in6").write_bytes(index)
    return mainlib


def test_nist_structure_decoding():
    s = decode_structure(ETHANOL)
    assert [a.element for a in s.atoms] == ["C", "C", "O"]
    assert [a.hydrogens for a in s.atoms] == [None, None, 1]
    assert s.bonds == [(0, 1, 1), (2, 1, 1)]
    assert s.stereo == b""

    s = decode_structure(BENZENE)
    assert {o for _, _, o in s.bonds} == {BOND_AROMATIC}
    assert (5, 0, BOND_AROMATIC) in s.bonds
    assert s.circles == [(0, 0)]

    s = decode_structure(SALT)
    assert (s.atoms[0].element, s.atoms[0].charge, s.atoms[0].x) == ("N", 1, 200)
    br = s.atoms[-1]
    assert (br.element, br.charge, br.hydrogens, br.x, br.y) == ("Br", -1, 0, 240, -150)
    assert len(s.bonds) == 4

    s = decode_structure(CHAIN)
    assert len(s.atoms) == 70
    assert s.bonds[-1] == (69, 68, 1)
    with pytest.raises(ValueError, match="Malformed"):
        decode_structure(ETHANOL[:-1])
    with pytest.raises(ValueError, match="label code"):
        decode_structure(ETHANOL[:-1] + bytes([0x1F]))  # unknown label code


def _structure_library(tmp_path):
    rids = [b"\0\1\0\0", b"\0\1\1\0", b"\0\2\0\0", b"\0\2\1\0"]
    peaks = {31: 999, 45: 500}
    records = b"".join(
        _nist_record(name, formula, 0, i, 46, peaks, rid=rid)
        for i, (name, formula, rid) in enumerate(
            [
                (b"Ethanol", "C2H6O", rids[0]),
                (b"Benzene", "C6H6", rids[1]),
                (b"Salt", "C4H12BrN", rids[2]),
                (b"No drawing", "X", rids[3]),
            ]
        )
    )
    drawings = {rids[0]: ETHANOL, rids[1]: BENZENE, rids[2]: SALT}
    return _write_mainlib(tmp_path, drawings, records)


def test_nist_structures_in_library(tmp_path):
    mainlib = _structure_library(tmp_path)
    data = read_nist_mainlib(mainlib)
    assert [bool(s) for s in data["nist_structure"]] == [True, True, True, False]
    lib = load_library(mainlib, cache_dir=tmp_path / "cache")
    again = load_library(mainlib, cache_dir=tmp_path / "cache")
    assert list(again.nist_structure) == data["nist_structure"]
    assert lib[3].smiles == ""


def _ctree_file(payloads: list[bytes]) -> bytes:
    """A c-tree variable-length data file holding ``payloads`` as active records."""
    header = bytearray(b"\xff" * 512)
    header[:128] = b"\0" * 128
    body = b"".join(
        b"\xfa\xfa" + struct.pack("<II", len(p) + 10, len(p)) + p for p in payloads
    )
    return bytes(header) + body + b"\xff" * 64


def _ri_record(key, name, formula, ri, phase_type, ri_type="Normal alkane RI", **kw):
    fields = dict.fromkeys(
        ("column_class", "phase", "length", "carrier", "support", "diameter", "film"),
        "",
    )
    fields.update(column_class="Capillary", length="30 m", diameter="250 um")
    fields.update(kw)
    program = ["Ramp", "50 C", "300 C", "5 K/min", "2 min", "10 min", "", "2001ABC/DEF"]
    values = [key, name, formula, ri, fields["column_class"], phase_type]
    values += [fields[k] for k in ("phase", "length", "carrier", "support")]
    values += [fields["diameter"], fields["film"], ri_type, *program]
    return "\t".join(values).encode("latin-1") + b"\0"


def test_nist_retention_indices(tmp_path):
    mainlib = tmp_path / "MAINLIB"
    mainlib.mkdir()
    records = [
        _ri_record("C124185", "Decane", "C10H22", "1000", "All column types"),
        _ri_record("C108883", "Toluene", "C7H8", "765", "SSNP", film="0.25 um"),
        _ri_record("C108883", "Toluene", "C7H8", "771", "semi-std-non-polar"),
        _ri_record(
            "C108883",
            "Toluene",
            "C7H8",
            "750",
            "Semi-standard non-polar",
            column_class="Packed",
        ),  # fmt: skip
        _ri_record("C108883", "Toluene", "C7H8", "210", "SSNP", ri_type="Lee RI"),
        _ri_record("C108883", "Toluene", "C7H8", "1040", "Standard polar"),
        _ri_record("U101479", '"1-Iodo-2-methylnonane', "C10H21I", "1350.5", "SSNP"),
    ]
    (mainlib / "ri.dat").write_bytes(_ctree_file(records))

    table = read_nist_ri(tmp_path)
    assert len(table) == 7
    assert table["cas"].iloc[1] == "108-88-3"
    assert table["nist_no"].tolist() == [0] * 6 + [101479]
    assert table["name"].iloc[6] == "1-Iodo-2-methylnonane"
    assert table["phase_type"].tolist()[:3] == ["all"] + ["semi-standard non-polar"] * 2
    assert table["ri_type"].iloc[4] == "lee"
    assert table["diameter_mm"].iloc[0] == pytest.approx(0.25)
    assert table["film_um"].iloc[1] == pytest.approx(0.25)
    assert table["rate_k_min"].iloc[0] == 5.0

    ssnp = ri_by_compound(table)
    assert ssnp.loc["C108883", "ri"] == 768.0  # no packed, Lee or polar values
    assert ssnp.loc["C108883", "n"] == 2
    assert ssnp.loc["C124185", "ri"] == 1000.0  # n-alkanes count for every phase
    assert ssnp.loc["U101479", "nist_no"] == 101479
    polar = ri_by_compound(table, "standard polar")
    assert polar["ri"].to_dict() == {"C108883": 1040.0, "C124185": 1000.0}


@pytest.mark.skipif(not LOCAL_NIST.exists(), reason="no local NIST library")
def test_local_nist_retention_indices():
    table = read_nist_ri(LOCAL_NIST)
    assert len(table) > 400_000
    ssnp = ri_by_compound(table).set_index("cas")
    assert ssnp.loc["544-76-3", "ri"] == 1600.0  # hexadecane
    assert abs(ssnp.loc["108-88-3", "ri"] - 766) < 10  # toluene on DB-5 type
    assert abs(ssnp.loc["91-20-3", "ri"] - 1182) < 10  # naphthalene


@pytest.mark.skipif(not rdkit_available(), reason="RDKit is not installed")
def test_nist_structure_smiles(tmp_path):
    lib = SpectralLibrary.from_nist(_structure_library(tmp_path))
    assert [lib[i].smiles for i in range(4)] == [
        "CCO",
        "c1ccccc1",
        "C[N+](C)(C)C.[Br-]",
        "",
    ]
    assert lib[0].inchikey == "LFQSCWFLJHTTHZ-UHFFFAOYSA-N"
    assert lib.smiles[0] == ""  # converted on demand, not stored


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


@pytest.mark.skipif(
    not (LOCAL_NIST.exists() and rdkit_available()), reason="no local NIST or RDKit"
)
def test_local_nist_structures():
    from rdkit import Chem
    from rdkit.Chem.rdMolDescriptors import CalcMolFormula

    lib = load_library(LOCAL_NIST)
    rng = np.random.default_rng(0)
    sample = rng.choice(len(lib), 1000, replace=False)
    matched = 0
    for i in sample:
        smiles, _ = lib.structure_ids(i)
        mol = Chem.MolFromSmiles(smiles) if smiles else None
        if mol is not None:
            formula = CalcMolFormula(mol).replace("+", "").replace("-", "")
            matched += formula == lib.formula[i]
    assert matched >= 970  # all formulas match; charges and D are written differently
    toluene = [i for i, n in enumerate(lib.compound_name) if n == "Toluene"]
    assert lib[toluene[0]].smiles == "Cc1ccccc1"


# ------------------------------------------------------------------ baseline
def _old_background(raw, n, shift, percentile=25.0):
    """The per-modulation background of pyGCxGC <= 0.0.1 (reference)."""
    data = raw.intensities
    n_scans = data.shape[0]
    n_mod = int(np.ceil((n_scans - shift) / n))
    base = np.zeros((n_mod, data.shape[1]), dtype=np.float32)
    noise = np.zeros_like(base)
    for i in range(n_mod):
        lo, hi = max(i * n + shift, 0), min((i + 1) * n + shift, n_scans)
        if hi - lo < 3:
            if i:
                base[i], noise[i] = base[i - 1], noise[i - 1]
            continue
        block = data[lo:hi]
        base[i] = np.percentile(block, percentile, axis=0)
        med = np.median(block, axis=0)
        noise[i] = 1.4826 * np.median(np.abs(block - med), axis=0)
    floor = np.maximum(np.median(noise[noise > 0]) * 0.1, 1.0)
    return base, np.maximum(noise, floor)


@pytest.mark.parametrize("shift", [0, 5, -3])
def test_default_baseline_unchanged(shift):
    raw, _ = _synthetic_run()
    chrom = GCxGC_MS(raw, modulation_time=4.0, shift=shift)
    base, noise = _old_background(raw, 80, shift)
    assert chrom.baseline.mode == "modulation_percentile"
    np.testing.assert_array_equal(chrom.baseline.levels, base)
    np.testing.assert_array_equal(chrom.baseline.noise, noise)
    mods = np.clip((np.arange(chrom.n_scans) - shift) // 80, 0, len(base) - 1)
    tic = raw.intensities.sum(axis=1) - base[mods].sum(axis=1)
    np.testing.assert_array_equal(chrom.tic_2d, chrom.fold(tic))
    scan = 845
    s = chrom.spectrum(scan, n_average=3)
    scans = np.arange(scan - 1, scan + 2)
    expected = raw.intensities[scans].astype(np.float64).sum(axis=0)
    expected -= base[mods[scans]].sum(axis=0)
    expected[expected < 3 * noise[mods[scans]].mean(axis=0) * np.sqrt(3)] = 0
    np.testing.assert_array_equal(s.intensity, np.clip(expected, 0, None))


def test_make_baseline():
    from pyGCxGC.ms.baseline import ModulationPercentile, make_baseline

    assert make_baseline() == ModulationPercentile(25)
    model = make_baseline("rolling_percentile", window=3)
    assert model.to_dict() == {
        "mode": "rolling_percentile",
        "percentile": 25.0,
        "window": 3,
    }
    assert make_baseline(model.to_dict()) == model
    assert make_baseline(model) is not model
    assert model.describe() == "rolling_percentile(percentile=25, window=3)"
    local = make_baseline({"mode": "local", "gap": 2})
    assert local.image_model == ModulationPercentile()
    with pytest.raises(ValueError, match="Unknown baseline"):
        make_baseline("asymmetric")
    with pytest.raises(ValueError, match="image_model"):
        make_baseline("local", image_model="local")


def test_no_baseline_keeps_raw_intensities():
    raw, _ = _synthetic_run()
    chrom = GCxGC_MS(raw, modulation_time=4.0, baseline="none")
    np.testing.assert_allclose(chrom.tic_2d, chrom.fold(raw.intensities.sum(axis=1)))
    s = chrom.spectrum(845, n_average=1)
    np.testing.assert_allclose(s.intensity, raw.intensities[845])  # no zeroing


def test_rolling_baseline_less_disturbed_by_broad_compound():
    raw, _ = _synthetic_run(seed=1)
    raw.intensities[20 * 80 : 21 * 80, 128 - 45] += 3000.0  # fills one modulation
    per_mod = GCxGC_MS(raw, 4.0)
    rolling = GCxGC_MS(raw, 4.0, baseline={"mode": "rolling_percentile", "window": 2})
    col = 128 - 45
    assert per_mod.baseline.levels[20, col] > 2500  # swallowed by the baseline
    assert rolling.baseline.levels[20, col] < 200
    assert rolling.eic_2d(128)[:, 20].sum() > 0.9 * 3000 * 80


def test_local_and_reference_baselines():
    raw, truth = _synthetic_run()
    chrom = GCxGC_MS(raw, 4.0)
    _, mod, rt2 = truth[0]
    scan = chrom.find_apex(chrom.scan_at(round(rt2 / 0.05), mod))
    default = chrom.spectrum(scan, n_average=3)
    tic = chrom.tic_2d.copy()

    chrom.set_baseline("local", gap=12, width=5)
    np.testing.assert_array_equal(chrom.tic_2d, tic)  # the image is unchanged
    model = chrom.baseline
    background = model.background_scans(chrom, np.arange(scan - 1, scan + 2))
    assert len(background) == 10
    assert all(chrom.pixel_of_scan(int(b))[1] == mod for b in background)
    distance = np.abs(background - scan)
    assert distance.min() >= 12
    assert distance.max() < 17
    local = chrom.spectrum(scan, n_average=3).nonzero()
    assert local.mz[np.argsort(-local.intensity)[:2]].tolist() == [91, 92]
    np.testing.assert_allclose(
        local.intensity.max(), default.intensity.max(), rtol=0.05
    )

    far = chrom.scan_at(5, 2)  # background only
    chrom.set_baseline({"mode": "reference", "scan": far, "n_scans": 5})
    ref = chrom.spectrum(scan, n_average=3).nonzero()
    assert ref.mz[np.argmax(ref.intensity)] == 91
    # peak identification ignores spectra-only models
    peaks = detect_peaks(chrom, snr=20)
    table, _ = identify_peaks(chrom, peaks, _lib_from_dict())
    assert set(table["name"]) == {"Toluene", "Naphthalene", "Decane"}


def test_snr_zero_disables_channel_zeroing():
    raw, _ = _synthetic_run()
    chrom = GCxGC_MS(raw, 4.0)
    zeroed = chrom.spectrum(845, n_average=3)
    kept = chrom.spectrum(845, n_average=3, snr=0)
    assert (kept.intensity > 0).sum() > (zeroed.intensity > 0).sum()


# ------------------------------------------------------------------ identifiers
MONA_RECORD = """Name: Toluene
DB#: XX000001
InChIKey: YXFVVABEGXRONW-UHFFFAOYSA-N
Spectrum_type: MS1
Instrument_type: EI-B
Formula: C7H8
MW: 92
Comments: "SMILES=Cc1ccccc1" "cas=108-88-3" "pubchem cid=1140" "InChI=InChI=1S/C7H8/c1-7-5-3-2-4-6-7/h2-6H,1H3" "computed SMILES=CC1=CC=CC=C1" "license=CC BY"
Num Peaks: 3
91 999
92 776
65 121

Name: Unknown thing
Num Peaks: 1
50 10
"""


def test_msp_structure_identifiers(tmp_path):
    path = tmp_path / "mona.msp"
    path.write_text(MONA_RECORD)
    lib = SpectralLibrary.from_msp(path)
    e = lib[0]
    assert e.smiles == "Cc1ccccc1"
    assert e.inchi == "InChI=1S/C7H8/c1-7-5-3-2-4-6-7/h2-6H,1H3"
    assert e.inchikey == "YXFVVABEGXRONW-UHFFFAOYSA-N"
    assert e.pubchem_cid == 1140
    assert e.cas == "108-88-3"
    assert (lib[1].smiles, lib[1].inchikey, lib[1].pubchem_cid) == ("", "", 0)
    # MSP and npz round trips keep the identifiers
    lib.to_msp(tmp_path / "out.msp")
    back = SpectralLibrary.from_msp(tmp_path / "out.msp")[0]
    assert (back.smiles, back.inchi, back.inchikey, back.pubchem_cid) == (
        e.smiles,
        e.inchi,
        e.inchikey,
        e.pubchem_cid,
    )
    lib.save(tmp_path / "lib.npz")
    cached = SpectralLibrary.load(tmp_path / "lib.npz")[0]
    assert (cached.smiles, cached.inchikey, cached.pubchem_cid) == (
        e.smiles,
        e.inchikey,
        e.pubchem_cid,
    )


def test_old_npz_cache_still_loads(tmp_path):
    from pyGCxGC.ms import library as libmod

    lib = _lib_from_dict()
    path = lib.save(tmp_path / "v1.npz")
    with np.load(path) as z:
        arrays = {
            k: z[k]
            for k in z.files
            if not k.startswith(("smiles", "inchi", "pubchem", "nist_structure"))
        }
    arrays["version"] = np.array([1])
    np.savez(path, **arrays)
    old = SpectralLibrary.load(path)
    assert old.compound_name.tolist() == lib.compound_name.tolist()
    assert old[0].smiles == ""
    assert old[0].pubchem_cid == 0
    # converted caches are keyed by the cache version, so old ones rebuild
    msp = tmp_path / "lib.msp"
    lib.to_msp(msp)
    assert libmod.CACHE_VERSION == 3
    cache = libmod.library_cache_file(msp, tmp_path / "cache")
    load_library(msp, cache_dir=tmp_path / "cache")
    assert cache.exists()
