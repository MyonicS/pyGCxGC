"""
Vector mask sets for 2D chromatograms.

A :class:`MaskSet` holds named masks. Each :class:`Mask` is an ordered list of
operations that add or subtract a rectangle, a polygon or a raster (an imported
TIFF). Shapes are stored in retention time units (RT1 in minutes, RT2 in
seconds), so a mask set can be applied to any chromatogram, also one with a
different modulation time or sampling rate. The pixel mask is produced by
replaying the operations on the chromatogram's :class:`Grid`.

Geometry
--------
Pixel ``(i, j)`` (row ``i`` along RT2, column ``j`` along RT1) is the rectangle
RT1 in ``[rt1[j], rt1[j] + P/60)`` and RT2 in ``[rt2[i], rt2[i] + dt)``, where
``P`` is the modulation time (s) and ``dt`` the sampling interval (s). A pixel
belongs to a rectangle or polygon when its centre lies inside it (even-odd
rule, like :meth:`matplotlib.path.Path.contains_points`).

Layouts
-------
``origin="upper"`` (the default of the public methods) is the layout of
``chrom.chrom_2D`` and of mask TIFFs: row 0 is the *highest* RT2.
``origin="lower"`` is the layout of :attr:`pyGCxGC.GCxGC_MS.tic_2d` and of the
app API: row 0 is the start of the modulation.

Weights
-------
Masks are weights between 0 and 1. Drawn shapes give 0 or 1. Imported TIFFs
keep their 8-bit values (``value / 255``), exactly as
:func:`pyGCxGC.integrate_masks` uses them.

Examples
--------
>>> import pyGCxGC as gcgc
>>> chrom = gcgc.parse_2D_chromatogram("run.csv", modulation_time=20)  # doctest: +SKIP
>>> masks = gcgc.MaskSet()
>>> aromatics = masks.new_mask("Monoaromatics", category="monoaromatics")
>>> aromatics.add_rect(rt1=(10, 25), rt2=(4, 9))
>>> aromatics.add_polygon([(26, 5), (40, 6), (40, 12), (26, 9)])
>>> volumes = masks.integrate(chrom)  # {"Monoaromatics": ...}  # doctest: +SKIP
>>> masks.save("run.gcxgc-masks.json")  # doctest: +SKIP
>>> masks.to_tiffs("masks/", chrom)  # doctest: +SKIP
"""

from __future__ import annotations

import base64
import functools
import itertools
import json
import uuid
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar

import numpy as np
import pandas as pd
import tifffile

from pyGCxGC.processing import integrate_2D

FORMAT = "pygcxgc-masks"
FORMAT_VERSION = 1
DEFAULT_COLORS = (
    "#e6194b",
    "#3cb44b",
    "#4363d8",
    "#f58231",
    "#911eb4",
    "#42d4f4",
    "#f032e6",
    "#bfef45",
    "#fabed4",
    "#ffe119",
)


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


