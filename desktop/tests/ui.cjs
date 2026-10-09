// Browser-only renderer QA: this fixture never sends native desktop input.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const http = require("node:http");
const fs = require("node:fs");
const path = require("node:path");
const assert = require("node:assert/strict");
const root = path.resolve(__dirname, "../dist");
const server = http.createServer((req, res) => {
  const file = path.resolve(
    root,
    "." + (req.url === "/" ? "/index.html" : req.url),
  );
  if (!file.startsWith(root + path.sep)) {
    res.writeHead(403).end();
    return;
  }
  res.setHeader(
    "Content-Type",
    file.endsWith(".js")
      ? "text/javascript"
      : file.endsWith(".css")
        ? "text/css"
        : "text/html",
  );
  fs.createReadStream(file)
    .on("error", () => res.end())
    .pipe(res);
});
(async () => {
  await new Promise((r) => server.listen(0, "127.0.0.1", r));
  const browser = await chromium.launch({ channel: "chrome", headless: true });
  try {
    const page = await browser.newPage({
      viewport: { width: 1220, height: 850 },
    });
    const errors = [];
    page.on("pageerror", (e) => errors.push(e.message));
    await page.addInitScript(() => {
      const callbacks = new Set();
      const p = {
        name: "local-vision",
        provider: "ollama",
        model: "test-vision",
        base_url: "http://127.0.0.1:11434",
        vision: true,
        timeout: 120,
        desktop_coordinates: "auto",
      };
      window.testCalls = [];
      window.testEvent = (e) => callbacks.forEach((fn) => fn(e));
      window.sentinel = {
        subscribe: (fn) => {
          callbacks.add(fn);
          return () => callbacks.delete(fn);
        },
        call: async (m, params) => {
          window.testCalls.push({ m, params });
          if (m === "status") return { version: "test", ocr_available: true };
          if (m === "models.list") return { profiles: [p] };
          if (m === "host.status")
            return { encryptedStorage: true, ollama: "" };
          if (m === "history.list") return [];
          if (m === "windows.list")
            return [{ window_id: 42, title: "Paint", app: "mspaint.exe" }];
          if (m === "models.discover")
            return { models: [{ name: "test-vision" }] };
          if (m === "models.save") return params;
          if (m === "run.start") {
            window.testEvent({
              event: "started",
              task: {
                id: "r1",
                goal: params.goal,
                mode: "desktop",
                status: "running",
              },
            });
            return { run_id: "r1" };
          }
          if (m === "run.approve") {
            window.testEvent({ event: "approval_closed", token: "t1" });
            return {};
          }
          if (m === "run.cancel") {
            window.testEvent({
              event: "finished",
              result: { status: "cancelled", reason: "Stopped by user" },
            });
            return {};
          }
          return {};
        },
      };
    });
    await page.goto(`http://127.0.0.1:${server.address().port}`);
    await page.getByText("Agent connected", { exact: true }).waitFor();
    await page.getByRole("button", { name: "Models & setup" }).click();
    await page.getByLabel("Model ID").fill("test-vision");
    await page
      .getByRole("button", { name: "Save profile", exact: true })
      .click();
    await page.getByText("Profile saved.", { exact: false }).waitFor();
    await page.getByRole("button", { name: "Discover local models" }).click();
    await page.getByText("Server connected", { exact: false }).waitFor();
    await page.getByRole("button", { name: "Tasks", exact: true }).click();
    await page.getByRole("button", { name: "Refresh windows" }).click();
    await page.getByLabel("Target window").selectOption("42");
    await page.getByLabel("Task instruction").fill("Draw a circle in Paint");
    await page.getByRole("button", { name: "Start task", exact: true }).click();
    await page.evaluate(() =>
      window.testEvent({
        event: "approval",
        run_id: "r1",
        token: "t1",
        details: {
          window: "Paint",
          action: { kind: "drag", start: [100, 100], end: [200, 200] },
        },
      }),
    );
    await page.getByRole("button", { name: "Approve once" }).waitFor();
    fs.mkdirSync(path.resolve(__dirname, "../test-results"), {
      recursive: true,
    });
    await page.screenshot({
      path: path.resolve(__dirname, "../test-results/conversation.png"),
      fullPage: true,
    });
    await page.getByRole("button", { name: "Approve once" }).click();
    const approval = await page.evaluate(() =>
      window.testCalls.find((c) => c.m === "run.approve"),
    );
    assert.deepEqual(approval.params, {
      run_id: "r1",
      token: "t1",
      approved: true,
    });
    await page.getByRole("button", { name: "Stop", exact: true }).click();
    await page
      .getByText("cancelled: Stopped by user", { exact: true })
      .waitFor();
    await page.evaluate(() =>
      window.testEvent({
        event: "disconnected",
        message: "Runtime unavailable",
      }),
    );
    await page
      .getByRole("alert")
      .filter({ hasText: "Runtime unavailable" })
      .waitFor();
    assert.equal(
      await page
        .getByRole("button", { name: "Start task", exact: true })
        .isDisabled(),
      true,
    );
    assert.deepEqual(errors, []);
    console.log(
      "PASS: setup, discovery, start, approval token, stop, disconnect; no browser errors. Native actions mocked.",
    );
  } finally {
    await browser.close();
    server.close();
  }
})().catch((e) => {
  console.error(e);
  server.close();
  process.exitCode = 1;
});
