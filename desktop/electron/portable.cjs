const path = require('node:path');

function portableDataDirectory(packaged, env = process.env) {
  if (env.SENTINEL_DESKTOP_DATA) return path.resolve(env.SENTINEL_DESKTOP_DATA);
  if (packaged && env.PORTABLE_EXECUTABLE_DIR) {
    if (!path.isAbsolute(env.PORTABLE_EXECUTABLE_DIR)) throw Error('Portable directory must be absolute');
    return path.join(env.PORTABLE_EXECUTABLE_DIR, 'SentinelData');
  }
  return null;
}

function decryptCredentials(vault, decrypt) {
  const keys = [], unavailable = [];
  for (const [name, value] of Object.entries(vault)) {
    try { keys.push({ name, key: decrypt(Buffer.from(value, 'base64')) }); }
    catch { unavailable.push(name); }
  }
  return { keys, unavailable };
}
module.exports = { portableDataDirectory, decryptCredentials };
