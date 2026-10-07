# SentinelAgent

An offline-first, safety-aware computer-use agent research MVP. It can use Ollama, OpenAI-compatible servers, OpenAI, Anthropic or Gemini for constrained planning while deterministic code owns policy decisions, execution and postcondition verification.

## Why an 8B model is enough

The model does **not** receive unlimited control of the computer. It selects from a small action vocabulary and returns a typed plan. DOM/accessibility data, OCR and explicit tool results provide observations; deterministic policy and verification code decide whether actions are allowed and successful. This makes a quantized 7B/8B model viable for a focused domain.

This workspace defaults to the already-installed `qwen2.5-coder:7b` (7.6B parameters, Q4_K_M, about 4.8 GB). It is enough for the constrained planner, although it runs at 100% CPU on the detected machine. For a later quality upgrade, try `qwen3:8b-q4_K_M` (about 5.2 GB); for speed, use `qwen3:4b-q4_K_M`. Add `qwen3-vl:2b` only for screenshots where DOM/OCR cannot identify the target.

## Quick start (Windows PowerShell)

```powershell
# Already present on this machine; only needed elsewhere:
ollama pull qwen2.5-coder:7b
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
sentinel-agent
```

The terminal is the primary interface. Enter a task at the `sentinel ›` prompt. Use `:help` to see commands, `:model off` for the fast deterministic planner and `:popup on` to test recovery.

## Choose or change the model

Model selection is profile-based. The active profile is persistent, while `--model-profile` overrides it for one run. Profiles contain only connection metadata and the **name** of an API-key environment variable; SentinelAgent never writes the secret itself to disk.

```powershell
sentinel-agent models list
sentinel-agent models test local-ollama
sentinel-agent models use local-ollama
sentinel-agent --model-profile lm-studio "create report.docx with content model test"
```

Included profiles support native Ollama, LM Studio and other OpenAI-compatible servers (such as vLLM or llama.cpp), OpenAI, Anthropic, and Gemini. Add another compatible local or hosted server like this:

```powershell
sentinel-agent models add my-server `
  --provider openai_compatible `
  --model my-model-id `
  --base-url http://127.0.0.1:8001/v1
```

For a cloud provider, put the key in an environment variable and store only its variable name:

```powershell
$env:OPENAI_API_KEY = "your-key"
sentinel-agent models use openai
sentinel-agent models test openai
```

Inside interactive mode, use `:models`, `:model use NAME`, `:model on`, and `:model off`. Model choice changes only planning; every provider still uses the same action allowlist, approval policy and post-action verifier.

Run one task directly:

```powershell
sentinel-agent "Check attendance and create a report" --no-model
```

Desktop actions are simulated by default. To launch allowlisted Windows apps for real:

```powershell
sentinel-agent "Open Chrome, open YouTube, and search hello world" --no-model --live-desktop
```

Currently allowlisted: Chrome, Edge, Notepad and Calculator. YouTube searches are encoded into a safe HTTPS results URL; arbitrary commands and executable paths are rejected.

Install the optional accessibility-based Windows control layer:

```powershell
python -m pip install -e ".[desktop,dev]"
```

The live agent can inspect accessible controls, focus a uniquely identified window, click a uniquely identified control, type into one editable control and press a small allowlist of keys. Generic input actions always pause for human approval. If multiple windows or controls match, execution fails closed and asks for a title/control hint rather than guessing.

For Notepad tasks, SentinelAgent creates and opens a uniquely named scratch file under `artifacts/`. It will not type into an existing document. The scratch filename is carried as a mandatory window-title hint for every subsequent action.

## Excel, Word and PDF files

SentinelAgent can create styled `.xlsx`, `.docx` and `.pdf` files inside `artifacts/`. Edits require approval and create a timestamped copy under `artifacts/backups/` before changing the original.

```powershell
sentinel-agent "create excel file results.xlsx with content experiment results" --no-model
sentinel-agent "set B3 to 95 in results.xlsx" --no-model

