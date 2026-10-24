"use strict";

const { browser, settle, assert } = require("./dashboard_browser_harness");

const audio = () => ({
  type: "audio_chunk", turn_id: "turn-1", index: 0, media_type: "audio/wav",
  audio_base64: Buffer.from("RIFF-transport-test-WAVE").toString("base64"),
});
const controls = (app) => app.calls.filter(({ url }) => url.endsWith("/audio-stream/playback"))
  .map(({ options }) => JSON.parse(options.body));

async function sendFrames(app, count = 24) {
  const before = app.frames().length;
  for (let index = 0; index < count; index++) {
    app.emit(index);
    await settle();
    await app.advance(256);
    assert.equal(app.idle(), false, "A slow side request must not stop a healthy PCM connection");
  }
  assert.equal(app.frames().length, before + count, "Every microphone frame must reach the PCM lane");
  assert.equal(app.stats.maxPCMInFlight, 1, "PCM requests must remain serial");
  assert.deepEqual(app.frames().map(({ options }) => options.headers["X-Audio-Sequence"]),
    app.frames().map((_, index) => String(index)), "PCM ACKs alone advance the ordered sequence");
}

async function slowPollingDoesNotHoldPCM() {
  const app = browser({ deferGET: true });
  await app.begin();
  assert.equal(app.pendingGET.length, 1);
  await sendFrames(app);
  assert.ok(app.environment.now > 5_000, "Keep the state endpoint delayed beyond its request timeout");
  assert.equal(app.stats.maxPollInFlight, 1, "Polls must not overlap or accumulate in-flight requests");
  assert.equal(app.stats.controlInFlight, 0);
  app.stop(); await settle();
}

async function slowPlaybackNotificationsStayFIFOWhilePCMFlows() {
  const app = browser({ deferControl: true });
  await app.begin();
  app.emitEvent(audio());
  app.emitEvent({ type: "complete", turn_id: "turn-1", transcript: "現在の発話", response_text: "現在の返答" });
  await settle();
  assert.equal(app.pendingControls.length, 1);
  app.element("voice-audio").finish(); await settle();
  assert.ok(controls(app).every(({ event }) => event === "started"), "Ended must wait behind the unacknowledged started control");
  await sendFrames(app);
  assert.ok(app.environment.now > 5_000, "Delay playback acknowledgement beyond five seconds");
  assert.equal(app.stats.maxControlInFlight, 1, "Playback controls must have only one in-flight request");

  const started = app.pendingControls.findLast(({ signal }) => !signal.aborted);
  assert.ok(started, "The current started notification must still be retryable");
  started.resolve(); await settle();
  const ended = app.pendingControls.findLast(({ signal }) => !signal.aborted);
  assert.notEqual(ended, started, "FIFO must advance to ended only after started is acknowledged");
  assert.equal(controls(app).at(-1).event, "ended");
  ended.resolve(); await settle();
  assert.deepEqual(app.server().playback.map(({ event }) => event), ["started", "ended"]);
  const order = controls(app).map(({ event }) => event);
  assert.ok(order.slice(0, -1).every((event) => event === "started"));
  assert.equal(app.stats.maxControlInFlight, 1);
  app.stop(); await settle();
}

async function transientSideFailuresDoNotStopPCM() {
  const app = browser({ pollFailures: 100, controlFailures: 1 });
  await app.begin();
  app.emitEvent(audio());
  app.emitEvent({ type: "complete", turn_id: "turn-1", response_text: "応答" });
  await settle();
  await sendFrames(app);
  assert.ok(app.stats.controlAttempts >= 2, "A transient control failure must be retried");
  assert.deepEqual(app.server().playback.map(({ event }) => event), ["started"]);
  assert.equal(app.stats.maxControlInFlight, 1);
  assert.equal(app.stats.maxPollInFlight, 1);
  app.element("voice-audio").finish(); await settle();
  assert.equal(app.idle(), false);
  assert.deepEqual(app.server().playback.map(({ event }) => event), ["started", "ended"]);
  app.stop(); await settle();
}

