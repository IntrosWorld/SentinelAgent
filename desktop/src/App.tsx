import { useEffect, useState } from "react";
import {
  Plus,
  Settings,
  MessageSquare,
  ArrowUp,
  Square,
  ShieldCheck,
  ChevronRight,
} from "lucide-react";

type Data = Record<string, any>;
declare global {
  interface Window {
    sentinel?: {
      call(method: string, params?: Data): Promise<any>;
      subscribe(fn: (event: Data) => void): () => void;
    };
  }
}
const call = (method: string, params: Data = {}) => {
  if (!window.sentinel)
    return Promise.reject(Error("Open this interface in Sentinel Desktop."));
  return window.sentinel.call(method, params);
};
const initial = {
  name: "my-ollama",
  provider: "ollama",
  base_url: "http://127.0.0.1:11434",
  model: "",
  timeout: 120,
  vision: true,
  desktop_coordinates: "auto",
};
const resultReason = (reason: string) =>
  (
    ({
      "model-timeout":
        "The model timed out. Choose a smaller model or increase its timeout in Models & setup.",
      "model-key-missing":
        "Configure the API key for this cloud model in Models & setup.",
      "model-request-failed":
        "The model request failed. Test the selected server and profile in Models & setup.",
      "invalid-model-action":
        "The model returned an invalid target or action. Review the window and try a more capable model.",
    }) as Record<string, string>
  )[reason] || reason;

