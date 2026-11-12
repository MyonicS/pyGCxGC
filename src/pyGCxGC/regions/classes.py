"""
Peak classes: labels, the rule language, class schemes and evidence cards.

Peaks of a GCxGC-MS peak table are labelled with a class (``category``) and
an optional ``subclass``. Labels come from three sources:

* rules (``category_source = "rule:<scheme>/<class>"``): a class scheme
  applied to the peaks, e.g. the built-in ``hydrocarbon-group-type`` scheme,
  which only *proposes* labels;
* ``agent``: individual judgements of an AI agent, with a short reason;
* ``user``: labels set in the app or from Python. They win: rules and agents
  never change them unless asked to (``overwrite_user``).

Rule language
-------------
A class is defined by a list of criteria that must all hold; a class without
criteria is a label that rules never assign (e.g. ``excluded``). ``{"any":
[...]}`` holds when one of its items holds (an item may itself be a list of
criteria, all of which must hold). Criteria on the library hits:

``{"smarts": "c1ccccc1"}`` / ``{"not_smarts": ...}``
    The hit's structure contains / does not contain the pattern (RDKit).
``{"aromatic_carbocycles": [lo, hi]}``, ``{"rings": [lo, hi]}``
    Number of aromatic carbocycles / rings of the structure (``hi`` may be
    ``null``).
``{"formula": {"only": ["C", "H"], "contains_any": ["Cl"], "c": [lo, hi], "dbe": [lo, hi]}}``
    Element set, carbon number and double-bond equivalents of the formula.
``{"hit_name": "regex"}``
    A regular expression (case-insensitive) on the hit name.
``{"min_match": 700}``, ``{"min_probability": 50}``, ``{"min_delta_match": 20}``
    Hit quality (spec MPR-1/3).

and on the peak itself:

``{"ions": [{"mz": 91, "min": 30, "max": 100}]}``
    Ions of the measured spectrum in percent of the base peak.
``{"base_peak": [91, 105]}``
    The base peak of the measured spectrum.
``{"region": {"rt1_min": [a, b], "rt2_s": [c, d]}}`` or ``{"region": {"mask": "name"}}``
    The apex position.

Without a vote, the hit criteria are checked on the best hit. A class (or a
scheme) can vote instead: ``"vote": {"top_k": 5, "min_match": 700,
"min_share": 0.6}`` checks every hit of the top ``k`` with a match of at least
``min_match`` and proposes the class when the hits that satisfy it hold at
least ``min_share`` of the match-weighted vote.

Structures are the SMILES of the library entries
(:meth:`~pyGCxGC.ms.library.SpectralLibrary.structure_ids`). For hits without
a structure (or without RDKit) the structure criteria do not apply: a class's
``fallback`` criteria are used instead, and these votes count half; without
a fallback only the other criteria apply (a class that has only structure
criteria then does not match).

Examples
--------
>>> scheme = ClassScheme.builtin("hydrocarbon-group-type")  # doctest: +SKIP
>>> labels = propose(table, hits, scheme, libraries={"NIST": lib})  # doctest: +SKIP
"""

from __future__ import annotations

import copy
import functools
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from pyGCxGC.masks import Grid
    from pyGCxGC.ms.search import Hit

SCHEME_DIR = Path(__file__).with_name("schemes")
BUILTIN_SCHEMES = ("hydrocarbon-group-type",)
LABEL_COLUMNS = (
    "category",
    "subclass",
    "category_source",
    "category_reason",
    "category_confidence",
    "reviewed",
    "proposal",
    "proposal_subclass",
    "proposal_share",
)
LABEL_DEFAULTS: dict[str, Any] = {
    "category": "",
    "subclass": "",
    "category_source": "",
    "category_reason": "",
    "category_confidence": "",
    "reviewed": False,
    "proposal": "",
    "proposal_subclass": "",
    "proposal_share": 0.0,
}
CONFIDENCES = ("high", "medium", "low")
STRUCTURE_CRITERIA = ("smarts", "not_smarts", "aromatic_carbocycles", "rings")
HIT_CRITERIA = (
    *STRUCTURE_CRITERIA,
    "formula",
    "hit_name",
    "min_match",
    "min_probability",
    "min_delta_match",
)
PEAK_CRITERIA = ("ions", "base_peak", "region")
DEFAULT_VOTE = {"top_k": 5, "min_match": 700, "min_share": 0.6}


def add_label_columns(table: pd.DataFrame) -> pd.DataFrame:
    """Add the label columns that are missing (in place); returns ``table``."""
    for col in LABEL_COLUMNS:
        if col not in table:
            table[col] = [LABEL_DEFAULTS[col]] * len(table)
    table["reviewed"] = table["reviewed"].fillna(False).astype(bool)
    for col in ("category", "subclass", "category_source", "proposal"):
        table[col] = table[col].fillna("").astype(str)
    return table


