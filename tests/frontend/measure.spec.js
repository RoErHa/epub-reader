import { test, expect } from '@playwright/test'
import { openBook, blobFrame } from './helpers.js'

/* The words use the width of the pane they are given.
 *
 * foliate's paginator caps a column at 720 px unless told otherwise, so on
 * an iPad in landscape a third of the screen was margin either side of a
 * narrow strip of text. The measure follows the pane now: one column fills
 * it, less a gutter, and two columns split it.
 */

const BOOK = 'left-hand-of-darkness.epub'
const DISPLAY = 'epub-reader.display'

// The width of one column of text, in the book's own frame.
async function columnWidth (page) {
  let width = 0
  await expect.poll(async () => {
    const frame = blobFrame(page)
    if (!frame) return 0
    width = await frame.evaluate(() =>
      parseFloat(getComputedStyle(document.documentElement).columnWidth) || 0
    ).catch(() => 0)
    return width
  }, { timeout: 15000 }).toBeGreaterThan(0)
  return width
}

async function stageWidth (page) {
  return (await page.locator('#reader-stage').boundingBox()).width
}

async function withColumns (page, columns) {
  await page.addInitScript(([key, value]) => {
    localStorage.setItem(key, JSON.stringify({ columns: value }))
  }, [DISPLAY, columns])
}

for (const [name, width, height] of [
  ['an iPad in portrait', 810, 1080],
  ['an iPad in landscape', 1080, 810],
  ['a phone', 393, 852],
  ['a wide Mac window', 1728, 1050]
]) {
  test(`on ${name}, one column fills the pane`, async ({ page }) => {
    await page.setViewportSize({ width, height })
    await page.goto('/')
    await openBook(page, BOOK)
    await expect.poll(async () =>
      (await columnWidth(page)) / (await stageWidth(page))
    ).toBeGreaterThan(0.86)
  })
}

test('turning the iPad takes the text with it', async ({ page }) => {
  await page.setViewportSize({ width: 810, height: 1080 })
  await page.goto('/')
  await openBook(page, BOOK)
  await page.setViewportSize({ width: 1080, height: 810 })
  await expect.poll(async () =>
    (await columnWidth(page)) / (await stageWidth(page))
  ).toBeGreaterThan(0.86)
})

test('two columns split a landscape iPad between them', async ({ page }) => {
  await withColumns(page, 2)
  await page.setViewportSize({ width: 1080, height: 810 })
  await page.goto('/')
  await openBook(page, BOOK)
  await expect.poll(async () =>
    (await columnWidth(page)) / (await stageWidth(page))
  ).toBeGreaterThan(0.4)
  expect((await columnWidth(page)) / (await stageWidth(page))).toBeLessThan(0.5)
})

test('two columns still give portrait one full column', async ({ page }) => {
  // Two columns of 380 points is not a page anyone wants to read, which is
  // why the paginator keeps portrait to one; it must fill the pane too.
  await withColumns(page, 2)
  await page.setViewportSize({ width: 810, height: 1080 })
  await page.goto('/')
  await openBook(page, BOOK)
  await expect.poll(async () =>
    (await columnWidth(page)) / (await stageWidth(page))
  ).toBeGreaterThan(0.86)
})

test('on an iPad in portrait, the gutter is narrow', async ({ page }) => {
  // The gutter is where a thumb rests and nothing more; a quarter inch
  // either side is enough, and every point of it is a point of text lost.
  await page.setViewportSize({ width: 810, height: 1080 })
  await page.goto('/')
  await openBook(page, BOOK)
  const stage = await page.locator('#reader-stage').boundingBox()
  await expect.poll(async () => {
    const frame = blobFrame(page)
    if (!frame) return Infinity
    const left = await frame.evaluate(() => {
      const p = document.querySelector('p')
      return frameElement.getBoundingClientRect().left +
        p.getBoundingClientRect().left
    }).catch(() => Infinity)
    return left - stage.x
  }).toBeLessThan(23)
})

// Where the book's frame sits inside the stage, top and bottom.
async function frameInset (page) {
  const stage = await page.locator('#reader-stage').boundingBox()
  const frame = blobFrame(page)
  if (!frame) return { top: Infinity, bottom: Infinity }
  const box = await frame.evaluate(() => {
    const r = frameElement.getBoundingClientRect()
    return { top: r.top, bottom: r.bottom }
  }).catch(() => null)
  if (!box) return { top: Infinity, bottom: Infinity }
  return { top: box.top - stage.y, bottom: stage.y + stage.height - box.bottom }
}

test.describe('on an iPad in portrait, top and bottom', () => {
  test.use({ viewport: { width: 810, height: 1080 }, hasTouch: true })

  test('the page starts and ends close to the edges', async ({ page }) => {
    await page.goto('/')
    await openBook(page, BOOK)
    await expect.poll(async () => (await frameInset(page)).top).toBeLessThan(28)
    await expect.poll(async () => (await frameInset(page)).bottom).toBeLessThan(28)
  })

  test('a device that saved the old margin gets the new one', async ({ page }) => {
    // Every setting is written back whole, so the iPad that read under
    // the 48 px margin has it stored — a default alone would not reach it.
    await page.addInitScript(key => {
      localStorage.setItem(key, JSON.stringify({ margin: 48 }))
    }, DISPLAY)
    await page.goto('/')
    await openBook(page, BOOK)
    await expect.poll(async () => (await frameInset(page)).top).toBeLessThan(28)
  })

  test('the back arrow sits in the header, clear of the words', async ({ page }) => {
    await page.goto('/')
    await openBook(page, BOOK)
    const back = await page.locator('#reader-back').boundingBox()
    const header = await page.locator('header').boundingBox()
    expect(back.y).toBeGreaterThanOrEqual(header.y - 1)
    expect(back.y + back.height).toBeLessThanOrEqual(header.y + header.height + 1)
    expect(back.width).toBeGreaterThanOrEqual(40)
    // ...and it is not hidden behind the title it shares the bar with.
    const title = await page.locator('.hdr-book').boundingBox()
    expect(back.x + back.width).toBeLessThanOrEqual(title.x)
  })
})
