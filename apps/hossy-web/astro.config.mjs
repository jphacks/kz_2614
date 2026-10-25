// @ts-check
import { defineConfig } from 'astro/config';
import { voiceUpstream, voiceViteConfig } from './scripts/voice-proxy.mjs';

export default defineConfig({ vite: voiceViteConfig(voiceUpstream()) });
