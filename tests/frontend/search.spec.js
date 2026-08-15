import { test, expect } from '@playwright/test'

const ready = async page => {
  await page.goto('/')
  await expect(page.locator('#status')).toHaveText('Ready')
}

const search = async (page, q) => {
  await page.locator('#search-input').fill(q)
  await page.locator('#search-input').press('Enter')
}

test.describe('search', () => {
  test('finds a book by title', async ({ page }) => {
    await ready(page)
    await search(page, 'darkness')
    await expect(page.locator('.book-row')).toHaveCount(1)
    await expect(page.locator('.book-name')).toHaveText(
      'The Left Hand of Darkness'
    )
  })

  test('finds books by author', async ({ page }) => {
    await ready(page)
    await search(page, 'le guin')
    await expect(page.locator('.result-group', { hasText: 'Authors' }))
      .toBeVisible()
    await expect(page.locator('.group-row', { hasText: 'Ursula K. Le Guin' }))
      .toBeVisible()
  })

  test('offers a matching series', async ({ page }) => {
    await ready(page)
    await search(page, 'hainish')
    await expect(page.locator('.group-row', { hasText: 'Hainish Cycle' }))
      .toBeVisible()
  })

  test('clicking a matched author drills into their books', async ({ page }) => {
    await ready(page)
    await search(page, 'le guin')
    await page.locator('.group-row', { hasText: 'Ursula K. Le Guin' }).click()
    await expect(page.locator('#browse-back-title')).toHaveText(
      'Ursula K. Le Guin'
    )
    await expect(page.locator('.book-row')).toHaveCount(2)
  })

  test('a query with no matches says so', async ({ page }) => {
    await ready(page)
    await search(page, 'zzzznothing')
    await expect(page.locator('#book-list .msg')).toContainText('Nothing found')
  })

  test('punctuation does not break it', async ({ page }) => {
    await ready(page)
    for (const q of ['"', 'AND', '*', 'a OR b', "hitchhiker's"]) {
      await search(page, q)
      await expect(page.locator('#book-list')).toBeVisible()
    }
  })

  test('clearing the box returns to browsing', async ({ page }) => {
    await ready(page)
    const before = await page.locator('.book-row').count()
    await search(page, 'darkness')
    await expect(page.locator('.book-row')).toHaveCount(1)
    await page.locator('#search-input').fill('')
    await page.locator('#search-input').press('Enter')
    await expect(page.locator('.book-row')).toHaveCount(before)
  })

  test('a search result opens in the reader', async ({ page }) => {
    await ready(page)
    await search(page, 'darkness')
    await page.locator('.book-row').first().click()
    await expect(page.locator('#reader-stage')).toHaveAttribute(
      'data-state', 'open', { timeout: 15000 }
    )
  })

  test('searching hides the letter bar', async ({ page }) => {
    await ready(page)
    await expect(page.locator('#letter-bar')).toBeVisible()
    await search(page, 'darkness')
    await expect(page.locator('#letter-bar')).toBeHidden()
  })
})
