const defaults = { sound: 'みみもとささやき', bedtime: '22:30', duration: 30, wakeTime: '07:00', sessionStart: null, sessions: [] };
const STORAGE_KEY = 'nengitsune-ui';
const LEGACY_STORAGE_KEY = 'nemuri-ui';

function loadState() {
  try {
    const stored = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? localStorage.getItem(LEGACY_STORAGE_KEY) ?? '{}');
    const sessions = Array.isArray(stored.sessions)
      ? stored.sessions.filter((item) => Number.isFinite(item?.start) && Number.isFinite(item?.end) && item.end >= item.start)
      : [];
    return {
      ...defaults,
      ...stored,
      bedtime: /^([01]\d|2[0-3]):[0-5]\d$/.test(stored.bedtime) ? stored.bedtime : defaults.bedtime,
      duration: [15, 30, 45, 60, 75, 90].includes(stored.duration) ? stored.duration : defaults.duration,
      wakeTime: /^([01]\d|2[0-3]):[0-5]\d$/.test(stored.wakeTime) ? stored.wakeTime : defaults.wakeTime,
      sessionStart: Number.isFinite(stored.sessionStart) ? stored.sessionStart : null,
      sessions,
    };
  } catch {
    return { ...defaults };
  }
}

const state = loadState();
const screens = [...document.querySelectorAll('[data-screen]')];
const navigation = [...document.querySelectorAll('.nav-item')];
const soundOptions = [...document.querySelectorAll('[data-sound]')];
const toast = document.querySelector('.toast');
let toastTimer;
const loadingScreen = document.querySelector('#loading-screen');
const themeColorMeta = document.querySelector('#theme-color-meta');
const installBanner = document.querySelector('#install-banner');
const installAction = document.querySelector('#install-action');
const installGuide = document.querySelector('#install-guide');
const isIOS = /iPhone|iPad|iPod/.test(navigator.userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
const isAndroid = /Android/i.test(navigator.userAgent) || navigator.userAgentData?.platform === 'Android';
const isIOSSafari = isIOS && /Safari/.test(navigator.userAgent) && !/CriOS|FxiOS|EdgiOS|OPiOS/.test(navigator.userAgent);
const isMobileBrowser = isIOS || isAndroid || (matchMedia('(pointer: coarse)').matches && matchMedia('(max-width: 600px)').matches);
let deferredInstallPrompt = null;
let installDismissed = false;
let installedRelatedApp = false;
let installCheckPending = isAndroid && typeof navigator.getInstalledRelatedApps === 'function';

function isInstalledView() {
  return window.matchMedia('(display-mode: standalone)').matches || navigator.standalone === true;
}

function updateInstallBanner() {
  const show = isMobileBrowser && !installDismissed && !isInstalledView();
  installBanner.hidden = !show;
  if (!show) return;
  document.querySelector('#install-message').textContent = isIOS
    ? isIOSSafari ? 'Safariの共有メニューから追加できます。' : 'Safariで開くとホーム画面に追加できます。'
    : deferredInstallPrompt ? 'アプリのように、いつでもすぐ開けます。' : 'ブラウザのメニューから追加できます。';
  if (deferredInstallPrompt) installGuide.hidden = true;
  installAction.textContent = deferredInstallPrompt ? 'Chromeでインストール' : installGuide.hidden ? '追加方法を見る' : '手順を閉じる';
  if (installedRelatedApp && !deferredInstallPrompt) {
    document.querySelector('#install-message').textContent = 'Chromeでは追加済みと認識されています。見当たらない場合は追加方法を確認してください。';
  }
}

function dismissInstallBanner() {
  installDismissed = true;
  installBanner.hidden = true;
}

async function checkAndroidInstallation() {
  updateInstallBanner();
  if (!installCheckPending) return;
  const fallback = setTimeout(() => {
    installCheckPending = false;
    updateInstallBanner();
  }, 1200);
  try {
    const apps = await navigator.getInstalledRelatedApps();
    installedRelatedApp = apps.some((app) => app.platform === 'webapp' &&
      (app.id === `${location.origin}/` || app.url === `${location.origin}/manifest.json`));
  } catch { /* The prompt can still show when detection is unavailable. */ }
  clearTimeout(fallback);
  installCheckPending = false;
  updateInstallBanner();
}

window.addEventListener('beforeinstallprompt', (event) => {
  if (!isMobileBrowser) return;
  event.preventDefault();
  deferredInstallPrompt = event;
  updateInstallBanner();
});
window.addEventListener('appinstalled', () => {
  deferredInstallPrompt = null;
  installedRelatedApp = true;
  dismissInstallBanner();
});
window.matchMedia('(display-mode: standalone)').addEventListener?.('change', updateInstallBanner);
document.querySelector('#install-close').addEventListener('click', dismissInstallBanner);
installAction.addEventListener('click', async () => {
  if (deferredInstallPrompt) {
    const prompt = deferredInstallPrompt;
    deferredInstallPrompt = null;
    try {
      await prompt.prompt();
      await prompt.userChoice;
      dismissInstallBanner();
    } catch {
      installGuide.hidden = false;
      installGuide.textContent = 'ブラウザのメニュー（︙）から「アプリをインストール」または「ホーム画面に追加」を選んでください。';
      updateInstallBanner();
    }
    return;
  }
  installGuide.hidden = !installGuide.hidden;
  installGuide.textContent = isIOS
    ? `${isIOSSafari ? '' : 'Safariでこのページを開き、'}共有（四角に上矢印）→「ホーム画面に追加」を選んでください。`
    : 'ブラウザのメニュー（︙）から「アプリをインストール」または「ホーム画面に追加」を選んでください。';
  updateInstallBanner();
});
window.addEventListener('load', () => {
  if ('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js').catch(() => {});
});

function saveState() {
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify(state)); } catch { /* Storage is optional. */ }
}

