"use strict";
const {
  app,
  BrowserWindow,
  ipcMain,
  protocol,
  net,
  session,
  dialog,
  safeStorage,
  shell,
} = require("electron");
const fs = require("node:fs");
const path = require("node:path");
const { pathToFileURL } = require("node:url");
const { spawn } = require("node:child_process");
const {
  RuntimeBridge,
  validateCall,
  executable,
  findOllama,
} = require("./runtime.cjs");

protocol.registerSchemesAsPrivileged([
  {
    scheme: "sentinel",
    privileges: { standard: true, secure: true, supportFetchAPI: true },
  },
]);
app.setName("Sentinel Desktop");
if (process.env.SENTINEL_DESKTOP_DATA)
  app.setPath("userData", path.resolve(process.env.SENTINEL_DESKTOP_DATA));
const smoke = process.argv.includes("--smoke-test");
if (!app.requestSingleInstanceLock()) app.exit(0);
let window, bridge, ownedOllama, runtimeReady;
const readJSON = (file, fallback = {}) => {
  try {
    return JSON.parse(fs.readFileSync(file, "utf8"));
  } catch {
    return fallback;
  }
};
function writeJSON(file, value) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file + ".tmp", JSON.stringify(value, null, 2));
  fs.renameSync(file + ".tmp", file);
}
const data = () => app.getPath("userData");
const settingsFile = () => path.join(data(), "desktop.json");
const secretsFile = () => path.join(data(), "credentials.json");
function event(value) {
  if (window && !window.isDestroyed())
    window.webContents.send("sentinel:event", value);
}

async function runtime() {
  if (runtimeReady) return runtimeReady;
  runtimeReady = startRuntime();
  try {
    return await runtimeReady;
  } finally {
    runtimeReady = null;
  }
}
async function startRuntime() {
  if (bridge && !bridge.closed) return bridge;
  const settings = readJSON(settingsFile());
  const project = path.resolve(__dirname, "../..");
  const bundled = path.join(
    process.resourcesPath,
    "runtime",
    "sentinel-runtime.exe",
  );
  const venv = path.join(project, ".venv", "Scripts", "python.exe");
  const command = app.isPackaged
    ? bundled
    : settings.python ||
      process.env.SENTINEL_PYTHON ||
      (fs.existsSync(venv) ? venv : "python");
  if (app.isPackaged && !fs.existsSync(command))
    throw Error(
      "Bundled agent runtime is missing. Reinstall the complete desktop package.",
    );
  const work = path.join(data(), "agent");
  fs.mkdirSync(work, { recursive: true });
  const args = app.isPackaged
    ? ["--data-dir", work]
    : ["-u", "-m", "sentinel_agent.desktop_service", "--data-dir", work];
  const current = new RuntimeBridge({
    command,
    args,
    cwd: app.isPackaged ? work : project,
    env: {
      ...process.env,
      PYTHONUTF8: "1",
      PYTHONUNBUFFERED: "1",
      PYTHONPATH: app.isPackaged ? "" : project,
      SENTINEL_DESKTOP_HOST_PID: String(process.pid),
    },
  });
  bridge = current;
  current.on("event", event);
  try {
    await current.request("status", {}, 15000);
    if (safeStorage.isEncryptionAvailable()) {
      for (const [name, encrypted] of Object.entries(readJSON(secretsFile()))) {
        const key = safeStorage.decryptString(Buffer.from(encrypted, "base64"));
        await current.request("credentials.set", { name, key });
      }
    }
  } catch (error) {
    current.close();
    bridge = null;
    throw error;
  }
  return current;
}
async function hostCall(method, params) {
  if (method === "host.status")
    return {
      packaged: app.isPackaged,
      ollama: readJSON(settingsFile()).ollama || "",
      encryptedStorage: safeStorage.isEncryptionAvailable(),
      dataDirectory: data(),
    };
  if (method === "host.detectOllama") return findOllama();
  if (method === "host.chooseOllama") {
    const result = await dialog.showOpenDialog(window, {
      title: "Choose the Ollama executable",
      properties: ["openFile"],
      filters: [{ name: "Ollama executable", extensions: ["exe"] }],
    });
    if (result.canceled) return null;
    const chosen = executable(result.filePaths[0], "ollama.exe");
    writeJSON(settingsFile(), { ...readJSON(settingsFile()), ollama: chosen });
    return chosen;
  }
  if (method === "host.saveOllama") {
    const chosen = executable(params.path, "ollama.exe");
    writeJSON(settingsFile(), { ...readJSON(settingsFile()), ollama: chosen });
    return chosen;
  }
  if (method === "host.choosePython") {
    if (app.isPackaged)
      throw Error("The installed app uses its bundled runtime");
    const result = await dialog.showOpenDialog(window, {
      title: "Choose Python with Sentinel dependencies installed",
      properties: ["openFile"],
      filters: [{ name: "Python", extensions: ["exe"] }],
    });
    if (result.canceled) return null;
    const chosen = executable(result.filePaths[0], "python.exe");
    if (bridge && !bridge.closed && (await bridge.request("status")).active_run)
      throw Error("Stop the current task first");
    if (bridge) bridge.close();
    bridge = null;
    writeJSON(settingsFile(), { ...readJSON(settingsFile()), python: chosen });
    await runtime();
    return chosen;
  }
  if (method === "host.startOllama") {
    const target = executable(
      readJSON(settingsFile()).ollama || findOllama()[0],
      "ollama.exe",
    );
    const agent = await runtime();
    try {
      await agent.request("models.discover", {
        provider: "ollama",
        base_url: "http://127.0.0.1:11434",
      });
      return { message: "Ollama is already running on port 11434." };
    } catch {
      /* Explicit launch below. */
    }
    if (ownedOllama)
      return { message: "Ollama is starting. Test the connection shortly." };
    const decision = await dialog.showMessageBox(window, {
      type: "question",
      title: "Start local Ollama?",
      message: "Run this executable as the local model server?",
      detail: target + "\nListening address: 127.0.0.1:11434",
      buttons: ["Cancel", "Start Ollama"],
      defaultId: 0,
      cancelId: 0,
    });
    if (decision.response !== 1) return { message: "Start cancelled." };
    ownedOllama = spawn(target, ["serve"], {
      shell: false,
      windowsHide: true,
      stdio: "ignore",
      env: { ...process.env, OLLAMA_HOST: "127.0.0.1:11434" },
    });
    ownedOllama.on("error", () => {
      ownedOllama = null;
      event({
        event: "host_notice",
        message: "Ollama could not start. Check the selected executable.",
      });
    });
    ownedOllama.on("exit", () => {
      ownedOllama = null;
    });
    return {
      message: "Ollama start requested. Test the connection in a moment.",
    };
  }
  if (method === "host.downloadOllama") {
    await shell.openExternal("https://ollama.com/download/windows");
    return true;
  }
  if (method === "host.openData") {
    await shell.openPath(path.join(data(), "agent"));
    return true;
  }
  if (method === "host.restart") {
    if (bridge && !bridge.closed && (await bridge.request("status")).active_run)
      throw Error("Stop the current task first");
    if (bridge) bridge.close();
    bridge = null;
    await runtime();
    return true;
  }
  validateCall(method, params);
  const agent = await runtime();
  if (method === "models.save") {
    const { apiKey, ...profile } = params;
    if (
      apiKey !== undefined &&
      (typeof apiKey !== "string" || apiKey.length > 4096)
    )
      throw Error("Invalid API key");
    if (apiKey && !safeStorage.isEncryptionAvailable())
      throw Error("OS credential encryption is unavailable. No key was saved.");
    const result = await agent.request(method, profile);
    if (apiKey) {
      const vault = readJSON(secretsFile());
      vault[result.name] = safeStorage.encryptString(apiKey).toString("base64");
      writeJSON(secretsFile(), vault);
      await agent.request("credentials.set", {
        name: result.name,
        key: apiKey,
      });
    }
    return result;
  }
  return agent.request(method, params);
}

