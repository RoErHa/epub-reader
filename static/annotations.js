/* Highlights and notes.
 *
 * Two pieces of interface: the offer that appears when you have
 * selected something, and the panel that lists what you have marked.
 *
 * Marks are server-side and shared across devices (decision 7), so this
 * writes through to the API rather than keeping its own copy — what you
 * highlighted on the iPad is highlighted on the Mac.
 */

export class Annotations {
  #dom = null
  #reader = null
  #bookId = null
  #marks = []
  #pending = null        // the selection an offer is currently about

  constructor (dom, reader) {
    this.#dom = dom
    this.#reader = reader
    this.#wire()
  }

  // ── the book ──────────────────────────────────────────────────────
  /** Called when a book opens: fetch what is already marked and draw it. */
  async load (bookId) {
    this.#bookId = bookId
    this.#marks = []
    this.hideOffer()
    if (bookId == null) return this.render()
    try {
      const res = await fetch(`/api/books/${bookId}/annotations`)
      if (res.ok) this.#marks = (await res.json()).annotations ?? []
    } catch { /* offline: the book still reads, it is just unmarked */ }
    this.render()
    await this.#reader.drawAll(this.#marks)
  }

  clear () {
    this.#bookId = null
    this.#marks = []
    this.hideOffer()
    this.render()
  }

  // ── the offer ─────────────────────────────────────────────────────
  /** The reader has finished selecting — or has cleared the selection. */
  offer (selection) {
    if (this.#writingNote()) return
    if (!selection || !selection.cfi) return this.hideOffer()
    this.#pending = selection
    const menu = this.#dom.selectionMenu
    menu.hidden = false
    this.#place(menu, selection.rect)
  }

  hideOffer () {
    this.#pending = null
    if (this.#dom.selectionMenu) this.#dom.selectionMenu.hidden = true
  }

  /** A selection settling must not snatch away a note being written. */
  #writingNote () {
    return this.#dom.noteEditor && !this.#dom.noteEditor.hidden
  }

  /** Above the selection where there is room, below it where there is
   *  not — a menu that covers the words you just chose is no use. */
  #place (menu, rect) {
    if (!rect) return
    const height = menu.offsetHeight || 44
    const above = rect.top - height - 8
    menu.style.top = `${above > 8 ? above : rect.bottom + 8}px`
    const width = menu.offsetWidth || 220
    const left = rect.left + rect.width / 2 - width / 2
    menu.style.left = `${Math.max(8, Math.min(left, window.innerWidth - width - 8))}px`
  }

  // ── making a mark ─────────────────────────────────────────────────
  async highlight (color = 'yellow', note = null) {
    const selection = this.#pending
    if (!selection || this.#bookId == null) return
    this.hideOffer()

    const body = {
      cfi: selection.cfi,
      text: selection.text,
      color,
      note,
      fraction: this.#reader.position?.fraction ?? 0
    }
    const res = await fetch(`/api/books/${this.#bookId}/annotations`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    })
    if (!res.ok) return
    const mark = await res.json()
    this.#marks.push(mark)
    this.#marks.sort((a, b) => a.fraction - b.fraction)
    this.render()
    await this.#reader.mark(mark.cfi, mark.color)
  }

  async remove (id) {
    const mark = this.#marks.find(m => m.id === id)
    if (!mark) return
    const res = await fetch(`/api/annotations/${id}`, { method: 'DELETE' })
    if (!res.ok && res.status !== 404) return
    this.#marks = this.#marks.filter(m => m.id !== id)
    this.render()
    await this.#reader.unmark(mark.cfi)
  }