# --------------------------------------------------------------------- grid
@dataclass(frozen=True)
class Grid:
    """
    Pixel grid of a 2D chromatogram (see the module docstring for the geometry).

    Attributes
    ----------
    n_rt2, n_mod : int
        Number of rows (points per modulation) and columns (modulations).
    rt1_start : float
        RT1 of the first modulation (min).
    modulation_time : float
        Modulation time ``P`` (s).
    sampling_interval : float
        Sampling interval ``dt`` (s).
    rt2_start : float
        RT2 of the first row (s).
    """

    n_rt2: int
    n_mod: int
    rt1_start: float
    modulation_time: float
    sampling_interval: float
    rt2_start: float = 0.0

    @property
    def shape(self) -> tuple[int, int]:
        """``(n_rt2, n_mod)``."""
        return (self.n_rt2, self.n_mod)

    @property
    def rt1(self) -> np.ndarray:
        """Start of every column (min)."""
        return self.rt1_start + np.arange(self.n_mod) * (self.modulation_time / 60.0)

    @property
    def rt2(self) -> np.ndarray:
        """Start of every row (s)."""
        return self.rt2_start + np.arange(self.n_rt2) * self.sampling_interval

    @property
    def rt1_centres(self) -> np.ndarray:
        return self.rt1 + self.modulation_time / 120.0

    @property
    def rt2_centres(self) -> np.ndarray:
        return self.rt2 + self.sampling_interval / 2.0

    @property
    def pixel_size(self) -> tuple[float, float]:
        """``(width in min, height in s)`` of a pixel."""
        return (self.modulation_time / 60.0, self.sampling_interval)

    @classmethod
    def from_chromatogram(cls, chrom) -> Grid:
        """
        Grid of a :class:`~pyGCxGC.GCxGC_FID` or :class:`~pyGCxGC.GCxGC_MS`.

        A :class:`Grid` is returned unchanged.
        """
        if isinstance(chrom, Grid):
            return chrom
        if hasattr(chrom, "tic_2d"):  # GCxGC_MS
            dt = float(chrom.sampling_interval)
            return cls(
                n_rt2=int(chrom.n_rt2),
                n_mod=int(chrom.n_modulations),
                rt1_start=float(chrom.rt1[0]),
                modulation_time=chrom.n_rt2 * dt,
                sampling_interval=dt,
            )
        if hasattr(chrom, "chrom_2D"):  # GCxGC_FID
            n_rt2, n_mod = chrom.chrom_2D.shape
            period = float(chrom.modulation_time)
            return cls(
                n_rt2=int(n_rt2),
                n_mod=int(n_mod),
                rt1_start=float(chrom.chrom_2D.columns[0]) / 60.0,
                modulation_time=period,
                sampling_interval=period / n_rt2,
                rt2_start=float(np.min(chrom.chrom_2D.index)),
            )
        raise TypeError(
            f"Expected a GCxGC_FID, GCxGC_MS or Grid, not {type(chrom).__name__}"
        )

    def to_dict(self) -> dict:
        return {
            "n_rt2": self.n_rt2,
            "n_mod": self.n_mod,
            "rt1_start": self.rt1_start,
            "rt2_start": self.rt2_start,
            "modulation_time": self.modulation_time,
            "sampling_interval": self.sampling_interval,
        }

    @classmethod
    def from_dict(cls, d: dict) -> Grid:
        return cls(
            n_rt2=int(d["n_rt2"]),
            n_mod=int(d["n_mod"]),
            rt1_start=float(d["rt1_start"]),
            modulation_time=float(d["modulation_time"]),
            sampling_interval=float(d["sampling_interval"]),
            rt2_start=float(d.get("rt2_start", 0.0)),
        )


# --------------------------------------------------------------------- shapes
@dataclass(frozen=True)
class Rect:
    """Rectangle in retention time units (RT1 in min, RT2 in s)."""

    rt1: tuple[float, float]
    rt2: tuple[float, float]
    type: ClassVar[str] = "rect"

    def __post_init__(self):
        object.__setattr__(self, "rt1", tuple(sorted(map(float, self.rt1))))
        object.__setattr__(self, "rt2", tuple(sorted(map(float, self.rt2))))

    def to_dict(self) -> dict:
        return {"type": self.type, "rt1": list(self.rt1), "rt2": list(self.rt2)}


@dataclass(frozen=True)
class Polygon:
    """Polygon (implicitly closed) with vertices ``(RT1 min, RT2 s)``."""

    vertices: tuple[tuple[float, float], ...]
    type: ClassVar[str] = "polygon"

    def __post_init__(self):
        verts = tuple((float(x), float(y)) for x, y in self.vertices)
        if len(verts) > 1 and verts[0] == verts[-1]:
            verts = verts[:-1]
        if len(verts) < 3:
            raise ValueError("A polygon needs at least three vertices")
        object.__setattr__(self, "vertices", verts)

    def to_dict(self) -> dict:
        return {"type": self.type, "vertices": [list(v) for v in self.vertices]}


