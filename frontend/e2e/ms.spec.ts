/**
 * End-to-end tests of milestone 3 (spec §7, §8): click → spectrum → hits →
 * head-to-tail → structure (PubChem stubbed in e2e/backend.py), library
 * switching, EIC, peak detection and editing, region spectra and the
 * spectra-only baselines, on the synthetic GCxGC-MS run. Screenshots of the
 * MS layout in both themes (THEME-5).
 */

import { readFileSync } from 'node:fs'

import { expect, test, type APIRequestContext, type Page } from '@playwright/test'

import {
  addLibrary,
  api,
  DATA,
  download,
  pngSize,
  imageShown,
  MS,
  openApp,
  openDocument,
  resetSession,
  screenPoint,
} from './helpers'

// compounds of tests/synthetic.py: modulation (4 s each) and RT2 (s)
const TOLUENE = [(10.5 * 4) / 60, 1.0] as const
const DECANE = [(18.5 * 4) / 60, 1.6] as const

interface Selection {
  id: number
  scan: number
  hits: { name: string; rank: number }[] | null
  region: { name: string } | null
  baseline: string
}

interface PeakTable {
  rows: {
    peak_id: number
    scan: number
    name: string
    assigned_by: string
    rt1_min: number
    rt2_s: number
  }[]
  stale: boolean
}

test.beforeEach(async ({ request }) => {
  await resetSession(request)
})

async function openMs(page: Page, request: APIRequestContext, library = true) {
  const docId = await openDocument(request, MS, { modulation_time: 4 })
  if (library) await addLibrary(request)
  await openApp(page)
  await imageShown(page)
  const at = await screenPoint(page, request, docId)
  return { docId, at }
}

const tab = (page: Page, name: string) => page.locator('.dv-tab', { hasText: name }).click()

test('click → spectrum → hits → match → structure', async ({ page, request }) => {
  const { docId, at } = await openMs(page, request)
  await page.mouse.click(...at(...TOLUENE))

  const header = page.getByTestId('spectrum-header')
  await expect(header).toContainText('RT1 0.667 min · RT2 1.000 s')
  await expect(header).toContainText('3 scans · snapped')
  await expect(header).toContainText('baseline modulation_percentile(percentile=25)')
  const hits = page.getByTestId('hits-panel')
  await expect(hits.getByRole('row').nth(1)).toContainText('Toluene')
  await expect(page.getByTestId('match-header')).toContainText('Toluene')
  await expect(page.getByTestId('match-header')).toContainText(/MF 99\d/)
  // the structure from the library entry (MoNA-style identifiers)
  await expect(page.getByTestId('structure-image')).toBeVisible()
  await expect(page.getByTestId('structure-source')).toHaveText('Structure: library')
  // the selected point is marked
  await expect(page.getByTestId('selection-marker')).toBeVisible()
  await page.mouse.move(0, 0)
  await expect(page).toHaveScreenshot('ms-search.png')

  // hovering a stick shows m/z and intensity
  const plot = page.getByTestId('spectrum-plot')
  const label = (await plot.locator('text', { hasText: /^91$/ }).boundingBox())!
  await page.mouse.move(label.x + label.width / 2, label.y + label.height + 8)
  await expect(page.getByTestId('stick-tooltip')).toContainText('m/z 91')

  // another hit in the Match panel
  await hits.getByRole('row').nth(2).click()
  await expect(page.getByTestId('match-header')).toContainText('Naphthalene')

  // raw spectrum and absolute intensities change only the display
  await page.getByRole('radio', { name: 'Raw' }).click()
  await page.getByRole('radio', { name: 'Absolute' }).click()
  await expect(plot.getByTestId('sticks-top')).toBeVisible()

  // Decane has no structure in the library: the app asks before PubChem
  await page.mouse.click(...at(...DECANE))
  await expect(page.getByTestId('match-header')).toContainText('Decane')
  // (lookups are cached by the backend: the first project asks, later ones find it)
  const ask = page.getByTestId('structure-ask')
  await expect(ask.or(page.getByTestId('structure-source'))).toBeVisible()
  if (await ask.isVisible()) {
    await expect(ask).toContainText('Only compound identifiers of library hits are sent')
    await ask.getByRole('button', { name: 'Fetch' }).click()
  }
  await expect(page.getByTestId('structure-source')).toHaveText('Structure: PubChem')
  await expect(page.getByRole('link', { name: 'PubChem' })).toHaveAttribute(
    'href',
    'https://pubchem.ncbi.nlm.nih.gov/compound/15600',
  )
  if (test.info().project.name === 'light') {
    const settings = await api<{ structures: { online: string } }>(request, 'GET', '/api/settings')
    expect(settings.structures.online).toBe('on')
  }

  // without snapping, the clicked pixel is selected
  await page.getByTestId('snap').click()
  await page.mouse.click(...at(...TOLUENE))
  await expect(header).not.toContainText('snapped')
  await expect
    .poll(async () => {
      const sel = await api<Selection>(request, 'GET', `/api/documents/${docId}/selection`)
      return sel.hits?.[0]?.name
    })
    .toBe('Toluene')
})

