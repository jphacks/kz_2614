"use strict";
const { browser, settle, assert } = require("./dashboard_browser_harness");

(async () => {
  const app = browser({ autoplayReject: true, replayLastEventOnReconnect: true });
  await app.begin();
  const audio = Buffer.alloc(3 * 1024 * 1024, 1).toString("base64");
  for (let index = 0; index < 3; index++) {
    app.emitEvent({ type: "audio_chunk", index, audio_base64: audio, media_type: "audio/wav" });
    await settle();
  }
  app.emitEvent({ type: "complete", transcript: "発話", response_text: "長い返答" });
  await settle();
  assert.equal(app.stats.plays, 1);
  await app.advance(1_000);
  assert.equal(app.eventConnections.length, 1, "A full playback queue must pause its event reader instead of silently dropping audio");
  app.emit(0); await settle(); assert.equal(app.frames().length, 1, "Playback backpressure must not stop microphone transport");
  app.environment.allowPlayback = true; await app.element("voice-audio").play(); await settle();
  app.element("voice-audio").finish(); await settle(); await app.advance(100);
  assert.equal(app.eventConnections.at(-1).after, 2, "Reconnect must resume before the event that did not fit in the queue");
  assert.equal(app.stats.plays, 3, "Manual playback plus the next queued chunk must play without repeating accepted chunks");
  assert.equal(app.server().playback.some((event) => event.event === "ended"), false);
  app.element("voice-audio").finish(); await settle();
  app.element("voice-audio").finish(); await settle();
  assert.equal(app.server().playback.filter((event) => event.event === "started").length, 1);
  assert.equal(app.server().playback.filter((event) => event.event === "ended").length, 1);
  console.log("Event backpressure checked: bounded audio queue, unaccepted cursor replay, preserved queue and uninterrupted PCM transport");
})().catch((error) => { console.error(error); process.exitCode = 1; });
