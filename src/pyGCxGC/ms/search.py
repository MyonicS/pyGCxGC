"""
Library search for EI mass spectra.

The scores follow the NIST MS Search "identity" algorithm described by
Stein & Scott (J. Am. Soc. Mass Spectrom. 1994, 5, 859-866):

* peaks are weighted as ``w = (m/z) ** mz_power * intensity ** int_power``;
* the dot product term is ``F_D = (sum w_u w_l)^2 / (sum w_u^2 * sum w_l^2)``;
* the ratio term ``F_R`` compares the intensity ratios of neighbouring peaks
  that are present in both spectra;
* the composite match factor is
  ``MF = 1000 * (N_u F_D + N_ul F_R) / (N_u + N_ul)``.

The reverse match factor (RMF) is computed in the same way but ignores all
peaks of the unknown that are absent from the library spectrum, which makes it
robust against co-eluting compounds and background ions.

All library spectra are first compared with a vectorised (sparse matrix)
cosine prescreen; the full composite scores are only computed for the best
candidates.

Match probability
-----------------
A match factor is a similarity, not a confidence: two isomers with
near-identical spectra can both score 900. Every hit therefore also gets
(:func:`annotate_hits`, spec MPR-1…6):

* ``delta_match``: its match minus the best match of a *different* compound
  in the hit list (for the top hit: its lead over the nearest competitor);
* ``probability`` (percent): the probability that the hit is the right
  compound, **assuming the compound is in the searched libraries**. It is a
  softmax over the distinct compounds among the top 20 hits,
  ``p_c ∝ exp(match_c / T)``, as NIST MS Search's "Prob %" (Stein, J. Am. Soc.
  Mass Spectrom. 1994, 5, 316). The temperature ``T`` is fitted by
  ``scripts/calibrate_match_probability.py`` for the default peak weighting
  and stored in ``match_probability.json``; with other weighting exponents
  the probability is ``None`` (:func:`probability_unavailable` says why).
"""

from __future__ import annotations

import functools
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from pyGCxGC.ms.library import SpectralLibrary, _to_nominal, _weights


@dataclass
class SearchParameters:
    """
    Settings for :func:`search_spectrum`.

    Attributes
    ----------
    mz_min, mz_max : int or None
        Mass range used for matching. Library peaks outside the range are
        ignored, which is important when the acquisition started above m/z 40.
        ``None`` uses the range of the unknown spectrum.
    mz_power, int_power : float
        Peak weighting exponents. Stein & Scott used 3 and 0.6, but on
        GCxGC-qMS spectra (spectral skew, noise, co-elution) a weak mass
        weighting identified e.g. the n-alkane series correctly where 3 failed,
        and recovered 97 % vs 75 % of simulated distorted NIST spectra.
    n_hits : int
        Number of hits returned.
    n_candidates : int
        Number of prescreen candidates for which the full scores are computed.
    min_relative_intensity : float
        Peaks of the unknown below this fraction of its base peak are removed.
    sort_by : str
        ``"match"`` (forward MF) or ``"reverse"`` (RMF).
    """

    mz_min: int | None = None
    mz_max: int | None = None
    mz_power: float = 0.5
    int_power: float = 0.5
    n_hits: int = 10
    n_candidates: int = 200
    min_relative_intensity: float = 0.0
    sort_by: str = "match"


@dataclass
class Hit:
    """A library search hit."""

    rank: int
    index: int
    name: str
    formula: str
    cas: str
    mw: int
    nist_no: int
    db_no: int
    match: float
    reverse_match: float
    library: str = ""
    extra: dict = field(default_factory=dict)
    inchikey: str = ""  # compound identity (spec MPR-2), not in as_dict
    delta_match: float | None = None
    probability: float | None = None  # percent; None without a calibration

    def as_dict(self) -> dict:
        """Hit as a flat dictionary (for tables)."""
        return {
            "rank": self.rank,
            "name": self.name,
            "match": round(self.match),
            "reverse_match": round(self.reverse_match),
            "formula": self.formula,
            "mw": self.mw,
            "cas": self.cas,
            "nist_no": self.nist_no,
            "db_no": self.db_no,
            "library": self.library,
            "delta_match": None
            if self.delta_match is None
            else round(self.delta_match),
            "probability": None
            if self.probability is None
            else round(self.probability, 1),
        }


