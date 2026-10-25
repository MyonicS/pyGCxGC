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

Structures (``struct.in6`` and ``struct.db``)
--------------------------------------------
The record id of a ``nist.db`` entry is its payload bytes 0-1 (a big-endian
group number) plus bytes 10-11 (the slot within the group). ``struct.in6`` is
a c-tree index with 0x200 byte nodes (header: ``uint32`` next and previous
node, ``uint16`` key count and bytes used). A leaf node holds 8 byte entries
from offset 0x12, each a ``uint32`` offset into ``struct.db`` followed by the
4 byte record id.

``struct.db`` is not a c-tree file. It is one ``00`` byte followed by
``uint16`` length-prefixed records, each a 2D drawing:

* 5 byte header: number of seed bonds, chain atoms, ring closures, labels and
  aromatic circles.
* Bond orders, 2 bits per bond (LSB first): 0 single, 1 double, 2 triple,
  3 aromatic. The bonds are the seed bonds, then one per chain atom, then the
  ring closures.
* Seed bonds: ``x1 y1 x2 y2`` (two new atoms and the bond between them).
* Chain atoms: ``x y endpoint`` (a new atom bonded to an existing one).
* Ring closures: ``endpoint endpoint``.
* Labels: ``endpoint code``. Unlabelled atoms are carbon with implicit
  hydrogens. Codes below 0x20 are in ``LABEL_CODES``; other codes start a
  NUL-terminated text label (``Fe``, ``PH2``, ``N+``, ``O-``, ...). The
  endpoint ``0xFF`` is a label without an atom (a counter-ion), followed by
  its ``x y``.
* Aromatic circles: ``x y`` of their centres.
* Optional stereo data (not decoded).

Bond ``i`` has the endpoints ``2i`` (its first atom) and ``2i + 1`` (its second
atom; for a chain bond, the atom it is attached to). Coordinates are signed
bytes, where ``0x7F``/``0x81`` add +127/-127 to the next byte. Endpoints are
unsigned bytes, where ``0x7F`` adds 127 to the next byte.

