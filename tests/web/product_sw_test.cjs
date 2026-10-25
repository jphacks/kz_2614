const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../../apps/hossy-web/public/sw.js'), 'utf8');
function worker(fetcher, initial = new Map()) {
  const listeners = {}, pending = [], timers = new Map(), writes = [], deletions = [];
  const cache = {
    match: async key => initial.get(key)?.clone(),
    put: async (key, value) => { writes.push(key); initial.set(key, value.clone()); },
    delete: async key => initial.delete(key),
  };
  let nextTimer = 0;
  vm.runInNewContext(source, { URL, Response, AbortController,
    setTimeout: fn => { timers.set(++nextTimer, fn); return nextTimer; }, clearTimeout: id => timers.delete(id),
    self: { location: { origin: 'https://app.test' }, addEventListener: (name, fn) => listeners[name] = fn,
      skipWaiting: async()=>{}, clients:{claim:async()=>{}} },
    caches: { open: async()=>cache, keys: async()=>['nengitsune-shell-v6', 'unrelated-app', 'nengitsune-shell-v7-mvp', 'nengitsune-shell-v8-mvp-stop'], delete: async name=>deletions.push(name) },
    fetch: fetcher,
  });
  return { writes, deletions, timers, cache,
    request(url, method='GET') {
      let result;
      listeners.fetch({ request: { url: 'https://app.test'+url, method }, respondWith: promise=>result=promise, waitUntil: promise=>pending.push(promise) });
      return result;
    },
    async activate() { let result; listeners.activate({waitUntil:promise=>result=promise}); await result; },
    async install() { let result; listeners.install({waitUntil:promise=>result=promise}); await result; },
    async flush() { await Promise.all(pending); },
  };
}
const home = () => new Response('<main id="main-content"><section id="screen-home"></section></main>', {headers:{'Content-Type':'text/html'}});
const initial = () => new Map([['/',home()]]);
(async()=>{
  const offline=worker(async()=>{throw new Error('offline')}, initial());
  for(const url of ['/api/v1/voice/status','/api/v1/voice/sessions/a/audio-stream/events','/api/v1/dashboard/runtime','/login','/?code=secret','/speech/a.wav']) {
    assert.equal(offline.request(url),undefined,'must not intercept '+url);
  }
  assert.equal(offline.request('/app.js','POST'),undefined);
  assert.equal((await offline.request('/')).status,200);
  const unavailable=worker(async()=>new Response('unavailable',{status:503}),initial());
  assert.equal((await unavailable.request('/')).status,200);
  const blocked=worker(()=>new Promise(()=>{}),initial());
  const response=blocked.request('/');await new Promise(setImmediate);
  for(const fn of [...blocked.timers.values()])fn();
  assert.equal((await response).status,200);
  for(const status of [401,403]){
    const unauthorized=worker(async()=>new Response('login',{status}),initial());
    assert.equal((await unauthorized.request('/')).status,status);
    assert.equal(await unauthorized.cache.match('/'),undefined);
  }
  const login=worker(async()=>new Response('<form>login</form>',{headers:{'Content-Type':'text/html'}}),initial());
  await login.request('/');assert.equal(login.writes.length,0);assert.equal(await login.cache.match('/'),undefined);
  const redirected=worker(async()=>{const response=home();Object.defineProperty(response,'redirected',{value:true});return response},initial());
  await redirected.request('/');assert.equal(redirected.writes.length,0);assert.equal(await redirected.cache.match('/'),undefined);
  const good=worker(async()=>home());await good.request('/');await good.flush();assert.deepEqual(good.writes,['/']);
  await good.activate();assert.deepEqual(good.deletions,['nengitsune-shell-v6', 'nengitsune-shell-v7-mvp']);
  const badInstall=worker(async()=>new Response('<form>login</form>',{headers:{'Content-Type':'text/html'}}));
  await assert.rejects(()=>badInstall.install(),/application_shell_unavailable/);
  const empty=worker(async()=>{throw new Error('offline')});
  assert.equal((await empty.request('/')).status,503);
  assert.equal((await empty.request('/voice_pcm_worklet.js')).type,'error');
  console.log('Product SW: API/auth bypass, offline/503/deadline fallback, auth eviction, login rejection, cache cleanup passed');
})().catch(error=>{console.error(error);process.exitCode=1});
