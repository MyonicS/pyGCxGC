"""
The MCP server: tools, guides and the prompt on one :class:`Session`
(spec mcp.md §3-4).

Tools are thin (MCP-1): every effect is a ``Session`` method. This module
resolves document, mask and library references, checks paths
(:mod:`pyGCxGC.mcp.guard`), converts units and shapes, waits for jobs with
progress notifications, limits and redacts results, and formats them as
structured content with a short text summary.

Annotations are evaluated at registration (the SDK reads them to build the
JSON schemas), so this module does not use ``from __future__ import
annotations``.
"""

import contextlib
import csv
import functools
import io
import logging
import subprocess
import sys
from importlib import resources
from pathlib import Path
from typing import Annotated, Any, Literal

import anyio
import anyio.to_thread
import mcp_types as types
import numpy as np
from mcp.server.mcpserver import Context, Image, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from pyGCxGC.app.session import (
    Document,
    InvalidRequest,
    Job,
    JobCancelled,
    MSDocument,
    NotFound,
    Session,
    SessionError,
    is_project_file,
    project_files,
)
from pyGCxGC.mcp.guard import (
    MAX_ROWS,
    GuardError,
    Roots,
    dumps,
    fit,
    jsonable,
    redact_hit,
    redact_rows,
)
from pyGCxGC.regions.classes import add_label_columns
from pyGCxGC.regions.from_peaks import source_kind

log = logging.getLogger(__name__)

# the structured content of every tool is a JSON object (TOOL-3)
ToolResult = Annotated[types.CallToolResult, dict[str, Any]]

Range = Annotated[
    list[float] | None, Field(description="[min, max]", min_length=2, max_length=2)
]
DocRef = Annotated[
    str | None,
    Field(description="Document id, file name or stem; default: the active document"),
]
MaskRef = Annotated[str, Field(description="Mask id or name")]
WaitS = Annotated[
    float,
    Field(
        description="Seconds to wait for the job (at most 600); after that the job "
        "keeps running: use wait_job",
        ge=0,
        le=600,
    ),
]

PEAK_COLUMNS = (
    "peak_id",
    "rt1_min",
    "rt2_s",
    "volume_percent",
    "snr",
    "name",
    "match",
    "probability",
    "formula",
    "category",
    "subclass",
    "category_source",
    "reviewed",
    "proposal",
)
GUIDES = {
    "group-type": "Group-type workflow: from two runs to a reportable area percentage",
    "classes": "Where compound classes elute in GCxGC and their diagnostic ions",
    "report-template": "What the final report of a group-type result contains",
}

INSTRUCTIONS = """\
pyGCxGC analyses comprehensive two-dimensional GC data (GCxGC-FID and GCxGC-MS) \
on the user's machine. The user may watch the same session in the GC² app.

Units are part of the argument names: RT1 in minutes (rt1_min), RT2 in seconds \
(rt2_s), modulation_s, shift_scans, solvent_cutoff_min, delay_s. Ranges are \
[min, max]. Shapes: {"type": "rect", "rt1_min": [a, b], "rt2_s": [c, d]} or \
{"type": "polygon", "vertices": [[rt1_min, rt2_s], ...]}. A document is its \
id, file name or stem (default: the active one); a mask is its id or name.

Usual order of work: describe_run → set the solvent cut-off (set_processing; 0 \
means checked, none needed) → estimate_delay(MS, FID) and correct it with the \
MS shift_scans before detecting peaks → detect_peaks → propose_classes → \
review_peaks around the class and set_categories (with a reason where you \
disagree with the proposal) → turn repeated judgements into rules \
(define_class, apply_rules) → mask_from_peaks → render to check → copy_masks \
to the FID run → integrate. Render often: the image shows what numbers hide.

Every integration carries a computed grade. "reportable" needs: every peak in \
or near a class mask reviewed, the delay between the runs corrected, no open \
conflicts with user labels, copied masks unchanged, known signal and \
libraries, and an explicit solvent cut-off. Otherwise it is "exploratory" and \
lists what failed; say so in your answer. You cannot set the grade.

User labels win: your labels are "agent" labels, and conflicts are reported, \
not overwritten. Library hits are candidates: check them with the spectrum \
(base peak, molecular ion, diagnostic ions) and the position in the plot. \
Probabilities assume the compound is in the library.

A final answer states the percentages with both denominators (total and \
without excluded masks), the grade and failed criteria, the signal, the \
libraries and thresholds, the review status and label sources, the delay and \
solvent cut-offs, that these are area percentages, and the files written.

Files are read only below the folders the user allowed and written only to \
the output folder. For group-type questions read the guides first: \
gcxgc://guides/group-type, gcxgc://guides/classes and \
gcxgc://guides/report-template."""


# ------------------------------------------------------------------ helpers
def _call(fn, *args, **kwargs):
    """Run a blocking session call in a worker thread."""
    return anyio.to_thread.run_sync(functools.partial(fn, *args, **kwargs))


def _result(
    summary: str, data: dict, *extra: types.ContentBlock
) -> types.CallToolResult:
    """Structured content, and a text block with the summary and the JSON."""
    data = jsonable(data)
    text = f"{summary}\n{dumps(data)}" if data else summary
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=text), *extra],
        structured_content=data,
    )


def _r(value, digits: int):
    if value is None:
        return None
    value = float(value)
    return None if value != value else round(value, digits)


def _with_percent(table):
    """The peak table with each peak's volume in percent of all peaks."""
    total = float(table["volume"].sum())
    return table.assign(volume_percent=table["volume"] / total * 100 if total else 0.0)


def _pct(share) -> str:
    return "n/a" if share is None else f"{share:.1%}"


def _shape(shape: dict) -> dict:
    """A tool shape (TOOL-1) as a Session shape."""
    if not isinstance(shape, dict):
        raise InvalidRequest("A shape is an object with a type")
    kind = shape.get("type")
    if kind == "rect":
        if "rt1_min" not in shape or "rt2_s" not in shape:
            raise InvalidRequest(
                'A rect needs "rt1_min": [a, b] (min) and "rt2_s": [c, d] (s)'
            )
        return {"type": "rect", "rt1": shape["rt1_min"], "rt2": shape["rt2_s"]}
    if kind == "polygon":
        if "vertices" not in shape:
            raise InvalidRequest('A polygon needs "vertices": [[rt1_min, rt2_s], ...]')
        return {"type": "polygon", "vertices": shape["vertices"]}
    raise InvalidRequest(f"Unknown shape type {kind!r}: use 'rect' or 'polygon'")


def _bounds(mask) -> tuple[float, float, float, float] | None:
    """RT1 (min) and RT2 (s) bounds of a mask's added rectangles and polygons."""
    xs: list[float] = []
    ys: list[float] = []
    for op in mask.operations:
        if op.mode != "add":
            continue
        shape = op.shape
        if shape.type == "rect":
            xs += list(shape.rt1)
            ys += list(shape.rt2)
        elif shape.type == "polygon":
            xs += [v[0] for v in shape.vertices]
            ys += [v[1] for v in shape.vertices]
    if not xs:
        return None
    return min(xs), max(xs), min(ys), max(ys)