# ------------------------------------------------------------------ chemistry
_FORMULA_RE = re.compile(r"([A-Z][a-z]?)(\d*)")


@functools.lru_cache(maxsize=65536)
def parse_formula(formula: str) -> dict[str, int]:
    """Element counts of a molecular formula (``{}`` if it cannot be read)."""
    text = re.sub(r"[\s\[\]+-]", "", str(formula))
    counts: dict[str, int] = {}
    pos = 0
    for m in _FORMULA_RE.finditer(text):
        if m.start() != pos:
            return {}
        counts[m.group(1)] = counts.get(m.group(1), 0) + int(m.group(2) or 1)
        pos = m.end()
    return counts if pos == len(text) else {}


def dbe(counts: Mapping[str, int]) -> float | None:
    """Double-bond equivalents of a formula (``None`` without carbon)."""
    c = counts.get("C", 0) + counts.get("Si", 0)
    if not c:
        return None
    halogens = sum(counts.get(x, 0) for x in ("F", "Cl", "Br", "I"))
    n = counts.get("N", 0) + counts.get("P", 0)
    return c - (counts.get("H", 0) + halogens) / 2 + n / 2 + 1


def rdkit_available() -> bool:
    try:
        import rdkit  # noqa: F401
    except ImportError:
        return False
    return True


@functools.lru_cache(maxsize=16384)
def _mol(smiles: str):
    if not smiles or not rdkit_available():
        return None
    from rdkit import Chem, RDLogger

    RDLogger.DisableLog("rdApp.*")
    return Chem.MolFromSmiles(smiles)


@functools.lru_cache(maxsize=1024)
def _pattern(smarts: str):
    from rdkit import Chem

    pattern = Chem.MolFromSmarts(smarts)
    if pattern is None:
        raise ValueError(f"Invalid SMARTS {smarts!r}")
    return pattern


@functools.lru_cache(maxsize=16384)
def _ring_counts(smiles: str) -> tuple[int, int]:
    """``(rings, aromatic carbocycles)`` of a structure."""
    mol = _mol(smiles)
    rings = mol.GetRingInfo().AtomRings()
    aromatic = sum(
        all(
            mol.GetAtomWithIdx(i).GetIsAromatic()
            and mol.GetAtomWithIdx(i).GetSymbol() == "C"
            for i in ring
        )
        for ring in rings
    )
    return len(rings), aromatic


def _in_range(value, bounds) -> bool:
    if value is None:
        return False
    lo, hi = [*list(bounds), None, None][:2]
    return (lo is None or value >= lo) and (hi is None or value <= hi)


# ------------------------------------------------------------------ evidence
@dataclass
class PeakEvidence:
    """
    What the criteria look at for one peak.

    Attributes
    ----------
    peak : dict
        The peak table row (``peak_id``, ``rt1_min``, ``rt2_s``, ...).
    hits : list of Hit
        Library hits, best first.
    spectrum : (mz, intensity), optional
        The measured (background-corrected) apex spectrum.
    structures : list of str
        SMILES of every hit ("" when unknown).
    in_masks : set of str
        Names and ids of the masks that contain the apex.
    """

    peak: dict
    hits: list
    spectrum: tuple | None = None
    structures: list[str] = field(default_factory=list)
    in_masks: set[str] = field(default_factory=set)

    @functools.cached_property
    def relative_spectrum(self) -> dict[int, float]:
        """Measured ions in percent of the base peak."""
        if self.spectrum is None:
            return {}
        mz, inten = (np.asarray(a, dtype=np.float64) for a in self.spectrum)
        if not len(inten) or inten.max() <= 0:
            return {}
        rel = 100.0 * inten / inten.max()
        return {int(m): float(v) for m, v in zip(mz, rel, strict=True) if v > 0}


def structure_of(hit: Hit, libraries: Mapping[str, Any] | None) -> str:
    """SMILES of a hit's library entry ("" when unknown)."""
    if not libraries:
        return ""
    lib = libraries.get(hit.extra.get("library_id", "")) or libraries.get(hit.library)
    if lib is None:
        return ""
    try:
        return lib.structure_ids(hit.index)[0]
    except (IndexError, ValueError):
        return ""


