// Nomad Karaoke singer service worker.
// Handles Web Push + notification click. Scope: /sing/
// sw-v0.22.0

const CACHE = 'nomad-sing-shell-v1';
const SHELL = [
  '/sing/static/sing.css',
  '/sing/static/sing.js',
  '/sing/static/icon-192.png',
  '/sing/static/icon-512.png',
  '/sing/static/badge-72.png',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE).then((cache) => cache.addAll(SHELL)).then(() => self.skipWaiting()),
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
