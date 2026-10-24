/** Aliases of the generated API types (`pnpm gen:api` writes `schema.d.ts`). */

import type { components } from './schema'

type Schemas = components['schemas']

export type SessionInfo = Schemas['SessionInfo']
export type DocumentInfo = Schemas['DocumentInfo']
export type GridInfo = Schemas['GridInfo']
export type JobInfo = Schemas['JobInfo']
export type LibraryInfo = Schemas['LibraryInfo']
export type SelectionInfo = Schemas['SelectionInfo']
export type HitInfo = Schemas['HitInfo']
export type PeakTable = Schemas['PeakTable']
export type MaskSetInfo = Schemas['MaskSetInfo']
export type MaskInfo = Schemas['MaskInfo']
export type IntegrationResult = Schemas['IntegrationResult']
export type FsListing = Schemas['FsListing']
export type FsEntry = Schemas['FsEntry']
export type FileProbe = Schemas['FileProbe']
export type ProcessingParams = Schemas['ProcessingParams']
export type OpenDocumentRequest = Schemas['OpenDocumentRequest']
export type JobStarted = Schemas['JobStarted']
/** Fields with defaults are optional in requests. */
export type SelectRequest = Partial<Schemas['SelectRequest']>
export type ErrorResponse = Schemas['ErrorResponse']
export type OperationInfo = Schemas['OperationInfo']
export type IntegrationRow = Schemas['IntegrationRow']
export type MaskUpdate = Schemas['MaskUpdate']
export type MaskImport = Schemas['MaskImport']
export type ExportRequest = Schemas['ExportRequest']
export type StructureInfo = Schemas['StructureInfo']
export type StructureData = Schemas['StructureData']
export type PeakEdit = Omit<Schemas['PeakEdit'], 'delete'> & { delete?: boolean }
export type LibraryAdd = Schemas['LibraryAdd']
export type ProjectInfo = Schemas['ProjectInfo']
export type ProjectSaved = Schemas['ProjectSaved']
export type SessionFiles = Schemas['SessionFiles']

/** A row of the peak table (`PeakTable.rows`). */
export interface PeakRow {
  peak_id: number
  rt1_min: number
  rt2_s: number
  scan: number
  rt2_index: number
  mod_index: number
  height: number
  volume: number
  n_pixels: number
  name: string
  match: number
  reverse_match: number
  formula: string
  mw: number
  cas: string
  nist_no: number
  library: string
  baseline: string
  assigned_by: '' | 'library' | 'user'
  category: string
}

/** Header of `GET /api/documents/{id}/spectrum` (columns `mz`, `raw`, `corrected`). */
export interface SpectrumHeader {
  document_id: string
  rt2_index: number
  mod_index: number
  scan: number
  rt1: number | null
  rt2: number | null
  n_average: number
  snap: boolean
  baseline: string
  snr: number
  columns: ['mz', 'raw', 'corrected']
}

/** Header of `GET /api/libraries/{id}/spectra/{index}` (columns `mz`, `intensity`). */
export interface LibrarySpectrumHeader {
  library_id: string
  index: number
  name: string
  formula: string
  mw: number
  cas: string
  nist_no: number
  inchikey: string
}

/** Shapes of mask operations (`OperationInfo.shape`). */
export type Shape =
  | { type: 'rect'; rt1: [number, number]; rt2: [number, number] }
  | { type: 'polygon' | 'lasso'; vertices: [number, number][] }

export type ShapeInfo = Shape | { type: 'raster'; shape?: number[] }

export type DocumentKind = DocumentInfo['kind']

/** `{start, step, count}` of an image axis (binary header). */
export interface AxisHeader {
  start: number
  step: number
  count: number
}

/** Header of `GET /api/documents/{id}/image`. */
export interface ImageHeader {
  axes: ['rt2', 'rt1']
  units: { rt1: 'min'; rt2: 's' }
  rt1: AxisHeader
  rt2: AxisHeader
  P: number
  dt: number
  grid: GridInfo
  document_id: string
  revision: number
  signal: 'tic' | 'eic'
  mz: number[]
  pool: [number, number]
  region: [number, number, number, number]
  full_shape: [number, number]
  min: number
  max: number
}

/** Header of `GET .../masks/{mask_id}/raster`. */
export interface RasterHeader {
  rt1: AxisHeader
  rt2: AxisHeader
  document_id: string
  mask_id: string
}

/** Events of `/api/events` (spec API-4). */
export type SessionEvent =
  | (EventBase & { type: 'job.progress' | 'job.done' | 'job.failed'; job: JobInfo })
  | (EventBase & {
      type: 'document.updated'
      change: string
      document: DocumentInfo | null
    })
  | (EventBase & {
      type: 'masks.updated'
      change?: string
      masks_revision?: number
      can_undo?: boolean
      can_redo?: boolean
    })
  | (EventBase & { type: 'peaks.updated'; n_peaks?: number; stale?: boolean })
  | (EventBase & { type: 'library.updated'; library: LibraryInfo })
  | (EventBase & { type: 'selection.changed'; selection: SelectionInfo | null })
  | (EventBase & { type: 'settings.updated'; settings: Settings })
  | (EventBase & {
      type: 'project.updated'
      change: 'saved' | 'opened' | 'closed'
      project: ProjectInfo | null
    })

interface EventBase {
  document_id: string | null
  revision: number
}

/** Library search settings (spec LIB-5). */
export interface SearchSettings {
  mz_power?: number
  int_power?: number
  n_hits?: number
  min_match?: number
  /** `null`: the acquired range. */
  mz_min?: number | null
  mz_max?: number | null
}

/** Settings keys the frontend uses (the backend stores any JSON). */
export type ThemeSetting = 'light' | 'dark' | 'system'

export interface Settings {
  theme?: ThemeSetting
  display?: {
    colormap?: string
    scaling?: string
    interpolation?: string
    reverse?: boolean
    log_range?: number
  }
  fid?: { modulation_time?: number | null }
  search?: SearchSettings
  structures?: { online?: 'ask' | 'on' | 'off' }
  ms?: { snap?: boolean; n_average?: number; peak_snr?: number }
  recent_files?: string[]
  ui?: {
    /** Saved dockview layouts by kind (`fid`, `ms`, `compact-fid`, `compact-ms`). */
    layouts?: Record<string, string | undefined>
    last_folder?: string
  }
  [key: string]: unknown
}
