/* A book's blurb, shown as what it is.
 *
 * Most synopses here are HTML — an OPF's dc:description and a MOBI's EXTH
 * description both carry markup, and 214 of the 356 the real library holds
 * have it. Set as text, they read as a page of tags.
 *
 * It is somebody else's markup, though, and only its shape is wanted. So
 * the blurb is parsed into an inert document — DOMParser runs no script
 * and fetches nothing — and rebuilt here element by element from a closed
 * list, with no attributes at all. Anything not on the list gives up its
 * words and loses itself (`<font>`, `<span>`, Word's `<st1:place>`, and a
 * link, which a blurb has no business making); what can only do harm goes
 * with its contents.
 */

export class Synopsis {
  static KEEP = new Set([
    'p', 'br', 'em', 'i', 'strong', 'b', 'u', 'h3', 'h4', 'h5', 'h6',
    'ul', 'ol', 'li', 'blockquote', 'hr'
  ])

  // Headings inside a blurb are its own sub-heads ("From Publishers
  // Weekly"), so they are brought down below the panel's own heading.
  static RENAME = { h1: 'h4', h2: 'h4', h3: 'h4', h5: 'h4', h6: 'h4' }

  static DROP = new Set([
    'script', 'style', 'iframe', 'object', 'embed', 'img', 'svg', 'math',
    'video', 'audio', 'source', 'picture', 'canvas', 'template', 'form',
    'input', 'button', 'select', 'textarea', 'link', 'meta', 'head', 'title',
    'noscript', 'frame', 'frameset'
  ])

  /** True when the blurb is markup rather than prose. */
  static isHtml (text) {
    return /<\/?[a-z][\w:-]*(\s[^>]*)?\/?>/i.test(text) ||
      /&(#\d+|#x[\da-f]+|[a-z]+);/i.test(text)
  }

  /** Fill `target` with the blurb, as HTML where it is HTML. */
  static render (target, text) {
    target.replaceChildren()
    const html = Synopsis.isHtml(text)
    target.classList.toggle('plain', !html)
    if (!html) {
      target.textContent = text
      return
    }
    const inert = new DOMParser().parseFromString(text, 'text/html')
    target.append(Synopsis.#copy(inert.body, target.ownerDocument))
  }

  static #copy (from, doc) {
    const out = doc.createDocumentFragment()
    for (const node of from.childNodes) {
      if (node.nodeType === Node.TEXT_NODE) {
        out.append(doc.createTextNode(node.data))
        continue
      }
      if (node.nodeType !== Node.ELEMENT_NODE) continue
      const name = node.localName.toLowerCase()
      if (Synopsis.DROP.has(name)) continue
      const children = Synopsis.#copy(node, doc)
      const kept = Synopsis.RENAME[name] ?? name
      if (Synopsis.KEEP.has(kept)) {
        const element = doc.createElement(kept)
        element.append(children)
        out.append(element)
      } else {
        out.append(children)
      }
    }
    return out
  }
}