# ------------------------------------------------------------------ rules
def _check_criteria(criteria: list) -> None:
    for c in criteria:
        if isinstance(c, list):
            _check_criteria(c)
            continue
        if not isinstance(c, dict) or len(c) != 1:
            raise ValueError(f"A criterion is a dict with one key, not {c!r}")
        ((key, value),) = c.items()
        if key == "any":
            if not isinstance(value, list) or not value:
                raise ValueError("'any' needs a non-empty list of criteria")
            _check_criteria([v if isinstance(v, list) else [v] for v in value])
        elif key in ("smarts", "not_smarts"):
            if rdkit_available():
                _pattern(str(value))
        elif key == "hit_name":
            re.compile(str(value))
        elif key == "formula":
            unknown = set(value) - {"only", "contains_any", "contains", "c", "dbe"}
            if unknown:
                raise ValueError(f"Unknown formula conditions: {sorted(unknown)}")
        elif key == "ions":
            for ion in value:
                if "mz" not in ion:
                    raise ValueError("Every ion condition needs 'mz'")
        elif key == "region":
            if not set(value) <= {"rt1_min", "rt2_s", "mask"}:
                raise ValueError("region takes rt1_min, rt2_s or mask")
        elif key not in (*HIT_CRITERIA, *PEAK_CRITERIA):
            raise ValueError(
                f"Unknown criterion {key!r}; use one of "
                f"{', '.join((*HIT_CRITERIA, *PEAK_CRITERIA, 'any'))}"
            )


def _structural(criteria: list) -> bool:
    """Whether the criteria (or one of their 'any' items) need a structure."""
    for c in criteria:
        if isinstance(c, list):
            if _structural(c):
                return True
            continue
        ((key, value),) = c.items()
        if key in STRUCTURE_CRITERIA:
            return True
        if key == "any" and _structural(
            [v if isinstance(v, list) else [v] for v in value]
        ):
            return True
    return False


def _without_structure(criteria: list) -> list:
    """The criteria without the structure criteria."""
    out = []
    for c in criteria:
        if isinstance(c, list):
            out.append(_without_structure(c))
            continue
        ((key, value),) = c.items()
        if key in STRUCTURE_CRITERIA:
            continue
        if key == "any":
            items = [
                _without_structure(v if isinstance(v, list) else [v]) for v in value
            ]
            items = [i for i in items if i]
            if len(items) == len(value):
                out.append({"any": items})
            continue  # an alternative that needed a structure: undecided
        out.append(c)
    return out


def _holds(criterion, ev: PeakEvidence, k: int | None, context) -> bool:
    """Whether one criterion holds for hit ``k`` (``None``: no hit) of a peak."""
    if isinstance(criterion, list):
        return all(_holds(c, ev, k, context) for c in criterion)
    ((key, value),) = criterion.items()
    if key == "any":
        return any(_holds(v, ev, k, context) for v in value)
    if key in PEAK_CRITERIA:
        return _peak_holds(key, value, ev, context)
    if k is None:
        return False
    hit = ev.hits[k]
    if key in STRUCTURE_CRITERIA:
        smiles = ev.structures[k] if k < len(ev.structures) else ""
        mol = _mol(smiles)
        if mol is None:
            return False
        if key == "smarts":
            return mol.HasSubstructMatch(_pattern(str(value)))
        if key == "not_smarts":
            return not mol.HasSubstructMatch(_pattern(str(value)))
        rings, aromatic = _ring_counts(smiles)
        return _in_range(aromatic if key == "aromatic_carbocycles" else rings, value)
    if key == "formula":
        counts = parse_formula(hit.formula)
        if not counts:
            return False
        if "only" in value and not set(counts) <= set(value["only"]):
            return False
        if "contains_any" in value and not set(counts) & set(value["contains_any"]):
            return False
        if "contains" in value and not set(value["contains"]) <= set(counts):
            return False
        if "c" in value and not _in_range(counts.get("C", 0), value["c"]):
            return False
        return "dbe" not in value or _in_range(dbe(counts), value["dbe"])
    if key == "hit_name":
        return re.search(str(value), hit.name, re.IGNORECASE) is not None
    if key == "min_match":
        return hit.match >= float(value)
    if key == "min_probability":
        return hit.probability is not None and hit.probability >= float(value)
    if key == "min_delta_match":
        return hit.delta_match is not None and hit.delta_match >= float(value)
    raise ValueError(f"Unknown criterion {key!r}")


def _peak_holds(key: str, value, ev: PeakEvidence, context) -> bool:  # noqa: ARG001
    if key == "ions":
        spectrum = ev.relative_spectrum
        for ion in value:
            v = spectrum.get(int(ion["mz"]), 0.0)
            if v < float(ion.get("min", 0.0)) or v > float(ion.get("max", 100.0)):
                return False
            if "min" not in ion and "max" not in ion and v <= 0:
                return False
        return True
    if key == "base_peak":
        spectrum = ev.relative_spectrum
        if not spectrum:
            return False
        base = max(spectrum, key=spectrum.get)
        return base in {int(m) for m in value}
    # region
    if "mask" in value:
        return str(value["mask"]) in ev.in_masks
    rt1, rt2 = float(ev.peak["rt1_min"]), float(ev.peak["rt2_s"])
    return _in_range(rt1, value.get("rt1_min", [None, None])) and _in_range(
        rt2, value.get("rt2_s", [None, None])
    )


