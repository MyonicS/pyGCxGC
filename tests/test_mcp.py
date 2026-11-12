"""
The MCP server (spec mcp.md milestone 2): tools, guides and the prompt
through the SDK's in-memory client, the guards, the library redaction, the
scripted workflow on the synthetic pair (WF-1) and the stdio command.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from synthetic import (
    PAIR_MODULATION,
    _synthetic_pair_cached,
    pair_library,
    write_andi,
    write_fid_csv,
)

pytest.importorskip("mcp", reason="the MCP server needs the mcp extra")
pytest.importorskip("rdkit", reason="the class rules need RDKit")

import anyio
from mcp.client import Client

import pyGCxGC as gcgc
from pyGCxGC.app.session import Session
from pyGCxGC.mcp.guard import GuardError, Roots, redact_hit, redact_rows
from pyGCxGC.mcp.server import GUIDES, INSTRUCTIONS, build_server

NIST_BASE = 7340001  # NIST numbers of the test library (must never leak)
SPEC_TOOLS = {
    "session_info",
    "open_file",
    "describe_run",
    "set_processing",
    "set_signal",
    "activate",
    "close_document",
    "render",
    "spectrum",
    "detect_peaks",
    "peaks",
    "peak_hits",
    "edit_peak",
    "class_schemes",
    "define_class",
    "propose_classes",
    "review_peaks",
    "set_categories",
    "apply_rules",
    "review_status",
    "masks",
    "create_mask",
    "update_mask",
    "delete_mask",
    "add_shape",
    "delete_shape",
    "undo_masks",
    "mask_from_peaks",
    "estimate_delay",
    "copy_masks",
    "integrate",
    "add_library",
    "set_library_active",
    "export",
    "save_project",
    "wait_job",
    "cancel_job",
}


# ------------------------------------------------------------------ fixtures
@pytest.fixture(scope="module")
def pair_files(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("pair")
    raw, frame, truth = _synthetic_pair_cached()
    ms = write_andi(raw, tmp / "sample_MS.cdf")
    fid = write_fid_csv(frame, tmp / "sample_FID.csv")
    lib = pair_library()
    lib.nist_no[:] = NIST_BASE + np.arange(len(lib))  # looks like NIST
    path = lib.save(tmp / "pair.npz")
    return {
        "ms": ms,
        "fid": fid,
        "lib": path,
        "truth": truth,
        "dir": tmp,
        "library": lib,
    }


@pytest.fixture
def session(tmp_path):
    s = Session(settings_path=tmp_path / "app.json", cache_dir=tmp_path / "cache")
    yield s
    s.close()


@pytest.fixture
def roots(tmp_path, pair_files):
    return Roots([pair_files["dir"]], tmp_path / "out", cwd=tmp_path)


class Agent:
    """Calls tools like an agent and keeps every result (for the leak test)."""

    def __init__(self, client: Client):
        self.client = client
        self.log: list[tuple[str, object]] = []

    async def __call__(self, name: str, /, **args):
        result = await self.client.call_tool(name, args)
        self.log.append((name, result))
        if result.is_error:
            raise AssertionError(f"{name}: {result.content[0].text}")
        return result.structured_content

    async def error(self, name: str, /, **args) -> str:
        result = await self.client.call_tool(name, args)
        self.log.append((name, result))
        assert result.is_error, f"{name} should fail"
        return result.content[0].text

    def texts(self) -> list[str]:
        out = []
        for _, r in self.log:
            out.extend(
                b.text for b in r.content if getattr(b, "text", None) is not None
            )
            if r.structured_content is not None:
                out.append(json.dumps(r.structured_content))
        return out


def run(session, roots, scenario):
    app = build_server(session, roots)

    async def main():
        async with Client(app.mcp) as client:
            return await scenario(Agent(client), app)

    try:
        return anyio.run(main)
    finally:
        app.gui.close()


# ------------------------------------------------------------------ catalogue
def test_tools_schemas_and_descriptions(session, roots):
    async def scenario(agent, app):
        return (await agent.client.list_tools()).tools

    tools = run(session, roots, scenario)
    assert {t.name for t in tools} == SPEC_TOOLS
    by_name = {t.name: t for t in tools}
    for t in tools:
        assert t.output_schema is not None
        assert t.output_schema["type"] == "object"
        text = " ".join(t.description.split())
        first = re.split(r"(?<=\.)\s", text)[0]
        assert len(first) <= 120, (t.name, first)  # TOOL-8
        params = set(t.input_schema.get("properties", {}))
        if any(p.endswith("_min") for p in params):
            assert "min" in first, (t.name, first)
        if any(p.endswith("_s") and p != "wait_s" for p in params):
            assert re.search(r"\bs\b|\(s\)", first), (t.name, first)
    for name in ("session_info", "describe_run", "peaks", "render", "integrate"):
        assert by_name[name].annotations.read_only_hint
    for name in (
        "close_document",
        "delete_mask",
        "delete_shape",
        "copy_masks",
        "detect_peaks",
    ):
        assert by_name[name].annotations.destructive_hint  # TOOL-6
    assert by_name["add_library"].annotations.open_world_hint
    assert not by_name["create_mask"].annotations.destructive_hint


def test_guides_prompt_and_instructions(session, roots):
    async def scenario(agent, app):
        listed = (await agent.client.list_resources()).resources
        texts = {}
        for r in listed:
            content = await agent.client.read_resource(str(r.uri))
            texts[str(r.uri)] = content.contents[0].text
        prompts = (await agent.client.list_prompts()).prompts
        prompt = await agent.client.get_prompt(
            "group-type-area",
            {
                "fid_file": "a_FID.gcd",
                "ms_file": "a_MS.qgd",
                "compound_class": "diaromatics",
            },
        )
        return texts, prompts, prompt, app.mcp.instructions

    texts, prompts, prompt, instructions = run(session, roots, scenario)
    assert set(texts) == {f"gcxgc://guides/{n}" for n in GUIDES}
    assert all(len(t) > 500 for t in texts.values())
    assert [p.name for p in prompts] == ["group-type-area"]
    message = prompt.messages[0].content.text
    assert "a_FID.gcd" in message
    assert "diaromatics" in message
    assert "gcxgc://guides/group-type" in message
    assert instructions == INSTRUCTIONS
    assert len(INSTRUCTIONS) < 4000
    assert len(INSTRUCTIONS.split()) < 400
    for guide in GUIDES:
        assert f"gcxgc://guides/{guide}" in INSTRUCTIONS


# ------------------------------------------------------------------ workflow
def truth_assignments(rows, truth):
    unknown = {"class": "unknown", "subclass": ""}
    return [
        {
            "peak_id": r["peak_id"],
            "category": truth["compounds"].get(r["name"], unknown)["class"],
            "subclass": truth["compounds"].get(r["name"], unknown)["subclass"],
            "confidence": "high",
            "reason": "synthetic ground truth",
        }
        for r in rows
    ]


async def workflow(agent, app, files, correct_delay=True):
    """Spec §11 through tool calls only (WF-1); returns the integration."""
    ms = await agent(
        "open_file", path=str(files["ms"]), modulation_s=PAIR_MODULATION, wait_s=60
    )
    fid = await agent(
        "open_file", path=str(files["fid"]), modulation_s=PAIR_MODULATION, wait_s=60
    )
    assert ms["kind"] == "ms"
    assert fid["kind"] == "fid"
    assert ms["rt1_min"] == pytest.approx(fid["rt1_min"], abs=0.11)  # ALIGN-1
    for run_ in (ms, fid):
        assert run_["solvent_cutoff"] == "not set"
        assert run_["solvent_cutoff_suggestion"] is not None  # SOL-3
        done = await agent(
            "set_processing",
            document=run_["id"],
            solvent_cutoff_min=run_["solvent_cutoff_suggestion"],
        )
        assert done["solvent_cutoff"] == run_["solvent_cutoff_suggestion"]
    if correct_delay:
        delay = await agent("estimate_delay", source="sample_MS", target="sample_FID")
        assert delay["delay_s"] == pytest.approx(0.5, abs=0.1)  # one MS scan
        await agent(
            "set_processing", document="sample_MS", shift_scans=delay["suggested_shift"]
        )
        again = await agent("estimate_delay", source="sample_MS", target="sample_FID")
        assert again["aligned"]
    lib = await agent("add_library", path=str(files["lib"]))
    assert lib["origin"] == "nist"
    assert not lib["redistributable"]
    found = await agent("render", document="sample_MS")
    assert found["rt1_min"]
    detected = await agent("detect_peaks", document="sample_MS", snr=10)
    assert detected["n_peaks"] == len(files["truth"]["compounds"])
    assert detected["libraries"] == ["pair"]
    proposed = await agent("propose_classes", document="sample_MS")
    assert proposed["classes"]["monoaromatics"]["n"] == 11

    seen, cursor = [], None
    while True:
        args = {"document": "sample_MS", "status": "all", "batch": 15}
        if cursor:
            args["cursor"] = cursor
        batch = await agent("review_peaks", **args)
        assert "written_to" not in batch
        seen += [c["id"] for c in batch["cards"]]
        cursor = batch["next_cursor"]
        if cursor is None:
            break
    assert len(seen) == len(set(seen)) == detected["n_peaks"]
    rows = (
        await agent(
            "peaks", document="sample_MS", limit=200, columns=["peak_id", "name"]
        )
    )["rows"]
    labelled = await agent(
        "set_categories",
        document="sample_MS",
        assignments=truth_assignments(rows, files["truth"]),
    )
    assert labelled["n_set"] == len(rows)
    assert not labelled["rejected"]
    status = await agent("review_status", document="sample_MS")
    assert status["status"]["reviewed"]["n"] == len(rows)
    assert status["sources"]["agent"]["n"] == len(rows)  # CLS-18

    made = await agent(
        "mask_from_peaks", document="sample_MS", category="monoaromatics"
    )
    assert made["report"]["coverage"]["volume_share"] >= 0.95
    assert made["report"]["neighbourhood"]["n_unreviewed"] == 0
    copied = await agent("copy_masks", source="sample_MS", target="sample_FID")
    assert len(copied["new_mask_ids"]) == 1
    assert copied["compatibility"]["masks"][0]["outside_fraction"] == 0
    shown = await agent("render", document="sample_FID", overlays=["masks"])
    assert "monoaromatics" in json.dumps(shown)
    result = await agent("integrate", document="sample_FID", out="integration.csv")
    exported = await agent("export", document="sample_FID", what="masks")
    await agent("save_project", filename="monoaromatics")
    return result, exported


def test_workflow_reportable(session, roots, pair_files):
    """WF-1: reportable, bit-identical to integrate_masks, within 2 % of truth."""

    async def scenario(agent, app):
        result, exported = await workflow(agent, app, pair_files)
        return result, exported, agent

    result, exported, agent = run(session, roots, scenario)
    assert result["grade"] == "reportable", result["failed"]
    row = result["masks"][0]
    truth = pair_files["truth"]["class_volume"]
    expected = truth["monoaromatics"] / sum(truth.values())
    assert row["percent"] / 100 == pytest.approx(expected, rel=0.02)
    assert row["percent_of_included"] == row["percent"]
    assert result["signal"] == "FID"
    report = result["report"]["masks"][0]
    assert report["libraries"] == ["pair"]
    assert report["label_sources"]["agent"] == 1.0

    # bit for bit as integrate_masks with the exported TIFF
    fid = next(d for d in session.documents.values() if d.kind == "fid")
    volumes = gcgc.integrate_masks(fid.chrom.chrom_2D, masks=exported["path"] + "/")
    assert float(np.asarray(volumes[row["name"]]).ravel()[0]) == row["volume"]

    out = roots.output
    assert (out / "integration.csv").read_text().startswith("document,signal")
    assert (out / "monoaromatics.gcxgc").is_file()
    assert session.unsaved_changes(fid.id) == []

    # SAFE-3: nothing of the (NIST-like) library reached the agent
    lib = pair_files["library"]
    smiles = [s for s in lib.smiles if len(s) >= 6]
    numbers = [str(n) for n in lib.nist_no]
    for text in agent.texts():
        assert not any(s in text for s in smiles)
        assert not any(n in text for n in numbers)
        assert '"nist_no"' not in text
        assert '"smiles"' not in text


def test_workflow_without_delay_correction(session, roots, pair_files):
    async def scenario(agent, app):
        result, _ = await workflow(agent, app, pair_files, correct_delay=False)
        return result

    result = run(session, roots, scenario)
    assert result["grade"] == "exploratory"
    assert [f["criterion"] for f in result["failed"]] == ["delay_corrected"]


# ------------------------------------------------------------------ tools in detail
def test_inspection_tools_are_redacted(session, roots, pair_files):
    """spectrum, peak_hits, peaks and the peak export never show NIST numbers."""

    async def scenario(agent, app):
        await agent(
            "open_file", path=str(pair_files["ms"]), modulation_s=PAIR_MODULATION
        )
        await agent("add_library", path=str(pair_files["lib"]))
        await agent("detect_peaks", snr=10)
        rows = (await agent("peaks", sort_by="volume", limit=3))["rows"]
        pid = rows[0]["peak_id"]
        hits = await agent("peak_hits", peak_id=pid, n=3)
        spec = await agent("spectrum", peak_id=pid, n_hits=3)
        point = await agent("spectrum", rt1_min=5.0, rt2_s=2.0, search=False)
        every = await agent("peaks", columns=["peak_id", "name", "nist_no", "library"])
        exported = await agent("export", what="peaks")
        schemes = await agent("class_schemes")
        cards = await agent("review_peaks", batch=5)
        shares = await agent("peaks", limit=200, columns=["peak_id", "volume_percent"])
        assert sum(r["volume_percent"] for r in shares["rows"]) == pytest.approx(
            100, abs=0.1
        )
        assert shares["rows"][0]["volume_percent"] == rows[0]["volume_percent"]
        off = await agent("set_library_active", library="pair", active=False)
        assert [lib["active"] for lib in off["libraries"]] == [False]
        unsearched = await agent("spectrum", peak_id=pid)
        assert unsearched["note"].startswith("No active library")
        return hits, spec, point, every, exported, schemes, cards, agent

    hits, spec, point, every, exported, schemes, cards, agent = run(
        session, roots, scenario
    )
    assert hits["hits"][0]["class"] is not None
    assert set(hits["hits"][0]) <= {
        "rank",
        "name",
        "formula",
        "mw",
        "cas",
        "match",
        "reverse_match",
        "delta_match",
        "probability",
        "library",
        "class",
        "subclass",
        "redistributable",
    }
    assert spec["ions"][0][1] == 100.0
    assert spec["base_peak"] == spec["ions"][0][0]
    assert spec["hits"][0]["name"] == hits["hits"][0]["name"]
    assert "hits" not in point
    assert all("nist_no" not in r for r in every["rows"])
    csv_text = Path(exported["path"]).read_text()
    assert "nist_no" not in csv_text.splitlines()[0]
    assert cards["cards"]
    numbers = [str(n) for n in pair_files["library"].nist_no]
    for text in agent.texts():
        assert not any(n in text for n in numbers)


def test_errors_say_what_to_do(session, roots, pair_files, tmp_path):
    outside = tmp_path.parent / "elsewhere.csv"
    outside.write_text("x")
    link = pair_files["dir"] / "link_to_outside.csv"
    if not link.exists():
        link.symlink_to(outside)

    async def scenario(agent, app):
        errors = {}
        errors["no_doc"] = await agent.error("describe_run")
        fid = await agent(
            "open_file", path=str(pair_files["fid"]), modulation_s=PAIR_MODULATION
        )
        errors["unknown"] = await agent.error("describe_run", document="nope")
        errors["kind"] = await agent.error("detect_peaks", document=fid["id"])
        errors["outside"] = await agent.error("open_file", path=str(outside))
        errors["symlink"] = await agent.error("open_file", path=str(link))
        errors["traversal"] = await agent.error(
            "export", what="integration", filename="../x.csv"
        )
        errors["absolute"] = await agent.error(
            "export", what="integration", filename=str(tmp_path / "abs.csv")
        )
        await agent("export", what="integration", filename="i.csv")
        errors["overwrite"] = await agent.error(
            "export", what="integration", filename="i.csv"
        )
        await agent("export", what="integration", filename="i.csv", overwrite=True)
        mask = await agent("create_mask", name="Band", category="monoaromatics")
        errors["shape"] = await agent.error(
            "add_shape", mask="Band", shape={"type": "rect", "rt1": [1, 2]}
        )
        await agent(
            "add_shape",
            mask=mask["mask_id"],
            shape={"type": "rect", "rt1_min": [2, 4], "rt2_s": [1, 3]},
        )
        errors["mask"] = await agent.error("delete_mask", mask="Other")
        errors["close"] = await agent.error("close_document", document="sample_FID")
        ms = await agent(
            "open_file", path=str(pair_files["ms"]), modulation_s=PAIR_MODULATION
        )
        errors["no_peaks"] = await agent.error("peaks", document=ms["id"])
        errors["as_user"] = await agent.error(
            "set_categories",
            document=ms["id"],
            assignments=[{"peak_id": 0, "category": "x"}],
            as_user=True,
        )
        closed = await agent("close_document", document="sample_FID", force=True)
        return errors, closed

    errors, closed = run(session, roots, scenario)
    assert "use open_file first" in errors["no_doc"]
    assert "open documents: sample_FID.csv" in errors["unknown"]
    assert "needs a MS document" in errors["kind"]
    for key in ("outside", "symlink"):
        assert "outside the folders this server may read" in errors[key]
        assert "--allow-read" in errors[key]
    assert "without '..'" in errors["traversal"]
    assert "without '..'" in errors["absolute"]
    assert "overwrite=true" in errors["overwrite"]
    assert '"rt1_min"' in errors["shape"]
    assert "masks: Band" in errors["mask"]
    assert "1 masks" in errors["close"]
    assert "force=true" in errors["close"]
    assert "run detect_peaks first" in errors["no_peaks"]
    assert "mcp.allow_user_labels" in errors["as_user"]
    assert closed["documents"] == ["sample_MS.cdf"]
    assert not (tmp_path / "abs.csv").exists()
    assert not (roots.output.parent / "x.csv").exists()


def test_labels_and_refusals(session, roots, pair_files):
    """Agent labels, as_user with the setting, user labels win, CLS-3 refusal."""
    session.update_settings({"mcp": {"allow_user_labels": True}})

    async def scenario(agent, app):
        await agent(
            "open_file", path=str(pair_files["ms"]), modulation_s=PAIR_MODULATION
        )
        await agent("add_library", path=str(pair_files["lib"]))
        await agent("detect_peaks", snr=10)
        rows = (await agent("peaks", sort_by="peak_id", limit=3))["rows"]
        a, b, c = (r["peak_id"] for r in rows)
        doc = next(iter(app.session.documents))
        app.session.set_categories(doc, [{"peak_id": a, "category": "oxygenates"}])
        set_ = await agent(
            "set_categories",
            assignments=[
                {"peak_id": a, "category": "monoaromatics", "reason": "ions 91, 105"},
                {"peak_id": b, "category": "monoaromatics", "reason": "ions 91, 105"},
            ],
        )
        dictated = await agent(
            "set_categories",
            assignments=[
                {"peak_id": c, "category": "n_alkanes", "reason": "user said"}
            ],
            as_user=True,
        )
        refused = await agent.error("detect_peaks", snr=10)
        user_rows = (await agent("peaks", source="user", limit=10))["rows"]
        agent_rows = (await agent("peaks", source="agent", limit=10))["rows"]
        edited = await agent("edit_peak", peak_id=b, hit_rank=1)
        return set_, dictated, refused, user_rows, agent_rows, edited

    set_, dictated, refused, user_rows, agent_rows, edited = run(
        session, roots, scenario
    )
    assert set_["n_set"] == 1
    assert set_["conflicts"][0]["user"] == "oxygenates"
    assert dictated["n_set"] == 1
    assert "reviewed labels" in refused
    assert "discard_labels" in refused
    assert {r["category"] for r in user_rows} == {"oxygenates", "n_alkanes"}
    assert [r["category"] for r in agent_rows] == ["monoaromatics"]
    assert edited["row"]["assigned_by"] == "agent"
    assert edited["revision"] >= 0
    assert "masks_revision" in edited


def test_masks_undo_and_rules(session, roots, pair_files):
    async def scenario(agent, app):
        await agent(
            "open_file", path=str(pair_files["ms"]), modulation_s=PAIR_MODULATION
        )
        await agent("add_library", path=str(pair_files["lib"]))
        await agent("detect_peaks", snr=10)
        defined = await agent(
            "define_class",
            name="alkylbenzenes",
            rules=[{"formula": {"only": ["C", "H"], "dbe": [4, 4]}}],
        )
        applied = await agent("apply_rules")
        made = await agent("create_mask", name="Box")
        await agent(
            "add_shape",
            mask="Box",
            shape={"type": "polygon", "vertices": [[2, 1], [6, 1], [6, 4]]},
        )
        listed = await agent("masks")
        op = (
            app.session.masks(next(iter(app.session.documents)))
            .get("Box")
            .operations[0]
        )
        await agent("update_mask", mask="Box", name="Triangle", category="excluded")
        await agent("delete_shape", mask="Triangle", operation_id=op.id)
        undone = await agent("undo_masks")
        redone = await agent("undo_masks", redo=True)
        deleted = await agent("delete_mask", mask=made["mask_id"])
        eic = await agent("set_signal", signal="eic", mz=[91, 105])
        integrated = await agent("integrate")
        return defined, applied, listed, undone, redone, deleted, eic, integrated

    defined, applied, listed, undone, redone, deleted, eic, integrated = run(
        session, roots, scenario
    )
    assert defined["matches"] == {"sample_MS.cdf": 8}  # the alkylbenzenes
    assert applied["scheme"] == "project"
    box = listed["masks"][0]
    assert box["rt1_min"] == [2.0, 6.0]
    assert box["rt2_s"] == [1.0, 4.0]
    assert box["n_shapes"] == 1
    assert undone["changed"]
    assert undone["masks"][0]["n_shapes"] == 1
    assert redone["changed"]
    assert redone["masks"][0]["n_shapes"] == 0
    assert deleted["masks"] == []
    assert eic["label"] == "EIC m/z 91, 105"
    assert integrated["signal"] == "EIC m/z 91, 105"


def test_jobs_progress_and_wait(session, roots, pair_files):
    async def scenario(agent, app):
        progress = []

        async def on_progress(value, total, message):
            progress.append((value, total, message))

        opened = await agent.client.call_tool(
            "open_file",
            {"path": str(pair_files["ms"]), "modulation_s": PAIR_MODULATION},
            progress_callback=on_progress,
        )
        assert not opened.is_error
        await agent("add_library", path=str(pair_files["lib"]))
        started = await agent("detect_peaks", snr=10, wait_s=0)
        finished = await agent("wait_job", job_id=started["job_id"])
        unknown = await agent.error("wait_job", job_id="nope")
        cancelled = await agent("cancel_job", job_id=started["job_id"])
        assert cancelled["status"] == "done"  # too late: nothing to cancel
        return progress, started, finished, unknown

    progress, started, finished, unknown = run(session, roots, scenario)
    assert progress
    assert all(t == 1.0 for _, t, _ in progress)
    assert started["status"] in ("queued", "running")
    assert finished["status"] == "done"
    assert finished["result"]["n_peaks"] > 0
    assert "No job" in unknown


def test_gui_coupling_events(session, roots, pair_files):
    pytest.importorskip("fastapi")
    events = []
    session.subscribe(lambda e: events.append(e["type"]))

    async def scenario(agent, app):
        await agent(
            "open_file", path=str(pair_files["fid"]), modulation_s=PAIR_MODULATION
        )
        url = app.gui.show(open_window=False)
        info = await agent("session_info")
        await agent("create_mask", name="Band")
        shown = await agent("activate", document="sample_FID")
        return url, info, shown

    url, info, shown = run(session, roots, scenario)
    assert url.startswith("http://127.0.0.1:")
    assert info["gui_url"] == url
    assert shown["gui_url"] == url
    assert "masks.updated" in events
    assert "document.updated" in events


# ------------------------------------------------------------------ guards
def test_roots(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "a.csv").write_text("x")
    secret = tmp_path / "secret.txt"
    secret.write_text("x")
    (data / "link").symlink_to(secret)
    roots = Roots([data], tmp_path / "out", cwd=data)
    assert roots.check_read("a.csv") == (data / "a.csv").resolve()
    for bad in (str(secret), "link", "../secret.txt", ""):
        with pytest.raises(GuardError):
            roots.check_read(bad)
    roots.allow(secret)
    assert roots.check_read(str(secret)) == secret.resolve()

    out = roots.output_path("sub/file.csv")
    assert out == (tmp_path / "out" / "sub" / "file.csv").resolve()
    out.write_text("x")
    with pytest.raises(GuardError, match="overwrite"):
        roots.output_path("sub/file.csv")
    assert roots.output_path("sub/file.csv", overwrite=True) == out
    (tmp_path / "out" / "escape").symlink_to(tmp_path)
    for bad in ("../x", "/etc/x", "escape/x", ""):
        with pytest.raises(GuardError):
            roots.output_path(bad)


def test_redaction():
    hit = {
        "rank": 1,
        "name": "Toluene",
        "match": 900,
        "nist_no": 1234,
        "db_no": 5,
        "index": 7,
        "library_id": "x",
        "smiles": "Cc1ccccc1",
        "redistributable": False,
    }
    assert redact_hit(hit) == {
        "rank": 1,
        "name": "Toluene",
        "match": 900,
        "redistributable": False,
    }
    open_hit = {**hit, "redistributable": True}
    assert redact_hit(open_hit)["nist_no"] == 1234
    assert "index" not in redact_hit(open_hit)
    rows = [{"library": "NIST", "nist_no": 1}, {"library": "MoNA", "nist_no": 0}]
    assert redact_rows(rows, {"MoNA"}) == [{"library": "NIST"}, rows[1]]


# ------------------------------------------------------------------ command
def test_mcp_imports_no_web_framework():
    """
    MCP-3: pyGCxGC[mcp] works without the app extra; FastAPI and the app
    server are imported only for the GUI (the SDK itself needs uvicorn).
    """
    code = (
        "import sys, pyGCxGC.mcp.server, pyGCxGC.mcp.__main__; "
        "print([m for m in ('fastapi', 'pydantic_settings', 'pyGCxGC.app.server') "
        "if m in sys.modules])"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert out.stdout.strip() == "[]"


def test_stdio_command(tmp_path, pair_files):
    """MCP-2: the real command completes the handshake; stdout carries only protocol."""
    from mcp.client.stdio import StdioServerParameters

    params = StdioServerParameters(
        command=sys.executable,
        args=[
            "-m",
            "pyGCxGC.app",
            "mcp",
            str(pair_files["fid"]),
            "-m",
            str(PAIR_MODULATION),
            "--settings",
            str(tmp_path / "app.json"),
            "--no-restore",
            "--output",
            str(tmp_path / "out"),
            "--allow-read",
            str(pair_files["dir"]),
        ],
        cwd=str(tmp_path),
        env={**os.environ, "PYGCXGC_TEST": "1"},
    )

    async def main():
        async with Client(params) as client:
            tools = (await client.list_tools()).tools
            # the legacy FID parser prints to stdout: it must not break the protocol
            opened = await client.call_tool(
                "open_file",
                {"path": str(pair_files["fid"]), "modulation_s": PAIR_MODULATION},
            )
            for _ in range(100):  # the file of the command line opens in a job
                info = await client.call_tool("session_info", {})
                if len(info.structured_content["documents"]) == 2:
                    break
                await anyio.sleep(0.1)
            return tools, opened, info

    tools, opened, info = anyio.run(main)
    assert len(tools) == len(SPEC_TOOLS)
    assert not opened.is_error, opened.content[0].text
    names = [d["name"] for d in info.structured_content["documents"]]
    assert names.count("sample_FID.csv") == 2  # from the command line and the tool
    assert info.structured_content["output_folder"] == str((tmp_path / "out").resolve())