async function olderSameGenerationPollCannotOverwritePCMOrEvents() {
  const app = browser({ deferGET: true });
  await app.begin();
  app.server().stage = "speaking"; app.server().transcript = "新しいPCMの認識";
  app.emit(); await settle();
  const before = app.status();
  app.pendingGET[0].resolve({
    session_id: "session-1", generation: 0, next_sequence: 0, event_cursor: 0,
    stage: "failed", transcript: "古い状態取得の文字",
  });
  await settle();
  assert.equal(app.status(), before, "An older same-generation poll must not regress a newer PCM state");
  assert.equal(app.element("voice-transcript").textContent, "新しいPCMの認識");
  app.stop(); await settle();

  const events = browser({ deferGET: true });
  await events.begin();
  events.emitEvent(audio());
  events.emitEvent({ type: "complete", turn_id: "turn-1", transcript: "確定した新しい発話", response_text: "確定返答" });
  await settle();
  events.element("voice-audio").finish(); await settle();
  const eventStatus = events.status();
  events.pendingGET[0].resolve({
    session_id: "session-1", generation: 0, next_sequence: 0, event_cursor: 0,
    stage: "failed", transcript: "同じPCM連番でも古い文字",
  });
  await settle();
  assert.equal(events.status(), eventStatus, "Equal PCM sequence must not let an older poll override newer events or controls");
  assert.equal(events.element("voice-transcript").textContent, "確定した新しい発話");
  events.stop(); await settle();
}

async function olderControlSnapshotCannotOverwriteNewEvents() {
  const app = browser({ deferControl: true });
  await app.begin();
  app.emitEvent(audio()); await settle();
  app.emitEvent({ type: "complete", turn_id: "turn-1", transcript: "新しい確定発話", response_text: "確定返答" });
  await settle();
  app.element("voice-audio").finish(); await settle();
  const before = app.status();
  app.pendingControls[0].resolve({
    session_id: "session-1", generation: 0, next_sequence: 0, event_cursor: 1,
    turn_id: "turn-1", stage: "failed", transcript: "古い再生通知の文字",
  });
  await settle();
  assert.equal(app.status(), before, "An acknowledged control's stale snapshot must not regress newer event state");
  assert.equal(app.element("voice-transcript").textContent, "新しい確定発話");
  app.stop(); await settle();
}

async function stoppedSideRequestsCannotAffectTheNextSession() {
  const app = browser({ deferGET: true, ignoreGETAbort: true, deferControl: true, ignoreControlAbort: true });
  await app.begin();
  app.emitEvent(audio()); await settle();
  const oldPoll = app.pendingGET[0], oldControl = app.pendingControls[0];
  assert.ok(oldPoll && oldControl);
  app.stop(); await settle();
  assert.equal(oldPoll.signal.aborted, true);
  assert.equal(oldControl.signal.aborted, true);
  await app.begin();
  app.server().stage = "speaking"; app.server().transcript = "次の会話の文字";
  app.emit(); await settle();
  const before = app.status(), frameCount = app.frames().length;
  const old = { session_id: "session-1", generation: 0, next_sequence: 0, event_cursor: 0,
    stage: "failed", transcript: "停止済みの会話からの遅着" };
  oldPoll.resolve(old); oldControl.resolve(old); await settle();
  assert.equal(app.status(), before);
  assert.equal(app.element("voice-transcript").textContent, "次の会話の文字");
  assert.equal(app.frames().length, frameCount);
  assert.equal(app.streams.at(-1).stopped, false);
  assert.equal(app.idle(), false);
  app.emit(); await settle();
  assert.deepEqual(app.frames().slice(-2).map(({ options }) => options.headers["X-Audio-Sequence"]), ["0", "1"]);
  assert.ok(app.frames().slice(-2).every(({ url }) => url.includes("session-2")));
  app.stop(); await settle();
}

