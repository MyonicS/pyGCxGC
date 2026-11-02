"""
Readers for GC-MS raw data.

Supported formats
-----------------
* Everything :mod:`chromparser` reads: ANDI-MS / AIA netCDF (``.cdf``), the
  vendor neutral export of practically every GC-MS software, Shimadzu
  GCMSsolution ``.qgd``, Thermo ``.raw`` (also inside Chromeleon ``.cmbx``)
  and chromparser HDF5 files.
* OpenChrom "CSV" export (one row per scan, one column per nominal m/z).
* JCAMP-DX GC-MS exports (``##RETENTION_TIME`` / ``##XYDATA`` blocks).

All readers return a :class:`RawMSData` with a dense ``(n_scans, n_mz)``
intensity matrix on a nominal (integer) m/z axis.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np


@dataclass
class RawMSData:
    """
    GC-MS data on a nominal mass axis.

    Attributes
    ----------
    scan_times : np.ndarray
        Acquisition time of each scan in seconds.
    mz : np.ndarray
        Integer m/z values of the matrix columns.
    intensities : np.ndarray
        ``float32`` array of shape ``(n_scans, n_mz)``.
    metadata : dict
        Global attributes of the file. ``"intensity_units"`` is the short
        intensity unit (``"counts"``, ``"a.u."``, ...).
    """

    scan_times: np.ndarray
    mz: np.ndarray
    intensities: np.ndarray
    metadata: dict = field(default_factory=dict)

    @property
    def tic(self) -> np.ndarray:
        """Total ion chromatogram."""
        return self.intensities.sum(axis=1)


def read_ms_file(path: str | Path) -> RawMSData:
    """
    Read a GC-MS file, choosing the reader from the file contents.

    Parameters
    ----------
    path : str or Path
        A file :func:`read_raw_ms` reads (ANDI/AIA ``.cdf``, Shimadzu
        ``.qgd``, ...), an OpenChrom ``.csv`` or a JCAMP-DX (``.jdx``) file.
    """
    from pyGCxGC.vendor import vendor_format

    path = Path(path)
    if vendor_format(path) is not None:
        return read_raw_ms(path)
    suffix = path.suffix.lower()
    if suffix in (".jdx", ".dx", ".jcamp"):
        return read_jcamp(path)
    if suffix in (".csv", ".txt"):
        return read_openchrom_csv(path)
    raise ValueError(f"Unsupported GC-MS file format: {path}")


#: Short forms of intensity units (ANDI ``intensity_values:units`` spellings).
INTENSITY_UNITS = {
    "total counts": "counts",
    "counts": "counts",
    "arbitrary intensity units": "a.u.",
    "arbitrary units": "a.u.",
}
#: Unit of files that do not state one.
DEFAULT_INTENSITY_UNIT = "a.u."


def intensity_unit(unit: str | None) -> str:
    """Short form of an intensity unit; ``"a.u."`` when it is unknown."""
    if not unit or not str(unit).strip():
        return DEFAULT_INTENSITY_UNIT
    return INTENSITY_UNITS.get(str(unit).strip().lower(), str(unit).strip())


# ---------------------------------------------------------------- vendor files
def read_raw_ms(path: str | Path, injection: int | None = None) -> RawMSData:
    """
    Read GC-MS data with :mod:`chromparser`.

    Parameters
    ----------
    path : str or Path
        Any GC-MS file chromparser reads: ANDI-MS netCDF (``.cdf``), Shimadzu
        GCMSsolution ``.qgd``, Thermo ``.raw``, Chromeleon ``.cmbx`` with MS
        data or a chromparser HDF5 file.
    injection : int, optional
        Index of the injection among those with MS data, for files that hold
        several. Required when there is more than one.

    Returns
    -------
    RawMSData
        Centroids rounded to the nearest integer m/z and summed per scan.
    """
    import chromparser

    path = Path(path)
    runs = [
        (inj, ms) for inj in chromparser.read(path, ms=True) for ms in inj.ms.values()
    ]
    if not runs:
        raise ValueError(f"{path.name} contains no GC-MS data")
    if injection is None:
        if len(runs) > 1:
            raise ValueError(
                f"{path.name} holds {len(runs)} GC-MS runs; choose one with injection="
            )
        injection = 0
    inj, ms = runs[injection]
    return from_msdata(ms, {**inj.metadata, **ms.metadata})


def read_andi(path: str | Path) -> RawMSData:
    """
    Read an ANDI-MS (AIA, netCDF classic) file.

    Parameters
    ----------
    path : str or Path
        Path to the ``.cdf`` file.

    Returns
    -------
    RawMSData
    """
    return read_raw_ms(path)


def from_msdata(ms, metadata: dict | None = None) -> RawMSData:
    """
    Convert a :class:`chromparser.MSData` to a :class:`RawMSData`.

    Parameters
    ----------
    ms : chromparser.MSData
        Centroided MS run.
    metadata : dict, optional
        Stored in the result (default: ``ms.metadata``).
        ``"intensity_units"`` is set from ``ms.metadata["intensity_unit"]``
        (see :func:`intensity_unit`).
    """
    if ms.spectrum_type != "centroid":
        raise ValueError(f"{ms.name}: profile spectra are not supported")
    n_scans = len(ms)
    scan = np.repeat(np.arange(n_scans), np.diff(ms.peak_offset))
    # retention times are minutes; round off the conversion error (µs)
    seconds = np.round(ms.retention_time * 60.0, 6)
    return _to_matrix(
        seconds,
        scan,
        ms.mz,
        ms.intensity,
        n_scans,
        {
            **(ms.metadata if metadata is None else metadata),
            "intensity_units": intensity_unit(ms.metadata.get("intensity_unit")),
        },
    )


# ---------------------------------------------------------------- OpenChrom CSV
def read_openchrom_csv(path: str | Path) -> RawMSData:
    """
    Read an OpenChrom CSV export (``RT(milliseconds)``, ``RT(minutes)``, ``RI``
    followed by one column per m/z).
    """
    import pandas as pd

    path = Path(path)
    head = pd.read_csv(path, nrows=0)
    cols = list(head.columns)
    mz_cols = []
    for c in cols:
        try:
            mz_cols.append((c, float(c)))
        except ValueError:
            continue
    if not mz_cols:
        raise ValueError(f"{path.name}: no m/z columns found")
    rt_col = next((c for c in cols if "millisecond" in c.lower()), None)
    usecols = [c for c, _ in mz_cols] + ([rt_col] if rt_col else [])
    table = pd.read_csv(path, usecols=usecols, dtype=np.float32, engine="c")
    if rt_col:
        times = table[rt_col].to_numpy(np.float64) / 1000.0
    else:
        times = (
            pd.read_csv(path, usecols=[cols[1]]).iloc[:, 0].to_numpy(np.float64) * 60
        )
    values = table[[c for c, _ in mz_cols]].to_numpy(np.float32)
    mz = np.array([m for _, m in mz_cols])
    n_scans = values.shape[0]
    scan = np.repeat(np.arange(n_scans), len(mz))
    return _to_matrix(
        times,
        scan,
        np.tile(mz, n_scans),
        values.ravel(),
        n_scans,
        {"source": str(path)},
    )


# ---------------------------------------------------------------- JCAMP-DX
_RT_RE = re.compile(r"##RETENTION_TIME=\s*([-+0-9.eE]+)")


def read_jcamp(path: str | Path) -> RawMSData:
    """Read a JCAMP-DX GC-MS export with one ``(XY..XY)`` block per scan."""
    path = Path(path)
    times, scans, mzs, ints = [], [], [], []
    in_xy = False
    scan = -1
    with path.open(encoding="latin-1") as fh:
        for line in fh:
            if line.startswith("##"):
                in_xy = False
                if line.startswith("##RETENTION_TIME="):
                    m = _RT_RE.match(line)
                    scan += 1
                    times.append(float(m.group(1)) if m else np.nan)
                elif line.startswith("##XYDATA="):
                    in_xy = True
                continue
            if in_xy and scan >= 0:
                parts = line.replace(",", " ").split()
                for a, b in zip(parts[::2], parts[1::2], strict=False):
                    mzs.append(float(a))
                    ints.append(float(b))
                    scans.append(scan)
    if scan < 0:
        raise ValueError(f"{path.name}: no ##RETENTION_TIME blocks found")
    times_arr = np.array(times)
    # OpenChrom writes retention times in seconds
    return _to_matrix(
        times_arr,
        np.array(scans),
        np.array(mzs),
        np.array(ints),
        scan + 1,
        {"source": str(path)},
    )


# ---------------------------------------------------------------- helpers
def _to_matrix(
    scan_times, scan_of_point, mz, intensity, n_scans, metadata
) -> RawMSData:
    mz = np.asarray(mz, dtype=np.float64)
    intensity = np.asarray(intensity, dtype=np.float64)
    # drop netCDF fill values / garbage of truncated files
    good = (
        np.isfinite(mz)
        & (mz >= 0.5)
        & (mz < 1e5)
        & np.isfinite(intensity)
        & (np.abs(intensity) < 1e30)
    )
    nominal = np.rint(mz[good]).astype(np.int64)
    scan_of_point = np.asarray(scan_of_point)[good]
    intensity = intensity[good].astype(np.float32)
    lo, hi = int(nominal.min()), int(nominal.max())
    matrix = np.zeros((n_scans, hi - lo + 1), dtype=np.float32)
    np.add.at(matrix, (scan_of_point, nominal - lo), intensity)
    metadata = {"intensity_units": DEFAULT_INTENSITY_UNIT, **metadata}
    return RawMSData(
        scan_times=np.asarray(scan_times, dtype=np.float64)[:n_scans],
        mz=np.arange(lo, hi + 1),
        intensities=matrix,
        metadata=metadata,
    )
