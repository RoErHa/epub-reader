import { test, expect } from '@playwright/test'
import { openBook, waitForBookFrame } from './helpers.js'

/* PDF.
 *
 * A different rendering model from everything else here: foliate hands
 * the file to pdf.js, which rasterises pages onto a canvas rather than
 * reflowing text. Its own adapter calls itself a proof-of-concept, so
 * these tests ask only that a PDF opens, draws, and pages — not that it
 * behaves like an EPUB.
 *
 * The interesting part is the CSP. pdf.js runs its parser in a Web
 * Worker, which nothing else in this app does.
 */
const PDF = 'Herbert, Frank - A Pdf Book.pdf'

test.describe('a pdf', () => {
  test('is listed with the title from its filename', async ({ page }) => {
    await page.goto('/')
    const row = page.locator(`.book-row[data-name="${PDF}"]`)
    await expect(row).toBeVisible()
    // No usable title inside, as with most real PDFs — so the
    // filename is the title, and the repair passes understand it.
    await expect(row).toContainText('Herbert, Frank - A Pdf Book')
  })

  test('opens without the CSP refusing anything', async ({ page }) => {
    const refusals = []
    page.on('console', msg => {
      const text = msg.text()
      if (/Content Security Policy|Refused to/i.test(text)) refusals.push(text)
    })
    await page.goto('/')
    await openBook(page, PDF)
    expect(refusals).toEqual([])
  })

  test('draws its first page', async ({ page }) => {
    await page.goto('/')
    await openBook(page, PDF)
    const frame = await waitForBookFrame(page)
    // pdf.js paints onto a canvas; an EPUB has no canvas at all.
    await expect.poll(
      () => frame.evaluate(() => document.querySelectorAll('canvas').length)
        .catch(() => 0),
      { timeout: 20000 }
    ).toBeGreaterThan(0)
  })

  test('pages forward and back', async ({ page }) => {
    await page.goto('/')
    await openBook(page, PDF)
    await page.locator('#btn-next').click()
    await page.locator('#btn-prev').click()
    await expect(page.locator('#reader-stage')).toHaveAttribute('data-state', 'open')
  })
})
