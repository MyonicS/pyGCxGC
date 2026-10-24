"""
Molecular structures of library compounds.

:func:`resolve` finds the structure (SMILES / InChI) of a library entry by
trying, in order:

1. the identifiers stored in the entry (MoNA's MSP export carries SMILES,
   InChI and InChIKey);
2. structures from a licensed NIST copy (``MAINLIB/struct.db``, ``ichi.dat``),
   which are not decoded yet;
3. an opt-in online lookup at PubChem (PUG REST) by InChIKey, then CAS
   number, then name.

Only compound identifiers of library entries are ever sent to PubChem, never
spectra or file names. Lookups, including "not found", are cached in a local
SQLite database (:class:`StructureCache`), so each compound is looked up once.

:func:`depict_svg` draws a structure with RDKit (an optional dependency, part
of the ``app`` extra).

Examples
--------
>>> from pyGCxGC.ms.structures import StructureCache, resolve, depict_svg
>>> entry = library.entry(hit.index)  # doctest: +SKIP
>>> s = resolve(entry, online=True, cache=StructureCache())  # doctest: +SKIP
>>> svg = depict_svg(s, dark=True)  # doctest: +SKIP
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from pyGCxGC.ms.library import LibraryEntry

PUBCHEM_URL = "https://pubchem.ncbi.nlm.nih.gov/rest/pug"
# ``SMILES`` replaced ``IsomericSMILES`` in 2025; the older names are still
# accepted but answered as ``SMILES`` / ``ConnectivitySMILES``
PUBCHEM_PROPERTIES = "SMILES,InChI,InChIKey"
_SMILES_KEYS = ("SMILES", "IsomericSMILES", "CanonicalSMILES", "ConnectivitySMILES")


@dataclass
class Structure:
    """
    Structure of a compound.

    Attributes
    ----------
    smiles, inchi, inchikey : str
        Identifiers (at least one of ``smiles`` and ``inchi`` is set).
    pubchem_cid : int
        PubChem compound id (0 = unknown).
    source : str
        Where the structure comes from (a library name or ``"PubChem"``).
    """

    smiles: str = ""
    inchi: str = ""
    inchikey: str = ""
    pubchem_cid: int = 0
    source: str = ""

    @property
    def key(self) -> str:
        """Stable identifier: the InChIKey, else a hash of the SMILES/InChI."""
        if self.inchikey:
            return self.inchikey
        digest = hashlib.sha1((self.smiles or self.inchi).encode()).hexdigest()
        return f"h{digest[:24]}"

    def to_dict(self) -> dict:
        return {**asdict(self), "key": self.key}


def from_entry(entry: LibraryEntry, source: str = "library") -> Structure | None:
    """The structure stored in a library entry, if any."""
    smiles = (entry.smiles or "").strip()
    inchi = (entry.inchi or "").strip()
    if not smiles and not inchi:
        return None
    return Structure(
        smiles=smiles,
        inchi=inchi,
        inchikey=(entry.inchikey or "").strip(),
        pubchem_cid=int(entry.pubchem_cid or 0),
        source=source,
    )


def lookup_keys(entry: LibraryEntry) -> list[tuple[str, str]]:
    """``(kind, value)`` identifiers of an entry for online lookup, in order."""
    keys = []
    if entry.inchikey and entry.inchikey.strip():
        keys.append(("inchikey", entry.inchikey.strip()))
    cas = (entry.cas or "").strip()
    if cas and cas not in ("0", "0-00-0"):
        keys.append(("cas", cas))
    name = (entry.name or "").strip()
    if name:
        keys.append(("name", name))
    return keys


# ------------------------------------------------------------------ cache
class StructureCache:
    """
    SQLite cache of structure lookups and of resolved structures.

    Parameters
    ----------
    path : str or Path, optional
        Database file; in memory if omitted.
    """

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else None
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(
            str(self.path) if self.path else ":memory:", check_same_thread=False
        )
        with self._lock, self._db:
            self._db.execute(
                "CREATE TABLE IF NOT EXISTS lookups (kind TEXT, value TEXT, "
                "structure TEXT, time REAL, PRIMARY KEY (kind, value))"
            )
            self._db.execute(
                "CREATE TABLE IF NOT EXISTS structures (key TEXT PRIMARY KEY, "
                "structure TEXT)"
            )

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def lookup(self, kind: str, value: str) -> tuple[bool, Structure | None]:
        """``(known, structure)``; ``(True, None)`` means "not found" was cached."""
        with self._lock:
            row = self._db.execute(
                "SELECT structure FROM lookups WHERE kind = ? AND value = ?",
                (kind, _norm(kind, value)),
            ).fetchone()
        if row is None:
            return False, None
        return True, _decode(row[0])

    def put_lookup(self, kind: str, value: str, structure: Structure | None) -> None:
        text = json.dumps(asdict(structure)) if structure else None
        with self._lock, self._db:
            self._db.execute(
                "INSERT OR REPLACE INTO lookups VALUES (?, ?, ?, ?)",
                (kind, _norm(kind, value), text, time.time()),
            )
        if structure is not None:
            self.put(structure)

    def put(self, structure: Structure) -> str:
        """Remember a structure under its :attr:`~Structure.key`; returns the key."""
        with self._lock, self._db:
            self._db.execute(
                "INSERT OR REPLACE INTO structures VALUES (?, ?)",
                (structure.key, json.dumps(asdict(structure))),
            )
        return structure.key

    def get(self, key: str) -> Structure | None:
        """A structure remembered with :meth:`put`."""
        with self._lock:
            row = self._db.execute(
                "SELECT structure FROM structures WHERE key = ?", (key,)
            ).fetchone()
        return _decode(row[0]) if row else None


def _norm(kind: str, value: str) -> str:
    return value.strip().lower() if kind == "name" else value.strip()


def _decode(text: str | None) -> Structure | None:
    return Structure(**json.loads(text)) if text else None


# ------------------------------------------------------------------ PubChem
class PubChem:
    """
    Minimal PubChem PUG REST client (structure lookups only).

    Requests are spaced to stay under PubChem's limit of 5 per second.

    Parameters
    ----------
    timeout : float, default 10
        Timeout of each request (s).
    opener : callable, optional
        ``opener(url, data, timeout) -> bytes`` (for tests); it raises
        ``urllib.error.HTTPError`` with code 404 when nothing is found.
    """

    min_interval = 0.21  # s

    def __init__(self, timeout: float = 10.0, opener: Callable | None = None):
        self.timeout = timeout
        self.opener = opener or _urlopen
        self._lock = threading.Lock()
        self._last = 0.0

    def lookup(self, kind: str, value: str) -> Structure | None:
        """
        Structure of the compound with this InChIKey, CAS number or name.

        Returns ``None`` when PubChem does not know the compound. Network and
        server errors raise ``OSError``.
        """
        if kind == "inchikey":
            url = (
                f"{PUBCHEM_URL}/compound/inchikey/{urllib.parse.quote(value)}"
                f"/property/{PUBCHEM_PROPERTIES}/JSON"
            )
            data = None
        elif kind in ("cas", "name"):
            # POST, so names with "/" or other special characters work
            url = f"{PUBCHEM_URL}/compound/name/property/{PUBCHEM_PROPERTIES}/JSON"
            data = urllib.parse.urlencode({"name": value}).encode()
        else:
            raise ValueError(f"Unknown identifier kind {kind!r}")
        self._wait()
        try:
            body = self.opener(url, data, self.timeout)
        except urllib.error.HTTPError as exc:
            if exc.code in (400, 404):  # 400: PubChem cannot parse the name
                return None
            raise OSError(f"PubChem: HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise OSError(f"PubChem unreachable: {exc.reason}") from exc
        props = json.loads(body).get("PropertyTable", {}).get("Properties", [])
        if not props:
            return None
        p = props[0]
        smiles = next((p[k] for k in _SMILES_KEYS if p.get(k)), "")
        if not smiles and not p.get("InChI"):
            return None
        return Structure(
            smiles=smiles,
            inchi=p.get("InChI", ""),
            inchikey=p.get("InChIKey", ""),
            pubchem_cid=int(p.get("CID", 0) or 0),
            source="PubChem",
        )

    def _wait(self) -> None:
        with self._lock:
            delay = self._last + self.min_interval - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            self._last = time.monotonic()


def _urlopen(url: str, data: bytes | None, timeout: float) -> bytes:
    request = urllib.request.Request(url, data=data, headers={"User-Agent": "pyGCxGC"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


# ------------------------------------------------------------------ resolve
def resolve(
    entry: LibraryEntry,
    online: bool = False,
    cache: StructureCache | None = None,
    pubchem: PubChem | None = None,
    source: str = "library",
) -> Structure | None:
    """
    Structure of a library entry, or ``None``.

    Parameters
    ----------
    entry : LibraryEntry
    online : bool, default False
        Look the compound up at PubChem when the entry has no structure.
        Only its InChIKey, CAS number and name are sent.
    cache : StructureCache, optional
        Cache of earlier lookups (consulted even when ``online`` is false).
    pubchem : PubChem, optional
        Client to use (default: a new :class:`PubChem`).
    source : str, default "library"
        Source noted on structures taken from the entry (e.g. the library
        name).
    """
    structure = from_entry(entry, source)
    if structure is not None:
        if cache is not None:
            cache.put(structure)
        return structure
    for kind, value in lookup_keys(entry):
        known, found = cache.lookup(kind, value) if cache else (False, None)
        if not known:
            if not online:
                return None
            pubchem = pubchem or PubChem()
            found = pubchem.lookup(kind, value)
            if cache is not None:
                cache.put_lookup(kind, value, found)
        if found is not None:
            return found
    return None


def is_resolved(entry: LibraryEntry, cache: StructureCache | None) -> bool:
    """Whether :func:`resolve` can answer without going online."""
    if from_entry(entry) is not None:
        return True
    for kind, value in lookup_keys(entry):
        known, found = cache.lookup(kind, value) if cache else (False, None)
        if not known:
            return False
        if found is not None:
            return True
    return True


# ------------------------------------------------------------------ drawing
def rdkit_available() -> bool:
    try:
        import rdkit.Chem  # noqa: F401
    except ImportError:
        return False
    return True


def depict_svg(
    structure: Structure, width: int = 300, height: int = 220, dark: bool = False
) -> str:
    """
    SVG depiction of a structure (RDKit, transparent background).

    Parameters
    ----------
    structure : Structure
    width, height : int
        Size in pixels.
    dark : bool, default False
        Light bonds and atom colours for a dark background.

    Raises
    ------
    ImportError
        RDKit is not installed.
    ValueError
        The SMILES and InChI cannot be parsed.
    """
    from rdkit import Chem, RDLogger
    from rdkit.Chem import rdDepictor
    from rdkit.Chem.Draw import rdMolDraw2D

    RDLogger.DisableLog("rdApp.*")
    mol = None
    if structure.smiles:
        mol = Chem.MolFromSmiles(structure.smiles)
    if mol is None and structure.inchi:
        with contextlib.suppress(Exception):
            mol = Chem.MolFromInchi(structure.inchi)
    if mol is None:
        raise ValueError("The structure cannot be parsed")
    rdDepictor.SetPreferCoordGen(True)
    rdDepictor.Compute2DCoords(mol)
    drawer = rdMolDraw2D.MolDraw2DSVG(int(width), int(height))
    opts = drawer.drawOptions()
    if dark:
        rdMolDraw2D.SetDarkMode(opts)
    opts.clearBackground = False
    opts.padding = 0.08
    drawer.DrawMolecule(mol)
    drawer.FinishDrawing()
    svg = drawer.GetDrawingText()
    # strip the XML declaration so the SVG can be inlined
    return svg[svg.index("<svg") :]


__all__ = [
    "PubChem",
    "Structure",
    "StructureCache",
    "depict_svg",
    "from_entry",
    "is_resolved",
    "lookup_keys",
    "rdkit_available",
    "resolve",
]