function showToast(message) {
  if (!toast) return;
  toast.textContent = message;
  toast.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { toast.hidden = true; }, 3200);
}

function goTo(screen, updateAddress = true) {
  const target = screen === 'diary-calendar' ? 'diary' : screen;
  if (!screens.some((element) => element.dataset.screen === target)) return;
  screens.forEach((element) => { element.hidden = element.dataset.screen !== target; });
  navigation.forEach((button) => {
    const active = button.dataset.go === target;
    button.classList.toggle('active', active);
    if (active) button.setAttribute('aria-current', 'page');
    else button.removeAttribute('aria-current');
  });
  if (updateAddress) {
    const address = screen === 'home' ? location.pathname + location.search : `#${screen}`;
    history.pushState(null, '', address);
  }
  if (target === 'diary') setDiaryView(screen === 'diary-calendar' ? 'month' : 'day');
  updateInstallBanner();
  window.scrollTo({ top: 0, behavior: 'auto' });
}

document.querySelectorAll('[data-go]').forEach((button) => {
  button.addEventListener('click', () => goTo(button.dataset.go));
});
window.addEventListener('popstate', () => goTo(location.hash.slice(1) || 'home', false));
document.querySelector('#notification-button')?.addEventListener('click', () => {
  showToast('お知らせはまだありません。今夜もゆっくり休んでね。');
});

function updateSound() {
  document.querySelector('#home-sound').textContent = state.sound;
  document.querySelector('#routine-sound').textContent = state.sound;
  soundOptions.forEach((option) => {
    const selected = option.dataset.sound === state.sound;
    option.classList.toggle('selected', selected);
    option.setAttribute('aria-pressed', String(selected));
  });
}

soundOptions.forEach((option) => {
  option.addEventListener('click', () => {
    state.sound = option.dataset.sound;
    saveState();
    updateSound();
    showToast(`今夜のサウンドを「${state.sound}」にしました。`);
  });
});

const bedtime = document.querySelector('#bedtime');
const duration = document.querySelector('#duration');
const wakeTime = document.querySelector('#wake-time');
bedtime.value = state.bedtime;
duration.value = String(state.duration);
wakeTime.value = state.wakeTime;
const bedtimeDisplay = document.querySelector('#bedtime-display');
const wakeTimeDisplay = document.querySelector('#wake-time-display');
function syncTimeDisplays() {
  if (bedtimeDisplay) bedtimeDisplay.textContent = bedtime.value || state.bedtime;
  if (wakeTimeDisplay) wakeTimeDisplay.textContent = wakeTime.value || state.wakeTime;
}
syncTimeDisplays();
[bedtime, wakeTime].forEach((input) => {
  input.addEventListener('input', () => {
    if (input === bedtime && bedtime.value) state.bedtime = bedtime.value;
    if (input === wakeTime && wakeTime.value) state.wakeTime = wakeTime.value;
    syncTimeDisplays();
    saveState();
  });
  input.addEventListener('click', () => { try { input.showPicker?.(); } catch {} });
});
bedtime.addEventListener('change', () => {
  state.bedtime = bedtime.value;
  syncTimeDisplays();
  saveState();
});
wakeTime.addEventListener('change', () => {
  state.wakeTime = wakeTime.value;
  syncTimeDisplays();
  saveState();
});

