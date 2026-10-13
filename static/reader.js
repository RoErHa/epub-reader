/* The ONLY file in this project that imports foliate-js.
 *
 * foliate-js declares itself unstable ("expect the API to change at any
 * time"), so the whole app talks to ReaderEngine instead, and upstream
 * churn lands here and nowhere else.
 */
import '/static/vendor/foliate-js/view.js'
import { Overlayer } from '/static/vendor/foliate-js/overlayer.js'
import { TextScale } from '/static/textscale.js'

/* How a highlight is drawn. foliate hands us the rectangles and asks
 * what to paint; these are the only colours a mark can be, and the set
 * matches the one the server will accept. */
const INK = {
  yellow: { draw: Overlayer.highlight, options: { color: '#FFC24A' } },
  green: { draw: Overlayer.highlight, options: { color: '#7BD88F' } },
  blue: { draw: Overlayer.highlight, options: { color: '#7AB8FF' } },
  pink: { draw: Overlayer.highlight, options: { color: '#FF9BC2' } },
  underline: { draw: Overlayer.underline, options: { color: '#FFC24A' } }
}
const DEFAULT_INK = 'yellow'

/* Two things foliate's PDF adapter leaves undone, and foliate calls
 * getDocument itself with options we cannot reach — so pdf.js is told on
 * the way in, and the document it hands back is adjusted before foliate
 * sees it.
 *
 * 1. Where JPEG 2000 is decoded. pdf.js loads OpenJPEG from the
 *    `wasmUrl` it is given; foliate gives none, so every JPX image
 *    failed and its page was drawn blank. 17 PDFs here carry their
 *    pictures that way, and a scan carries nothing else (§8.28). The
 *    plain-JavaScript build rather than the WebAssembly one: compiling
 *    WebAssembly would need 'wasm-unsafe-eval' in the CSP, and a scan
 *    opening a moment slower is not worth loosening the one rule that
 *    contains a book's scripts (§3). JBIG2 has a decoder of its own
 *    inside pdf.js and needs nothing.
 *
 * 2. Contents entries that lead nowhere. Opening a book asks for the
 *    page behind every entry, and one that has none — a scanner's
 *    "Local Disk" with no target, a bookmark aimed at something that is
 *    not a page — throws, which refused the whole book. 87 PDFs here
 *    (§8.29). Such an entry is taken out and whatever sat beneath it
 *    moves up into its place, so everything that leads somewhere stays.
 */
class PdfAdapter {
  static DECODERS = new URL('/static/vendor/pdfjs-decoders/', location.href).href
  static #installed = null

  static install () {
    this.#installed ??= import(
      '/static/vendor/foliate-js/vendor/pdfjs/pdf.mjs'
    ).then(() => {
      const lib = globalThis.pdfjsLib
      const getDocument = lib.getDocument
      lib.getDocument = source => {
        const task = getDocument({
          wasmUrl: PdfAdapter.DECODERS, useWasm: false, ...source
        })
        // Registered before foliate's own await, so it runs first.
        task.promise.then(pdf => PdfAdapter.#onlyReachable(pdf), () => {})
        return task
      }
    })
    return this.#installed
  }

  static #onlyReachable (pdf) {
    const getOutline = pdf.getOutline.bind(pdf)
    const leadsToPage = async target => {
      try {
        const dest = typeof target === 'string'
          ? await pdf.getDestination(target) : target
        return Array.isArray(dest) && await pdf.getPageIndex(dest[0]) >= 0
      } catch {
        return false
      }
    }
    const keep = async items => {
      const kept = []
      for (const item of items ?? []) {
        const below = await keep(item.items)
        if (await leadsToPage(item.dest)) kept.push({ ...item, items: below })
        else kept.push(...below)
      }
      return kept
    }
    pdf.getOutline = async () => {
      const outline = await getOutline()
      return outline && keep(outline)
    }
  }

  /** Only a PDF pays for loading pdf.js, as with foliate's own import. */
  static async isPdf (file) {
    if (!(file instanceof Blob)) return false
    const head = await file.slice(0, 5).text()
    return head === '%PDF-'
  }
}

