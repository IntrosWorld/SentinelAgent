"use strict";
const { spawn } = require("node:child_process");
const { EventEmitter } = require("node:events");
const { randomUUID } = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");

const METHODS = new Set([
  "status",
  "models.list",
  "models.save",
  "models.discover",
  "models.test",
  "windows.list",
  "history.list",
  "run.start",
  "run.approve",
  "run.cancel",
]);

function validateCall(method, params) {
  if (
    !METHODS.has(method) ||
    !params ||
    Array.isArray(params) ||
    typeof params !== "object"
  )
    throw Error("Unsupported request");
  if (Buffer.byteLength(JSON.stringify(params)) > 32000)
    throw Error("Request too large");
}
function executable(candidate, name) {
  if (
    typeof candidate !== "string" ||
    !path.isAbsolute(candidate) ||
    candidate.includes("\0") ||
    path.basename(candidate).toLowerCase() !== name ||
    !fs.statSync(candidate).isFile()
  )
    throw Error(`Choose ${name}`);
  return path.resolve(candidate);
}
function findOllama() {
  const folders = [
    ...(process.env.PATH || "").split(path.delimiter),
    path.join(process.env.LOCALAPPDATA || "", "Programs", "Ollama"),
    path.join(process.env.ProgramFiles || "", "Ollama"),
  ];
  return [
    ...new Set(folders.filter(Boolean).map((p) => path.join(p, "ollama.exe"))),
  ].filter((p) => {
    try {
      return path.isAbsolute(p) && fs.statSync(p).isFile();
    } catch {
      return false;
    }
  });
}

class RuntimeBridge extends EventEmitter {
  constructor({ command, args, cwd, env }) {
    super();
    this.pending = new Map();
    this.buffer = "";
    this.closed = false;
    this.child = spawn(command, args, {
      cwd,
      env,
      shell: false,
      windowsHide: true,
      stdio: ["pipe", "pipe", "pipe"],
    });
    this.child.stdout.setEncoding("utf8");
    this.child.stdout.on("data", (chunk) => this.receive(chunk));
    // Do not persist arbitrary provider/process errors; they may contain secrets.
    this.child.stderr.on("data", () => {});
    this.child.on("error", () =>
      this.fail(
        "Agent runtime could not start. Check the Python/runtime installation.",
      ),
    );
    this.child.on("exit", () =>
      this.fail(
        "Agent runtime stopped. Restart the connection before continuing.",
      ),
    );
  }
  receive(chunk) {
    this.buffer += chunk;
    if (Buffer.byteLength(this.buffer) > 2 * 1024 * 1024) {
      this.child.kill();
      this.fail("Invalid runtime response");
      return;
    }
    let index;
    while ((index = this.buffer.indexOf("\n")) >= 0) {
      const line = this.buffer.slice(0, index);
      this.buffer = this.buffer.slice(index + 1);
      let message;
      try {
        message = JSON.parse(line);
      } catch {
        this.child.kill();
        this.fail("Invalid runtime protocol");
        return;
      }
      if (!message || typeof message !== "object" || Array.isArray(message)) {
        this.child.kill();
        this.fail("Invalid runtime protocol");
        return;
      }
      if (message.event) this.emit("event", message);
      else {
        const job = this.pending.get(message.id);
        if (job) {
          clearTimeout(job.timer);
          this.pending.delete(message.id);
          message.error
            ? job.reject(Error(message.error.message || "Agent request failed"))
            : job.resolve(message.result);
        }
      }
    }
  }
  request(method, params = {}, timeout = 25000) {
    if (this.closed) return Promise.reject(Error("Agent is disconnected"));
    if (this.pending.size >= 32)
      return Promise.reject(Error("Too many pending requests"));
    const id = randomUUID();
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(
          Error(
            "Agent response timed out. Stop or restart before retrying an action.",
          ),
        );
      }, timeout);
      this.pending.set(id, { resolve, reject, timer });
      this.child.stdin.write(
        JSON.stringify({ id, method, params }) + "\n",
        (error) => {
          if (error) this.fail("Agent connection closed");
        },
      );
    });
  }
  fail(message) {
    if (this.closed) return;
    this.closed = true;
    for (const job of this.pending.values()) {
      clearTimeout(job.timer);
      job.reject(Error(message));
    }
    this.pending.clear();
    this.emit("event", { event: "disconnected", message });
  }
  close() {
    this.fail("Agent connection closed");
    this.child.stdin.end();
    const timer = setTimeout(() => {
      if (this.child.exitCode === null) this.child.kill();
    }, 2500);
    timer.unref();
  }
}
module.exports = {
  RuntimeBridge,
  validateCall,
  executable,
  findOllama,
  METHODS,
};