@dataclass
class Rule:
    """
    The criteria of a class (see the module docstring).

    Attributes
    ----------
    criteria : list
        All must hold.
    fallback : list, optional
        Used for hits without a structure instead of ``criteria``; such hits
        count half in a vote.
    vote : dict, optional
        ``{"top_k", "min_match", "min_share"}``; without it the best hit
        decides.
    """

    criteria: list = field(default_factory=list)
    fallback: list | None = None
    vote: dict | None = None

    def __post_init__(self):
        if not isinstance(self.criteria, list):
            raise ValueError("The rules of a class are a list of criteria")
        _check_criteria(self.criteria)
        if self.fallback is not None:
            _check_criteria(self.fallback)
        if self.vote is not None:
            unknown = set(self.vote) - set(DEFAULT_VOTE)
            if unknown:
                raise ValueError(f"Unknown vote settings: {sorted(unknown)}")

    def hit_matches(self, ev: PeakEvidence, k: int, context=None) -> float:
        """
        Weight with which hit ``k`` supports the class: 1 (with a
        structure), 0.5 (fallback criteria) or 0.
        """
        if not self.criteria and self.fallback is None:
            return 0.0  # a label-only class (e.g. "excluded")
        has_structure = k < len(ev.structures) and _mol(ev.structures[k]) is not None
        if has_structure:
            return float(_holds(self.criteria, ev, k, context))
        if self.fallback is not None:
            return 0.5 * _holds(self.fallback, ev, k, context)
        if not _structural(self.criteria):
            return float(_holds(self.criteria, ev, k, context))
        rest = _without_structure(self.criteria)
        return float(bool(rest) and _holds(rest, ev, k, context))

    def matches(
        self,
        peak,
        hits: list,
        spectrum=None,
        libraries: Mapping[str, Any] | None = None,
        context: dict | None = None,
    ) -> float:
        """
        Whether a peak matches (spec CLS-15).

        Parameters
        ----------
        peak : dict or pandas.Series
            Peak table row.
        hits : list of Hit
            The peak's hits, best first.
        spectrum : (mz, intensity), optional
            Measured apex spectrum (for ``ions`` and ``base_peak``).
        libraries : mapping, optional
            Library id or name -> :class:`~pyGCxGC.ms.library.SpectralLibrary`,
            for the hit structures.
        context : dict, optional
            ``masks`` (MaskSet) and ``grid`` for ``region`` criteria on masks.

        Returns
        -------
        float
            The vote share, or 1/0 without a vote (0 = no match).
        """
        ev = evidence(peak, hits, spectrum, libraries, context)
        return self._score(ev, context)

    def _score(self, ev: PeakEvidence, context=None) -> float:
        if not self.criteria and self.fallback is None:
            return 0.0  # a label-only class (e.g. "excluded")
        if not ev.hits:  # only the peak criteria can hold
            return float(_holds(self.criteria, ev, None, context))
        if self.vote is None:
            return float(bool(self.hit_matches(ev, 0, context)))
        share, _ = _vote(
            ev, self.vote, lambda k: "yes" if self.hit_matches(ev, k, context) else None
        )
        return share if share >= self.vote.get("min_share", 0.6) else 0.0


def _vote(ev: PeakEvidence, vote: dict, claim: Callable[[int], str | None]):
    """
    Match-weighted vote of the top hits with at least ``min_match``.

    ``claim(k)`` names the label hit ``k`` votes for (or ``None``). Hits
    without a structure count half. Returns ``(share, label)`` of the winner.
    """
    settings = {**DEFAULT_VOTE, **(vote or {})}
    total, weights = 0.0, {}
    for k, hit in enumerate(ev.hits[: int(settings["top_k"])]):
        if hit.match < settings["min_match"]:
            continue
        has_structure = k < len(ev.structures) and _mol(ev.structures[k]) is not None
        w = float(hit.match) * (1.0 if has_structure else 0.5)
        total += w
        label = claim(k)
        if label is not None:
            weights[label] = weights.get(label, 0.0) + w
    if not total or not weights:
        return 0.0, None
    label = max(weights, key=weights.get)
    return weights[label] / total, label


def evidence(peak, hits, spectrum=None, libraries=None, context=None) -> PeakEvidence:
    """Collect the evidence of one peak (see :class:`PeakEvidence`)."""
    row = dict(peak) if not isinstance(peak, dict) else peak
    hits = list(hits or [])
    structures = [structure_of(h, libraries) for h in hits]
    in_masks: set[str] = set()
    if context and context.get("masks") is not None and context.get("grid") is not None:
        in_masks = _masks_at(row, context)
    return PeakEvidence(row, hits, spectrum, structures, in_masks)


