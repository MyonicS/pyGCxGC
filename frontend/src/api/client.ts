/** Typed functions for the API endpoints the frontend uses. */

import { decodeArray } from './arrays'
import type { Transport } from './transport'
import type {
  DocumentInfo,
  FileProbe,
  FsListing,
  ImageHeader,
  JobInfo,
  JobStarted,
  LibraryInfo,
  MaskSetInfo,
  OpenDocumentRequest,
  PeakTable,
  ProcessingParams,
  RasterHeader,
  SelectionInfo,
  SelectRequest,
  SessionInfo,
  Settings,
} from './types'

export interface ImageRequest {
  /** Max-pool the image (after `region`) to at most this many pixels per axis. */
  maxSize?: number
  /** `[row0, row1, col0, col1]`, half-open, in full-resolution pixels. */
  region?: [number, number, number, number]
  signal?: AbortSignal
}

const enc = encodeURIComponent

export class Api {
  readonly transport: Transport

  constructor(transport: Transport) {
    this.transport = transport
  }

  private get<T>(path: string, query?: Record<string, string | number | boolean | undefined>) {
    return this.transport.request<T>('GET', path, { query })
  }

  session() {
    return this.get<SessionInfo>('/api/session')
  }

  settings() {
    return this.get<Settings>('/api/settings')
  }

  updateSettings(changes: Settings) {
    return this.transport.request<Settings>('PUT', '/api/settings', { body: changes })
  }

  listDir(path?: string, hidden = false) {
    return this.get<FsListing>('/api/fs', { path, hidden })
  }

  probe(path: string) {
    return this.get<FileProbe>('/api/fs/probe', { path })
  }

  cancelJob(jobId: string) {
    return this.transport.request<JobInfo>('DELETE', `/api/jobs/${enc(jobId)}`)
  }

  openDocument(req: OpenDocumentRequest) {
    return this.transport.request<JobStarted>('POST', '/api/documents', { body: req })
  }

  document(docId: string) {
    return this.get<DocumentInfo>(`/api/documents/${enc(docId)}`)
  }

  updateDocument(docId: string, params: ProcessingParams) {
    return this.transport.request<JobStarted>('PATCH', `/api/documents/${enc(docId)}`, {
      body: params,
    })
  }

  closeDocument(docId: string) {
    return this.transport.request<void>('DELETE', `/api/documents/${enc(docId)}`)
  }

  async image(docId: string, req: ImageRequest = {}) {
    const buffer = await this.transport.request<ArrayBuffer>(
      'GET',
      `/api/documents/${enc(docId)}/image`,
      {
        binary: true,
        signal: req.signal,
        query: { max_size: req.maxSize, region: req.region?.join(',') },
      },
    )
    const { header, data } = decodeArray<ImageHeader>(buffer)
    if (!(data instanceof Float32Array)) throw new Error(`Unexpected image dtype ${header.dtype}`)
    return { header, data }
  }

  selection(docId: string) {
    return this.get<SelectionInfo | null>(`/api/documents/${enc(docId)}/selection`)
  }

  select(docId: string, req: SelectRequest) {
    return this.transport.request<SelectionInfo>('PUT', `/api/documents/${enc(docId)}/selection`, {
      body: req,
    })
  }

  peaks(docId: string) {
    return this.get<PeakTable | null>(`/api/documents/${enc(docId)}/peaks`)
  }

  masks(docId: string) {
    return this.get<MaskSetInfo>(`/api/documents/${enc(docId)}/masks`)
  }

  async maskRaster(docId: string, maskId: string) {
    const buffer = await this.transport.request<ArrayBuffer>(
      'GET',
      `/api/documents/${enc(docId)}/masks/${enc(maskId)}/raster`,
      { binary: true },
    )
    const { header, data } = decodeArray<RasterHeader>(buffer)
    if (!(data instanceof Uint8Array)) throw new Error(`Unexpected raster dtype ${header.dtype}`)
    return { header, data }
  }

  libraries() {
    return this.get<LibraryInfo[]>('/api/libraries')
  }

  setLibraryActive(libraryId: string, active: boolean, exclusive = false) {
    return this.transport.request<LibraryInfo>('PATCH', `/api/libraries/${enc(libraryId)}`, {
      body: { active, exclusive },
    })
  }
}
