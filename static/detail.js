/* The book-detail screen: what you need to decide whether to read it.
 *
 * Cover, series and number, author, genre, when it was first published;
 * then the synopsis, who wrote it, and what else they wrote.
 */

export class BookDetail {
  #dom = null
  #onRead = null
  #onOpenBook = null
  #onStatus = () => {}
  #book = null
  #author = null
  #showAll = false

  constructor (dom, { onRead, onOpenBook, onStatus = () => {} }) {
    this.#dom = dom
    this.#onRead = onRead
    this.#onOpenBook = onOpenBook
    this.#onStatus = onStatus
    dom.detailRead.addEventListener('click', () => {
      if (this.#book) this.#onRead(this.#book)
    })
    dom.detailFinished.addEventListener('click', () => this.#toggleRead())
    dom.detailLookup.addEventListener('click', () => this.#lookUpAuthor())
    dom.detailShowAll.addEventListener('click', () => {
      this.#showAll = !this.#showAll
      if (this.#book) this.show(this.#book.id)
    })
  }

  get book () { return this.#book }

  hide () { this.#dom.detail.hidden = true }

  async show (bookId) {
    const query = this.#showAll ? '?show_all=true' : ''
    const data = await fetch(`/api/books/${bookId}/detail${query}`)
      .then(r => r.json())
    this.#book = data.book
    this.#author = data.author
    this.#note('')
    this.#dom.detail.hidden = false

    this.#renderHead(data)
    this.#renderSynopsis(data.book)
    this.#renderAuthor(data.author)
    this.#renderAlso(data.also_published, data.hidden_works ?? 0)
    return data
  }

  #renderHead ({ book, genres }) {
    const d = this.#dom
    d.detailTitle.textContent = book.title || book.name
    d.detailAuthor.textContent = book.authors.join(' & ')

    d.detailSeries.textContent = book.series
      ? (book.series_index != null
          ? `${book.series} · Book ${this.#number(book.series_index)}`
          : book.series)
      : ''
    d.detailSeries.hidden = !book.series

    // The facts you scan rather than read.
    const facts = []
    if (genres.length) facts.push(genres[0])
    if (book.first_published) facts.push(`First published ${book.first_published}`)
    facts.push(book.format.toUpperCase())
    if (book.language) facts.push(book.language)
    facts.push(`${Math.round(book.size / 1024)} kB`)
    if (book.percent > 0) facts.push(`${book.percent}% read`)
    d.detailFacts.textContent = facts.join(' · ')

    this.#showRead(book.status === 'finished')

    if (book.cover) {
      d.detailCover.src = book.cover
      d.detailCover.alt = `Cover of ${book.title ?? book.name}`
      d.detailCover.hidden = false
    } else {
      d.detailCover.removeAttribute('src')
      d.detailCover.hidden = true
    }
  }

  /** Read is a fact about the book, and "Finished" is a view of it — so
   *  this button is a toggle, not a one-way door: a book marked read by
   *  mistake, or picked up again, can say so. */
  #showRead (done) {
    const button = this.#dom.detailFinished
    button.classList.toggle('done', done)
    button.textContent = done ? '✓ Read' : '✓ Mark as read'
    button.title = done
      ? 'Mark this as unread — it leaves Finished'
      : 'Mark this as read — it moves to Finished'
  }

  async #toggleRead () {
    if (!this.#book) return
    const done = this.#book.status === 'finished'
    // Say it on screen before the round trip: the fact is ours to state.
    this.#showRead(!done)
    await fetch(`/api/books/${this.#book.id}/status`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status: done ? 'unread' : 'finished' })
    })
    this.#book.status = done ? 'unread' : 'finished'
    this.#onStatus(this.#book)
  }

  #renderSynopsis (book) {
    const text = (book.synopsis ?? '').trim()
    this.#dom.detailSynopsis.textContent = text ||
      'This book carries no description of its own.'
    this.#dom.detailSynopsis.classList.toggle('muted', !text)
  }

  /** Ask the server to look this author up on Open Library. */
  async #lookUpAuthor () {
    if (!this.#author) return
    const button = this.#dom.detailLookup
    button.disabled = true
    button.textContent = 'Looking…'
    try {
      const report = await fetch(
        `/api/authors/${this.#author.id}/enrich`, { method: 'POST' }
      ).then(r => r.json())

      // Re-read first, then report: the note must outlive the redraw.
      if (this.#book) await this.show(this.#book.id)
      this.#note(report.matched
        ? `Found them — ${report.works} works.`
        // Say what happened rather than silently doing nothing.
        : (report.reason ?? report.error ?? 'Nothing found.'))
    } catch (err) {
      this.#note(`Could not reach Open Library: ${err.message}`)
    } finally {
      button.disabled = false
      button.textContent = 'Look this author up'
    }
  }

  #note (text) {
    const el = this.#dom.detailNote
    el.textContent = text
    el.hidden = !text
  }

  #renderAuthor (author) {
    const el = this.#dom.detailBio
    const lookup = this.#dom.detailLookup
    lookup.hidden = true
    if (!author) {
      el.textContent = 'No author recorded for this book.'
      el.classList.add('muted')
      return
    }
    if (!author.enriched || !author.bio) {
      // Honest about the gap, and offer to close it.
      el.textContent = author.enriched
        ? `Open Library has nothing on ${author.name}.`
        : `Nothing about ${author.name} has been looked up yet.`
      el.classList.add('muted')
      lookup.hidden = false
      return
    }
    el.classList.remove('muted')
    const life = [author.born, author.died].filter(Boolean).join(' – ')
    const line = [author.name, life, author.nationality].filter(Boolean).join(' · ')
    el.replaceChildren()
    const heading = document.createElement('div')
    heading.className = 'bio-line'
    heading.textContent = line
    const body = document.createElement('p')
    body.textContent = author.bio
    el.append(heading, body)
  }

  #renderAlso (works, hidden = 0) {
    const el = this.#dom.detailAlso
    const toggle = this.#dom.detailShowAll
    // Only offer the toggle when the filter is actually hiding something.
    toggle.hidden = !(hidden > 0 || this.#showAll)
    toggle.textContent = this.#showAll
      ? 'novels and novellas only'
      : `show all (${hidden} more)`
    el.replaceChildren()
    if (!works.length) {
      const p = document.createElement('div')
      p.className = 'muted'
      p.textContent = 'Nothing else by this author, yet.'
      el.append(p)
      return
    }

    let series = Symbol('none')
    for (const work of works) {
      if (work.series !== series) {
        series = work.series
        const heading = document.createElement('div')
        heading.className = 'also-series'
        heading.textContent = series || 'Standalone'
        el.append(heading)
      }
      const row = document.createElement('button')
      row.className = 'also-row' + (work.owned ? ' owned' : '')
      row.disabled = !work.owned
      row.innerHTML =
        '<span class="also-index"></span><span class="also-title"></span>' +
        '<span class="also-year"></span><span class="also-owned"></span>'
      row.querySelector('.also-index').textContent =
        work.series_index != null ? this.#number(work.series_index) : ''
      row.querySelector('.also-title').textContent = work.title
      row.querySelector('.also-year').textContent = work.first_published ?? ''
      row.querySelector('.also-owned').textContent =
        work.owned ? '✓ in library' : '· not owned'
      if (work.owned) {
        row.addEventListener('click', () => this.#onOpenBook(work.book_id))
      }
      el.append(row)
    }
  }

  #number (value) {
    return Number.isInteger(value) ? String(value) : String(value)
  }
}
