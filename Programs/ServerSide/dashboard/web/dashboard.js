"use strict";

const API_SETTINGS = "/api/v1/dashboard/settings";
const API_HEALTH = "/api/v1/dashboard/health";
const API_REFERENCE_AUDIO = "/api/v1/dashboard/reference-audio";
const API_RUNTIME = "/api/v1/dashboard/runtime";
const TOKEN_KEY = "nen-gitsune-dashboard-token";
const MAX_REFERENCE_WAV_BYTES = 10 * 1024 * 1024;

const DEFAULTS = {
  vad: {
    threshold: 0.65,
    end_threshold: 0.35,
    min_speech_ms: 384,
    silence_ms: 896,
    preroll_ms: 640,
    max_speech_seconds: 20,
  },
  stt: {
    model_id: "Systran/faster-whisper-small",
    language: "ja",
    device: "cuda",
    compute_type: "float16",
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
const rememberedToken = sessionStorage.getItem(TOKEN_KEY);
if (rememberedToken) tokenInput.value = rememberedToken;
const saveButton = document.getElementById("save-button");
const reloadButton = document.getElementById("reload-button");
const resetButton = document.getElementById("reset-button");
const statusPill = document.getElementById("connection-status");
const statusLabel = document.getElementById("connection-label");
const stateTitle = document.getElementById("settings-state-title");
const stateCopy = document.getElementById("settings-state-copy");
const saveMessage = document.getElementById("save-message");
const instructionCount = document.getElementById("instruction-count");
const vadSensitivity = document.getElementById("vad-sensitivity");
const vadValidation = document.getElementById("vad-validation-message");
const runtimeRefresh = document.getElementById("runtime-refresh");
const runtimeSummary = document.getElementById("runtime-state-summary");
const runtimeUpdated = document.getElementById("runtime-last-updated");
const VAD_PRESETS = { low: [0.8, 0.5], normal: [0.65, 0.35], high: [0.5, 0.25] };
const VAD_FIELDS = Object.keys(DEFAULTS.vad);
const RUNTIME_STATES = { unverified: "未確認", not_loaded: "未読み込み", loading: "準備中", loaded: "読み込み済み・推論未確認", processing: "処理中", ready: "処理成功を確認", error: "エラー" };

function clone(value) {
  return JSON.parse(JSON.stringify(value));
}

let currentDefaults = clone(DEFAULTS);
let loading = false;
let reloadAfterLoad = false;
let referenceFile = null;
let runtimeTimer = null;
let runtimeRequest = null;
let runtimeEpoch = 0;
let hasRuntimeSnapshot = false;
let runtimePageActive = true;

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
  const typedToken = tokenInput.value.trim();
  if (typedToken) sessionStorage.setItem(TOKEN_KEY, typedToken);
  const savedToken = typedToken || sessionStorage.getItem(TOKEN_KEY) || "";
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
  } catch (error) {
    if (options.signal?.aborted || error.name === "AbortError") throw error;
    body = {};
  }
  if (options.signal?.aborted) {
    const error = new Error("Request aborted");
    error.name = "AbortError";
    throw error;
  }
  if (!response.ok) {
    const error = new Error(body?.message || `サーバー応答 ${response.status}`);
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
  const safe = { ...currentDefaults, ...(settings || {}), vad: { ...DEFAULTS.vad, ...currentDefaults.vad, ...settings?.vad } };
  for (const [section, values] of Object.entries(safe)) {
    for (const [key, value] of Object.entries(values)) {
      assignField(`${section}.${key}`, value);
    }
  }
  assignField("behavior.backchannel_enabled", true);
  updateVadPreset();
  validateVadSettings();
  updateInstructionCount();
}

function collectSettings() {
  const value = (name) => form.elements.namedItem(name).value;
  const checked = (name) => form.elements.namedItem(name).checked;
  return {
    vad: Object.fromEntries(VAD_FIELDS.map((key) => [key, Number(value(`vad.${key}`))])),
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

function updateVadPreset() {
  const start = Number(form.elements.namedItem("vad.threshold").value);
  const end = Number(form.elements.namedItem("vad.end_threshold").value);
  vadSensitivity.value = Object.entries(VAD_PRESETS).find(([, pair]) => pair[0] === start && pair[1] === end)?.[0] || "custom";
}

function validateVadSettings() {
  const values = Object.fromEntries(VAD_FIELDS.map((key) => [key, Number(form.elements.namedItem(`vad.${key}`).value)]));
  for (const key of VAD_FIELDS) form.elements.namedItem(`vad.${key}`).setCustomValidity("");
  let error = "", field = "threshold";
  const missingField = VAD_FIELDS.find((key) => !Number.isFinite(values[key]) || form.elements.namedItem(`vad.${key}`).value.trim() === "");
  if (missingField) {
    error = "発話検出の各項目に数値を入力してください。";
    field = missingField;
  } else if (!(0 < values.end_threshold && values.end_threshold < values.threshold && values.threshold < 1)) {
    error = "発話の判定値は、0 < 終了の値 < 開始の値 < 1 にしてください。";
    field = "end_threshold";
  } else if (!Number.isInteger(values.min_speech_ms) || values.min_speech_ms < 128 || values.min_speech_ms > 1000) {
    error = "声と判定する最短の長さは128〜1000msの整数にしてください。"; field = "min_speech_ms";
  } else if (!Number.isInteger(values.silence_ms) || values.silence_ms < 320 || values.silence_ms > 2000) {
    error = "話し終わりまで待つ無音時間は320〜2000msの整数にしてください。"; field = "silence_ms";
  } else if (!Number.isInteger(values.preroll_ms) || values.preroll_ms < values.min_speech_ms + 128 || values.preroll_ms > 2000) {
    error = "発話の先頭を保つ時間は、最短の発話より128ms以上長い整数（最大2000ms）にしてください。"; field = "preroll_ms";
  } else if (values.max_speech_seconds < 1 || values.max_speech_seconds > 30) {
    error = "1回の発話の上限は1〜30秒にしてください。"; field = "max_speech_seconds";
  }
  if (error) {
    form.elements.namedItem(`vad.${field}`).setCustomValidity(error);
    if (["threshold", "end_threshold", "preroll_ms"].includes(field)) document.getElementById("vad-advanced").open = true;
  }
  vadValidation.textContent = error;
  return !error;
}

function runtimeConfiguration(config = {}) {
  if (!config || typeof config !== "object" || Array.isArray(config)) config = {};
  const parts = [config.model || "モデル未確認"];
  parts.push(config.device ? config.device === "cuda" ? "CUDA GPU" : config.device === "cpu" ? "CPU" : String(config.device) : "実行デバイス未確認");
  if (config.precision) parts.push(String(config.precision));
  if (config.backend) parts.push(config.backend === "local" ? "ローカルTTS" : config.backend === "gradio" ? "Gradio API" : String(config.backend));
  return parts.join(" · ");
}

function vadDescription(settings = {}) {
  if (!settings || typeof settings !== "object" || Array.isArray(settings)) settings = {};
  const preset = Object.entries(VAD_PRESETS).find(([, pair]) => pair[0] === settings.threshold && pair[1] === settings.end_threshold)?.[0];
  const sensitivity = { low: "感度低め", normal: "感度標準", high: "感度高め" }[preset] || "感度カスタム";
  return `${sensitivity} · 無音待ち ${settings.silence_ms ?? "未確認"}ms · 最短 ${settings.min_speech_ms ?? "未確認"}ms · 上限 ${settings.max_speech_seconds ?? "未確認"}秒`;
}

function validateRuntimeSnapshot(snapshot) {
  const object = (value) => value && typeof value === "object" && !Array.isArray(value);
  if (!object(snapshot) || typeof snapshot.checked_at !== "string" || Number.isNaN(new Date(snapshot.checked_at).getTime()) || !object(snapshot.components)) {
    throw new Error("稼働状態の応答形式が正しくありません。");
  }
  for (const name of ["stt", "llm", "tts"]) {
    const component = snapshot.components[name];
    if (!object(component) || !object(component.configured) || typeof component.state !== "string" || !Array.isArray(component.active)
      || component.active.some((item) => !object(item))) {
      throw new Error("稼働状態の応答形式が正しくありません。");
    }
  }
}

function showRuntimeStatus(snapshot) {
  validateRuntimeSnapshot(snapshot);
  for (const name of ["stt", "llm", "tts"]) {
    const component = snapshot.components?.[name] || {};
    const badge = document.getElementById(`runtime-${name}-state`);
    const state = Object.hasOwn(RUNTIME_STATES, component.state) ? component.state : "unverified";
    badge.textContent = RUNTIME_STATES[state];
    badge.dataset.state = state;
    document.getElementById(`runtime-${name}-configured`).textContent = `次の会話の設定: ${runtimeConfiguration(component.configured)}`;
    const active = Array.isArray(component.active) ? component.active : [];
    document.getElementById(`runtime-${name}-active`).textContent = active.length
      ? active.map((item, index) => {
        const observed = `稼働中 ${index + 1}: ${runtimeConfiguration(item)} · ${RUNTIME_STATES[item.state] || "未確認"}`;
        const successAt = new Date(item.last_success_at);
        const success = item.last_success_at && !Number.isNaN(successAt.getTime()) ? `最終処理成功: ${successAt.toLocaleString("ja-JP")}` : "処理成功の観測はまだありません。";
        return [observed, item.configured ? `この会話で選択中: ${runtimeConfiguration(item.configured)}` : "", typeof item.detail === "string" ? item.detail : "", success, item.settings_pending ? "保存後の設定は次の会話で使われます。" : ""].filter(Boolean).join("\n");
      }).join("\n\n")
      : "稼働中の会話の観測値はありません。";
    document.getElementById(`runtime-${name}-detail`).textContent = component.detail || "状態は実際の処理で観測した情報です。";
  }
  const activeVad = Array.isArray(snapshot.vad?.active) ? snapshot.vad.active : [];
  document.getElementById("runtime-vad").textContent = [
    `発話検出・次の会話: ${vadDescription(snapshot.vad?.configured)}`,
    ...activeVad.map((item, index) => `発話検出・稼働中 ${index + 1}: ${vadDescription(item.settings)}`),
  ].join("\n");
  const count = Number.isInteger(snapshot.active_sessions) ? snapshot.active_sessions : "未確認";
  runtimeSummary.textContent = `稼働中の会話: ${count}件。準備状態は観測結果に基づきます。`;
  runtimeSummary.classList.remove("error");
  const checkedAt = new Date(snapshot.checked_at);
  runtimeUpdated.textContent = `確認時刻: ${Number.isNaN(checkedAt.getTime()) ? "不明" : checkedAt.toLocaleString("ja-JP")} · 表示中は約10秒ごとに更新`;
  hasRuntimeSnapshot = true;
}

function cancelRuntimeRequest() {
  runtimeEpoch += 1;
  clearTimeout(runtimeTimer);
  runtimeTimer = null;
  runtimeRequest?.controller.abort();
}

async function loadRuntimeStatus() {
  if (!runtimeSummary || !runtimePageActive || document.visibilityState === "hidden" || runtimeRequest) return;
  clearTimeout(runtimeTimer);
  runtimeTimer = null;
  const controller = new AbortController();
  const request = { controller, epoch: runtimeEpoch };
  runtimeRequest = request;
  runtimeRefresh.disabled = true;
  let expired = false;
  const deadline = setTimeout(() => { expired = true; controller.abort(); }, 5000);
  try {
    const snapshot = await requestJson(API_RUNTIME, { signal: controller.signal });
    if (request.epoch !== runtimeEpoch || !runtimePageActive || document.visibilityState === "hidden") return;
    showRuntimeStatus(snapshot);
  } catch (error) {
    if (request.epoch !== runtimeEpoch || !runtimePageActive || document.visibilityState === "hidden") return;
    runtimeSummary.classList.add("error");
    const reason = error.status === 401 ? "管理トークンを確認してください。" : expired ? "状態取得がタイムアウトしました。" : "最新の状態を取得できませんでした。";
    runtimeSummary.textContent = reason + (hasRuntimeSnapshot ? " 前回取得した状態を表示しています。" : " モデルの準備状態は未確認です。");
  } finally {
    clearTimeout(deadline);
    if (runtimeRequest === request) runtimeRequest = null;
    runtimeRefresh.disabled = false;
    if (runtimePageActive && document.visibilityState !== "hidden") runtimeTimer = setTimeout(loadRuntimeStatus, request.epoch === runtimeEpoch ? 10000 : 0);
  }
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
  if (loading) {
    reloadAfterLoad = true;
    return;
  }
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
    if (reloadAfterLoad) {
      reloadAfterLoad = false;
      void loadSettings();
    }
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
  const vadValid = validateVadSettings();
  if (!form.reportValidity() || !vadValid) {
    if (!vadValid) setMessage(vadValidation.textContent, "error");
    return;
  }
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
    cancelRuntimeRequest();
    void loadRuntimeStatus();
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
  cancelRuntimeRequest();
  if (runtimeSummary) runtimeSummary.textContent = "管理トークン変更後の状態は未確認です。入力を確定すると更新します。";
});
tokenInput.addEventListener("change", () => { void loadSettings(); void loadRuntimeStatus(); });
vadSensitivity.addEventListener("change", () => {
  const preset = VAD_PRESETS[vadSensitivity.value];
  if (preset) {
    assignField("vad.threshold", preset[0]);
    assignField("vad.end_threshold", preset[1]);
    validateVadSettings();
    setMessage("未保存の変更があります。");
  }
});
for (const key of VAD_FIELDS) {
  form.elements.namedItem(`vad.${key}`).addEventListener("input", () => { updateVadPreset(); validateVadSettings(); });
}
runtimeRefresh.addEventListener("click", () => void loadRuntimeStatus());
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "hidden") cancelRuntimeRequest();
  else void loadRuntimeStatus();
});
window.addEventListener("pagehide", () => { runtimePageActive = false; cancelRuntimeRequest(); });
window.addEventListener("pageshow", () => { runtimePageActive = true; void loadRuntimeStatus(); });
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
loadRuntimeStatus();
