# Research: match probability of library hits

A match factor is a similarity, not a confidence: two isomers with
near-identical spectra can both score 900. pyGCxGC therefore gives every hit

* `delta_match`: its match minus the best match of a *different* compound in
  the hit list (for the top hit: its lead over the nearest competitor), and
* `probability` (percent): the probability that the hit is the right
  compound, **assuming the compound is in the searched libraries**. It is a
  softmax over the distinct compounds among the top 20 hits,
  `p_c ∝ exp(match_c / T)`, like NIST MS Search's "Prob %" (Stein, *J. Am.
  Soc. Mass Spectrom.* 1994, 5, 316).

NIST's thresholds and calibration do not carry over, because pyGCxGC weights
peaks with `(m/z)^0.5 · I^0.5` (see `pyGCxGC.ms.search`), so `T` is our own
fit. The code is `annotate_hits` in `src/pyGCxGC/ms/search.py`; the fitted
parameters are in `src/pyGCxGC/ms/match_probability.json`.

## Compound identity

Hits are the same compound when their CAS numbers are equal (replicate
spectra), or, without equal CAS numbers, when both have InChIKeys and they are
equal. Equal InChIKeys with *different* CAS numbers are treated as different
compounds: the InChIKeys of NIST entries come from the `struct.db` drawings,
which carry no stereo, so cis/trans isomers would otherwise merge. Without
identifiers the normalised names decide. Every replicate hit shows its
compound's probability, so the probabilities of the distinct compounds sum to
100 %. Hits beyond the top 20 get no probability.

## Calibration (2026-11-12)

`scripts/calibrate_match_probability.py`, default settings:

* 3000 random NIST 20 main library spectra (306,869 entries, at least five
  peaks), restricted to the acquisition range m/z 45-500.
* Distortions, as GCxGC-qMS records spectra: a mass-dependent skew
  `(m/z / 100)^s` with `s ∈ [-0.5, 0.5]`, log-normal intensity noise
  (σ = 0.15), 30 % of the ions below 5 % dropped, a co-eluting library
  spectrum at 5-30 % in 30 % of the queries, a Poisson(3) number of
  background ions at 1-8 %, additive noise of 1 % of the base peak.
* Searched against the full library with the default search settings
  (`mz_power = int_power = 0.5`, 200 prescreen candidates, 20 hits).
* `T` maximises the likelihood of the true compound among the distinct
  compounds of the top 20 hits (2962 of 3000 queries had it there).

Result: **T = 11.6** (match-factor units). Top-1 accuracy 91.7 %, expected
calibration error 1.7 %. On MoNA (18,558 spectra, 300 queries) the same
procedure gives T ≈ 20 with 93 % top-1 accuracy; one calibration (NIST) is
used for all libraries for now.

Reliability of the top hit's probability (NIST, 3000 queries):

| Predicted (top hit) | Queries | Mean predicted | Observed accuracy |
|---|---|---|---|
| 0.0-0.1 | 2 | 0.090 | 0.000 |
| 0.1-0.2 | 20 | 0.172 | 0.250 |
| 0.2-0.3 | 40 | 0.259 | 0.475 |
| 0.3-0.4 | 52 | 0.345 | 0.423 |
| 0.4-0.5 | 70 | 0.450 | 0.500 |
| 0.5-0.6 | 90 | 0.545 | 0.622 |
| 0.6-0.7 | 87 | 0.656 | 0.644 |
| 0.7-0.8 | 114 | 0.749 | 0.798 |
| 0.8-0.9 | 161 | 0.854 | 0.857 |
| 0.9-1.0 | 2364 | 0.993 | 0.985 |

Above 0.6 the probability is well calibrated; below it, it is
under-confident (a top hit given 25 % is right about 45 % of the time),
because a single temperature cannot describe both clearly separated and
crowded hit lists. A second parameter (e.g. a temperature that depends on
the number of competitors) could fix that if it matters in practice.

## Settings

The probability is computed only for the weighting it was calibrated for
(`(m/z)^0.5 · I^0.5`); the mass range may differ. With other exponents it is
`None`, and `probability_unavailable` says why (shown as
`probability_note` on selections and peak tables).

## Open

* Distorted library spectra are not real peaks (co-elution and background
  are only simulated). Check the reliability on peaks of known identity, the
  n-alkane and alkylbenzene series of PY_010 and Clean PP_016.
* Per-library calibration, if MoNA's reliability differs clearly from
  NIST's.
