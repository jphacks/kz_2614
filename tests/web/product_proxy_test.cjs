const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {spawnSync} = require('node:child_process');
const root = path.resolve(__dirname,'../..');
const temp = fs.mkdtempSync(path.join(os.tmpdir(),'nengitsune-proxy-'));
try {
  const config = path.join(temp,'voice.inc');
  function generate(target) {
    return spawnSync('sh',[path.join(root,'apps/hossy-web/nginx/40-voice-proxy.sh')],{
      env:{...process.env,VOICE_API_UPSTREAM:target,VOICE_PROXY_CONFIG_FILE:config},encoding:'utf8',
    });
  }
  assert.equal(generate('').status,0);
  assert.match(fs.readFileSync(config,'utf8'),/return 503/);
  for(const target of ['http://voice.test:8765','https://voice.test']) {
    assert.equal(generate(target).status,0);
    const text=fs.readFileSync(config,'utf8');
    assert.ok(text.includes('proxy_pass '+target+';'));
    assert.match(text,/proxy_intercept_errors off/);
    assert.match(text,/error_page 502 = @voice_unreachable/);
    assert.match(text,/error_page 504 = @voice_timeout/);
    assert.match(text,/proxy_buffering off/);assert.match(text,/proxy_ssl_verify on/);
  }
  for(const target of ['http://user:secret@host','http://host/path','http://host;return 200','http://host\nreturn 200;']) {
    assert.notEqual(generate(target).status,0);
  }
  const locations = fs.readFileSync(path.join(root, 'apps/hossy-web/nginx/default.conf'), 'utf8');
  assert.match(locations, /location @voice_unreachable[\s\S]*return 502/);
  assert.match(locations, /location @voice_timeout[\s\S]*return 504/);
  console.log('Product proxy: disabled default, HTTP/HTTPS streaming configuration, invalid origins rejected');
} finally { fs.rmSync(temp,{recursive:true,force:true}); }
