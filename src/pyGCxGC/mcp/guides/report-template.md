# Report template for a group-type result

Report what `integrate` and `review_status` returned; do not round away the
denominators or the grade. Use this structure:

## Result

* **<class>: X.XX % of the total FID chromatogram** (area percent of the
  detector signal, not mass percent), and Y.YY % of the total without the
  excluded masks (name them).
* **Grade: reportable** or **exploratory**. For exploratory, list every
  failed criterion with its message, and what would fix it.

## How it was obtained

* Runs: the FID and MS files, their modulation times, phase shifts and
  solvent cut-offs (state "checked, none" for 0).
* Alignment: the estimated RT2 delay (s and scans), the shift applied to the
  MS run, the remaining offset, and the assumptions: same column set, oven
  programme and modulation time (RT1 agrees within a modulation), and a
  constant RT2 delay.
* Signal integrated (FID; for MS checks the TIC or the EIC m/z values).
* Peaks: the peak S/N, the libraries searched, the proposal thresholds (top
  k, minimum match, minimum share), the number of peaks inside each mask.
* Review: peaks and volume share reviewed, per label source (rules you wrote,
  your individual labels, the user's), and your low-confidence labels inside
  the mask by name. Mention conflicts with user labels.
* Masks: method (band or hull) and margins, coverage and purity, whether a
  mask was drawn by hand without peaks, changes made after copying.

## Files

The files written to the output folder: integration CSV, mask set JSON (and
TIFFs if exported), the project (`.gcxgc`) and its classes file
(`.classes.json`) with the rules you defined.

## Caveats

Anything you noticed that a reader should know: drift of the delay, a poor
modulation estimate, wrap-around, column bleed, peaks of the class you could
not identify, a class boundary you were unsure about.
