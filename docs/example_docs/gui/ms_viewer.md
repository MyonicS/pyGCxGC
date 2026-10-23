# GCxGC-MS viewer and library search

pyGCxGC can read GCxGC-MS data, show the 2D chromatogram, extract the mass
spectrum of any point and identify it against a spectral library, either a
licensed NIST MS Search library or open libraries in MSP format.

## Starting the viewer

```bash
pygcxgc-msviewer path/to/run.cdf --library path/to/MSSEARCH/MAINLIB --modulation 20
```

or from Python:

```python
import pyGCxGC as gcgc

gcgc.launch_ms_viewer("run.cdf", library_path="MSSEARCH/MAINLIB", modulation=20)
```

All arguments are optional. The viewer remembers the last data file, library
and modulation time (`~/.config/pyGCxGC/ms_viewer.json`). If no modulation time
is given, it is estimated from the periodicity of the TIC.

## Layout

| Area | Content |
| --- | --- |
| Left (2/3) | 2D chromatogram (TIC or extracted ions), zoom/pan toolbar |
| Top right | Mass spectrum at the selected point (background subtracted) |
| Bottom right | Head-to-tail plot of the spectrum and the selected library hit, the hit list and the peak table |

* **Click** a point in the chromatogram to show its spectrum and search the
  library. With *Snap to apex*, the click jumps to the most intense scan nearby.
  *Avg scans* sums scans around the apex. This reduces the spectral skew of
  scanning quadrupoles.
* **EIC m/z**: type one or more masses (e.g. `91` or `57 71 85`) and press
  Enter to show an extracted ion chromatogram. Leave the field empty to return
  to the TIC.
* **Detect & identify peaks**: finds 2D peaks with a signal-to-noise above
  *Peak S/N* and searches each apex spectrum against the library. Click a row
  in the peak table to inspect a peak. Click a column header to sort.
  *Export table…* writes the table as CSV.
* **Shift (scans)** changes the modulation phase. Positive values move peaks
  down.

## Supported data

* ANDI-MS / AIA netCDF (`.cdf`). Export it from the instrument software, for
  example GCMSsolution *File Conversion → AIA*. Truncated exports and files
  with a missing record count are read as far as they are complete.
* OpenChrom CSV export (one column per m/z).
* JCAMP-DX GC-MS exports (`.jdx`).

Vendor binary files such as Shimadzu `.qgd` cannot be read directly.

## Libraries

* **NIST MS Search** main libraries: select the `MAINLIB` folder (or the
  `MSSEARCH` folder containing it). The library is decoded directly from the
  `nist.db` file, so NIST MS Search does not have to run (works on Linux and
  macOS). The NIST library is licensed software. Only use your own licensed
  copy, and never commit or share the converted cache.
* **MSP files** (NIST MSP, MoNA, MassBank, ...), optionally zipped.
* **MoNA GC-MS**: the *Load library…* dialog can download the open MassBank of
  North America GC-MS collection (~18 500 EI spectra).

Converted libraries are cached in `~/.cache/pyGCxGC`. The first load of NIST 20
takes about 10 s, and later loads take a fraction of a second.

## Match factors

Spectra are compared with the composite match factor of Stein & Scott (1994),
the algorithm behind the NIST MS Search identity search:

* **Match** (MF, 0-1000): weighted dot product combined with the ratios of
  neighbouring peaks.
* **R.match** (RMF): the same score, ignoring peaks of the unknown that are
  absent in the library spectrum. A high RMF with a lower MF points to
  co-elution or background.

Only the acquired mass range is compared (for example m/z 45-500), so library
peaks below the scan range do not lower the score. Peaks are weighted as
`(m/z)^0.5 * intensity^0.5`, which proved more robust for GCxGC-qMS spectra
than NIST's `(m/z)^3 * intensity^0.6`. The weighting can be changed through
`SearchParameters`.

## Python API

```python
import pyGCxGC as gcgc
from pyGCxGC.ms.peaks import detect_peaks, identify_peaks

chrom = gcgc.read_gcxgc_ms("run.cdf", modulation_time=20)
library = gcgc.load_library("MSSEARCH/MAINLIB")  # or an .msp/.zip file

spectrum = chrom.spectrum_at(10.33, 10.1, n_average=3)  # RT1 (min), RT2 (s)
hits = gcgc.search_spectrum(
    spectrum.mz, spectrum.intensity, library, mz_min=45, mz_max=500
)
print(hits[0].name, hits[0].match)

peaks = detect_peaks(chrom, snr=10)
table, all_hits = identify_peaks(chrom, peaks, library)
table.to_csv("peaks.csv", index=False)
```

`chrom.chrom_2D` holds the TIC in the same layout as `GCxGC_FID.chrom_2D`, so
masks and `integrate_masks` work for GCxGC-MS data too.
