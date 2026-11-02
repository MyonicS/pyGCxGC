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
from pyGCxGC.ms.structures import rdkit_available

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


def test_session_integration_reuses_unchanged_masks(session, ms_files, monkeypatch):
    """Edits integrate only the changed mask; results equal MaskSet.integration."""
    import pyGCxGC.app.session as session_module

    cdf, _, _ = ms_files
    doc_id = session.wait(session.open_document(cdf, params={"modulation_time": 4}))[
        "document_id"
    ]
    doc = session.document(doc_id)
    a = session.create_mask(doc_id, "A")
    session.add_shape(
        doc_id, a.id, {"type": "rect", "rt1": [0.3, 1.0], "rt2": [0.5, 2]}
    )
    b = session.create_mask(doc_id, "B")
    session.add_shape(
        doc_id,
        b.id,
        {"type": "polygon", "vertices": [[0.8, 1], [1.8, 1.2], [1.2, 3.5]]},
    )
    c = session.create_mask(doc_id, "C")
    op = session.add_shape(
        doc_id, c.id, {"type": "rect", "rt1": [2.0, 2.5], "rt2": [0.1, 0.4]}
    )
    session.add_shape(
        doc_id, b.id, {"type": "rect", "rt1": [1, 1.2], "rt2": [1, 2]}, "subtract"
    )

    def check():
        result = session.integration(doc_id)
        table = doc.masks.integration(doc.grid, doc.integration_signal())
        got = [
            (r["name"], r["volume"], r["percent"], r["n_pixels"])
            for r in result["masks"]
        ]
        got.append(
            (
                "unassigned",
                result["unassigned"]["volume"],
                result["unassigned"]["percent"],
                result["unassigned"]["n_pixels"],
            )
        )
        want = [(n, r.volume, r.percent, r.n_pixels) for n, r in table.iterrows()]
        assert got == want  # bit for bit
        assert result["total"] == table.attrs["total"]
        overlaps = [(*o["names"], o["n_pixels"]) for o in result["overlaps"]]
        assert overlaps == doc.masks.overlaps(doc.grid)
        return result

    assert check()["overlaps"]  # A and B overlap
    calls = []
    real = session_module._masked_volume
    monkeypatch.setattr(
        session_module, "_masked_volume", lambda v, w: calls.append(1) or real(v, w)
    )
    session.replace_shape(
        doc_id, c.id, op, {"type": "rect", "rt1": [2.0, 2.6], "rt2": [0.1, 0.4]}
    )
    check()
    assert len(calls) == 2  # the changed mask and the union
    calls.clear()
    session.undo_masks(doc_id)
    session.delete_mask(doc_id, a.id)
    check()
    # C changed back by the undo, and the union; B is reused
    assert len(calls) == 2
    calls.clear()
    session.update_document(doc_id, shift=1)
    session.wait(next(j for j in session.jobs.values() if j.kind == "process"))
    check()
    assert len(calls) == 3  # new signal: every mask again, plus the union


