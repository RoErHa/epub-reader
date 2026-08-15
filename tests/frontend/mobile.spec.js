import { test, expect, devices } from '@playwright/test'
import { openBook, chooseBook, expectBookText } from './helpers.js'

/* On a phone there is no room for three columns: the reading pane ends
 * up a couple of hundred pixels wide and the paginator has nothing to
 * lay out into, so the book shows nothing at all. Selecting a book takes
 * the whole screen, with a back arrow to the library.
 */

const BOOK = 'left-hand-of-darkness.epub'

// defaultBrowserType cannot be set inside a describe — and it should not
// be here anyway: this runs on whichever engine the project selects.
const strip = name => {
  const { defaultBrowserType, ...rest } = devices[name] ?? {}
  return rest
}

// The devices this is actually read on. An iPad 9th generation is
// 810 x 1080 points, so landscape must be covered as well.
const IPHONE = { ...strip('iPhone 15'), viewport: { width: 393, height: 852 },
  hasTouch: true, isMobile: true }
const IPAD_PORTRAIT = { viewport: { width: 810, height: 1080 }, hasTouch: true }
const IPAD_LANDSCAPE = { viewport: { width: 1080, height: 810 }, hasTouch: true }

test.describe('on a phone', () => {
  test.use(IPHONE)

  test('the library fills the screen before a book is chosen', async ({ page }) => {
    await page.goto('/')
    await expect(page.locator('#status')).toHaveText('Ready')
    const view = await page.locator('#browser').boundingBox()
    const width = page.viewportSize().width
    expect(view.width).toBeGreaterThan(width * 0.9)
  })

  test('choosing a book gives the reader the whole screen', async ({ page }) => {
    await page.goto('/')
    await openBook(page, BOOK)
    const reader = await page.locator('#reader').boundingBox()
    const width = page.viewportSize().width
    expect(reader.width).toBeGreaterThan(width * 0.9)
    await expect(page.locator('#browser')).toBeHidden()
  })

  test('the book actually renders at phone size', async ({ page }) => {
    await page.goto('/')
    await openBook(page, BOOK)
    await expectBookText(page, 'Chapter One')
  })

  test('back steps out one level at a time', async ({ page }) => {
    // library → book → reading, so back retraces that rather than
    // dumping you at the library from the middle of a chapter.
    await page.goto('/')
    await openBook(page, BOOK)
    await expect(page.locator('#reader-back')).toBeVisible()

    await page.locator('#reader-back').click()
    await expect(page.locator('#detail')).toBeVisible()      // back to the book

    await page.locator('#reader-back').click()
    await expect(page.locator('#browser')).toBeVisible()     // back to the library
    await expect(page.locator('#reader')).toBeHidden()
  })

  test('back from the book itself returns to the library', async ({ page }) => {
    await page.goto('/')
    await chooseBook(page, BOOK)
    await page.locator('#reader-back').click()
    await expect(page.locator('#browser')).toBeVisible()
  })

  test('the back arrow is reachable, not under the notch', async ({ page }) => {
    await page.goto('/')
    await openBook(page, BOOK)
    const box = await page.locator('#reader-back').boundingBox()
    expect(box.y).toBeGreaterThanOrEqual(0)
    // Comfortably tappable.
    expect(box.width).toBeGreaterThanOrEqual(40)
    expect(box.height).toBeGreaterThanOrEqual(40)
  })

  test('going back and opening another book still works', async ({ page }) => {
    await page.goto('/')
    await openBook(page, BOOK)
    await page.locator('#reader-back').click()   // to the book
    await page.locator('#reader-back').click()   // to the library
    await expect(page.locator('#browser')).toBeVisible()
    await openBook(page, 'start-no-cover.epub')
    await expect(page.locator('#book-title')).toHaveText('Start No Cover')
    await expectBookText(page, 'Chapter One')
  })

  test('search still works, and opening a result reads full screen',
    async ({ page }) => {
      await page.goto('/')
      await page.locator('#search-input').fill('darkness')
      await page.locator('#search-input').press('Enter')
      await page.locator('.book-row').first().click()
      await page.locator('#detail-read').click()
      await expect(page.locator('#reader-stage')).toHaveAttribute(
        'data-state', 'open', { timeout: 20000 }
      )
      await expect(page.locator('#browser')).toBeHidden()
    })

  test('the header stays on one line while reading', async ({ page }) => {
    await page.goto('/')
    await openBook(page, BOOK)
    const header = await page.locator('header').boundingBox()
    // Three wrapped lines of title used to push the header to ~90pt.
    expect(header.height).toBeLessThan(56)
  })

  test('the search box gives way to the book while reading', async ({ page }) => {
    await page.goto('/')
    await expect(page.locator('#search-input')).toBeVisible()
    await openBook(page, BOOK)
    await expect(page.locator('#search-input')).toBeHidden()
    // Still hidden on the book's own page — you are in a book, not the
    // library. It comes back when you do.
    await page.locator('#reader-back').click()
    await expect(page.locator('#search-input')).toBeHidden()
    await page.locator('#reader-back').click()
    await expect(page.locator('#search-input')).toBeVisible()
  })

  test('the loading indicator fills the screen too', async ({ page }) => {
    await page.route('**/api/books/*/file', async route => {
      await new Promise(r => setTimeout(r, 1500))
      await route.continue()
    })
    await page.goto('/')
    await chooseBook(page, BOOK)
    await page.locator('#detail-read').click()
    const overlay = await page.locator('#loading').boundingBox()
    expect(overlay.width).toBeGreaterThan(page.viewportSize().width * 0.9)
  })
})