@dataclass(frozen=True, eq=False)
class Raster:
    """
    Pixel weights (``uint8``, 255 = fully inside) in the ``origin="lower"`` layout.

    Rasters only apply to chromatograms with the same shape.
    """

    data: np.ndarray
    type: ClassVar[str] = "raster"

    def __post_init__(self):
        data = np.asarray(self.data)
        if data.ndim != 2 or data.dtype != np.uint8:
            raise ValueError("Raster data must be a 2D uint8 array")
        data = data.copy()
        data.flags.writeable = False
        object.__setattr__(self, "data", data)

    def to_dict(self) -> dict:
        packed = zlib.compress(np.ascontiguousarray(self.data).tobytes(), 6)
        return {
            "type": self.type,
            "shape": list(self.data.shape),
            "origin": "lower",
            "encoding": "zlib+base64",
            "data": base64.b64encode(packed).decode("ascii"),
        }


Shape = Rect | Polygon | Raster


def shape_from_dict(d: dict) -> Shape:
    """Inverse of ``shape.to_dict()``."""
    kind = d.get("type")
    if kind == "rect":
        return Rect(tuple(d["rt1"]), tuple(d["rt2"]))
    if kind == "polygon":
        return Polygon(tuple(tuple(v) for v in d["vertices"]))
    if kind == "raster":
        if d.get("encoding", "zlib+base64") != "zlib+base64":
            raise ValueError(f"Unknown raster encoding {d.get('encoding')!r}")
        raw = zlib.decompress(base64.b64decode(d["data"]))
        data = np.frombuffer(raw, dtype=np.uint8).reshape(d["shape"])
        if d.get("origin", "lower") == "upper":
            data = data[::-1]
        return Raster(data)
    raise ValueError(f"Unknown shape type {kind!r}")


@dataclass(frozen=True)
class Operation:
    """Add a shape to a mask or subtract it."""

    mode: str
    shape: Shape
    id: str = field(default_factory=_new_id)

    def __post_init__(self):
        if self.mode not in ("add", "subtract"):
            raise ValueError(f"mode must be 'add' or 'subtract', not {self.mode!r}")

    def to_dict(self) -> dict:
        return {"id": self.id, "mode": self.mode, "shape": self.shape.to_dict()}

    @classmethod
    def from_dict(cls, d: dict) -> Operation:
        return cls(d["mode"], shape_from_dict(d["shape"]), d.get("id") or _new_id())


# --------------------------------------------------------------- rasterising
def polygon_mask(vertices, x_centres, y_centres) -> np.ndarray:
    """
    Pixels whose centre lies inside a polygon (even-odd rule).

    Parameters
    ----------
    vertices : array-like, shape (k, 2)
        Polygon vertices ``(x, y)``; the polygon is closed implicitly.
    x_centres, y_centres : np.ndarray
        Increasing centre coordinates of the columns and rows.

    Returns
    -------
    np.ndarray
        Boolean array of shape ``(len(y_centres), len(x_centres))``.
    """
    v = np.asarray(vertices, dtype=np.float64)
    out = np.zeros((len(y_centres), len(x_centres)), dtype=bool)
    x0, y0 = v[:, 0], v[:, 1]
    x1, y1 = np.roll(x0, -1), np.roll(y0, -1)
    rows, crossings = [], []
    # scanline: each edge crosses the rows whose centre is in [min(y), max(y))
    for ax, ay, bx, by in zip(x0, y0, x1, y1, strict=True):
        if ay == by:
            continue
        lo, hi = (ay, by) if ay < by else (by, ay)
        r0 = np.searchsorted(y_centres, lo, side="left")
        r1 = np.searchsorted(y_centres, hi, side="left")
        if r1 <= r0:
            continue
        r = np.arange(r0, r1)
        rows.append(r)
        crossings.append(ax + (y_centres[r] - ay) * (bx - ax) / (by - ay))
    if not rows:
        return out
    r = np.concatenate(rows)
    # a crossing at x toggles every pixel whose centre lies to its right
    k = np.searchsorted(x_centres, np.concatenate(crossings), side="right")
    toggles = np.zeros((len(y_centres), len(x_centres) + 1), dtype=np.int32)
    np.add.at(toggles, (r, k), 1)
    np.cumsum(toggles, axis=1, out=toggles)
    out[:] = (toggles[:, :-1] & 1).astype(bool)
    return out


