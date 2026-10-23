# Mask sets

A mask set holds named masks for integrating regions of a 2D chromatogram.
Unlike a TIFF mask, which is tied to one pixel grid, every mask is an ordered
list of shapes in retention time units (RT1 in minutes, RT2 in seconds):

* add or subtract a rectangle or a polygon;
* add or subtract a raster (an imported TIFF).

Because the shapes are stored in retention times, a mask set drawn on one run
can be applied to another run, also with a different modulation time or
sampling rate. Raster masks only apply to chromatograms of the same shape.

## Drawing masks in Python

```python
import pyGCxGC as gcgc

chrom = gcgc.parse_2D_chromatogram("run.csv", modulation_time=20)

masks = gcgc.MaskSet()
aromatics = masks.new_mask("Monoaromatics", category="monoaromatics")
aromatics.add_rect(rt1=(10, 25), rt2=(4, 9))
aromatics.add_polygon([(26, 5), (40, 6), (40, 12), (26, 9)])
aromatics.subtract_rect(rt1=(15, 16), rt2=(4, 5))

masks.integrate(chrom)  # {"Monoaromatics": volume}
masks.integration(chrom)  # table with volume, percent, n_pixels and "unassigned"
masks.overlaps(chrom)  # pairs of masks sharing pixels
```

A pixel belongs to a shape when its centre lies inside it (even-odd rule, as
`matplotlib.path.Path.contains_points`). Pixel `(i, j)` covers RT1
`[rt1[j], rt1[j] + P/60)` and RT2 `[rt2[i], rt2[i] + dt)`, with `P` the
modulation time and `dt` the sampling interval.

Masks are integrated on the processed signal with `integrate_2D`, exactly like
`integrate_masks`. They work on GCxGC-MS data too; there `chrom.chrom_2D` is the
TIC, and another signal can be passed as `data=` in the `chrom_2D` layout:

```python
ms = gcgc.read_gcxgc_ms("run.cdf", modulation_time=20)
masks.integrate(ms, data=ms.eic_2d([91, 105])[::-1])
```

## Files

```python
masks.save("run.gcxgc-masks.json")  # versioned JSON, shapes in RT units
masks = gcgc.MaskSet.load("run.gcxgc-masks.json")

masks.to_tiffs("masks/", chrom)  # one uint8 0/255 TIFF per mask
gcgc.integrate_masks(chrom.chrom_2D, masks="masks/")  # same numbers

old = gcgc.MaskSet.from_tiffs("masks/", chrom)  # e.g. from the mask creator
```

TIFFs keep the `chrom_2D` layout and shape, so they also work with
`mask_chromatogram`. Imported TIFFs keep their 8-bit values as weights
(`value / 255`), exactly as `integrate_masks` uses them.

`rasterize(chrom)` returns the pixel weights of every mask in the `chrom_2D`
layout (row 0 is the highest RT2). `rasterize(chrom, origin="lower")` gives the
layout of `GCxGC_MS.tic_2d`, with row 0 at the start of the modulation.
