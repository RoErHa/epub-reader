import { test, expect } from '@playwright/test'

/* The review queue: candidates side by side with what they would change,
 * accepted or rejected one at a time or in bulk. This is the screen the
 * design says is where Calibre is powerful but tedious. */

const QUEUE = [
  {
    id: 1, score: 0.72, provider: 'openlibrary',
    parts: { title: 1.0, author: 0.2, year: null },
    book: { id: 10, title: 'Dune', cover: null },
    candidate: { title: 'Dune', authors: ['Someone Else'],
                 first_published: 1999, synopsis: 'From a provider.' },
    changes: [
      { field: 'synopsis', from: null, to: 'From a provider.' },
      { field: 'publisher', from: 'Old', to: 'Chilton' }
    ]
  },
  {
    id: 2, score: 0.64, provider: 'googlebooks',
    parts: { title: 0.9, author: 1.0 },
    book: { id: 11, title: 'Emma', cover: null },
    candidate: { title: 'Emma', authors: ['Jane Austen'] },
    changes: [{ field: 'isbn13', from: null, to: '9780141439587' }]
  }
]

async function stubQueue (page, items = QUEUE) {
  let current = [...items]
  // '*' does not cross a '/' in Playwright globs, so the accept and
  // reject POSTs slipped through to the real server.
  await page.route('**/api/review**', async route => {
    const url = route.request().url()
    if (route.request().method() === 'POST') {
      const id = Number(url.match(/review\/(\d+)\//)?.[1])
      current = current.filter(i => i.id !== id)
      if (url.endsWith('accept-all')) current = []
      return route.fulfill({ json: { ok: true, accepted: items.length } })
    }
    await route.fulfill({ json: { items: current } })
  })
}

const openReview = async page => {
  await page.goto('/')
  await expect(page.locator('#status')).toHaveText('Ready')
  await page.locator('.browse-mode', { hasText: 'Review' }).click()
}

test.describe('review queue', () => {
  test('is offered as a browse mode', async ({ page }) => {
    await stubQueue(page)
    await page.goto('/')
    await expect(page.locator('.browse-mode', { hasText: 'Review' }))
      .toBeVisible()
  })

  test('lists what is waiting, best first', async ({ page }) => {
    await stubQueue(page)
    await openReview(page)
    await expect(page.locator('.review-row')).toHaveCount(2)
    await expect(page.locator('.review-row').first()).toContainText('Dune')
  })

  test('shows the score as a percentage', async ({ page }) => {
    await stubQueue(page)
    await openReview(page)
    await expect(page.locator('.review-row').first()).toContainText('72%')
  })

  test('choosing one shows what would change', async ({ page }) => {
    await stubQueue(page)
    await openReview(page)
    await page.locator('.review-row').first().click()
    await expect(page.locator('.change-row')).toHaveCount(2)
    await expect(page.locator('.change-row').first()).toContainText('synopsis')
    await expect(page.locator('.change-row').first()).toContainText(
      'From a provider.'
    )
  })

  test('shows why it scored what it did', async ({ page }) => {
    // A number without a reason is not reviewable.
    await stubQueue(page)
    await openReview(page)
    await page.locator('.review-row').first().click()
    const reasons = page.locator('#review-parts')
    await expect(reasons).toContainText('title')
    await expect(reasons).toContainText('author')
  })

  test('accepting removes it from the queue', async ({ page }) => {
    await stubQueue(page)
    await openReview(page)
    await page.locator('.review-row').first().click()
    await page.locator('#review-accept').click()
    await expect(page.locator('.review-row')).toHaveCount(1)
  })

  test('rejecting removes it too', async ({ page }) => {
    await stubQueue(page)
    await openReview(page)
    await page.locator('.review-row').first().click()
    await page.locator('#review-reject').click()
    await expect(page.locator('.review-row')).toHaveCount(1)
  })

  test('everything can be accepted at once, without opening one',
    async ({ page }) => {
    // A queue-level action belongs with the queue, not inside an item.
    await stubQueue(page)
    await openReview(page)
    page.once('dialog', d => d.accept())
    await page.locator('#review-accept-all').click()
    await expect(page.locator('#book-list .msg')).toContainText(/Nothing|empty/i)
  })

  test('the accept-all action only appears in the review queue',
    async ({ page }) => {
      await stubQueue(page)
      await page.goto('/')
      await expect(page.locator('#review-accept-all')).toBeHidden()
      await page.locator('.browse-mode', { hasText: 'Review' }).click()
      await expect(page.locator('#review-accept-all')).toBeVisible()
    })

  test('an empty queue says so', async ({ page }) => {
    await stubQueue(page, [])
    await openReview(page)
    await expect(page.locator('#book-list .msg')).toContainText(/Nothing|empty/i)
  })
})
