"use strict";

const { browser, settle, assert } = require("./dashboard_browser_harness");

const audio = (turnId = "turn-1", index = 0) => ({
  type: "audio_chunk", turn_id: turnId, index, media_type: "audio/wav",
  audio_base64: Buffer.from(`RIFF-${turnId}-${index}-WAVE`).toString("base64"),
});
const interruption = (oldTurn = "turn-1", newTurn = "turn-2") => ({
  type: "interruption", turn_id: oldTurn, interrupted_turn_id: oldTurn, new_turn_id: newTurn,
  cancellation_requested: true, native_work_may_continue: true,
});
const controls = (app) => app.calls.filter(({ url }) => url.endsWith("/audio-stream/playback"))
  .map(({ options }) => JSON.parse(options.body));
function frame(cursor, turnId, frameIndex = 0, samples = 1_600) {
  const count = Math.ceil(samples / 1_600);
  return {
    type: "audio_frame", cursor, turn_id: turnId, source_type: "audio_chunk", index: 0,
    media_type: "audio/pcm", sample_rate: 16_000, channels: 1, format: "s16le",
    frame_index: frameIndex, frame_count: count, sample_offset: frameIndex * 1_600,
    total_samples: samples, is_last: frameIndex === count - 1,
    audio_base64: Buffer.alloc(Math.min(1_600, samples - frameIndex * 1_600) * 2, 1).toString("base64"),
  };
}

async function interruptGeneratingReplyWithoutRestartingMicrophone() {
  const app = browser(); await app.begin();
  const input = app.worklets[0], stream = app.streams[0], context = app.contexts[0];
  app.emitEvent({ type: "transcript", turn_id: "turn-1", text: "前の発話" });
  app.emitEvent({ type: "text_delta", turn_id: "turn-1", text: "生成途中の返答" });
  app.emitEvent(interruption()); await settle();
  assert.equal(app.element("voice-response").textContent, "");
  assert.equal(app.element("voice-audio").paused, true);
  assert.equal(app.idle(), false);
  app.emit(); await settle();
  assert.equal(app.frames().length, 1);
  assert.equal(app.stats.microphoneRequests, 1);
  assert.equal(app.worklets[0], input);
  assert.equal(app.streams[0], stream); assert.equal(stream.stopped, false);
  assert.equal(app.contexts[0], context); assert.equal(context.state, "running");
  app.emitEvent({ type: "transcript", turn_id: "turn-2", text: "割り込んだ新しい発話" });
  app.emitEvent({ type: "text_delta", turn_id: "turn-2", text: "新しい返答" });
  app.emitEvent(audio("turn-2")); await settle();
  assert.equal(app.element("voice-transcript").textContent, "割り込んだ新しい発話");
  assert.equal(app.element("voice-response").textContent, "新しい返答");
  assert.deepEqual(controls(app).map(({ turn_id }) => turn_id), ["turn-2"]);
  app.stop(); await settle();
}

async function queuedPlaybackAndLatePromisesCannotReviveInterruptedTurn() {
  const app = browser({ deferControl: true, ignoreControlAbort: true, deferPlayback: true });
  app.element("voice-heartbeat-enabled").checked = true;
  await app.begin();
  app.emitEvent(audio()); app.emitEvent(audio("turn-1", 1)); await settle();
  const oldEnded = app.element("voice-audio").onended, oldError = app.element("voice-audio").onerror;
  const oldControl = app.pendingControls[0], oldPlayback = app.pendingPlayback[0];
  assert.equal(app.stats.replyPlays, 1);
  app.emitEvent(interruption()); await settle();
  assert.equal(app.element("voice-audio").paused, true);
  assert.equal(app.element("voice-audio").src || "", "");
  assert.equal(oldControl.signal.aborted, true);
  assert.equal(app.element("voice-heartbeat-audio").paused, false, "Barge-in must preserve the separately enabled heartbeat");
  assert.equal(app.streams[0].stopped, false);
  app.emitEvent({ type: "transcript", turn_id: "turn-2", text: "新発話" });
  app.emitEvent(audio("turn-2")); await settle();
  const src = app.element("voice-audio").src;
  oldEnded(); oldError(); oldPlayback.resolve();
  oldControl.resolve({ session_id: "session-1", generation: 99, stage: "failed", transcript: "古い通知" });
  app.emitEvent(audio("turn-1", 2), { updateServer: false });
  app.emitEvent({ type: "text_delta", turn_id: "turn-1", text: "遅れた旧返答" }, { updateServer: false });
  app.emitEvent({ type: "error", turn_id: "turn-1", error: "tts_timeout", message: "旧処理の期限切れ" }, { updateServer: false });
  app.emitEvent({ type: "complete", turn_id: "turn-1", transcript: "古い発話", response_text: "古い完了" }, { updateServer: false });
  await settle();
  assert.equal(app.idle(), false);
  assert.equal(app.stats.replyPlays, 2, "Neither the old queue nor an old play promise may restart output");
  assert.equal(app.element("voice-audio").src, src);
  assert.equal(app.element("voice-transcript").textContent, "新発話");
  assert.deepEqual(controls(app).map(({ turn_id }) => turn_id), ["turn-1", "turn-2"]);
  app.pendingControls.at(-1).resolve(); app.pendingPlayback.at(-1).resolve(); await settle();
  app.emit(); await settle();
  assert.equal(app.frames().length, 1);
  app.stop(); await settle();
}