function updateDuration() {
  document.querySelector('#duration-value').textContent = duration.value;
  const percent = ((Number(duration.value) - 15) / 75) * 100;
  duration.style.background = `linear-gradient(90deg,#f0c259 0%,#f0c259 ${percent}%,#253554 ${percent}%,#253554 100%)`;
  document.querySelectorAll('[data-duration-step]').forEach((btn) => {
    const active = Number(btn.dataset.durationStep) === Number(duration.value);
    btn.classList.toggle('active', active);
    btn.setAttribute('aria-pressed', String(active));
  });
}

duration.addEventListener('input', () => {
  state.duration = Number(duration.value);
  saveState();
  updateDuration();
document.querySelectorAll('[data-duration-step]').forEach((btn) => {
  if (!btn.dataset.bound) {
    btn.dataset.bound = '1';
    btn.addEventListener('click', () => {
      duration.value = btn.dataset.durationStep;
      state.duration = Number(duration.value);
      saveState();
      updateDuration();
    });
  }
});
  document.querySelectorAll('[data-duration-step]').forEach((btn) => {
    if (!btn.dataset.bound) {
      btn.dataset.bound = '1';
      btn.addEventListener('click', () => {
        duration.value = btn.dataset.durationStep;
        state.duration = Number(duration.value);
        saveState();
        updateDuration();
document.querySelectorAll('[data-duration-step]').forEach((btn) => {
  if (!btn.dataset.bound) {
    btn.dataset.bound = '1';
    btn.addEventListener('click', () => {
      duration.value = btn.dataset.durationStep;
      state.duration = Number(duration.value);
      saveState();
      updateDuration();
    });
  }
});
      });
    }
  });
});

function dateKey(date) {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
}

function dateFromKey(key) {
  const [year, month, day] = key.split('-').map(Number);
  return new Date(year, month - 1, day, 12);
}

function offsetKey(days) {
  const date = new Date();
  date.setHours(12, 0, 0, 0);
  date.setDate(date.getDate() + days);
  return dateKey(date);
}

function clockTime(timestamp) {
  return new Intl.DateTimeFormat('ja-JP', { hour: '2-digit', minute: '2-digit', hour12: false }).format(new Date(timestamp));
}

function durationText(milliseconds) {
  const minutes = Math.floor(milliseconds / 60000);
  if (minutes < 1) return '1分未満';
  const hours = Math.floor(minutes / 60);
  return hours ? `${hours}時間${minutes % 60}分` : `${minutes}分`;
}

