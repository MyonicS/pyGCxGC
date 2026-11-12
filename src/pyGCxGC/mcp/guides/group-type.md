# Group-type workflow: what fraction of the FID chromatogram is a class?

Two runs of the same sample, measured with the same column set, oven
programme and modulation time: a GCxGC-MS run identifies and classifies the
peaks, and a GCxGC-FID run gives the area percentages. The mask of a class is
drawn around its MS peaks and copied to the FID run.

The same steps give a quick (*exploratory*) estimate and a *reportable*
number. Only the grade differs, and it is computed by `integrate`, never set
by you. Decide early which one the user needs, and say which one you
delivered.

## Steps and what to check

1. **Open both runs.** `open_file(MS)`, `open_file(FID, modulation_s=...)`.
   `describe_run` both. Check: the MS modulation estimate agrees with the
   setting (warning above 1 %), the RT1 ranges of the two runs agree, the
   mass range of the MS (the search uses it), and the warnings.
2. **Solvent cut-off.** Set it on both runs with `set_processing(...,
   solvent_cutoff_min=...)`. `describe_run` suggests a value when the run
   starts with a dominant solvent peak; look at a `render` of the first
   minutes before accepting it. If there is no solvent peak, set 0 (checked,
   none needed). A reportable result needs it set.
3. **Delay.** `estimate_delay(source=MS, target=FID)`, then
   `set_processing(MS, shift_scans=<suggested>)` **before** detecting peaks
   (shifting afterwards makes the peak table stale). Run `estimate_delay`
   again: `aligned` must be true. Read its warnings (drift, low correlation,
   different modulation times).
4. **Libraries.** `session_info`: a library must be active, else
   `add_library`.
5. **Overview.** `render(MS)`. Find the alkane line, the aromatic bands and
   the bleed.
6. **Peaks.** `detect_peaks(MS, snr=10)`. It refuses to throw away reviewed
   labels; never pass `discard_labels=true` without asking the user.
7. **Proposals.** `propose_classes(MS)` labels peaks from a vote of their top
   hits. Proposals are not reviewed and do not count.
8. **Review.** `review_peaks(MS, order="rt")` batch by batch, then
   `set_categories` for each batch: the class, a confidence and, where you
   disagree with the proposal, a one-sentence reason. Judge each peak by its
   hits *and* its spectrum (base peak, molecular ion, diagnostic ions, see
   `gcxgc://guides/classes`) and its position. Doubtful peaks:
   `peak_hits(peak_id)`, `spectrum(peak_id=...)`, a zoomed `render`.
   `unknown` is a valid label. For a quick estimate the largest peaks may be
   enough; for a reportable number step 11 decides what else needs review.
9. **Rules.** Turn repeated judgements into rules (`define_class`, which
   reports how many peaks a rule matches before you apply it), then
   `apply_rules`. Keep individual labels for the rest. Rules carry over to
   the next sample; individual judgements do not. `review_status` shows the
   share of the volume reviewed and per source.
10. **Look.** `render(MS, color_peaks_by="category")`. The class should form a
    coherent band or cluster; isolated points far from it are suspicious.
11. **Mask.** `mask_from_peaks(MS, category=...)`, method `band` for classes
    that run across RT1 (monoaromatics, alkanes), `hull` for a cluster. Read
    the report: coverage (class volume inside), purity (other classes
    inside), intruders, unreviewed peaks in the neighbourhood, wrap-around.
    Review the unreviewed neighbours (`review_peaks(near_mask=...)`). Refine:
    smaller margins, `add_shape(mode="subtract")` around an intruder, or
    `undo_masks` and a new call. Render again until the mask fits.
12. **Optional check.** `set_signal(MS, "eic", [91, 105, 119])` and
    `integrate(MS)`: how much of the diagnostic ion signal the mask holds.
    Switch back to the TIC afterwards.
13. **Copy.** `copy_masks(source=MS, target=FID)`. Read the compatibility
    report: modulation times, remaining delay (must be under half a pixel),
    parts of masks outside the FID run.
14. **Check on the FID.** `render(FID, overlays=["masks"])`. If column bleed
    or a solvent tail lies inside the integrated area, draw a mask of
    category `excluded` around it (`create_mask`, `add_shape`).
15. **Integrate.** `integrate(FID)`: volume, percent of the total and of the
    total without excluded masks, and the grade with its failed criteria.
16. **Save.** `export(FID, "integration")`, `export(FID, "mask_set")`,
    `save_project("<name>")`.
17. **Report** as in `gcxgc://guides/report-template`.

## Reportable: the criteria

* Every peak inside a class mask or within its margin is reviewed (agent or
  user labels, or rules you wrote).
* The delay between the runs was estimated and corrected (less than half a
  pixel remains), and the runs were not re-processed since.
* No open conflicts with user labels.
* Copied masks were not changed after the copy.
* The signal and the libraries (with their thresholds) are known, and the
  peak table is current.
* The integrated run has an explicit solvent cut-off.

## Pitfalls

* The user may edit labels and masks in the GC² window at the same time.
  Results carry `revision` and `masks_revision`; when they jump, look again.
  User labels win: conflicts are reported, not overwritten.
* A wrong isomer with the right class is common for alkylbenzenes; the class
  vote is robust to it. A right class with a poor match is common for small
  peaks; judge them by their ions.
* Peaks wrapped around in RT2 (very high RT2 appearing at low RT2) break
  bands. The phase shift is the fix, not a bigger mask.
* Percentages are area percentages of the detector signal, not mass
  percentages.
