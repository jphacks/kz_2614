import assert from 'node:assert/strict';
import { createServer as createHttpServer } from 'node:http';
import { mkdtempSync, writeFileSync, rmSync, mkdirSync, copyFileSync, symlinkSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createServer as createViteServer } from '../../apps/hossy-web/node_modules/vite/dist/node/index.js';
import { voiceUpstream, voiceViteConfig } from '../../apps/hossy-web/scripts/voice-proxy.mjs';
import { dev as astroDev, preview } from '../../apps/hossy-web/node_modules/astro/dist/index.js';
const appRoot = new URL('../../apps/hossy-web/', import.meta.url);
const temp = mkdtempSync(join(tmpdir(), 'voice-dev-'));
const servers = [];
async function listen(server) { servers.push(server); await new Promise(r => server.listen(0, '127.0.0.1', r)); return `http://127.0.0.1:${server.address().port}`; }
async function dev(upstream, shortenTimeout = false) {
  const config = voiceViteConfig(upstream);
  assert.deepEqual(config.preview.proxy, {});
  if (shortenTimeout) for (const proxy of Object.values(config.server.proxy)) { proxy.proxyTimeout = 80; }
  const server = await createViteServer({ configFile: false, root: temp, ...config, server: { ...config.server, host: '127.0.0.1', port: 0 }, logLevel: 'silent' });
  await server.listen(); servers.push(server);
  return `http://127.0.0.1:${server.httpServer.address().port}`;
}
try {
  const file = join(temp, '.env.local');
  writeFileSync(file, 'VOICE_API_UPSTREAM=http://127.0.0.1:8765\nOTHER_PRIVATE=secret\n');
  assert.equal(voiceUpstream({}, file), 'http://127.0.0.1:8765');
  assert.equal(voiceUpstream({ VOICE_API_UPSTREAM: '' }, file), undefined);
  assert.equal(voiceUpstream({ VOICE_API_UPSTREAM: 'https://voice.test' }, file), 'https://voice.test');
  assert.equal(voiceUpstream({}, join(temp, 'absent')), undefined);
  for (const invalid of ['http://user:secret@host', 'http://host/path', 'http://host?x', 'http://host#x', 'http://host:99999', ' http://host', 'http://host\n']) assert.throws(() => voiceUpstream({ VOICE_API_UPSTREAM: invalid }, file));
  assert.equal(process.env.OTHER_PRIVATE, undefined);
  const disabled = await dev(undefined);
  for (const path of ['/api/v1/voice/status', '/api/v1/dashboard/runtime?x=1']) {
    const response = await fetch(disabled + path);
    assert.equal(response.status, 503); assert.equal(response.headers.get('cache-control'), 'no-store');
    assert.deepEqual(await response.json(), { error: 'voice_upstream_unconfigured' });
  }
  const dead = createHttpServer(); const deadOrigin = await listen(dead); await new Promise(r => dead.close(r));
  const unavailable = await dev(deadOrigin);
  const error = await fetch(unavailable + '/api/v1/voice/status');
  assert.equal(error.status, 502); assert.equal(error.headers.get('cache-control'), 'no-store');
  assert.deepEqual(await error.json(), { error: 'voice_upstream_unreachable' });
  const requests = [];
  let releaseStream;
  const upstream = await listen(createHttpServer(async (request, response) => {
    let body = ''; for await (const chunk of request) body += chunk;
    requests.push({ url: request.url, method: request.method, body, header: request.headers['x-voice-test'] });
    if (request.url.includes('/hang')) return;
    if (request.url.includes('/headers-abort')) {
      response.writeHead(200, { 'Content-Length': '9000', 'Content-Encoding': 'gzip', 'Content-Range': 'bytes 0-8999/9000', ETag: 'stale' });
      response.flushHeaders(); setTimeout(() => response.destroy(), 20); return;
    }
    if (request.url.includes('/broken')) { response.writeHead(200); response.write('partial'); setTimeout(() => response.destroy(), 20); return; }
    const status = new URL(request.url, 'http://test').searchParams.get('status');
    if (status) { response.writeHead(Number(status), { 'Content-Type': 'text/html' }); response.end(`upstream-${status}`); return; }
    response.writeHead(200, { 'Content-Type': 'application/octet-stream' });
    response.write('first');
    if (request.url.includes('/pcm?')) releaseStream = () => response.end('second');
    else response.end('second');
  }));
  const live = await dev(upstream);
  const short = await dev(upstream, true);
  const timed = await fetch(short + '/api/v1/voice/hang');
  assert.equal(timed.status, 504); assert.deepEqual(await timed.json(), { error: 'voice_upstream_timeout' });
  const headerAbort = await fetch(live + '/api/v1/voice/headers-abort');
  assert.equal(headerAbort.status, 502);
  assert.deepEqual(await headerAbort.json(), { error: 'voice_upstream_unreachable' });
  for (const header of ['content-length', 'content-encoding', 'content-range', 'etag']) assert.equal(headerAbort.headers.get(header), null, header);
  const broken = await fetch(live + '/api/v1/voice/broken');
  assert.equal(broken.status, 200);
  // Fetch implementations may expose a closed partial body or reject it.
  let partial;
  try { partial = await broken.text(); } catch { partial = 'terminated'; }
  assert.ok(partial === 'partial' || partial === 'terminated', 'Broken stream must terminate without appended JSON');
  for (const status of [401, 403, 503]) {
    const result = await fetch(live + `/api/v1/voice/status?status=${status}`);
    assert.equal(result.status, status); assert.equal(await result.text(), `upstream-${status}`);
    assert.match(result.headers.get('content-type'), /text\/html/);
  }
  const stream = await fetch(live + '/api/v1/voice/sessions/id/pcm?x=1', { method: 'POST', headers: { 'X-Voice-Test': 'retained' }, body: 'pcm-data' });
  const reader = stream.body.getReader(); assert.equal(new TextDecoder().decode((await reader.read()).value), 'first');
  releaseStream();
  assert.equal(new TextDecoder().decode((await reader.read()).value), 'second');
  assert.deepEqual(requests.at(-1), { url: '/api/v1/voice/sessions/id/pcm?x=1', method: 'POST', body: 'pcm-data', header: 'retained' });
  await (await fetch(live + '/api/v1/dashboard/runtime?x=1')).text();
  assert.equal(requests.at(-1).url, '/api/v1/dashboard/runtime?x=1');
  const count = requests.length;
  for (const path of ['/api/v1/dashboard/runtimeevil', '/api/v1/dashboard/runtime/child', '/api/v1/settings']) assert.equal((await fetch(live + path)).status, 404);
  assert.equal(requests.length, count);
  // Load the actual Astro config in an isolated checkout fixture. Its helper must
  // resolve .env.local relative to itself, independent of the process cwd.
  mkdirSync(join(temp, 'scripts')); mkdirSync(join(temp, 'src/pages'), { recursive: true });
  symlinkSync(new URL('node_modules', appRoot), join(temp, 'node_modules'), 'dir');
  copyFileSync(new URL('astro.config.mjs', appRoot), join(temp, 'astro.config.mjs'));
  copyFileSync(new URL('scripts/voice-proxy.mjs', appRoot), join(temp, 'scripts/voice-proxy.mjs'));
  writeFileSync(join(temp, 'src/pages/index.astro'), '<html><body>isolated proxy fixture</body></html>');
  writeFileSync(file, `VOICE_API_UPSTREAM=${upstream}\n`);
  const inline = { root: temp, cacheDir: join(temp, '.astro/'), outDir: join(temp, 'dist/'), server: { host: '127.0.0.1', port: 0 }, logLevel: 'silent', devToolbar: { enabled: false } };
  const saved = process.env.VOICE_API_UPSTREAM; delete process.env.VOICE_API_UPSTREAM;
  try {
    const astro = await astroDev(inline);
    try { assert.equal(await (await fetch(`http://127.0.0.1:${astro.address.port}/api/v1/voice/status`)).text(), 'firstsecond'); }
    finally { await astro.stop(); }
    mkdirSync(join(temp, 'dist'));
    writeFileSync(join(temp, 'dist/index.html'), '<html><body>static fixture</body></html>');
    const beforePreview = requests.length;
    const staticPreview = await preview(inline);
    try { assert.equal((await fetch(`http://127.0.0.1:${staticPreview.port}/api/v1/voice/status`)).status, 404); }
    finally { await staticPreview.stop(); }
    assert.equal(requests.length, beforePreview, 'Static preview must never inherit dev proxy');
  } finally { if (saved === undefined) delete process.env.VOICE_API_UPSTREAM; else process.env.VOICE_API_UPSTREAM = saved; }
  console.log('Actual Astro config: checkout .env.local dev forwarding and static preview no-proxy PASS');
  console.log('Product dev proxy: persisted setting/override, missing503, dead502, timeout504, broken-stream termination, auth/error passthrough, exact routes, streaming POST and headers PASS');
} finally {
  for (const server of servers.reverse()) await new Promise(resolve => { if (server.httpServer) server.close().then(resolve); else server.close(resolve); });
  rmSync(temp, { recursive: true, force: true });
}