// These stories illustrate the planned diary UI. They are never presented as sensor measurements.
const samplePatterns = [
  {
    headline: 'すやすや眠れた夜', metrics: ['7時間42分', '2回', '6時間18分'],
    events: [
      { time: '22:26', kind: 'start', mark: '☾', title: 'ねんぎつねとおやすみを開始', detail: 'やさしい声と一緒に、夜の時間へ。' },
      { time: '23:04', kind: 'calm', mark: '✦', title: '眠りについたようす', detail: '動きが少なく、静かな時間が続きました。' },
      { time: '01:18', kind: 'turn', mark: '↝', title: '寝返りをした', detail: '姿勢を変えて、またゆっくり。' },
      { time: '03:42', kind: 'calm', mark: '✦', title: 'すやすや、おだやかに', detail: '朝まで落ち着いて過ごしました。' },
      { time: '06:48', kind: 'wake', mark: '☀', title: 'おはよう', detail: 'ねんぎつねと一緒に、朝を迎えました。' },
    ],
  },
  {
    headline: 'ゆっくり休めた夜', metrics: ['6時間58分', '3回', '5時間35分'],
    events: [
      { time: '23:12', kind: 'start', mark: '☾', title: 'ねんぎつねとおやすみを開始', detail: 'ふわふわのまくらで、ひとやすみ。' },
      { time: '23:48', kind: 'calm', mark: '✦', title: '静かな時間に', detail: '動きが少なくなりました。' },
      { time: '02:07', kind: 'turn', mark: '↝', title: '寝返りをした', detail: '少し姿勢を変えたようです。' },
      { time: '06:10', kind: 'wake', mark: '☀', title: 'おはよう', detail: '朝まで一緒に過ごしました。' },
    ],
  },
  {
    headline: '早めにおやすみした夜', metrics: ['8時間12分', '1回', '7時間02分'],
    events: [
      { time: '21:54', kind: 'start', mark: '☾', title: 'ねんぎつねとおやすみを開始', detail: 'いつもより少し早い夜。' },
      { time: '22:21', kind: 'calm', mark: '✦', title: '眠りについたようす', detail: 'ゆったりとした時間が始まりました。' },
      { time: '03:16', kind: 'turn', mark: '↝', title: '寝返りをした', detail: '一度姿勢を変えました。' },
      { time: '06:06', kind: 'wake', mark: '☀', title: 'おはよう', detail: '心地よい朝を迎えました。' },
    ],
  },
];
// Illustrative values only. A timer session does not measure sleep quality.
const sampleQualities = [
  [['22:30', -2], ['23:00', 1], ['23:30', 3], ['00:00', 4], ['01:00', -2], ['02:00', 3], ['03:00', 4], ['04:00', 3], ['05:00', 4], ['06:00', 2], ['06:30', -1]],
  [['23:15', -3], ['23:30', -2], ['00:00', 2], ['01:00', 3], ['02:00', -3], ['03:00', 2], ['04:00', 4], ['05:00', 3], ['06:00', -1]],
  [['22:00', -2], ['22:30', 2], ['23:00', 3], ['00:00', 4], ['01:00', 4], ['02:00', 3], ['03:00', -2], ['04:00', 4], ['05:00', 3], ['06:00', 2]],
];
const sampleRecords = new Map([-1, -2, -4, -6, -8, -10, -13].map((days, index) => {
  const pattern = index % samplePatterns.length;
  return [offsetKey(days), { ...samplePatterns[pattern], quality: sampleQualities[pattern] }];
}));
const latestSession = state.sessions.reduce((latest, session) => Math.max(latest, session.start), 0);
let selectedDate = latestSession ? dateKey(new Date(latestSession)) : offsetKey(-1);
let calendarMonth = dateFromKey(selectedDate);

function setDiaryView(view, updateAddress = false) {
  const day = view === 'day';
  document.querySelector('#diary-panel-day').hidden = !day;
  document.querySelector('#diary-panel-calendar').hidden = day;
  for (const [name, button] of [['day', document.querySelector('#diary-view-day')], ['month', document.querySelector('#diary-view-month')]]) {
    const active = name === view;
    button.classList.toggle('active', active);
    button.setAttribute('aria-selected', String(active));
    button.tabIndex = active ? 0 : -1;
  }
  if (updateAddress) history.replaceState(null, '', `#${day ? 'diary' : 'diary-calendar'}`);
}

document.querySelector('#diary-view-day').addEventListener('click', () => setDiaryView('day', true));
document.querySelector('#diary-view-month').addEventListener('click', () => setDiaryView('month', true));
document.querySelector('.diary-view-tabs').addEventListener('keydown', (event) => {
  if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
  event.preventDefault();
  const view = event.key === 'ArrowLeft' ? 'day' : 'month';
  setDiaryView(view, true);
  document.querySelector(`#diary-view-${view}`).focus();
});

