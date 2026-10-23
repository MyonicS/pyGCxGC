"""
Session: the document state of the pyGCxGC app.

All science and all document state (data, processing parameters, masks, peak
tables, library selection) lives in a :class:`Session`. The HTTP server
(:mod:`pyGCxGC.app.server`), the Tk GCxGC-MS viewer and, later, notebooks and
an MCP server are clients of the same methods; every UI action is a Session
method with typed arguments.

This module needs only the core dependencies of pyGCxGC (no FastAPI).

Jobs
----
Long operations (opening files, loading libraries, EICs, peak detection and
identification) run as :class:`Job` objects in a thread pool. Jobs report
progress, can be cancelled and finish with a small JSON-friendly result (e.g.
``{"document_id": ...}``); the objects themselves stay in the session.

Events
------
State changes are published to subscribers (:meth:`Session.subscribe`) as
dicts with ``type``, ``document_id`` and a monotonically increasing
``revision``. Types: ``job.progress``, ``job.done``, ``job.failed``,
``document.updated``, ``masks.updated``, ``peaks.updated``,
``library.updated``, ``selection.changed``.

Examples
--------
>>> from pyGCxGC.app.session import Session
>>> session = Session()
>>> job = session.open_document("run.cdf")  # doctest: +SKIP
>>> doc_id = session.wait(job)["document_id"]  # doctest: +SKIP
>>> session.select(doc_id, rt2_index=74, mod_index=120)  # doctest: +SKIP
"""

from __future__ import annotations

import contextlib
import copy
import csv
import io
import itertools
import json
import threading
import time
import traceback
import uuid
import warnings
import zipfile
from collections import OrderedDict
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import asdict, dataclass, field, fields, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

import numpy as np

from pyGCxGC.main import GCxGC_FID
from pyGCxGC.masks import (
    Grid,
    Mask,
    MaskSet,
    Polygon,
    Rect,
    _masked_volume,
    _signal,
    simplify_lasso,
)
from pyGCxGC.ms.baseline import make_baseline
from pyGCxGC.ms.data import GCxGC_MS, Spectrum, estimate_modulation_time
from pyGCxGC.ms.io import OLE_MAGIC, read_ms_file
from pyGCxGC.ms.library import (
    SpectralLibrary,
    download_mona_gcms,
    library_cache_file,
    load_library,
)
from pyGCxGC.ms.peaks import detect_peaks, identify_peaks
from pyGCxGC.ms.search import Hit, SearchParameters, search_spectrum
from pyGCxGC.parsing import parse_2D_chromatogram, parse_csv
from pyGCxGC.processing import integrate_2D

if TYPE_CHECKING:
    from collections.abc import Callable

    import pandas as pd

MAX_UNDO = 200
IDENTITY_COLUMNS = (
    "name",
    "match",
    "reverse_match",
    "formula",
    "mw",
    "cas",
    "nist_no",
    "library",
)
DEFAULT_SETTINGS: dict[str, Any] = {
    "theme": "system",
    "display": {"colormap": "viridis", "scaling": "sqrt", "interpolation": "nearest"},
    "search": {"mz_power": 0.5, "int_power": 0.5, "n_hits": 20, "min_match": 0},
    "structures": {"online": "ask"},
    "fid": {"modulation_time": None},
    "libraries": [],
    "recent_files": [],
}


# ------------------------------------------------------------------ errors
class SessionError(Exception):
    """Base class of the errors reported to clients."""


class NotFound(SessionError):
    """Unknown document, mask, library, job, ..."""


class InvalidRequest(SessionError):
    """The request cannot be carried out (bad parameters, wrong state)."""


class JobCancelled(Exception):
    """Raised inside a job when it has been cancelled."""


def _new_id() -> str:
    return uuid.uuid4().hex[:10]


# ------------------------------------------------------------------ jobs
class Job:
    """
    A long operation running in the session's worker pool.

    Work functions receive the job and call :meth:`report` with their
    progress; :meth:`report` raises :class:`JobCancelled` once the job has
    been cancelled.
    """

    def __init__(self, session: Session, kind: str, label: str, document_id=None):
        self.id = _new_id()
        self.kind = kind
        self.label = label
        self.document_id = document_id
        self.status = "queued"
        self.progress = 0.0
        self.message = ""
        self.result: Any = None
        self.error: str | None = None
        self.traceback: str | None = None
        self.warnings: list[str] = []
        self.created = time.time()
        self._session = session
        self._cancel = threading.Event()
        self._future: Future | None = None
        self._last_report = 0.0

    def __repr__(self) -> str:
        return f"<Job {self.id} {self.kind} {self.status} {self.progress:.0%}>"

    @property
    def done(self) -> bool:
        return self.status in ("done", "failed", "cancelled")

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def cancel(self) -> None:
        """Request cancellation (honoured at the next progress report)."""
        self._cancel.set()
        if self._future is not None and self._future.cancel():
            self._finish("cancelled", error="Cancelled")

    def check(self) -> None:
        """Raise :class:`JobCancelled` if the job was cancelled."""
        if self._cancel.is_set():
            raise JobCancelled

    def report(self, fraction: float, message: str | None = None) -> None:
        """Set the progress (0-1) and publish it (at most 10 times per second)."""
        self.check()
        self.progress = float(min(max(fraction, 0.0), 1.0))
        if message is not None:
            self.message = message
        now = time.monotonic()
        if now - self._last_report >= 0.1 or self.progress >= 1.0:
            self._last_report = now
            self._session.emit("job.progress", self.document_id, job=self.to_dict())

    def wait(self, timeout: float | None = None) -> Any:
        """Wait for the job and return its result (raises if it failed)."""
        if self._future is not None:
            with contextlib.suppress(Exception):
                self._future.result(timeout)
        if self.status == "done":
            return self.result
        if self.status == "cancelled":
            raise JobCancelled(self.label)
        if self.status == "failed":
            raise SessionError(self.error)
        raise TimeoutError(f"Job {self.label!r} is still running")

    def add_done_callback(self, fn: Callable[[Job], None]) -> None:
        """Call ``fn(job)`` when the job has finished (from a worker thread)."""
        if self._future is None or self._future.done():
            fn(self)
        else:
            self._future.add_done_callback(lambda _f: fn(self))

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "label": self.label,
            "document_id": self.document_id,
            "status": self.status,
            "progress": self.progress,
            "message": self.message,
            "result": self.result if _is_jsonable(self.result) else None,
            "error": self.error,
            "traceback": self.traceback,
            "warnings": list(self.warnings),
        }

    def _run(self, func: Callable[[Job], Any]) -> None:
        if self._cancel.is_set():
            self._finish("cancelled", error="Cancelled")
            return
        self.status = "running"
        self._session.emit("job.progress", self.document_id, job=self.to_dict())
        try:
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                result = func(self)
            self.warnings.extend(str(w.message) for w in caught)
        except JobCancelled:
            self._finish("cancelled", error="Cancelled")
        except Exception as exc:
            self._finish("failed", error=_error_text(exc), tb=traceback.format_exc())
        else:
            self.result = result
            self.progress = 1.0
            self._finish("done")

    def _finish(self, status: str, error: str | None = None, tb: str | None = None):
        self.status = status
        self.error = error
        self.traceback = tb
        kind = "job.done" if status == "done" else "job.failed"
        self._session.emit(kind, self.document_id, job=self.to_dict())


# ------------------------------------------------------------------ settings
class Settings:
    """User settings (a nested dict), optionally persisted as JSON."""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else None
        self._lock = threading.Lock()
        self._data = copy.deepcopy(DEFAULT_SETTINGS)
        if self.path and self.path.exists():
            with contextlib.suppress(OSError, ValueError):
                _deep_update(self._data, json.loads(self.path.read_text("utf-8")))

    def get(self, key: str, default=None):
        with self._lock:
            return copy.deepcopy(self._data.get(key, default))

    def as_dict(self) -> dict:
        with self._lock:
            return copy.deepcopy(self._data)

    def update(self, changes: dict) -> dict:
        """Merge ``changes`` (nested dicts are merged) and save."""
        with self._lock:
            _deep_update(self._data, copy.deepcopy(changes))
            self._save()
            return copy.deepcopy(self._data)

    def _save(self) -> None:
        if self.path is None:
            return
        with contextlib.suppress(OSError):
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._data, indent=2), "utf-8")
            tmp.replace(self.path)


# ------------------------------------------------------------------ parameters
@dataclass
class FIDParams:
    """Processing parameters of an FID document (see ``parse_2D_chromatogram``)."""

    modulation_time: float | None = None  # s; required for FID data
    sampling_interval: float | None = None  # s; None = inferred from the data
    shift: int = 0  # data points (cyclic roll of the rows)
    solvent_cutoff: float = 0.0  # min
    baseline: str = "stridewise"  # stridewise | global | none
    normalize: str = "volume"  # volume | max | none

    def validate(self) -> None:
        if self.modulation_time is None or self.modulation_time <= 0:
            raise InvalidRequest("A positive modulation time (s) is required")
        if self.sampling_interval is not None and self.sampling_interval <= 0:
            raise InvalidRequest("The sampling interval must be positive")
        if self.baseline not in ("stridewise", "global", "none"):
            raise InvalidRequest(f"Unknown FID baseline {self.baseline!r}")
        if self.normalize not in ("volume", "max", "none"):
            raise InvalidRequest(f"Unknown normalisation {self.normalize!r}")
        if self.solvent_cutoff < 0:
            raise InvalidRequest("The solvent cutoff must not be negative")


