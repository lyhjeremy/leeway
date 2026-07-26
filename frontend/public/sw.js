// Minimal service worker - exists only to satisfy PWA installability
// criteria (a registered SW with a fetch handler). No offline-caching
// ambition: this app depends entirely on live routing/charging/weather
// APIs, so "works offline" would be a false promise. Pure network
// passthrough.
self.addEventListener('install', () => self.skipWaiting())
self.addEventListener('activate', (event) => event.waitUntil(self.clients.claim()))
self.addEventListener('fetch', (event) => {
  event.respondWith(fetch(event.request))
})
