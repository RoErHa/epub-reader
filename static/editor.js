/* The whole record, by hand (§12.3).
 *
 * What no pass can reach — a title cut off mid-word, a series nobody
 * else has heard of, a synopsis no file carried — a person can. What
 * they change is kept as theirs and locked on the server.
 *
 * Only what changed is sent. The form shows every field, but saving it
 * is not a decision about every field: an untouched title sent back
 * would be locked as the reader's, and the file's typo with it.
 */

export class RecordEditor {
  #dom = null
  #form = null
  #book = null
  #before = null
  #onSaved = () => {}
  #onClose = () => {}
  #suggested = false

  constructor (root, { onSaved, onClose }) {
    this.#onSaved = onSaved
    this.#onClose = onClose
    const $ = id => root.getElementById(id)
    this.#form = $('editor')
    this.#dom = {
      title: $('edit-title'),
      authors: $('edit-authors'),
      series: $('edit-series'),
      index: $('edit-index'),
      year: $('edit-year'),
      genres: $('edit-genres'),
      synopsis: $('edit-synopsis'),
      note: $('edit-note'),
      save: $('edit-save'),
      cancel: $('edit-cancel'),
      seriesNames: $('edit-series-names'),
      genreNames: $('edit-genre-names')
    }
    this.#form.addEventListener('submit', event => {
      event.preventDefault()
      this.#save()
    })
    this.#dom.cancel.addEventListener('click', () => this.close())
    this.#form.addEventListener('keydown', event => {
      if (event.key === 'Escape') {
        event.preventDefault()
        this.close()
      }
    })
  }

  get isOpen () { return !this.#form.hidden }

  open (book, genres) {
    this.#book = book
    this.#before = this.#read(book, genres)
    const d = this.#dom
    d.title.value = this.#before.title
    d.authors.value = this.#before.authors
    d.series.value = this.#before.series
    d.index.value = this.#before.index
    d.year.value = this.#before.year
    d.genres.value = this.#before.genres
    d.synopsis.value = this.#before.synopsis
    this.#markYours(book.edited ?? [])
    this.#note('')
    this.#form.hidden = false
    this.#suggest()
    d.title.focus()
  }

  close () {
    if (this.#form.hidden) return
    this.#form.hidden = true
    this.#onClose()
  }

  /** The book as the form spells it, so "did this change?" compares
   *  like with like. */
  #read (book, genres) {
    return {
      title: book.title ?? '',
      authors: (book.authors ?? []).join(' & '),
      series: book.series ?? '',
      index: book.series_index ?? '',
      year: book.first_published ?? '',
      genres: (genres ?? []).join(', '),
      synopsis: book.synopsis ?? ''
    }
  }

  /** A value nothing will ever overwrite should look like one. */
  #markYours (edited) {
    const fields = {
      title: 'edit-title',
      authors: 'edit-authors',
      series: 'edit-series',
      genres: 'edit-genres',
      synopsis: 'edit-synopsis',
      first_published: 'edit-year'
    }
    for (const [field, id] of Object.entries(fields)) {
      const label = this.#form.querySelector(`label[for="${id}"]`)
      label.classList.toggle('yours', edited.includes(field))
      label.title = edited.includes(field)
        ? 'You set this — no scan will change it'
        : ''
    }
  }

  /** Names already in the library, offered as you type — one spelling
   *  per series is what keeps the Series browse from splitting. Asked
   *  for once, the first time the form is opened. */
  async #suggest () {
    if (this.#suggested) return
    this.#suggested = true
    const fill = (list, names) => {
      list.replaceChildren(...names.map(name => {
        const option = document.createElement('option')
        option.value = name
        return option
      }))
    }
    try {
      const [series, genres] = await Promise.all([
        fetch('/api/series').then(r => r.json()),
        fetch('/api/genres').then(r => r.json())
      ])
      fill(this.#dom.seriesNames, series.series.map(s => s.name))
      fill(this.#dom.genreNames, genres.genres.map(g => g.name))
    } catch {
      this.#suggested = false   // nothing lost; ask again next time
    }
  }

  /** What was said, as the server wants it — and only the fields whose
   *  words actually changed. */
  #changes () {
    const d = this.#dom
    const now = {
      title: d.title.value,
      authors: d.authors.value,
      series: d.series.value,
      index: d.index.value,
      year: d.year.value,
      genres: d.genres.value,
      synopsis: d.synopsis.value
    }
    const moved = key => String(now[key]).trim() !== String(this.#before[key]).trim()
    const out = {}
    if (moved('title')) out.title = now.title.trim()
    if (moved('authors')) {
      out.authors = now.authors.split('&').map(s => s.trim()).filter(Boolean)
    }
    if (moved('series')) out.series = now.series.trim() || null
    if (moved('index')) {
      out.series_index = now.index.trim() === '' ? null : Number(now.index)
    }
    if (moved('year')) {
      out.first_published = now.year.trim() === '' ? null : Number(now.year)
    }
    if (moved('genres')) {
      out.genres = now.genres.split(',').map(s => s.trim()).filter(Boolean)
    }
    if (moved('synopsis')) out.synopsis = now.synopsis.trim() || null
    return out
  }

  /** Said before the round trip, where it can be: nothing is lost by
   *  asking, but a form that waits on the server to say "a book needs a
   *  title" is slower than it need be. */
  #refusal (changes) {
    if ('title' in changes && !changes.title) return 'A book needs a title.'
    if ('authors' in changes && !changes.authors.length) {
      return 'A book needs at least one author.'
    }
    for (const key of ['series_index', 'first_published']) {
      if (key in changes && changes[key] !== null &&
          !Number.isFinite(changes[key])) {
        return 'That is not a number.'
      }
    }
    return null
  }

  async #save () {
    const changes = this.#changes()
    if (!Object.keys(changes).length) {
      this.close()
      return
    }
    const refused = this.#refusal(changes)
    if (refused) {
      this.#note(refused)
      return
    }
    this.#dom.save.disabled = true
    this.#note('')
    try {
      const response = await fetch(`/api/books/${this.#book.id}/metadata`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(changes)
      })
      const body = await response.json()
      if (!response.ok) {
        this.#note(this.#explain(body.detail))
        return
      }
      this.#form.hidden = true
      await this.#onSaved(body.book)
    } catch (err) {
      this.#note(`Could not save: ${err.message}`)
    } finally {
      this.#dom.save.disabled = false
    }
  }

  /** The server's reason, in words. A 422 from validation arrives as a
   *  list; one from the editor as a sentence. */
  #explain (detail) {
    if (typeof detail === 'string') return detail
    if (Array.isArray(detail) && detail.length) {
      const first = detail[0]
      const field = first.loc?.at(-1) ?? 'a field'
      return `Could not save ${field}: ${first.msg}`
    }
    return 'Could not save.'
  }

  #note (text) {
    this.#dom.note.textContent = text
    this.#dom.note.hidden = !text
  }
}
