"""
The pyGCxGC app in a notebook: ``viewer = gcgc.view(obj)`` (spec §10).

:func:`view` starts the app's server in a background thread of the kernel,
in the same process, so the app works on the very objects passed in (a
:class:`~pyGCxGC.GCxGC_FID`, a :class:`~pyGCxGC.GCxGC_MS` or a file path).
In Jupyter and VS Code the app is shown inline; :meth:`Viewer.open_window`
opens it in the browser. :func:`view` returns at once.

The returned :class:`Viewer` reads the current state of the app, never a copy
taken at launch: ``viewer.masks``, ``viewer.peaks``, ``viewer.selection``,
``viewer.integration`` and ``viewer.library`` refer to the active document
(the tab shown in the app). Changes made from Python appear in the app::

    viewer.set_signal(eic=[91])
    viewer.select(rt1=12.5, rt2=3.2)  # RT1 in min, RT2 in s
    viewer.masks.add_rect((10, 12), (1.0, 2.5), mask="Aromatics")
    viewer.on("select", lambda event: print(event["selection"]["rt1"]))

The viewer needs the ``app`` extra (``pip install "pyGCxGC[app]"``).
"""

from __future__ import annotations

import contextlib
import logging
import threading
import traceback
import webbrowser
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pandas as pd

from pyGCxGC.app.session import (
    FIDDocument,
    InvalidRequest,
    Job,
    MSDocument,
    Selection,
    Session,
)
from pyGCxGC.main import GCxGC_FID
from pyGCxGC.masks import Mask, MaskSet
from pyGCxGC.ms.data import GCxGC_MS

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from pyGCxGC.ms.data import Spectrum
    from pyGCxGC.ms.library import SpectralLibrary

log = logging.getLogger("pyGCxGC.app")

#: Event names of :meth:`Viewer.on` and the session events they stand for.
EVENTS: dict[str, tuple[str, ...] | None] = {
    "select": ("selection.changed",),
    "masks": ("masks.updated",),
    "peaks": ("peaks.updated",),
    "document": ("document.updated",),
    "library": ("library.updated",),
    "job": ("job.progress", "job.done", "job.failed"),
    "project": ("project.updated",),
    "*": None,
}

# the viewer of this process (one server per kernel)
_current: dict[str, Viewer | None] = {"viewer": None}
_current_lock = threading.Lock()


def view(
    obj: GCxGC_FID | GCxGC_MS | str | Path | None = None,
    library: str | Path | SpectralLibrary | None = None,
    *,
    height: int = 640,
    display: bool | None = None,
    open_window: bool = False,
    name: str | None = None,
) -> Viewer:
    """
    Open the pyGCxGC app on a chromatogram (spec NB-1).

    The first call starts the app's server in a background thread; later
    calls reuse it and add their object as another document.

    Parameters
    ----------
    obj : GCxGC_FID, GCxGC_MS, str or Path, optional
        A live chromatogram (shown as it is, not copied) or a file to open
        (in the background).
    library : str, Path or SpectralLibrary, optional
        Spectral library to search (a NIST ``MAINLIB`` folder, an
        ``.msp``/``.npz`` file or a loaded library). Without one, the
        libraries saved in the app's settings are loaded.
    height : int, default 640
        Height of the inline view in pixels.
    display : bool, optional
        Show the app inline (default: when running in a notebook).
    open_window : bool, default False
        Also open the app in the web browser.
    name : str, optional
        Document name (default: the object's name or the file name).

    Returns
    -------
    Viewer

    Examples
    --------
    >>> import pyGCxGC as gcgc
    >>> data = gcgc.read_gcxgc_ms("run.cdf", modulation_time=20)  # doctest: +SKIP
    >>> viewer = gcgc.view(data, library="MAINLIB")  # doctest: +SKIP
    >>> viewer.selection.hits[0].name  # after a click in the app  # doctest: +SKIP
    """
    with _current_lock:
        viewer = _current["viewer"]
        if viewer is None or not viewer.running:
            viewer = Viewer.start(restore_libraries=library is None)
            _current["viewer"] = viewer
    viewer.height = height
    if library is not None:
        viewer.add_library(library, exclusive=True, wait=False)
    if obj is not None:
        viewer.open(obj, name=name, wait=False)
    if display is None:
        display = _in_notebook()
    if display:
        viewer.show()
    if open_window:
        viewer.open_window()
    return viewer


