# pyGCxGC Studio (app)

pyGCxGC Studio is the new graphical application for GCxGC data. It shows the
2D chromatogram with GPU rendering, draws and integrates masks on FID and
GCxGC-MS data, and identifies GCxGC-MS spectra and peaks with spectral
libraries. It replaces the Tk mask creator and MS viewer. All calculations
run in the pyGCxGC Python library, so everything the app does can also be
done in Python with the same results.

## Starting the app

The app needs the `app` extra:

```bash
pip install "pyGCxGC[app]"
pygcxgc --open
```

(`python -m pyGCxGC.app` is the same command.)

`--open` opens the app in the web browser. Without it, the command prints the
address (it contains an access token). Files can be passed directly:

```bash
pygcxgc --open -m 20 run.csv
pygcxgc --open -l path/to/MSSEARCH/MAINLIB run.cdf
```

`-m` sets the modulation time in seconds for all files given. FID files need
one; for GCxGC-MS files it is estimated when omitted. `-l` loads a spectral
library. The server only accepts connections from the same computer.

`pygcxgc-maskcreator` and `pygcxgc-msviewer` are deprecated: they now start
the app. Add `--legacy` to run the old Tk tools while they still exist.

## Opening files and the workspace

*Open* (or `Ctrl+O`) shows a file browser for OpenChrom CSV (FID or MS),
ANDI-MS `.cdf` and JCAMP-DX files. For FID files it asks for the modulation
time and can remember it for the next files. Several files can be open at
once; they appear as tabs in the top bar.

The workspace consists of panels that can be resized, moved and stacked as
tabs by dragging. The layout is remembered separately for FID and MS
documents. *Menu ▸ Reset layout* restores the default, and *Menu ▸ Panels*
reopens a closed panel.

`Ctrl+K` opens the command palette, which lists all commands and their
shortcuts.

## Chromatogram view

| Action | How |
| --- | --- |
| Zoom | Mouse wheel (about the cursor); over an axis it zooms only that axis |
| Zoom to a region | Zoom box tool (`Z`), drag a rectangle |
| Pan | Drag with the select (`V`) or pan (`H`) tool, or with the middle button |
| Reset the zoom | Double click, or `0` |
| Read values | The status bar shows RT1, RT2, intensity, scan and pixel under the cursor |

*Display* sets the colour map (the matplotlib maps, optionally reversed), the
scaling (linear, square root, cube root or log), the contrast and the
interpolation (none, bilinear, bicubic, as in matplotlib). The contrast
histogram has draggable limits; *Auto* sets them to 0 and the 99.9th
percentile of the visible data, and *Lock* keeps them when switching between
documents. Display settings never change the data or the integration.

## Processing (FID)

The *Processing* panel holds the parameters of
`pyGCxGC.parse_2D_chromatogram`: modulation time, sampling interval, phase
shift, solvent cutoff, baseline and normalisation. *Apply* reprocesses the
data. Dragging the phase-shift slider previews the shift immediately and
applies it on release.

## Masks

Masks select regions of the chromatogram to integrate, for example compound
classes. A mask is a list of shapes that are added or subtracted, stored in
retention-time units, so a mask set can be applied to any chromatogram. The
pixels are computed by pyGCxGC (`pyGCxGC.MaskSet`): a pixel belongs to a shape
when its centre lies inside it.

### Drawing

| Tool | Key | Use |
| --- | --- | --- |
| Rectangle | `R` | Drag |
| Lasso | `L` | Drag freehand |
| Polygon | `P` | Click the vertices; double click, `Enter` or a click on the first vertex closes it; `Backspace` removes the last vertex, `Esc` cancels |

New shapes go into the *active* mask, shown next to the tools (choose another
one there or click a mask in the *Masks* panel). Without masks, the first
shape creates one. Hold `Alt` while drawing, or switch on *Subtract*, to cut a
shape out of the mask.

To edit a shape, click it with the select tool (or click it in the shape list
of the *Masks* panel). Drag its handles to move vertices, drag inside it to
move it, and press `Delete` to remove it.

`Ctrl+Z` undoes and `Ctrl+Shift+Z` redoes any mask change.

### The Masks panel

The panel lists the masks with their colour (click to change it), visibility,
name (double click to rename), number of pixels, volume and share of the total
volume. The toolbar creates, duplicates, deletes and reorders masks (masks can
also be reordered by dragging). Below the list are the shapes of the active
mask and its optional *category* (for example "monoaromatics").

*Import* loads

* a folder of mask TIFFs, as written by the old mask creator or
  `MaskSet.to_tiffs`;
* a single TIFF or a mask set (`.gcxgc-masks.json`);
* the masks of another open document. Shapes are placed by retention time, so
  they transfer between chromatograms with different modulation or sampling.
  Imported TIFFs (raster masks) only apply to chromatograms of the same shape.

*Export* writes the mask set (`.gcxgc-masks.json`), the masks as TIFF files
(compatible with `integrate_masks`) or the integration results as CSV.

Masks also work on GCxGC-MS documents, where they integrate the TIC or the
current extracted ion chromatogram.

### The Integration panel

