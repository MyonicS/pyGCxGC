"""Synthetic GCxGC-MS data and libraries shared by the tests."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from pyGCxGC.ms import RawMSData, SpectralLibrary

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

# structure identifiers as in a MoNA export; Decane has only a CAS number, so
# its structure needs the online lookup
IDENTIFIERS = {
    "Toluene": {
        "cas": "108-88-3",
        "smiles": "Cc1ccccc1",
        "inchikey": "YXFVVABEGXRONW-UHFFFAOYSA-N",
    },
    "Naphthalene": {"cas": "91-20-3", "smiles": "c1ccc2ccccc2c1"},
    "Decane": {"cas": "124-18-5"},
}


def lib_from_dict(spectra=SPECTRA) -> SpectralLibrary:
    names = list(spectra)
    pairs = [
        (np.array(list(p)), np.array(list(p.values()), float))
        for p, _, _ in spectra.values()
    ]
    metadata = {
        "name": names,
        "formula": [v[1] for v in spectra.values()],
        "mw": [v[2] for v in spectra.values()],
        "cas": [IDENTIFIERS.get(n, {}).get("cas", "") for n in names],
        "smiles": [IDENTIFIERS.get(n, {}).get("smiles", "") for n in names],
        "inchikey": [IDENTIFIERS.get(n, {}).get("inchikey", "") for n in names],
    }
    return SpectralLibrary.from_spectra(pairs, name="test", metadata=metadata)


def synthetic_run(modulation=4.0, dt=0.05, n_mod=40, seed=0):
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


def write_andi(raw: RawMSData, path: str | Path) -> Path:
    """Write ``raw`` as a minimal ANDI-MS netCDF file."""
    from scipy.io import netcdf_file

    path = Path(path)
    scans, n_mz = raw.intensities.shape
    with netcdf_file(path, "w") as f:
        f.experiment_type = "Centroided Mass Spectrum"
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
    return path


# ------------------------------------------------------------------ GCxGC pair
# A GCxGC-FID run and a GCxGC-MS run of the same sample (spec mcp.md §12): an
# n-alkane line, a monoaromatic band, diaromatics, a phenol and a solvent peak.
# The MS detector sees everything 0.5 s later than the FID (5 MS scans).
# Spectra are made up for the tests (not copied from any library).
PAIR_MODULATION = 6.0  # s
PAIR_MS_DT = 0.1  # s, 10 Hz
PAIR_FID_DT = 0.02  # s, 50 Hz
PAIR_N_MOD = 100
PAIR_DELAY = 0.5  # s, MS later than FID
PAIR_MZ = (40, 200)


def _alkane_spectrum(n: int) -> dict[int, float]:
    peaks = {41: 350.0, 55: 150.0, 56: 120.0, 69: 80.0, 70: 90.0}
    rel = {43: 800.0, 57: 999.0, 71: 600.0, 85: 400.0}
    for k in range(3, n):
        m = 14 * k + 1
        peaks[m] = rel.get(m, 400.0 * 0.62 ** (k - 6))
    peaks[14 * n + 2] = 40.0
    return peaks


# name: (spectrum, formula, MW, SMILES, class, subclass, (modulation, RT2 s on
# the FID), relative amount); position None = library only
PAIR_COMPOUNDS: dict[str, tuple] = {
    "Dichloromethane": (
        {49: 999, 84: 750, 86: 480, 51: 320, 47: 120, 88: 80},
        "CH2Cl2",
        84,
        "ClCCl",
        "halogenated",
        "",
        None,
        0.0,
    ),
    **{
        f"n-{name}": (
            _alkane_spectrum(n),
            f"C{n}H{2 * n + 2}",
            14 * n + 2,
            "C" * n,
            "n_alkanes",
            "",
            (12.3 + 10 * (n - 8), 1.0 + 0.05 * (n - 8)),
            1.0,
        )
        for n, name in zip(
            range(8, 15),
            (
                "Octane",
                "Nonane",
                "Decane",
                "Undecane",
                "Dodecane",
                "Tridecane",
                "Tetradecane",
            ),
            strict=True,
        )
    },
    "2-Methylnonane": (
        {43: 999, 57: 700, 71: 300, 85: 180, 41: 400, 127: 80, 142: 15, 56: 120},
        "C10H22",
        142,
        "CCCCCCCC(C)C",
        "isoalkanes",
        "",
        (29.4, 0.95),
        0.6,
    ),
    "Toluene": (
        {91: 999, 92: 780, 65: 120, 63: 70, 51: 60, 50: 40},
        "C7H8",
        92,
        "Cc1ccccc1",
        "monoaromatics",
        "alkylbenzenes",
        (15.2, 2.0),
        1.2,
    ),
    "Ethylbenzene": (
        {91: 999, 106: 300, 105: 80, 77: 90, 65: 60, 51: 100, 78: 70},
        "C8H10",
        106,
        "CCc1ccccc1",
        "monoaromatics",
        "alkylbenzenes",
        (24.6, 2.2),
        0.8,
    ),
    "o-Xylene": (
        {91: 999, 106: 600, 105: 250, 77: 130, 65: 50, 51: 120, 79: 80},
        "C8H10",
        106,
        "Cc1ccccc1C",
        "monoaromatics",
        "alkylbenzenes",
        (27.7, 2.45),
        0.9,
    ),
    "Styrene": (
        {104: 999, 103: 450, 78: 330, 51: 250, 77: 180, 63: 50, 52: 120},
        "C8H8",
        104,
        "C=Cc1ccccc1",
        "monoaromatics",
        "alkenylbenzenes",
        (30.6, 2.7),
        0.5,
    ),
    "Propylbenzene": (
        {91: 999, 120: 250, 92: 90, 65: 110, 77: 40, 51: 50},
        "C9H12",
        120,
        "CCCc1ccccc1",
        "monoaromatics",
        "alkylbenzenes",
        (35.3, 2.3),
        0.7,
    ),
    "1,2,4-Trimethylbenzene": (
        {105: 999, 120: 600, 119: 120, 91: 120, 77: 100, 79: 60, 51: 50},
        "C9H12",
        120,
        "Cc1ccc(C)c(C)c1",
        "monoaromatics",
        "alkylbenzenes",
        (40.4, 2.6),
        0.8,
    ),
    "Indane": (
        {117: 999, 118: 650, 115: 380, 91: 250, 116: 100, 63: 40, 51: 50},
        "C9H10",
        118,
        "c1ccc2c(c1)CCC2",
        "monoaromatics",
        "naphthenobenzenes",
        (43.5, 3.0),
        0.4,
    ),
    "Butylbenzene": (
        {91: 999, 92: 550, 134: 280, 65: 90, 105: 60, 51: 40},
        "C10H14",
        134,
        "CCCCc1ccccc1",
        "monoaromatics",
        "alkylbenzenes",
        (46.3, 2.4),
        0.6,
    ),
    "Tetralin": (
        {104: 999, 132: 650, 91: 400, 117: 200, 131: 90, 115: 110, 51: 60},
        "C10H12",
        132,
        "c1ccc2c(c1)CCCC2",
        "monoaromatics",
        "naphthenobenzenes",
        (54.4, 3.1),
        0.4,
    ),
    "Pentylbenzene": (
        {91: 999, 92: 520, 148: 250, 105: 70, 65: 80, 77: 30},
        "C11H16",
        148,
        "CCCCCc1ccccc1",
        "monoaromatics",
        "alkylbenzenes",
        (56.6, 2.5),
        0.5,
    ),
    "Hexylbenzene": (
        {92: 999, 91: 900, 162: 230, 105: 60, 65: 70, 77: 30},
        "C12H18",
        162,
        "CCCCCCc1ccccc1",
        "monoaromatics",
        "alkylbenzenes",
        (66.5, 2.6),
        0.5,
    ),
    "Phenol": (
        {94: 999, 66: 400, 65: 280, 95: 70, 55: 70, 63: 50},
        "C6H6O",
        94,
        "Oc1ccccc1",
        "oxygenates",
        "",
        (38.3, 3.5),
        0.5,
    ),
    "Naphthalene": (
        {128: 999, 127: 130, 129: 110, 102: 80, 64: 50, 51: 60},
        "C10H8",
        128,
        "c1ccc2ccccc2c1",
        "diaromatics",
        "",
        (58.5, 4.2),
        0.7,
    ),
    "2-Methylnaphthalene": (
        {142: 999, 141: 800, 115: 280, 139: 100, 143: 120, 71: 50, 63: 40},
        "C11H10",
        142,
        "Cc1ccc2ccccc2c1",
        "diaromatics",
        "",
        (68.4, 4.3),
        0.6,
    ),
    "Biphenyl": (
        {154: 999, 153: 400, 152: 300, 155: 130, 76: 120, 77: 60, 51: 50},
        "C12H10",
        154,
        "c1ccc(-c2ccccc2)cc1",
        "diaromatics",
        "",
        (75.5, 4.4),
        0.5,
    ),
    "1,6-Dimethylnaphthalene": (
        {156: 999, 141: 800, 155: 400, 115: 220, 157: 130, 128: 60},
        "C12H12",
        156,
        "Cc1ccc2cc(C)ccc2c1",
        "diaromatics",
        "",
        (78.4, 4.5),
        0.4,
    ),
    # library only: isomers with near-identical spectra and a hit without a
    # structure (formula fallback)
    "m-Xylene": (
        {91: 999, 106: 650, 105: 280, 77: 120, 65: 45, 51: 110, 79: 85},
        "C8H10",
        106,
        "Cc1cccc(C)c1",
        "monoaromatics",
        "alkylbenzenes",
        None,
        0.0,
    ),
    "1,3,5-Trimethylbenzene": (
        {105: 999, 120: 620, 119: 130, 91: 110, 77: 95, 79: 55, 51: 45},
        "C9H12",
        120,
        "Cc1cc(C)cc(C)c1",
        "monoaromatics",
        "alkylbenzenes",
        None,
        0.0,
    ),
    "Isopropylbenzene": (
        {105: 999, 120: 300, 77: 100, 79: 80, 51: 70},
        "C9H12",
        120,
        "",
        "monoaromatics",
        "alkylbenzenes",
        None,
        0.0,
    ),
}


def pair_library(name: str = "pair") -> SpectralLibrary:
    """Test library with SMILES for the compounds of :func:`synthetic_pair`."""
    names = list(PAIR_COMPOUNDS)
    pairs = [
        (np.array(list(c[0]), float), np.array(list(c[0].values()), float))
        for c in PAIR_COMPOUNDS.values()
    ]
    metadata = {
        "name": names,
        "formula": [c[1] for c in PAIR_COMPOUNDS.values()],
        "mw": [c[2] for c in PAIR_COMPOUNDS.values()],
        "smiles": [c[3] for c in PAIR_COMPOUNDS.values()],
        "cas": [f"{100 + k}-00-{k % 10}" for k in range(len(names))],
    }
    return SpectralLibrary.from_spectra(pairs, name=name, metadata=metadata)


def _modulated(t, mod, rt2, period, s1=1.0, s2=0.12):
    """A compound's modulated 1D signal (apex pulse height 1)."""
    out = np.zeros_like(t)
    for k in range(max(int(mod) - 4, 0), int(mod) + 5):
        a = np.exp(-0.5 * ((k + 0.5 - mod) / s1) ** 2)
        centre = k * period + rt2
        near = np.abs(t - centre) < 6 * s2
        out[near] += a * np.exp(-0.5 * ((t[near] - centre) / s2) ** 2)
    return out


