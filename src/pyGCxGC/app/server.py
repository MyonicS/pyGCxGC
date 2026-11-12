"""
HTTP and WebSocket API of the pyGCxGC app (FastAPI).

Security (local only)
---------------------
* The server binds ``127.0.0.1`` on a random free port.
* Every ``/api`` request carries the per-launch token as
  ``Authorization: Bearer <token>``; the WebSocket passes it as the ``token``
  query parameter.
* Requests whose ``Host`` header is not ``127.0.0.1:<port>`` or
  ``localhost:<port>`` are rejected (DNS rebinding). CORS is off.

Commands and metadata are JSON; arrays use the binary format of
:mod:`pyGCxGC.app.arrays`. Events are pushed over ``/api/events``.

Start a server with ``python -m pyGCxGC.app`` (prints ``READY <port>``).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import math
import secrets
import socket
import threading
import time
import traceback
from pathlib import Path
from typing import Any, Literal

import numpy as np
from fastapi import APIRouter, FastAPI, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, Response

from pyGCxGC.app import schemas as S
from pyGCxGC.app.arrays import MEDIA_TYPE, encode_array, max_pool
from pyGCxGC.app.session import InvalidRequest, NotFound, Session, SessionError

log = logging.getLogger("pyGCxGC.app")
STATIC_DIR = Path(__file__).parent / "static"
BINARY = {200: {"content": {MEDIA_TYPE: {}}, "description": "Binary array"}}
ERRORS: dict[int | str, dict[str, Any]] = {
    400: {"model": S.ErrorResponse},
    404: {"model": S.ErrorResponse},
}


# ------------------------------------------------------------------ security
class LocalSecurity:
    """ASGI middleware: Host check for everything, token check for ``/api``."""

    def __init__(self, app, token: str, port: int):
        self.app = app
        self.token = token
        self.hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        headers = {
            k.decode("latin-1").lower(): v.decode("latin-1")
            for k, v in scope["headers"]
        }
        if headers.get("host", "") not in self.hosts:
            await self._reject(scope, receive, send, 403, "Host not allowed")
            return
        if scope["path"].startswith("/api"):
            if scope["type"] == "http":
                supplied = headers.get("authorization", "").removeprefix("Bearer ")
            else:
                query = scope.get("query_string", b"").decode("latin-1")
                params = dict(p.partition("=")[::2] for p in query.split("&") if p)
                supplied = params.get("token", "")
            if not secrets.compare_digest(supplied.encode(), self.token.encode()):
                await self._reject(
                    scope, receive, send, 401, "Invalid or missing token"
                )
                return
        await self.app(scope, receive, send)

    @staticmethod
    async def _reject(scope, receive, send, status: int, detail: str):
        if scope["type"] == "websocket":
            await receive()  # websocket.connect
            await send({"type": "websocket.close", "code": 1008, "reason": detail})
            return
        response = JSONResponse(
            {"detail": detail, "type": "Forbidden"}, status_code=status
        )
        await response(scope, receive, send)


# ------------------------------------------------------------------ helpers
def clean(value):
    """Make a value JSON safe (NaN/inf -> None, numpy scalars -> Python)."""
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [clean(v) for v in value]
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def binary(array: np.ndarray, header: dict) -> Response:
    return Response(encode_array(array, clean(header)), media_type=MEDIA_TYPE)


def grid_header(grid) -> dict:
    return {
        "axes": ["rt2", "rt1"],
        "units": {"rt1": "min", "rt2": "s"},
        "rt1": {
            "start": grid.rt1_start,
            "step": grid.modulation_time / 60.0,
            "count": grid.n_mod,
        },
        "rt2": {
            "start": grid.rt2_start,
            "step": grid.sampling_interval,
            "count": grid.n_rt2,
        },
        "P": grid.modulation_time,
        "dt": grid.sampling_interval,
        "grid": grid.to_dict(),
    }


def parse_mz(text: str | None) -> list[int]:
    if not text:
        return []
    try:
        return [int(float(x)) for x in text.replace(",", " ").split()]
    except ValueError:
        raise InvalidRequest(f"Invalid m/z list {text!r}") from None


def parse_region(text: str | None, n_rows: int, n_cols: int) -> tuple[int, ...]:
    """``"row0,row1,col0,col1"`` clipped to the image (whole image if omitted)."""
    if not text:
        return (0, n_rows, 0, n_cols)
    try:
        r0, r1, c0, c1 = (int(x) for x in text.split(","))
    except ValueError:
        raise InvalidRequest(
            f"Invalid region {text!r} (use row0,row1,col0,col1)"
        ) from None
    r0, r1 = max(r0, 0), min(r1, n_rows)
    c0, c1 = max(c0, 0), min(c1, n_cols)
    if r0 >= r1 or c0 >= c1:
        raise InvalidRequest(f"The region {text!r} is outside the image")
    return (r0, r1, c0, c1)


def _params(model) -> dict:
    return model.model_dump(exclude_none=True)


# ------------------------------------------------------------------ app
def create_app(session: Session, token: str, port: int, static_dir: Path | None = None):
    """
    FastAPI application serving ``session``.

    Parameters
    ----------
    session : Session
    token : str
        Per-launch token required on every ``/api`` request.
    port : int
        The port the server listens on (for the ``Host`` check).
    static_dir : Path, optional
        Built frontend to serve at ``/`` (default: ``pyGCxGC/app/static``).
    """
    import pyGCxGC

    app = FastAPI(
        title="pyGCxGC",
        version=pyGCxGC.__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    app.add_middleware(LocalSecurity, token=token, port=port)
    app.state.session = session

    @app.exception_handler(SessionError)
    async def session_error(_request: Request, exc: SessionError):
        status = 404 if isinstance(exc, NotFound) else 400
        cause = exc.__cause__
        tb = "".join(traceback.format_exception(cause)) if cause else None
        return JSONResponse(
            {"detail": str(exc), "type": type(exc).__name__, "traceback": tb},
            status_code=status,
        )

    @app.exception_handler(Exception)
    async def server_error(request: Request, exc: Exception):
        log.error(
            "%s %s failed",
            request.method,
            request.url.path,
            exc_info=(type(exc), exc, exc.__traceback__),
        )
        return JSONResponse(
            {
                "detail": f"{type(exc).__name__}: {exc}",
                "type": type(exc).__name__,
                "traceback": "".join(traceback.format_exception(exc)),
            },
            status_code=500,
        )

    api = APIRouter(prefix="/api", responses=ERRORS)

    # -------------------------------------------------------------- session
    @api.get("/session", response_model=S.SessionInfo)
    def get_session():
        return clean(session.info())

    @api.get("/settings", response_model=dict[str, Any])
    def get_settings():
        return session.settings.as_dict()

    @api.put("/settings", response_model=dict[str, Any])
    def put_settings(changes: dict[str, Any]):
        """Merge ``changes`` into the settings (nested objects are merged)."""
        return session.update_settings(changes)

    @api.put("/session/active", status_code=204)
    def set_active_document(req: S.ActiveDocument):
        """Report the document shown by the app (Python clients see it as active)."""
        session.set_active_document(req.document_id)

    @api.post("/logs/open", status_code=204)
    def open_log_folder():
        """Show the log folder in the system's file manager (spec FILE-2)."""
        from pyGCxGC.app.paths import open_folder

        try:
            open_folder(session.log_dir)
        except OSError as exc:
            raise InvalidRequest(f"Cannot open {session.log_dir}: {exc}") from exc

    # -------------------------------------------------------------- projects
    @api.post("/project/save", response_model=S.ProjectSaved)
    def save_project(req: S.ProjectSave):
        """Save the session as a project file (``*.gcxgc``)."""
        return session.save_project(req.path, req.ui)

    @api.post("/project/open", response_model=S.JobStarted, status_code=202)
    def open_project(req: S.ProjectOpen):
        """Open a project file (job; result ``{"document_ids", "project"}``)."""
        return started(session.open_project(req.path, req.replace))

    @api.post("/project/close", status_code=204)
    def close_project():
        """Close all documents and forget the project."""
        session.close_project()

    @api.get("/fs", response_model=S.FsListing)
    def list_files(path: str | None = None, hidden: bool = False):
        return session.list_dir(path, hidden)

    @api.get("/fs/probe", response_model=S.FileProbe)
    def probe_file(path: str):
        """Detect the kind of a file (FID or MS) before opening it."""
        return session.probe_file(path)

    # -------------------------------------------------------------- jobs
    @api.get("/jobs", response_model=list[S.JobInfo])
    def list_jobs():
        return clean([j.to_dict() for j in session.jobs.values()])

    @api.get("/jobs/{job_id}", response_model=S.JobInfo)
    def get_job(job_id: str):
        return clean(session.job(job_id).to_dict())

    @api.delete("/jobs/{job_id}", response_model=S.JobInfo)
    def cancel_job(job_id: str):
        return clean(session.cancel_job(job_id).to_dict())

    def started(job) -> dict:
        return {"job": clean(job.to_dict())}

    # -------------------------------------------------------------- documents
    @api.post("/documents", response_model=S.JobStarted, status_code=202)
    def open_document(req: S.OpenDocumentRequest):
        """Open a file; the job result is ``{"document_id": ...}``."""
        return started(session.open_document(req.path, req.kind, _params(req.params)))

    @api.get("/documents", response_model=list[S.DocumentInfo])
    def list_documents():
        return clean([d.info() for d in session.documents.values()])

    @api.get("/documents/{doc_id}", response_model=S.DocumentInfo)
    def get_document(doc_id: str):
        return clean(session.document(doc_id).info())

    @api.patch("/documents/{doc_id}", response_model=S.JobStarted, status_code=202)
    def update_document(doc_id: str, params: S.ProcessingParams):
        """Change processing parameters and recompute (job)."""
        return started(session.update_document(doc_id, _params(params)))

    @api.delete("/documents/{doc_id}", status_code=204)
    def close_document(doc_id: str):
        session.close_document(doc_id)

    @api.put("/documents/{doc_id}/view", status_code=204)
    def set_view(doc_id: str, view: dict[str, Any] | None = None):
        """Store the frontend's view state (display, zoom); saved in projects."""
        session.set_view(doc_id, view)

    @api.put("/documents/{doc_id}/signal", response_model=S.JobStarted, status_code=202)
    def set_signal(doc_id: str, req: S.SignalRequest):
        """Switch an MS document between TIC and EIC (job)."""
        return started(session.set_signal(doc_id, req.signal, req.mz))

    @api.get("/documents/{doc_id}/image", responses=BINARY, response_class=Response)
    def get_image(
        doc_id: str,
        signal: Literal["tic", "eic"] | None = None,
        mz: str | None = Query(None, description='e.g. "91,105"'),
        max_size: int | None = Query(None, ge=16, description="Max-pool larger images"),
        region: str | None = Query(
            None,
            description='"row0,row1,col0,col1": a half-open window of the full '
            "image (clipped to it), taken before pooling",
        ),
    ):
        """
        2D image, ``float32``, ``(n_rt2, n_mod)`` with row 0 = lowest RT2.

        The header's ``rt1``/``rt2`` axes describe the returned pixels (after
        ``region`` and pooling); ``region`` and ``full_shape`` locate them in
        the full image.
        """
        result = session.image(doc_id, signal, parse_mz(mz) if mz is not None else None)
        data, fr, fc = result.data, 1, 1
        n_rows, n_cols = data.shape
        r0, r1, c0, c1 = parse_region(region, n_rows, n_cols)
        data = data[r0:r1, c0:c1]
        if max_size:
            data, fr, fc = max_pool(data, max_size, max_size)
        header = grid_header(result.grid)
        header["rt1"]["start"] += c0 * header["rt1"]["step"]
        header["rt2"]["start"] += r0 * header["rt2"]["step"]
        header["rt1"]["step"] *= fc
        header["rt2"]["step"] *= fr
        header["rt1"]["count"], header["rt2"]["count"] = data.shape[1], data.shape[0]
        header.update(
            document_id=result.document_id,
            revision=result.revision,
            signal=result.signal,
            mz=result.mz,
            value=result.value,
            pool=[fr, fc],
            region=[r0, r1, c0, c1],
            full_shape=[n_rows, n_cols],
            min=float(data.min()) if data.size else 0.0,
            max=float(data.max()) if data.size else 0.0,
        )
        return binary(data, header)

    # -------------------------------------------------------------- spectra
    @api.get("/documents/{doc_id}/spectrum", responses=BINARY, response_class=Response)
    def get_spectrum(
        doc_id: str,
        rt2_index: int | None = None,
        mod_index: int | None = None,
        scan: int | None = None,
        snap: bool = True,
        n_average: int = Query(3, ge=1),
    ):
        """
        Spectrum at a point (or of the selection when no point is given):
        ``float32`` ``(n_mz, 3)`` with the columns ``mz``, ``raw``, ``corrected``.
        """
        point = {"rt2_index": rt2_index, "mod_index": mod_index, "scan": scan}
        if all(v is None for v in point.values()):
            result = session.spectrum(doc_id)
        else:
            result = session.spectrum(doc_id, snap=snap, n_average=n_average, **point)
        table = np.column_stack(
            [result.raw.mz, result.raw.intensity, result.corrected.intensity]
        ).astype(np.float32)
        header = {**result.info(), "columns": ["mz", "raw", "corrected"]}
        return binary(table, header)

    @api.get("/documents/{doc_id}/selection", response_model=S.SelectionInfo | None)
    def get_selection(doc_id: str):
        doc = session.document(doc_id, "ms")
        return clean(doc.selection.info()) if doc.selection else None

    @api.put("/documents/{doc_id}/selection", response_model=S.SelectionInfo)
    def select(doc_id: str, req: S.SelectRequest):
        """Select a point (without searching)."""
        return clean(session.select(doc_id, **_params(req)).info())

    @api.post("/search", response_model=S.SelectionInfo)
    def search(req: S.SearchRequest):
        """
        Search the active libraries. With a document and a point, the point is
        selected first; with a document only, its selection is searched.
        """
        if req.spectrum is not None:
            sel = session.search(
                spectrum=(req.spectrum.mz, req.spectrum.intensity), n_hits=req.n_hits
            )
            return clean({**sel.info(), "document_id": req.document_id or ""})
        point = {}
        if req.rt2_index is not None or req.mod_index is not None:
            point = {
                "rt2_index": req.rt2_index,
                "mod_index": req.mod_index,
                "snap": req.snap,
                "n_average": req.n_average,
            }
        sel = session.search(req.document_id, n_hits=req.n_hits, **point)
        return clean(sel.info())

    # -------------------------------------------------------------- peaks
    @api.post("/documents/{doc_id}/peaks", response_model=S.JobStarted, status_code=202)
    def detect(doc_id: str, req: S.DetectPeaksRequest):
        return started(
            session.detect_peaks(doc_id, req.snr, req.identify, req.n_average)
        )

    @api.get("/documents/{doc_id}/peaks", response_model=S.PeakTable | None)
    def get_peaks(doc_id: str):
        table, info = session.peaks(doc_id)
        if table is None:
            return None
        return clean(
            {
                "document_id": doc_id,
                "columns": list(table.columns),
                "rows": table.to_dict("records"),
                "stale": bool(info.get("stale")),
                "stale_reason": info.get("stale_reason"),
                "libraries": info.get("libraries", []),
                "baseline": info.get("baseline"),
                "snr": info.get("snr"),
            }
        )

    @api.post(
        "/documents/{doc_id}/peaks/reidentify",
        response_model=S.JobStarted,
        status_code=202,
    )
    def reidentify(doc_id: str):
        """Identify the peaks again with the active libraries (keeps user edits)."""
        return started(session.reidentify_peaks(doc_id))

    @api.patch("/documents/{doc_id}/peaks", status_code=204)
    def edit_peaks(doc_id: str, edits: list[S.PeakEdit]):
        """Rename, assign a hit, categorise or delete peaks."""
        for e in edits:
            session.edit_peak(
                doc_id, e.peak_id, e.name, e.hit_rank, e.delete, e.category, hits=e.hits
            )

    # -------------------------------------------------------------- masks
    @api.get("/documents/{doc_id}/masks", response_model=S.MaskSetInfo)
    def get_masks(doc_id: str):
        return clean(session.masks_info(doc_id))

    @api.post(
        "/documents/{doc_id}/masks", response_model=S.MaskSetInfo, status_code=201
    )
    def create_mask(doc_id: str, req: S.MaskCreate):
        session.create_mask(doc_id, req.name, req.color, req.category)
        return clean(session.masks_info(doc_id))

    @api.post("/documents/{doc_id}/masks/import", response_model=S.MaskSetInfo)
    def import_masks(doc_id: str, req: S.MaskImport):
        """Import masks from a file or folder, another document or a mask set."""
        session.import_masks(
            doc_id, req.path, req.from_document, req.mask_set, req.replace
        )
        return clean(session.masks_info(doc_id))

    @api.post("/documents/{doc_id}/masks/undo", response_model=S.MaskSetInfo)
    def undo(doc_id: str):
        session.undo_masks(doc_id)
        return clean(session.masks_info(doc_id))

    @api.post("/documents/{doc_id}/masks/redo", response_model=S.MaskSetInfo)
    def redo(doc_id: str):
        session.redo_masks(doc_id)
        return clean(session.masks_info(doc_id))

    @api.patch("/documents/{doc_id}/masks/{mask_id}", response_model=S.MaskSetInfo)
    def update_mask(doc_id: str, mask_id: str, req: S.MaskUpdate):
        session.update_mask(doc_id, mask_id, **req.model_dump(exclude_unset=True))
        return clean(session.masks_info(doc_id))

    @api.delete("/documents/{doc_id}/masks/{mask_id}", response_model=S.MaskSetInfo)
    def delete_mask(doc_id: str, mask_id: str):
        session.delete_mask(doc_id, mask_id)
        return clean(session.masks_info(doc_id))

    @api.post(
        "/documents/{doc_id}/masks/{mask_id}/duplicate", response_model=S.MaskSetInfo
    )
    def duplicate_mask(doc_id: str, mask_id: str):
        session.duplicate_mask(doc_id, mask_id)
        return clean(session.masks_info(doc_id))

    @api.post(
        "/documents/{doc_id}/masks/{mask_id}/operations", response_model=S.MaskSetInfo
    )
    def add_shape(doc_id: str, mask_id: str, req: S.OperationCreate):
        """Add or subtract a rectangle, polygon or lasso."""
        session.add_shape(doc_id, mask_id, req.shape.model_dump(), req.mode)
        return clean(session.masks_info(doc_id))

    @api.put(
        "/documents/{doc_id}/masks/{mask_id}/operations/{op_id}",
        response_model=S.MaskSetInfo,
    )
    def replace_shape(doc_id: str, mask_id: str, op_id: str, req: S.OperationUpdate):
        session.replace_shape(doc_id, mask_id, op_id, req.shape.model_dump())
        return clean(session.masks_info(doc_id))

    @api.delete(
        "/documents/{doc_id}/masks/{mask_id}/operations/{op_id}",
        response_model=S.MaskSetInfo,
    )
    def delete_shape(doc_id: str, mask_id: str, op_id: str):
        session.delete_shape(doc_id, mask_id, op_id)
        return clean(session.masks_info(doc_id))

    @api.get(
        "/documents/{doc_id}/masks/{mask_id}/raster",
        responses=BINARY,
        response_class=Response,
    )
    def mask_raster(doc_id: str, mask_id: str):
        """Pixel weights (``uint8``, 255 = inside) in the image layout."""
        data, grid = session.mask_raster(doc_id, mask_id)
        return binary(
            data, {**grid_header(grid), "document_id": doc_id, "mask_id": mask_id}
        )

    @api.get("/documents/{doc_id}/integration", response_model=S.IntegrationResult)
    def integration(doc_id: str):
        return clean(session.integration(doc_id))

    @api.post("/documents/{doc_id}/batch", response_model=S.JobStarted, status_code=202)
    def batch(doc_id: str, req: S.BatchRequest):
        """Integrate the masks on other files (job; the result holds the CSV)."""
        return started(session.batch_integrate(doc_id, req.paths, req.path))

    # -------------------------------------------------------------- export
    @api.post(
        "/documents/{doc_id}/export",
        response_model=S.ExportResult,
        responses={200: {"content": {"application/octet-stream": {}}}},
    )
    def export(doc_id: str, req: S.ExportRequest):
        """Write to ``path`` (returns the path) or return the file as a download."""
        content, name, media_type = session.export(
            doc_id, req.what, req.format, req.path
        )
        if content is None:
            return {"path": name}
        return Response(
            content,
            media_type=media_type,
            headers={"Content-Disposition": f'attachment; filename="{name}"'},
        )

    # -------------------------------------------------------------- libraries
    @api.get("/libraries", response_model=list[S.LibraryInfo])
    def list_libraries():
        return [r.info() for r in session.libraries.values()]

    @api.post("/libraries", response_model=S.JobStarted, status_code=202)
    def add_library(req: S.LibraryAdd):
        """Load a library (or download MoNA with ``path="mona"``) as a job."""
        return started(session.add_library(req.path, req.activate, req.exclusive))

    @api.patch("/libraries/{library_id}", response_model=S.LibraryInfo)
    def update_library(library_id: str, req: S.LibraryUpdate):
        if req.redistributable is not None:
            session.set_library_redistributable(library_id, req.redistributable)
        if req.active is not None:
            session.set_library_active(library_id, req.active, req.exclusive)
        return session.library(library_id).info()

    @api.delete("/libraries/{library_id}", status_code=204)
    def remove_library(library_id: str):
        session.remove_library(library_id)

    @api.get(
        "/libraries/{library_id}/spectra/{index}",
        responses=BINARY,
        response_class=Response,
    )
    def library_spectrum(library_id: str, index: int):
        """One library spectrum, ``float32`` ``(n_peaks, 2)``: ``mz``, ``intensity``."""
        e = session.library_entry(library_id, index)
        header = {
            "columns": ["mz", "intensity"],
            "library_id": library_id,
            "index": e.index,
            "name": e.name,
            "formula": e.formula,
            "mw": e.mw,
            "cas": e.cas,
            "nist_no": e.nist_no,
            "inchikey": e.inchikey,
        }
        return binary(np.column_stack([e.mz, e.intensity]).astype(np.float32), header)

    @api.get(
        "/libraries/{library_id}/spectra/{index}/structure",
        response_model=S.StructureInfo,
    )
    def library_structure(library_id: str, index: int, online: bool | None = None):
        """
        Structure of a library entry. ``online`` overrides the setting
        ``structures.online`` for this request (``pending``: a lookup job).
        """
        return clean(session.structure(library_id, index, online))

    @api.get(
        "/structures/{key}.svg",
        response_class=Response,
        responses={200: {"content": {"image/svg+xml": {}}}},
    )
    def structure_svg(
        key: str,
        theme: Literal["light", "dark"] = "light",
        w: int = Query(300, ge=50, le=1200),
        h: int = Query(220, ge=50, le=1200),
    ):
        """Depiction of a resolved structure (404 without RDKit)."""
        svg = session.structure_svg(key, theme, w, h)
        return Response(
            svg,
            media_type="image/svg+xml",
            headers={"Cache-Control": "private, max-age=86400"},
        )

    app.include_router(api)

    # -------------------------------------------------------------- events
    @app.websocket("/api/events")
    async def events(ws: WebSocket):
        """Session events as JSON messages."""
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()

        def push(event: dict):
            loop.call_soon_threadsafe(queue.put_nowait, clean(event))

        unsubscribe = session.subscribe(push)
        await ws.accept()

        async def pump():
            while True:
                await ws.send_json(await queue.get())

        sender = asyncio.create_task(pump())
        try:
            while True:
                message = await ws.receive()
                if message["type"] == "websocket.disconnect":
                    break
        except WebSocketDisconnect:
            pass
        finally:
            unsubscribe()
            sender.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await sender

    static = static_dir or STATIC_DIR
    if (static / "index.html").exists():
        from fastapi.staticfiles import StaticFiles

        app.mount("/", StaticFiles(directory=static, html=True), name="static")
    return app


