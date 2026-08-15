import { defineConfig, devices } from '@playwright/test'

const PORT = 8771
const BASE = `http://127.0.0.1:${PORT}`

export default defineConfig({
  testDir: './tests/frontend',
  // One server, one database, and reading position is shared across
  // devices BY DESIGN (decision 7) — so parallel tests genuinely race
  // each other over real application state, not over test scaffolding.
  // Serial is deterministic and costs ~14s for the whole suite.
  // Revisit only by giving each worker its own server and database.
  fullyParallel: false,
  workers: 1,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? 'list' : [['list']],
  use: {
    baseURL: BASE,
    trace: 'on-first-retry'
  },
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
    // This is served to Safari on a Mac, an iPhone and an iPad, and
    // foliate-js is developed against WebKitGTK — so WebKit is the
    // engine that matters most here, not the one that was easiest to
    // test first.
    { name: 'webkit', use: { ...devices['Desktop Safari'] } }
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
