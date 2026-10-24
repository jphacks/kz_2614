"use strict";

const API_SETTINGS = "/api/v1/dashboard/settings";
const API_HEALTH = "/api/v1/dashboard/health";
const API_REFERENCE_AUDIO = "/api/v1/dashboard/reference-audio";
const TOKEN_KEY = "nen-gitsune-dashboard-token";
const MAX_REFERENCE_WAV_BYTES = 10 * 1024 * 1024;

const DEFAULTS = {
  stt: {
    model_id: "Systran/faster-whisper-small",
    language: "ja",
    device: "cpu",
    compute_type: "int8",
    vad_filter: true,
  },
  tts: {
    checkpoint: "Aratako/Irodori-TTS-600M-v3-VoiceDesign",
    caption: "落ち着いて親しみやすく、聞き取りやすい自然な日本語で話す。話者の声質を一定に保つ。",
  },
  behavior: {
    persona: "gentle",
    response_length: "short",
    question_frequency: "rarely",
    custom_instruction: "",
    backchannel_enabled: true,
  },
};

const form = document.getElementById("settings-form");
const tokenInput = document.getElementById("dashboard-token");
const saveButton = document.getElementById("save-button");
const reloadButton = document.getElementById("reload-button");
const resetButton = document.getElementById("reset-button");
const statusPill = document.getElementById("connection-status");
const statusLabel = document.getElementById("connection-label");
const stateTitle = document.getElementById("settings-state-title");
const stateCopy = document.getElementById("settings-state-copy");
const saveMessage = document.getElementById("save-message");
const instructionCount = document.getElementById("instruction-count");

function clone(value) {
  return JSON.parse(JSON.stringify(value));
}

let currentDefaults = clone(DEFAULTS);
let loading = false;
let referenceFile = null;

function setConnection(state, label) {
  statusPill.classList.remove("pending", "offline", "error");
  if (state !== "online") statusPill.classList.add(state);
  statusLabel.textContent = label;
}

function setMessage(message, kind = "") {
  saveMessage.textContent = message;
  saveMessage.classList.remove("error", "success");
  if (kind) saveMessage.classList.add(kind);
}

function requestHeaders(extra = {}) {
  const token = tokenInput.value.trim();
  if (token) sessionStorage.setItem(TOKEN_KEY, token);
  else sessionStorage.removeItem(TOKEN_KEY);
  const savedToken = token || sessionStorage.getItem(TOKEN_KEY) || "";
  const headers = { ...extra };
  if (savedToken) headers.Authorization = `Bearer ${savedToken}`;
  return headers;
}

async function requestJson(url, options = {}) {
  const response = await fetch(url, {
    cache: "no-store",
    credentials: "same-origin",
    ...options,
    headers: requestHeaders(options.headers || {}),
  });
  let body = {};
  try {
    body = await response.json();
  } catch (_) {
    body = {};
  }
  if (!response.ok) {
    const error = new Error(body.message || `サーバー応答 ${response.status}`);
    error.status = response.status;
    throw error;
  }
  return body;
}

function assignField(name, value) {
  const field = form.elements.namedItem(name);
  if (!field) return;
  if (field.type === "checkbox") field.checked = Boolean(value);
  else field.value = value == null ? "" : String(value);
}

function displaySettings(settings) {
  const safe = settings || currentDefaults;
  for (const [section, values] of Object.entries(safe)) {
    for (const [key, value] of Object.entries(values)) {
      assignField(`${section}.${key}`, value);
    }
  }
  assignField("behavior.backchannel_enabled", true);
  updateInstructionCount();
}

function collectSettings() {
  const value = (name) => form.elements.namedItem(name).value;
  const checked = (name) => form.elements.namedItem(name).checked;
  return {
    stt: {
      model_id: value("stt.model_id").trim(),
      language: value("stt.language"),
      device: value("stt.device"),
      compute_type: value("stt.compute_type"),
      vad_filter: checked("stt.vad_filter"),
    },
    tts: {
      checkpoint: value("tts.checkpoint").trim(),
      caption: value("tts.caption").trim(),
    },
    behavior: {
      persona: value("behavior.persona"),
      response_length: value("behavior.response_length"),
      question_frequency: value("behavior.question_frequency"),
      custom_instruction: value("behavior.custom_instruction").trim(),
      backchannel_enabled: true,
    },
  };
}

