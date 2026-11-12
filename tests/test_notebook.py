"""
Tests for milestone 4: projects, settings and logs, and the notebook viewer
(``gcgc.view``) with its live state.
"""

from __future__ import annotations

import contextlib
import json
import logging
import shutil
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from synthetic import lib_from_dict, synthetic_run, write_andi

import pyGCxGC as gcgc
from pyGCxGC.app import paths
from pyGCxGC.app.session import InvalidRequest, Session
from pyGCxGC.masks import MaskSet

EXAMPLE = Path(__file__).parents[1] / "example_data"
FID_CSV = EXAMPLE / "example_chromatograms" / "Example_FID.csv"
MASK_DIR = EXAMPLE / "example_masks"
PORT = 8765
TOKEN = "test-token"


@pytest.fixture
def files(tmp_path):
    """A data folder with an FID file, a synthetic MS run and its library."""
    data = tmp_path / "data"
    data.mkdir()
    fid = shutil.copy(FID_CSV, data / "fid.csv")
    raw, truth = synthetic_run()
    cdf = write_andi(raw, data / "run.cdf")
    lib = lib_from_dict().save(data / "lib.npz")
    return Path(fid), Path(cdf), Path(lib), truth


@pytest.fixture
def session(tmp_path):
    s = Session(settings_path=tmp_path / "app.json", cache_dir=tmp_path / "cache")
    yield s
    s.close()


def new_session(tmp_path, name: str) -> Session:
    return Session(
        settings_path=tmp_path / f"{name}.json", cache_dir=tmp_path / "cache"
    )


def client_for(session):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from pyGCxGC.app.server import create_app

    return TestClient(
        create_app(session, TOKEN, PORT),
        base_url=f"http://127.0.0.1:{PORT}",
        headers={"Authorization": f"Bearer {TOKEN}"},
    )


