/* Browsing the library: mode strip, letter bar, drill-down.
 *
 * Shaped exactly like dlna-gateway's browser — Artists/Albums/Tracks/
 * Genres/Decades become Authors/Titles/Series/Genres/Years — so the two
 * apps behave the same way under the hand.
 */

import { NameComposer } from './composer.js'

export const MODES = [
  { id: 'titles', label: 'Titles' },
  { id: 'authors', label: 'Authors', endpoint: '/api/authors', key: 'authors' },
  { id: 'series', label: 'Series', endpoint: '/api/series', key: 'series' },
  { id: 'genres', label: 'Genres', endpoint: '/api/genres', key: 'genres' },
  { id: 'formats', label: 'Formats', endpoint: '/api/formats', key: 'formats' },
  { id: 'years', label: 'Years', endpoint: '/api/years', key: 'decades' },
  { id: 'shelves', label: 'Shelves', endpoint: '/api/shelves', key: 'shelves' },
  { id: 'review', label: 'Review', endpoint: '/api/review', key: 'items' }
]

const json = async url => {
  const res = await fetch(url)
  if (!res.ok) throw new Error(`${res.status} from ${url}`)
  return res.json()
}

export class Browser {
  #dom = null
  #onOpenBook = null
  #onShelveBook = () => {}
  #onReview = null
  #onStatus = () => {}
  #composer = null
  #mode = 'titles'
  #letter = null
  #drill = null // { title, endpoint, shelfId, smart }
  #query = ''
  #sort = 'title'

  constructor (dom, onOpenBook, onReview = () => {}, onShelveBook = () => {},
               onStatus = () => {}) {
    this.#dom = dom
    this.#onOpenBook = onOpenBook
    this.#onShelveBook = onShelveBook
    this.#onReview = onReview
    this.#onStatus = onStatus
    this.#composer = new NameComposer(
      dom.shelfName, dom.newShelf,
      name => this.createShelf(name),
      message => this.#onStatus(message))
  }

  /** Show results for a query, or return to browsing when it is empty. */
  async search (query) {
    this.#query = (query ?? '').trim()
    this.#drill = null
    this.#letter = null
    await this.#render()
  }

  /** Reorder whatever is on screen. */
  async setSort (sort) {
    this.#sort = sort || 'title'
    this.#letter = null
    await this.#render()
  }