function updateInstructionCount() {
  const field = form.elements.namedItem("behavior.custom_instruction");
  instructionCount.textContent = String(field.value.length);
}

function updateLoadedState() {
  stateTitle.textContent = "設定を読み込みました";
  stateCopy.textContent = "サーバーへ保存すると、新しい会話パイプラインから設定が使われます。";
  setMessage("サーバー上の設定を表示しています。");
}

function formatBytes(bytes) {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function showReferenceAudio(info) {
  const state = document.getElementById("reference-audio-state");
  const detail = document.getElementById("reference-audio-info");
  const deleteButton = document.getElementById("delete-reference-audio");
  if (!info || !info.uploaded) {
    state.textContent = "未設定";
    state.classList.remove("saved");
    detail.textContent = "参照音声はまだ登録されていません。";
    deleteButton.disabled = true;
    return;
  }
  state.textContent = "登録済み";
  state.classList.add("saved");
  const channels = info.channels === 1 ? "モノラル" : "ステレオ";
  detail.textContent = `${info.name} · ${formatBytes(info.size_bytes)} · ${info.duration_seconds}秒 · ${info.sample_rate / 1000}kHz · ${channels}`;
  deleteButton.disabled = false;
}

async function loadReferenceAudio() {
  try {
    showReferenceAudio(await requestJson(API_REFERENCE_AUDIO));
  } catch (_) {
    document.getElementById("reference-audio-state").textContent = "読込失敗";
    document.getElementById("reference-audio-info").textContent = "参照音声の状態を読み込めません。接続と管理トークンを確認してください。";
  }
}

async function uploadReferenceAudio() {
  const uploadButton = document.getElementById("upload-reference-audio");
  if (!referenceFile) return;
  if (!referenceFile.name.toLowerCase().endsWith(".wav")) {
    setMessage("PCM WAVファイルを選択してください。", "error");
    return;
  }
  if (referenceFile.size > MAX_REFERENCE_WAV_BYTES) {
    setMessage("WAVファイルは10MB以下にしてください。", "error");
    return;
  }
  uploadButton.disabled = true;
  setMessage("WAVをサーバーへアップロードしています…");
  try {
    const info = await requestJson(API_REFERENCE_AUDIO, {
      method: "PUT",
      headers: { "Content-Type": "audio/wav" },
      body: referenceFile,
    });
    showReferenceAudio(info);
    referenceFile = null;
    document.getElementById("reference-audio-file").value = "";
    stateTitle.textContent = "参照音声をサーバーに保存しました";
    stateCopy.textContent = "新しい音声処理インスタンスからIrodori-TTSの参照音声として使われます。";
    setMessage("参照音声を保存しました。次の会話から利用されます。", "success");
  } catch (error) {
    if (error.status === 401) setConnection("error", "管理トークンが必要です");
    setMessage(error.message || "参照音声を保存できません。", "error");
  } finally {
    uploadButton.disabled = !referenceFile;
  }
}

async function deleteReferenceAudio() {
  const deleteButton = document.getElementById("delete-reference-audio");
  if (!window.confirm("サーバーに保存した参照WAVを削除しますか？")) return;
  deleteButton.disabled = true;
  setMessage("サーバーから参照音声を削除しています…");
  try {
    await requestJson(API_REFERENCE_AUDIO, { method: "DELETE" });
    showReferenceAudio({ uploaded: false });
    stateTitle.textContent = "参照音声を削除しました";
    stateCopy.textContent = "アップロード音声を削除しました。VOICE_TTS_REFERENCE_AUDIO が設定されている場合は、その音声が使われます。";
    setMessage("サーバーから参照音声を削除しました。", "success");
  } catch (error) {
    if (error.status === 401) setConnection("error", "管理トークンが必要です");
    setMessage(error.message || "参照音声を削除できません。", "error");
    deleteButton.disabled = false;
  }
}

async function loadSettings() {
  if (loading) return;
  loading = true;
  reloadButton.disabled = true;
  stateTitle.textContent = "サーバー設定を読み込みます";
  stateCopy.textContent = "設定サーバーへ接続しています。";
  setConnection("pending", "サーバーを確認中");
  try {
    const result = await requestJson(API_SETTINGS);
    currentDefaults = result.defaults || clone(DEFAULTS);
    displaySettings(result.settings);
    await loadReferenceAudio();
    setConnection("online", "設定サーバー接続済み");
    updateLoadedState();
  } catch (error) {
    if (error.status === 401) {
      setConnection("error", "管理トークンが必要です");
      stateTitle.textContent = "管理トークンを入力してください";
      stateCopy.textContent = "NENGITSUNE_DASHBOARD_TOKEN と同じ値を管理アクセス欄へ入力します。";
      setMessage("認証されていません。トークンを確認してください。", "error");
    } else {
      setConnection("offline", "設定サーバー未接続");
      stateTitle.textContent = "サーバー設定を読み込めません";
      stateCopy.textContent = "ダッシュボードサーバーを起動し、接続先を確認してください。";
      setMessage("設定は保存されていません。サーバーに接続してから再読み込みしてください。", "error");
    }
  } finally {
    loading = false;
    reloadButton.disabled = false;
  }
}

async function checkHealth() {
  try {
    await requestJson(API_HEALTH);
    if (statusPill.classList.contains("offline")) {
      setConnection("online", "設定サーバー接続済み");
    }
  } catch (_) {
    // The settings load reports the actionable connection state.
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!form.reportValidity()) return;
  saveButton.disabled = true;
  setMessage("サーバーへ設定を保存しています…");
  try {
    const result = await requestJson(API_SETTINGS, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(collectSettings()),
    });
    displaySettings(result.settings);
    setConnection("online", "設定サーバー接続済み");
    stateTitle.textContent = "サーバーに設定を保存しました";
    stateCopy.textContent = "設定は新しい会話パイプラインから反映されます。進行中の会話には後から適用されません。";
    setMessage("保存しました。次の会話から反映されます。", "success");
  } catch (error) {
    if (error.status === 401) {
      setConnection("error", "管理トークンが必要です");
      setMessage("認証されていません。管理トークンを確認してください。", "error");
    } else {
      setConnection("offline", "設定を保存できません");
      setMessage(error.message || "設定を保存できません。サーバーとの接続を確認してください。", "error");
    }
  } finally {
    saveButton.disabled = false;
  }
});