@dataclass
class MSParams:
    """Processing parameters of a GCxGC-MS document."""

    modulation_time: float | None = None  # s; None = estimated from the TIC
    shift: int = 0  # scans
    baseline: dict = field(
        default_factory=lambda: {"mode": "modulation_percentile", "percentile": 25.0}
    )
    snr: float = 3.0  # channel zeroing threshold, 0 = off

    def validate(self) -> None:
        if self.modulation_time is not None and self.modulation_time <= 0:
            raise InvalidRequest("The modulation time must be positive")
        if self.snr < 0:
            raise InvalidRequest("The S/N threshold must not be negative")
        try:
            self.baseline = make_baseline(self.baseline).to_dict()
        except (TypeError, ValueError) as exc:
            raise InvalidRequest(f"Invalid baseline: {exc}") from exc


def _apply_params(params, changes: dict):
    names = {f.name for f in fields(params)}
    unknown = set(changes) - names
    if unknown:
        raise InvalidRequest(
            f"Unknown parameter(s) for this document: {', '.join(sorted(unknown))}"
        )
    updated = replace(params, **changes)
    updated.validate()
    return updated


# ------------------------------------------------------------------ results
@dataclass
class ImageResult:
    """A 2D image in the API layout (``origin="lower"``, ``(n_rt2, n_mod)``)."""

    document_id: str
    revision: int
    signal: str
    mz: list[int]
    grid: Grid
    data: np.ndarray  # float32


@dataclass
class SpectrumResult:
    """Spectrum at a point, raw and background corrected."""

    document_id: str
    rt2_index: int
    mod_index: int
    scan: int
    rt1: float
    rt2: float
    n_average: int
    snap: bool
    baseline: str
    snr: float
    raw: Spectrum
    corrected: Spectrum

    def info(self) -> dict:
        return {
            "document_id": self.document_id,
            "rt2_index": self.rt2_index,
            "mod_index": self.mod_index,
            "scan": self.scan,
            "rt1": self.rt1,
            "rt2": self.rt2,
            "n_average": self.n_average,
            "snap": self.snap,
            "baseline": self.baseline,
            "snr": self.snr,
        }


@dataclass
class Selection:
    """The selected point of a document, its spectrum and library hits."""

    spectrum: SpectrumResult
    hits: list[Hit] | None = None
    libraries: list[str] = field(default_factory=list)

    def info(self) -> dict:
        return {
            **self.spectrum.info(),
            "hits": None if self.hits is None else [hit_dict(h) for h in self.hits],
            "libraries": list(self.libraries),
        }


def hit_dict(hit: Hit) -> dict:
    """JSON-friendly hit, including the library id."""
    d = hit.as_dict()
    d["match"] = float(hit.match)
    d["reverse_match"] = float(hit.reverse_match)
    d["index"] = hit.index
    d["library_id"] = hit.extra.get("library_id")
    return d


# ------------------------------------------------------------------ documents
class Document:
    """Base class of open chromatograms."""

    kind: ClassVar[str] = ""

    def __init__(self, name: str, path: str | None):
        self.id = _new_id()
        self.name = name
        self.path = path
        self.revision = 0
        self.lock = threading.RLock()
        self.masks = MaskSet()
        self.mask_undo: list[MaskSet] = []
        self.mask_redo: list[MaskSet] = []
        self.masks_revision = 0
        self._integration_cache: tuple | None = None
        self._volume_cache: dict = {}
        self.warnings: list[str] = []

    @property
    def grid(self) -> Grid:
        raise NotImplementedError

    @property
    def params(self):
        raise NotImplementedError

    def image(self, signal: str = "tic", mz=()) -> np.ndarray:
        """Image in the API layout (``float32``)."""
        raise NotImplementedError

    def integration_signal(self):
        """Signal integrated by the masks, in the ``chrom_2D`` layout."""
        raise NotImplementedError

    def summary(self) -> str:
        return ""

    def info(self) -> dict:
        grid = self.grid
        return {
            "id": self.id,
            "kind": self.kind,
            "name": self.name,
            "path": self.path,
            "revision": self.revision,
            "params": asdict(self.params),
            "shape": list(grid.shape),
            "grid": grid.to_dict(),
            "summary": self.summary(),
            "warnings": list(self.warnings),
            "n_masks": len(self.masks),
        }


class FIDDocument(Document):
    """GCxGC-FID chromatogram (OpenChrom CSV)."""

    kind = "fid"

    def __init__(
        self, chrom: GCxGC_FID, params: FIDParams, raw: pd.DataFrame | None, path=None
    ):
        super().__init__(chrom.name or "FID", path)
        self.chrom = chrom
        self._params = params
        self.raw = raw
        self.masks.grid = self.grid

    @classmethod
    def open(cls, path: Path, params: FIDParams) -> FIDDocument:
        raw = parse_csv(path)
        chrom = cls.process(raw, params, path.name)
        return cls(chrom, params, raw, str(path))

    @classmethod
    def from_object(cls, chrom: GCxGC_FID) -> FIDDocument:
        params = FIDParams(
            modulation_time=float(chrom.modulation_time),
            sampling_interval=float(chrom.sampling_interval) / 1000.0,
            shift=int(chrom.shift or 0),
            solvent_cutoff=float(chrom.solvent_cutoff or 0.0),
        )
        raw = chrom.chrom_1D[["Ret.Time[s]", "Absolute Intensity"]].copy()
        return cls(chrom, params, raw)

    @staticmethod
    def process(raw: pd.DataFrame, params: FIDParams, name: str) -> GCxGC_FID:
        params.validate()
        try:
            return parse_2D_chromatogram(
                raw[["Ret.Time[s]", "Absolute Intensity"]].copy(),
                modulation_time=params.modulation_time,
                sampling_interval=(
                    "infer"
                    if params.sampling_interval is None
                    else params.sampling_interval * 1000.0
                ),
                shift=params.shift,
                baseline_type=None if params.baseline == "none" else params.baseline,
                normalize=None if params.normalize == "none" else params.normalize,
                solvent_cutoff=params.solvent_cutoff,
                name=name,
            )
        except AssertionError as exc:
            raise InvalidRequest(str(exc).split("\n")[0].strip()) from exc

    @property
    def params(self) -> FIDParams:
        return replace(
            self._params, sampling_interval=float(self.chrom.sampling_interval) / 1000.0
        )

    @property
    def grid(self) -> Grid:
        return Grid.from_chromatogram(self.chrom)

    def image(self, signal: str = "tic", mz=()) -> np.ndarray:  # noqa: ARG002
        if signal != "tic":
            raise InvalidRequest("FID documents have a single signal ('tic')")
        return np.ascontiguousarray(
            self.chrom.chrom_2D.to_numpy()[::-1], dtype=np.float32
        )

    def integration_signal(self):
        return self.chrom.chrom_2D

    def info(self) -> dict:
        info = super().info()
        info["sampling_inferred"] = self._params.sampling_interval is None
        return info

    def summary(self) -> str:
        g = self.grid
        hz = 1.0 / g.sampling_interval
        return f"FID · {hz:.4g} Hz · {g.n_mod} × {g.n_rt2}"  # noqa: RUF001


