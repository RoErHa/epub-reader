/* Asking for a name.
 *
 * window.prompt blocks the page, cannot be styled, and on iOS reads as
 * Safari asking rather than the app — the same reasons the note composer
 * exists (§8.2). Naming a shelf deserves the same treatment: a field
 * where the thing is, Enter to keep it, Escape to give up.
 */

export class NameComposer {
  #input = null
  #trigger = null
  #onName = null
  #onError = () => {}
  #open = false

  /**
   * @param {HTMLInputElement} input  the field, hidden until asked for
   * @param {HTMLElement} trigger     the button that opens it
   * @param {(name: string) => Promise<unknown>} onName
   * @param {(message: string) => void} onError
   */
  constructor (input, trigger, onName, onError = () => {}) {
    this.#input = input
    this.#trigger = trigger
    this.#onName = onName
    this.#onError = onError

    input.addEventListener('keydown', event => {
      if (event.key === 'Enter') {
        event.preventDefault()
        this.#submit()
      } else if (event.key === 'Escape') {
        event.preventDefault()
        this.close()
      }
    })
    // Tapping elsewhere is giving up, not naming a shelf "".
    input.addEventListener('blur', () => this.close())
    trigger.addEventListener('click', () => this.open())
  }

  open () {
    this.#open = true
    this.#input.value = ''
    this.#input.hidden = false
    this.#trigger.hidden = true
    this.#input.focus()
  }

  /** Give up. Harmless when nothing was open. */
  close () {
    this.#input.hidden = true
    this.#input.value = ''
    if (this.#open) this.#trigger.hidden = false
    this.#open = false
  }

  async #submit () {
    const name = this.#input.value.trim()
    // An empty name is giving up too, not a shelf called "".
    if (!name) return this.close()
    this.close()
    try {
      await this.#onName(name)
    } catch (err) {
      this.#onError(err.message)
    }
  }
}
