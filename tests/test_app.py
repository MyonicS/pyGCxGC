"""Tests for the app layer: Session (core only) and the HTTP/WebSocket API."""

from __future__ import annotations

import contextlib
import csv
import io
import json
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import numpy as np
import pytest
import tifffile
from synthetic import lib_from_dict, synthetic_run, write_andi

import pyGCxGC as gcgc
from pyGCxGC.app.arrays import decode_array, encode_array, max_pool
from pyGCxGC.app.session import InvalidRequest, JobCancelled, Session
from pyGCxGC.masks import MaskSet
from pyGCxGC.ms import GCxGC_MS

EXAMPLE = Path(__file__).parents[1] / "example_data"
FID_CSV = EXAMPLE / "example_chromatograms" / "Example_FID.csv"
MASK_DIR = EXAMPLE / "example_masks"
PY_010 = (
    Path(__file__).parents[1]
    / "local_data"
    / "gcxgc-ms_data"
    / "SR_2DGC_003_PY_010_ANDI.CDF"
)
PORT = 8765
TOKEN = "test-token"


@pytest.fixture(scope="module")
def ms_files(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("ms")
    raw, truth = synthetic_run()
    cdf = write_andi(raw, tmp / "run.cdf")
    lib = lib_from_dict().save(tmp / "lib.npz")
    return cdf, lib, truth


@pytest.fixture
def session(tmp_path):
    s = Session(settings_path=tmp_path / "app.json", cache_dir=tmp_path / "cache")
    yield s
    s.close()


@pytest.fixture
def client(session):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from pyGCxGC.app.server import create_app

    app = create_app(session, TOKEN, PORT)
    return TestClient(
        app,
        base_url=f"http://127.0.0.1:{PORT}",
        headers={"Authorization": f"Bearer {TOKEN}"},
    )


def finish_job(client, response) -> dict:
    """Wait for the job started by ``response`` and return its JSON."""
    assert response.status_code == 202, response.text
    job_id = response.json()["job"]["id"]
    with contextlib.suppress(Exception):
        client.app.state.session.job(job_id).wait(60)
    return client.get(f"/api/jobs/{job_id}").json()


def wait_job(client, response) -> dict:
    """Wait for a job that must succeed and return its result."""
    job = finish_job(client, response)
    assert job["status"] == "done", job
    return job["result"]


# ------------------------------------------------------------------ core
def test_session_needs_no_web_dependencies():
    code = (
        "import sys, pyGCxGC.app.session, pyGCxGC.gui.ms_viewer;"
        "bad = {'fastapi', 'starlette', 'pydantic', 'uvicorn'} & set(sys.modules);"
        "assert not bad, bad"
    )
    subprocess.run([sys.executable, "-c", code], check=True)


def test_array_encoding():
    a = np.arange(12, dtype=np.float32).reshape(3, 4)
    body = encode_array(a, {"signal": "tic", "P": 20.0})
    (n,) = np.frombuffer(body[:4], "<u4")
    assert (4 + n) % 8 == 0  # data can be viewed without copying
    header = json.loads(body[4 : 4 + n])
    assert header == {"signal": "tic", "P": 20.0, "shape": [3, 4], "dtype": "float32"}
    assert body[4 + n :] == a.astype("<f4").tobytes()
    h, back = decode_array(body)
    assert h["signal"] == "tic"
    np.testing.assert_array_equal(back, a)
    _, u8 = decode_array(encode_array(np.array([[0, 255]], np.uint8)))
    assert u8.dtype == np.uint8
    with pytest.raises(ValueError, match="Unsupported dtype"):
        encode_array(np.array(["a"]))


def test_max_pool():
    img = np.arange(30, dtype=np.float32).reshape(5, 6)
    pooled, fr, fc = max_pool(img, 3, 3)
    assert (fr, fc) == (2, 2)
    np.testing.assert_array_equal(pooled, [[7, 9, 11], [19, 21, 23], [25, 27, 29]])
    same, fr, fc = max_pool(img, 10, 10)
    assert same is img
    assert (fr, fc) == (1, 1)


def test_session_fid_masks_and_undo(session):
    doc_id = session.wait(
        session.open_document(FID_CSV, params={"modulation_time": 20})
    )["document_id"]
    doc = session.document(doc_id)
    assert doc.kind == "fid"
    image = session.image(doc_id)
    expected_image = doc.chrom.chrom_2D.to_numpy()[::-1].astype(np.float32)
    np.testing.assert_array_equal(image.data, expected_image)
    assert image.grid.shape == (5000, 317)

    ids = session.import_masks(doc_id, path=MASK_DIR)
    result = session.integration(doc_id)
    expected = gcgc.integrate_masks(doc.chrom.chrom_2D, MASK_DIR)
    assert {r["name"]: r["volume"] for r in result["masks"]} == expected
    assert result["overlaps"][0]["n_pixels"] == 625

    mask = session.create_mask(doc_id, "Box")
    op = session.add_shape(
        doc_id, mask.id, {"type": "rect", "rt1": [20, 40], "rt2": [5, 10]}
    )
    raster, grid = session.mask_raster(doc_id, mask.id)
    assert raster.dtype == np.uint8
    assert grid.shape == raster.shape
    assert (raster == 255).sum() == 60 * 1250  # 60 modulations x 5 s / 4 ms
    session.delete_shape(doc_id, mask.id, op)
    assert session.mask_raster(doc_id, mask.id)[0].sum() == 0
    assert session.undo_masks(doc_id)
    assert (session.mask_raster(doc_id, mask.id)[0] == 255).sum() == 75000
    assert session.undo_masks(doc_id)  # add_shape
    assert session.undo_masks(doc_id)  # create_mask
    assert session.masks(doc_id).names == ["Mask_1", "Mask_2"]
    assert session.redo_masks(doc_id)
    assert session.masks(doc_id).names == ["Mask_1", "Mask_2", "Box"]
    with pytest.raises(InvalidRequest, match="already exists"):
        session.update_mask(doc_id, ids[0], name="Mask_2")
    assert session.masks(doc_id).names == ["Mask_1", "Mask_2", "Box"]


def test_session_live_ms_object(session):
    raw, truth = synthetic_run()
    data = GCxGC_MS(raw, 4.0)
    doc = session.add_document(data, name="live")
    assert session.document(doc.id).data is data
    _, mod, rt2 = truth[1]  # naphthalene
    sel = session.select(doc.id, rt2_index=round(rt2 / 0.05) - 3, mod_index=mod - 1)
    assert (sel.spectrum.rt2_index, sel.spectrum.mod_index) == (round(rt2 / 0.05), mod)
    assert sel.spectrum.corrected.mz[np.argmax(sel.spectrum.corrected.intensity)] == 128
    # no library: search returns no hits
    assert session.search(doc.id).hits == []
    # masks on MS documents integrate the current signal
    mask = session.create_mask(doc.id)
    session.add_shape(
        doc.id,
        mask.id,
        {"type": "lasso", "vertices": [[1.5, 2.0], [1.9, 2.0], [1.9, 3.0], [1.5, 3.0]]},
    )
    tic = session.integration(doc.id)
    session.wait(session.set_signal(doc.id, "eic", [128]))
    eic = session.integration(doc.id)
    assert eic["signal"] == "eic"
    assert 0 < eic["masks"][0]["volume"] < tic["masks"][0]["volume"]
    # vector masks can be reused on another grid, raster ones only on the same shape
    fid_doc = session.add_document(
        gcgc.parse_2D_chromatogram(FID_CSV, modulation_time=20)
    )
    assert session.import_masks(fid_doc.id, from_document=doc.id)
    session.import_masks(fid_doc.id, path=MASK_DIR)
    with pytest.raises(InvalidRequest, match="does not match the chromatogram"):
        session.import_masks(doc.id, from_document=fid_doc.id)
    assert len(session.masks(doc.id)) == 1


def test_job_cancel(session):
    events = []
    session.subscribe(events.append)
    started = []

    def slow(job):
        started.append(True)
        for i in range(500):
            job.report(i / 500)
            time.sleep(0.01)
        return {"ok": True}

    job = session.submit("test", "Slow", slow)
    while not started:
        time.sleep(0.01)
    job.cancel()
    with pytest.raises(JobCancelled):
        job.wait(10)
    assert job.status == "cancelled"
    final = [e for e in events if e["type"] == "job.failed"]
    assert final[-1]["job"]["status"] == "cancelled"
    revisions = [e["revision"] for e in events]
    assert revisions == sorted(revisions)
    assert len(set(revisions)) == len(revisions)


def test_settings_persist(tmp_path):
    path = tmp_path / "app.json"
    s = Session(settings_path=path)
    s.update_settings({"display": {"colormap": "magma"}, "theme": "dark"})
    s.close()
    again = Session(settings_path=path)
    assert again.settings.get("display") == {
        "colormap": "magma",
        "scaling": "sqrt",
        "interpolation": "nearest",
    }
    assert again.settings.get("theme") == "dark"
    again.close()


# ------------------------------------------------------------------ API
def test_security(client):
    from starlette.websockets import WebSocketDisconnect

    assert client.get("/api/session").status_code == 200
    assert client.get("/api/session", headers={"Authorization": ""}).status_code == 401
    assert (
        client.get("/api/session", headers={"Authorization": "Bearer x"}).status_code
        == 401
    )
    for host in ("evil.example:8765", "127.0.0.1:9999", "localhost"):
        r = client.get("/api/session", headers={"Host": host})
        assert r.status_code == 403, host
    assert client.get("/", headers={"Host": "evil.example"}).status_code == 403
    assert (
        client.get("/api/session", headers={"Host": f"localhost:{PORT}"}).status_code
        == 200
    )
    # no CORS
    r = client.options(
        "/api/session",
        headers={
            "Origin": "http://evil.example",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert "access-control-allow-origin" not in r.headers
    good_host = {"host": f"127.0.0.1:{PORT}"}
    for url, headers in (
        ("/api/events", good_host),
        ("/api/events?token=wrong", good_host),
        (f"/api/events?token={TOKEN}", {"host": "evil.example"}),
    ):
        with (
            pytest.raises(WebSocketDisconnect) as info,
            client.websocket_connect(url, headers=headers),
        ):
            pass
        assert info.value.code == 1008


def test_api_fid_workflow(client, tmp_path):
    r = client.post("/api/documents", json={"path": str(FID_CSV)})
    assert r.status_code == 400  # FID data need a modulation time
    assert "modulation time" in r.json()["detail"]

    r = client.post(
        "/api/documents", json={"path": str(FID_CSV), "params": {"modulation_time": 20}}
    )
    doc_id = wait_job(client, r)["document_id"]
    info = client.get(f"/api/documents/{doc_id}").json()
    assert info["kind"] == "fid"
    assert info["params"]["sampling_interval"] == pytest.approx(0.004)
    assert info["summary"] == "FID · 250 Hz · 317 × 5000"  # noqa: RUF001

    r = client.get(f"/api/documents/{doc_id}/image")
    assert r.headers["content-type"] == "application/vnd.pygcxgc.array"
    header, image = decode_array(r.content)
    assert header["shape"] == [5000, 317]
    assert header["axes"] == ["rt2", "rt1"]
    assert (header["P"], header["dt"]) == (20.0, pytest.approx(0.004))
    assert header["rt1"] == {"start": 0.0, "step": pytest.approx(1 / 3), "count": 317}
    chrom = gcgc.parse_2D_chromatogram(FID_CSV, modulation_time=20)
    np.testing.assert_array_equal(
        image, chrom.chrom_2D.to_numpy()[::-1].astype(np.float32)
    )
    header, pooled = decode_array(
        client.get(f"/api/documents/{doc_id}/image?max_size=1024").content
    )
    assert pooled.shape == (1000, 317)
    assert header["pool"] == [5, 1]
    assert header["rt2"]["step"] == pytest.approx(0.02)

    # masks: import, integrate exactly like integrate_masks, export
    r = client.post(
        f"/api/documents/{doc_id}/masks/import", json={"path": str(MASK_DIR)}
    )
    assert [m["name"] for m in r.json()["masks"]] == ["Mask_1", "Mask_2"]
    result = client.get(f"/api/documents/{doc_id}/integration").json()
    expected = gcgc.integrate_masks(chrom.chrom_2D, MASK_DIR)
    assert {m["name"]: m["volume"] for m in result["masks"]} == expected
    assert result["unassigned"]["name"] == "unassigned"
    assert result["overlaps"][0]["names"] == ["Mask_1", "Mask_2"]

    r = client.post(f"/api/documents/{doc_id}/export", json={"what": "masks"})
    assert r.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        for name in ("Mask_1", "Mask_2"):
            exported = tifffile.imread(io.BytesIO(zf.read(f"{name}.tif")))
            np.testing.assert_array_equal(
                exported, tifffile.imread(MASK_DIR / f"{name}.tif")
            )
    r = client.post(
        f"/api/documents/{doc_id}/export",
        json={"what": "mask_set", "path": str(tmp_path / "set.gcxgc-masks.json")},
    )
    assert r.json() == {"path": str(tmp_path / "set.gcxgc-masks.json")}
    assert MaskSet.load(tmp_path / "set.gcxgc-masks.json").integrate(chrom) == expected
    r = client.post(f"/api/documents/{doc_id}/export", json={"what": "integration"})
    row = next(csv.DictReader(io.StringIO(r.text)))
    assert float(row["Mask_1"]) == expected["Mask_1"]
    assert row["modulation_time"] == "20.0"
    assert "unassigned" in row

    # drawing, editing, undo/redo
    r = client.post(
        f"/api/documents/{doc_id}/masks", json={"name": "Box", "category": "alkanes"}
    )
    box = r.json()["masks"][-1]
    assert box["category"] == "alkanes"
    r = client.post(
        f"/api/documents/{doc_id}/masks/{box['id']}/operations",
        json={"shape": {"type": "rect", "rt1": [20, 40], "rt2": [5, 10]}},
    )
    box = r.json()["masks"][-1]
    assert box["n_pixels"] == 75000
    assert box["operations"][0]["shape"] == {
        "type": "rect",
        "rt1": [20.0, 40.0],
        "rt2": [5.0, 10.0],
    }
    op_id = box["operations"][0]["id"]
    r = client.put(
        f"/api/documents/{doc_id}/masks/{box['id']}/operations/{op_id}",
        json={"shape": {"type": "polygon", "vertices": [[20, 5], [40, 5], [40, 10]]}},
    )
    assert 0 < r.json()["masks"][-1]["n_pixels"] < 75000
    header, raster = decode_array(
        client.get(f"/api/documents/{doc_id}/masks/{box['id']}/raster").content
    )
    assert raster.shape == (5000, 317)
    assert header["mask_id"] == box["id"]
    r = client.patch(
        f"/api/documents/{doc_id}/masks/{box['id']}",
        json={"name": "Triangle", "visible": False, "position": 0},
    )
    assert r.json()["masks"][0]["name"] == "Triangle"
    assert r.json()["masks"][0]["visible"] is False
    r = client.post(f"/api/documents/{doc_id}/masks/{box['id']}/duplicate")
    assert [m["name"] for m in r.json()["masks"]][:2] == ["Triangle", "Triangle copy"]
    r = client.post(f"/api/documents/{doc_id}/masks/undo")
    assert r.json()["can_redo"]
    assert len(r.json()["masks"]) == 3
    assert len(client.post(f"/api/documents/{doc_id}/masks/redo").json()["masks"]) == 4
    r = client.delete(f"/api/documents/{doc_id}/masks/{box['id']}")
    assert len(r.json()["masks"]) == 3

    # reprocessing keeps the masks (they are in RT units)
    r = client.patch(f"/api/documents/{doc_id}", json={"shift": 10, "normalize": "max"})
    wait_job(client, r)
    info = client.get(f"/api/documents/{doc_id}").json()
    assert info["params"]["shift"] == 10
    assert info["n_masks"] == 3
    assert client.delete(f"/api/documents/{doc_id}").status_code == 204
    assert client.get(f"/api/documents/{doc_id}").status_code == 404


def test_api_ms_workflow(client, ms_files, tmp_path):
    cdf, lib_path, truth = ms_files
    r = client.post(
        "/api/documents", json={"path": str(cdf), "params": {"modulation_time": 4}}
    )
    doc_id = wait_job(client, r)["document_id"]
    info = client.get(f"/api/documents/{doc_id}").json()
    assert info["kind"] == "ms"
    assert info["params"]["modulation_time"] == 4.0
    assert info["mass_range"] == [45, 200]
    assert info["baseline"] == "modulation_percentile(percentile=25)"

    # click before a library is loaded: spectrum, no hits
    _, mod, rt2 = truth[0]  # toluene
    j = round(rt2 / 0.05)
    r = client.post(
        "/api/search",
        json={"document_id": doc_id, "rt2_index": j - 2, "mod_index": mod},
    )
    assert r.json()["hits"] == []
    assert r.json()["scan"] == mod * 80 + j  # snapped to the apex

    lib_id = wait_job(
        client, client.post("/api/libraries", json={"path": str(lib_path)})
    )["library_id"]
    libs = client.get("/api/libraries").json()
    assert libs[0]["status"] == "ready"
    assert libs[0]["active"]
    assert libs[0]["n_spectra"] == 4
    sel = client.post("/api/search", json={"document_id": doc_id}).json()
    assert sel["hits"][0]["name"] == "Toluene"
    assert sel["hits"][0]["match"] > 900
    assert sel["hits"][0]["library_id"] == lib_id
    assert (
        client.get(f"/api/documents/{doc_id}/selection").json()["scan"] == sel["scan"]
    )

    header, spec = decode_array(client.get(f"/api/documents/{doc_id}/spectrum").content)
    assert header["columns"] == ["mz", "raw", "corrected"]
    assert header["scan"] == sel["scan"]
    assert spec[np.argmax(spec[:, 2]), 0] == 91
    assert (spec[:, 1] >= spec[:, 2]).all()
    header, ref = decode_array(
        client.get(f"/api/libraries/{lib_id}/spectra/{sel['hits'][0]['index']}").content
    )
    assert header["name"] == "Toluene"
    assert ref[np.argmax(ref[:, 1]), 0] == 91

    # explicit spectrum search
    r = client.post(
        "/api/search",
        json={
            "spectrum": {"mz": [128, 127, 129, 102], "intensity": [999, 130, 110, 80]}
        },
    )
    assert r.json()["hits"][0]["name"] == "Naphthalene"

    # EIC
    wait_job(
        client,
        client.put(
            f"/api/documents/{doc_id}/signal", json={"signal": "eic", "mz": [128]}
        ),
    )
    header, eic = decode_array(client.get(f"/api/documents/{doc_id}/image").content)
    assert (header["signal"], header["mz"]) == ("eic", [128])
    assert np.unravel_index(np.argmax(eic), eic.shape)[1] == truth[1][1]
    _, tic = decode_array(
        client.get(f"/api/documents/{doc_id}/image?signal=tic").content
    )
    assert tic.sum() > eic.sum()
    r = client.put(
        f"/api/documents/{doc_id}/signal", json={"signal": "eic", "mz": [20]}
    )
    assert r.status_code == 400

    # peaks
    assert client.get(f"/api/documents/{doc_id}/peaks").json() is None
    wait_job(client, client.post(f"/api/documents/{doc_id}/peaks", json={"snr": 20}))
    table = client.get(f"/api/documents/{doc_id}/peaks").json()
    assert sorted(r["name"] for r in table["rows"]) == [
        "Decane",
        "Naphthalene",
        "Toluene",
    ]
    assert {"library", "baseline", "assigned_by", "category"} <= set(table["columns"])
    assert not table["stale"]
    assert table["libraries"] == ["test"]
    client.patch(f"/api/libraries/{lib_id}", json={"active": False})
    table = client.get(f"/api/documents/{doc_id}/peaks").json()
    assert table["stale"]
    assert "Identified with test" in table["stale_reason"]
    client.patch(f"/api/libraries/{lib_id}", json={"active": True})

    # peak edits
    toluene = next(r for r in table["rows"] if r["name"] == "Toluene")
    client.post(
        "/api/search",
        json={
            "document_id": doc_id,
            "rt2_index": toluene["rt2_index"],
            "mod_index": toluene["mod_index"],
        },
    )
    r = client.patch(
        f"/api/documents/{doc_id}/peaks",
        json=[
            {"peak_id": toluene["peak_id"], "hit_rank": 1},
            {"peak_id": 1 if toluene["peak_id"] != 1 else 2, "name": "My compound"},
        ],
    )
    assert r.status_code == 204
    rows = client.get(f"/api/documents/{doc_id}/peaks").json()["rows"]
    assert sorted(r["assigned_by"] for r in rows) == ["library", "user", "user"]
    assert "My compound" in {r["name"] for r in rows}
    r = client.post(f"/api/documents/{doc_id}/export", json={"what": "peaks"})
    exported = list(csv.DictReader(io.StringIO(r.text)))
    assert exported[0]["baseline"] == "modulation_percentile(percentile=25)"

    # baseline change: spectrum re-extracted, peak table stale
    r = client.patch(
        f"/api/documents/{doc_id}",
        json={"baseline": {"mode": "rolling_percentile", "window": 1}},
    )
    wait_job(client, r)
    sel = client.get(f"/api/documents/{doc_id}/selection").json()
    assert sel["baseline"] == "rolling_percentile(percentile=25, window=1)"
    assert client.get(f"/api/documents/{doc_id}/peaks").json()["stale"]
    r = client.patch(
        f"/api/documents/{doc_id}", json={"baseline": {"mode": "local", "gap": 4}}
    )
    wait_job(client, r)
    header, _ = decode_array(client.get(f"/api/documents/{doc_id}/spectrum").content)
    assert header["baseline"].startswith("local(gap=4, width=5)")
    r = client.post(
        f"/api/documents/{doc_id}/export", json={"what": "spectrum", "format": "txt"}
    )
    assert r.text.splitlines()[0].split("\t")[0].isdigit()
    assert (
        client.patch(f"/api/documents/{doc_id}", json={"normalize": "max"}).status_code
        == 400
    )


def test_api_events(client, ms_files):
    cdf, _, _ = ms_files
    url = f"/api/events?token={TOKEN}"
    with client.websocket_connect(url, headers={"host": f"127.0.0.1:{PORT}"}) as ws:
        r = client.post(
            "/api/documents", json={"path": str(cdf), "params": {"modulation_time": 4}}
        )
        job_id = r.json()["job"]["id"]
        events = []
        while not events or events[-1]["type"] != "job.done":
            events.append(ws.receive_json())
    types = [e["type"] for e in events]
    assert "job.progress" in types
    assert "document.updated" in types
    opened = next(e for e in events if e["type"] == "document.updated")
    assert opened["document"]["kind"] == "ms"
    assert opened["document_id"] == opened["document"]["id"]
    assert events[-1]["job"]["id"] == job_id
    revisions = [e["revision"] for e in events]
    assert revisions == sorted(revisions)


def test_api_jobs_and_errors(client, tmp_path):
    session = client.app.state.session

    def slow(job):
        for i in range(1000):
            job.report(i / 1000)
            time.sleep(0.01)

    job = session.submit("test", "Slow", slow)
    r = client.delete(f"/api/jobs/{job.id}")
    assert r.status_code == 200
    with pytest.raises(JobCancelled):
        job.wait(10)
    assert client.get(f"/api/jobs/{job.id}").json()["status"] == "cancelled"

    r = client.get("/api/documents/nope")
    assert r.status_code == 404
    assert r.json()["type"] == "NotFound"
    vendor = tmp_path / "run.cdf"
    vendor.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 100)
    job = finish_job(client, client.post("/api/documents", json={"path": str(vendor)}))
    assert job["status"] == "failed"
    assert "ANDI" in job["error"]
    assert "Traceback" in job["traceback"]
    r = client.post("/api/documents", json={"path": str(tmp_path / "missing.cdf")})
    assert r.status_code == 400
    assert "not found" in r.json()["detail"]
    r = client.post(
        "/api/documents", json={"path": str(FID_CSV), "params": {"modulation_time": -1}}
    )
    assert r.status_code == 422


def test_api_settings_and_files(client, tmp_path):
    r = client.put("/api/settings", json={"display": {"colormap": "inferno"}})
    assert r.json()["display"] == {
        "colormap": "inferno",
        "scaling": "sqrt",
        "interpolation": "nearest",
    }
    assert client.get("/api/settings").json()["display"]["colormap"] == "inferno"
    (tmp_path / "sub").mkdir()
    (tmp_path / "a.cdf").write_bytes(b"x")
    (tmp_path / ".hidden").write_text("x")
    listing = client.get("/api/fs", params={"path": str(tmp_path)}).json()
    names = [e["name"] for e in listing["entries"]]
    assert names[0] == "sub"  # folders first
    assert "a.cdf" in names
    assert ".hidden" not in names
    assert listing["parent"] == str(tmp_path.parent)
    assert (
        client.get("/api/fs", params={"path": str(tmp_path / "a.cdf")}).status_code
        == 400
    )


def test_openapi_schema(client):
    schema = client.get("/api/openapi.json").json()
    paths = schema["paths"]
    for path in (
        "/api/session",
        "/api/documents",
        "/api/documents/{doc_id}/image",
        "/api/documents/{doc_id}/masks/{mask_id}/operations",
        "/api/search",
        "/api/libraries",
        "/api/fs",
    ):
        assert path in paths
    assert (
        "BaselineSpec" in json.dumps(schema)
        or "LocalBaseline" in schema["components"]["schemas"]
    )


@pytest.mark.skipif(not PY_010.exists(), reason="no local GCxGC-MS data")
def test_session_estimates_modulation_of_local_run(session):
    doc_id = session.wait(session.open_document(PY_010), 60)["document_id"]
    doc = session.document(doc_id)
    assert doc.params.modulation_time == 20.0
    assert doc.summary() == "10 Hz · m/z 45–500 · 318 × 200"  # noqa: RUF001