function renderCalendar() {
  const year = calendarMonth.getFullYear();
  const month = calendarMonth.getMonth();
  document.querySelector('#calendar-month').textContent = `${year}年${month + 1}月`;
  const grid = document.querySelector('#calendar-grid');
  grid.replaceChildren();

  const leading = new Date(year, month, 1).getDay();
  for (let index = 0; index < leading; index += 1) {
    const blank = document.createElement('span');
    blank.className = 'calendar-blank';
    grid.append(blank);
  }

  const days = new Date(year, month + 1, 0).getDate();
  const today = offsetKey(0);
  for (let day = 1; day <= days; day += 1) {
    const key = dateKey(new Date(year, month, day, 12));
    const hasActual = state.sessions.some((session) => dateKey(new Date(session.start)) === key);
    const hasSample = sampleRecords.has(key);
    const button = document.createElement('button');
    button.type = 'button';
    button.classList.toggle('has-entry', hasActual || hasSample);
    button.classList.toggle('selected', key === selectedDate);
    button.disabled = key > today;
    button.setAttribute('aria-label', `${year}年${month + 1}月${day}日${hasActual || hasSample ? '、記録あり' : ''}`);
    button.setAttribute('aria-pressed', String(key === selectedDate));
    const number = document.createElement('span');
    number.textContent = String(day);
    const marker = document.createElement('span');
    marker.className = 'calendar-dots';
    marker.textContent = hasActual ? '●' : hasSample ? '✦' : '';
    button.append(number, marker);
    button.addEventListener('click', () => selectDate(key));
    grid.append(button);
  }

  const nextMonth = new Date(year, month + 1, 1);
  document.querySelector('#calendar-next').disabled = nextMonth > new Date();
}

function appendEvent(container, event) {
  const row = document.createElement('div');
  row.className = `diary-event ${event.kind}`;
  const time = document.createElement('time');
  time.textContent = event.time;
  const mark = document.createElement('span');
  mark.className = 'diary-event-mark';
  mark.textContent = event.mark;
  mark.setAttribute('aria-hidden', 'true');
  const copy = document.createElement('div');
  const title = document.createElement('strong');
  title.textContent = event.title;
  const detail = document.createElement('small');
  detail.textContent = event.detail;
  copy.append(title, detail);
  row.append(time, mark, copy);
  container.append(row);
}

function renderQuality(sample) {
  const card = document.querySelector('.diary-quality-card');
  const rows = document.querySelector('#diary-quality-rows');
  const source = document.querySelector('#diary-quality-source');
  const note = document.querySelector('#diary-quality-note');
  rows.replaceChildren();
  card.classList.toggle('is-empty', !sample);
  source.textContent = sample ? 'サンプル表示' : '未計測';

  if (!sample) {
    const message = document.createElement('p');
    message.className = 'diary-quality-empty';
    message.textContent = '睡眠状態はまだ測定していません。';
    rows.append(message);
    note.textContent = '記録できるのは、おやすみの開始・終了時刻です。';
    return;
  }

  note.textContent = '左右の長さは画面イメージ用のサンプルです。実際の睡眠状態は測定していません。';
  sample.quality.forEach(([time, score]) => {
    const row = document.createElement('div');
    row.className = 'diary-quality-row';
    row.setAttribute('role', 'listitem');
    row.setAttribute('aria-label', `${time}、${score < 0 ? '眠りが浅い' : 'よく眠れた'}、サンプル`);
    const clock = document.createElement('time');
    clock.textContent = time;
    const track = document.createElement('div');
    track.className = 'diary-quality-track';
    track.setAttribute('aria-hidden', 'true');
    const bar = document.createElement('span');
    bar.className = `diary-quality-bar ${score < 0 ? 'shallow' : 'restful'}`;
    bar.style.width = `${Math.abs(score) * 10.5}%`;
    track.append(bar);
    row.append(clock, track);
    rows.append(row);
  });
}