test.describe('on a desktop', () => {
  test.use({ viewport: { width: 1280, height: 800 } })

  test('both panes stay on screen and there is no back arrow',
    async ({ page }) => {
      await page.goto('/')
      await openBook(page, BOOK)
      await expect(page.locator('#browser')).toBeVisible()
      await expect(page.locator('#reader')).toBeVisible()
      await expect(page.locator('#reader-back')).toBeHidden()
    })
})

test.describe('on an iPad 9th generation, portrait', () => {
  test.use(IPAD_PORTRAIT)

  test('reading takes the whole screen', async ({ page }) => {
    await page.goto('/')
    await openBook(page, BOOK)
    const reader = await page.locator('#reader').boundingBox()
    expect(reader.width).toBeGreaterThan(page.viewportSize().width * 0.9)
    await expect(page.locator('#browser')).toBeHidden()
  })

  test('the book renders', async ({ page }) => {
    await page.goto('/')
    await openBook(page, BOOK)
    await expectBookText(page, 'Chapter One')
  })

  test('back steps out to the book, then the library', async ({ page }) => {
    await page.goto('/')
    await openBook(page, BOOK)
    await page.locator('#reader-back').click()
    await expect(page.locator('#detail')).toBeVisible()
    await page.locator('#reader-back').click()
    await expect(page.locator('#browser')).toBeVisible()
  })
})

test.describe('on an iPad 9th generation, landscape', () => {
  test.use(IPAD_LANDSCAPE)

  test('1080 points still gets the whole screen, not a squeezed column',
    async ({ page }) => {
      // Three columns here would leave the reader about 520 points —
      // readable, but not what you want on the device you read on most.
      await page.goto('/')
      await openBook(page, BOOK)
      const reader = await page.locator('#reader').boundingBox()
      expect(reader.width).toBeGreaterThan(page.viewportSize().width * 0.9)
      await expect(page.locator('#reader-back')).toBeVisible()
    })

  test('the book renders', async ({ page }) => {
    await page.goto('/')
    await openBook(page, BOOK)
    await expectBookText(page, 'Chapter One')
  })
})

test.describe('the Mac layout is untouched', () => {
  // Explicitly pinned: the desktop UI is not to change.
  for (const width of [1280, 1440, 1728]) {
    test(`at ${width} points, three columns and no back arrow`, async ({ page }) => {
      await page.setViewportSize({ width, height: 900 })
      await page.goto('/')
      await openBook(page, BOOK)
      await expect(page.locator('#browser')).toBeVisible()
      await expect(page.locator('#side')).toBeVisible()
      await expect(page.locator('#reader-back')).toBeHidden()
      const library = await page.locator('#browser').boundingBox()
      expect(Math.round(library.width)).toBe(330)
    })
  }
})