def composite_scores(
    mz_u, int_u, mz_l, int_l, mz_power=3.0, int_power=0.6
) -> tuple[float, float]:
    """
    Forward and reverse composite match factors (0-1000) of two spectra.

    Both spectra must be nominal mass, restricted to the same mass range.

    Returns
    -------
    match, reverse_match : float
    """
    mz_u = np.asarray(mz_u, dtype=np.int64)
    mz_l = np.asarray(mz_l, dtype=np.int64)
    wu_all = _weights(mz_u, int_u, mz_power, int_power)
    wl_all = _weights(mz_l, int_l, mz_power, int_power)
    keep_u, keep_l = wu_all > 0, wl_all > 0
    mz_u, wu_all = mz_u[keep_u], wu_all[keep_u]
    mz_l, wl_all = mz_l[keep_l], wl_all[keep_l]
    n_u, n_l = len(mz_u), len(mz_l)
    if n_u == 0 or n_l == 0:
        return 0.0, 0.0
    common, iu, il = np.intersect1d(mz_u, mz_l, assume_unique=True, return_indices=True)
    n_ul = len(common)
    if n_ul == 0:
        return 0.0, 0.0
    wu, wl = wu_all[iu], wl_all[il]
    cross = float(np.dot(wu, wl))
    su_all = float(np.dot(wu_all, wu_all))
    sl_all = float(np.dot(wl_all, wl_all))
    su_common = float(np.dot(wu, wu))
    f_d = cross**2 / (su_all * sl_all)
    f_d_rev = cross**2 / (su_common * sl_all)
    if n_ul > 1:
        # mass weights cancel in the ratio, only the intensity powers remain
        ratio = (wl[1:] / wl[:-1]) * (wu[:-1] / wu[1:])
        f_r = float(np.minimum(ratio, 1.0 / ratio).mean())
    else:
        f_r = 0.0
    match = 1000.0 * (n_u * f_d + n_ul * f_r) / (n_u + n_ul)
    reverse = 1000.0 * (n_l * f_d_rev + n_ul * f_r) / (n_l + n_ul)
    return match, reverse


def prepare_unknown(mz, intensity, params: SearchParameters):
    """Nominal-mass, range-restricted and thresholded unknown spectrum."""
    mz_n, int_n = _to_nominal(mz, intensity)
    mz_n = mz_n.astype(np.int64)
    if len(mz_n) == 0:
        return mz_n, int_n.astype(np.float64), 0, 0
    lo = params.mz_min if params.mz_min is not None else int(mz_n.min())
    hi = params.mz_max if params.mz_max is not None else int(mz_n.max())
    keep = (mz_n >= lo) & (mz_n <= hi)
    mz_n, int_n = mz_n[keep], int_n[keep].astype(np.float64)
    if len(int_n) and params.min_relative_intensity > 0:
        keep = int_n >= params.min_relative_intensity * int_n.max()
        mz_n, int_n = mz_n[keep], int_n[keep]
    if len(int_n):
        int_n = int_n / int_n.max() * 999.0
    return mz_n, int_n, lo, hi


def search_spectrum(
    mz,
    intensity,
    library: SpectralLibrary,
    params: SearchParameters | None = None,
    **kwargs,
) -> list[Hit]:
    """
    Search a spectrum against a library.

    Parameters
    ----------
    mz, intensity : array-like
        The unknown spectrum.
    library : SpectralLibrary
        Reference library.
    params : SearchParameters, optional
        Search settings; keyword arguments override individual fields.

    Returns
    -------
    list of Hit
        Best hits, sorted by ``params.sort_by``.
    """
    return search_spectra([(mz, intensity)], library, params, **kwargs)[0]