class Viewer:
    """
    Handle of the app running in this process (see :func:`view`).

    Properties refer to the active document: the one shown in the app, or
    the one last opened from Python (:meth:`activate` switches).

    Attributes
    ----------
    session : Session
        The app's session; every UI action is a method of it.
    height : int
        Height of the inline view (pixels).
    """

    def __init__(self, session: Session, server, height: int = 640):
        self.session = session
        self.height = height
        self._server = server
        self._pending: list[Job] = []

    @classmethod
    def start(
        cls,
        session: Session | None = None,
        port: int = 0,
        restore_libraries: bool = True,
    ) -> Viewer:
        """Start a server for ``session`` (default: one with the user settings)."""
        from pyGCxGC.app.paths import settings_file, setup_logging
        from pyGCxGC.app.server import ServerThread

        if session is None:
            session = Session(settings_path=settings_file())
            setup_logging(session.log_dir)
            if restore_libraries:
                session.restore_libraries()
        server = ServerThread(session, port).start()
        log.info("Notebook viewer at %s", server.base_url)
        return cls(session, server)

    # ------------------------------------------------------------ the app
    @property
    def url(self) -> str:
        """URL of the app, with its access token."""
        return self._server.url

    @property
    def running(self) -> bool:
        return self._server.running

    def show(self, height: int | None = None) -> None:
        """Show the app inline in the notebook (compact layout)."""
        from IPython.display import IFrame, display

        display(IFrame(f"{self.url}&layout=compact", "100%", height or self.height))

    def open_window(self) -> None:
        """Open the app in the web browser."""
        webbrowser.open(self.url)

    def close(self) -> None:
        """Stop the server and close the session."""
        self._server.stop()
        self.session.close()
        with _current_lock:
            if _current["viewer"] is self:
                _current["viewer"] = None

    def wait(self, timeout: float | None = 600) -> None:
        """Wait for the files and libraries this viewer is still loading."""
        while self._pending:
            job = self._pending.pop(0)
            job.wait(timeout)

    def __repr__(self) -> str:
        state = "running" if self.running else "stopped"
        n = len(self.session.documents)
        return f"<pyGCxGC Viewer {self._server.base_url} ({state}, {n} documents)>"

    # ------------------------------------------------------------ documents
    def open(
        self,
        obj: GCxGC_FID | GCxGC_MS | str | Path,
        name: str | None = None,
        wait: bool = True,
        **params,
    ) -> str | None:
        """
        Add a chromatogram and make it the active document.

        ``obj`` is a live object or a file; ``params`` are processing
        parameters for files (e.g. ``modulation_time=20``). Returns the
        document id (``None`` with ``wait=False`` for files, which then open
        in the background).
        """
        if isinstance(obj, GCxGC_FID | GCxGC_MS):
            doc = self.session.add_document(obj, name)
            self.session.set_active_document(doc.id)
            return doc.id
        job = self.session.open_document(obj, params=params)

        def opened(j: Job) -> None:
            if j.status != "done":
                return
            doc_id = j.result["document_id"]
            if name:
                self.session.documents[doc_id].name = name
            with contextlib.suppress(Exception):
                self.session.set_active_document(doc_id)

        job.add_done_callback(opened)
        if not wait:
            self._pending.append(job)
            return None
        return self.session.wait(job)["document_id"]

    @property
    def documents(self) -> dict[str, str]:
        """Open documents: ``{id: name}`` in the order they were opened."""
        return {d.id: d.name for d in self.session.documents.values()}

    @property
    def document(self) -> str:
        """Id of the active document."""
        return self._doc().id

    @property
    def data(self) -> GCxGC_FID | GCxGC_MS:
        """The chromatogram object of the active document."""
        doc = self._doc()
        return doc.chrom if isinstance(doc, FIDDocument) else doc.data

    def activate(self, document: str | GCxGC_FID | GCxGC_MS) -> None:
        """Make a document (id, name or object) the active one; the app follows."""
        self.session.set_active_document(self._doc(document).id)

    def open_project(self, path: str | Path) -> list[str]:
        """Open a project file (``*.gcxgc``); returns the document ids."""
        result = self.session.wait(self.session.open_project(path))
        return list(result["document_ids"])

    def save_project(self, path: str | Path | None = None) -> Path:
        """Save the session as a project (default: the current project file)."""
        result = self.session.save_project(path)
        for note in result["warnings"]:
            log.warning(note)
        return Path(result["path"])

    # ------------------------------------------------------------ live state
    @property
    def masks(self) -> LiveMasks:
        """The masks of the active document (edits appear in the app)."""
        return LiveMasks(self.session, self._doc().id)

    @property
    def integration(self) -> pd.DataFrame:
        """
        Integration of the active document's masks: one row per mask plus
        ``unassigned``, with ``volume``, ``percent`` and ``n_pixels``.
        """
        result = self.session.integration(self._doc().id)
        rows = [*result["masks"], result["unassigned"]]
        frame = pd.DataFrame(rows, columns=["name", "volume", "percent", "n_pixels"])
        frame = frame.set_index("name")
        frame.attrs.update(total=result["total"], signal=result["signal"])
        return frame

    @property
    def selection(self) -> Selection | None:
        """
        The selected point of the active MS document: ``spectrum`` (raw and
        ``corrected``), ``hits`` (a list of :class:`~pyGCxGC.ms.search.Hit`,
        ``None`` before the search) and ``region`` (for a mask's spectrum).
        """
        doc = self._doc()
        return doc.selection if isinstance(doc, MSDocument) else None

    @property
    def spectrum(self) -> Spectrum | None:
        """The background-corrected spectrum of the selection."""
        sel = self.selection
        return None if sel is None else sel.spectrum.corrected

    @property
    def hits(self) -> pd.DataFrame | None:
        """The library hits of the selection as a table."""
        sel = self.selection
        if sel is None or sel.hits is None:
            return None
        return pd.DataFrame([h.as_dict() for h in sel.hits])

    @property
    def peaks(self) -> pd.DataFrame | None:
        """The peak table of the active MS document (``None`` before detection)."""
        doc = self._doc()
        if not isinstance(doc, MSDocument):
            return None
        table, info = self.session.peaks(doc.id)
        if table is not None:
            table.attrs.update(info)
        return table

    @property
    def library(self) -> SpectralLibrary | None:
        """The (first) active spectral library."""
        self.wait()
        active = self.session.active_libraries()
        return active[0].library if active else None

    @property
    def libraries(self) -> dict[str, SpectralLibrary]:
        """Loaded libraries by name."""
        self.wait()
        return {
            r.name: r.library
            for r in self.session.libraries.values()
            if r.library is not None
        }

    # ------------------------------------------------------------ actions
    def set_signal(
        self, signal: str | None = None, eic: list[int] | int | None = None
    ) -> None:
        """
        Show the TIC (``set_signal("tic")``) or an EIC (``set_signal(eic=[91])``)
        of the active MS document.
        """
        if eic is not None:
            mz = [eic] if isinstance(eic, int) else list(eic)
            signal = "eic"
        else:
            mz = []
            signal = signal or "tic"
        self.session.wait(self.session.set_signal(self._doc().id, signal, mz))

    def select(
        self,
        rt1: float | None = None,
        rt2: float | None = None,
        *,
        scan: int | None = None,
        mask: str | None = None,
        snap: bool = True,
        n_average: int = 3,
        search: bool = True,
    ) -> Selection:
        """
        Select a point of the active MS document, as a click in the app does.

        Parameters
        ----------
        rt1, rt2 : float
            Retention times (RT1 in min, RT2 in s).
        scan : int, optional
            A scan instead of retention times.
        mask : str, optional
            Select the summed spectrum of a mask (name or id) instead.
        snap : bool, default True
            Move to the most intense scan nearby.
        n_average : int, default 3
            Number of scans summed.
        search : bool, default True
            Search the active libraries.
        """
        doc_id = self._doc().id
        if mask is not None:
            self.session.select(doc_id, mask_id=mask)
        elif scan is not None:
            self.session.select(doc_id, scan=scan, snap=snap, n_average=n_average)
        else:
            self.session.select(
                doc_id, rt1=rt1, rt2=rt2, snap=snap, n_average=n_average
            )
        if search:
            self.wait()
            return self.session.search(doc_id)
        selection = self.selection
        assert selection is not None
        return selection

    def detect_peaks(
        self, snr: float = 10.0, identify: bool = True, n_average: int = 3
    ) -> pd.DataFrame:
        """Detect (and identify) the peaks of the active MS document."""
        self.wait()
        doc_id = self._doc().id
        self.session.wait(self.session.detect_peaks(doc_id, snr, identify, n_average))
        table = self.peaks
        assert table is not None
        return table

    def add_library(
        self,
        library: str | Path | SpectralLibrary,
        exclusive: bool = False,
        wait: bool = True,
    ) -> str | None:
        """Load and activate a spectral library; returns its id."""
        job = self.session.add_library(library, activate=True, exclusive=exclusive)
        if not wait:
            self._pending.append(job)
            return None
        return self.session.wait(job)["library_id"]

    def on(self, event: str, callback: Callable[[dict], Any]) -> Callable[[], None]:
        """
        Call ``callback(event)`` when something changes (spec NB-3).

        Events: ``"select"`` (``event["selection"]``), ``"masks"``,
        ``"peaks"``, ``"document"``, ``"library"``, ``"job"``, ``"project"``
        or ``"*"`` for all. The callback runs in a background thread.
        Returns a function that removes the callback.
        """
        try:
            types = EVENTS[event]
        except KeyError:
            raise ValueError(
                f"Unknown event {event!r}; use one of {', '.join(EVENTS)}"
            ) from None

        def handler(e: dict) -> None:
            if types is None or e["type"] in types:
                try:
                    callback(e)
                except Exception:
                    traceback.print_exc()

        return self.session.subscribe(handler)

    # ------------------------------------------------------------ helpers
    def _doc(self, document=None):
        self.wait()
        if document is None:
            doc_id = self.session.active_document_id
            if doc_id is None:
                raise InvalidRequest("No document is open: use viewer.open(...)")
            return self.session.document(doc_id)
        for doc in self.session.documents.values():
            obj = doc.chrom if isinstance(doc, FIDDocument) else doc.data
            if document is obj or document in (doc.id, doc.name):
                return doc
        raise InvalidRequest(f"No open document {document!r}")


