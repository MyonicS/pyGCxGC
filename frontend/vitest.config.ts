import { fileURLToPath, URL } from 'node:url'

import { playwright } from '@vitest/browser-playwright'
import { defineConfig } from 'vitest/config'

// CHROMIUM_PATH selects a system Chromium instead of Playwright's download.
// SwiftShader gives WebGL2 without a GPU (CI).
const launchOptions = {
  executablePath: process.env.CHROMIUM_PATH || undefined,
  args: ['--enable-unsafe-swiftshader', '--use-angle=swiftshader', '--ignore-gpu-blocklist'],
}

export default defineConfig({
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  test: {
    projects: [
      {
        extends: true,
        test: {
          name: 'unit',
          include: ['src/**/*.test.ts'],
          exclude: ['src/**/*.browser.test.ts'],
        },
      },
      {
        extends: true,
        test: {
          name: 'browser',
          include: ['src/**/*.browser.test.ts'],
          browser: {
            enabled: true,
            headless: true,
            provider: playwright({ launchOptions }),
            instances: [{ browser: 'chromium' }],
          },
        },
      },
    ],
  },
})