sentinel-agent "create word file report.docx with content project summary" --no-model
sentinel-agent "append report.docx with content final conclusion" --no-model

sentinel-agent "create pdf file report.pdf with content project summary" --no-model
sentinel-agent "append report.pdf with content appendix" --no-model
```

Excel files are generated through the bundled spreadsheet artifact runtime and receive a rendered preview plus formula-error scan. PDFs are structurally reopened and rendered through Poppler. DOCX files are structurally reopened after writing; visual DOCX rendering additionally requires LibreOffice, which is not installed on the current machine.

The optional web debugging interface remains available with:

```powershell
sentinel-agent-web
```

Then open <http://127.0.0.1:8000>.

Run the tests and benchmark:

```powershell
pytest
python -m sentinel_agent.benchmark
python -m sentinel_agent.benchmark --model
```

## Current MVP

- Local Ollama planner with strict JSON schema and action allowlist
- Deterministic fallback for reproducible demonstrations
- Fixed risk policy and approval gate for external side effects
- State-based verifier for every action
- Simulated college portal and popup disturbance
- Benchmark harness with normal, disturbed and approval scenarios
- FastAPI dashboard showing the complete execution trace
- Terminal-first interactive interface with approval prompts and verified traces

## Research roadmap

### Phase 1 — foundation (weeks 1–4)

Complete the included safe simulator, collect 30 task variants and measure planner JSON validity, step accuracy, latency and RAM usage for 4B versus 8B models.

### Phase 2 — real browser perception (weeks 5–8)

Add Playwright in a Docker or disposable browser profile. Build a compact observation from the accessibility tree, visible text, URL and a screenshot. Use OCR first; call a small vision model only when grounding confidence is low.

### Phase 3 — recovery (weeks 9–11)

Introduce controlled disturbances: popups, renamed buttons, delayed loads, reordered tables and stale pages. Add bounded retries, re-observation and re-planning. Never retry actions with external side effects automatically.

### Phase 4 — benchmark and experiments (weeks 12–14)

Grow to at least 150 tasks. Compare deterministic baseline, 4B planner, 8B planner, planner plus verifier, and planner plus verifier plus recovery. Report task success, unsafe-action rate, recovery rate, latency and energy/cost.

### Phase 5 — final artifact (weeks 15–16)

Produce the reproducible benchmark, ablation study, architecture documentation, demo video and an IEEE-format paper. Keep real credentials and irreversible actions outside the research environment.

## Safety boundary

### Zero Trust egress gateway — implementation in progress

The first implementation stage adds `sentinel_agent.egress.config` and
`sentinel_agent.egress.policy`. These derive exact local model endpoints and
provider host allowlists from operator-owned model profiles, allow YouTube as
an approved site, reject raw IP destinations outside configured local models,
enforce remote HTTPS, and check every DNS answer for prohibited IP addresses.
An allowed decision contains the validated `pinned_ip` for a future proxy to
connect to without resolving the hostname again. Unknown public hosts remain
denied and return `approval_required=True` for later approval integration.

The policy is not yet connected to model HTTP calls or browser actions: **this
stage alone does not enforce network routing**. Proxy, DLP, telemetry, approval
integration, and measured red-team results follow in separate stages.

Policy details: matching is by exact normalized hostname, never suffix or
wildcard; local exceptions include scheme and port; non-ASCII hostnames are
rejected (use an explicitly configured ASCII IDNA hostname when needed).
Punycode hosts need explicit allowlisting and still undergo DNS checks.
Plain remote HTTP on port 80 reports `tls-required`, matching scenario A9;
other prohibited ports report `port`. Configuration of model profiles is a
trusted operator action, since adding a cloud profile grants its hostname.

Run the offline policy tests with `python -m pytest tests/test_egress_policy.py -q`.
They use fake resolvers and do not contact the internet.

Real browser control is disabled by default. Keep all experiments on a local portal clone or accounts created specifically for testing. Credentials, CAPTCHAs, payments and security bypasses are outside project scope.
