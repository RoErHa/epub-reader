/* The ONLY file in this project that imports foliate-js.
 *
 * foliate-js declares itself unstable ("expect the API to change at any
 * time"), so the whole app talks to ReaderEngine instead, and upstream
 * churn lands here and nowhere else.
 */
import '/static/vendor/foliate-js/view.js'

export class ReaderEngine {
  #view = null
  #host = null
  #styles = ''
  #attributes = {}
  #listeners = { relocate: [], load: [] }

  constructor (host) {
    this.#host = host
  }

  get isOpen () {
    return this.#view !== null
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

    view.addEventListener('relocate', e => this.#emit('relocate', {
      fraction: e.detail.fraction ?? 0,
      cfi: e.detail.cfi ?? null,
      tocItem: e.detail.tocItem ?? null,
      index: e.detail.index
    }))
    view.addEventListener('load', e => {
      // Layout attributes could not be applied before a section existed
      // (see #applyAttributes) — the first load is the earliest safe point.
      this.#applyAttributes()
      this.#emit('load', { index: e.detail.index })
    })

    await view.open(url)
    this.#guardRenderer(view.renderer)
    this.#applyStyles()

    // Resuming beats starting over, but a stale CFI must not strand the
    // reader on a blank view — fall back to the beginning.
    if (cfi) {
      try {
        await view.goTo(cfi)
      } catch {
        await view.renderer.next()
      }
    } else {
      await view.renderer.next()
    }
    return this.metadata
  }

  close () {
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
    this.#attributes = { ...this.#attributes, ...attributes }
    this.#applyAttributes()
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
    for (const [name, value] of Object.entries(this.#attributes)) {
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