def test_batch_integration(client, ms_files, tmp_path):
    """INT-4: one CSV row per file, equal to integrating each file in the app."""
    cdf, _, _ = ms_files
    other = write_andi(synthetic_run(seed=5)[0], tmp_path / "other.cdf")
    (tmp_path / "broken.cdf").write_bytes(b"CDF\x01 not really")
    params = {"modulation_time": 4, "shift": 1}
    doc_id = wait_job(
        client, client.post("/api/documents", json={"path": str(cdf), "params": params})
    )["document_id"]
    masks = f"/api/documents/{doc_id}/masks"
    box = client.post(masks, json={"name": "Box"}).json()["masks"][0]["id"]
    client.post(
        f"{masks}/{box}/operations",
        json={"shape": {"type": "rect", "rt1": [0.3, 1.5], "rt2": [0.5, 2.5]}},
    )
    client.put(f"/api/documents/{doc_id}/signal", json={"signal": "eic", "mz": [91]})
    for _ in range(100):
        if client.get(f"/api/documents/{doc_id}").json()["signal"] == "eic":
            break
        time.sleep(0.05)
    out = tmp_path / "batch.csv"
    files = [str(cdf), str(other), str(tmp_path / "broken.cdf")]
    r = client.post(
        f"/api/documents/{doc_id}/batch", json={"paths": files, "path": str(out)}
    )
    result = wait_job(client, r)
    assert result["n_files"] == 3
    assert [f["file"] for f in result["failed"]] == files[2:]
    rows = list(csv.DictReader(io.StringIO(result["csv"])))
    assert out.read_bytes() == result["csv"].encode()
    assert [row["file"] for row in rows] == files
    assert rows[0]["signal"] == "EIC 91"
    assert rows[0]["shift"] == "1"
    assert rows[2]["error"]
    assert not rows[2]["Box"]

    # the same as opening each file and integrating it
    assert (
        float(rows[0]["Box"])
        == client.get(f"/api/documents/{doc_id}/integration").json()["masks"][0][
            "volume"
        ]
    )
    other_id = wait_job(
        client,
        client.post("/api/documents", json={"path": str(other), "params": params}),
    )["document_id"]
    client.post(
        f"/api/documents/{other_id}/masks/import", json={"from_document": doc_id}
    )
    client.put(f"/api/documents/{other_id}/signal", json={"signal": "eic", "mz": [91]})
    for _ in range(100):
        if client.get(f"/api/documents/{other_id}").json()["signal"] == "eic":
            break
        time.sleep(0.05)
    expected = client.get(f"/api/documents/{other_id}/integration").json()
    assert float(rows[1]["Box"]) == expected["masks"][0]["volume"]
    assert float(rows[1]["unassigned"]) == expected["unassigned"]["volume"]

    assert (
        client.post(f"/api/documents/{doc_id}/batch", json={"paths": []}).status_code
        == 422
    )
    r = client.post(f"/api/documents/{doc_id}/batch", json={"paths": ["/nope.cdf"]})
    assert r.status_code == 400


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
    # exactly as in Python (double precision, not the float32 display image)
    masks = session.masks(doc.id)
    assert tic["masks"][0]["volume"] == masks.integrate(data)[mask.name]
    expected = masks.integrate(data, data=data.eic_2d([128])[::-1])[mask.name]
    assert eic["masks"][0]["volume"] == expected
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


def test_document_events_carry_the_new_revision(session, ms_files):
    """The info in a document.updated event has the revision of the event."""
    cdf, _, _ = ms_files
    events = []
    session.subscribe(events.append)
    doc_id = session.wait(session.open_document(cdf, params={"modulation_time": 4}))[
        "document_id"
    ]
    session.wait(session.update_document(doc_id, shift=2))
    updates = [e for e in events if e["type"] == "document.updated"]
    assert [u["document"]["revision"] for u in updates] == [
        u["revision"] for u in updates
    ]
    assert updates[-1]["document"]["params"]["shift"] == 2
    assert session.document(doc_id).revision == updates[-1]["revision"]
    assert session.image(doc_id).revision == updates[-1]["revision"]


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
    r = client.post("/api/documents", json={"path": str(vendor)})
    assert r.status_code == 400
    assert "andi-ms or andi-chrom" in r.json()["detail"]
    broken = tmp_path / "broken.csv"
    broken.write_text("RT(milliseconds),RT(minutes),RI,45\nnot,a,number,x\n")
    job = finish_job(client, client.post("/api/documents", json={"path": str(broken)}))
    assert job["status"] == "failed"
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


def test_openapi_snapshot_is_current(client):
    """frontend/openapi.json (input of the TypeScript types) matches the API."""
    sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
    try:
        import export_openapi
    finally:
        sys.path.pop(0)
    if not export_openapi.OUT.exists():
        pytest.skip("no frontend/openapi.json")
    current = export_openapi.dumps(client.app.openapi())
    assert export_openapi.OUT.read_text("utf-8") == current, (
        "The API changed: run `python scripts/export_openapi.py` and "
        "`pnpm gen:api` in frontend/"
    )