  async annotate (id, note) {
    const res = await fetch(`/api/annotations/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ note })
    })
    if (!res.ok) return
    const updated = await res.json()
    this.#marks = this.#marks.map(m => (m.id === id ? updated : m))
    this.render()
  }

  // ── the panel ─────────────────────────────────────────────────────
  render () {
    const list = this.#dom.marksList
    if (!list) return
    list.replaceChildren()
    if (!this.#marks.length) {
      const empty = document.createElement('p')
      empty.className = 'panel-empty'
      empty.textContent = this.#bookId == null
        ? 'No book open.'
        : 'Nothing highlighted yet. Select some words to mark them.'
      list.append(empty)
      return
    }
    for (const mark of this.#marks) list.append(this.#row(mark))
  }

  #row (mark) {
    const row = document.createElement('div')
    row.className = 'mark-item'
    row.dataset.id = String(mark.id)

    const jump = document.createElement('button')
    jump.className = 'mark-text'
    jump.type = 'button'
    jump.dataset.color = mark.color ?? 'yellow'
    jump.textContent = mark.text
    jump.title = 'Go to this passage'
    jump.addEventListener('click', () => this.#reader.goToMark(mark.cfi))
    row.append(jump)

    if (mark.note) {
      const note = document.createElement('p')
      note.className = 'mark-note'
      note.textContent = mark.note
      row.append(note)
    }

    const annotate = document.createElement('button')
    annotate.className = 'mark-annotate'
    annotate.type = 'button'
    annotate.textContent = mark.note ? 'Edit note' : 'Add note'
    annotate.addEventListener('click', () => this.#editNote(row, mark))
    row.append(annotate)

    const remove = document.createElement('button')
    remove.className = 'mark-remove'
    remove.type = 'button'
    remove.textContent = 'Remove'
    remove.setAttribute('aria-label', `Remove highlight: ${mark.text.slice(0, 40)}`)
    remove.addEventListener('click', () => this.remove(mark.id))
    row.append(remove)
    return row
  }

  /** Editing a note in the panel happens where the note is, rather than
   *  somewhere else on the screen. */
  #editNote (row, mark) {
    if (row.querySelector('.mark-editor')) return
    const editor = document.createElement('div')
    editor.className = 'mark-editor'

    const field = document.createElement('textarea')
    field.rows = 3
    field.value = mark.note ?? ''
    field.setAttribute('aria-label', 'Note')
    editor.append(field)

    const save = document.createElement('button')
    save.className = 'mark-save'
    save.type = 'button'
    save.textContent = 'Save'
    save.addEventListener('click', async () => {
      // An emptied note is no note; the words stay marked either way.
      await this.annotate(mark.id, field.value.trim() || null)
    })

    const cancel = document.createElement('button')
    cancel.className = 'mark-cancel'
    cancel.type = 'button'
    cancel.textContent = 'Cancel'
    cancel.addEventListener('click', () => editor.remove())

    const actions = document.createElement('div')
    actions.className = 'mark-editor-actions'
    actions.append(cancel, save)
    editor.append(actions)

    row.append(editor)
    field.focus()
  }

  // ── wiring ────────────────────────────────────────────────────────
  #wire () {
    const dom = this.#dom
    dom.selectionHighlight?.addEventListener('click', () => this.highlight())
    dom.selectionNote?.addEventListener('click', () => this.#withNote())
    dom.selectionCopy?.addEventListener('click', () => this.#copy())
    dom.noteCancel?.addEventListener('click', () => {
      this.#closeNote()
      this.hideOffer()
    })
    dom.noteSave?.addEventListener('click', async () => {
      const note = dom.noteText.value.trim()
      this.#closeNote()
      await this.highlight('yellow', note || null)
    })
    dom.noteText?.addEventListener('keydown', event => {
      // Enter saves, Shift+Enter is a new line, Escape gives up.
      if (event.key === 'Escape') {
        this.#closeNote()
        this.hideOffer()
      } else if (event.key === 'Enter' && !event.shiftKey) {
        event.preventDefault()
        dom.noteSave.click()
      }
    })
    for (const swatch of dom.selectionColours ?? []) {
      swatch.addEventListener('click',
        () => this.highlight(swatch.dataset.color))
    }
  }

  /* The note is asked for before the mark is made, so cancelling leaves
   * no orphan highlight behind. A composer rather than window.prompt:
   * prompt blocks the page, cannot be styled, and on iOS reads as the
   * browser asking rather than the app. */
  #withNote () {
    if (!this.#pending) return
    const { noteEditor, noteText } = this.#dom
    if (!noteEditor) return
    this.#dom.selectionMenu.hidden = true
    noteText.value = ''
    noteEditor.hidden = false
    this.#place(noteEditor, this.#pending.rect)
    noteText.focus()
  }

  #closeNote () {
    if (this.#dom.noteEditor) this.#dom.noteEditor.hidden = true
  }

  async #copy () {
    const text = this.#pending?.text
    this.hideOffer()
    if (!text) return
    try {
      await navigator.clipboard.writeText(text)
    } catch { /* denied, or no clipboard: nothing to undo */ }
  }
}
