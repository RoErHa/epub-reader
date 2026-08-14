/* M0 app shell: list the library, open a book in the reader.
 * The full Browse/Search/Read interface lands in M2–M3.
 */
import { ReaderEngine } from '/static/reader.js'

class BookList {
  #el = null
  #onSelect = null

  constructor (el, onSelect) {
    this.#el = el
    this.#onSelect = onSelect
  }

  async load () {
    const res = await fetch('/api/books')
    if (!res.ok) return this.#message(`Could not read the library (${res.status}).`)
    const { books } = await res.json()
    if (!books.length) return this.#message('No books found in the library folder.')
    this.render(books)
  }

  render (books) {
    this.#el.replaceChildren()
    for (const book of books) {
      const row = document.createElement('button')
      row.className = 'book-row'
      row.dataset.name = book.name
      row.innerHTML =
        `<span class="book-name"></span><span class="book-meta"></span>`
      row.querySelector('.book-name').textContent = book.name
      row.querySelector('.book-meta').textContent =
        `${book.format.toUpperCase()} · ${Math.round(book.size / 1024)} kB`
      row.addEventListener('click', () => this.select(row, book))
      this.#el.append(row)
    }
  }

  select (row, book) {
    for (const el of this.#el.querySelectorAll('.book-row')) {
      el.classList.toggle('active', el === row)
    }
    this.#onSelect(book)
  }

  #message (text) {
    this.#el.replaceChildren()
    const p = document.createElement('div')
    p.className = 'msg'
    p.textContent = text
    this.#el.append(p)
  }
}

class BooksApp {
  #reader = null
  #list = null

  constructor (root = document) {
    this.dom = {
      list: root.getElementById('book-list'),
      stage: root.getElementById('reader-stage'),
      title: root.getElementById('book-title'),
      author: root.getElementById('book-author'),
      status: root.getElementById('status'),
      progress: root.getElementById('progress'),
      prev: root.getElementById('btn-prev'),
      next: root.getElementById('btn-next')
    }
    this.#reader = new ReaderEngine(this.dom.stage)
    this.#list = new BookList(this.dom.list, book => this.open(book))
  }

  async start () {
    this.dom.prev.addEventListener('click', () => this.#reader.prev())
    this.dom.next.addEventListener('click', () => this.#reader.next())
    document.addEventListener('keydown', e => {
      if (e.key === 'ArrowLeft') this.#reader.prev()
      if (e.key === 'ArrowRight') this.#reader.next()
    })
    this.#reader.on('relocate', ({ fraction }) => {
      this.dom.progress.textContent = `${Math.round((fraction ?? 0) * 100)}%`
    })
    await this.#list.load()
    this.#status('Ready')
  }

  async open (book) {
    this.#status(`Opening ${book.name}…`)
    this.dom.stage.dataset.state = 'loading'
    try {
      const meta = await this.#reader.open(
        `/api/books/${encodeURIComponent(book.name)}`
      )
      this.dom.title.textContent = meta?.title ?? book.name
      this.dom.author.textContent = meta?.author ?? ''
      this.dom.stage.dataset.state = 'open'
      this.#status('Reading')
    } catch (err) {
      this.dom.stage.dataset.state = 'error'
      this.#status(`Could not open that book: ${err.message}`)
    }
  }

  #status (text) {
    this.dom.status.textContent = text
  }
}

const app = new BooksApp(document)
app.start()
