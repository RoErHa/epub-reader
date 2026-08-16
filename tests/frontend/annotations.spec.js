import { test, expect } from '@playwright/test'
import { openBook, waitForBookFrame, blobFrame, bookId } from './helpers.js'

/* Highlighting is a two-step act: you select words, then you say what to
 * do with them. The selection popover is the second step, and it has to
 * appear where the words are.
 *
 * Selection lives inside the book's blob: iframe, which cannot be
 * reached with a CSS selector because foliate's shadow root is closed —
 * so these drive it through page.frames(), like every other reader test.
 *
 * This book is opened only here. Annotations are server-side and shared
 * (decision 7), so a book another test marks up would carry those marks
 * into this one — and, unlike reading position, they accumulate within
 * this file too. Position could only be avoided by reserving a book;
 * marks can be removed, so each test starts from a clean one.
 */
const BOOK = 'annotate-me.epub'

test.beforeEach(async ({ page }) => {
  const id = await bookId(page, BOOK)
  const res = await page.request.get(`/api/books/${id}/annotations`)
  for (const mark of (await res.json()).annotations ?? []) {
    await page.request.delete(`/api/annotations/${mark.id}`)
  }
})

/** Select the first paragraph of the visible section, as a reader
 *  dragging across it would. */
async function selectParagraph (page) {
  const frame = await waitForBookFrame(page)
  await frame.evaluate(() => {
    const node = document.querySelector('p')
    const range = document.createRange()
    range.selectNodeContents(node)
    const sel = document.getSelection()
    sel.removeAllRanges()
    sel.addRange(range)
    // The reader lets go of the mouse; that is when the offer appears.
    document.dispatchEvent(new Event('pointerup', { bubbles: true }))
  })
  await expect(page.locator('#selection-menu')).toBeVisible({ timeout: 10000 })
}

async function marksOn (page, name) {
  const id = await bookId(page, name)
  const res = await page.request.get(`/api/books/${id}/annotations`)
  return (await res.json()).annotations
}

test.describe('selecting text', () => {
  test('offers what to do with it', async ({ page }) => {
    await page.goto('/')
    await openBook(page, BOOK)
    await selectParagraph(page)
    await expect(page.locator('#selection-highlight')).toBeVisible()
    await expect(page.locator('#selection-note')).toBeVisible()
    await expect(page.locator('#selection-copy')).toBeVisible()
  })

  test('the offer goes away when the selection does', async ({ page }) => {
    await page.goto('/')
    await openBook(page, BOOK)
    await selectParagraph(page)
    const frame = blobFrame(page)
    await frame.evaluate(() => {
      document.getSelection().removeAllRanges()
      document.dispatchEvent(new Event('pointerup', { bubbles: true }))
    })
    await expect(page.locator('#selection-menu')).toBeHidden({ timeout: 10000 })
  })
})

test.describe('highlighting', () => {
  test('keeps the words, not just the place', async ({ page }) => {
    await page.goto('/')
    await openBook(page, BOOK)
    await selectParagraph(page)
    await page.locator('#selection-highlight').click()

    await expect.poll(async () => (await marksOn(page, BOOK)).length,
      { timeout: 10000 }).toBe(1)
    const [mark] = await marksOn(page, BOOK)
    expect(mark.text.length).toBeGreaterThan(10)
    expect(mark.cfi).toContain('epubcfi')
  })

  test('it is listed under Highlights', async ({ page }) => {
    await page.goto('/')
    await openBook(page, BOOK)
    await selectParagraph(page)
    await page.locator('#selection-highlight').click()

    await page.locator('#tab-marks').click()
    await expect(page.locator('.mark-item')).toHaveCount(1, { timeout: 10000 })
  })

  test('it is still there when the book is opened again', async ({ page }) => {
    await page.goto('/')
    await openBook(page, BOOK)
    await selectParagraph(page)
    await page.locator('#selection-highlight').click()
    await expect.poll(async () => (await marksOn(page, BOOK)).length,
      { timeout: 10000 }).toBe(1)

    await page.reload()
    await openBook(page, BOOK)
    await page.locator('#tab-marks').click()
    await expect(page.locator('.mark-item')).toHaveCount(1, { timeout: 15000 })
  })

  test('removing it removes it everywhere', async ({ page }) => {
    await page.goto('/')
    await openBook(page, BOOK)
    await selectParagraph(page)
    await page.locator('#selection-highlight').click()
    await page.locator('#tab-marks').click()
    await expect(page.locator('.mark-item')).toHaveCount(1, { timeout: 10000 })

    await page.locator('.mark-remove').first().click()
    await expect(page.locator('.mark-item')).toHaveCount(0)
    await expect.poll(async () => (await marksOn(page, BOOK)).length).toBe(0)
  })
})