class MSDocument(Document):
    """GCxGC-MS chromatogram."""

    kind = "ms"

    def __init__(self, data: GCxGC_MS, params: MSParams, path=None):
        super().__init__(data.name, path)
        self.data = data
        self._params = params
        self.signal = "tic"
        self.mz: list[int] = []
        self.selection: Selection | None = None
        self.peaks: pd.DataFrame | None = None
        self.peak_hits: dict[int, list[Hit]] = {}
        self.peaks_info: dict = {}
        self._images: OrderedDict = OrderedDict()
        self._integration_eic: tuple | None = None
        self.masks.grid = self.grid

    @classmethod
    def open(cls, path: Path, params: MSParams, job: Job | None = None) -> MSDocument:
        raw = read_ms_file(path)
        if job is not None:
            job.report(0.5, "Folding")
        if params.modulation_time is None:
            params = replace(params, modulation_time=estimate_modulation_time(raw))
        data = cls.build(raw, params, path.name)
        return cls(data, params, str(path))

    @classmethod
    def from_object(cls, data: GCxGC_MS) -> MSDocument:
        params = MSParams(
            modulation_time=float(data.modulation_time),
            shift=int(data.shift),
            baseline=data.baseline.to_dict(),
        )
        return cls(data, params)

    @staticmethod
    def build(raw, params: MSParams, name: str) -> GCxGC_MS:
        params.validate()
        try:
            return GCxGC_MS(
                raw,
                params.modulation_time,
                shift=params.shift,
                name=name,
                baseline=params.baseline,
            )
        except ValueError as exc:
            raise InvalidRequest(str(exc)) from exc

    @property
    def params(self) -> MSParams:
        return replace(self._params, modulation_time=float(self.data.modulation_time))

    @property
    def grid(self) -> Grid:
        return Grid.from_chromatogram(self.data)

    def image(self, signal: str = "tic", mz=()) -> np.ndarray:
        mz = tuple(sorted({int(m) for m in mz}))
        if signal == "tic" or (signal == "eic" and not mz):
            key = ("tic", ())
        elif signal == "eic":
            key = ("eic", mz)
        else:
            raise InvalidRequest(f"Unknown signal {signal!r} (use 'tic' or 'eic')")
        with self.lock:
            cached = self._images.get(key)
            data = self.data
        if cached is not None and cached[0] is data:
            return cached[1]
        img = data.tic_2d if key[0] == "tic" else data.eic_2d(list(mz))
        img = np.ascontiguousarray(img, dtype=np.float32)
        with self.lock:
            if self.data is data:
                self._images[key] = (data, img)
                while len(self._images) > 6:
                    self._images.popitem(last=False)
        return img

    def integration_signal(self):
        if self.signal == "tic":
            return self.data.chrom_2D
        # the EIC in double precision, as ``data.eic_2d(mz)[::-1]`` in Python
        # (the image is float32 for display)
        with self.lock:
            data, mz = self.data, tuple(self.mz)
            cached = self._integration_eic
        if cached is not None and cached[0] is data and cached[1] == mz:
            return cached[2]
        signal = data.eic_2d(list(mz))[::-1]
        with self.lock:
            self._integration_eic = (data, mz, signal)
        return signal

    def summary(self) -> str:
        d = self.data
        lo, hi = d.mass_range
        hz = 1.0 / d.sampling_interval
        return f"{hz:.4g} Hz · m/z {lo}–{hi} · {d.n_modulations} × {d.n_rt2}"  # noqa: RUF001

    def info(self) -> dict:
        info = super().info()
        info.update(
            signal=self.signal,
            mz=list(self.mz),
            mass_range=list(self.data.mass_range),
            n_scans=self.data.n_scans,
            baseline=self.data.baseline.describe(),
            has_peaks=self.peaks is not None,
        )
        return info


# ------------------------------------------------------------------ libraries
LICENCES = {
    "nist": "Licensed NIST library: for local use only, never redistribute.",
    "mona": "MoNA: open data, mostly CC BY / CC BY-SA (see each record).",
    "msp": "",
    "npz": "",
}


@dataclass
class LibraryRecord:
    """A spectral library known to the session."""

    id: str
    path: str
    source: str  # nist | mona | msp | npz
    name: str
    status: str = "loading"  # loading | ready | failed
    active: bool = False
    library: SpectralLibrary | None = None
    error: str | None = None
    cache_file: str | None = None

    def info(self) -> dict:
        cached = bool(self.cache_file and Path(self.cache_file).exists())
        return {
            "id": self.id,
            "name": self.name,
            "path": self.path,
            "source": self.source,
            "status": self.status,
            "active": self.active,
            "n_spectra": len(self.library) if self.library is not None else None,
            "licence": LICENCES.get(self.source, ""),
            "cache_file": self.cache_file,
            "cached": cached,
            "error": self.error,
        }


def library_source(path: str | Path) -> str:
    """``nist``, ``mona``, ``msp`` or ``npz``."""
    from pyGCxGC.ms.nist import is_nist_library

    path = Path(path)
    if path.suffix.lower() == ".npz":
        return "npz"
    if is_nist_library(path):
        return "nist"
    return "mona" if "mona" in path.name.lower() else "msp"


