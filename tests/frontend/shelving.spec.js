import { test, expect } from '@playwright/test'

/* Shelving a book without opening it.
 *
 * Putting a book on a shelf used to mean opening it first: the only way
 * in was the reader's ＋ Shelf button. But shelving is something you do
 * *while browsing* — you recognise a book in the list and file it — so
 * the offer belongs on the row.
 *
 * These use a book nothing else opens, because opening writes reading
 * state and this suite runs against one shared server.
 */
const BOOK = 'untouched.epub'
const SHELF = 'Holiday reading'

async function ready (page) {
  await page.goto('/')
  await expect(page.locator('#status')).toHaveText('Ready')
}

async function shelfNamed (page, name) {
  const res = await page.request.get('/api/shelves')
  return (await res.json()).shelves.find(s => s.name === name)
}

test.beforeEach(async ({ page }) => {
  // Shelves are shared server-side, so each test starts from a known one.
  const existing = await shelfNamed(page, SHELF)
  if (existing) await page.request.delete(`/api/shelves/${existing.id}`)
  await page.request.post('/api/shelves', { data: { name: SHELF } })
})

test.describe('from the browse list', () => {
  test('every book offers a shelf', async ({ page }) => {
    await ready(page)
    await expect(page.locator(`.book-item[data-name="${BOOK}"] .row-shelf`))
      .toBeVisible()
  })

  test('it does not open the book', async ({ page }) => {
    // The whole point: you are filing, not reading.
    await ready(page)
    await page.locator(`.book-item[data-name="${BOOK}"] .row-shelf`).click()
    await expect(page.locator('#shelf-sheet')).toBeVisible()
    await expect(page.locator('#reader-stage')).toHaveAttribute(
      'data-state', 'empty')
  })

  test('the book goes on the shelf', async ({ page }) => {
    await ready(page)
    await page.locator(`.book-item[data-name="${BOOK}"] .row-shelf`).click()
    await page.locator('.shelf-choice', { hasText: SHELF }).click()

    const shelf = await shelfNamed(page, SHELF)
    await expect.poll(async () => {
      const res = await page.request.get(`/api/shelves/${shelf.id}/books`)
      return (await res.json()).books.length
    }, { timeout: 10000 }).toBe(1)
  })

  test('choosing it again takes it off', async ({ page }) => {
    await ready(page)
    const open = () =>
      page.locator(`.book-item[data-name="${BOOK}"] .row-shelf`).click()
    await open()
    await page.locator('.shelf-choice', { hasText: SHELF }).click()
    await expect(page.locator('.shelf-choice.on-shelf')).toBeVisible()
    await page.locator('.shelf-choice', { hasText: SHELF }).click()
    await expect(page.locator('.shelf-choice.on-shelf')).toHaveCount(0)
  })

  test('a book already shelved says so when you open the sheet',
    async ({ page }) => {
      await ready(page)
      await page.locator(`.book-item[data-name="${BOOK}"] .row-shelf`).click()
      await page.locator('.shelf-choice', { hasText: SHELF }).click()
      await page.locator('#shelf-close').click()

      await page.locator(`.book-item[data-name="${BOOK}"] .row-shelf`).click()
      await expect(page.locator('.shelf-choice', { hasText: SHELF }))
        .toHaveClass(/on-shelf/)
    })
})

test.describe('while looking at a shelf', () => {
  async function openShelf (page) {
    await ready(page)
    await page.locator('.browse-mode', { hasText: 'Shelves' }).click()
    await page.locator('.group-row', { hasText: SHELF }).click()
  }

  test('a book can be taken off the shelf you are looking at',
    async ({ page }) => {
      await ready(page)
      await page.locator(`.book-item[data-name="${BOOK}"] .row-shelf`).click()
      await page.locator('.shelf-choice', { hasText: SHELF }).click()
      await page.locator('#shelf-close').click()

      await openShelf(page)
      await expect(page.locator('.book-item')).toHaveCount(1)
      await page.locator('.row-unshelf').click()
      await expect(page.locator('.book-item')).toHaveCount(0)
    })

  test('the remove button only appears on a shelf', async ({ page }) => {
    // In Titles there is no shelf to remove it from.
    await ready(page)
    await expect(page.locator('.row-unshelf')).toHaveCount(0)
  })

  test('taking it off does not open it either', async ({ page }) => {
    await ready(page)
    await page.locator(`.book-item[data-name="${BOOK}"] .row-shelf`).click()
    await page.locator('.shelf-choice', { hasText: SHELF }).click()
    await page.locator('#shelf-close').click()

    await openShelf(page)
    await page.locator('.row-unshelf').click()
    await expect(page.locator('#reader-stage')).toHaveAttribute(
      'data-state', 'empty')
  })
})

test.describe('choosing a book still works', () => {
  test('clicking the row shows the book, as it always did', async ({ page }) => {
    await ready(page)
    await page.locator('.book-row[data-name="left-hand-of-darkness.epub"]').click()
    await expect(page.locator('#detail-read')).toBeVisible({ timeout: 15000 })
  })
})
