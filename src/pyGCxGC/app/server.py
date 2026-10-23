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
import math
import secrets
import socket
import traceback
from pathlib import Path
from typing import Any, Literal

import numpy as np
from fastapi import APIRouter, FastAPI, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, Response

from pyGCxGC.app import schemas as S
from pyGCxGC.app.arrays import MEDIA_TYPE, encode_array, max_pool
from pyGCxGC.app.session import InvalidRequest, NotFound, Session, SessionError

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
    async def server_error(_request: Request, exc: Exception):
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

    @api.get("/fs", response_model=S.FsListing)
    def list_files(path: str | None = None, hidden: bool = False):
        return session.list_dir(path, hidden)

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
    ):
        """2D image, ``float32``, ``(n_rt2, n_mod)`` with row 0 = lowest RT2."""
        result = session.image(doc_id, signal, parse_mz(mz) if mz is not None else None)
        data, fr, fc = result.data, 1, 1
        if max_size:
            data, fr, fc = max_pool(data, max_size, max_size)
        header = grid_header(result.grid)
        header["rt1"]["step"] *= fc
        header["rt2"]["step"] *= fr
        header["rt1"]["count"], header["rt2"]["count"] = data.shape[1], data.shape[0]
        header.update(
            document_id=result.document_id,
            revision=result.revision,
            signal=result.signal,
            mz=result.mz,
            pool=[fr, fc],
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

    @api.patch("/documents/{doc_id}/peaks", status_code=204)
    def edit_peaks(doc_id: str, edits: list[S.PeakEdit]):
        """Rename, assign a hit, categorise or delete peaks."""
        for e in edits:
            session.edit_peak(
                doc_id, e.peak_id, e.name, e.hit_rank, e.delete, e.category
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
    import uvicorn

    session = session or Session()
    token = token or secrets.token_urlsafe(32)
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", port))
    port = sock.getsockname()[1]
    app = create_app(session, token, port)
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning", lifespan="off"))
    if on_ready is not None:
        on_ready(port, token)
    try:
        server.run(sockets=[sock])
    finally:
        session.close()
