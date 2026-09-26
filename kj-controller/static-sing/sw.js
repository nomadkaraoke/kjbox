// Nomad Karaoke singer service worker.
// Handles Web Push + notification click. Scope: /sing/
// sw-v0.23.1

const CACHE = 'nomad-sing-shell-__APP_VERSION__';
const SHELL = [
  '/sing/static/sing.css',
  '/sing/static/sing.js',
  '/sing/static/i18n.js',
  '/sing/static/make.js',
  '/sing/static/messages/en.json',
  '/sing/static/icon-192.png',
  '/sing/static/icon-512.png',
  '/sing/static/badge-72.png',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE).then((cache) => cache.addAll(SHELL)).then(() => self.skipWaiting()),
  );
});

// Shell assets are requested with a cache-busting `?v=<APP_VERSION>` query, so
// a plain cache lookup would never hit the precached copies. Serve shell
// paths network-first (fresh code when online) and fall back to the precache
// (ignoring the query) when offline — the i18n message file in particular
// must never fail silently, or the UI would render raw keys.
const SHELL_PATHS = new Set(SHELL);

self.addEventListener('fetch', (event) => {
  if (event.request.method !== 'GET') return;
  let path;
  try { path = new URL(event.request.url).pathname; } catch { return; }
  if (!SHELL_PATHS.has(path)) return;
  event.respondWith(
    fetch(event.request).then((resp) => {
      if (resp && resp.ok) {
        const copy = resp.clone();
        caches.open(CACHE).then((cache) => cache.put(event.request, copy)).catch(() => {});
      }
      return resp;
    }).catch(() => caches.match(event.request, { ignoreSearch: true })
      .then((hit) => hit || Response.error())),
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) => Promise.all(
      keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)),
    )).then(() => self.clients.claim()),
  );
});

// Pull token from the registration URL's query string. The client registers
// sw.js with ?t=TOKEN so we can build notificationclick URLs that include it.
function getToken() {
  try {
    return new URL(self.location.href).searchParams.get('t') || '';
  } catch {
    return '';
  }
}

self.addEventListener('push', (event) => {
  let data = {};
  try { data = event.data ? event.data.json() : {}; } catch { /* non-JSON */ }
  const title = data.title || 'Nomad Karaoke';
  const opts = {
    body: data.body || '',
    tag: data.tag || 'sing-default',
    icon: data.icon || '/sing/static/icon-192.png',
    badge: data.badge || '/sing/static/badge-72.png',
    data: data.data || {},
    renotify: true,
  };
  event.waitUntil(self.registration.showNotification(title, opts));
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const d = (event.notification.data || {});
  const token = getToken();
  const base = `/sing/?t=${encodeURIComponent(token)}`;
  const url = d.request_id ? `${base}&r=${d.request_id}` : base;
  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((wins) => {
      for (const w of wins) {
        if (w.url.includes('/sing/')) {
          w.focus();
          w.postMessage({ type: 'push-focus', data: d });
          return;
        }
      }
      return self.clients.openWindow(url);
    }),
  );
});
