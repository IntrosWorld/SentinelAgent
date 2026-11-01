// Real application launch and native typing; no model, mock IPC or test window.
const { _electron } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const assert = require("node:assert/strict");
(async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "sentinel-notepad-ui-"));
  const app = await _electron.launch({
    executablePath: process.env.SENTINEL_ELECTRON_EXECUTABLE || require("electron"),
    args: process.env.SENTINEL_ELECTRON_EXECUTABLE ? [] : [path.resolve(__dirname, "..")],
    env: { ...process.env, SENTINEL_DESKTOP_DATA: root },
  });
  try {
    const page = await app.firstWindow();
    page.setDefaultTimeout(30000);
    await page.getByText("Agent connected", { exact: true }).waitFor();
    await page.evaluate(() => window.sentinel.subscribe(event => {
      if (event.event === "finished") window.notepadResult = event.result;
    }));
    await page.getByLabel("Task instruction").fill("Open Notepad and write Hello from Sentinel desktop.");
    await page.getByRole("button", { name: "Start task", exact: true }).click();
    await page.getByRole("button", { name: "Approve once", exact: true }).click();
    // Wait for the second, distinct approval token rather than double-clicking.
    await page.getByRole("heading", { name: "type", exact: true }).waitFor();
    await page.getByRole("button", { name: "Approve once", exact: true }).click();
    await page.waitForFunction(async () => {
      const history = await window.sentinel.call("history.list");
      return history[0] && !["running", "approval"].includes(history[0].status);
    });
    const result = await page.evaluate(() => window.notepadResult);
    if (result && result.status !== "completed") console.log(JSON.stringify(result));
    await page.getByText("completed: goal-verified", { exact: true }).waitFor();
    const history = await page.evaluate(() => window.sentinel.call("history.list"));
    assert.equal(history[0].reason, "goal-verified");
    console.log("PASS: real Electron -> launch Notepad -> approve typing -> content readback. " + root);
  } finally {
    await app.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
