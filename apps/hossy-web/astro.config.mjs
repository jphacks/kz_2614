// @ts-check
import { defineConfig } from 'astro/config';

// https://astro.build/config
const upstream = process.env.VOICE_API_UPSTREAM;
if (upstream && (upstream !== upstream.trim() || !/^https?:\/\/[a-zA-Z0-9.-]+(?::[0-9]+)?$/.test(upstream))) {
  throw new Error('VOICE_API_UPSTREAM must be an http(s) origin without path or credentials');
}

export default defineConfig({
  vite: {
    server: {
      // Opt-in only: do not connect to or start a local service implicitly.
      proxy: upstream ? {
        '/api/v1/voice/': { target: upstream, changeOrigin: true, timeout: 180000, proxyTimeout: 180000 },
        '/api/v1/dashboard/runtime': { target: upstream, changeOrigin: true, timeout: 10000 },
      } : {},
    },
  },
});