test('libraries: add through the file browser, switch, remove', async ({ page, request }) => {
  const { at } = await openMs(page, request, false)
  await page.mouse.click(...at(...TOLUENE))
  const hits = page.getByTestId('hits-panel')
  await expect(hits).toContainText('No active library')

  await tab(page, 'Libraries')
  await page.getByTestId('add-library').click()
  const dialog = page.getByTestId('path-dialog')
  const folder = dialog.getByRole('textbox', { name: 'Folder', exact: true })
  await folder.fill(DATA)
  await folder.press('Enter')
  await dialog.getByRole('option', { name: /^library\.msp/ }).click()
  await page.getByTestId('path-confirm').click()

  const row = page.getByTestId('library-row')
  await expect(row).toContainText('4 spectra')
  await expect(row).toContainText('MSP')
  // the selected point is searched again once the library is ready (LIB-2)
  await tab(page, 'Hits')
  await expect(hits.getByRole('row').nth(1)).toContainText('Toluene')

  // switching the library off from the Match panel re-searches too
  await page.getByTestId('match-panel').getByTestId('library-picker').click()
  await page.getByRole('menuitemcheckbox', { name: /library/ }).click()
  await page.keyboard.press('Escape')
  await expect(hits).toContainText('No active library')
  await expect(page.getByTestId('library-picker').first()).toContainText('No library')

  await tab(page, 'Libraries')
  await row.getByRole('switch').click()
  await tab(page, 'Hits')
  await expect(hits.getByRole('row').nth(1)).toContainText('Toluene')

  // search settings: fewer hits re-search the point
  await tab(page, 'Libraries')
  await page.locator('#search-n_hits').fill('1')
  await page.locator('#search-n_hits').press('Enter')
  await tab(page, 'Hits')
  await expect(hits.getByRole('row')).toHaveCount(2) // header + 1 hit

  await tab(page, 'Libraries')
  await page.getByRole('button', { name: 'Remove library' }).click()
  await expect(row).toHaveCount(0)
})

test('EIC, peaks, assign and rename, export', async ({ page, request }) => {
  const { docId } = await openMs(page, request)

  // EIC from an m/z list; empty returns to the TIC (MS-3)
  const eic = page.getByTestId('eic-input')
  await eic.fill('91 92')
  await eic.press('Enter')
  await expect(
    page.getByRole('region', { name: 'Chromatogram' }).getByText('EIC m/z 91, 92', { exact: true }),
  ).toBeVisible()
  let doc = await api<{ signal: string; mz: number[] }>(request, 'GET', `/api/documents/${docId}`)
  expect(doc).toMatchObject({ signal: 'eic', mz: [91, 92] })
  await eic.fill('')
  await eic.press('Enter')
  await expect(eic).toHaveAttribute('placeholder', 'TIC')
  doc = await api(request, 'GET', `/api/documents/${docId}`)
  expect(doc.signal).toBe('tic')

  await tab(page, 'Peaks')
  const panel = page.getByTestId('peaks-panel')
  await panel.getByLabel('Peak S/N').fill('30')
  await page.getByTestId('detect-peaks').click()
  await expect(panel).toContainText(/\d+ peaks · S\/N 30/)
  const table = await api<PeakTable>(request, 'GET', `/api/documents/${docId}/peaks`)
  const toluene = table.rows.find((r) => r.name === 'Toluene')!
  expect(toluene).toBeTruthy()

  // filter and select a peak: its apex becomes the selection
  await panel.getByLabel('Filter by name').fill('tol')
  await expect(panel).toContainText(`1 of ${table.rows.length} peaks`)
  await panel.getByRole('row').nth(1).click()
  await expect
    .poll(
      async () =>
        (await api<Selection | null>(request, 'GET', `/api/documents/${docId}/selection`))?.scan,
    )
    .toBe(toluene.scan)

  // assign the second hit (MS-9)
  await tab(page, 'Hits')
  const hits = page.getByTestId('hits-panel')
  await hits.getByRole('row').nth(2).click()
  await page.getByTestId('assign-hit').click()
  await expect
    .poll(async () => {
      const t = await api<PeakTable>(request, 'GET', `/api/documents/${docId}/peaks`)
      return t.rows.find((r) => r.peak_id === toluene.peak_id)
    })
    .toMatchObject({ name: 'Naphthalene', assigned_by: 'user' })

  // rename in place
  await tab(page, 'Peaks')
  await panel.getByLabel('Filter by name').fill('')
  const cell = panel.locator(`[row-id="${toluene.peak_id}"] [col-id="name"]`)
  await cell.dblclick()
  await page.keyboard.press('ControlOrMeta+a')
  await page.keyboard.type('Methylbenzene')
  await page.keyboard.press('Enter')
  await expect
    .poll(async () => {
      const t = await api<PeakTable>(request, 'GET', `/api/documents/${docId}/peaks`)
      return t.rows.find((r) => r.peak_id === toluene.peak_id)?.name
    })
    .toBe('Methylbenzene')

  // export with the identities and who assigned them
  const download = page.waitForEvent('download')
  await panel.getByRole('button', { name: 'Export peak table' }).click()
  const path = await (await download).path()
  const csv = readFileSync(path, 'utf-8')
  expect(csv.split('\n')[0]).toContain('assigned_by')
  expect(csv).toContain('Methylbenzene')

  // switching the library off makes the table stale; re-identify keeps edits
  await api(
    request,
    'PATCH',
    `/api/libraries/${(await api<{ id: string }[]>(request, 'GET', '/api/libraries'))[0].id}`,
    {
      active: false,
    },
  )
  await expect(page.getByTestId('peaks-stale')).toBeVisible()
  await page.getByTestId('peaks-stale').getByRole('button', { name: 'Re-identify' }).click()
  await expect(page.getByTestId('peaks-stale')).toHaveCount(0)
  const after = await api<PeakTable>(request, 'GET', `/api/documents/${docId}/peaks`)
  expect(after.rows.find((r) => r.peak_id === toluene.peak_id)?.name).toBe('Methylbenzene')

  // delete the selected peak
  await panel.getByLabel('Filter by name').fill('methyl')
  await panel.getByRole('row').nth(1).click()
  await page.getByTestId('delete-peak').click()
  await expect(panel).toContainText(`0 of ${after.rows.length - 1} peaks`)
})