def test_api_probe_file(client, ms_files, tmp_path):
    cdf, _, _ = ms_files
    probe = client.get("/api/fs/probe", params={"path": str(cdf)}).json()
    assert probe["kind"] == "ms"
    assert probe["error"] is None
    assert not probe["needs_modulation_time"]

    probe = client.get("/api/fs/probe", params={"path": str(FID_CSV)}).json()
    assert probe["kind"] == "fid"
    assert probe["needs_modulation_time"]
    client.put("/api/settings", json={"fid": {"modulation_time": 20}})
    probe = client.get("/api/fs/probe", params={"path": str(FID_CSV)}).json()
    assert not probe["needs_modulation_time"]

    (tmp_path / "x.bin").write_bytes(b"\0" * 10)
    probe = client.get("/api/fs/probe", params={"path": str(tmp_path / "x.bin")})
    assert probe.json()["kind"] is None
    assert "chromparser can read" in probe.json()["error"]
    probe = client.get("/api/fs/probe", params={"path": str(tmp_path / "missing")})
    assert "not found" in probe.json()["error"]


def test_api_image_region(client, ms_files):
    cdf, _, _ = ms_files
    r = client.post(
        "/api/documents", json={"path": str(cdf), "params": {"modulation_time": 4}}
    )
    doc_id = wait_job(client, r)["document_id"]
    url = f"/api/documents/{doc_id}/image"
    full_header, full = decode_array(client.get(url).content)
    assert full_header["region"] == [0, 80, 0, 40]
    assert full_header["full_shape"] == [80, 40]

    header, part = decode_array(client.get(url, params={"region": "10,30,5,9"}).content)
    np.testing.assert_array_equal(part, full[10:30, 5:9])
    assert header["region"] == [10, 30, 5, 9]
    assert header["rt1"]["start"] == pytest.approx(
        full_header["rt1"]["start"] + 5 * full_header["rt1"]["step"]
    )
    assert header["rt2"]["start"] == pytest.approx(10 * 0.05)
    assert header["rt2"]["count"] == 20

    # clipped to the image, then pooled
    params = {"region": "-5,100,30,99", "max_size": 16}
    header, pooled = decode_array(client.get(url, params=params).content)
    assert header["region"] == [0, 80, 30, 40]
    assert header["pool"] == [5, 1]
    np.testing.assert_array_equal(pooled, max_pool(full[:, 30:], 16, 16)[0])
    assert header["rt2"]["step"] == pytest.approx(0.25)

    assert client.get(url, params={"region": "1,2,3"}).status_code == 400
    assert client.get(url, params={"region": "90,99,0,5"}).status_code == 400


@pytest.mark.skipif(not PY_010.exists(), reason="no local GCxGC-MS data")
def test_session_estimates_modulation_of_local_run(session):
    doc_id = session.wait(session.open_document(PY_010), 60)["document_id"]
    doc = session.document(doc_id)
    assert doc.params.modulation_time == 20.0
    assert doc.summary() == "10 Hz · m/z 45–500 · 318 × 200"  # noqa: RUF001


# ------------------------------------------------------------------ milestone 3
def open_ms(session, ms_files) -> str:
    cdf = ms_files[0]
    job = session.open_document(cdf, params={"modulation_time": 4})
    return session.wait(job, 60)["document_id"]


def wait_jobs(session, kind: str) -> None:
    """Wait for all jobs of a kind (also those started by other jobs)."""
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        pending = [j for j in session.jobs.values() if j.kind == kind and not j.done]
        if not pending:
            return
        for j in pending:
            with contextlib.suppress(Exception):
                j.wait(60)
    raise TimeoutError(kind)


