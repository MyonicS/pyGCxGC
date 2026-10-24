/** Shared helpers of the end-to-end tests. */

import { execFileSync } from 'node:child_process'
import { existsSync } from 'node:fs'
import { resolve } from 'node:path'

import { expect, type APIRequestContext, type Page } from '@playwright/test'

export const TOKEN = 'e2e'
export const FID = resolve('../example_data/example_chromatograms/Example_FID.csv')
export const MASK_DIR = resolve('../example_data/example_masks')
export const DATA = resolve('e2e/.data')
export const MS = resolve(DATA, 'run.cdf')
export const MS_OTHER = resolve(DATA, 'other.cdf')
export const LIBRARY = resolve(DATA, 'library.msp')
export const headers = { Authorization: `Bearer ${TOKEN}` }

const VENV_PYTHON = resolve('../.venv/bin/python')
const PYTHON = process.env.PYTHON ?? (existsSync(VENV_PYTHON) ? VENV_PYTHON : 'python')

/** Run Python code (with pyGCxGC importable) and return its stdout. */
export function python(code: string, cwd?: string): string {
  return execFileSync(PYTHON, ['-c', code], {
    cwd,
    encoding: 'utf-8',
    env: { ...process.env, PYTHONPATH: resolve('../src') },
  })
}

export async function api<T = unknown>(
  request: APIRequestContext,
  method: string,
  path: string,
  data?: unknown,
): Promise<T> {
  const r = await request.fetch(path, { method, headers, data })
  expect(r.ok(), `${method} ${path}: ${r.status()} ${await r.text()}`).toBeTruthy()
  return (r.status() === 204 ? undefined : await r.json()) as T
}

/** Open a file through the API and wait for its job. */
export async function openDocument(request: APIRequestContext, path: string, params = {}) {
  const { job } = await api<{ job: { id: string } }>(request, 'POST', '/api/documents', {
    path,
    params,
  })
  for (;;) {
    const j = await api<{ status: string; result: { document_id: string } }>(
      request,
      'GET',
      `/api/jobs/${job.id}`,
    )
    if (j.status === 'done') return j.result.document_id
    expect(['queued', 'running']).toContain(j.status)
    await new Promise((r) => setTimeout(r, 100))
  }
}

export async function openApp(page: Page) {
  await page.goto(`/?token=${TOKEN}`)
  await expect(page.getByLabel('Backend open')).toBeVisible()
}

export async function imageShown(page: Page) {
  await expect(page.getByTestId('chromatogram-canvas')).toBeVisible()
  await expect(page.getByText('Loading image…')).toHaveCount(0)
  await page.waitForTimeout(300) // let the first frame and the colour bar settle
}

export async function resetSession(request: APIRequestContext) {
  await api(request, 'POST', '/api/project/close') // closes the documents too
  const libs = await api<{ id: string }[]>(request, 'GET', '/api/libraries')
  for (const l of libs) await api(request, 'DELETE', `/api/libraries/${l.id}`)
  await api(request, 'PUT', '/api/settings', {
    theme: 'system',
    recent_files: [],
    libraries: [],
    fid: { modulation_time: null },
    ms: { snap: true, n_average: 3, peak_snr: 10 },
    search: { mz_power: 0.5, int_power: 0.5, n_hits: 20, min_match: 0, mz_min: null, mz_max: null },
    structures: { online: 'ask' },
    ui: {
      layouts: { fid: null, ms: null, 'compact-fid': null, 'compact-ms': null },
      last_folder: null,
    },
  })
}

interface Grid {
  n_rt2: number
  n_mod: number
  rt1_start: number
  rt2_start: number
  modulation_time: number
  sampling_interval: number
}

/**
 * Screen position of a retention time pair in the unzoomed view of a
 * document (the full image fills the canvas).
 */
export async function screenPoint(
  page: Page,
  request: APIRequestContext,
  docId: string,
): Promise<(rt1: number, rt2: number) => [number, number]> {
  const { grid } = await api<{ grid: Grid }>(request, 'GET', `/api/documents/${docId}`)
  const box = (await page.getByTestId('chromatogram-canvas').boundingBox())!
  const x0 = grid.rt1_start
  const x1 = x0 + (grid.n_mod * grid.modulation_time) / 60
  const y0 = grid.rt2_start
  const y1 = y0 + grid.n_rt2 * grid.sampling_interval
  return (rt1, rt2) => [
    box.x + ((rt1 - x0) / (x1 - x0)) * box.width,
    box.y + (1 - (rt2 - y0) / (y1 - y0)) * box.height,
  ]
}

/** Load a library through the API and wait until it is ready. */
export async function addLibrary(request: APIRequestContext, path = LIBRARY) {
  const { job } = await api<{ job: { id: string } }>(request, 'POST', '/api/libraries', { path })
  for (;;) {
    const j = await api<{ status: string; result: { library_id: string } }>(
      request,
      'GET',
      `/api/jobs/${job.id}`,
    )
    if (j.status === 'done') return j.result.library_id
    expect(['queued', 'running']).toContain(j.status)
    await new Promise((r) => setTimeout(r, 100))
  }
}
