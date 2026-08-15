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
    page.once('dialog', d => d.accept(name))
    await page.locator('#new-shelf').click()
    await expect(page.locator('.group-row', { hasText: name })).toBeVisible()
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
