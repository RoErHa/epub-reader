import { test, expect } from '@playwright/test'
import { openBook, expectBookText } from './helpers.js'

const PLAIN = 'left-hand-of-darkness.epub'
const HOSTILE = 'hostile.epub'
const SENTINEL = '__epub_script_executed'

test.describe('app shell', () => {
  test('loads and lists the library', async ({ page }) => {
    await page.goto('/')
    await expect(page.locator('#status')).toHaveText('Ready')
    await expect(page.locator('.book-row')).toHaveCount(24)
    await expect(page.locator(`.book-row[data-name="${PLAIN}"]`)).toBeVisible()
  })

  test('shows the author and series for each book', async ({ page }) => {
    // The browse row shows what helps you choose a book — author,
    // series and year — rather than format and file size.
    await page.goto('/')
    const row = page.locator(`.book-row[data-name="${PLAIN}"]`)
    await expect(row.locator('.book-author')).toHaveText('Ursula K. Le Guin')
    await expect(row.locator('.book-meta')).toContainText('Hainish Cycle')
  })
})

test.describe('reading', () => {
  test('renders book text through foliate-js', async ({ page }) => {
    await page.goto('/')
    await openBook(page, PLAIN)

    await expect(page.locator('foliate-view')).toHaveCount(1)
    // Like a real book, this fixture opens on an image-only cover, so
    // step into the first chapter before expecting prose.
    await page.locator('#tab-toc').click()
    await page.locator('.toc-item', { hasText: 'Chapter One' }).click()
    await expectBookText(page, 'quick brown fox')
  })

  test('reads metadata out of the package document', async ({ page }) => {
    await page.goto('/')
    await openBook(page, PLAIN)

    await expect(page.locator('#book-title')).toHaveText(
      'The Left Hand of Darkness'
    )
    await expect(page.locator('#book-author')).toHaveText('Ursula K. Le Guin')
  })

  test('reports reading progress', async ({ page }) => {
    await page.goto('/')
    await openBook(page, PLAIN)
    await expect(page.locator('#progress')).toContainText('%')
  })

  test('pages forward and back', async ({ page }) => {
    await page.goto('/')
    await openBook(page, PLAIN)
    await page.locator('#btn-next').click()
    await page.locator('#btn-prev').click()
    // Still rendering after navigating — no crash, no blank view.
    await expect(page.locator('foliate-view')).toHaveCount(1)
  })

  test('opens without console errors', async ({ page }) => {
    const errors = []
    page.on('pageerror', e => errors.push(e.message))
    await page.goto('/')
    await openBook(page, PLAIN)
    expect(errors).toEqual([])
  })
})

test.describe('security', () => {
  test('sends an enforcing CSP that pins scripts to self', async ({ page }) => {
    const response = await page.goto('/')
    const csp = response.headers()['content-security-policy']
    expect(csp).toBeTruthy()
    expect(csp).toContain("script-src 'self'")
    expect(csp).not.toContain('unsafe-inline\'; script')
    expect(csp).toContain("object-src 'none'")
    // 'self', not 'none' — WebKit propagates this into the book's blob:
    // iframe, and 'none' stops books loading in Safari altogether.
    expect(csp).toContain("frame-ancestors 'self'")
    expect(response.headers()['x-frame-options']).toBe('DENY')
  })

  test('does NOT execute scripts embedded in an EPUB', async ({ page }) => {
    await page.goto('/')
    await openBook(page, HOSTILE)

    // The hostile fixture carries both an inline and an external script,
    // each setting a global. If CSP holds, neither ever runs — checked in
    // every frame, since book sections render in their own blob: iframe.
    for (const frame of page.frames()) {
      const fired = await frame.evaluate(
        name => ({
          inline: typeof window[name],
          external: typeof window[`${name}_external`]
        }),
        SENTINEL
      ).catch(() => null)
      if (fired === null) continue // frame detached mid-check
      expect(fired.inline, `inline script ran in ${frame.url()}`).toBe('undefined')
      expect(fired.external, `external script ran in ${frame.url()}`).toBe('undefined')
    }
  })

  test('still renders the hostile book\'s text', async ({ page }) => {
    // Containment must not mean breakage: the prose still has to appear.
    await page.goto('/')
    await openBook(page, HOSTILE)
    await expectBookText(page, 'quick brown fox')
  })
})

test.describe('library API', () => {
  test('refuses anything that is not a known book id', async ({ request }) => {
    for (const attempt of ['..%2f.env', '999999', 'abc']) {
      const res = await request.get(`/api/books/${attempt}/file`)
      expect([400, 404, 422]).toContain(res.status())
    }
  })

  test('is read-only', async ({ page, request }) => {
    await page.goto('/')
    const { books } = await (await request.get('/api/books')).json()
    expect((await request.delete(`/api/books/${books[0].id}/file`)).status())
      .toBe(405)
  })
})