def search_spectra(
    spectra,
    library: SpectralLibrary,
    params: SearchParameters | None = None,
    progress=None,
    **kwargs,
) -> list[list[Hit]]:
    """
    Search several spectra against a library (batched prescreen).

    Parameters
    ----------
    spectra : list of (mz, intensity)
        The unknown spectra.
    library : SpectralLibrary
        Reference library.
    params : SearchParameters, optional
        Search settings; keyword arguments override individual fields.
    progress : callable, optional
        Called as ``progress(fraction)``.

    Returns
    -------
    list of list of Hit
    """
    params = params or SearchParameters()
    for key, value in kwargs.items():
        setattr(params, key, value)
    prepared = [prepare_unknown(mz, inten, params) for mz, inten in spectra]
    valid = [p for p in prepared if len(p[0])]
    if not valid or len(library) == 0:
        return [[] for _ in spectra]
    lo = min(p[2] for p in valid)
    hi = max(p[3] for p in valid)
    W, P, _, _ = library.search_matrix(lo, hi, params.mz_power, params.int_power)

    results: list[list[Hit]] = []
    batch = 64
    for start in range(0, len(prepared), batch):
        chunk = prepared[start : start + batch]
        U = np.zeros((W.shape[1], len(chunk)), dtype=np.float32)
        for j, (mz_u, int_u, _, _) in enumerate(chunk):
            if len(mz_u):
                U[mz_u, j] = _weights(mz_u, int_u, params.mz_power, params.int_power)
        dots = np.asarray(W @ U)  # sum(w_u * w_l) / |w_l|
        u_norm = np.sqrt((U.astype(np.float64) ** 2).sum(axis=0))
        restricted = np.sqrt(np.asarray(P @ (U**2)))  # |w_u| over library peaks
        for j, (mz_u, int_u, _, _) in enumerate(chunk):
            if len(mz_u) == 0 or u_norm[j] == 0:
                results.append([])
                continue
            fwd = dots[:, j] / u_norm[j]
            with np.errstate(divide="ignore", invalid="ignore"):
                rev = np.where(restricted[:, j] > 0, dots[:, j] / restricted[:, j], 0.0)
            # reverse cosine is weak evidence when only one or two peaks overlap
            rev = rev * np.minimum(1.0, fwd / 0.3)
            cand = _top_k(fwd, params.n_candidates)
            cand = np.union1d(cand, _top_k(rev, params.n_candidates // 2))
            results.append(_score_candidates(mz_u, int_u, cand, library, params))
        if progress is not None:
            progress(min(1.0, (start + batch) / len(prepared)))
    return results


def _score_candidates(mz_u, int_u, cand, library: SpectralLibrary, params) -> list[Hit]:
    lo, hi = int(mz_u.min()), int(mz_u.max())
    lo = params.mz_min if params.mz_min is not None else lo
    hi = params.mz_max if params.mz_max is not None else hi
    scored = []
    for idx in cand:
        a, b = library.offsets[idx], library.offsets[idx + 1]
        mz_l = library.mz[a:b].astype(np.int64)
        int_l = library.intensity[a:b]
        keep = (mz_l >= lo) & (mz_l <= hi)
        mf, rmf = composite_scores(
            mz_u, int_u, mz_l[keep], int_l[keep], params.mz_power, params.int_power
        )
        scored.append((mf, rmf, int(idx)))
    key = 1 if params.sort_by == "reverse" else 0
    scored.sort(key=lambda s: (s[key], s[1 - key]), reverse=True)
    hits = []
    # the probability needs the top N_COMPETITORS hits, whatever n_hits is
    keep = max(params.n_hits, N_COMPETITORS)
    for rank, (mf, rmf, idx) in enumerate(scored[:keep], start=1):
        hits.append(
            Hit(
                rank=rank,
                index=idx,
                name=str(library.compound_name[idx]),
                formula=str(library.formula[idx]),
                cas=str(library.cas[idx]),
                mw=int(library.mw[idx]),
                nist_no=int(library.nist_no[idx]),
                db_no=int(library.db_no[idx]),
                match=mf,
                reverse_match=rmf,
                library=library.name,
                inchikey=library.structure_ids(idx)[1],
            )
        )
    annotate_hits(hits, params.mz_power, params.int_power)
    return hits[: params.n_hits]


def _top_k(values: np.ndarray, k: int) -> np.ndarray:
    k = min(k, len(values))
    if k <= 0:
        return np.zeros(0, dtype=np.int64)
    idx = np.argpartition(values, -k)[-k:]
    return idx[values[idx] > 0]


# ------------------------------------------------------------ probability
N_COMPETITORS = 20  # hits that compete for the probability (spec MPR-3)
CALIBRATION_FILE = Path(__file__).with_name("match_probability.json")
_CAS_RE = re.compile(r"^0*(\d+)-(\d\d)-(\d)$")


@functools.cache
def match_calibration() -> dict | None:
    """
    The fitted parameters of the match probability (``match_probability.json``).

    Returns ``None`` when the file is missing. Keys: ``temperature``,
    ``mz_power``, ``int_power`` and the provenance of the fit (library,
    search settings, date).
    """
    try:
        return json.loads(CALIBRATION_FILE.read_text("utf-8"))
    except (OSError, ValueError):
        return None


def probability_unavailable(mz_power: float, int_power: float) -> str | None:
    """
    Why no probability can be computed for these weighting exponents, or
    ``None`` when it can (spec MPR-5).
    """
    cal = match_calibration()
    if cal is None:
        return "The match probability is not calibrated (match_probability.json)"
    if (float(mz_power), float(int_power)) != (cal["mz_power"], cal["int_power"]):
        return (
            f"The match probability is calibrated for the weighting "
            f"(m/z)^{cal['mz_power']:g}·I^{cal['int_power']:g} only; the search "
            f"uses (m/z)^{mz_power:g}·I^{int_power:g}"
        )
    return None


def _cas_key(cas: str) -> str:
    m = _CAS_RE.match(str(cas).strip())
    return "-".join(m.groups()) if m and m.group(1) != "0" else ""


def _name_key(name: str) -> str:
    return " ".join(str(name).lower().split())


def same_compound(a: Hit, b: Hit) -> bool:
    """
    Whether two hits are the same compound (spec MPR-2).

    Equal CAS numbers mean the same compound (replicate spectra). Otherwise
    InChIKeys decide when both hits have one, but equal keys with different
    CAS numbers are stereoisomers (NIST structures carry no stereo), which
    stay separate. Without identifiers, the normalised names decide.
    """
    cas_a, cas_b = _cas_key(a.cas), _cas_key(b.cas)
    if cas_a and cas_a == cas_b:
        return True
    if a.inchikey and b.inchikey:
        return a.inchikey == b.inchikey and not (cas_a and cas_b)
    if cas_a and cas_b:
        return False
    return _name_key(a.name) == _name_key(b.name) != ""


def compound_groups(hits: list[Hit]) -> list[int]:
    """Group number of every hit; hits of the same compound share it."""
    group = list(range(len(hits)))

    def root(i: int) -> int:
        while group[i] != i:
            group[i] = group[group[i]]
            i = group[i]
        return i

    for i in range(len(hits)):
        for j in range(i):
            if root(i) != root(j) and same_compound(hits[i], hits[j]):
                group[root(i)] = root(j)
    return [root(i) for i in range(len(hits))]


def compound_probabilities(matches, temperature: float) -> np.ndarray:
    """Softmax of the best match per compound (percent), ``p ∝ exp(m / T)``."""
    m = np.asarray(matches, dtype=np.float64)
    if not len(m):
        return m
    z = np.exp((m - m.max()) / temperature)
    return 100.0 * z / z.sum()


def annotate_hits(hits: list[Hit], mz_power: float = 0.5, int_power: float = 0.5):
    """
    Set ``delta_match`` and ``probability`` of a hit list (in place).

    The list must be sorted by match and hold the top hits of the search
    (at least :data:`N_COMPETITORS` when there are that many), before it is
    cut to the number of hits shown. Every hit of a compound (replicate
    spectra) gets the compound's probability, so the probabilities of the
    distinct compounds sum to 100 %.

    Returns
    -------
    list of Hit
        ``hits``.
    """
    top = hits[:N_COMPETITORS]
    groups = compound_groups(top)
    best: dict[int, float] = {}
    for g, h in zip(groups, top, strict=True):
        best[g] = max(best.get(g, -np.inf), float(h.match))
    for g, h in zip(groups, top, strict=True):
        others = [m for k, m in best.items() if k != g]
        h.delta_match = float(h.match) - (max(others) if others else 0.0)
    cal = match_calibration()
    reason = probability_unavailable(mz_power, int_power)
    if reason is None and cal is not None:
        keys = list(best)
        p = compound_probabilities([best[k] for k in keys], cal["temperature"])
        prob = dict(zip(keys, p.tolist(), strict=True))
        for g, h in zip(groups, top, strict=True):
            h.probability = prob[g]
    else:
        for h in top:
            h.probability = None
    for h in hits[N_COMPETITORS:]:  # outside the competition
        h.delta_match = None
        h.probability = None
    return hits