@functools.lru_cache(maxsize=128)
def _shape_pixels(shape: Rect | Polygon, grid: Grid) -> tuple[slice, slice, np.ndarray]:
    """Pixels of a vector shape: ``(rows, cols, block)`` in the lower layout."""
    xc, yc = grid.rt1_centres, grid.rt2_centres
    if isinstance(shape, Rect):
        (xa, xb), (ya, yb) = shape.rt1, shape.rt2
    else:
        v = np.asarray(shape.vertices)
        xa, xb = v[:, 0].min(), v[:, 0].max()
        ya, yb = v[:, 1].min(), v[:, 1].max()
    c0, c1 = np.searchsorted(xc, xa, "left"), np.searchsorted(xc, xb, "right")
    r0, r1 = np.searchsorted(yc, ya, "left"), np.searchsorted(yc, yb, "right")
    rows, cols = slice(int(r0), int(r1)), slice(int(c0), int(c1))
    if isinstance(shape, Rect):
        block = np.ones((r1 - r0, c1 - c0), dtype=bool)
    else:
        block = polygon_mask(shape.vertices, xc[cols], yc[rows])
    block.flags.writeable = False
    return rows, cols, block


def _raster_weights(raster: Raster, grid: Grid, mask_name: str) -> np.ndarray:
    if raster.data.shape != grid.shape:
        raise ValueError(
            f"Mask {mask_name!r} contains a raster layer (an imported TIFF) of "
            f"shape {raster.data.shape}, which does not match the chromatogram "
            f"{grid.shape}. Raster masks only apply to chromatograms of the same "
            "shape; redraw the mask as shapes to transfer it."
        )
    return raster.data / 255.0


def simplify_polygon(vertices, tolerance: float, scale=(1.0, 1.0)) -> np.ndarray:
    """
    Douglas-Peucker simplification of a closed polygon.

    Parameters
    ----------
    vertices : array-like, shape (k, 2)
        Polygon vertices.
    tolerance : float
        Maximum distance between the polygon and its simplification, in units
        of ``scale``.
    scale : (float, float)
        Size of one unit along x and y (e.g. the pixel size), so that the
        tolerance is isotropic in pixels.

    Returns
    -------
    np.ndarray
        The kept vertices (at least three).
    """
    v = np.asarray(vertices, dtype=np.float64)
    if len(v) > 1 and np.array_equal(v[0], v[-1]):
        v = v[:-1]
    if len(v) <= 3:
        return v
    p = v / np.asarray(scale, dtype=np.float64)
    # split the closed ring at the vertex farthest from the first one
    far = int(np.argmax(((p - p[0]) ** 2).sum(axis=1)))
    keep = np.zeros(len(p), dtype=bool)
    keep[[0, far]] = True
    ring = np.vstack([p, p[:1]])
    stack = [(0, far), (far, len(p))]
    while stack:
        a, b = stack.pop()
        if b - a < 2:
            continue
        seg = ring[b] - ring[a]
        pts = ring[a + 1 : b] - ring[a]
        norm = np.hypot(*seg)
        if norm == 0:
            dist = np.hypot(pts[:, 0], pts[:, 1])
        else:
            dist = np.abs(seg[0] * pts[:, 1] - seg[1] * pts[:, 0]) / norm
        i = int(np.argmax(dist))
        if dist[i] > tolerance:
            mid = a + 1 + i
            keep[mid % len(p)] = True
            stack.extend([(a, mid), (mid, b)])
    out = v[keep]
    return out if len(out) >= 3 else v


def simplify_lasso(vertices, grid: Grid) -> np.ndarray:
    """Simplify a freehand lasso to within half a pixel of ``grid``."""
    return simplify_polygon(vertices, 0.5, scale=grid.pixel_size)


