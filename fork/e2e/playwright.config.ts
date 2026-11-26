import { existsSync } from 'node:fs'
import { defineConfig } from '@playwright/test'

import { stack } from './lib/stack'

// Evidence (screenshots, traces, JSON responses) goes outside the repository:
// FORK_E2E_OUT=~/scratch/securo-custom/<issue>/e2e npm test
const out = process.env.FORK_E2E_OUT ?? 'results'
// Use the machine's Chromium so no browser download is needed; FORK_E2E_CHROMIUM overrides.
const chromium = process.env.FORK_E2E_CHROMIUM ?? '/usr/bin/chromium'

export default defineConfig({
  testDir: 'scenarios',
  outputDir: `${out}/artifacts`,
  workers: 1,
  timeout: 60_000,
  reporter: [['list'], ['json', { outputFile: `${out}/report.json` }]],
  use: {
    baseURL: stack.frontend,
    viewport: { width: 1440, height: 1100 },
    locale: 'en-US',
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
    launchOptions: existsSync(chromium) ? { executablePath: chromium } : {},
  },
})
