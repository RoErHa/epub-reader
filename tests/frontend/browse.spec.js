import { test, expect } from '@playwright/test'

/* Browse mirrors dlna-gateway: a mode strip, a letter bar, and
 * drill-down with a back link. */

const ready = async page => {
  await page.goto('/')
  await expect(page.locator('#status')).toHaveText('Ready')
}

test.describe('browse modes', () => {
  test('titles is the default mode', async ({ page }) => {
    await ready(page)
    await expect(page.locator('.browse-mode.active')).toHaveText('Titles')
    await expect(page.locator('.book-row').first()).toBeVisible()
  })

  test('every mode is offered', async ({ page }) => {
    await ready(page)
    const modes = await page.locator('.browse-mode').allTextContents()
    expect(modes).toEqual(
      ['Titles', 'Authors', 'Series', 'Genres', 'Years', 'Shelves', 'Review'])
  })

  test('authors mode lists authors with counts', async ({ page }) => {
    await ready(page)
    await page.locator('.browse-mode', { hasText: 'Authors' }).click()
    const row = page.locator('.group-row', { hasText: 'Ursula K. Le Guin' })
    await expect(row).toBeVisible()
    await expect(row.locator('.group-count')).toHaveText('2')
  })

  test('drilling into an author shows their books', async ({ page }) => {
    await ready(page)
    await page.locator('.browse-mode', { hasText: 'Authors' }).click()
    await page.locator('.group-row', { hasText: 'Ursula K. Le Guin' }).click()
    await expect(page.locator('#browse-back')).toBeVisible()
    await expect(page.locator('#browse-back-title')).toHaveText('Ursula K. Le Guin')
    await expect(page.locator('.book-row')).toHaveCount(2)
  })

  test('back returns to the list of authors', async ({ page }) => {
    await ready(page)
    await page.locator('.browse-mode', { hasText: 'Authors' }).click()
    await page.locator('.group-row').first().click()
    await page.locator('#browse-back .back-link').click()
    await expect(page.locator('.group-row').first()).toBeVisible()
    await expect(page.locator('#browse-back')).toBeHidden()
  })

  test('series mode lists series', async ({ page }) => {
    await ready(page)
    await page.locator('.browse-mode', { hasText: 'Series' }).click()
    await expect(
      page.locator('.group-row', { hasText: 'Hainish Cycle' })
    ).toBeVisible()
  })

  test('genres mode lists genres', async ({ page }) => {
    await ready(page)
    await page.locator('.browse-mode', { hasText: 'Genres' }).click()
    await expect(
      page.locator('.group-row', { hasText: 'Science Fiction' })
    ).toBeVisible()
  })

  test('years mode lists decades', async ({ page }) => {
    await ready(page)
    await page.locator('.browse-mode', { hasText: 'Years' }).click()
    await expect(page.locator('.group-row', { hasText: '1960s' })).toBeVisible()
  })
})

test.describe('letter bar', () => {
  test('offers only letters that have books', async ({ page }) => {
    await ready(page)
    const letters = await page.locator('.letter').allTextContents()
    expect(letters).toContain('L')          // Left Hand of Darkness
    expect(letters).not.toContain('Z')
  })

  test('filters the list', async ({ page }) => {
    await ready(page)
    await page.locator('.letter', { hasText: 'L' }).click()
    // The list re-renders asynchronously, so wait for it to settle
    // rather than reading it mid-flight.
    await expect.poll(async () => {
      const titles = await page.locator('.book-name').allTextContents()
      return titles.length > 0 &&
        titles.every(t => t.toLowerCase().includes('left hand'))
    }, { timeout: 10000 }).toBe(true)
  })

  test('the chosen letter is marked', async ({ page }) => {
    await ready(page)
    await page.locator('.letter', { hasText: 'L' }).click()
    await expect(page.locator('.letter.active')).toHaveText('L')
  })

  test('clicking it again clears the filter', async ({ page }) => {
    await ready(page)
    const all = await page.locator('.book-row').count()
    await page.locator('.letter', { hasText: 'L' }).click()
    await page.locator('.letter.active').click()
    await expect(page.locator('.book-row')).toHaveCount(all)
  })
})

test.describe('book rows', () => {
  test('show title, author and cover', async ({ page }) => {
    await ready(page)
    const row = page.locator('.book-row', { hasText: 'The Left Hand of Darkness' })
    await expect(row.locator('.book-name')).toHaveText('The Left Hand of Darkness')
    await expect(row.locator('.book-author')).toHaveText('Ursula K. Le Guin')
    await expect(row.locator('.book-cover')).toHaveAttribute('src', /\/api\/covers\//)
  })

  test('opening a book from browse still works', async ({ page }) => {
    await ready(page)
    await page.locator('.book-row', { hasText: 'The Left Hand of Darkness' }).click()
    // Choosing shows the book; Read opens it.
    await expect(page.locator('#detail-title')).toHaveText(
      'The Left Hand of Darkness'
    )
    await page.locator('#detail-read').click()
    await expect(page.locator('#reader-stage')).toHaveAttribute(
      'data-state', 'open', { timeout: 20000 }
    )
  })

  test('a book with no cover still renders', async ({ page }) => {
    await ready(page)
    await expect(
      page.locator('.book-row', { hasText: 'Untouched' })
    ).toBeVisible()
  })
})

test.describe('status bar', () => {
  test('reports what is indexed', async ({ page }) => {
    await ready(page)
    await expect(page.locator('#statusbar')).toContainText('books')
  })
})