async function pcmDeadlineIncludesUncooperativeFetchAndJSONRead() {
  for (const options of [{ deferPCM: true, ignorePCMAbort: true }, { deferPCMJSON: true }]) {
    const app = browser({ ...options, deferGET: true });
    await app.begin();
    app.emit(1234); await settle();
    await app.advance(24_749);
    assert.equal(app.idle(), false, "The PCM retry budget must remain usable until its absolute deadline");
    await app.advance(1);
    assert.equal(app.idle(), true, "Unsettled fetch or JSON reads must not exceed the 24.75s recovery budget");
    assert.equal(app.streams[0].stopped, true);
    assert.deepEqual(app.frames().map(({ options: request }) => request.headers["X-Audio-Sequence"]), ["0", "0", "0"]);
    const original = Buffer.from(app.frames()[0].options.body);
    for (const { options: request } of app.frames()) assert.deepEqual(Buffer.from(request.body), original);
    assert.equal(app.server().received.length, options.deferPCMJSON ? 1 : 0, "A JSON timeout must not duplicate accepted PCM");
    const oldRequests = [...app.pendingPCM, ...app.pendingPCMJSON];
    await app.begin();
    const before = app.status();
    for (const request of oldRequests) request.resolve();
    await settle();
    assert.equal(app.status(), before, "Late timed-out fetch/JSON completions cannot affect a replacement session");
    assert.equal(app.idle(), false);
    assert.equal(app.streams.at(-1).stopped, false);
    app.stop(); await settle();
  }
}

function capturedBytes(app) {
  return Buffer.concat(app.server().received.map((pcm) => Buffer.from(pcm)));
}

function assertExactCapturedStream(app, captured, gains) {
  assert.deepEqual(capturedBytes(app), Buffer.concat(captured.map((pcm) => Buffer.from(pcm))),
    "Recovery must deliver every captured PCM sample once and in capture order");
  assert.deepEqual(app.server().receivedRequests.map(({ sequence }) => sequence), captured.map((_, index) => index));
  assert.ok(app.server().receivedRequests.every(({ body }) => body.byteLength === 8192),
    "Keep the established PCM frame boundaries used by server VAD");
  if (gains) assert.deepEqual(app.server().receivedRequests.map(({ gain }) => gain), gains);
  const attempts = new Map();
  for (const { options } of app.frames()) {
    const sequence = options.headers["X-Audio-Sequence"];
    const frozen = { body: Buffer.from(options.body), gain: options.headers["X-Audio-Input-Gain"] };
    if (attempts.has(sequence)) assert.deepEqual(frozen, attempts.get(sequence), "A retry cannot change a packet's bytes or capture-time gain");
    else attempts.set(sequence, frozen);
  }
  assert.equal(app.stats.maxPCMInFlight, 1);
  assert.equal(app.stats.microphoneRequests, 1);
  assert.equal(app.streams[0].stopped, false, "A recoverable stall must preserve the microphone");
  assert.equal(app.idle(), false);
}

async function slowACKAndStatusRecoverWithoutDroppingContinuousInput() {
  for (const milliseconds of [2_000, 5_000]) {
    const app = browser({ pcmACKBlockedUntilMs: milliseconds, pollDisconnectUntilMs: milliseconds });
    await app.begin();
    const captured = [], gains = [];
    for (let index = 0; index < Math.ceil((milliseconds + 1_500) / 256); index++) {
      const gain = index < 8 ? 60 : 150;
      app.element("voice-input-gain").value = String(gain);
      app.element("voice-input-gain").dispatch("input");
      captured.push(app.emit(index + 1)); gains.push(gain / 100);
      await settle(); await app.advance(256);
      assert.equal(app.idle(), false, "A normal 2–5 second ACK/status stall must not trigger a fatal timeout");
    }
    await app.advance(1_000);
    assertExactCapturedStream(app, captured, gains);
    app.stop(); await settle();
  }
}

async function fastStatusConfirmsPermanentlySlowACKsAtInputRate() {
  const app = browser({ pcmACKDelayMs: 5_000 });
  await app.begin();
  const captured = [];
  for (let index = 0; index < 80; index++) {
    captured.push(app.emit(index + 1)); await settle(); await app.advance(256);
    assert.equal(app.idle(), false);
    assert.ok(app.server().received.length >= captured.length - 2,
      "Independent status confirmation must keep up with continuous input rather than build a growing ACK queue");
  }
  await app.advance(500);
  assertExactCapturedStream(app, captured);
  assert.equal(app.frames().length, captured.length, "Status confirmation must avoid resending already accepted audio");
  app.stop(); await settle();
}

