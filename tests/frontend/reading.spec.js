import { test, expect } from '@playwright/test'
import {
  openBook, expectBookText, bookBodyStyle
} from './helpers.js'

const BOOK = 'left-hand-of-darkness.epub'

test.describe('table of contents', () => {
  test('lists every chapter', async ({ page }) => {
    await page.goto('/')
    await openBook(page, BOOK)
    await page.locator('#tab-toc').click()
    await expect(page.locator('.toc-item')).toHaveCount(4)
    await expect(page.locator('.toc-item').first()).toHaveText('Chapter One')
  })

  test('navigates to the chapter you click', async ({ page }) => {
    await page.goto('/')
    await openBook(page, BOOK)
    await page.locator('#tab-toc').click()
    await page.locator('.toc-item', { hasText: 'Chapter Three' }).click()

    await expectBookText(page, 'Chapter Three')
  })

  test('marks the chapter you are in', async ({ page }) => {
    await page.goto('/')
    await openBook(page, BOOK)
    await page.locator('#tab-toc').click()
    await page.locator('.toc-item', { hasText: 'Chapter Two' }).click()
    await expect(
      page.locator('.toc-item', { hasText: 'Chapter Two' })
    ).toHaveClass(/current/, { timeout: 10000 })
  })

  test('is empty before a book is open', async ({ page }) => {
    await page.goto('/')
    await page.locator('#tab-toc').click()
    await expect(page.locator('.toc-item')).toHaveCount(0)
  })
})

test.describe('display settings', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/')
    await openBook(page, BOOK)
    await page.locator('#tab-display').click()
  })

  test('changing the theme repaints the book', async ({ page }) => {
    const before = await bookBodyStyle(page, 'backgroundColor')
    await page.locator('#theme-select').selectOption('sepia')
    await expect
      .poll(() => bookBodyStyle(page, 'backgroundColor'))
      .not.toBe(before)
  })

  test('the dark theme is genuinely dark', async ({ page }) => {
    await page.locator('#theme-select').selectOption('dark')
    await expect.poll(async () => {
      const rgb = await bookBodyStyle(page, 'backgroundColor')
      const [r, g, b] = rgb.match(/\d+/g).map(Number)
      return (r + g + b) / 3
    }).toBeLessThan(80)
  })

  test('font size can be increased and decreased', async ({ page }) => {
    const read = () => bookBodyStyle(page, 'fontSize').then(parseFloat)
    const start = await read()

    await page.locator('#font-bigger').click()
    await expect.poll(read).toBeGreaterThan(start)

    await page.locator('#font-smaller').click()
    await page.locator('#font-smaller').click()
    await expect.poll(read).toBeLessThan(start)
  })

  test('line height can be changed', async ({ page }) => {
    const before = await bookBodyStyle(page, 'lineHeight')
    await page.locator('#line-height').fill('2')
    await page.locator('#line-height').dispatchEvent('input')
    await expect.poll(() => bookBodyStyle(page, 'lineHeight')).not.toBe(before)
  })

  test('the reader can switch to scrolled flow', async ({ page }) => {
    await page.locator('#flow-select').selectOption('scrolled')
    await expect
      .poll(() => page.evaluate(
        () => document.querySelector('foliate-view')
          ?.renderer?.getAttribute('flow')))
      .toBe('scrolled')
  })

  test('settings survive a reload', async ({ page }) => {
    await page.locator('#theme-select').selectOption('sepia')
    await page.locator('#font-bigger').click()
    const size = await page.locator('#font-size-value').textContent()

    await page.reload()
    await expect(page.locator('#status')).toHaveText('Ready')
    await page.locator('#tab-display').click()
    await expect(page.locator('#theme-select')).toHaveValue('sepia')
    await expect(page.locator('#font-size-value')).toHaveText(size)
  })
})

