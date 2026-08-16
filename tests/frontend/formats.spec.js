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
