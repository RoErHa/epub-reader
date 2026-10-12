/* A language's name, from its code.
 *
 * The server keeps codes — `en`, `nl` — and the browser names them, with
 * Intl.DisplayNames rather than a table of ours (§11). The app speaks
 * English, so the names do too: "Dutch", not "Nederlands".
 */

export class LanguageNames {
  static UNKNOWN = 'unknown'
  static #names = new Intl.DisplayNames(['en'], { type: 'language' })

  /** "Dutch" for `nl`; "Unknown" for a book nobody could place. */
  static of (code) {
    if (!code || code === LanguageNames.UNKNOWN) return 'Unknown'
    try {
      return LanguageNames.#names.of(code) ?? code
    } catch {
      return code     // a code Intl refuses is still better than nothing
    }
  }
}
