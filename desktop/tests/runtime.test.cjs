const { test } = require("node:test");
const assert = require("node:assert/strict");
const {
  RuntimeBridge,
  validateCall,
  executable,
} = require("../electron/runtime.cjs");

test("renderer methods cannot invoke credentials, shells, or arbitrary file access", () => {
  for (const method of ["exec", "credentials.set", "fs.read", "host.shell"])
    assert.throws(() => validateCall(method, {}));
  validateCall("run.approve", { token: "one-use" });
  assert.throws(() => validateCall("status", []));
  assert.throws(() => validateCall("status", { text: "x".repeat(33000) }));
});
test("executable selection refuses relative paths and unrelated executables", () => {
  assert.throws(() => executable("ollama.exe", "ollama.exe"));
  assert.throws(() => executable(process.execPath, "ollama.exe"));
});
test("private bridge correlates responses and forwards events", async () => {
  const code = `require('readline').createInterface({input:process.stdin}).on('line',l=>{const r=JSON.parse(l);console.log(JSON.stringify({event:'progress',message:'ready'}));console.log(JSON.stringify({id:r.id,result:r.params}));});`;
  const bridge = new RuntimeBridge({
    command: process.execPath,
    args: ["-e", code],
    env: process.env,
  });
  let event;
  bridge.on("event", (e) => (event = e));
  try {
    assert.deepEqual(await bridge.request("status", { hello: 1 }), {
      hello: 1,
    });
    assert.equal(event.event, "progress");
  } finally {
    bridge.close();
  }
});
test("malformed protocol fails closed without exposing stderr", async () => {
  const bridge = new RuntimeBridge({
    command: process.execPath,
    args: [
      "-e",
      `process.stderr.write('secret'); console.log('not json');setTimeout(()=>{},10000)`,
    ],
    env: process.env,
  });
  await assert.rejects(bridge.request("status"), /Invalid runtime protocol/);
  bridge.close();
});
