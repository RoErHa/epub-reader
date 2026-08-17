import { test, expect } from '@playwright/test'
import { openBook } from './helpers.js'

const BOOK = 'left-hand-of-darkness.epub'

/** Shelves are server state shared by every test, so each test makes its
 *  own uniquely named one and clears up after itself. */
const uniqueName = () => `Shelf ${Math.random().toString(36).slice(2, 8)}`

async function makeShelf (page, name) {
  const res = await page.request.post('/api/shelves', { data: { name } })
  return (await res.json()).shelf
}

async function shelfById (page, id) {
  const res = await page.request.get('/api/shelves')
  return (await res.json()).shelves.find(s => s.id === id)
}

async function shelfNamed (page, name) {
  const res = await page.request.get('/api/shelves')
  return (await res.json()).shelves.find(s => s.name === name)
}

test.describe('shelves', () => {
  test('the browse strip offers Shelves', async ({ page }) => {
    await page.goto('/')
    await expect(page.locator('.browse-mode', { hasText: 'Shelves' }))
      .toBeVisible()
  })

  test('smart shelves are listed first', async ({ page }) => {
    await page.goto('/')
    await page.locator('.browse-mode', { hasText: 'Shelves' }).click()
    // The list renders asynchronously — read it once it has settled.
    await expect.poll(async () => {
      const names = await page.locator('.group-name').allTextContents()
      return names.slice(0, 2).join('|')
    }, { timeout: 10000 }).toBe('Reading now|Finished')
  })

  test('a shelf can be created from the UI', async ({ page }) => {
    const name = uniqueName()
    await page.goto('/')
    await page.locator('.browse-mode', { hasText: 'Shelves' }).click()
    await page.locator('#new-shelf').click()
    await page.locator('#shelf-name').fill(name)
    await page.locator('#shelf-name').press('Enter')
    await expect(page.locator('.group-row', { hasText: name })).toBeVisible()
  })

  test('naming it is the app asking, not the browser', async ({ page }) => {
    // window.prompt blocks the page, cannot be styled, and on iOS reads
    // as Safari asking rather than the app — the same reason the note
    // composer exists (§8.2).
    let asked = false
    page.on('dialog', d => { asked = true; d.dismiss() })
    await page.goto('/')
    await page.locator('.browse-mode', { hasText: 'Shelves' }).click()
    await page.locator('#new-shelf').click()
    await expect(page.locator('#shelf-name')).toBeFocused()
    expect(asked).toBe(false)
  })

  test('giving up on the name leaves no shelf behind', async ({ page }) => {
    await page.goto('/')
    await page.locator('.browse-mode', { hasText: 'Shelves' }).click()
    const before = await (await page.request.get('/api/shelves')).json()
    await page.locator('#new-shelf').click()
    await page.locator('#shelf-name').fill('Never mind')
    await page.locator('#shelf-name').press('Escape')

    await expect(page.locator('#shelf-name')).toBeHidden()
    const after = await (await page.request.get('/api/shelves')).json()
    expect(after.shelves.length).toBe(before.shelves.length)
  })

  test('a shelf can be deleted from the list', async ({ page }) => {
    const shelf = await makeShelf(page, uniqueName())
    await page.goto('/')
    await page.locator('.browse-mode', { hasText: 'Shelves' }).click()
    const row = page.locator('.shelf-item', { hasText: shelf.name })
    await expect(row).toBeVisible()

    // Two taps, not one: deleting a shelf cannot be undone, and a stray
    // tap on a phone should not cost you a list you built.
    await row.locator('.row-shelf-delete').click()
    await expect(row.locator('.row-shelf-delete')).toHaveText(/Delete/)
    await row.locator('.row-shelf-delete').click()

    await expect(page.locator('.group-row', { hasText: shelf.name }))
      .toHaveCount(0)
    expect(await shelfById(page, shelf.id)).toBeUndefined()
  })

  test('one tap deletes nothing', async ({ page }) => {
    const shelf = await makeShelf(page, uniqueName())
    await page.goto('/')
    await page.locator('.browse-mode', { hasText: 'Shelves' }).click()
    await page.locator('.shelf-item', { hasText: shelf.name })
      .locator('.row-shelf-delete').click()
    expect(await shelfById(page, shelf.id)).toBeTruthy()
  })

  test('deleting a shelf keeps its books', async ({ page }) => {
    // The shelf was yours; the books are not the shelf's to take.
    const shelf = await makeShelf(page, uniqueName())
    const books = await (await page.request.get('/api/books?limit=500')).json()
    const book = books.books[0]
    await page.request.put(`/api/shelves/${shelf.id}/books/${book.id}`)

    await page.goto('/')
    await page.locator('.browse-mode', { hasText: 'Shelves' }).click()
    const row = page.locator('.shelf-item', { hasText: shelf.name })
    await row.locator('.row-shelf-delete').click()
    await row.locator('.row-shelf-delete').click()
    await expect(page.locator('.group-row', { hasText: shelf.name }))
      .toHaveCount(0)

    const res = await page.request.get(`/api/books/${book.id}/detail`)
    expect(res.ok()).toBe(true)
  })

  test('a smart shelf cannot be deleted', async ({ page }) => {
    // It is a consequence of reading, not a list — there is nothing to
    // delete, only a fact to change.
    await page.goto('/')
    await page.locator('.browse-mode', { hasText: 'Shelves' }).click()
    await expect(page.locator('.group-row', { hasText: 'Reading now' }))
      .toBeVisible()
    await expect(page.locator('.shelf-item', { hasText: 'Reading now' })
      .locator('.row-shelf-delete')).toHaveCount(0)
  })

  test('a book can be put on a shelf while reading it', async ({ page }) => {
    const shelf = await makeShelf(page, uniqueName())
    await page.goto('/')
    await openBook(page, BOOK)
    await page.locator('#btn-shelf').click()
    await page.locator('.shelf-choice', { hasText: shelf.name }).click()
    await expect(page.locator('.shelf-choice', { hasText: shelf.name }))
      .toHaveClass(/on-shelf/)
  })

  test('and taken off again', async ({ page }) => {
    const shelf = await makeShelf(page, uniqueName())
    await page.goto('/')
    await openBook(page, BOOK)
    await page.locator('#btn-shelf').click()
    const choice = page.locator('.shelf-choice', { hasText: shelf.name })
    await choice.click()
    await expect(choice).toHaveClass(/on-shelf/)
    await choice.click()
    await expect(choice).not.toHaveClass(/on-shelf/)
  })

  test('a shelf can be made from the sheet, and the book goes on it',
    async ({ page }) => {
      const name = uniqueName()
      await page.goto('/')
      // This suite's own book: the shelving suite counts how many
      // shelves untouched.epub sits on, so it must not gain one here.
      await page.locator(`.book-item[data-name="${BOOK}"] .row-shelf`).click()
      await page.locator('#shelf-new').click()
      await page.locator('#shelf-sheet-name').fill(name)
      await page.locator('#shelf-sheet-name').press('Enter')

      // Made and chosen in one gesture: you asked for a shelf while
      // holding a book.
      await expect(page.locator('.shelf-choice', { hasText: name }))
        .toHaveClass(/on-shelf/)

      // Clear up: this book is shared with the shelving suite, which
      // counts how many shelves it sits on.
      const shelf = await shelfNamed(page, name)
      await page.request.delete(`/api/shelves/${shelf.id}`)
    })

  test('drilling into a shelf shows what is on it', async ({ page }) => {
    const shelf = await makeShelf(page, uniqueName())
    await page.goto('/')
    await openBook(page, BOOK)
    await page.locator('#btn-shelf').click()
    await page.locator('.shelf-choice', { hasText: shelf.name }).click()
    await page.keyboard.press('Escape')

    await page.locator('.browse-mode', { hasText: 'Shelves' }).click()
    await page.locator('.group-row', { hasText: shelf.name }).click()
    await expect(page.locator('#browse-back-title')).toHaveText(shelf.name)
    await expect(page.locator('.book-row')).toHaveCount(1)
  })

  test('Reading now fills up as you read', async ({ page }) => {
    // Its own book: opening one writes reading state, so this would
    // otherwise disturb the opening-position tests.
    await page.goto('/')
    await openBook(page, 'shelf-reading.epub')
    // The position save is debounced, so wait for the server to have it.
    await expect.poll(async () => {
      const { shelves } = await (await page.request.get('/api/shelves')).json()
      return shelves.find(s => s.key === 'reading')?.books ?? 0
    }, { timeout: 15000 }).toBeGreaterThan(0)

    // No need to leave the reader: on a desktop the library is still
    // on screen, and the back arrow only exists on touch layouts.
    await page.locator('.browse-mode', { hasText: 'Shelves' }).click()
    await page.locator('.group-row', { hasText: 'Reading now' }).click()
    await expect(page.locator('.book-row').first()).toBeVisible()
  })
})

