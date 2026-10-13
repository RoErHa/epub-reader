import { test, expect } from '@playwright/test'
import { openBook, expectBookText, waitForBookFrame } from './helpers.js'

/* MOBI and AZW3.
 *
 * The reader got these for free: foliate-js sniffs the container rather
 * than trusting the extension, so nothing in the reading path changed.
 * What had to be written was the library side — their metadata lives in
 * a PalmDB record instead of an OPF. These tests are the proof that the
 * free half really is free.
 */
const MOBI = 'kindle-book.mobi'
const PALM = 'palm-doc.prc'

/* Six PRC files here are the older PalmDOC container, which foliate
 * refuses — "File type not supported". The server hands it over as a
 * MOBI, and the reader never knows (§8.30). */
test.describe('a palmdoc book', () => {
  test('opens and renders', async ({ page }) => {
    await page.goto('/')
    await openBook(page, PALM)
    await expectBookText(page, 'Words from a PalmDOC')
  })
})

test.describe('a kindle book', () => {
  test('is listed with its title and author', async ({ page }) => {
    await page.goto('/')
    const row = page.locator(`.book-row[data-name="${MOBI}"]`)
    await expect(row).toBeVisible()
    await expect(row).toContainText('A Kindle Book')
    await expect(row).toContainText('Frank Herbert')
  })

  test('opens and renders', async ({ page }) => {
    await page.goto('/')
    await openBook(page, MOBI)
    await expectBookText(page, 'Text.')
  })

  test('turns a page like any other book', async ({ page }) => {
    await page.goto('/')
    await openBook(page, MOBI)
    await page.locator('#btn-next').click()
    await page.locator('#btn-prev').click()
    await expect(page.locator('#reader-stage')).toHaveAttribute('data-state', 'open')
  })

  test('is searchable in the library', async ({ page }) => {
    await page.goto('/')
    await page.locator('#search-input').fill('Kindle')
    await page.locator('#search-input').press('Enter')
    // That the book is found, not that it is the first row: asserting on
    // .first() depends on when the list re-renders, and failed once in a
    // full run while passing alone.
    await expect(page.locator('.book-name', { hasText: 'A Kindle Book' }))
      .toBeVisible()
  })
})


/* A comic.
 *
 * A CBZ is a zip of images and foliate draws it page by page — a third
 * rendering model beside reflowed text and rasterised PDF pages. The
 * filename is the only metadata most of them have, and it is full of
 * the scanner's notes.
 */
const COMIC = 'Zootopia 001 (2025) (digital) (Salem-Empire).cbz'

test.describe('a comic', () => {
  test('is listed without the scanner\'s notes', async ({ page }) => {
    await page.goto('/')
    const row = page.locator(`.book-row[data-name="${COMIC}"]`)
    await expect(row).toBeVisible()
    await expect(row).toContainText('Zootopia 001')
    await expect(row).not.toContainText('Salem-Empire')
  })

  test('opens and draws a page', async ({ page }) => {
    await page.goto('/')
    await openBook(page, COMIC)
    const frame = await waitForBookFrame(page)
    await expect.poll(
      () => frame.evaluate(() => document.querySelectorAll('img, canvas').length)
        .catch(() => 0),
      { timeout: 20000 }
    ).toBeGreaterThan(0)
  })

  test('turns its pages', async ({ page }) => {
    await page.goto('/')
    await openBook(page, COMIC)
    await page.locator('#btn-next').click()
    await page.locator('#btn-prev').click()
    await expect(page.locator('#reader-stage')).toHaveAttribute(
      'data-state', 'open')
  })
})

/* A book that was never zipped.
 *
 * 265 of the real library are directories — iTunes exports, and
 * anything somebody unzipped and never put back. foliate reads a zip,
 * so these are zipped on the way out and nothing downstream knows the
 * difference.
 */
const LOOSE = 'Never Zipped.epub'

test.describe('a book stored as a directory', () => {
  test('is listed like any other', async ({ page }) => {
    await page.goto('/')
    const row = page.locator(`.book-row[data-name="${LOOSE}"]`)
    await expect(row).toBeVisible()
    await expect(row).toContainText('Never Zipped')
  })

  test('opens and renders', async ({ page }) => {
    // Any chapter: position is shared server-side, so the paging test
    // below leaves this book wherever it last was.
    await page.goto('/')
    await openBook(page, LOOSE)
    await expectBookText(page, 'Chapter')
  })

  test('its contents are read', async ({ page }) => {
    await page.goto('/')
    await openBook(page, LOOSE)
    await page.locator('#tab-toc').click()
    await expect(page.locator('.toc-item')).toHaveCount(3)
  })

  test('it pages like any other', async ({ page }) => {
    await page.goto('/')
    await openBook(page, LOOSE)
    await page.locator('#btn-next').click()
    await page.locator('#btn-prev').click()
    await expect(page.locator('#reader-stage')).toHaveAttribute(
      'data-state', 'open')
  })
})
