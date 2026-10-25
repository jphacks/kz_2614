"use strict";
const { product, settle, assert } = require('./product_browser_harness');
const PREFIX = 'nengitsune-session-v2:';
async function run() {
  const shared = new Map();
  const a = product({ productStorage: shared, sessionPrefix: 'a' });
  const b = product({ productStorage: shared, sessionPrefix: 'b' });
  await a.begin(); await b.begin(); a.stop(); await settle();
  b.element('duration').value = '45'; b.element('duration').dispatch('input');
  assert.equal(a.records().length, 1, 'A stale settings save must not replace another tab diary');
  b.stop(); await settle();
  assert.equal(a.records().length, 2, 'Independent session keys preserve both tab completions');
  a.windowEvent('storage', { key: `${PREFIX}b-1` });
  assert.equal(a.eval('state.sessions.length'), 2);
  assert.ok(!JSON.parse(shared.get('nengitsune-settings-v2')).sessions);
  assert.ok(!JSON.parse(shared.get('nengitsune-settings-v2')).sessionStart);
  const reload = product({ productStorage: shared });
  assert.equal(reload.stats.starts, 0); assert.equal(reload.eval('state.sessionStart'), null);
  assert.equal(reload.eval('state.sessions.length'), 2);

  const failed = product(); await failed.begin(); failed.productEnvironment.saveFails = true; failed.stop(); await settle();
  assert.equal(failed.records().length, 0);
  assert.equal(failed.eval('state.sessions.length'), 1, 'Unsaved diary remains visible in this page');
  assert.equal(failed.element('storage-status').hidden, false);
  assert.ok(failed.element('storage-status').textContent.includes('保存できません'));
  assert.ok(failed.element('.toast').textContent.includes('保存できません'));
  assert.ok(failed.element('diary-source').textContent.includes('未保存'));
  failed.element('duration').value = '15'; failed.element('duration').dispatch('input');
  assert.ok(failed.element('storage-status').textContent.includes('保存できません'));

  const changed = product(); changed.element('duration').value = '15'; changed.element('duration').dispatch('input'); await changed.begin();
  const start = changed.eval('activeSession.start');
  changed.element('duration').value = '90'; changed.element('duration').dispatch('input');
  assert.equal(changed.eval('activeSession.deadlineAt'), start + 15 * 60000);
  changed.environment.now = 15 * 60000 + 5000;
  changed.eval('updateSession()'); await settle();
  assert.equal(changed.streams[0].stopped, true);
  assert.equal(changed.records()[0].end, start + 15 * 60000 + 5000);
  assert.equal(changed.records()[0].deadlineAt, start + 15 * 60000);
  assert.equal(changed.records()[0].reason, 'deadline');
  changed.stop(); changed.eval('updateSession()'); await settle(); assert.equal(changed.records().length, 1);

  const manualRace = product(); await manualRace.begin(); const manualStart = manualRace.eval('activeSession.start');
  manualRace.environment.now = 31 * 60000; manualRace.stop(); await settle();
  assert.equal(manualRace.records()[0].end, manualStart + 31 * 60000);
  assert.equal(manualRace.records()[0].deadlineAt, manualStart + 30 * 60000);
  assert.equal(manualRace.records()[0].reason, 'manual', 'A delayed timer must not rewrite a manual stop as a deadline stop');
  const errorRace = product(); await errorRace.begin(); const errorStart = errorRace.eval('activeSession.start');
  errorRace.environment.now = 31 * 60000; errorRace.streams[0].disconnectDevice(); await settle();
  assert.equal(errorRace.records()[0].end, errorStart + 31 * 60000);
  assert.equal(errorRace.records()[0].deadlineAt, errorStart + 30 * 60000);
  assert.equal(errorRace.records()[0].reason, 'error');

  const legacy = new Map([['nengitsune-ui', JSON.stringify({ sessionStart: 100, duration: 15, sessions: [{ start: 1000, end: 2000 }] })]]);
  const migrated = product({ productStorage: legacy });
  assert.equal(migrated.stats.starts, 0); assert.equal(migrated.eval('state.sessionStart'), null);
  migrated.element('duration').value = '45'; migrated.element('duration').dispatch('input');
  assert.equal(migrated.eval('state.sessions.length'), 1);
  assert.equal(JSON.parse(legacy.get('nengitsune-ui')).sessions.length, 1, 'Legacy records remain intact during settings migration');

  const historical = new Map();
  for (let i = 0; i < 101; i++) historical.set(PREFIX + i, JSON.stringify({ start: 1000 + i, end: 2000 + i }));
  const bounded = product({ productStorage: historical }); await bounded.begin(); bounded.stop(); await settle();
  assert.equal(bounded.records().length, 100); assert.equal(bounded.eval('state.sessions.length'), 100);
  assert.equal([...historical.keys()].filter((key) => key.startsWith(PREFIX)).length, 100, 'Pruning must remove actual keys, not merely truncate display');
  const mixed = new Map(historical);
  const legacyRecords = Array.from({ length: 4 }, (_, index) => ({ start: Date.now() + index, end: Date.now() + index + 1000 }));
  const legacyJSON = JSON.stringify({ sessions: legacyRecords });
  mixed.set('nengitsune-ui', legacyJSON);
  mixed.set(PREFIX + 'extra', JSON.stringify({ start: 1000, end: 2000 }));
  const mixedApp = product({ productStorage: mixed, sessionPrefix: 'mixed' }); await mixedApp.begin(); mixedApp.stop(); await settle();
  assert.equal([...mixed.keys()].filter((key) => key.startsWith(PREFIX)).length, 100, 'Legacy rows must not displace immutable-record retention accounting');
  assert.equal(mixed.get('nengitsune-ui'), legacyJSON);
  assert.equal(mixedApp.eval('state.sessions.length'), 100);

  const racingStorage = new Map();
  for (let i = 0; i < 100; i++) racingStorage.set(PREFIX + i, JSON.stringify({ start: 1000 + i, end: 2000 + i }));
  const racingA = product({ productStorage: racingStorage, sessionPrefix: 'race-a' });
  const racingB = product({ productStorage: racingStorage, sessionPrefix: 'race-b' });
  await racingA.begin(); await racingB.begin();
  racingA.productEnvironment.beforeRemove = () => { racingA.productEnvironment.beforeRemove = null; racingB.stop(); };
  racingA.stop(); await settle();
  assert.ok(racingStorage.has(PREFIX + 'race-a-1'));
  assert.ok(racingStorage.has(PREFIX + 'race-b-1'), 'A concurrent immutable record must survive an earlier pruning snapshot');
  assert.equal([...racingStorage.keys()].filter((key) => key.startsWith(PREFIX)).length, 100);
  console.log('Product storage checked: stale-tab settings, independent completions, storage sync, permission/reload safety, failed-save visibility, frozen deadline, actual manual/error/deadline timestamps, legacy coexistence, actual 100-key retention and concurrent prune');
}
run().catch((error) => { console.error(error); process.exitCode = 1; });
