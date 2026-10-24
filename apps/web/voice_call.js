"use strict";

(() => {
  const API_SESSIONS = "/api/v1/voice/sessions";
  const MICROPHONE_KEY = "nen-gitsune-voice-microphone";
  const INPUT_GAIN_KEY = "nen-gitsune-voice-input-gain-percent";
  const PCM_CONTENT_TYPE = "audio/pcm;rate=16000;channels=1;format=s16le";
  const PCM_FRAME_BYTES = 8192;
  const MAX_QUEUE_BYTES = 960_000; // 30 seconds, including the unacknowledged packet.
  const START_TIMEOUT_MS = 15_000;
  const MICROPHONE_TIMEOUT_MS = 15_000;
  const REQUEST_TIMEOUT_MS = 5_000;
  const PCM_REQUEST_TIMEOUT_MS = 8_000;
  const PCM_RETRY_WINDOW_MS = 24_750;
  const PCM_CONFIRM_INTERVAL_MS = 100;
  const POLL_INTERVAL_MS = 350;
  const MAX_PLAYBACK_QUEUE_BYTES = 8 * 1024 * 1024;
  const PCM_OUTPUT_PROFILE = "pcm_s16le_16000_stream";
  const PCM_OUTPUT_RATE = 16_000;
  const PCM_OUTPUT_FRAME_SAMPLES = 1_600;
  const MAX_EVENT_LINE_CHARS = 40 * 1024 * 1024;
  const EVENT_IDLE_TIMEOUT_MS = 35_000;
  const startButton = document.getElementById("voice-start-button");
  const recordButton = document.getElementById("voice-record-button");
  const stopButton = document.getElementById("voice-stop-button");
  const statusLine = document.getElementById("voice-call-status");
  const transcriptLine = document.getElementById("voice-transcript");
  const responseLine = document.getElementById("voice-response");
  const audioPlayer = document.getElementById("voice-audio");
  const heartbeatToggle = document.getElementById("voice-heartbeat-enabled");
  const heartbeatVolume = document.getElementById("voice-heartbeat-volume");
  const heartbeatPlayer = document.getElementById("voice-heartbeat-audio");
  const heartbeatStatus = document.getElementById("voice-heartbeat-status");
  let heartbeatVersion = 0;
  function stopHeartbeat() {
    ++heartbeatVersion;
    if (!heartbeatPlayer) return;
    heartbeatPlayer.pause();
    heartbeatPlayer.currentTime = 0;
    heartbeatPlayer.hidden = true;
  }
  async function syncHeartbeat() {
    stopHeartbeat();
    if (!heartbeatPlayer || !heartbeatToggle?.checked) {
      if (heartbeatStatus) heartbeatStatus.textContent = "心音はOffです。";
      return;
    }
    if (!captureRequest?.ready || !sessionId) {
      if (heartbeatStatus) heartbeatStatus.textContent = "会話を始めると心音を再生します。";
      return;
    }
    const version = heartbeatVersion;
    heartbeatPlayer.volume = Math.min(1, Math.max(0, Number(heartbeatVolume?.value ?? 20) / 100));
    heartbeatPlayer.loop = true;
    try {
      await heartbeatPlayer.play();
      if (version !== heartbeatVersion) return;
      heartbeatStatus.textContent = "心音をOnで再生中です。";
    } catch (_) {
      if (version !== heartbeatVersion) return;
      heartbeatPlayer.hidden = false;
      heartbeatPlayer.controls = true;
      heartbeatStatus.textContent = "心音を再生できません。再生ボタンを押すか、OffからOnにして再試行してください。";
    }
  }
  heartbeatToggle?.addEventListener("change", () => void syncHeartbeat());
  heartbeatVolume?.addEventListener("input", () => {
    if (heartbeatPlayer) heartbeatPlayer.volume = Math.min(1, Math.max(0, Number(heartbeatVolume.value) / 100));
  });
  heartbeatPlayer?.addEventListener("play", () => {
    if (!heartbeatToggle?.checked || !captureRequest?.ready || !sessionId) { stopHeartbeat(); return; }
    if (heartbeatStatus) heartbeatStatus.textContent = "心音をOnで再生中です。";
  });
  heartbeatPlayer?.addEventListener("error", () => {
    if (heartbeatToggle?.checked && sessionId && heartbeatStatus) heartbeatStatus.textContent = "心音の音源を読み込めません。OffからOnにして再試行してください。";
  });
  const inputLevel = document.getElementById("voice-input-level");
  const microphoneStatus = document.getElementById("voice-microphone-status");
  const modeTag = document.getElementById("voice-mode-tag");
  const microphoneSelect = document.getElementById("voice-microphone-select");
  const inputGainSlider = document.getElementById("voice-input-gain");
  const inputGainValue = document.getElementById("voice-input-gain-value");
  const inputGainPresets = [
    [document.getElementById("voice-input-gain-noisy"), 60],
    [document.getElementById("voice-input-gain-normal"), 100],
    [document.getElementById("voice-input-gain-quiet"), 150],
  ];

  let sessionId = null;
  let generation = null;
  let startRequest = null;
  let captureRequest = null;
  let audioContext = null;
  let audioObjectUrl = null;
  let activityVersion = 0;
  let microphoneDevicesReady = false;
  let conversationChannel = null;
  let inputGainPercent = 100;
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

  function updateInputGain(value, save = true) {
    const number = Number(value);
    inputGainPercent = Number.isFinite(number) ? Math.min(200, Math.max(25, Math.round(number / 5) * 5)) : 100;
    if (inputGainSlider) {
      inputGainSlider.value = String(inputGainPercent);
      inputGainSlider.setAttribute?.("aria-valuetext", `${inputGainPercent}%`);
    }
    if (inputGainValue) inputGainValue.textContent = `${inputGainPercent}%`;
    for (const [button, percent] of inputGainPresets) button?.setAttribute?.("aria-pressed", String(inputGainPercent === percent));
    if (save) writePreference(INPUT_GAIN_KEY, String(inputGainPercent));
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

  async function readJson(response) {
    let body = {};
    try { body = await response.json(); } catch (_) {
      if (response.ok) throw new Error("サーバーの応答を読み取れませんでした。接続を確認しています。");
    }
    if (!response.ok) {
      const error = new Error(body.message || `サーバー応答 ${response.status}`);
      error.status = response.status;
      error.code = body.error || "request_failed";
      error.fatal = body.fatal === true || body.retryable === false;
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
      method: "DELETE", cache: "no-store", credentials: "same-origin", keepalive,
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
        method: "POST", cache: "no-store", credentials: "same-origin", signal: request.controller.signal,
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
    if (request.pendingPCM) {
      const pending = request.pendingPCM;
      pending.controller.abort();
      if (!pending.settled) {
        pending.settled = true;
        const error = new Error("Session stopped"); error.name = "AbortError";
        pending.reject(error);
      }
    }
    clearTimeout(request.permissionTimer); clearTimeout(request.pollTimer); clearTimeout(request.controlTimer);
    request.queue = []; request.queuedBytes = 0; request.controls = [];
    request.controlOperation?.controller.abort();
    request.context?.removeEventListener?.("statechange", request.contextStateHandler);
    resetPCMOutput(request);
    if (request.pcmOutput) request.pcmOutput.port.onmessage = null;
    request.eventController?.abort();
    try { Promise.resolve(request.eventReader?.cancel()).catch(() => {}); } catch (_) {}
    for (const resume of request.spaceWaiters || []) resume();
    request.spaceWaiters?.clear();
    if (request.reply) { request.reply.queue = []; request.reply.queuedBytes = 0; }
    if (request.worklet) request.worklet.port.onmessage = null;
    for (const node of [request.source, request.worklet, request.output, request.pcmOutput, request.pcmGain]) {
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
      pcmOutput: null, pcmGain: null, outputEpoch: 0, frameParent: null, context: null, contextStateHandler: null,
      queue: [], queuedBytes: 0, nextSequence: 0, pcmInFlight: false, pendingPCM: null, pcmRetrying: false,
      controls: [], controlInFlight: false, controlOperation: null, controlTimer: null, controlRetrying: false,
      pollInFlight: false, pollTimer: null, pollDue: false, stateEpoch: 0,
      stage: "listening", reply: null, completedTurns: new Set(), supersededTurns: new Set(),
      eventCursor: 0, eventController: null, eventReader: null, spaceWaiters: new Set(),
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
      await abortable(Promise.all([
        context.audioWorklet.addModule("/voice_pcm_worklet.js"),
        context.audioWorklet.addModule("/voice_pcm_output_worklet.js"),
      ]), request.controller.signal);
      if (!currentCapture(request)) { disposeCapture(request); return; }
      request.source = context.createMediaStreamSource(stream);
      request.worklet = new window.AudioWorkletNode(context, "nen-gitsune-pcm", {
        numberOfInputs: 1, numberOfOutputs: 1, outputChannelCount: [1],
      });
      request.output = context.createGain(); request.output.gain.value = 0;
      request.source.connect(request.worklet); request.worklet.connect(request.output); request.output.connect(context.destination);
      request.pcmOutput = new window.AudioWorkletNode(context, "nen-gitsune-pcm-output", {
        numberOfInputs: 0, numberOfOutputs: 1, outputChannelCount: [1],
      });
      request.pcmGain = context.createGain(); request.pcmGain.gain.value = audioPlayer.volume;
      request.pcmOutput.connect(request.pcmGain); request.pcmGain.connect(context.destination);
      request.pcmOutput.port.onmessage = (event) => receivePCMOutput(request, event.data);
      request.worklet.port.onmessage = (event) => receivePCM(request, event.data);
      for (const track of stream.getAudioTracks?.() || []) {
        track.addEventListener?.("ended", () => failCapture(request, "マイクとの接続が切れました。"));
      }
      clearTimeout(request.permissionTimer); request.permissionTimer = null;
      request.ready = true;
      request.context = context;
      request.contextStateHandler = () => {
        if (currentCapture(request) && request.ready && context.state !== "running") {
          failCapture(request, "音声の入出力が中断されました。");
        }
      };
      context.addEventListener?.("statechange", request.contextStateHandler);
      request.contextStateHandler();
      if (!currentCapture(request)) return;
      void syncHeartbeat();
      void refreshMicrophones();
      renderState(request);
      schedulePoll(request, 0);
      void consumeEventStream(request);
    } catch (error) {
      if (!currentCapture(request)) { disposeCapture(request); return; }
      const denied = ["NotAllowedError", "SecurityError"].includes(error.name);
      const missing = ["NotFoundError", "OverconstrainedError"].includes(error.name);
      failCapture(request, denied ? "マイクが許可されていません。" : missing ? "選択したマイクを利用できません。録音マイクを選び直してください。" : error.message || "マイクを開始できませんでした。");
    }
  }

  function receivePCM(request, data) {
    if (!currentCapture(request) || !request.ready || data?.type !== "pcm") return;
    // RMS is display-only. Send every frame, including silence and reply playback.
    // The server alone decides whether a frame participates in recognition.
    if (inputLevel) inputLevel.value = Number.isFinite(data.level) ? Math.min(1, Math.max(0, data.level * 8 * inputGainPercent / 100)) : 0;
    if (!data.pcm || data.pcm.byteLength !== PCM_FRAME_BYTES) { failCapture(request, "音声フレームを正しく作成できませんでした。"); return; }
    if (request.queuedBytes + (request.pendingPCM?.bytes || 0) + data.pcm.byteLength > MAX_QUEUE_BYTES) {
      failCapture(request, "通信待ちの音声が約30秒の保存上限に達しました。未送信の音声を黙って捨てずに停止しました。");
      return;
    }
    // Freeze the server-side gain with this frame; changing the slider cannot alter a retry.
    request.queue.push({ pcm: data.pcm, gain: inputGainPercent / 100 }); request.queuedBytes += data.pcm.byteLength;
    if (request.queuedBytes >= 64_000) renderState(request);
    pumpTransport(request);
  }

  function schedulePoll(request, delay = POLL_INTERVAL_MS) {
    if (!currentCapture(request)) return;
    clearTimeout(request.pollTimer);
    request.pollDue = false;
    request.pollTimer = setTimeout(() => { request.pollDue = true; pumpTransport(request); }, delay);
  }

  async function fetchJSON(request, url, options = {}, timeoutMs = REQUEST_TIMEOUT_MS, signal = request.controller.signal) {
    const controller = new AbortController();
    const abort = () => controller.abort();
    request.controller.signal.addEventListener("abort", abort, { once: true });
    if (signal !== request.controller.signal) signal.addEventListener("abort", abort, { once: true });
    if (signal.aborted) abort();
    const timer = setTimeout(abort, timeoutMs);
    try {
      const response = await abortable(fetch(url, { cache: "no-store", credentials: "same-origin", ...options, signal: controller.signal }), controller.signal);
      return await abortable(readJson(response), controller.signal);
    } finally {
      clearTimeout(timer);
      request.controller.signal.removeEventListener("abort", abort);
      if (signal !== request.controller.signal) signal.removeEventListener("abort", abort);
    }
  }

  function retryableTransportError(error) {
    return !error.fatal && (!error.status || error.status >= 500 || [408, 429].includes(error.status));
  }

  async function withRetries(request, operation, { pcm = false, signal = request.controller.signal } = {}) {
    const deadline = pcm ? performance.now() + PCM_RETRY_WINDOW_MS : Infinity;
    for (let attempt = 0; pcm || attempt < 3; attempt++) {
      if (!currentCapture(request)) throw new Error("Session stopped");
      if (signal.aborted) { const error = new Error("Request aborted"); error.name = "AbortError"; throw error; }
      const remaining = deadline - performance.now();
      if (remaining <= 0) throw new Error("音声サーバーとの通信を約25秒間回復できませんでした。");
      try { return await abortable(operation(pcm ? Math.min(PCM_REQUEST_TIMEOUT_MS, remaining) : REQUEST_TIMEOUT_MS), signal); }
      catch (error) {
        if (!currentCapture(request)) throw error;
        if (signal.aborted) throw error;
        if (!retryableTransportError(error)) throw error;
        if (!pcm && attempt === 2) throw error;
        const available = deadline - performance.now();
        if (available <= 0) throw new Error("音声サーバーとの通信を約25秒間回復できませんでした。");
        const delay = Math.min(pcm ? Math.min(2_000, 250 * 2 ** Math.min(attempt, 3)) : 250 * (attempt + 1), available);
        if (pcm) { request.pcmRetrying = true; renderState(request); }
        await abortable(new Promise((resolve) => setTimeout(resolve, delay)), signal);
      }
    }
  }

  function validState(request, body) {
    return currentCapture(request) && body.session_id === request.sessionId && body.generation === request.generation;
  }

  function applyState(request, body, epoch) {
    if (!validState(request, body)) return;
    if (Number.isSafeInteger(body.next_sequence) && body.next_sequence < request.nextSequence) return;
    confirmPendingPCM(request, body);
    // PCM ACKs still carry this control when a full playback queue has paused
    // the event reader. Do not skip event cursors: replay will consume them.
    if (body.interruption && interruptReply(request, body.interruption)) request.stateEpoch++;
    if (epoch !== request.stateEpoch) return;
    request.stateEpoch++;
    if (typeof body.stage === "string") request.stage = body.stage;
    // Confirmed transcript/text events are authoritative for the current output turn.
    if (body.transcript && (!request.reply || request.reply.ended || !request.reply.transcriptConfirmed)) {
      transcriptLine.textContent = body.transcript;
    }
    if (request.reply?.ended && ["listening", "speaking"].includes(request.stage)) request.reply = null;
    renderState(request);
  }

  function renderState(request) {
    if (!currentCapture(request) || !request.ready) return;
    const reply = request.reply;
    if (reply && !reply.ended) {
      setStatus(reply.failed ? "返答音声を再生できませんでした。再生を再試行するか、会話を開始し直してください。"
        : reply.awaitingUser ? "音声を受け取りました。再生ボタンを押してください。マイクの送信は継続しています。"
          : reply.playing ? "返答音声を出力しています。話しかけると聞き取りに切り替わります。" : "次の返答音声を待っています…", reply.failed ? "error" : "active");
    } else {
      const messages = {
        listening: "音声をサーバーへ送信中です。話しかけると文字が表示されます。",
        speaking: "聞き取り中です。話し終えるとサーバーが返答を作ります。",
        stt: "音声を文字にしています…", llm: "返事を考えています…", tts: "返答音声を作っています…",
        reply_ready: "返答音声を待っています…", playing: "返答音声を出力中です。",
        cooldown: "サーバーが次の発話を受け付ける準備をしています…", failed: "音声処理に失敗しました。停止して会話を開始し直してください。",
      };
      setStatus(messages[request.stage] || "サーバーの音声状態を確認しています…", request.stage === "failed" ? "error" : "active");
    }
    if (request.controlRetrying) setStatus("再生状態を再送信しています。マイクの音声送信は続けています。", "error");
    if (request.pcmRetrying || request.queuedBytes >= 64_000) {
      setStatus("音声の到着を確認しています。マイク入力を保ち、通信が戻り次第、順番に送信します。", "active");
    }
    if (microphoneStatus) microphoneStatus.textContent = "マイク接続・音声送信を継続中";
    refreshButtons();
  }

  function pumpTransport(request) {
    if (!currentCapture(request)) return;
    void pumpAudio(request);
    void pumpControls(request);
    void pumpPoll(request);
  }

  async function pumpAudio(request) {
    if (!currentCapture(request) || request.pcmInFlight || !request.queue.length) return;
    const frame = request.queue.shift(); request.queuedBytes -= frame.pcm.byteLength;
    const sequence = request.nextSequence;
    const epoch = request.stateEpoch;
    const operation = { sequence, bytes: frame.pcm.byteLength, controller: new AbortController(), settled: false, resolve: null, reject: null };
    const confirmation = new Promise((resolve, reject) => { operation.resolve = resolve; operation.reject = reject; });
    request.pendingPCM = operation;
    request.pcmInFlight = true;
    const route = `${API_SESSIONS}/${encodeURIComponent(request.sessionId)}/audio-stream`;
    try {
      const upload = withRetries(request, async (timeoutMs) => {
        const body = await fetchJSON(request, route, {
          method: "POST", headers: { "Content-Type": PCM_CONTENT_TYPE, "X-Voice-Generation": String(request.generation), "X-Audio-Sequence": String(sequence), "X-Audio-Input-Gain": String(frame.gain) }, body: frame.pcm,
        }, timeoutMs, operation.controller.signal);
        validatePCMACK(request, body, sequence);
        return body;
      }, { pcm: true, signal: operation.controller.signal });
      const body = await Promise.race([upload, confirmation]);
      if (!currentCapture(request)) return;
      // ACK ownership and sequence are validated even when its UI snapshot is stale.
      validatePCMACK(request, body, sequence);
      if (request.pendingPCM !== operation) return;
      request.nextSequence = body.next_sequence;
      request.pcmRetrying = false;
      operation.settled = true;
      applyState(request, body, epoch);
      renderState(request);
    } catch (error) {
      if (currentCapture(request)) failCapture(request, error.message || "音声サーバーへ接続できませんでした。");
    } finally {
      operation.settled = true;
      operation.controller.abort();
      if (request.pendingPCM === operation) request.pendingPCM = null;
      request.pcmInFlight = false;
      if (currentCapture(request)) pumpTransport(request);
    }
  }

  function validatePCMACK(request, body, sequence) {
    if (!validState(request, body)) throw eventError("音声サーバーの会話識別を確認できませんでした。");
    if (body.sequence !== sequence || body.next_sequence !== sequence + 1) {
      throw eventError("音声データの順序を確認できませんでした。");
    }
    if (body.fatal === true || body.error === "vad_unavailable") throw eventError(body.message || "サーバーの音声判定を利用できません。");
  }

  function confirmPendingPCM(request, body) {
    const pending = request.pendingPCM;
    if (!pending || pending.settled || !Number.isSafeInteger(body.next_sequence)) return;
    if (body.fatal === true || body.error === "vad_unavailable") {
      pending.settled = true;
      pending.reject(eventError(body.message || "サーバーの音声判定を利用できません。"));
    } else if (body.next_sequence === pending.sequence + 1) {
      pending.settled = true;
      pending.resolve({ ...body, sequence: pending.sequence });
    } else if (body.next_sequence > pending.sequence + 1) {
      pending.settled = true;
      pending.reject(eventError("音声データの順序を確認できませんでした。"));
    }
  }

  async function pumpControls(request) {
    if (!currentCapture(request) || request.controlInFlight || request.controlTimer !== null || !request.controls.length) return;
    const control = request.controls.shift();
    const operation = { control, controller: new AbortController() };
    request.controlOperation = operation;
    const epoch = request.stateEpoch;
    request.controlInFlight = true;
    const route = `${API_SESSIONS}/${encodeURIComponent(request.sessionId)}/audio-stream/playback`;
    try {
      const body = await withRetries(request, () => fetchJSON(request, route, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ generation: request.generation, turn_id: control.turnId, event: control.event }),
      }, REQUEST_TIMEOUT_MS, operation.controller.signal), { signal: operation.controller.signal });
      if (!currentCapture(request)) return;
      if (request.supersededTurns.has(control.turnId) || operation.controller.signal.aborted) return;
      if (!validState(request, body)) throw eventError("音声サーバーの会話識別を確認できませんでした。");
      request.controlRetrying = false;
      applyState(request, body, epoch);
      renderState(request);
    } catch (error) {
      if (!currentCapture(request)) return;
      if (request.supersededTurns.has(control.turnId) || operation.controller.signal.aborted) return;
      // A lost ended ACK may be retried after PCM has already started the next
      // turn. Only this superseded, locally completed turn can be discarded.
      if (error.code === "playback_turn_mismatch" && request.completedTurns.has(control.turnId)) {
        request.controlRetrying = false;
        renderState(request);
        return;
      }
      if (!retryableTransportError(error)) { failCapture(request, error.message); return; }
      request.controls.unshift(control);
      request.controlRetrying = true;
      renderState(request);
      request.controlTimer = setTimeout(() => {
        request.controlTimer = null;
        if (currentCapture(request)) pumpTransport(request);
      }, 1_000);
    } finally {
      if (request.controlOperation === operation) {
        request.controlOperation = null;
        request.controlInFlight = false;
      }
      if (currentCapture(request)) pumpTransport(request);
    }
  }

  async function pumpPoll(request) {
    if (!currentCapture(request) || request.pollInFlight || !request.pollDue) return;
    const epoch = request.stateEpoch;
    request.pollDue = false;
    request.pollInFlight = true;
    let failed = false;
    try {
      const route = `${API_SESSIONS}/${encodeURIComponent(request.sessionId)}/audio-stream`;
      const body = await fetchJSON(request, route, { headers: { "X-Voice-Generation": String(request.generation) } });
      if (!currentCapture(request)) return;
      if (!validState(request, body)) throw eventError("音声サーバーの会話識別を確認できませんでした。");
      applyState(request, body, epoch);
    } catch (error) {
      failed = true;
      if (currentCapture(request) && !retryableTransportError(error)) failCapture(request, error.message);
    } finally {
      request.pollInFlight = false;
      if (currentCapture(request)) schedulePoll(request, failed ? 1_000
        : request.pendingPCM || request.queue.length ? PCM_CONFIRM_INTERVAL_MS : POLL_INTERVAL_MS);
    }
  }

  function notifyPlayback(request, reply, event) {
    if (!currentCapture(request) || request.reply !== reply || request.supersededTurns.has(reply.turnId)) return;
    if (event === "started") {
      if (reply.started) return;
      reply.started = true;
    } else if (event === "ended") {
      if (reply.ended || !reply.complete || reply.current || reply.queue.length) return;
      reply.ended = true;
      request.completedTurns.add(reply.turnId);
    } else if (event === "failed") {
      if (reply.failureNotified) return;
      reply.failureNotified = true;
    }
    request.controls.push({ turnId: reply.turnId, event });
    renderState(request);
    pumpTransport(request);
  }

  function eventError(message, code = "invalid_voice_event") {
    const error = new Error(message);
    error.code = code;
    error.fatal = true;
    return error;
  }

  function replyForEvent(request, event) {
    if (typeof event.turn_id !== "string" || !event.turn_id) throw eventError("返答の識別情報を確認できませんでした。");
    if (request.completedTurns.has(event.turn_id)) {
      if (event.type === "complete") return null;
      throw eventError("終了した返答のデータが遅れて届きました。");
    }
    if (!request.reply || request.reply.turnId !== event.turn_id) {
      if (request.reply && !request.reply.ended) throw eventError("複数の返答が同時に届きました。");
      request.reply = {
        turnId: event.turn_id, queue: [], queuedBytes: 0, current: null,
        complete: false, started: false, ended: false, failed: false, failureNotified: false,
        playing: false, awaitingUser: false, text: "", transcriptConfirmed: false,
        audioKeys: new Set(), audioCount: 0,
        outputMode: null, pcmEpoch: null, pcmDrained: true,
      };
    }
    return request.reply;
  }

  function resumeEventReader(request) {
    if (request.reply && request.reply.queuedBytes >= MAX_PLAYBACK_QUEUE_BYTES / 2) return;
    for (const resume of request.spaceWaiters) resume();
    request.spaceWaiters.clear();
  }

  function finishReplyIfReady(request, reply) {
    if (!currentCapture(request) || request.reply !== reply || !reply.complete || reply.current || reply.queue.length) return;
    if (reply.outputMode === "pcm" && (!reply.pcmDrained || reply.queuedBytes !== 0)) return;
    if (!reply.audioCount) throw eventError("返答の音声データがありませんでした。");
    notifyPlayback(request, reply, "ended");
  }

  function interruptReply(request, event) {
    if (!currentCapture(request) || event.session_id !== request.sessionId || event.generation !== request.generation) return false;
    const oldTurn = event.interrupted_turn_id || event.turn_id;
    if (typeof oldTurn !== "string" || !oldTurn || event.turn_id !== oldTurn
      || typeof event.new_turn_id !== "string" || !event.new_turn_id || event.new_turn_id === oldTurn) {
      throw eventError("割り込み対象の返答を確認できませんでした。");
    }
    if (request.supersededTurns.has(oldTurn)) return false;
    request.supersededTurns.add(oldTurn);
    request.completedTurns.add(oldTurn);
    const pendingRetry = request.controls[0]?.turnId === oldTurn;
    request.controls = request.controls.filter((control) => control.turnId !== oldTurn);
    const activeControl = request.controlOperation?.control.turnId === oldTurn;
    if (activeControl) request.controlOperation.controller.abort();
    if (pendingRetry || activeControl) {
      clearTimeout(request.controlTimer); request.controlTimer = null;
      request.controlRetrying = false;
    }
    if (!request.reply || request.reply.turnId === oldTurn) {
      resetAudio();
      resetPCMOutput(request);
      if (request.reply) {
        request.reply.queue = []; request.reply.queuedBytes = 0;
        request.reply.current = null; request.reply.ended = true;
      }
      request.reply = null;
      request.stage = "speaking";
      transcriptLine.textContent = "";
      responseLine.textContent = "";
      resumeEventReader(request);
      renderState(request);
    }
    pumpTransport(request);
    return true;
  }

  function resetPCMOutput(request) {
    request.outputEpoch++;
    request.pcmOutput?.port.postMessage({ type: "reset", epoch: request.outputEpoch, turn_id: null });
  }

  function receivePCMOutput(request, message) {
    const reply = request.reply;
    if (!currentCapture(request) || !reply || reply.outputMode !== "pcm" || reply.ended
      || message?.epoch !== request.outputEpoch || message.epoch !== reply.pcmEpoch
      || message.turn_id !== reply.turnId || request.supersededTurns.has(reply.turnId)) return;
    if (message.type === "started") {
      reply.playing = true;
      notifyPlayback(request, reply, "started");
    } else if (message.type === "consumed") {
      if (!Number.isSafeInteger(message.bytes) || message.bytes <= 0 || message.bytes > reply.queuedBytes) {
        failCapture(request, "音声出力の消費量を確認できませんでした。"); return;
      }
      reply.queuedBytes -= message.bytes;
      resumeEventReader(request);
    } else if (message.type === "drained") {
      reply.pcmDrained = reply.queuedBytes === 0;
      if (reply.pcmDrained) reply.playing = false;
      try { finishReplyIfReady(request, reply); }
      catch (error) { failCapture(request, error.message); return; }
    } else if (message.type === "error") {
      failCapture(request, "返答のPCM音声を出力できませんでした。"); return;
    }
    renderState(request);
  }

  function receiveAudioFrame(request, event) {
    if (!Number.isSafeInteger(event.cursor) || event.cursor < 1) throw eventError("音声応答の番号を確認できませんでした。");
    if (event.cursor <= request.eventCursor) return;
    if (event.cursor !== request.eventCursor + 1) throw eventError("音声応答の一部を受信できませんでした。", "event_cursor_gap");
    if (typeof event.turn_id !== "string" || !event.turn_id
      || !["audio_chunk", "acknowledgement"].includes(event.source_type)
      || !Number.isSafeInteger(event.index) || event.index < 0
      || event.media_type !== "audio/pcm" || event.sample_rate !== PCM_OUTPUT_RATE
      || event.channels !== 1 || event.format !== "s16le"
      || typeof event.audio_base64 !== "string"
      || !Number.isSafeInteger(event.total_samples) || event.total_samples < 1
      || event.total_samples > PCM_OUTPUT_RATE * 180
      || event.frame_count !== Math.ceil(event.total_samples / PCM_OUTPUT_FRAME_SAMPLES)
      || !Number.isSafeInteger(event.frame_index) || event.frame_index < 0 || event.frame_index >= event.frame_count
      || event.sample_offset !== event.frame_index * PCM_OUTPUT_FRAME_SAMPLES
      || event.is_last !== (event.frame_index === event.frame_count - 1)) {
      throw eventError("返答PCM音声のフレーム情報を確認できませんでした。");
    }
    const key = `${event.source_type}:${event.index}`;
    const identity = `${event.turn_id}/${key}`;
    let parent = request.frameParent;
    if (!parent) {
      parent = { cursor: event.cursor, identity, totalSamples: event.total_samples, count: event.frame_count, next: 0, counted: false };
      request.frameParent = parent;
    }
    if (parent.cursor !== event.cursor || parent.identity !== identity
      || parent.totalSamples !== event.total_samples || parent.count !== event.frame_count) {
      throw eventError("返答PCM音声の途中で識別情報が変わりました。");
    }
    if (event.frame_index < parent.next) return;
    if (event.frame_index !== parent.next) throw eventError("返答PCM音声のフレームが欠けています。");
    const superseded = request.supersededTurns.has(event.turn_id);
    if (!superseded) {
      const reply = replyForEvent(request, event);
      if (reply && !reply.audioKeys.has(key)) {
        if (!request.pcmOutput || audioContext?.state !== "running") throw eventError("PCM音声の出力を開始できませんでした。");
        if (reply.outputMode && reply.outputMode !== "pcm") throw eventError("返答音声の出力形式が途中で変わりました。");
        let binary;
        try { binary = atob(event.audio_base64); } catch (_) { throw eventError("返答PCM音声を読み取れませんでした。"); }
        const expected = Math.min(PCM_OUTPUT_FRAME_SAMPLES, event.total_samples - event.sample_offset) * 2;
        if (binary.length !== expected) throw eventError("返答PCM音声の長さを確認できませんでした。");
        if (reply.queuedBytes + binary.length > MAX_PLAYBACK_QUEUE_BYTES) {
          const error = new Error("Playback queue is full"); error.backpressure = true; throw error;
        }
        if (!reply.outputMode) {
          reply.outputMode = "pcm";
          reply.pcmEpoch = ++request.outputEpoch;
          request.pcmOutput.port.postMessage({ type: "reset", epoch: reply.pcmEpoch, turn_id: reply.turnId });
        }
        const bytes = new Uint8Array(binary.length);
        for (let index = 0; index < binary.length; index++) bytes[index] = binary.charCodeAt(index);
        request.pcmOutput.port.postMessage({ type: "audio", epoch: reply.pcmEpoch, turn_id: reply.turnId, pcm: bytes.buffer }, [bytes.buffer]);
        reply.queuedBytes += binary.length;
        reply.pcmDrained = false;
        if (!parent.counted) { reply.audioCount++; parent.counted = true; }
        if (event.is_last) reply.audioKeys.add(key);
        renderState(request);
      }
    }
    parent.next++;
    request.stateEpoch++;
    if (event.is_last) {
      request.eventCursor = event.cursor;
      request.frameParent = null;
    }
  }

  function handleVoiceEvent(request, event) {
    if (!currentCapture(request) || event.session_id !== request.sessionId || event.generation !== request.generation) return;
    // Heartbeats and terminal controls do not advance the durable event cursor.
    if (event.type === "heartbeat") return;
    if (event.type === "audio_frame") { receiveAudioFrame(request, event); return; }
    if (request.frameParent) throw eventError("返答PCM音声が完了する前に次のイベントが届きました。");
    const noSpeech = event.type === "error" && event.error === "no_speech"
      && typeof event.turn_id === "string" && Boolean(event.turn_id)
      && Number.isSafeInteger(event.cursor) && event.cursor >= 1;
    const superseded = request.supersededTurns.has(event.turn_id);
    if (event.type === "error" && !noSpeech && !superseded) throw eventError(event.message || "音声応答の受信に失敗しました。", event.error || "voice_stream_error");
    if (!Number.isSafeInteger(event.cursor) || event.cursor < 1) throw eventError("音声応答の番号を確認できませんでした。");
    if (event.cursor <= request.eventCursor) return;
    if (event.cursor !== request.eventCursor + 1) throw eventError("音声応答の一部を受信できませんでした。", "event_cursor_gap");
    if (event.type === "interruption" || superseded) {
      if (event.type === "interruption") interruptReply(request, event);
      request.eventCursor = event.cursor;
      request.stateEpoch++;
      return;
    }
    if (noSpeech) {
      if (request.reply?.turnId === event.turn_id) {
        resetAudio();
        request.reply.queue = [];
        request.reply.current = null;
        request.reply.queuedBytes = 0;
        request.reply = null;
        responseLine.textContent = "";
        resumeEventReader(request);
      }
      request.completedTurns.add(event.turn_id);
      request.eventCursor = event.cursor;
      request.stateEpoch++;
      setStatus("発話を確認できませんでした。聞き取りと音声送信を続けています。", "active");
      return;
    }
    if (["transcript", "text_delta", "acknowledgement", "audio_chunk", "complete"].includes(event.type)) {
      const reply = replyForEvent(request, event);
      if (reply) {
        if (event.type === "transcript") {
          reply.transcriptConfirmed = true;
          transcriptLine.textContent = event.text || "（認識できませんでした）";
        } else if (event.type === "text_delta") {
          reply.text += event.text || "";
          if (reply.text.length > 4_000) throw eventError("返答テキストの受信上限を超えました。");
          responseLine.textContent = reply.text;
        } else if (event.type === "acknowledgement" || event.type === "audio_chunk") {
          if (reply.outputMode && reply.outputMode !== "legacy") throw eventError("返答音声の出力形式が途中で変わりました。");
          reply.outputMode = "legacy";
          const key = event.type === "acknowledgement" ? "acknowledgement" : Number.isSafeInteger(event.index) ? `audio:${event.index}` : `event:${event.cursor}`;
          if (!reply.audioKeys.has(key)) {
            if (typeof event.audio_base64 !== "string" || !event.media_type?.startsWith("audio/")) throw eventError("返答音声の形式を確認できませんでした。");
            let binary;
            try { binary = atob(event.audio_base64); } catch (_) { throw eventError("返答音声を読み取れませんでした。"); }
            if (!binary.length || binary.length > MAX_PLAYBACK_QUEUE_BYTES) throw eventError("返答音声の大きさが受信上限を超えました。");
            if (reply.queuedBytes + binary.length > MAX_PLAYBACK_QUEUE_BYTES) {
              const error = new Error("Playback queue is full"); error.backpressure = true; throw error;
            }
            const bytes = new Uint8Array(binary.length);
            for (let index = 0; index < binary.length; index++) bytes[index] = binary.charCodeAt(index);
            reply.queue.push({ key, blob: new Blob([bytes], { type: event.media_type }), bytes: bytes.length });
            reply.queuedBytes += bytes.length;
            reply.audioCount++;
            reply.audioKeys.add(key);
            pumpPlayback(request, reply);
          }
        } else if (event.type === "complete") {
          reply.complete = true;
          reply.transcriptConfirmed = true;
          if (event.transcript) transcriptLine.textContent = event.transcript;
          if (event.response_text) responseLine.textContent = event.response_text;
          finishReplyIfReady(request, reply);
        }
        renderState(request);
      }
    }
    request.eventCursor = event.cursor;
    request.stateEpoch++;
  }

  function markChunkPlaying(request, reply, chunk) {
    if (!currentCapture(request) || request.reply !== reply || reply.current !== chunk || reply.ended) return;
    reply.playing = true;
    reply.awaitingUser = false;
    reply.failed = false;
    notifyPlayback(request, reply, "started");
    renderState(request);
  }

  function pumpPlayback(request, reply) {
    if (!currentCapture(request) || request.reply !== reply || reply.current || reply.ended) return;
    if (!reply.queue.length) { finishReplyIfReady(request, reply); return; }
    const chunk = reply.queue.shift();
    reply.current = chunk;
    reply.playing = false;
    reply.awaitingUser = false;
    resetAudio();
    audioObjectUrl = URL.createObjectURL(chunk.blob);
    audioPlayer.src = audioObjectUrl; audioPlayer.hidden = false;
    audioPlayer.onended = () => {
      if (!currentCapture(request) || request.reply !== reply || reply.current !== chunk) return;
      reply.current = null;
      reply.playing = false;
      reply.queuedBytes -= chunk.bytes;
      resetAudio();
      resumeEventReader(request);
      try { pumpPlayback(request, reply); renderState(request); }
      catch (error) { failCapture(request, error.message); }
    };
    audioPlayer.onerror = () => {
      if (!currentCapture(request) || request.reply !== reply || reply.current !== chunk) return;
      reply.failed = true; reply.playing = false; reply.awaitingUser = true;
      notifyPlayback(request, reply, "failed"); renderState(request);
    };
    try {
      Promise.resolve(audioPlayer.play()).then(() => markChunkPlaying(request, reply, chunk)).catch(() => {
        if (!currentCapture(request) || request.reply !== reply || reply.current !== chunk || reply.playing) return;
        reply.awaitingUser = true; renderState(request);
      });
    } catch (_) { reply.awaitingUser = true; renderState(request); }
  }

  async function consumeEventStream(request) {
    let failures = 0;
    let disconnectedSince = null;
    while (currentCapture(request)) {
      const controller = new AbortController();
      request.eventController = controller;
      const abort = () => controller.abort();
      request.controller.signal.addEventListener("abort", abort, { once: true });
      let reader = null;
      let headerTimer = setTimeout(abort, REQUEST_TIMEOUT_MS);
      let idleTimer = null;
      let backpressure = false;
      let receivedEvent = false;
      const beforeCursor = request.eventCursor;
      const acceptEvent = (line) => {
        let event;
        try { event = JSON.parse(line); } catch (_) { throw eventError("音声応答の形式を確認できませんでした。"); }
        if (!event || typeof event !== "object") throw eventError("音声応答の形式を確認できませんでした。");
        handleVoiceEvent(request, event);
        if (validState(request, event) && ["heartbeat", "audio_frame", "interruption", "transcript", "text_delta", "acknowledgement", "audio_chunk", "complete", "error"].includes(event.type)) {
          receivedEvent = true;
          failures = 0;
          disconnectedSince = null;
        }
      };
      try {
        const route = `${API_SESSIONS}/${encodeURIComponent(request.sessionId)}/audio-stream/events?after=${request.eventCursor}`;
        const response = await fetch(route, { cache: "no-store", credentials: "same-origin",
          headers: { Accept: "application/x-ndjson", "X-Voice-Generation": String(request.generation), "X-Voice-Audio-Profile": PCM_OUTPUT_PROFILE }, signal: controller.signal });
        clearTimeout(headerTimer); headerTimer = null;
        if (!response.ok) await readJson(response);
        if (!response.body?.getReader) throw eventError("音声の逐次受信に対応していないサーバー応答です。");
        reader = response.body.getReader(); request.eventReader = reader;
        const decoder = new TextDecoder("utf-8");
        let pending = "";
        while (currentCapture(request)) {
          clearTimeout(idleTimer); idleTimer = setTimeout(abort, EVENT_IDLE_TIMEOUT_MS);
          const { value, done } = await reader.read();
          clearTimeout(idleTimer); idleTimer = null;
          if (!currentCapture(request)) return;
          pending += decoder.decode(value || new Uint8Array(), { stream: !done });
          if (pending.length > MAX_EVENT_LINE_CHARS) throw eventError("音声応答の受信上限を超えました。");
          const lines = pending.split("\n"); pending = lines.pop() || "";
          for (const line of lines) {
            if (!line.trim()) continue;
            acceptEvent(line);
          }
          if (done) {
            if (pending.trim()) acceptEvent(pending);
            if (!receivedEvent) throw new Error("音声応答の接続を確認しています。");
            break;
          }
        }
        failures = 0;
        disconnectedSince = null;
      } catch (error) {
        if (!currentCapture(request)) return;
        if (error.backpressure) backpressure = true;
        else if (error.fatal || (error.status && error.status < 500 && ![408, 429].includes(error.status))) {
          failCapture(request, error.message || "音声応答を継続して受信できませんでした。"); return;
        } else {
          failures = request.eventCursor > beforeCursor ? 1 : failures + 1;
          if (disconnectedSince === null || request.eventCursor > beforeCursor) disconnectedSince = performance.now();
          if (performance.now() - disconnectedSince >= PCM_RETRY_WINDOW_MS) {
            failCapture(request, "音声応答との通信を約25秒間回復できませんでした。"); return;
          }
          setStatus("音声応答を再接続しています。受信済みの音声は順に再生します。", "error");
        }
      } finally {
        clearTimeout(headerTimer); clearTimeout(idleTimer);
        controller.abort();
        if (reader) { try { await reader.cancel(); } catch (_) {} try { reader.releaseLock(); } catch (_) {} }
        if (request.eventReader === reader) request.eventReader = null;
        request.controller.signal.removeEventListener("abort", abort);
      }
      if (!currentCapture(request)) return;
      try {
        if (backpressure && request.reply && request.reply.queuedBytes >= MAX_PLAYBACK_QUEUE_BYTES / 2) {
          await abortable(new Promise((resolve) => request.spaceWaiters.add(resolve)), request.controller.signal);
        }
        await abortable(new Promise((resolve) => setTimeout(resolve, failures ? Math.min(2_000, 250 * failures) : 100)), request.controller.signal);
      } catch (_) { return; }
    }
  }

  async function stopCall({ silent = false, keepalive = false } = {}) {
    const stopVersion = ++activityVersion;
    if (startRequest) { startRequest.cancelled = true; startRequest = null; }
    const stoppedSession = sessionId; sessionId = null; generation = null;
    if (captureRequest) { disposeCapture(captureRequest); captureRequest = null; }
    resetAudio();
    stopHeartbeat();
    if (heartbeatStatus) heartbeatStatus.textContent = heartbeatToggle?.checked ? "心音を停止しました。次の会話で再開します。" : "心音はOffです。";
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

  const savedGain = Number(readPreference(INPUT_GAIN_KEY));
  updateInputGain(Number.isInteger(savedGain) && savedGain >= 25 && savedGain <= 200 && savedGain % 5 === 0 ? savedGain : 100, false);
  inputGainSlider?.addEventListener("input", () => updateInputGain(inputGainSlider.value));
  for (const [button, percent] of inputGainPresets) button?.addEventListener("click", () => updateInputGain(percent));
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
    if (!request?.reply?.current || request.reply.ended || !currentCapture(request)) { audioPlayer.pause(); return; }
    markChunkPlaying(request, request.reply, request.reply.current);
  });
  audioPlayer.addEventListener("volumechange", () => {
    if (captureRequest?.pcmGain) captureRequest.pcmGain.gain.value = audioPlayer.volume;
  });
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") { void stopCall({ silent: true, keepalive: true }); setStatus("画面が切り替わったため停止しました。戻ったら会話を開始してください。"); }
  });
  window.addEventListener("pagehide", () => void stopCall({ silent: true, keepalive: true }));
  refreshButtons();
})();