function renderDiary() {
  renderCalendar();
  const date = dateFromKey(selectedDate);
  document.querySelector('#diary-date').textContent = `${date.getMonth() + 1}月${date.getDate()}日（${'日月火水木金土'[date.getDay()]}）`;
  document.querySelector('#diary-next-day').disabled = selectedDate >= offsetKey(0);

  const sessions = state.sessions.filter((session) => dateKey(new Date(session.start)) === selectedDate).sort((a, b) => a.start - b.start);
  const sample = sessions.length ? null : sampleRecords.get(selectedDate);
  renderQuality(sample);
  const card = document.querySelector('#diary-summary-card');
  const timeline = document.querySelector('#diary-timeline');
  const disclaimer = document.querySelector('#diary-disclaimer');
  timeline.replaceChildren();
  card.classList.toggle('empty', !sessions.length && !sample);
  document.querySelector('#home-diary-summary').textContent = state.sessions.length ? '最新の記録を見る' : '夜のようすを見る';

  let events = [];
  let metrics = ['—', '—', '—'];
  let metricLabels = ['いっしょの時間', '寝返り', 'おだやか時間'];
  let headline = 'この日の記録はまだありません';
  let source = '記録なし';

  if (sessions.length) {
    source = 'このブラウザの記録';
    headline = 'ねんぎつねと過ごした夜';
    metrics = [durationText(sessions.reduce((total, session) => total + session.end - session.start, 0)), `${sessions.length}回`, '未連携'];
    metricLabels = ['いっしょの時間', 'おやすみ回数', '動きの記録'];
    events = sessions.flatMap((session) => [
      { time: clockTime(session.start), kind: 'start', mark: '☾', title: 'ねんぎつねとおやすみを開始', detail: '画面内のタイマーを開始しました。' },
      { time: clockTime(session.end), kind: 'wake', mark: '☀', title: 'おやすみ時間を終了', detail: '一緒に過ごした時間を記録しました。' },
    ]);
    disclaimer.textContent = 'このブラウザで記録した開始・終了時刻です。寝返りなどの睡眠状態はまだ測定していません。';
  } else if (sample) {
    source = 'サンプル記録';
    headline = sample.headline;
    metrics = sample.metrics;
    events = sample.events;
    disclaimer.textContent = 'この日の出来事と数値は画面イメージ用のサンプルです。実際の睡眠状態は測定していません。';
  } else {
    disclaimer.textContent = '入眠準備からタイマーを使うと、開始・終了時刻をこのブラウザに記録できます。';
  }

  document.querySelector('#diary-source').textContent = source;
  document.querySelector('#diary-headline').textContent = headline;
  [1, 2, 3].forEach((number) => {
    document.querySelector(`#diary-metric-${number}`).textContent = metrics[number - 1];
    document.querySelector(`#diary-metric-label-${number}`).textContent = metricLabels[number - 1];
  });
  document.querySelector('#diary-event-count').textContent = events.length ? `${events.length}つのできごと` : '記録なし';

  if (events.length) {
    events.forEach((event) => appendEvent(timeline, event));
  } else {
    const empty = document.createElement('div');
    empty.className = 'diary-empty';
    const moon = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    const use = document.createElementNS('http://www.w3.org/2000/svg', 'use');
    use.setAttribute('href', '#i-moon');
    moon.append(use);
    const title = document.createElement('strong');
    title.textContent = 'この日の出来事はまだありません';
    const detail = document.createElement('small');
    detail.textContent = 'ねんぎつねと過ごした時間が、ここに並びます。';
    empty.append(moon, title, detail);
    timeline.append(empty);
  }
}

function selectDate(key) {
  selectedDate = key;
  calendarMonth = dateFromKey(key);
  renderDiary();
  setDiaryView('day', true);
}

function shiftDay(amount) {
  const date = dateFromKey(selectedDate);
  date.setDate(date.getDate() + amount);
  const key = dateKey(date);
  if (key > offsetKey(0)) return;
  selectDate(key);
}

document.querySelector('#diary-prev-day').addEventListener('click', () => shiftDay(-1));
document.querySelector('#diary-next-day').addEventListener('click', () => shiftDay(1));
document.querySelector('#calendar-prev').addEventListener('click', () => {
  calendarMonth = new Date(calendarMonth.getFullYear(), calendarMonth.getMonth() - 1, 1);
  renderCalendar();
});
document.querySelector('#calendar-next').addEventListener('click', () => {
  const next = new Date(calendarMonth.getFullYear(), calendarMonth.getMonth() + 1, 1);
  if (next <= new Date()) { calendarMonth = next; renderCalendar(); }
});

const sleepButton = document.querySelector('#sleep-button');

function finishSession(message, endedAt = Date.now()) {
  const start = state.sessionStart;
  if (!Number.isFinite(start) || start <= 0) return;
  state.sessions.unshift({ start, end: Math.max(start, endedAt) });
  state.sessions = state.sessions.slice(0, 100);
  state.sessionStart = null;
  selectedDate = dateKey(new Date(start));
  calendarMonth = dateFromKey(selectedDate);
  saveState();
  renderDiary();
  updateSession();
  showToast(message);
}

