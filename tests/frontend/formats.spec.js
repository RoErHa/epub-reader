import { test, expect } from '@playwright/test'
import { openBook, expectBookText } from './helpers.js'

/* MOBI and AZW3.
 *
 * The reader got these for free: foliate-js sniffs the container rather
 * than trusting the extension, so nothing in the reading path changed.
 * What had to be written was the library side — their metadata lives in
 * a PalmDB record instead of an OPF. These tests are the proof that the
 * free half really is free.
 */
const MOBI = 'kindle-book.mobi'

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
    await expect(page.locator('.book-name').first()).toHaveText('A Kindle Book')
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