def _solvent(t):
    """Unmodulated solvent peak at 0.25 min with a tail."""
    peak = np.exp(-0.5 * ((t - 15.0) / 5.0) ** 2)
    tail = np.where(t > 15.0, 0.3 * np.exp(-(t - 15.0) / 8.0), 0.0)
    return peak + tail


def synthetic_pair(delay: float = PAIR_DELAY, seed: int = 0, drift: float = 0.0):
    """
    A GCxGC-MS run and a GCxGC-FID run of the same sample.

    Parameters
    ----------
    delay : float
        Seconds by which the MS sees every compound later than the FID.
    drift : float
        Extra MS delay at the end of the run (s), growing linearly.

    Returns
    -------
    ms : RawMSData
    fid : pandas.DataFrame
        ``Ret.Time[s]`` and ``Absolute Intensity``.
    truth : dict
        ``compounds`` (name -> class, subclass, modulation, rt2 on the FID,
        FID volume) and ``class_volume`` (class -> summed FID volume of the
        compounds, solvent excluded).
    """
    import pandas as pd

    rng = np.random.default_rng(seed)
    period = PAIR_MODULATION
    duration = PAIR_N_MOD * period
    t_ms = np.arange(round(duration / PAIR_MS_DT)) * PAIR_MS_DT
    t_fid = np.arange(round(duration / PAIR_FID_DT)) * PAIR_FID_DT
    mz = np.arange(PAIR_MZ[0], PAIR_MZ[1] + 1)
    ms = np.zeros((len(t_ms), len(mz)))
    # background (air, column bleed) on a few ions only, as in real runs
    for m, level in ((40, 300.0), (44, 500.0), (73, 120.0), (147, 80.0), (133, 60.0)):
        noise = np.sqrt(level) * rng.standard_normal(len(t_ms))
        ms[:, m - PAIR_MZ[0]] = level * (1 + 0.5 * t_ms / t_ms[-1]) + noise
    fid = 0.05 + 0.0005 * rng.standard_normal(len(t_fid))
    lag = delay + drift * t_ms / duration  # MS time = FID time + lag

    def spectrum(peaks):
        spec = np.zeros(len(mz))
        for m, v in peaks.items():
            if PAIR_MZ[0] <= m <= PAIR_MZ[1]:
                spec[m - PAIR_MZ[0]] = v
        return spec / spec.max()

    solvent_spec = spectrum(PAIR_COMPOUNDS["Dichloromethane"][0])
    ms += 2e4 * _solvent(t_ms - lag)[:, None] * solvent_spec[None, :]
    fid += 40.0 * _solvent(t_fid)
    compounds, class_volume = {}, {}
    for name, (peaks, *_, cls, sub, pos, amount) in PAIR_COMPOUNDS.items():
        if pos is None or not amount:
            continue
        mod, rt2 = pos
        ms += (
            5e3
            * amount
            * _modulated(t_ms - lag, mod, rt2, period)[:, None]
            * (spectrum(peaks)[None, :])
        )
        signal = 10.0 * amount * _modulated(t_fid, mod, rt2, period)
        fid += signal
        volume = float(signal.sum() * PAIR_FID_DT)
        compounds[name] = {
            "class": cls,
            "subclass": sub,
            "modulation": mod,
            "rt1_min": mod * period / 60.0,
            "rt2_s": rt2,
            "fid_volume": volume,
        }
        class_volume[cls] = class_volume.get(cls, 0.0) + volume
    raw = RawMSData(t_ms, mz, ms.astype(np.float32), {"source": "synthetic_ms"})
    frame = pd.DataFrame({"Ret.Time[s]": t_fid, "Absolute Intensity": fid})
    return raw, frame, {"compounds": compounds, "class_volume": class_volume}


def write_fid_csv(frame, path: str | Path) -> Path:
    """Write an FID chromatogram as an OpenChrom CSV (as ``parse_csv`` reads)."""
    path = Path(path)
    t = frame["Ret.Time[s]"].to_numpy()
    y = frame["Absolute Intensity"].to_numpy()
    lines = ["RT(milliseconds),RT(minutes) - NOT USED BY IMPORT,RI,TIC"]
    lines += [
        f"{round(a * 1000)},{a / 60.0!r},0.0,{b!r}" for a, b in zip(t, y, strict=True)
    ]
    path.write_text("\n".join(lines) + "\n")
    return path


_PAIR_CACHE: dict = {}


def _synthetic_pair_cached():
    """:func:`synthetic_pair` with the defaults, computed once per test run."""
    if "pair" not in _PAIR_CACHE:
        _PAIR_CACHE["pair"] = synthetic_pair()
    raw, frame, truth = _PAIR_CACHE["pair"]
    return raw, frame.copy(), truth
