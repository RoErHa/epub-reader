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
  #listeners = { relocate: [], load: [] }

  constructor (host) {
    this.#host = host
  }

  get isOpen () {
    return this.#view !== null
  }

  /** Open a book by URL. Returns its metadata. */
  async open (url) {
    this.close()
    const view = document.createElement('foliate-view')
    this.#host.append(view)
    this.#view = view

    view.addEventListener('relocate', e => this.#emit('relocate', {
      fraction: e.detail.fraction,
      cfi: e.detail.cfi ?? null,
      index: e.detail.index
    }))
    view.addEventListener('load', e => this.#emit('load', { index: e.detail.index }))

    await view.open(url)
    await view.renderer.next()
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

  async next () { await this.#view?.next() }
  async prev () { await this.#view?.prev() }
  async goTo (target) { await this.#view?.goTo(target) }

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