def _masks_at(row: dict, context: dict) -> set[str]:
    """Masks (names and ids) whose raster contains the peak apex."""
    cache = context.setdefault("_rasters", {})
    masks, grid = context["masks"], context["grid"]
    j, i = grid_point(row, grid)
    out = set()
    for m in masks:
        if m.id not in cache:
            try:
                cache[m.id] = m.rasterize(grid, "lower") > 0
            except ValueError:
                cache[m.id] = None
        raster = cache[m.id]
        inside = (
            raster is not None and 0 <= j < raster.shape[0] and 0 <= i < raster.shape[1]
        )
        if inside and raster[j, i]:
            out.update((m.id, m.name))
    return out


# ------------------------------------------------------------------ classes
@dataclass
class ClassDef:
    """
    A peak class: a name, its rule, optional subclasses and a colour.

    Subclasses are ``{"name", "rules"[, "fallback"]}`` dicts, checked in order
    on the hits that support the class; an empty rule list matches always.
    """

    name: str
    rule: Rule = field(default_factory=Rule)
    description: str = ""
    color: str = ""
    subclasses: list[dict] = field(default_factory=list)
    ions: list[int] = field(default_factory=list)  # diagnostic ions (REG-5)

    def __post_init__(self):
        if not self.name or not str(self.name).strip():
            raise ValueError("A class needs a name")
        self._subrules = [
            (str(s["name"]), Rule(list(s.get("rules", [])), s.get("fallback")))
            for s in self.subclasses
        ]

    def subclass_of(self, ev: PeakEvidence, k: int, context=None) -> str:
        """Subclass of the class by hit ``k`` ("" for hits without a structure)."""
        if self._subrules and (
            k >= len(ev.structures) or _mol(ev.structures[k]) is None
        ):
            return ""
        for name, rule in self._subrules:
            if not rule.criteria and rule.fallback is None:
                return name
            if rule.hit_matches(ev, k, context):
                return name
        return ""

    def to_dict(self) -> dict:
        d: dict[str, Any] = {"name": self.name, "description": self.description}
        d["color"] = self.color
        d["rules"] = copy.deepcopy(self.rule.criteria)
        if self.rule.fallback is not None:
            d["fallback"] = copy.deepcopy(self.rule.fallback)
        if self.rule.vote is not None:
            d["vote"] = dict(self.rule.vote)
        if self.subclasses:
            d["subclasses"] = copy.deepcopy(self.subclasses)
        if self.ions:
            d["ions"] = list(self.ions)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> ClassDef:
        return cls(
            name=str(d["name"]),
            rule=Rule(list(d.get("rules", [])), d.get("fallback"), d.get("vote")),
            description=str(d.get("description", "")),
            color=str(d.get("color", "")),
            subclasses=list(d.get("subclasses", [])),
            ions=[int(m) for m in d.get("ions", [])],
        )


@dataclass
class Label:
    """A class assigned to a peak by a scheme."""

    category: str
    subclass: str
    share: float  # vote share, or 1.0 when the best hit decided
    source: str  # rule:<scheme>/<class>


