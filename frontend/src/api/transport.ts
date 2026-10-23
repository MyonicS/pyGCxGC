/**
 * How the frontend reaches the backend (spec NB-4). UI code only talks to a
 * {@link Transport}: the HTTP/WebSocket one below serves the desktop app, the
 * browser and local notebooks; an anywidget transport for remote kernels can
 * be added later without touching the UI.
 */

import { ARRAY_MEDIA_TYPE } from './arrays'
import type { SessionEvent } from './types'

export type Query = Record<string, string | number | boolean | null | undefined>

export interface RequestOptions {
  query?: Query
  body?: unknown
  /** Return the raw body (binary arrays, downloads). */
  binary?: boolean
  signal?: AbortSignal
}

export type ConnectionState = 'connecting' | 'open' | 'closed'

/** A file returned by the API (exports in the browser). */
export interface DownloadedFile {
  data: ArrayBuffer
  filename: string
  mediaType: string
}

export interface Transport {
  request<T = unknown>(method: string, path: string, options?: RequestOptions): Promise<T>
  /** Like `request`, but return the body as a file (name from Content-Disposition). */
  download(method: string, path: string, options?: RequestOptions): Promise<DownloadedFile>
  /** Receive session events; returns an unsubscribe function. */
  subscribe(
    onEvent: (event: SessionEvent) => void,
    onState: (state: ConnectionState) => void,
  ): () => void
}

/** An error response of the API (spec SHELL-4 shows `traceback` in a toast). */
export class ApiError extends Error {
  readonly status: number
  readonly type: string
  readonly traceback: string | null

  constructor(message: string, status: number, type = 'Error', traceback: string | null = null) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.type = type
    this.traceback = traceback
  }
}

function buildQuery(query?: Query): string {
  if (!query) return ''
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined && value !== null) params.set(key, String(value))
  }
  const text = params.toString()
  return text ? `?${text}` : ''
}

/** HTTP for commands and arrays, a WebSocket for events (spec API-2..4). */
export class HttpTransport implements Transport {
  readonly baseUrl: string
  readonly token: string

  constructor(token: string, baseUrl = '') {
    this.token = token
    this.baseUrl = baseUrl
  }

  async request<T = unknown>(method: string, path: string, options: RequestOptions = {}) {
    const response = await this.fetch(method, path, options)
    if (response.status === 204) return undefined as T
    if (options.binary) return (await response.arrayBuffer()) as T
    return (await response.json()) as T
  }

  async download(method: string, path: string, options: RequestOptions = {}) {
    const response = await this.fetch(method, path, { ...options, binary: true })
    const disposition = response.headers.get('Content-Disposition') ?? ''
    const match = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(disposition)
    return {
      data: await response.arrayBuffer(),
      filename: match ? decodeURIComponent(match[1]) : 'download',
      mediaType: response.headers.get('Content-Type') ?? 'application/octet-stream',
    }
  }

  private async fetch(method: string, path: string, options: RequestOptions): Promise<Response> {
    const headers: Record<string, string> = { Authorization: `Bearer ${this.token}` }
    let body: string | undefined
    if (options.body !== undefined) {
      headers['Content-Type'] = 'application/json'
      body = JSON.stringify(options.body)
    }
    if (options.binary) headers.Accept = `${ARRAY_MEDIA_TYPE}, application/octet-stream`
    let response: Response
    try {
      response = await fetch(`${this.baseUrl}${path}${buildQuery(options.query)}`, {
        method,
        headers,
        body,
        signal: options.signal,
      })
    } catch (err) {
      if ((err as Error).name === 'AbortError') throw err
      throw new ApiError('The pyGCxGC backend cannot be reached', 0, 'ConnectionError')
    }
    if (!response.ok) {
      let detail = `${response.status} ${response.statusText}`
      let type = 'HTTPError'
      let traceback: string | null = null
      try {
        const err = (await response.json()) as {
          detail?: unknown
          type?: string
          traceback?: string | null
        }
        if (typeof err.detail === 'string') detail = err.detail
        else if (Array.isArray(err.detail)) detail = formatValidation(err.detail)
        type = err.type ?? (response.status === 422 ? 'ValidationError' : type)
        traceback = err.traceback ?? null
      } catch {
        // not JSON
      }
      throw new ApiError(detail, response.status, type, traceback)
    }
    return response
  }

  subscribe(
    onEvent: (event: SessionEvent) => void,
    onState: (state: ConnectionState) => void,
  ): () => void {
    let socket: WebSocket | null = null
    let closed = false
    let retry = 0
    let timer: ReturnType<typeof setTimeout> | undefined

    const connect = () => {
      onState('connecting')
      const base = this.baseUrl || window.location.origin
      const url = new URL(`/api/events?token=${encodeURIComponent(this.token)}`, base)
      url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:'
      socket = new WebSocket(url)
      socket.onopen = () => {
        retry = 0
        onState('open')
      }
      socket.onmessage = (message) => {
        try {
          onEvent(JSON.parse(message.data as string) as SessionEvent)
        } catch (err) {
          console.error('Bad event', err)
        }
      }
      socket.onclose = () => {
        socket = null
        if (closed) return
        onState('closed')
        timer = setTimeout(connect, Math.min(5000, 250 * 2 ** retry++))
      }
    }
    connect()
    return () => {
      closed = true
      clearTimeout(timer)
      socket?.close()
    }
  }
}

function formatValidation(errors: unknown[]): string {
  return errors
    .map((e) => {
      const err = e as { loc?: unknown[]; msg?: string }
      const where = (err.loc ?? []).filter((p) => p !== 'body').join('.')
      return where ? `${where}: ${err.msg}` : (err.msg ?? 'Invalid request')
    })
    .join('; ')
}

/**
 * The per-launch token (spec API-2). It arrives as `?token=` in the URL; it
 * is moved to session storage and removed from the address bar.
 */
export function takeToken(): string | null {
  const url = new URL(window.location.href)
  const fromUrl = url.searchParams.get('token')
  if (fromUrl) {
    try {
      sessionStorage.setItem('pygcxgc.token', fromUrl)
    } catch {
      // storage unavailable: keep using the URL value
    }
    url.searchParams.delete('token')
    window.history.replaceState(null, '', url.toString())
    return fromUrl
  }
  try {
    return sessionStorage.getItem('pygcxgc.token')
  } catch {
    return null
  }
}
