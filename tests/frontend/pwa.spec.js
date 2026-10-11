// The app installs: a service worker controls the whole site, the shell
// opens with the network gone, and the library itself is never answered
// from a cache. 127.0.0.1 counts as a secure origin, as the tailnet name
// does over TLS — so this is the same path the iPad takes.
import { test, expect } from '@playwright/test'

test.use({ serviceWorkers: 'allow' })

async function installed (page) {
  await page.goto('/')
  const scope = await page.evaluate(async () =>
    (await navigator.serviceWorker.ready).scope)
  // A worker controls pages loaded after it activated, not the one
  // that registered it.
  await page.reload()
  await expect.poll(() => page.evaluate(() =>
    Boolean(navigator.serviceWorker.controller))).toBe(true)
  return scope
}

test('a service worker controls the whole site', async ({ page, baseURL }) => {
  expect(await installed(page)).toBe(new URL('/', baseURL).href)
})

test('the shell is kept for when the network is gone', async ({ page }) => {
  await installed(page)
  const kept = await page.evaluate(async () => {
    const cache = await caches.open('books-shell-v1')
    return (await cache.keys()).map((r) => new URL(r.url).pathname)
  })
  expect(kept).toEqual(expect.arrayContaining(['/', '/static/app.js', '/static/app.css']))
  expect(kept.filter((path) => path.startsWith('/api/'))).toEqual([])
})

test('the app opens with the network gone', async ({ page, context, browserName }) => {
  // Playwright's offline emulation in WebKit refuses a request before
  // the worker is asked, so there it fails with the shell sitting in the
  // cache — the test above is what WebKit can honestly be asked.
  test.skip(browserName === 'webkit', 'WebKit offline emulation bypasses the worker')
  await installed(page)
  await context.setOffline(true)
  try {
    await page.reload()
    await expect(page.locator('header .logo')).toBeVisible()
    await expect(page.locator('#search-input')).toBeVisible()
  } finally {
    await context.setOffline(false)
  }
})

test('the library is never answered from a cache', async ({ page, context }) => {
  await installed(page)
  // Asked once online, so a worker that cached it would have it to give.
  expect(await page.evaluate(async () =>
    (await fetch('/api/status')).status)).toBe(200)
  await context.setOffline(true)
  try {
    const answered = await page.evaluate(() =>
      fetch('/api/status').then(() => true, () => false))
    expect(answered).toBe(false)
  } finally {
    await context.setOffline(false)
  }
})
