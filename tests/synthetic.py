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
