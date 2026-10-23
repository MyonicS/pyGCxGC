# pyGCxGC Studio (app)

pyGCxGC Studio is the new graphical application for GCxGC data. It shows the
2D chromatogram with GPU rendering, draws and integrates masks on FID and
GCxGC-MS data, and will replace the Tk mask creator and MS viewer. All
calculations run in the pyGCxGC Python library, so everything the app does
can also be done in Python with the same results.

The app is under development: GCxGC-MS spectra, library search and peak
tables follow in the next milestone. Until then, use the
[GCxGC-MS viewer](ms_viewer.md) for those.

## Starting the app

The app needs the `app` extra:

```bash
pip install "pyGCxGC[app]"
python -m pyGCxGC.app --open
```

`--open` opens the app in the web browser. Without it, the command prints the
address (it contains an access token). Files can be passed directly:

```bash
python -m pyGCxGC.app --open -m 20 run.csv
```

`-m` sets the modulation time in seconds for all files given. FID files need
one; for GCxGC-MS files it is estimated when omitted. The server only accepts
connections from the same computer.

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
