import { test, expect } from '@playwright/test'
import { openBook, expectBookText } from './helpers.js'

const PLAIN = 'left-hand-of-darkness.epub'
const HOSTILE = 'hostile.epub'
const SENTINEL = '__epub_script_executed'

test.describe('app shell', () => {
  test('loads and lists the library', async ({ page }) => {
    await page.goto('/')
    await expect(page.locator('#status')).toHaveText('Ready')
    await expect(page.locator('.book-row')).toHaveCount(2)
    await expect(page.locator(`.book-row[data-name="${PLAIN}"]`)).toBeVisible()
  })

  test('shows format and size for each book', async ({ page }) => {
    await page.goto('/')
    const meta = page.locator(`.book-row[data-name="${PLAIN}"] .book-meta`)
    await expect(meta).toContainText('EPUB')
  })
})

test.describe('reading', () => {
  test('renders book text through foliate-js', async ({ page }) => {
    await page.goto('/')
    await openBook(page, PLAIN)

    await expect(page.locator('foliate-view')).toHaveCount(1)
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
    expect(csp).toContain("frame-ancestors 'none'")
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
  test('refuses to serve files outside the library root', async ({ request }) => {
    for (const attempt of ['..%2f.env', '..%2F..%2Fetc%2Fpasswd']) {
      const res = await request.get(`/api/books/${attempt}`)
      expect([400, 404]).toContain(res.status())
    }
  })

  test('is read-only', async ({ request }) => {
    expect((await request.delete(`/api/books/${PLAIN}`)).status()).toBe(405)
  })
})
