"use strict";

const { browser, settle, assert } = require("./dashboard_browser_harness");

const audio = (index) => ({
  type: "audio_chunk", turn_id: "turn-1", index, text: `文節${index}`,
  media_type: "audio/wav", audio_base64: Buffer.from(`RIFF-part-${index}-WAVE`).toString("base64"),
});
const playbackEvents = (app) => app.server().playback.map((event) => event.event);
const complete = (app) => app.emitEvent({
  type: "complete", turn_id: "turn-1", transcript: "確定した発話", response_text: "文節0。文節1。",
});

async function checkEarlyAudioOrderedPlaybackAndContinuousInput() {
  const app = browser({ splitEventBytes: true });
  await app.begin();
  assert.equal(app.eventConnections.length, 1);
  app.emitEvent({ type: "transcript", turn_id: "turn-1", text: "話しかけた内容" });
  app.emitEvent(audio(0));
  await settle();
  assert.equal(app.stats.plays, 1, "The first chunk must play before generation completes");
  assert.deepEqual(playbackEvents(app), ["started"]);
  assert.equal(app.element("voice-transcript").textContent, "話しかけた内容");

  app.emitEvent({ type: "text_delta", turn_id: "turn-1", text: "返事の途中" });
  await settle();
  assert.ok(app.element("voice-response").textContent.includes("返事の途中"), "Text deltas must be visible immediately");
  const beforeFrames = app.frames().length;
  for (const stage of ["stt", "llm", "tts", "playing"]) {
    app.server().stage = stage;
    app.emit(0);
    await settle();
  }
  assert.equal(app.frames().length, beforeFrames + 4, "Silent PCM must continue while the server processes and audio plays");

  app.emitEvent(audio(1));
  complete(app);
  await settle();
  assert.equal(app.stats.plays, 1, "Chunks must not overlap");
  assert.deepEqual(playbackEvents(app), ["started"], "Complete alone must not acknowledge playback end");
  app.element("voice-audio").finish();
  await settle();
  assert.equal(app.stats.plays, 2);
  assert.deepEqual(playbackEvents(app), ["started"], "The first chunk ending must not end a multi-chunk reply");
  app.element("voice-audio").finish();
  await settle();
  assert.deepEqual(playbackEvents(app), ["started", "ended"]);
  assert.equal(app.element("voice-response").textContent, "文節0。文節1。");
}

async function checkReconnectCursorAndDuplicateSuppression() {
  const app = browser({ replayLastEventOnReconnect: true });
  await app.begin();
  const cursor = app.emitEvent(audio(0));
  await settle();
  assert.equal(typeof cursor, "number");
  assert.equal(app.stats.plays, 1);
  app.disconnectEvents();
  await app.advance(1_000);
  assert.ok(app.eventConnections.length >= 2, "A lost event connection must reconnect");
  assert.equal(app.eventConnections.at(-1).after, cursor, "Reconnect after the last queued event, not the last played event");
  app.replayEvent(cursor);
  await settle();
  assert.equal(app.stats.plays, 1, "Replayed cursors must not replay audio");
  assert.deepEqual(playbackEvents(app), ["started"]);
  complete(app);
  await settle();
  app.element("voice-audio").finish();
  await settle();
  assert.deepEqual(playbackEvents(app), ["started", "ended"]);

  const eof = browser();
  await eof.begin();
  const textCursor = eof.emitEvent({ type: "text_delta", turn_id: "turn-1", text: "接続をまたぐ返事" });
  await settle();
  eof.endEvents();
  await eof.advance(1_000);
  assert.ok(eof.eventConnections.length >= 2, "A normal EOF must also reconnect");
  assert.equal(eof.eventConnections.at(-1).after, textCursor);
  assert.equal(eof.element("voice-response").textContent, "接続をまたぐ返事");
}

async function checkAutoplayDenialKeepsQueuedChunks() {
  const app = browser({ autoplayReject: true });
  await app.begin();
  app.emitEvent(audio(0));
  app.emitEvent(audio(1));
  complete(app);
  await settle();
  assert.equal(app.stats.plays, 1);
  assert.deepEqual(playbackEvents(app), [], "Autoplay rejection is not a playback start or media failure");
  assert.ok(app.status().includes("再生ボタン"));
  app.emit(0);
  await settle();
  assert.equal(app.frames().length, 1, "A playback permission prompt must not stop continuous PCM input");
  app.environment.allowPlayback = true;
  await app.element("voice-audio").play();
  await settle();
  assert.deepEqual(playbackEvents(app), ["started"]);
  app.element("voice-audio").finish();
  await settle();
  assert.equal(app.stats.plays, 3, "Both retained chunks must play after manual permission");
  assert.deepEqual(playbackEvents(app), ["started"]);
  app.element("voice-audio").finish();
  await settle();
  assert.deepEqual(playbackEvents(app), ["started", "ended"]);
}

