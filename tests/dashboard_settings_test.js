"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const source = fs.readFileSync(path.join(__dirname, "../Programs/ServerSide/dashboard/web/dashboard.js"), "utf8");
const html = fs.readFileSync(path.join(__dirname, "../Programs/ServerSide/dashboard/web/index.html"), "utf8");
const numericRules = new Map([...html.matchAll(/<input\s+name="([^"]+)"[^>]*type="number"[^>]*>/g)].map((match) => [match[1], Object.fromEntries([...match[0].matchAll(/(min|max|step)="([^"]+)"/g)].map((attribute) => [attribute[1], attribute[2]]))]));
const settle = () => new Promise(setImmediate);
const defaults = {
  vad: { threshold: 0.65, end_threshold: 0.35, min_speech_ms: 384, silence_ms: 896, preroll_ms: 640, max_speech_seconds: 20 },
  stt: { model_id: "whisper", language: "ja", device: "cuda", compute_type: "float16", vad_filter: true },
  tts: { checkpoint: "irodori-v4", caption: "静かな声" },
  behavior: { persona: "gentle", response_length: "short", question_frequency: "rarely", custom_instruction: "", backchannel_enabled: true },
};
const runtime = {
  checked_at: "2026-10-24T08:00:00Z", active_sessions: 1,
  components: {
    stt: { configured: { model: "next-whisper", device: "cuda", precision: "float16" }, active: [], state: "not_loaded", detail: "モデルはまだ読み込まれていません。" },
    llm: { configured: { model: "next-llm" }, active: [{ model: "loaded-llm", device: null, state: "ready", last_success_at: "2026-10-24T07:59:00Z", configured: { model: "requested-llm" }, detail: "外部サービスのGPU配置は未確認です。" }], state: "ready" },
    tts: { configured: { model: "next-tts", device: "cuda", backend: "local" }, active: [{ model: "loaded-tts", device: "cpu", precision: "fp32", state: "loaded", settings_pending: true }], state: "loaded" },
  },
  vad: { configured: defaults.vad, active: [{ stage: "listening", settings: { ...defaults.vad, silence_ms: 1200 } }] },
};

