# Roadmap

## In progress: pyGCxGC app

A modern desktop and web application replaces the two Tk tools (mask creator
and GCxGC-MS viewer). It runs on the Python library, ships as installers for
Windows, macOS and Linux, and can be opened from notebooks.

The two backend features below come after the app. The app already reserves
hooks for them: mask categories, a plugin registry, and a `Session` API that
agents can call.

## Group-type integration: regions instead of peaks

Often the individual peaks do not matter, only how much of the sample belongs
to each compound class: for example alkanes, alkenes, cycloalkanes,
monoaromatics, diaromatics, triaromatics, larger PAHs, oxygenates and
N/S compounds. Today this is done with hand-drawn masks. The goal is to
generate these regions automatically and reproducibly. The two approaches
below can be combined.

### Approach 1: retention-based featurisation and templates

GCxGC chromatograms are structured: compound classes form bands, and
homologous series form lines. This makes class regions predictable once the
retention axes are normalised.

* **Retention index axes.** Templates drawn in these coordinates move with
  retention drift and transfer between runs and similar methods.
    * Fit RT1 to a first-dimension retention index from the n-alkane series, found
      automatically from peaks with alkane spectra (MS) or from a reference run
      (FID).
    * Express RT2 relative to a reference series, such as the alkane line, as a
      second-dimension index.
* **Templates.** A template is a mask set in index coordinates, with a category
    per region. Applying it is warping it to the run (from anchor peaks,
    piecewise linear in RT1, smooth in RT2) and then rasterising it. Templates
    are edited in the app's mask editor.
* **Featurisation for MS data.**
    * Per-pixel features: characteristic ion ratios such as m/z 57/71/85
    (alkanes), 91/105/119 (alkylbenzenes), 128/142/156 (naphthalenes) and 178
    (three-ring aromatics), the base peak, and the M⁺ series.
    * Segment the image on these features: a watershed of blobs, then each blob
    gets the class of its dominant features. This also covers the unresolved
    hump (UCM), where peak-based methods fail.

### Approach 2: machine-learning classification

* **Labels without manual work.**
    * Peaks identified confidently by the library (high match and reverse match)
    get a class from the structure of the hit. RDKit SMARTS rules decide, for
    example, "aromatic ring count = 2 → diaromatic".
    * Every GC-MS run therefore produces training data. User-drawn masks provide
    further labels.
* **Features per peak:**
    * retention indices in both dimensions;
    * the binned, normalised spectrum and key fragment and neutral-loss
    indicators;
    * the class distribution of the top-10 library hits, which is robust even
    when the exact identity is wrong.
* **Models.** Start with gradient boosting (scikit-learn) with calibrated
    probabilities and per-class confusion matrices. Spectrum embeddings or small
    neural networks come later, if they help.
* **Pixel or tile classification** for unresolved regions. Classify local
    summed spectra instead of peaks, then clean up spatially (morphology), which
    yields class masks and volumes.

### FID: transfer from GC-MS

FID gives better quantification but no spectra. Run the same method on
GCxGC-MS, classify there, align both runs with anchor peaks (n-alkanes), and
project the class regions onto the FID chromatogram as a template. Integrate
with the existing mask integration.

### Deliverables

* **A `pyGCxGC.regions` module:**
    * `RetentionIndexer` (fit from an alkane series);
    * `Template` (load, save, warp, rasterise);
    * `classify_peaks`, `classify_pixels`;
    * `integrate_classes`, which returns class volumes and fractions with
    uncertainties.
* **A "Group-type" panel in the app.** It applies or edits a template, shows the
    class map, and exports the class table.
* **Validation.** Compare with hand-drawn masks on reference samples, and
    report the differences in class fractions.

## Agent interface: an MCP server

The goal is that an AI agent can process GCxGC data for the user. The
acceptance example is *"What fraction of the FID chromatogram is
monoaromatics?"*, answered from a GCxGC-FID run and a GCxGC-MS run of the
same sample: the agent opens both runs, sets the solvent cut-offs, corrects
the RT2 delay between the detectors, detects and identifies the MS peaks,
labels them by class, draws a mask from the labelled peaks, copies it to the
FID run and integrates.

`pygcxgc mcp` is a local MCP server (official Python MCP SDK, stdio), tested
with Claude Code and opencode. Its tools are thin wrappers around the app's
`Session`, so with `--gui`, or when attached to a running app, the GC²
window shows every step live and the user can correct it. All guidance
(server instructions, workflow guides as MCP resources, a prompt) ships with
the server, so nothing else has to be installed.

New core code, usable from Python and the app as well:

* peak classification: the agent labels peaks from evidence cards, built-in
  and agent-written rules propose labels and make them reproducible;
* masks from labelled peaks, with coverage and purity reports;
* the RT2 delay between two runs, estimated by cross-correlation;
* a match probability per library hit (lead over the competing compounds,
  calibrated like NIST's "Prob %");
* rendering of chromatograms with masks and peaks as PNG images.

### Guardrails

* Every result carries a grade, "exploratory" or "reportable", computed from
  the review status, the delay correction and the processing. The agent
  cannot set it.
* Labels record their source (rule, agent, user); user labels win.
* Tool results stay small: summaries, the top N rows, and file paths for
  anything large. Long jobs return a job id with progress.
* The server reads only below allowed folders and writes only to an output
  folder. These checks run in the server, whatever the client allows.
* Licensed libraries (NIST) never leave the machine: the agent gets hit
  names, formulas, CAS numbers and match values, never library spectra or
  structures.

## Other planned work

* Spectral deconvolution of co-eluting compounds (e.g. MCR-ALS).
* Retention-index filtering of library hits. NIST's `ri.dat` holds 447,285
    readable retention-index records keyed by CAS.
* Decoding NIST synonyms, InChIKeys and structures (`synonym.db`, `ichi.dat`,
    `struct.db`).
* More file formats (ANDI-chrom FID, vendor files via OpenChrom exports).
* High-resolution TOF data: memory-mapped storage and tiled rendering.
* Multi-run alignment, difference images, batch processing and reports.
* Quantification with response factors and internal standards (FID).
