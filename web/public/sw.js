const STATIC_CACHE = 'threadcells-fingerprinted-static-v1'
const LEGACY_STATIC_CACHE_PREFIX = 'threadmesh-fingerprinted-static-'

self.addEventListener('install', () => {
  self.skipWaiting()
})

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys()
      .then(names => Promise.all(names
        .filter(name => (
          name.startsWith(LEGACY_STATIC_CACHE_PREFIX)
          || (name.startsWith('threadcells-fingerprinted-static-') && name !== STATIC_CACHE)
        ))
        .map(name => caches.delete(name))))
      .then(() => self.clients.claim()),
  )
})

self.addEventListener('fetch', event => {
  const request = event.request
  if (request.method !== 'GET') return

  const url = new URL(request.url)
  if (url.origin !== self.location.origin || !url.pathname.startsWith('/assets/')) return

  event.respondWith(
    caches.open(STATIC_CACHE).then(async cache => {
      const cached = await cache.match(request)
      if (cached) return cached

      const response = await fetch(request)
      if (response.ok && response.type === 'basic') await cache.put(request, response.clone())
      return response
    }),
  )
})