function browser(options = {}) {
  const elements = new Map(), documentListeners = new Map(), windowListeners = new Map(), timers = new Map();
  const calls = [], pendingRuntime = [], storage = new Map();
  const stats = { runtimeCalls: 0, runtimeInFlight: 0, maxRuntimeInFlight: 0, puts: [] };
  let timerId = 0, now = 0, runtimeMode = "ok", runtimeSnapshot = structuredClone(runtime), ignoreAbort = false;
  const element = (id) => {
    if (!elements.has(id)) {
      const classes = new Set(), listeners = new Map();
      const value = { value: "", textContent: "", type: "text", checked: false, dataset: {}, disabled: false, validityMessage: "", listeners,
        classList: { add(...names) { names.forEach((name) => classes.add(name)); }, remove(...names) { names.forEach((name) => classes.delete(name)); }, contains(name) { return classes.has(name); } },
        setCustomValidity(message) { this.validityMessage = message; },
        addEventListener(name, listener) { listeners.set(name, [...listeners.get(name) || [], listener]); },
        fire(name, event = {}) { return Promise.all((listeners.get(name) || []).map((listener) => listener({ preventDefault() {}, ...event }))); },
      };
      Object.defineProperty(value, "innerHTML", { set() { throw new Error("Runtime data must use textContent"); } });
      elements.set(id, value);
    }
    return elements.get(id);
  };
  const fields = [];
  for (const [section, values] of Object.entries(defaults)) {
    for (const [name, value] of Object.entries(values)) {
      const field = element(`${section}.${name}`);
      field.value = String(value);
      if (typeof value === "boolean") { field.type = "checkbox"; field.checked = value; }
      fields.push(field);
    }
  }
  fields.namedItem = (name) => elements.get(name) || null;
  const form = element("settings-form");
  form.elements = fields;
  form.reportValidity = () => fields.every((field) => {
    if (field.validityMessage) return false;
    const name = [...elements.entries()].find(([, value]) => value === field)?.[0];
    const rules = numericRules.get(name);
    if (!rules) return true;
    const value = Number(field.value), minimum = Number(rules.min || 0);
    if (!field.value.trim() || !Number.isFinite(value) || value < minimum || value > Number(rules.max)) return false;
    if (rules.step !== "any") {
      const steps = (value - minimum) / Number(rules.step || 1);
      if (Math.abs(steps - Math.round(steps)) > 1e-8) return false;
    }
    return true;
  });
  const response = (status, body) => ({ ok: status >= 200 && status < 300, status, json: async () => structuredClone(body) });
  const context = {
    document: { visibilityState: "visible", getElementById: element, addEventListener(name, handler) { documentListeners.set(name, handler); } },
    window: { confirm: () => true, addEventListener(name, handler) { windowListeners.set(name, handler); } },
    sessionStorage: { getItem(key) { return storage.get(key) || null; }, setItem(key, value) { storage.set(key, value); }, removeItem(key) { storage.delete(key); } },
    AbortController, Date,
    setTimeout(callback, delay) { timers.set(++timerId, { callback, due: now + delay }); return timerId; },
    clearTimeout(id) { timers.delete(id); },
    fetch: async (url, request = {}) => {
      calls.push({ url, request });
      if (url.endsWith("/health")) return response(200, { status: "ok" });
      if (url.endsWith("/reference-audio")) return response(200, { uploaded: false });
      if (url.endsWith("/settings")) {
        if (request.method === "PUT") { stats.puts.push(JSON.parse(request.body)); return response(200, { settings: stats.puts.at(-1) }); }
        const settings = structuredClone(defaults);
        if (options.vad) settings.vad = structuredClone(options.vad);
        if (options.legacySettings) delete settings.vad;
        return response(200, { settings, defaults: settings });
      }
      assert.ok(url.endsWith("/runtime"), url);
      stats.runtimeCalls++; stats.runtimeInFlight++; stats.maxRuntimeInFlight = Math.max(stats.maxRuntimeInFlight, stats.runtimeInFlight);
      try {
        if (runtimeMode === "fail") return response(503, { message: "unavailable" });
        if (runtimeMode === "body_pending") {
          return { ok: true, status: 200, json: () => new Promise((resolve, reject) => {
            pendingRuntime.push({ resolve(body) { resolve(structuredClone(body)); }, reject });
            request.signal.addEventListener("abort", () => reject(new DOMException("body aborted", "AbortError")), { once: true });
          }) };
        }
        if (runtimeMode === "pending") {
          return await new Promise((resolve, reject) => {
            pendingRuntime.push({ resolve(body) { resolve(response(200, body)); }, reject });
            if (!ignoreAbort) request.signal.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")), { once: true });
          });
        }
        return response(200, runtimeSnapshot);
      } finally { stats.runtimeInFlight--; }
    },
  };
  vm.createContext(context);
  vm.runInContext(source, context, { filename: "dashboard.js" });
  return {
    element, fields, form, stats, calls, pendingRuntime, requestJson: vm.runInContext("requestJson", context),
    setRuntime(mode, snapshot = runtime, keepPendingOnAbort = false) { runtimeMode = mode; runtimeSnapshot = structuredClone(snapshot); ignoreAbort = keepPendingOnAbort; },
    async tick(milliseconds) {
      now += milliseconds;
      const due = [...timers.entries()].filter(([, timer]) => timer.due <= now);
      for (const [id, timer] of due) { if (timers.delete(id)) timer.callback(); }
      await settle();
    },
    async hidden(value) { context.document.visibilityState = value ? "hidden" : "visible"; documentListeners.get("visibilitychange")?.(); await settle(); },
    async pageEvent(name) { windowListeners.get(name)?.(); await settle(); },
  };
}