class ClassScheme:
    """
    An ordered list of classes; the first matching class wins.

    Parameters
    ----------
    name : str
    classes : list of ClassDef
    vote : dict, optional
        Scheme-level vote (see the module docstring): every hit is given the
        first class it matches, and the class with most of the
        match-weighted vote wins if it has ``min_share`` of it.
    description : str
    builtin : bool
        Built-in schemes only propose labels.
    """

    FORMAT = "pygcxgc-classes"
    VERSION = 1

    def __init__(
        self,
        name: str,
        classes: list[ClassDef] | None = None,
        vote: dict | None = None,
        description: str = "",
        builtin: bool = False,
    ):
        self.name = name
        self.classes: list[ClassDef] = list(classes or [])
        self.vote = dict(vote) if vote is not None else None
        self.description = description
        self.builtin = builtin

    def __repr__(self) -> str:
        return f"ClassScheme({self.name!r}, {[c.name for c in self.classes]!r})"

    def __len__(self) -> int:
        return len(self.classes)

    def __iter__(self):
        return iter(self.classes)

    @property
    def names(self) -> list[str]:
        return [c.name for c in self.classes]

    def get(self, name: str) -> ClassDef:
        for c in self.classes:
            if c.name == name:
                return c
        raise KeyError(f"No class {name!r} in scheme {self.name!r}")

    def color(self, name: str) -> str:
        """Colour of a class ("" if unknown)."""
        for c in self.classes:
            if c.name == name:
                return c.color
        return ""

    def define(
        self,
        name: str,
        rules: list,
        description: str = "",
        color: str | None = None,
        fallback: list | None = None,
        vote: dict | None = None,
        subclasses: list[dict] | None = None,
        position: int | None = None,
    ) -> ClassDef:
        """Add a class, or replace the class of the same name (keeping its place)."""
        old = next((c for c in self.classes if c.name == name), None)
        cls = ClassDef(
            name,
            Rule(list(rules), fallback, vote),
            description,
            color if color is not None else (old.color if old else ""),
            list(subclasses or []),
        )
        if old is not None:
            self.classes[self.classes.index(old)] = cls
        elif position is None:
            self.classes.append(cls)
        else:
            self.classes.insert(max(0, position), cls)
        return cls

    def remove(self, name: str) -> None:
        self.classes.remove(self.get(name))

    def copy(self) -> ClassScheme:
        return ClassScheme.from_dict(self.to_dict())

    # -------------------------------------------------------------- labelling
    def classify(self, ev: PeakEvidence, context=None) -> Label | None:
        """The label of a peak, or ``None``."""
        if self.vote is not None and ev.hits:
            return self._classify_vote(ev, self.vote, context)
        for cls in self.classes:
            score = cls.rule._score(ev, context)
            if not score:
                continue
            k = self._supporting_hit(cls, ev, context)
            sub = cls.subclass_of(ev, k, context) if k is not None else ""
            return Label(cls.name, sub, round(float(score), 3), self.source(cls.name))
        return None

    def _supporting_hit(self, cls: ClassDef, ev: PeakEvidence, context):
        for k in range(min(len(ev.hits), 5)):
            if cls.rule.hit_matches(ev, k, context):
                return k
        return None

    def _classify_vote(self, ev: PeakEvidence, vote: dict, context) -> Label | None:
        claims: dict[int, tuple[str, str]] = {}

        def claim(k: int) -> str | None:
            for cls in self.classes:
                if cls.rule.hit_matches(ev, k, context):
                    claims[k] = (cls.name, cls.subclass_of(ev, k, context))
                    return cls.name
            return None

        settings = {**DEFAULT_VOTE, **vote}
        share, name = _vote(ev, settings, claim)
        if name is None or share < settings["min_share"]:
            return None
        subs: dict[str, float] = {}
        for k, (c, sub) in claims.items():
            if c == name:
                subs[sub] = subs.get(sub, 0.0) + float(ev.hits[k].match)
        sub = max(subs, key=subs.get) if subs else ""
        return Label(name, sub, round(share, 3), self.source(name))

    def source(self, class_name: str) -> str:
        return f"rule:{self.name}/{class_name}"

    # -------------------------------------------------------------- files
    def to_dict(self) -> dict:
        d: dict[str, Any] = {
            "format": self.FORMAT,
            "version": self.VERSION,
            "name": self.name,
            "description": self.description,
        }
        if self.vote is not None:
            d["vote"] = dict(self.vote)
        d["classes"] = [c.to_dict() for c in self.classes]
        return d

    @classmethod
    def from_dict(cls, d: dict, builtin: bool = False) -> ClassScheme:
        if d.get("format", cls.FORMAT) != cls.FORMAT:
            raise ValueError("Not a pyGCxGC class scheme")
        return cls(
            str(d.get("name", "project")),
            [ClassDef.from_dict(c) for c in d.get("classes", [])],
            d.get("vote"),
            str(d.get("description", "")),
            builtin,
        )

    def save(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=1), "utf-8")
        return path

    @classmethod
    def load(cls, path: str | Path) -> ClassScheme:
        return cls.from_dict(json.loads(Path(path).read_text("utf-8")))

    @classmethod
    def builtin(cls, name: str = "hydrocarbon-group-type") -> ClassScheme:
        """A built-in scheme (spec CLS-13); it only proposes labels."""
        if name not in BUILTIN_SCHEMES:
            raise KeyError(f"No built-in scheme {name!r}; use {BUILTIN_SCHEMES}")
        return cls.from_dict(_builtin_dict(name), builtin=True)


@functools.cache
def _builtin_dict(name: str) -> dict:
    return json.loads((SCHEME_DIR / f"{name}.json").read_text("utf-8"))


# ------------------------------------------------------------------ tables
def _hits_of(hits, peak_id):
    if isinstance(hits, dict):
        return hits.get(int(peak_id)) or hits.get(peak_id) or []
    return []


