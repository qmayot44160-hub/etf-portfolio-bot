/* PortfolioQuant - Service Worker
 * Stratégie:
 *  - Navigations HTML (mode === 'navigate'): network-first avec fallback cache.
 *    On veut TOUJOURS la dernière UI quand le réseau répond, mais garantir
 *    une ouverture instantanée même hors-ligne.
 *  - Assets statiques (/static/*, CDN polices/scripts): cache-first (stale-while-revalidate).
 *  - API (/api/*): network-only, jamais en cache (données live, sensibles).
 */

const VERSION = 'pq-v16';
const STATIC_CACHE = `pq-static-${VERSION}`;
const HTML_CACHE = `pq-html-${VERSION}`;

const PRECACHE = [
  '/static/quant-icon.svg',
  '/static/quant-logo.svg',
  '/static/manifest.webmanifest'
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(STATIC_CACHE).then((cache) => cache.addAll(PRECACHE))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) => Promise.all(
      keys
        .filter((k) => !k.endsWith(VERSION))
        .map((k) => caches.delete(k))
    )).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;

  const url = new URL(req.url);
  const sameOrigin = url.origin === self.location.origin;

  // Jamais cacher l'API ni l'auth
  if (sameOrigin && (url.pathname.startsWith('/api/') ||
                     url.pathname.startsWith('/login') ||
                     url.pathname.startsWith('/logout') ||
                     url.pathname.startsWith('/health'))) {
    return; // laisse le réseau gérer
  }

  // Navigations (pages HTML): network-first
  if (req.mode === 'navigate') {
    event.respondWith(
      fetch(req).then((res) => {
        const copy = res.clone();
        caches.open(HTML_CACHE).then((c) => c.put(req, copy));
        return res;
      }).catch(() => caches.match(req).then((cached) => cached || caches.match('/')))
    );
    return;
  }

  // Statiques: cache-first + revalidation en arrière-plan (stale-while-revalidate)
  if (sameOrigin && url.pathname.startsWith('/static/')) {
    event.respondWith(
      caches.open(STATIC_CACHE).then((cache) =>
        cache.match(req).then((cached) => {
          const fetchPromise = fetch(req).then((res) => {
            if (res && res.status === 200) cache.put(req, res.clone());
            return res;
          }).catch(() => cached);
          return cached || fetchPromise;
        })
      )
    );
    return;
  }

  // CDN tiers (Plotly, polices…): cache opportuniste
  if (!sameOrigin) {
    event.respondWith(
      caches.open(STATIC_CACHE).then((cache) =>
        cache.match(req).then((cached) => {
          const fetchPromise = fetch(req).then((res) => {
            if (res && res.status === 200 && res.type === 'basic') {
              cache.put(req, res.clone());
            }
            return res;
          }).catch(() => cached);
          return cached || fetchPromise;
        })
      )
    );
  }
});
