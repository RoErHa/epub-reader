/* Table of contents panel.
 *
 * The engine hands back a nested structure (items with .subitems), so
 * this flattens it for display while keeping depth as an indent.
 */

export class TableOfContents {
  #el = null
  #onSelect = null
  #items = []

  constructor (el, onSelect) {
    this.#el = el
    this.#onSelect = onSelect
  }

  clear () {
    this.#items = []
    this.#el.replaceChildren()
    this.#message('No book open.')
  }

  render (toc) {
    this.#items = this.#flatten(toc)
    this.#el.replaceChildren()
    if (!this.#items.length) return this.#message('This book has no contents.')

    for (const item of this.#items) {
      const row = document.createElement('button')
      row.className = 'toc-item'
      row.dataset.href = item.href ?? ''
      row.style.paddingLeft = `${14 + item.depth * 14}px`
      row.textContent = item.label
      row.addEventListener('click', () => this.#onSelect(item))
      this.#el.append(row)
    }
  }

  /** Highlight the entry the reader is currently inside. */
  mark (tocItem) {
    const href = tocItem?.href ?? null
    for (const el of this.#el.querySelectorAll('.toc-item')) {
      el.classList.toggle('current', href !== null && el.dataset.href === href)
    }
  }

  #flatten (items, depth = 0) {
    const out = []
    for (const item of items ?? []) {
      out.push({
        label: (item.label ?? '').trim() || 'Untitled',
        href: item.href,
        depth
      })
      if (item.subitems?.length) out.push(...this.#flatten(item.subitems, depth + 1))
    }
    return out
  }

  #message (text) {
    const p = document.createElement('div')
    p.className = 'msg'
    p.textContent = text
    this.#el.replaceChildren(p)
  }
}
