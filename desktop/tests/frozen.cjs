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
      const timer = setTimeout(() => reject(Error('Demo timed out')), 15000);
      bridge.on('event', e => { if (e.event === 'finished') { clearTimeout(timer); resolve(e.result); } });
    });
    await bridge.request('run.start', { goal: 'Check attendance and create a report', mode: 'demo' });
    assert.equal((await completed).status, 'completed');
    console.log('PASS: frozen runtime status and deterministic demo without Python/Ollama on PATH.');
  } finally { bridge.close(); }
})().catch(e => { console.error(e); process.exitCode = 1; });
