/**
 * End-to-end tests of milestone 1 (spec §14): shell, chromatogram view and
 * FID processing, against e2e/backend.py. Each test starts from an empty
 * session; screenshots cover the FID and MS layouts in both themes (THEME-5).
 */

import { resolve } from 'node:path'

import { expect, test } from '@playwright/test'

import { api, FID, headers, imageShown, MS, openApp, openDocument, resetSession } from './helpers'

test.beforeEach(async ({ request }) => {
  await resetSession(request)
})

test('opens the example FID through the file browser', async ({ page }) => {
  await openApp(page)
  await expect(page.getByTestId('welcome')).toBeVisible()
  await page.getByTestId('welcome-open').click()
  const folder = page.getByRole('textbox', { name: 'Folder', exact: true })
  await folder.fill(resolve('../example_data/example_chromatograms'))
  await folder.press('Enter')
  await page.getByRole('option', { name: /Example_FID\.csv/ }).click()
  // FID files need a modulation time
  await expect(page.getByTestId('open-confirm')).toBeDisabled()
  await page.locator('#open-mod').fill('20')
  await page.getByTestId('open-confirm').click()

  await imageShown(page)
  await expect(page.getByTestId('status-bar')).toContainText('FID · 250 Hz · 317 × 5000')
  await expect(page.getByRole('tab', { name: /Example_FID\.csv/ })).toHaveAttribute(
    'aria-selected',
    'true',
  )
  // default FID layout
  for (const title of ['Chromatogram', 'Masks', 'Processing', 'Integration']) {
    await expect(page.locator('.dv-tab', { hasText: title })).toBeVisible()
  }
  await expect(page).toHaveScreenshot('fid.png')

  // cursor readout in the status bar and the tooltip
  const box = (await page.getByTestId('chromatogram-canvas').boundingBox())!
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2)
  const readout = page.getByTestId('cursor-readout')
  await expect(readout).toContainText('RT1')
  await expect(readout).toContainText('px 2500')
  await expect(page.getByTestId('cursor-tooltip')).toBeVisible()

  // the modulation time is remembered for the next FID files
  const settings = await api<{ fid: { modulation_time: number } }>(
    page.request,
    'GET',
    '/api/settings',
  )
  expect(settings.fid.modulation_time).toBe(20)
})

test('display changes never call the backend', async ({ page, request }) => {
  await openDocument(request, FID, { modulation_time: 20 })
  await openApp(page)
  await imageShown(page)
  const calls: string[] = []
  page.on('request', (r) => {
    if (r.url().includes('/api/')) calls.push(`${r.method()} ${r.url()}`)
  })
  await page.getByTestId('display-button').click()
  const popover = page.getByTestId('display-popover')
  await popover.getByRole('button', { name: 'magma' }).click()
  await popover.getByRole('radio', { name: 'Log' }).click()
  await popover.getByRole('radio', { name: 'Bicubic' }).click()
  await popover.getByLabel('vmax').fill('2e-5')
  await popover.getByLabel('vmax').press('Enter')
  await popover.getByTestId('auto-contrast').click()
  await page.keyboard.press('Escape')
  // zoom and pan
  const box = (await page.getByTestId('chromatogram-canvas').boundingBox())!
  await page.mouse.move(box.x + box.width * 0.3, box.y + box.height * 0.6)
  for (let i = 0; i < 5; i++) await page.mouse.wheel(0, -120)
  await page.waitForTimeout(1000)
  // only the view state is copied to the backend (debounced, for projects)
  const views = calls.filter((c) => c.startsWith('PUT') && c.endsWith('/view'))
  expect(calls.filter((c) => !views.includes(c))).toEqual([])
  expect(views.length).toBeLessThanOrEqual(2)
})

test('zoom box, axis zoom and reset', async ({ page, request }) => {
  await openDocument(request, FID, { modulation_time: 20 })
  await openApp(page)
  await imageShown(page)
  const box = (await page.getByTestId('chromatogram-canvas').boundingBox())!
  const ticks = (axis: 'x' | 'y') =>
    page.getByTestId(`${axis}-ticks`).locator('text').allTextContents()
  const x0 = await ticks('x')
  const y0 = await ticks('y')

  await page.keyboard.press('z')
  await expect(page.getByTestId('tool-zoom')).toHaveAttribute('aria-checked', 'true')
  await page.mouse.move(box.x + box.width * 0.1, box.y + box.height * 0.3)
  await page.mouse.down()
  await page.mouse.move(box.x + box.width * 0.3, box.y + box.height * 0.6, { steps: 5 })
  await page.mouse.up()
  await expect.poll(() => ticks('x')).not.toEqual(x0)
  await expect.poll(() => ticks('y')).not.toEqual(y0)

  await page.keyboard.press('0')
  await expect.poll(() => ticks('x')).toEqual(x0)
  await expect.poll(() => ticks('y')).toEqual(y0)

  // the wheel over the RT1 axis zooms RT1 only
  await page.mouse.move(box.x + box.width / 2, box.y + box.height + 15)
  for (let i = 0; i < 4; i++) await page.mouse.wheel(0, -120)
  await expect.poll(() => ticks('x')).not.toEqual(x0)
  expect(await ticks('y')).toEqual(y0)
})