Retention indices (``ri.dat``)
------------------------------
A c-tree variable-length file with one NUL-terminated, tab-separated text
record per measurement (21 fields, see ``RI_FIELDS``). The key is ``C`` plus
the CAS digits, ``U`` plus the NIST number (compounds without a CAS number),
or ``R`` plus an id of a compound that has no spectrum in the library. The
phase type is spelled in several ways (``SSNP``, ``semi-std-non-polar``, ...)
and normalised by ``RI_PHASE_TYPES``. ``Lee RI`` values use the PAH scale
(naphthalene 200, phenanthrene 300), not the n-alkane scale. ``riref.dat``
holds the full citations; how they link to the reference codes is not known.
"""

from __future__ import annotations

import base64
import functools
import re
import struct
from dataclasses import dataclass, field
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

# Structure label codes below 0x20: (element, hydrogens). The second spelling
# of a label (HO, HN, H2N, 19, 20) is the form used left of a bond.
LABEL_CODES = {
    1: ("O", 0), 2: ("O", 1), 3: ("O", 1), 4: ("N", 0), 5: ("N", 1), 6: ("N", 1),
    7: ("N", 2), 8: ("N", 2), 9: ("H", 0), 10: ("Cl", 0), 11: ("Br", 0),
    12: ("F", 0), 13: ("Si", 0), 14: ("S", 0), 15: ("S", 1), 16: ("Si", 3),
    17: ("P", 0), 19: ("Cl", 0), 20: ("Br", 0),
}  # fmt: skip
_TEXT_LABEL = re.compile(r"^[\^\\]?(?:H(\d*))?([A-Z][a-z]?)(?:H(\d*))?([+-]\d*|\.|:)?$")
BOND_AROMATIC = 4
_NODE_SIZE = 0x200

# Fields of a ri.dat record, in order.
RI_FIELDS = (
    "key", "name", "formula", "ri", "column_class", "phase_type", "phase",
    "length", "carrier", "support", "diameter", "film", "ri_type", "program",
    "t_start", "t_end", "rate", "hold_start", "hold_end", "program_text",
    "reference",
)  # fmt: skip
RI_PHASE_TYPES = {
    "semi-standard non-polar": "semi-standard non-polar",
    "ssnp": "semi-standard non-polar",
    "semi-std-non-polar": "semi-standard non-polar",
    "semi-standatd non-polar": "semi-standard non-polar",
    "semi-standard-non-polar": "semi-standard non-polar",
    "standard non-polar": "standard non-polar",
    "snp": "standard non-polar",
    "std-non-polar": "standard non-polar",
    "standard polar": "standard polar",
    "std-polar": "standard polar",
    "sp": "standard polar",
    "standard semi-polar": "standard semi-polar",
    "semi-std-polar": "semi-standard polar",
    "all column types": "all",
    "non-std": "non-standard",
}
RI_TYPES = {
    "normal alkane ri": "normal alkane",
    "normal alkane ri value specified by scale definition": "normal alkane",
    "linear ri": "linear",
    "kovats ri": "kovats",
    "lee ri": "lee",
    "lee ri value specified by scale definition": "lee",
}
# RI types on the n-alkane scale (all but Lee).
ALKANE_RI_TYPES = ("normal alkane", "linear", "kovats")

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


# ---------------------------------------------------------------- structures
@dataclass
class StructureAtom:
    """An atom of a NIST structure drawing."""

    element: str
    hydrogens: int | None = None  # None: implicit (unlabelled carbon)
    charge: int = 0
    radicals: int = 0
    isotope: int = 0
    x: int = 0
    y: int = 0


@dataclass
class NistStructure:
    """
    A decoded ``struct.db`` drawing.

    ``bonds`` are ``(atom, atom, order)`` with order 1, 2, 3 or
    ``BOND_AROMATIC``. Counter-ions are atoms without bonds. ``stereo`` is the
    undecoded stereo data.
    """

    atoms: list[StructureAtom] = field(default_factory=list)
    bonds: list[tuple[int, int, int]] = field(default_factory=list)
    circles: list[tuple[int, int]] = field(default_factory=list)
    stereo: bytes = b""


def record_id(rec: bytes) -> bytes:
    """The id of a ``nist.db`` record payload, as used by ``struct.in6``."""
    return rec[0:2] + rec[10:12]


def read_struct_records(path: str | Path) -> dict[int, bytes]:
    """
    All records of a ``struct.db`` file.

    Returns
    -------
    dict
        Record payload keyed by the file offset of the record.
    """
    buf = Path(path).read_bytes()
    out, off = {}, 1
    while off + 2 <= len(buf):
        (n,) = struct.unpack_from("<H", buf, off)
        out[off] = buf[off + 2 : off + 2 + n]
        off += 2 + n
    return out


def read_struct_index(path: str | Path, records=None) -> dict[bytes, int]:
    """
    Read ``struct.in6``: ``struct.db`` offset keyed by :func:`record_id`.

    Parameters
    ----------
    path : str or Path
        The ``struct.in6`` file.
    records : container of int, optional
        Valid record offsets (keys of :func:`read_struct_records`). A node is
        a leaf when all its pointers are valid records; without ``records``
        every node with 8 byte entries is used.
    """
    buf = Path(path).read_bytes()
    index = {}
    for node in range(_NODE_SIZE, len(buf) - _NODE_SIZE + 1, _NODE_SIZE):
        _, _, nkv, nkb = struct.unpack_from("<IIHH", buf, node)
        if nkv == 0 or nkb != 8 * nkv or 0x12 + nkb > _NODE_SIZE:
            continue
        entries = []
        for k in range(nkv):
            e = node + 0x12 + 8 * k
            (ptr,) = struct.unpack_from("<I", buf, e)
            entries.append((buf[e + 4 : e + 8], ptr))
        if records is None or all(ptr in records for _, ptr in entries):
            index.update(entries)
    return index


def _coord(rec: bytes, off: int) -> tuple[int, int]:
    v = 0
    while rec[off] in (0x7F, 0x81):
        v += 127 if rec[off] == 0x7F else -127
        off += 1
    b = rec[off]
    return v + (b - 256 if b > 127 else b), off + 1


def _endpoint(rec: bytes, off: int) -> tuple[int, int]:
    v = 0
    while rec[off] == 0x7F:
        v += 127
        off += 1
    return v + rec[off], off + 1


def _label_atom(code) -> StructureAtom:
    if isinstance(code, int):
        if code not in LABEL_CODES:
            raise ValueError(f"Unknown structure label code {code}")
        element, hydrogens = LABEL_CODES[code]
        return StructureAtom(element, hydrogens)
    m = _TEXT_LABEL.match(code)
    if not m:
        raise ValueError(f"Unsupported structure label {code!r}")
    h_before, element, h_after, suffix = m.groups()
    hydrogens = sum(int(g or 1) for g in (h_before, h_after) if g is not None)
    atom = StructureAtom(element, hydrogens)
    if element == "D":
        atom.element, atom.isotope = "H", 2
    if suffix == ".":
        atom.radicals = 1
    elif suffix == ":":
        atom.radicals = 2
    elif suffix:
        atom.charge = (1 if suffix[0] == "+" else -1) * int(suffix[1:] or 1)
    return atom


def decode_structure(rec: bytes) -> NistStructure:
    """
    Decode a ``struct.db`` record payload (see the module docstring).

    Raises
    ------
    ValueError
        Unknown label, or the record is truncated.
    """
    try:
        return _decode_structure(rec)
    except (IndexError, KeyError) as exc:
        raise ValueError("Malformed NIST structure record") from exc


def _decode_structure(rec: bytes) -> NistStructure:
    n_seed, n_chain, n_ring, n_label, n_circle = rec[:5]
    n_bonds = n_seed + n_chain + n_ring
    off = 5
    n_mask = (2 * n_bonds + 7) // 8
    mask = int.from_bytes(rec[off : off + n_mask], "little")
    off += n_mask
    orders = [((mask >> (2 * i)) & 3) + 1 for i in range(n_bonds)]  # 4 = aromatic
    xy, pairs, ends = [], [], []  # ends[endpoint] -> atom
    for _ in range(n_seed):
        for _ in range(2):
            x, off = _coord(rec, off)
            y, off = _coord(rec, off)
            xy.append((x, y))
        a = len(xy) - 2
        pairs.append((a, a + 1))
        ends += [a, a + 1]
    for _ in range(n_chain):
        x, off = _coord(rec, off)
        y, off = _coord(rec, off)
        parent, off = _endpoint(rec, off)
        a = len(xy)
        xy.append((x, y))
        pairs.append((a, ends[parent]))
        ends += [a, ends[parent]]
    for _ in range(n_ring):
        e1, off = _endpoint(rec, off)
        e2, off = _endpoint(rec, off)
        pairs.append((ends[e1], ends[e2]))
        ends += [ends[e1], ends[e2]]
    atoms = [StructureAtom("C", x=x, y=y) for x, y in xy]
    for _ in range(n_label):
        if rec[off] == 0xFF:
            x, off = _coord(rec, off + 1)
            y, off = _coord(rec, off)
            target = None
        else:
            e, off = _endpoint(rec, off)
            target = ends[e]
        code = rec[off]
        if code >= 0x20:
            end = rec.index(0, off)
            code = rec[off:end].decode("latin-1")
            off = end + 1
        else:
            off += 1
        atom = _label_atom(code)
        if target is None:
            atom.x, atom.y = x, y
            atoms.append(atom)
        else:
            atom.x, atom.y = atoms[target].x, atoms[target].y
            atoms[target] = atom
    circles = []
    for _ in range(n_circle):
        x, off = _coord(rec, off)
        y, off = _coord(rec, off)
        circles.append((x, y))
    bonds = [(a, b, o) for (a, b), o in zip(pairs, orders, strict=True)]
    return NistStructure(atoms, bonds, circles, stereo=bytes(rec[off:]))


@functools.lru_cache(maxsize=1)
def _rdkit():
    try:
        from rdkit import Chem, RDLogger
    except ImportError:
        return None
    RDLogger.DisableLog("rdApp.*")
    return Chem


def structure_to_rdkit(structure: NistStructure):
    """
    RDKit molecule of a decoded structure, without stereo and explicit H atoms.

    Molecules that RDKit cannot sanitise (e.g. metal complexes or nitro groups
    drawn with pentavalent N) are only partially sanitised.

    Raises
    ------
    ImportError
        RDKit is not installed.
    """
    Chem = _rdkit()
    if Chem is None:
        raise ImportError("RDKit is required to convert NIST structures")
    types = {
        1: Chem.BondType.SINGLE,
        2: Chem.BondType.DOUBLE,
        3: Chem.BondType.TRIPLE,
        BOND_AROMATIC: Chem.BondType.AROMATIC,
    }
    mol = Chem.RWMol()
    for a in structure.atoms:
        atom = Chem.Atom(a.element)
        if a.hydrogens is not None:
            atom.SetNoImplicit(True)
            atom.SetNumExplicitHs(a.hydrogens)
        atom.SetFormalCharge(a.charge)
        atom.SetNumRadicalElectrons(a.radicals)
        atom.SetIsotope(a.isotope)
        mol.AddAtom(atom)
    for a, b, order in structure.bonds:
        if a == b or mol.GetBondBetweenAtoms(a, b) is not None:
            continue
        mol.AddBond(a, b, types[order])
        if order == BOND_AROMATIC:
            mol.GetBondBetweenAtoms(a, b).SetIsAromatic(True)
            mol.GetAtomWithIdx(a).SetIsAromatic(True)
            mol.GetAtomWithIdx(b).SetIsAromatic(True)
    mol = mol.GetMol()
    try:
        Chem.SanitizeMol(mol)
        return Chem.RemoveHs(mol)
    except Exception:
        mol.UpdatePropertyCache(strict=False)
        Chem.SanitizeMol(
            mol,
            Chem.SANITIZE_ALL ^ Chem.SANITIZE_PROPERTIES ^ Chem.SANITIZE_KEKULIZE,
            catchErrors=True,
        )
        return Chem.RemoveHs(mol, sanitize=False)


def structure_identifiers(data: bytes | str) -> tuple[str, str] | None:
    """
    SMILES and InChIKey of a ``struct.db`` record.

    Parameters
    ----------
    data : bytes or str
        The record payload, or its base64 text as stored in the
        ``nist_structure`` field of a library.

    Returns
    -------
    (smiles, inchikey) or None
        Empty strings when the record cannot be converted (the SMILES is only
        kept when RDKit can parse it back), ``None`` when RDKit is not
        installed. Stereo is not included.
    """
    Chem = _rdkit()
    if Chem is None:
        return None
    if isinstance(data, str):
        data = base64.b64decode(data)
    try:
        mol = structure_to_rdkit(decode_structure(data))
        smiles = Chem.MolToSmiles(mol)
    except Exception:
        return "", ""
    if Chem.MolFromSmiles(smiles) is None:
        return "", ""
    try:
        inchikey = Chem.MolToInchiKey(mol)
    except Exception:
        inchikey = ""
    return smiles, inchikey


def read_nist_structures(path: str | Path) -> dict[bytes, bytes]:
    """
    Structure records of a NIST ``MAINLIB`` folder keyed by :func:`record_id`.

    Returns an empty dict when the folder has no ``struct.db``/``struct.in6``.
    """
    folder = find_nist_db(path).parent
    db, index = folder / "struct.db", folder / "struct.in6"
    if not (db.is_file() and index.is_file()):
        return {}
    records = read_struct_records(db)
    return {key: records[ptr] for key, ptr in read_struct_index(index, records).items()}


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
        metadata lists ``name``, ``formula``, ``cas``, ``nist_no``, ``mw``,
        ``db_no`` (1-based position in the library, as shown by NIST MS Search)
        and ``nist_structure`` (base64 ``struct.db`` record, empty if there is
        none; see :func:`structure_identifiers`).
    """
    db = find_nist_db(path)
    records = list(iter_ctree_records(db))
    if not records:
        raise ValueError(f"No spectra found in {db}")
    structures = read_nist_structures(db)
    names, formulas, cas, nist_no, mw, structs = [], [], [], [], [], []
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
        drawing = structures.get(record_id(rec))
        structs.append(base64.b64encode(drawing).decode("ascii") if drawing else "")
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
        "nist_structure": structs,
    }