async function lostAcceptedACKAndTemporaryDisconnectBothRecover() {
  for (const options of [
    { loseFirstACK: true, pollDisconnectUntilMs: 1_000 },
    { pcmDisconnectUntilMs: 5_000, pollDisconnectUntilMs: 5_000 },
  ]) {
    const app = browser(options); await app.begin();
    const captured = [];
    for (let index = 0; index < 28; index++) {
      captured.push(app.emit(index + 1)); await settle(); await app.advance(256);
      assert.equal(app.idle(), false, "A lost ACK or short disconnect must retry within the recovery budget");
    }
    await app.advance(1_000);
    assertExactCapturedStream(app, captured);
    assert.ok(app.frames().length > captured.length, "The recovery case must exercise a real retry");
    app.stop(); await settle();
  }
}

async function concurrentPOSTAndStatusCommitOnlyOnePacket() {
  const app = browser({ deferPCM: true, deferGET: true }); await app.begin();
  const first = app.emit(1), second = app.emit(2); await settle();
  app.pendingPCM[0].resolve();
  app.pendingGET[0].resolve({ session_id: "session-1", generation: 0, next_sequence: 1, stage: "listening" });
  await settle();
  assert.equal(app.pendingPCM.length, 2, "The first packet must commit once despite simultaneous POST and status confirmations");
  assert.deepEqual(app.frames().map(({ options }) => options.headers["X-Audio-Sequence"]), ["0", "1"]);
  app.pendingPCM[1].resolve(); await settle();
  assertExactCapturedStream(app, [first, second]);
  app.stop(); await settle();
}

async function statusCannotConfirmFailedOrInconsistentPCM() {
  const vad = browser({ pcmAcceptError: "vad_unavailable" }); await vad.begin();
  vad.emit(); await settle(); await vad.advance(1_000);
  assert.equal(vad.server().received.length, 1);
  assert.equal(vad.idle(), true, "An incremented server sequence must not turn failed VAD processing into a successful PCM ACK");
  assert.equal(vad.streams[0].stopped, true);

  for (const snapshot of [
    { session_id: "session-1", generation: 0, next_sequence: 2, stage: "listening" },
    { session_id: "session-1", generation: 1, next_sequence: 1, stage: "listening" },
    { session_id: "other-session", generation: 0, next_sequence: 1, stage: "listening" },
  ]) {
    const app = browser({ deferPCM: true, deferGET: true }); await app.begin();
    app.emit(); await settle(); app.pendingGET[0].resolve(snapshot); await settle();
    assert.equal(app.idle(), true, "A status confirmation cannot skip PCM sequence or change session/generation");
    assert.equal(app.streams[0].stopped, true);
  }
}

async function unacceptedAndStaleStatusCannotAdvanceOrRegressPCM() {
  const app = browser({ deferPCM: true, deferGET: true }); await app.begin();
  const first = app.emit(1); await settle();
  app.pendingGET[0].resolve({ session_id: "session-1", generation: 0, next_sequence: 0, stage: "listening" });
  await settle();
  assert.equal(app.pendingPCM[0].signal.aborted, false, "The current sequence means the server has not acknowledged the packet yet");
  assert.equal(app.frames().length, 1);
  app.pendingPCM[0].resolve(); await settle();
  const second = app.emit(2); await settle(); await app.advance(350);
  const before = app.status(), transcript = app.element("voice-transcript").textContent;
  app.pendingGET[1].resolve({ session_id: "session-1", generation: 0, next_sequence: 0, stage: "failed", transcript: "古い認識状態" });
  await settle();
  assert.equal(app.pendingPCM[1].signal.aborted, false, "A stale sequence cannot acknowledge or cancel the next packet");
  assert.equal(app.status(), before, "A status older than the committed PCM sequence cannot regress the current UI");
  assert.equal(app.element("voice-transcript").textContent, transcript);
  app.pendingPCM[1].resolve(); await settle();
  assertExactCapturedStream(app, [first, second]);
  app.stop(); await settle();
}

