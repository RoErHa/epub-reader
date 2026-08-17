import { test, expect } from '@playwright/test'

/* Marking a book read, and leaving a smart shelf.
 *
 * "Reading now" and "Finished" are not lists anyone maintains — they are
 * a view of a fact (§10.9), which is why nothing puts a book on one. But
 * you must still be able to get a book *off* them, and the honest way is
 * to change the fact: marking it read moves it from one to the other,
 * and taking it off "Reading now" says you are not reading it, keeping
 * your place.
 *
 * Reserved book: setting a status writes reading state exactly as
 * opening one does, and this suite runs against one shared server.
 */
const BOOK = 'finish-me.epub'

async function bookId (page, path) {
  const res = await page.request.get('/api/books?limit=500')
  return (await res.json()).books.find(b => b.path === path).id
}

async function statusOf (page, id) {
  const res = await page.request.get(`/api/books/${id}/position`)
  return (await res.json()).position?.status ?? 'unread'
}

async function openShelf (page, name) {
  await page.goto('/')
  await expect(page.locator('#status')).toHaveText('Ready')
  await page.locator('.browse-mode', { hasText: 'Shelves' }).click()
  await page.locator('.group-row', { hasText: name }).click()
  await expect(page.locator('#browse-back-title')).toHaveText(name)
}

let id = null

test.beforeEach(async ({ page }) => {
  // Each test starts from "currently reading", the state the controls
  // are about. Status can be set back; a position could not.
  id = await bookId(page, BOOK)
  await page.request.put(`/api/books/${id}/status`, { data: { status: 'reading' } })
})

test.afterEach(async ({ page }) => {
  await page.request.put(`/api/books/${id}/status`, { data: { status: 'unread' } })
})

test.describe('Reading now', () => {
  test('offers to mark a book read', async ({ page }) => {
    await openShelf(page, 'Reading now')
    await expect(page.locator(`.book-item[data-name="${BOOK}"] .row-finish`))
      .toBeVisible()
  })

  test('marking it read moves it to Finished', async ({ page }) => {
    await openShelf(page, 'Reading now')
    await page.locator(`.book-item[data-name="${BOOK}"] .row-finish`).click()

    await expect(page.locator(`.book-item[data-name="${BOOK}"]`))
      .toHaveCount(0)
    await expect.poll(() => statusOf(page, id), { timeout: 10000 })
      .toBe('finished')

    await openShelf(page, 'Finished')
    await expect(page.locator(`.book-item[data-name="${BOOK}"]`)).toBeVisible()
  })

  test('a book can be taken off it', async ({ page }) => {
    // The bug this fixes: a stored shelf offered −, a smart one did not,
    // so a book you had stopped reading stayed under "Reading now" for
    // ever.
    await openShelf(page, 'Reading now')
    await page.locator(`.book-item[data-name="${BOOK}"] .row-unshelf`).click()

    await expect(page.locator(`.book-item[data-name="${BOOK}"]`)).toHaveCount(0)
    await expect.poll(() => statusOf(page, id), { timeout: 10000 }).toBe('unread')
  })

  test('taking it off keeps your place', async ({ page }) => {
    const cfi = 'epubcfi(/6/4[chap01]!/4/2/2[p1]:0)'
    await page.request.put(`/api/books/${id}/position`,
      { data: { cfi, fraction: 0.4 } })

    await openShelf(page, 'Reading now')
    await page.locator(`.book-item[data-name="${BOOK}"] .row-unshelf`).click()
    await expect(page.locator(`.book-item[data-name="${BOOK}"]`)).toHaveCount(0)

    const res = await page.request.get(`/api/books/${id}/position`)
    expect((await res.json()).position.cfi).toBe(cfi)
  })

  test('neither control opens the book', async ({ page }) => {
    // The whole point of a row control: you are filing, not reading.
    await openShelf(page, 'Reading now')
    await page.locator(`.book-item[data-name="${BOOK}"] .row-finish`).click()
    await expect(page.locator('#reader-stage')).toHaveAttribute(
      'data-state', 'empty')
  })
})

test.describe('Finished', () => {
  test('a book can be taken off it too', async ({ page }) => {
    await page.request.put(`/api/books/${id}/status`,
      { data: { status: 'finished' } })
    await openShelf(page, 'Finished')
    await page.locator(`.book-item[data-name="${BOOK}"] .row-unshelf`).click()

    await expect(page.locator(`.book-item[data-name="${BOOK}"]`)).toHaveCount(0)
    await expect.poll(() => statusOf(page, id), { timeout: 10000 }).toBe('unread')
  })

  test('offers no ✓, because it is already read', async ({ page }) => {
    await page.request.put(`/api/books/${id}/status`,
      { data: { status: 'finished' } })
    await openShelf(page, 'Finished')
    await expect(page.locator(`.book-item[data-name="${BOOK}"]`)).toBeVisible()
    await expect(page.locator(`.book-item[data-name="${BOOK}"] .row-finish`))
      .toHaveCount(0)
  })
})

test.describe('the book detail', () => {
  test('offers to mark the book read', async ({ page }) => {
    await openShelf(page, 'Reading now')
    await page.locator(`.book-item[data-name="${BOOK}"] .book-row`).click()
    await expect(page.locator('#detail-finished')).toBeVisible()

    await page.locator('#detail-finished').click()
    await expect.poll(() => statusOf(page, id), { timeout: 10000 })
      .toBe('finished')
  })

  test('says so when the book is already read, and can undo it',
    async ({ page }) => {
      await page.request.put(`/api/books/${id}/status`,
        { data: { status: 'finished' } })
      await openShelf(page, 'Finished')
      await page.locator(`.book-item[data-name="${BOOK}"] .book-row`).click()

      const button = page.locator('#detail-finished')
      await expect(button).toHaveClass(/done/)
      await expect(button).toHaveText(/Read/)

      await button.click()
      await expect.poll(() => statusOf(page, id), { timeout: 10000 })
        .toBe('unread')
      await expect(button).not.toHaveClass(/done/)
    })
})