def classify_peaks(
    table: pd.DataFrame,
    hits: Mapping[int, list],
    scheme: ClassScheme,
    spectra: Mapping[int, tuple] | Callable[[dict], tuple] | None = None,
    libraries: Mapping[str, Any] | None = None,
    context: dict | None = None,
) -> dict[int, Label | None]:
    """
    Label every peak with a scheme (nothing is changed).

    Parameters
    ----------
    table : pandas.DataFrame
        Peak table (``peak_id``, ``rt1_min``, ``rt2_s``, ...).
    hits : dict
        ``peak_id`` -> hits, best first.
    scheme : ClassScheme
    spectra : dict or callable, optional
        ``peak_id`` -> ``(mz, intensity)``, or a function of the row.
    libraries : mapping, optional
        For the hit structures (see :meth:`Rule.matches`).
    context : dict, optional
        ``masks`` and ``grid`` for mask regions.

    Returns
    -------
    dict
        ``peak_id`` -> :class:`Label` or ``None``.
    """
    context = dict(context or {})
    out: dict[int, Label | None] = {}
    for row in table.to_dict("records"):
        pid = int(row["peak_id"])
        spectrum = None
        if callable(spectra):
            spectrum = spectra(row)
        elif spectra is not None:
            spectrum = spectra.get(pid)
        ev = evidence(row, _hits_of(hits, pid), spectrum, libraries, context)
        out[pid] = scheme.classify(ev, context)
    return out


def propose(
    table: pd.DataFrame,
    hits: Mapping[int, list],
    scheme: ClassScheme | None = None,
    top_k: int = 5,
    min_match: float = 700,
    min_share: float = 0.6,
    **kwargs,
) -> dict[int, Label | None]:
    """
    Proposals of a scheme (default: ``hydrocarbon-group-type``) with a hit vote.

    ``kwargs`` go to :func:`classify_peaks`.
    """
    scheme = (scheme or ClassScheme.builtin()).copy()
    scheme.vote = {"top_k": top_k, "min_match": min_match, "min_share": min_share}
    return classify_peaks(table, hits, scheme, **kwargs)


def apply_scheme(
    table: pd.DataFrame,
    hits: Mapping[int, list],
    scheme: ClassScheme,
    only_unreviewed: bool = True,
    overwrite_user: bool = False,
    reviewed: bool | None = None,
    **kwargs,
) -> tuple[pd.DataFrame, dict]:
    """
    Label the peaks of a table with a scheme (first matching class wins).

    User labels are kept unless ``overwrite_user``; with ``only_unreviewed``
    reviewed labels are kept too. Peaks that match no class keep their label.

    Parameters
    ----------
    reviewed : bool, optional
        Mark the new labels reviewed. Defaults to ``True`` for schemes that
        are not built in (their rules are someone's judgement) and ``False``
        for built-in schemes (proposals).
    **kwargs
        See :func:`classify_peaks`.

    Returns
    -------
    table : pandas.DataFrame
        A copy with the new labels.
    report : dict
        ``labelled`` (class -> number of peaks), ``unchanged`` (no class
        matched), ``kept`` (reviewed or user labels left alone) and
        ``conflicts`` (user labels that differ from the rule).
    """
    table = add_label_columns(table.copy())
    if reviewed is None:
        reviewed = not scheme.builtin
    labels = classify_peaks(table, hits, scheme, **kwargs)
    report: dict[str, Any] = {
        "labelled": {},
        "unchanged": 0,
        "kept": 0,
        "conflicts": [],
    }
    for idx, row in table.iterrows():
        label = labels.get(int(row["peak_id"]))
        if label is None:
            report["unchanged"] += 1
            continue
        is_user = row["category_source"] == "user"
        if is_user and not overwrite_user:
            if row["category"] != label.category:
                report["conflicts"].append(
                    {
                        "peak_id": int(row["peak_id"]),
                        "user": row["category"],
                        "rule": label.category,
                    }
                )
            report["kept"] += 1
            continue
        if only_unreviewed and bool(row["reviewed"]) and not is_user:
            report["kept"] += 1
            continue
        table.loc[idx, "category"] = label.category
        table.loc[idx, "subclass"] = label.subclass
        table.loc[idx, "category_source"] = label.source
        table.loc[idx, "category_reason"] = ""
        table.loc[idx, "category_confidence"] = label.share
        table.loc[idx, "reviewed"] = bool(reviewed)
        report["labelled"][label.category] = (
            report["labelled"].get(label.category, 0) + 1
        )
    return table, report


# ------------------------------------------------------------------ evidence cards
def spectrum_summary(spectrum, top: int = 8) -> dict:
    """
    Base peak, molecular-ion candidate (highest m/z above 5 %) and the
    ``top`` largest ions in percent of the base peak.
    """
    rel = (
        spectrum.relative_spectrum
        if isinstance(spectrum, PeakEvidence)
        else PeakEvidence({}, [], spectrum).relative_spectrum
    )
    if not rel:
        return {"base": None, "M": None, "ions": []}
    base = max(rel, key=rel.get)
    above = [m for m, v in rel.items() if v >= 5.0]
    largest = sorted(rel.items(), key=lambda kv: -kv[1])[:top]
    return {
        "base": int(base),
        "M": int(max(above)) if above else None,
        "ions": [[int(m), round(v)] for m, v in largest],
    }