export default function App() {
  const [page, setPage] = useState("task"),
    [profiles, setProfiles] = useState<Data[]>([]),
    [profile, setProfile] = useState("local-vision");
  const [history, setHistory] = useState<Data[]>([]),
    [task, setTask] = useState<Data | null>(null),
    [approval, setApproval] = useState<Data | null>(null);
  const [messages, setMessages] = useState<string[]>([]),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<Data>({}),
    [host, setHost] = useState<Data>({}),
    [goal, setGoal] = useState("");
  const [windows, setWindows] = useState<Data[]>([]),
    [windowId, setWindowId] = useState(""),
    [vision, setVision] = useState(true);
  const [ocr, setOcr] = useState(false),
    [single, setSingle] = useState(true),
    [cloud, setCloud] = useState(false);
  const [form, setForm] = useState<Data>(initial),
    [apiKey, setApiKey] = useState(""),
    [ollama, setOllama] = useState("");
  const [models, setModels] = useState<string[]>([]),
    [notice, setNotice] = useState("");
  const [taskMode, setTaskMode] = useState("auto"),
    [expectedText, setExpectedText] = useState("");
  const documentTask =
    taskMode === "documents" ||
    (taskMode === "auto" && /\.(docx|xlsx|pdf)\b/i.test(goal));
  const running = task?.status === "running";
  async function act(fn: () => Promise<void>) {
    setError("");
    setBusy(true);
    try {
      await fn();
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e));
    } finally {
      setBusy(false);
    }
  }
  async function refresh() {
    const [s, m, h, local] = await Promise.all([
      call("status"),
      call("models.list"),
      call("history.list"),
      call("host.status"),
    ]);
    setStatus(s);
    setProfiles(m.profiles);
    setHistory(h);
    setHost(local);
    setOllama(local.ollama);
  }
  useEffect(() => {
    const off = window.sentinel?.subscribe((e) => {
      if (e.event === "started") {
        setTask(e.task);
        setMessages(["Task started"]);
        setApproval(null);
      }
      if (e.event === "progress")
        setMessages((m) => [
          ...m.slice(-49),
          `${e.message}${e.elements !== undefined ? ` · ${e.elements} accessible elements` : ""}`,
        ]);
      if (e.event === "approval") setApproval(e);
      if (e.event === "approval_closed")
        setApproval((a) => (a?.token === e.token ? null : a));
      if (e.event === "finished") {
        setTask((t) => (t ? { ...t, ...e.result } : t));
        setApproval(null);
        setMessages((m) => [
          ...m,
          `${e.result.status}: ${resultReason(e.result.reason)}`,
        ]);
        call("history.list")
          .then(setHistory)
          .catch(() => {});
      }
      if (e.event === "disconnected") {
        setError(e.message);
        setStatus({});
        setApproval(null);
        setTask((t) =>
          t?.status === "running" ? { ...t, status: "interrupted" } : t,
        );
      }
      if (e.event === "host_notice") setNotice(e.message);
    });
    void act(refresh);
    return off;
  }, []);
  async function start() {
    await act(async () => {
      const text = goal.trim();
      await call("run.start", {
        goal: text,
        mode: taskMode,
        profile,
        window_id: Number(windowId),
        vision,
        ocr: vision && ocr,
        single_step: single,
        allow_cloud: cloud,
        max_seconds: 300,
        expect_text: expectedText.trim() || null,
      });
      setPage("task");
      setGoal("");
    });
  }
  function edit(p: Data) {
    const { api_key_env: _, ...rest } = p;
    setForm(rest);
    setApiKey("");
    setNotice("");
    setModels([]);
  }
  const field = (key: string, value: any) =>
    setForm((f) => ({ ...f, [key]: value }));
  return (
    <div className="app">
      <aside>
        <div className="brand">
          <span>S</span> Sentinel
        </div>
        <button
          className="new"
          disabled={running}
          onClick={() => {
            setPage("task");
            setTask(null);
            setMessages([]);
          }}
        >
          <Plus size={16} /> New task
        </button>
        <button
          className={page === "task" ? "nav selected" : "nav"}
          onClick={() => setPage("task")}
        >
          <MessageSquare size={17} /> Tasks
        </button>
        <button
          className={page === "models" ? "nav selected" : "nav"}
          onClick={() => setPage("models")}
        >
          <Settings size={17} /> Models & setup
        </button>
        <div className="caption">RECENT TASKS</div>
        <div className="recents">
          {history.map((h) => (
            <button
              key={h.id}
              disabled={running}
              onClick={() => {
                setTask(h);
                setMessages([h.reason || h.status]);
                setPage("task");
              }}
              title={h.goal}
            >
              {h.goal}
              <small>{h.status}</small>
            </button>
          ))}
        </div>
        <footer>
          <ShieldCheck size={17} />
          <div>
            Local-first
            <small>
              {status.version ? "Agent connected" : "Agent disconnected"}
            </small>
          </div>
        </footer>
      </aside>
      <main>
        <header>
          <span>{page === "models" ? "Models & setup" : "Your workspace"}</span>
          {running ? (
            <button
              className="stop"
              onClick={() =>
                void act(async () => {
                  await call("run.cancel", { run_id: task.id });
                })
              }
            >
              <Square size={13} /> Stop task
            </button>
          ) : (
            <span className="subtle">Sentinel Desktop · Preview</span>
          )}
        </header>
        {error && (
          <div className="error" role="alert">
            {error}
            <button onClick={() => void act(refresh)}>Retry connection</button>
            {!host.packaged && (
              <button
                onClick={() =>
                  void act(async () => {
                    await call("host.choosePython");
                    await refresh();
                  })
                }
              >
                Choose Python
              </button>
            )}
          </div>
        )}
        {page === "models" ? (
          <section className="settings">
            <div className="eyebrow">MAKE IT YOURS</div>
            <h1>Your models. Your machine.</h1>
            <p>
              Connect a local server, or explicitly choose a cloud provider. A
              path is only needed to start Ollama from this app.
            </p>
            <div className="profile-tabs">
              {profiles.map((p) => (
                <button key={p.name} onClick={() => edit(p)}>
                  {p.name}
                </button>
              ))}
              <button onClick={() => edit(initial)}>+ Add profile</button>
            </div>
            <fieldset disabled={busy || running}>
              <div className="panel">
                <h2>Model connection</h2>
                <div className="grid">
                  <label>
                    Profile name
                    <input
                      value={form.name}
                      onChange={(e) => field("name", e.target.value)}
                    />
                  </label>
                  <label>
                    Provider
                    <select
                      value={form.provider}
                      onChange={(e) => {
                        field("provider", e.target.value);
                        setNotice("");
                        setModels([]);
                      }}
                    >
                      {[
                        "ollama",
                        "openai_compatible",
                        "anthropic",
                        "gemini",
                      ].map((p) => (
                        <option key={p}>{p}</option>
                      ))}
                    </select>
                  </label>
                  <label className="wide">
                    Server URL
                    <input
                      value={form.base_url}
                      onChange={(e) => {
                        field("base_url", e.target.value);
                        setNotice("");
                      }}
                    />
                  </label>
                  <label>
                    Model ID
                    <input
                      list="models"
                      value={form.model}
                      placeholder="e.g. qwen3-vl:2b-instruct"
                      onChange={(e) => field("model", e.target.value)}
                    />
                    <datalist id="models">
                      {models.map((m) => (
                        <option key={m}>{m}</option>
                      ))}
                    </datalist>
                  </label>
                  <label>
                    Timeout (seconds)
                    <input
                      type="number"
                      min="1"
                      max="900"
                      value={form.timeout}
                      onChange={(e) => field("timeout", Number(e.target.value))}
                    />
                  </label>
                  <label>
                    Coordinates
                    <select
                      value={form.desktop_coordinates}
                      onChange={(e) =>
                        field("desktop_coordinates", e.target.value)
                      }
                    >
                      <option value="auto">Auto</option>
                      <option value="pixels">Pixels</option>
                      <option value="normalized_1000">Normalized 0–1000</option>
                    </select>
                  </label>
                  <label>
                    API key (optional)
                    <input
                      type="password"
                      autoComplete="off"
                      value={apiKey}
                      placeholder="Leave blank to keep saved key"
                      onChange={(e) => setApiKey(e.target.value)}
                    />
                  </label>
                </div>
                <label className="check">
                  <input
                    type="checkbox"
                    checked={form.vision}
                    onChange={(e) => field("vision", e.target.checked)}
                  />{" "}
                  This model supports images
                </label>
                <div className="actions">
                  <button
                    onClick={() =>
                      void act(async () => {
                        const r = await call("models.discover", {
                          provider: form.provider,
                          base_url: form.base_url,
                        });
                        setModels(r.models.map((m: Data) => m.name));
                        setNotice(
                          `Server connected · ${r.models.length} installed models. Choose a model ID above.`,
                        );
                      })
                    }
                  >
                    Discover local models
                  </button>
                  <button
                    className="primary"
                    onClick={() =>
                      void act(async () => {
                        await call("models.save", { ...form, apiKey });
                        setApiKey("");
                        await refresh();
                        setProfile(form.name);
                        setVision(Boolean(form.vision));
                        setCloud(false);
                        setNotice(
                          "Profile saved. Connection has not yet been tested.",
                        );
                      })
                    }
                  >
                    Save profile
                  </button>
                  <button
                    onClick={() =>
                      void act(async () => {
                        const r = await call("models.test", {
                          name: form.name,
                        });
                        setNotice(r.note || JSON.stringify(r));
                      })
                    }
                  >
                    Test saved profile
                  </button>
                </div>
                <p className="hint">
                  Discovery is local-only. Cloud testing can contact your saved
                  endpoint. API keys are encrypted by Windows; screenshots
                  require separate consent for each task.
                </p>
              </div>
              <div className="panel">
                <h2>
                  Local Ollama setup <span className="subtle">Optional</span>
                </h2>
                <label>
                  Ollama executable
                  <input
                    value={ollama}
                    placeholder="F:\Ollama\ollama.exe"
                    onChange={(e) => setOllama(e.target.value)}
                  />
                </label>
                <div className="actions">
                  <button
                    onClick={() =>
                      void act(async () => {
                        const p = await call("host.chooseOllama");
                        if (p) setOllama(p);
                      })
                    }
                  >
                    Browse…
                  </button>
                  <button
                    onClick={() =>
                      void act(async () => {
                        const p = await call("host.detectOllama");
                        if (p.length) setOllama(p[0]);
                        setNotice(
                          p.length
                            ? "Found Ollama. Save this path to use it."
                            : "Ollama not found. Browse or install it first.",
                        );
                      })
                    }
                  >
                    Auto-detect
                  </button>
                  <button
                    onClick={() =>
                      void act(async () => {
                        setOllama(
                          await call("host.saveOllama", { path: ollama }),
                        );
                        setNotice("Executable path saved.");
                      })
                    }
                  >
                    Save path
                  </button>
                  <button
                    onClick={() =>
                      void act(async () =>
                        setNotice((await call("host.startOllama")).message),
                      )
                    }
                  >
                    Start Ollama
                  </button>
                  <button
                    onClick={() =>
                      void act(async () => {
                        await call("host.downloadOllama");
                      })
                    }
                  >
                    Get Ollama ↗
                  </button>
                </div>
                <p className="hint">
                  Install a vision model in Ollama first, then discover models.
                  No model downloads happen automatically. OCR additionally
                  requires Tesseract on PATH.
                </p>
              </div>
            </fieldset>
            {notice && (
              <div className="notice" role="status">
                {notice}
              </div>
            )}
            <button
              onClick={() =>
                void act(async () => {
                  await call("host.openData");
                })
              }
            >
              Open app data folder
            </button>
          </section>
        ) : (
          <section className="conversation">
            <div className="conversation-body">
              {task ? (
                <>
                  <div className="eyebrow">
                    {task.mode === "documents"
                      ? "DOCUMENT TASK"
                      : "DESKTOP TASK"}{" "}
                    · {task.status}
                  </div>
                  <h1>{task.goal}</h1>
                  <div className="user-message">{task.goal}</div>
                  <div className="assistant-label">
                    <span className="logo">S</span> Sentinel
                  </div>
                  <div className="timeline">
                    {messages.map((m, i) => (
                      <div key={i}>
                        <ChevronRight size={14} />
                        {m}
                      </div>
                    ))}
                  </div>
                  {task.artifacts?.map((artifact: Data) => (
                    <div className="panel" key={artifact.path}>
                      <strong>{artifact.filename}</strong>
                      <p>{artifact.bytes} bytes · File reopened and verified</p>
                      <div className="actions">
                        <button
                          onClick={() =>
                            void act(async () => {
                              await call("host.openArtifact", {
                                filename: artifact.filename,
                              });
                            })
                          }
                        >
                          Open file
                        </button>
                        <button
                          onClick={() =>
                            void act(async () => {
                              await call("host.showArtifacts");
                            })
                          }
                        >
                          Show output folder
                        </button>
                      </div>
                    </div>
                  ))}
                  {approval && (
                    <div className="approval">
                      <div className="eyebrow">YOUR APPROVAL IS NEEDED</div>
                      <h2>
                        {String(
                          approval.details.action?.kind || "Review next action",
                        ).replaceAll("_", " ")}
                      </h2>
                      <p>
                        {approval.details.window || "Simulated workspace"}
                        {approval.details.target?.name
                          ? ` · ${approval.details.target.name}`
                          : ""}
                      </p>
                      {approval.details.mode === "documents" && (
                        <>
                          <p>{approval.details.output}</p>
                          <p>
                            {approval.details.replaces_existing
                              ? "An existing file will be backed up before replacement."
                              : "A new file will be created."}
                          </p>
                          <pre>
                            {JSON.stringify(approval.details.action, null, 2)}
                          </pre>
                        </>
                      )}
                      {approval.details.action?.x !== undefined && (
                        <p>
                          Position ({approval.details.action.x},{" "}
                          {approval.details.action.y})
                          {approval.details.action.end_x !== undefined
                            ? ` → (${approval.details.action.end_x}, ${approval.details.action.end_y})`
                            : ""}{" "}
                          · relative to the selected window
                        </p>
                      )}
                      {approval.details.action?.text && (
                        <blockquote>{approval.details.action.text}</blockquote>
                      )}
                      {approval.details.action?.key && (
                        <p>Key: {approval.details.action.key}</p>
                      )}
                      <details>
                        <summary>Action details</summary>
                        <pre>{JSON.stringify(approval.details, null, 2)}</pre>
                      </details>
                      <div className="actions">
                        <button
                          className="primary"
                          disabled={busy}
                          onClick={() =>
                            void act(async () => {
                              await call("run.approve", {
                                run_id: approval.run_id,
                                token: approval.token,
                                approved: true,
                              });
                            })
                          }
                        >
                          Approve once
                        </button>
                        <button
                          disabled={busy}
                          onClick={() =>
                            void act(async () => {
                              await call("run.approve", {
                                run_id: approval.run_id,
                                token: approval.token,
                                approved: false,
                              });
                            })
                          }
                        >
                          Decline
                        </button>
                      </div>
                    </div>
                  )}
                </>
              ) : (
                <div className="welcome">
                  <span className="hero-mark">S</span>
                  <h1>What would you like to do?</h1>
                  <p>
                    A local assistant for your desktop.
                    <br />
                    You choose the window. You approve the actions.
                  </p>
                  <button onClick={() => setPage("models")}>
                    Set up your model <ChevronRight size={15} />
                  </button>
                </div>
              )}
            </div>
            <div className="composer">
              <textarea
                aria-label="Task instruction"
                placeholder="Describe a desktop task, or create report.docx with content…"
                value={goal}
                disabled={running}
                onChange={(e) => setGoal(e.target.value)}
              />
              <div className="composer-bar">
                <select
                  aria-label="Model profile"
                  disabled={running}
                  value={profile}
                  onChange={(e) => {
                    setProfile(e.target.value);
                    setVision(
                      Boolean(
                        profiles.find((p) => p.name === e.target.value)?.vision,
                      ),
                    );
                    setCloud(false);
                  }}
                >
                  {profiles.map((p) => (
                    <option key={p.name}>{p.name}</option>
                  ))}
                </select>
                {running ? (
                  <button
                    className="stop"
                    disabled={busy}
                    onClick={() =>
                      void act(async () => {
                        await call("run.cancel", { run_id: task.id });
                      })
                    }
                  >
                    <Square size={13} /> Stop
                  </button>
                ) : (
                  <button
                    className="primary"
                    aria-label="Start task"
                    disabled={
                      busy ||
                      goal.trim().length < 3 ||
                      (!documentTask && !windowId) ||
                      !status.version
                    }
                    onClick={() => void start()}
                  >
                    <ArrowUp size={18} />
                  </button>
                )}
              </div>
            </div>
            <div className="task-options">
              <select
                aria-label="Task type"
                value={taskMode}
                disabled={running}
                onChange={(e) => setTaskMode(e.target.value)}
              >
                <option value="auto">Auto: desktop or documents</option>
                <option value="desktop">Control a window</option>
                <option value="documents">Create or edit documents</option>
              </select>
              <button
                disabled={busy || running}
                onClick={() =>
                  void act(async () => {
                    const imported = await call("host.importDocument");
                    if (imported) {
                      setTaskMode("documents");
                      setNotice(
                        "Imported " +
                          imported.filename +
                          ". Use this filename in your instruction.",
                      );
                    }
                  })
                }
              >
                Import document
              </button>
              <button
                disabled={busy}
                onClick={() =>
                  void act(async () => {
                    await call("host.showArtifacts");
                  })
                }
              >
                Output folder
              </button>
              <select
                aria-label="Target window"
                disabled={running || documentTask}
                value={windowId}
                onChange={(e) => setWindowId(e.target.value)}
              >
                <option value="">Choose an open window</option>
                {windows.map((w) => (
                  <option key={w.window_id} value={w.window_id}>
                    {w.title} · {w.app}
                  </option>
                ))}
              </select>
              <button
                disabled={busy || running}
                onClick={() =>
                  void act(async () => setWindows(await call("windows.list")))
                }
              >
                Refresh windows
              </button>
              <label className="check">
                <input
                  type="checkbox"
                  checked={single}
                  disabled={running}
                  onChange={(e) => setSingle(e.target.checked)}
                />{" "}
                Single step
              </label>
              <label className="check">
                <input
                  type="checkbox"
                  checked={vision}
                  disabled={running}
                  onChange={(e) => setVision(e.target.checked)}
                />{" "}
                Vision
              </label>
              <label className="check">
                <input
                  type="checkbox"
                  checked={ocr}
                  disabled={running || !vision || !status.ocr_available}
                  onChange={(e) => setOcr(e.target.checked)}
                />{" "}
                OCR
              </label>
            </div>
            {documentTask ? (
              <p className="hint">
                Examples: create report.docx with content Hello world; append
                report.pdf with content Appendix; set B3 to 42 in results.xlsx.
                For tables, use create results.xlsx with content {"{"}
                "headers":["Item","Value"],"rows":[["Sales",42]]{"}"}. Files
                stay in the output folder; import an existing document to edit
                it. No model or target window is required.
              </p>
            ) : (
              <label className="check">
                Expected result text (optional)
                <input
                  aria-label="Expected result text"
                  value={expectedText}
                  maxLength={300}
                  disabled={running}
                  onChange={(e) => setExpectedText(e.target.value)}
                  placeholder="Text to verify in the target window"
                />
              </label>
            )}
            <label className="check consent">
              <input
                type="checkbox"
                checked={cloud}
                disabled={running}
                onChange={(e) => setCloud(e.target.checked)}
              />{" "}
              Allow this task's observations and screenshots to go to a cloud
              model.
            </label>
            <p className="hint centered">
              Experimental computer control · Stop prevents further steps; it
              cannot undo an action already sent.
            </p>
          </section>
        )}
      </main>
    </div>
  );
}
