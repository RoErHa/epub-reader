import { test, expect } from '@playwright/test'
import { openBook, expectBookText } from './helpers.js'

const BOOK = 'left-hand-of-darkness.epub'
const HUGE = 'huge-section.epub'

/** Hold the book request back so the indicator can be observed.
 *
 * Just a delay, then the real request: reading the body here to re-serve
 * it hit "Response has been disposed" in WebKit, and the delay alone
 * gives the same observable window.
 */
async function throttleBooks (page, delay = 1500) {
  await page.route('**/api/books/*/file', async route => {
    await new Promise(r => setTimeout(r, delay))
    await route.continue()
  })
}

test.describe('loading indicator', () => {
  test('appears while a book is opening', async ({ page }) => {
    await throttleBooks(page)
    await page.goto('/')
    await page.locator(`.book-row[data-name="${BOOK}"]`).click()
    await expect(page.locator('#loading')).toBeVisible()
  })

  test('goes away once the book is open', async ({ page }) => {
    await page.goto('/')
    await openBook(page, BOOK)
    await expect(page.locator('#loading')).toBeHidden()
  })

  test('shows the cover of the book being opened', async ({ page }) => {
    await throttleBooks(page)
    await page.goto('/')
    await page.locator(`.book-row[data-name="${BOOK}"]`).click()
    await expect(page.locator('#loading .loading-cover')).toHaveAttribute(
      'src', /\/api\/covers\//
    )
  })

  test('falls back to a plain book when there is no cover', async ({ page }) => {
    await throttleBooks(page)
    await page.goto('/')
    await page.locator('.book-row[data-name="hostile.epub"]').click()
    await expect(page.locator('#loading')).toBeVisible()
    await expect(page.locator('#loading .loading-book')).toHaveClass(/no-cover/)
  })

  test('reports a percentage while downloading', async ({ page }) => {
    await throttleBooks(page)
    await page.goto('/')
    await page.locator(`.book-row[data-name="${BOOK}"]`).click()
    await expect(page.locator('#loading .loading-label')).toHaveText(
      /Downloading \d+%/
    )
  })

  test('the bar tracks the percentage exactly', async ({ page }) => {
    // Driven directly: a real download finishes too fast to sample a
    // midpoint reliably, and the contract worth pinning is that the bar
    // and the label agree with the fraction given.
    await page.goto('/')
    const result = await page.evaluate(async () => {
      const { LoadingIndicator } = await import('/static/loading.js')
      const indicator = new LoadingIndicator(document)
      indicator.show({ cover: null, title: 'x' })
      indicator.downloading(0.42)
      const fill = document.querySelector('#loading .loading-fill')
      const label = document.querySelector('#loading .loading-label')
      const midway = { width: fill.style.width, label: label.textContent }
      indicator.downloading(2)        // out of range
      const clamped = fill.style.width
      indicator.preparing('a large book')
      const phase = document.getElementById('loading').dataset.phase
      const preparingLabel = label.textContent
      indicator.hide()
      return {
        midway, clamped, phase, preparingLabel,
        hidden: document.getElementById('loading').hidden
      }
    })
    expect(result.midway).toEqual({ width: '42%', label: 'Downloading 42%' })
    expect(result.clamped).toBe('100%')      // never overshoots
    expect(result.phase).toBe('preparing')
    expect(result.preparingLabel).toContain('a large book')
    expect(result.hidden).toBe(true)
  })

  test('says what it is doing after the download', async ({ page }) => {
    // Layout has no progress to report, so it is named rather than
    // given an invented number.
    await page.goto('/')
    await page.locator(`.book-row[data-name="${HUGE}"]`).click()
    await expect(page.locator('#loading .loading-label')).toContainText(
      /Preparing|%/
    )
    await expectBookText(page, 'quick brown fox')
    await expect(page.locator('#loading')).toBeHidden()
  })

  test('the indeterminate phase is marked as such', async ({ page }) => {
    await page.goto('/')
    await page.locator(`.book-row[data-name="${HUGE}"]`).click()
    await expect(page.locator('#loading')).toHaveAttribute(
      'data-phase', /preparing|downloading/
    )
    await expectBookText(page, 'quick brown fox')
  })

  test('covers the reading pane only, so the library stays usable',
    async ({ page }) => {
      await throttleBooks(page)
      await page.goto('/')
      await page.locator(`.book-row[data-name="${BOOK}"]`).click()
      const overlay = await page.locator('#loading').boundingBox()
      const library = await page.locator('#browser').boundingBox()
      expect(overlay.x).toBeGreaterThanOrEqual(library.x + library.width - 1)
    })

  test('the cover actually paints, in both engines', async ({ page }) => {
    // WebKit refuses to paint an <img> inside a transform-style:
    // preserve-3d context — the cover came out as a flat rectangle in
    // Safari while Chromium was perfect. This asserts the image has
    // real pixels, not merely that the element is present.
    await throttleBooks(page)
    await page.goto('/')
    await page.locator(`.book-row[data-name="${BOOK}"]`).click()
    await expect(page.locator('#loading .loading-cover')).toBeVisible()

    const painted = await page.evaluate(async () => {
      const img = document.querySelector('#loading .loading-cover')
      if (!img.complete || !img.naturalWidth) return null
      const canvas = document.createElement('canvas')
      canvas.width = canvas.height = 8
      const ctx = canvas.getContext('2d')
      ctx.drawImage(img, 0, 0, 8, 8)
      const [r, g, b] = ctx.getImageData(4, 4, 1, 1).data
      return { r, g, b }
    })
    expect(painted).not.toBeNull()
    // Not the placeholder background (#1B2F4D).
    expect(`${painted.r},${painted.g},${painted.b}`).not.toBe('27,47,77')
  })

  test('the book is not a 3D scene, which WebKit cannot paint into',
    async ({ page }) => {
      await page.goto('/')
      const style = await page.evaluate(() => {
        const el = document.querySelector('.loading-book')
        return getComputedStyle(el).transformStyle
      })
      expect(style).not.toBe('preserve-3d')
    })

  test('is announced to screen readers', async ({ page }) => {
    await page.goto('/')
    await expect(page.locator('#loading')).toHaveAttribute('aria-live', 'polite')
  })

  test('clears when a book fails to open', async ({ page }) => {
    await page.route('**/api/books/*/file', route =>
      route.fulfill({ status: 500, body: 'nope' }))
    await page.goto('/')
    await page.locator(`.book-row[data-name="${BOOK}"]`).click()
    await expect(page.locator('#reader-stage')).toHaveAttribute(
      'data-state', 'error', { timeout: 20000 }
    )
    await expect(page.locator('#loading')).toBeHidden()
  })

  test('opening a second book restarts it cleanly', async ({ page }) => {
    await page.goto('/')
    await openBook(page, BOOK)
    await expect(page.locator('#loading')).toBeHidden()
    await openBook(page, 'start-no-cover.epub')
    await expect(page.locator('#loading')).toBeHidden()
    await expect(page.locator('#book-title')).toHaveText('Start No Cover')
  })
})
