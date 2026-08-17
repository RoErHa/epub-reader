/* M1 app shell: library list, reader, contents and display settings.
 * The full Browse/Search/Read interface lands in M2–M3.
 */
import { BookDetail } from '/static/detail.js'
import { ReviewPanel } from '/static/review.js'
import { Browser } from '/static/browse.js'
import { LoadingIndicator, fetchWithProgress } from '/static/loading.js'
import { ReaderEngine } from '/static/reader.js'
import { ReaderSettings } from '/static/settings.js'
import { Annotations } from '/static/annotations.js'
import { NameComposer } from '/static/composer.js'
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

  async load (id) {
    const res = await fetch(`/api/books/${id}/position`)
    if (!res.ok) return null
    return (await res.json()).position
  }

  save (id, { cfi, fraction }) {
    this.#pending = { id, cfi, fraction }
    clearTimeout(this.#timer)
    this.#timer = setTimeout(() => this.flush(), this.#delay)
  }

  async flush () {
    const pending = this.#pending
    if (!pending) return
    this.#pending = null
    clearTimeout(this.#timer)
    await fetch(`/api/books/${pending.id}/position`, {
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

class ShelfSheet {
  /** Choosing which shelves the open book sits on. Membership is shown
   *  by state on each row, so one list does both adding and removing. */
  #dom = null
  #bookId = null
  #composer = null

  constructor (dom) {
    this.#dom = dom
    this.#composer = new NameComposer(
      dom.shelfSheetName, dom.shelfNew, name => this.#create(name))
    dom.shelfClose.addEventListener('click', () => this.hide())
    document.addEventListener('keydown', e => {
      // Escape inside the composer gives up on the name, not the sheet.
      if (e.key === 'Escape' && e.target !== dom.shelfSheetName) this.hide()
    })
  }

  hide () {
    this.#composer.close()
    this.#dom.shelfSheet.hidden = true
  }

  async show (bookId) {
    this.#bookId = bookId
    this.#dom.shelfSheet.hidden = false
    await this.render()
  }

  async render () {
    const [all, mine] = await Promise.all([
      fetch('/api/shelves').then(r => r.json()),
      fetch(`/api/books/${this.#bookId}/shelves`).then(r => r.json())
    ])
    const on = new Set(mine.shelves.map(s => s.id))
    const list = this.#dom.shelfChoices
    list.replaceChildren()
    // Smart shelves are a consequence of reading, not a choice.
    const stored = all.shelves.filter(s => !s.smart)
    if (!stored.length) {
      const p = document.createElement('div')
      p.className = 'msg'
      p.textContent = 'No shelves yet.'
      list.append(p)
      return
    }
    for (const shelf of stored) {
      const row = document.createElement('button')
      row.className = 'shelf-choice' + (on.has(shelf.id) ? ' on-shelf' : '')
      row.textContent = shelf.name
      row.addEventListener('click', () => this.#toggle(shelf, on.has(shelf.id)))
      list.append(row)
    }
  }

  async #toggle (shelf, isOn) {
    await fetch(`/api/shelves/${shelf.id}/books/${this.#bookId}`, {
      method: isOn ? 'DELETE' : 'PUT'
    })
    await this.render()
  }

  /** A shelf made while holding a book is a shelf meant for that book,
   *  so it is chosen in the same gesture. */
  async #create (name) {
    const res = await fetch('/api/shelves', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name })
    })
    if (!res.ok) return
    const { shelf } = await res.json()
    await fetch(`/api/shelves/${shelf.id}/books/${this.#bookId}`,
                { method: 'PUT' })
    await this.render()
  }
}

class BooksApp {
  #reader = null
  #list = null
  #browserDom = null
  #toc = null
  #marks = null
  #settings = null
  #positions = null
  #loading = null
  #sheet = null
  #detail = null
  #review = null
  #current = null

  constructor (root = document) {
    this.dom = {
      list: root.getElementById('book-list'),
      modes: root.getElementById('browse-modes'),
      letters: root.getElementById('letter-bar'),
      back: root.getElementById('browse-back'),
      backTitle: root.getElementById('browse-back-title'),
      statusbar: root.getElementById('statusbar'),
      search: root.getElementById('search-input'),
      stage: root.getElementById('reader-stage'),
      title: root.getElementById('book-title'),
      author: root.getElementById('book-author'),
      status: root.getElementById('status'),
      progress: root.getElementById('progress'),
      chapter: root.getElementById('chapter'),
      readerBack: root.getElementById('reader-back'),
      sort: root.getElementById('sort-select'),
      newShelf: root.getElementById('new-shelf'),
      shelfName: root.getElementById('shelf-name'),
      shelf: root.getElementById('btn-shelf'),
      shelfSheet: root.getElementById('shelf-sheet'),
      shelfChoices: root.getElementById('shelf-choices'),
      shelfClose: root.getElementById('shelf-close'),
      shelfNew: root.getElementById('shelf-new'),
      shelfSheetName: root.getElementById('shelf-sheet-name'),
      detail: root.getElementById('detail'),
      detailCover: root.getElementById('detail-cover'),
      detailTitle: root.getElementById('detail-title'),
      detailSeries: root.getElementById('detail-series'),
      detailAuthor: root.getElementById('detail-author'),
      detailFacts: root.getElementById('detail-facts'),
      detailRead: root.getElementById('detail-read'),
      detailFinished: root.getElementById('detail-finished'),
      detailShelf: root.getElementById('detail-shelf'),
      detailSynopsis: root.getElementById('detail-synopsis'),
      detailBio: root.getElementById('detail-bio'),
      detailAlso: root.getElementById('detail-also'),
      detailLookup: root.getElementById('detail-lookup'),
      detailNote: root.getElementById('detail-note'),
      review: root.getElementById('review'),
      reviewTitle: root.getElementById('review-title'),
      reviewScore: root.getElementById('review-score'),
      reviewParts: root.getElementById('review-parts'),
      reviewChanges: root.getElementById('review-changes'),
      reviewAccept: root.getElementById('review-accept'),
      reviewReject: root.getElementById('review-reject'),
      reviewAcceptAll: root.getElementById('review-accept-all'),
      detailShowAll: root.getElementById('detail-showall'),
      prev: root.getElementById('btn-prev'),
      next: root.getElementById('btn-next'),
      toc: root.getElementById('toc-list'),
      btnPanels: root.getElementById('btn-panels'),
      panelsClose: root.getElementById('panels-close'),
      tabToc: root.getElementById('tab-toc'),
      tabMarks: root.getElementById('tab-marks'),
      panelMarks: root.getElementById('panel-marks'),
      marksList: root.getElementById('marks-list'),
      selectionMenu: root.getElementById('selection-menu'),
      selectionHighlight: root.getElementById('selection-highlight'),
      selectionNote: root.getElementById('selection-note'),
      selectionCopy: root.getElementById('selection-copy'),
      selectionColours: [...root.querySelectorAll('#selection-menu .ink')],
      noteEditor: root.getElementById('note-editor'),
      noteText: root.getElementById('note-text'),
      noteSave: root.getElementById('note-save'),
      noteCancel: root.getElementById('note-cancel'),
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

    this.#loading = new LoadingIndicator(root)
    this.#settings = new ReaderSettings()
    this.#reader = new ReaderEngine(this.dom.stage)
    this.#positions = new PositionStore()
    this.#browserDom = {
        list: this.dom.list, modes: this.dom.modes, letters: this.dom.letters,
        back: this.dom.back, backTitle: this.dom.backTitle,
      statusbar: this.dom.statusbar, newShelf: this.dom.newShelf,
      shelfName: this.dom.shelfName,
      acceptAll: this.dom.reviewAcceptAll
    }
    this.#review = new ReviewPanel(this.dom, () => this.#list.reload())
    this.#list = new Browser(
      this.#browserDom,
      book => this.show(book.id),
      item => this.#showReview(item),
      // Shelving from the list: filing a book you recognise, without
      // opening it first.
      book => this.#sheet.show(book.id)
    )
    this.#toc = new TableOfContents(this.dom.toc, item => this.#reader.goTo(item.href))
    this.#marks = new Annotations(this.dom, this.#reader)
    // The reader reports a selection settling; what to offer about it
    // is not the engine's business.
    this.#reader.on('selection', selection => this.#marks.offer(selection))
    this.#sheet = new ShelfSheet(this.dom)
    // Choosing a book shows it; reading starts when you ask for it.
    this.#detail = new BookDetail(this.dom, {
      onRead: book => this.open(book),
      onOpenBook: bookId => this.show(bookId),
      // Marking a book read moves it between smart shelves, so the list
      // behind the detail is now out of date.
      onStatus: () => this.#list.reload()
    })
  }

  async start () {
    this.#bindReaderControls()
    this.#bindPanels()
    new DisplayPanel(this.#settings, this.dom).start()

    this.#settings.onChange(() => this.#applySettings())
    this.#applySettings()

    this.#reader.on('relocate', location => this.#onRelocate(location))

    this.#showView('browse')
    this.#toc.clear()
    await this.#bindShelves()
    await this.#bindSort()
    await this.#list.start()
    this.#status('Ready')
  }

  /** Which pane owns the screen. Only consulted on narrow screens, but
   *  kept accurate everywhere so the CSS stays declarative. */
  #showView (view) {
    document.body.dataset.view = view
  }

  /** The sort order is a display preference, so it lives on the device
   *  alongside the reader's typography — not on the server. */
  async #bindSort () {
    const { sorts } = await fetch('/api/sorts').then(r => r.json())
    this.dom.sort.replaceChildren()
    for (const sort of sorts) {
      const option = document.createElement('option')
      option.value = sort.key
      option.textContent = sort.label
      this.dom.sort.append(option)
    }
    const saved = this.#settings.get('sort')
    if (sorts.some(s => s.key === saved)) this.dom.sort.value = saved
    await this.#list.setSort(this.dom.sort.value)
    this.dom.sort.addEventListener('change', async () => {
      this.#settings.set('sort', this.dom.sort.value)
      await this.#list.setSort(this.dom.sort.value)
    })
  }

  async #bindShelves () {
    // Naming a new shelf is the Browser's own composer now: it owns the
    // list the shelf appears in.
    this.dom.shelf.addEventListener('click', () => {
      if (this.#current) this.#sheet.show(this.#current)
    })
    this.dom.detailShelf.addEventListener('click', () => {
      const book = this.#detail.book
      if (book) this.#sheet.show(book.id)
    })
  }

  #bindReaderControls () {
    this.dom.readerBack.addEventListener('click', () => {
      // From the reader, back goes to the book; from the book, to the list.
      if (!this.dom.detail.hidden || !this.#reader.isOpen) {
        this.#detail.hide()
        this.#showView('browse')
      } else {
        this.#reader.close()
        this.dom.stage.dataset.state = 'empty'
        if (this.#detail.book) this.#detail.show(this.#detail.book.id)
      }
    })
    this.dom.prev.addEventListener('click', () => this.#reader.prev())
    this.dom.next.addEventListener('click', () => this.#reader.next())
    document.addEventListener('keydown', e => {
      if (e.target.matches('input, select, textarea')) return
      if (e.key === 'ArrowLeft') this.#reader.prev()
      if (e.key === 'ArrowRight') this.#reader.next()
    })
    let timer = null
    const run = () => this.#list.search(this.dom.search.value)
    this.dom.search.addEventListener('input', () => {
      clearTimeout(timer)
      timer = setTimeout(run, 250)   // typing should not hammer the index
    })
    this.dom.search.addEventListener('keydown', e => {
      if (e.key === 'Enter') { clearTimeout(timer); run() }
      if (e.key === 'Escape') { this.dom.search.value = ''; clearTimeout(timer); run() }
    })

    // A page closed mid-chapter should not lose the last few pages.
    window.addEventListener('pagehide', () => this.#positions.flush())
  }

  #bindPanels () {
    const panels = {
      toc: [this.dom.tabToc, this.dom.panelToc],
      marks: [this.dom.tabMarks, this.dom.panelMarks],
      display: [this.dom.tabDisplay, this.dom.panelDisplay]
    }
    const show = which => {
      for (const [name, [tab, panel]] of Object.entries(panels)) {
        tab.classList.toggle('active', name === which)
        panel.hidden = name !== which
      }
    }
    for (const [name, [tab]] of Object.entries(panels)) {
      tab.addEventListener('click', () => show(name))
    }
    show('toc')

    // On a touch layout the column is not on screen; this brings it in
    // over the page rather than duplicating what is in it.
    const panelsOpen = open => {
      if (open) document.body.dataset.panels = 'open'
      else delete document.body.dataset.panels
    }
    this.dom.btnPanels?.addEventListener('click', () => {
      panelsOpen(document.body.dataset.panels !== 'open')
    })
    this.dom.panelsClose?.addEventListener('click', () => panelsOpen(false))
    // Leaving the book takes its panels with it.
    this.dom.readerBack?.addEventListener('click', () => panelsOpen(false))
  }

  #applySettings () {
    // The panel sheet sits over the book, so it has to know which
    // colours it is sitting on.
    document.body.dataset.readTheme = this.#settings.get('theme')
    this.#reader.setStyles(this.#settings.stylesheet())
    this.#reader.setRendererAttributes(this.#settings.rendererAttributes())
  }

  #showReview (item) {
    this.#detail.hide()
    this.#showView('read')
    this.#review.show(item)
  }

  /** Show a book without opening it. */
  async show (bookId) {
    this.#review.hide()
    this.#showView('read')          // the detail pane owns the right side
    this.#reader.close()
    this.dom.stage.dataset.state = 'empty'
    this.#toc.clear()
    try {
      const { book } = await this.#detail.show(bookId)
      this.dom.title.textContent = book.title ?? book.name
      this.dom.author.textContent = book.authors.join(' & ')
      this.#status('Ready')
    } catch (err) {
      this.#status(`Could not load that book: ${err.message}`)
    }
  }

  async open (book) {
    this.#detail.hide()
    this.#review.hide()
    await this.#positions.flush()
    this.#status(`Opening ${book.title ?? book.name}…`)
    this.dom.stage.dataset.state = 'loading'
    this.#current = book.id
    this.#showView('read')
    this.#loading.show({ cover: book.cover, title: book.title })

    try {
      const saved = await this.#positions.load(book.id)
      // Fetched here rather than by the engine, so the bytes can be
      // counted; foliate opens a Blob just as happily as a URL.
      const file = await fetchWithProgress(
        book.file ?? `/api/books/${book.id}/file`,
        fraction => this.#loading.downloading(fraction),
        book.name
      )
      this.#loading.preparing(
        book.size > 1_000_000 ? 'this is a large book' : ''
      )
      const meta = await this.#reader.open(file, { cfi: saved?.cfi ?? null })
      this.dom.title.textContent = book.title ?? meta?.title ?? book.name
      this.dom.author.textContent = meta?.author ?? ''
      this.#toc.render(this.#reader.toc)
      // Drawn after the book is laid out, or there is nothing to draw on.
      await this.#marks.load(book.id)
      this.#loading.hide()
      this.dom.stage.dataset.state = 'open'
      this.#status(this.#reader.forcedScrolled
        ? 'Reading — one very large chapter, shown scrolled'
        : 'Reading')
    } catch (err) {
      this.#loading.hide()
      this.dom.stage.dataset.state = 'error'
      this.#toc.clear()
      this.#marks.clear()
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
