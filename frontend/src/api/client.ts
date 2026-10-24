/** Typed functions for the API endpoints the frontend uses. */

import { decodeArray } from './arrays'
import type { Transport } from './transport'
import type {
  DocumentInfo,
  ExportRequest,
  IntegrationResult,
  MaskImport,
  MaskUpdate,
  Shape,
  FileProbe,
  FsListing,
  ImageHeader,
  JobInfo,
  LibrarySpectrumHeader,
  PeakEdit,
  SpectrumHeader,
  StructureInfo,
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

  /** Search the selection (or select `point` first) against the active libraries. */
  search(docId: string, point?: Omit<SelectRequest, 'scan' | 'mask_id'>) {
    return this.transport.request<SelectionInfo>('POST', '/api/search', {
      body: { document_id: docId, ...point },
    })
  }

  setSignal(docId: string, signal: 'tic' | 'eic', mz: number[] = []) {
    return this.transport.request<JobStarted>('PUT', `/api/documents/${enc(docId)}/signal`, {
      body: { signal, mz },
    })
  }

  /** Spectrum of the selection: rows of `[mz, raw, corrected]`. */
  async spectrum(docId: string, signal?: AbortSignal) {
    const buffer = await this.transport.request<ArrayBuffer>(
      'GET',
      `/api/documents/${enc(docId)}/spectrum`,
      { binary: true, signal },
    )
    const { header, data } = decodeArray<SpectrumHeader>(buffer)
    if (!(data instanceof Float32Array))
      throw new Error(`Unexpected spectrum dtype ${header.dtype}`)
    return { header, data }
  }

  peaks(docId: string) {
    return this.get<PeakTable | null>(`/api/documents/${enc(docId)}/peaks`)
  }

  detectPeaks(docId: string, req: { snr: number; identify?: boolean; n_average?: number }) {
    return this.transport.request<JobStarted>('POST', `/api/documents/${enc(docId)}/peaks`, {
      body: req,
    })
  }

  reidentifyPeaks(docId: string) {
    return this.transport.request<JobStarted>(
      'POST',
      `/api/documents/${enc(docId)}/peaks/reidentify`,
    )
  }

  editPeaks(docId: string, edits: PeakEdit[]) {
    return this.transport.request<void>('PATCH', `/api/documents/${enc(docId)}/peaks`, {
      body: edits,
    })
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

  // ---------------------------------------------------------------- masks
  private masksPath(docId: string, rest = '') {
    return `/api/documents/${enc(docId)}/masks${rest}`
  }

  createMask(docId: string, req: { name?: string; color?: string; category?: string } = {}) {
    return this.transport.request<MaskSetInfo>('POST', this.masksPath(docId), { body: req })
  }

  updateMask(docId: string, maskId: string, changes: MaskUpdate) {
    return this.transport.request<MaskSetInfo>('PATCH', this.masksPath(docId, `/${enc(maskId)}`), {
      body: changes,
    })
  }

  deleteMask(docId: string, maskId: string) {
    return this.transport.request<MaskSetInfo>('DELETE', this.masksPath(docId, `/${enc(maskId)}`))
  }

  duplicateMask(docId: string, maskId: string) {
    return this.transport.request<MaskSetInfo>(
      'POST',
      this.masksPath(docId, `/${enc(maskId)}/duplicate`),
    )
  }

  addShape(docId: string, maskId: string, shape: Shape, mode: 'add' | 'subtract' = 'add') {
    return this.transport.request<MaskSetInfo>(
      'POST',
      this.masksPath(docId, `/${enc(maskId)}/operations`),
      { body: { shape, mode } },
    )
  }

  replaceShape(docId: string, maskId: string, opId: string, shape: Shape) {
    return this.transport.request<MaskSetInfo>(
      'PUT',
      this.masksPath(docId, `/${enc(maskId)}/operations/${enc(opId)}`),
      { body: { shape } },
    )
  }

  deleteShape(docId: string, maskId: string, opId: string) {
    return this.transport.request<MaskSetInfo>(
      'DELETE',
      this.masksPath(docId, `/${enc(maskId)}/operations/${enc(opId)}`),
    )
  }

  undoMasks(docId: string) {
    return this.transport.request<MaskSetInfo>('POST', this.masksPath(docId, '/undo'))
  }

  redoMasks(docId: string) {
    return this.transport.request<MaskSetInfo>('POST', this.masksPath(docId, '/redo'))
  }

  importMasks(docId: string, req: MaskImport) {
    return this.transport.request<MaskSetInfo>('POST', this.masksPath(docId, '/import'), {
      body: req,
    })
  }

  integration(docId: string) {
    return this.get<IntegrationResult>(`/api/documents/${enc(docId)}/integration`)
  }

  /** Integrate the masks on other files (spec INT-4); the job result holds the CSV. */
  batchIntegrate(docId: string, paths: string[], path?: string) {
    return this.transport.request<JobStarted>('POST', `/api/documents/${enc(docId)}/batch`, {
      body: { paths, path },
    })
  }

  /** Export as a download (browser); with `path`, the backend writes the file. */
  exportFile(docId: string, req: ExportRequest) {
    return this.transport.download('POST', `/api/documents/${enc(docId)}/export`, { body: req })
  }

  exportTo(docId: string, req: ExportRequest & { path: string }) {
    return this.transport.request<{ path: string }>('POST', `/api/documents/${enc(docId)}/export`, {
      body: req,
    })
  }

  // ---------------------------------------------------------------- libraries
  libraries() {
    return this.get<LibraryInfo[]>('/api/libraries')
  }

  /** Load a library (`"mona"` downloads MoNA GC-MS) as a job. */
  addLibrary(path: string, activate = true, exclusive = false) {
    return this.transport.request<JobStarted>('POST', '/api/libraries', {
      body: { path, activate, exclusive },
    })
  }

  removeLibrary(libraryId: string) {
    return this.transport.request<void>('DELETE', `/api/libraries/${enc(libraryId)}`)
  }

  /** One library spectrum: rows of `[mz, intensity]`. */
  async librarySpectrum(libraryId: string, index: number) {
    const buffer = await this.transport.request<ArrayBuffer>(
      'GET',
      `/api/libraries/${enc(libraryId)}/spectra/${index}`,
      { binary: true },
    )
    const { header, data } = decodeArray<LibrarySpectrumHeader>(buffer)
    if (!(data instanceof Float32Array))
      throw new Error(`Unexpected spectrum dtype ${header.dtype}`)
    return { header, data }
  }

  structure(libraryId: string, index: number, online?: boolean) {
    return this.get<StructureInfo>(`/api/libraries/${enc(libraryId)}/spectra/${index}/structure`, {
      online,
    })
  }

  /** SVG depiction of a resolved structure (text). */
  async structureSvg(key: string, theme: 'light' | 'dark', w: number, h: number) {
    const buffer = await this.transport.request<ArrayBuffer>(
      'GET',
      `/api/structures/${enc(key)}.svg`,
      { binary: true, query: { theme, w, h } },
    )
    return new TextDecoder().decode(buffer)
  }

  setLibraryActive(libraryId: string, active: boolean, exclusive = false) {
    return this.transport.request<LibraryInfo>('PATCH', `/api/libraries/${enc(libraryId)}`, {
      body: { active, exclusive },
    })
  }
}