/* What foliate's fixed-layout renderer does not do for us. */
class FixedPage {
  /** A page's own size, read the way foliate reads it: an SVG's viewBox,
   *  the viewport meta a PDF page carries, or a comic page's image. */
  static size (doc) {
    const root = doc?.documentElement
    if (!root) return null
    if (root.localName === 'svg') {
      const [, , w, h] = (root.getAttribute('viewBox') ?? '').split(/[\s,]+/)
      return FixedPage.#valid(w, h)
    }
    const meta = doc.querySelector('meta[name="viewport"]')?.getAttribute('content')
    if (meta) {
      const pairs = Object.fromEntries(meta.split(/[,;\s]+/).filter(Boolean)
        .map(x => x.split('=').map(y => y.trim())))
      const found = FixedPage.#valid(pairs.width, pairs.height)
      if (found) return found
    }
    const img = doc.querySelector('img')
    return img ? FixedPage.#valid(img.naturalWidth, img.naturalHeight) : null
  }

  static #valid (w, h) {
    const width = parseFloat(w)
    const height = parseFloat(h)
    return width > 0 && height > 0 ? { width, height } : null
  }

  /* foliate centres the page with flex, and centred content wider than
   * its box overflows on both sides — the left half of a zoomed page
   * could not be scrolled to. `safe` centres only while it fits. The
   * shadow root is closed, but a host's own style outranks :host. */
  static reachable (renderer) {
    renderer.style.justifyContent = 'safe center'
    renderer.style.alignItems = 'safe center'
  }
}

export class ReaderEngine {
  #view = null
  #host = null
  #styles = ''
  #requested = {}        // what the display settings asked for
  #forcedScrolled = false
  #listeners = { relocate: [], load: [], selection: [] }
  #marks = new Map()     // cfi → colour, everything drawn on this book
  #resize = null         // follows the pane, so the measure can
  #page = null           // a fixed-layout page's own size, for zoom

  constructor (host) {
    this.#host = host
  }

  get isOpen () {
    return this.#view !== null
  }

  /** True when this book was too large to paginate — see #mustScroll. */
  get forcedScrolled () {
    return this.#forcedScrolled
  }

  /* Some EPUBs hold the entire book in one document. CSS multi-column
   * lays that out as a single strip whose width scales with the content
   * — a 1.2 MB section measured 8,120,880 px wide, which neither
   * Chromium nor WebKit can paint, so the reader showed the cover and
   * then nothing. Scrolled flow skips columnisation and renders it fine.
   *
   * The threshold sits between what is known to work and known to fail
   * in the real library: 252 kB paginates correctly, 1.18 MB does not.
   */
  static MAX_PAGINATED_SECTION = 400 * 1024

  /* Books open where the words start, not on the cover.
   *
   * foliate's goToTextStart() uses the bodymatter landmark and falls
   * back to the first linear section — but many EPUBs have no landmarks
   * and list the cover as linear, so it lands on the cover anyway. A
   * cover is a small image on a large empty pane, which reads as a
   * broken reader.
   *
   * Front matter is tiny next to a chapter (cover.html 456 B against a
   * 1.18 MB section) — but "tiny" only means anything relative to the
   * book itself, so the threshold is a fraction of the median section
   * rather than a fixed size. A fixed 2 kB skipped every section of a
   * book whose chapters were 1 kB.
   */
  static CONTENT_FRACTION = 0.25
  static MIN_CONTENT_FLOOR = 512
  static MAX_FRONT_MATTER = 6

  #firstContentIndex (book) {
    const sections = book?.sections ?? []
    const start = Math.max(0, sections.findIndex(s => s.linear !== 'no'))
    const sizes = sections.slice(start).map(s => s?.size ?? 0)
      .filter(n => n > 0).sort((a, b) => a - b)
    if (!sizes.length) return start

