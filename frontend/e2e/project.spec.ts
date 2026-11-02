/**
 * End-to-end tests of milestone 4 (spec §9, §10): saving and opening
 * projects, changes made from Python (another client of the session) showing
 * in the app, and the compact layout of the notebook view.
 */

import { existsSync, readFileSync, rmSync } from 'node:fs'
import { resolve } from 'node:path'

import { expect, test } from '@playwright/test'

import {
  addLibrary,
  api,
  DATA,
  FID,
  imageShown,
  MS,
  openApp,
  openDocument,
  resetSession,
} from './helpers'

const PROJECT = resolve(DATA, 'e2e-project.gcxgc')
const TOLUENE_SCAN = { rt2_index: 20, mod_index: 10 }

const tab = (page: import('@playwright/test').Page, name: string) =>
  page.locator('.dv-tab', { hasText: name }).click()

test.beforeEach(async ({ request }) => {
  await resetSession(request)
  rmSync(PROJECT, { force: true })
})

test('save a project and open it again', async ({ page, request }) => {
  const docId = await openDocument(request, MS, { modulation_time: 4 })
  await addLibrary(request)
  const created = await api<{ masks: { id: string }[] }>(
    request,
    'POST',
    `/api/documents/${docId}/masks`,
    {
      name: 'Region',
    },
  )
  await api(
    request,
    'POST',
    `/api/documents/${docId}/masks/${created.masks.at(-1)!.id}/operations`,
    {
      shape: { type: 'rect', rt1: [0.5, 0.9], rt2: [0.5, 1.5] },
    },
  )
  await api(request, 'PUT', `/api/documents/${docId}/selection`, TOLUENE_SCAN)
  await api(request, 'POST', '/api/search', { document_id: docId })
  await openApp(page)
  await imageShown(page)
  await expect(page.getByTestId('hits-panel').getByRole('row').nth(1)).toContainText('Toluene')

  // a display change is part of the project
  await page.getByTestId('display-button').click()
  await page.getByTestId('display-popover').getByRole('button', { name: 'magma' }).click()
  await page.keyboard.press('Escape')

  // Ctrl+S asks for a file name the first time
  await page.keyboard.press('Control+s')
  const dialog = page.getByTestId('path-dialog')
  const folder = dialog.getByRole('textbox', { name: 'Folder', exact: true })
  await folder.fill(DATA)
  await folder.press('Enter')
  await dialog.getByTestId('path-name').fill('e2e-project.gcxgc')
  await page.getByTestId('path-confirm').click()
  await expect(page.getByText('Saved e2e-project.gcxgc')).toBeVisible()
  await expect(page.getByTestId('project-name')).toHaveText('e2e-project')
  expect(existsSync(PROJECT)).toBeTruthy()
  const saved = JSON.parse(readFileSync(PROJECT, 'utf-8'))
  expect(saved.documents[0].path).toBe('run.cdf')
  expect(saved.documents[0].view.display.colormap).toBe('magma')

  // close everything, then open the project from the recent files
  await page.getByRole('button', { name: 'Menu', exact: true }).click()
  await page.getByRole('menuitem', { name: 'Close all' }).click()
  await expect(page.getByTestId('welcome')).toBeVisible()
  await expect(page.getByTestId('project-name')).toHaveCount(0)
  await page
    .getByTestId('welcome')
    .getByRole('button', { name: /e2e-project\.gcxgc/ })
    .click()

  await imageShown(page)
  await expect(page.getByTestId('project-name')).toHaveText('e2e-project')
  await expect(page.getByRole('tab', { name: /run\.cdf/ })).toHaveAttribute('aria-selected', 'true')
  await expect(page.getByTestId('hits-panel').getByRole('row').nth(1)).toContainText('Toluene')
  await tab(page, 'Masks')
  await expect(page.getByTestId('masks-panel')).toContainText('Region')
  await page.getByTestId('display-button').click()
  await expect(
    page.getByTestId('display-popover').getByRole('button', { name: 'magma' }),
  ).toHaveAttribute('aria-pressed', 'true')
  await page.keyboard.press('Escape')

  // opening it again while documents are open asks first (non-blocking)
  await page.getByRole('button', { name: 'Recent files' }).click()
  await page.getByRole('menuitem', { name: /e2e-project\.gcxgc/ }).click()
  await expect(page.getByText('Open e2e-project.gcxgc?')).toBeVisible()
  await page
    .locator('[data-sonner-toast]')
    .getByRole('button', { name: 'Open', exact: true })
    .click()
  await expect(page.getByRole('tab', { name: /run\.cdf/ })).toHaveCount(1)
})

test('changes made from Python show in the app', async ({ page, request }) => {
  const fid = await openDocument(request, FID, { modulation_time: 20 })
  const ms = await openDocument(request, MS, { modulation_time: 4 })
  await openApp(page)
  await imageShown(page)
  const fidTab = page.getByRole('tab', { name: /Example_FID\.csv/ })
  const msTab = page.getByRole('tab', { name: /run\.cdf/ })

  // the app reports the tab it shows; another client can switch it
  await msTab.click()
  await expect
    .poll(
      async () =>
        (await api<{ active_document_id: string }>(request, 'GET', '/api/session'))
          .active_document_id,
    )
    .toBe(ms)
  await api(request, 'PUT', '/api/session/active', { document_id: fid })
  await expect(fidTab).toHaveAttribute('aria-selected', 'true')

  // a mask added from Python appears with its integration
  const created = await api<{ masks: { id: string }[] }>(
    request,
    'POST',
    `/api/documents/${fid}/masks`,
    {
      name: 'From Python',
    },
  )
  await api(request, 'POST', `/api/documents/${fid}/masks/${created.masks.at(-1)!.id}/operations`, {
    shape: { type: 'rect', rt1: [10, 20], rt2: [1, 3] },
  })
  await expect(page.getByTestId('masks-panel')).toContainText('From Python')
  await expect(page.getByTestId('integration-panel')).toContainText('From Python')

  // a point selected from Python shows its spectrum
  await api(request, 'PUT', '/api/session/active', { document_id: ms })
  await expect(msTab).toHaveAttribute('aria-selected', 'true')
  await api(request, 'PUT', `/api/documents/${ms}/selection`, TOLUENE_SCAN)
  await expect(page.getByTestId('spectrum-header')).toContainText('scan ')
})

test('compact layout for the notebook view', async ({ page, request }) => {
  await openDocument(request, MS, { modulation_time: 4 })
  await page.setViewportSize({ width: 1000, height: 640 })
  await page.goto('/?token=e2e&layout=compact')
  await imageShown(page)
  for (const title of ['Chromatogram', 'Spectrum', 'Match', 'Hits', 'Peaks', 'Processing'])
    await expect(page.locator('.dv-tab', { hasText: title })).toHaveCount(1)
  // Spectrum and Match share a group in the compact layout
  const spectrumGroup = page.locator('.dv-groupview', {
    has: page.locator('.dv-tab', { hasText: 'Spectrum' }),
  })
  await expect(spectrumGroup.locator('.dv-tab', { hasText: 'Match' })).toHaveCount(1)
  // the compact layout is saved apart from the normal one
  await page.locator('.dv-tab', { hasText: 'Peaks' }).click()
  await page.waitForTimeout(1200)
  const settings = await api<{ ui: { layouts: Record<string, string | null> } }>(
    request,
    'GET',
    '/api/settings',
  )
  expect(settings.ui.layouts['compact-ms']).toBeTruthy()
  expect(settings.ui.layouts.ms).toBeFalsy()
})
