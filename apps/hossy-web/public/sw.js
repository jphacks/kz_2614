const CACHE_NAME = 'nengitsune-shell-v7-mvp';
const NETWORK_WAIT_MS = 4000;
const CORE_FILES = ['/', '/app.js', '/voice_call.js', '/voice_pcm_worklet.js', '/voice_pcm_output_worklet.js', '/nengitsune.jpg', '/nengitsune-loading.jpg', '/manifest.json', '/icons/nengitsune-192.png', '/icons/nengitsune-512.png', '/icons/nengitsune-maskable-512.png'];

function cachePath(url) {
  // Query-bearing pages can be auth callbacks; only versioned assets are normalized.
  if (url.origin !== self.location.origin || (url.pathname === '/' && url.search)) return null;
  return CORE_FILES.includes(url.pathname) || /^\/_astro\/[\w.-]+\.(css|js)$/.test(url.pathname) ? url.pathname : null;
}
async function isStaticResponse(response, path) {
  if (!response.ok || response.redirected || response.type === 'opaque' || response.type === 'opaqueredirect') return false;
  const type = response.headers.get('Content-Type') || '';
  if (path === '/') {
    if (!type.includes('text/html')) return false;
    const html = await response.clone().text();
    return html.includes('id="main-content"') && html.includes('id="screen-home"');
  }
  if (path.endsWith('.js')) return /(?:javascript|ecmascript)/.test(type);
  if (path.endsWith('.css')) return type.includes('text/css');
  if (path.endsWith('.json')) return /json/.test(type);
  return type.startsWith('image/');
}
async function networkWithinDeadline(request) {
  const controller = new AbortController();
  let timer;
  try {
    return await Promise.race([
      fetch(request, { cache: 'no-cache', signal: controller.signal }),
      new Promise((_, reject) => {
        timer = setTimeout(() => { controller.abort(); reject(new Error('network_deadline')); }, NETWORK_WAIT_MS);
      }),
    ]);
  } finally { clearTimeout(timer); }
}
self.addEventListener('install', (event) => {
  event.waitUntil((async () => {
    const cache = await caches.open(CACHE_NAME);
    const home = await networkWithinDeadline('/');
    if (!(await isStaticResponse(home, '/'))) throw new Error('application_shell_unavailable');
    const html = await home.clone().text();
    const styles = [...html.matchAll(/<link\b[^>]*rel="stylesheet"[^>]*href="([^"]+)"/g)]
      .map((match) => cachePath(new URL(match[1], self.location.origin))).filter(Boolean);
    const assets = await Promise.all([...new Set([...CORE_FILES.slice(1), ...styles])].map(async (path) => {
      const response = await networkWithinDeadline(path);
      if (!(await isStaticResponse(response, path))) throw new Error('static_asset_unavailable');
      return [path, response];
    }));
    await cache.put('/', home);
    await Promise.all(assets.map(([path, response]) => cache.put(path, response)));
    await self.skipWaiting();
  })());
});
self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    const names = await caches.keys();
    await Promise.all(names.filter((name) => /^(nemuri|nengitsune)-shell-/.test(name) && name !== CACHE_NAME).map((name) => caches.delete(name)));
    await self.clients.claim();
  })());
});
self.addEventListener('fetch', (event) => {
  const request = event.request;
  const path = cachePath(new URL(request.url));
  // API, audio, identity, callbacks and arbitrary routes are network-only.
  if (request.method !== 'GET' || !path) return;
  event.respondWith((async () => {
    const cache = await caches.open(CACHE_NAME);
    try {
      const response = await networkWithinDeadline(request);
      if ([401, 403].includes(response.status) || response.redirected) {
        if (path === '/') await cache.delete('/');
        return response;
      }
      if (response.status >= 500) return (await cache.match(path)) || response;
      if (await isStaticResponse(response, path)) {
        event.waitUntil(cache.put(path, response.clone()).catch(() => {}));
      } else if (path === '/') {
        await cache.delete('/');
      }
      return response;
    } catch {
      const cached = await cache.match(path);
      if (cached) return cached;
      if (path === '/') return new Response('<!doctype html><html lang="ja"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>接続を確認してください</title><p>接続できません。通信を確認して再読み込みしてください。</p></html>', { status: 503, headers: { 'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store' } });
      return Response.error();
    }
  })());
});
