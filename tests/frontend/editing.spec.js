import { test, expect } from '@playwright/test'

/* Editing a book's whole record by hand (§12.3).
 *
 * `edit-me.epub` is reserved: what a person says is locked and outlives
 * every rescan, so whatever these tests write is still there next run —
 * and the suite runs once per engine against one database. Every value
 * written is therefore unique to the test that wrote it, and nothing
 * asserts on what a previous run left behind. */

const BOOK = 'edit-me.epub'
const unique = (what) => `${what} ${Date.now()}${Math.floor(Math.random() * 1e4)}`

const choose = async (page) => {
  await page.goto('/')
  await expect(page.locator('#status')).toHaveText('Ready')
  await page.locator(`.book-row[data-name="${BOOK}"]`).click()
  await expect(page.locator('#detail')).toBeVisible()
}

const edit = async (page) => {
  await page.locator('#detail-edit').click()
  await expect(page.locator('#editor')).toBeVisible()
}

test.describe('editing the record', () => {
  test('the form opens holding what the book says now', async ({ page }) => {
    await choose(page)
    const title = await page.locator('#detail-title').textContent()
    await edit(page)
    await expect(page.locator('#edit-title')).toHaveValue(title)
    await expect(page.locator('#edit-authors')).toHaveValue(/\S/)
  })

  test('a saved record is what the detail shows, and stays',
    async ({ page }) => {
      const title = unique('Edit Me, edited')
      const synopsis = unique('A blurb nobody wrote but me')
      await choose(page)
      await edit(page)
      await page.locator('#edit-title').fill(title)
      await page.locator('#edit-synopsis').fill(synopsis)
      await page.locator('#edit-genres').fill('Thriller, Martial Arts')
      await page.locator('#edit-year').fill('1975')
      await page.locator('#edit-save').click()

      await expect(page.locator('#editor')).toBeHidden()
      await expect(page.locator('#detail-title')).toHaveText(title)
      await expect(page.locator('#detail-synopsis')).toHaveText(synopsis)
      await expect(page.locator('#detail-facts')).toContainText('Thriller')
      await expect(page.locator('#detail-facts')).toContainText('1975')

      // A server-side fact, not a screen that forgot to redraw.
      await choose(page)
      await expect(page.locator('#detail-title')).toHaveText(title)
    })

  test('the list follows a new title', async ({ page }) => {
    const title = unique('Edit Me, listed')
    await choose(page)
    await edit(page)
    await page.locator('#edit-title').fill(title)
    await page.locator('#edit-save').click()
    await expect(page.locator(`.book-row[data-name="${BOOK}"]`))
      .toContainText(title)
  })

  test('a field you changed is marked as yours', async ({ page }) => {
    await choose(page)
    await edit(page)
    await page.locator('#edit-synopsis').fill(unique('Mine'))
    await page.locator('#edit-save').click()
    await expect(page.locator('#editor')).toBeHidden()
    await edit(page)
    await expect(page.locator('label[for="edit-synopsis"]'))
      .toHaveClass(/yours/)
  })

  test('only what changed is sent', async ({ page }) => {
    await choose(page)
    await edit(page)
    const sent = page.waitForRequest(r =>
      r.method() === 'PATCH' && r.url().endsWith('/metadata'))
    await page.locator('#edit-synopsis').fill(unique('Only this'))
    await page.locator('#edit-save').click()
    // An untouched field is not a decision, and sending it would lock
    // the file's typo in for ever.
    expect(Object.keys((await sent).postDataJSON())).toEqual(['synopsis'])
  })

  test('cancel changes nothing', async ({ page }) => {
    await choose(page)
    const title = await page.locator('#detail-title').textContent()
    await edit(page)
    await page.locator('#edit-title').fill('Never saved')
    await page.locator('#edit-cancel').click()
    await expect(page.locator('#editor')).toBeHidden()
    await expect(page.locator('#detail-title')).toHaveText(title)
  })

  test('Escape gives up too', async ({ page }) => {
    await choose(page)
    await edit(page)
    await page.locator('#edit-title').press('Escape')
    await expect(page.locator('#editor')).toBeHidden()
  })

  test('a value the server refuses says why and keeps the form',
    async ({ page }) => {
      await choose(page)
      await edit(page)
      await page.locator('#edit-year').fill('19755')
      await page.locator('#edit-save').click()
      await expect(page.locator('#edit-note')).toContainText('not a year')
      await expect(page.locator('#editor')).toBeVisible()
    })

  test('an empty title is refused before it is sent', async ({ page }) => {
    await choose(page)
    await edit(page)
    await page.locator('#edit-title').fill('   ')
    await page.locator('#edit-save').click()
    await expect(page.locator('#edit-note')).toContainText('title')
    await expect(page.locator('#editor')).toBeVisible()
  })
})
