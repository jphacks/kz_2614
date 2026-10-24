"use strict";

const { browser, settle, assert } = require("./dashboard_browser_harness");

function frame(cursor, frameIndex, totalSamples = 4_000, overrides = {}) {
  const samples = Math.min(1_600, totalSamples - frameIndex * 1_600);
  const pcm = Buffer.alloc(samples * 2);
  for (let offset = 0; offset < pcm.length; offset += 2) pcm.writeInt16LE(1_000 + frameIndex, offset);
  const count = Math.ceil(totalSamples / 1_600);
  return {
    type: "audio_frame", turn_id: "turn-1", cursor, source_type: "audio_chunk", index: 0,
    media_type: "audio/pcm", sample_rate: 16_000, channels: 1, format: "s16le",
    frame_index: frameIndex, frame_count: count, sample_offset: frameIndex * 1_600,
    total_samples: totalSamples, is_last: frameIndex === count - 1,
    audio_base64: pcm.toString("base64"), ...overrides,
  };
}
const audioMessages = (app) => app.outputWorklets[0].messages.filter(({ message }) => message.type === "audio");
const playback = (app) => app.server().playback.map(({ event }) => event);

async function outputStartsOnActualProcessingAndEndsOnlyAfterDrain() {
  const app = browser(); await app.begin();
  const connection = app.calls.find(({ url }) => url.includes("/audio-stream/events?"));
  assert.equal(connection.options.headers["X-Voice-Audio-Profile"], "pcm_s16le_16000_stream");
  assert.equal(app.outputWorklets.length, 1);
  app.frameEvent(frame(1, 0)); await settle();
  assert.equal(audioMessages(app).length, 1);
  assert.equal(app.stats.replyPlays, 0, "PCM output must not create a file/blob audio player");
  assert.equal(app.element("voice-audio").src || "", "");
  assert.deepEqual(playback(app), [], "Receiving audio is not proof that output has started");
  app.outputWorklets[0].processAudio(1); await settle();
  assert.deepEqual(playback(app), ["started"], "Started must come from the first worklet render");
  app.emit(); await settle();
  assert.equal(app.frames().length, 1, "Microphone PCM must continue while reply PCM is output");

  app.frameEvent(frame(1, 1)); app.frameEvent(frame(1, 2));
  app.emitEvent({ type: "complete", turn_id: "turn-1", response_text: "返答です。" });
  await settle();
  assert.deepEqual(playback(app), ["started"], "Generation completion must not end queued output");
  app.outputWorklets[0].processAudio(1); await settle();
  assert.deepEqual(playback(app), ["started"]);
  app.outputWorklets[0].processAudio(1); await settle();
  assert.deepEqual(playback(app), ["started", "ended"]);
  assert.equal(app.stats.replyPlays, 0);
  for (const { message, transfer } of audioMessages(app)) assert.equal(transfer[0], message.pcm);
  app.stop(); await settle();
}

async function parentCursorCommitsOnlyAfterItsFinalFrame() {
  const app = browser(); await app.begin();
  app.frameEvent(frame(1, 0)); app.frameEvent(frame(1, 1)); await settle();
  assert.equal(audioMessages(app).length, 2);
  app.disconnectEvents(); await settle(); await app.advance(250);
  assert.equal(app.eventConnections.at(-1).after, 0, "An unfinished parent must reconnect before its durable cursor");
  assert.equal(audioMessages(app).length, 2, "Replayed frame indexes must not duplicate already accepted PCM");
  app.frameEvent(frame(1, 2)); await settle();
  assert.equal(audioMessages(app).length, 3);
  app.disconnectEvents(); await settle(); await app.advance(250);
  assert.equal(app.eventConnections.at(-1).after, 1, "The final accepted frame commits its parent cursor");
  assert.equal(audioMessages(app).length, 3);
  app.stop(); await settle();
}

async function missingOrChangingFrameMetadataIsAnExplicitFailure() {
  const cases = [
    [frame(1, 0), frame(1, 2)],
    [frame(1, 0), frame(1, 1, 4_100)],
    [frame(1, 0, 4_000, { is_last: true })],
    [frame(1, 0, 4_000, { sample_offset: 1 })],
    [frame(1, 0, 4_000, { audio_base64: Buffer.alloc(2).toString("base64") })],
  ];
  for (const events of cases) {
    const app = browser(); await app.begin();
    for (const event of events) app.frameEvent(event);
    await settle();
    assert.equal(app.idle(), true, "A missing or malformed frame must fail rather than output incomplete audio");
    assert.equal(app.streams[0].stopped, true);
  }
}