async function cancelledControlBackoffAndRetryTimerCannotSendOldTurn() {
  for (const failures of [1, 3]) {
    const app = browser({ controlFailures: failures }); await app.begin();
    app.emitEvent(audio()); await settle();
    if (failures === 3) await app.advance(750);
    app.emitEvent(interruption());
    app.emitEvent(audio("turn-2")); await settle();
    await app.advance(2_000);
    assert.equal(app.idle(), false);
    assert.equal(controls(app).filter(({ turn_id }) => turn_id === "turn-1").length, failures,
      "Interruption must cancel both in-flight retry backoff and a deferred control retry timer");
    assert.equal(controls(app).at(-1).turn_id, "turn-2");
    app.stop(); await settle();
  }
}

async function pcmACKBreaksPlaybackBackpressureWithoutSkippingEventCursors() {
  const app = browser({ autoplayReject: true }); await app.begin();
  const data = Buffer.alloc(3 * 1024 * 1024, 1).toString("base64");
  for (let index = 0; index < 3; index++) { app.emitEvent({ ...audio(), index, audio_base64: data }); await settle(); }
  assert.equal(app.eventConnections.length, 1);
  const cursor = app.emitEvent(interruption()); await settle();
  app.emit(); await settle();
  assert.equal(app.element("voice-audio").src || "", "", "The input ACK must stop a reply even when its event reader is waiting for output space");
  assert.equal(app.idle(), false);
  await app.advance(100);
  assert.equal(app.eventConnections.at(-1).after, 2, "ACK delivery must not skip the unaccepted old event cursor");
  assert.equal(app.stats.replyPlays, 1);
  app.frameEvent(frame(cursor + 1, "turn-2")); await settle();
  const output = app.outputWorklets[0], nextFrame = output.audio[0];
  app.emit(); await settle();
  assert.equal(output.audio[0], nextFrame, "Repeated snapshots of an old interruption must preserve new output");
  output.processAudio(); await settle();
  assert.equal(app.streams[0].stopped, false);
  assert.equal(app.stats.microphoneRequests, 1);
  app.stop(); await settle();
}

async function pcmInterruptionDropsRingAndFencesOldWorkletAcknowledgements() {
  const app = browser(); await app.begin();
  app.frameEvent(frame(1, "turn-1", 0, 4_800)); await settle();
  const output = app.outputWorklets[0], old = output.audio[0];
  const payload = { ...interruption(), session_id: "session-1", generation: 0, cursor: 2 };
  app.server().interruption = payload;
  app.server().turnId = "turn-2"; app.server().stage = "speaking";
  app.emit(); await settle();
  assert.equal(output.audio.length, 0, "An interruption must flush already transferred PCM from the output ring");
  app.frameEvent(frame(1, "turn-1", 1, 4_800), { updateServer: false });
  app.frameEvent(frame(1, "turn-1", 2, 4_800), { updateServer: false });
  app.emitEvent(interruption());
  app.frameEvent(frame(3, "turn-2"));
  app.emitEvent({ type: "complete", turn_id: "turn-2", response_text: "次の返答" });
  await settle();
  assert.equal(output.audio.length, 1, "Remaining old parent frames must consume cursors without entering the new ring");
  for (const type of ["started", "consumed", "drained", "error"]) {
    output.port.onmessage({ data: { type, epoch: old.epoch, turn_id: old.turn_id, bytes: old.pcm.byteLength } });
  }
  await settle();
  assert.equal(app.idle(), false);
  assert.deepEqual(controls(app), [], "Old output acknowledgements must never notify against the new turn");
  output.processAudio(); await settle();
  assert.deepEqual(controls(app).map(({ turn_id, event }) => [turn_id, event]), [["turn-2", "started"], ["turn-2", "ended"]]);
  assert.equal(app.stats.replyPlays, 0);
  assert.equal(app.stats.microphoneRequests, 1);
  app.stop(); await settle();
}

(async () => {
  await interruptGeneratingReplyWithoutRestartingMicrophone();
  await queuedPlaybackAndLatePromisesCannotReviveInterruptedTurn();
  await cancelledControlBackoffAndRetryTimerCannotSendOldTurn();
  await pcmACKBreaksPlaybackBackpressureWithoutSkippingEventCursors();
  await pcmInterruptionDropsRingAndFencesOldWorkletAcknowledgements();
  console.log("Barge-in checked: generating/playing replies, queue/ring flush, persistent input/heartbeat, control cancellation, stale events and output acknowledgements");
})().catch((error) => { console.error(error); process.exitCode = 1; });
