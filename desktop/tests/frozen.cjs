// Integration check without Python or Ollama on PATH; no native UI input.
const { RuntimeBridge } = require('../electron/runtime.cjs');
const path = require('node:path');
const fs = require('node:fs');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '../.test-data/frozen');
fs.mkdirSync(root, { recursive: true });
const bridge = new RuntimeBridge({
  command: path.resolve(__dirname, '../runtime/sentinel-runtime/sentinel-runtime.exe'),
  args: ['--data-dir', root], cwd: root,
  env: { ...process.env, PATH: process.env.SystemRoot + '\\System32', PYTHONPATH: '', PYTHONHOME: '' }
});
(async () => {
  try {
    assert.equal((await bridge.request('status')).version, require('../package.json').version);
    const completed = new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(Error('Document timed out')), 15000);
      bridge.on('event', e => {
        if (e.event === 'approval') bridge.request('run.approve', {run_id:e.run_id,token:e.token,approved:true}).catch(reject);
        if (e.event === 'finished') { clearTimeout(timer); resolve(e.result); }
      });
    });
    await bridge.request('run.start', { goal: 'create frozen-smoke.pdf with content Real bundled runtime output', mode: 'documents' });
    const result = await completed;
    assert.equal(result.status, 'completed');
    assert(result.artifacts[0].verified);
    assert(fs.statSync(path.join(root,'artifacts','frozen-smoke.pdf')).size > 100);
    console.log('PASS: frozen runtime creates a verified real PDF without Python/Ollama on PATH.');
  } finally { bridge.close(); }
})().catch(e => { console.error(e); process.exitCode = 1; });