# ------------------------------------------------------------------ running
def _bind(port: int) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", port))
    # connections made before uvicorn has started wait in the backlog
    # (READY is printed before ``server.run``)
    sock.listen(2048)
    return sock


def _uvicorn_server(session: Session, token: str, port: int):
    import uvicorn

    app = create_app(session, token, port)
    return uvicorn.Server(uvicorn.Config(app, log_level="warning", lifespan="off"))


def serve(
    session: Session | None = None,
    port: int = 0,
    token: str | None = None,
    on_ready=None,
) -> None:
    """
    Run the server on ``127.0.0.1`` until interrupted.

    Parameters
    ----------
    session : Session, optional
        A new session is created if omitted.
    port : int, default 0
        ``0`` picks a free port.
    token : str, optional
        Random if omitted.
    on_ready : callable, optional
        ``on_ready(port, token)``, called once the socket is bound.
    """
    session = session or Session()
    token = token or secrets.token_urlsafe(32)
    sock = _bind(port)
    port = sock.getsockname()[1]
    server = _uvicorn_server(session, token, port)
    if on_ready is not None:
        on_ready(port, token)
    try:
        server.run(sockets=[sock])
    finally:
        session.close()


class ServerThread:
    """
    The server in a background thread of this process (notebooks, the
    pywebview window): it serves the very objects of the session.

    Examples
    --------
    >>> server = ServerThread(Session()).start()  # doctest: +SKIP
    >>> server.url  # doctest: +SKIP
    'http://127.0.0.1:41234/?token=...'
    >>> server.stop()  # doctest: +SKIP
    """

    def __init__(self, session: Session, port: int = 0, token: str | None = None):
        self.session = session
        self.token = token or secrets.token_urlsafe(32)
        self._sock = _bind(port)
        self.port: int = self._sock.getsockname()[1]
        self._server = _uvicorn_server(session, self.token, self.port)
        self._thread = threading.Thread(
            target=self._server.run,
            kwargs={"sockets": [self._sock]},
            name="pygcxgc-server",
            daemon=True,
        )

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def url(self) -> str:
        """The app's URL, with the access token."""
        return f"{self.base_url}/?token={self.token}"

    @property
    def running(self) -> bool:
        return self._thread.is_alive() and not self._server.should_exit

    def start(self, timeout: float = 15.0) -> ServerThread:
        """Start serving; returns once the server accepts connections."""
        self._thread.start()
        deadline = time.monotonic() + timeout
        while not self._server.started:
            if not self._thread.is_alive():
                raise RuntimeError("The pyGCxGC server did not start")
            if time.monotonic() > deadline:
                raise TimeoutError("The pyGCxGC server did not start in time")
            time.sleep(0.01)
        return self

    def join(self, timeout: float | None = None) -> None:
        """Wait until the server stops (Ctrl+C interrupts the wait)."""
        while self._thread.is_alive():
            self._thread.join(0.5 if timeout is None else timeout)
            if timeout is not None:
                break

    def stop(self, timeout: float = 5.0) -> None:
        """Stop serving (the session stays open)."""
        self._server.should_exit = True
        if self._thread.is_alive():
            self._thread.join(timeout)
        with contextlib.suppress(OSError):
            self._sock.close()
