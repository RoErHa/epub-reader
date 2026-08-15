import { test, expect } from '@playwright/test'

const BOOK = 'left-hand-of-darkness.epub'

const choose = async (page, name = BOOK) => {
  await page.goto('/')
  await expect(page.locator('#status')).toHaveText('Ready')
  await page.locator(`.book-row[data-name="${name}"]`).click()
  await expect(page.locator('#detail')).toBeVisible()
}

/** Answer the detail endpoint with an enriched author, so the panels can
 *  be tested without reaching Open Library. */
async function stubEnriched (page, { works = [], hidden = 0 } = {}) {
  await page.route('**/api/books/*/detail*', async route => {
    const response = await route.fetch()
    const data = await response.json()
    data.author = {
      ...data.author,
      bio: 'American author of speculative fiction.',
      born: '21 October 1929',
      died: '22 January 2018',
      nationality: 'American',
      enriched: true
    }
    const showAll = new URL(route.request().url()).searchParams.get('show_all')
    data.also_published = showAll === 'true'
      ? [...works, ...Array.from({ length: hidden }, (_, i) => ({
          title: `Hidden Collection ${i + 1}`, first_published: 1975,
          series: null, series_index: null, work_type: 'collection',
          owned: false, book_id: null
        }))]
      : works
    data.hidden_works = hidden
    await route.fulfill({ json: data })
  })
}

test.describe('about the author', () => {
  test('offers to look the author up when nothing is known', async ({ page }) => {
    await choose(page)
    await expect(page.locator('#detail-lookup')).toBeVisible()
  })

  test('looking up asks the server and reports back', async ({ page }) => {
    let asked = null
    await page.route('**/api/authors/*/enrich', async route => {
      asked = route.request().url()
      await route.fulfill({ json: { matched: true, works: 12, bio: true } })
    })
    await choose(page)
    await page.locator('#detail-lookup').click()
    await expect.poll(() => asked).toContain('/enrich')
    await expect(page.locator('#detail-author-panel')).toContainText(
      /12 works|Found/i
    )
  })

  test('says so plainly when the author cannot be found', async ({ page }) => {
    await page.route('**/api/authors/*/enrich', route =>
      route.fulfill({ json: { matched: false, works: 0, reason: 'Not on Open Library.' } }))
    await choose(page)
    await page.locator('#detail-lookup').click()
    await expect(page.locator('#detail-author-panel')).toContainText(
      'Not on Open Library.'
    )
  })

  test('a bio is shown once there is one', async ({ page }) => {
    await stubEnriched(page)
    await choose(page)
    await expect(page.locator('#detail-bio')).toContainText('speculative fiction')
    await expect(page.locator('#detail-bio')).toContainText('1929')
    await expect(page.locator('#detail-lookup')).toBeHidden()
  })
})

test.describe('also published', () => {
  const WORKS = [
    { title: 'The Dispossessed', first_published: 1974, series: 'Hainish Cycle',
      series_index: 6, work_type: 'novel', owned: false, book_id: null },
    { title: 'A Wizard of Earthsea', first_published: 1968, series: null,
      series_index: null, work_type: 'novel', owned: true, book_id: 2 }
  ]

  test('shows works you do not own, marked as such', async ({ page }) => {
    await stubEnriched(page, { works: WORKS })
    await choose(page)
    const row = page.locator('.also-row', { hasText: 'The Dispossessed' })
    await expect(row).toBeVisible()
    await expect(row).not.toHaveClass(/owned/)
    await expect(row).toContainText('not owned')
  })

  test('owned works are still marked and clickable', async ({ page }) => {
    await stubEnriched(page, { works: WORKS })
    await choose(page)
    await expect(page.locator('.also-row', { hasText: 'A Wizard of Earthsea' }))
      .toHaveClass(/owned/)
  })

  test('show all reveals what the filter hid', async ({ page }) => {
    await stubEnriched(page, { works: WORKS, hidden: 3 })
    await choose(page)
    await expect(page.locator('#detail-also')).not.toContainText('Hidden Collection')
    await page.locator('#detail-showall').click()
    await expect(page.locator('#detail-also')).toContainText('Hidden Collection 1')
  })

  test('the default view says it is filtered', async ({ page }) => {
    await stubEnriched(page, { works: WORKS, hidden: 3 })
    await choose(page)
    await expect(page.locator('#detail-showall')).toContainText(
      /novels|show all/i
    )
  })
})
