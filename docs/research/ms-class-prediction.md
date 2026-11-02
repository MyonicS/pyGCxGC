# Research: class-level assignment of EI mass spectra with machine learning

Status: literature and repository scan, 2026-10-24. Updated 2026-10-25: the NIST
structures (section F) and retention indices (section G) are decoded and
readable from `ms/nist.py`. Sections H and I record a discussion on using RI
and on how to set up the ML project; they are input for a plan, not a plan.
Items marked **(unverified)** come from search snippets only; check them before
relying on them. The ACS Omega paper and MASSISTANT page were read in full
through headless Chromium.

## Goal

For GCxGC-MS we often do not need the exact compound. A rough assignment is enough:
carbon-number range, boiling-point range, compound class (n-/iso-alkane,
cycloalkane, mono-/di-/tri-aromatic, oxygenate, N/S compound). The idea is to
predict structural descriptors (fingerprints, substructures, classes) from each
EI spectrum and use them to label peaks, regions and masks automatically. This
feeds the group-type work in [`docs/roadmap.md`](../roadmap.md) (Approach 2).

## Summary

1. **Fingerprint prediction from EI-MS is an established idea**, but the open
   ecosystem is thin. Almost every model was trained on **NIST**, which cannot be
   redistributed. Code is public, trained models mostly are not.
2. **The mature "spectrum → class" tools target LC-MS/MS** (SIRIUS, CANOPUS,
   MIST, DiffMS, MassSpecGym). They do not accept EI spectra. Their architecture
   is the template to copy: spectrum → fingerprint → class, where the class
   network is trained on *structures* and needs no spectra.
3. **No published ML classifier for GCxGC-MS group types was found.** GCxGC class
   assignment in the literature is rule-based (characteristic ions, retention
   position, ChromaTOF regions). This looks like a genuine gap.
4. **Our own library already gives labels for free.** `SpectralLibrary` carries
   SMILES/InChI. With RDKit we can derive any class label for every library
   spectrum and train a model on the library the user already has. This avoids
   shipping NIST-derived weights.

## A. Spectrum → fingerprint or structure (EI-MS)

