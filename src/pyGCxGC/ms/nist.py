"""
Reader for NIST MS Search main libraries (``MAINLIB`` folders).

The NIST/EPA/NIH Mass Spectral Library ships as a set of FairCom c-tree
database files. Only ``nist.db`` is needed to obtain the spectra: it holds one
variable-length record per compound containing the CAS number, NIST number,
nominal molecular weight, (token-compressed) name, formula and the spectrum.

The layout was determined by inspecting the files of a licensed NIST 20 copy
and the decoder routine in ``nistms32.dll`` (NIST MS Search 2.0). It allows the
library to be searched on platforms where NIST MS Search does not run. The
NIST library itself is licensed software; this module only reads a copy that the
user already owns and must never be used to redistribute it.

c-tree variable-length data file
--------------------------------
* 128 byte header followed by 0xFF padding.
* Records start with a two byte marker (``FA FA`` active, ``FD FD`` deleted,
  ``FE FE`` c-tree resource), a uint32 total record length (including the
  10 byte record header) and a uint32 used data length.

``nist.db`` record payload
--------------------------
=======  ==========================================================
offset   content
=======  ==========================================================
0        uint16, unknown (flags)
2        uint32 CAS number as an integer including the check digit
6        uint32 NIST number
10       uint32, unknown
14       uint16 nominal molecular weight
16       uint16 contributor index (record number in ``contrib.db``)
18       name, NUL terminated; bytes >= 0x80 are tokens (``NAME_TOKENS``)
...      formula, NUL terminated
...      int16 number of peaks, followed by the encoded masses and abundances
=======  ==========================================================

Masses: the first mass is the sum of a run of 0xFF bytes and the next byte.
Each following byte ``b`` encodes the next mass as ``previous + b`` when
``b < 0xCC``; ``b == 0xCC`` starts a large gap (``0xCC`` repeated, then a
remainder byte); ``b > 0xCC`` encodes ``0x101 - b`` consecutive masses.

Abundances: one byte per peak if the byte is ``< 0xFA``; otherwise the value is
``(0xFF - b) + 5 * next_byte`` (two bytes). The base peak is 999.
"""

from __future__ import annotations

import struct
from pathlib import Path

import numpy as np

# Substring dictionary used to compress compound names (byte 0x80 + index).
NAME_TOKENS = (
    "phenyl", "dimethyl", "trimethyl", "methyl", "ethyl", "benzene", "pyridine",
    "propyl", "isobutyl", "cyclopropane", "cyclobutane", "cyclopent", "cyclohex",
    "acetic", "quino", "butyl", "triazole", "imid", "bicyclo", "aldehyde",
    "hydride", "cyan", "dihydro", "vinyl", "form", "bor", "cyclohept", "pyrrol",
    "cis", "morpho", "alanan", "lactone", "indinone", "thiophene", "glycol",
    "nor", "diol", "croto", "spiro", "methoxy", "keto", "alcohol", "piper",
    "malon", "prop", "but", "pent", "hex", "hept", "oct", "dec", "diene", "ene",
    "ane", "one", "acetate", "ate", "hydroxy", " acid", "ester", "ether", "eth",
    "amin", "nitro", "nitrile", "phosph", "azo", "sulf", "ine", "oxo", "oxide",
    "amide", "furan", "yne", "ol", "oic", "naph", "phen", "chlor", "fluor",
    "iod", "brom", "cyclo", "ammon", "trans", "bi", "thio", "carb", ", ", "hydr",
    "mono", "tetra", "tri", "epoxy", "(e)-", "(z)-", ".alpha.", ".beta.", "ide",
    ".pi.", "yl", ".sigma.", ".mu.", ".gamma.", "iso", "oxa", ".omega.",
    ".delta.", "mer", "acet", ".epsilon.", "pyr", "oxy", ".+/-.", "sil", "ic",
    "ino", "ano", "1,", "2,", "en", "gly", "tetr", "tert", ".eta.", "sec", "gua",
    "fur",
)  # fmt: skip

_RECORD_ACTIVE = b"\xfa\xfa"
_RECORD_DELETED = b"\xfd\xfd"
_RECORD_RESOURCE = b"\xfe\xfe"
_RECORD_HEADER = struct.Struct("<II")
_ENTRY_HEADER = struct.Struct("<HIIIHH")


def _detokenize(raw: bytes) -> str:
    """Expand the name compression tokens of a NIST name."""
    if not any(c >= 0x80 for c in raw):
        return raw.decode("latin-1")
    return "".join(NAME_TOKENS[c - 0x80] if c >= 0x80 else chr(c) for c in raw)


def format_cas(cas: int) -> str:
    """Format an integer CAS number (``71432`` -> ``"71-43-2"``)."""
    if cas <= 0:
        return ""
    s = str(cas)
    return f"{s[:-3]}-{s[-3:-1]}-{s[-1]}"


