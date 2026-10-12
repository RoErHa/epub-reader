import { test, expect } from '@playwright/test'

/* A synopsis is HTML more often than not — 214 of the 356 the real library
 * carries, because that is what an OPF's dc:description and a MOBI's EXTH
 * description hold. Shown as text it read as a page of tags. It is shown
 * as what it is now, through a closed list of elements and no attributes
 * at all: a blurb is somebody else's markup, and only its shape is wanted.
 */

const BOOK = 'left-hand-of-darkness.epub'

async function chooseWithSynopsis (page, synopsis) {
  await page.route('**/api/books/*/detail*', async route => {
    const response = await route.fetch()
    const data = await response.json()
    data.book = { ...data.book, synopsis }
    await route.fulfill({ response, json: data })
  })
  await page.goto('/')
  await expect(page.locator('#status')).toHaveText('Ready')
  await page.locator(`.book-row[data-name="${BOOK}"]`).click()
  await expect(page.locator('#detail')).toBeVisible()
}

const panel = page => page.locator('#detail-synopsis')

test('a synopsis written in HTML is shown as HTML', async ({ page }) => {
  await chooseWithSynopsis(page,
    '<div><h3>From Publishers Weekly</h3><p>Twin <em>siblings</em> take ' +
    'summer jobs.<br>Then <strong>everything</strong> changes.</p></div>')
  await expect(panel(page)).not.toContainText('<p>')
  await expect(panel(page)).not.toContainText('<em>')
  await expect(panel(page).locator('p')).toHaveCount(1)
  await expect(panel(page).locator('em')).toHaveText('siblings')
  await expect(panel(page).locator('strong')).toHaveText('everything')
  await expect(panel(page).locator('br')).toHaveCount(1)
  await expect(panel(page)).toContainText('From Publishers Weekly')
})

test('a plain synopsis is still plain, line breaks and all', async ({ page }) => {
  await chooseWithSynopsis(page, 'One paragraph.\n\nAnd a second, 5 < 6.')
  await expect(panel(page)).toContainText('5 < 6')
  const shape = await panel(page).evaluate(el => getComputedStyle(el).whiteSpace)
  expect(shape).toBe('pre-line')
})

test('character references are read, not shown', async ({ page }) => {
  await chooseWithSynopsis(page, '<p>Fish &amp; chips &mdash; &quot;quoted&quot;</p>')
  await expect(panel(page)).toHaveText('Fish & chips — "quoted"')
})

test('nothing in a synopsis runs, loads or links', async ({ page }) => {
  // A protection is only proved by a test that fails when it is removed.
  const loads = []
  page.on('request', r => { if (r.url().includes('evil')) loads.push(r.url()) })
  await chooseWithSynopsis(page,
    '<p onclick="window.__clicked=1" style="color:red" class="x">Safe ' +
    '<a href="https://evil.example/">words</a></p>' +
    '<img src="https://evil.example/pixel.png" onerror="window.__ran=1">' +
    '<script>window.__ran=1</script><style>body{display:none}</style>' +
    '<iframe src="https://evil.example/"></iframe>')
  await expect(panel(page)).toContainText('Safe words')
  const html = await panel(page).evaluate(el => el.innerHTML)
  for (const banned of ['onclick', 'style', 'class=', '<a', '<img', '<script',
    '<iframe', 'evil', 'display:none']) {
    expect(html).not.toContain(banned)
  }
  await panel(page).locator('p').click()
  expect(await page.evaluate(() => window.__ran ?? window.__clicked ?? null)).toBeNull()
  expect(loads).toEqual([])
})

test('the scanner\'s private tags keep their words and lose themselves',
  async ({ page }) => {
    await chooseWithSynopsis(page,
      '<p><font face="Arial">Kept</font> <st1:place>Rome</st1:place> ' +
      '<span lang="en">too</span></p>')
    await expect(panel(page)).toHaveText('Kept Rome too')
    const html = await panel(page).evaluate(el => el.innerHTML)
    expect(html).not.toMatch(/font|st1|span/)
  })
