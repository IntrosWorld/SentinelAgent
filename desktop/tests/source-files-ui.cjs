const { _electron } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const assert = require("node:assert/strict");
(async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "sentinel-source-ui-"));
  const app = await _electron.launch({
    executablePath: process.env.SENTINEL_ELECTRON_EXECUTABLE || require("electron"),
    args: process.env.SENTINEL_ELECTRON_EXECUTABLE ? [] : [path.resolve(__dirname, "..")],
    env: { ...process.env, SENTINEL_DESKTOP_DATA: root },
  });
  try {
    const page = await app.firstWindow();
    page.setDefaultTimeout(30000);
    await page.getByText("Agent connected", { exact: true }).waitFor();
    const files = {
      "style.css": "h1 { color: rgb(12, 34, 56); }",
      "app.js": "document.querySelector('h1').textContent = 'Local JS works';",
      "index.html": '<!doctype html><meta charset="utf-8"><link rel="stylesheet" href="style.css"><h1>Hello</h1><script src="app.js"></script>',
      "notes.md": "# Source files\nCreated through the real desktop bridge.",
      "example.ts": "const greeting: string = 'Hello';",
    };
    for (const [filename, content] of Object.entries(files)) {
      await page.getByLabel("Task instruction").fill("create " + filename + " with content " + content);
      await page.getByRole("button", { name: "Start task", exact: true }).click();
      await page.getByRole("button", { name: "Approve once", exact: true }).click();
      await page.getByText("completed: Document written and verified", { exact: true }).waitFor();
      assert.equal(fs.readFileSync(path.join(root, "agent", "artifacts", filename), "utf8"), content);
    }
    // Exercise the real preview renderer directly; the host's confirmation dialog
    // is deliberately outside this automated renderer/security test.
    const waiting = app.waitForEvent("window");
    waiting.catch(() => {});
    await app.evaluate(async ({ BrowserWindow, session, app: electronApp }, args) => {
      const modulePath = electronApp.isPackaged ? process.resourcesPath + "/app.asar/electron/file-preview.cjs" : args.module;
      await process.mainModule.require(modulePath).openPreview({ BrowserWindow, session }, args.target, args.root);
    }, { module: path.resolve(__dirname, "../electron/file-preview.cjs"),
         target: path.join(root, "agent", "artifacts", "index.html"), root: path.join(root, "agent", "artifacts") });
    const preview = await waiting;
    await preview.getByRole("heading", { name: "Local JS works" }).waitFor();
    assert.equal(await preview.locator("h1").evaluate(el => getComputedStyle(el).color), "rgb(12, 34, 56)");
    assert.equal(await preview.evaluate(() => typeof require), "undefined");
    assert.equal(await preview.evaluate(async () => { try { await fetch("https://example.com"); return "allowed"; } catch { return "blocked"; } }), "blocked");
    console.log("PASS: real source files via Electron/Python; HTML rendered with local CSS/JS; network and Node blocked. " + root);
  } finally { await app.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