def test_selection_is_searched_again_when_libraries_change(session, ms_files):
    doc_id = open_ms(session, ms_files)
    _, mod, rt2 = ms_files[2][0]  # toluene
    sel = session.search(doc_id, rt2_index=round(rt2 / 0.05), mod_index=mod)
    assert sel.hits == []
    events = []
    session.subscribe(events.append)

    lib_id = session.wait(session.add_library(ms_files[1]), 60)["library_id"]
    wait_jobs(session, "search")
    doc = session.document(doc_id)
    assert doc.selection is sel  # the same point, searched again
    assert sel.hits[0].name == "Toluene"
    changed = [e for e in events if e["type"] == "selection.changed"]
    assert changed[-1]["selection"]["id"] == sel.id
    assert changed[-1]["selection"]["hits"][0]["name"] == "Toluene"

    # new search settings
    session.update_settings({"search": {"n_hits": 2}})
    wait_jobs(session, "search")
    assert len(sel.hits) == 2
    session.set_library_active(lib_id, False)
    wait_jobs(session, "search")
    assert sel.hits == []
    assert sel.libraries == []

    # re-processing re-extracts and re-searches the point, keeping its id
    session.set_library_active(lib_id, True)
    wait_jobs(session, "search")
    session.wait(session.update_document(doc_id, snr=2.0), 60)
    assert doc.selection.id == sel.id
    assert doc.selection.hits[0].name == "Toluene"
    assert doc.selection.spectrum.snr == 2.0


def test_search_mass_range_setting(session):
    assert session.search_parameters((45, 200)).mz_min == 45
    session.update_settings({"search": {"mz_min": 60, "mz_max": 300}})
    params = session.search_parameters((45, 200))
    assert (params.mz_min, params.mz_max) == (60, 200)
    assert session.search_parameters().mz_min == 60


def test_region_spectrum(session, ms_files):
    doc_id = open_ms(session, ms_files)
    session.wait(session.add_library(ms_files[1]), 60)
    mask = session.create_mask(doc_id, "toluene")
    # toluene: modulation 10 (4 s each), RT2 1.0 s
    session.add_shape(
        doc_id,
        mask.id,
        {"type": "rect", "rt1": [8 * 4 / 60, 13 * 4 / 60], "rt2": [0.6, 1.4]},
    )
    sel = session.select(doc_id, mask_id=mask.id)
    info = sel.info()
    assert info["region"] == {"mask_id": mask.id, "name": "toluene", "n_pixels": 80}
    assert info["scan"] == -1
    assert info["rt1"] == pytest.approx(10 * 4 / 60, abs=0.01)
    assert info["rt2"] == pytest.approx(1.0, abs=0.05)
    spec = sel.spectrum.corrected
    assert spec.mz[np.argmax(spec.intensity)] == 91
    assert (sel.spectrum.raw.intensity >= spec.intensity).all()
    assert session.search(doc_id).hits[0].name == "Toluene"
    text = session.export(doc_id, "spectrum", "msp")[0].decode()
    assert text.startswith("Name: run.cdf region toluene\n")
    assert '"mask=toluene" "n_pixels=80"' in text

    data = session.document(doc_id).data
    with pytest.raises(ValueError, match="shape"):
        data.region_spectrum(np.ones((3, 3)))
    session.create_mask(doc_id, "empty")
    empty = session.masks(doc_id).masks[-1].id
    with pytest.raises(InvalidRequest, match="empty"):
        session.select(doc_id, mask_id=empty)


def test_reidentify_peaks_keeps_user_edits(session, ms_files):
    doc_id = open_ms(session, ms_files)
    session.wait(session.add_library(ms_files[1]), 60)
    session.wait(session.detect_peaks(doc_id, snr=10, identify=False), 60)
    table, info = session.peaks(doc_id)
    assert (table["name"] == "").all()
    assert info["libraries"] == []
    n = len(table)
    first, second = (int(p) for p in table["peak_id"][:2])
    session.edit_peak(doc_id, first, name="Mine")
    session.edit_peak(doc_id, second, delete=True)

    session.wait(session.reidentify_peaks(doc_id), 60)
    table, info = session.peaks(doc_id)
    assert len(table) == n - 1  # deleted stays deleted
    row = table[table["peak_id"] == first].iloc[0]
    assert (row["name"], row["assigned_by"]) == ("Mine", "user")
    names = set(table["name"])
    assert {"Toluene", "Naphthalene", "Decane"} <= names
    assert info["libraries"] == ["test"]
    assert not info["stale"]

    # after re-processing, the positions are stale: detected again
    session.wait(
        session.update_document(
            doc_id, baseline={"mode": "modulation_percentile", "percentile": 30}
        ),
        60,
    )
    assert session.peaks(doc_id)[1]["stale"]
    job = session.reidentify_peaks(doc_id)
    assert job.kind == "peaks"
    session.wait(job, 60)
    table, info = session.peaks(doc_id)
    assert not info["stale"]
    assert "Mine" not in set(table["name"])


