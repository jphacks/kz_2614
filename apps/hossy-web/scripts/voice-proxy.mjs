import { readFileSync } from 'node:fs';
import { parseEnv } from 'node:util';

// Team LAN default for development only; explicit settings stay server-side.
const DEFAULT_VOICE_API_UPSTREAM = 'http://192.168.100.113:8765';
export function voiceUpstream(env = process.env, file = new URL('../.env.local', import.meta.url)) {
  let value;
  if (Object.hasOwn(env, 'VOICE_API_UPSTREAM')) value = env.VOICE_API_UPSTREAM;
  else {
    try { value = parseEnv(readFileSync(file, 'utf8')).VOICE_API_UPSTREAM; }
    catch (error) { if (error.code !== 'ENOENT') throw new Error('Cannot read apps/hossy-web/.env.local'); }
  }
  // Missing configuration uses the requested PC; an explicit empty value disables it.
  value ??= DEFAULT_VOICE_API_UPSTREAM;
  if (!value) return undefined;
  let url;
  try { url = new URL(value); } catch { /* Report without disclosing the setting. */ }
  if (!/^https?:\/\/[a-zA-Z0-9.-]+(?::[0-9]+)?$/.test(value) || !url?.hostname) {
    throw new Error('VOICE_API_UPSTREAM must be an http(s) origin without path or credentials');
  }
  return value;
}

export function isVoicePath(url = '') {
  const pathname = url.split('?')[0];
  return pathname.startsWith('/api/v1/voice/') || pathname === '/api/v1/dashboard/runtime';
}

function jsonError(response, status, code) {
  if (response.destroyed || response.writableEnded) return;
  if (response.headersSent) { response.destroy(); return; }
  for (const header of ['Content-Length', 'Content-Encoding', 'Transfer-Encoding', 'Content-Range', 'ETag']) response.removeHeader(header);
  response.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store' });
  response.end(JSON.stringify({ error: code }));
}

export function voiceViteConfig(upstream) {
  const proxy = (timeout) => ({
    target: upstream, changeOrigin: true, proxyTimeout: timeout,
    configure(server) {
      // Handle transport failures before Vite's empty fallback response;
      // upstream HTTP responses (including authentication/errors) pass through.
      const timedOut = new WeakSet();
      server.on('proxyReq', (outgoing, request) => {
        outgoing.once('timeout', () => timedOut.add(request));
      });
      server.on('proxyRes', (incoming, _request, response) => {
        incoming.once('aborted', () => jsonError(response, 502, 'voice_upstream_unreachable'));
        incoming.once('error', () => { if (!response.destroyed) jsonError(response, 502, 'voice_upstream_unreachable'); });
      });
      server.on('error', (error, request, response) => {
        const timeout = timedOut.has(request) || ['ETIMEDOUT', 'ESOCKETTIMEDOUT'].includes(error.code);
        jsonError(response, timeout ? 504 : 502, timeout ? 'voice_upstream_timeout' : 'voice_upstream_unreachable');
      });
    },
  });
  return {
    plugins: [{
      name: 'voice-api-configuration',
      configureServer(server) {
        if (!upstream) server.middlewares.use((request, response, next) => {
          if (isVoicePath(request.url)) jsonError(response, 503, 'voice_upstream_unconfigured');
          else next();
        });
      },
    }],
    server: { proxy: upstream ? {
      '^/api/v1/voice/': proxy(180000),
      '^/api/v1/dashboard/runtime(?:\\?|$)': proxy(10000),
    } : {} },
    // Vite otherwise inherits server.proxy for Astro's static preview.
    preview: { proxy: {} },
  };
}