async function temporaryEventEndpointDisconnectDoesNotStopContinuousInput() {
  const app = browser({ eventDisconnectUntilMs: 5_000 }); await app.begin();
  app.emitEvent({ type: "transcript", turn_id: "turn-1", text: "再接続後に届く発話" });
  app.emitEvent({ type: "text_delta", turn_id: "turn-1", text: "保持された返答" });
  const captured = [];
  for (let index = 0; index < 28; index++) {
    captured.push(app.emit(index + 1)); await settle(); await app.advance(256);
    assert.equal(app.idle(), false, "Five seconds of retryable reply connection failures must preserve microphone input");
  }
  assertExactCapturedStream(app, captured);
  assert.ok(app.stats.eventAttempts > 4, "Recovery must outlive the removed four-attempt reply limit");
  assert.ok(app.eventConnections.length > 0);
  assert.equal(app.element("voice-transcript").textContent, "再接続後に届く発話");
  assert.equal(app.element("voice-response").textContent, "保持された返答");
  app.stop(); await settle();
}

async function delayedACKDoesNotCrossBargeInOrStopFences() {
  const app = browser({ pcmACKBlockedUntilMs: 5_000, pollDisconnectUntilMs: 5_000 });
  await app.begin();
  const captured = [app.emit(1)]; await settle();
  app.emitEvent({ type: "transcript", turn_id: "turn-1", text: "前の発話" });
  app.emitEvent({ type: "interruption", turn_id: "turn-1", interrupted_turn_id: "turn-1", new_turn_id: "turn-2" });
  app.emitEvent({ type: "transcript", turn_id: "turn-2", text: "新しい発話" }); await settle();
  for (let index = 0; index < 24; index++) { captured.push(app.emit(index + 2)); await settle(); await app.advance(256); }
  await app.advance(500);
  assertExactCapturedStream(app, captured);
  assert.equal(app.element("voice-transcript").textContent, "新しい発話",
    "Committing a delayed PCM ACK must not regress the UI to its pre-interruption snapshot");
  app.stop(); await settle();

  const stopped = browser({ deferPCM: true, ignorePCMAbort: true, deferGET: true, ignoreGETAbort: true });
  await stopped.begin(); stopped.emit(1); await settle(); await stopped.advance(2_000);
  assert.equal(stopped.idle(), false, "A pending PCM request must survive longer than the removed 1750ms deadline");
  const oldPCM = stopped.pendingPCM[0], oldStatus = stopped.pendingGET[0];
  stopped.stop(); await settle(); await stopped.begin();
  const next = stopped.emit(2); await settle();
  oldPCM.resolve(); oldStatus.resolve({ session_id: "session-1", generation: 0, next_sequence: 1, stage: "failed", transcript: "旧文字" });
  await settle();
  assert.equal(stopped.idle(), false); assert.equal(stopped.streams.at(-1).stopped, false);
  stopped.pendingPCM.at(-1).resolve(); await settle();
  assert.deepEqual(capturedBytes(stopped), Buffer.from(next));
  assert.deepEqual(stopped.frames().filter(({ url }) => url.includes("session-2")).map(({ options }) => options.headers["X-Audio-Sequence"]), ["0"]);
  stopped.stop(); await settle();
}