reloadButton.addEventListener("click", loadSettings);
resetButton.addEventListener("click", () => {
  displaySettings(currentDefaults);
  setMessage("既定値をフォームへ戻しました。保存するまでサーバー設定は変わりません。");
});
const referenceInput = document.getElementById("reference-audio-file");
const uploadButton = document.getElementById("upload-reference-audio");
referenceInput.addEventListener("change", () => {
  referenceFile = referenceInput.files && referenceInput.files[0] ? referenceInput.files[0] : null;
  uploadButton.disabled = !referenceFile;
  if (referenceFile) {
    document.getElementById("reference-audio-info").textContent = `選択中: ${referenceFile.name} · ${formatBytes(referenceFile.size)}`;
    setMessage("選択したWAVをサーバーへ保存できます。");
  }
});
uploadButton.addEventListener("click", uploadReferenceAudio);
document.getElementById("delete-reference-audio").addEventListener("click", deleteReferenceAudio);
tokenInput.addEventListener("input", () => {
  const token = tokenInput.value.trim();
  if (token) sessionStorage.setItem(TOKEN_KEY, token);
  else sessionStorage.removeItem(TOKEN_KEY);
});
form.elements.namedItem("behavior.custom_instruction").addEventListener("input", updateInstructionCount);
for (const field of form.elements) {
  if (field !== tokenInput) {
    field.addEventListener("input", () => {
      if (!loading) setMessage("未保存の変更があります。");
    });
    field.addEventListener("change", () => {
      if (!loading) setMessage("未保存の変更があります。");
    });
  }
}

tokenInput.value = sessionStorage.getItem(TOKEN_KEY) || "";
checkHealth();
loadSettings();
