"""
Mass spectral libraries.

A :class:`SpectralLibrary` stores nominal-mass EI spectra in a compact
CSR-like layout (one flat array of m/z values and intensities plus row offsets)
together with compound metadata. Libraries can be built from

* MSP text files (NIST MSP, MoNA, MassBank, ...), see :meth:`SpectralLibrary.from_msp`
* a licensed NIST MS Search ``MAINLIB`` folder, see :meth:`SpectralLibrary.from_nist`

and cached as a single ``.npz`` file for fast loading (:meth:`SpectralLibrary.save`).
"""

from __future__ import annotations

import contextlib
import functools
import hashlib
import io
import itertools
import os
import re
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np

META_FIELDS = (
    "name",
    "formula",
    "cas",
    "mw",
    "nist_no",
    "db_no",
    "source",
    "smiles",
    "inchi",
    "inchikey",
    "pubchem_cid",
)
_INT_FIELDS = ("mw", "nist_no", "db_no", "pubchem_cid")
CACHE_VERSION = 2  # 2: structure identifiers (smiles, inchi, inchikey, pubchem_cid)
_READABLE_CACHE_VERSIONS = (1, 2)


def default_cache_dir() -> Path:
    """
    Directory used to cache converted libraries.

    ``~/.cache/pyGCxGC`` on Linux (``$XDG_CACHE_HOME`` if set), otherwise the
    platform's cache folder from ``platformdirs`` when it is installed (it
    comes with the ``app`` extra), e.g. ``~/Library/Caches/pyGCxGC`` on macOS
    and ``%LOCALAPPDATA%\\pyGCxGC\\Cache`` on Windows.
    """
    if not sys.platform.startswith("linux"):
        with contextlib.suppress(ImportError):
            from platformdirs import user_cache_dir

            return Path(user_cache_dir("pyGCxGC", appauthor=False))
    base = os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"
    return Path(base) / "pyGCxGC"


@dataclass
class LibraryEntry:
    """A single library spectrum with its metadata."""

    index: int
    name: str
    formula: str
    cas: str
    mw: int
    nist_no: int
    db_no: int
    source: str
    mz: np.ndarray
    intensity: np.ndarray
    smiles: str = ""
    inchi: str = ""
    inchikey: str = ""
    pubchem_cid: int = 0


