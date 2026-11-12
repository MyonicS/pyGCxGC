# Peak classes, masks from peaks and run alignment

`pyGCxGC.regions` answers questions such as *"What fraction of the FID
chromatogram is monoaromatics?"* from a GCxGC-MS run and a GCxGC-FID run of
the same sample: classify the MS peaks, draw a mask around a class, check it,
move it to the FID run and integrate. Everything is ordinary pyGCxGC data
(peak tables, `MaskSet` polygons), so it can be opened in the app, saved in a
project and re-run.

The same steps are `Session` methods (`pyGCxGC.app.session`), which the app
and the agent interface use; the examples below use both.

## Solvent cut-off

GCxGC-MS runs have a solvent cut-off too. The background-corrected signal up
to it is zeroed before folding, so the TIC, EICs, renders, integration and
peak detection ignore the solvent:

```python
import pyGCxGC as gcgc
from pyGCxGC.regions import suggest_solvent_cutoff

ms = gcgc.read_gcxgc_ms("sample_MS.cdf", modulation_time=20)
suggest_solvent_cutoff(ms)  # minutes, or None without a dominant solvent peak
ms = gcgc.read_gcxgc_ms("sample_MS.cdf", modulation_time=20, solvent_cutoff=2.5)
```

In documents of the app a cut-off can be *not set* (`None`, the default) or
set; `0` means "checked, none needed". A reportable result needs it set.

## Aligning the MS run with the FID run

The MS usually sees every compound a little later than the FID (the
transfer line), so peaks have a higher RT2. Estimate the delay and correct it
with the MS phase shift **before** detecting peaks:

```python
from pyGCxGC.regions import estimate_delay

fid = gcgc.parse_2D_chromatogram("sample_FID.gcd", modulation_time=20)
est = estimate_delay(ms, fid)
est.delay_s, est.suggested_shift, est.warnings  # e.g. 0.53 s, +5 scans
ms.set_modulation(shift=est.suggested_shift)
estimate_delay(ms, fid).aligned  # True: less than half a pixel remains
```

The estimate cross-correlates the 1D signals in windows of 30 modulations and
warns about drift, poor correlation and differing modulation times. RT1 is
not corrected: both runs must use the same column set, oven programme and
modulation time.

## Match probability

Every library hit has `delta_match` (its lead over the best *different*
compound) and `probability`, the percent probability that it is the right
compound **assuming the compound is in the searched libraries** (a softmax
over the distinct compounds of the top 20 hits, calibrated for the default
peak weighting):

```python
hits = gcgc.search_spectrum(spectrum.mz, spectrum.intensity, library)
[(h.name, round(h.match), h.delta_match, h.probability) for h in hits[:3]]
```

With other weighting exponents the probability is `None`.

## Classifying peaks

A class is a list of criteria on the library hits (structure SMARTS, formula,
carbon number, double-bond equivalents, hit names, match quality) and on the
peak (ions of the measured spectrum, base peak, RT ranges or a mask). The
built-in `hydrocarbon-group-type` scheme proposes hydrocarbon group types
from a vote of the top 5 hits:

```python
from pyGCxGC.ms.peaks import detect_peaks, identify_peaks
from pyGCxGC.regions import ClassScheme, propose

table, hits = identify_peaks(ms, detect_peaks(ms, snr=10), library)
labels = propose(table, hits, libraries={library.name: library})
labels[12]  # Label(category='monoaromatics', subclass='alkylbenzenes', share=0.9, ...)
```

Own classes are written in the same rule language and checked in order (the
first matching class wins):

```python
scheme = ClassScheme("project")
scheme.define(
    "alkylbenzenes",
    [{"formula": {"only": ["C", "H"], "dbe": [4, 4]}}, {"base_peak": [91, 105]}],
)
scheme.save("study.classes.json")
```

Structure criteria need RDKit (`pip install "pyGCxGC[app]"`).

In a `Session`, labels live in the peak table (`category`, `subclass`,
`category_source`, `category_reason`, `category_confidence`, `reviewed`) and
are saved in projects; the project's classes are saved next to it as
`<project>.classes.json`:

```python
from pyGCxGC.app.session import Session

session = Session()
ms_id = session.wait(session.open_document("sample_MS.cdf", modulation_time=20))[
    "document_id"
]
session.wait(session.add_library("MSSEARCH/MAINLIB"))
session.wait(session.detect_peaks(ms_id))
session.wait(session.propose_classes(ms_id))  # unreviewed proposals
batch = session.review_batch(ms_id)  # evidence cards, 40 at a time
session.set_categories(ms_id, [{"peak_id": 12, "category": "monoaromatics"}])
session.define_class("alkylbenzenes", [{"base_peak": [91, 105]}])
session.apply_rules(ms_id)
session.review_status(ms_id)
```

Labels set from Python or the app are *user* labels; rules and agents never
change them unless asked to. `detect_peaks` refuses to throw reviewed labels
away (peak ids change); `reidentify_peaks` keeps them.

## Masks from peaks

`mask_from_peaks` draws a polygon around the peaks of a class: a `band` that
follows the class across RT1 (e.g. monoaromatics between the n-alkanes and
the diaromatics) or the convex `hull` of a cluster, widened by margins (by
default half the median peak width in RT2, at least 2 pixels, and 2
modulations in RT1). `mask_report` checks it against the labelled table:

```python
from pyGCxGC.regions import mask_from_peaks, mask_report

mono = table[table["category"] == "monoaromatics"]
mask = mask_from_peaks(mono, ms, method="band", name="Monoaromatics")
report = mask_report(mask, table, ms)
report["coverage"], report["purity"], report["intruders"], report["warnings"]
```

In a session, `session.mask_from_peaks(ms_id, category="monoaromatics")`
creates the mask as one undoable edit and returns the report.

## Moving the mask to the FID run and the grade

```python
session.import_masks(fid_id, from_document=ms_id)  # remembers the copy
session.mask_compatibility(ms_id, fid_id)  # modulation, delay, ranges, pixels
result = session.integration_report(fid_id)
result["masks"][0]["percent"], result["masks"][0]["percent_of_included"]
result["grade"], result["failed"]
```

The grade is *reportable* when every peak in or near a class mask is
reviewed, the delay between the runs was corrected, no user label is
contradicted, the copied masks were not changed, the signal and libraries
are known and the solvent cut-off is set; otherwise it is *exploratory* with
the failed criteria. Percentages are area percentages of the detector
signal, not mass percentages. `percent_of_included` leaves out masks of
category `excluded` (column bleed, a solvent tail).

## Renders

`pyGCxGC.render.render_chromatogram` (or `session.render`) draws a
chromatogram with its masks and peaks as a PNG, with readable RT axes and a
caption that says what was drawn:

```python
from pyGCxGC.render import render_chromatogram

png, caption = render_chromatogram(ms, masks=masks, peaks=table, peak_labels="name")
Path("overview.png").write_bytes(png)
print(caption["text"])
```
