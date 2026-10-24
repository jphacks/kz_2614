"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const source = fs.readFileSync(path.join(__dirname, "../Programs/ServerSide/dashboard/web/voice_call.js"), "utf8");
const settle = () => new Promise(setImmediate);

function browser(options = {}) {
  const elements = new Map(), timers = new Map(), documentListeners = new Map(), mediaListeners = new Map();
  const calls = [], streams = [], contexts = [], worklets = [], microphoneConstraints = [], servers = new Map();
  const pendingStarts = [], pendingMicrophones = [], pendingPCM = [], pendingGET = [], pendingDeletes = [];
  const storage = options.storage || new Map();
  const stats = { starts: 0, microphoneRequests: 0, resumes: 0, plays: 0, pcmInFlight: 0, maxPCMInFlight: 0, pcmAttempts: 0, modules: [] };
  const environment = { now: 0, allowPlayback: !options.autoplayReject };
  let timerId = 0;
  const setTimer = (callback, delay = 0) => { timers.set(++timerId, { callback, due: environment.now + delay }); return timerId; };
  const clearTimer = (id) => timers.delete(id);
  const response = (status, body) => ({ ok: status >= 200 && status < 300, status, json: async () => body });
  const element = (id) => {
    if (options.optionalDom === false && ["voice-input-level", "voice-microphone-status", "voice-mode-tag", "voice-record-button"].includes(id)) return null;
    if (!elements.has(id)) elements.set(id, {
      value: "", textContent: "", paused: true, ended: false, hidden: false, children: [],
      classList: { remove() {}, add() {}, toggle() {} }, listeners: new Map(),
      replaceChildren() { this.children = []; }, appendChild(child) { this.children.push(child); },
      addEventListener(name, handler) { this.listeners.set(name, handler); },
      dispatch(name) { this.listeners.get(name)?.(); this[`on${name}`]?.(); },
      click() { this.listeners.get("click")?.(); },
      pause() { this.paused = true; }, load() { this.paused = true; this.ended = false; }, removeAttribute(name) { this[name] = ""; },
      play() {
        stats.plays++;
        if (!environment.allowPlayback) return Promise.reject(new Error("Autoplay denied"));
        this.paused = false; this.ended = false; this.dispatch("play"); return Promise.resolve();
      },
      finish() { this.paused = true; this.ended = true; this.dispatch("ended"); },
    });
    return elements.get(id);
  };
  function deferred(list, signal, makeResponse, ignoreAbort = false) {
    return new Promise((resolve, reject) => {
      list.push({ signal, resolve(value) { resolve(makeResponse(value)); }, reject(error = new Error("Network unavailable")) { reject(error); } });
      if (!ignoreAbort) signal?.addEventListener("abort", () => { const error = new Error("Aborted"); error.name = "AbortError"; reject(error); }, { once: true });
    });
  }
  function newStream() {
    const listeners = new Map();
    const stream = { id: streams.length + 1, stopped: false };
    const track = { stop() { stream.stopped = true; }, addEventListener(name, handler) { listeners.set(name, handler); } };
    stream.getTracks = () => [track]; stream.getAudioTracks = () => [track]; stream.disconnectDevice = () => listeners.get("ended")?.();
    streams.push(stream); return stream;
  }
  function node() {
    return { disconnected: false, connections: [], connect(target) { this.connections.push(target); }, disconnect() { this.disconnected = true; } };
  }
  class Context {
    constructor() {
      this.state = "suspended"; this.destination = {}; this.nodes = []; contexts.push(this);
      this.audioWorklet = { addModule: async (url) => { stats.modules.push(url); if (options.failModule) throw new Error("Worklet unavailable"); } };
    }
    resume() { stats.resumes++; this.state = "running"; return Promise.resolve(); }
    close() { this.state = "closed"; return Promise.resolve(); }
    createMediaStreamSource() { const value = node(); this.nodes.push(value); return value; }
    createGain() { const value = { ...node(), gain: { value: 1 } }; this.nodes.push(value); return value; }
  }
  class Worklet {
    constructor(context, name, config) {
      Object.assign(this, node()); this.port = { onmessage: null }; this.name = name; this.config = config;
      context.nodes.push(this); worklets.push(this);
    }
    emit(value = 0, level = Math.abs(value) / 32768) {
      const pcm = new ArrayBuffer(8192); const view = new DataView(pcm);
      for (let offset = 0; offset < 8192; offset += 2) view.setInt16(offset, value, true);
      this.port.onmessage?.({ data: { type: "pcm", pcm, level } });
      return pcm;
    }
  }
  const snapshot = (server, extra = {}) => ({ session_id: server.id, generation: 0, next_sequence: server.nextSequence, stage: server.stage,
    turn_id: server.turnId, transcript: server.transcript, response_text: server.responseText,
    ...(server.turnId ? { speech: { url: `/speech/${server.turnId}`, media_type: "audio/wav" } } : {}), ...extra });
  function acceptPCM(server, request) {
    const sequence = Number(request.headers["X-Audio-Sequence"]);
    if (server.acks.has(sequence)) return response(200, server.acks.get(sequence));
    assert.equal(sequence, server.nextSequence, "Server saw a sequence gap");
    server.nextSequence++;
    server.received.push(request.body);
    const nextStage = options.ackStages?.[sequence];
    if (nextStage) server.stage = nextStage;
    const ack = snapshot(server, { sequence }); server.acks.set(sequence, ack);
    return response(200, ack);
  }
  const context = {
    document: { visibilityState: "visible", getElementById: element, createElement() { return { value: "", textContent: "" }; }, addEventListener(name, handler) { documentListeners.set(name, handler); } },
    window: { isSecureContext: true, AudioContext: Context, AudioWorkletNode: Worklet, addEventListener() {} },
    navigator: { mediaDevices: {
      addEventListener(name, handler) { mediaListeners.set(name, handler); },
      enumerateDevices: async () => options.microphones || [
        { kind: "audioinput", deviceId: "default", label: "Default microphone" },
        { kind: "audioinput", deviceId: "hyperx-device", label: "HyperX headset microphone" },
      ],
      getUserMedia: (constraints) => {
        stats.microphoneRequests++; microphoneConstraints.push(constraints);
        if (!options.deferMicrophone) return Promise.resolve(newStream());
        return new Promise((resolve, reject) => pendingMicrophones.push({ resolve() { const stream = newStream(); resolve(stream); return stream; }, reject }));
      },
    } },
    sessionStorage: { getItem(key) { return storage.get(key) || null; }, setItem(key, value) { storage.set(key, value); } },
    Blob, ArrayBuffer, DataView, Uint8Array, AbortController, URL, setTimeout: setTimer, clearTimeout: clearTimer,
    fetch: async (url, request = {}) => {
      calls.push({ url, options: request });
      if (request.method === "DELETE") {
        if (options.deferDeletes) return deferred(pendingDeletes, undefined, () => response(200, { state: "stopped" }));
        return response(200, { state: "stopped" });
      }
      if (url === "/api/v1/voice/sessions") {
        const id = `session-${++stats.starts}`;
        const server = { id, nextSequence: 0, acks: new Map(), stage: "listening", turnId: null, transcript: "", responseText: "", received: [], playback: [] };
        servers.set(id, server);
        if (stats.starts <= (options.deferStarts || 0)) return deferred(pendingStarts, request.signal, () => response(201, { session_id: id, generation: 0 }));
        if (options.failFirstStart && stats.starts === 1) return response(503, { message: "準備エラー" });
        return response(201, { session_id: id, generation: 0 });
      }
      if (url.startsWith("/speech/")) {
        if (options.failAudio) throw new Error("Audio unavailable");
        return { ok: true, blob: async () => new Blob(["RIFFtestWAVE"], { type: "audio/wav" }) };
      }
      const server = servers.get(url.split("/")[5]);
      assert.ok(server, `Unknown server route: ${url}`);
      if (url.endsWith("/audio-stream/playback")) {
        const control = JSON.parse(request.body); server.playback.push(control);
        if (control.event === "started") server.stage = "playing";
        else if (control.event === "ended") {
          server.stage = "cooldown";
          setTimer(() => { server.stage = "listening"; server.turnId = null; }, 600);
        } else server.stage = "reply_ready";
        return response(200, snapshot(server));
      }
      if (url.endsWith("/audio-stream") && request.method === "POST") {
        stats.pcmAttempts++; stats.pcmInFlight++; stats.maxPCMInFlight = Math.max(stats.maxPCMInFlight, stats.pcmInFlight);
        try {
          if (options.deferPCM) return await deferred(pendingPCM, request.signal, (override) => override ? response(200, override) : acceptPCM(server, request), options.ignorePCMAbort);
          if (options.pcmFailures && stats.pcmAttempts <= options.pcmFailures) throw new Error("Network unavailable");
          const ack = acceptPCM(server, request);
          if (options.loseFirstACK && stats.pcmAttempts === 1) throw new Error("ACK lost after server accepted frame");
          return ack;
        } finally { stats.pcmInFlight--; }
      }
      if (url.endsWith("/audio-stream")) {
        if (options.deferGET) return deferred(pendingGET, request.signal, (override) => response(200, override || snapshot(server)), options.ignoreGETAbort);
        return response(200, snapshot(server));
      }
      throw new Error(`Unexpected route: ${url}`);
    },
  };
  if (options.broadcastBus) context.window.BroadcastChannel = class {
    constructor() { options.broadcastBus.push(this); }
    postMessage(data) { for (const peer of options.broadcastBus) if (peer !== this) peer.onmessage?.({ data }); }
  };
  vm.runInNewContext(source, context, { filename: "voice_call.js" });
  async function advance(ms) {
    const target = environment.now + ms;
    for (let count = 0; count < 10_000; count++) {
      const next = [...timers.entries()].filter(([, value]) => value.due <= target).sort((a, b) => a[1].due - b[1].due || a[0] - b[0])[0];
      if (!next) { environment.now = target; await settle(); return; }
      environment.now = next[1].due; timers.delete(next[0]); next[1].callback(); await settle();
    }
    throw new Error("Timer loop did not settle");
  }
  return {
    context, environment, element, calls, streams, contexts, worklets, microphoneConstraints, stats, storage,
    pendingStarts, pendingMicrophones, pendingPCM, pendingGET, pendingDeletes, advance,
    start: () => element("voice-start-button").click(), stop: () => element("voice-stop-button").click(),
    idle: () => !element("voice-start-button").disabled,
    status: () => element("voice-call-status").textContent,
    server: () => servers.get(`session-${stats.starts}`),
    frames: () => calls.filter(({ url, options: request }) => url.endsWith("/audio-stream") && request.method === "POST"),
    emit(value = 0, level) { return worklets.at(-1).emit(value, level); },
    reply(turnId = "turn-1") { const server = this.server(); server.stage = "reply_ready"; server.turnId = turnId; server.transcript = "認識した発話"; server.responseText = "返答です。"; },
    visibility(state, notify = true) { context.document.visibilityState = state; if (notify) documentListeners.get("visibilitychange")(); },
    devicesChanged() { mediaListeners.get("devicechange")?.(); },
    async begin() { this.start(); await settle(); await advance(0); },
  };
}

module.exports = { browser, settle, assert };
