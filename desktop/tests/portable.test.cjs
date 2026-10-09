const { test } = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const { portableDataDirectory, decryptCredentials } = require('../electron/portable.cjs');

test('portable data follows the launcher, including spaces', () => {
  const folder = path.resolve('USB drive/Sentinel');
  assert.equal(portableDataDirectory(true, { PORTABLE_EXECUTABLE_DIR: folder }), path.join(folder, 'SentinelData'));
});
test('development retains its default and explicit data overrides take priority', () => {
  assert.equal(portableDataDirectory(false, { PORTABLE_EXECUTABLE_DIR: path.resolve('usb') }), null);
  assert.equal(portableDataDirectory(true, {}), null);
  assert.equal(portableDataDirectory(true, { SENTINEL_DESKTOP_DATA: './test', PORTABLE_EXECUTABLE_DIR: path.resolve('usb') }), path.resolve('test'));
  assert.throws(() => portableDataDirectory(true, { PORTABLE_EXECUTABLE_DIR: 'relative' }));
});
test('machine-bound keys do not prevent startup after moving the app', () => {
  const vault = { local: Buffer.from('good').toString('base64'), moved: Buffer.from('foreign').toString('base64') };
  const result = decryptCredentials(vault, value => {
    if (value.toString() === 'foreign') throw Error('Different Windows account');
    return 'test-key';
  });
  assert.deepEqual(result.keys, [{ name: 'local', key: 'test-key' }]);
  assert.deepEqual(result.unavailable, ['moved']);
  assert.equal(Object.keys(vault).length, 2); // Preserve originals; no destructive migration.
});
