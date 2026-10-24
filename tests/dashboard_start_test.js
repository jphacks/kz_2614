"use strict";
const { browser, settle, assert } = require("./dashboard_browser_harness");

(async () => {
  const app = browser({ deferStarts: 1 });
  app.start(); assert.equal(app.stats.resumes, 1); assert.equal(app.element("voice-start-button").textContent, "準備中…");
  app.start(); await settle(); assert.equal(app.stats.starts, 1);
  app.pendingStarts[0].resolve(); await settle(); await app.advance(0);
  assert.equal(app.worklets.length, 1); assert.equal(app.worklets[0].name, "nen-gitsune-pcm");
  assert.deepEqual(app.stats.modules, ["/voice_pcm_worklet.js"]);
  assert.equal(app.contexts[0].nodes.at(-1).gain.value, 0, "The capture graph must never monitor the microphone through speakers");
  app.stop(); await settle(); assert.equal(app.idle(), true); assert.equal(app.streams[0].stopped, true); assert.equal(app.contexts[0].state, "closed");

  const failed = browser({ failFirstStart: true }); await failed.begin(); assert.equal(failed.idle(), true); await failed.begin(); assert.equal(failed.worklets.length, 1);
  const timeout = browser({ deferStarts: 1 }); timeout.start(); await settle(); await timeout.advance(15_000);
  assert.equal(timeout.idle(), true); assert.ok(timeout.status().includes("時間内")); await timeout.begin(); assert.equal(timeout.worklets.length, 1);

  const late = browser({ deferStarts: 1 }); late.start(); await settle(); late.stop(); await late.begin();
  late.pendingStarts[0].resolve(); await settle(); assert.equal(late.streams.at(-1).stopped, false);
  assert.ok(late.calls.some(({ url, options }) => options.method === "DELETE" && url.endsWith("session-1")));

  const permission = browser({ deferMicrophone: true }); await permission.begin(); await permission.advance(15_000);
  assert.equal(permission.idle(), true); await permission.begin();
  const oldStream = permission.pendingMicrophones[0].resolve(); await settle(); assert.equal(oldStream.stopped, true);
  assert.equal(permission.worklets.length, 0);
  permission.pendingMicrophones[1].resolve(); await settle(); await permission.advance(0); assert.equal(permission.worklets.length, 1);

  for (const feature of ["secure", "microphone", "context", "worklet"]) {
    const unsupported = browser();
    if (feature === "secure") unsupported.context.window.isSecureContext = false;
    if (feature === "microphone") unsupported.context.navigator.mediaDevices = undefined;
    if (feature === "context") unsupported.context.window.AudioContext = undefined;
    if (feature === "worklet") unsupported.context.window.AudioWorkletNode = undefined;
    unsupported.start(); await settle(); assert.equal(unsupported.stats.starts, 0); assert.ok(unsupported.status().includes(feature === "secure" ? "localhost" : "Chrome"));
  }
  const moduleFailure = browser({ failModule: true }); await moduleFailure.begin(); assert.equal(moduleFailure.idle(), true); assert.equal(moduleFailure.streams[0].stopped, true);
  const optional = browser({ optionalDom: false }); await optional.begin(); optional.emit(); await settle(); assert.equal(optional.frames().length, 1);

  const available = [ { kind: "audioinput", deviceId: "default", label: "Default" }, { kind: "audioinput", deviceId: "headset", label: "HyperX headset" } ];
  const devices = browser({ microphones: available }); await devices.begin();
  const picker = devices.element("voice-microphone-select"); assert.ok(picker.children.some((option) => option.textContent.includes("HyperX")));
  picker.value = "headset"; picker.dispatch("change"); await settle(); assert.equal(devices.idle(), true); await devices.begin();
  const constraints = devices.microphoneConstraints.at(-1).audio;
  assert.equal(constraints.deviceId.exact, "headset"); assert.equal(constraints.autoGainControl, false); assert.equal(constraints.channelCount, 1);
  available.pop(); devices.devicesChanged(); await settle(); assert.equal(devices.idle(), true); assert.equal(picker.value, "headset", "A disconnected chosen microphone must not silently fall back");

  for (const reject of [false, true]) {
    const stale = browser({ deferDeletes: true }); await stale.begin(); stale.stop(); await stale.begin(); const status = stale.status();
    if (reject) stale.pendingDeletes[0].reject(); else stale.pendingDeletes[0].resolve(); await settle(); assert.equal(stale.status(), status);
  }
  const bus = []; const first = browser({ broadcastBus: bus }); await first.begin(); const second = browser({ broadcastBus: bus }); await second.begin();
  assert.equal(first.idle(), true); assert.equal(first.streams[0].stopped, true); assert.ok(first.status().includes("別のタブ")); assert.equal(second.streams[0].stopped, false);
  const hidden = browser(); await hidden.begin(); hidden.visibility("hidden"); await settle(); assert.equal(hidden.idle(), true); assert.equal(hidden.streams[0].stopped, true);
  console.log("Start checked: PCM worklet graph, permission/timeout/races, microphone choice, no fallback, cancellation and cross-tab ownership");
})().catch((error) => { console.error(error); process.exitCode = 1; });
