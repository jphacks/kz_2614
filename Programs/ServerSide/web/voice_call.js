"use strict";

(() => {
  const API_SESSIONS = "/api/v1/voice/sessions";
  const TOKEN_KEY = "nen-gitsune-dashboard-token";
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
  let turnRequest = null;
  let audioObjectUrl = null;

  function setStatus(message, kind = "") {
    statusLine.textContent = message;
    statusLine.classList.remove("error", "active");
    if (kind) statusLine.classList.add(kind);
  }

  function refreshButtons() {
    const active = Boolean(sessionId);
    const recording = recorder && recorder.state === "recording";
    startButton.disabled = active;
    stopButton.disabled = !active;
    recordButton.disabled = !active || Boolean(turnRequest);
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
    if (!sessionId || turnRequest) return;
    if (!window.isSecureContext || !navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      setStatus("マイクを使うにはHTTPSでこのWeb画面を開いてください。", "error");
      return;
    }

    try {
      resetAudio();
      stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
        video: false,
      });
      const preferredType = ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus"]
        .find((type) => window.MediaRecorder && MediaRecorder.isTypeSupported(type));
      recorder = preferredType ? new MediaRecorder(stream, { mimeType: preferredType }) : new MediaRecorder(stream);
      chunks = [];
      discardRecording = false;
      recorder.ondataavailable = (event) => {
        if (event.data && event.data.size > 0) chunks.push(event.data);
      };
      recorder.onerror = () => {
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
        const mediaType = (completedRecorder && completedRecorder.mimeType) || "audio/webm";
        const audio = new Blob(chunks, { type: mediaType });
        chunks = [];
        refreshButtons();
        if (!audio.size) {
          setStatus("録音データがありません。もう一度お試しください。", "error");
          return;
        }
        void sendTurn(audio);
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
      setStatus(
        permissionDenied
          ? "マイクが許可されていません。ブラウザーまたはAndroidのアプリ設定で許可してください。"
          : "録音を開始できませんでした。マイクが利用可能か確認してください。",
        "error",
      );
      refreshButtons();
    }
  }

  async function playReply(url, expectedSession, expectedGeneration) {
    const response = await fetch(url, {
      cache: "no-store",
      credentials: "same-origin",
      headers: authHeaders(),
      signal: turnRequest.signal,
    });
    if (!response.ok) throw new Error("返答音声を取得できませんでした。");
    if (sessionId !== expectedSession || generation !== expectedGeneration) return;
    const audioBlob = await response.blob();
    if (sessionId !== expectedSession || generation !== expectedGeneration) return;
    resetAudio();
    audioObjectUrl = URL.createObjectURL(audioBlob);
    audioPlayer.src = audioObjectUrl;
    audioPlayer.hidden = false;
    audioPlayer.onended = () => {
      if (sessionId === expectedSession) setStatus("返答が終わりました。続けて話せます。", "active");
      refreshButtons();
    };
    try {
      await audioPlayer.play();
      setStatus("返答を再生中です。", "active");
    } catch (_) {
      setStatus("返答を受け取りました。再生ボタンを押して聞いてください。", "active");
    }
  }

  async function sendTurn(audio) {
    if (!sessionId) return;
    const submittedSession = sessionId;
    const submittedGeneration = generation;
    const turnId = crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
    const route = `${API_SESSIONS}/${encodeURIComponent(submittedSession)}/turns/${encodeURIComponent(turnId)}`;
    turnRequest = new AbortController();
    refreshButtons();
    setStatus("音声を送信し、返答を作っています…");

    try {
      const uploadResponse = await fetch(route, {
        method: "POST",
        cache: "no-store",
        credentials: "same-origin",
        headers: authHeaders({ "Content-Type": audio.type || "audio/webm" }),
        body: audio,
        signal: turnRequest.signal,
      });
      const body = await readJson(uploadResponse);
      if (sessionId !== submittedSession || body.generation !== submittedGeneration) return;
      transcriptLine.textContent = body.transcript || "（認識できませんでした）";
      responseLine.textContent = body.response_text || "返答がありません。";
      setStatus("返答音声を受け取っています…");
      await playReply(body.speech.url, submittedSession, submittedGeneration);
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
  window.addEventListener("pagehide", () => void stopCall({ silent: true, keepalive: true }));
})();