async function checkStoppedCallbacksAndReaderCannotAffectNewSession() {
  const app = browser({ ignoreEventAbort: true });
  await app.begin();
  app.emitEvent(audio(0));
  await settle();
  const oldEnded = app.element("voice-audio").onended;
  const oldError = app.element("voice-audio").onerror;
  const oldConnection = app.eventConnections[0];
  app.stop();
  await settle();
  const oldPlaybackCount = app.server().playback.length;
  await app.begin();
  const status = app.status();
  const transcript = app.element("voice-transcript").textContent;
  oldEnded();
  oldError();
  oldConnection.reader.push({
    type: "text_delta", cursor: 2, session_id: "session-1", generation: 0, turn_id: "turn-1", text: "古い返事",
  });
  oldConnection.reader.push({ ...audio(1), cursor: 3, session_id: "session-1", generation: 0 });
  await settle();
  assert.equal(oldConnection.signal.aborted, true);
  assert.equal(app.stats.plays, 1);
  assert.equal(app.server().playback.length, 0, "Old callbacks must not notify against the new session");
  assert.equal(app.status(), status);
  assert.equal(app.element("voice-transcript").textContent, transcript);
  assert.equal(oldPlaybackCount, 1);
}

async function checkSameCursorControlEvents() {
  const app = browser();
  await app.begin();
  const cursor = app.emitEvent({ type: "text_delta", turn_id: "turn-1", text: "表示を保つ" });
  await settle();
  const before = app.element("voice-response").textContent;
  app.controlEvent({ type: "heartbeat", cursor });
  await settle();
  assert.equal(app.idle(), false, "A heartbeat using the current cursor must not stop the session");
  assert.equal(app.element("voice-response").textContent, before);
  app.controlEvent({ type: "error", cursor, error: "event_stream_failed", message: "音声応答を続けられません" });
  await settle();
  assert.equal(app.idle(), true, "A same-cursor terminal error must be handled before duplicate suppression");
  assert.equal(app.streams[0].stopped, true);
  assert.ok(app.status().includes("音声応答を続けられません"));

  const noSpeechControl = browser();
  await noSpeechControl.begin();
  const last = noSpeechControl.emitEvent({ type: "text_delta", turn_id: "turn-1", text: "制御エラーの確認" });
  await settle();
  noSpeechControl.controlEvent({ type: "error", cursor: last, error: "no_speech", turn_id: null, message: "制御接続が終了しました" });
  await settle();
  assert.equal(noSpeechControl.idle(), true, "A same-cursor control error without a turn must remain terminal, including no_speech");
}

async function checkNoSpeechTurnKeepsContinuousInputAndConsumesCursor() {
  const app = browser({ replayLastEventOnReconnect: true });
  await app.begin();
  app.emitEvent({ type: "text_delta", turn_id: "empty-turn", text: "破棄する空返答の候補" });
  const cursor = app.emitEvent({ type: "error", turn_id: "empty-turn", error: "no_speech", message: "発話を検出できませんでした" });
  await settle();
  assert.equal(app.idle(), false);
  assert.equal(app.streams[0].stopped, false);
  assert.equal(app.element("voice-response").textContent, "");
  assert.deepEqual(playbackEvents(app), []);
  app.emit(0); await settle(); assert.equal(app.frames().length, 1, "No-speech must not interrupt continuous silent PCM");
  app.endEvents(); await settle(); await app.advance(100);
  assert.equal(app.eventConnections.at(-1).after, cursor, "The recoverable error must consume its durable cursor");
  assert.equal(app.idle(), false, "Replaying the same recoverable error must be harmless");
  app.emitEvent({ type: "transcript", turn_id: "next-turn", text: "次の発話" });
  app.emitEvent({ ...audio(0), turn_id: "next-turn" });
  app.emitEvent({ type: "complete", turn_id: "next-turn", transcript: "次の発話", response_text: "次の返答" });
  await settle();
  assert.equal(app.stats.plays, 1, "The next valid utterance must still produce audio");
  app.element("voice-audio").finish(); await settle();
  assert.deepEqual(playbackEvents(app), ["started", "ended"]);
}

(async () => {
  await checkEarlyAudioOrderedPlaybackAndContinuousInput();
  await checkReconnectCursorAndDuplicateSuppression();
  await checkAutoplayDenialKeepsQueuedChunks();
  await checkStoppedCallbacksAndReaderCannotAffectNewSession();
  await checkSameCursorControlEvents();
  await checkNoSpeechTurnKeepsContinuousInputAndConsumesCursor();
  console.log("NDJSON output checked: early audio, ordered chunks, continuous PCM, text deltas, cursor replay, EOF, autoplay queue, stop fences and same-cursor controls");
})().catch((error) => { console.error(error); process.exitCode = 1; });
