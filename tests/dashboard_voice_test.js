"use strict";
const { browser, settle, assert } = require("./dashboard_browser_harness");

(async () => {
  const app = browser(); await app.begin();
  app.server().stage = "speaking"; app.server().transcript = "話している途中"; await app.advance(350);
  assert.equal(app.element("voice-transcript").textContent, "話している途中");
  for (const [stage, text] of [["stt", "音声を文字"], ["llm", "返事を考え"], ["tts", "返答音声を作って"]]) {
    app.server().stage = stage; await app.advance(350); assert.ok(app.status().includes(text));
    app.emit(30000); app.emit(0); await settle(); assert.equal(app.frames().length, 0, "Only server stage may suspend input enqueue");
  }
  app.reply(); await app.advance(350);
  assert.equal(app.stats.plays, 1); assert.equal(app.element("voice-transcript").textContent, "認識した発話");
  assert.deepEqual(app.server().playback.map((event) => event.event), ["started"]);
  for (let count = 0; count < 10; count++) app.emit(0);
  await app.advance(1_000); assert.equal(app.frames().length, 0); assert.equal(app.streams[0].stopped, false, "Busy transport must not need to reopen the microphone");
  app.element("voice-audio").finish(); await settle();
  assert.deepEqual(app.server().playback.map((event) => event.event), ["started", "ended"]);
  assert.equal(app.element("voice-audio").hidden, true); assert.equal(app.element("voice-audio").src, "");
  await app.advance(350); app.emit(); await settle(); assert.equal(app.frames().length, 0, "Server cooldown must still block enqueue");
  await app.advance(350); app.emit(); await settle(); assert.equal(app.frames().length, 1);
  await app.element("voice-audio").play(); await settle();
  assert.equal(app.element("voice-audio").paused, true, "Old reply audio must not replay while the server is listening");
  assert.deepEqual(app.server().playback.map((event) => event.event), ["started", "ended"]);

  const autoplay = browser({ autoplayReject: true }); await autoplay.begin(); autoplay.reply(); await autoplay.advance(350);
  assert.ok(autoplay.status().includes("再生ボタン")); assert.equal(autoplay.server().playback.length, 0);
  await autoplay.advance(1_000); assert.equal(autoplay.stats.plays, 1, "The same reply_ready must not play more than once");
  autoplay.emit(); await settle(); assert.equal(autoplay.frames().length, 0);
  autoplay.environment.allowPlayback = true; await autoplay.element("voice-audio").play(); await settle();
  assert.deepEqual(autoplay.server().playback.map((event) => event.event), ["started"]);
  const oldEnded = autoplay.element("voice-audio").onended;
  const oldError = autoplay.element("voice-audio").onerror;
  autoplay.stop(); await settle(); oldEnded(); await autoplay.advance(1_000);
  assert.equal(autoplay.server().playback.length, 1, "An ended event after stop must not notify or resume");
  await autoplay.begin(); const nextStatus = autoplay.status(); oldError();
  assert.equal(autoplay.status(), nextStatus, "An old playback error must not overwrite a newer session");

  const failedAudio = browser({ failAudio: true }); await failedAudio.begin(); failedAudio.reply(); await failedAudio.advance(350);
  assert.deepEqual(failedAudio.server().playback.map((event) => event.event), ["failed"]);
  await failedAudio.advance(700); assert.ok(failedAudio.status().includes("再生できません"));

  const oldGET = browser({ deferGET: true, ignoreGETAbort: true }); await oldGET.begin(); oldGET.stop(); await oldGET.begin();
  oldGET.pendingGET[0].resolve({ session_id: "session-1", generation: 0, stage: "speaking", transcript: "前の会話" }); await settle();
  assert.notEqual(oldGET.element("voice-transcript").textContent, "前の会話");
  console.log("Server output checked: transcript/stages, busy gating, unique playback, started/ended/failed events, cooldown and stale replies");
})().catch((error) => { console.error(error); process.exitCode = 1; });
