"""
Result grade: "exploratory" or "reportable" (spec GRD-1…3).

The grade of an integration is computed from the data, never set by hand,
so a quick estimate and a number for a report come from the same tools and
differ only in their grade. A result is **reportable** when every criterion
holds; otherwise it is **exploratory**, with the list of failed criteria:

1. ``neighbourhood_reviewed``: every peak of the source MS run inside a class
   mask or within its margin is reviewed (by an agent or the user);
2. ``delay_corrected``: for masks copied from another run, the delay between
   the runs was estimated and what remains is under half a pixel of the
   coarser run;
3. ``no_conflicts``: no open conflicts with user labels;
4. ``copied_masks_unchanged``: the copied masks were not changed after the
   copy (the changes are listed);
5. ``stated_inputs``: the integration signal and the libraries used for the
   peaks (with their thresholds) are known, and the peak table is current;
6. ``solvent_cutoff_set``: the integrated run has an explicit solvent
   cut-off (0 counts).

:func:`grade` works on plain facts (:class:`MaskFacts`, :class:`RunFacts`),
which :class:`pyGCxGC.app.session.Session` collects.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

CRITERIA = {
    "neighbourhood_reviewed": "Every peak in or near a class mask is reviewed",
    "delay_corrected": "The RT2 delay between the runs was estimated and corrected",
    "no_conflicts": "No open conflicts with user labels",
    "copied_masks_unchanged": "Copied masks were not changed after the copy",
    "stated_inputs": "The signal, the libraries and the thresholds are known",
    "solvent_cutoff_set": "The solvent cut-off of the integrated run is set",
}
EXCLUDED = "excluded"


@dataclass
class MaskFacts:
    """
    What the grade needs to know about one mask.

    Attributes
    ----------
    name, category : str
    peaks_document : str or None
        The MS run whose peaks define the mask's neighbourhood (``None``: no
        peak table behind the mask).
    n_near, n_unreviewed : int
        Peaks inside the mask or within its margin, and how many of them are
        not reviewed.
    drawn_without_peaks : bool
        The mask was drawn by hand (spec REG-8).
    copied_from : str or None
        The run the mask was copied from.
    delay : dict or None
        For copied masks: the last delay estimate between the runs
        (``aligned``, ``rt2_offset_s``, ``current``), ``None`` if never
        estimated.
    changes : list of str
        Changes since the copy.
    n_peaks, libraries, thresholds
        The peaks of the class inside the mask and how they were identified.
    sources : dict
        Class volume inside the mask per label source.
    low_confidence : list of dict
        ``agent`` labels with low confidence inside the mask.
    """

    name: str
    category: str | None
    peaks_document: str | None = None
    n_near: int = 0
    n_unreviewed: int = 0
    drawn_without_peaks: bool = False
    copied_from: str | None = None
    delay: dict | None = None
    changes: list[str] = field(default_factory=list)
    n_peaks: int = 0
    libraries: list[str] = field(default_factory=list)
    thresholds: dict = field(default_factory=dict)
    sources: dict = field(default_factory=dict)
    low_confidence: list[dict] = field(default_factory=list)
    peak_table_current: bool = True
    libraries_known: bool = True

    @property
    def is_class_mask(self) -> bool:
        return self.category != EXCLUDED


@dataclass
class RunFacts:
    """The integrated run: name, signal, solvent cut-off and open conflicts."""

    name: str
    kind: str
    signal: str
    solvent_cutoff: float | None
    n_conflicts: int = 0


def grade(run: RunFacts, masks: list[MaskFacts]) -> dict[str, Any]:
    """
    Grade an integration (spec GRD-1).

    Returns
    -------
    dict
        ``grade`` ("reportable" or "exploratory"), ``failed`` (criteria with
        a message), ``criteria`` (every criterion with ``ok``) and ``masks``
        (the facts per mask, for the report).
    """
    failed: dict[str, list[str]] = {k: [] for k in CRITERIA}
    class_masks = [m for m in masks if m.is_class_mask]
    if not class_masks:
        failed["neighbourhood_reviewed"].append("There are no class masks")
    for m in class_masks:
        if m.peaks_document is None:
            failed["neighbourhood_reviewed"].append(
                f"{m.name}: no peak table behind the mask (detect and review peaks "
                "on the MS run, then copy the mask)"
            )
        elif m.n_unreviewed:
            failed["neighbourhood_reviewed"].append(
                f"{m.name}: {m.n_unreviewed} of {m.n_near} peaks in or near the mask "
                f"on {m.peaks_document} are not reviewed"
            )
        if m.copied_from is not None:
            d = m.delay
            if d is None:
                failed["delay_corrected"].append(
                    f"{m.name}: the delay between {m.copied_from} and {run.name} was "
                    "never estimated"
                )
            elif not d.get("current", True):
                failed["delay_corrected"].append(
                    f"{m.name}: the runs were re-processed after the last delay "
                    "estimate; estimate it again"
                )
            elif not d.get("aligned"):
                failed["delay_corrected"].append(
                    f"{m.name}: {d.get('rt2_offset_s')} s of RT2 offset remain between "
                    f"{m.copied_from} and {run.name}"
                )
            if m.changes:
                failed["copied_masks_unchanged"].append(
                    f"{m.name}: changed after the copy ({'; '.join(m.changes)})"
                )
        if m.peaks_document is not None:
            if not m.peak_table_current:
                failed["stated_inputs"].append(
                    f"{m.name}: the peak table of {m.peaks_document} is stale "
                    "(re-identify or detect again)"
                )
            if not m.libraries_known:
                failed["stated_inputs"].append(
                    f"{m.name}: a library used for the peaks of {m.peaks_document} "
                    "is not loaded"
                )
    if not run.signal:
        failed["stated_inputs"].append("The integration signal is unknown")
    if run.n_conflicts:
        failed["no_conflicts"].append(
            f"{run.n_conflicts} open conflicts with user labels"
        )
    if run.solvent_cutoff is None:
        failed["solvent_cutoff_set"].append(
            f"The solvent cut-off of {run.name} is not set (0 = checked, none needed)"
        )
    failures = [
        {"criterion": k, "description": CRITERIA[k], "messages": v}
        for k, v in failed.items()
        if v
    ]
    return {
        "grade": "exploratory" if failures else "reportable",
        "failed": failures,
        "criteria": [
            {"criterion": k, "description": d, "ok": not failed[k]}
            for k, d in CRITERIA.items()
        ],
        "masks": [asdict(m) for m in masks],
    }


__all__ = ["CRITERIA", "MaskFacts", "RunFacts", "grade"]
