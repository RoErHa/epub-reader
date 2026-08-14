/* Browsing the library: mode strip, letter bar, drill-down.
 *
 * Shaped exactly like dlna-gateway's browser — Artists/Albums/Tracks/
 * Genres/Decades become Authors/Titles/Series/Genres/Years — so the two
 * apps behave the same way under the hand.
 */

export const MODES = [
  { id: 'titles', label: 'Titles' },
  { id: 'authors', label: 'Authors', endpoint: '/api/authors', key: 'authors' },
  { id: 'series', label: 'Series', endpoint: '/api/series', key: 'series' },
  { id: 'genres', label: 'Genres', endpoint: '/api/genres', key: 'genres' },
  { id: 'years', label: 'Years', endpoint: '/api/years', key: 'decades' }
]

const json = async url => {
  const res = await fetch(url)
  if (!res.ok) throw new Error(`${res.status} from ${url}`)
  return res.json()
}

export class Browser {
  #dom = null
  #onOpenBook = null
  #mode = 'titles'
  #letter = null
  #drill = null // { title, endpoint }

  constructor (dom, onOpenBook) {
    this.#dom = dom
    this.#onOpenBook = onOpenBook
  }

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
      : this.#mode === 'series' ? 'series' : 'titles'
    const usable = ['titles', 'authors', 'series'].includes(this.#mode)
    bar.hidden = !usable || this.#drill !== null
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

  // ── content ───────────────────────────────────────────────────────
  async #render () {
    await this.#renderLetters()
    this.#dom.back.hidden = this.#drill === null
    if (this.#drill) {
      this.#dom.backTitle.textContent = this.#drill.title
      return this.#renderBooks(this.#drill.endpoint)
    }
    const mode = MODES.find(m => m.id === this.#mode)
    if (!mode.endpoint) {
      const query = this.#letter ? `?letter=${encodeURIComponent(this.#letter)}` : ''
      return this.#renderBooks(`/api/books${query}`)
    }
    return this.#renderGroups(mode)
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
            ? `/api/books?decade=${row.decade}`
            : mode.id === 'genres'
              ? `/api/genres/${row.id}/books`
              : `/api/${mode.id}/${row.id}/books`
        }
        this.#render()
      })
      list.append(button)
    }
  }

  async #renderBooks (endpoint) {
    const { books } = await json(endpoint)
    const list = this.#dom.list
    list.replaceChildren()
    if (!books.length) return this.#message('No books here.')

    for (const book of books) {
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
      list.append(row)
    }
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