test('region spectrum and set as background', async ({ page, request }) => {
  const { docId, at } = await openMs(page, request)
  const masks = await api<{ masks: { id: string }[] }>(
    request,
    'POST',
    `/api/documents/${docId}/masks`,
    { name: 'toluene' },
  )
  const maskId = masks.masks[0].id
  await api(request, 'POST', `/api/documents/${docId}/masks/${maskId}/operations`, {
    shape: { type: 'rect', rt1: [(8 * 4) / 60, (13 * 4) / 60], rt2: [0.6, 1.4] },
  })
  await tab(page, 'Masks')
  await page.getByTestId('mask-spectrum').click()
  await expect(page.getByTestId('spectrum-header')).toContainText('Mask toluene · 80 pixels')
  await tab(page, 'Hits')
  await expect(page.getByTestId('hits-panel').getByRole('row').nth(1)).toContainText('Toluene')
  await expect(page.getByTestId('selection-marker')).toHaveCount(0)

  // a background point for the spectra (BASE-2 reference)
  await page.mouse.click(...at((30.5 * 4) / 60, 3.0))
  await expect(page.getByTestId('spectrum-header')).toContainText('snapped')
  const background = await api<Selection>(request, 'GET', `/api/documents/${docId}/selection`)
  await page.getByRole('button', { name: 'Spectrum menu' }).click()
  await page.getByRole('menuitem', { name: 'Set as background' }).click()
  await expect(page.getByTestId('spectrum-header')).toContainText(
    `reference(scan=${background.scan}, n_scans=3)`,
  )
  await page.locator('.dv-tab', { hasText: 'Processing' }).click()
  await expect(page.locator('#p-ref')).toHaveValue(String(background.scan))
  await expect(page.getByTestId('processing-panel')).toContainText('Image baseline')
})

test('spectrum and head-to-tail figures (VIEW-12)', async ({ page }) => {
  const { at } = await openMs(page, page.request)
  await page.mouse.click(...at(...TOLUENE))
  await expect(page.getByTestId('match-header')).toContainText('Toluene')

  await page.getByRole('button', { name: 'Spectrum menu' }).click()
  await page.getByRole('menuitem', { name: 'Save figure' }).click()
  const svg = await download(page, () => page.getByRole('menuitem', { name: /^SVG/ }).click())
  expect(svg.name).toMatch(/^run_scan\d+\.svg$/)
  expect(svg.data.toString()).toContain('m/z')
  expect(svg.data.toString()).not.toContain('class=')

  await page.getByRole('button', { name: 'Save match figure' }).click()
  const png = await download(page, () => page.getByRole('menuitem', { name: /PNG/ }).click())
  expect(png.name).toMatch(/^run_scan\d+_Toluene\.png$/)
  const box = (await page.getByTestId('head-to-tail').boundingBox())!
  expect(pngSize(png.data)).toEqual([Math.floor(box.width) * 2, Math.floor(box.height) * 2])
})
