import { existsSync } from 'node:fs'

import { defineConfig } from '@playwright/test'

// End-to-end tests against the built frontend (`pnpm build` first), served by
// a Python backend with synthetic data (e2e/backend.py).
//   PYTHON         interpreter with pyGCxGC[app] (default: ../.venv/bin/python)
//   CHROMIUM_PATH  a system Chromium instead of Playwright's download
const PORT = 8767
const python =
  process.env.PYTHON ?? (existsSync('../.venv/bin/python') ? '../.venv/bin/python' : 'python')

export default defineConfig({
  testDir: 'e2e',
  outputDir: 'test-results',
  fullyParallel: false,
  workers: 1,
  timeout: 60_000,
  expect: {
    timeout: 15_000,
    toHaveScreenshot: { maxDiffPixelRatio: 0.01, animations: 'disabled', caret: 'hide' },
  },
  reporter: process.env.CI ? 'github' : 'list',
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    viewport: { width: 1440, height: 900 },
    launchOptions: {
      executablePath: process.env.CHROMIUM_PATH || undefined,
      // WebGL2 without a GPU, deterministic for screenshots
      args: ['--enable-unsafe-swiftshader', '--use-angle=swiftshader', '--ignore-gpu-blocklist'],
    },
  },
  projects: [
    { name: 'light', use: { colorScheme: 'light' } },
    { name: 'dark', use: { colorScheme: 'dark' } },
  ],
  webServer: {
    command: `${python} e2e/backend.py --port ${PORT} --data e2e/.data`,
    url: `http://127.0.0.1:${PORT}/`,
    reuseExistingServer: false,
    timeout: 60_000,
    stdout: 'pipe',
  },
})
