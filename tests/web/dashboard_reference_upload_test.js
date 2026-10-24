"use strict";
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../../apps/web/dashboard.js'), 'utf8');
function setup() {
  const elements = new Map();
  function element(id) {
    if (!elements.has(id)) elements.set(id, {
      value: '', textContent: '', disabled: false, files: [], type: 'text', listeners: {},
      classList: {add(){}, remove(){}, contains(){return false;}},
      addEventListener(type, fn) {this.listeners[type] = fn;},
      reportValidity(){return true;},
      setCustomValidity(){},
    });
    return elements.get(id);
  }
  const fields = [];
  fields.namedItem = element;
  element('settings-form').elements = fields;
  const requests = [];
  let fail = false;
  const context = vm.createContext({
    document: {getElementById: element, addEventListener(){}},
    window: {confirm:()=>true, addEventListener(){}}, console,
    fetch: async (url, options) => {
      requests.push({url, options});
      if (url.endsWith('reference-audio') && options.method === 'PUT') {
        return {ok: !fail, status: fail ? 400 : 200, json: async()=>fail ? {message:'16-bit PCMを選択してください。'} :
          {uploaded:true,name:'reference_voice.wav',size_bytes:32044,duration_seconds:1,sample_rate:16000,channels:1}};
      }
      return {ok:true,status:200,json:async()=>({settings:{},defaults:{},uploaded:false})};
    },
  });
  // Avoid unrelated initial network loads; exercise the actual production handlers.
  vm.runInContext(source.replace(/checkHealth\(\);\s*loadSettings\(\);\s*loadRuntimeStatus\(\);\s*$/, ''), context);
  vm.runInContext('displaySettings(currentDefaults); loadRuntimeStatus = async () => {};', context);
  const select = async file => {
    element('reference-audio-file').files = [file];
    await element('reference-audio-file').listeners.change();
  };
  return {context, element, requests, select, setFail: v=>fail=v};
}
(async()=>{
  let app = setup();
  const file = {name:'reference.wav',size:32044};
  await app.select(file);
  assert.equal(app.element('reference-audio-state').textContent,'未保存');
  await app.element('settings-form').listeners.submit({preventDefault(){}});
  assert.equal(app.requests.filter(r=>r.url.endsWith('reference-audio') && r.options.method==='PUT').length,1);
  assert.equal(app.requests.find(r=>r.url.endsWith('reference-audio')).options.body,file);
  assert.equal(app.element('reference-audio-state').textContent,'登録済み');
  assert.equal(app.element('upload-reference-audio').disabled,true);

  app = setup(); await app.select(file); app.setFail(true);
  await app.element('settings-form').listeners.submit({preventDefault(){}});
  assert.equal(app.element('reference-audio-state').textContent,'保存失敗');
  assert.match(app.element('reference-audio-info').textContent,/16-bit/);
  assert.equal(app.element('upload-reference-audio').disabled,false);
  assert.doesNotMatch(app.element('save-message').textContent,/次の会話から反映/);
  app.setFail(false);
  await app.element('upload-reference-audio').listeners.click();
  assert.equal(app.element('reference-audio-state').textContent,'登録済み');

  app = setup(); await app.select({name:'large.wav',size:20*1024*1024});
  await app.element('upload-reference-audio').listeners.click();
  assert.equal(app.element('reference-audio-state').textContent,'登録済み');
  app = setup(); await app.select({name:'large.wav',size:101*1024*1024});
  await app.element('upload-reference-audio').listeners.click();
  assert.equal(app.requests.length,0);
  assert.match(app.element('reference-audio-info').textContent,/100MB/);

  app = setup(); await app.select({name:'wrong.mp3',size:100});
  await app.element('settings-form').listeners.submit({preventDefault(){}});
  assert.equal(app.requests.filter(r=>r.url.endsWith('reference-audio')).length,0);
  assert.equal(app.element('reference-audio-state').textContent,'保存失敗');
  console.log('Reference upload: combined save, failure visibility, retry and format rejection passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