test.describe('sorting', () => {
  test('a sort picker is offered', async ({ page }) => {
    await page.goto('/')
    await expect(page.locator('#sort-select')).toBeVisible()
    const options = await page.locator('#sort-select option').allTextContents()
    expect(options).toContain('Title')
    expect(options).toContain('Author')
    expect(options).toContain('Recently read')
  })

  test('choosing a sort reorders the list', async ({ page }) => {
    await page.goto('/')
    const byTitle = await page.locator('.book-name').allTextContents()
    await page.locator('#sort-select').selectOption('author')
    await expect.poll(async () => {
      const now = await page.locator('.book-name').allTextContents()
      return now.length > 0 && now.join('|') !== byTitle.join('|')
    }).toBe(true)
  })

  test('the chosen sort survives a reload', async ({ page }) => {
    await page.goto('/')
    await page.locator('#sort-select').selectOption('year')
    await page.reload()
    await expect(page.locator('#status')).toHaveText('Ready')
    await expect(page.locator('#sort-select')).toHaveValue('year')
  })

  test('the letter bar follows the sort', async ({ page }) => {
    // Sorting by author means the alphabet should be authors' initials.
    await page.goto('/')
    await page.locator('#sort-select').selectOption('author')
    await expect.poll(
      () => page.locator('.letter', { hasText: 'L' }).count()
    ).toBeGreaterThan(0)   // Le Guin
  })
})
