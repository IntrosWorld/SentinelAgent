const fs = require('node:fs');
const path = require('node:path');
const { randomUUID } = require('node:crypto');
const { spawnSync } = require('node:child_process');
const assert = require('node:assert/strict');
// The portable launcher does not forward its child's stdout. Read the smoke
// marker from a fresh per-test directory, not a stale result or launcher code.
const root = path.resolve(__dirname, '../.test-data/portable-' + randomUUID());
const result = spawnSync(path.resolve(__dirname, '../release/Sentinel-Desktop-0.2.0-Windows.exe'), ['--smoke-test'], {
  env: { ...process.env, SENTINEL_DESKTOP_DATA: root }, encoding: 'utf8', timeout: 60000,
});
assert.equal(result.status, 0, 'Portable launcher failed or timed out');
const evidence = JSON.parse(fs.readFileSync(path.join(root, 'smoke-result.json'), 'utf8'));
assert.equal(evidence.smoke, 'passed');
assert.equal(evidence.packaged, true);
console.log('PASS: portable launcher, sandboxed renderer IPC, bundled Python runtime, model registry.', evidence);
