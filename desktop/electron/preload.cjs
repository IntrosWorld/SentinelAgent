"use strict";
const { contextBridge, ipcRenderer } = require("electron");
contextBridge.exposeInMainWorld(
  "sentinel",
  Object.freeze({
    call: (method, params = {}) =>
      ipcRenderer.invoke("sentinel:call", method, params),
    subscribe: (callback) => {
      const listener = (_event, data) => callback(data);
      ipcRenderer.on("sentinel:event", listener);
      return () => ipcRenderer.removeListener("sentinel:event", listener);
    },
  }),
);
