"""
Vendor raw files, read with :mod:`chromparser`.

chromparser reads Chromeleon, Thermo, Agilent, Shimadzu GCMSsolution and ANDI
netCDF files into one data model (`Injection` with `Signal` and `MSData`).
This module classifies such files as FID (1D detector trace) or GC-MS data and
converts signals to the chromatogram DataFrame of
:func:`pyGCxGC.parsing.parse_csv`; MS runs are converted by
:func:`pyGCxGC.ms.io.read_raw_ms`.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

#: chromparser formats holding GC-MS data.
MS_FORMATS = frozenset({"shimadzu-qgd", "andi-ms", "thermo-raw"})
#: chromparser formats holding detector traces. Chromeleon and Agilent
#: archives can also hold MS data, which pyGCxGC does not read from them.
FID_FORMATS = frozenset(
    {
        "shimadzu-gcd",
        "andi-chrom",
        "agilent-ch",
        "agilent-d",
        "agilent-dx",
        "chromeleon-cmbx",
        "chromeleon-raw",
        "chromeleon-txt",
    }
)


def vendor_format(path: str | Path) -> str | None:
    """
    chromparser's format id of ``path`` (e.g. ``"shimadzu-qgd"``).

    Returns
    -------
    str or None
        `None` for files chromparser does not read (OpenChrom CSV, JCAMP-DX).
    """
    import chromparser

    try:
        return chromparser.sniff(path)
    except chromparser.UnsupportedFormat:
        return None


def vendor_kind(path: str | Path) -> str | None:
    """
    ``"fid"`` or ``"ms"`` for a file chromparser reads, else `None`.

    Raises
    ------
    chromparser.UnsupportedFormat
        For files that chromparser recognises but cannot read (e.g. a netCDF
        file without ANDI data).
    """
    fmt = vendor_format(path)
    if fmt is None:
        return None
    if fmt in MS_FORMATS:
        return "ms"
    if fmt in FID_FORMATS:
        return "fid"
    return "ms" if _hdf5_has_ms(path) else "fid"


def _hdf5_has_ms(path: str | Path) -> bool:
    """Whether a chromparser HDF5 file holds MS data (layout v3)."""
    import h5py

    with h5py.File(path, "r") as hdf:
        return any(
            isinstance(item, h5py.Group) and item.attrs.get("kind") == "ms"
            for injection in hdf.values()
            if isinstance(injection, h5py.Group)
            for item in injection.values()
        )


def read_signal(
    path: str | Path, channel: str | None = None, injection: int | None = None
) -> pd.DataFrame:
    """
    Read a detector trace (e.g. FID) with :mod:`chromparser`.

    Parameters
    ----------
    path : str or Path
        Any file chromparser reads, e.g. Shimadzu ``.gcd``, Agilent ``.ch``,
        Chromeleon ``.cmbx`` or ANDI chromatography ``.cdf``.
    channel : str, optional
        Channel name; required when the injection has several signals.
    injection : int, optional
        Index of the injection; required when the file holds several.

    Returns
    -------
    pandas.DataFrame
        Columns ``"Ret.Time"`` (min), ``"Ret.Time[s]"`` and
        ``"Absolute Intensity"``, like :func:`pyGCxGC.parsing.parse_csv`.
        ``attrs`` holds the injection and signal metadata, the channel, unit
        and sampling interval.
    """
    import chromparser

    path = Path(path)
    injections = [inj for inj in chromparser.read(path, ms=False) if inj.signals]
    if not injections:
        raise ValueError(f"{path.name} contains no detector signals")
    if injection is None:
        if len(injections) > 1:
            raise ValueError(
                f"{path.name} holds {len(injections)} injections; "
                "choose one with injection="
            )
        injection = 0
    inj = injections[injection]
    if channel is None:
        if len(inj.signals) > 1:
            raise ValueError(
                f"{path.name} has several channels ({', '.join(inj.signals)}); "
                "choose one with channel="
            )
        channel = next(iter(inj.signals))
    elif channel not in inj.signals:
        raise ValueError(
            f"{path.name} has no channel {channel!r} (found: {', '.join(inj.signals)})"
        )
    return from_signal(inj.signals[channel], inj.metadata)


def from_signal(signal, metadata: dict | None = None) -> pd.DataFrame:
    """
    Convert a :class:`chromparser.Signal` to a chromatogram DataFrame.

    Parameters
    ----------
    signal : chromparser.Signal
        Detector trace.
    metadata : dict, optional
        Injection metadata, stored in ``attrs`` with the signal's.

    Returns
    -------
    pandas.DataFrame
        See :func:`read_signal`.
    """
    minutes = np.asarray(signal.time, dtype=np.float64)
    if signal.sampling_interval:
        # regular sampling: exact times, without the error of minutes
        seconds = minutes[0] * 60.0 + signal.sampling_interval * np.arange(len(minutes))
        seconds = np.round(seconds, 6)
    else:
        seconds = minutes * 60.0
    chrom = pd.DataFrame(
        {
            "Ret.Time": seconds / 60.0,
            "Ret.Time[s]": seconds,
            "Absolute Intensity": np.asarray(signal.values, dtype=np.float64),
        }
    )
    attrs = {**(metadata or {}), **signal.metadata, "channel": signal.channel}
    if signal.unit:
        attrs["unit"] = signal.unit
    if signal.sampling_interval:
        attrs["sampling_interval"] = signal.sampling_interval
    chrom.attrs = attrs
    return chrom
