import { expect } from '@playwright/test'

/* foliate-view attaches a CLOSED shadow root, so the book's iframe cannot
 * be reached with a CSS selector — `frameLocator('foliate-view iframe')`
 * never matches. Book sections are served as blob: URLs, so we find the
 * frame through page.frames().
 *
 * Crucially, foliate REPLACES that iframe when moving between sections,
 * which detaches any Frame object captured earlier. Every helper here
 * therefore re-resolves the frame on each poll rather than holding a
 * reference.
 */

export const blobFrame = page =>
  page.frames().find(f => f.url().startsWith('blob:'))

export async function waitForBookFrame (page, timeout = 15000) {
  await expect.poll(() => Boolean(blobFrame(page)), { timeout }).toBe(true)
  return blobFrame(page)
}

/** Assert the currently rendered section contains some text. */
export async function expectBookText (page, text, timeout = 15000) {
  await expect.poll(async () => {
    const frame = blobFrame(page)
    if (!frame) return ''
    return frame.evaluate(() => document.body?.innerText ?? '').catch(() => '')
  }, { timeout }).toContain(text)
}

/** Read a computed style off the book's body, resolving the frame fresh. */
export async function bookBodyStyle (page, property) {
  const frame = blobFrame(page)
  if (!frame) return null
  return frame
    .evaluate(p => getComputedStyle(document.body)[p], property)
    .catch(() => null)
}

/** Books are addressed by index id; the DOM still carries the path. */
export async function bookId (page, name) {
  const res = await page.request.get('/api/books?limit=500')
  const { books } = await res.json()
  const book = books.find(b => b.path === name || b.name === name)
  if (!book) throw new Error(`no such book in the index: ${name}`)
  return book.id
}

/** Choose a book — shows its detail page, without opening it. */
export async function chooseBook (page, name) {
  await page.locator(`.book-row[data-name="${name}"]`).click()
  await expect(page.locator('#detail-read')).toBeVisible({ timeout: 15000 })
}

/** Choose a book and start reading it.
 *
 * Choosing shows the book's details; reading begins when you ask for it,
 * so this does both — the two steps a reader actually takes.
 */
export async function openBook (page, name) {
  await page.locator(`.book-row[data-name="${name}"]`).click()
  await expect(page.locator('#detail-read')).toBeVisible({ timeout: 15000 })
  await page.locator('#detail-read').click()
  await expect(page.locator('#reader-stage')).toHaveAttribute(
    'data-state', 'open', { timeout: 20000 }
  )
  await waitForBookFrame(page)
}