class LiveMasks:
    """
    The mask set of a document in the app.

    Reading works like a :class:`~pyGCxGC.masks.MaskSet` (``len``, iteration,
    ``names``, ``masks["Aromatics"]``, ``rasterize``, ``integrate``,
    ``save``, ``to_tiffs``, ...) on the current masks; the masks returned are
    snapshots. Edits go through the session, so they appear in the app and
    can be undone there.

    Shapes are in retention times: RT1 in min, RT2 in s.
    """

    _READ = frozenset(
        {
            "grid",
            "name",
            "names",
            "get",
            "index",
            "rasterize",
            "integrate",
            "integration",
            "overlaps",
            "to_tiffs",
            "to_dict",
            "save",
        }
    )

    def __init__(self, session: Session, document_id: str):
        self._session = session
        self._doc = document_id

    def snapshot(self) -> MaskSet:
        """A copy of the current mask set."""
        return self._session.masks(self._doc).copy()

    copy = snapshot

    def __getattr__(self, name: str):
        if name in LiveMasks._READ:
            return getattr(self.snapshot(), name)
        raise AttributeError(
            f"{type(self).__name__} has no attribute {name!r}; edit masks with "
            "new_mask, add_rect, add_polygon, add, remove, rename, update, ..."
        )

    def __len__(self) -> int:
        return len(self._session.masks(self._doc))

    def __iter__(self) -> Iterator[Mask]:
        return iter(self.snapshot())

    def __getitem__(self, key: int | str) -> Mask:
        return self.snapshot()[key]

    def __contains__(self, key: str) -> bool:
        return key in self._session.masks(self._doc)

    def __repr__(self) -> str:
        return f"LiveMasks({self.snapshot().names!r})"

    # ------------------------------------------------------------ editing
    def new_mask(
        self,
        name: str | None = None,
        color: str | None = None,
        category: str | None = None,
    ) -> Mask:
        """Create an empty mask (the name is made unique)."""
        return self._session.create_mask(self._doc, name, color, category).copy()

    def add_rect(self, rt1, rt2, mask: str | None = None, mode: str = "add") -> str:
        """
        Add the rectangle ``rt1=(min, max)`` (min) x ``rt2=(min, max)`` (s) to
        a mask (name or id; default: the last mask, created if there is
        none). Returns the operation id.
        """
        shape = {"type": "rect", "rt1": list(rt1), "rt2": list(rt2)}
        return self._session.add_shape(self._doc, self._target(mask), shape, mode)

    def add_polygon(self, vertices, mask: str | None = None, mode: str = "add") -> str:
        """Add a polygon (``[(rt1, rt2), ...]``) to a mask; see :meth:`add_rect`."""
        shape = {"type": "polygon", "vertices": [list(v) for v in vertices]}
        return self._session.add_shape(self._doc, self._target(mask), shape, mode)

    def subtract_rect(self, rt1, rt2, mask: str | None = None) -> str:
        return self.add_rect(rt1, rt2, mask, "subtract")

    def subtract_polygon(self, vertices, mask: str | None = None) -> str:
        return self.add_polygon(vertices, mask, "subtract")

    def add(
        self, masks: Mask | MaskSet | str | Path, replace: bool = False
    ) -> list[str]:
        """
        Import masks: a :class:`Mask`, a :class:`MaskSet`, a
        ``.gcxgc-masks.json`` file, a ``.tif`` or a folder of TIFFs. Names are
        made unique. Returns the new mask ids.
        """
        if isinstance(masks, Mask):
            masks = MaskSet([masks.copy()])
        if isinstance(masks, MaskSet):
            return self._session.import_masks(
                self._doc, mask_set=masks, replace_all=replace
            )
        return self._session.import_masks(self._doc, path=masks, replace_all=replace)

    def replace(self, masks: Mask | MaskSet | str | Path) -> list[str]:
        """Replace all masks (see :meth:`add`)."""
        return self.add(masks, replace=True)

    def remove(self, key: int | str) -> None:
        self._session.delete_mask(self._doc, self._id(key))

    def clear(self) -> None:
        """Remove all masks (one undo step)."""
        self.replace(MaskSet())

    def rename(self, key: int | str, name: str) -> None:
        self._session.update_mask(self._doc, self._id(key), name=name)

    def update(
        self,
        key: int | str,
        color: str | None = None,
        visible: bool | None = None,
        category: str | None = None,
    ) -> None:
        """Change a mask's colour, visibility or category."""
        self._session.update_mask(
            self._doc, self._id(key), color=color, visible=visible, category=category
        )

    def move(self, key: int | str, position: int) -> None:
        self._session.update_mask(self._doc, self._id(key), position=position)

    def duplicate(self, key: int | str, name: str | None = None) -> Mask:
        return self._session.duplicate_mask(self._doc, self._id(key), name).copy()

    def undo(self) -> bool:
        return self._session.undo_masks(self._doc)

    def redo(self) -> bool:
        return self._session.redo_masks(self._doc)

    def _id(self, key: int | str) -> str:
        try:
            return self._session.masks(self._doc).get(key).id
        except (KeyError, IndexError):
            raise InvalidRequest(f"No mask {key!r}") from None

    def _target(self, key: str | None) -> str:
        if key is not None and key not in self._session.masks(self._doc):
            return self.new_mask(key).id  # a new mask with that name
        if key is None:
            current = self._session.masks(self._doc)
            return current.masks[-1].id if len(current) else self.new_mask().id
        return self._id(key)


def _in_notebook() -> bool:
    """Whether this runs in a Jupyter (or VS Code) kernel."""
    try:
        from IPython import get_ipython
    except ImportError:
        return False
    shell = get_ipython()
    return shell is not None and hasattr(shell, "kernel")


__all__ = ["EVENTS", "LiveMasks", "Viewer", "view"]
