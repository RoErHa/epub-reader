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

export async function openBook (page, name) {
  await page.locator(`.book-row[data-name="${name}"]`).click()
  await expect(page.locator('#reader-stage')).toHaveAttribute(
    'data-state', 'open', { timeout: 15000 }
  )
  await waitForBookFrame(page)
}
