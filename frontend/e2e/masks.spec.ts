/**
 * End-to-end tests of milestone 2 (spec §14): drawing tools, Masks and
 * Integration panels, undo/redo, exports and mask-set reuse. "Done when masks
 * drawn in the app integrate exactly like integrate_masks, and TIFFs from the
 * old mask creator load": both are checked against Python itself.
 */

import { mkdtempSync, readFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

import { expect, test, type Page } from '@playwright/test'

import {
  api,
  DATA,
  FID,
  imageShown,
  MASK_DIR,
  MS,
  MS_OTHER,
  openApp,
  openDocument,
  python,
  resetSession,
  screenPoint,
} from './helpers'

interface Integration {
  masks: { name: string; volume: number; mask_id: string }[]
  unassigned: { volume: number }
}
interface MaskSet {
  masks: {
    id: string
    name: string
    visible: boolean
    operations: { id: string; mode: string; shape: { type: string; rt1?: number[] } }[]
  }[]
}

test.beforeEach(async ({ request }) => {
  await resetSession(request)
})

async function drag(page: Page, from: [number, number], to: [number, number]) {
  await page.mouse.move(...from)
  await page.mouse.down()
  await page.mouse.move(...to, { steps: 6 })
  await page.mouse.up()
}

/** Wait until the masks of a document satisfy `check`. */
async function masksOf(page: Page, docId: string): Promise<MaskSet> {
  return api<MaskSet>(page.request, 'GET', `/api/documents/${docId}/masks`)
}

test('draw rectangle, lasso and polygon; subtract; undo and redo', async ({ page, request }) => {
  const docId = await openDocument(request, FID, { modulation_time: 20 })
  await openApp(page)
  await imageShown(page)
  const at = await screenPoint(page, request, docId)
  const rows = page.getByTestId('mask-row')
  const shapes = page.getByTestId('shape-row')

  // a rectangle creates "Mask 1"
  await page.keyboard.press('r')
  await expect(page.getByTestId('tool-rect')).toHaveAttribute('aria-checked', 'true')
  await drag(page, at(20, 5), at(40, 10))
  await expect(rows).toHaveCount(1)
  await expect(rows.first()).toContainText('Mask 1')
  await expect(shapes).toHaveCount(1)
  await expect(shapes.first()).toContainText('Rectangle')

  // a lasso into a new mask
  await page.getByTestId('mask-new').click()
  await expect(rows).toHaveCount(2)
  await page.keyboard.press('l')
  await page.mouse.move(...at(68, 12))
  await page.mouse.down()
  for (let i = 1; i <= 30; i++) {
    const a = (i / 30) * 2 * Math.PI
    await page.mouse.move(...at(60 + 8 * Math.cos(a), 12 + 3 * Math.sin(a)))
  }
  await page.mouse.up()
  await expect(shapes).toHaveCount(1)
  await expect(shapes.first()).toContainText('Polygon')

  // a polygon subtracted from Mask 1 (Alt held), closed with Enter
  await rows.first().click()
  await page.keyboard.press('p')
  await page.keyboard.down('Alt')
  for (const p of [at(25, 6), at(35, 6), at(30, 9)]) await page.mouse.click(...p)
  await page.keyboard.up('Alt')
  await page.keyboard.press('Enter')
  await expect(shapes).toHaveCount(2)
  await expect(shapes.nth(1).getByLabel('subtracts')).toBeVisible()

  const set = await masksOf(page, docId)
  expect(set.masks.map((m) => m.operations.map((o) => `${o.mode} ${o.shape.type}`))).toEqual([
    ['add rect', 'subtract polygon'],
    ['add polygon'],
  ])

  // undo and redo (spec MASK-5)
  await page.keyboard.press('Control+z')
  await expect(shapes).toHaveCount(1)
  await page.keyboard.press('Control+Shift+z')
  await expect(shapes).toHaveCount(2)
  await page.getByTestId('mask-undo').click()
  await expect(shapes).toHaveCount(1)
  await page.getByTestId('mask-redo').click()
  await expect(shapes).toHaveCount(2)
  // the Integration panel follows
  await expect(page.getByTestId('integration-grid')).toContainText('Mask 2')
  await expect(page.getByTestId('integration-grid')).toContainText('Unassigned')
})

test('drawn masks integrate exactly like integrate_masks', async ({ page, request }) => {
  const docId = await openDocument(request, FID, { modulation_time: 20 })
  await openApp(page)
  await imageShown(page)
  const at = await screenPoint(page, request, docId)
  await page.keyboard.press('r')
  await drag(page, at(15, 4), at(45, 9))
  await page.getByTestId('mask-new').click()
  await page.keyboard.press('p')
  for (const p of [at(50, 6), at(80, 7), at(75, 12), at(52, 10)]) await page.mouse.click(...p)
  await page.keyboard.press('Enter')
  await expect(page.getByTestId('shape-row')).toHaveCount(1)

  // export the TIFFs from the app and integrate them with the old API
  await page.getByTestId('mask-export').click()
  const download = page.waitForEvent('download')
  await page.getByRole('menuitem', { name: /Masks as TIFF/ }).click()
  const dir = mkdtempSync(join(tmpdir(), 'pygcxgc-tiffs-'))
  const zip = join(dir, (await download).suggestedFilename())
  await (await download).saveAs(zip)
  const expected = JSON.parse(
    python(
      `import json, zipfile, pyGCxGC as gcgc
zipfile.ZipFile(${JSON.stringify(zip)}).extractall(${JSON.stringify(join(dir, 'masks'))})
chrom = gcgc.parse_2D_chromatogram(${JSON.stringify(FID)}, modulation_time=20)
print(json.dumps(gcgc.integrate_masks(chrom.chrom_2D, ${JSON.stringify(join(dir, 'masks'))})))`,
    ),
  ) as Record<string, number>
  const result = await api<Integration>(request, 'GET', `/api/documents/${docId}/integration`)
  expect(Object.fromEntries(result.masks.map((m) => [m.name, m.volume]))).toEqual(expected)
  expect(Object.keys(expected).sort()).toEqual(['Mask 1', 'Mask 2'])
})

test('TIFFs from the old mask creator load', async ({ page, request }) => {
  const docId = await openDocument(request, FID, { modulation_time: 20 })
  await openApp(page)
  await imageShown(page)
  await page.getByTestId('mask-import').click()
  await page.getByRole('menuitem', { name: 'Folder of TIFFs…' }).click()
  const dialog = page.getByTestId('path-dialog')
  const folder = dialog.getByRole('textbox', { name: 'Folder', exact: true })
  await folder.fill(join(MASK_DIR, '..'))
  await folder.press('Enter')
  await dialog.getByRole('option', { name: 'example_masks' }).click()
  await page.getByTestId('path-confirm').click()

  const names = page.getByTestId('mask-name')
  await expect(names).toHaveText(['Mask_1', 'Mask_2'])
  const expected = JSON.parse(readFileSync(join(DATA, 'expected.json'), 'utf-8'))
  const result = await api<Integration>(request, 'GET', `/api/documents/${docId}/integration`)
  expect(Object.fromEntries(result.masks.map((m) => [m.name, m.volume]))).toEqual(expected)
  // overlapping masks are allowed and reported (spec MASK-7)
  await expect(page.getByTestId('overlap-warning')).toContainText(
    'Mask_1 and Mask_2 share 625 pixels',
  )
  // raster masks have no handles but are listed
  await expect(page.getByTestId('shape-row').first()).toContainText('Raster')
  await page.mouse.move(0, 0)
  await page.waitForTimeout(500) // mask overlay rasters
  await expect(page).toHaveScreenshot('fid-masks.png')
})

test('edit a shape: select, drag a handle, delete', async ({ page, request }) => {
  const docId = await openDocument(request, FID, { modulation_time: 20 })
  await openApp(page)
  await imageShown(page)
  const at = await screenPoint(page, request, docId)
  await page.keyboard.press('r')
  await drag(page, at(20, 5), at(40, 10))
  await expect(page.getByTestId('shape-row')).toHaveCount(1)
  const before = (await masksOf(page, docId)).masks[0].operations[0].shape.rt1!

  await page.keyboard.press('v')
  await page.mouse.click(...at(30, 7))
  await expect(page.getByTestId('selected-shape')).toBeVisible()
  const handles = page.getByTestId('shape-handle')
  await expect(handles).toHaveCount(4)
  // corner 2 is (RT1 max, RT2 max)
  const h = (await handles.nth(2).boundingBox())!
  const edited = page.waitForResponse((r) => r.request().method() === 'PUT')
  await drag(page, [h.x + h.width / 2, h.y + h.height / 2], at(50, 12))
  await edited
  const after = (await masksOf(page, docId)).masks[0].operations[0].shape.rt1!
  expect(after[0]).toBeCloseTo(before[0], 6)
  expect(after[1]).toBeGreaterThan(before[1] + 5)

  await page.keyboard.press('Delete')
  await expect(page.getByTestId('shape-row')).toHaveCount(0)
  expect((await masksOf(page, docId)).masks[0].operations).toEqual([])
})

test('reuse masks on another document; rename and hide', async ({ page, request }) => {
  const fid = await openDocument(request, FID, { modulation_time: 20 })
  const created = await api<MaskSet>(request, 'POST', `/api/documents/${fid}/masks`, {
    name: 'Region',
  })
  await api(request, 'POST', `/api/documents/${fid}/masks/${created.masks[0].id}/operations`, {
    shape: { type: 'rect', rt1: [0.5, 1.5], rt2: [0.5, 2.5] },
  })
  const ms = await openDocument(request, MS, { modulation_time: 4 })
  await openApp(page)
  await page.getByRole('tab', { name: /run\.cdf/ }).click()
  await imageShown(page)
  await page.locator('.dv-tab', { hasText: 'Masks' }).click()
  await page.getByTestId('mask-import').click()
  await page.getByRole('menuitem', { name: 'From Example_FID.csv' }).click()
  await expect(page.getByTestId('mask-name')).toHaveText(['Region'])
  // integrated on the TIC of the MS run (spec MASK-8)
  const result = await api<Integration>(request, 'GET', `/api/documents/${ms}/integration`)
  expect(result.masks[0].volume).toBeGreaterThan(0)

  await page.getByTestId('mask-name').dblclick()
  await page.getByLabel('Mask name').fill('Region A')
  await page.getByLabel('Mask name').press('Enter')
  await expect(page.getByTestId('mask-name')).toHaveText(['Region A'])
  await page.getByRole('button', { name: 'Hide Region A' }).click()
  await expect(page.getByRole('button', { name: 'Show Region A' })).toBeVisible()
  const set = await masksOf(page, ms)
  expect(set.masks.map((m) => [m.name, m.visible])).toEqual([['Region A', false]])
  // the FID document keeps its own masks
  expect((await masksOf(page, fid)).masks.map((m) => m.name)).toEqual(['Region'])
})

test('Copy as Python reproduces the integration', async ({ page, request, context }) => {
  await context.grantPermissions(['clipboard-read', 'clipboard-write'])
  const docId = await openDocument(request, FID, { modulation_time: 20, shift: 5 })
  const created = await api<MaskSet>(request, 'POST', `/api/documents/${docId}/masks`, {})
  await api(request, 'POST', `/api/documents/${docId}/masks/${created.masks[0].id}/operations`, {
    shape: {
      type: 'polygon',
      vertices: [
        [20, 5],
        [60, 6],
        [40, 12],
      ],
    },
  })
  await openApp(page)
  await imageShown(page)
  await page.getByTestId('copy-python').click()
  await expect(page.getByText('Python code copied')).toBeVisible()
  const code = await page.evaluate(() => navigator.clipboard.readText())
  expect(code).toContain('shift=5,')

  await page.getByTestId('mask-export').click()
  const download = page.waitForEvent('download')
  await page.getByRole('menuitem', { name: /Mask set/ }).click()
  const dir = mkdtempSync(join(tmpdir(), 'pygcxgc-python-'))
  await (await download).saveAs(join(dir, (await download).suggestedFilename()))
  const volumes = JSON.parse(python(`${code}\nimport json\nprint(json.dumps(volumes))`, dir))
  const result = await api<Integration>(request, 'GET', `/api/documents/${docId}/integration`)
  expect(volumes).toEqual(Object.fromEntries(result.masks.map((m) => [m.name, m.volume])))
})

test('batch integration of several files (INT-4)', async ({ page, request }) => {
  const docId = await openDocument(request, MS, { modulation_time: 4 })
  const created = await api<MaskSet>(request, 'POST', `/api/documents/${docId}/masks`, {
    name: 'Box',
  })
  await api(request, 'POST', `/api/documents/${docId}/masks/${created.masks[0].id}/operations`, {
    shape: { type: 'rect', rt1: [0.3, 1.5], rt2: [0.5, 2.5] },
  })
  await openApp(page)
  await imageShown(page)
  await page.locator('.dv-tab', { hasText: 'Integration' }).click()
  await page.getByTestId('batch').click()
  const dialog = page.getByTestId('path-dialog')
  const folder = dialog.getByRole('textbox', { name: 'Folder', exact: true })
  await folder.fill(DATA)
  await folder.press('Enter')
  await dialog.getByRole('option', { name: /^run\.cdf/ }).click()
  await dialog.getByRole('option', { name: /^other\.cdf/ }).click()
  await expect(dialog).toContainText('2 files')
  const download = page.waitForEvent('download')
  await page.getByTestId('path-confirm').click()
  const file = join(mkdtempSync(join(tmpdir(), 'pygcxgc-batch-')), 'batch.csv')
  await (await download).saveAs(file)
  const lines = readFileSync(file, 'utf-8').trim().split(/\r?\n/)
  const header = lines[0].split(',')
  const rows = lines
    .slice(1)
    .map((l) => Object.fromEntries(l.split(',').map((v, i) => [header[i], v])))
  expect(rows.map((r) => r.file)).toEqual([MS, MS_OTHER])
  const result = await api<Integration>(request, 'GET', `/api/documents/${docId}/integration`)
  expect(Number(rows[0].Box)).toBe(result.masks[0].volume)
  expect(Number(rows[1].Box)).toBeGreaterThan(0)
  await expect(page.getByText('2 files integrated')).toBeVisible()
})
