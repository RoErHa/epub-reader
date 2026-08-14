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

test.describe('reading position', () => {
  test('is remembered across a reload', async ({ page }) => {
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
    await page.goto('/')
    await openBook(page, BOOK)
    await page.locator('#tab-toc').click()
    await page.locator('.toc-item', { hasText: 'Chapter Four' }).click()

    await expect.poll(async () => {
      const r = await page.request.get(`/api/books/${BOOK}/position`)
      return (await r.json()).position?.percent ?? 0
    }, { timeout: 10000 }).toBeGreaterThan(0)

    await page.reload()
    await expect(
      page.locator(`.book-row[data-name="${BOOK}"] .book-progress`)
    ).toContainText('%')
  })

  test('a fresh book reports no progress', async ({ page }) => {
    await page.goto('/')
    await expect(
      page.locator('.book-row[data-name="hostile.epub"] .book-progress')
    ).toHaveText('')
  })
})
