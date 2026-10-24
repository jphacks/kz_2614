"use strict";
const { browser, settle, assert } = require("./dashboard_browser_harness");

(async () => {
  const app = browser(); await app.begin();
  app.emit(0, 0); app.emit(2000, 0.8); app.emit(0, 0); await settle();
  assert.equal(app.frames().length, 3, "Silence and louder frames must all be sent, without browser speech detection");
  assert.deepEqual(app.frames().map((frame) => frame.options.headers["X-Audio-Sequence"]), ["0", "1", "2"]);
  assert.equal(app.stats.maxPCMInFlight, 1);
  assert.equal(app.frames()[0].options.headers["Content-Type"], "audio/pcm;rate=16000;channels=1;format=s16le");
  assert.equal(app.frames()[0].options.headers["X-Voice-Generation"], "0");
  assert.equal(app.frames()[0].options.body.byteLength, 8192);
  assert.ok(new Uint8Array(app.frames()[0].options.body).every((value) => value === 0));
  assert.equal(app.calls.some(({ url }) => url.includes("/turns/") || url.endsWith("/transcription")), false, "The browser must not decide utterance boundaries or start turns");

  const retry = browser({ loseFirstACK: true }); await retry.begin(); retry.emit(1234); await settle(); await retry.advance(250);
  assert.deepEqual(retry.frames().map((frame) => frame.options.headers["X-Audio-Sequence"]), ["0", "0"]);
  assert.deepEqual(Buffer.from(retry.frames()[0].options.body), Buffer.from(retry.frames()[1].options.body));
  assert.equal(retry.server().received.length, 1, "Retrying the same sequence must not duplicate accepted audio");
  retry.emit(); await settle(); assert.equal(retry.frames().at(-1).options.headers["X-Audio-Sequence"], "1");

  const waiting = browser({ deferPCM: true }); await waiting.begin(); waiting.emit(1); waiting.emit(2); waiting.emit(3); await settle();
  assert.equal(waiting.frames().length, 1); waiting.pendingPCM[0].resolve(); await settle(); assert.equal(waiting.frames().length, 2);
  waiting.server().stage = "llm"; waiting.pendingPCM[1].resolve(); await settle();
  assert.equal(waiting.frames().length, 2, "The server busy state must discard unsent capture queue, without assigning sequence numbers");
  waiting.emit(3000); waiting.emit(0); await settle(); assert.equal(waiting.frames().length, 2);
  waiting.server().stage = "listening"; await waiting.advance(350); waiting.emit(9); await settle();
  assert.equal(waiting.frames().at(-1).options.headers["X-Audio-Sequence"], "2");

  const overflow = browser({ deferPCM: true }); await overflow.begin(); overflow.emit();
  for (let index = 0; index < 8; index++) overflow.emit();
  await settle(); assert.equal(overflow.idle(), true); assert.equal(overflow.streams[0].stopped, true);
  assert.ok(overflow.status().includes("待機上限")); assert.equal(overflow.frames().length, 1);

  const exhausted = browser({ pcmFailures: 3 }); await exhausted.begin(); exhausted.emit(); await settle(); await exhausted.advance(750);
  assert.equal(exhausted.idle(), true); assert.deepEqual(exhausted.frames().map((frame) => frame.options.headers["X-Audio-Sequence"]), ["0", "0", "0"]);

  const wrongACK = browser({ deferPCM: true }); await wrongACK.begin(); wrongACK.emit(); await settle();
  wrongACK.pendingPCM[0].resolve({ session_id: "session-1", generation: 0, sequence: 99, next_sequence: 100, stage: "listening" }); await settle();
  assert.equal(wrongACK.idle(), true); assert.ok(wrongACK.status().includes("順序"));

  const late = browser({ deferPCM: true, ignorePCMAbort: true }); await late.begin(); late.emit(); await settle(); late.stop(); await late.begin();
  const before = late.status(); late.pendingPCM[0].resolve({ session_id: "session-1", generation: 0, sequence: 0, next_sequence: 1, stage: "speaking", transcript: "古い文字" }); await settle();
  assert.equal(late.status(), before); assert.notEqual(late.element("voice-transcript").textContent, "古い文字");
  console.log("PCM transport checked: unconditional silence, sequence, one in-flight, retry identity, bounded overflow, server gating and stale ACKs");
})().catch((error) => { console.error(error); process.exitCode = 1; });
