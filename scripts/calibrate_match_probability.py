"""
Calibrate the match probability of pyGCxGC's library search (spec MPR-4).

Library spectra are distorted the way GCxGC-qMS spectra are (intensity
noise, a mass-dependent intensity skew, dropped small ions, added background
ions and, sometimes, a co-eluting compound), restricted to the acquisition
mass range and searched against the whole library with the default search
settings. The temperature ``T`` of ``p_c ∝ exp(match_c / T)`` (a softmax over
the distinct compounds among the top 20 hits) is fitted by maximum
likelihood.

Only aggregate numbers leave this script: the fitted parameters go to
``src/pyGCxGC/ms/match_probability.json`` and the reliability table
(predicted against observed accuracy of the top hit in 10 bins) is printed as
Markdown for ``docs/research/match-probability.md``. No spectra, names or
library numbers are written or printed, so the script can run on a licensed
NIST library.

Usage::

    python scripts/calibrate_match_probability.py            # NIST if present, else MoNA
    python scripts/calibrate_match_probability.py --library path/to/lib.msp -n 3000
"""

# ruff: noqa: T201  (a command-line script: it prints its results)
from __future__ import annotations

import argparse
import itertools
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from scipy.optimize import minimize_scalar
from scipy.special import logsumexp

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pyGCxGC.ms.library import load_library  # noqa: E402
from pyGCxGC.ms.search import (  # noqa: E402
    CALIBRATION_FILE,
    N_COMPETITORS,
    Hit,
    SearchParameters,
    compound_groups,
    same_compound,
    search_spectra,
)

NIST = ROOT / "local_data" / "MSSEARCH" / "MAINLIB"
MONA = (ROOT / "local_data" / "libraries" / "mona_gcms.zip",)


def distort(mz, inten, lib, rng, mz_min, mz_max):
    """A library spectrum as a GCxGC-qMS run might record it."""
    mz = mz.astype(np.float64)
    inten = inten.astype(np.float64) / inten.max()
    keep = (mz >= mz_min) & (mz <= mz_max)
    mz, inten = mz[keep], inten[keep]
    if not len(mz):
        return None
    inten = inten * (mz / 100.0) ** rng.uniform(-0.5, 0.5)  # spectral skew
    inten = inten * rng.lognormal(0.0, 0.15, len(inten))  # intensity noise
    small = inten < 0.05 * inten.max()
    inten[small & (rng.random(len(inten)) < 0.3)] = 0.0  # lost small ions
    spectrum = dict(zip(mz.astype(int).tolist(), inten.tolist(), strict=True))
    if rng.random() < 0.3:  # a co-eluting compound
        j = int(rng.integers(len(lib)))
        a, b = lib.offsets[j], lib.offsets[j + 1]
        other = lib.intensity[a:b].astype(np.float64)
        scale = rng.uniform(0.05, 0.3) * max(spectrum.values()) / max(other.max(), 1e-9)
        for m, v in zip(lib.mz[a:b].astype(int).tolist(), other.tolist(), strict=True):
            if mz_min <= m <= mz_max:
                spectrum[m] = spectrum.get(m, 0.0) + v * scale
    base = max(spectrum.values())
    for m in rng.integers(mz_min, mz_max + 1, rng.poisson(3)):  # background ions
        spectrum[int(m)] = spectrum.get(int(m), 0.0) + rng.uniform(0.01, 0.08) * base
    noise = 0.01 * base * rng.standard_normal(len(spectrum))
    out_mz = np.array(list(spectrum), dtype=np.float64)
    out_int = np.clip(np.array(list(spectrum.values())) + noise, 0, None)
    return out_mz, out_int