def iter_ctree_records(path: str | Path):
    """
    Yield the payload of all active records of a c-tree variable length file.

    Parameters
    ----------
    path : str or Path
        Path to a c-tree data file (e.g. ``nist.db``).

    Yields
    ------
    bytes
        The data of each active record, in file order.
    """
    buf = Path(path).read_bytes()
    off = 128
    while off < len(buf) and buf[off] == 0xFF:
        off += 1
    end = len(buf) - _RECORD_HEADER.size - 2
    while off < end:
        mark = buf[off : off + 2]
        if mark not in (_RECORD_ACTIVE, _RECORD_DELETED, _RECORD_RESOURCE):
            break
        total, used = _RECORD_HEADER.unpack_from(buf, off + 2)
        if total <= 0:
            break
        if mark == _RECORD_ACTIVE:
            yield buf[off + 10 : off + 10 + used]
        off += total


def decode_spectrum(rec: bytes, off: int) -> tuple[list[int], list[int]]:
    """
    Decode the peak list stored at ``off`` in a ``nist.db`` record.

    Returns
    -------
    masses, abundances : list of int
    """
    n = struct.unpack_from("<h", rec, off)[0]
    off += 2
    mass = 0
    while rec[off] == 0xFF:
        mass += 0xFF
        off += 1
    mass += rec[off]
    off += 1
    masses = [mass]
    while len(masses) < n:
        b = rec[off]
        if b < 0xCC:
            masses.append(masses[-1] + b)
        elif b > 0xCC:
            last = masses[-1]
            masses.extend(range(last + 1, last + 1 + 0x101 - b))
        else:
            gap = 0xCC
            off += 1
            while rec[off] == 0xCC:
                gap += 0xCC
                off += 1
            masses.append(masses[-1] + gap + rec[off])
        off += 1
    del masses[n:]
    abundances = []
    for _ in range(n):
        c = rec[off]
        if c < 0xFA:
            abundances.append(c)
            off += 1
        else:
            abundances.append(0xFF - c + 5 * rec[off + 1])
            off += 2
    return masses, abundances


def read_nist_mainlib(path: str | Path, progress=None) -> dict:
    """
    Read all spectra and compound information of a NIST ``MAINLIB`` folder.

    Parameters
    ----------
    path : str or Path
        The ``MAINLIB`` directory, its parent (e.g. ``MSSEARCH``) or the
        ``nist.db`` file itself.
    progress : callable, optional
        Called as ``progress(fraction)`` while reading.

    Returns
    -------
    dict
        Arrays suitable for :class:`pyGCxGC.ms.library.SpectralLibrary`:
        ``mz``, ``intensity`` and ``offsets`` (CSR-like peak storage) and the
        metadata lists ``name``, ``formula``, ``cas``, ``nist_no``, ``mw`` and
        ``db_no`` (1-based position in the library, as shown by NIST MS Search).
    """
    db = find_nist_db(path)
    records = list(iter_ctree_records(db))
    if not records:
        raise ValueError(f"No spectra found in {db}")
    names, formulas, cas, nist_no, mw = [], [], [], [], []
    mz_chunks, int_chunks, counts = [], [], []
    step = max(1, len(records) // 100)
    for i, rec in enumerate(records):
        _, cas_i, nist_i, _, mw_i, _ = _ENTRY_HEADER.unpack_from(rec, 0)
        o = _ENTRY_HEADER.size
        e = rec.index(b"\0", o)
        names.append(_detokenize(rec[o:e]))
        o = e + 1
        e = rec.index(b"\0", o)
        formulas.append(rec[o:e].decode("latin-1"))
        masses, abundances = decode_spectrum(rec, e + 1)
        mz_chunks.append(masses)
        int_chunks.append(abundances)
        counts.append(len(masses))
        cas.append(format_cas(cas_i))
        nist_no.append(nist_i)
        mw.append(mw_i)
        if progress is not None and i % step == 0:
            progress(i / len(records))
    offsets = np.zeros(len(records) + 1, dtype=np.int64)
    np.cumsum(counts, out=offsets[1:])
    mz = np.fromiter(
        (m for chunk in mz_chunks for m in chunk), dtype=np.uint16, count=offsets[-1]
    )
    intensity = np.fromiter(
        (a for chunk in int_chunks for a in chunk), dtype=np.float32, count=offsets[-1]
    )
    if progress is not None:
        progress(1.0)
    return {
        "mz": mz,
        "intensity": intensity,
        "offsets": offsets,
        "name": names,
        "formula": formulas,
        "cas": cas,
        "nist_no": nist_no,
        "mw": mw,
        "db_no": list(range(1, len(records) + 1)),
    }


def find_nist_db(path: str | Path) -> Path:
    """Locate ``nist.db`` given a MAINLIB folder, its parent or the file."""
    path = Path(path)
    if path.is_file():
        if path.suffix.lower() == ".db":
            return path
        raise FileNotFoundError(f"{path} is not a NIST .db file")
    for candidate in (path / "nist.db", path / "MAINLIB" / "nist.db"):
        if candidate.is_file():
            return candidate
    for candidate in path.glob("*/nist.db"):
        return candidate
    raise FileNotFoundError(f"Could not find a NIST 'nist.db' file in {path}")


def is_nist_library(path: str | Path) -> bool:
    """Return True if ``path`` looks like a NIST MAINLIB folder or ``nist.db``."""
    try:
        find_nist_db(path)
    except FileNotFoundError:
        return False
    return True
