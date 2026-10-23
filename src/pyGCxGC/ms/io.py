"""
Readers for GC-MS raw data.

Supported formats
-----------------
* ANDI-MS / AIA netCDF (``.cdf``), the vendor neutral export of practically
  every GC-MS software (e.g. Shimadzu GCMSsolution "File Conversion" -> AIA).
  Files with an unset record count (written by some converters) or that are
  truncated are recovered as far as possible.
* OpenChrom "CSV" export (one row per scan, one column per nominal m/z).
* JCAMP-DX GC-MS exports (``##RETENTION_TIME`` / ``##XYDATA`` blocks).

All readers return a :class:`RawMSData` with a dense ``(n_scans, n_mz)``
intensity matrix on a nominal (integer) m/z axis.
"""

from __future__ import annotations

import re
import struct
import warnings
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


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
        Global attributes of the file.
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
        ANDI/AIA netCDF (``.cdf``), OpenChrom ``.csv`` or JCAMP-DX (``.jdx``).
    """
    path = Path(path)
    with path.open("rb") as fh:
        magic = fh.read(8)
    if magic[:3] == b"CDF":
        return read_andi(path)
    if magic == OLE_MAGIC:
        raise ValueError(
            f"{path.name} is a vendor binary file (OLE compound document, e.g. a "
            "Shimadzu .qgd renamed). Export it as ANDI/AIA netCDF (*.cdf) from "
            "the instrument software or OpenChrom first."
        )
    suffix = path.suffix.lower()
    if suffix in (".jdx", ".dx", ".jcamp"):
        return read_jcamp(path)
    if suffix in (".csv", ".txt"):
        return read_openchrom_csv(path)
    raise ValueError(f"Unsupported GC-MS file format: {path}")


# ---------------------------------------------------------------- ANDI-MS
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
    from scipy.io import netcdf_file

    path = Path(path)
    with netcdf_file(path, "r", mmap=False) as f:
        attrs = {k: _decode_attr(v) for k, v in f._attributes.items()}
        var = f.variables
        scan_times = np.array(var["scan_acquisition_time"][:], dtype=np.float64)
        scan_index = np.array(var["scan_index"][:], dtype=np.int64)
        point_count = np.array(var["point_count"][:], dtype=np.int64)
        masses = np.array(var["mass_values"][:], dtype=np.float64)
        intens = np.array(var["intensity_values"][:], dtype=np.float64)
        units = _decode_attr(var["scan_acquisition_time"]._attributes.get("units", b""))

    if len(masses) == 0 and point_count.max(initial=0) > 0:
        # record count not set in the header (streaming writers / truncated)
        masses, intens = _read_andi_records(path)
        warnings.warn(
            f"{path.name}: netCDF record count missing; recovered "
            f"{len(masses)} data points from the file body.",
            stacklevel=2,
        )

    valid = (
        np.isfinite(scan_times)
        & (np.abs(scan_times) < 1e30)
        & (point_count >= 0)
        & (scan_index >= 0)
        & (scan_index + point_count <= len(masses))
    )
    if not valid.all():
        last = np.flatnonzero(~valid)[0]
        warnings.warn(
            f"{path.name}: only the first {last} of {len(valid)} scans are complete; "
            "the file appears to be truncated.",
            stacklevel=2,
        )
        scan_times, scan_index, point_count = (
            scan_times[:last],
            scan_index[:last],
            point_count[:last],
        )
    if "min" in str(units).lower():
        scan_times = scan_times * 60.0

    n_points = int(scan_index[-1] + point_count[-1]) if len(scan_index) else 0
    masses, intens = masses[:n_points], intens[:n_points]
    scan_of_point = np.repeat(np.arange(len(point_count)), point_count)
    starts = np.repeat(scan_index, point_count)
    # scan_index may not be contiguous; index the points explicitly
    offsets = np.arange(n_points) - np.repeat(
        np.concatenate([[0], np.cumsum(point_count)[:-1]]), point_count
    )
    point_idx = starts + offsets
    mz_all = masses[point_idx]
    int_all = intens[point_idx]
    return _to_matrix(
        scan_times, scan_of_point, mz_all, int_all, len(point_count), attrs
    )


def _read_andi_records(path: Path) -> tuple[np.ndarray, np.ndarray]:
    """Read ``mass_values``/``intensity_values`` when numrecs is 0."""
    header = _parse_netcdf_header(path)
    rec_layout = sorted(
        ((n, h) for n, h in header.items() if h["isrec"]), key=lambda x: x[1]["begin"]
    )
    recsize = sum(h["vsize"] for _, h in rec_layout)
    start = rec_layout[0][1]["begin"]
    nrec = (path.stat().st_size - start) // recsize
    dtype = np.dtype(
        [(n, h["dtype"], (h["vsize"] // h["dtype"].itemsize,)) for n, h in rec_layout]
    )
    data = np.fromfile(path, dtype=dtype, count=nrec, offset=start)
    masses = data["mass_values"].reshape(-1).astype(np.float64)
    intens = data["intensity_values"].reshape(-1).astype(np.float64)
    return masses, intens


_NC_TYPES = {1: "i1", 2: "S1", 3: ">i2", 4: ">i4", 5: ">f4", 6: ">f8"}


def _parse_netcdf_header(path: Path) -> dict:
    """Minimal netCDF classic header parser (variable offsets and sizes)."""
    with path.open("rb") as fh:
        buf = fh.read(1 << 20)
    if buf[:3] != b"CDF":
        raise ValueError("not a netCDF classic file")
    offset_size = 8 if buf[3] == 2 else 4
    pos = 8

    def u32():
        nonlocal pos
        v = struct.unpack_from(">I", buf, pos)[0]
        pos += 4
        return v

    def name():
        nonlocal pos
        n = u32()
        s = buf[pos : pos + n].decode("latin-1")
        pos += (n + 3) & ~3
        return s

    def skip_attrs():
        nonlocal pos
        tag, n = u32(), u32()
        for _ in range(n if tag else 0):
            name()
            typ, nelems = u32(), u32()
            size = np.dtype(_NC_TYPES[typ]).itemsize * nelems
            pos += (size + 3) & ~3

    tag, ndims = u32(), u32()
    dims = [(name(), u32()) for _ in range(ndims if tag else 0)]
    skip_attrs()
    tag, nvars = u32(), u32()
    out = {}
    for _ in range(nvars if tag else 0):
        vname = name()
        nd = u32()
        dimids = [u32() for _ in range(nd)]
        skip_attrs()
        typ, vsize = u32(), u32()
        begin = struct.unpack_from(">Q" if offset_size == 8 else ">I", buf, pos)[0]
        pos += offset_size
        isrec = bool(dimids) and dims[dimids[0]][1] == 0
        out[vname] = {
            "dtype": np.dtype(_NC_TYPES[typ]),
            "vsize": vsize,
            "begin": begin,
            "isrec": isrec,
        }
    return out


def _decode_attr(value):
    if isinstance(value, bytes):
        return value.decode("latin-1").rstrip("\0").strip()
    if isinstance(value, np.ndarray) and value.size == 1:
        return value.item()
    return value


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
    return RawMSData(
        scan_times=np.asarray(scan_times, dtype=np.float64)[:n_scans],
        mz=np.arange(lo, hi + 1),
        intensities=matrix,
        metadata=metadata,
    )