def test_structures_api(client, session, ms_files):
    from test_structures import DECANE, pubchem

    session.pubchem, opener = pubchem({"124-18-5": DECANE})
    doc_id = open_ms(session, ms_files)
    lib_id = session.wait(session.add_library(ms_files[1]), 60)["library_id"]
    url = f"/api/libraries/{lib_id}/spectra"

    # from the library entry
    toluene = client.get(f"{url}/0/structure").json()
    assert toluene["status"] == "found"
    assert toluene["structure"]["smiles"] == "Cc1ccccc1"
    assert toluene["structure"]["source"] == "test"
    assert toluene["depiction"] == rdkit_available()
    key = toluene["structure"]["key"]
    if rdkit_available():
        light = client.get(f"/api/structures/{key}.svg")
        assert light.status_code == 200
        assert light.headers["content-type"].startswith("image/svg+xml")
        dark = client.get(f"/api/structures/{key}.svg?theme=dark&w=120&h=90")
        assert "width='120px'" in dark.text
        assert dark.text != light.text
    assert client.get("/api/structures/unknown.svg").status_code == 404

    # Decane needs PubChem: ask (default), off, then on
    assert client.get(f"{url}/2/structure").json()["status"] == "ask"
    client.put("/api/settings", json={"structures": {"online": "off"}})
    assert client.get(f"{url}/2/structure").json()["status"] == "offline"
    assert opener.calls == []

    # an explicit request overrides the setting; unknown compound lookup of an unknown compound
    heavy = client.get(f"{url}/3/structure?online=true").json()
    assert heavy["status"] == "pending"
    session.wait(heavy["job"]["id"], 60)
    assert client.get(f"{url}/3/structure").json()["status"] == "not_found"
    assert [c[1] for c in opener.calls] == [b"name=Heavy"]

    # batch identification prefetches the top hits' structures (STRUCT-5)
    client.put("/api/settings", json={"structures": {"online": "on"}})
    session.wait(session.detect_peaks(doc_id, snr=10), 60)
    wait_jobs(session, "structure")
    assert b"name=124-18-5" in [c[1] for c in opener.calls]
    decane = client.get(f"{url}/2/structure").json()
    assert decane["status"] == "found"
    assert decane["structure"]["source"] == "PubChem"
    assert decane["structure"]["pubchem_cid"] == 15600

    assert client.get(f"{url}/9/structure").status_code == 404


def test_api_region_selection_and_reidentify(client, session, ms_files):
    doc_id = open_ms(session, ms_files)
    mask = session.create_mask(doc_id, "m")
    session.add_shape(
        doc_id, mask.id, {"type": "rect", "rt1": [0.5, 0.9], "rt2": [0.6, 1.4]}
    )
    r = client.put(f"/api/documents/{doc_id}/selection", json={"mask_id": mask.id})
    assert r.status_code == 200, r.text
    assert r.json()["region"]["name"] == "m"
    header, spec = decode_array(client.get(f"/api/documents/{doc_id}/spectrum").content)
    assert header["scan"] == -1
    assert spec[np.argmax(spec[:, 2]), 0] == 91
    r = client.post(f"/api/documents/{doc_id}/peaks/reidentify")
    assert r.status_code == 400  # no peaks yet
    wait_job(client, client.post(f"/api/documents/{doc_id}/peaks", json={}))
    assert wait_job(client, client.post(f"/api/documents/{doc_id}/peaks/reidentify"))[
        "n_peaks"
    ]
