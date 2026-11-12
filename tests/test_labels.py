"""
Session methods of spec mcp.md milestone 1 on the synthetic GCxGC pair:
labels and their sources, review, rules, masks from peaks, the delay, the
grade, rendering and projects.
"""

from __future__ import annotations

import json

import numpy as np
import pytest
from synthetic import (
    PAIR_MODULATION,
    _synthetic_pair_cached,
    pair_library,
    write_andi,
    write_fid_csv,
)

import pyGCxGC as gcgc
from pyGCxGC.app.session import InvalidRequest, Session

pytest.importorskip("rdkit", reason="the class rules need RDKit")


@pytest.fixture(scope="module")
def pair_files(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("pair")
    raw, frame, truth = _synthetic_pair_cached()
    ms = write_andi(raw, tmp / "sample_MS.cdf")
    fid = write_fid_csv(frame, tmp / "sample_FID.csv")
    lib = pair_library().save(tmp / "pair.npz")
    return ms, fid, lib, truth


@pytest.fixture
def session(tmp_path):
    s = Session(settings_path=tmp_path / "app.json", cache_dir=tmp_path / "cache")
    yield s
    s.close()


@pytest.fixture
def calibrated(monkeypatch):
    import pyGCxGC.ms.search as search_mod

    cal = {"temperature": 10.0, "mz_power": 0.5, "int_power": 0.5}
    monkeypatch.setattr(search_mod, "match_calibration", lambda: cal)
    return cal


def open_pair(session, pair_files, correct_delay=True, cutoff=True):
    """The workflow of spec §11 up to the peak table (steps 2-7)."""
    ms_path, fid_path, lib, _ = pair_files
    ms = session.wait(session.open_document(ms_path, modulation_time=PAIR_MODULATION))[
        "document_id"
    ]
    fid = session.wait(
        session.open_document(fid_path, modulation_time=PAIR_MODULATION)
    )["document_id"]
    if cutoff:
        for doc in (ms, fid):
            suggestion = session.describe(doc)["solvent_cutoff_suggestion"]
            session.wait(session.update_document(doc, solvent_cutoff=suggestion))
    if correct_delay:
        delay = session.estimate_delay(ms, fid)
        session.wait(session.update_document(ms, shift=delay["suggested_shift"]))
    session.wait(session.add_library(lib))
    session.wait(session.detect_peaks(ms, snr=10))
    return ms, fid


def truth_labels(session, ms, truth, reason="from the synthetic truth"):
    table, _ = session.peaks(ms)
    unknown = {"class": "unknown", "subclass": ""}  # solvent peaks without a cut-off
    return [
        {
            "peak_id": int(r.peak_id),
            "category": truth["compounds"].get(r.name, unknown)["class"],
            "subclass": truth["compounds"].get(r.name, unknown)["subclass"],
            "confidence": "high",
            "reason": reason,
        }
        for r in table.itertuples()
    ]


# ------------------------------------------------------------------ workflow
def test_monoaromatics_workflow(session, pair_files, tmp_path):
    _, _, _, truth = pair_files
    ms, fid = open_pair(session, pair_files)
    info = session.describe(ms)
    assert info["shift"] == 5
    assert info["solvent_cutoff"] == pytest.approx(0.97, abs=0.05)
    table, peaks_info = session.peaks(ms)
    assert len(table) == len(truth["compounds"])
    assert {"category", "subclass", "reviewed", "snr", "delta_match"} <= set(table)

    proposals = session.wait(session.propose_classes(ms))
    assert proposals["classes"]["monoaromatics"]["n"] == 11
    assert proposals["n_without_proposal"] == 0
    status = session.review_status(ms)["status"]
    assert status["proposed"]["n"] == len(table)
    assert status["reviewed"]["n"] == 0

    # review every peak, batch by batch along RT1
    seen, cursor = [], None
    while True:
        batch = session.review_batch(ms, batch=10, cursor=cursor, status="all")
        seen += [c["id"] for c in batch["cards"]]
        cursor = batch["next_cursor"]
        if cursor is None:
            break
    assert sorted(seen) == sorted(table["peak_id"])
    assert len(seen) == len(set(seen))
    result = session.set_categories(
        ms, truth_labels(session, ms, truth), channel="agent"
    )
    assert result["n_set"] == len(table)
    assert not result["rejected"]
    assert session.review_status(ms)["status"]["reviewed"]["n"] == len(table)

    made = session.mask_from_peaks(ms, category="monoaromatics")
    assert made["report"]["coverage"]["volume_share"] >= 0.95
    assert made["report"]["neighbourhood"]["n_unreviewed"] == 0
    mask = session.masks(ms).get(made["mask_id"])
    assert mask.category == "monoaromatics"
    assert mask.color == session.class_color("monoaromatics")

    session.import_masks(fid, from_document=ms)
    compat = session.mask_compatibility(ms, fid)
    assert compat["delay"]["aligned"]
    assert compat["masks"][0]["outside_fraction"] == 0
    assert compat["masks"][0]["pixels_target"] > compat["masks"][0]["pixels_source"]

    report = session.integration_report(fid)
    assert report["grade"] == "reportable", report["failed"]
    row = report["masks"][0]
    expected = truth["class_volume"]["monoaromatics"] / sum(
        truth["class_volume"].values()
    )
    assert row["percent"] / 100 == pytest.approx(expected, rel=0.02)
    assert row["percent_of_included"] == row["percent"]  # nothing excluded
    assert report["report"]["masks"][0]["libraries"] == ["pair"]
    assert report["report"]["masks"][0]["label_sources"]["agent"] == 1.0

    # bit for bit as integrate_masks with the exported mask
    folder = tmp_path / "tiffs"
    session.export(fid, "masks", path=folder)
    chrom = session.document(fid).chrom
    volumes = gcgc.integrate_masks(chrom.chrom_2D, masks=str(folder) + "/")
    assert float(np.asarray(volumes[mask.name]).ravel()[0]) == row["volume"]

    # an exclusion mask changes the second denominator only
    bleed = session.create_mask(fid, "Bleed", category="excluded")
    session.add_shape(fid, bleed.id, {"type": "rect", "rt1": [8.5, 9.9], "rt2": [0, 6]})
    excluded = session.integration_report(fid)
    assert excluded["excluded"] == ["Bleed"]
    assert excluded["grade"] == "reportable"
    mono = excluded["masks"][0]
    assert mono["percent"] == row["percent"]
    assert mono["percent_of_included"] > mono["percent"]

    # changing the MS processing invalidates the delay estimate
    session.wait(session.update_document(ms, shift=0))
    later = session.integration_report(fid)
    assert later["grade"] == "exploratory"
    assert "delay_corrected" in {f["criterion"] for f in later["failed"]}


def test_without_delay_correction_exploratory(session, pair_files):
    _, _, _, truth = pair_files
    ms, fid = open_pair(session, pair_files, correct_delay=False)
    session.set_categories(ms, truth_labels(session, ms, truth), channel="agent")
    session.mask_from_peaks(ms, category="monoaromatics")
    session.import_masks(fid, from_document=ms)
    compat = session.mask_compatibility(ms, fid)
    assert not compat["delay"]["aligned"]
    assert compat["delay"]["suggested_shift"] == 5
    assert any("half a pixel" in w for w in compat["warnings"])
    report = session.integration_report(fid)
    assert report["grade"] == "exploratory"
    assert [f["criterion"] for f in report["failed"]] == ["delay_corrected"]


def test_grade_criteria_through_the_session(session, pair_files):
    _, _, _, truth = pair_files
    ms, fid = open_pair(session, pair_files, cutoff=False)
    session.set_categories(ms, truth_labels(session, ms, truth), channel="agent")
    made = session.mask_from_peaks(ms, category="monoaromatics")
    report = session.integration_report(ms)  # on the MS run itself: no copy
    assert {f["criterion"] for f in report["failed"]} == {"solvent_cutoff_set"}
    session.wait(session.update_document(ms, solvent_cutoff=0))
    # the shift change drops the peaks' positions: the table is stale
    stale = session.integration_report(ms)
    assert "stated_inputs" in {f["criterion"] for f in stale["failed"]}
    session.wait(session.detect_peaks(ms, discard_labels=True))
    session.set_categories(ms, truth_labels(session, ms, truth), channel="agent")
    assert session.integration_report(ms)["grade"] == "reportable"
    # a copied mask changed on the target
    session.import_masks(fid, from_document=ms)
    session.mask_compatibility(ms, fid)
    session.wait(session.update_document(fid, solvent_cutoff=0))
    copied = session.masks(fid)[0]
    session.add_shape(fid, copied.id, {"type": "rect", "rt1": [1, 2], "rt2": [4, 5]})
    failed = {f["criterion"] for f in session.integration_report(fid)["failed"]}
    assert failed == {"copied_masks_unchanged"}
    # an unreviewed peak in the neighbourhood
    session.undo_masks(fid)
    table, _ = session.peaks(ms)
    toluene = int(table.loc[table["name"] == "Toluene", "peak_id"].iloc[0])
    session.set_categories(ms, [{"peak_id": toluene, "category": ""}])
    failed = {f["criterion"] for f in session.integration_report(fid)["failed"]}
    assert failed == {"neighbourhood_reviewed"}
    assert made["mask_id"] in [m.id for m in session.masks(ms)]


# ------------------------------------------------------------------ labels
def test_label_sources_and_conflicts(session, pair_files):
    ms, _ = open_pair(session, pair_files)
    table, _ = session.peaks(ms)
    first, second = (int(p) for p in table["peak_id"].iloc[:2])
    # Python and the app set user labels
    session.set_categories(ms, [{"peak_id": first, "category": "n_alkanes"}])
    row = session.peaks(ms)[0].set_index("peak_id").loc[first]
    assert (row.category_source, row.reviewed) == ("user", True)
    # agents cannot change them...
    result = session.set_categories(
        ms,
        [{"peak_id": first, "category": "unknown", "reason": "odd spectrum"}],
        channel="agent",
    )
    assert result["n_set"] == 0
    assert result["conflicts"][0]["user"] == "n_alkanes"
    assert len(session.review_status(ms)["conflicts"]) == 1
    # ...unless asked to (and then the conflict is gone)
    session.set_categories(
        ms,
        [{"peak_id": first, "category": "unknown", "reason": "odd spectrum"}],
        channel="agent",
        overwrite_user=True,
    )
    row = session.peaks(ms)[0].set_index("peak_id").loc[first]
    assert (row.category, row.category_source) == ("unknown", "agent")
    assert session.review_status(ms)["conflicts"] == []
    # agent labels: confidence and, where they differ from the proposal, a reason
    bad = session.set_categories(
        ms,
        [
            {"peak_id": second, "category": "x", "confidence": "certain"},
            {"peak_id": second, "category": "x"},
            {"peak_id": 999, "category": "x"},
        ],
        channel="agent",
    )
    assert [r["reason"][:10] for r in bad["rejected"]] == [
        "confidence",
        "a reason i",
        "no such pe",
    ]
    # as_user needs the setting
    dictated = [{"peak_id": second, "category": "isoalkanes", "reason": "user said"}]
    with pytest.raises(InvalidRequest, match="mcp.allow_user_labels"):
        session.set_categories(ms, dictated, channel="agent", as_user=True)
    session.update_settings({"mcp": {"allow_user_labels": True}})
    session.set_categories(ms, dictated, channel="agent", as_user=True)
    row = session.peaks(ms)[0].set_index("peak_id").loc[second]
    assert row.category_source == "user"
    with pytest.raises(InvalidRequest, match="200"):
        session.set_categories(ms, [{"peak_id": first, "category": "a"}] * 201)


def test_category_edit_is_a_user_edit(session, pair_files):
    ms, _ = open_pair(session, pair_files)
    table, _ = session.peaks(ms)
    pid = int(table["peak_id"].iloc[0])
    name = table["name"].iloc[0]
    session.edit_peak(ms, pid, category="mine")
    row = session.peaks(ms)[0].set_index("peak_id").loc[pid]
    assert (row.category, row.category_source, row.assigned_by) == (
        "mine",
        "user",
        "user",
    )
    # re-identification keeps the hit and the label (spec CLS-19)
    session.wait(session.reidentify_peaks(ms))
    row = session.peaks(ms)[0].set_index("peak_id").loc[pid]
    assert (row["name"], row.category, row.reviewed) == (name, "mine", True)
    # re-detection refuses to drop reviewed labels (spec CLS-3)
    with pytest.raises(InvalidRequest, match="1 reviewed labels"):
        session.detect_peaks(ms)
    session.wait(session.detect_peaks(ms, discard_labels=True))
    assert not session.peaks(ms)[0]["reviewed"].any()


def test_edit_peak_assigns_its_own_hits(session, pair_files):
    ms, _ = open_pair(session, pair_files)
    table, _ = session.peaks(ms)
    pid = int(table["peak_id"].iloc[1])
    second = session.peak_hits(ms, pid)["hits"][1]
    session.edit_peak(ms, pid, hit_rank=2)
    row = session.peaks(ms)[0].set_index("peak_id").loc[pid]
    assert row["name"] == second["name"]
    # the app's Hits panel assigns from the current selection
    with pytest.raises(InvalidRequest, match="current selection"):
        session.edit_peak(ms, pid, hit_rank=1, hits="selection")


def test_review_batch_filters_and_cursor(session, pair_files):
    _, _, _, truth = pair_files
    ms, _ = open_pair(session, pair_files)
    session.wait(session.propose_classes(ms))
    labels = {a["peak_id"]: a for a in truth_labels(session, ms, truth)}
    # labelling between batches does not skip peaks
    seen, cursor = [], None
    while True:
        batch = session.review_batch(ms, batch=5, cursor=cursor, order="volume")
        ids = [c["id"] for c in batch["cards"]]
        seen += ids
        session.set_categories(ms, [labels[i] for i in ids], channel="agent")
        cursor = batch["next_cursor"]
        if cursor is None:
            break
    assert sorted(seen) == sorted(labels)
    volumes = session.peaks(ms)[0].set_index("peak_id")["volume"]
    assert volumes.loc[seen[0]] == volumes.max()
    assert session.review_batch(ms)["cards"] == []  # all reviewed
    mono = session.review_batch(ms, status="all", category="monoaromatics")
    assert len(mono["cards"]) == 11
    near = session.mask_from_peaks(ms, category="monoaromatics")
    around = session.review_batch(ms, status="all", near_mask=near["mask_id"])
    assert {c.get("mask") for c in around["cards"]} <= {"in", "near"}
    assert sum(c.get("mask") == "in" for c in around["cards"]) == 11
    window = session.review_batch(ms, status="all", rt1_min=(1, 2.5), rt2_s=(1.5, 3))
    assert {c["id"] for c in window["cards"]} <= set(labels)
    with pytest.raises(InvalidRequest, match="cursor"):
        session.review_batch(ms, order="rt", cursor="volume|1|2|3")


def test_rules_define_and_apply(session, pair_files, tmp_path):
    ms, _ = open_pair(session, pair_files)
    defined = session.define_class(
        "alkylbenzenes",
        [
            {"formula": {"only": ["C", "H"], "dbe": [4, 4]}},
            {"base_peak": [91, 92, 105]},
        ],
        description="C,H only, DBE 4, tropylium base peak",
        color="#123456",
    )
    # the alkylbenzenes (hexylbenzene's base peak is 92), not indane, tetralin, styrene
    assert defined["matches"]["sample_MS.cdf"] == 8
    assert [s["name"] for s in session.class_schemes()] == [
        "hydrocarbon-group-type",
        "project",
    ]
    result = session.apply_rules(ms)
    assert result["labelled"] == {"alkylbenzenes": 8}
    table, _ = session.peaks(ms)
    rule = table[table["category"] == "alkylbenzenes"]
    assert set(rule["category_source"]) == {"rule:project/alkylbenzenes"}
    assert rule["reviewed"].all()  # project rules are a judgement
    with pytest.raises(InvalidRequest, match="project scheme"):
        session.define_class("x", [], scheme="hydrocarbon-group-type")
    with pytest.raises(InvalidRequest, match="Invalid class"):
        session.define_class("x", [{"nonsense": 1}])
    hits = session.peak_hits(ms, int(rule["peak_id"].iloc[0]))
    assert hits["hits"][0]["class"] == "monoaromatics"
    assert hits["hits"][0]["subclass"] == "alkylbenzenes"
    assert hits["hits"][0]["redistributable"] is False


def test_projects_keep_labels_classes_hits_and_delays(session, pair_files, tmp_path):
    _, _, _, truth = pair_files
    ms, fid = open_pair(session, pair_files)
    session.set_categories(ms, truth_labels(session, ms, truth), channel="agent")
    session.define_class("tropylium", [{"base_peak": [91]}])
    session.mask_from_peaks(ms, category="monoaromatics")
    session.import_masks(fid, from_document=ms)
    session.mask_compatibility(ms, fid)
    before = session.integration_report(fid)
    assert before["grade"] == "reportable"
    path = session.save_project(tmp_path / "study")["path"]
    classes = tmp_path / "study.classes.json"
    assert json.loads(classes.read_text())["classes"][0]["name"] == "tropylium"
    other = Session(settings_path=tmp_path / "o.json", cache_dir=tmp_path / "cache2")
    try:
        ms2, fid2 = other.wait(other.open_project(path), 60)["document_ids"]
        table, _ = other.peaks(ms2)
        assert table["reviewed"].all()
        assert set(table["category_source"]) == {"agent"}
        assert other.class_scheme.names == ["tropylium"]
        assert other.peak_hits(ms2, int(table["peak_id"].iloc[0]))["hits"]
        assert other.masks(fid2)[0].meta["copied_from"]["mask"] == "monoaromatics"
        after = other.integration_report(fid2)
        assert after["grade"] == "reportable", after["failed"]
        assert after["masks"][0]["volume"] == before["masks"][0]["volume"]
    finally:
        other.close()


def test_probability_columns(session, pair_files, calibrated, tmp_path):
    ms, _ = open_pair(session, pair_files)
    table, info = session.peaks(ms)
    assert table["probability"].notna().all()
    assert info["probability_note"] is None
    pid = int(table["peak_id"].iloc[0])
    hits = session.peak_hits(ms, pid)["hits"]
    assert hits[0]["probability"] == pytest.approx(
        table["probability"].iloc[0], abs=0.1
    )
    session.wait(session.reidentify_peaks(ms))
    assert session.peaks(ms)[0]["probability"].notna().all()
    path = session.save_project(tmp_path / "p")["path"]
    other = Session(settings_path=tmp_path / "o.json", cache_dir=tmp_path / "cache2")
    try:
        ms2, _ = other.wait(other.open_project(path), 60)["document_ids"]
        assert other.peaks(ms2)[0]["probability"].notna().all()
    finally:
        other.close()
    # other weights: no probability, and the note says why
    session.update_settings({"search": {"mz_power": 3.0, "int_power": 0.6}})
    session.wait(session.reidentify_peaks(ms))
    table, info = session.peaks(ms)
    assert table["probability"].isna().all()
    assert "calibrated" in info["probability_note"]
    selection = session.search(
        ms,
        rt2_index=int(table["rt2_index"].iloc[0]),
        mod_index=int(table["mod_index"].iloc[0]),
    )
    assert selection.info()["probability_note"]


# ------------------------------------------------------------------ describe, render
def test_describe_and_render(session, pair_files):
    ms_path, fid_path, _, _ = pair_files
    ms = session.wait(session.open_document(ms_path, modulation_time=PAIR_MODULATION))[
        "document_id"
    ]
    fid = session.wait(
        session.open_document(fid_path, modulation_time=PAIR_MODULATION)
    )["document_id"]
    d = session.describe(ms)
    assert d["kind"] == "ms"
    assert d["mass_range"] == [40, 200]
    assert d["solvent_cutoff"] == "not set"
    assert 0.6 < d["solvent_cutoff_suggestion"] < 1.2
    assert any("not set" in w for w in d["warnings"])
    assert any("injection artefact" in w for w in d["warnings"])
    assert "modulation_estimate" in d
    f = session.describe(fid)
    assert f["kind"] == "fid"
    assert f["modulation_time"] == PAIR_MODULATION
    assert 0.6 < f["solvent_cutoff_suggestion"] < 1.2
    session.wait(session.update_document(fid, solvent_cutoff=0))
    assert session.describe(fid)["solvent_cutoff"] == 0
    png, caption = session.render(ms, width=600, height=400, rt1_min=[1, 5])
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert caption["rt1_min"] == [1, 5]
    assert caption["signal"] == "TIC"
    png, caption = session.render(ms, signal="eic", mz=[91, 105], overlays=["masks"])
    assert caption["signal"].startswith("EIC m/z 91, 105")
    with pytest.raises(InvalidRequest):
        session.render(ms, overlays=["labels"])
    with pytest.raises(InvalidRequest, match="peak table"):
        session.review_batch(ms)