test('MS document: layout, click selects and snaps to the apex', async ({ page, request }) => {
  const docId = await openDocument(request, MS, { modulation_time: 4 })
  await openApp(page)
  await imageShown(page)
  await expect(page.getByTestId('status-bar')).toContainText('20 Hz · m/z 45–200 · 40 × 80')
  for (const title of ['Spectrum', 'Processing', 'Match', 'Hits', 'Peaks', 'Libraries']) {
    await expect(page.locator('.dv-tab', { hasText: title })).toBeVisible()
  }
  await page.mouse.move(0, 0)
  await expect(page).toHaveScreenshot('ms.png')

  // toluene: modulation 10, RT2 1.0 s; click two scans below the apex
  const box = (await page.getByTestId('chromatogram-canvas').boundingBox())!
  const x = box.x + ((10 + 0.5) / 40) * box.width
  const y = box.y + box.height * (1 - (0.9 + 0.025) / 4)
  await page.mouse.click(x, y)
  await expect(page.getByTestId('selection-marker')).toBeVisible()
  const selection = await api<{ scan: number; mod_index: number }>(
    request,
    'GET',
    `/api/documents/${docId}/selection`,
  )
  expect(selection.mod_index).toBe(10)
  expect(selection.scan).toBe(10 * 80 + 20)
  await page.mouse.move(x, y)
  await expect(page.getByTestId('cursor-readout')).toContainText('scan')
})

test('phase-shift preview, applied on release (FID-5)', async ({ page, request }) => {
  const docId = await openDocument(request, FID, { modulation_time: 20 })
  await openApp(page)
  await imageShown(page)
  await page.locator('.dv-tab', { hasText: 'Processing' }).click()
  const thumb = page.getByRole('slider', { name: 'Phase shift preview' })
  const t = (await thumb.boundingBox())!
  const patch = page.waitForRequest((r) => r.method() === 'PATCH')
  await page.mouse.move(t.x + t.width / 2, t.y + t.height / 2)
  await page.mouse.down()
  await page.mouse.move(t.x + t.width / 2 + 40, t.y + t.height / 2, { steps: 6 })
  const preview = await page.locator('#p-shift').inputValue()
  expect(Number(preview)).toBeGreaterThan(0)
  await page.mouse.up()
  expect((await patch).postDataJSON()).toEqual({ shift: Number(preview) })
  await page.waitForResponse((r) => r.url().includes('/image'))
  await expect
    .poll(
      async () =>
        (await api<{ params: { shift: number } }>(request, 'GET', `/api/documents/${docId}`)).params
          .shift,
    )
    .toBe(Number(preview))
  await expect(page.locator('#p-shift')).toHaveValue(preview)
})

test('theme choice persists; layout can be reset', async ({ page, request }) => {
  await openDocument(request, FID, { modulation_time: 20 })
  await openApp(page)
  await imageShown(page)
  await page.getByTestId('theme-menu').click()
  await page.getByRole('menuitemradio', { name: 'Dark' }).click()
  await expect(page.locator('html')).toHaveClass(/dark/)
  const settings = await api<{ theme: string }>(request, 'GET', '/api/settings')
  expect(settings.theme).toBe('dark')

  // close a panel, then restore the default layout from the menu
  const integration = page.locator('.dv-tab', { hasText: 'Integration' })
  await integration.locator('.dv-default-tab-action').click()
  await expect(integration).toHaveCount(0)
  await page.getByRole('button', { name: 'Menu' }).click()
  await page.getByRole('menuitem', { name: 'Reset layout' }).click()
  await expect(integration).toBeVisible()

  // reloading keeps the theme
  await page.reload()
  await expect(page.locator('html')).toHaveClass(/dark/)
})

test('command palette runs commands', async ({ page, request }) => {
  await openDocument(request, FID, { modulation_time: 20 })
  await openApp(page)
  await imageShown(page)
  await page.keyboard.press('Control+k')
  const palette = page.getByRole('dialog', { name: 'Command palette' })
  await expect(palette).toBeVisible()
  await page.keyboard.type('colour map: magma')
  await page.keyboard.press('Enter')
  await expect(palette).toHaveCount(0)
  await page.getByTestId('display-button').click()
  await expect(
    page.getByTestId('display-popover').getByRole('button', { name: 'magma' }),
  ).toHaveAttribute('aria-pressed', 'true')
  await page.keyboard.press('Escape')

  await page.keyboard.press('Control+k')
  await page.keyboard.type('zoom box')
  await page.keyboard.press('Enter')
  await expect(page.getByTestId('tool-zoom')).toHaveAttribute('aria-checked', 'true')
})

test('errors appear as toasts with details', async ({ page }) => {
  await openApp(page)
  await page.getByTestId('welcome-open').click()
  const folder = page.getByRole('textbox', { name: 'Folder', exact: true })
  await folder.fill('/definitely/not/here')
  await folder.press('Enter')
  await expect(page.getByTestId('open-dialog')).toContainText(/not a folder|No such|not found/i)
  await page.keyboard.press('Escape')
  // an error from a job: open a file that is not a chromatogram
  const r = await page.request.fetch('/api/documents', {
    method: 'POST',
    headers,
    data: { path: FID, params: { modulation_time: 20.001 } }, // not a multiple of 4 ms
  })
  expect(r.status()).toBe(202)
  const toast = page.locator('[data-sonner-toast]').filter({ hasText: 'Opening Example_FID.csv' })
  await expect(toast).toBeVisible()
  await toast.getByRole('button', { name: 'Details' }).click()
  await expect(toast.locator('pre')).toContainText('Traceback')
})