# --------------------------------------------------------- retention indices
_NUMBER = re.compile(r"[-+]?\d+(?:\.\d+)?")


def _number(text: str, micro_to_milli: bool = False) -> float:
    m = _NUMBER.search(text)
    if not m:
        return np.nan
    value = float(m.group())
    if micro_to_milli and "um" in text:
        value /= 1000.0
    return value


def read_nist_ri(path: str | Path):
    """
    Read the retention indices of a NIST ``MAINLIB`` folder (``ri.dat``).

    Parameters
    ----------
    path : str or Path
        The ``MAINLIB`` folder, its parent, ``nist.db`` or ``ri.dat`` itself.

    Returns
    -------
    pandas.DataFrame
        One row per measurement with the text fields of ``RI_FIELDS`` and

        * ``ri`` (float), ``cas`` (formatted, ``C`` keys), ``nist_no``
          (``U`` keys, else 0),
        * ``phase_type`` and ``ri_type`` normalised (``RI_PHASE_TYPES``,
          ``RI_TYPES``; unknown spellings are lower-cased), ``program`` lower
          case (``ramp``, ``complex``, ``isothermal``),
        * numbers parsed from the column and program fields: ``length_m``,
          ``diameter_mm``, ``film_um``, ``t_start_c``, ``t_end_c``,
          ``rate_k_min`` (NaN when not given).
    """
    import pandas as pd

    path = Path(path)
    if path.is_file() and path.name.lower() != "ri.dat":
        path = path.parent
    if path.is_dir():
        found = [path / "ri.dat", path / "MAINLIB" / "ri.dat", *path.glob("*/ri.dat")]
        path = next((c for c in found if c.is_file()), path / "ri.dat")
    if not path.is_file():
        raise FileNotFoundError(f"Could not find a NIST 'ri.dat' file in {path.parent}")
    rows = [
        rec.rstrip(b"\0").decode("latin-1").split("\t")
        for rec in iter_ctree_records(path)
    ]
    rows = [r for r in rows if len(r) == len(RI_FIELDS)]
    table = pd.DataFrame(rows, columns=list(RI_FIELDS))
    table["ri"] = pd.to_numeric(table["ri"], errors="coerce")
    table["name"] = table["name"].str.strip('"')
    kind, digits = table["key"].str[0], table["key"].str[1:]
    table["cas"] = [
        format_cas(int(d)) if k == "C" and d.isdigit() else ""
        for k, d in zip(kind, digits, strict=True)
    ]
    table["nist_no"] = [
        int(d) if k == "U" and d.isdigit() else 0
        for k, d in zip(kind, digits, strict=True)
    ]
    phase = table["phase_type"].str.strip().str.lower()
    table["phase_type"] = phase.map(RI_PHASE_TYPES).fillna(phase)
    ri_type = table["ri_type"].str.strip().str.lower()
    table["ri_type"] = ri_type.map(RI_TYPES).fillna(ri_type)
    table["program"] = table["program"].str.lower().replace({"isotherm": "isothermal"})
    table["length_m"] = table["length"].map(_number)
    table["diameter_mm"] = table["diameter"].map(
        lambda t: _number(t, micro_to_milli=True)
    )
    table["film_um"] = table["film"].map(_number)
    table["t_start_c"] = table["t_start"].map(_number)
    table["t_end_c"] = table["t_end"].map(_number)
    table["rate_k_min"] = table["rate"].map(_number)
    return table