  get sort () { return this.#sort }

  async start () {
    this.#renderModes()
    this.#dom.back.querySelector('.back-link')
      .addEventListener('click', () => this.#leaveDrill())
    await this.reload()
  }

  async reload () {
    await this.#refreshStatus()
    await this.#render()
  }

  // ── chrome ────────────────────────────────────────────────────────
  #renderModes () {
    this.#dom.modes.replaceChildren()
    for (const mode of MODES) {
      const button = document.createElement('button')
      button.className = 'browse-mode' + (mode.id === this.#mode ? ' active' : '')
      button.dataset.mode = mode.id
      button.textContent = mode.label
      button.addEventListener('click', () => this.#setMode(mode.id))
      this.#dom.modes.append(button)
    }
  }

  async #setMode (id) {
    this.#mode = id
    this.#letter = null
    this.#drill = null
    this.#renderModes()
    await this.#render()
  }

  async #renderLetters () {
    const bar = this.#dom.letters
    // Only titles and authors have a meaningful alphabet to jump around.
    const mode = this.#mode === 'authors' ? 'authors'
      : this.#mode === 'series' ? 'series'
        // Sorting books by author means the alphabet is authors' initials.
        : this.#sort === 'author' ? 'authors' : 'titles'
    const usable = ['titles', 'authors', 'series'].includes(this.#mode)
    bar.hidden = !usable || this.#drill !== null || Boolean(this.#query)
    if (bar.hidden) return

    const { letters } = await json(`/api/letters?mode=${mode}`)
    bar.replaceChildren()
    for (const letter of letters) {
      const button = document.createElement('button')
      button.className = 'letter' + (letter === this.#letter ? ' active' : '')
      button.textContent = letter
      button.addEventListener('click', () => this.#toggleLetter(letter))
      bar.append(button)
    }
  }

  async #toggleLetter (letter) {
    this.#letter = this.#letter === letter ? null : letter
    await this.#render()
  }

  #leaveDrill () {
    this.#drill = null
    this.#render()
  }

  /** Create a shelf, then show it. */
  async createShelf (name) {
    const res = await fetch('/api/shelves', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name })
    })
    if (!res.ok) {
      const detail = await res.json().catch(() => ({}))
      throw new Error(detail.detail ?? `Could not create that shelf (${res.status}).`)
    }
    await this.#render()
    return (await res.json()).shelf
  }

  // ── content ───────────────────────────────────────────────────────
  async #render () {
    // Asking for a name belongs to the screen you asked on: leaving it
    // takes the question with you.
    this.#composer.close()
    await this.#renderLetters()
    this.#dom.back.hidden = this.#drill === null
    // One assignment, on every path: the offer to make a shelf belongs
    // to the list of shelves and nowhere else.
    this.#dom.newShelf.hidden = !(
      this.#mode === 'shelves' && !this.#drill && !this.#query)
    if (this.#drill || this.#query) {
      this.#dom.acceptAll.hidden = true
    }
    if (this.#drill) {
      this.#dom.backTitle.textContent = this.#drill.title
      return this.#renderBooks(this.#drill.endpoint)
    }
    if (this.#query) return this.#renderSearch()
    if (this.#mode === 'review') return this.#renderReview()
    const mode = MODES.find(m => m.id === this.#mode)
    if (!mode.endpoint) {
      const query = new URLSearchParams({ sort: this.#sort })
      if (this.#letter) query.set('letter', this.#letter)
      return this.#renderBooks(`/api/books?${query}`)
    }
    return this.#renderGroups(mode)
  }

  /** The review queue is neither a group list nor a book list. */
  async #renderReview () {
    const { items } = await json('/api/review')
    const list = this.#dom.list
    list.replaceChildren()
    this.#dom.acceptAll.hidden = !items.length
    if (!items.length) {
      return this.#message('Nothing waiting to be reviewed.')
    }
    for (const item of items) {
      const row = document.createElement('button')
      row.className = 'review-row'
      row.dataset.id = String(item.id)
      row.innerHTML =
        '<span class="review-book"></span><span class="review-score"></span>'
      row.querySelector('.review-book').textContent = item.book.title
      row.querySelector('.review-score').textContent =
        `${Math.round(item.score * 100)}%`
      row.addEventListener('click', () => {
        for (const el of list.querySelectorAll('.review-row')) {
          el.classList.toggle('active', el === row)
        }
        this.#onReview(item)
      })
      list.append(row)
    }
  }

  async #renderSearch () {
    const result = await json(`/api/search?q=${encodeURIComponent(this.#query)}`)
    const list = this.#dom.list
    list.replaceChildren()

    const total = result.books.length + result.authors.length +
      result.series.length
    if (!total) return this.#message(`Nothing found for “${this.#query}”.`)

    // Grouped by kind: at library scale, "le guin" should offer the
    // author before forty loose rows.
    if (result.authors.length) {
      list.append(this.#groupHeading('Authors'))
      for (const author of result.authors) {
        list.append(this.#group(author.name, author.books, () => {
          this.#drill = {
            title: author.name,
            endpoint: `/api/authors/${author.id}/books`
          }
          this.#query = ''
          this.#render()
        }))
      }
    }
    if (result.series.length) {
      list.append(this.#groupHeading('Series'))
      for (const series of result.series) {
        list.append(this.#group(series.name, series.books, () => {
          this.#drill = {
            title: series.name,
            endpoint: `/api/series/${series.id}/books`
          }
          this.#query = ''
          this.#render()
        }))
      }
    }
    if (result.books.length) {
      list.append(this.#groupHeading('Books'))
      this.#appendBooks(list, result.books)
    }
  }

  #groupHeading (text) {
    const heading = document.createElement('div')
    heading.className = 'result-group'
    heading.textContent = text
    return heading
  }

  #group (label, count, onClick) {
    const button = document.createElement('button')
    button.className = 'group-row'
    button.innerHTML =
      '<span class="group-name"></span><span class="group-count"></span>'
    button.querySelector('.group-name').textContent = label
    button.querySelector('.group-count').textContent = String(count)
    button.addEventListener('click', onClick)
    return button
  }

  async #renderGroups (mode) {
    const data = await json(mode.endpoint)
    let rows = data[mode.key] ?? []
    if (this.#letter && mode.id !== 'years') {
      const want = this.#letter.toLowerCase()
      rows = rows.filter(r => (r.sort_name ?? r.name ?? '').toLowerCase()
        .startsWith(want))
    }
    const list = this.#dom.list
    list.replaceChildren()
    if (!rows.length) return this.#message('Nothing here yet.')

    this.#dom.acceptAll.hidden = true
    for (const row of rows) {
      const isYear = mode.id === 'years'
      const label = isYear ? `${row.decade}s` : row.name
      const button = document.createElement('button')
      button.className = 'group-row'
      button.dataset.id = String(isYear ? row.decade : row.id)
      button.innerHTML =
        '<span class="group-name"></span><span class="group-count"></span>'
      button.querySelector('.group-name').textContent = label
      button.querySelector('.group-count').textContent = String(row.books)
      button.addEventListener('click', () => {
        this.#drill = {
          title: label,
          endpoint: isYear
            ? `/api/books?decade=${row.decade}&sort=${this.#sort}`
            : mode.id === 'genres'
              ? `/api/genres/${row.id}/books`
              : mode.id === 'shelves'
                ? (row.smart
                    ? `/api/shelves/smart/${row.key}/books`
                    : `/api/shelves/${row.id}/books`)
                : `/api/${mode.id}/${row.id}/books`,
          // A stored shelf is the one place a book can be taken *off*
          // something. A smart shelf is a consequence of reading, so
          // there is nothing to remove it from.
          shelfId: mode.id === 'shelves' && !row.smart ? row.id : null,
          // A smart shelf has no membership to edit, but the fact behind
          // it can be changed — which is how a book leaves one.
          smart: mode.id === 'shelves' && row.smart ? row.key : null
        }
        this.#render()
      })
      // A shelf you made can be thrown away; a smart one is a
      // consequence of reading, and there is nothing there to delete.
      if (mode.id === 'shelves' && !row.smart) {
        const item = document.createElement('div')
        item.className = 'shelf-item'
        item.append(button, this.#deleteShelf(row))
        list.append(item)
      } else {
        list.append(button)
      }
    }
  }

  /** Two taps, not one: a shelf cannot be un-deleted, and a stray tap on
   *  a phone should not cost you a list you built. The books stay. */
  #deleteShelf (shelf) {
    const button = document.createElement('button')
    button.className = 'row-shelf-delete'
    button.type = 'button'
    button.textContent = '−'
    button.title = `Delete the shelf "${shelf.name}"`
    button.setAttribute('aria-label', button.title)

    let asked = false
    button.addEventListener('click', async event => {
      event.stopPropagation()
      if (!asked) {
        asked = true
        button.classList.add('asking')
        button.textContent = 'Delete?'
        // Asking is not waiting for ever: an unanswered question goes
        // back to being an offer.
        setTimeout(() => {
          if (!asked) return
          asked = false
          button.classList.remove('asking')
          button.textContent = '−'
        }, 5000)
        return
      }
      await fetch(`/api/shelves/${shelf.id}`, { method: 'DELETE' })
      await this.#render()
    })
    return button
  }

  async #renderBooks (endpoint) {
    const { books } = await json(endpoint)
    const list = this.#dom.list
    list.replaceChildren()
    if (!books.length) return this.#message('No books here.')
    this.#appendBooks(list, books)
  }

  #appendBooks (list, books) {
    for (const book of books) {
      // The row is a button, so the shelf controls sit beside it rather
      // than inside it: a button within a button is not a thing.
      const item = document.createElement('div')
      item.className = 'book-item'
      item.dataset.name = book.path

      const row = document.createElement('button')
      row.className = 'book-row'
      row.dataset.name = book.path
      row.innerHTML =
        '<span class="book-cover-wrap"></span>' +
        '<span class="book-text">' +
        '<span class="book-name"></span>' +
        '<span class="book-author"></span>' +
        '<span class="book-line"><span class="book-meta"></span>' +
        '<span class="book-progress"></span></span></span>'

      const wrap = row.querySelector('.book-cover-wrap')
      if (book.cover) {
        const img = document.createElement('img')
        img.className = 'book-cover'
        img.loading = 'lazy'
        img.alt = ''
        img.src = book.cover
        wrap.append(img)
      } else {
        wrap.classList.add('no-cover')
        wrap.textContent = '📕'
      }

      item.append(row, ...this.#shelfControls(book))
      row.querySelector('.book-name').textContent = book.title || book.name
      row.querySelector('.book-author').textContent = book.authors.join(' & ')
      const bits = []
      if (book.series) {
        bits.push(book.series_index
          ? `${book.series} #${this.#number(book.series_index)}`
          : book.series)
      }
      if (book.first_published) bits.push(String(book.first_published))
      row.querySelector('.book-meta').textContent = bits.join(' · ')
      row.querySelector('.book-progress').textContent =
        book.percent > 0 ? `${book.percent}%` : ''

      row.addEventListener('click', () => {
        for (const el of list.querySelectorAll('.book-row')) {
          el.classList.toggle('active', el === row)
        }
        this.#onOpenBook(book)
      })
      list.append(item)
    }
  }

  /** Shelving belongs on the row: you recognise a book in the list and
   *  file it, without opening it first. */
  #shelfControls (book) {
    const controls = []

    const shelve = document.createElement('button')
    shelve.className = 'row-shelf'
    shelve.type = 'button'
    shelve.textContent = '＋'
    shelve.title = `Put "${book.title || book.name}" on a shelf`
    shelve.setAttribute('aria-label', shelve.title)
    shelve.addEventListener('click', event => {
      event.stopPropagation()
      this.#onShelveBook(book)
    })
    controls.push(shelve)

    // Only while looking at a shelf: elsewhere there is no shelf to
    // take it off.
    if (this.#drill?.shelfId) {
      controls.push(this.#control(
        'row-unshelf', '−',
        `Take "${book.title || book.name}" off ${this.#drill.title}`,
        () => fetch(`/api/shelves/${this.#drill.shelfId}/books/${book.id}`,
                    { method: 'DELETE' })))
    }

    // A smart shelf is a view of a fact, so you cannot take a book off
    // it — you change the fact, and it leaves.
    if (this.#drill?.smart === 'reading') {
      controls.push(this.#control(
        'row-finish', '✓', `Mark "${book.title || book.name}" as read`,
        () => this.#setStatus(book, 'finished')))
      controls.push(this.#control(
        'row-unshelf', '−',
        `Stop reading "${book.title || book.name}" — your place is kept`,
        () => this.#setStatus(book, 'unread')))
    } else if (this.#drill?.smart === 'finished') {
      controls.push(this.#control(
        'row-unshelf', '−',
        `"${book.title || book.name}" is not finished after all`,
        () => this.#setStatus(book, 'unread')))
    }
    return controls
  }

  #control (className, glyph, title, act) {
    const button = document.createElement('button')
    button.className = className
    button.type = 'button'
    button.textContent = glyph
    button.title = title
    button.setAttribute('aria-label', title)
    button.addEventListener('click', async event => {
      event.stopPropagation()
      await act()
      await this.#render()
    })
    return button
  }

  #setStatus (book, status) {
    return fetch(`/api/books/${book.id}/status`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status })
    })
  }

  #number (value) {
    return Number.isInteger(value) ? String(value) : String(value)
  }

  async #refreshStatus () {
    try {
      const s = await json('/api/status')
      const bits = [`${s.books} books`, `${s.authors} authors`]
      if (s.missing) bits.push(`${s.missing} missing`)
      if (s.failed) bits.push(`${s.failed} unreadable`)
      this.#dom.statusbar.textContent = bits.join(' · ')
    } catch {
      this.#dom.statusbar.textContent = ''
    }
  }

  #message (text) {
    const div = document.createElement('div')
    div.className = 'msg'
    div.textContent = text
    this.#dom.list.replaceChildren(div)
  }
}