class SpectralLibrary:
    """
    Collection of nominal-mass reference spectra.

    Parameters
    ----------
    mz : np.ndarray
        Flat array with the (integer) m/z values of all spectra.
    intensity : np.ndarray
        Flat array with the intensities belonging to ``mz``.
    offsets : np.ndarray
        Array of length ``n_spectra + 1``; spectrum ``i`` occupies
        ``mz[offsets[i]:offsets[i + 1]]``.
    name : str, optional
        Name of the library, shown in the GUI.
    **metadata
        Per spectrum lists for the fields in ``META_FIELDS`` (compound names
        are passed as ``compound_name``). Missing fields are filled with
        empty values.
    """

    def __init__(self, mz, intensity, offsets, name: str = "library", **metadata):
        self.mz = np.asarray(mz, dtype=np.uint16)
        self.intensity = np.asarray(intensity, dtype=np.float32)
        self.offsets = np.asarray(offsets, dtype=np.int64)
        self.name = name
        n = len(self.offsets) - 1
        for field in META_FIELDS:
            attr = _attr(field)
            values = metadata.get(attr)
            if values is None:
                values = [0] * n if field in _INT_FIELDS else [""] * n
            if len(values) != n:
                raise ValueError(f"Length of '{field}' ({len(values)}) != {n}")
            if field in _INT_FIELDS:
                values = np.asarray(values, dtype=np.int64)
            else:
                values = np.asarray(values, dtype=object)
            setattr(self, attr, values)
        self._search_cache: dict = {}

    # ------------------------------------------------------------------ basics
    def __len__(self) -> int:
        return len(self.offsets) - 1

    def __repr__(self) -> str:
        return f"SpectralLibrary(name={self.name!r}, n_spectra={len(self)})"

    def __getitem__(self, index: int) -> LibraryEntry:
        return self.entry(index)

    def entry(self, index: int) -> LibraryEntry:
        """Return spectrum ``index`` and its metadata."""
        lo, hi = self.offsets[index], self.offsets[index + 1]
        return LibraryEntry(
            index=int(index),
            name=str(self.name_of(index)),
            formula=str(self.formula[index]),
            cas=str(self.cas[index]),
            mw=int(self.mw[index]),
            nist_no=int(self.nist_no[index]),
            db_no=int(self.db_no[index]),
            source=str(self.source[index]),
            mz=self.mz[lo:hi].astype(np.int64),
            intensity=self.intensity[lo:hi].astype(np.float64),
            smiles=str(self.smiles[index]),
            inchi=str(self.inchi[index]),
            inchikey=str(self.inchikey[index]),
            pubchem_cid=int(self.pubchem_cid[index]),
        )

    def name_of(self, index: int) -> str:
        """Compound name of spectrum ``index`` (stored as ``compound_name``)."""
        return self.compound_name[index]

    @property
    def n_peaks(self) -> np.ndarray:
        """Number of peaks of each spectrum."""
        return np.diff(self.offsets)

    def find(self, pattern: str, max_results: int = 50) -> list[int]:
        """Indices of spectra whose name contains ``pattern`` (case-insensitive)."""
        rx = re.compile(re.escape(pattern), re.IGNORECASE)
        hits = []
        for i, nm in enumerate(self.compound_name):
            if rx.search(nm):
                hits.append(i)
                if len(hits) >= max_results:
                    break
        return hits

    # ---------------------------------------------------------- construction
    @classmethod
    def from_spectra(
        cls,
        spectra: list[tuple[np.ndarray, np.ndarray]],
        name: str = "library",
        metadata: dict | None = None,
    ) -> SpectralLibrary:
        """
        Build a library from a list of ``(mz, intensity)`` pairs.

        Intensities are rounded to nominal mass (summing duplicates) and scaled
        so that the base peak is 999.

        Parameters
        ----------
        spectra : list of (mz, intensity)
            The spectra.
        name : str, default "library"
            Name of the library.
        metadata : dict, optional
            Per spectrum lists keyed by the fields in ``META_FIELDS``
            (``"name"`` holds the compound names).
        """
        mz_list, int_list, counts = [], [], []
        for mz, inten in spectra:
            mz_n, inten_n = _to_nominal(mz, inten)
            mz_list.append(mz_n)
            int_list.append(inten_n)
            counts.append(len(mz_n))
        offsets = np.zeros(len(spectra) + 1, dtype=np.int64)
        np.cumsum(counts, out=offsets[1:])
        mz = np.concatenate(mz_list) if mz_list else np.zeros(0)
        inten = np.concatenate(int_list) if int_list else np.zeros(0)
        return cls(mz, inten, offsets, name=name, **_meta_kwargs(metadata or {}))

    @classmethod
    def from_msp(
        cls, path: str | Path, name: str | None = None, ei_only: bool = True
    ) -> SpectralLibrary:
        """
        Read an MSP file (optionally inside a ``.zip``).

        Parameters
        ----------
        path : str or Path
            Path to a ``.msp`` file or a ``.zip`` containing one.
        name : str, optional
            Library name. Defaults to the file name.
        ei_only : bool, default True
            Skip records that are clearly not EI spectra (MS2 spectra,
            CI/APCI/ESI/FI ionisation).
        """
        path = Path(path)
        spectra, meta = [], {k: [] for k in META_FIELDS}
        for rec in iter_msp(path):
            if ei_only and not _is_ei_record(rec):
                continue
            spectra.append((rec["mz"], rec["intensity"]))
            meta["name"].append(rec.get("name", ""))
            meta["formula"].append(rec.get("formula", ""))
            meta["cas"].append(rec.get("cas", ""))
            meta["mw"].append(_safe_int(rec.get("mw")))
            meta["nist_no"].append(_safe_int(rec.get("nist_no")))
            meta["db_no"].append(len(spectra))
            meta["source"].append(rec.get("db_id", ""))
            meta["smiles"].append(rec.get("smiles", ""))
            meta["inchi"].append(rec.get("inchi", ""))
            meta["inchikey"].append(rec.get("inchikey", ""))
            meta["pubchem_cid"].append(_safe_int(rec.get("pubchem_cid")))
        return cls.from_spectra(spectra, name=name or path.stem, metadata=meta)

    @classmethod
    def from_nist(cls, path: str | Path, name: str = "NIST", progress=None):
        """
        Read a NIST MS Search main library (``MAINLIB`` folder).

        See :func:`pyGCxGC.ms.nist.read_nist_mainlib`. Converting the full
        NIST 20 library takes a few seconds; use :func:`load_library` to cache
        the result.
        """
        from pyGCxGC.ms.nist import read_nist_mainlib

        data = read_nist_mainlib(path, progress=progress)
        meta = {k: data[k] for k in META_FIELDS if k in data}
        meta["source"] = ["NIST"] * len(data["name"])
        return cls(
            data["mz"],
            data["intensity"],
            data["offsets"],
            name=name,
            **_meta_kwargs(meta),
        )

    def merge(self, other: SpectralLibrary, name: str | None = None) -> SpectralLibrary:
        """Return a new library containing the spectra of ``self`` and ``other``."""
        offsets = np.concatenate([self.offsets, other.offsets[1:] + self.offsets[-1]])
        meta = {}
        for field in META_FIELDS:
            a, b = getattr(self, _attr(field)), getattr(other, _attr(field))
            meta[field] = list(a) + list(b)
        return SpectralLibrary(
            np.concatenate([self.mz, other.mz]),
            np.concatenate([self.intensity, other.intensity]),
            offsets,
            name=name or f"{self.name}+{other.name}",
            **_meta_kwargs(meta),
        )

    # ------------------------------------------------------------- caching
    def save(self, path: str | Path) -> Path:
        """Save the library to an (uncompressed, fast to load) ``.npz`` file."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        arrays = {
            "version": np.array([CACHE_VERSION]),
            "lib_name": np.array([self.name]),
            "mz": self.mz,
            "intensity": self.intensity,
            "offsets": self.offsets,
        }
        for field in META_FIELDS:
            values = getattr(self, _attr(field))
            if field in _INT_FIELDS:
                arrays[field] = np.asarray(values, dtype=np.int64)
            else:
                blob, offs = _pack_strings(values)
                arrays[f"{field}_blob"] = blob
                arrays[f"{field}_offsets"] = offs
        tmp = path.with_suffix(".tmp.npz")
        np.savez(tmp, **arrays)
        tmp.replace(path)
        return path

    @classmethod
    def load(cls, path: str | Path) -> SpectralLibrary:
        """Load a library saved with :meth:`save`."""
        with np.load(path, allow_pickle=False) as z:
            if int(z["version"][0]) not in _READABLE_CACHE_VERSIONS:
                raise ValueError(f"Unsupported library cache version in {path}")
            meta = {}
            for field in META_FIELDS:
                if field not in z and f"{field}_blob" not in z:
                    continue  # older cache without this field: left empty
                if field in _INT_FIELDS:
                    meta[field] = z[field]
                else:
                    meta[field] = _unpack_strings(
                        z[f"{field}_blob"], z[f"{field}_offsets"]
                    )
            return cls(
                z["mz"],
                z["intensity"],
                z["offsets"],
                name=str(z["lib_name"][0]),
                **_meta_kwargs(meta),
            )

    def to_msp(self, path: str | Path, indices=None) -> None:
        """Write (a subset of) the library as an MSP text file."""
        indices = range(len(self)) if indices is None else indices
        with Path(path).open("w", encoding="utf-8") as fh:
            for i in indices:
                fh.write(format_msp(self.entry(i)))
                fh.write("\n")

    # ------------------------------------------------------------- search
    def search_matrix(
        self, mz_min: int, mz_max: int, mz_power: float, int_power: float
    ):
        """
        Sparse matrices used by :mod:`pyGCxGC.ms.search` (cached).

        Returns
        -------
        tuple
            ``(W, P, norms, n_peaks)`` where ``W`` is a CSR matrix with the
            weighted, L2 normalised library spectra restricted to
            ``mz_min..mz_max``, ``P`` its binary peak pattern, ``norms`` the
            weighted norms before normalisation and ``n_peaks`` the number of
            peaks per spectrum inside the mass range.
        """
        key = (int(mz_min), int(mz_max), float(mz_power), float(int_power))
        if key not in self._search_cache:
            from scipy import sparse

            rows = np.repeat(np.arange(len(self)), self.n_peaks)
            keep = (self.mz >= mz_min) & (self.mz <= mz_max) & (self.intensity > 0)
            rows, cols = rows[keep], self.mz[keep].astype(np.int64)
            w = _weights(cols, self.intensity[keep], mz_power, int_power)
            n_cols = int(mz_max) + 1
            shape = (len(self), n_cols)
            W = sparse.csr_matrix((w, (rows, cols)), shape=shape, dtype=np.float64)
            norms = np.sqrt(np.asarray(W.multiply(W).sum(axis=1)).ravel())
            safe = np.where(norms > 0, norms, 1.0)
            W = sparse.diags(1.0 / safe) @ W
            W = W.tocsr().astype(np.float32)
            P = W.copy()
            P.data[:] = 1.0
            n_peaks = np.diff(W.indptr)
            if len(self._search_cache) >= 2:  # keep memory bounded
                self._search_cache.pop(next(iter(self._search_cache)))
            self._search_cache[key] = (W, P, norms, n_peaks)
        return self._search_cache[key]


# ---------------------------------------------------------------------- MSP
_MSP_KEYS = {
    "name": "name",
    "formula": "formula",
    "mw": "mw",
    "cas#": "cas",
    "cas": "cas",
    "casno": "cas",
    "nist#": "nist_no",
    "db#": "db_id",
    "num peaks": "num_peaks",
    "inchikey": "inchikey",
    "smiles": "smiles",
    "inchi": "inchi",
    "pubchem": "pubchem_cid",
    "pubchemid": "pubchem_cid",
    "pubchem_cid": "pubchem_cid",
    "spectrum_type": "spectrum_type",
    "instrument_type": "instrument_type",
    "ion_mode": "ion_mode",
    "precursormz": "precursor_mz",
    "precursor_mz": "precursor_mz",
    "comments": "comments",
    "comment": "comments",
}
# "key=value" entries of MSP comments (MoNA, MassBank) -> record fields
_COMMENT_KEYS = {
    "cas": "cas",
    "smiles": "smiles",
    "inchi": "inchi",
    "inchikey": "inchikey",
    "pubchem cid": "pubchem_cid",
}
_COMMENT_RE = re.compile(r'"([^"=]+)=([^"]*)"')
_PEAK_RE = re.compile(
    r"([0-9]+(?:\.[0-9]*)?(?:[eE][-+]?[0-9]+)?)[\s,:]+([0-9]+(?:\.[0-9]*)?(?:[eE][-+]?[0-9]+)?)"
)


def _open_text(path: Path):
    if path.suffix.lower() == ".zip":
        zf = zipfile.ZipFile(path)
        members = [m for m in zf.namelist() if m.lower().endswith(".msp")]
        if not members:
            raise ValueError(f"No .msp file in {path}")
        return io.TextIOWrapper(zf.open(members[0]), encoding="utf-8", errors="replace")
    return path.open(encoding="utf-8", errors="replace")


def iter_msp(path: str | Path):
    """
    Iterate over the records of an MSP file.

    Yields
    ------
    dict
        Record fields (lower-case keys, see ``_MSP_KEYS``) plus ``mz`` and
        ``intensity`` arrays.
    """
    path = Path(path)
    with _open_text(path) as fh:
        rec: dict = {}
        peaks: list[str] = []
        in_peaks = False
        for raw in fh:
            line = raw.strip()
            if not line:
                if in_peaks or peaks:
                    yield _finish_msp_record(rec, peaks)
                    rec, peaks, in_peaks = {}, [], False
                continue
            if in_peaks:
                peaks.append(line)
                continue
            key, sep, value = line.partition(":")
            if not sep:
                continue
            k = key.strip().lower()
            if k == "num peaks":
                in_peaks = True
            if k in _MSP_KEYS:
                rec.setdefault(_MSP_KEYS[k], value.strip())
            if k in ("comments", "comment"):
                _parse_comment_fields(value, rec)
        if in_peaks or peaks:
            yield _finish_msp_record(rec, peaks)


def _parse_comment_fields(comment: str, rec: dict) -> None:
    """Identifiers stored as ``"key=value"`` in an MSP comment (MoNA style)."""
    for key, value in _COMMENT_RE.findall(comment):
        field = _COMMENT_KEYS.get(key.strip().lower())
        if field and value.strip():
            rec.setdefault(field, value.strip())


def _finish_msp_record(rec: dict, peak_lines: list[str]) -> dict:
    pairs = _PEAK_RE.findall(" ".join(peak_lines))
    arr = np.array(pairs, dtype=np.float64).reshape(-1, 2)
    rec["mz"] = arr[:, 0]
    rec["intensity"] = arr[:, 1]
    return rec


def _is_ei_record(rec: dict) -> bool:
    stype = rec.get("spectrum_type", "").upper()
    if stype and stype not in ("MS1", "MS"):
        return False
    if rec.get("precursor_mz"):
        return False
    itype = rec.get("instrument_type", "").upper()
    return not any(
        tag in itype for tag in ("CI-", "APCI", "ESI", "FI-", "-FI", "MALDI", "APPI")
    )


def format_msp(entry: LibraryEntry) -> str:
    """Format a :class:`LibraryEntry` as an MSP record."""
    lines = [f"Name: {entry.name}"]
    if entry.formula:
        lines.append(f"Formula: {entry.formula}")
    if entry.mw:
        lines.append(f"MW: {entry.mw}")
    if entry.cas:
        lines.append(f"CAS#: {entry.cas}")
    if entry.nist_no:
        lines.append(f"NIST#: {entry.nist_no}")
    lines.append(f"DB#: {entry.db_no}")
    if entry.inchikey:
        lines.append(f"InChIKey: {entry.inchikey}")
    comments = [
        f'"{key}={value}"'
        for key, value in (
            ("SMILES", entry.smiles),
            ("InChI", entry.inchi),
            ("pubchem cid", entry.pubchem_cid or ""),
        )
        if value
    ]
    if comments:
        lines.append("Comments: " + " ".join(comments))
    lines.append(f"Num Peaks: {len(entry.mz)}")
    lines.extend(
        f"{int(m)} {round(float(i))}"
        for m, i in zip(entry.mz, entry.intensity, strict=False)
    )
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------ open data
MONA_API = "https://mona.fiehnlab.ucdavis.edu/rest/downloads"


def download_mona_gcms(dest: str | Path | None = None, timeout: float = 300) -> Path:
    """
    Download the MassBank of North America (MoNA) "GC-MS Spectra" library.

    MoNA is an open spectral database (mostly CC BY / CC BY-SA licensed
    records, see the ``license`` comment of each record). Its GC-MS export
    contains roughly 19 000 spectra (about 18 500 EI spectra); a useful open
    alternative or complement to the NIST library.

    Parameters
    ----------
    dest : str or Path, optional
        Target ``.zip`` file. Defaults to ``mona_gcms.zip`` in
        :func:`default_cache_dir`.
    timeout : float, default 300
        Network timeout in seconds.

    Returns
    -------
    Path
        The downloaded zip file, which can be passed to :func:`load_library`.
    """
    import json
    import urllib.request

    dest = Path(dest) if dest else default_cache_dir() / "mona_gcms.zip"
    with urllib.request.urlopen(f"{MONA_API}/predefined", timeout=timeout) as resp:
        exports = json.load(resp)
    export = next(
        (
            e
            for e in exports
            if e.get("label") == "GC-MS Spectra" and e.get("format", "msp") == "msp"
        ),
        None,
    )
    if export is None:
        raise RuntimeError("MoNA does not list a 'GC-MS Spectra' MSP export")
    export_id = export.get("id") or export.get("mspExport", {}).get("id")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".part")
    with urllib.request.urlopen(
        f"{MONA_API}/retrieve/{export_id}", timeout=timeout
    ) as resp:
        tmp.write_bytes(resp.read())
    if not zipfile.is_zipfile(tmp):
        tmp.unlink(missing_ok=True)
        raise RuntimeError("MoNA download is not a zip file")
    tmp.replace(dest)
    return dest


# ------------------------------------------------------------------ loading
def load_library(path: str | Path, cache_dir: str | Path | None = None, progress=None):
    """
    Load a spectral library from a NIST MAINLIB folder, an MSP file or a cache.

    NIST folders and MSP files are converted once and cached as ``.npz`` in
    ``cache_dir`` (default: :func:`default_cache_dir`), so later loads take
    well under a second.

    Parameters
    ----------
    path : str or Path
        ``MAINLIB`` folder (or its parent / ``nist.db``), ``.msp``/``.zip`` file
        or ``.npz`` cache written by :meth:`SpectralLibrary.save`.
    cache_dir : str or Path, optional
        Where converted libraries are cached. ``False`` disables caching.
    progress : callable, optional
        ``progress(fraction)`` callback used while converting.
    """
    from pyGCxGC.ms.nist import find_nist_db, is_nist_library

    path = Path(path)
    if path.suffix.lower() == ".npz":
        return SpectralLibrary.load(path)
    if is_nist_library(path):
        source = find_nist_db(path)
        lib_name = "NIST " + (source.parent.name if source.parent.name else "mainlib")
        builder = functools.partial(
            SpectralLibrary.from_nist, source, name=lib_name, progress=progress
        )
    elif path.is_file():
        source = path
        builder = functools.partial(SpectralLibrary.from_msp, path)
    else:
        raise FileNotFoundError(f"Not a NIST library folder or MSP file: {path}")

    if cache_dir is False:
        return builder()
    cache_file = library_cache_file(path, cache_dir)
    if cache_file.exists():
        try:
            return SpectralLibrary.load(cache_file)
        except Exception:
            cache_file.unlink(missing_ok=True)
    lib = builder()
    with contextlib.suppress(OSError):
        lib.save(cache_file)
    return lib


def library_cache_file(path: str | Path, cache_dir: str | Path | None = None):
    """
    Cache file that :func:`load_library` uses for a NIST folder or MSP file.

    Returns ``None`` for ``.npz`` files, which are loaded directly.
    """
    from pyGCxGC.ms.nist import find_nist_db, is_nist_library

    path = Path(path)
    if path.suffix.lower() == ".npz":
        return None
    source = find_nist_db(path) if is_nist_library(path) else path
    cache_dir = Path(cache_dir) if cache_dir else default_cache_dir()
    stat = source.stat()
    digest = hashlib.sha1(
        f"{source.resolve()}|{stat.st_size}|{stat.st_mtime_ns}|{CACHE_VERSION}".encode()
    ).hexdigest()[:12]
    return cache_dir / f"library_{source.stem}_{digest}.npz"


# ------------------------------------------------------------------ helpers
def _weights(mz, intensity, mz_power: float, int_power: float) -> np.ndarray:
    mz = np.asarray(mz, dtype=np.float64)
    intensity = np.clip(np.asarray(intensity, dtype=np.float64), 0, None)
    return np.power(mz, mz_power) * np.power(intensity, int_power)


def _to_nominal(mz, intensity) -> tuple[np.ndarray, np.ndarray]:
    mz = np.rint(np.asarray(mz, dtype=np.float64)).astype(np.int64)
    intensity = np.asarray(intensity, dtype=np.float64)
    keep = (mz > 0) & (mz < 65535) & (intensity > 0)
    mz, intensity = mz[keep], intensity[keep]
    if len(mz) == 0:
        return np.zeros(0, np.uint16), np.zeros(0, np.float32)
    uniq, inv = np.unique(mz, return_inverse=True)
    summed = np.zeros(len(uniq))
    np.add.at(summed, inv, intensity)
    summed = summed / summed.max() * 999.0
    return uniq.astype(np.uint16), summed.astype(np.float32)


def _pack_strings(values) -> tuple[np.ndarray, np.ndarray]:
    encoded = [str(v).encode("utf-8") for v in values]
    offsets = np.zeros(len(encoded) + 1, dtype=np.int64)
    np.cumsum([len(e) for e in encoded], out=offsets[1:])
    return np.frombuffer(b"".join(encoded), dtype=np.uint8), offsets


def _unpack_strings(blob: np.ndarray, offsets: np.ndarray) -> list[str]:
    data = blob.tobytes()
    return [data[a:b].decode("utf-8") for a, b in itertools.pairwise(offsets)]


def _safe_int(value) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _attr(field: str) -> str:
    """Attribute holding a metadata field (``name`` is the library name)."""
    return "compound_name" if field == "name" else field


def _meta_kwargs(meta: dict) -> dict:
    """Map the public ``name`` metadata field to the internal attribute."""
    return {_attr(k): v for k, v in meta.items()}