app.whenReady().then(async () => {
  const assets = path.resolve(__dirname, "../dist");
  protocol.handle("sentinel", (request) => {
    const url = new URL(request.url);
    if (request.method !== "GET" || url.hostname !== "app")
      return new Response("", { status: 403 });
    let file;
    try {
      file = path.resolve(
        assets,
        "." +
          decodeURIComponent(
            url.pathname === "/" ? "/index.html" : url.pathname,
          ),
      );
    } catch {
      return new Response("", { status: 400 });
    }
    if (!file.startsWith(assets + path.sep))
      return new Response("", { status: 403 });
    return net.fetch(pathToFileURL(file).toString());
  });
  session.defaultSession.setPermissionRequestHandler(
    (_wc, _permission, callback) => callback(false),
  );
  session.defaultSession.setPermissionCheckHandler(() => false);
  window = new BrowserWindow({
    width: 1220,
    height: 850,
    minWidth: 780,
    minHeight: 600,
    show: false,
    title: "Sentinel Desktop",
    backgroundColor: "#faf9f6",
    autoHideMenuBar: true,
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: true,
      webSecurity: true,
      webviewTag: false,
      spellcheck: false,
    },
  });
  window.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
  window.webContents.on("will-navigate", (e) => e.preventDefault());
  window.webContents.on("will-attach-webview", (e) => e.preventDefault());
  ipcMain.handle("sentinel:call", async (event, method, params = {}) => {
    if (
      event.sender !== window.webContents ||
      event.senderFrame !== window.webContents.mainFrame ||
      new URL(event.senderFrame.url).protocol !== "sentinel:" ||
      new URL(event.senderFrame.url).hostname !== "app"
    )
      throw Error("Untrusted sender");
    if (
      typeof method !== "string" ||
      !params ||
      typeof params !== "object" ||
      Array.isArray(params) ||
      Buffer.byteLength(JSON.stringify(params)) > 32000
    )
      throw Error("Invalid request");
    return hostCall(method, params);
  });
  await window.loadURL("sentinel://app/index.html");
  if (smoke) {
    try {
      const agent = await runtime();
      const status = await window.webContents.executeJavaScript(
        "window.sentinel.call('status')",
      );
      const models = await agent.request("models.list");
      writeJSON(path.join(data(), "smoke-result.json"), {
        smoke: "passed", packaged: app.isPackaged, version: status.version,
        profiles: models.profiles.length,
      });
      console.log(
        JSON.stringify({
          smoke: "passed",
          renderer: window.webContents.getURL(),
          version: status.version,
          profiles: models.profiles.length,
        }),
      );
      bridge.close();
      app.quit();
    } catch {
      console.error("Desktop smoke test failed");
      if (bridge) bridge.close();
      app.exit(1);
    }
  } else window.show();
});
app.on("second-instance", () => {
  if (window) {
    if (window.isMinimized()) window.restore();
    window.focus();
  }
});
app.on("window-all-closed", () => app.quit());
app.on("before-quit", () => {
  if (bridge) bridge.close();
  if (ownedOllama) ownedOllama.kill();
});