def query_identity(lib, index: int) -> Hit:
    """The query's own compound, as a hit (for :func:`same_compound`)."""
    hit = Hit(
        0, index, str(lib.compound_name[index]), "", str(lib.cas[index]), 0, 0, 0, 0, 0
    )
    hit.inchikey = lib.structure_ids(index)[1]
    return hit


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--library", type=Path, help="Library (default NIST, else MoNA)"
    )
    parser.add_argument("-n", type=int, default=2000, help="Number of queries")
    parser.add_argument("--seed", type=int, default=20261112)
    parser.add_argument("--mz-min", type=int, default=45)
    parser.add_argument("--mz-max", type=int, default=500)
    parser.add_argument("--out", type=Path, default=CALIBRATION_FILE)
    parser.add_argument("--dry-run", action="store_true", help="Do not write the JSON")
    args = parser.parse_args()

    path = args.library
    if path is None:
        path = NIST if NIST.exists() else next((p for p in MONA if p.exists()), None)
    if path is None:
        sys.exit("No library: pass --library (NIST MAINLIB folder or an MSP file)")
    t0 = time.monotonic()
    lib = load_library(path)
    print(
        f"Library: {lib.name} ({lib.origin}), {len(lib)} spectra, "
        f"loaded in {time.monotonic() - t0:.0f} s",
        file=sys.stderr,
    )

    rng = np.random.default_rng(args.seed)
    n_in_range = np.diff(lib.offsets)
    candidates = np.flatnonzero(n_in_range >= 5)
    queries, spectra = [], []
    while len(queries) < args.n:
        i = int(rng.choice(candidates))
        a, b = lib.offsets[i], lib.offsets[i + 1]
        s = distort(lib.mz[a:b], lib.intensity[a:b], lib, rng, args.mz_min, args.mz_max)
        if s is None or np.count_nonzero(s[1]) < 3:
            continue
        queries.append(i)
        spectra.append(s)

    params = SearchParameters(
        mz_min=args.mz_min, mz_max=args.mz_max, n_hits=N_COMPETITORS
    )
    t0 = time.monotonic()
    results = search_spectra(spectra, lib, params)
    print(
        f"Searched {len(spectra)} spectra in {time.monotonic() - t0:.0f} s",
        file=sys.stderr,
    )

    fit_rows = []  # (matches per compound, index of the true compound)
    top_rows = []  # (best match per compound, top compound is right)
    for index, hits in zip(queries, results, strict=True):
        if not hits:
            continue
        truth = query_identity(lib, index)
        groups = compound_groups(hits)
        best: dict[int, float] = {}
        for g, h in zip(groups, hits, strict=True):
            best[g] = max(best.get(g, -1.0), float(h.match))
        keys = list(best)
        true_group = next(
            (g for g, h in zip(groups, hits, strict=True) if same_compound(h, truth)),
            None,
        )
        matches = np.array([best[k] for k in keys])
        top_rows.append((matches, keys.index(groups[0]), true_group == groups[0]))
        if true_group is not None:
            fit_rows.append((matches, keys.index(true_group)))

    def nll(log_t: float) -> float:
        t = np.exp(log_t)
        return -sum(m[k] / t - logsumexp(m / t) for m, k in fit_rows)

    best_fit = minimize_scalar(
        nll, bounds=(np.log(1.0), np.log(1000.0)), method="bounded"
    )
    temperature = float(np.exp(best_fit.x))

    def prob_top(m, k):
        z = np.exp((m - m.max()) / temperature)
        return float(z[k] / z.sum())

    predicted = np.array([prob_top(m, k) for m, k, _ in top_rows])
    correct = np.array([ok for _, _, ok in top_rows], dtype=float)
    edges = np.linspace(0, 1, 11)
    table = []
    for lo, hi in itertools.pairwise(edges):
        sel = (predicted >= lo) & ((predicted < hi) if hi < 1 else (predicted <= hi))
        if sel.any():
            table.append(
                (lo, hi, int(sel.sum()), predicted[sel].mean(), correct[sel].mean())
            )
    ece = sum(n * abs(p - o) for _, _, n, p, o in table) / max(len(predicted), 1)

    calibration = {
        "temperature": round(temperature, 3),
        "mz_power": params.mz_power,
        "int_power": params.int_power,
        "n_competitors": N_COMPETITORS,
        "library": f"{lib.name} ({lib.origin}, {len(lib)} spectra)",
        "search": {
            "mz_min": args.mz_min,
            "mz_max": args.mz_max,
            "n_candidates": params.n_candidates,
        },
        "queries": len(top_rows),
        "queries_with_true_compound_in_top20": len(fit_rows),
        "top1_accuracy": round(float(correct.mean()), 4),
        "expected_calibration_error": round(float(ece), 4),
        "seed": args.seed,
        "date": datetime.now(timezone.utc).date().isoformat(),
        "script": "scripts/calibrate_match_probability.py",
    }
    print(json.dumps(calibration, indent=1))
    print("\n| Predicted (top hit) | Queries | Mean predicted | Observed accuracy |")
    print("|---|---|---|---|")
    for lo, hi, n, p, o in table:
        print(f"| {lo:.1f}-{hi:.1f} | {n} | {p:.3f} | {o:.3f} |")
    if not args.dry_run:
        args.out.write_text(json.dumps(calibration, indent=1) + "\n", "utf-8")
        print(f"\nWrote {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
