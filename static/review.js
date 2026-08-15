/* The review queue.
 *
 * A score without a reason is not reviewable, so this shows the parts it
 * was made of and exactly which fields would change — old value beside
 * new — before anything is written.
 */

export class ReviewPanel {
  #dom = null
  #item = null
  #onDecided = null

  constructor (dom, onDecided) {
    this.#dom = dom
    this.#onDecided = onDecided
    dom.reviewAccept.addEventListener('click', () => this.#decide('accept'))
    dom.reviewReject.addEventListener('click', () => this.#decide('reject'))
    dom.reviewAcceptAll.addEventListener('click', () => this.#acceptAll())
  }

  hide () { this.#dom.review.hidden = true }

  show (item) {
    this.#item = item
    this.#dom.review.hidden = false
    this.#dom.reviewTitle.textContent = item.book.title
    this.#dom.reviewScore.textContent =
      `${Math.round(item.score * 100)}% · ${item.provider}`

    this.#renderParts(item.parts)
    this.#renderChanges(item.changes)
  }

  #renderParts (parts) {
    const el = this.#dom.reviewParts
    el.replaceChildren()
    for (const [name, value] of Object.entries(parts ?? {})) {
      const row = document.createElement('div')
      row.className = 'part-row'
      // null means neither side knew — that is not a failure.
      const shown = value == null ? 'not known' : `${Math.round(value * 100)}%`
      row.innerHTML = '<span class="part-name"></span><span class="part-value"></span>'
      row.querySelector('.part-name').textContent = name
      row.querySelector('.part-value').textContent = shown
      if (value != null && value < 0.5) row.classList.add('weak')
      el.append(row)
    }
  }

  #renderChanges (changes) {
    const el = this.#dom.reviewChanges
    el.replaceChildren()
    if (!changes?.length) {
      const p = document.createElement('div')
      p.className = 'muted'
      p.textContent = 'Nothing would change.'
      el.append(p)
      return
    }
    for (const change of changes) {
      const row = document.createElement('div')
      row.className = 'change-row'
      row.innerHTML =
        '<span class="change-field"></span>' +
        '<span class="change-from"></span><span class="change-to"></span>'
      row.querySelector('.change-field').textContent = change.field
      row.querySelector('.change-from').textContent = change.from ?? '—'
      row.querySelector('.change-to').textContent = change.to ?? '—'
      el.append(row)
    }
  }

  async #decide (what) {
    if (!this.#item) return
    await fetch(`/api/review/${this.#item.id}/${what}`, { method: 'POST' })
    this.hide()
    this.#item = null
    await this.#onDecided()
  }

  async #acceptAll () {
    // Irreversible across the whole queue, so it asks first.
    if (!window.confirm('Accept every pending match?')) return
    await fetch('/api/review/accept-all', { method: 'POST' })
    this.hide()
    this.#item = null
    await this.#onDecided()
  }
}
