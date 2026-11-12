# Compound classes in GCxGC and how to recognise them

On a non-polar × mid-polar column set (the usual set-up), RT1 follows the
boiling point and RT2 the polarity. Within one modulation, classes appear
from bottom to top roughly as:

| RT2 position | Classes |
|---|---|
| lowest | n-alkanes and isoalkanes (a thin line), alkenes just above |
| low | cycloalkanes (naphthenes), above the alkane line |
| middle | monoaromatics: a band above the cycloalkanes |
| higher | diaromatics (naphthalenes, biphenyls), then polyaromatics |
| varies | oxygenates (phenols high), sulphur and nitrogen compounds |

Members of a homologous series lie on a smooth curve: each added CH2 moves
the peak right (RT1) and slightly down (RT2). A peak far off the curve of
its class is probably misclassified, or wrapped around in RT2.

## Diagnostic ions (EI, 70 eV)

| Class | Base peak / ions | Molecular ion |
|---|---|---|
| n-alkanes, isoalkanes | 43, 57, 71, 85 (CnH2n+1), smooth decay | weak; isoalkanes weaker, with strong ions at the branch |
| alkenes, cycloalkanes | 41, 55, 69, 83 (CnH2n-1) | visible, CnH2n |
| alkylbenzenes | 91 (tropylium), 105, 119, 133; 92 for long chains | clear: 92, 106, 120, 134, ... (CnH2n-6) |
| alkenylbenzenes (styrenes) | 104, 117, 118 | strong, CnH2n-8 |
| indanes, tetralins | 117, 118, 131, 132 | strong, CnH2n-8 |
| naphthalenes | 128, 141, 142, 155, 156 | very strong (128, 142, 156, ...) |
| biphenyls, acenaphthenes | 152, 153, 154, 168 | very strong |
| phenols | 94, 107, 108, 121, 122 | strong |
| alkylthiophenes | 97, 111, 125 | strong |
| siloxanes (bleed) | 73, 147, 207, 221, 281, 355 | none |

A molecular ion candidate (highest m/z above 5 % of the base peak) and the
spacing of the series often decide the class when the hits disagree.

## When to trust what

* **Hits.** Use the class vote of the top hits, not only the best hit. A
  match above 800 with a clear lead (`delta_match` > 20 or a probability
  above 50 %) is usually right about the compound; isomers of alkylbenzenes
  are often wrong while the class is right.
* **Spectrum.** For poor matches (below 700) judge from the ions above. A
  co-eluting peak mixes two spectra: the reverse match stays high while the
  forward match drops.
* **Position.** A peak in the middle of a coherent band of one class, with a
  compatible spectrum, belongs to it even with poor hits.
* **EIC.** When peaks are small or overlap, render an EIC of the diagnostic
  ions (`render(signal="eic", mz=[91, 105, 119])` for alkylbenzenes,
  `[128, 142, 156]` for naphthalenes, `[57, 71, 85]` for alkanes) and look
  where the signal lies, instead of trusting the peak list.

## Rules

`define_class` takes criteria that must all hold, for example:

* `{"formula": {"only": ["C", "H"], "dbe": [4, 4], "c": [7, 12]}}`
* `{"base_peak": [91, 105, 119]}`, `{"ions": [{"mz": 91, "min": 30}]}`
* `{"aromatic_carbocycles": [1, 1]}`, `{"smarts": "c1ccccc1C=C"}`,
  `{"not_smarts": "[O,N,S]"}`, `{"rings": [1, null]}`
* `{"hit_name": "benzene"}` (a case-insensitive regular expression)
* `{"min_match": 750}`, `{"min_probability": 30}`, `{"min_delta_match": 10}`
* `{"region": {"rt1_min": [10, 40], "rt2_s": [2.0, 3.5]}}` or
  `{"region": {"mask": "Monoaromatics"}}`
* `{"any": [{...}, [{...}, {...}]]}`: one of the alternatives holds

Add `"vote": {"top_k": 5, "min_match": 700, "min_share": 0.6}` to judge by
the top hits instead of the best one. Test a rule with the match counts that
`define_class` returns before you `apply_rules`. Classes are checked in order
and the first matching class wins.