    const median = sizes[Math.floor(sizes.length / 2)]
    const threshold = Math.max(
      ReaderEngine.MIN_CONTENT_FLOOR, median * ReaderEngine.CONTENT_FRACTION
    )
    const limit = Math.min(sections.length, start + ReaderEngine.MAX_FRONT_MATTER)
    for (let i = start; i < limit; i++) {
      if ((sections[i]?.size ?? Infinity) >= threshold) return i
    }
    return start
  }

  async #goToStart (view) {
    const landmark = view.book?.landmarks?.find(
      m => (m.type ?? '').includes('bodymatter')
    )
    if (landmark?.href && await view.goTo(landmark.href)) return
    if (await view.goTo(this.#firstContentIndex(view.book))) return
    await view.renderer.next()
  }

  #mustScroll (book) {
    return (book?.sections ?? []).some(
      section => (section?.size ?? 0) > ReaderEngine.MAX_PAGINATED_SECTION
    )
  }

  get view () {
    return this.#view
  }

  /** Open a book by URL, optionally resuming at a saved location.
   *  Returns its metadata. */
  async open (url, { cfi = null } = {}) {
    this.close()
    const view = document.createElement('foliate-view')
    this.#host.append(view)
    this.#view = view
    this.#resize = new ResizeObserver(() => this.#applyAttributes())
    this.#resize.observe(this.#host)

    view.addEventListener('relocate', e => this.#emit('relocate', {
      fraction: e.detail.fraction ?? 0,
      cfi: e.detail.cfi ?? null,
      tocItem: e.detail.tocItem ?? null,
      index: e.detail.index
    }))
    view.addEventListener('load', e => {
      // Absolute text sizes follow the size control (§8.31); a fixed
      // page reports its size so the same control can zoom it.
      TextScale.apply(e.detail.doc)
      if (view.isFixedLayout) this.#page = FixedPage.size(e.detail.doc) ?? this.#page
      // Layout attributes could not be applied before a section existed
      // (see #applyAttributes) — the first load is the earliest safe point.
      this.#applyAttributes()
      this.#watchSelection(e.detail.doc)
      // A section gets a fresh overlay when it loads, so whatever was
      // drawn on it has to be drawn again.
      this.#redraw()
      this.#emit('load', { index: e.detail.index })
    })

    // foliate asks what a mark should look like rather than deciding.
    view.addEventListener('draw-annotation', e => {
      const { draw, annotation } = e.detail
      const ink = INK[annotation.color] ?? INK[DEFAULT_INK]
      draw(ink.draw, ink.options)
    })

    if (await PdfAdapter.isPdf(url)) await PdfAdapter.install()
    await view.open(url)
    this.#guardRenderer(view.renderer)

    // Decided before the first section lays out, because switching
    // afterwards would mean paying for the unpaintable layout first.
    this.#forcedScrolled = this.#mustScroll(view.book)

    this.#applyStyles()

    // Resuming beats starting over, but a stale CFI must not strand the
    // reader on a blank view — fall back to the beginning.
    if (cfi) {
      try {
        await view.goTo(cfi)
      } catch {
        await this.#goToStart(view)
      }
    } else {
      await this.#goToStart(view)
    }
    return this.metadata
  }

  // ── marks ─────────────────────────────────────────────────────────
  /** Draw a highlight, or move it to another colour. */
  async mark (cfi, color = DEFAULT_INK) {
    if (!this.#view || !cfi) return false
    this.#marks.set(cfi, color)
    try {
      await this.#view.addAnnotation({ value: cfi, color })
      return true
    } catch {
      // A CFI that no longer resolves — the book was replaced. The
      // words are still in the database, which is the whole reason
      // they are stored there.
      return false
    }
  }

  /** Take a highlight off the page. */
  async unmark (cfi) {
    if (!this.#view || !cfi) return
    this.#marks.delete(cfi)
    try {
      await this.#view.deleteAnnotation({ value: cfi })
    } catch { /* already gone with its section */ }
  }

  /** Draw a whole set at once, replacing anything drawn before. */
  async drawAll (marks) {
    this.#marks.clear()
    for (const mark of marks ?? []) this.#marks.set(mark.cfi, mark.color)
    await this.#redraw()
  }

  async goToMark (cfi) {
    if (!this.#view || !cfi) return false
    try {
      await this.#view.showAnnotation({ value: cfi })
      return true
    } catch {
      return false
    }
  }

  async #redraw () {
    for (const [cfi, color] of this.#marks) {
      try {
        await this.#view?.addAnnotation({ value: cfi, color })
      } catch { /* belongs to a section that is not loaded */ }
    }
  }

  // ── selection ─────────────────────────────────────────────────────
  /** Watch one section's document for the reader letting go of a
   *  selection. Sections are replaced as you move, so every load gets
   *  its own listeners. */
  #watchSelection (doc) {
    if (!doc) return
    const settled = () => this.#emit('selection', this.selection())
    doc.addEventListener('pointerup', settled)
    doc.addEventListener('keyup', settled)
  }

  /** What is selected right now: the words, where they are, and where
   *  on screen to put an offer about them. Null when nothing is. */
  selection () {
    const contents = this.#view?.renderer?.getContents?.() ?? []
    for (const { doc, index } of contents) {
      const sel = doc?.getSelection?.()
      if (!sel || !sel.rangeCount || sel.isCollapsed) continue
      const text = sel.toString().trim()
      if (!text) continue
      const range = sel.getRangeAt(0)
      let cfi = null
      try {
        cfi = this.#view.getCFI(index, range)
      } catch {
        return null
      }
      return { cfi, text, rect: this.#screenRect(doc, range) }
    }
    return null
  }

  /** The selection's rectangle in *page* coordinates, not the book
   *  frame's — the offer is drawn by the app, outside the frame. */
  #screenRect (doc, range) {
    const inner = range.getBoundingClientRect()
    const frame = doc.defaultView?.frameElement?.getBoundingClientRect()
    if (!frame) return { top: inner.top, left: inner.left, bottom: inner.bottom,
      width: inner.width, height: inner.height }
    return {
      top: inner.top + frame.top,
      left: inner.left + frame.left,
      bottom: inner.bottom + frame.top,
      width: inner.width,
      height: inner.height
    }
  }

  close () {
    this.#marks.clear()
    this.#forcedScrolled = false
    this.#resize?.disconnect()
    this.#resize = null
    if (!this.#view) return
    this.#view.remove()
    this.#view = null
  }

  get metadata () {
    const book = this.#view?.book
    if (!book) return null
    return {
      title: this.#text(book.metadata?.title),
      author: this.#text(book.metadata?.author),
      language: book.metadata?.language ?? null
    }
  }

  get toc () {
    return this.#view?.book?.toc ?? []
  }

  get location () {
    const last = this.#view?.lastLocation
    if (!last) return null
    return {
      cfi: last.cfi ?? null,
      fraction: last.fraction ?? 0,
      tocItem: last.tocItem ?? null
    }
  }

  async next () { await this.#view?.next() }
  async prev () { await this.#view?.prev() }

  /* Upstream bug, worked around here.
   *
   * epub.js resolveHref() looks the path up with decodeURI(), which by
   * design leaves reserved characters percent-encoded. Calibre writes
   * the NCX href as "Name%2c A_split_0.html" while the OPF manifest
   * holds the literal "Name, A_split_0.html", so the lookup misses and
   * navigation silently fails — view.goTo() logs and returns undefined.
   *
   * About 22% of a real 1,300-book library is affected. Those books
   * still read fine by paging; it is the contents that break.
   *
   * So: if a navigation resolves to nothing, retry with the reserved
   * characters decoded. Delete this once foliate-js uses a decoder that
   * handles them.
   */
  static RESERVED = {
    '%2c': ',', '%3b': ';', '%3a': ':', '%40': '@',
    '%26': '&', '%3d': '=', '%2b': '+', '%24': '$'
  }

  #decodeReserved (href) {
    // Only the path — '/', '?' and '#' are left alone so the shape of
    // the reference cannot change.
    const [path, ...rest] = String(href).split('#')
    const decoded = path.replace(
      /%[0-9a-f]{2}/gi,
      m => ReaderEngine.RESERVED[m.toLowerCase()] ?? m
    )
    return rest.length ? `${decoded}#${rest.join('#')}` : decoded
  }

  async goTo (target) {
    if (!this.#view || target == null) return null
    let href = target
    // Resolve before navigating: a failed goTo() logs an error we cannot
    // suppress, whereas resolveNavigation() simply returns nothing.
    if (typeof target === 'string' && !this.#view.resolveNavigation(target)) {
      const retry = this.#decodeReserved(target)
      if (retry !== target && this.#view.resolveNavigation(retry)) href = retry
    }
    return (await this.#view.goTo(href)) ?? null
  }

  /** Typography and colour, injected into the book document. */
  setStyles (css) {
    this.#styles = css
    this.#applyStyles()
  }

  /** Layout attributes the paginator reads directly. */
  setRendererAttributes (attributes) {
    this.#requested = { ...this.#requested, ...attributes }
    this.#applyAttributes()
  }

  /** What the requested settings become for THIS book. The override
   *  belongs to the book, not to the reader, so it is derived on every
   *  application rather than written into the stored settings. */
  #effectiveAttributes () {
    const attributes = { ...this.#requested }
    // A book that cannot be paginated stays scrolled whatever the
    // settings say — a blank page is not a preference.
    if (this.#forcedScrolled) attributes.flow = 'scrolled'
    const measure = this.#measure(attributes)
    if (measure) attributes['max-inline-size'] = measure
    const scale = Number(attributes['text-scale']) || 1
    delete attributes['text-scale']
    if (this.#view?.isFixedLayout) attributes.zoom = this.#zoom(scale)
    return attributes
  }

  /* The words use the pane they are given.
   *
   * foliate caps a column at 720 px unless told otherwise, which on an
   * iPad in landscape left a third of the screen as margin either side
   * of a narrow strip. So the column is sized from the pane: one column
   * fills it, two split it, and the paginator's own gap is the gutter.
   * Portrait keeps one column whatever was asked — foliate's rule, and
   * the right one — so there the one column fills the pane too, as it
   * does in scrolled flow, which has no columns at all.
   */
  #measure ({ flow, 'max-column-count': count }) {
    const { width, height } = this.#host.getBoundingClientRect()
    if (!width) return null
    const columns = Math.max(1, Number(count) || 1)
    const spread = flow === 'scrolled' || height > width ? 1 : columns
    return `${Math.floor(width / spread)}px`
  }

  /* A PDF or a comic is a picture of a page: there is no font to size,
   * so the size control zooms the page (§8.31). 100% is foliate's own
   * "fit the page"; anything else is that fit times the setting — so it
   * is worked out here the way foliate works it out, from the pane and
   * the page, one page in portrait and two side by side otherwise. */
  #zoom (scale) {
    if (scale === 1 || !this.#page) return 'fit-page'
    const { width, height } = this.#view.renderer.getBoundingClientRect()
    if (!width || !height) return 'fit-page'
    const pages = height > width ? 1 : 2
    const fit = Math.min(width / (this.#page.width * pages),
      height / this.#page.height)
    return String(fit * scale)
  }

  #applyStyles () {
    this.#view?.renderer?.setStyles?.(this.#styles)
  }

  /* Upstream workaround, contained here on purpose.
   *
   * paginator.js observes the container with a ResizeObserver that calls
   * render() → View.columnize(), which dereferences
   * `this.document.documentElement` with no null check. While a section
   * is still loading that document is null, so any resize landing in
   * that window throws a TypeError and abandons the layout pass — which
   * can leave the page blank. Real books hit this constantly because
   * they are large enough for loads to overlap resizes; the fixtures
   * were too small to expose it.
   *
   * We cannot edit the submodule, so the renderer's render() is wrapped
   * to no-op until there is a document to lay out. When foliate-js adds
   * its own guard, delete this.
   */
  #guardRenderer (renderer) {
    if (!renderer || renderer.__renderGuarded) return
    const original = renderer.render?.bind(renderer)
    if (!original) return
    renderer.render = (...args) => {
      if (!this.#hasDocument()) return
      return original(...args)
    }
    renderer.__renderGuarded = true
  }

  /** True once a section document exists to lay out. */
  #hasDocument () {
    try {
      return Boolean(this.#view?.renderer?.getContents?.()?.[0]?.doc)
    } catch {
      return false
    }
  }

  #applyAttributes () {
    const renderer = this.#view?.renderer
    if (!renderer) return
    // Setting a layout attribute re-renders immediately, and the
    // paginator's columnize() dereferences `this.document.documentElement`
    // without a null check. Before the first section has loaded that
    // document is null, so applying attributes early throws inside
    // foliate-js. Real books hit this; small fixtures usually did not.
    if (!this.#hasDocument()) return
    if (this.#view.isFixedLayout) FixedPage.reachable(renderer)
    for (const [name, value] of Object.entries(this.#effectiveAttributes())) {
      if (renderer.getAttribute(name) !== String(value)) {
        renderer.setAttribute(name, value)
      }
    }
  }

  on (event, handler) {
    this.#listeners[event]?.push(handler)
  }

  #emit (event, detail) {
    for (const handler of this.#listeners[event] ?? []) handler(detail)
  }

  /** Titles and names may be a string, a localised object, or an array. */
  #text (value) {
    if (value == null) return null
    if (typeof value === 'string') return value
    if (Array.isArray(value)) return value.map(v => this.#text(v)).join(', ')
    if (typeof value === 'object') {
      return value.name ? this.#text(value.name) : Object.values(value)[0] ?? null
    }
    return String(value)
  }
}