def wait_idle(session, timeout: float = 60) -> None:
    """Wait until no job is running (jobs may start other jobs)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        pending = [j for j in list(session.jobs.values()) if not j.done]
        if not pending:
            return
        for j in pending:
            with contextlib.suppress(Exception):
                j.wait(timeout)
    raise TimeoutError


def build_project_session(session, files):
    """FID with masks and a view, MS with an EIC, a selection and edited peaks."""
    fid_path, cdf, lib, truth = files
    fid = session.wait(
        session.open_document(fid_path, params={"modulation_time": 20, "shift": 3})
    )["document_id"]
    session.import_masks(fid, path=MASK_DIR)
    mask = session.create_mask(fid, "Drawn", "#ff0000", "aromatics")
    session.add_shape(fid, mask.id, {"type": "rect", "rt1": [10, 20], "rt2": [1, 3]})
    session.set_view(fid, {"display": {"colormap": "magma"}, "domain": [1, 2, 3, 4]})
    lib_id = session.wait(session.add_library(lib))["library_id"]
    ms = session.wait(session.open_document(cdf, params={"modulation_time": 4}))[
        "document_id"
    ]
    session.wait(session.set_signal(ms, "eic", [91, 92]))
    session.wait(session.detect_peaks(ms, snr=10))
    table, _ = session.peaks(ms)
    first, second = (int(p) for p in table["peak_id"].iloc[:2])
    _, mod, rt2 = truth[0]  # toluene
    session.search(ms, rt2_index=round(rt2 / 0.05), mod_index=mod)
    session.edit_peak(ms, first, name="My compound")
    session.edit_peak(ms, second, delete=True)
    wait_idle(session)
    return fid, ms, lib_id


# ------------------------------------------------------------------ settings
def test_legacy_settings_are_migrated_once(tmp_path):
    legacy = tmp_path / "ms_viewer.json"
    legacy.write_text(
        json.dumps(
            {"library": "/nist/MAINLIB", "data": "/runs/a.cdf", "modulation": 20}
        )
    )
    target = tmp_path / "config" / "app.json"
    assert paths.migrate_legacy_settings(target, legacy)
    s = Session(settings_path=target)
    assert s.settings.get("libraries") == ["/nist/MAINLIB"]
    assert s.settings.get("recent_files") == ["/runs/a.cdf"]
    assert s.settings.get("theme") == "system"  # defaults still apply
    s.close()
    target.write_text("{}")
    assert not paths.migrate_legacy_settings(target, legacy)  # never twice
    assert target.read_text() == "{}"
    assert not paths.migrate_legacy_settings(tmp_path / "x.json", tmp_path / "none")


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux paths")
def test_linux_cache_stays_in_xdg_cache(monkeypatch, tmp_path):
    from pyGCxGC.ms.library import default_cache_dir

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    assert default_cache_dir() == tmp_path / "pyGCxGC"
    assert paths.log_dir() == tmp_path / "pyGCxGC" / "logs"
    monkeypatch.delenv("XDG_CACHE_HOME")
    assert default_cache_dir() == Path.home() / ".cache" / "pyGCxGC"


def test_failed_jobs_are_logged(session, tmp_path):
    file = paths.setup_logging(session.log_dir)
    assert paths.setup_logging(session.log_dir) == file  # no second handler
    root = logging.getLogger("pyGCxGC")
    handlers = [h for h in root.handlers if getattr(h, "_pygcxgc_log", None)]
    try:
        job = session.submit("test", "Broken job", lambda _j: 1 / 0)
        with pytest.raises(Exception, match="ZeroDivisionError"):
            job.wait(10)
        for h in handlers:
            h.flush()
        text = file.read_text()
        assert "Broken job" in text
        assert "ZeroDivisionError" in text
        assert file.parent == tmp_path / "cache" / "logs"
    finally:
        for h in handlers:
            root.removeHandler(h)
            logging.getLogger("uvicorn.error").removeHandler(h)
            h.close()


def test_session_files_and_log_folder_api(session, tmp_path, monkeypatch):
    client = client_for(session)
    info = client.get("/api/session").json()
    assert info["files"] == {
        "settings": str(tmp_path / "app.json"),
        "cache": str(tmp_path / "cache"),
        "logs": str(tmp_path / "cache" / "logs"),
    }
    assert info["project"] is None
    assert info["active_document_id"] is None
    opened = []
    monkeypatch.setattr(paths, "open_folder", opened.append)
    assert client.post("/api/logs/open").status_code == 204
    assert opened == [session.log_dir]


def test_settings_changes_are_events(session, files):
    events = []
    session.subscribe(events.append)
    session.update_settings({"theme": "dark"})
    session.wait(session.open_document(files[1], params={"modulation_time": 4}))
    updates = [e for e in events if e["type"] == "settings.updated"]
    assert updates[0]["settings"]["theme"] == "dark"
    # the backend's own changes too (here the recent files)
    assert updates[-1]["settings"]["recent_files"] == [str(files[1])]


# ------------------------------------------------------------------ session
def test_active_document_and_view_state(session, files):
    events = []
    session.subscribe(events.append)
    a = session.add_document(gcgc.parse_2D_chromatogram(FID_CSV, modulation_time=20))
    raw, _ = synthetic_run()
    b = session.add_document(gcgc.GCxGC_MS(raw, 4.0))
    assert session.active_document_id == b.id  # the last one opened
    session.set_active_document(a.id)
    assert session.active_document_id == a.id
    activated = [e for e in events if e.get("change") == "activated"]
    assert [e["document_id"] for e in activated] == [a.id]
    session.set_active_document(a.id)  # unchanged: no event
    assert len([e for e in events if e.get("change") == "activated"]) == 1
    session.close_document(a.id)
    assert session.active_document_id == b.id

    session.set_view(b.id, {"display": {"colormap": "plasma"}})
    assert session.document(b.id).info()["view"] == {"display": {"colormap": "plasma"}}
    with pytest.raises(InvalidRequest, match="too large"):
        session.set_view(b.id, {"x": "y" * 300_000})
    with pytest.raises(InvalidRequest, match="JSON object"):
        session.set_view(b.id, {"x": object()})

    client = client_for(session)
    r = client.put(f"/api/documents/{b.id}/view", json={"zoom": [1, 2]})
    assert r.status_code == 204
    assert client.get(f"/api/documents/{b.id}").json()["view"] == {"zoom": [1, 2]}
    assert (
        client.put("/api/session/active", json={"document_id": b.id}).status_code == 204
    )
    assert (
        client.put("/api/session/active", json={"document_id": "x"}).status_code == 404
    )


def test_select_by_retention_time(session):
    raw, truth = synthetic_run()
    doc = session.add_document(gcgc.GCxGC_MS(raw, 4.0))
    g = doc.grid
    _, mod, rt2 = truth[1]  # naphthalene
    rt1 = g.rt1_start + (mod + 0.5) * g.modulation_time / 60
    sel = session.select(doc.id, rt1=rt1, rt2=rt2 + 0.01, snap=False)
    assert (sel.spectrum.rt2_index, sel.spectrum.mod_index) == (round(rt2 / 0.05), mod)
    assert session.pixel_at(doc.id, g.rt1_start, g.rt2_start) == (0, 0)
    with pytest.raises(InvalidRequest, match="outside"):
        session.select(doc.id, rt1=g.rt1_start - 1, rt2=1.0)
    with pytest.raises(InvalidRequest, match="both"):
        session.select(doc.id, rt1=rt1)


def test_library_objects_from_python(session):
    lib = lib_from_dict()
    job = session.add_library(lib, exclusive=True)
    library_id = session.wait(job)["library_id"]
    rec = session.library(library_id)
    assert rec.library is lib
    assert rec.active
    assert rec.source == "python"
    assert session.wait(session.add_library(lib))["library_id"] == library_id
    assert session.settings.get("libraries") == []  # not remembered


# ------------------------------------------------------------------ projects
def test_project_round_trip(session, files, tmp_path):
    fid, ms, _ = build_project_session(session, files)
    session.update_settings({"ui": {"layouts": {"fid": "{saved layout}"}}})
    before_fid = session.integration(fid)
    before_ms = session.integration(ms)
    table, info = session.peaks(ms)
    selection = session.document(ms).selection
    session.set_active_document(fid)

    result = session.save_project(tmp_path / "projects" / "study")
    path = Path(result["path"])
    assert path == tmp_path / "projects" / "study.gcxgc"
    assert result["warnings"] == []
    assert session.project_info() == {"path": str(path), "name": "study"}
    assert session.settings.get("recent_files")[0] == str(path)
    saved = json.loads(path.read_text())
    assert saved["format"] == "pygcxgc-project"
    assert saved["version"] == 2
    assert [d["path"] for d in saved["documents"]] == [
        "../data/fid.csv",
        "../data/run.cdf",
    ]
    assert saved["libraries"][0]["path"] == "../data/lib.npz"
    assert "chrom_2D" not in path.read_text()  # paths, never data

    other = new_session(tmp_path, "other")
    try:
        job = other.open_project(path)
        result = other.wait(job, 120)
        wait_idle(other)
        assert job.warnings == []
        fid2, ms2 = result["document_ids"]
        assert other.active_document_id == fid2
        assert other.project_info()["name"] == "study"
        # processing, masks and their integration
        assert other.document(fid2).params == session.document(fid).params
        assert (
            other.document(fid2).masks.to_dict()["masks"]
            == (session.document(fid).masks.to_dict()["masks"])
        )
        assert other.integration(fid2)["masks"] == before_fid["masks"]
        assert other.integration(ms2)["total"] == before_ms["total"]
        assert other.document(fid2).view == {
            "display": {"colormap": "magma"},
            "domain": [1, 2, 3, 4],
        }
        # signal, peaks with the user's edits, the selection and its hits
        doc = other.document(ms2)
        assert (doc.signal, doc.mz) == ("eic", [91, 92])
        table2, info2 = other.peaks(ms2)
        pd.testing.assert_frame_equal(table2, table, check_dtype=False)
        assert "My compound" in set(table2["name"])
        assert not info2["stale"], info2
        assert info2["snr"] == info["snr"]
        assert doc.selection.spectrum.scan == selection.spectrum.scan
        assert [h.name for h in doc.selection.hits] == [h.name for h in selection.hits]
        # libraries and layout
        assert [r.active for r in other.libraries.values()] == [True]
        assert other.settings.get("ui")["layouts"] == {"fid": "{saved layout}"}
        # re-identify keeps the user's names (positions are current)
        other.wait(other.reidentify_peaks(ms2))
        assert "My compound" in set(other.peaks(ms2)[0]["name"])
    finally:
        other.close()


def test_project_follows_moved_folders_and_reports_missing_files(
    session, files, tmp_path
):
    build_project_session(session, files)
    session.save_project(tmp_path / "study.gcxgc")
    moved = tmp_path / "moved"
    moved.mkdir()
    shutil.move(tmp_path / "data", moved / "data")
    shutil.move(tmp_path / "study.gcxgc", moved / "study.gcxgc")
    other = new_session(tmp_path, "moved")
    try:
        job = other.open_project(moved / "study.gcxgc")
        assert len(other.wait(job, 120)["document_ids"]) == 2  # relative paths
        assert job.warnings == []
        (moved / "data" / "fid.csv").unlink()
        (moved / "data" / "lib.npz").unlink()
        wait_idle(other)
        job = other.open_project(moved / "study.gcxgc")
        assert len(other.wait(job, 120)["document_ids"]) == 1
        assert any("fid.csv: file not found" in w for w in job.warnings)
        assert any("Library not found" in w for w in job.warnings)
        assert len(other.documents) == 1  # the open documents were replaced
        with pytest.warns(UserWarning, match="file not found"):
            Session.load(
                moved / "study.gcxgc", settings_path=tmp_path / "x.json"
            ).close()
    finally:
        other.close()


def test_project_errors_and_unsaved_documents(session, tmp_path):
    with pytest.raises(InvalidRequest, match="file name"):
        session.save_project()
    session.add_document(gcgc.parse_2D_chromatogram(FID_CSV, modulation_time=20))
    result = session.save_project(tmp_path / "p.gcxgc")
    assert "not opened from a file" in result["warnings"][0]
    assert session.save_project()["path"] == result["path"]  # the current project
    bad = tmp_path / "bad.gcxgc"
    bad.write_text("{}")
    with pytest.raises(InvalidRequest, match="not a pyGCxGC project"):
        session.open_project(bad)
    bad.write_text(json.dumps({"format": "pygcxgc-project", "version": 99}))
    with pytest.raises(InvalidRequest, match="newer pyGCxGC"):
        session.open_project(bad)
    with pytest.raises(InvalidRequest, match="open it as a project"):
        session.open_document(bad)
    assert session.probe_file(bad)["kind"] == "project"
    session.close_project()
    assert not session.documents
    assert session.project_info() is None


def test_project_api(session, files, tmp_path):
    client = client_for(session)
    build_project_session(session, files)
    r = client.post("/api/project/save", json={"path": str(tmp_path / "api")})
    assert r.status_code == 200, r.text
    saved = r.json()
    assert saved["project"]["name"] == "api"
    assert client.get("/api/session").json()["project"] == saved["project"]
    r = client.post(
        "/api/project/save", json={"ui": {"layouts": {"ms": "L"}, "extra": 1}}
    )
    project = json.loads(Path(saved["path"]).read_text())
    assert project["ui"] == {"layouts": {"ms": "L"}, "extra": 1}
    r = client.post("/api/project/open", json={"path": saved["path"]})
    assert r.status_code == 202
    session.job(r.json()["job"]["id"]).wait(120)
    job = client.get(f"/api/jobs/{r.json()['job']['id']}").json()
    assert job["status"] == "done", job
    assert len(job["result"]["document_ids"]) == 2
    assert (
        client.get("/api/fs/probe", params={"path": saved["path"]}).json()["kind"]
        == "project"
    )
    assert client.post("/api/project/close").status_code == 204
    assert client.get("/api/documents").json() == []
    r = client.post("/api/project/open", json={"path": str(tmp_path / "none.gcxgc")})
    assert r.status_code == 400


# ------------------------------------------------------------------ viewer
@pytest.fixture
def viewer(tmp_path):
    pytest.importorskip("uvicorn")
    from pyGCxGC.app.viewer import Viewer

    v = Viewer.start(new_session(tmp_path, "viewer"))
    yield v
    v.close()


def test_viewer_serves_the_live_objects(viewer):
    import httpx

    chrom = gcgc.parse_2D_chromatogram(FID_CSV, modulation_time=20)
    doc_id = viewer.open(chrom)
    assert viewer.data is chrom
    assert viewer.document == doc_id
    assert "token=" in viewer.url
    assert viewer.running
    base = viewer.url.split("/?")[0]
    headers = {"Authorization": f"Bearer {viewer._server.token}"}
    info = httpx.get(f"{base}/api/session", headers=headers).json()
    assert info["active_document_id"] == doc_id
    assert httpx.get(f"{base}/api/session").status_code == 401

    events = []
    viewer.on("masks", events.append)
    masks = viewer.masks
    op = masks.add_rect((10, 20), (1, 3), mask="Drawn")
    assert masks.names == ["Drawn"]
    assert isinstance(op, str)
    assert masks.add(MASK_DIR)
    assert len(masks) == 3
    masks.add_polygon([(30, 1), (40, 1), (40, 4)])  # into the last mask
    assert len(masks["Mask_2"].operations) == 2
    masks.rename("Drawn", "Aromatics")
    masks.update("Aromatics", color="#00ff00")
    assert masks["Aromatics"].color == "#00ff00"
    # a shape for a new mask is two undo steps (create, add)
    assert len(events) == 6
    assert all(e["type"] == "masks.updated" for e in events)
    # the app's integration equals MaskSet.integrate on the object
    expected = masks.snapshot().integrate(chrom)
    assert viewer.integration["volume"].drop("unassigned").to_dict() == expected
    assert viewer.integration.loc["unassigned", "n_pixels"] > 0
    assert masks.undo()
    assert masks["Aromatics"].color != "#00ff00"
    assert masks.undo()
    assert masks.names == ["Drawn", "Mask_1", "Mask_2"]
    masks.remove("Mask_1")
    masks.clear()
    assert len(masks) == 0
    assert masks.undo()
    assert len(masks) == 2
    with pytest.raises(AttributeError, match="edit masks"):
        masks.append  # noqa: B018
    with pytest.raises(InvalidRequest, match="No mask"):
        masks.remove("nope")
    assert viewer.selection is None
    assert viewer.peaks is None


def test_viewer_ms_selection_signal_and_callbacks(viewer, files):
    _, cdf, lib, truth = files
    viewer.add_library(lib)
    assert viewer.library is not None
    assert list(viewer.libraries) == ["test"]
    viewer.open(cdf, modulation_time=4, wait=False)  # in the background
    selected = []
    unsubscribe = viewer.on("select", selected.append)
    g = viewer.session.document(viewer.document).grid  # waits for the file
    _, mod, rt2 = truth[0]  # toluene
    sel = viewer.select(rt1=g.rt1_start + (mod + 0.5) * g.modulation_time / 60, rt2=rt2)
    assert sel.hits[0].name == "Toluene"
    assert viewer.hits.iloc[0]["name"] == "Toluene"
    assert viewer.spectrum is sel.spectrum.corrected
    assert selected[-1]["selection"]["hits"][0]["name"] == "Toluene"
    unsubscribe()
    n = len(selected)
    viewer.select(scan=sel.spectrum.scan, search=False)
    assert len(selected) == n
    viewer.set_signal(eic=91)
    doc = viewer.session.document(viewer.document)
    assert (doc.signal, doc.mz) == ("eic", [91])
    viewer.set_signal()
    assert doc.signal == "tic"
    table = viewer.detect_peaks()
    assert len(table)
    assert table.attrs["snr"] == 10.0
    assert viewer.peaks["name"].tolist() == table["name"].tolist()
    rt1 = g.rt1_start + mod * g.modulation_time / 60
    viewer.masks.add_rect((rt1 - 0.2, rt1 + 0.2), (rt2 - 0.5, rt2 + 0.5))
    assert viewer.select(mask=viewer.masks.names[0]).region is not None
    with pytest.raises(ValueError, match="Unknown event"):
        viewer.on("click", print)


def test_view_reuses_one_server(tmp_path, monkeypatch, files):
    pytest.importorskip("uvicorn")
    from pyGCxGC.app import viewer as viewer_module

    monkeypatch.setattr(paths, "settings_file", lambda: tmp_path / "view.json")
    monkeypatch.setattr(
        "pyGCxGC.ms.library.default_cache_dir", lambda: tmp_path / "cache"
    )
    monkeypatch.setattr(
        "pyGCxGC.app.session.default_cache_dir", lambda: tmp_path / "cache"
    )
    shown = []
    monkeypatch.setattr(viewer_module.Viewer, "show", lambda self: shown.append(self))
    chrom = gcgc.parse_2D_chromatogram(FID_CSV, modulation_time=20)
    first = gcgc.view(chrom, display=True)
    try:
        assert shown == [first]
        assert first.data is chrom
        second = gcgc.view(files[1], library=lib_from_dict(), display=False)
        assert second is first
        assert len(shown) == 1
        assert second.data.n_scans > 0  # waits for the file
        assert second.library is not None
        assert len(second.documents) == 2
        second.activate(chrom)
        assert second.data is chrom
        assert repr(second).startswith("<pyGCxGC Viewer http://127.0.0.1:")
    finally:
        first.close()
    assert not first.running
    third = gcgc.view(display=False)
    try:
        assert third is not first
        assert not third.documents
    finally:
        third.close()


# ------------------------------------------------------------------ command line
def test_command_line_headless(tmp_path, files):
    pytest.importorskip("uvicorn")
    import httpx

    fid_path, _, _, _ = files
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "pyGCxGC.app",
            "--headless",
            "--no-restore",
            "--settings",
            str(tmp_path / "cli.json"),
            "--token",
            "cli",
            "-m",
            "20",
            str(fid_path),
        ],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        line = proc.stdout.readline()
        assert line.startswith("READY "), line
        port = int(line.split()[1])
        assert proc.stdout.readline().strip() == f"http://127.0.0.1:{port}/?token=cli"
        headers = {"Authorization": "Bearer cli"}
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            docs = httpx.get(f"http://127.0.0.1:{port}/api/documents", headers=headers)
            if docs.json():
                break
            time.sleep(0.1)
        assert docs.json()[0]["params"]["modulation_time"] == 20
    finally:
        proc.terminate()
        proc.wait(10)


def test_project_masks_rasterize_like_the_original(session, files, tmp_path):
    fid, _, _ = build_project_session(session, files)
    session.save_project(tmp_path / "r.gcxgc")
    loaded = Session.load(tmp_path / "r.gcxgc", settings_path=tmp_path / "r.json")
    try:
        doc = next(d for d in loaded.documents.values() if d.kind == "fid")
        original = session.masks(fid).rasterize(session.document(fid).grid)
        again = MaskSet.from_dict(doc.masks.to_dict()).rasterize(doc.grid)
        assert original.keys() == again.keys()
        for name in original:
            np.testing.assert_array_equal(original[name], again[name])
    finally:
        loaded.close()


# ------------------------------------------------------------------ solvent cut-off
def test_solvent_cutoff_not_set_survives_projects(session, files, tmp_path):
    fid_path, cdf, _, _ = files
    fid = session.wait(session.open_document(fid_path, modulation_time=20))[
        "document_id"
    ]
    ms = session.wait(session.open_document(cdf, modulation_time=4))["document_id"]
    assert session.document(fid).params.solvent_cutoff is None
    assert session.document(ms).params.solvent_cutoff is None
    session.wait(session.update_document(fid, solvent_cutoff=0))
    session.wait(session.update_document(ms, solvent_cutoff=0.1))
    data = session.document(ms).data
    assert data.solvent_cutoff == 0.1
    assert not data.tic_2d[:, 0].any()  # the first 6 s are zeroed
    path = session.save_project(tmp_path / "cut")["path"]
    other = new_session(tmp_path, "other")
    try:
        fid2, ms2 = other.wait(other.open_project(path), 60)["document_ids"]
        assert other.document(fid2).params.solvent_cutoff == 0  # explicit 0
        assert other.document(ms2).params.solvent_cutoff == 0.1
    finally:
        other.close()
    # version 1 projects stored 0 for "not set"
    project = json.loads(Path(path).read_text())
    project["version"] = 1
    old = tmp_path / "old.gcxgc"
    old.write_text(json.dumps(project))
    other = new_session(tmp_path, "old")
    try:
        fid3, ms3 = other.wait(other.open_project(old), 60)["document_ids"]
        assert other.document(fid3).params.solvent_cutoff is None
        assert other.document(ms3).params.solvent_cutoff == 0.1
    finally:
        other.close()
    with pytest.raises(InvalidRequest, match="negative"):
        session.update_document(ms, solvent_cutoff=-1)
