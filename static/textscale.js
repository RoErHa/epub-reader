/* Text size that reaches every word (§8.31).
 *
 * The control sets `font-size` on <body>, which moves only text sized
 * relative to its parent. A book that sizes its own text absolutely —
 * 12pt, 16px, `medium`, 1.2rem, or <font size="3"> — kept that size
 * whatever the control said. 36 of the 51 PRCs here use <font size>,
 * and one EPUB in twenty-five uses pt or px.
 *
 * So every absolute size a section declares is rewritten, once, as
 * itself times `--reader-scale`, a custom property the reader's own
 * stylesheet sets. At 100% that is the book's own size to the pixel;
 * the control then moves it live, without walking the document again.
 * Relative sizes — em, %, larger — are left alone: they follow <body>.
 * So is the root's own size: <body> is sized relative to it, and
 * scaling both would scale the words twice.
 */

const VAR = 'var(--reader-scale, 1)'

// CSS's absolute keywords, in the pixels every browser gives them.
const KEYWORDS = {
  'xx-small': 9, 'x-small': 10, small: 13, medium: 16,
  large: 18, 'x-large': 24, 'xx-large': 32, 'xxx-large': 48
}

// What <font size> means: 1–7, or steps from 3. Each is an absolute
// keyword, which is why <body> could not move it.
const FONT_TAG = [null, 10, 13, 16, 18, 24, 32, 48]

export class TextScale {
  /** The rules the reader's stylesheet carries for <font size>. */
  static fontTagRules () {
    const rules = []
    for (let n = 1; n <= 7; n++) {
      const px = FONT_TAG[n]
      const values = [String(n)]
      const step = n - 3
      if (step > 0) values.push(`+${step}`)
      if (step < 0) values.push(String(step))
      for (const v of values) {
        rules.push(`font[size="${v}"] { font-size: calc(${px}px * ${VAR}); }`)
      }
    }
    return rules.join('\n')
  }

  /** `font-size` as a multiple of the scale, or null when it is relative
   *  already and follows <body> by itself. */
  static scaled (value) {
    const v = value.trim().toLowerCase()
    if (v.includes('--reader-scale')) return null
    if (v in KEYWORDS) return `calc(${KEYWORDS[v]}px * ${VAR})`
    const m = v.match(/^(-?[\d.]+)(px|pt|pc|in|cm|mm|rem)$/)
    if (!m) return null
    return `calc(${m[1]}${m[2]} * ${VAR})`
  }

  /** Rewrite one section's absolute sizes. Safe to call twice. */
  static apply (doc) {
    for (const sheet of Array.from(doc?.styleSheets ?? [])) {
      let rules
      try {
        rules = sheet.cssRules
      } catch {
        continue    // a sheet we may not read is one we leave alone
      }
      TextScale.#rules(rules)
    }
    for (const el of doc?.querySelectorAll?.('[style]') ?? []) {
      if (el !== doc.documentElement) TextScale.#declaration(el.style)
    }
  }

  static ROOT = /^\s*(html|:root)\s*$/i

  static #rules (rules) {
    for (const rule of Array.from(rules ?? [])) {
      const root = TextScale.ROOT.test(rule.selectorText ?? '')
      if (rule.style && !root) TextScale.#declaration(rule.style)
      if (rule.cssRules) TextScale.#rules(rule.cssRules)   // @media and co
    }
  }

  static #declaration (style) {
    const value = style.getPropertyValue('font-size')
    if (!value) return
    const scaled = TextScale.scaled(value)
    if (scaled) {
      style.setProperty('font-size', scaled,
        style.getPropertyPriority('font-size'))
    }
  }
}