def ri_by_compound(
    table,
    phase_type: str = "semi-standard non-polar",
    ri_types=ALKANE_RI_TYPES,
    capillary_only: bool = True,
):
    """
    Median retention index per compound for one phase type.

    Parameters
    ----------
    table : pandas.DataFrame
        Output of :func:`read_nist_ri`.
    phase_type : str, default "semi-standard non-polar"
        Normalised phase type (5 % phenyl columns such as DB-5 are
        semi-standard non-polar, 100 % dimethylpolysiloxane such as DB-1
        standard non-polar, PEG/wax standard polar). Rows with the phase type
        ``all`` (the n-alkanes, 100 x carbon number by definition) are always
        included.
    ri_types : sequence of str
        RI types to include; the default excludes the Lee (PAH) scale.
    capillary_only : bool, default True
        Drop packed-column measurements.

    Returns
    -------
    pandas.DataFrame
        Indexed by ``key`` with ``cas``, ``nist_no``, ``name``, ``formula``,
        ``ri`` (median), ``ri_mad`` (median absolute deviation) and ``n``.
    """
    phase = table["phase_type"].isin([phase_type, "all"])
    sel = table[phase & table["ri_type"].isin(ri_types)]
    if capillary_only:
        sel = sel[(sel["column_class"] == "Capillary") | (sel["phase_type"] == "all")]
    sel = sel.dropna(subset=["ri"])
    grouped = sel.groupby("key")
    out = grouped[["cas", "nist_no", "name", "formula"]].first()
    out["ri"] = grouped["ri"].median()
    out["ri_mad"] = (
        (sel["ri"] - sel["key"].map(out["ri"])).abs().groupby(sel["key"]).median()
    )
    out["n"] = grouped.size()
    return out


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
