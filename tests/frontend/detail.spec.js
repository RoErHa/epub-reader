import { test, expect } from '@playwright/test'
import { expectBookText } from './helpers.js'

const BOOK = 'left-hand-of-darkness.epub'

/* Choosing a book shows it before opening it: cover, series and number,
 * author, genre, year, then the synopsis, who wrote it, and what else
 * they wrote. Reading starts when you ask for it. */

const choose = async (page, name = BOOK) => {
  await page.goto('/')
  await expect(page.locator('#status')).toHaveText('Ready')
  await page.locator(`.book-row[data-name="${name}"]`).click()
  await expect(page.locator('#detail')).toBeVisible()
}

test.describe('book detail', () => {
  test('choosing a book shows its details, not the reader', async ({ page }) => {
    await choose(page)
    await expect(page.locator('#detail-title')).toHaveText(
      'The Left Hand of Darkness'
    )
    await expect(page.locator('#reader-stage')).toHaveAttribute(
      'data-state', 'empty'
    )
  })

  test('the headline facts are shown', async ({ page }) => {
    await choose(page)
    await expect(page.locator('#detail-author')).toHaveText('Ursula K. Le Guin')
    await expect(page.locator('#detail-series')).toContainText('Hainish Cycle')
    await expect(page.locator('#detail-series')).toContainText('4')
    await expect(page.locator('#detail-facts')).toContainText('1969')
    await expect(page.locator('#detail-facts')).toContainText('Science Fiction')
  })

  test('the cover is shown', async ({ page }) => {
    await choose(page)
    await expect(page.locator('#detail-cover')).toHaveAttribute(
      'src', /\/api\/covers\//
    )
  })

  test('Read opens the book', async ({ page }) => {
    await choose(page)
    await page.locator('#detail-read').click()
    await expect(page.locator('#reader-stage')).toHaveAttribute(
      'data-state', 'open', { timeout: 20000 }
    )
    await expectBookText(page, 'Chapter One')
  })

  test('About the author says so when nothing has been fetched',
    async ({ page }) => {
      await choose(page)
      await expect(page.locator('#detail-author-panel')).toContainText(
        /Nothing about|not been looked up|No details/i
      )
    })

  test('Also published lists the rest of the library by that author',
    async ({ page }) => {
      await choose(page)
      const rows = page.locator('.also-row')
      await expect(rows.first()).toBeVisible()
      await expect(page.locator('#detail-also')).toContainText(
        'A Wizard of Earthsea'
      )
      await expect(page.locator('#detail-also')).not.toContainText(
        'The Left Hand of Darkness'
      )
    })

  test('owned books in Also published are marked and clickable',
    async ({ page }) => {
      await choose(page)
      const row = page.locator('.also-row', { hasText: 'A Wizard of Earthsea' })
      await expect(row).toHaveClass(/owned/)
      await row.click()
      await expect(page.locator('#detail-title')).toHaveText(
        'A Wizard of Earthsea'
      )
    })

  test('a book can be put on a shelf from its detail page', async ({ page }) => {
    const name = `Detail ${Math.random().toString(36).slice(2, 7)}`
    await page.request.post('/api/shelves', { data: { name } })
    await choose(page)
    await page.locator('#detail-shelf').click()
    await page.locator('.shelf-choice', { hasText: name }).click()
    await expect(page.locator('.shelf-choice', { hasText: name }))
      .toHaveClass(/on-shelf/)
  })

  test('progress is shown once you have read some', async ({ page }) => {
    await choose(page, 'detail-progress.epub')
    await page.locator('#detail-read').click()
    await expect(page.locator('#reader-stage')).toHaveAttribute(
      'data-state', 'open', { timeout: 20000 }
    )
    await expect.poll(async () => {
      const r = await page.request.get('/api/books?limit=500')
      const { books } = await r.json()
      return books.find(b => b.name === 'detail-progress.epub')?.percent ?? 0
    }, { timeout: 15000 }).toBeGreaterThan(0)

    await page.goto('/')
    await page.locator('.book-row[data-name="detail-progress.epub"]').click()
    await expect(page.locator('#detail-facts')).toContainText('%')
  })

  test('going back from the reader returns to the detail page',
    async ({ page }) => {
      await choose(page)
      await page.locator('#detail-read').click()
      await expect(page.locator('#reader-stage')).toHaveAttribute(
        'data-state', 'open', { timeout: 20000 }
      )
      await expect(page.locator('#detail')).toBeHidden()
    })
})
