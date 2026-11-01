const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { pathToFileURL } = require("node:url");
const { permittedResource } = require("../electron/file-preview.cjs");
test("preview permits only local allowlisted resources inside its output folder", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "sentinel-preview-"));
  fs.writeFileSync(path.join(root, "index.html"), "<h1>Hello</h1>");
  fs.writeFileSync(path.join(root, "secret.txt"), "private");
  assert.equal(permittedResource(pathToFileURL(path.join(root, "index.html")).href, root), true);
  assert.equal(permittedResource(pathToFileURL(path.join(root, "secret.txt")).href, root), false);
  assert.equal(permittedResource("https://example.com", root), false);
  assert.equal(permittedResource("file:///C:/Windows/win.ini", root), false);
});