# --------------------------------------------------------------------- masks
@dataclass
class Mask:
    """
    A named mask: an ordered list of add/subtract operations.

    Attributes
    ----------
    name : str
        Name (column name in integration results and TIFF file name).
    color : str
        Display colour (``#rrggbb``).
    visible : bool
        Display visibility (masks are integrated regardless).
    category : str or None
        Optional class of the region, e.g. ``"monoaromatics"``.
    operations : list of Operation
    id : str
        Stable identifier.
    """

    name: str
    color: str = DEFAULT_COLORS[0]
    visible: bool = True
    category: str | None = None
    operations: list[Operation] = field(default_factory=list)
    id: str = field(default_factory=_new_id)

    def add(self, shape: Shape, mode: str = "add") -> Operation:
        """Append an operation and return it."""
        op = Operation(mode, shape)
        self.operations.append(op)
        return op

    def add_rect(self, rt1, rt2) -> Operation:
        """Add the rectangle ``rt1=(min, max)`` (min) x ``rt2=(min, max)`` (s)."""
        return self.add(Rect(tuple(rt1), tuple(rt2)))

    def subtract_rect(self, rt1, rt2) -> Operation:
        """Subtract a rectangle (see :meth:`add_rect`)."""
        return self.add(Rect(tuple(rt1), tuple(rt2)), "subtract")

    def add_polygon(self, vertices) -> Operation:
        """Add a polygon with vertices ``[(rt1 min, rt2 s), ...]``."""
        return self.add(Polygon(tuple(map(tuple, vertices))))

    def subtract_polygon(self, vertices) -> Operation:
        """Subtract a polygon (see :meth:`add_polygon`)."""
        return self.add(Polygon(tuple(map(tuple, vertices))), "subtract")

    def add_raster(self, weights, origin: str = "upper") -> Operation:
        """
        Add a pixel mask.

        Parameters
        ----------
        weights : np.ndarray
            ``bool`` or 0/1 masks, ``uint8`` masks with 255 = inside (TIFFs), or
            floats between 0 and 1.
        origin : {"upper", "lower"}
            Layout of ``weights`` (see the module docstring).
        """
        return self.add(Raster(_to_raster(weights, origin)))

    def rasterize(self, target, origin: str = "upper") -> np.ndarray:
        """
        Pixel weights (0-1, ``float64``) of the mask on a chromatogram or grid.

        Parameters
        ----------
        target : GCxGC_FID, GCxGC_MS or Grid
        origin : {"upper", "lower"}
            Layout of the result. ``"upper"`` matches ``chrom.chrom_2D``.
        """
        grid = Grid.from_chromatogram(target)
        weights = np.zeros(grid.shape, dtype=np.float64)
        for op in self.operations:
            if isinstance(op.shape, Raster):
                w = _raster_weights(op.shape, grid, self.name)
                if op.mode == "add":
                    np.maximum(weights, w, out=weights)
                else:
                    np.minimum(weights, 1.0 - w, out=weights)
            else:
                rows, cols, block = _shape_pixels(op.shape, grid)
                weights[rows, cols][block] = 1.0 if op.mode == "add" else 0.0
        return _orient(weights, origin)

    def copy(self, name: str | None = None, new_id: bool = False) -> Mask:
        """Copy (operations are immutable and shared)."""
        return Mask(
            name=self.name if name is None else name,
            color=self.color,
            visible=self.visible,
            category=self.category,
            operations=list(self.operations),
            id=_new_id() if new_id else self.id,
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "color": self.color,
            "visible": self.visible,
            "category": self.category,
            "operations": [op.to_dict() for op in self.operations],
        }

    @classmethod
    def from_dict(cls, d: dict) -> Mask:
        return cls(
            name=str(d["name"]),
            color=d.get("color") or DEFAULT_COLORS[0],
            visible=bool(d.get("visible", True)),
            category=d.get("category"),
            operations=[Operation.from_dict(o) for o in d.get("operations", [])],
            id=d.get("id") or _new_id(),
        )


