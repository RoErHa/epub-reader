// Registers the service worker, where a browser will allow one.
//
// Only a secure origin may have a worker: the tailnet name, served over
// TLS, and loopback. The LAN address is plain HTTP on purpose (§8.18),
// and there the app simply runs as a web page — which is what it was.

export class Installation {
  constructor (navigator, window) {
    this.navigator = navigator
    this.window = window
  }

  get possible () {
    return Boolean(this.window.isSecureContext && this.navigator.serviceWorker)
  }

  async register () {
    if (!this.possible) return null
    try {
      return await this.navigator.serviceWorker.register('/sw.js', { scope: '/' })
    } catch (err) {
      // An app that cannot be installed is still an app.
      console.warn('Service worker not registered:', err)
      return null
    }
  }
}