async function boundedPCMQueueReplaysOnlyUnacceptedFrames() {
  const app = browser(); await app.begin();
  for (let parent = 1; parent <= 5; parent++) {
    for (let index = 0; index < 600; index++) app.frameEvent(frame(parent, index, 960_000, { index: parent - 1 }));
    await settle();
  }
  assert.equal(audioMessages(app).length, 2_621, "The raw PCM output queue must stay below 8MiB");
  assert.equal(app.eventConnections.length, 1);
  app.emit(); await settle();
  assert.equal(app.frames().length, 1, "Output backpressure must not stop the input lane");
  app.emitEvent({ type: "complete", turn_id: "turn-1", response_text: "長い返答" });
  app.outputWorklets[0].processAudio(1_600); await settle(); await app.advance(100);
  assert.equal(app.eventConnections.at(-1).after, 4, "Reconnect before the partially queued fifth parent");
  assert.equal(audioMessages(app).length, 3_000, "Retry must fill the missing tail without replaying accepted frames");
  app.outputWorklets[0].processAudio(); await settle();
  assert.deepEqual(playback(app), ["started", "ended"]);
  app.stop(); await settle();
}

async function volumeAndStopRemainScopedToTheCurrentOutput() {
  const app = browser(); await app.begin();
  app.element("voice-audio").volume = 0.37;
  app.element("voice-audio").dispatch("volumechange");
  assert.equal(app.outputWorklets[0].connections[0].gain.value, 0.37);
  app.frameEvent(frame(1, 0, 1_600)); await settle();
  const output = app.outputWorklets[0], old = output.audio[0], oldHandler = output.port.onmessage;
  app.stop(); await settle();
  assert.equal(output.audio.length, 0);
  assert.equal(output.disconnected, true);
  await app.begin();
  const before = app.status();
  for (const type of ["started", "consumed", "drained", "error"]) {
    oldHandler({ data: { type, epoch: old.epoch, turn_id: old.turn_id, bytes: old.pcm.byteLength } });
  }
  await settle();
  assert.equal(app.status(), before);
  assert.equal(app.streams.at(-1).stopped, false);
  app.stop(); await settle();
}

async function suspendedAudioContextStopsWaitingAndQueuedOutput() {
  for (const queued of [false, true]) {
    const app = browser(); await app.begin();
    if (queued) {
      app.frameEvent(frame(1, 0, 1_600));
      app.emitEvent({ type: "complete", turn_id: "turn-1", response_text: "未出力の返答" });
      await settle();
      assert.equal(app.outputWorklets[0].audio.length, 1);
    }
    const oldContext = app.contexts[0], oldHandler = oldContext.listeners.get("statechange");
    oldContext.changeState(queued ? "interrupted" : "suspended"); await settle();
    assert.equal(app.idle(), true, "A non-running context cannot remain in listening or drain-wait state");
    assert.equal(app.streams[0].stopped, true);
    assert.equal(app.outputWorklets[0].audio.length, 0);
    assert.equal(oldContext.listeners.has("statechange"), false);
    await app.begin();
    const before = app.status();
    oldHandler(); await settle();
    assert.equal(app.status(), before, "A late statechange from the old context cannot stop a replacement conversation");
    assert.equal(app.streams.at(-1).stopped, false);
    app.stop(); await settle();
  }
}

(async () => {
  await outputStartsOnActualProcessingAndEndsOnlyAfterDrain();
  await parentCursorCommitsOnlyAfterItsFinalFrame();
  await missingOrChangingFrameMetadataIsAnExplicitFailure();
  await boundedPCMQueueReplaysOnlyUnacceptedFrames();
  await volumeAndStopRemainScopedToTheCurrentOutput();
  await suspendedAudioContextStopsWaitingAndQueuedOutput();
  console.log("PCM output checked: profile, actual render acknowledgement, ordered/replayed frames, parent cursor, bounded queue, stop fences and gain");
})().catch((error) => { console.error(error); process.exitCode = 1; });