function updateSession() {
  const active = Number.isFinite(state.sessionStart) && state.sessionStart > 0;
  const buttonLabel = sleepButton.querySelector('span');
  const heroStatus = document.querySelector('#hero-status');
  const ctaTitle = document.querySelector('#cta-title');
  const ctaCaption = document.querySelector('#cta-caption');
  const sessionNote = document.querySelector('#session-note');

  if (!active) {
    sleepButton.classList.remove('active');
    buttonLabel.textContent = 'ねんぎつねとおやすみ';
    heroStatus.textContent = 'いっしょに休む準備ができたよ';
    ctaTitle.textContent = 'ねんぎつねとおやすみしよう';
    ctaCaption.textContent = 'やさしい時間を、ここから。';
    sessionNote.textContent = '時刻は目安です。会話・アラームは準備中。タイマーで時間を記録します。';
    return;
  }

  const elapsed = Math.max(0, Math.floor((Date.now() - state.sessionStart) / 1000));
  const remaining = Math.max(0, state.duration * 60 - elapsed);
  if (remaining === 0) {
    finishSession('おやすみ時間が終わりました。ゆっくり休んでね。', state.sessionStart + state.duration * 60000);
    return;
  }
  const minutes = Math.floor(remaining / 60);
  const seconds = String(remaining % 60).padStart(2, '0');
  sleepButton.classList.add('active');
  buttonLabel.textContent = 'おやすみ時間を終える';
  heroStatus.textContent = 'あなたのそばで、おやすみ中';
  ctaTitle.textContent = 'ねんぎつねとおやすみ中';
  ctaCaption.textContent = `あと ${minutes}:${seconds}、ゆっくりしよう。`;
  sessionNote.textContent = `残り ${minutes}:${seconds} · この画面で時間を記録中`;
}

sleepButton.addEventListener('click', () => {
  if (state.sessionStart) {
    finishSession('おやすみ時間を終了し、ダイアリーに記録しました。');
  } else {
    state.sessionStart = Date.now();
    saveState();
    updateSession();
    showToast('ねんぎつねと一緒に、ゆっくりおやすみ。');
    goTo('home');
  }
});

updateSound();
updateDuration();
document.querySelectorAll('[data-duration-step]').forEach((btn) => {
  if (!btn.dataset.bound) {
    btn.dataset.bound = '1';
    btn.addEventListener('click', () => {
      duration.value = btn.dataset.durationStep;
      state.duration = Number(duration.value);
      saveState();
      updateDuration();
    });
  }
});
renderDiary();
updateSession();
goTo(location.hash.slice(1) || 'home', false);
checkAndroidInstallation();
setInterval(updateSession, 1000);

function finishLoadingScreen() {
  if (!loadingScreen || loadingScreen.hidden) return;
  loadingScreen.classList.add('is-done');
  if (themeColorMeta) themeColorMeta.setAttribute('content', '#152036');
  setTimeout(() => { loadingScreen.hidden = true; }, 560);
}

if (loadingScreen) {
  loadingScreen.addEventListener('click', finishLoadingScreen);
  setTimeout(finishLoadingScreen, 900);
}

const tapLayer = document.querySelector('#tap-layer');
const reduceMotionQuery = window.matchMedia('(prefers-reduced-motion: reduce)');
if (tapLayer) {
  window.addEventListener('pointerdown', (event) => {
    if (reduceMotionQuery.matches || (loadingScreen && !loadingScreen.hidden)) return;
    if (tapLayer.childElementCount >= 5) tapLayer.firstElementChild?.remove();
    const burst = document.createElement('span');
    burst.className = 'tap-burst';
    burst.style.left = `${event.clientX}px`;
    burst.style.top = `${event.clientY}px`;
    const ring = document.createElement('span');
    ring.className = 'tap-ring';
    burst.append(ring);
    ['m1', 'm2', 'm3'].forEach((cls, idx) => {
      const mote = document.createElement('span');
      mote.className = `tap-mote ${cls}`;
      mote.textContent = idx === 0 ? '✦' : '·';
      burst.append(mote);
    });
    tapLayer.append(burst);
    setTimeout(() => burst.remove(), 900);
  }, { passive: true });
}
