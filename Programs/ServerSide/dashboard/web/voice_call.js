"use strict";

(() => {
  const API_SESSIONS = "/api/v1/voice/sessions";
  const TOKEN_KEY = "nen-gitsune-dashboard-token";
  const ACCEPTED_AUDIO_TYPES = new Set(["audio/webm", "audio/ogg", "audio/wav", "audio/mp4"]);
  const MAX_RECORDING_SECONDS = 60;
  const startButton = document.getElementById("voice-start-button");
  const recordButton = document.getElementById("voice-record-button");
  const stopButton = document.getElementById("voice-stop-button");
  const statusLine = document.getElementById("voice-call-status");
  const transcriptLine = document.getElementById("voice-transcript");
  const responseLine = document.getElementById("voice-response");
  const audioPlayer = document.getElementById("voice-audio");
  const tokenInput = document.getElementById("dashboard-token");

  let sessionId = null;
  let generation = null;
  let stream = null;
  let recorder = null;
  let chunks = [];
  let stopTimer = null;
  let discardRecording = false;
  let micRequestPending = false;
  let turnRequest = null;
  let audioObjectUrl = null;
  let playbackQueue = Promise.resolve();
  let playbackEpoch = 0;
  let cancelCurrentPlayback = null;
  let pageIsHidden = document.visibilityState === "hidden";

  function setStatus(message, kind = "") {
    statusLine.textContent = message;
    statusLine.classList.remove("error", "active");
    if (kind) statusLine.classList.add(kind);
  }

  function refreshButtons() {
    const active = Boolean(sessionId);
    const recording = recorder && recorder.state === "recording";
    const playing = !audioPlayer.paused && !audioPlayer.ended;
    startButton.disabled = active;
    stopButton.disabled = !active;
    recordButton.disabled = !active || Boolean(turnRequest) || micRequestPending || playing;
    recordButton.textContent = recording ? "録音を止めて送信" : "録音して話す";
    recordButton.classList.toggle("recording", Boolean(recording));
  }

  function authHeaders(extra = {}) {
    const typedToken = tokenInput ? tokenInput.value.trim() : "";
    const token = typedToken || sessionStorage.getItem(TOKEN_KEY) || "";
    const headers = { ...extra };
    if (token) headers.Authorization = `Bearer ${token}`;
    return headers;
  }

  async function readJson(response) {
    let body = {};
    try {
      body = await response.json();
    } catch (_) {
      body = {};
    }
    if (!response.ok) {
      const error = new Error(body.message || `サーバー応答 ${response.status}`);
      error.code = body.error || "request_failed";
      error.status = response.status;
      throw error;
    }
    return body;
  }

  function baseAudioType(mimeType) {
    return (mimeType || "").split(";", 1)[0].trim().toLowerCase();
  }

  function createTurnId() {
    const cryptoApi = window.crypto;
    if (cryptoApi && typeof cryptoApi.randomUUID === "function") return cryptoApi.randomUUID();
    if (cryptoApi && typeof cryptoApi.getRandomValues === "function") {
      const bytes = cryptoApi.getRandomValues(new Uint8Array(16));
      bytes[6] = (bytes[6] & 0x0f) | 0x40;
      bytes[8] = (bytes[8] & 0x3f) | 0x80;
      const hex = Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join("");
      return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
    }
    return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (character) => {
      const value = Math.floor(Math.random() * 16);
      return (character === "x" ? value : (value & 0x03) | 0x08).toString(16);
    });
  }

  async function startCall() {
    if (!window.isSecureContext || !navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      setStatus("マイクを使うにはHTTPSでこのWeb画面を開いてください。", "error");
      return;
    }
    startButton.disabled = true;
    setStatus("会話を準備しています…");
    try {
      const response = await fetch(API_SESSIONS, {
        method: "POST",
        cache: "no-store",
        credentials: "same-origin",
        headers: authHeaders(),
      });
      const body = await readJson(response);
      sessionId = body.session_id;
      generation = body.generation;
      if (pageIsHidden) {
        await stopCall({ silent: true, keepalive: true });
        return;
      }
      transcriptLine.textContent = "まだ発話はありません。";
      responseLine.textContent = "録音して話しかけてください。";
      setStatus("会話中です。録音ボタンを押して話してください。", "active");
    } catch (error) {
      setStatus(error.message, "error");
    } finally {
      refreshButtons();
    }
  }

  function releaseStream() {
    if (stream) {
      for (const track of stream.getTracks()) track.stop();
      stream = null;
    }
  }

  function resetAudio() {
    playbackEpoch += 1;
    if (cancelCurrentPlayback) {
      cancelCurrentPlayback();
      cancelCurrentPlayback = null;
    }
    playbackQueue = Promise.resolve();
    audioPlayer.pause();
    audioPlayer.removeAttribute("src");
    audioPlayer.hidden = true;
    audioPlayer.load();
    if (audioObjectUrl) {
      URL.revokeObjectURL(audioObjectUrl);
      audioObjectUrl = null;
    }
  }

  async function startRecording() {
    if (!sessionId || turnRequest || micRequestPending) return;
    if (typeof window.MediaRecorder !== "function") {
      setStatus("このブラウザーは音声録音に対応していません。Android WebViewを更新してください。", "error");
      return;
    }
    if (!window.isSecureContext || !navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      setStatus("マイクを使うにはHTTPSでこのWeb画面を開いてください。", "error");
      return;
    }

    const requestedSession = sessionId;
    micRequestPending = true;
    refreshButtons();
    try {
      resetAudio();
      const acquiredStream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
        video: false,
      });
      if (sessionId !== requestedSession || pageIsHidden) {
        for (const track of acquiredStream.getTracks()) track.stop();
        if (sessionId === requestedSession && pageIsHidden) {
          void stopCall({ silent: true, keepalive: true });
        }
        return;
      }
      stream = acquiredStream;
      const preferredType = [
        "audio/webm;codecs=opus",
        "audio/webm",
        "audio/ogg;codecs=opus",
        "audio/ogg",
        "audio/mp4",
        "audio/wav",
      ]
        .find((type) => typeof MediaRecorder.isTypeSupported === "function" && MediaRecorder.isTypeSupported(type));
      recorder = preferredType ? new MediaRecorder(stream, { mimeType: preferredType }) : new MediaRecorder(stream);
      const recordedMimeType = recorder.mimeType || preferredType || "";
      const uploadType = baseAudioType(recordedMimeType);
      if (!ACCEPTED_AUDIO_TYPES.has(uploadType)) {
        recorder = null;
        releaseStream();
        setStatus("このブラウザーの録音形式は音声APIに対応していません。WebViewの音声録音機能を更新してください。", "error");
        return;
      }
      chunks = [];
      discardRecording = false;
      recorder.ondataavailable = (event) => {
        if (event.data && event.data.size > 0) chunks.push(event.data);
      };
      recorder.onerror = () => {
        clearTimeout(stopTimer);
        stopTimer = null;
        chunks = [];
        releaseStream();
        recorder = null;
        setStatus("録音に失敗しました。マイクの許可と端末の入力を確認してください。", "error");
        refreshButtons();
      };
      recorder.onstop = () => {
        const completedRecorder = recorder;
        recorder = null;
        clearTimeout(stopTimer);
        stopTimer = null;
        releaseStream();
        if (discardRecording) {
          chunks = [];
          discardRecording = false;
          refreshButtons();
          return;
        }
        const mediaType = (completedRecorder && completedRecorder.mimeType) || recordedMimeType;
        const audio = new Blob(chunks, { type: mediaType });
        chunks = [];
        refreshButtons();
        if (!audio.size) {
          setStatus("録音データがありません。もう一度お試しください。", "error");
          return;
        }
        void sendTurn(audio, uploadType);
      };
      recorder.start(250);
      setStatus(`録音中です。最大${MAX_RECORDING_SECONDS}秒で自動停止します。話し終えたらボタンを押してください。`, "active");
      stopTimer = setTimeout(() => {
        if (recorder && recorder.state === "recording") recorder.stop();
      }, MAX_RECORDING_SECONDS * 1000);
      refreshButtons();
    } catch (error) {
      releaseStream();
      recorder = null;
      const permissionDenied = error && (error.name === "NotAllowedError" || error.name === "SecurityError");
      if (sessionId === requestedSession) {
        setStatus(
          permissionDenied
            ? "マイクが許可されていません。ブラウザーまたはAndroidのアプリ設定で許可してください。"
            : "録音を開始できませんでした。マイクが利用可能か確認してください。",
          "error",
        );
      }
    } finally {
      micRequestPending = false;
      refreshButtons();
    }
  }

  function playStreamAudio(event, expectedSession, expectedGeneration, epoch) {
    if (sessionId !== expectedSession || generation !== expectedGeneration || epoch !== playbackEpoch) {
      return Promise.resolve();
    }
    if (!event.audio_base64 || !event.media_type) {
      return Promise.reject(new Error("音声チャンクの形式が正しくありません。"));
    }

    const binary = atob(event.audio_base64);
    const bytes = new Uint8Array(binary.length);
    for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index);
    const audioBlob = new Blob([bytes], { type: event.media_type });
    const nextUrl = URL.createObjectURL(audioBlob);
    const previousUrl = audioObjectUrl;
    audioObjectUrl = nextUrl;
    if (previousUrl) URL.revokeObjectURL(previousUrl);
    audioPlayer.pause();
    audioPlayer.src = nextUrl;
    audioPlayer.hidden = false;
    audioPlayer.controls = true;
    audioPlayer.load();
    refreshButtons();

    return new Promise((resolve, reject) => {
      let settled = false;
      let playbackStarted = false;
      const finish = (error) => {
        if (settled) return;
        settled = true;
        audioPlayer.removeEventListener("ended", onEnded);
        audioPlayer.removeEventListener("error", onError);
        audioPlayer.removeEventListener("play", onPlay);
        cancelCurrentPlayback = null;
        if (error) reject(error);
        else resolve();
      };
      const onEnded = () => finish();
      const onError = () => finish(new Error("受信した音声を再生できませんでした。"));
      const onPlay = () => {
        playbackStarted = true;
        refreshButtons();
      };
      audioPlayer.addEventListener("ended", onEnded);
      audioPlayer.addEventListener("error", onError);
      audioPlayer.addEventListener("play", onPlay);
      cancelCurrentPlayback = () => finish(new DOMException("Playback stopped", "AbortError"));

      audioPlayer.play().then(() => {
        if (sessionId === expectedSession && epoch === playbackEpoch) {
          setStatus(event.type === "acknowledgement" ? "相づちを再生中です。" : "返答を再生中です。", "active");
        }
      }).catch(() => {
        if (sessionId !== expectedSession || epoch !== playbackEpoch) {
          finish(new DOMException("Playback stopped", "AbortError"));
          return;
        }
        setStatus("音声を受け取りました。プレーヤーの再生ボタンを押してください。", "active");
        if (!playbackStarted) refreshButtons();
      });
    });
  }

  function enqueueStreamAudio(event, expectedSession, expectedGeneration) {
    const epoch = playbackEpoch;
    playbackQueue = playbackQueue
      .then(() => playStreamAudio(event, expectedSession, expectedGeneration, epoch))
      .catch((error) => {
        if (error && error.name !== "AbortError" && sessionId === expectedSession) {
          setStatus(error.message || "音声を再生できませんでした。", "error");
        }
      });
  }

  async function consumeTurnStream(response, expectedSession, expectedGeneration) {
    if (!response.ok) await readJson(response);
    if (!response.body || typeof response.body.getReader !== "function") {
      throw new Error("このブラウザーは音声の逐次受信に対応していません。WebViewを更新してください。");
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder("utf-8");
    let pending = "";
    let completion = null;
    let streamError = null;
    const handleLine = (line) => {
      if (!line.trim()) return;
      const event = JSON.parse(line);
      if (
        sessionId !== expectedSession
        || generation !== expectedGeneration
        || event.generation !== expectedGeneration
      ) return;
      if (event.type === "transcript") {
        transcriptLine.textContent = event.text || "（認識できませんでした）";
        responseLine.textContent = "返答を作っています…";
        setStatus("発話を認識しました。返答を作っています…", "active");
      } else if (event.type === "text_delta") {
        if (responseLine.textContent === "返答を作っています…") responseLine.textContent = "";
        responseLine.textContent += event.text || "";
      } else if (event.type === "acknowledgement" || event.type === "audio_chunk") {
        enqueueStreamAudio(event, expectedSession, expectedGeneration);
      } else if (event.type === "complete") {
        completion = event;
      } else if (event.type === "error") {
        streamError = new Error(event.message || "音声を処理できませんでした。");
        streamError.code = event.error || "voice_processing_failed";
      }
    };

    try {
      while (true) {
        const { value, done } = await reader.read();
        pending += decoder.decode(value || new Uint8Array(), { stream: !done });
        const lines = pending.split("\n");
        pending = lines.pop() || "";
        for (const line of lines) handleLine(line);
        if (done) break;
      }
      if (pending.trim()) handleLine(pending);
    } finally {
      reader.releaseLock();
    }

    await playbackQueue;
    if (sessionId !== expectedSession || generation !== expectedGeneration) return;
    if (streamError) throw streamError;
    if (!completion) throw new Error("音声応答ストリームが途中で終了しました。");
    transcriptLine.textContent = completion.transcript || transcriptLine.textContent;
    responseLine.textContent = completion.response_text || responseLine.textContent;
    setStatus("返答が終わりました。続けて話せます。", "active");
    refreshButtons();
  }

  async function sendTurn(audio, mediaType) {
    if (!sessionId) return;
    if (!ACCEPTED_AUDIO_TYPES.has(mediaType)) {
      setStatus("録音形式が音声APIの対応範囲外です。もう一度録音してください。", "error");
      return;
    }
    const submittedSession = sessionId;
    const submittedGeneration = generation;
    const turnId = createTurnId();
    const route = `${API_SESSIONS}/${encodeURIComponent(submittedSession)}/turns/${encodeURIComponent(turnId)}`;
    turnRequest = new AbortController();
    refreshButtons();
    setStatus("音声を送信し、返答を作っています…");

    try {
      const uploadResponse = await fetch(route, {
        method: "POST",
        cache: "no-store",
        credentials: "same-origin",
        headers: authHeaders({ "Content-Type": mediaType, Accept: "application/x-ndjson" }),
        body: audio,
        signal: turnRequest.signal,
      });
      await consumeTurnStream(uploadResponse, submittedSession, submittedGeneration);
    } catch (error) {
      if (error.name === "AbortError") return;
      if (sessionId === submittedSession) {
        setStatus(error.message || "音声を送信できませんでした。", "error");
      }
    } finally {
      if (sessionId === submittedSession) {
        turnRequest = null;
        refreshButtons();
      }
    }
  }

  async function stopCall({ silent = false, keepalive = false } = {}) {
    const stoppedSession = sessionId;
    sessionId = null;
    generation = null;
    clearTimeout(stopTimer);
    stopTimer = null;
    if (turnRequest) {
      turnRequest.abort();
      turnRequest = null;
    }
    if (recorder && recorder.state === "recording") {
      discardRecording = true;
      recorder.stop();
    } else {
      recorder = null;
      releaseStream();
    }
    chunks = [];
    resetAudio();
    refreshButtons();

    if (stoppedSession) {
      try {
        const response = await fetch(`${API_SESSIONS}/${encodeURIComponent(stoppedSession)}`, {
          method: "DELETE",
          cache: "no-store",
          credentials: "same-origin",
          headers: authHeaders(),
          keepalive,
        });
        if (!response.ok && !silent) throw new Error("サーバーへの停止要求を確認できませんでした。");
      } catch (error) {
        if (!silent) setStatus(`${error.message} 録音と再生は端末で停止しました。`, "error");
        return;
      }
    }
    if (!silent) setStatus("録音・再生を停止し、会話を終了しました。");
  }

  startButton.addEventListener("click", () => void startCall());
  recordButton.addEventListener("click", () => {
    if (recorder && recorder.state === "recording") recorder.stop();
    else void startRecording();
  });
  stopButton.addEventListener("click", () => void stopCall());
  audioPlayer.addEventListener("play", refreshButtons);
  audioPlayer.addEventListener("pause", refreshButtons);
  audioPlayer.addEventListener("ended", refreshButtons);
  document.addEventListener("visibilitychange", () => {
    pageIsHidden = document.visibilityState === "hidden";
    if (pageIsHidden && !micRequestPending) {
      void stopCall({ silent: true, keepalive: true });
    }
  });
  window.addEventListener("pagehide", () => void stopCall({ silent: true, keepalive: true }));
})();
