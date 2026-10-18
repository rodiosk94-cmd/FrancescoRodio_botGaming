/* Radar: l'app si apre anche offline con gli ultimi dati scaricati. */
const CACHE = 'radar-v1';
const GUSCIO = ['./', 'index.html', 'style.css', 'app.js', 'manifest.webmanifest',
  'icons/icon.svg', 'icons/icon-192.png', 'icons/apple-touch-icon.png'];

self.addEventListener('install', (ev) => {
  ev.waitUntil(caches.open(CACHE).then((c) => c.addAll(GUSCIO)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (ev) => {
  ev.waitUntil(caches.keys()
    .then((chiavi) => Promise.all(chiavi.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

async function primaLaRete(req) {
  const cache = await caches.open(CACHE);
  try {
    const r = await fetch(req);
    if (r.ok) cache.put(req, r.clone());
    return r;
  } catch (e) {
    const salvata = await cache.match(req, { ignoreSearch: true });
    if (salvata) return salvata;
    throw e;
  }
}

async function primaLaCache(req) {
  const cache = await caches.open(CACHE);
  const salvata = await cache.match(req);
  const aggiorna = fetch(req).then((r) => {
    if (r.ok || r.type === 'opaque') cache.put(req, r.clone());
    return r;
  }).catch(() => salvata);
  return salvata || aggiorna;
}

self.addEventListener('fetch', (ev) => {
  const req = ev.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin === self.location.origin) {
    // i dati cambiano a ogni report: rete prima, cache se offline. Il resto si aggiorna in background.
    ev.respondWith(url.pathname.includes('/data/') || req.mode === 'navigate' ? primaLaRete(req) : primaLaCache(req));
  } else if (url.hostname.endsWith('fonts.googleapis.com') || url.hostname.endsWith('fonts.gstatic.com')) {
    ev.respondWith(primaLaCache(req));
  }
});
