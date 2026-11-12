"""Tests for pyGCxGC.regions and pyGCxGC.render on the synthetic GCxGC pair."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from synthetic import (
    PAIR_MODULATION,
    _synthetic_pair_cached,
    pair_library,
    synthetic_pair,
)

import pyGCxGC as gcgc
from pyGCxGC.ms import GCxGC_MS
from pyGCxGC.ms.peaks import detect_peaks
from pyGCxGC.regions.alignment import estimate_delay, suggest_solvent_cutoff


def fid_of(frame, **kwargs):
    return gcgc.parse_2D_chromatogram(
        frame.copy(), modulation_time=PAIR_MODULATION, **kwargs
    )


# ------------------------------------------------------------------ solvent
def test_ms_solvent_cutoff_zeroes_the_solvent():
    raw, _, _ = _synthetic_pair_cached()
    plain = GCxGC_MS(raw, PAIR_MODULATION)
    for unset in (None, 0, 0.0):  # "not set" and 0 change nothing
        same = GCxGC_MS(raw, PAIR_MODULATION, solvent_cutoff=unset)
        np.testing.assert_array_equal(same.tic_2d, plain.tic_2d)
    cut = GCxGC_MS(raw, PAIR_MODULATION, solvent_cutoff=0.8)
    n = cut.n_solvent_scans
    assert n == int(np.searchsorted(raw.scan_times, 48.0, side="right"))
    tic = cut.tic_2d.T.ravel()  # scan order (shift 0)
    assert not tic[:n].any()
    np.testing.assert_array_equal(tic[n:], plain.tic_2d.T.ravel()[n:])
    eic = cut.eic_2d([49, 84]).T.ravel()
    assert not eic[:n].any()
    np.testing.assert_array_equal(cut.baseline.levels, plain.baseline.levels)
    assert not cut.spectrum(150, n_average=3).intensity.any()
    assert cut.spectrum(150, subtract_background=False).intensity.any()
    # peak detection: the solvent peaks are gone, the compounds stay
    before = detect_peaks(plain, snr=10)
    after = detect_peaks(cut, snr=10)
    assert before["rt1_min"].min() < 0.5
    assert after["rt1_min"].min() > 0.75
    assert len(before) - len(after) == int((before["rt1_min"] < 0.75).sum())


def test_solvent_cutoff_suggestion():
    raw, frame, truth = _synthetic_pair_cached()
    first = min(c["rt1_min"] for c in truth["compounds"].values())
    for suggestion in (
        suggest_solvent_cutoff(GCxGC_MS(raw, PAIR_MODULATION)),
        suggest_solvent_cutoff(fid_of(frame)),
    ):
        # after the solvent (0.25 min), before the first compound's rise
        assert 0.6 < suggestion < first - 0.1
    # no solvent peak, no suggestion
    t = frame["Ret.Time[s]"].to_numpy()
    y = frame["Absolute Intensity"].to_numpy().copy()
    y[t < 50] = 0.05
    assert suggest_solvent_cutoff(times=t, signal=y, modulation_time=6.0) is None
    # a maximum late in the run is not a solvent
    late = y.copy()
    late[(t > 500) & (t < 520)] = 1e4
    assert suggest_solvent_cutoff(times=t, signal=late, modulation_time=6.0) is None


# ------------------------------------------------------------------ delay
@pytest.mark.parametrize("delay", [0.0, 0.5, -0.5, 0.3])
def test_estimate_delay_recovers_known_delays(delay):
    raw, frame, _ = synthetic_pair(delay=delay)
    ms = GCxGC_MS(raw, PAIR_MODULATION)
    est = estimate_delay(ms, fid_of(frame))
    assert est.delay_s == pytest.approx(delay, abs=0.05)
    assert est.suggested_shift == round(delay / 0.1)
    assert est.warnings == []
    assert est.aligned == (abs(delay) < 0.05)
    # after the correction the runs are aligned
    ms.set_modulation(shift=est.suggested_shift)
    again = estimate_delay(ms, fid_of(frame))
    assert again.aligned
    assert again.shift_change == 0
    assert again.delay_s == pytest.approx(delay, abs=0.05)


def test_estimate_delay_accounts_for_phase_shifts():
    raw, frame, _ = _synthetic_pair_cached()
    est = estimate_delay(GCxGC_MS(raw, PAIR_MODULATION, shift=-3), fid_of(frame))
    assert est.delay_s == pytest.approx(0.5, abs=0.05)
    assert (est.shift_change, est.suggested_shift) == (8, 5)
    # the FID's shift (in its own samples) moves the target
    shifted = estimate_delay(GCxGC_MS(raw, PAIR_MODULATION), fid_of(frame, shift=10))
    assert shifted.rt2_offset_s == pytest.approx(0.5 + 10 * 0.02, abs=0.05)


def test_estimate_delay_warnings():
    raw, frame, _ = synthetic_pair(drift=0.4)
    est = estimate_delay(GCxGC_MS(raw, PAIR_MODULATION), fid_of(frame))
    assert any("drifts" in w for w in est.warnings)
    assert len(est.windows) >= 3
    # an unrelated target correlates poorly
    rng = np.random.default_rng(1)
    noisy = frame.copy()
    noisy["Absolute Intensity"] = rng.random(len(noisy)) + 0.2 * np.roll(
        noisy["Absolute Intensity"].to_numpy(), 4000
    )
    bad = estimate_delay(GCxGC_MS(raw, PAIR_MODULATION), fid_of(noisy))
    assert bad.delay_s is None or any("correlate" in w for w in bad.warnings)
    # differing modulation times are reported
    other = gcgc.parse_2D_chromatogram(frame.copy(), modulation_time=5.0)
    est = estimate_delay(GCxGC_MS(raw, PAIR_MODULATION), other)
    assert any("modulation times differ" in w for w in est.warnings)


def test_pair_library_has_structures():
    lib = pair_library()
    assert lib.structure_ids(lib.find("Toluene")[0])[0] == "Cc1ccccc1"


# ------------------------------------------------------------------ classes
from pyGCxGC.ms.search import Hit
from pyGCxGC.regions.classes import (
    ClassScheme,
    PeakEvidence,
    Rule,
    add_label_columns,
    apply_scheme,
    dbe,
    parse_formula,
    propose,
    review_cards,
)

rdkit = pytest.importorskip("rdkit", reason="structure rules need RDKit")

# (name, SMILES, formula, class, subclass); "" SMILES = formula fallback
GROUP_TYPES = [
    ("Toluene", "Cc1ccccc1", "C7H8", "monoaromatics", "alkylbenzenes"),
    ("o-Xylene", "Cc1ccccc1C", "C8H10", "monoaromatics", "alkylbenzenes"),
    ("Ethylbenzene", "CCc1ccccc1", "C8H10", "monoaromatics", "alkylbenzenes"),
    ("Cumene", "CC(C)c1ccccc1", "C9H12", "monoaromatics", "alkylbenzenes"),
    ("Mesitylene", "Cc1cc(C)cc(C)c1", "C9H12", "monoaromatics", "alkylbenzenes"),
    ("Hexylbenzene", "CCCCCCc1ccccc1", "C12H18", "monoaromatics", "alkylbenzenes"),
    ("Styrene", "C=Cc1ccccc1", "C8H8", "monoaromatics", "alkenylbenzenes"),
    (
        "alpha-Methylstyrene",
        "CC(=C)c1ccccc1",
        "C9H10",
        "monoaromatics",
        "alkenylbenzenes",
    ),
    ("Indane", "c1ccc2c(c1)CCC2", "C9H10", "monoaromatics", "naphthenobenzenes"),
    ("Tetralin", "c1ccc2c(c1)CCCC2", "C10H12", "monoaromatics", "naphthenobenzenes"),
    ("Indene", "C1=Cc2ccccc2C1", "C9H8", "monoaromatics", "naphthenobenzenes"),
    ("Biphenyl", "c1ccc(-c2ccccc2)cc1", "C12H10", "diaromatics", ""),
    ("Naphthalene", "c1ccc2ccccc2c1", "C10H8", "diaromatics", ""),
    ("2-Methylnaphthalene", "Cc1ccc2ccccc2c1", "C11H10", "diaromatics", ""),
    ("Acenaphthene", "c1cc2cccc3c2c(c1)CC3", "C12H10", "diaromatics", ""),
    ("Diphenylmethane", "c1ccc(Cc2ccccc2)cc1", "C13H12", "diaromatics", ""),
    ("Anthracene", "c1ccc2cc3ccccc3cc2c1", "C14H10", "polyaromatics", ""),
    ("Phenanthrene", "c1ccc2c(c1)ccc1ccccc12", "C14H10", "polyaromatics", ""),
    ("Pyrene", "c1cc2ccc3cccc4ccc(c1)c2c34", "C16H10", "polyaromatics", ""),
    ("Phenol", "Oc1ccccc1", "C6H6O", "oxygenates", ""),
    ("p-Cresol", "Cc1ccc(O)cc1", "C7H8O", "oxygenates", ""),
    ("2-Butanone", "CCC(C)=O", "C4H8O", "oxygenates", ""),
    ("Furan", "c1ccoc1", "C4H4O", "oxygenates", ""),
    ("Levoglucosan", "OC1C(O)C2COC(O2)C1O", "C6H10O5", "oxygenates", ""),
    ("Thiophene", "c1ccsc1", "C4H4S", "n_s_compounds", ""),
    ("Pyridine", "c1ccncc1", "C5H5N", "n_s_compounds", ""),
    ("Benzonitrile", "N#Cc1ccccc1", "C7H5N", "n_s_compounds", ""),
    ("Chlorobenzene", "Clc1ccccc1", "C6H5Cl", "halogenated", ""),
    ("Dichloromethane", "ClCCl", "CH2Cl2", "halogenated", ""),
    (
        "Hexamethylcyclotrisiloxane",
        "C[Si]1(C)O[Si](C)(C)O[Si](C)(C)O1",
        "C6H18O3Si3",
        "silicon",
        "",
    ),
    ("n-Decane", "CCCCCCCCCC", "C10H22", "n_alkanes", ""),
    ("n-Hexadecane", "CCCCCCCCCCCCCCCC", "C16H34", "n_alkanes", ""),
    ("2-Methylnonane", "CCCCCCCC(C)C", "C10H22", "isoalkanes", ""),
    ("Pristane", "CC(C)CCCC(C)CCCC(C)CCCC(C)C", "C19H40", "isoalkanes", ""),
    ("Cyclohexane", "C1CCCCC1", "C6H12", "cycloalkanes", ""),
    ("Decalin", "C1CCC2CCCCC2C1", "C10H18", "cycloalkanes", ""),
    ("Butylcyclohexane", "CCCCC1CCCCC1", "C10H20", "cycloalkanes", ""),
    ("1-Decene", "C=CCCCCCCCC", "C10H20", "alkenes", ""),
    ("Cyclohexene", "C1=CCCCC1", "C6H10", "alkenes", ""),
    ("1-Octyne", "C#CCCCCCC", "C8H14", "alkenes", ""),
    ("Limonene", "CC1=CCC(CC1)C(C)=C", "C10H16", "alkenes", ""),
    # formula fallback (hits without a structure)
    ("Propylbenzene", "", "C9H12", "monoaromatics", ""),
    ("Decane", "", "C10H22", "n_alkanes", ""),
    ("3-Methyldecane", "", "C11H24", "isoalkanes", ""),
    ("Dimethylnaphthalene", "", "C12H12", "diaromatics", ""),
    ("Hexene", "", "C6H12", None, ""),
    ("Benzene", "", "C6H6", "monoaromatics", ""),
    ("Phenol", "", "C6H6O", "oxygenates", ""),
]


def hit(name="x", formula="", match=900.0, rank=1, probability=None, delta=None):
    h = Hit(rank, 0, name, formula, "", 0, 0, 0, float(match), float(match))
    h.probability, h.delta_match = probability, delta
    return h


def ev(hits, structures, spectrum=None, peak=None):
    peak = peak or {"peak_id": 1, "rt1_min": 10.0, "rt2_s": 2.0}
    return PeakEvidence(peak, hits, spectrum, structures)


@pytest.mark.parametrize(("name", "smiles", "formula", "cls", "sub"), GROUP_TYPES)
def test_builtin_group_types(name, smiles, formula, cls, sub):
    scheme = ClassScheme.builtin()
    label = scheme.classify(ev([hit(name, formula)], [smiles]))
    if cls is None:
        assert label is None
        return
    assert (label.category, label.subclass) == (cls, sub)
    assert label.source == f"rule:hydrocarbon-group-type/{cls}"


def test_builtin_vote():
    scheme = ClassScheme.builtin()
    # three alkylbenzene isomers outvote a phenol: right class, wrong isomer
    hits = [
        hit("a", "C9H12", 900),
        hit("b", "C9H12", 880),
        hit("c", "C7H8O", 870),
        hit("d", "C9H12", 860),
        hit("e", "C9H12", 650),
    ]
    smiles = ["CCCc1ccccc1", "Cc1ccc(C)c(C)c1", "Cc1ccc(O)cc1", "CC(C)c1ccccc1", "C"]
    label = scheme.classify(ev(hits, smiles))
    assert label.category == "monoaromatics"
    assert label.share == pytest.approx(
        (900 + 880 + 860) / (900 + 880 + 870 + 860), abs=1e-3
    )
    # no class reaches 60 %: no proposal
    mixed = [hit("a", "C9H12", 900), hit("b", "C6H6O", 880), hit("c", "C10H22", 870)]
    split = scheme.classify(ev(mixed, ["CCCc1ccccc1", "Oc1ccccc1", "CCCCCCCCCC"]))
    assert split is None
    # hits without a structure count half
    half = scheme.classify(
        ev([hit("a", "C9H12", 900), hit("b", "C10H22", 900)], ["CCCc1ccccc1", ""])
    )
    assert half.category == "monoaromatics"
    assert half.share == pytest.approx(900 / 1350, abs=1e-3)
    # hits below min_match do not vote
    assert scheme.classify(ev([hit("a", "C9H12", 600)], ["CCCc1ccccc1"])) is None
    # excluded is a label only
    assert "excluded" in scheme.names
    assert not scheme.get("excluded").rule.matches({"peak_id": 1}, [hit()])


def test_rule_criteria():
    spectrum = (np.array([91, 105, 120]), np.array([1000.0, 300.0, 120.0]))
    e = ev(
        [hit("Propylbenzene", "C9H12", 850, probability=70, delta=40)],
        ["CCCc1ccccc1"],
        spectrum,
    )

    def holds(*criteria):
        return Rule(list(criteria))._score(e) > 0

    assert holds({"smarts": "c1ccccc1"})
    assert not holds({"smarts": "C=C"})
    assert holds({"not_smarts": "[OX2H]"})
    assert holds({"aromatic_carbocycles": [1, 1]})
    assert not holds({"rings": [2, None]})
    assert holds({"formula": {"only": ["C", "H"], "c": [9, 9], "dbe": [4, 4]}})
    assert not holds({"formula": {"contains_any": ["O"]}})
    assert holds({"formula": {"contains": ["C", "H"]}})
    assert holds({"hit_name": "propyl"})
    assert not holds({"hit_name": "^butyl"})
    assert holds({"min_match": 850})
    assert not holds({"min_match": 851})
    assert holds({"min_probability": 50})
    assert not holds({"min_probability": 80})
    assert holds({"min_delta_match": 40})
    assert not holds({"min_delta_match": 41})
    assert holds({"ions": [{"mz": 105, "min": 25}, {"mz": 120}]})
    assert not holds({"ions": [{"mz": 105, "min": 31}]})
    assert not holds({"ions": [{"mz": 77}]})  # absent ion
    assert holds({"base_peak": [91, 105]})
    assert not holds({"base_peak": [105]})
    assert holds({"region": {"rt1_min": [5, 15], "rt2_s": [1, 3]}})
    assert not holds({"region": {"rt2_s": [3, 4]}})
    assert holds({"any": [{"smarts": "C=C"}, {"base_peak": [91]}]})
    assert holds(
        {"any": [[{"min_match": 900}], [{"min_match": 800}, {"hit_name": "benz"}]]}
    )
    assert not holds({"any": [{"smarts": "C=C"}, {"base_peak": [105]}]})
    # masks as regions
    e.in_masks = {"Aromatics", "abc123"}
    assert holds({"region": {"mask": "Aromatics"}})
    for bad in ({"nonsense": 1}, {"smarts": "c1(("}, {"any": []}, {"region": {"x": 1}}):
        with pytest.raises(ValueError, match="criteri|SMARTS|region"):
            Rule([bad])


def test_structure_free_hits():
    no_structure = ev([hit("x", "C9H12")], [""])
    # structure criteria do not apply; the others do
    assert Rule([{"smarts": "c1ccccc1"}, {"formula": {"c": [9, 9]}}])._score(
        no_structure
    )
    assert not Rule([{"smarts": "c1ccccc1"}])._score(no_structure)
    fallback = Rule([{"smarts": "c1ccccc1"}], fallback=[{"formula": {"dbe": [4, 6]}}])
    assert fallback.hit_matches(no_structure, 0) == 0.5
    # peak criteria alone work without hits
    peak_only = ev([], [], (np.array([91.0]), np.array([1.0])))
    assert Rule([{"base_peak": [91]}])._score(peak_only)
    assert not Rule([{"min_match": 0}])._score(peak_only)


def test_formula_helpers():
    assert parse_formula("C10H22") == {"C": 10, "H": 22}
    assert parse_formula("CH2Cl2") == {"C": 1, "H": 2, "Cl": 2}
    assert parse_formula("C6H18O3Si3")["Si"] == 3
    assert parse_formula("not a formula") == {}
    assert dbe({"C": 6, "H": 6}) == 4
    assert dbe({"C": 5, "H": 5, "N": 1}) == 4
    assert dbe({"H": 2, "O": 1}) is None


def test_scheme_order_round_trip_and_define(tmp_path):
    scheme = ClassScheme("project")
    scheme.define("aromatic", [{"smarts": "c1ccccc1"}], color="#ff0000")
    scheme.define("anything_c9", [{"formula": {"c": [9, 9]}}])
    e = ev([hit("x", "C9H12")], ["CCCc1ccccc1"])
    assert scheme.classify(e).category == "aromatic"  # first match wins
    scheme.define("aromatic", [{"smarts": "C=C"}])  # replaced in place, colour kept
    assert scheme.names == ["aromatic", "anything_c9"]
    assert scheme.color("aromatic") == "#ff0000"
    assert scheme.classify(e).category == "anything_c9"
    path = scheme.save(tmp_path / "p.classes.json")
    back = ClassScheme.load(path)
    assert back.to_dict() == scheme.to_dict()
    assert ClassScheme.builtin().copy().to_dict() == ClassScheme.builtin().to_dict()
    with pytest.raises(KeyError):
        ClassScheme.builtin("nonsense")


@pytest.fixture(scope="module")
def pair_peaks():
    """Peaks of the synthetic MS run (delay corrected), identified."""
    from pyGCxGC.ms.peaks import identify_peaks

    raw, _, truth = _synthetic_pair_cached()
    data = GCxGC_MS(raw, PAIR_MODULATION, shift=5, solvent_cutoff=0.9)
    lib = pair_library()
    table, hits = identify_peaks(data, detect_peaks(data, snr=10), lib)
    return data, add_label_columns(table), hits, lib, truth


def test_propose_on_the_synthetic_run(pair_peaks):
    data, table, hits, lib, truth = pair_peaks
    labels = propose(table, hits, libraries={"pair": lib})
    for row in table.itertuples():
        expected = truth["compounds"][row.name]
        assert labels[row.peak_id].category == expected["class"], row.name
        if expected["subclass"]:
            assert labels[row.peak_id].subclass == expected["subclass"]
    # user labels win
    labelled = table.copy()
    labelled.loc[0, ["category", "category_source"]] = ["unknown", "user"]
    new, report = apply_scheme(
        labelled, hits, ClassScheme.builtin(), libraries={"pair": lib}
    )
    assert new.loc[0, "category"] == "unknown"
    assert report["conflicts"][0]["peak_id"] == int(table.loc[0, "peak_id"])
    assert not new["reviewed"].any()  # built-in rules only propose
    new, _ = apply_scheme(
        labelled,
        hits,
        ClassScheme.builtin(),
        overwrite_user=True,
        libraries={"pair": lib},
    )
    assert new.loc[0, "category"] == truth["compounds"][table.loc[0, "name"]]["class"]


def test_review_cards(pair_peaks):
    import json

    data, table, hits, lib, _ = pair_peaks
    table = table.copy()
    table["proposal"] = "n_alkanes"
    cards = review_cards(data, table, hits)
    assert len(cards) == len(table)
    card = cards[0]
    assert card["id"] == int(table.loc[0, "peak_id"])
    assert card["hits"][0][0] == table.loc[0, "name"]
    assert len(card["hits"][0]) == 5  # name, formula, match, reverse, probability
    # n-octane: M+ (114) is 4 %, so the candidate is the highest ion above 5 %
    assert card["base"] == 57
    assert card["M"] == 99
    assert len(card["ions"]) <= 8
    assert card["prop"][0] == "n_alkanes"
    sizes = [len(json.dumps(c)) / 4 for c in cards]  # ~4 characters per token
    assert np.mean(sizes) < 110
    # no library numbers on the cards
    assert "nist" not in json.dumps(cards).lower()


# ------------------------------------------------------------------ masks from peaks
from pyGCxGC.masks import Grid, MaskSet
from pyGCxGC.regions.from_peaks import mask_from_peaks, mask_report
from pyGCxGC.regions.grade import MaskFacts, RunFacts, grade


def labelled(pair_peaks, reviewed=True):
    data, table, hits, lib, truth = pair_peaks
    table = table.copy()
    table["category"] = [truth["compounds"][n]["class"] for n in table["name"]]
    table["category_source"] = "agent"
    table["reviewed"] = reviewed
    return data, table, truth


@pytest.mark.parametrize("method", ["band", "hull"])
def test_mask_from_peaks(pair_peaks, method):
    data, table, truth = labelled(pair_peaks)
    grid = Grid.from_chromatogram(data)
    mono = table[table["category"] == "monoaromatics"]
    mask = mask_from_peaks(mono, grid, method, category="monoaromatics")
    assert mask.category == "monoaromatics"
    assert mask.name == "monoaromatics"
    assert len(mask.operations) == 1
    assert len(mask.operations[0].shape.vertices) <= 60
    assert mask.meta["method"] == method
    assert mask.meta["margin_rt2_s"] >= 2 * grid.sampling_interval
    assert mask.meta["margin_rt1_min"] == pytest.approx(2 * PAIR_MODULATION / 60)
    report = mask_report(mask, table, grid)
    assert report["coverage"]["volume_share"] >= 0.95
    assert report["coverage"]["n_inside"] == len(mono)
    assert report["sources"]["agent"] == 1.0
    assert report["neighbourhood"]["n_unreviewed"] == 0
    assert not report["wrap_around"]
    inside = {i["category"] for i in report["intruders"]}
    assert "n_alkanes" not in inside  # never the alkane line
    if method == "band":
        assert report["purity"] == 1.0
    # masks are ordinary polygons: they rasterise on another grid
    other = Grid(
        n_rt2=300, n_mod=100, rt1_start=0.0, modulation_time=6.0, sampling_interval=0.02
    )
    assert mask.rasterize(other).any()


def test_mask_report_flags(pair_peaks):
    data, table, _ = labelled(pair_peaks, reviewed=False)
    grid = Grid.from_chromatogram(data)
    mono = table[table["category"] == "monoaromatics"]
    # a mask too wide in RT2 takes the phenol and the diaromatics in
    mask = mask_from_peaks(
        mono, grid, "hull", margin_rt2_s=2.0, category="monoaromatics"
    )
    report = mask_report(mask, table, grid)
    assert {i["category"] for i in report["intruders"]} >= {"oxygenates"}
    assert report["purity"] < 1
    assert report["neighbourhood"]["n_unreviewed"] >= len(mono)
    assert any("not reviewed" in w for w in report["warnings"])
    # a class at both ends of the modulation wraps around
    wrapped = table.copy()
    wrapped.loc[wrapped.index[:2], "category"] = "edge"
    wrapped.loc[wrapped.index[0], "rt2_s"] = 0.1
    wrapped.loc[wrapped.index[1], "rt2_s"] = 5.9
    edge = mask_from_peaks(wrapped[wrapped["category"] == "edge"], grid, "hull")
    assert mask_report(edge, wrapped, grid, "edge")["wrap_around"]
    with pytest.raises(ValueError, match="No peaks"):
        mask_from_peaks(table.iloc[:0], grid)


def test_mask_meta_round_trip():
    masks = MaskSet()
    m = masks.new_mask("A")
    m.add_rect((1, 2), (1, 2))
    m.meta = {"source": "peaks", "margin_rt2_s": 0.3}
    back = MaskSet.from_dict(masks.to_dict())
    assert back["A"].meta == m.meta
    assert back["A"].fingerprint() == m.fingerprint()
    plain = MaskSet()
    plain.new_mask("B")
    assert "meta" not in plain.to_dict()["masks"][0]  # files stay as they were
    m.add_rect((3, 4), (1, 2))
    assert back["A"].fingerprint() != m.fingerprint()


# ------------------------------------------------------------------ grade
def good_facts():
    run = RunFacts("FID", "fid", "FID", 0.9, 0)
    mask = MaskFacts(
        "Monoaromatics",
        "monoaromatics",
        peaks_document="MS",
        n_near=12,
        copied_from="MS",
        delay={"aligned": True, "rt2_offset_s": 0.0, "current": True},
    )
    return run, [mask]


def test_grade_reportable():
    run, masks = good_facts()
    result = grade(run, masks)
    assert result["grade"] == "reportable"
    assert result["failed"] == []
    assert all(c["ok"] for c in result["criteria"])


@pytest.mark.parametrize(
    ("criterion", "change"),
    [
        ("neighbourhood_reviewed", lambda r, m: setattr(m, "n_unreviewed", 3)),
        ("neighbourhood_reviewed", lambda r, m: setattr(m, "peaks_document", None)),
        ("delay_corrected", lambda r, m: setattr(m, "delay", None)),
        ("delay_corrected", lambda r, m: m.delay.update(aligned=False)),
        ("delay_corrected", lambda r, m: m.delay.update(current=False)),
        ("no_conflicts", lambda r, m: setattr(r, "n_conflicts", 2)),
        ("copied_masks_unchanged", lambda r, m: setattr(m, "changes", ["edited"])),
        ("stated_inputs", lambda r, m: setattr(m, "peak_table_current", False)),
        ("stated_inputs", lambda r, m: setattr(m, "libraries_known", False)),
        ("stated_inputs", lambda r, m: setattr(r, "signal", "")),
        ("solvent_cutoff_set", lambda r, m: setattr(r, "solvent_cutoff", None)),
    ],
)
def test_grade_each_criterion_alone(criterion, change):
    run, masks = good_facts()
    change(run, masks[0])
    result = grade(run, masks)
    assert result["grade"] == "exploratory"
    assert [f["criterion"] for f in result["failed"]] == [criterion]
    assert result["failed"][0]["messages"]


def test_grade_ignores_exclusion_masks():
    run, masks = good_facts()
    masks.append(MaskFacts("Bleed", "excluded"))  # no peaks behind it: fine
    assert grade(run, masks)["grade"] == "reportable"
    assert grade(run, masks[1:])["grade"] == "exploratory"  # no class masks


# ------------------------------------------------------------------ render
def test_render(pair_peaks):
    from pyGCxGC.regions.classes import scheme_colors
    from pyGCxGC.render import render_chromatogram

    PIL = pytest.importorskip("PIL.Image")
    data, table, _ = labelled(pair_peaks)
    masks = MaskSet()
    mono = mask_from_peaks(
        table[table["category"] == "monoaromatics"],
        data,
        category="monoaromatics",
        name="Monoaromatics",
    )
    mono.color = "#ff00ff"
    masks.append(mono)
    png, caption = render_chromatogram(
        data,
        masks=masks,
        peaks=table,
        category_colors=scheme_colors(),
        peak_labels="id",
        signal="TIC",
        width=800,
        height=500,
    )
    import io

    image = np.asarray(PIL.open(io.BytesIO(png)).convert("RGB"))
    assert image.shape == (500, 800, 3)
    assert image.std() > 10  # not blank
    magenta = (image[..., 0] > 200) & (image[..., 1] < 80) & (image[..., 2] > 200)
    assert magenta.sum() > 100  # the mask outline
    assert caption["masks"] == [
        {"name": "Monoaromatics", "color": "#ff00ff", "category": "monoaromatics"}
    ]
    assert caption["peaks"]["monoaromatics"] == 11
    assert len(caption["labelled"]) == len(table)
    assert "Monoaromatics (#ff00ff)" in caption["text"]
    assert "monoaromatics 11" in caption["text"]
    again, _ = render_chromatogram(
        data,
        masks=masks,
        peaks=table,
        category_colors=scheme_colors(),
        peak_labels="id",
        signal="TIC",
        width=800,
        height=500,
    )
    assert again == png  # deterministic
    # zoom and limits
    _, zoom = render_chromatogram(
        data, rt1_min=(2, 4), rt2_s=(1, 3), scale="log", peaks=table
    )
    assert zoom["rt1_min"] == [2, 4]
    assert zoom["scale"] == "log"
    assert sum(zoom["peaks"].values()) < len(table)
    big, cap = render_chromatogram(data, width=4000, height=1000)
    assert cap["size"] == [2048, 512]
    with pytest.raises(ValueError, match="scale"):
        render_chromatogram(data, scale="cubic")
    with pytest.raises(ValueError, match="outside"):
        render_chromatogram(data, rt1_min=(50, 60))


# ------------------------------------------------------------------ local data
LOCAL_PAIR = Path(__file__).parents[1] / "local_data" / "Data"


@pytest.mark.skipif(
    not (LOCAL_PAIR / "Clean PP_016_MS.qgd").exists(), reason="local data absent"
)
def test_local_pair_delay_and_no_solvent():
    from pyGCxGC.ms.io import read_ms_file

    ms = GCxGC_MS(read_ms_file(LOCAL_PAIR / "Clean PP_016_MS.qgd"), 20.0)
    fid = gcgc.parse_2D_chromatogram(
        LOCAL_PAIR / "CleanPP_016 _FID.gcd", modulation_time=20
    )
    est = estimate_delay(ms, fid)
    assert est.delay_s == pytest.approx(0.5, abs=0.1)  # spec WF-2
    assert est.suggested_shift == 5
    # the run has no solvent peak: no suggestion (docs/research/delay-and-solvent.md)
    assert suggest_solvent_cutoff(ms) is None
    raw = gcgc.read_chromatogram(LOCAL_PAIR / "CleanPP_016 _FID.gcd")
    assert (
        suggest_solvent_cutoff(
            times=raw["Ret.Time[s]"],
            signal=raw["Absolute Intensity"],
            modulation_time=20,
        )
        is None
    )
