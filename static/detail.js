/* The book-detail screen: what you need to decide whether to read it.
 *
 * Cover, series and number, author, genre, when it was first published;
 * then the synopsis, who wrote it, and what else they wrote.
 */

export class BookDetail {
  #dom = null
  #onRead = null
  #onOpenBook = null
  #book = null

  constructor (dom, { onRead, onOpenBook }) {
    this.#dom = dom
    this.#onRead = onRead
    this.#onOpenBook = onOpenBook
    dom.detailRead.addEventListener('click', () => {
      if (this.#book) this.#onRead(this.#book)
    })
  }

  get book () { return this.#book }

  hide () { this.#dom.detail.hidden = true }

  async show (bookId) {
    const data = await fetch(`/api/books/${bookId}/detail`).then(r => r.json())
    this.#book = data.book
    this.#dom.detail.hidden = false

    this.#renderHead(data)
    this.#renderSynopsis(data.book)
    this.#renderAuthor(data.author)
    this.#renderAlso(data.also_published)
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

    if (book.cover) {
      d.detailCover.src = book.cover
      d.detailCover.alt = `Cover of ${book.title ?? book.name}`
      d.detailCover.hidden = false
    } else {
      d.detailCover.removeAttribute('src')
      d.detailCover.hidden = true
    }
  }

  #renderSynopsis (book) {
    const text = (book.synopsis ?? '').trim()
    this.#dom.detailSynopsis.textContent = text ||
      'This book carries no description of its own.'
    this.#dom.detailSynopsis.classList.toggle('muted', !text)
  }

  #renderAuthor (author) {
    const el = this.#dom.detailBio
    if (!author) {
      el.textContent = 'No author recorded for this book.'
      el.classList.add('muted')
      return
    }
    if (!author.enriched || !author.bio) {
      // Honest about the gap rather than showing an empty panel.
      el.textContent =
        `Nothing about ${author.name} has been looked up yet.`
      el.classList.add('muted')
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

  #renderAlso (works) {
    const el = this.#dom.detailAlso
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
