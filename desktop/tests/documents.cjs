// Real JSON bridge and real document files. No model server or mocked runtime.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const os = require("node:os");
const { RuntimeBridge } = require("../electron/runtime.cjs");
const project = path.resolve(__dirname, "../..");
const root = fs.mkdtempSync(path.join(os.tmpdir(), "sentinel-documents-"));
const command = process.env.SENTINEL_RUNTIME || process.env.SENTINEL_PYTHON || "python";
const args = process.env.SENTINEL_RUNTIME ? ["--data-dir", root] :
  ["-u", "-m", "sentinel_agent.desktop_service", "--data-dir", root];
const env = {...process.env, PYTHONPATH: project};
if (process.env.SENTINEL_RUNTIME) {
  env.PATH = process.env.SystemRoot + "\\System32";
  env.PYTHONPATH = "";
  env.PYTHONHOME = "";
}
const bridge = new RuntimeBridge({command, args, cwd: project, env});
let resolveRun, rejectRun;
bridge.on("event", event => {
  if (process.env.SENTINEL_TEST_TRACE) console.log(event.event, event.result?.status || "");
  if (event.event === "approval") {
    bridge.request("run.approve", {run_id:event.run_id, token:event.token, approved:true}).catch(rejectRun);
  }
  if (event.event === "finished") resolveRun(event.result);
});
(async () => {
  try {
    await bridge.request("status");
    for (const extension of ["docx", "xlsx", "pdf"]) {
      if (process.env.SENTINEL_TEST_TRACE) console.log("Creating", extension);
      const finished = new Promise((resolve, reject) => {resolveRun=resolve;rejectRun=reject;});
      await bridge.request("run.start", {mode:"documents", goal:"create bridge."+extension+" with content Real bridge output"});
      const result = await Promise.race([finished, new Promise((_, reject) => {
        const timer = setTimeout(() => reject(Error("Document timed out")), 20000); timer.unref();
      })]);
      assert.equal(result.status,"completed",JSON.stringify(result));
      assert(result.artifacts[0].verified);
      assert(fs.statSync(path.join(root,"artifacts","bridge."+extension)).size > 100);
    }
    console.log("PASS: real private runtime bridge creates verified DOCX, XLSX and PDF files. Output: "+root);
  } finally {bridge.close();}
})().catch(error=>{console.error(error);process.exitCode=1;bridge.close();});
