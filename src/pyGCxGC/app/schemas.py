"""
pydantic models of the app API (the contract the TypeScript types are
generated from with ``openapi-typescript``).
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Info(BaseModel):
    """Response model; unknown fields are kept."""

    model_config = ConfigDict(extra="allow")


# ------------------------------------------------------------------ baseline
class NoneBaseline(Model):
    mode: Literal["none"] = "none"


class ModulationPercentileBaseline(Model):
    mode: Literal["modulation_percentile"] = "modulation_percentile"
    percentile: float = Field(25.0, ge=0, le=100)


class RollingPercentileBaseline(Model):
    mode: Literal["rolling_percentile"] = "rolling_percentile"
    percentile: float = Field(25.0, ge=0, le=100)
    window: int = Field(2, ge=0, description="Modulations on each side")


ImageBaseline = Annotated[
    NoneBaseline | ModulationPercentileBaseline | RollingPercentileBaseline,
    Field(discriminator="mode"),
]


class LocalBaseline(Model):
    mode: Literal["local"] = "local"
    gap: int = Field(3, ge=1, description="Scans between the point and the background")
    width: int = Field(5, ge=1, description="Background scans on each side")
    image_model: ImageBaseline | None = None


class ReferenceBaseline(Model):
    mode: Literal["reference"] = "reference"
    scan: int = Field(ge=0, description="Centre scan of the background")
    n_scans: int = Field(3, ge=1)
    image_model: ImageBaseline | None = None


BaselineSpec = Annotated[
    NoneBaseline
    | ModulationPercentileBaseline
    | RollingPercentileBaseline
    | LocalBaseline
    | ReferenceBaseline,
    Field(discriminator="mode"),
]


# ------------------------------------------------------------------ documents
class ProcessingParams(Model):
    """
    Processing parameters. FID: ``modulation_time``, ``sampling_interval``,
    ``shift`` (points), ``solvent_cutoff``, ``baseline`` (stridewise, global,
    none), ``normalize``. MS: ``modulation_time``, ``shift`` (scans),
    ``baseline`` (a model), ``snr``.
    """

    modulation_time: float | None = Field(None, gt=0, description="s")
    sampling_interval: float | None = Field(None, gt=0, description="s (FID)")
    shift: int | None = None
    solvent_cutoff: float | None = Field(None, ge=0, description="min (FID)")
    baseline: Literal["stridewise", "global", "none"] | BaselineSpec | None = None
    normalize: Literal["volume", "max", "none"] | None = None
    snr: float | None = Field(None, ge=0, description="Channel zeroing S/N (MS)")


class OpenDocumentRequest(Model):
    path: str
    kind: Literal["fid", "ms"] | None = None
    params: ProcessingParams = Field(default_factory=ProcessingParams)


class GridInfo(Model):
    n_rt2: int
    n_mod: int
    rt1_start: float
    rt2_start: float
    modulation_time: float
    sampling_interval: float


class DocumentInfo(Info):
    id: str
    kind: Literal["fid", "ms"]
    name: str
    path: str | None
    revision: int
    params: dict[str, Any]
    shape: list[int]
    grid: GridInfo
    summary: str
    warnings: list[str]
    n_masks: int
    signal: Literal["tic", "eic"] | None = None
    mz: list[int] | None = None
    mass_range: list[int] | None = None
    n_scans: int | None = None
    baseline: str | None = None
    has_peaks: bool | None = None
    sampling_inferred: bool | None = Field(
        None, description="FID: the sampling interval was inferred from the data"
    )


class SignalRequest(Model):
    signal: Literal["tic", "eic"] = "tic"
    mz: list[int] = Field(default_factory=list)


# ------------------------------------------------------------------ jobs
class JobInfo(Info):
    id: str
    kind: str
    label: str
    document_id: str | None
    status: Literal["queued", "running", "done", "failed", "cancelled"]
    progress: float
    message: str
    result: dict[str, Any] | None
    error: str | None
    traceback: str | None
    warnings: list[str]


class JobStarted(Model):
    job: JobInfo


# ------------------------------------------------------------------ libraries
class LibraryInfo(Info):
    id: str
    name: str
    path: str
    source: Literal["nist", "mona", "msp", "npz"]
    status: Literal["loading", "ready", "failed", "removed"]
    active: bool
    n_spectra: int | None
    licence: str
    cache_file: str | None
    cached: bool
    error: str | None


class LibraryAdd(Model):
    path: str = Field(description='NIST MAINLIB folder, .msp/.zip/.npz file or "mona"')
    activate: bool = True
    exclusive: bool = False


class LibraryUpdate(Model):
    active: bool
    exclusive: bool = False


# ------------------------------------------------------------------ session
class SessionInfo(Info):
    version: str
    revision: int
    documents: list[DocumentInfo]
    libraries: list[LibraryInfo]
    jobs: list[JobInfo]
    settings: dict[str, Any]


# ------------------------------------------------------------------ search
class SpectrumIn(Model):
    mz: list[float]
    intensity: list[float]


class SearchRequest(Model):
    """Search a point of a document (selecting it) or an explicit spectrum."""

    document_id: str | None = None
    rt2_index: int | None = None
    mod_index: int | None = None
    snap: bool = True
    n_average: int = Field(3, ge=1)
    spectrum: SpectrumIn | None = None
    n_hits: int | None = Field(None, ge=1, le=100)


class SelectRequest(Model):
    """A point (pixel or scan), or with ``mask_id`` the summed spectrum of a mask."""

    rt2_index: int | None = None
    mod_index: int | None = None
    scan: int | None = None
    snap: bool = True
    n_average: int = Field(3, ge=1)
    mask_id: str | None = Field(None, description="Region spectrum of this mask")


class HitInfo(Info):
    rank: int
    index: int
    name: str
    match: float
    reverse_match: float
    formula: str
    mw: int
    cas: str
    nist_no: int
    db_no: int
    library: str
    library_id: str | None


class RegionInfo(Info):
    mask_id: str
    name: str
    n_pixels: int


class SelectionInfo(Info):
    id: int = Field(0, description="Increases with every new selection")
    region: RegionInfo | None = Field(None, description="Set for a mask's spectrum")
    document_id: str
    rt2_index: int
    mod_index: int
    scan: int
    rt1: float | None
    rt2: float | None
    n_average: int
    snap: bool
    baseline: str
    snr: float
    hits: list[HitInfo] | None
    libraries: list[str]


# ------------------------------------------------------------------ peaks
class DetectPeaksRequest(Model):
    snr: float = Field(10.0, gt=0)
    identify: bool = True
    n_average: int = Field(3, ge=1)


class PeakEdit(Model):
    peak_id: int
    name: str | None = None
    hit_rank: int | None = Field(None, ge=1, description="Hit of the current selection")
    category: str | None = None
    delete: bool = False


class PeakTable(Info):
    document_id: str
    columns: list[str]
    rows: list[dict[str, Any]]
    stale: bool
    stale_reason: str | None
    libraries: list[str]
    baseline: str | None
    snr: float | None


# ------------------------------------------------------------------ structures
class StructureData(Info):
    smiles: str
    inchi: str
    inchikey: str
    pubchem_cid: int
    source: str
    key: str = Field(description="For /api/structures/{key}.svg")


class StructureInfo(Info):
    """Structure of a library entry (spec §8)."""

    library_id: str
    index: int
    name: str
    formula: str
    mw: int
    cas: str
    nist_no: int
    status: Literal["found", "not_found", "ask", "offline", "pending"]
    structure: StructureData | None
    job: JobInfo | None = Field(None, description="The lookup, when pending")
    depiction: bool = Field(description="RDKit can draw the structure")


# ------------------------------------------------------------------ masks
class RectShape(Model):
    type: Literal["rect"] = "rect"
    rt1: tuple[float, float] = Field(description="min")
    rt2: tuple[float, float] = Field(description="s")


class PolygonShape(Model):
    type: Literal["polygon", "lasso"] = "polygon"
    vertices: list[tuple[float, float]] = Field(
        min_length=3, description="[RT1 min, RT2 s]; a lasso is simplified"
    )


ShapeIn = Annotated[RectShape | PolygonShape, Field(discriminator="type")]


class OperationCreate(Model):
    mode: Literal["add", "subtract"] = "add"
    shape: ShapeIn


class OperationUpdate(Model):
    shape: ShapeIn


class OperationInfo(Info):
    id: str
    mode: Literal["add", "subtract"]
    shape: dict[str, Any]


class MaskCreate(Model):
    name: str | None = None
    color: str | None = None
    category: str | None = None


class MaskUpdate(Model):
    name: str | None = None
    color: str | None = None
    visible: bool | None = None
    category: str | None = None
    position: int | None = Field(None, ge=0)


class MaskInfo(Info):
    id: str
    name: str
    color: str
    visible: bool
    category: str | None
    operations: list[OperationInfo]
    n_pixels: int | None
    volume: float | None
    percent: float | None


class MaskSetInfo(Info):
    document_id: str
    revision: int
    can_undo: bool
    can_redo: bool
    masks: list[MaskInfo]
    error: str | None
    integration: IntegrationResult | None = Field(
        None, description="The integration (when the masks can be integrated)"
    )


class MaskImport(Model):
    path: str | None = None
    from_document: str | None = None
    mask_set: dict[str, Any] | None = None
    replace: bool = False


class IntegrationRow(Info):
    mask_id: str | None
    name: str
    volume: float
    percent: float | None
    n_pixels: int


class Overlap(Info):
    a: str
    b: str
    names: list[str]
    n_pixels: int


class IntegrationResult(Info):
    document_id: str
    signal: str
    mz: list[int]
    total: float
    masks: list[IntegrationRow]
    unassigned: IntegrationRow
    overlaps: list[Overlap]
    error: str | None
    masks_revision: int | None = Field(None, description="Mask revision integrated")


# ------------------------------------------------------------------ export, files
class ExportRequest(Model):
    what: Literal["masks", "mask_set", "integration", "peaks", "spectrum"]
    format: Literal["tiff", "json", "csv", "msp", "txt"] | None = None
    path: str | None = Field(None, description="Write here (desktop); else download")


class BatchRequest(Model):
    paths: list[str] = Field(min_length=1, description="Files of the same kind")
    path: str | None = Field(None, description="Also write the CSV here")


class ExportResult(Model):
    path: str


class FsEntry(Info):
    name: str
    path: str
    is_dir: bool
    size: int | None
    modified: float


class FsListing(Info):
    path: str
    parent: str | None
    entries: list[FsEntry]


class FileProbe(Info):
    """What opening a file would do (``kind`` is ``None`` with an ``error``)."""

    path: str
    name: str
    size: int | None
    kind: Literal["fid", "ms"] | None
    error: str | None
    needs_modulation_time: bool


class ErrorResponse(Info):
    detail: str
    type: str
    traceback: str | None = None