class MaskSet:
    """
    An ordered collection of uniquely named masks.

    Parameters
    ----------
    masks : list of Mask, optional
    grid : Grid, optional
        The grid the masks were drawn on (stored in the JSON file).
    name : str, optional

    Examples
    --------
    See the module docstring of :mod:`pyGCxGC.masks`.
    """

    def __init__(
        self, masks: list[Mask] | None = None, grid: Grid | None = None, name: str = ""
    ):
        self.masks: list[Mask] = []
        self.grid = grid
        self.name = name
        for mask in masks or []:
            self.append(mask)

    # ------------------------------------------------------------ container
    def __len__(self) -> int:
        return len(self.masks)

    def __iter__(self):
        return iter(self.masks)

    def __getitem__(self, key: int | str) -> Mask:
        return self.get(key)

    def __contains__(self, key: str) -> bool:
        return any(key in (m.id, m.name) for m in self.masks)

    def __repr__(self) -> str:
        return f"MaskSet({self.names!r})"

    @property
    def names(self) -> list[str]:
        return [m.name for m in self.masks]

    def get(self, key: int | str) -> Mask:
        """Mask by position, id or name."""
        if isinstance(key, int):
            return self.masks[key]
        for m in self.masks:
            if key in (m.id, m.name):
                return m
        raise KeyError(f"No mask {key!r}")

    def index(self, key: str) -> int:
        """Position of a mask (by id or name)."""
        return self.masks.index(self.get(key))

    def unique_name(self, base: str) -> str:
        """``base``, or ``base (2)``, ``base (3)``, ... if it is taken."""
        names = set(self.names)
        if base not in names:
            return base
        for i in itertools.count(2):
            candidate = f"{base} ({i})"
            if candidate not in names:
                return candidate
        raise AssertionError  # pragma: no cover

    def append(self, mask: Mask) -> Mask:
        """Add a mask; its name must be unique."""
        if mask.name in self.names:
            raise ValueError(f"A mask named {mask.name!r} already exists")
        if any(m.id == mask.id for m in self.masks):
            mask.id = _new_id()
        self.masks.append(mask)
        return mask

    def new_mask(
        self,
        name: str | None = None,
        color: str | None = None,
        category: str | None = None,
    ) -> Mask:
        """Create an empty mask (the name is made unique)."""
        name = self.unique_name(name or f"Mask {len(self.masks) + 1}")
        color = color or DEFAULT_COLORS[len(self.masks) % len(DEFAULT_COLORS)]
        return self.append(Mask(name=name, color=color, category=category))

    def remove(self, key: int | str) -> Mask:
        mask = self.get(key)
        self.masks.remove(mask)
        return mask

    def duplicate(self, key: int | str, name: str | None = None) -> Mask:
        """Copy a mask and insert it after the original."""
        mask = self.get(key)
        copy = mask.copy(self.unique_name(name or f"{mask.name} copy"), new_id=True)
        self.masks.insert(self.masks.index(mask) + 1, copy)
        return copy

    def rename(self, key: int | str, name: str) -> Mask:
        mask = self.get(key)
        if name != mask.name and name in self.names:
            raise ValueError(f"A mask named {name!r} already exists")
        if not name:
            raise ValueError("Mask names must not be empty")
        mask.name = name
        return mask

    def move(self, key: int | str, position: int) -> None:
        """Move a mask to ``position`` in the list."""
        mask = self.remove(key)
        self.masks.insert(max(0, min(position, len(self.masks))), mask)

    def copy(self) -> MaskSet:
        """Copy; masks are copied, the immutable operations are shared."""
        out = MaskSet(grid=self.grid, name=self.name)
        out.masks = [m.copy() for m in self.masks]
        return out

    # ------------------------------------------------------------ pixels
    def rasterize(self, target, origin: str = "upper") -> dict[str, np.ndarray]:
        """
        Pixel weights of every mask.

        Parameters
        ----------
        target : GCxGC_FID, GCxGC_MS or Grid
        origin : {"upper", "lower"}
            ``"upper"`` (default) gives the ``chrom.chrom_2D`` / TIFF layout.

        Returns
        -------
        dict
            ``name -> float64 array`` of weights between 0 and 1.
        """
        return {m.name: m.rasterize(target, origin) for m in self.masks}

    def integrate(self, target, data=None) -> dict[str, float]:
        """
        Integrated volume of every mask.

        The result equals :func:`pyGCxGC.integrate_masks` with the masks
        exported by :meth:`to_tiffs`.

        Parameters
        ----------
        target : GCxGC_FID or GCxGC_MS
            The chromatogram (its ``chrom_2D`` is integrated), or a
            :class:`Grid` when ``data`` is given.
        data : np.ndarray or pandas.DataFrame, optional
            Signal to integrate instead of ``target.chrom_2D``, in the
            ``chrom_2D`` layout (e.g. an EIC flipped with ``[::-1]``).
        """
        values = _signal(target, data)
        return {
            name: _masked_volume(values, w)
            for name, w in self.rasterize(target).items()
        }

    def integration(self, target, data=None) -> pd.DataFrame:
        """
        Integration table.

        Returns
        -------
        pandas.DataFrame
            Indexed by mask name plus ``unassigned`` (total minus the union of
            all masks), with the columns ``volume``, ``percent`` (of the total
            volume) and ``n_pixels``. ``table.attrs["total"]`` is the total
            volume.
        """
        values = _signal(target, data)
        total = float(integrate_2D(values))
        weights = self.rasterize(target)
        rows = []
        union = np.zeros(values.shape)
        for name, w in weights.items():
            np.maximum(union, w, out=union)
            rows.append((name, _masked_volume(values, w), int((w > 0).sum())))
        unassigned = total - _masked_volume(values, union)
        rows.append(("unassigned", unassigned, int((union == 0).sum())))
        table = pd.DataFrame(rows, columns=["mask", "volume", "n_pixels"])
        table["percent"] = table["volume"] / total * 100 if total else np.nan
        table = table.set_index("mask")[["volume", "percent", "n_pixels"]]
        table.attrs["total"] = total
        return table

    def overlaps(self, target) -> list[tuple[str, str, int]]:
        """``(name_a, name_b, shared pixels)`` of every overlapping pair."""
        inside = {k: w > 0 for k, w in self.rasterize(target, "lower").items()}
        out = []
        for (a, wa), (b, wb) in itertools.combinations(inside.items(), 2):
            n = int(np.count_nonzero(wa & wb))
            if n:
                out.append((a, b, n))
        return out

    # ------------------------------------------------------------ TIFF
    def to_tiffs(self, folder: str | Path, target) -> list[Path]:
        """
        Write every mask as ``<folder>/<name>.tif``.

        The TIFFs are ``uint8`` (0/255) in the ``chrom_2D`` layout and shape, as
        written by the mask creator, so they work with
        :func:`pyGCxGC.integrate_masks` and :func:`pyGCxGC.mask_chromatogram`.
        """
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        paths = []
        for name, w in self.rasterize(target).items():
            path = folder / f"{_safe_filename(name)}.tif"
            tifffile.imwrite(path, np.rint(w * 255).astype(np.uint8))
            paths.append(path)
        return paths

    def add_tiff(
        self, path: str | Path, target=None, name: str | None = None, **kwargs
    ) -> Mask:
        """
        Import a mask TIFF (``chrom_2D`` layout) as a raster mask.

        Parameters
        ----------
        path : str or Path
            The TIFF file.
        target : GCxGC_FID, GCxGC_MS or Grid, optional
            If given, the TIFF shape is checked against it.
        name : str, optional
            Mask name (default: the file name without extension).
        **kwargs
            ``color`` and ``category`` of the new mask.
        """
        path = Path(path)
        weights = tifffile.imread(path)
        if target is not None:
            grid = Grid.from_chromatogram(target)
            if weights.shape != grid.shape:
                raise ValueError(
                    f"Mask {path.name} has shape {weights.shape}, the chromatogram "
                    f"{grid.shape}"
                )
            self.grid = self.grid or grid
        mask = self.new_mask(name or path.stem, **kwargs)
        mask.add_raster(weights, origin="upper")
        return mask

    @classmethod
    def from_tiffs(cls, masks, target=None) -> MaskSet:
        """
        Mask set from TIFF files (as accepted by :func:`pyGCxGC.integrate_masks`).

        Parameters
        ----------
        masks : str, Path or list
            A folder with ``.tif`` files or a list of files.
        target : GCxGC_FID, GCxGC_MS or Grid, optional
            Used to check the shapes and stored as the grid.
        """
        if isinstance(masks, str | Path):
            paths = sorted(
                p
                for p in Path(masks).iterdir()
                if p.suffix.lower() in (".tif", ".tiff")
            )
            if not paths:
                raise ValueError(f"No .tif masks found in {masks}")
        else:
            paths = [Path(p) for p in masks]
        out = cls()
        for path in paths:
            out.add_tiff(path, target)
        return out

    # ------------------------------------------------------------ JSON
    def to_dict(self) -> dict:
        return {
            "format": FORMAT,
            "version": FORMAT_VERSION,
            "name": self.name,
            "grid": self.grid.to_dict() if self.grid else None,
            "masks": [m.to_dict() for m in self.masks],
        }

    @classmethod
    def from_dict(cls, d: dict) -> MaskSet:
        if d.get("format") != FORMAT:
            raise ValueError("Not a pyGCxGC mask set")
        if int(d.get("version", 0)) > FORMAT_VERSION:
            raise ValueError(
                f"Mask set format version {d['version']} is newer than this "
                f"pyGCxGC supports ({FORMAT_VERSION}); please update pyGCxGC"
            )
        grid = Grid.from_dict(d["grid"]) if d.get("grid") else None
        return cls(
            [Mask.from_dict(m) for m in d.get("masks", [])], grid, d.get("name", "")
        )

    def save(self, path: str | Path) -> Path:
        """Write the mask set as JSON (by convention ``*.gcxgc-masks.json``)."""
        path = Path(path)
        path.write_text(json.dumps(self.to_dict(), indent=1), encoding="utf-8")
        return path

    @classmethod
    def load(cls, path: str | Path) -> MaskSet:
        """Read a mask set written by :meth:`save`."""
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