# ------------------------------------------------------------------ session
class Session:
    """
    Documents, libraries, jobs and settings of one app instance.

    Parameters
    ----------
    settings_path : str or Path, optional
        JSON file for the user settings (in memory only if omitted).
    cache_dir : str or Path, optional
        Library cache directory (default
        :func:`pyGCxGC.ms.library.default_cache_dir`).
    max_workers : int, default 4
        Size of the job worker pool.
    """

    def __init__(
        self,
        settings_path: str | Path | None = None,
        cache_dir: str | Path | None = None,
        max_workers: int = 4,
    ):
        self.settings = Settings(settings_path)
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.documents: dict[str, Document] = {}
        self.libraries: dict[str, LibraryRecord] = {}
        self.jobs: dict[str, Job] = {}
        self._lock = threading.RLock()
        self._revision = 0
        self._subscribers: list[Callable[[dict], None]] = []
        self._executor = ThreadPoolExecutor(max_workers, thread_name_prefix="pygcxgc")

    def close(self) -> None:
        """Cancel running jobs and stop the worker pool."""
        for job in list(self.jobs.values()):
            if not job.done:
                job.cancel()
        self._executor.shutdown(wait=False, cancel_futures=True)

    # ------------------------------------------------------------ events
    @property
    def revision(self) -> int:
        return self._revision

    def subscribe(self, callback: Callable[[dict], None]) -> Callable[[], None]:
        """Call ``callback(event)`` for every event; returns an unsubscribe function."""
        with self._lock:
            self._subscribers.append(callback)

        def unsubscribe():
            with self._lock, contextlib.suppress(ValueError):
                self._subscribers.remove(callback)

        return unsubscribe

    def emit(self, kind: str, document_id: str | None = None, **data) -> dict:
        """Publish an event to all subscribers."""
        with self._lock:
            self._revision += 1
            event = {
                "type": kind,
                "document_id": document_id,
                "revision": self._revision,
            }
            event.update(data)
            if kind == "document.updated" and document_id in self.documents:
                self.documents[document_id].revision = self._revision
                # the info was taken before this revision existed
                if isinstance(event.get("document"), dict):
                    event["document"] = {
                        **event["document"],
                        "revision": self._revision,
                    }
            subscribers = list(self._subscribers)
        for callback in subscribers:
            with contextlib.suppress(Exception):
                callback(event)
        return event

    # ------------------------------------------------------------ jobs
    def submit(
        self,
        kind: str,
        label: str,
        func: Callable[[Job], Any],
        document_id: str | None = None,
    ) -> Job:
        """Run ``func(job)`` in the worker pool."""
        job = Job(self, kind, label, document_id)
        with self._lock:
            self.jobs[job.id] = job
            finished = [j for j in self.jobs.values() if j.done]
            for old in finished[: max(len(finished) - 50, 0)]:
                self.jobs.pop(old.id, None)
        job._future = self._executor.submit(job._run, func)
        return job

    def job(self, job_id: str) -> Job:
        try:
            return self.jobs[job_id]
        except KeyError:
            raise NotFound(f"No job {job_id!r}") from None

    def cancel_job(self, job_id: str) -> Job:
        job = self.job(job_id)
        job.cancel()
        return job

    def wait(self, job: Job | str, timeout: float | None = None) -> Any:
        """Wait for a job and return its result."""
        return (self.job(job) if isinstance(job, str) else job).wait(timeout)

    # ------------------------------------------------------------ session
    def info(self) -> dict:
        import pyGCxGC

        return {
            "version": pyGCxGC.__version__,
            "revision": self._revision,
            "documents": [d.info() for d in self.documents.values()],
            "libraries": [r.info() for r in self.libraries.values()],
            "jobs": [j.to_dict() for j in self.jobs.values() if not j.done],
            "settings": self.settings.as_dict(),
        }

    def update_settings(self, changes: dict) -> dict:
        return self.settings.update(changes)

    # ------------------------------------------------------------ documents
    def document(self, document_id: str, kind: str | None = None) -> Document:
        try:
            doc = self.documents[document_id]
        except KeyError:
            raise NotFound(f"No document {document_id!r}") from None
        if kind is not None and doc.kind != kind:
            raise InvalidRequest(f"This needs a {kind.upper()} document")
        return doc

    def open_document(
        self,
        path: str | Path,
        kind: str | None = None,
        params: dict | None = None,
        **kw,
    ) -> Job:
        """
        Open a chromatogram file (job; the result is ``{"document_id": ...}``).

        Parameters
        ----------
        path : str or Path
            OpenChrom CSV (FID or MS), ANDI-MS ``.cdf`` or JCAMP-DX file.
        kind : {"fid", "ms"}, optional
            Detected from the file when omitted.
        params : dict, optional
            Processing parameters (:class:`FIDParams` / :class:`MSParams`).
        """
        path = Path(path).expanduser()
        if not path.is_file():
            raise InvalidRequest(f"File not found: {path}")
        kind = kind or detect_kind(path)
        changes = {**(params or {}), **kw}
        if kind == "fid":
            if changes.get("modulation_time") is None:
                changes["modulation_time"] = self.settings.get("fid", {}).get(
                    "modulation_time"
                )
            p = _apply_params(FIDParams(), changes)

            def work(_job: Job):
                return self._add(FIDDocument.open(path, p))

        elif kind == "ms":
            p = _apply_params(MSParams(), changes)

            def work(job: Job):
                return self._add(MSDocument.open(path, p, job))

        else:
            raise InvalidRequest(f"Unknown document kind {kind!r}")
        return self.submit("open", f"Opening {path.name}", work)

    def add_document(self, obj, name: str | None = None) -> Document:
        """Add a live :class:`~pyGCxGC.GCxGC_FID` or :class:`~pyGCxGC.GCxGC_MS`."""
        if isinstance(obj, GCxGC_MS):
            doc = MSDocument.from_object(obj)
        elif isinstance(obj, GCxGC_FID):
            doc = FIDDocument.from_object(obj)
        else:
            raise InvalidRequest(f"Cannot open a {type(obj).__name__}")
        if name:
            doc.name = name
        self._add(doc)
        return doc

    def _add(self, doc: Document) -> dict:
        with self._lock:
            self.documents[doc.id] = doc
        if doc.path:
            recent = [p for p in self.settings.get("recent_files", []) if p != doc.path]
            self.settings.update({"recent_files": [doc.path, *recent][:10]})
        self.emit("document.updated", doc.id, change="opened", document=doc.info())
        return {"document_id": doc.id}

    def close_document(self, document_id: str) -> None:
        with self._lock:
            doc = self.document(document_id)
            del self.documents[document_id]
        self.emit("document.updated", doc.id, change="closed", document=None)

    def update_document(
        self, document_id: str, params: dict | None = None, **kw
    ) -> Job:
        """Change processing parameters and recompute (job)."""
        doc = self.document(document_id)
        changes = {**(params or {}), **kw}
        new = _apply_params(doc.params, changes)

        def work(_job: Job):
            if isinstance(doc, FIDDocument):
                if doc.raw is None:
                    raise InvalidRequest("This document cannot be re-processed")
                chrom = FIDDocument.process(doc.raw, new, doc.name)
                with doc.lock:
                    doc.chrom, doc._params = chrom, new
            else:
                assert isinstance(doc, MSDocument)
                data = doc.data
                rebuild = (new.modulation_time, new.shift) != (
                    data.modulation_time,
                    data.shift,
                ) or new.baseline != doc._params.baseline
                if rebuild:
                    data = MSDocument.build(data.raw, new, doc.name)
                with doc.lock:
                    doc.data, doc._params = data, new
                    doc._images.clear()
                if rebuild:
                    self._mark_peaks_stale(doc, "Processing parameters changed")
                self._refresh_selection(doc)
            with doc.lock:
                doc.masks.grid = doc.grid
                doc._integration_cache = None
            self.emit(
                "document.updated", doc.id, change="processing", document=doc.info()
            )
            return {"document_id": doc.id}

        return self.submit("process", f"Processing {doc.name}", work, doc.id)

    def image(
        self, document_id: str, signal: str | None = None, mz=None
    ) -> ImageResult:
        """
        2D image of a document (``origin="lower"`` layout).

        ``signal`` and ``mz`` default to the document's current signal.
        """
        doc = self.document(document_id)
        if isinstance(doc, MSDocument):
            signal = signal or doc.signal
            mz = doc.mz if mz is None and signal == doc.signal else (mz or [])
        else:
            signal = signal or "tic"
            mz = []
        data = doc.image(signal, mz)
        if signal == "eic" and not mz:
            signal = "tic"
        return ImageResult(
            doc.id, doc.revision, signal, sorted(set(mz)), doc.grid, data
        )

    def set_signal(self, document_id: str, signal: str = "tic", mz=()) -> Job:
        """Switch an MS document between TIC and an EIC (job)."""
        doc = self.document(document_id, "ms")
        assert isinstance(doc, MSDocument)
        mz = sorted({int(m) for m in mz})
        if signal not in ("tic", "eic"):
            raise InvalidRequest(f"Unknown signal {signal!r}")
        if signal == "eic" and not mz:
            signal = "tic"
        lo, hi = doc.data.mass_range
        outside = [m for m in mz if not lo <= m <= hi]
        if outside:
            raise InvalidRequest(f"m/z {outside} outside the acquired range {lo}-{hi}")

        def work(_job: Job):
            doc.image(signal, mz)
            with doc.lock:
                doc.signal, doc.mz = signal, (mz if signal == "eic" else [])
                doc._integration_cache = None
            self.emit("document.updated", doc.id, change="signal", document=doc.info())
            return {"signal": signal, "mz": mz}

        label = "TIC" if signal == "tic" else f"EIC m/z {', '.join(map(str, mz))}"
        return self.submit("signal", label, work, doc.id)

    # ------------------------------------------------------------ spectra
    def spectrum(
        self,
        document_id: str,
        rt2_index: int | None = None,
        mod_index: int | None = None,
        snap: bool = True,
        n_average: int = 3,
        scan: int | None = None,
    ) -> SpectrumResult:
        """
        Spectrum at a pixel or scan (raw and background corrected).

        Without a point, the current selection's spectrum is returned.
        """
        doc = self.document(document_id, "ms")
        assert isinstance(doc, MSDocument)
        if rt2_index is None and mod_index is None and scan is None:
            if doc.selection is None:
                raise InvalidRequest("No point selected")
            return doc.selection.spectrum
        data, params = doc.data, doc._params
        if scan is None:
            if rt2_index is None or mod_index is None:
                raise InvalidRequest("Give both rt2_index and mod_index")
            scan = data.scan_at(int(rt2_index), int(mod_index))
            if scan < 0:
                raise InvalidRequest("The point is outside the data")
            if snap:
                scan = data.find_apex(scan)
        elif not 0 <= scan < data.n_scans:
            raise InvalidRequest(f"Scan {scan} is outside the data")
        n_average = max(int(n_average), 1)
        corrected = data.spectrum(scan, n_average=n_average, snr=params.snr)
        raw = data.spectrum(scan, n_average=n_average, subtract_background=False)
        j, i = data.pixel_of_scan(scan)
        return SpectrumResult(
            doc.id,
            j,
            i,
            int(scan),
            corrected.rt1,
            corrected.rt2,
            n_average,
            bool(snap),
            data.baseline.describe(),
            params.snr,
            raw,
            corrected,
        )

    def select(self, document_id: str, **point) -> Selection:
        """
        Select a point (see :meth:`spectrum` for the arguments).

        The library hits are computed by :meth:`search`.
        """
        doc = self.document(document_id, "ms")
        assert isinstance(doc, MSDocument)
        selection = Selection(self.spectrum(document_id, **point))
        with doc.lock:
            doc.selection = selection
        self.emit("selection.changed", doc.id, selection=selection.info())
        return selection

    def _refresh_selection(self, doc: MSDocument) -> None:
        sel = doc.selection
        if sel is None:
            return
        s = sel.spectrum
        try:
            new = self.spectrum(doc.id, scan=s.scan, n_average=s.n_average, snap=s.snap)
        except SessionError:
            doc.selection = None
            self.emit("selection.changed", doc.id, selection=None)
            return
        doc.selection = Selection(new)
        self.emit("selection.changed", doc.id, selection=doc.selection.info())

    def active_libraries(self) -> list[LibraryRecord]:
        return [
            r
            for r in self.libraries.values()
            if r.active and r.status == "ready" and r.library is not None
        ]

    def search_parameters(self, mass_range=None) -> SearchParameters:
        s = self.settings.get("search", {})
        lo, hi = mass_range if mass_range else (None, None)
        return SearchParameters(
            mz_min=lo,
            mz_max=hi,
            mz_power=float(s.get("mz_power", 0.5)),
            int_power=float(s.get("int_power", 0.5)),
            n_hits=int(s.get("n_hits", 20)),
        )

    def search(
        self,
        document_id: str | None = None,
        spectrum: tuple | None = None,
        n_hits: int | None = None,
        **point,
    ) -> Selection:
        """
        Search a spectrum against the active libraries.

        Parameters
        ----------
        document_id : str, optional
            Search the selection of this document. If a point is given
            (``rt2_index``, ``mod_index``, ``snap``, ``n_average``), it is
            selected first.
        spectrum : (mz, intensity), optional
            Search this spectrum instead.
        n_hits : int, optional
            Number of hits (default: settings).

        Returns
        -------
        Selection
            With ``hits`` set; for documents also stored as the selection.
        """
        if spectrum is not None:
            mz, inten = (np.asarray(a, dtype=np.float64) for a in spectrum)
            if mz.shape != inten.shape:
                raise InvalidRequest("mz and intensity must have the same length")
            spec = Spectrum(mz, inten)
            result = SpectrumResult(
                "", -1, -1, -1, np.nan, np.nan, 1, False, "", 0, spec, spec
            )
            hits, libs = self._search_spectrum(spec, None, n_hits)
            return Selection(result, hits, libs)
        if document_id is None:
            raise InvalidRequest("Give a document or a spectrum")
        doc = self.document(document_id, "ms")
        assert isinstance(doc, MSDocument)
        selection = self.select(document_id, **point) if point else doc.selection
        if selection is None:
            raise InvalidRequest("No point selected")
        hits, libs = self._search_spectrum(
            selection.spectrum.corrected, doc.data.mass_range, n_hits
        )
        selection.hits, selection.libraries = hits, libs
        if doc.selection is selection:
            self.emit("selection.changed", doc.id, selection=selection.info())
        return selection

    def _search_spectrum(
        self, spec: Spectrum, mass_range, n_hits
    ) -> tuple[list[Hit], list[str]]:
        params = self.search_parameters(mass_range)
        if n_hits:
            params.n_hits = int(n_hits)
        records = self.active_libraries()
        merged: list[Hit] = []
        for rec in records:
            for hit in search_spectrum(
                spec.mz, spec.intensity, rec.library, copy.copy(params)
            ):
                hit.library = rec.name
                hit.extra["library_id"] = rec.id
                merged.append(hit)
        merged.sort(key=lambda h: (h.match, h.reverse_match), reverse=True)
        min_match = float(self.settings.get("search", {}).get("min_match", 0) or 0)
        merged = [h for h in merged if h.match >= min_match][: params.n_hits]
        for rank, hit in enumerate(merged, start=1):
            hit.rank = rank
        return merged, [r.name for r in records]

    # ------------------------------------------------------------ peaks
    def detect_peaks(
        self,
        document_id: str,
        snr: float = 10.0,
        identify: bool = True,
        n_average: int = 3,
    ) -> Job:
        """Detect (and identify with the active libraries) the peaks (job)."""
        doc = self.document(document_id, "ms")
        assert isinstance(doc, MSDocument)
        if snr <= 0:
            raise InvalidRequest("The peak S/N must be positive")

        def work(job: Job):
            data = doc.data
            records = self.active_libraries() if identify else []
            job.report(0.02, "Detecting peaks")
            table = detect_peaks(data, snr=snr)
            hits: dict[int, list[Hit]] = {}
            n_lib = max(len(records), 1)
            for k, rec in enumerate(records):
                if not len(table):
                    break

                def progress(f, k=k, rec=rec):
                    job.report(0.1 + 0.9 * (k + f) / n_lib, f"Identifying ({rec.name})")

                params = self.search_parameters(data.mass_range)
                found, lib_hits = identify_peaks(
                    data, table, rec.library, params, n_average, progress
                )
                found["library"] = rec.name
                for peak_hits in lib_hits.values():
                    for h in peak_hits:
                        h.library = rec.name
                        h.extra["library_id"] = rec.id
                if k == 0:
                    table = found
                else:
                    better = found["match"] > table["match"]
                    for col in IDENTITY_COLUMNS:
                        table.loc[better, col] = found.loc[better, col]
                for pid, peak_hits in lib_hits.items():
                    combined = hits.get(pid, []) + peak_hits
                    combined.sort(key=lambda h: h.match, reverse=True)
                    hits[pid] = combined[: params.n_hits]
            for col in IDENTITY_COLUMNS:
                if col not in table:
                    table[col] = (
                        "" if col in ("name", "formula", "cas", "library") else 0
                    )
            table["baseline"] = data.baseline.describe()
            table["assigned_by"] = np.where(table["name"] != "", "library", "")
            table["category"] = ""
            with doc.lock:
                changed = doc.data is not data
                doc.peaks = table
                doc.peak_hits = hits
                doc.peaks_info = {
                    "stale": changed,
                    "stale_reason": "Processing changed during detection"
                    if changed
                    else None,
                    "libraries": [r.name for r in records],
                    "library_ids": [r.id for r in records],
                    "baseline": data.baseline.describe(),
                    "snr": float(snr),
                    "n_average": int(n_average),
                }
            self.emit(
                "peaks.updated",
                doc.id,
                n_peaks=len(table),
                stale=doc.peaks_info["stale"],
            )
            return {"n_peaks": len(table)}

        label = "Detecting & identifying peaks" if identify else "Detecting peaks"
        return self.submit("peaks", label, work, doc.id)

    def peaks(self, document_id: str) -> tuple[pd.DataFrame | None, dict]:
        """The peak table (or ``None``) and its metadata (stale, libraries, ...)."""
        doc = self.document(document_id, "ms")
        assert isinstance(doc, MSDocument)
        with doc.lock:
            table = None if doc.peaks is None else doc.peaks.copy()
            return table, dict(doc.peaks_info)

    def edit_peak(
        self,
        document_id: str,
        peak_id: int,
        name: str | None = None,
        hit_rank: int | None = None,
        delete: bool = False,
        category: str | None = None,
    ) -> None:
        """
        Edit a peak: rename it, assign a hit of the current selection, set
        its category or delete it. Edits are marked ``assigned_by = "user"``.
        """
        doc = self.document(document_id, "ms")
        assert isinstance(doc, MSDocument)
        with doc.lock:
            if doc.peaks is None:
                raise InvalidRequest("No peak table")
            rows = doc.peaks.index[doc.peaks["peak_id"] == peak_id]
            if not len(rows):
                raise NotFound(f"No peak {peak_id}")
            row = rows[0]
            table = doc.peaks
            if delete:
                doc.peaks = table.drop(index=row).reset_index(drop=True)
                doc.peak_hits.pop(peak_id, None)
            else:
                if hit_rank is not None:
                    hits = doc.selection.hits if doc.selection else None
                    if not hits or not 1 <= hit_rank <= len(hits):
                        raise InvalidRequest("No such hit in the current selection")
                    h = hits[hit_rank - 1]
                    values = {
                        "name": h.name,
                        "match": round(h.match),
                        "reverse_match": round(h.reverse_match),
                        "formula": h.formula,
                        "mw": h.mw,
                        "cas": h.cas,
                        "nist_no": h.nist_no,
                        "library": h.library,
                    }
                    for col, value in values.items():
                        if col in table:
                            table.loc[row, col] = value
                    table.loc[row, "assigned_by"] = "user"
                if name is not None:
                    table.loc[row, "name"] = name
                    table.loc[row, "assigned_by"] = "user"
                if category is not None:
                    table.loc[row, "category"] = category
        self.emit(
            "peaks.updated",
            doc.id,
            n_peaks=len(doc.peaks),
            stale=doc.peaks_info.get("stale", False),
        )

    def _mark_peaks_stale(self, doc: MSDocument, reason: str) -> None:
        if doc.peaks is None or doc.peaks_info.get("stale"):
            return
        doc.peaks_info.update(stale=True, stale_reason=reason)
        self.emit("peaks.updated", doc.id, n_peaks=len(doc.peaks), stale=True)

    # ------------------------------------------------------------ masks
    def masks(self, document_id: str) -> MaskSet:
        """The live mask set of a document (edit it through the session)."""
        return self.document(document_id).masks

    def masks_info(self, document_id: str) -> dict:
        doc = self.document(document_id)
        integration = self._integration(doc, raise_errors=False)
        by_id = {r["mask_id"]: r for r in integration.get("masks", [])}
        with doc.lock:
            masks = []
            for m in doc.masks:
                d = m.to_dict()
                d["operations"] = [_operation_info(o) for o in m.operations]
                d.update(
                    n_pixels=by_id.get(m.id, {}).get("n_pixels"),
                    volume=by_id.get(m.id, {}).get("volume"),
                    percent=by_id.get(m.id, {}).get("percent"),
                )
                masks.append(d)
            return {
                "document_id": doc.id,
                "revision": doc.masks_revision,
                "can_undo": bool(doc.mask_undo),
                "can_redo": bool(doc.mask_redo),
                "masks": masks,
                "error": integration.get("error"),
                # saves the Integration panel a request after every edit
                "integration": None if integration.get("error") else integration,
            }

    def _edit_masks(self, doc: Document, change: str, fn: Callable[[MaskSet], Any]):
        with doc.lock:
            snapshot = doc.masks.copy()
            try:
                result = fn(doc.masks)
            except (KeyError, ValueError) as exc:
                doc.masks = snapshot
                raise InvalidRequest(str(exc).strip("'\"")) from exc
            except Exception:
                doc.masks = snapshot
                raise
            doc.mask_undo.append(snapshot)
            del doc.mask_undo[:-MAX_UNDO]
            doc.mask_redo.clear()
            self._masks_changed(doc, change)
        return result

    def _masks_changed(self, doc: Document, change: str) -> None:
        doc.masks_revision += 1
        doc._integration_cache = None
        self.emit(
            "masks.updated",
            doc.id,
            change=change,
            masks_revision=doc.masks_revision,
            can_undo=bool(doc.mask_undo),
            can_redo=bool(doc.mask_redo),
        )

    def create_mask(
        self,
        document_id: str,
        name: str | None = None,
        color: str | None = None,
        category: str | None = None,
    ) -> Mask:
        doc = self.document(document_id)
        return self._edit_masks(
            doc, "created", lambda ms: ms.new_mask(name, color, category)
        )

    def update_mask(
        self,
        document_id: str,
        mask_id: str,
        name: str | None = None,
        color: str | None = None,
        visible: bool | None = None,
        category: str | None = None,
        position: int | None = None,
    ) -> Mask:
        """Rename, recolour, show/hide, categorise or move a mask."""
        doc = self.document(document_id)
        self._mask(doc, mask_id)

        def fn(ms: MaskSet) -> Mask:
            mask = ms.get(mask_id)
            if name is not None:
                try:
                    ms.rename(mask_id, name)
                except ValueError as exc:
                    raise InvalidRequest(str(exc)) from exc
            if color is not None:
                mask.color = color
            if visible is not None:
                mask.visible = bool(visible)
            if category is not None:
                mask.category = category or None
            if position is not None:
                ms.move(mask_id, int(position))
            return mask

        return self._edit_masks(doc, "updated", fn)

    def delete_mask(self, document_id: str, mask_id: str) -> None:
        doc = self.document(document_id)
        self._mask(doc, mask_id)
        self._edit_masks(doc, "deleted", lambda ms: ms.remove(mask_id))

    def duplicate_mask(
        self, document_id: str, mask_id: str, name: str | None = None
    ) -> Mask:
        doc = self.document(document_id)
        self._mask(doc, mask_id)
        return self._edit_masks(doc, "created", lambda ms: ms.duplicate(mask_id, name))

    def add_shape(
        self, document_id: str, mask_id: str, shape: dict, mode: str = "add"
    ) -> str:
        """
        Add (or subtract) a shape to a mask; returns the operation id.

        Parameters
        ----------
        shape : dict
            ``{"type": "rect", "rt1": [a, b], "rt2": [c, d]}``,
            ``{"type": "polygon", "vertices": [[rt1, rt2], ...]}`` or
            ``{"type": "lasso", "vertices": ...}`` (simplified to within half
            a pixel).
        mode : {"add", "subtract"}
        """
        doc = self.document(document_id)
        self._mask(doc, mask_id)
        parsed = self._parse_shape(doc, shape)
        if mode not in ("add", "subtract"):
            raise InvalidRequest("mode must be 'add' or 'subtract'")
        return self._edit_masks(
            doc, "shape", lambda ms: ms.get(mask_id).add(parsed, mode).id
        )

    def replace_shape(
        self, document_id: str, mask_id: str, operation_id: str, shape: dict
    ) -> None:
        """Replace the shape of an operation (e.g. after dragging vertices)."""
        doc = self.document(document_id)
        mask = self._mask(doc, mask_id)
        self._operation_index(mask, operation_id)
        parsed = self._parse_shape(doc, shape)

        def fn(ms: MaskSet):
            m = ms.get(mask_id)
            k = self._operation_index(m, operation_id)
            old = m.operations[k]
            m.operations[k] = replace(old, shape=parsed)

        self._edit_masks(doc, "shape", fn)

    def delete_shape(self, document_id: str, mask_id: str, operation_id: str) -> None:
        doc = self.document(document_id)
        self._operation_index(self._mask(doc, mask_id), operation_id)

        def fn(ms: MaskSet):
            m = ms.get(mask_id)
            del m.operations[self._operation_index(m, operation_id)]

        self._edit_masks(doc, "shape", fn)

    def undo_masks(self, document_id: str) -> bool:
        doc = self.document(document_id)
        with doc.lock:
            if not doc.mask_undo:
                return False
            doc.mask_redo.append(doc.masks)
            doc.masks = doc.mask_undo.pop()
            self._masks_changed(doc, "undo")
        return True

    def redo_masks(self, document_id: str) -> bool:
        doc = self.document(document_id)
        with doc.lock:
            if not doc.mask_redo:
                return False
            doc.mask_undo.append(doc.masks)
            doc.masks = doc.mask_redo.pop()
            self._masks_changed(doc, "redo")
        return True

    def import_masks(
        self,
        document_id: str,
        path: str | Path | None = None,
        from_document: str | None = None,
        mask_set: dict | MaskSet | None = None,
        replace_all: bool = False,
    ) -> list[str]:
        """
        Import masks from a file (``.gcxgc-masks.json``, a ``.tif`` or a folder
        of ``.tif``), another document or a mask set; returns the new mask ids.

        The masks are rasterised on the document first, so a raster mask that
        does not fit the chromatogram is reported and nothing changes.
        """
        doc = self.document(document_id)
        sources = sum(x is not None for x in (path, from_document, mask_set))
        if sources != 1:
            raise InvalidRequest("Give exactly one of path, from_document or mask_set")
        try:
            if from_document is not None:
                incoming = self.document(from_document).masks.copy()
            elif isinstance(mask_set, MaskSet):
                incoming = mask_set.copy()
            elif mask_set is not None:
                incoming = MaskSet.from_dict(mask_set)
            else:
                p = Path(path).expanduser()  # type: ignore[arg-type]
                if p.is_dir() or p.suffix.lower() in (".tif", ".tiff"):
                    incoming = MaskSet.from_tiffs(p if p.is_dir() else [p], doc.grid)
                else:
                    incoming = MaskSet.load(p)
            incoming.rasterize(doc.grid, "lower")  # validate before changing anything
        except (OSError, ValueError, KeyError) as exc:
            raise InvalidRequest(str(exc)) from exc

        def fn(ms: MaskSet) -> list[str]:
            if replace_all:
                ms.masks.clear()
            ids = []
            for mask in incoming:
                copy_ = mask.copy(ms.unique_name(mask.name), new_id=mask.id in ms)
                ids.append(ms.append(copy_).id)
            return ids

        return self._edit_masks(doc, "imported", fn)

    def mask_raster(self, document_id: str, mask_id: str) -> tuple[np.ndarray, Grid]:
        """Pixel weights (``uint8``, 255 = inside) in the API layout."""
        doc = self.document(document_id)
        with doc.lock:
            mask = self._mask(doc, mask_id).copy()
            grid = doc.grid
        try:
            weights = mask.rasterize(grid, "lower")
        except ValueError as exc:
            raise InvalidRequest(str(exc)) from exc
        return np.rint(weights * 255).astype(np.uint8), grid

    def integration(self, document_id: str) -> dict:
        """Volumes of all masks (``integrate_2D``, as ``integrate_masks``)."""
        return self._integration(self.document(document_id), raise_errors=True)

    def _integration(self, doc: Document, raise_errors: bool) -> dict:
        with doc.lock:
            signal_key = (
                doc.revision,
                getattr(doc, "signal", None),
                tuple(getattr(doc, "mz", ())),
            )
            key = (doc.masks_revision, *signal_key)
            cache = doc._integration_cache
            if cache is not None and cache[0] == key:
                return cache[1]
            masks = doc.masks.copy()
            signal = doc.integration_signal()
            grid = doc.grid
        try:
            rows, total, overlaps = self._integrate(
                doc, masks, grid, signal, signal_key
            )
        except ValueError as exc:
            if raise_errors:
                raise InvalidRequest(str(exc)) from exc
            return {"masks": [], "error": str(exc)}
        result = {
            "document_id": doc.id,
            "signal": getattr(doc, "signal", "tic"),
            "mz": list(getattr(doc, "mz", [])),
            "total": total,
            "masks": rows[:-1],
            "unassigned": rows[-1],
            "overlaps": overlaps,
            "error": None,
            "masks_revision": key[0],
        }
        with doc.lock:
            doc._integration_cache = (key, result)
        return result

    @staticmethod
    def _integrate(doc: Document, masks: MaskSet, grid: Grid, signal, signal_key):
        """
        ``MaskSet.integration`` and ``overlaps``, reusing what is known about
        masks whose operations did not change (operations are immutable and
        shared between copies, so their identity is the key): the volume, the
        pixel count and the weights inside the mask's bounding box. An edit
        integrates only the changed mask and the union, whatever the number
        of masks. The same functions run on the same arrays in the same
        order, so the results are identical.
        """
        values = _signal(grid, signal)
        cache = doc._volume_cache
        if cache.get("signal") != signal_key:
            total = float(integrate_2D(values))
            cache = {"signal": signal_key, "total": total, "masks": {}}
        total = cache["total"]
        union = np.zeros(values.shape)
        rows, blocks, entries = [], [], {}
        for m in masks:
            ops = tuple(m.operations)
            hit = cache["masks"].get(m.id)
            if (
                hit is None
                or len(hit["ops"]) != len(ops)
                or not all(a is b for a, b in zip(hit["ops"], ops, strict=True))
            ):
                w = m.rasterize(grid)
                r = np.flatnonzero(w.any(axis=1))
                c = np.flatnonzero(w.any(axis=0))
                box = (
                    (int(r[0]), int(r[-1]) + 1, int(c[0]), int(c[-1]) + 1)
                    if len(r)
                    else None
                )
                hit = {
                    "ops": ops,
                    "volume": _masked_volume(values, w),
                    "n_pixels": int((w > 0).sum()),
                    "box": box,
                    "block": w[box[0] : box[1], box[2] : box[3]].copy()
                    if box
                    else None,
                }
            entries[m.id] = hit
            if hit["box"] is not None:
                r0, r1, c0, c1 = hit["box"]
                # outside the box the weights are 0, so this is the full maximum
                np.maximum(union[r0:r1, c0:c1], hit["block"], out=union[r0:r1, c0:c1])
            rows.append((m, hit["volume"], hit["n_pixels"]))
            blocks.append((m, hit["box"], hit["block"]))
        unassigned = total - _masked_volume(values, union)
        cache["masks"] = entries
        doc._volume_cache = cache

        def row(mask_id, name, volume, n_pixels):
            percent = volume / total * 100 if total else None
            return {
                "mask_id": mask_id,
                "name": name,
                "volume": float(volume),
                "percent": percent,
                "n_pixels": int(n_pixels),
            }

        out = [row(m.id, m.name, v, n) for m, v, n in rows]
        out.append(row(None, "unassigned", unassigned, int((union == 0).sum())))
        return out, total, _overlaps(blocks)

    def _parse_shape(self, doc: Document, shape: dict):
        try:
            kind = shape.get("type")
            if kind == "rect":
                return Rect(tuple(shape["rt1"]), tuple(shape["rt2"]))
            if kind in ("polygon", "lasso"):
                vertices = np.asarray(shape["vertices"], dtype=np.float64)
                if vertices.ndim != 2 or vertices.shape[1] != 2:
                    raise ValueError("vertices must be a list of [rt1, rt2] pairs")
                if kind == "lasso":
                    vertices = simplify_lasso(vertices, doc.grid)
                return Polygon(tuple(map(tuple, vertices)))
        except (KeyError, TypeError, ValueError) as exc:
            raise InvalidRequest(f"Invalid shape: {exc}") from exc
        raise InvalidRequest(f"Unknown shape type {shape.get('type')!r}")

    @staticmethod
    def _mask(doc: Document, mask_id: str) -> Mask:
        try:
            return doc.masks.get(mask_id)
        except KeyError:
            raise NotFound(f"No mask {mask_id!r}") from None

    @staticmethod
    def _operation_index(mask: Mask, operation_id: str) -> int:
        for k, op in enumerate(mask.operations):
            if op.id == operation_id:
                return k
        raise NotFound(f"No operation {operation_id!r} in mask {mask.name!r}")

    # ------------------------------------------------------------ libraries
    def add_library(
        self, path: str | Path, activate: bool = True, exclusive: bool = False
    ) -> Job:
        """
        Load a spectral library (job; the result is ``{"library_id": ...}``).

        Parameters
        ----------
        path : str or Path
            NIST ``MAINLIB`` folder, ``.msp``/``.zip``/``.npz`` file, or
            ``"mona"`` to download the open MoNA GC-MS library.
        activate : bool, default True
            Search the library once it is loaded.
        exclusive : bool, default False
            Deactivate the other libraries when this one is activated.
        """
        download = str(path).lower() == "mona"
        if not download:
            path = Path(path).expanduser().resolve()
            if not path.exists():
                raise InvalidRequest(f"Not found: {path}")
            for rec in self.libraries.values():
                if rec.path == str(path) and rec.status != "failed":
                    if activate:
                        self.set_library_active(rec.id, True, exclusive)
                    return self.submit(
                        "library", rec.name, lambda _j, r=rec: {"library_id": r.id}
                    )
        source = "mona" if download else library_source(path)
        name = "MoNA GC-MS" if download else Path(path).name
        rec = LibraryRecord(_new_id(), "" if download else str(path), source, name)
        with self._lock:
            self.libraries[rec.id] = rec
        self.emit("library.updated", None, library=rec.info())

        def work(job: Job):
            try:
                target = Path(path) if not download else None
                if download:
                    job.report(0.0, "Downloading MoNA GC-MS (about 9 MB)")
                    target = download_mona_gcms(
                        (self.cache_dir / "mona_gcms.zip") if self.cache_dir else None
                    )
                    rec.path = str(target)
                job.check()
                with contextlib.suppress(OSError, ValueError):
                    cache = library_cache_file(target, self.cache_dir)
                    rec.cache_file = str(cache) if cache else None
                if rec.cache_file and not Path(rec.cache_file).exists():
                    job.report(0.01, "Converting (first time only)")
                library = load_library(
                    target,
                    cache_dir=self.cache_dir,
                    progress=lambda f: job.report(0.95 * f),
                )
            except Exception as exc:
                rec.status, rec.error = "failed", _error_text(exc)
                self.emit("library.updated", None, library=rec.info())
                raise
            rec.library, rec.status = library, "ready"
            if not download:
                rec.name = library.name
            saved = [p for p in self.settings.get("libraries", []) if p != rec.path]
            self.settings.update({"libraries": [*saved, rec.path]})
            if activate:
                self.set_library_active(rec.id, True, exclusive)
            else:
                self.emit("library.updated", None, library=rec.info())
            return {"library_id": rec.id}

        return self.submit("library", f"Loading library {name}", work)

    def restore_libraries(self) -> list[Job]:
        """Load the libraries saved in the settings (at start-up)."""
        jobs = []
        for p in self.settings.get("libraries", []):
            if Path(p).exists():
                with contextlib.suppress(SessionError):
                    jobs.append(self.add_library(p))
        return jobs

    def library(self, library_id: str) -> LibraryRecord:
        try:
            return self.libraries[library_id]
        except KeyError:
            raise NotFound(f"No library {library_id!r}") from None

    def set_library_active(
        self, library_id: str, active: bool = True, exclusive: bool = False
    ) -> None:
        """Activate or deactivate a library; existing peak tables become stale."""
        rec = self.library(library_id)
        with self._lock:
            rec.active = bool(active)
            if active and exclusive:
                for other in self.libraries.values():
                    if other is not rec:
                        other.active = False
        self.emit("library.updated", None, library=rec.info())
        active_ids = {r.id for r in self.active_libraries()}
        for doc in list(self.documents.values()):
            if isinstance(doc, MSDocument) and doc.peaks is not None:
                used = set(doc.peaks_info.get("library_ids", []))
                if used and used != active_ids:
                    names = ", ".join(doc.peaks_info.get("libraries", []))
                    self._mark_peaks_stale(doc, f"Identified with {names}")

    def remove_library(self, library_id: str) -> None:
        with self._lock:
            rec = self.libraries.pop(library_id, None)
        if rec is None:
            raise NotFound(f"No library {library_id!r}")
        saved = [p for p in self.settings.get("libraries", []) if p != rec.path]
        self.settings.update({"libraries": saved})
        self.emit("library.updated", None, library={**rec.info(), "status": "removed"})

    def library_entry(self, library_id: str, index: int):
        """One library spectrum (for the head-to-tail plot)."""
        rec = self.library(library_id)
        if rec.library is None:
            raise InvalidRequest(f"Library {rec.name} is not loaded")
        if not 0 <= index < len(rec.library):
            raise NotFound(f"No spectrum {index} in {rec.name}")
        return rec.library.entry(index)

    # ------------------------------------------------------------ export
    def export(
        self,
        document_id: str,
        what: str,
        format: str | None = None,  # noqa: A002
        path=None,
    ) -> tuple[bytes | None, str, str]:
        """
        Export masks, the mask set, integration results, the peak table or
        the selected spectrum.

        Parameters
        ----------
        what : {"masks", "mask_set", "integration", "peaks", "spectrum"}
        format : str, optional
            ``tiff`` (masks), ``json`` (mask set), ``csv`` (integration, peaks),
            ``msp`` or ``txt`` (spectrum).
        path : str or Path, optional
            Write here (a folder for ``masks``). Without a path the content is
            returned.

        Returns
        -------
        content, filename, media_type
            ``content`` is ``None`` when written to ``path``.
        """
        doc = self.document(document_id)
        base = Path(doc.name).stem or "chromatogram"
        formats = {
            "masks": ("tiff",),
            "mask_set": ("json",),
            "integration": ("csv",),
            "peaks": ("csv",),
            "spectrum": ("msp", "txt"),
        }
        if what not in formats:
            raise InvalidRequest(f"Unknown export {what!r}")
        fmt = format or formats[what][0]
        if fmt not in formats[what]:
            raise InvalidRequest(
                f"{what} can be exported as {', '.join(formats[what])}"
            )
        if what == "masks":
            with doc.lock:
                masks, grid = doc.masks.copy(), doc.grid
            if not len(masks):
                raise InvalidRequest("There are no masks")
            if path is not None:
                try:
                    masks.to_tiffs(path, grid)
                except ValueError as exc:
                    raise InvalidRequest(str(exc)) from exc
                return None, str(path), "inode/directory"
            import tempfile

            with tempfile.TemporaryDirectory() as tmp:
                try:
                    files = masks.to_tiffs(tmp, grid)
                except ValueError as exc:
                    raise InvalidRequest(str(exc)) from exc
                buf = io.BytesIO()
                with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                    for f in files:
                        zf.write(f, f.name)
            return self._write(
                buf.getvalue(), path, f"{base}_masks.zip", "application/zip"
            )
        if what == "mask_set":
            with doc.lock:
                masks = doc.masks.copy()
                masks.grid = doc.grid
                masks.name = masks.name or base
            text = json.dumps(masks.to_dict(), indent=1)
            return self._write(
                text.encode(), path, f"{base}.gcxgc-masks.json", "application/json"
            )
        if what == "integration":
            text = self._integration_csv(doc)
            return self._write(
                text.encode(), path, f"{base}_integration.csv", "text/csv"
            )
        if what == "peaks":
            table, info = self.peaks(doc.id)
            if table is None:
                raise InvalidRequest("Detect peaks first")
            text = table.sort_values("peak_id").to_csv(index=False)
            return self._write(text.encode(), path, f"{base}_peaks.csv", "text/csv")
        # spectrum
        assert isinstance(doc, MSDocument)
        sel = doc.selection
        if sel is None:
            raise InvalidRequest("No point selected")
        s = sel.spectrum
        spec = s.corrected.nonzero()
        if fmt == "txt":
            text = "".join(
                f"{int(m)}\t{v:.6g}\n"
                for m, v in zip(spec.mz, spec.intensity, strict=True)
            )
            return self._write(
                text.encode(), path, f"{base}_spectrum.txt", "text/plain"
            )
        text = (
            f"Name: {doc.name} RT1 {s.rt1:.3f} min RT2 {s.rt2:.3f} s\n"
            f'Comments: "scan={s.scan}" "n_average={s.n_average}" "baseline={s.baseline}"\n'
            f"Num Peaks: {len(spec.mz)}\n"
            + "".join(
                f"{int(m)} {v:.6g}\n"
                for m, v in zip(spec.mz, spec.intensity, strict=True)
            )
        )
        return self._write(
            text.encode(), path, f"{base}_scan{s.scan}.msp", "chemical/x-msp"
        )

    @staticmethod
    def _write(content: bytes, path, filename: str, media_type: str):
        if path is None:
            return content, filename, media_type
        path = Path(path).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return None, str(path), media_type

    def _integration_csv(self, doc: Document) -> str:
        return _csv([self._integration_row(doc, self.integration(doc.id))])

    @staticmethod
    def _integration_row(doc: Document, result: dict) -> dict[str, Any]:
        """CSV row: file, name, processing parameters, signal, one column per mask."""
        row: dict[str, Any] = {"file": doc.path or "", "name": doc.name}
        for key, value in asdict(doc.params).items():
            is_model = key == "baseline" and isinstance(value, dict)
            row[key] = make_baseline(value).describe() if is_model else value
        if isinstance(doc, MSDocument):
            row["signal"] = (
                "TIC" if doc.signal == "tic" else "EIC " + " ".join(map(str, doc.mz))
            )
        for r in result["masks"]:
            row[r["name"]] = r["volume"]
        row["unassigned"] = result["unassigned"]["volume"]
        return row

    def batch_integrate(
        self, document_id: str, paths: list[str | Path], out: str | Path | None = None
    ) -> Job:
        """
        Integrate the masks of a document on other files (spec INT-4).

        Every file is processed with the document's parameters (for MS files
        also its signal, TIC or EIC) and integrated exactly as if it were
        opened in the app. Job result: ``{"csv": text, "n_files": n, "failed":
        [{"file", "error"}], "path": out}``; the CSV has one row per file, and
        files that fail get an ``error`` column instead of volumes.

        Parameters
        ----------
        paths : list of str or Path
            Chromatogram files of the same kind as the document.
        out : str or Path, optional
            Also write the CSV here.
        """
        doc = self.document(document_id)
        files = [Path(p).expanduser() for p in paths]
        if not files:
            raise InvalidRequest("Choose at least one file")
        for f in files:
            if not f.is_file():
                raise InvalidRequest(f"File not found: {f}")
        with doc.lock:
            masks = doc.masks.copy()
            params = copy.deepcopy(doc._params if doc.kind == "fid" else doc.params)
            signal = getattr(doc, "signal", "tic")
            mz = list(getattr(doc, "mz", []))
        if not len(masks):
            raise InvalidRequest("There are no masks")

        def work(job: Job) -> dict:
            rows, failed = [], []
            for k, path in enumerate(files):
                job.report(k / len(files), f"{path.name} ({k + 1}/{len(files)})")
                try:
                    if isinstance(params, FIDParams):
                        other: Document = FIDDocument.open(path, replace(params))
                    else:
                        other = MSDocument.open(path, replace(params))
                        other.signal, other.mz = signal, mz
                    other.masks = masks.copy()
                    out_rows, _, _ = self._integrate(
                        other,
                        other.masks,
                        other.grid,
                        other.integration_signal(),
                        ("batch",),
                    )
                    result = {"masks": out_rows[:-1], "unassigned": out_rows[-1]}
                    rows.append(self._integration_row(other, result))
                except JobCancelled:
                    raise
                except Exception as exc:  # report the file, go on with the next
                    error = _error_text(exc)
                    failed.append({"file": str(path), "error": error})
                    rows.append({"file": str(path), "name": path.name, "error": error})
            text = _csv(rows)
            if out is not None:
                self._write(text.encode(), out, "", "text/csv")
            return {
                "csv": text,
                "n_files": len(files),
                "failed": failed,
                "path": str(Path(out).expanduser()) if out is not None else None,
            }

        return self.submit(
            "batch", f"Batch integration of {len(files)} files", work, doc.id
        )

    # ------------------------------------------------------------ files
    @staticmethod
    def list_dir(path: str | Path | None = None, show_hidden: bool = False) -> dict:
        """Directory listing for the built-in file browser."""
        p = Path(path).expanduser() if path else Path.home()
        try:
            p = p.resolve()
            if not p.is_dir():
                raise InvalidRequest(f"Not a folder: {p}")
            entries = []
            for child in p.iterdir():
                if not show_hidden and child.name.startswith("."):
                    continue
                with contextlib.suppress(OSError):
                    st = child.stat()
                    entries.append(
                        {
                            "name": child.name,
                            "path": str(child),
                            "is_dir": child.is_dir(),
                            "size": None if child.is_dir() else st.st_size,
                            "modified": st.st_mtime,
                        }
                    )
        except PermissionError as exc:
            raise InvalidRequest(f"No access to {p}") from exc
        entries.sort(key=lambda e: (not e["is_dir"], e["name"].lower()))
        return {
            "path": str(p),
            "parent": str(p.parent) if p.parent != p else None,
            "entries": entries,
        }

    def probe_file(self, path: str | Path) -> dict:
        """
        What opening ``path`` would do, without loading it.

        ``kind`` is ``"fid"`` or ``"ms"`` (``None`` with an ``error`` when the
        file cannot be opened). ``needs_modulation_time`` is true for FID
        files when the settings hold no default modulation time.
        """
        p = Path(path).expanduser()
        info = {"path": str(p), "name": p.name, "size": None, "kind": None}
        info.update(error=None, needs_modulation_time=False)
        if not p.is_file():
            info["error"] = f"File not found: {p}"
            return info
        try:
            info["size"] = p.stat().st_size
            info["kind"] = detect_kind(p)
        except (OSError, InvalidRequest) as exc:
            info["error"] = str(exc)
            return info
        if info["kind"] == "fid":
            default = self.settings.get("fid", {}).get("modulation_time")
            info["needs_modulation_time"] = default is None
        return info


