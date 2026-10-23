# pyGCxGC

<p align="center">
  <img src="docs/assets/pyGCxGC_logo-01.svg" alt="pyGCxGC Logo" width="200"/>
</p>

## Overview
pyGCxGC is a python package for processing of two dimensional Gas Chromatography (GCxGC) data.
It supports 2D chromatograms from single-channel detectors such as FID, and GCxGC-MS data with library searching.

[![Tests](https://github.com/MyonicS/pyGCxGC/actions/workflows/tests.yaml/badge.svg)](https://github.com/MyonicS/pyGCxGC/actions/workflows/tests.yaml)
[![Test Status](https://github.com/MyonicS/pyGCxGC/actions/workflows/python-package.yml/badge.svg?branch=main)](https://github.com/MyonicS/pyGCxGC/actions/workflows/python-package.yml)
![Coverage](docs/assets/coverage-badge.svg)

## Features
- Load 1D Chromatograms from a csv or a pandas dataframe
- Generate 2D Chromatograms
- Integrate areas in 2D Chromatograms using .tif masks
- GUI for generation of masks
- GCxGC-MS: read ANDI/AIA netCDF, OpenChrom CSV and JCAMP-DX data, extract background-corrected spectra, view extracted ion chromatograms
- Spectral library search (NIST-style match / reverse match factors) against your own NIST MS Search library (read directly, no Windows needed) or open MSP libraries such as MoNA
- 2D peak detection with automatic identification and CSV export
- GCxGC-MS viewer GUI: chromatogram, spectrum at the selected point and best library match

<p align="center">
  <img src="docs/assets/Example_chromatogram.png" alt="Example 2D Chromatogram" width="400"/>
</p>

> **⚠️ WARNING**: pyGCxGC is under active development. Braking changes can occur. Please report any issues using the [Issue Tracker](https://github.com/MyonicS/pyGCxGC/issues).

## Installation

pyGCxGC is not yet available on PyPI. To install the latest development version, clone the repository and install it using pip:

```bash
git clone https://github.com/MyonicS/pyGCxGC.git
cd pyGCxGC
pip install .
```

in editable mode:

```bash
pip install -e .
```

> **Note**: The GUI functionality requires tkinter, which is included in most Python installations. If you're having issues with the GUI, ensure tkinter is installed on your system.

## Documentation

For a short tutorial, see the [Quickstart Notebook](docs/notebooks/Quickstart_notebook.ipynb).

## Quick Start

### Parsing and Plotting

Parse a 2D chromatogram from a CSV file or from a Dataframe with retention time (`Ret.Time[s]`) and `Absolute Intensity` columns:

```python
import pyGCxGC as gcgc
import numpy as np
from matplotlib import pyplot as plt

# Parse chromatogram
chrom = gcgc.parse_2D_chromatogram(
    'example_data/example_chromatograms/Example_FID.csv',
    modulation_time=20,  # seconds
    sampling_interval='infer',
    baseline_type='stridewise',
    normalize='volume',
    name='Example FID'
)

# Plot
plt.imshow(np.sqrt(chrom.chrom_2D), 
           cmap='viridis', 
           extent=chrom.limits, 
           aspect='auto')
plt.xlabel('Retention time 1 (min)')
plt.ylabel('Retention time 2 (s)')
plt.colorbar(label=r'$\sqrt{\mathrm{intensity}}$')
plt.show()
```

### Masking and Integration

Integrate specific regions using binary mask files:

```python
# Single mask
masked = gcgc.mask_chromatogram(chrom.chrom_2D, 'path/to/mask.tif')

# Multiple masks
results = gcgc.integrate_masks(
    chrom.chrom_2D, 
    masks='path/to/masks/',  # directory or list of paths
    mask_names='infer'
)
```

### Creating Masks with the GUI

The package includes a graphical user interface for creating masks for 2D chromatograms. 
The GUI allows you to load a chromatogram, draw regions of interest, and save them as binary mask files (`.tif`).

To launch the GUI from within Python:

```python
import pyGCxGC as gcgc

# Launch the mask creator GUI
gcgc.launch_mask_creator()
```

The GUI provides tools to:
- Load and visualize 2D chromatograms
- Draw masks using selection tools
- Add/remove selections to/from masks
- Save masks as .tif files for later use with pyGCxGC's masking functions

For a finer control, save the chromatogram as tif and create a mask image processing software such as ImageJ.

### GCxGC-MS viewer and library search

```bash
pygcxgc-msviewer run.cdf --library /path/to/MSSEARCH/MAINLIB --modulation 20
```

The 2D chromatogram is shown on the left. Clicking a point shows its
background-subtracted mass spectrum (top right) and the best library matches
(bottom right). *Detect & identify peaks* builds an identified peak table for
the whole run.

```python
import pyGCxGC as gcgc

chrom = gcgc.read_gcxgc_ms("run.cdf", modulation_time=20)
library = gcgc.load_library("/path/to/MSSEARCH/MAINLIB")  # or a .msp / .zip file
spectrum = chrom.spectrum_at(10.33, 10.1)  # RT1 (min), RT2 (s)
hits = gcgc.search_spectrum(spectrum.mz, spectrum.intensity, library, mz_min=45, mz_max=500)
```

Libraries are converted once and cached in `~/.cache/pyGCxGC`. The NIST
library is licensed software: use only your own copy and never commit it or its
cache. An open alternative (MoNA GC-MS, ~18 500 EI spectra) can be downloaded
from the viewer or with `gcgc.ms.download_mona_gcms()`. See
[docs/example_docs/gui/ms_viewer.md](docs/example_docs/gui/ms_viewer.md) for details.