The *Integration* panel shows, for every mask, the volume of the processed
signal under it (integrated exactly like `pyGCxGC.integrate_masks`), its share
of the total and its number of pixels, plus the *unassigned* rest and the
total. It updates as you draw. Overlapping masks are allowed and each mask is
integrated on its own; the panel warns about overlaps and how many pixels the
masks share.

* **Export CSV**: one row with the file, the processing parameters and one
  column per mask.
* **Batch**: integrate the same masks on other files with the same
  processing parameters; the CSV has one row per file.
* **Copy as Python**: copies code that reproduces the processing and the
  integration with the pyGCxGC API, using the exported mask set:

```python
import pyGCxGC as gcgc

chrom = gcgc.parse_2D_chromatogram(
    r"/data/Example_FID.csv",
    modulation_time=20,
    sampling_interval="infer",
    shift=0,
    solvent_cutoff=0,
    baseline_type="stridewise",
    normalize="volume",
)
masks = gcgc.MaskSet.load("Example_FID.gcxgc-masks.json")
volumes = masks.integrate(chrom)  # {mask name: volume}
table = masks.integration(chrom)  # volume, percent and pixels, plus "unassigned"
```

See [Mask sets](../masks.md) for working with mask sets in Python.

## GCxGC-MS

The MS layout has the chromatogram on the left, the *Spectrum* panel (with
*Processing* as a tab) and the *Match* panel on the right, and *Hits*,
*Peaks*, *Libraries*, *Masks* and *Integration* as tabs below them.

### Signal and spectra

The *EIC* field in the chromatogram toolbar shows the extracted ion
chromatogram of one or more m/z values (`91`, or `57 71 85`); clear it and
press `Enter` to return to the TIC. Masks are integrated on the signal shown.

Click the chromatogram (select tool) to select a point. With *snap to apex*
on (the crosshair button in the Spectrum panel, on by default) the point
moves to the most intense scan nearby; *Avg* sets how many scans are summed
around it (default 3). The spectrum appears at once, the library hits right
after.

The Spectrum panel shows the background-corrected or raw spectrum, relative
to the base peak (999) or in absolute counts. The most intense peaks are
labelled; hover a peak for its m/z and intensity. The header gives RT1, RT2,
scan, the averaged scans and the baseline in force. The `⋯` menu copies the
spectrum as text (`m/z<TAB>intensity`), saves it as MSP, or uses the selected
point as the background for spectra (see below).

The summed spectrum of a mask is searched like a point: select the mask in
the Masks panel and click the spectrum button in its toolbar.

### Library hits and structures

*Hits* lists the best 20 hits of the active libraries with match and reverse
match factors, formula, MW, CAS, NIST number and library; click a column
header to sort. Click a hit to show it in the *Match* panel: the unknown
spectrum points up, the library spectrum down, and the structure is shown
beside the plot (below it when the panel is narrow).

Structures come from the library entry when it has one (MoNA records carry
SMILES and InChI). Other compounds can be looked up at PubChem: the first
time, the Match panel asks whether to do so. Only compound identifiers of
library hits (InChIKey, CAS number, name) are sent, never spectra or file
names. Answers are cached on your computer, so each compound is looked up
once. Change the choice later under *Structures from PubChem* in the
Libraries panel.

### Libraries

The *Libraries* panel lists the loaded libraries with their size, source,
licence note and cache status. *Add…* takes a NIST MS Search `MAINLIB` (or
`MSSEARCH`) folder or an MSP, zip or npz file; *Download MoNA GC-MS* fetches
the open MoNA library. Libraries are converted once and cached, and they load
again at the next start.

Switch a library on or off to search it; several libraries can be searched
together, and their hits are merged by match factor. The library button in
the top bar and in the Match panel switches quickly: tick libraries, or click
*only* next to one. Changing libraries searches the current point again.

*Search settings* set the mass range (by default the acquired range; library
peaks outside it are ignored), the m/z and intensity weighting exponents
(default 0.5 and 0.5), the number of hits and the minimum match shown.

### Peaks

In the *Peaks* panel, *Detect & identify* finds the peaks above the given
S/N (default 10) and searches their apex spectra; progress is shown and the
job can be cancelled. *Peaks* in the chromatogram toolbar shows the markers.

Filter the table by name or minimum match and sort it by any column. Clicking
a row (or a peak marker in the chromatogram) selects the peak's apex, so its
spectrum and hits appear. To correct an identity:

* select a hit in *Hits* and click *Assign to peak*;
* double click the name to type one;
* delete the selected peak with the trash button.

Edited peaks have `user` in the *By* column, and the export (CSV, download
button) keeps them. When the libraries or the processing change, the table is
marked stale; *Re-identify* searches the peaks again with the active
libraries and keeps your edits (after re-processing, the peaks are detected
again).

### Baseline (MS)

The Processing panel sets the baseline of the image, EICs and spectra:
*Modulation percentile* (default), *Rolling percentile* or *None*, and the
channel S/N below which spectrum channels are zeroed (0 = off). Two modes
change spectra only and keep an image baseline of your choice:

* *Local*: subtracts the mean of scans on both sides of the selected point in
  the same modulation (gap and width in scans);
* *Reference point*: subtracts the spectrum of a background point; pick it
  with *Set as background* in the Spectrum panel's menu.

The baseline in force is shown in the spectrum header and written to the
exports.