# ------------------------------------------------------------------ helpers
def detect_kind(path: str | Path) -> str:
    """``"fid"`` or ``"ms"`` from the file contents."""
    path = Path(path)
    with path.open("rb") as fh:
        head = fh.read(65536)
    if head[:3] == b"CDF":
        if b"mass_values" in head:
            return "ms"
        if b"ordinate_values" in head:
            raise InvalidRequest(
                f"{path.name} is an ANDI chromatography (FID) file, which is not "
                "supported yet. Export it as OpenChrom CSV."
            )
        return "ms"
    if head[:8] == OLE_MAGIC:
        return "ms"  # the reader explains how to export vendor files
    suffix = path.suffix.lower()
    if suffix in (".jdx", ".dx", ".jcamp"):
        return "ms"
    if suffix in (".csv", ".txt"):
        header = head.split(b"\n", 1)[0].decode("utf-8", "replace")
        for col in header.split(","):
            with contextlib.suppress(ValueError):
                float(col.strip().strip('"'))
                return "ms"  # numeric column names are m/z values
        return "fid"
    raise InvalidRequest(f"Unsupported file type: {path.name}")


def _overlaps(blocks: list[tuple[Mask, tuple | None, np.ndarray | None]]) -> list[dict]:
    """Shared pixels of every overlapping pair (masks as bounding box and weights)."""
    out = []
    for (a, ba, wa), (b, bb, wb) in itertools.combinations(blocks, 2):
        if ba is None or bb is None:
            continue
        r0, r1 = max(ba[0], bb[0]), min(ba[1], bb[1])
        c0, c1 = max(ba[2], bb[2]), min(ba[3], bb[3])
        if r0 >= r1 or c0 >= c1:
            continue
        sa = wa[r0 - ba[0] : r1 - ba[0], c0 - ba[2] : c1 - ba[2]]
        sb = wb[r0 - bb[0] : r1 - bb[0], c0 - bb[2] : c1 - bb[2]]
        n = int(np.count_nonzero((sa > 0) & (sb > 0)))
        if n:
            out.append({"a": a.id, "b": b.id, "names": [a.name, b.name], "n_pixels": n})
    return out


