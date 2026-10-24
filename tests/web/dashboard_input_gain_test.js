"use strict";
const { browser, settle, assert } = require("./dashboard_browser_harness");

const KEY = "nen-gitsune-voice-input-gain-percent";
const change = (app, percent) => {
  const slider = app.element("voice-input-gain");
  slider.value = String(percent); slider.dispatch("input");
};
const gainHeaders = (app) => app.frames().map((frame) => frame.options.headers["X-Audio-Input-Gain"]);

(async () => {
  const app = browser(); await app.begin();
  const stream = app.streams[0], worklet = app.worklets[0];
  app.element("voice-audio").volume = 0.37;
  assert.equal(app.element("voice-input-gain-value").textContent, "100%");
  assert.equal(app.element("voice-input-gain-normal").getAttribute("aria-pressed"), "true");
  app.emit(); await settle(); assert.equal(gainHeaders(app)[0], "1");
  app.element("voice-input-gain-noisy").click();
  assert.equal(app.element("voice-input-gain-value").textContent, "60%");
  assert.equal(app.element("voice-input-gain").getAttribute("aria-valuetext"), "60%");
  assert.equal(app.element("voice-input-gain-noisy").getAttribute("aria-pressed"), "true");
  app.emit(2000, 0.02); await settle();
  assert.equal(gainHeaders(app).at(-1), "0.6");
  assert.equal(new DataView(app.frames().at(-1).options.body).getInt16(0, true), 2000, "Gain must be applied by the server, not by browser PCM mutation");
  assert.ok(Math.abs(app.element("voice-input-level").value - 0.096) < 1e-9);
  app.element("voice-input-gain-quiet").click(); app.emit(); await settle();
  assert.equal(gainHeaders(app).at(-1), "1.5"); assert.equal(app.storage.get(KEY), "150");
  app.element("voice-input-gain-normal").click(); app.emit(); await settle(); assert.equal(gainHeaders(app).at(-1), "1");
  change(app, 25); app.emit(); await settle(); change(app, 200); app.emit(); await settle();
  assert.deepEqual(gainHeaders(app).slice(-2), ["0.25", "2"]);
  assert.equal(app.stats.microphoneRequests, 1); assert.equal(app.streams[0], stream); assert.equal(stream.stopped, false);
  assert.equal(app.worklets[0], worklet); assert.equal(app.eventConnections.length, 1);
  assert.equal(app.calls.some(({ options }) => options.method === "DELETE"), false, "Changing input gain must not end the conversation");
  assert.equal(app.element("voice-audio").volume, 0.37, "Input gain controls must never change playback volume");

  const queued = browser({ deferPCM: true }); await queued.begin();
  change(queued, 60); queued.emit(1);
  change(queued, 100); queued.emit(2);
  change(queued, 150); queued.emit(3);
  change(queued, 25);
  queued.pendingPCM[0].resolve(); await settle(); queued.pendingPCM[1].resolve(); await settle(); queued.pendingPCM[2].resolve(); await settle();
  assert.deepEqual(gainHeaders(queued), ["0.6", "1", "1.5"], "Queued frames must retain their capture-time gain");
  queued.emit(4); await settle(); assert.equal(gainHeaders(queued).at(-1), "0.25");

  const retry = browser({ loseFirstACK: true }); await retry.begin();
  change(retry, 60); retry.emit(1234); await settle();
  change(retry, 150); retry.emit(4321); await retry.advance(250);
  assert.deepEqual(retry.frames().map((frame) => frame.options.headers["X-Audio-Sequence"]), ["0", "0", "1"]);
  assert.deepEqual(gainHeaders(retry), ["0.6", "0.6", "1.5"], "Retrying a sequence must retain the same gain despite a live slider change");
  assert.deepEqual(Buffer.from(retry.frames()[0].options.body), Buffer.from(retry.frames()[1].options.body));

  const restored = browser({ storage: new Map([[KEY, "150"]]) }); await restored.begin(); restored.emit(); await settle();
  assert.equal(restored.element("voice-input-gain-value").textContent, "150%"); assert.equal(gainHeaders(restored)[0], "1.5");
  for (const invalid of ["NaN", "Infinity", "", "0", "201", "62"]) {
    const fallback = browser({ storage: new Map([[KEY, invalid]]) }); await fallback.begin(); fallback.emit(); await settle();
    assert.equal(gainHeaders(fallback)[0], "1", "Invalid saved values must use the standard gain");
  }
  console.log("Input gain checked: live presets/range, server-only DSP header, storage, uninterrupted capture, queued/retry identity and unchanged output volume");
})().catch((error) => { console.error(error); process.exitCode = 1; });