async function savedVadIsNumericAndLegacySettingsRemainUsable() {
  const b = browser({ legacySettings: true }); await settle();
  assert.equal(b.element("vad-sensitivity").value, "normal");
  assert.equal(b.element("vad.silence_ms").value, "896");
  b.element("vad-sensitivity").value = "low"; b.element("vad-sensitivity").fire("change");
  assert.equal(b.element("vad.threshold").value, "0.8");
  assert.equal(b.element("vad.end_threshold").value, "0.5");
  b.element("vad.silence_ms").value = "1400";
  b.element("vad.max_speech_seconds").value = "18.5";
  await b.form.fire("submit");
  assert.equal(b.stats.puts.length, 1);
  assert.deepEqual(b.stats.puts[0].vad, { threshold: 0.8, end_threshold: 0.5, min_speech_ms: 384, silence_ms: 1400, preroll_ms: 640, max_speech_seconds: 18.5 });
  assert.match(b.element("settings-state-copy").textContent, /新しい会話/);
}

async function invalidVadCannotBeSaved() {
  const b = browser(); await settle();
  b.element("vad.min_speech_ms").value = "800";
  await b.form.fire("submit");
  assert.equal(b.stats.puts.length, 0);
  assert.match(b.element("vad-validation-message").textContent, /128ms以上/);
  assert.equal(b.element("vad-advanced").open, true);
  b.element("vad.preroll_ms").value = "1000";
  b.element("vad.end_threshold").value = "0.9";
  b.element("vad.end_threshold").fire("input");
  assert.equal(b.element("vad-sensitivity").value, "custom");
  await b.form.fire("submit");
  assert.equal(b.stats.puts.length, 0);
  assert.match(b.element("vad-validation-message").textContent, /終了の値/);
  b.element("vad.end_threshold").value = "0.35";
  b.element("vad.silence_ms").value = "NaN";
  await b.form.fire("submit");
  assert.equal(b.stats.puts.length, 0);
}

async function arbitraryValidCustomVadValuesRemainSavable() {
  const custom = { ...defaults.vad, threshold: 0.625, end_threshold: 0.005, max_speech_seconds: 20.25 };
  const b = browser({ vad: custom }); await settle();
  assert.equal(b.element("vad-sensitivity").value, "custom");
  assert.equal(b.element("vad.threshold").value, "0.625");
  assert.equal(b.element("vad.end_threshold").value, "0.005");
  assert.equal(b.element("vad.max_speech_seconds").value, "20.25");
  assert.equal(b.form.reportValidity(), true, "HTML numeric limits must accept backend-valid custom settings");
  await b.form.fire("submit");
  assert.deepEqual(b.stats.puts[0].vad, custom);
}

async function runtimeDistinguishesConfiguredAndObservedState() {
  const b = browser(); await settle();
  assert.equal(b.element("runtime-stt-state").textContent, "未読み込み");
  assert.match(b.element("runtime-stt-configured").textContent, /next-whisper.*CUDA/);
  assert.match(b.element("runtime-stt-active").textContent, /観測値はありません/);
  assert.match(b.element("runtime-llm-active").textContent, /loaded-llm.*実行デバイス未確認/);
  assert.match(b.element("runtime-llm-active").textContent, /requested-llm/);
  assert.match(b.element("runtime-llm-active").textContent, /最終処理成功/);
  assert.match(b.element("runtime-llm-active").textContent, /GPU配置は未確認/);
  assert.match(b.element("runtime-tts-configured").textContent, /next-tts.*CUDA/);
  assert.match(b.element("runtime-tts-active").textContent, /loaded-tts.*CPU/);
  assert.match(b.element("runtime-tts-active").textContent, /次の会話/);
  assert.match(b.element("runtime-vad").textContent, /896ms[\s\S]*1200ms/);
  const unsafe = structuredClone(runtime); unsafe.components.stt.configured.model = '<img src=x onerror="alert(1)">'; unsafe.components.stt.state = "unknown";
  b.setRuntime("ok", unsafe); b.element("runtime-refresh").fire("click"); await settle();
  assert.equal(b.element("runtime-stt-state").textContent, "未確認");
  assert.match(b.element("runtime-stt-configured").textContent, /<img/);
}