test.describe('writing a note', () => {
  test('opens a composer rather than a browser prompt', async ({ page }) => {
    // window.prompt blocks the page, cannot be styled, and on iOS looks
    // like the browser asking rather than the app.
    await page.goto('/')
    await openBook(page, BOOK)
    await selectParagraph(page)
    await page.locator('#selection-note').click()
    await expect(page.locator('#note-editor')).toBeVisible()
    await expect(page.locator('#note-text')).toBeFocused()
  })

  test('saves the note with the highlight', async ({ page }) => {
    await page.goto('/')
    await openBook(page, BOOK)
    await selectParagraph(page)
    await page.locator('#selection-note').click()
    await page.locator('#note-text').fill('Orwell opens on a clock.')
    await page.locator('#note-save').click()

    await expect.poll(async () => (await marksOn(page, BOOK))[0]?.note,
      { timeout: 10000 }).toBe('Orwell opens on a clock.')
  })

  test('the note is shown under the words it belongs to', async ({ page }) => {
    await page.goto('/')
    await openBook(page, BOOK)
    await selectParagraph(page)
    await page.locator('#selection-note').click()
    await page.locator('#note-text').fill('Worth coming back to.')
    await page.locator('#note-save').click()

    await page.locator('#tab-marks').click()
    await expect(page.locator('.mark-note')).toHaveText('Worth coming back to.')
  })

  test('cancelling leaves no highlight behind', async ({ page }) => {
    await page.goto('/')
    await openBook(page, BOOK)
    await selectParagraph(page)
    await page.locator('#selection-note').click()
    await page.locator('#note-text').fill('Never mind.')
    await page.locator('#note-cancel').click()

    await expect(page.locator('#note-editor')).toBeHidden()
    expect(await marksOn(page, BOOK)).toHaveLength(0)
  })

  test('a note can be written afterwards, from the panel', async ({ page }) => {
    await page.goto('/')
    await openBook(page, BOOK)
    await selectParagraph(page)
    await page.locator('#selection-highlight').click()
    await page.locator('#tab-marks').click()
    await expect(page.locator('.mark-item')).toHaveCount(1, { timeout: 10000 })

    await page.locator('.mark-annotate').click()
    await page.locator('.mark-editor textarea').fill('Second thoughts.')
    await page.locator('.mark-editor .mark-save').click()

    await expect(page.locator('.mark-note')).toHaveText('Second thoughts.')
    await expect.poll(async () => (await marksOn(page, BOOK))[0]?.note).toBe(
      'Second thoughts.')
  })

  test('an existing note can be changed', async ({ page }) => {
    await page.goto('/')
    await openBook(page, BOOK)
    await selectParagraph(page)
    await page.locator('#selection-note').click()
    await page.locator('#note-text').fill('First thought.')
    await page.locator('#note-save').click()
    await page.locator('#tab-marks').click()
    await expect(page.locator('.mark-note')).toHaveText('First thought.')

    await page.locator('.mark-annotate').click()
    await expect(page.locator('.mark-editor textarea')).toHaveValue('First thought.')
    await page.locator('.mark-editor textarea').fill('Better thought.')
    await page.locator('.mark-editor .mark-save').click()
    await expect(page.locator('.mark-note')).toHaveText('Better thought.')
  })

  test('emptying a note keeps the highlight', async ({ page }) => {
    // The words are still marked; you have only stopped saying why.
    await page.goto('/')
    await openBook(page, BOOK)
    await selectParagraph(page)
    await page.locator('#selection-note').click()
    await page.locator('#note-text').fill('Delete me.')
    await page.locator('#note-save').click()
    await page.locator('#tab-marks').click()

    await page.locator('.mark-annotate').click()
    await page.locator('.mark-editor textarea').fill('')
    await page.locator('.mark-editor .mark-save').click()

    await expect(page.locator('.mark-note')).toHaveCount(0)
    await expect(page.locator('.mark-item')).toHaveCount(1)
  })
})

test.describe('the highlights panel', () => {
  test('says so when there is nothing in it', async ({ page }) => {
    await page.goto('/')
    await openBook(page, BOOK)
    await page.locator('#tab-marks').click()
    await expect(page.locator('#marks-list')).toContainText('Nothing highlighted')
  })

  test('shows the words you marked', async ({ page }) => {
    await page.goto('/')
    await openBook(page, BOOK)
    await selectParagraph(page)
    await page.locator('#selection-highlight').click()
    await page.locator('#tab-marks').click()
    await expect(page.locator('.mark-text').first()).not.toBeEmpty()
  })
})
