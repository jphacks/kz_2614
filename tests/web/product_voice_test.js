"use strict";
const { product, settle, assert } = require('./product_browser_harness');
async function run() {
  const app = product();
  assert.equal(app.stats.starts, 0, 'Loading/reloading the product must never start recording');
  await app.begin();
  assert.equal(app.productEnvironment.statusRequests, 1);
  assert.ok(app.element('voice-api-status').textContent.includes('最初の発話'));
  assert.equal(app.records().length, 0, 'Ready is not a finished diary entry');
  app.emit(0, 0.2);
  app.emitEvent({ type: 'transcript', turn_id: 'turn-1', text: '途中文字' });
  app.emitEvent({ type: 'text_delta', turn_id: 'turn-1', text: '途中の返答' });
  await settle();
  assert.equal(app.element('voice-transcript').textContent, '途中文字');
  assert.ok(app.element('voice-response').textContent.includes('途中の返答'));
  assert.ok(app.element('voice-input-level').value > 0);
  app.stop(); await settle();
  assert.equal(app.records().length, 1);
  assert.equal(app.records()[0].reason, 'manual');
  assert.equal(app.streams[0].stopped, true);
  assert.ok(app.element('voice-stop-result').textContent.includes('停止応答'));
  assert.ok(!JSON.stringify([...app.productStorage]).includes('途中文字'), 'Transcript must stay in memory');
  assert.ok(!JSON.stringify([...app.productStorage]).includes('途中の返答'), 'Reply must stay in memory');
  app.visibility('hidden'); app.windowEvent('pagehide'); await settle();
  assert.equal(app.records().length, 1, 'Repeated terminal events must not duplicate diary entries');

  for (const statusKind of ['html', 'redirect', 'unauthorized', 'forbidden', 'network', 'invalid-json', 'wrong-shape', 'unavailable']) {
    const failed = product({ statusKind }); await failed.begin();
    assert.equal(failed.stats.starts, 0, `${statusKind}: must reject before session POST`);
    assert.equal(failed.stats.microphoneRequests, 0);
    assert.equal(failed.records().length, 0);
    assert.ok(failed.element('voice-call-status').textContent);
  }
  const denied = product({ deferMicrophone: true }); await denied.begin();
  assert.equal(denied.records().length, 0);
  denied.pendingMicrophones[0].reject(Object.assign(new Error('Denied'), { name: 'NotAllowedError' }));
  await settle();
  assert.equal(denied.records().length, 0, 'Permission rejection must not manufacture a timer session');
  assert.ok(denied.status().includes('許可されていません'));

  const hidden = product(); await hidden.begin(); hidden.visibility('hidden'); await settle();
  assert.equal(hidden.records()[0].reason, 'hidden');
  assert.equal(hidden.streams[0].stopped, true);
  hidden.windowEvent('pagehide'); await settle(); assert.equal(hidden.records().length, 1);

  const late = product({ deferMicrophone: true }); await late.begin(); late.stop();
  const stream = late.pendingMicrophones[0].resolve(); await settle();
  assert.equal(stream.stopped, true); assert.equal(late.records().length, 0);
  const lateStart = product({ deferStarts: 1 }); await lateStart.begin(); lateStart.visibility('hidden');
  lateStart.pendingStarts[0].resolve(); await settle();
  assert.equal(lateStart.stats.microphoneRequests, 0); assert.equal(lateStart.records().length, 0);

  const failedCapture = product(); await failedCapture.begin(); failedCapture.streams[0].disconnectDevice(); await settle();
  assert.equal(failedCapture.records()[0].reason, 'error');
  const pendingDelete = product({ deferDeletes: true }); await pendingDelete.begin(); pendingDelete.stop();
  assert.equal(pendingDelete.records().length, 1, 'Diary termination must not wait for remote DELETE');
  assert.equal(pendingDelete.streams[0].stopped, true);
  pendingDelete.pendingDeletes[0].reject(); await settle();
  assert.ok(pendingDelete.element('voice-stop-result').textContent.includes('確認できません'));

  for (const deleteKind of ['html', 'redirect', 'invalid-json', 'wrong-session']) {
    const badStop = product({ deleteKind }); await badStop.begin(); badStop.stop(); await settle();
    assert.equal(badStop.streams[0].stopped, true);
    assert.equal(badStop.records().length, 1);
    assert.ok(badStop.element('voice-stop-result').textContent.includes('確認できません'), `${deleteKind}: reject untrusted stop response`);
  }
  for (const deleteKind of ['pending', 'pending-json']) {
    const timeoutStop = product({ deleteKind }); await timeoutStop.begin(); timeoutStop.stop(); await settle();
    assert.equal(timeoutStop.records().length, 1);
    await timeoutStop.advance(5000);
    assert.equal(timeoutStop.productEnvironment.deleteSignal.aborted, true);
    assert.ok(timeoutStop.element('voice-stop-result').textContent.includes('確認できません'), `${deleteKind}: bounded DELETE including body parsing`);
  }
  for (const statusKind of ['pending', 'pending-json']) {
    const cancelled = product({ statusKind }); await cancelled.begin();
    assert.equal(cancelled.stats.starts, 0);
    assert.equal(cancelled.stats.resumes, 1, 'AudioContext must be resumed in the user click before network preflight');
    cancelled.stop(); await settle();
    assert.equal(cancelled.productEnvironment.statusSignal.aborted, true);
    assert.ok(cancelled.element('voice-stop-result').textContent.includes('取り消しました'));
    assert.equal(cancelled.eval('activeSession'), null);
    assert.equal(cancelled.stats.starts, 0);
    const stableStatus = cancelled.element('voice-api-status').textContent;
    if (statusKind === 'pending') cancelled.productEnvironment.resolveStatus({ ok: true, status: 200, headers: { get: () => 'application/json' }, json: async () => ({}) });
    else cancelled.productEnvironment.resolveStatusJSON({ voice_api: 'available' });
    await settle();
    assert.equal(cancelled.element('voice-api-status').textContent, stableStatus, 'Late preflight results cannot replace cancellation status');
    assert.equal(cancelled.stats.starts, 0);
    cancelled.productEnvironment.statusKind = 'ok'; await cancelled.begin();
    assert.equal(cancelled.stats.starts, 1, 'Preflight cancellation must allow immediate manual restart');
  }
  console.log('Product voice checked: explicit start, API/auth gate, capture readiness, partial text, reply, level, manual/hidden/error stop, late capture/start, local and remote stop results, no conversation persistence, bounded session-specific DELETE, HTML/redirect/JSON rejection, preflight cancellation');
}
run().catch((error) => { console.error(error); process.exitCode = 1; });