async function runtimeFailureAndTimeoutDoNotBlockSettings() {
  const b = browser(); await settle();
  const previous = b.element("runtime-tts-active").textContent;
  b.setRuntime("fail"); b.element("runtime-refresh").fire("click"); await settle();
  assert.match(b.element("runtime-state-summary").textContent, /前回取得した状態/);
  assert.equal(b.element("runtime-tts-active").textContent, previous);
  assert.equal(b.element("connection-label").textContent, "設定サーバー接続済み");
  b.setRuntime("pending"); b.element("runtime-refresh").fire("click"); await settle();
  b.element("runtime-refresh").fire("click");
  assert.equal(b.stats.maxRuntimeInFlight, 1);
  await b.tick(5000);
  assert.match(b.element("runtime-state-summary").textContent, /タイムアウト/);
  await b.form.fire("submit");
  assert.equal(b.stats.puts.length, 1);
  assert.equal(b.element("save-button").disabled, false);
}

async function hiddenPagesAndTokenChangesFenceOldResponses() {
  const b = browser(); await settle();
  b.setRuntime("pending", runtime, true); b.element("runtime-refresh").fire("click"); await settle();
  const old = b.pendingRuntime.shift();
  b.element("dashboard-token").value = "new-token";
  b.element("dashboard-token").fire("input"); b.element("dashboard-token").fire("change"); await settle();
  const stale = structuredClone(runtime); stale.components.tts.configured.model = "stale-secret-model";
  old.resolve(stale); await settle();
  assert.doesNotMatch(b.element("runtime-tts-configured").textContent, /stale-secret/);
  b.setRuntime("ok"); await b.tick(0);
  const lastRuntime = b.calls.filter((call) => call.url.endsWith("/runtime")).at(-1);
  assert.equal(lastRuntime.request.headers.Authorization, "Bearer new-token");
  assert.equal(b.stats.maxRuntimeInFlight, 1);
  await b.hidden(true); const count = b.stats.runtimeCalls; await b.tick(30000);
  assert.equal(b.stats.runtimeCalls, count);
  await b.hidden(false); assert.equal(b.stats.runtimeCalls, count + 1);
  await b.pageEvent("pagehide"); const afterPageHide = b.stats.runtimeCalls; await b.tick(30000);
  assert.equal(b.stats.runtimeCalls, afterPageHide);
  await b.pageEvent("pageshow"); assert.equal(b.stats.runtimeCalls, afterPageHide + 1);
}

async function malformedSnapshotAndAbortedJsonBodyKeepLastObservation() {
  const b = browser(); await settle();
  const previous = b.element("runtime-tts-active").textContent;
  for (const malformed of [{}, { ...runtime, checked_at: "not-a-date" }, { ...runtime, components: { ...runtime.components, llm: null } }]) {
    b.setRuntime("ok", malformed); b.element("runtime-refresh").fire("click"); await settle();
    assert.match(b.element("runtime-state-summary").textContent, /前回取得した状態/);
    assert.equal(b.element("runtime-tts-active").textContent, previous);
  }
  b.setRuntime("body_pending"); b.element("runtime-refresh").fire("click"); await settle();
  await b.tick(5000);
  assert.match(b.element("runtime-state-summary").textContent, /タイムアウト/);
  assert.equal(b.element("runtime-tts-active").textContent, previous);
  const controller = new AbortController();
  const request = b.requestJson("/api/v1/dashboard/runtime", { signal: controller.signal });
  const rejection = assert.rejects(request, (error) => error.name === "AbortError");
  await settle(); controller.abort(); await rejection;
}

(async () => {
  for (const test of [savedVadIsNumericAndLegacySettingsRemainUsable, invalidVadCannotBeSaved, arbitraryValidCustomVadValuesRemainSavable, runtimeDistinguishesConfiguredAndObservedState, runtimeFailureAndTimeoutDoNotBlockSettings, hiddenPagesAndTokenChangesFenceOldResponses, malformedSnapshotAndAbortedJsonBodyKeepLastObservation]) {
    await test(); process.stdout.write(`PASS ${test.name}\n`);
  }
})().catch((error) => { console.error(error); process.exitCode = 1; });
