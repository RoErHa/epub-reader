import { defineConfig, devices } from '@playwright/test'

const PORT = 8771
const BASE = `http://127.0.0.1:${PORT}`

export default defineConfig({
  testDir: './tests/frontend',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? 'list' : [['list']],
  use: {
    baseURL: BASE,
    trace: 'on-first-retry'
  },
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } }
  ],
  webServer: {
    // Regenerate the fixture library, then serve it.
    command:
      '.venv/bin/python tools/make_test_library.py && ' +
      '.venv/bin/python epub_gateway.py --env .env.test',
    url: `${BASE}/healthz`,
    reuseExistingServer: !process.env.CI,
    stdout: 'pipe',
    stderr: 'pipe'
  }
})
