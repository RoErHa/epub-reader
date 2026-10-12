import { test, expect } from '@playwright/test'

/* Browsing by language. The codes come from the server; the names are
 * the browser's own (Intl.DisplayNames), so nobody keeps a table of them.
 *
 * `het-stille-boek.epub` says nothing about its language (`und`), so it
 * is Dutch here only because the startup scan read its words — the whole
 * pass, end to end. `edit-me.epub` is the editor's (§10.2).
 */

const DUTCH = 'het-stille-boek.epub'

async function languages (page) {
  await page.goto('/')
  await expect(page.locator('#status')).toHaveText('Ready')
  await page.locator('.browse-mode', { hasText: 'Languages' }).click()
  await expect(page.locator('.group-row').first()).toBeVisible()
}

test('languages are listed by name, with what nobody could place last',
  async ({ page }) => {
    await languages(page)
    const names = await page.locator('.group-row .group-name').allTextContents()
    expect(names).toContain('English')
    expect(names).toContain('Dutch')
    expect(names.at(-1)).toBe('Unknown')
    expect(names).not.toContain('en')
  })

test('a book that never said its language is found by its words',
  async ({ page }) => {
    await languages(page)
    await page.locator('.group-row', { hasText: 'Dutch' }).click()
    await expect(page.locator(`.book-row[data-name="${DUTCH}"]`)).toBeVisible()
    await expect(page.locator('.book-row[data-name="left-hand-of-darkness.epub"]'))
      .toHaveCount(0)
  })

test('the comics are under Unknown', async ({ page }) => {
  await languages(page)
  await page.locator('.group-row', { hasText: 'Unknown' }).click()
  await expect(page.locator('.book-row', { hasText: 'Zootopia' })).toBeVisible()
})

test('a book\'s detail names its language rather than coding it',
  async ({ page }) => {
    await page.goto('/')
    await page.locator(`.book-row[data-name="${DUTCH}"]`).click()
    await expect(page.locator('#detail-facts')).toContainText('Dutch')
    await expect(page.locator('#detail-facts')).not.toContainText(' nl ')
  })

test('the language can be set by hand', async ({ page }) => {
  await page.goto('/')
  await page.locator('.book-row[data-name="edit-me.epub"]').click()
  await page.locator('#detail-edit').click()
  const select = page.locator('#edit-language')
  await expect(select).toBeVisible()
  // What these tests write is locked and outlives the run, so move to
  // whichever of two languages it is not already.
  const now = await select.inputValue()
  const want = now === 'fr' ? 'de' : 'fr'
  await select.selectOption(want)
  const sent = page.waitForRequest(r => r.method() === 'PATCH')
  await page.locator('#edit-save').click()
  expect(Object.keys((await sent).postDataJSON())).toEqual(['language'])
  await expect(page.locator('#detail-facts'))
    .toContainText(want === 'fr' ? 'French' : 'German')
})