# ------------------------------------------------------------------ helpers
def _orient(weights: np.ndarray, origin: str) -> np.ndarray:
    if origin == "lower":
        return weights
    if origin == "upper":
        return weights[::-1].copy()
    raise ValueError(f"origin must be 'upper' or 'lower', not {origin!r}")


def _to_raster(weights, origin: str) -> np.ndarray:
    w = np.asarray(weights)
    if w.dtype == bool:
        data = w.astype(np.uint8) * 255
    elif np.issubdtype(w.dtype, np.floating):
        if w.size and (w.min() < 0 or w.max() > 1):
            w = w / 255.0 if w.max() > 1 else w
        data = np.rint(np.clip(w, 0, 1) * 255).astype(np.uint8)
    else:
        top = int(w.max()) if w.size else 0
        if top <= 1:
            data = (w * 255).astype(np.uint8)
        else:
            data = np.clip(w, 0, 255).astype(np.uint8)
    return _orient(data, origin)  # flipping is its own inverse


def _signal(target, data) -> pd.DataFrame | np.ndarray:
    if data is None:
        if not hasattr(target, "chrom_2D"):
            raise TypeError("Pass the signal as `data` when integrating on a Grid")
        data = target.chrom_2D
    if not isinstance(data, pd.DataFrame):
        data = np.asarray(data, dtype=np.float64)
    return data


def _masked_volume(signal, weights: np.ndarray) -> float:
    """Volume of ``signal * weights``, computed exactly like ``integrate_masks``."""
    masked = signal * weights
    if not isinstance(masked, pd.DataFrame):
        masked = pd.DataFrame(masked)
    return float(integrate_2D(masked))


def _safe_filename(name: str) -> str:
    keep = "".join(c if c.isalnum() or c in " ._-()" else "_" for c in name).strip()
    return keep or "mask"
