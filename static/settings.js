/* Display settings, and the stylesheet they produce for the book.
 *
 * These are stored per DEVICE in localStorage, deliberately unlike the
 * reading position, which is stored server-side and shared (decision 7).
 * Where you are in a book is a fact about the book; how large you want
 * the type is a fact about the screen you are reading on.
 */

export const THEMES = {
  light: { bg: '#FFFFFF', fg: '#151515', link: '#1A5FB4' },
  sepia: { bg: '#F4ECD8', fg: '#3B2F1E', link: '#8A5A15' },
  dark: { bg: '#121821', fg: '#D8E2F0', link: '#FFC24A' },
  black: { bg: '#000000', fg: '#C4C4C4', link: '#FFC24A' }
}

export const FONTS = {
  default: null, // leave the book's own typography alone
  serif: 'Georgia, "Iowan Old Style", "Times New Roman", serif',
  sans: 'system-ui, -apple-system, "Helvetica Neue", sans-serif',
  mono: 'ui-monospace, SFMono-Regular, Menlo, monospace'
}

export class ReaderSettings {
  static KEY = 'epub-reader.display'
  static DEFAULTS = {
    theme: 'dark',
    font: 'default',
    fontSize: 100, // percent
    lineHeight: 1.5,
    margin: 48,
    columns: 1,
    flow: 'paginated',
    sort: 'title'
  }

  static LIMITS = { fontSize: [70, 220], lineHeight: [1.0, 2.4] }

  #values = null
  #storage = null
  #listeners = []

  constructor (storage = window.localStorage) {
    this.#storage = storage
    this.#values = { ...ReaderSettings.DEFAULTS, ...this.#read() }
  }

  #read () {
    try {
      const raw = this.#storage.getItem(ReaderSettings.KEY)
      return raw ? JSON.parse(raw) : {}
    } catch {
      // Unreadable settings are not worth failing over — fall back to
      // the defaults and let the next save repair the entry.
      return {}
    }
  }

  #write () {
    try {
      this.#storage.setItem(ReaderSettings.KEY, JSON.stringify(this.#values))
    } catch { /* private browsing, quota — display prefs are not critical */ }
  }

  get values () { return { ...this.#values } }
  get (key) { return this.#values[key] }

  set (key, value) {
    const limit = ReaderSettings.LIMITS[key]
    if (limit) value = Math.min(limit[1], Math.max(limit[0], Number(value)))
    this.#values[key] = value
    this.#write()
    for (const listener of this.#listeners) listener(this.values)
    return this.#values[key]
  }

  nudge (key, delta) {
    return this.set(key, Number(this.#values[key]) + delta)
  }

  onChange (listener) { this.#listeners.push(listener) }

  /** The stylesheet injected into the book document. */
  stylesheet () {
    const theme = THEMES[this.#values.theme] ?? THEMES.dark
    const family = FONTS[this.#values.font]
    return `
      html, body {
        color: ${theme.fg} !important;
        background: ${theme.bg} !important;
        line-height: ${this.#values.lineHeight} !important;
        ${family ? `font-family: ${family} !important;` : ''}
      }
      body { font-size: ${this.#values.fontSize}% !important; }
      a, a:visited { color: ${theme.link} !important; }
      img, svg, video { max-width: 100% !important; height: auto !important; }
      ::selection { background: ${theme.link}44; }
    `
  }

  /** Attributes the paginator reads directly. */
  rendererAttributes () {
    return {
      flow: this.#values.flow,
      margin: `${this.#values.margin}px`,
      gap: '3%',
      'max-column-count': String(this.#values.columns)
    }
  }
}
