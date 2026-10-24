"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const source = fs.readFileSync(path.join(__dirname, "../Programs/ServerSide/dashboard/web/voice_call.js"), "utf8");
const settle = () => new Promise(setImmediate);

function browser(options = {}) {
  const elements = new Map(), timers = new Map(), documentListeners = new Map(), mediaListeners = new Map();
  const calls = [], streams = [], contexts = [], worklets = [], outputWorklets = [], microphoneConstraints = [], servers = new Map(), eventConnections = [];
  const pendingStarts = [], pendingMicrophones = [], pendingPCM = [], pendingPCMJSON = [], pendingGET = [], pendingControls = [], pendingPlayback = [], pendingDeletes = [];
  const storage = options.storage || new Map();
  const stats = { starts: 0, microphoneRequests: 0, resumes: 0, plays: 0, pcmInFlight: 0, maxPCMInFlight: 0, pcmAttempts: 0,
    controlInFlight: 0, maxControlInFlight: 0, controlAttempts: 0,
    pollInFlight: 0, maxPollInFlight: 0, pollAttempts: 0, eventAttempts: 0, lostEndedACKs: 0, replyPlays: 0, modules: [] };
  const environment = { now: 0, allowPlayback: !options.autoplayReject };
  let timerId = 0;
  const setTimer = (callback, delay = 0) => { timers.set(++timerId, { callback, due: environment.now + delay }); return timerId; };
  const clearTimer = (id) => timers.delete(id);
  const response = (status, body) => ({ ok: status >= 200 && status < 300, status, json: async () => body });
  const element = (id) => {
    if (options.optionalDom === false && ["voice-input-level", "voice-microphone-status", "voice-mode-tag", "voice-record-button", "voice-input-gain", "voice-input-gain-value", "voice-input-gain-noisy", "voice-input-gain-normal", "voice-input-gain-quiet"].includes(id)) return null;
    if (!elements.has(id)) elements.set(id, {
      value: "", textContent: "", paused: true, ended: false, hidden: false, children: [], volume: 1,
      classList: { remove() {}, add() {}, toggle() {} }, listeners: new Map(), attributes: new Map(),
      setAttribute(name, value) { this.attributes.set(name, value); }, getAttribute(name) { return this.attributes.get(name); },
      replaceChildren() { this.children = []; }, appendChild(child) { this.children.push(child); },
      addEventListener(name, handler) { this.listeners.set(name, handler); },
      dispatch(name) { this.listeners.get(name)?.(); this[`on${name}`]?.(); },
      click() { this.listeners.get("click")?.(); },
      pause() { this.paused = true; }, load() { this.paused = true; this.ended = false; }, removeAttribute(name) { this[name] = ""; },
      play() {
        stats.plays++;
        if (id === "voice-audio") stats.replyPlays++;
        if (!environment.allowPlayback) return Promise.reject(new Error("Autoplay denied"));
        this.paused = false; this.ended = false; this.dispatch("play");
        if (options.deferPlayback && id === "voice-audio") return new Promise((resolve, reject) => pendingPlayback.push({ resolve, reject }));
        return Promise.resolve();
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
  function delayResponse(milliseconds, signal, ignoreAbort = false) {
    return new Promise((resolve, reject) => {
      const abort = () => {
        clearTimer(timer);
        const error = new Error("Aborted"); error.name = "AbortError"; reject(error);
      };
      const timer = setTimer(() => { signal?.removeEventListener("abort", abort); resolve(); }, milliseconds);
      if (!ignoreAbort) {
        if (signal?.aborted) abort();
        else signal?.addEventListener("abort", abort, { once: true });
      }
    });
  }
  function newStream() {
    const listeners = new Map();
    const stream = { id: streams.length + 1, stopped: false };
    const track = { stop() { stream.stopped = true; }, addEventListener(name, handler) { listeners.set(name, handler); } };
    stream.getTracks = () => [track]; stream.getAudioTracks = () => [track]; stream.disconnectDevice = () => listeners.get("ended")?.();
    streams.push(stream); return stream;
  }
  function eventReader(server) {
    const chunks = [];
    let waiting = null, ended = false, failure = null, heartbeat = null;
    const encoder = new TextEncoder();
    const reader = {
      read() {
        if (chunks.length) return Promise.resolve({ value: chunks.shift(), done: false });
        if (failure) return Promise.reject(failure);
        if (ended) return Promise.resolve({ done: true });
        return new Promise((resolve, reject) => { waiting = { resolve, reject }; });
      },
      push(event) {
        if (ended || failure) return;
        const bytes = encoder.encode(JSON.stringify(event) + "\n");
        const pieces = options.splitEventBytes ? [bytes.subarray(0, 7), bytes.subarray(7, 19), bytes.subarray(19)] : [bytes];
        for (const value of pieces) {
          if (!value.length) continue;
          if (waiting) { const pending = waiting; waiting = null; pending.resolve({ value, done: false }); }
          else chunks.push(value);
        }
      },
      end() { ended = true; clearTimer(heartbeat); if (waiting) { waiting.resolve({ done: true }); waiting = null; } },
      fail(error = new Error("Event connection lost")) { failure = error; clearTimer(heartbeat); if (waiting) { waiting.reject(error); waiting = null; } },
      cancel() { chunks.length = 0; this.end(); return Promise.resolve(); },
      releaseLock() {},
    };
    const beat = () => {
      if (ended || failure) return;
      reader.push({ type: "heartbeat", cursor: server.eventCursor, session_id: server.id, generation: 0, turn_id: null });
      heartbeat = setTimer(beat, 10_000);
    };
    heartbeat = setTimer(beat, 10_000);
    return reader;
  }
  function node() {
    return { disconnected: false, connections: [], connect(target) { this.connections.push(target); }, disconnect() { this.disconnected = true; } };
  }
  class Context {
    constructor() {
      this.state = "suspended"; this.destination = {}; this.nodes = []; contexts.push(this);
      this.listeners = new Map();
      this.audioWorklet = { addModule: async (url) => { stats.modules.push(url); if (options.failModule) throw new Error("Worklet unavailable"); } };
    }
    addEventListener(name, handler) { this.listeners.set(name, handler); }
    removeEventListener(name, handler) { if (this.listeners.get(name) === handler) this.listeners.delete(name); }
    changeState(state) { this.state = state; this.listeners.get("statechange")?.(); }
    resume() { stats.resumes++; this.state = "running"; return Promise.resolve(); }
    close() { this.state = "closed"; return Promise.resolve(); }
    createMediaStreamSource() { const value = node(); this.nodes.push(value); return value; }
    createGain() { const value = { ...node(), gain: { value: 1 } }; this.nodes.push(value); return value; }
  }
  class Worklet {
    constructor(context, name, config) {
      Object.assign(this, node()); this.port = { onmessage: null, postMessage() {} }; this.name = name; this.config = config;
      context.nodes.push(this);
      if (name === "nen-gitsune-pcm-output") {
        this.messages = []; this.audio = []; this.epoch = 0; this.turnId = null; this.started = false;
        this.port.postMessage = (message, transfer = []) => {
          this.messages.push({ message, transfer });
          if (message.type === "reset") {
            this.epoch = message.epoch; this.turnId = message.turn_id;
            this.audio = []; this.started = false;
          } else if (message.type === "audio" && message.epoch === this.epoch && message.turn_id === this.turnId) this.audio.push(message);
        };
        this.processAudio = (count = Infinity) => {
          let processed = 0;
          while (this.audio.length && processed < count) {
            const audio = this.audio.shift();
            if (!this.started) { this.started = true; this.port.onmessage?.({ data: { type: "started", epoch: audio.epoch, turn_id: audio.turn_id } }); }
            this.port.onmessage?.({ data: { type: "consumed", epoch: audio.epoch, turn_id: audio.turn_id, bytes: audio.pcm.byteLength } });
            processed++;
          }
          if (!this.audio.length && processed) this.port.onmessage?.({ data: { type: "drained", epoch: this.epoch, turn_id: this.turnId } });
        };
        outputWorklets.push(this);
      } else worklets.push(this);
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
    output_streaming: true, turn_complete: server.turnComplete, event_cursor: server.eventCursor,
    ...(server.turnId ? { speech: { url: `/speech/${server.turnId}`, media_type: "audio/wav" } } : {}),
    ...(server.interruption ? { interruption: server.interruption } : {}),
    ...(server.error || {}), ...extra });
  function acceptPCM(server, request) {
    const sequence = Number(request.headers["X-Audio-Sequence"]);
    if (server.acks.has(sequence)) return response(200, server.acks.get(sequence));
    assert.equal(sequence, server.nextSequence, "Server saw a sequence gap");
    server.nextSequence++;
    server.received.push(request.body);
    server.receivedRequests.push({ sequence, body: request.body, gain: Number(request.headers["X-Audio-Input-Gain"]) });
    const nextStage = options.ackStages?.[sequence];
    if (nextStage) server.stage = nextStage;
    const ack = snapshot(server, { sequence }); server.acks.set(sequence, ack);
    return response(200, ack);
  }
  function acceptControl(server, request) {
    const control = JSON.parse(request.body);
    if (options.enforceControlTurn && control.turn_id !== server.turnId) {
      return response(409, { error: "playback_turn_mismatch", message: "The active turn has changed" });
    }
    server.playback.push(control);
    if (control.event === "started") { server.playbackStarted = true; if (server.turnComplete) server.stage = "playing"; }
    else if (control.event === "ended") {
      if (!server.turnComplete) return response(409, { error: "playback_incomplete", message: "Reply generation is not complete" });
      server.stage = "cooldown";
      setTimer(() => { server.stage = "listening"; server.turnId = null; }, 600);
    } else server.stage = "reply_ready";
    return response(200, snapshot(server));
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
    Blob, ArrayBuffer, DataView, Uint8Array, AbortController, URL, TextDecoder, atob,
    performance: { now: () => environment.now }, setTimeout: setTimer, clearTimeout: clearTimer,
    fetch: async (url, request = {}) => {
      calls.push({ url, options: request, startedAt: environment.now });
      if (request.method === "DELETE") {
        if (options.deferDeletes) return deferred(pendingDeletes, undefined, () => response(200, { state: "stopped" }));
        return response(200, { state: "stopped" });
      }
      if (url === "/api/v1/voice/sessions") {
        const id = `session-${++stats.starts}`;
        const server = { id, nextSequence: 0, acks: new Map(), stage: "listening", turnId: null, transcript: "", responseText: "", received: [], receivedRequests: [], playback: [],
          events: [], eventCursor: 0, readers: [], turnComplete: false, playbackStarted: false };
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
      if (url.includes("/audio-stream/events?after=")) {
        stats.eventAttempts++;
        if (environment.now < (options.eventDisconnectUntilMs || 0)) throw new Error("Reply event connection temporarily unavailable");
        const after = Number(url.split("after=")[1]);
        const reader = eventReader(server);
        server.readers.push(reader); eventConnections.push({ after, reader, signal: request.signal });
        for (const event of server.events) if (event.cursor > after || (options.replayLastEventOnReconnect && event.cursor === after)) reader.push(event);
        if (!options.ignoreEventAbort) request.signal?.addEventListener("abort", () => { const error = new Error("Aborted"); error.name = "AbortError"; reader.fail(error); }, { once: true });
        return { ok: true, body: { getReader: () => reader } };
      }
      if (url.endsWith("/audio-stream/playback")) {
        stats.controlAttempts++; stats.controlInFlight++;
        stats.maxControlInFlight = Math.max(stats.maxControlInFlight, stats.controlInFlight);
        try {
          if (options.deferControl) return await deferred(pendingControls, request.signal,
            (override) => override ? response(200, override) : acceptControl(server, request), options.ignoreControlAbort);
          if (stats.controlAttempts <= (options.controlFailures || 0)) throw new Error("Playback notification unavailable");
          const ack = acceptControl(server, request);
          if (options.loseFirstEndedACK && !stats.lostEndedACKs && ack.ok && JSON.parse(request.body).event === "ended") {
            stats.lostEndedACKs++;
            await new Promise((_, reject) => {
              const loseACK = () => { request.signal?.removeEventListener("abort", abort); reject(new Error("Ended ACK lost after server acceptance")); };
              const timer = setTimer(loseACK, options.endedACKLossDelayMs ?? 800);
              const abort = () => { clearTimer(timer); const error = new Error("Aborted"); error.name = "AbortError"; reject(error); };
              request.signal?.addEventListener("abort", abort, { once: true });
            });
          }
          return ack;
        } finally { stats.controlInFlight--; }
      }
      if (url.endsWith("/audio-stream") && request.method === "POST") {
        stats.pcmAttempts++;
        let counted = !request.signal?.aborted;
        if (counted) stats.pcmInFlight++;
        stats.maxPCMInFlight = Math.max(stats.maxPCMInFlight, stats.pcmInFlight);
        // A cancelled POST has released its lane even if an intentionally
        // uncooperative mock retains its late Promise completion.
        const finish = () => { if (counted) { counted = false; stats.pcmInFlight--; } };
        request.signal?.addEventListener("abort", finish, { once: true });
        try {
          if (options.deferPCM) return await deferred(pendingPCM, request.signal, (override) => override ? response(200, override) : acceptPCM(server, request), options.ignorePCMAbort);
          if (environment.now < (options.pcmDisconnectUntilMs || 0)) throw new Error("PCM connection temporarily unavailable");
          if (options.pcmFailures && stats.pcmAttempts <= options.pcmFailures) throw new Error("Network unavailable");
          const ack = acceptPCM(server, request);
          if (options.pcmAcceptError) {
            server.stage = "failed";
            server.error = { error: options.pcmAcceptError, message: "Accepted PCM processing failed" };
            return response(503, server.error);
          }
          const configuredDelay = options.pcmACKDelays?.[stats.pcmAttempts - 1] ?? options.pcmACKDelayMs ?? 0;
          const ackDelay = Math.max(configuredDelay, (options.pcmACKBlockedUntilMs || 0) - environment.now);
          if (ackDelay > 0) await delayResponse(ackDelay, request.signal, options.ignorePCMAbort);
          if (options.loseFirstACK && stats.pcmAttempts === 1) throw new Error("ACK lost after server accepted frame");
          if (options.deferPCMJSON) {
            const body = await ack.json();
            return { ...ack, json: () => new Promise((resolve) => pendingPCMJSON.push({
              signal: request.signal, resolve(value) { resolve(value || body); },
            })) };
          }
          return ack;
        } finally { finish(); request.signal?.removeEventListener("abort", finish); }
      }
      if (url.endsWith("/audio-stream")) {
        stats.pollAttempts++; stats.pollInFlight++;
        stats.maxPollInFlight = Math.max(stats.maxPollInFlight, stats.pollInFlight);
        try {
          if (options.deferGET) return await deferred(pendingGET, request.signal, (override) => response(200, override || snapshot(server)), options.ignoreGETAbort);
          if (environment.now < (options.pollDisconnectUntilMs || 0)) throw new Error("State polling connection temporarily unavailable");
          if (stats.pollAttempts <= (options.pollFailures || 0)) throw new Error("State polling unavailable");
          return response(200, snapshot(server));
        } finally { stats.pollInFlight--; }
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
    context, environment, element, calls, streams, contexts, worklets, outputWorklets, microphoneConstraints, stats, storage,
    pendingStarts, pendingMicrophones, pendingPCM, pendingPCMJSON, pendingGET, pendingControls, pendingPlayback, pendingDeletes, eventConnections, advance,
    start: () => element("voice-start-button").click(), stop: () => element("voice-stop-button").click(),
    idle: () => !element("voice-start-button").disabled,
    status: () => element("voice-call-status").textContent,
    server: () => servers.get(`session-${stats.starts}`),
    frames: () => calls.filter(({ url, options: request }) => url.endsWith("/audio-stream") && request.method === "POST"),
    emit(value = 0, level) { return worklets.at(-1).emit(value, level); },
    reply(turnId = "turn-1") { const server = this.server(); server.stage = "reply_ready"; server.turnId = turnId; server.transcript = "認識した発話"; server.responseText = "返答です。"; },
    emitEvent(event, { updateServer = true } = {}) {
      const server = this.server();
      const payload = { cursor: ++server.eventCursor, session_id: server.id, generation: 0, turn_id: event.turn_id || server.turnId || "turn-1", ...event };
      if (updateServer) {
        if (server.turnId !== payload.turn_id) { server.turnComplete = false; server.playbackStarted = false; }
        server.turnId = payload.turn_id;
        if (event.type === "interruption") {
          server.interruption = payload; server.turnId = event.new_turn_id;
          server.transcript = ""; server.responseText = ""; server.turnComplete = false; server.playbackStarted = false; server.stage = "speaking";
        }
        if (event.type === "transcript") { server.transcript = event.text; server.stage = "llm"; }
        if (event.type === "text_delta") server.responseText += event.text || "";
        if (["audio_chunk", "audio_frame", "acknowledgement"].includes(event.type)) server.stage = "tts";
        if (event.type === "complete") { server.turnComplete = true; server.stage = server.playbackStarted ? "playing" : "reply_ready"; server.responseText = event.response_text || server.responseText; }
      }
      server.events.push(payload); for (const reader of server.readers) reader.push(payload); return payload.cursor;
    },
    frameEvent(event, { updateServer = true } = {}) {
      const server = this.server();
      const cursor = event.cursor ?? server.eventCursor + 1;
      server.eventCursor = Math.max(server.eventCursor, cursor);
      const payload = { session_id: server.id, generation: 0, turn_id: server.turnId || "turn-1", ...event, cursor };
      if (updateServer) { server.turnId = payload.turn_id; server.stage = "tts"; }
      server.events.push(payload); for (const reader of server.readers) reader.push(payload); return cursor;
    },
    replayEvent(cursor) { const server = this.server(); const event = server.events.find((value) => value.cursor === cursor); assert.ok(event); for (const reader of server.readers) reader.push(event); },
    controlEvent(event) { const server = this.server(); const payload = { cursor: server.eventCursor, session_id: server.id, generation: 0, turn_id: null, ...event }; for (const reader of server.readers) reader.push(payload); },
    disconnectEvents() { eventConnections.at(-1).reader.fail(); },
    endEvents() { eventConnections.at(-1).reader.end(); },
    visibility(state, notify = true) { context.document.visibilityState = state; if (notify) documentListeners.get("visibilitychange")(); },
    devicesChanged() { mediaListeners.get("devicechange")?.(); },
    async begin() { this.start(); await settle(); await advance(0); },
  };
}

module.exports = { browser, settle, assert };
