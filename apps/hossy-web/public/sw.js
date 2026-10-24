const CACHE_NAME = 'nengitsune-shell-v2';
const CORE_FILES = ['/', '/app.js?v=20261024-1', '/nengitsune-plush.png', '/manifest.json', '/icons/nengitsune-192.png', '/icons/nengitsune-512.png'];

self.addEventListener('install', (event) => {
  event.waitUntil((async () => {
    const cache = await caches.open(CACHE_NAME);
    await cache.addAll(CORE_FILES);
    const home = await cache.match('/');
    const html = await home.text();
    const stylesheets = [...html.matchAll(/<link\b[^>]*rel="stylesheet"[^>]*href="([^"]+)"/g)].map((match) => match[1]);
    await cache.addAll(stylesheets);
    await self.skipWaiting();
  })());
});

self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    const names = await caches.keys();
    await Promise.all(names.filter((name) => (name.startsWith('nemuri-shell-') || name.startsWith('nengitsune-shell-')) && name !== CACHE_NAME).map((name) => caches.delete(name)));
    await self.clients.claim();
  })());
});

self.addEventListener('fetch', (event) => {
  const request = event.request;
  if (request.method !== 'GET' || new URL(request.url).origin !== self.location.origin) return;
  event.respondWith((async () => {
    try {
      const response = await fetch(request, { cache: 'no-cache' });
      if (response.ok) {
        const cache = await caches.open(CACHE_NAME);
        event.waitUntil(cache.put(request, response.clone()));
      }
      return response;
    } catch {
      return (await caches.match(request)) || (request.mode === 'navigate' ? await caches.match('/') : Response.error());
    }
  })());
});
