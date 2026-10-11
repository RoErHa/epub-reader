// The service worker: what makes this an installable app, and what lets
// it open its own window with the network gone.
//
// It keeps the app shell — the page and /static/ — and NOTHING else.
// Reading position is server-side and shared (decision 7), and a book is
// revalidated by its own ETag (§8.20), so a worker answering /api from a
// cache would be wrong about both. Those requests are never touched.
//
// Network first: online, every request goes out exactly as before and
// the copy kept is refreshed on the way back, so a deploy is seen at the
// next load rather than the one after. The cache is only the answer when
// the network has none.

class ShellCache {
  static NAME = 'books-shell-v1'

  constructor (scope) {
    this.scope = scope
  }

  handles (request) {
    if (request.method !== 'GET') return false
    const url = new URL(request.url)
    if (url.origin !== this.scope.origin) return false
    return url.pathname === '/' || url.pathname.startsWith('/static/')
  }

  async respond (request) {
    const cache = await caches.open(ShellCache.NAME)
    try {
      const response = await fetch(request)
      if (response.ok) await cache.put(request, response.clone())
      return response
    } catch (err) {
      const kept = await cache.match(request, { ignoreSearch: true })
      if (kept) return kept
      throw err
    }
  }

  async prime () {
    const cache = await caches.open(ShellCache.NAME)
    await cache.add('/')
  }

  async dropOthers () {
    const names = await caches.keys()
    await Promise.all(names.filter((n) => n !== ShellCache.NAME)
      .map((n) => caches.delete(n)))
  }
}

const shell = new ShellCache(new URL(self.registration.scope))

self.addEventListener('install', (event) => {
  event.waitUntil(shell.prime().then(() => self.skipWaiting()))
})

self.addEventListener('activate', (event) => {
  event.waitUntil(shell.dropOthers().then(() => self.clients.claim()))
})

self.addEventListener('fetch', (event) => {
  if (shell.handles(event.request)) event.respondWith(shell.respond(event.request))
})
