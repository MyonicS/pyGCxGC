"""
Regions of GCxGC chromatograms: peak classes, masks from peaks, alignment
of runs and the grade of results.

* :mod:`~pyGCxGC.regions.classes`: peak labels, the rule language, class
  schemes (the built-in ``hydrocarbon-group-type``) and evidence cards;
* :mod:`~pyGCxGC.regions.from_peaks`: band and hull masks around peak sets
  and their report (coverage, purity, intruders, unreviewed neighbours);
* :mod:`~pyGCxGC.regions.alignment`: the RT2 delay between two runs and the
  solvent cut-off suggestion;
* :mod:`~pyGCxGC.regions.grade`: "exploratory" or "reportable".

Rendering is in :mod:`pyGCxGC.render`.
"""

from __future__ import annotations

from pyGCxGC.regions.alignment import (
    DelayEstimate,
    estimate_delay,
    suggest_solvent_cutoff,
)
from pyGCxGC.regions.classes import (
    ClassDef,
    ClassScheme,
    Label,
    Rule,
    apply_scheme,
    classify_peaks,
    propose,
    review_cards,
)
from pyGCxGC.regions.from_peaks import mask_from_peaks, mask_report
from pyGCxGC.regions.grade import grade

__all__ = [
    "ClassDef",
    "ClassScheme",
    "DelayEstimate",
    "Label",
    "Rule",
    "apply_scheme",
    "classify_peaks",
    "estimate_delay",
    "grade",
    "mask_from_peaks",
    "mask_report",
    "propose",
    "review_cards",
    "suggest_solvent_cutoff",
]
