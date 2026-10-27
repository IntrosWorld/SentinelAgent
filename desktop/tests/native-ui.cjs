// Opt-in real Electron + Ollama + Win32 input. Requires an unlocked desktop.
const { _electron } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const { spawn } = require("node:child_process");
const { once } = require("node:events");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const assert = require("node:assert/strict");
const project = path.resolve(__dirname, "../..");
const root = fs.mkdtempSync(
  path.join(os.tmpdir(), "sentinel-native-electron-"),
);
(async () => {
  const fixture = spawn(
    process.env.SENTINEL_PYTHON || "python",
    [
      path.join(project, "benchmark/native_input_fixture.py"),
      "Sentinel Electron input fixture",
    ],
    { stdio: ["ignore", "pipe", "pipe"], windowsHide: true },
  );
  let application;
  try {
    const [chunk] = await once(fixture.stdout, "data");
    const windowId = String(Number(String(chunk).trim()));
    application = await _electron.launch({
      executablePath:
        process.env.SENTINEL_ELECTRON_EXECUTABLE || require("electron"),
      args: process.env.SENTINEL_ELECTRON_EXECUTABLE
        ? []
        : [path.resolve(__dirname, "..")],
      env: { ...process.env, SENTINEL_DESKTOP_DATA: root },
    });
    const page = await application.firstWindow();
    page.setDefaultTimeout(20000);
    await page.getByText("Agent connected", { exact: true }).waitFor();
    await page
      .getByRole("button", { name: "Models & setup", exact: true })
      .click();
    await page
      .getByLabel("Model ID")
      .fill(process.env.SENTINEL_TEST_MODEL || "qwen3:1.7b");
    await page
      .getByText("This model supports images", { exact: true })
      .locator("input")
      .uncheck();
    await page
      .getByRole("button", { name: "Save profile", exact: true })
      .click();
    await page.getByText("Profile saved.", { exact: false }).waitFor();
    await page.getByRole("button", { name: "Tasks", exact: true }).click();
    await page.getByLabel("Model profile").selectOption("my-ollama");
    await page
      .getByRole("button", { name: "Refresh windows", exact: true })
      .click();
    await page.getByLabel("Target window").selectOption(windowId);
    const expected = "Electron native input verified";
    await page
      .getByLabel("Task instruction")
      .fill(
        'Type exactly "' + expected + '" into the editable Test input field.',
      );
    await page.getByLabel("Expected result text").fill(expected);
    await page.getByRole("button", { name: "Start task", exact: true }).click();
    await page
      .getByRole("button", { name: "Approve once", exact: true })
      .click({ timeout: 180000 });
    await page
      .getByText("completed: goal-verified", { exact: true })
      .waitFor({ timeout: 180000 });
    const history = await page.evaluate(() =>
      window.sentinel.call("history.list"),
    );
    assert.equal(history[0].status, "completed");
    assert.equal(history[0].reason, "goal-verified");
    assert.equal(history[0].steps, 1);
    console.log(
      "PASS: real Electron approval, local Ollama, native Windows typing and observed-text verification. Evidence: " +
        root,
    );
  } finally {
    if (application) await application.close();
    fixture.kill();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