def _csv(rows: list[dict]) -> str:
    """CSV with the union of the rows' columns, in order of appearance."""
    fields: dict[str, None] = {}
    for row in rows:
        fields.update(dict.fromkeys(row))
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(fields), restval="")
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue()


def _operation_info(op) -> dict:
    d = op.to_dict()
    if d["shape"]["type"] == "raster":
        d["shape"] = {"type": "raster", "shape": d["shape"]["shape"]}
    return d


def _error_text(exc: Exception) -> str:
    text = str(exc) or type(exc).__name__
    return text if isinstance(exc, SessionError) else f"{type(exc).__name__}: {text}"


def _is_jsonable(value) -> bool:
    if value is None or isinstance(value, str | int | float | bool):
        return True
    if isinstance(value, list | tuple):
        return all(_is_jsonable(v) for v in value)
    if isinstance(value, dict):
        return all(isinstance(k, str) and _is_jsonable(v) for k, v in value.items())
    return False


def _deep_update(target: dict, changes: dict) -> None:
    for key, value in changes.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_update(target[key], value)
        else:
            target[key] = value


__all__ = [
    "FIDDocument",
    "FIDParams",
    "ImageResult",
    "InvalidRequest",
    "Job",
    "JobCancelled",
    "LibraryRecord",
    "MSDocument",
    "MSParams",
    "NotFound",
    "Selection",
    "Session",
    "SessionError",
    "Settings",
    "SpectrumResult",
    "detect_kind",
    "hit_dict",
]