def _csv_text(rows: list[dict]) -> str:
    buf = io.StringIO()
    if rows:
        writer = csv.DictWriter(buf, fieldnames=list(rows[0]), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return buf.getvalue()


class Gui:
    """
    The GC² window on the server's session (MCP-5): a ``ServerThread`` and
    a pywebview window in its own process (the browser without pywebview).
    FastAPI and uvicorn are imported only here (MCP-3).
    """

    WINDOW = (
        "import sys, webview\n"
        "webview.create_window('GC² (agent session)', sys.argv[1], width=1400, height=900)\n"
        "webview.start()\n"
    )

    def __init__(self, session: Session):
        self.session = session
        self.server = None
        self.window: subprocess.Popen | None = None
        self._browser_opened = False

    @property
    def url(self) -> str | None:
        return (
            self.server.url if self.server is not None and self.server.running else None
        )

    def show(self, open_window: bool = True) -> str:
        if self.url is None:
            try:
                from pyGCxGC.app.server import ServerThread
            except ImportError as exc:
                raise InvalidRequest(
                    'The GC² window needs the app extra (pip install "pyGCxGC[app]")'
                ) from exc
            self.server = ServerThread(self.session).start()
        if open_window:
            self._open()
        return self.server.url

    def _open(self) -> None:
        if self.window is not None and self.window.poll() is None:
            return
        try:
            import webview  # noqa: F401
        except ImportError:
            if not self._browser_opened:
                import webbrowser

                webbrowser.open(self.server.url)
                self._browser_opened = True
            return
        self.window = subprocess.Popen(
            [sys.executable, "-c", self.WINDOW, self.server.url],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def close(self) -> None:
        if self.window is not None and self.window.poll() is None:
            self.window.terminate()
        if self.server is not None:
            self.server.stop()


# ------------------------------------------------------------------ server
class GCxGCMCP:
    """
    The MCP server's state: a session, the path roots and the GUI.

    Parameters
    ----------
    session : Session
    roots : Roots
        Read roots and the output folder (spec §10).
    """

    def __init__(self, session: Session, roots: Roots, lifespan=None):
        self.session = session
        self.roots = roots
        self.gui = Gui(session)
        self.mcp = MCPServer(
            name="gcxgc",
            title="pyGCxGC",
            instructions=INSTRUCTIONS,
            version=_version(),
            lifespan=lifespan,
        )
        self._register_tools()
        self._register_guides()

    # -------------------------------------------------------------- references
    def doc(self, ref: str | None, kind: str | None = None) -> Document:
        """A document by id, file name or stem (TOOL-2); default the active one."""
        docs = list(self.session.documents.values())
        if ref is None:
            active = self.session.active_document_id
            if active is None:
                raise InvalidRequest("No document is open: use open_file first")
            doc = self.session.document(active)
        else:
            ref = str(ref)
            found = [d for d in docs if d.id == ref]
            for key in (
                lambda d: d.name,
                lambda d: Path(d.path).name if d.path else None,
                lambda d: Path(d.name).stem,
                lambda d: Path(d.path).stem if d.path else None,
            ):
                if found:
                    break
                found = [d for d in docs if key(d) == ref]
            if not found:
                listed = ", ".join(f"{d.name} ({d.id})" for d in docs) or "none"
                raise NotFound(f"No document {ref!r}; open documents: {listed}")
            if len(found) > 1:
                listed = ", ".join(f"{d.name} ({d.id}, {d.path})" for d in found)
                raise InvalidRequest(f"{ref!r} is ambiguous: {listed}; use the id")
            doc = found[0]
        if kind is not None and doc.kind != kind:
            raise InvalidRequest(
                f"{doc.name} is a {doc.kind.upper()} document; this needs a "
                f"{kind.upper()} document"
            )
        return doc

    def mask_id(self, doc: Document, ref: str) -> str:
        """A mask by id or name (TOOL-13)."""
        try:
            return doc.masks.get(str(ref)).id
        except KeyError:
            names = ", ".join(m.name for m in doc.masks) or "none"
            raise NotFound(f"No mask {ref!r} in {doc.name}; masks: {names}") from None

    def library_id(self, ref: str) -> str:
        libs = list(self.session.libraries.values())
        found = [r for r in libs if r.id == ref] or [r for r in libs if r.name == ref]
        if len(found) != 1:
            names = ", ".join(f"{r.name} ({r.id})" for r in libs) or "none"
            what = "ambiguous" if found else "unknown"
            raise NotFound(f"Library {ref!r} is {what}; libraries: {names}")
        return found[0].id

    def revisions(self, doc: Document) -> dict:
        """Revisions of a document, so the agent sees edits in between (TOOL-7)."""
        return {"revision": doc.revision, "masks_revision": doc.masks_revision}

    def redistributable_libraries(self) -> set[str]:
        return {r.name for r in self.session.libraries.values() if r.redistributable}

    def library_info(self, rec) -> dict:
        info = rec.info()
        return {
            k: info[k]
            for k in (
                "id",
                "name",
                "status",
                "active",
                "n_spectra",
                "origin",
                "redistributable",
                "error",
            )
        }

    # -------------------------------------------------------------- jobs
    async def wait(self, job: Job, wait_s: float, ctx: Context | None) -> dict | None:
        """
        Wait for a job with progress notifications (TOOL-4). Returns the
        job's result, or ``None`` when it is still running after ``wait_s``.
        Cancelling the request cancels the job.
        """
        deadline = anyio.current_time() + min(max(float(wait_s), 0.0), 600.0)
        last = None
        try:
            while not job.done and anyio.current_time() < deadline:
                if ctx is not None and job.progress != last:
                    last = job.progress
                    with contextlib.suppress(Exception):
                        await ctx.report_progress(
                            job.progress, 1.0, job.message or job.label
                        )
                await anyio.sleep(0.1)
        except anyio.get_cancelled_exc_class():
            job.cancel()
            raise
        if not job.done:
            return None
        return await _call(job.wait, 5)

    @staticmethod
    def running(job: Job) -> types.CallToolResult:
        data = {
            "job_id": job.id,
            "status": job.status,
            "progress": round(job.progress, 3),
            "label": job.label,
        }
        return _result(
            f"{job.label}: still running ({job.progress:.0%}); call wait_job", data
        )

    # -------------------------------------------------------------- formatting
    def session_summary(self) -> dict:
        s = self.session
        active = s.active_document_id
        docs = []
        for d in list(s.documents.values()):
            entry = {
                "id": d.id,
                "name": d.name,
                "kind": d.kind,
                "active": d.id == active,
                "path": d.path,
                "n_masks": len(d.masks),
                **self.revisions(d),
            }
            if isinstance(d, MSDocument):
                entry["n_peaks"] = None if d.peaks is None else len(d.peaks)
            docs.append(entry)
        return {
            "documents": docs,
            "libraries": [self.library_info(r) for r in s.libraries.values()],
            "jobs": [
                {"job_id": j.id, "label": j.label, "progress": round(j.progress, 3)}
                for j in list(s.jobs.values())
                if not j.done
            ],
            "output_folder": str(self.roots.output),
            "read_roots": [str(p) for p in self.roots.read_roots],
            "allow_user_labels": bool(
                s.settings.get("mcp", {}).get("allow_user_labels")
            ),
            "gui_url": self.gui.url,
            "project": s.project_info(),
        }

    def describe(self, doc: Document) -> dict:
        out = self.session.describe(doc.id)
        out.update(self.revisions(doc))
        out["active"] = doc.id == self.session.active_document_id
        return out

    def peak_rows(self, table, columns) -> list[dict]:
        """Rounded, redacted rows (``volume_percent`` from :func:`_with_percent`)."""
        rows = []
        for row in table.to_dict("records"):
            out = {}
            for c in columns:
                if c not in row:
                    continue
                v = row[c]
                if c == "rt1_min":
                    v = _r(v, 3)
                elif c in ("rt2_s", "rt2_width_s"):
                    v = _r(v, 2)
                elif c in ("volume_percent", "probability"):
                    v = _r(v, 3 if c == "volume_percent" else 1)
                elif c in ("match", "reverse_match", "delta_match", "snr"):
                    v = _r(v, 0)
                    v = None if v is None else int(v)
                elif c in ("volume", "height"):
                    v = None if v is None else float(f"{float(v):.4g}")
                out[c] = v
            rows.append(out)
        return redact_rows(jsonable(rows), self.redistributable_libraries())

    def masks_summary(self, doc: Document) -> dict:
        info = self.session.masks_info(doc.id)
        integration = info.get("integration") or {}
        masks = []
        for m in info["masks"]:
            shapes = list(m.get("operations", []))
            entry = {
                "id": m["id"],
                "name": m["name"],
                "category": m.get("category"),
                "color": m.get("color"),
                "n_shapes": len(shapes),
                "volume": m.get("volume"),
                "percent": _r(m.get("percent"), 4),
                "n_pixels": m.get("n_pixels"),
            }
            with contextlib.suppress(KeyError):
                bbox = _bounds(doc.masks.get(m["id"]))
                if bbox is not None:
                    entry["rt1_min"] = [_r(bbox[0], 3), _r(bbox[1], 3)]
                    entry["rt2_s"] = [_r(bbox[2], 2), _r(bbox[3], 2)]
            if m.get("meta", {}).get("copied_from"):
                entry["copied_from"] = m["meta"]["copied_from"].get("document")
            masks.append(entry)
        return {
            "document": doc.name,
            "masks": masks,
            "unassigned": integration.get("unassigned"),
            "overlaps": integration.get("overlaps"),
            "error": info.get("error"),
            "can_undo": info["can_undo"],
            "can_redo": info["can_redo"],
            **self.revisions(doc),
        }

    # -------------------------------------------------------------- registration
    def tool(
        self,
        read_only: bool = False,
        destructive: bool = False,
        idempotent: bool = False,
        open_world: bool = False,
    ):
        """Register a tool; session errors become tool errors (TOOL-5)."""
        annotations = types.ToolAnnotations(
            read_only_hint=read_only,
            destructive_hint=None if read_only else destructive,
            idempotent_hint=None if read_only else idempotent,
            open_world_hint=open_world,
        )

        def decorator(fn):
            @functools.wraps(fn)
            async def wrapper(*args, **kwargs):
                try:
                    return await fn(*args, **kwargs)
                except ToolError:
                    raise
                except JobCancelled:
                    raise ToolError("The job was cancelled") from None
                except (SessionError, GuardError) as exc:
                    raise ToolError(str(exc)) from None
                except TimeoutError as exc:
                    raise ToolError(str(exc)) from None
                except Exception as exc:
                    log.exception("Tool %s failed", fn.__name__)
                    raise ToolError(
                        f"Internal error in {fn.__name__} ({type(exc).__name__}: {exc}); "
                        f"the traceback is in the log ({self.session.log_dir})"
                    ) from None

            self.mcp.add_tool(wrapper, annotations=annotations)
            return fn

        return decorator

    def _register_guides(self) -> None:
        for name, description in GUIDES.items():
            self.mcp.resource(
                f"gcxgc://guides/{name}",
                name=name,
                description=description,
                mime_type="text/markdown",
            )(functools.partial(guide_text, name))

        @self.mcp.prompt(
            name="group-type-area",
            title="Group-type area percentage",
            description="What fraction of the FID chromatogram is a compound class? "
            "Runs the group-type workflow on an FID and an MS run of one sample.",
        )
        def group_type_area(
            fid_file: str, ms_file: str, compound_class: str = "monoaromatics"
        ) -> str:
            return (
                f"Here are {fid_file} (GCxGC-FID) and {ms_file} (GCxGC-MS) of the "
                f"same sample, measured with the same method. What fraction of the "
                f"FID chromatogram is {compound_class}?\n\n"
                "First read the resources gcxgc://guides/group-type, "
                "gcxgc://guides/classes and gcxgc://guides/report-template. Then "
                "follow the group-type workflow with the gcxgc tools, render the "
                "data to check each step, and answer as the report template says, "
                "with the grade of the result."
            )

    def _register_tools(self) -> None:
        s = self.session
        tool = self.tool

        # ---------------------------------------------------------- 4.1 session
        @tool(read_only=True)
        async def session_info() -> ToolResult:
            """
            Open documents, libraries, running jobs, output folder and read roots of
            this pyGCxGC session.
            """
            data = self.session_summary()
            names = ", ".join(f"{d['name']} ({d['kind']})" for d in data["documents"])
            active = [lib["name"] for lib in data["libraries"] if lib["active"]]
            return _result(
                f"Documents: {names or 'none'}. Active libraries: "
                f"{', '.join(active) or 'none'}.",
                data,
            )

        @tool(idempotent=False)
        async def open_file(
            path: Annotated[
                str, Field(description="Chromatogram file or .gcxgc project")
            ],
            ctx: Context,
            kind: Annotated[
                Literal["fid", "ms"] | None,
                Field(description="Detected from the file when omitted"),
            ] = None,
            modulation_s: Annotated[
                float | None,
                Field(description="Modulation time (s); MS: estimated if omitted"),
            ] = None,
            shift_scans: Annotated[
                int | None, Field(description="Phase shift in scans (data points)")
            ] = None,
            solvent_cutoff_min: Annotated[
                float | None,
                Field(description="Solvent cut-off (min); 0 = checked, none"),
            ] = None,
            wait_s: WaitS = 120,
        ) -> ToolResult:
            """
            Open a chromatogram (FID or MS; modulation_s in s, shift_scans in scans,
            solvent_cutoff_min in min) or a project.
            """
            file = self.roots.check_read(path)
            if is_project_file(file):
                for p in await _call(project_files, file):
                    self.roots.check_read(p)
                job = s.open_project(file, replace_all=False)
                result = await self.wait(job, wait_s, ctx)
                if result is None:
                    return self.running(job)
                docs = [self.describe(s.document(i)) for i in result["document_ids"]]
                return _result(
                    f"Opened project {file.name}: {len(docs)} documents.",
                    {
                        "project": result["project"],
                        "documents": docs,
                        "warnings": job.warnings,
                    },
                )
            params = {
                k: v
                for k, v in {
                    "modulation_time": modulation_s,
                    "shift": shift_scans,
                    "solvent_cutoff": solvent_cutoff_min,
                }.items()
                if v is not None
            }
            job = await _call(s.open_document, file, kind, params)
            result = await self.wait(job, wait_s, ctx)
            if result is None:
                return self.running(job)
            doc = s.document(result["document_id"])
            info = await _call(self.describe, doc)
            return _result(
                f"Opened {doc.name} ({doc.kind.upper()}) as {doc.id}; "
                f"{len(info['warnings'])} warnings.",
                info,
            )

        @tool(read_only=True)
        async def describe_run(document: DocRef = None) -> ToolResult:
            """
            Geometry, processing, solvent cut-off (min) with a suggestion, modulation
            estimate (s) and warnings of a run.
            """
            doc = self.doc(document)
            info = await _call(self.describe, doc)
            return _result(
                f"{doc.name}: {doc.kind.upper()}, RT1 {info['rt1_min']} min, RT2 "
                f"{info['rt2_s']} s, {len(info['warnings'])} warnings.",
                info,
            )

        @tool(idempotent=True)
        async def set_processing(
            ctx: Context,
            document: DocRef = None,
            modulation_s: Annotated[
                float | None, Field(description="Modulation time (s)")
            ] = None,
            shift_scans: Annotated[
                int | None,
                Field(
                    description="Phase shift (scans); positive moves peaks to lower RT2"
                ),
            ] = None,
            solvent_cutoff_min: Annotated[
                float | None,
                Field(description="Solvent cut-off (min); 0 = checked, none needed"),
            ] = None,
            baseline: Annotated[
                str | dict | None,
                Field(
                    description="FID: stridewise, global or none. MS: a model name "
                    "(modulation_percentile, rolling_percentile, none) or a model dict"
                ),
            ] = None,
            channel_snr: Annotated[
                float | None,
                Field(
                    description="MS: ion channels below this S/N are zeroed (0 = off)"
                ),
            ] = None,
            sampling_interval_s: Annotated[
                float | None, Field(description="FID sampling interval (s)")
            ] = None,
            normalize: Annotated[
                Literal["volume", "max", "none"] | None, Field(description="FID only")
            ] = None,
            wait_s: WaitS = 120,
        ) -> ToolResult:
            """
            Change the processing (modulation_s in s, shift_scans in scans,
            solvent_cutoff_min in min, baseline) and re-process.
            """
            doc = self.doc(document)
            changes: dict[str, Any] = {
                "modulation_time": modulation_s,
                "shift": shift_scans,
                "solvent_cutoff": solvent_cutoff_min,
            }
            if isinstance(doc, MSDocument):
                if sampling_interval_s is not None or normalize is not None:
                    raise InvalidRequest(
                        "sampling_interval_s and normalize are FID settings"
                    )
                if isinstance(baseline, str):
                    baseline = {"mode": baseline}
                changes.update(baseline=baseline, snr=channel_snr)
            else:
                if channel_snr is not None:
                    raise InvalidRequest("channel_snr is an MS setting")
                if baseline is not None and not isinstance(baseline, str):
                    raise InvalidRequest("FID baselines are stridewise, global or none")
                changes.update(
                    baseline=baseline,
                    sampling_interval=sampling_interval_s,
                    normalize=normalize,
                )
            changes = {k: v for k, v in changes.items() if v is not None}
            if not changes:
                raise InvalidRequest("Give at least one setting to change")
            job = await _call(s.update_document, doc.id, changes)
            if await self.wait(job, wait_s, ctx) is None:
                return self.running(job)
            info = await _call(self.describe, doc)
            return _result(f"Re-processed {doc.name}.", info)

        @tool(idempotent=True)
        async def set_signal(
            ctx: Context,
            document: DocRef = None,
            signal: Literal["tic", "eic"] = "tic",
            mz: Annotated[list[int], Field(description="m/z values of the EIC")] = [],  # noqa: B006
        ) -> ToolResult:
            """
            Show and integrate the TIC or an EIC (m/z values) of an MS run; renders
            and integrations use it.
            """
            doc = self.doc(document, "ms")
            job = await _call(s.set_signal, doc.id, signal, mz)
            result = await self.wait(job, 120, ctx)
            if result is None:
                return self.running(job)
            label = doc.value_label(doc.signal, doc.mz)["quantity"]
            return _result(
                f"{doc.name} now shows {label}.",
                {**result, "label": label, **self.revisions(doc)},
            )

        @tool(idempotent=True)
        async def activate(document: DocRef = None, show: bool = False) -> ToolResult:
            """
            Make a document the active one (the GC² window switches to it); show=true
            opens the window.
            """
            doc = self.doc(document)
            await _call(s.set_active_document, doc.id)
            url = await _call(self.gui.show) if show else self.gui.url
            return _result(
                f"{doc.name} is active" + (f"; GC² window at {url}" if url else "."),
                {"document": doc.name, "id": doc.id, "gui_url": url},
            )

        @tool(destructive=True)
        async def close_document(
            document: Annotated[
                str, Field(description="Document id, file name or stem")
            ],
            force: Annotated[
                bool, Field(description="Close although work is not saved in a project")
            ] = False,
        ) -> ToolResult:
            """
            Close a document; refused while masks, labels or a delay correction are
            unsaved, unless force=true.
            """
            doc = self.doc(document)
            unsaved = await _call(s.unsaved_changes, doc.id)
            if unsaved and not force:
                raise InvalidRequest(
                    f"{doc.name} has {', '.join(unsaved)} that are not saved in a "
                    "project: save_project first, or pass force=true to discard them"
                )
            await _call(s.close_document, doc.id)
            remaining = [d.name for d in s.documents.values()]
            return _result(
                f"Closed {doc.name}.", {"closed": doc.name, "documents": remaining}
            )

        # ---------------------------------------------------------- 4.2 looking
        @tool(read_only=True)
        async def render(
            document: DocRef = None,
            rt1_min: Range = None,
            rt2_s: Range = None,
            signal: Literal["tic", "eic"] | None = None,
            mz: list[int] | None = None,
            scale: Literal["sqrt", "log", "linear"] = "sqrt",
            overlays: list[Literal["masks", "peaks"]] = ["masks", "peaks"],  # noqa: B006
            color_peaks_by: Literal["category", "match", "none"] = "category",
            peak_labels: Literal["none", "id", "name"] = "none",
            width: Annotated[int, Field(ge=200, le=2048)] = 1024,
            height: Annotated[int, Field(ge=200, le=2048)] = 640,
        ) -> ToolResult:
            """
            Render a chromatogram as a PNG (zoom: rt1_min in min, rt2_s in s) with
            masks and peaks, and a text caption.
            """
            doc = self.doc(document)
            png, caption = await _call(
                s.render,
                doc.id,
                rt1_min=rt1_min,
                rt2_s=rt2_s,
                signal=signal,
                mz=mz,
                scale=scale,
                overlays=overlays,
                color_peaks_by=color_peaks_by,
                peak_labels=peak_labels,
                width=width,
                height=height,
            )
            image = Image(data=png, format="png").to_image_content()
            data = {k: v for k, v in caption.items() if k != "text"}
            data.update(self.revisions(doc))
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=caption["text"]), image],
                structured_content=jsonable(data),
            )

        @tool(idempotent=True)
        async def spectrum(
            document: DocRef = None,
            rt1_min: Annotated[float | None, Field(description="RT1 (min)")] = None,
            rt2_s: Annotated[float | None, Field(description="RT2 (s)")] = None,
            peak_id: int | None = None,
            mask: Annotated[
                str | None, Field(description="Summed spectrum of a mask")
            ] = None,
            top: Annotated[int, Field(ge=1, le=100)] = 20,
            search: bool = True,
            n_hits: Annotated[int, Field(ge=1, le=20)] = 5,
        ) -> ToolResult:
            """
            Measured spectrum at RT1 (min) and RT2 (s), a peak or a mask, and its
            library hits; the app shows the same point.
            """
            doc = self.doc(document, "ms")
            given = [
                rt1_min is not None or rt2_s is not None,
                peak_id is not None,
                mask,
            ]
            if sum(bool(g) for g in given) != 1:
                raise InvalidRequest("Give rt1_min and rt2_s, or peak_id, or mask")
            row = None
            if peak_id is not None:
                table, info = s.peaks(doc.id)
                if table is None:
                    raise InvalidRequest("No peak table: run detect_peaks first")
                rows = table[table["peak_id"] == int(peak_id)]
                if not len(rows):
                    raise NotFound(f"No peak {peak_id}")
                row = rows.iloc[0].to_dict()
                point = {
                    "scan": int(row["scan"]),
                    "snap": False,
                    "n_average": int(info.get("n_average", 3)),
                }
                sel = await _call(s.select, doc.id, **point)
            elif mask:
                sel = await _call(s.select, doc.id, mask_id=self.mask_id(doc, mask))
            else:
                if rt1_min is None or rt2_s is None:
                    raise InvalidRequest("Give both rt1_min (min) and rt2_s (s)")
                sel = await _call(s.select, doc.id, rt1=rt1_min, rt2=rt2_s)
            spec = sel.spectrum.corrected
            mzs = np.asarray(spec.mz)
            inten = np.asarray(spec.intensity, dtype=float)
            keep = inten > 0
            mzs, inten = mzs[keep], inten[keep]
            data: dict[str, Any] = {
                "document": doc.name,
                "rt1_min": _r(sel.spectrum.rt1, 3),
                "rt2_s": _r(sel.spectrum.rt2, 2),
                "scan": sel.spectrum.scan if sel.spectrum.scan >= 0 else None,
                "region": sel.region,
                "baseline": sel.spectrum.baseline,
            }
            if len(inten):
                base = float(inten.max())
                order = np.argsort(-inten, kind="stable")[: int(top)]
                data.update(
                    base_peak=int(mzs[int(np.argmax(inten))]),
                    total_ion_count=float(f"{float(inten.sum()):.4g}"),
                    ions=[
                        [int(mzs[k]), round(100 * inten[k] / base, 1)] for k in order
                    ],
                )
            else:
                data["ions"] = []
            note = None
            if search:
                if not s.active_libraries():
                    note = "No active library: add_library or set_library_active"
                else:
                    sel = await _call(s.search, doc.id, n_hits=n_hits)
                    hits = await _call(s.classify_hits, sel.hits or [], row)
                    data["hits"] = [redact_hit(h) for h in hits]
                    data["libraries"] = sel.libraries
                    note = sel.probability_note
            if note:
                data["note"] = note
            first = data.get("hits", [{}])[0] if data.get("hits") else {}
            summary = (
                f"Spectrum at RT1 {data['rt1_min']} min, RT2 {data['rt2_s']} s: base "
                f"peak {data.get('base_peak')}"
                + (f"; best hit {first['name']} ({first['match']})" if first else "")
            )
            return _result(summary, data)

        # ---------------------------------------------------------- 4.3 peaks
        @tool(destructive=True)
        async def detect_peaks(
            ctx: Context,
            document: DocRef = None,
            snr: Annotated[float, Field(gt=0, description="Peak signal-to-noise")] = 10,
            identify: bool = True,
            discard_labels: Annotated[
                bool,
                Field(description="Allow losing reviewed labels (peak ids change)"),
            ] = False,
            wait_s: WaitS = 120,
        ) -> ToolResult:
            """
            Detect (and identify) the peaks of an MS run at a peak S/N; replaces the
            peak table and its ids.
            """
            doc = self.doc(document, "ms")
            job = await _call(s.detect_peaks, doc.id, snr, identify, 3, discard_labels)
            if await self.wait(job, wait_s, ctx) is None:
                return self.running(job)
            table, info = s.peaks(doc.id)
            match = table["match"].astype(float) if len(table) else table.get("match")
            data = {
                "document": doc.name,
                "n_peaks": len(table),
                "n_match_800": int((match >= 800).sum()) if len(table) else 0,
                "n_match_700": int((match >= 700).sum()) if len(table) else 0,
                "libraries": info.get("libraries"),
                "snr": info.get("snr"),
                "probability_note": info.get("probability_note"),
                "warnings": job.warnings,
            }
            return _result(
                f"{len(table)} peaks in {doc.name}; {data['n_match_800']} identified at "
                f"match >= 800.",
                data,
            )

        @tool(read_only=True)
        async def peaks(
            document: DocRef = None,
            category: str | None = None,
            source: Annotated[
                Literal["rule", "agent", "user", "none"] | None,
                Field(description="Label source"),
            ] = None,
            reviewed: bool | None = None,
            min_match: float | None = None,
            min_probability: Annotated[
                float | None, Field(description="Percent")
            ] = None,
            name_contains: str | None = None,
            rt1_min: Range = None,
            rt2_s: Range = None,
            sort_by: Literal["volume", "rt", "match", "peak_id"] = "volume",
            limit: Annotated[int, Field(ge=1, le=MAX_ROWS)] = 25,
            columns: list[str] | None = None,
            out: Annotated[
                str | None,
                Field(description="CSV file name for the full filtered table"),
            ] = None,
        ) -> ToolResult:
            """
            Rows of the peak table (RT1 in min, RT2 in s, volume %), filtered and sorted,
            with labels and identities.
            """
            doc = self.doc(document, "ms")
            table, info = s.peaks(doc.id)
            if table is None:
                raise InvalidRequest("No peak table: run detect_peaks first")
            table = add_label_columns(table)
            total = float(table["volume"].sum()) or 1.0
            sel = np.ones(len(table), dtype=bool)
            if category is not None:
                sel &= (table["category"] == category).to_numpy()
            if source is not None:
                kinds = table["category_source"].map(source_kind).fillna("none")
                kinds = kinds.replace("", "none")
                sel &= (kinds == source).to_numpy()
            if reviewed is not None:
                sel &= (table["reviewed"].astype(bool) == reviewed).to_numpy()
            if min_match is not None:
                sel &= (table["match"].astype(float) >= min_match).to_numpy()
            if min_probability is not None:
                prob = (
                    table["probability"].astype(float)
                    if "probability" in table
                    else None
                )
                if prob is None:
                    raise InvalidRequest("This peak table has no probabilities")
                sel &= (prob.fillna(-1) >= min_probability).to_numpy()
            if name_contains:
                sel &= (
                    table["name"]
                    .astype(str)
                    .str.contains(name_contains, case=False, regex=False)
                ).to_numpy()
            if rt1_min:
                sel &= table["rt1_min"].between(*rt1_min).to_numpy()
            if rt2_s:
                sel &= table["rt2_s"].between(*rt2_s).to_numpy()
            chosen = table[sel].assign(
                volume_percent=table["volume"][sel] / total * 100
            )
            keys = {
                "volume": (["volume"], [False]),
                "rt": (["rt1_min", "rt2_s"], [True, True]),
                "match": (["match"], [False]),
                "peak_id": (["peak_id"], [True]),
            }[sort_by]
            chosen = chosen.sort_values(keys[0], ascending=keys[1], kind="stable")
            cols = list(columns or PEAK_COLUMNS)
            unknown = [c for c in cols if c not in chosen]
            if unknown:
                raise InvalidRequest(
                    f"Unknown columns {unknown}; available: {sorted(chosen.columns)}"
                )
            data: dict[str, Any] = {
                "document": doc.name,
                "total": len(chosen),
                "n_peaks": len(table),
                "truncated": len(chosen) > limit,
                "stale": bool(info.get("stale")),
            }
            if out:
                path = self.roots.output_path(out)
                rows = self.peak_rows(chosen, list(chosen.columns))
                await _call(path.write_text, _csv_text(rows), "utf-8")
                data["written_to"] = str(path)
            data["rows"] = self.peak_rows(chosen.head(limit), cols)
            data = fit(data, self.roots, "peaks")
            return _result(
                f"{data['total']} of {len(table)} peaks match"
                + (f"; showing {limit}" if data["truncated"] else "")
                + ".",
                data,
            )

        @tool(read_only=True)
        async def peak_hits(
            peak_id: int,
            document: DocRef = None,
            n: Annotated[int, Field(ge=1, le=20)] = 10,
        ) -> ToolResult:
            """
            Top library hits of a peak with match, reverse match, delta_match,
            probability (%) and their class.
            """
            doc = self.doc(document, "ms")
            result = await _call(s.peak_hits, doc.id, peak_id, n)
            result["hits"] = [redact_hit(h) for h in result["hits"]]
            result["document"] = doc.name
            first = result["hits"][0] if result["hits"] else None
            return _result(
                f"Peak {peak_id}: {len(result['hits'])} hits"
                + (f", best {first['name']} ({first['match']})." if first else "."),
                result,
            )

        @tool(idempotent=False)
        async def edit_peak(
            peak_id: int,
            document: DocRef = None,
            name: str | None = None,
            hit_rank: Annotated[
                int | None, Field(description="Rank of one of the peak's own hits")
            ] = None,
            delete: bool = False,
        ) -> ToolResult:
            """
            Change a peak's identity (name or one of its hits by rank) or delete it;
            labels go through set_categories.
            """
            doc = self.doc(document, "ms")
            if name is None and hit_rank is None and not delete:
                raise InvalidRequest("Give name, hit_rank or delete=true")
            await _call(
                s.edit_peak,
                doc.id,
                peak_id,
                name,
                hit_rank,
                delete,
                None,
                "peak",
                "agent",
            )
            if delete:
                return _result(
                    f"Deleted peak {peak_id}.",
                    {"deleted": peak_id, **self.revisions(doc)},
                )
            table, _ = s.peaks(doc.id)
            table = _with_percent(table)
            row = self.peak_rows(
                table[table["peak_id"] == peak_id], [*PEAK_COLUMNS, "assigned_by"]
            )
            return _result(
                f"Peak {peak_id} is now {row[0].get('name')!r}.",
                {"row": row[0], **self.revisions(doc)},
            )

        # ---------------------------------------------------------- 4.4 classes
        @tool(read_only=True)
        async def class_schemes() -> ToolResult:
            """Class schemes (built-in and project) with their classes, rules and colours."""
            schemes = await _call(s.class_schemes)
            names = ", ".join(f"{x['name']} ({len(x['classes'])})" for x in schemes)
            data = fit({"schemes": schemes}, self.roots, "class_schemes")
            return _result(f"Schemes: {names}.", data)

        @tool(idempotent=True)
        async def define_class(
            name: str,
            rules: Annotated[
                list[dict],
                Field(
                    description='Criteria that must all hold, e.g. {"formula": {"only": '
                    '["C", "H"], "dbe": [4, 4]}}, {"base_peak": [91, 105]}, '
                    '{"smarts": "c1ccccc1"}, {"any": [...]}; see '
                    "gcxgc://guides/classes"
                ),
            ],
            scheme: Literal["project"] = "project",
            description: str = "",
            color: str | None = None,
            subclasses: list[dict] | None = None,
            vote: dict | None = None,
        ) -> ToolResult:
            """
            Add or replace a class of the project scheme (rules on hits, formula, ions,
            RT in min/s); returns peaks matched.
            """
            result = await _call(
                s.define_class,
                name,
                rules,
                scheme,
                description,
                color,
                None,
                vote,
                subclasses,
            )
            matched = ", ".join(f"{k}: {v}" for k, v in result["matches"].items())
            return _result(
                f"Class {name} defined; matches {matched or 'no peak table yet'}.",
                result,
            )

        @tool(idempotent=True)
        async def propose_classes(
            ctx: Context,
            document: DocRef = None,
            scheme: str = "hydrocarbon-group-type",
            top_k: Annotated[int, Field(ge=1, le=20)] = 5,
            min_match: float = 700,
            min_share: Annotated[float, Field(gt=0, le=1)] = 0.6,
            wait_s: WaitS = 120,
        ) -> ToolResult:
            """
            Propose classes for the peaks from a vote of the top hits (built-in rules);
            proposals are not reviewed.
            """
            doc = self.doc(document, "ms")
            job = await _call(
                s.propose_classes, doc.id, scheme, top_k, min_match, min_share
            )
            result = await self.wait(job, wait_s, ctx)
            if result is None:
                return self.running(job)
            counts = ", ".join(f"{k} {v['n']}" for k, v in result["classes"].items())
            return _result(
                f"Proposals: {counts}; {result['n_without_proposal']} without.",
                {"document": doc.name, **result},
            )

        @tool(read_only=True)
        async def review_peaks(
            document: DocRef = None,
            status: Literal["unreviewed", "proposed", "all"] = "unreviewed",
            order: Literal["rt", "volume"] = "rt",
            cursor: str | None = None,
            batch: Annotated[int, Field(ge=1, le=200)] = 40,
            category: str | None = None,
            rt1_min: Range = None,
            rt2_s: Range = None,
            near_mask: Annotated[
                str | None, Field(description="Only peaks in or near this mask")
            ] = None,
        ) -> ToolResult:
            """
            Evidence cards of peaks to review (rt1 in min, rt2 in s, vol%, hits, ions),
            in batches with a cursor.
            """
            doc = self.doc(document, "ms")
            mask = self.mask_id(doc, near_mask) if near_mask else None
            result = await _call(
                s.review_batch,
                doc.id,
                status,
                order,
                cursor,
                batch,
                category,
                tuple(rt1_min) if rt1_min else None,
                tuple(rt2_s) if rt2_s else None,
                mask,
            )
            result["document"] = doc.name
            result["legend"] = (
                "id, rt1 (min), rt2 (s), vol% of all peaks, h height, sn S/N, hits "
                "[name, formula, match, reverse match, probability %], dm delta_match, "
                "base peak, M candidate, ions [m/z, % of base], label [category, "
                "subclass, source], prop proposal, mask in/near"
            )
            n = len(result["cards"])
            data = fit(result, self.roots, "review_peaks")
            return _result(
                f"{n} cards; {result['remaining']} more"
                + (" (pass next_cursor)." if result["next_cursor"] else "."),
                data,
            )

        @tool(idempotent=True)
        async def set_categories(
            assignments: Annotated[
                list[dict],
                Field(
                    description="Up to 200 of {peak_id, category, subclass?, confidence "
                    "(high|medium|low), reason}; a reason is required where you differ "
                    'from the proposal; category "" removes a label'
                ),
            ],
            document: DocRef = None,
            overwrite_user: Annotated[
                bool,
                Field(description="Replace user labels (only when the user says so)"),
            ] = False,
            as_user: Annotated[
                bool,
                Field(
                    description="A label the user dictated (needs the user's setting)"
                ),
            ] = False,
        ) -> ToolResult:
            """
            Label peaks with classes (agent labels, marked reviewed); user labels are
            kept and conflicts reported.
            """
            doc = self.doc(document, "ms")
            result = await _call(
                s.set_categories, doc.id, assignments, overwrite_user, as_user, "agent"
            )
            result.update(self.revisions(doc))
            return _result(
                f"{result['n_set']} labels set, {len(result['rejected'])} rejected, "
                f"{len(result['conflicts'])} conflicts with user labels.",
                fit(result, self.roots, "set_categories"),
            )

        @tool(idempotent=True)
        async def apply_rules(
            document: DocRef = None,
            scheme: str = "project",
            only_unreviewed: bool = True,
        ) -> ToolResult:
            """
            Label the peaks with a scheme's rules, first matching class wins; user
            labels are kept.
            """
            doc = self.doc(document, "ms")
            result = await _call(s.apply_rules, doc.id, scheme, only_unreviewed, False)
            result.update(self.revisions(doc))
            return _result(
                f"Applied {result['scheme']} to {doc.name}.",
                fit(result, self.roots, "apply_rules"),
            )

        @tool(read_only=True)
        async def review_status(document: DocRef = None) -> ToolResult:
            """
            Peaks and volume share per review status and label source, overall and
            around each class mask.
            """
            doc = self.doc(document, "ms")
            result = await _call(s.review_status, doc.id)
            st = result["status"]
            return _result(
                f"Reviewed {st['reviewed']['n']} of {result['n_peaks']} peaks "
                f"({st['reviewed']['volume_share']:.0%} of the volume).",
                fit({"document": doc.name, **result}, self.roots, "review_status"),
            )

        # ---------------------------------------------------------- 4.5 masks
        @tool(read_only=True)
        async def masks(document: DocRef = None) -> ToolResult:
            """
            Masks of a document with category, colour, RT bounds (min, s), volume,
            percent and overlaps.
            """
            doc = self.doc(document)
            data = await _call(self.masks_summary, doc)
            return _result(f"{len(data['masks'])} masks in {doc.name}.", data)

        @tool(idempotent=False)
        async def create_mask(
            name: str,
            document: DocRef = None,
            category: str | None = None,
            color: Annotated[str | None, Field(description="#rrggbb")] = None,
        ) -> ToolResult:
            """Create an empty mask (add shapes in RT1 min and RT2 s with add_shape)."""
            doc = self.doc(document)
            color = color or s.class_color(category)
            mask = await _call(s.create_mask, doc.id, name, color, category)
            return _result(
                f"Created mask {mask.name} ({mask.id}).",
                {"mask_id": mask.id, "name": mask.name, **self.revisions(doc)},
            )

        @tool(idempotent=True)
        async def update_mask(
            mask: MaskRef,
            document: DocRef = None,
            name: str | None = None,
            category: str | None = None,
            color: str | None = None,
        ) -> ToolResult:
            """Rename a mask or change its category or colour (#rrggbb)."""
            doc = self.doc(document)
            m = await _call(
                s.update_mask,
                doc.id,
                self.mask_id(doc, mask),
                name,
                color,
                None,
                category,
            )
            return _result(
                f"Updated mask {m.name}.",
                {
                    "mask_id": m.id,
                    "name": m.name,
                    "category": m.category,
                    "color": m.color,
                    **self.revisions(doc),
                },
            )

        @tool(destructive=True)
        async def delete_mask(mask: MaskRef, document: DocRef = None) -> ToolResult:
            """Delete a mask (undoable with undo_masks)."""
            doc = self.doc(document)
            await _call(s.delete_mask, doc.id, self.mask_id(doc, mask))
            data = await _call(self.masks_summary, doc)
            return _result(f"Deleted {mask}; {len(data['masks'])} masks left.", data)

        @tool(idempotent=False)
        async def add_shape(
            mask: MaskRef,
            shape: Annotated[
                dict,
                Field(
                    description='{"type": "rect", "rt1_min": [a, b], "rt2_s": [c, d]} or '
                    '{"type": "polygon", "vertices": [[rt1_min, rt2_s], ...]}'
                ),
            ],
            document: DocRef = None,
            mode: Literal["add", "subtract"] = "add",
        ) -> ToolResult:
            """
            Add or subtract a rectangle or polygon (RT1 in min, RT2 in s) to a mask;
            returns its new volume.
            """
            doc = self.doc(document)
            mask_id = self.mask_id(doc, mask)
            op = await _call(s.add_shape, doc.id, mask_id, _shape(shape), mode)
            integration = await _call(s.integration, doc.id)
            row = next(r for r in integration["masks"] if r["mask_id"] == mask_id)
            return _result(
                f"Shape {op} {mode}ed; {row['name']} now has {row['n_pixels']} pixels.",
                {
                    "operation_id": op,
                    "mask_id": mask_id,
                    "volume": row["volume"],
                    "percent": _r(row["percent"], 4),
                    "n_pixels": row["n_pixels"],
                    **self.revisions(doc),
                },
            )

        @tool(destructive=True)
        async def delete_shape(
            mask: MaskRef, operation_id: str, document: DocRef = None
        ) -> ToolResult:
            """Delete one shape (operation) of a mask (undoable with undo_masks)."""
            doc = self.doc(document)
            await _call(s.delete_shape, doc.id, self.mask_id(doc, mask), operation_id)
            data = await _call(self.masks_summary, doc)
            return _result(f"Deleted shape {operation_id}.", data)

        @tool(idempotent=False)
        async def undo_masks(document: DocRef = None, redo: bool = False) -> ToolResult:
            """Undo (or redo) the last mask edit of a document, the agent's or the user's."""
            doc = self.doc(document)
            fn = s.redo_masks if redo else s.undo_masks
            changed = await _call(fn, doc.id)
            data = await _call(self.masks_summary, doc)
            what = "Redone" if redo else "Undone"
            return _result(
                f"{what}."
                if changed
                else "Nothing to " + ("redo." if redo else "undo."),
                {"changed": changed, **data},
            )

        @tool(idempotent=False)
        async def mask_from_peaks(
            document: DocRef = None,
            category: str | None = None,
            peak_ids: list[int] | None = None,
            method: Literal["band", "hull"] = "band",
            margin_rt2_s: Annotated[
                float | None, Field(description="RT2 margin (s)")
            ] = None,
            margin_rt1_min: Annotated[
                float | None, Field(description="RT1 margin (min)")
            ] = None,
            trim: Annotated[float, Field(ge=0, lt=0.5)] = 0.02,
            mask: Annotated[
                str | None, Field(description="Replace the shapes of this mask")
            ] = None,
            name: str | None = None,
            subclass: str | None = None,
        ) -> ToolResult:
            """
            Draw a band or hull mask around a class's peaks (margins in s and min) and
            report coverage and purity.
            """
            doc = self.doc(document, "ms")
            target = self.mask_id(doc, mask) if mask else None
            result = await _call(
                s.mask_from_peaks,
                doc.id,
                category=category,
                peak_ids=peak_ids,
                method=method,
                margin_rt2_s=margin_rt2_s,
                margin_rt1_min=margin_rt1_min,
                trim=trim,
                mask=target,
                name=name,
                subclass=subclass,
            )
            result.update(self.revisions(doc))
            rep = result["report"]
            return _result(
                f"Mask {result['name']}: coverage "
                f"{_pct(rep['coverage']['volume_share'])}, purity "
                f"{_pct(rep['purity'])}, "
                f"{rep['neighbourhood']['n_unreviewed']} unreviewed peaks nearby.",
                fit(result, self.roots, "mask_from_peaks"),
            )

        @tool(idempotent=True)
        async def estimate_delay(
            source: Annotated[str, Field(description="Usually the MS run")],
            target: Annotated[str, Field(description="Usually the FID run")],
        ) -> ToolResult:
            """
            Estimate the RT2 delay (s and source scans) of source after target and the
            shift_scans that corrects it.
            """
            src, tgt = self.doc(source), self.doc(target)
            result = await _call(s.estimate_delay, src.id, tgt.id)
            return _result(
                f"{src.name} is {result['delay_s']} s behind {tgt.name}; suggested "
                f"shift_scans {result['suggested_shift']} for {src.name}"
                + (" (aligned)." if result["aligned"] else "."),
                result,
            )

        @tool(destructive=True)
        async def copy_masks(
            source: str,
            target: str,
            masks: list[str] | None = None,
            replace: Annotated[
                bool, Field(description="Delete the target's masks first (undoable)")
            ] = False,
            alignment: Annotated[
                None, Field(description="Reserved for anchor-peak alignment")
            ] = None,
        ) -> ToolResult:
            """
            Copy masks from one run to another (RT1 min, RT2 s) and report their
            compatibility and the remaining delay.
            """
            if alignment is not None:
                raise InvalidRequest("alignment is reserved and must be null")
            src, tgt = self.doc(source), self.doc(target)
            ids = [self.mask_id(src, m) for m in masks] if masks else None
            new = await _call(s.import_masks, tgt.id, None, src.id, None, replace, ids)
            compat = await _call(s.mask_compatibility, src.id, tgt.id, ids)
            data = {"new_mask_ids": new, "compatibility": compat, **self.revisions(tgt)}
            return _result(
                f"Copied {len(new)} masks from {src.name} to {tgt.name}; "
                f"{len(compat['warnings'])} warnings.",
                fit(data, self.roots, "copy_masks"),
            )

        @tool(read_only=True)
        async def integrate(
            document: DocRef = None,
            masks: list[str] | None = None,
            exclude: Annotated[
                list[str] | None,
                Field(
                    description="Masks left out of percent_of_included; default "
                    "category 'excluded'"
                ),
            ] = None,
            out: Annotated[str | None, Field(description="CSV file name")] = None,
        ) -> ToolResult:
            """
            Integrate the masks: volume, percent of the total and of the included
            total, the signal, and the grade.
            """
            doc = self.doc(document)
            ids = [self.mask_id(doc, m) for m in masks] if masks else None
            excl = (
                [self.mask_id(doc, m) for m in exclude] if exclude is not None else None
            )
            result = await _call(s.integration_report, doc.id, ids, excl)
            for row in result["masks"]:
                row["percent"] = _r(row["percent"], 4)
                row["percent_of_included"] = _r(row["percent_of_included"], 4)
            result["document"] = doc.name
            result.update(self.revisions(doc))
            if out:
                path = self.roots.output_path(out)
                rows = [
                    {
                        "document": doc.name,
                        "signal": result["signal"],
                        **row,
                        "grade": result["grade"],
                    }
                    for row in result["masks"]
                ]
                await _call(path.write_text, _csv_text(rows), "utf-8")
                result["written_to"] = str(path)
            parts = ", ".join(
                f"{r['name']} {r['percent']:.2f} %"
                for r in result["masks"]
                if r["percent"] is not None
            )
            return _result(
                f"{doc.name} ({result['signal']}): {parts or 'no masks'}; grade "
                f"{result['grade']}.",
                fit(result, self.roots, "integrate"),
            )

        # ---------------------------------------------------------- 4.6 libraries
        @tool(open_world=True)
        async def add_library(
            path: Annotated[
                str,
                Field(
                    description="NIST MAINLIB folder, .msp/.zip/.npz file, or 'mona'"
                ),
            ],
            ctx: Context,
            activate: bool = True,
            exclusive: Annotated[
                bool, Field(description="Deactivate the other libraries")
            ] = False,
            wait_s: WaitS = 120,
        ) -> ToolResult:
            """
            Load a spectral library (a path, or 'mona' to download MoNA GC-MS) and
            activate it for searches.
            """
            target = (
                path if path.strip().lower() == "mona" else self.roots.check_read(path)
            )
            job = await _call(s.add_library, target, activate, exclusive)
            result = await self.wait(job, wait_s, ctx)
            if result is None:
                return self.running(job)
            rec = s.library(result["library_id"])
            info = self.library_info(rec)
            return _result(
                f"Library {rec.name}: {info['n_spectra']} spectra, "
                + (
                    "redistributable."
                    if rec.redistributable
                    else "not redistributable."
                ),
                info,
            )

        @tool(idempotent=True)
        async def set_library_active(
            library: Annotated[str, Field(description="Library id or name")],
            active: bool = True,
            exclusive: bool = False,
        ) -> ToolResult:
            """Search with a library or not; peak tables identified with others become stale."""
            lib_id = self.library_id(library)
            await _call(s.set_library_active, lib_id, active, exclusive)
            libs = [self.library_info(r) for r in s.libraries.values()]
            names = ", ".join(lib["name"] for lib in libs if lib["active"]) or "none"
            return _result(f"Active libraries: {names}.", {"libraries": libs})

        # ---------------------------------------------------------- 4.7 files, jobs
        @tool(idempotent=False)
        async def export(
            what: Literal["masks", "mask_set", "integration", "peaks", "spectrum"],
            document: DocRef = None,
            format: Annotated[  # noqa: A002
                str | None,
                Field(
                    description="tiff (masks), json (mask_set), csv, msp or txt (spectrum)"
                ),
            ] = None,
            filename: Annotated[
                str | None, Field(description="Name in the output folder")
            ] = None,
            overwrite: bool = False,
        ) -> ToolResult:
            """
            Write masks (TIFF), the mask set (JSON), the integration or peaks (CSV) or
            the spectrum to the output folder.
            """
            doc = self.doc(document)
            stem = Path(doc.name).stem or "chromatogram"
            if what == "masks":
                if format not in (None, "tiff"):
                    raise InvalidRequest("masks are exported as tiff")
                folder = self.roots.output_path(filename or f"{stem}_masks", overwrite)
                await _call(s.export, doc.id, "masks", "tiff", folder)
                files = sorted(p.name for p in folder.iterdir())
                return _result(
                    f"Wrote {len(files)} TIFF masks to {folder}.",
                    {"path": str(folder), "files": files},
                )
            if what == "peaks":
                if format not in (None, "csv"):
                    raise InvalidRequest("peaks are exported as csv")
                table, _ = s.peaks(self.doc(document, "ms").id)
                if table is None:
                    raise InvalidRequest("No peak table: run detect_peaks first")
                path = self.roots.output_path(
                    filename or f"{stem}_peaks.csv", overwrite
                )
                rows = self.peak_rows(
                    _with_percent(table).sort_values("peak_id"),
                    [*table.columns, "volume_percent"],
                )
                await _call(path.write_text, _csv_text(rows), "utf-8")
            else:
                content, default, _ = await _call(s.export, doc.id, what, format)
                path = self.roots.output_path(filename or default, overwrite)
                await _call(path.write_bytes, content)
            return _result(f"Wrote {path}.", {"path": str(path)})

        @tool(idempotent=False)
        async def save_project(
            filename: Annotated[
                str, Field(description="Project name in the output folder (.gcxgc)")
            ],
            overwrite: bool = False,
        ) -> ToolResult:
            """
            Save the session as a .gcxgc project in the output folder (with the classes
            file next to it).
            """
            name = (
                filename if filename.lower().endswith(".gcxgc") else filename + ".gcxgc"
            )
            path = self.roots.output_path(name, overwrite)
            result = await _call(s.save_project, path)
            return _result(f"Saved {result['path']}.", result)

        @tool(read_only=True)
        async def wait_job(
            job_id: str, ctx: Context, wait_s: WaitS = 120
        ) -> ToolResult:
            """Wait for a running job (wait_s in s) and return its status and result."""
            job = s.job(job_id)
            with contextlib.suppress(SessionError, JobCancelled):
                await self.wait(job, wait_s, ctx)
            if not job.done:
                return self.running(job)
            data = {
                "job_id": job.id,
                "label": job.label,
                "status": job.status,
                "result": job.result if job.status == "done" else None,
                "error": job.error,
                "warnings": job.warnings,
            }
            return _result(f"{job.label}: {job.status}.", data)

        @tool(destructive=False, idempotent=True)
        async def cancel_job(job_id: str) -> ToolResult:
            """Cancel a running job (open, process, detect, propose, add a library)."""
            job = await _call(s.cancel_job, job_id)
            return _result(
                f"Cancel requested for {job.label}.",
                {"job_id": job.id, "status": job.status},
            )


def guide_text(name: str) -> str:
    """The Markdown text of a guide (``pyGCxGC/mcp/guides/<name>.md``)."""
    if name not in GUIDES:
        raise NotFound(f"No guide {name!r}")
    return (
        resources.files("pyGCxGC.mcp").joinpath(f"guides/{name}.md").read_text("utf-8")
    )


def _version() -> str:
    try:
        import pyGCxGC

        return str(pyGCxGC.__version__)
    except Exception:
        return ""


def build_server(session: Session, roots: Roots, lifespan=None) -> GCxGCMCP:
    """The MCP server on ``session`` (see :class:`GCxGCMCP`)."""
    return GCxGCMCP(session, roots, lifespan)


__all__ = ["GUIDES", "INSTRUCTIONS", "GCxGCMCP", "Gui", "build_server", "guide_text"]