| Project | What it does | Code / weights | Licence | Notes |
| --- | --- | --- | --- | --- |
| [DeepEI](https://github.com/hcji/DeepEI) (Ji et al., Anal. Chem. 2020, [paper](https://pubs.acs.org/doi/10.1021/acs.analchem.0c01450)) | One DNN per fingerprint bit set, predicts fingerprints from EI-MS, searches a structure DB, filters candidates with a retention-index CNN | Code only. No data, no weights (trained on NIST) | Apache-2.0 | Also has a structure → RI predictor and a NIST → numpy converter. Top-1 27.8 % as cited by the SpecTUS authors |
| EI2FP (Khrisanfov et al., Mass Spectrom. Lett. 2024, [paper](https://www.accesson.kr/msletters/assets/pdf/47009/journal-15-4-78.pdf)) | Single multi-output network predicting several fingerprints (MACCS, ECFP) | No repository found | n/a | Reports accuracy 0.91 vs 0.89, precision 0.86 vs 0.77 against DeepEI and >100x faster. A small multi-output net is the right size for our case |
| [SpecTUS](https://github.com/lorenzgerber/SpecTUS) (Anal. Chem. 2026, [paper](https://pmc.ncbi.nlm.nih.gov/articles/PMC13425552/)) | BART-style transformer (354 M parameters), EI-MS → SMILES, no database | Code, **pretrained (not fine-tuned) checkpoint and 17.2 M synthetic spectra on [Hugging Face `MS-ML`](https://huggingface.co/MS-ML)**. Fine-tuned NIST model is withheld | MIT (repo) | Pretrained on NEIMS/RASSP synthetic spectra from ZINC20, fine-tuned on NIST 20. Top-1 43 %, top-10 65 %, mean top-1 Tanimoto 0.67 on NIST test; on MoNA top-1 21 %, top-10 34 %. Input: integer m/z, 30 log intensity bins, max 300 peaks, m/z ≤ 500. Fails on unseen chemistry. FastAPI/Docker inference service. The paper names `hejjack/SpecTUS` while the fetched repo was `lorenzgerber/SpecTUS`; confirm which is canonical |
| MASSISTANT (J. Chromatogr. A 1759, 2025, [preprint](https://chemrxiv.org/engage/chemrxiv/article-details/67d9484d6dde43c9083d4dde)) | EI-MS → SELFIES de novo structure; multitask classifier over one-hot SELFIES parts | [BartaLazar/MASSISTANT](https://github.com/BartaLazar/MASSISTANT/) (archive, last push 2025-07). Training notebooks only. No weights, no NIST data, inference "will be available soon" | none declared | ~10 % exact on full NIST (180 k spectra); up to 54 % on a chemically homogeneous subset. That subset result is the relevant one for us: restricting the chemical domain (hydrocarbons) helps a lot. Not usable as is |
| Array-based ML for functional groups in EI-MS ([ACS Omega 2023](https://pubs.acs.org/doi/10.1021/acsomega.3c01684)) | One binary logistic-regression (LR) model per functional group (20 groups) and per class (aromatic, O-containing, N-containing) on raw m/z 0-300 intensities. An Inception V3 CNN on spectrum images was worse | [Ohio-State-Allen-Lab/Mass_Spec_Functional_Group_ML](https://github.com/Ohio-State-Allen-Lab/Mass_Spec_Functional_Group_ML) (notebooks, last push 2023-04, no licence declared). Data scraped from the NIST WebBook | none declared (paper CC BY-NC-ND) | 17/20 models >70 % test accuracy, 13/20 >75 %. Best: nitro, methyl, aromatic (fragment at m/z 78, odd-nitrogen rule). Weak: alcohol, ketone, amine; O-containing ~70 %. Most informative range is m/z 0-100. Checked on a few spectra outside NIST (limonene, pyridine, furfuryl alcohol). Simple linear models are competitive for class-level labels |

Takeaways:

* Exact structure from a low-resolution EI spectrum is hard, and isomers (alkanes,
  alkylbenzenes) are essentially indistinguishable. Class-level targets are much
  easier than SMILES, and class is all we need.
* Representation choice matters little: integer m/z bins with log or sqrt
  intensities. That is what we already have (`RawMSData`, dense nominal m/z).

## B. Forward models (structure → EI spectrum)

Useful for **augmenting training data** and for scoring candidates, not for
classification directly.

* **NEIMS** ([code](https://github.com/brain-research/deep-molecular-massspec), [paper](https://arxiv.org/pdf/1811.08545)): neural network predicting EI spectra from structure.
* **RASSP** ([code](https://github.com/thejonaslab/rassp-public), [paper](https://arxiv.org/pdf/2301.11419)): fast graph-network predictor. Predicted spectra for 70 M+ PubChem molecules are said to be hosted at spectroscopy.ai **(unverified)**.
* **CFM-EI** ([paper](https://pubmed.ncbi.nlm.nih.gov/27381172/)): rule/ML fragmentation predictor.

Idea: generate synthetic spectra for hydrocarbon-like molecules (alkanes, alkenes,
cycloalkanes, alkylbenzenes, PAHs, with alkyl chains of varying length). This
covers series that real libraries sample sparsely, and the class labels are exact.
Check that forward models are accurate for hydrocarbons before relying on them.

## C. Spectrum → compound class (LC-MS/MS, the architecture to copy)

* **SIRIUS / CSI:FingerID / CANOPUS** ([SIRIUS](https://github.com/sirius-ms/sirius)): fingerprint from the spectrum
  (SVMs), then a DNN predicts ClassyFire/NPClassifier classes from the
  fingerprint ([CANOPUS](https://www.biorxiv.org/content/10.1101/2020.04.17.046672.full.pdf)). The DNN is trained on ~1.1 M *structures*, not spectra. Java, MIT-style
  licence **(unverified)**. Not usable on EI directly.
* **MIST, DiffMS, MS-BART, MSNovelist** ([DiffMS](https://arxiv.org/html/2502.09571v1)): MS/MS de novo and fingerprint models. MSNovelist
  depends on closed-source CSI:FingerID fingerprints and its original
  implementation is reported as non-functional.
* **[MassSpecGym](https://arxiv.org/pdf/2410.23326)**: MS/MS benchmark. Relevant for evaluation design, not data.
* **Mass2SMILES** ([paper](https://www.biorxiv.org/content/10.1101/2023.07.06.547963.full.pdf)): predicts 60 functional groups from MS/MS. Same idea, different spectrum type.
* **ClassyFire** ([paper](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC5096306/)) classifies structures into a taxonomy (web service). Too coarse for hydrocarbons. **RDKit SMARTS** gives us finer, offline control.
* **IDSL_MINT** ([paper](https://jcheminf.biomedcentral.com/articles/10.1186/s13321-024-00804-5)): fingerprints from MS/MS, `.msp` input. Not EI.
* **matchms / Spec2Vec / MS2DeepScore** ([MS2DeepScore](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC8556919/)): spectrum embeddings and I/O tools. Trained on LC-MS/MS; fit for EI is **unverified**. matchms is a candidate for MSP I/O and cleaning, though we already have our own.

## D. GCxGC-specific literature

* Class assignment is **rule-based**: characteristic fragment ratios, formula
  series, and retention-space position
  ([ambient aerosol classification](https://pubmed.ncbi.nlm.nih.gov/17418225/),
  [diesel group types](https://www.nature.com/articles/s41598-026-54777-6)).
* **ChromaTOF** lets users draw class regions by hand, which is what our masks do today.
* **ASTM D2425** defines 11 hydrocarbon types in middle distillates from
  low-resolution MS via formula series (CnH2n-10, CnH2n-14, ...). Known
  interferences: FAME, and tertiary-carbon fragments inflating cycloparaffins.
  The characteristic ions are in the (paywalled) standard. They are a sensible
  hand-crafted feature set and baseline.
* **Retention modelling**: a hydrocarbon structure library with modelled GCxGC
  elution ([Environ. Sci. Technol.](https://pubs.acs.org/doi/10.1021/acs.est.2c06922))
  suggests nearest-neighbour methods for retention → class. Thermal-modulation
  RT2 prediction has systematic errors
  ([2018](https://www.sciencedirect.com/science/article/abs/pii/S002196731831344X)),
  while RI prediction with deep learning is
  [published](https://www.mdpi.com/1422-0067/22/17/9194). Retention is a second,
  independent evidence channel next to the spectrum.
* No ML paper for GCxGC-MS class assignment turned up in two searches. A third
  targeted search (random forest / CNN + "GC×GC" + "group type") is still worth doing.

## E. Data

* **NIST 20** (local, licensed): the only large EI set. Never commit it, its
  cache or spectra from it (see `AGENTS.md`). A model trained on it has the same
  licence problem as the SpecTUS fine-tune, so any weights we ship must not be
  derived from it.
* **MoNA / MassBank** (open, CC BY / CC BY-SA): `download_mona_gcms` already
  exists. The number of EI spectra is **unverified**, but it is much smaller than
  NIST. SpecTUS drops from 43 % to 21 % top-1 on MoNA.
* **Synthetic spectra**: the 17.2 M SpecTUS set on Hugging Face (licence to check)
  and our own NEIMS/RASSP runs (section B).
* **Retention indices**: `ri.dat` in NIST MAINLIB, local only, now decoded
  (section G). About a third of the NIST spectra have a semi-standard non-polar
  RI. Open RI data was not researched.
* **NIST structures**: `struct.db` is now decoded (section F), so every NIST
  spectrum has a local structure and RDKit labels. Same licence as the spectra:
  local use only, never commit or ship the structures or anything derived from them.

## F. NIST `struct.db` format (decoded 2026-10-25)

Status: constitution (atoms, elements, H counts, charges, bond orders) decoded
and validated. Stereo is located but not decoded. Implemented in `ms/nist.py`
(`read_nist_structures`, `decode_structure`, `structure_identifiers`); library
entries get SMILES/InChIKey on demand (`SpectralLibrary.structure_ids`).

**Linking spectra to structures.** `struct.in6` is a c-tree B-tree index with
0x200-byte nodes. The node header is `uint32 next, uint32 previous, uint16 key
count, uint16 bytes used`. A leaf holds 8-byte entries from `node + 0x12`: a
uint32 `struct.db` offset followed by the 4-byte key. The reader treats a node
as a leaf when all its offsets are valid `struct.db` records (in NIST 20 the
first leaf is at 0x400). The key is `nist.db` record bytes 0-1 plus 10-11. The fields `nist.py` calls
"flags" and "unknown" are a big-endian group number and a slot (0-4), so
together they form the record id. Every one of the 306,866 keys points at a
valid record start. `struct.db` holds 351,813 records, so about 45 k are not
referenced (possibly old or replicate structures).

**`struct.db`** is not a c-tree file. It is one `00` byte followed by
`uint16 LE length + payload` records that tile the file exactly. Payload:

| Part | Encoding |
| --- | --- |
| Header | 5 bytes: `n_seed, n_chain, n_ring, n_label, n_circle` |
| Bond orders | `ceil(2*n_bonds/8)` bytes, 2 bits per bond, LSB first: 0 single, 1 double, 2 triple, 3 aromatic. `n_bonds = n_seed + n_chain + n_ring` |
| Seed bonds | `n_seed` x (x1, y1, x2, y2): two new atoms and the bond between them |
| Chain atoms | `n_chain` x (x, y, endpoint): a new atom bonded to an existing one |
| Ring closures | `n_ring` x (endpoint, endpoint): a bond between existing atoms |
| Labels | `n_label` x (endpoint, code): atoms without a label are carbon with implicit H |
| Aromatic circles | `n_circle` x (x, y): centres of drawn aromatic rings |
| Tail (optional) | `[n][n bytes]` stereo (see below), sometimes followed by a `01` flag byte |

* **Endpoints.** Bond `i` has endpoints `2i` (first atom) and `2i+1` (second
  atom). For a chain bond, the first atom is the new atom and the second is the
  parent. All atom references use endpoint ids.
* **Numbers.** Coordinates are signed bytes in drawing units (bond length
  about 20, y down). `7f`/`81` mean "add +127/-127 and read the next byte",
  for large drawings. Endpoint indices escape with `7f` (+127, unsigned).
  An endpoint of `ff` in the label list is a free-floating label (counter-ion
  such as `Br-`), followed by its x, y.
* **Label codes.** 1 O, 2 OH, 3 HO, 4 N, 5 NH, 6 HN, 7 NH2, 8 H2N, 9 H
  (explicit atom), 10 Cl, 11 Br, 12 F, 13 Si, 14 S, 15 SH, 16 SiH3, 17 P,
  19 Cl, 20 Br. The second spelling (HO, HN, H2N, 19, 20) is the left-side
  form of a label. A code >= 0x20 starts a NUL-terminated text label, such as
  `Fe`, `PH`, `SiH2`, `N+`, `O-`, `O.` or `D`, sometimes prefixed with `^` or `\`.
* **Stereo tail.** `[n][n bytes]`. The first byte is a bond index (for example
  the N-Calpha bond of an amino acid), and the low 2 bits of the last byte
  differ between cis and trans (`85`/`86`). These look like a bit stream of
  stereo bonds. Not decoded. Roughly 17 k records end with a `01` byte of
  unknown meaning (common for TMS/TFA derivatives).

**Validation** (NIST 20, 306,866 linked structures):

* After building RDKit molecules (labelled atoms get exactly the H count of
  their label, carbon gets implicit H, aromatic bonds are kekulized), **all
  306,866 molecular formulas equal the NIST formula** (D counted as H).
  1,525 of them (metal complexes, isocyanides and drawings with pentavalent N)
  need partial sanitisation.
* Geometry: 99.1 % of the 6.5 M bonds have a length within 0.6-1.5x the
  molecule's median. Outliers are bridged rings drawn in perspective, metal
  complexes and long ring closures. So the endpoint decoding is right.
* Against MoNA, matched by CAS: 1,591 of 1,653 (96 %) have the same InChIKey
  connectivity block. In the 62 differences, NIST draws sugars open-chain,
  some tautomers differ, or MoNA has the wrong isomer for the CAS (butyl vs
  isobutyl benzoate, 1- vs 2-bromonaphthalene). At least one NIST entry has a
  name or CAS that does not match its drawing (NIST 428624
  "Hydratropaldehyde" is drawn as 3-phenylpropanal).

Not covered yet: stereo, the `01` flag, the 45 k unreferenced records, and
`ichi.dat` (compressed InChI, which would allow a second check).

## G. NIST `ri.dat` format (decoded 2026-10-25)

Implemented as `read_nist_ri` (one pandas row per measurement) and
`ri_by_compound` (median per compound and phase type) in `ms/nist.py`.

* A c-tree variable-length file with 447,285 records. Each is NUL-terminated,
  tab-separated latin-1 text with 21 fields: key, name, formula, RI, column
  class (capillary/packed), phase type, phase (e.g. DB-5), length, carrier,
  support (packed columns), diameter, film, RI type, program type, start and end
  temperature, rate, start and end hold, free-text program, reference code.
* **Keys.** `C<CAS digits>` (322 k records), `U<NIST number>` for compounds
  without CAS (77 k; 77,344 of 77,359 match a main-library NIST number with the
  same formula), and `R<id>` (48 k), compounds without a spectrum in the main
  library. So spectra join on CAS or NIST number.
* **Phase types** are spelled in 15 ways (`SSNP`, `semi-std-non-polar`, a typo
  `Semi-standatd`, ...). They normalise to semi-standard non-polar (237 k,
  5 % phenyl such as DB-5/HP-5), standard non-polar (121 k, 100 %
  dimethylpolysiloxane such as DB-1/OV-101), standard polar (89 k, PEG/wax) and
  a few rare ones.
* **RI types**: normal alkane (223 k), linear (140 k), Kovats (72 k) and blank
  (6 k) are on the n-alkane scale. **Lee RI** (6 k) uses the PAH scale
  (naphthalene 200, phenanthrene 300, chrysene 400) and must not be mixed in.
* The reference compounds of both scales (n-alkanes at 100 x carbon number,
  the Lee PAHs) are stored once with the phase type "All column types", so
  `ri_by_compound` adds them to every phase.
* Units in the column and program fields vary (`250 um` vs `0.25 mm`, `50C` vs
  `50 C`, `K/min` vs `C/min`). The reader parses `length_m`, `diameter_mm`,
  `film_um`, `t_start_c`, `t_end_c` and `rate_k_min` and keeps the text.
* `riref.dat` (6,253 records) holds full citations. How they link to the short
  reference codes (`2005VRA/PAS500-508`) is not known; `ri.idx`, `riref.idx`
  and `rivalues.in6` are indices that were not needed.

Validation (NIST 20): toluene 766, naphthalene 1182 and phenanthrene 1776 on
semi-standard non-polar phases; ethanol 932 and toluene 1040 on polar phases,
as in the literature. The median absolute deviation between repeated
measurements of a compound (n >= 3) is 3 units (non-polar) to 8 units (polar).
Coverage of the 306,869 main-library spectra: 32 % have a semi-standard
non-polar RI (109 k compounds), 4 % standard non-polar, 2 % polar.

## H. Retention index for GCxGC (discussion 2026-10-25)

**Calculating it from a chromatogram.** With a temperature program, use the
linear retention index (van den Dool & Kratz):

    RI_x = 100 * (n + (t_x - t_n) / (t_(n+1) - t_n))

Here `t_n` and `t_(n+1)` are the first-dimension retention times of the
n-alkanes with n and n+1 carbons that bracket peak `x`. An n-alkane has
RI = 100 x carbon number. Kovats' logarithmic form is for isothermal runs only.

1. **Alkane series.** Polyolefin pyrolysis oils contain it, usually as the
   alkane/alkene/alkadiene triplet series. The alkanes are the lowest straight
   line in the 2D plane (m/z 57/71/85, library hits). Biomass pyrolysis may not
   contain enough of them; then run an alkane standard mix (e.g. C7-C40) with
   the same method and reuse that calibration.
2. **Precise RT1.** With a 20 s modulation, RT1 only moves in 20 s steps, which
   is 10-20 RI units. A modulated peak is spread over 3-4 slices, though, and
   the intensity-weighted mean RT1 of its slices (or a Gaussian fit to the
   slice volumes) gives about 1-2 s. Do this for the alkanes and for every peak.
3. **Interpolate** piecewise between neighbouring alkanes. Extrapolation beyond
   the first or last alkane is unreliable. Constant offsets (second-column
   time, modulation phase) largely cancel because RI is relative to alkanes in
   the same run.

**Using it.**

1. **Identification.** Compare the observed RI with the library RI of the
   compound on the same phase type and rerank or flag hits by
   `|RI_obs - RI_lib|`; expect about 10-30 units for good data. NIST MS Search
   does this as an "RI penalty". It helps exactly where spectra fail: isomers
   with near-identical spectra (branched alkanes, alkylbenzenes) often differ
   by 20-100 units. Useful on its own, before any ML.
2. **Class features.** RI1 is a volatility / boiling-point proxy; for
   hydrocarbons RI/100 is close to an effective carbon number, the quantity
   spectra give poorly because alkanes lack a molecular ion. **ΔRT2**, the
   second-dimension retention of a peak minus that of the alkane line at the
   same RT1, is a polarity / aromaticity proxy: cycloalkanes sit just above the
   alkane line, then monoaromatics, then di- and tri-aromatics. (RI, ΔRT2) is an
   instrument-independent coordinate in which classes form bands, which is the
   information a spectrum lacks.
3. **Training.** Give the model the spectrum plus RI1. During training use the
   library RI plus noise of the expected measurement error (about 15 units) so
   that it learns realistic precision. Compounds without an experimental value
   could use a structure-to-RI predictor (section D), labelled as predicted.
   ΔRT2 has no library equivalent; it must come from our own runs (confidently
   identified peaks), or class boundaries in (RI, ΔRT2) are set without learning.

**Checks before relying on it.** The phase of our first column (to pick the
NIST phase type: 5 % phenyl -> semi-standard non-polar), and whether every run
contains enough n-alkanes.

**Possible pyGCxGC features**, in order: RI values for library entries (join
`ri_by_compound` on CAS / NIST number); an RI calibration from detected
alkanes in `GCxGC_MS` (with sub-modulation RT1); a ΔRI column and RI-aware
reranking in the hits table; an RI axis option in the app.

## I. Setting up the ML project (discussion 2026-10-25)

An initial take on a small research project, recorded for planning.

**1. Targets first, as classes rather than fingerprints.** The papers predict
fingerprints because they search a structure database afterwards. We want
class labels, so predict them directly as a multi-output model, with a
fingerprint as an optional auxiliary target. A first label set, all derivable
from the decoded NIST structures with RDKit SMARTS (minutes for 300 k):

* Heteroatoms: contains O / N / S / halogen / Si (Si matters: TMS derivatives,
  column bleed).
* Hydrocarbon group type (C,H only): n-alkane, branched alkane, cycloalkane,
  alkene, mono-, di-, tri+-aromatic (ASTM D2425-like, close to the roadmap).
* Carbon number (regression or ordinal): the most useful number for
  boiling-point ranges, and the hardest from spectra alone.
* Later: oxygenate subclasses (alcohol, ketone, acid, ester, ether, phenol),
  which the ACS Omega paper found hard.

Labelling is then almost free and deterministic. The real work is defining
classes that do not overlap and that match how masks are drawn.

**2. Freeze the evaluation before any experiments.** An agent loop optimises
exactly the metric it gets and finds any leakage.

* Split by structure (first InChIKey block), not by spectrum: NIST has
  replicate spectra and close isomers, so random splits inflate every number.
* Mimic the acquisition: crop inputs to m/z 45-500 like the Shimadzu runs.
  This matters, because the ACS Omega paper found m/z 0-100 the most
  informative range.
* A sealed test set the agent never sees: our own GCxGC peaks with confident
  IDs plus distorted library spectra (noise, co-elution mixtures, background
  subtraction; the distortion simulator from the weighting work exists).
* One scalar metric (e.g. macro PR-AUC over classes, optionally with
  carbon-number MAE), several seeds, because differences will be small.

**3. Baselines before networks.**

* **Library-search vote**: the top-k hits of the composite match factor vote on
  the class, measured on held-out structures (so it is analog search). This may
  already get hydrocarbon group types mostly right. If so, the value of ML is
  calibrated uncertainty, mixtures and compounds absent from the library rather
  than raw accuracy. Measure it first; it may solve the immediate need.
* Ion-series features plus gradient boosting (Option 4).
* Per-class logistic regression on binned spectra (ACS Omega).
* A small multi-output MLP (EI2FP).

**4. Agent-driven experiments (autoresearch-style).** As understood: an agent
edits one training script, each run has a fixed short time budget, and only
changes that improve a fixed validation metric are kept. This fits, with
caveats:

* The bottleneck is the information in a low-resolution spectrum, not the
  architecture, so architecture search will plateau quickly. Larger gains are
  likely from input representation (sqrt/log intensity, neutral losses, mass
  differences), augmentation (mixtures, noise, m/z cropping) and label-noise
  handling. Let the agent change those, never the labels, split or metric.
* The data is small (about 300 k x 456 bins): runs take seconds to minutes, a
  single consumer GPU or a CPU suffices, so short budgets give many experiments
  per hour.
* Hundreds of runs against one validation set overfit it. Keep the sealed test
  set for rare checks and report how the validation score drifts.

**5. Licensing.** Weights trained on NIST are for our own use; pyGCxGC can ship
the training code but not the model (the position DeepEI and SpecTUS took).
Check the NIST licence before copying the library to a rented cloud GPU, since
it is licensed per installation; a local GPU avoids the question.

**6. Novelty.** Spectrum-only classification is incremental. The open gap
(section D) is combining the spectrum with retention: NIST RI (section G)
gives the first-dimension feature for training, RT2 comes from our own runs
(section H). A natural two-phase plan: spectrum only, then spectrum + RI, with
RT2 tested on our own data.

**Suggested order:** write the label spec, freeze split and metric, run the
library-vote and gradient-boosting baselines on our own data, and only then
start the agent loop.

## Options for pyGCxGC

**Option 1: class model trained per user on their own library (recommended start).**
Label library spectra by RDKit SMARTS (alkane, alkene, ring count, aromatic ring
count, heteroatoms) and carbon number. Train a small multi-output network or
gradient-boosted model (EI2FP-style) on the binned spectrum. Output: calibrated
probabilities per class and an estimated carbon number. Runs locally on CPU, no
redistributable-weights problem, and fits the existing `SpectralLibrary` and
`Session` job model. Validate on held-out compounds (split by structure, not by
spectrum) and on the n-alkane series, which earlier tuning already found hard.

**Option 2: ship open weights trained on MoNA plus synthetic hydrocarbons.**
Same model, trained once by us on open data. Check the licences of the data
(CC BY-SA propagates to weights, arguably) and of NEIMS/RASSP outputs first.

**Option 3: wrap SpecTUS as an optional backend.** Gives SMILES candidates for
unknown peaks, from which classes follow via RDKit. Needs a GPU for comfort, the
user must fine-tune on their own NIST copy to match the paper, and the open
pretrained checkpoint alone is untested for us. Optional extra, not core.

**Option 4: hand-crafted ion features plus retention (no deep learning).**
ASTM-style ion series (m/z 57/71/85, 91/105/119, 128/142/156, 178) and RI/RT2
as features for a random forest. Interpretable and very cheap. The ACS Omega result (per-class logistic regression on raw spectra beats a CNN) supports it. Also a baseline
that any neural model must beat.

Combining 1 or 4 with retention (RI1 from the alkane series, RT2) as extra
features is the most promising route for GCxGC. Spectrum alone cannot separate
isomers, but position in the 2D plane can separate classes.

## Open questions

* Which class set do we need first? (the roadmap list: alkanes, alkenes,
  cycloalkanes, mono-/di-/tri-aromatics, larger PAHs, oxygenates, N/S)
* Acceptable label source for training: library hits only, or also user masks?
* How to treat co-eluting peaks and unresolved humps, where spectra are mixtures?
  Models trained on pure spectra will degrade. Mixture augmentation is needed.
* Output granularity: probabilities per peak, or per pixel (feeds mask generation
  directly)?
* Do we want uncertainty reporting (e.g. abstain below a threshold)?
* Which stationary phase is our first column (selects the NIST RI phase type)?
* Do all our runs contain enough n-alkanes for an RI calibration, or do we need
  an alkane standard per sequence?
* Is the library-search vote already good enough for hydrocarbon group types
  on our data (section I.3)?

## Suggested next steps

1. ~~Read the ACS Omega functional-group paper and MASSISTANT's repository.~~ Done, see section A.
1. ~~Decode NIST `struct.db`.~~ Done and wired into the library, see
   section F. Remaining: stereo.
1. ~~Decode NIST `ri.dat`.~~ Done (`read_nist_ri`, `ri_by_compound`), see
   section G. Not yet joined to library entries.
1. Write the label spec and the evaluation protocol (section I.1-I.2).
2. Check licences: SpecTUS Hugging Face data, NEIMS/RASSP, MoNA subsets.
3. Prototype Option 4 (features + RF) on the local PY_010 run using
   library hits as labels, as the baseline.
4. Prototype Option 1 and compare on a structure-split test set.
5. Run the targeted GCxGC + ML literature search.

## Sources

* DeepEI: https://github.com/hcji/DeepEI , https://pubs.acs.org/doi/10.1021/acs.analchem.0c01450
* EI2FP: https://www.accesson.kr/msletters/assets/pdf/47009/journal-15-4-78.pdf
* SpecTUS: https://github.com/lorenzgerber/SpecTUS , https://pmc.ncbi.nlm.nih.gov/articles/PMC13425552/ , https://huggingface.co/MS-ML
* MASSISTANT: https://chemrxiv.org/engage/chemrxiv/article-details/67d9484d6dde43c9083d4dde
* Functional groups from EI-MS: https://pubs.acs.org/doi/10.1021/acsomega.3c01684
* NEIMS: https://github.com/brain-research/deep-molecular-massspec , https://arxiv.org/pdf/1811.08545
* RASSP: https://github.com/thejonaslab/rassp-public , https://arxiv.org/pdf/2301.11419
* CFM-EI: https://pubmed.ncbi.nlm.nih.gov/27381172/
* SIRIUS/CANOPUS: https://github.com/sirius-ms/sirius , https://www.biorxiv.org/content/10.1101/2020.04.17.046672.full.pdf
* DiffMS: https://arxiv.org/html/2502.09571v1 ; MassSpecGym: https://arxiv.org/pdf/2410.23326
* Mass2SMILES: https://www.biorxiv.org/content/10.1101/2023.07.06.547963.full.pdf
* ClassyFire: https://www.ncbi.nlm.nih.gov/pmc/articles/PMC5096306/
* IDSL_MINT: https://jcheminf.biomedcentral.com/articles/10.1186/s13321-024-00804-5
* MS2DeepScore: https://www.ncbi.nlm.nih.gov/pmc/articles/PMC8556919/
* GCxGC classification: https://pubmed.ncbi.nlm.nih.gov/17418225/ , https://www.nature.com/articles/s41598-026-54777-6 , https://pubs.acs.org/doi/10.1021/acs.est.2c06922
* ASTM D2425: https://store.astm.org/d2425-19.html
* RT/RI prediction: https://www.sciencedirect.com/science/article/abs/pii/S002196731831344X , https://www.mdpi.com/1422-0067/22/17/9194