test.describe('front matter', () => {
  // Real books open on an image-only cover, and applying layout
  // attributes before that first section exists threw inside the
  // paginator. Fixtures without a cover never caught it.
  test('opening and paging raise no page errors', async ({ page }) => {
    const errors = []
    page.on('pageerror', e => errors.push(e.message))

    await page.goto('/')
    await openBook(page, BOOK)
    await page.locator('#tab-display').click()
    await page.locator('#theme-select').selectOption('sepia')
    for (let i = 0; i < 3; i++) {
      await page.locator('#btn-next').click()
      await page.waitForTimeout(200)
    }
    await page.locator('#tab-toc').click()
    await page.locator('.toc-item', { hasText: 'Chapter Two' }).click()
    await expectBookText(page, 'Chapter Two')

    expect(errors).toEqual([])
  })

  test('the contents still lists only real chapters', async ({ page }) => {
    await page.goto('/')
    await openBook(page, BOOK)
    await page.locator('#tab-toc').click()
    await expect(page.locator('.toc-item')).toHaveCount(4)
  })
})

test.describe('reading position', () => {
  // Each of these uses a book NOTHING else opens: opening writes
  // position, and position is shared server-side by design.
  test('is remembered across a reload', async ({ page }) => {
    const BOOK = 'position-a.epub'
    await page.goto('/')
    await openBook(page, BOOK)
    await page.locator('#tab-toc').click()
    await page.locator('.toc-item', { hasText: 'Chapter Three' }).click()

    // Saves are debounced, so wait until the server agrees with what the
    // reader is showing — otherwise we would capture an intermediate save.
    let saved = null
    await expect.poll(async () => {
      const shown = (await page.locator('#progress').textContent()).trim()
      const r = await page.request.get(`/api/books/${BOOK}/position`)
      const percent = (await r.json()).position?.percent
      if (percent > 0 && `${percent}%` === shown) {
        saved = shown
        return true
      }
      return false
    }, { timeout: 15000 }).toBe(true)

    await page.reload()
    await openBook(page, BOOK)

    await expect.poll(
      () => page.locator('#progress').textContent(),
      { timeout: 10000 }
    ).toContain(saved)
  })

  test('shows up as progress in the library list', async ({ page }) => {
    // A different book from the test above: reading position is shared
    // server-side by design, so two parallel tests on the same book
    // would overwrite each other.
    const OTHER = 'position-b.epub'
    await page.goto('/')
    await openBook(page, OTHER)
    await page.locator('#tab-toc').click()
    await page.locator('.toc-item', { hasText: 'Chapter Four' }).click()

    await expect.poll(async () => {
      const r = await page.request.get(`/api/books/${OTHER}/position`)
      return (await r.json()).position?.percent ?? 0
    }, { timeout: 10000 }).toBeGreaterThan(0)

    await page.reload()
    await expect(
      page.locator(`.book-row[data-name="${OTHER}"] .book-progress`)
    ).toContainText('%')
  })

  test('a book nobody has opened reports no progress', async ({ page }) => {
    // untouched.epub is opened by no other test, on purpose.
    await page.goto('/')
    await expect(
      page.locator('.book-row[data-name="untouched.epub"] .book-progress')
    ).toHaveText('')
  })
})

test.describe('books with commas in internal filenames', () => {
  // decodeURI() does not decode %2C, so foliate's resolveHref() misses
  // the manifest entry and navigation silently fails. About 22% of the
  // real library is affected — Calibre names sections "Author, Name -
  // Title_split_0.html". Readable by paging either way; this is about
  // the contents actually working.
  const COMMA = 'comma-filenames.epub'

  test('the contents can navigate to a chapter', async ({ page }) => {
    await page.goto('/')
    await openBook(page, COMMA)
    await page.locator('#tab-toc').click()
    await page.locator('.toc-item', { hasText: 'Chapter Three' }).click()
    await expectBookText(page, 'Chapter Three')
  })

  test('navigation reports no failure in the console', async ({ page }) => {
    const failures = []
    page.on('console', m => {
      if (/Could not go to|Could not resolve/.test(m.text())) failures.push(m.text())
    })
    await page.goto('/')
    await openBook(page, COMMA)
    await page.locator('#tab-toc').click()
    for (const label of ['Chapter Two', 'Chapter Four']) {
      await page.locator('.toc-item', { hasText: label }).click()
      await page.waitForTimeout(400)
    }
    expect(failures).toEqual([])
  })
})
