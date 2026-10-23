# Roadmap

## In progress: pyGCxGC app

A modern desktop and web application replaces the two Tk tools (mask creator
and GCxGC-MS viewer). It runs on the Python library, ships as installers for
Windows, macOS and Linux, and can be opened from notebooks. The full
specification is in
[`specs/app.md`](https://github.com/MyonicS/pyGCxGC/blob/main/specs/app.md).

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

## Agent interface: skills and an MCP server

The goal is that an AI agent can process GCxGC data for the user. Typical
tasks:

* load runs;
* check modulation, phase shift and wrap-around;
* detect and identify peaks;
* apply templates or masks and integrate;
* compare samples;
* write a short report with figures.

This is planned in two steps: agent skills first, then an MCP server.

### Step 1: agent skills

Skills need no infrastructure. They live in `agents/skills/<name>/SKILL.md`,
each with small scripts that use the public Python API, and produce CSV and
PNG files plus a Markdown summary. Examples:

* `gcxgc-qc`: modulation estimate, phase, saturation, solvent region;
* `gcxgc-identify`: peak table with identifications and flagged low-quality
  matches;
* `gcxgc-group-type`: apply a template and report class fractions;
* `gcxgc-compare`: differences between runs.

### Step 2: MCP server

`pyGCxGC.mcp` uses the official Python MCP SDK, with stdio for local agents.
Its tools map one-to-one onto the app's `Session` methods, so when it is
connected to a running app, the GUI shows what the agent does live:

* `open_run`, `describe_run`;
* `render_chromatogram`, which returns a PNG so agents that read images can
  look at the data;
* `get_spectrum`, `search_library`;
* `detect_peaks`, `identify_peaks`, `get_peak_table`;
* `apply_mask_set`, `integrate`;
* `export`.

### Guardrails

* Tool results stay small: summaries, the top N rows, and file paths for
  anything large.
* Long jobs return a job id with progress.
* Writes are restricted to an output folder.
* Licensed library content is never returned in bulk: only hits, never whole
  library spectra.

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
