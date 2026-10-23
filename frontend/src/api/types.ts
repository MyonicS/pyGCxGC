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
export type SelectRequest = Schemas['SelectRequest']
export type ErrorResponse = Schemas['ErrorResponse']

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
  | (EventBase & { type: 'masks.updated'; change?: string })
  | (EventBase & { type: 'peaks.updated'; n_peaks?: number; stale?: boolean })
  | (EventBase & { type: 'library.updated'; library: LibraryInfo })
  | (EventBase & { type: 'selection.changed'; selection: SelectionInfo | null })

interface EventBase {
  document_id: string | null
  revision: number
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
  recent_files?: string[]
  ui?: {
    layouts?: Partial<Record<DocumentKind, string>>
    last_folder?: string
  }
  [key: string]: unknown
}
