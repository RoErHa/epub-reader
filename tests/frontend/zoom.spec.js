import { test, expect } from '@playwright/test'
import { openBook, blobFrame } from './helpers.js'

/* Text size, in every kind of book (§8.31).
 *
 * The control set `font-size` on <body>, which reaches text that sizes
 * itself relative to its parent and nothing else. 36 of the 51 PRCs here
 * size theirs with <font size="3">, and one EPUB in twenty-five with pt
 * or px — both absolute, so the button moved the number and not the
 * words. And a PDF or a comic is a picture of a page: there is no font
 * to size, so it has to be the page that grows.
 */
const SIZED = 'Fixed Sizes.epub'
const PDF = 'Herbert, Frank - A Pdf Book.pdf'

const sizeOf = (page, id) => async () => {
  const frame = blobFrame(page)
  if (!frame) return null
  return frame.evaluate(id => {
    const el = document.getElementById(id)
    return el ? parseFloat(getComputedStyle(el).fontSize) : null
  }, id).catch(() => null)
}

async function bigger (page, times) {
  for (let i = 0; i < times; i++) await page.locator('#font-bigger').click()
}

test.describe('text that sizes itself', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/')
    await openBook(page, SIZED)
    await page.locator('#tab-display').click()
  })

  test('looks as its book meant at 100%', async ({ page }) => {
    // 12pt, 16px and size 3 are all the browser's 16 px.
    for (const id of ['css', 'inline', 'tag']) {
      await expect.poll(sizeOf(page, id)).toBeCloseTo(16, 0)
    }
    await expect.poll(sizeOf(page, 'bigger-tag')).toBeCloseTo(24, 0)
  })

  for (const [id, label] of [['css', 'pixels in a stylesheet'],
    ['inline', 'points in a style attribute'],
    ['tag', 'a font tag'], ['bigger-tag', 'a relative font tag']]) {
    test(`follows the control when sized by ${label}`, async ({ page }) => {
      await expect.poll(sizeOf(page, id)).not.toBeNull()
      const start = await sizeOf(page, id)()
      await bigger(page, 3)                       // 130%
      await expect.poll(sizeOf(page, id)).toBeCloseTo(start * 1.3, 0)
    })
  }
})

test.describe('a page that is a picture', () => {
  const width = page => async () => {
    const frame = blobFrame(page)
    if (!frame) return 0
    const box = await (await frame.frameElement()).boundingBox()
    return box ? Math.round(box.width) : 0
  }

  test.beforeEach(async ({ page }) => {
    await page.goto('/')
    await openBook(page, PDF)
    await page.locator('#tab-display').click()
    await expect.poll(width(page)).toBeGreaterThan(0)
  })

  test('grows and shrinks with the control', async ({ page }) => {
    const fitted = await width(page)()
    await bigger(page, 5)                         // 150%
    await expect.poll(width(page)).toBeGreaterThan(fitted * 1.4)
    for (let i = 0; i < 5; i++) await page.locator('#font-smaller').click()
    await expect.poll(width(page)).toBeLessThan(fitted * 1.05)
  })

  test('can be scrolled to its left edge when larger than the pane',
    async ({ page }) => {
      // One page, as on an iPad in portrait, zoomed past the pane.
      await page.setViewportSize({ width: 810, height: 1080 })
      // On a touch-sized screen the controls live behind Aa.
      await page.locator('#btn-panels').click()
      await bigger(page, 10)                      // 200%
      const reachable = async () => {
        const frame = blobFrame(page)
        if (!frame) return null
        const box = await (await frame.frameElement()).boundingBox()
        const scroll = await page.evaluate(() => {
          const r = document.querySelector('foliate-view')?.renderer
          if (!r) return null
          r.scrollLeft = 0
          return r.scrollWidth
        })
        return box && scroll && { page: box.width, scroll }
      }
      // Centred content wider than its box overflows on BOTH sides, and
      // what spills to the left cannot be scrolled to: the scroll width
      // is then the pane plus only the right half of the overflow. The
      // whole page must be scrollable. (WebKit lays an overflowing page
      // out from the left already; Chromium needed telling.)
      await expect.poll(async () => {
        const m = await reachable()
        return m && m.page > 900 && m.scroll >= m.page - 1
      }, { timeout: 15000 }).toBe(true)
    })
})
