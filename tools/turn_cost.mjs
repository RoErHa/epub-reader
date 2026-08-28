/* What a page turn actually costs, measured rather than assumed.
 *
 *     node tools/turn_cost.mjs http://127.0.0.1:8771 Cyclops.epub
 *
 * §3 says the paginator uses CSS multi-column and warns about its
 * slowness, which made page turns the obvious suspect when the iPad's
 * battery came up. They are not: this reports 5 ms of latency and ~15 ms
 * of CPU for a turn, and 1 ms per ten seconds for a book sitting open.
 * The number worth watching is the middle column — `getBoundingClientRect`
 * calls per turn — because foliate's `getVisibleRange` walks the section
 * on every relocate and a multi-column strip prunes almost nothing, so
 * the cost is O(elements in the section) rather than O(what is on screen).
 *
 * > **It must be pointed at a throwaway instance, and it refuses the
 * > live one.** Opening a book writes reading position, server-side and
 * > shared by design (decision 7) — so measuring against the service you
 * > read on moves your place in whatever it opens. That is not a
 * > hypothetical: it cost a real position and three invented "Reading
 * > now" rows, recovered only because the pre-change pages were still in
 * > the main database file with the new ones in the WAL. §10.2 says the
 * > same thing about the UI suite; this is the same rule for a tool.
 *
 * Stand one up with its own LIBRARY_ROOT, DB_PATH and port:
 *
 *     ./run.sh --env /tmp/turn.env start
 */
import { chromium } from 'playwright'

const BASE = process.argv[2] ?? 'http://127.0.0.1:8771'
const TITLE = process.argv[3] ?? 'Cyclops.epub'
if (new URL(BASE).port === '8770') {
  console.error('Refusing to measure the live service: opening a book would\n' +
    'move your reading position. Point this at a throwaway instance.')
  process.exit(2)
}

const blob = page => page.frames().find(f => f.url().startsWith('blob:'))

/** Count every rect measurement the engine makes inside the book. */
const instrument = () => {
  window.__stats = { calls: 0, ms: 0 }
  for (const [proto, name] of [
    [Element.prototype, 'getBoundingClientRect'], [Range.prototype, 'getBoundingClientRect'],
    [Element.prototype, 'getClientRects'], [Range.prototype, 'getClientRects'],
  ]) {
    const original = proto[name]
    proto[name] = function (...args) {
      const t = performance.now()
      const out = original.apply(this, args)
      window.__stats.calls++; window.__stats.ms += performance.now() - t
      return out
    }
  }
  window.__elements = document.body.getElementsByTagName('*').length
  window.__chars = (document.body.innerText || '').length
}

const browser = await chromium.launch()
const page = await (await browser.newContext({
  viewport: { width: 1280, height: 900 } })).newPage()
await page.goto(BASE, { waitUntil: 'domcontentloaded' })
await page.locator(`.book-row[data-name="${TITLE}"]`).click()
await page.locator('#detail-read').click()
await page.waitForTimeout(6000)
// Past the front matter: a cover is a tiny section, and turning there
// only swaps the iframe.
for (let i = 0; i < 6; i++) { await page.keyboard.press('ArrowRight'); await page.waitForTimeout(700) }

await page.evaluate(() => {
  window.__turn = { t0: 0, ms: [] }
  document.querySelector('foliate-view').addEventListener('relocate', () => {
    if (window.__turn.t0) window.__turn.ms.push(performance.now() - window.__turn.t0)
    window.__turn.t0 = 0
  })
  document.addEventListener('keydown', () => { window.__turn.t0 = performance.now() }, true)
})

const perTurn = []
for (let i = 0; i < 10; i++) {
  const frame = blob(page)
  if (frame) await frame.evaluate(instrument).catch(() => {})
  await page.keyboard.press('ArrowRight')
  await page.waitForTimeout(600)
  const stats = await (blob(page)?.evaluate(() => window.__stats) ?? null).catch(() => null)
  if (stats?.calls) perTurn.push(stats)
}
const meta = await blob(page).evaluate(
  () => ({ elements: window.__elements, chars: window.__chars })).catch(() => ({}))
const median = list => list.slice().sort((a, b) => a - b)[list.length >> 1]
const latency = await page.evaluate(() => window.__turn.ms)

const cdp = await page.context().newCDPSession(page)
await cdp.send('Performance.enable')
const metrics = async () => Object.fromEntries(
  (await cdp.send('Performance.getMetrics')).metrics.map(m => [m.name, m.value]))
const cost = async (label, work) => {
  const a = await metrics(); await work(); const b = await metrics()
  console.log(`  ${label.padEnd(30)} ${((b.TaskDuration - a.TaskDuration) * 1000)
    .toFixed(0).padStart(4)} ms CPU  ${String(b.LayoutCount - a.LayoutCount).padStart(3)} layouts`)
}

console.log(`\n${TITLE} — section of ${meta.elements} elements, ${meta.chars} characters`)
console.log(`  turn latency (key → relocate)  ${median(latency)?.toFixed(1)} ms`)
console.log(`  rect calls per turn            ${median(perTurn.map(t => t.calls))}`
  + `  (${Math.round(median(perTurn.map(t => t.ms)))} ms measuring)`)
await cost('sitting open, 10 s', () => page.waitForTimeout(10000))
await cost('10 turns over 10 s', async () => {
  for (let i = 0; i < 10; i++) { await page.keyboard.press('ArrowRight'); await page.waitForTimeout(1000) }
})
await browser.close()