def review_cards(
    data,
    table: pd.DataFrame,
    hits: Mapping[int, list],
    spectra: Mapping[int, tuple] | Callable[[dict], tuple] | None = None,
    n_hits: int = 3,
    near: Callable[[dict], str | None] | None = None,
) -> list[dict]:
    """
    Compact evidence cards for reviewing peaks (spec CLS-4, about 80 tokens each).

    Parameters
    ----------
    data : GCxGC_MS or None
        For the apex spectra when ``spectra`` is not given.
    table : pandas.DataFrame
        The peaks to describe (a subset of the peak table). Volumes are in
        percent of the volume of ``total_volume`` in ``table.attrs`` if set,
        else of ``table``.
    hits : dict
        ``peak_id`` -> hits.
    spectra : dict or callable, optional
        Apex spectra; default: ``data.spectrum(scan, 3)`` with the image
        baseline.
    n_hits : int, default 3
    near : callable, optional
        ``near(row)`` -> ``"in"``, ``"near"`` or ``None`` for the mask under
        review.

    Returns
    -------
    list of dict
        Keys: ``id``, ``rt1`` (min), ``rt2`` (s), ``vol%``, ``h`` (height),
        ``sn`` (S/N), ``hits`` (``[name, formula, match, reverse match,
        probability]``, hit metadata only), ``dm`` (the top hit's
        ``delta_match``), ``base``, ``M`` (molecular-ion candidate), ``ions``
        (``[m/z, %]``), ``label`` (``[class, subclass, source, reviewed]``),
        ``prop`` (``[class, subclass, share]`` of the built-in proposal) and
        ``mask`` (``"in"``/``"near"``).
    """
    total = float(table.attrs.get("total_volume") or table["volume"].sum() or 1.0)
    cards = []
    for row in table.to_dict("records"):
        pid = int(row["peak_id"])
        if callable(spectra):
            spectrum = spectra(row)
        elif spectra is not None:
            spectrum = spectra.get(pid)
        elif data is not None:
            s = data.spectrum(
                int(row["scan"]), n_average=3, baseline=data.baseline.image_model
            )
            spectrum = (s.mz, s.intensity)
        else:
            spectrum = None
        peak_hits = _hits_of(hits, pid)
        card: dict[str, Any] = {
            "id": pid,
            "rt1": round(float(row["rt1_min"]), 3),
            "rt2": round(float(row["rt2_s"]), 2),
            "vol%": round(100.0 * float(row["volume"]) / total, 2),
            "h": float(f"{float(row['height']):.3g}"),
        }
        if "snr" in row and row["snr"] == row["snr"]:
            card["sn"] = round(float(row["snr"]))
        card["hits"] = [
            [
                h.name,
                h.formula,
                round(h.match),
                round(h.reverse_match),
                None if h.probability is None else round(h.probability),
            ]
            for h in peak_hits[:n_hits]
        ]
        if peak_hits and peak_hits[0].delta_match is not None:
            card["dm"] = round(peak_hits[0].delta_match)
        card.update(spectrum_summary(spectrum))
        if row.get("category"):
            card["label"] = [
                row["category"],
                row.get("subclass") or "",
                row.get("category_source") or "",
                bool(row.get("reviewed")),
            ]
        if row.get("proposal"):
            card["prop"] = [
                row["proposal"],
                row.get("proposal_subclass") or "",
                round(float(row.get("proposal_share") or 0), 2),
            ]
        where = near(row) if near is not None else None
        if where:
            card["mask"] = where
        cards.append(card)
    return cards


def scheme_colors(scheme: ClassScheme | None = None) -> dict[str, str]:
    """Class name -> colour (the built-in scheme by default)."""
    scheme = scheme or ClassScheme.builtin()
    return {c.name: c.color for c in scheme if c.color}


def grid_point(row: Mapping, grid: Grid) -> tuple[int, int]:
    """``(rt2_index, mod_index)`` of a peak row on a grid."""
    from pyGCxGC.regions.from_peaks import peak_pixels

    rows, cols = peak_pixels(pd.DataFrame([dict(row)]), grid)
    return int(rows[0]), int(cols[0])


__all__ = [
    "BUILTIN_SCHEMES",
    "LABEL_COLUMNS",
    "ClassDef",
    "ClassScheme",
    "Label",
    "PeakEvidence",
    "Rule",
    "add_label_columns",
    "apply_scheme",
    "classify_peaks",
    "dbe",
    "evidence",
    "parse_formula",
    "propose",
    "review_cards",
    "scheme_colors",
    "spectrum_summary",
]
