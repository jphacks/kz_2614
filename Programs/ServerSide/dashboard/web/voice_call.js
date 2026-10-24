"use strict";

(() => {
  const API_SESSIONS = "/api/v1/voice/sessions";
  const TOKEN_KEY = "nen-gitsune-dashboard-token";
  const MICROPHONE_KEY = "nen-gitsune-voice-microphone";
  const PCM_CONTENT_TYPE = "audio/pcm;rate=16000;channels=1;format=s16le";
  const PCM_FRAME_BYTES = 8192;
  const MAX_QUEUE_BYTES = 64_000;
  const START_TIMEOUT_MS = 15_000;
  const MICROPHONE_TIMEOUT_MS = 15_000;
  const REQUEST_TIMEOUT_MS = 5_000;
  const POLL_INTERVAL_MS = 350;
  const INPUT_STAGES = new Set(["listening", "speaking"]);
  const startButton = document.getElementById("voice-start-button");
  const recordButton = document.getElementById("voice-record-button");
  const stopButton = document.getElementById("voice-stop-button");
  const statusLine = document.getElementById("voice-call-status");
  const transcriptLine = document.getElementById("voice-transcript");
  const responseLine = document.getElementById("voice-response");
  const audioPlayer = document.getElementById("voice-audio");
  const tokenInput = document.getElementById("dashboard-token");
  const inputLevel = document.getElementById("voice-input-level");
  const microphoneStatus = document.getElementById("voice-microphone-status");
  const modeTag = document.getElementById("voice-mode-tag");
  const microphoneSelect = document.getElementById("voice-microphone-select");

  let sessionId = null;
  let generation = null;
  let startRequest = null;
  let captureRequest = null;
  let audioContext = null;
  let audioObjectUrl = null;
  let activityVersion = 0;
  let microphoneDevicesReady = false;
  let conversationChannel = null;
  const tabOwner = makeId();

  function makeId() {
    if (window.crypto?.randomUUID) return window.crypto.randomUUID();
    return `${Date.now()}-${Math.random()}`;
  }

  function readPreference(key) {
    try { return sessionStorage.getItem(key) || ""; } catch (_) { return ""; }
  }

  function writePreference(key, value) {
    try { sessionStorage.setItem(key, value); } catch (_) { /* Preferences are optional. */ }
  }

  function setStatus(message, kind = "") {
    statusLine.textContent = message;
    statusLine.classList.remove("error", "active");
    if (kind) statusLine.classList.add(kind);
  }

  function showMicrophone(message, level = 0) {
    if (microphoneStatus) microphoneStatus.textContent = message;
    if (inputLevel) inputLevel.value = Number.isFinite(level) ? Math.min(1, Math.max(0, level)) : 0;
  }

  function refreshButtons() {
    startButton.disabled = Boolean(sessionId || startRequest);
    startButton.textContent = startRequest ? "準備中…" : sessionId ? "会話中" : "会話を始める";
    stopButton.disabled = !sessionId && !startRequest;
    if (recordButton) recordButton.hidden = true;
    if (modeTag) modeTag.textContent = "サーバー側で音声認識";
  }

  function authHeaders(extra = {}) {
    const token = tokenInput?.value.trim() || readPreference(TOKEN_KEY);
    return token ? { ...extra, Authorization: `Bearer ${token}` } : extra;
  }

  async function readJson(response) {
    let body = {};
    try { body = await response.json(); } catch (_) { /* HTTP status still reports a failed request. */ }
    if (!response.ok) {
      const error = new Error(body.message || `サーバー応答 ${response.status}`);
      error.status = response.status;
      error.code = body.error || "request_failed";
      throw error;
    }
    return body;
  }

  function currentCapture(request) {
    return captureRequest === request && !request.cancelled && sessionId === request.sessionId && generation === request.generation;
  }

  function captureUnavailableMessage() {
    if (!window.isSecureContext) return "マイクを使うにはHTTPS、または同じPCのlocalhostでこのページを開いてください。";
    if (!navigator.mediaDevices || typeof navigator.mediaDevices.getUserMedia !== "function") return "このブラウザーではマイクを利用できません。Chromeなどのブラウザーでこのページを開いてください。";
    if (typeof (window.AudioContext || window.webkitAudioContext) !== "function" || typeof window.AudioWorkletNode !== "function") {
      return "このブラウザーはPCM音声の送信に対応していません。Chromeなどのブラウザーでこのページを開いてください。";
    }
    return "";
  }

  function prepareAudioContext() {
    const Context = window.AudioContext || window.webkitAudioContext;
    if (!audioContext || audioContext.state === "closed") audioContext = new Context();
    const context = audioContext;
    // Called directly from the user's start click, before a network wait.
    return Promise.resolve(context.resume()).then(() => {
      if (context.state !== "running" || !context.audioWorklet?.addModule) throw new Error("マイクの音声処理を開始できません。ブラウザーの音声設定を確認してください。");
      return context;
    });
  }

  async function refreshMicrophones() {
    if (!microphoneSelect || !navigator.mediaDevices?.enumerateDevices || !document.createElement
        || typeof microphoneSelect.replaceChildren !== "function") return;
    try {
      const devices = (await navigator.mediaDevices.enumerateDevices()).filter((device) => device.kind === "audioinput");
      const wanted = microphoneSelect.value && microphoneSelect.value !== "default" ? microphoneSelect.value : readPreference(MICROPHONE_KEY) || "default";
      const missing = wanted !== "default" && !devices.some((device) => device.deviceId === wanted);
      const disappeared = microphoneDevicesReady && missing;
      microphoneSelect.replaceChildren();
      const appendOption = (value, label) => {
        const option = document.createElement("option"); option.value = value; option.textContent = label;
        microphoneSelect.appendChild(option);
      };
      appendOption("default", devices.find((device) => device.deviceId === "default")?.label || "システム既定のマイク");
      let number = 0;
      for (const device of devices) {
        if (device.deviceId && !["default", "communications"].includes(device.deviceId)) appendOption(device.deviceId, device.label || `マイク ${++number}`);
      }
      if (missing) appendOption(wanted, "選択中のマイク（未接続）");
      microphoneSelect.value = wanted;
      microphoneDevicesReady = true;
      if (disappeared && (sessionId || startRequest)) {
        void stopCall({ silent: true });
        setStatus("選択していたマイクが切断されました。録音マイクを選び直して「会話を始める」を押してください。", "error");
      }
    } catch (_) { /* Device selection remains unchanged when enumeration is unavailable. */ }
  }

  function stopRemoteSession(id, keepalive = false) {
    return fetch(`${API_SESSIONS}/${encodeURIComponent(id)}`, {
      method: "DELETE", cache: "no-store", credentials: "same-origin", headers: authHeaders(), keepalive,
    });
  }

  async function startCall() {
    if (sessionId || startRequest) return;
    activityVersion++;
    const unavailable = captureUnavailableMessage();
    if (unavailable) { setStatus(unavailable, "error"); return; }
    conversationChannel?.postMessage({ type: "voice-owner", sender: tabOwner });
    const request = { controller: new AbortController(), cancelled: false, timedOut: false };
    startRequest = request;
    const timer = setTimeout(() => { request.timedOut = true; request.controller.abort(); }, START_TIMEOUT_MS);
    refreshButtons();
    setStatus("会話を準備しています…");
    try {
      await abortable(prepareAudioContext(), request.controller.signal);
      if (startRequest !== request || request.cancelled) return;
      const response = await fetch(API_SESSIONS, {
        method: "POST", cache: "no-store", credentials: "same-origin", headers: authHeaders(), signal: request.controller.signal,
      });
      const body = await readJson(response);
      if (startRequest !== request || request.cancelled || request.timedOut || document.visibilityState === "hidden") {
        if (body.session_id) void stopRemoteSession(body.session_id, true).catch(() => {});
        if (startRequest === request) setStatus(request.timedOut ? "会話の準備が時間内に終わりませんでした。もう一度開始してください。" : "画面が切り替わったため会話を終了しました。戻ったら開始してください。");
        return;
      }
      sessionId = body.session_id;
      generation = body.generation;
      transcriptLine.textContent = "話しかけると、サーバーが聞き取った内容をここに表示します。";
      responseLine.textContent = "返事を待っています。";
      void beginCapture();
    } catch (error) {
      if (startRequest === request && !request.cancelled) setStatus(request.timedOut ? "会話の準備が時間内に終わりませんでした。もう一度開始してください。" : error.message || "会話を始められませんでした。", "error");
    } finally {
      clearTimeout(timer);
      if (startRequest === request) { startRequest = null; refreshButtons(); }
    }
  }

  function abortable(promise, signal) {
    return new Promise((resolve, reject) => {
      const abort = () => { const error = new Error("Request aborted"); error.name = "AbortError"; reject(error); };
      if (signal.aborted) { abort(); return; }
      signal.addEventListener("abort", abort, { once: true });
      Promise.resolve(promise).then(resolve, reject).finally(() => signal.removeEventListener("abort", abort));
    });
  }

  function resetAudio() {
    audioPlayer.onended = null;
    audioPlayer.onerror = null;
    audioPlayer.pause(); audioPlayer.removeAttribute("src"); audioPlayer.hidden = true; audioPlayer.load();
    if (audioObjectUrl) { URL.revokeObjectURL(audioObjectUrl); audioObjectUrl = null; }
  }

  function disposeCapture(request) {
    request.cancelled = true;
    request.controller.abort();
    clearTimeout(request.permissionTimer); clearTimeout(request.pollTimer);
    request.queue = []; request.queuedBytes = 0; request.controls = [];
    if (request.worklet) request.worklet.port.onmessage = null;
    for (const node of [request.source, request.worklet, request.output]) {
      try { node?.disconnect(); } catch (_) { /* Already disconnected. */ }
    }
    if (request.stream) {
      for (const track of request.stream.getTracks()) track.stop();
      request.stream = null;
    }
  }

  function failCapture(request, message) {
    if (!currentCapture(request)) return;
    void stopCall({ silent: true });
    setStatus(`${message} 接続やマイクを確認して「会話を始める」から再開してください。`, "error");
  }

  async function beginCapture() {
    const request = {
      sessionId, generation, controller: new AbortController(), cancelled: false, ready: false,
      stream: null, source: null, worklet: null, output: null, permissionTimer: null,
      queue: [], queuedBytes: 0, nextSequence: 0, operation: false, controls: [], pollTimer: null, pollDue: false,
      stage: "listening", reply: null, handledReplies: new Set(),
    };
    captureRequest = request;
    setStatus("マイクの許可を待っています…ブラウザーの確認が表示されたら許可してください。");
    showMicrophone("マイクの許可待ち");
    request.permissionTimer = setTimeout(() => failCapture(request, "マイクの準備が時間内に終わりませんでした。"), MICROPHONE_TIMEOUT_MS);
    try {
      const selected = (microphoneDevicesReady ? microphoneSelect?.value : readPreference(MICROPHONE_KEY)) || microphoneSelect?.value || "default";
      const audio = { echoCancellation: true, noiseSuppression: true, autoGainControl: false, channelCount: 1 };
      if (selected !== "default") audio.deviceId = { exact: selected };
      const stream = await navigator.mediaDevices.getUserMedia({ audio, video: false });
      request.stream = stream;
      if (!currentCapture(request)) { disposeCapture(request); return; }
      const context = audioContext;
      if (!context || context.state !== "running") throw new Error("マイクの音声処理を開始できませんでした。");
      await abortable(context.audioWorklet.addModule("/voice_pcm_worklet.js"), request.controller.signal);
      if (!currentCapture(request)) { disposeCapture(request); return; }
      request.source = context.createMediaStreamSource(stream);
      request.worklet = new window.AudioWorkletNode(context, "nen-gitsune-pcm", {
        numberOfInputs: 1, numberOfOutputs: 1, outputChannelCount: [1],
      });
      request.output = context.createGain(); request.output.gain.value = 0;
      request.source.connect(request.worklet); request.worklet.connect(request.output); request.output.connect(context.destination);
      request.worklet.port.onmessage = (event) => receivePCM(request, event.data);
      for (const track of stream.getAudioTracks?.() || []) {
        track.addEventListener?.("ended", () => failCapture(request, "マイクとの接続が切れました。"));
      }
      clearTimeout(request.permissionTimer); request.permissionTimer = null;
      request.ready = true;
      void refreshMicrophones();
      renderState(request);
      schedulePoll(request, 0);
    } catch (error) {
      if (!currentCapture(request)) { disposeCapture(request); return; }
      const denied = ["NotAllowedError", "SecurityError"].includes(error.name);
      const missing = ["NotFoundError", "OverconstrainedError"].includes(error.name);
      failCapture(request, denied ? "マイクが許可されていません。" : missing ? "選択したマイクを利用できません。録音マイクを選び直してください。" : error.message || "マイクを開始できませんでした。");
    }
  }

  function maySendAudio(request) {
    return INPUT_STAGES.has(request.stage) && !request.reply;
  }

  function receivePCM(request, data) {
    if (!currentCapture(request) || !request.ready || data?.type !== "pcm") return;
    // RMS is display-only. Every frame, including complete silence, follows server state.
    if (inputLevel) inputLevel.value = Number.isFinite(data.level) ? Math.min(1, Math.max(0, data.level * 8)) : 0;
    if (!data.pcm || data.pcm.byteLength !== PCM_FRAME_BYTES) { failCapture(request, "音声フレームを正しく作成できませんでした。"); return; }
    if (!maySendAudio(request)) return;
    if (request.queuedBytes + data.pcm.byteLength > MAX_QUEUE_BYTES) {
      failCapture(request, "音声の送信が追いつかず、約2秒の待機上限を超えました。音声を黙って間引かず停止しました。");
      return;
    }
    request.queue.push(data.pcm); request.queuedBytes += data.pcm.byteLength;
    pumpTransport(request);
  }

  function schedulePoll(request, delay = POLL_INTERVAL_MS) {
    if (!currentCapture(request)) return;
    clearTimeout(request.pollTimer);
    request.pollDue = false;
    request.pollTimer = setTimeout(() => { request.pollDue = true; pumpTransport(request); }, delay);
  }

  async function fetchJSON(request, url, options = {}) {
    const controller = new AbortController();
    const abort = () => controller.abort();
    request.controller.signal.addEventListener("abort", abort, { once: true });
    const timer = setTimeout(abort, REQUEST_TIMEOUT_MS);
    try {
      return await readJson(await fetch(url, { cache: "no-store", credentials: "same-origin", ...options, signal: controller.signal }));
    } finally {
      clearTimeout(timer);
      request.controller.signal.removeEventListener("abort", abort);
    }
  }

  async function withRetries(request, operation) {
    for (let attempt = 0; attempt < 3; attempt++) {
      if (!currentCapture(request)) throw new Error("Session stopped");
      try { return await operation(); }
      catch (error) {
        if (!currentCapture(request)) throw error;
        if (error.status && error.status < 500 && ![408, 429].includes(error.status)) throw error;
        if (attempt === 2) throw error;
        setStatus("音声サーバーへの接続を再試行しています。同じ番号のデータを再送します。", "error");
        await abortable(new Promise((resolve) => setTimeout(resolve, 250 * (attempt + 1))), request.controller.signal);
      }
    }
  }

  function validState(request, body) {
    return currentCapture(request) && body.session_id === request.sessionId && body.generation === request.generation;
  }

  function applyState(request, body) {
    if (!validState(request, body)) return;
    if (typeof body.stage === "string") request.stage = body.stage;
    if (body.transcript) transcriptLine.textContent = body.transcript;
    if (body.response_text) responseLine.textContent = body.response_text;
    if (!INPUT_STAGES.has(request.stage)) { request.queue = []; request.queuedBytes = 0; }
    if (request.reply?.ended && INPUT_STAGES.has(request.stage)) request.reply = null;
    renderState(request);
    if (body.stage === "reply_ready" && body.turn_id && body.speech?.url && !request.handledReplies.has(body.turn_id)) {
      request.handledReplies.add(body.turn_id);
      request.reply = { turnId: body.turn_id, started: false, ended: false, awaitingUser: false, playing: false };
      request.queue = []; request.queuedBytes = 0;
      void playReply(request, request.reply, body.speech.url);
    }
  }

  function renderState(request) {
    if (!currentCapture(request) || !request.ready) return;
    const reply = request.reply;
    if (reply && !reply.ended) {
      setStatus(reply.failed ? "返答音声を再生できませんでした。再生を再試行するか、停止して会話を開始し直してください。"
        : reply.awaitingUser ? "返答を受け取りました。再生ボタンを押してください。再生が終わるまで入力はサーバーで待機します。"
          : reply.playing ? "返答を再生中です。再生が終わるとサーバーの待受へ戻ります。" : "返答音声を受け取っています…", reply.failed ? "error" : "active");
    } else {
      const messages = {
        listening: "音声をサーバーへ送信中です。話しかけると文字が表示されます。",
        speaking: "聞き取り中です。話し終えるとサーバーが返答を作ります。",
        stt: "音声を文字にしています…", llm: "返事を考えています…", tts: "返答音声を作っています…",
        reply_ready: "返答音声を受け取っています…", playing: "返答を再生中です。",
        cooldown: "聞き取りの再開を待っています…", failed: "音声処理に失敗しました。停止して会話を開始し直してください。",
      };
      setStatus(messages[request.stage] || "サーバーの音声状態を確認しています…", request.stage === "failed" ? "error" : "active");
    }
    if (microphoneStatus) microphoneStatus.textContent = maySendAudio(request) ? "マイク接続・サーバーへ送信中" : "マイク接続・サーバーの処理／再生を待機中";
    refreshButtons();
  }

  async function pumpTransport(request) {
    if (!currentCapture(request) || request.operation) return;
    let operation;
    if (request.controls.length) operation = { type: "control", control: request.controls.shift() };
    else if (maySendAudio(request) && request.queue.length) {
      const pcm = request.queue.shift(); request.queuedBytes -= pcm.byteLength;
      operation = { type: "audio", pcm, sequence: request.nextSequence };
    } else if (request.pollDue) { operation = { type: "poll" }; request.pollDue = false; }
    else return;
    request.operation = true;
    const route = `${API_SESSIONS}/${encodeURIComponent(request.sessionId)}/audio-stream`;
    try {
      const body = await withRetries(request, () => {
        if (operation.type === "audio") return fetchJSON(request, route, {
          method: "POST", headers: authHeaders({ "Content-Type": PCM_CONTENT_TYPE, "X-Voice-Generation": String(request.generation), "X-Audio-Sequence": String(operation.sequence) }), body: operation.pcm,
        });
        if (operation.type === "control") return fetchJSON(request, `${route}/playback`, {
          method: "POST", headers: authHeaders({ "Content-Type": "application/json" }),
          body: JSON.stringify({ generation: request.generation, turn_id: operation.control.turnId, event: operation.control.event }),
        });
        return fetchJSON(request, route, { headers: authHeaders({ "X-Voice-Generation": String(request.generation) }) });
      });
      if (!currentCapture(request)) return;
      if (!validState(request, body)) throw new Error("音声サーバーの会話識別を確認できませんでした。");
      if (operation.type === "audio") {
        if (body.sequence !== operation.sequence || body.next_sequence !== operation.sequence + 1) throw new Error("音声データの順序を確認できませんでした。");
        request.nextSequence = body.next_sequence;
      }
      applyState(request, body);
    } catch (error) {
      if (currentCapture(request)) failCapture(request, error.message || "音声サーバーへ接続できませんでした。");
    } finally {
      request.operation = false;
      if (currentCapture(request)) { schedulePoll(request); pumpTransport(request); }
    }
  }

  function notifyPlayback(request, reply, event) {
    if (!currentCapture(request) || request.reply !== reply) return;
    if (event === "started") { if (reply.started) return; reply.started = true; reply.failed = false; reply.playing = true; reply.awaitingUser = false; }
    if (event === "ended") {
      if (reply.ended) return;
      reply.ended = true; reply.playing = false;
      // A completed reply cannot be replayed outside the server's playback state.
      resetAudio();
    }
    if (event === "failed") { if (reply.failed) return; reply.failed = true; reply.started = false; reply.playing = false; reply.awaitingUser = true; }
    request.controls.push({ turnId: reply.turnId, event });
    renderState(request); pumpTransport(request);
  }

  async function playReply(request, reply, url) {
    try {
      const controller = new AbortController();
      const abort = () => controller.abort();
      request.controller.signal.addEventListener("abort", abort, { once: true });
      const timer = setTimeout(abort, REQUEST_TIMEOUT_MS);
      let audio;
      try {
        const response = await fetch(url, { cache: "no-store", credentials: "same-origin", headers: authHeaders(), signal: controller.signal });
        if (!response.ok) throw new Error("返答音声を取得できませんでした。");
        audio = await response.blob();
      } finally { clearTimeout(timer); request.controller.signal.removeEventListener("abort", abort); }
      if (!currentCapture(request) || request.reply !== reply) return;
      resetAudio();
      audioObjectUrl = URL.createObjectURL(audio); audioPlayer.src = audioObjectUrl; audioPlayer.hidden = false;
      audioPlayer.onended = () => notifyPlayback(request, reply, "ended");
      audioPlayer.onerror = () => {
        if (!currentCapture(request) || request.reply !== reply) return;
        notifyPlayback(request, reply, "failed"); setStatus("返答音声を再生できませんでした。", "error");
      };
      try {
        await audioPlayer.play();
        if (currentCapture(request) && request.reply === reply) notifyPlayback(request, reply, "started");
      } catch (_) {
        if (currentCapture(request) && request.reply === reply && !reply.started) { reply.awaitingUser = true; renderState(request); }
      }
    } catch (error) {
      if (currentCapture(request) && request.reply === reply) { notifyPlayback(request, reply, "failed"); setStatus(error.message || "返答音声を取得できませんでした。", "error"); }
    }
  }

  async function stopCall({ silent = false, keepalive = false } = {}) {
    const stopVersion = ++activityVersion;
    if (startRequest) { startRequest.cancelled = true; startRequest = null; }
    const stoppedSession = sessionId; sessionId = null; generation = null;
    if (captureRequest) { disposeCapture(captureRequest); captureRequest = null; }
    resetAudio();
    if (audioContext) {
      const context = audioContext; audioContext = null;
      try { Promise.resolve(context.close()).catch(() => {}); } catch (_) { /* Already closed. */ }
    }
    showMicrophone("マイク停止"); refreshButtons();
    if (stoppedSession) {
      try {
        const response = await stopRemoteSession(stoppedSession, keepalive);
        if (!response.ok && !silent) throw new Error("サーバーへの停止要求を確認できませんでした。");
      } catch (error) {
        if (!silent && activityVersion === stopVersion) setStatus(`${error.message} 端末のマイクと再生は停止しました。`, "error");
        return;
      }
    }
    if (!silent && activityVersion === stopVersion) setStatus("マイク・再生を停止し、会話を終了しました。");
  }

  if (microphoneSelect) {
    microphoneSelect.value = "default";
    microphoneSelect.addEventListener("change", () => {
      writePreference(MICROPHONE_KEY, microphoneSelect.value || "default");
      void stopCall({ silent: true });
      setStatus("録音マイクを変更しました。「会話を始める」を押すと再開します。");
    });
  }
  void refreshMicrophones();
  navigator.mediaDevices?.addEventListener?.("devicechange", () => void refreshMicrophones());
  if (typeof window.BroadcastChannel === "function") {
    try {
      conversationChannel = new window.BroadcastChannel("nen-gitsune-voice-owner");
      conversationChannel.onmessage = (event) => {
        if (event.data?.type === "voice-owner" && event.data.sender !== tabOwner && (sessionId || startRequest)) {
          void stopCall({ silent: true }); setStatus("別のタブで会話を開始したため、このタブのマイクを停止しました。");
        }
      };
    } catch (_) { /* Cross-tab messaging is optional. */ }
  }
  startButton.addEventListener("click", () => void startCall());
  stopButton.addEventListener("click", () => void stopCall());
  audioPlayer.addEventListener("play", () => {
    const request = captureRequest;
    if (!request?.reply || request.reply.ended || !currentCapture(request)) { audioPlayer.pause(); return; }
    notifyPlayback(request, request.reply, "started");
  });
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") { void stopCall({ silent: true, keepalive: true }); setStatus("画面が切り替わったため停止しました。戻ったら会話を開始してください。"); }
  });
  window.addEventListener("pagehide", () => void stopCall({ silent: true, keepalive: true }));
  refreshButtons();
})();
