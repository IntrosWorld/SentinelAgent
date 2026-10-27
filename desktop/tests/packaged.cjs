const fs = require('node:fs');
const path = require('node:path');
const { randomUUID } = require('node:crypto');
const { spawnSync } = require('node:child_process');
const assert = require('node:assert/strict');
// The portable launcher does not forward its child's stdout. Read the smoke
// marker from a fresh per-test directory, not a stale result or launcher code.
const folder = path.resolve(__dirname, '../.test-data/portable folder ' + randomUUID());
fs.mkdirSync(folder, { recursive: true });
const executable = path.join(folder, 'Sentinel.exe');
fs.copyFileSync(path.resolve(__dirname, '../release/Sentinel-Desktop-' + require('../package.json').version + '-Windows.exe'), executable);
const root = path.join(folder, 'SentinelData');
const env = { ...process.env };
delete env.SENTINEL_DESKTOP_DATA;
delete env.PORTABLE_EXECUTABLE_DIR;
const result = spawnSync(executable, ['--smoke-test'], {
  // Cold extraction plus Windows scanning can exceed one minute. A fresh
  // smoke marker below remains mandatory; waiting alone is never success.
  env, encoding: 'utf8', timeout: 180000,
});
assert.equal(result.status, 0, 'Portable launcher failed or timed out');
const evidence = JSON.parse(fs.readFileSync(path.join(root, 'smoke-result.json'), 'utf8'));
assert.equal(evidence.smoke, 'passed');
assert.equal(evidence.packaged, true);
assert.equal(fs.statSync(path.join(root, 'agent')).isDirectory(), true);
console.log('PASS: relocated portable executable, adjacent SentinelData, renderer IPC, bundled Python runtime.', evidence);
