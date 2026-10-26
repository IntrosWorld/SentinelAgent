// Full Electron renderer -> IPC -> private Python process -> real output files.
const { _electron } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const fs = require("node:fs");
const path = require("node:path");
const os = require("node:os");
const assert = require("node:assert/strict");
const root = fs.mkdtempSync(path.join(os.tmpdir(), "sentinel-electron-docs-"));
(async () => {
  const application = await _electron.launch({
    executablePath: process.env.SENTINEL_ELECTRON_EXECUTABLE || require("electron"),
    args: process.env.SENTINEL_ELECTRON_EXECUTABLE ? [] : [path.resolve(__dirname, "..")],
    env: {...process.env, SENTINEL_DESKTOP_DATA: root},
  });
  try {
    const page = await application.firstWindow();
    await page.getByText("Agent connected", {exact:true}).waitFor();
    if (process.env.SENTINEL_ELECTRON_EXECUTABLE)
      assert.equal(await page.evaluate(async () => (await window.sentinel.call("host.status")).packaged), true);
    for (const extension of ["docx", "xlsx", "pdf"]) {
      const filename = "electron-report." + extension;
      await page.getByLabel("Task instruction").fill("create " + filename + " with content Created through the real Electron app");
      await page.getByRole("button", {name:"Start task", exact:true}).click();
      await page.getByRole("button", {name:"Approve once", exact:true}).click();
      await page.getByText("completed: Document written and verified", {exact:true}).waitFor();
      await page.getByRole("button", {name:"Open file", exact:true}).waitFor();
      assert(fs.statSync(path.join(root,"agent","artifacts",filename)).size > 100);
    }
    for (const goal of [
      "append electron-report.docx with content Added through Electron",
      "set B3 to 99 in electron-report.xlsx",
      "append electron-report.pdf with content Added through Electron",
    ]) {
      await page.getByLabel("Task instruction").fill(goal);
      await page.getByRole("button", {name:"Start task",exact:true}).click();
      await page.getByRole("button", {name:"Approve once",exact:true}).click();
      await page.getByText("completed: Document written and verified", {exact:true}).waitFor();
    }
    assert.equal(fs.readdirSync(path.join(root,"agent","artifacts","backups")).length,3);
    fs.mkdirSync(path.resolve(__dirname,"../test-results"),{recursive:true});
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({path:path.resolve(__dirname,"../test-results/documents-real-electron.png")});
    await page.getByLabel("Task instruction").fill("create declined.docx with content Do not write");
    await page.getByRole("button", {name:"Start task",exact:true}).click();
    await page.getByRole("button", {name:"Decline",exact:true}).click();
    await page.getByText("cancelled: Document action declined",{exact:true}).waitFor();
    assert(!fs.existsSync(path.join(root,"agent","artifacts","declined.docx")));
    assert.equal(await page.evaluate(async () => {
      try { await window.sentinel.call("host.openArtifact",{filename:"../desktop.json"}); return false; }
      catch { return true; }
    }),true);
    console.log("PASS: real Electron conversation creates and edits DOCX, XLSX and PDF; backups, declined writes and confined output controls verified. Output: "+root);
  } finally {await application.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
