/* M1 app shell: library list, reader, contents and display settings.
 * The full Browse/Search/Read interface lands in M2–M3.
 */
import { Browser } from '/static/browse.js'
import { ReaderEngine } from '/static/reader.js'
import { ReaderSettings } from '/static/settings.js'
import { TableOfContents } from '/static/toc.js'

class PositionStore {
  /** Reading position lives on the server so every device shares it
   *  (decision 7). Saves are debounced — relocate fires on every page. */
  #timer = null
  #pending = null
  #delay = 0

  constructor (delay = 800) {
    this.#delay = delay
  }

  async load (name) {
    const res = await fetch(`/api/books/${encodeURIComponent(name)}/position`)
    if (!res.ok) return null
    return (await res.json()).position
  }

  save (name, { cfi, fraction }) {
    this.#pending = { name, cfi, fraction }
    clearTimeout(this.#timer)
    this.#timer = setTimeout(() => this.flush(), this.#delay)
  }

  async flush () {
    const pending = this.#pending
    if (!pending) return
    this.#pending = null
    clearTimeout(this.#timer)
    await fetch(`/api/books/${encodeURIComponent(pending.name)}/position`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ cfi: pending.cfi, fraction: pending.fraction })
    }).catch(() => { /* offline: the next relocate will retry */ })
  }
}

class DisplayPanel {
  /** Binds the display controls to ReaderSettings. */
  #settings = null
  #dom = null

  constructor (settings, dom) {
    this.#settings = settings
    this.#dom = dom
  }

  start () {
    const s = this.#settings
    const d = this.#dom

    d.theme.value = s.get('theme')
    d.font.value = s.get('font')
    d.flow.value = s.get('flow')
    d.lineHeight.value = String(s.get('lineHeight'))
    d.columns.value = String(s.get('columns'))
    this.#showFontSize()

    d.theme.addEventListener('change', () => s.set('theme', d.theme.value))
    d.font.addEventListener('change', () => s.set('font', d.font.value))
    d.flow.addEventListener('change', () => s.set('flow', d.flow.value))
    d.columns.addEventListener('change', () =>
      s.set('columns', Number(d.columns.value)))
    d.lineHeight.addEventListener('input', () =>
      s.set('lineHeight', Number(d.lineHeight.value)))
    d.bigger.addEventListener('click', () => {
      s.nudge('fontSize', 10); this.#showFontSize()
    })
    d.smaller.addEventListener('click', () => {
      s.nudge('fontSize', -10); this.#showFontSize()
    })
  }

  #showFontSize () {
    this.#dom.fontSizeValue.textContent = `${this.#settings.get('fontSize')}%`
  }
}

class BooksApp {
  #reader = null
  #list = null
  #toc = null
  #settings = null
  #positions = null
  #current = null

  constructor (root = document) {
    this.dom = {
      list: root.getElementById('book-list'),
      modes: root.getElementById('browse-modes'),
      letters: root.getElementById('letter-bar'),
      back: root.getElementById('browse-back'),
      backTitle: root.getElementById('browse-back-title'),
      statusbar: root.getElementById('statusbar'),
      stage: root.getElementById('reader-stage'),
      title: root.getElementById('book-title'),
      author: root.getElementById('book-author'),
      status: root.getElementById('status'),
      progress: root.getElementById('progress'),
      chapter: root.getElementById('chapter'),
      prev: root.getElementById('btn-prev'),
      next: root.getElementById('btn-next'),
      toc: root.getElementById('toc-list'),
      tabToc: root.getElementById('tab-toc'),
      tabDisplay: root.getElementById('tab-display'),
      panelToc: root.getElementById('panel-toc'),
      panelDisplay: root.getElementById('panel-display'),
      theme: root.getElementById('theme-select'),
      font: root.getElementById('font-select'),
      flow: root.getElementById('flow-select'),
      columns: root.getElementById('column-select'),
      lineHeight: root.getElementById('line-height'),
      bigger: root.getElementById('font-bigger'),
      smaller: root.getElementById('font-smaller'),
      fontSizeValue: root.getElementById('font-size-value')
    }

    this.#settings = new ReaderSettings()
    this.#reader = new ReaderEngine(this.dom.stage)
    this.#positions = new PositionStore()
    this.#list = new Browser(
      {
        list: this.dom.list, modes: this.dom.modes, letters: this.dom.letters,
        back: this.dom.back, backTitle: this.dom.backTitle,
        statusbar: this.dom.statusbar
      },
      book => this.open(book)
    )
    this.#toc = new TableOfContents(this.dom.toc, item => this.#reader.goTo(item.href))
  }

  async start () {
    this.#bindReaderControls()
    this.#bindPanels()
    new DisplayPanel(this.#settings, this.dom).start()

    this.#settings.onChange(() => this.#applySettings())
    this.#applySettings()

    this.#reader.on('relocate', location => this.#onRelocate(location))

    this.#toc.clear()
    await this.#list.start()
    this.#status('Ready')
  }

  #bindReaderControls () {
    this.dom.prev.addEventListener('click', () => this.#reader.prev())
    this.dom.next.addEventListener('click', () => this.#reader.next())
    document.addEventListener('keydown', e => {
      if (e.target.matches('input, select, textarea')) return
      if (e.key === 'ArrowLeft') this.#reader.prev()
      if (e.key === 'ArrowRight') this.#reader.next()
    })
    // A page closed mid-chapter should not lose the last few pages.
    window.addEventListener('pagehide', () => this.#positions.flush())
  }

  #bindPanels () {
    const show = which => {
      const isToc = which === 'toc'
      this.dom.tabToc.classList.toggle('active', isToc)
      this.dom.tabDisplay.classList.toggle('active', !isToc)
      this.dom.panelToc.hidden = !isToc
      this.dom.panelDisplay.hidden = isToc
    }
    this.dom.tabToc.addEventListener('click', () => show('toc'))
    this.dom.tabDisplay.addEventListener('click', () => show('display'))
    show('toc')
  }

  #applySettings () {
    this.#reader.setStyles(this.#settings.stylesheet())
    this.#reader.setRendererAttributes(this.#settings.rendererAttributes())
  }

  async open (book) {
    await this.#positions.flush()
    this.#status(`Opening ${book.name}…`)
    this.dom.stage.dataset.state = 'loading'
    this.#current = book.path ?? book.name

    try {
      const saved = await this.#positions.load(book.path ?? book.name)
      const meta = await this.#reader.open(
        `/api/books/${encodeURIComponent(book.path ?? book.name)}`,
        { cfi: saved?.cfi ?? null }
      )
      this.dom.title.textContent = book.title ?? meta?.title ?? book.name
      this.dom.author.textContent = meta?.author ?? ''
      this.#toc.render(this.#reader.toc)
      this.dom.stage.dataset.state = 'open'
      this.#status('Reading')
    } catch (err) {
      this.dom.stage.dataset.state = 'error'
      this.#toc.clear()
      this.#status(`Could not open that book: ${err.message}`)
    }
  }

  #onRelocate ({ fraction, cfi, tocItem }) {
    this.dom.progress.textContent = `${Math.round((fraction ?? 0) * 100)}%`
    this.dom.chapter.textContent = tocItem?.label?.trim() ?? ''
    this.#toc.mark(tocItem)
    if (this.#current) {
      this.#positions.save(this.#current, { cfi, fraction: fraction ?? 0 })
    }
  }

  #status (text) {
    this.dom.status.textContent = text
  }
}

const app = new BooksApp(document)
app.start()
