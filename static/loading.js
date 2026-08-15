/* The loading indicator: the book's own cover, turning.
 *
 * Two phases, because only one of them can be measured honestly.
 *
 *   downloading — a real percentage, from Content-Length against bytes
 *                 read off the stream.
 *   preparing   — foliate exposes no progress while it parses and lays
 *                 out a section, and a 1.2 MB single-section book takes
 *                 6-8 s of it. Rather than invent a number that would be
 *                 wrong, the bar goes indeterminate and the label says
 *                 what is happening.
 */

export class LoadingIndicator {
  #el = null
  #cover = null
  #book = null
  #fill = null
  #label = null

  constructor (root = document) {
    this.#el = root.getElementById('loading')
    this.#book = root.querySelector('#loading .loading-book')
    this.#cover = root.querySelector('#loading .loading-cover')
    this.#fill = root.querySelector('#loading .loading-fill')
    this.#label = root.querySelector('#loading .loading-label')
  }

  /** Start, showing this book's cover if it has one. */
  show ({ cover = null, title = '' } = {}) {
    if (!this.#el) return
    if (cover) {
      this.#cover.src = cover
      this.#cover.alt = title ? `Cover of ${title}` : ''
      this.#book.classList.remove('no-cover')
    } else {
      this.#cover.removeAttribute('src')
      this.#book.classList.add('no-cover')
    }
    this.downloading(0)
    this.#el.hidden = false
  }

  downloading (fraction) {
    if (!this.#el) return
    const percent = Math.min(100, Math.max(0, Math.round(fraction * 100)))
    this.#el.dataset.phase = 'downloading'
    this.#fill.style.width = `${percent}%`
    this.#label.textContent = `Downloading ${percent}%`
  }

  /** No measurable progress from here on — say so instead of guessing. */
  preparing (note = '') {
    if (!this.#el) return
    this.#el.dataset.phase = 'preparing'
    this.#fill.style.width = '100%'
    this.#label.textContent = note ? `Preparing — ${note}` : 'Preparing…'
  }

  hide () {
    if (!this.#el) return
    this.#el.hidden = true
    this.#el.dataset.phase = 'idle'
    this.#fill.style.width = '0%'
    // Let go of the image so a closed reader is not holding a decode.
    this.#cover.removeAttribute('src')
  }
}

/** Fetch a book, reporting real progress while the bytes arrive.
 *
 * Returns a File, not a Blob: foliate sniffs the format partly by name
 * (isCBZ/isFBZ read file.name), and its own fetch path builds a File for
 * exactly that reason. A bare Blob fails to open.
 */
export async function fetchWithProgress (url, onProgress, name = 'book.epub') {
  const response = await fetch(url)
  if (!response.ok) throw new Error(String(response.status))

  const total = Number(response.headers.get('content-length')) || 0
  const type = response.headers.get('content-type') || 'application/epub+zip'
  // Without a length or a stream there is nothing honest to report.
  if (!total || !response.body) {
    return new File([await response.blob()], name, { type })
  }

  const reader = response.body.getReader()
  const chunks = []
  let received = 0
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    chunks.push(value)
    received += value.length
    onProgress?.(received / total)
  }
  return new File(chunks, name, { type })
}