async function obsoleteEndedRetryDoesNotStopTheNextUtterance() {
  const app = browser({ enforceControlTurn: true, loseFirstEndedACK: true, endedACKLossDelayMs: 800 });
  await app.begin();
  app.emitEvent(audio());
  app.emitEvent({ type: "complete", turn_id: "turn-1", transcript: "前の発話", response_text: "前の返答" });
  await settle();
  app.element("voice-audio").finish(); await settle();
  assert.equal(app.stats.lostEndedACKs, 1);
  assert.equal(app.server().stage, "cooldown", "The server has accepted ended even though its ACK is lost");
  for (let index = 0; index < 3; index++) { app.emit(); await settle(); await app.advance(256); }
  assert.equal(app.server().stage, "listening", "The accepted ended notification must let the server reach the next utterance");
  app.emitEvent({ type: "transcript", turn_id: "turn-2", text: "次の発話" });
  app.emitEvent({ ...audio(), turn_id: "turn-2" });
  await settle();
  assert.equal(controls(app).at(-1).turn_id, "turn-1", "The next started control must queue behind the old ended retry");
  for (let index = 0; index < 4; index++) { app.emit(); await settle(); await app.advance(256); }
  assert.equal(app.idle(), false, "A completed turn's obsolete ended retry must not stop the current PCM session");
  assert.equal(app.streams[0].stopped, false);
  assert.equal(app.element("voice-transcript").textContent, "次の発話");
  assert.deepEqual(controls(app).map(({ turn_id, event }) => [turn_id, event]), [
    ["turn-1", "started"], ["turn-1", "ended"], ["turn-1", "ended"], ["turn-2", "started"],
  ], "After the obsolete retry is discarded, control FIFO must advance to the current turn");
  assert.deepEqual(app.server().playback.map(({ turn_id, event }) => [turn_id, event]), [
    ["turn-1", "started"], ["turn-1", "ended"], ["turn-2", "started"],
  ], "The old ended retry must be rejected by the server rather than applied to the new turn");
  assert.equal(app.frames().length, 7);
  assert.deepEqual(app.frames().map(({ options }) => options.headers["X-Audio-Sequence"]), ["0", "1", "2", "3", "4", "5", "6"]);
  assert.equal(app.stats.maxPCMInFlight, 1);
  assert.equal(app.stats.maxControlInFlight, 1);
  app.stop(); await settle();
}

async function onlyCompletedTurnMismatchIsRecoverable() {
  const failures = [
    { complete: false, status: 409, code: "playback_turn_mismatch" },
    { complete: true, status: 409, code: "playback_not_ready" },
    { complete: true, status: 404, code: "session_not_found" },
    { complete: true, generation: 1 },
  ];
  for (const failure of failures) {
    const app = browser({ deferControl: true });
    await app.begin();
    app.emitEvent(audio()); await settle();
    if (failure.complete) {
      app.emitEvent({ type: "complete", turn_id: "turn-1", response_text: "返答" });
      await settle(); app.element("voice-audio").finish(); await settle();
    }
    if (failure.generation !== undefined) {
      app.pendingControls[0].resolve({ session_id: "session-1", generation: failure.generation, next_sequence: 0, stage: "listening" });
    } else {
      app.pendingControls[0].reject(Object.assign(new Error("Fatal playback control response"), {
        status: failure.status, code: failure.code,
      }));
    }
    await settle();
    assert.equal(app.idle(), true, `Fatal control failure must still stop the session: ${JSON.stringify(failure)}`);
    assert.equal(app.streams[0].stopped, true);
  }
}

(async () => {
  await slowPollingDoesNotHoldPCM();
  await slowPlaybackNotificationsStayFIFOWhilePCMFlows();
  await transientSideFailuresDoNotStopPCM();
  await olderSameGenerationPollCannotOverwritePCMOrEvents();
  await olderControlSnapshotCannotOverwriteNewEvents();
  await stoppedSideRequestsCannotAffectTheNextSession();
  await slowACKAndStatusRecoverWithoutDroppingContinuousInput();
  await fastStatusConfirmsPermanentlySlowACKsAtInputRate();
  await lostAcceptedACKAndTemporaryDisconnectBothRecover();
  await concurrentPOSTAndStatusCommitOnlyOnePacket();
  await statusCannotConfirmFailedOrInconsistentPCM();
  await unacceptedAndStaleStatusCannotAdvanceOrRegressPCM();
  await temporaryEventEndpointDisconnectDoesNotStopContinuousInput();
  await delayedACKDoesNotCrossBargeInOrStopFences();
  await pcmDeadlineIncludesUncooperativeFetchAndJSONRead();
  await obsoleteEndedRetryDoesNotStopTheNextUtterance();
  await onlyCompletedTurnMismatchIsRecoverable();
  console.log("Independent transport lanes checked: slow/failed polls and playback, FIFO controls, serial PCM, snapshot/stop fences, absolute fetch/JSON deadline and obsolete ended retries");
})().catch((error) => { console.error(error); process.exitCode = 1; });
