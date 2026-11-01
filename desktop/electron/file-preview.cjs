"use strict";
const fs = require("node:fs");
const path = require("node:path");
const { pathToFileURL, fileURLToPath } = require("node:url");
const { randomUUID } = require("node:crypto");
const previews = new Set();

const localTypes = new Set([".html", ".css", ".js", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg", ".ico", ".woff", ".woff2"]);
function permittedResource(url, root) {
  try {
    if (url.startsWith("data:image/")) return true;
    const target = fs.realpathSync(fileURLToPath(url));
    return path.dirname(target) === root && localTypes.has(path.extname(target).toLowerCase());
  } catch { return false; }
}

async function openPreview(electron, filename, root) {
  const { BrowserWindow, session } = electron;
  const isolated = session.fromPartition("sentinel-preview-" + randomUUID());
  isolated.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
  isolated.setPermissionCheckHandler(() => false);
  isolated.on("will-download", event => event.preventDefault());
  isolated.webRequest.onBeforeRequest((details, callback) => {
    callback({ cancel: !permittedResource(details.url, root) && !details.url.startsWith("data:text/html,") });
  });
  isolated.webRequest.onHeadersReceived((details, callback) => callback({
    responseHeaders: { ...details.responseHeaders, "Content-Security-Policy": [
      "default-src 'none'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src 'none'; frame-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'",
    ] },
  }));
  const preview = new BrowserWindow({ width: 1100, height: 760, title: "Sentinel — local file preview",
    webPreferences: { session: isolated, nodeIntegration: false, contextIsolation: true, sandbox: true,
      webSecurity: true, navigateOnDragDrop: false, devTools: false } });
  previews.add(preview);
  preview.once("closed", () => previews.delete(preview));
  preview.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
  preview.webContents.on("will-navigate", event => event.preventDefault());
  preview.webContents.on("will-attach-webview", event => event.preventDefault());
  if (path.extname(filename).toLowerCase() === ".html") {
    await preview.loadURL(pathToFileURL(filename).href);
  } else {
    const escaped = fs.readFileSync(filename, "utf8").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;");
    await preview.loadURL("data:text/html," + encodeURIComponent("<!doctype html><meta charset=utf-8><title>Source preview</title><pre>" + escaped + "</pre>"));
  }
  return preview;
}
module.exports = { openPreview, permittedResource };
