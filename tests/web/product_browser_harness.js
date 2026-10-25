"use strict";
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { browser, settle, assert } = require('./dashboard_browser_harness');
const productSource = fs.readFileSync(path.join(__dirname, '../../apps/hossy-web/public/app.js'), 'utf8');
const markup = fs.readFileSync(path.join(__dirname, '../../apps/hossy-web/src/pages/index.astro'), 'utf8');
const BASE = Date.parse('2026-10-25T12:00:00+09:00');
function product(options = {}) {
  const storage = options.productStorage || new Map();
  const windowListeners = new Map();
  const environment = { saveFails: false, statusKind: options.statusKind || 'ok', deleteKind: options.deleteKind || 'ok', tailControl: options.tailControl, startKind: options.startKind || 'ok' };
  const app = browser({ ...options, beforeEngine(context, { element, environment: clock, setTimer, clearTimer }) {
    const decorate = (item) => {
      item.dataset ||= {}; item.style ||= {}; item.children ||= [];
      item.append = (...children) => item.children.push(...children);
      item.remove = () => {};
      item.querySelector = (selector) => node(`${item.id || 'child'}:${selector}`);
      item.removeAttribute ||= () => {};
      return item;
    };
    const node = (selector) => decorate(element(selector.startsWith('#') ? selector.slice(1) : selector));
    let child = 0;
    const screens = ['home', 'diary', 'routine', 'sounds', 'about'].map((name) => { const item = node(`screen-${name}`); item.dataset.screen = name; return item; });
    const nav = ['home', 'diary', 'routine', 'sounds'].map((name) => { const item = node(`nav-${name}`); item.dataset.go = name; return item; });
    const sounds = ['みみもとささやき', 'やさしい雨音', 'ふわふわの雲', 'しずかな波音'].map((name) => { const item = node(`sound-${name}`); item.dataset.sound = name; return item; });
    const durations = [15, 30, 45, 60, 75, 90].map((value) => { const item = node(`duration-${value}`); item.dataset.durationStep = String(value); return item; });
    context.document.querySelector = node;
    context.document.querySelectorAll = (selector) => ({ '[data-screen]': screens, '.nav-item': nav, '[data-go]': nav, '[data-sound]': sounds, '[data-duration-step]': durations }[selector] || []);
    context.document.createElement = () => node(`created-${++child}`);
    context.document.createElementNS = () => node(`created-${++child}`);
    for (const id of ['voice-start-button', 'voice-stop-button', 'voice-call-status', 'voice-transcript', 'voice-response', 'voice-audio', 'voice-microphone-select']) {
      assert.ok(markup.includes(`id="${id}"`), `Product markup must render shared engine element ${id}`);
      decorate(element(id));
    }
    context.window.matchMedia = context.matchMedia = () => ({ matches: false, addEventListener() {} });
    context.window.addEventListener = (name, handler) => {
      if (!windowListeners.has(name)) windowListeners.set(name, []);
      windowListeners.get(name).push(handler);
    };
    context.window.scrollTo = () => {};
    context.navigator.userAgent = 'Product VM';
    context.navigator.platform = 'MacIntel';
    context.location = { origin: 'https://product.test', pathname: '/', search: '', hash: '' };
    context.history = { pushState() {}, replaceState() {} };
    context.localStorage = {
      get length() { return storage.size; }, key: (index) => [...storage.keys()][index],
      getItem: (key) => { if (environment.readFails) throw new Error('Storage unavailable'); return storage.get(key) || null; },
      removeItem(key) { environment.beforeRemove?.(key); storage.delete(key); },
      setItem(key, value) { if (environment.saveFails) throw new Error('QuotaExceeded'); storage.set(key, value); },
    };
    context.Date = class extends Date { constructor(...args) { super(...(args.length ? args : [BASE + clock.now])); } static now() { return BASE + clock.now; } };
    const interval = (callback, ms) => { let id; const tick = () => { callback(); id = setTimer(tick, ms); }; id = setTimer(tick, ms); return id; };
    context.setInterval = interval;
    const originalFetch = context.fetch;
    context.fetch = async (url, request) => {
      if (url !== '/api/v1/voice/status') {
        const response = await originalFetch(url, request);
        if (url === '/api/v1/voice/sessions' && request?.method === 'POST') {
          const kind = environment.startKind;
          return { ...response, redirected: kind === 'redirect', headers: { get: () => kind === 'html' ? 'text/html' : 'application/json' },
            json: async () => {
              if (kind === 'invalid-json') throw new Error('Invalid JSON');
              const body = await response.json();
              const cases = { array: [], null: null, 'missing-id': { ...body, session_id: undefined }, 'blank-id': { ...body, session_id: ' ' },
                'wrong-generation': { ...body, generation: -1 }, 'unsafe-generation': { ...body, generation: Number.MAX_SAFE_INTEGER + 1 },
                'missing-state': { ...body, state: undefined }, 'wrong-state': { ...body, state: 'stopped' } };
              return Object.hasOwn(cases, kind) ? cases[kind] : body;
            },
          };
        }
        if (request?.method !== 'DELETE') return response;
        environment.deleteSignal = request.signal;
        const kind = environment.deleteKind;
        if (kind === 'pending') return new Promise((resolve) => { environment.resolveDelete = resolve; });
        return { ...response, redirected: kind === 'redirect', headers: { get: () => kind === 'html' ? 'text/html' : 'application/json' },
          json: async () => {
            if (kind === 'invalid-json') throw new Error('Invalid JSON');
            if (kind === 'pending-json') return new Promise((resolve) => { environment.resolveDeleteJSON = resolve; });
            if (kind === 'wrong-session') return { state: 'stopped', session_id: 'another-session' };
            return { ...await response.json(), tail_control: environment.tailControl, audio_device_stop_confirmed: true };
          },
        };
      }
      environment.statusRequests = (environment.statusRequests || 0) + 1;
      const kind = environment.statusKind;
      environment.statusSignal = request.signal;
      if (kind === 'pending') return new Promise((resolve) => { environment.resolveStatus = resolve; });
      if (kind === 'network') throw new Error('Network unavailable');
      const status = kind === 'unauthorized' ? 401 : kind === 'forbidden' ? 403 : kind === 'unavailable' ? 503 : 200;
      return {
        ok: status === 200, status, redirected: kind === 'redirect', headers: { get: () => kind === 'html' ? 'text/html' : 'application/json' },
        json: async () => {
          if (kind === 'invalid-json') throw new Error('Invalid JSON');
          if (kind === 'pending-json') return new Promise((resolve) => { environment.resolveStatusJSON = resolve; });
          return kind === 'wrong-shape' ? { ok: true } : { voice_api: 'available', models: 'checked_on_first_turn', active_sessions: 0, max_active_sessions: 4, session_ttl_seconds: 900 };
        },
      };
    };
    vm.runInNewContext(productSource, context, { filename: 'product-app.js' });
  } });
  return { ...app, productEnvironment: environment, productStorage: storage,
    records: () => [...storage.entries()].filter(([key]) => key.startsWith('nengitsune-session-v2:')).map(([, value]) => JSON.parse(value)),
    windowEvent(name, event = {}) { for (const callback of windowListeners.get(name) || []) callback(event); },
    eval: (code) => vm.runInNewContext(code, app.context),
  };
}
module.exports = { product, settle, assert };
