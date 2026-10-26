# SentinelAgent

An offline-first, safety-aware computer-use agent research MVP. It can use Ollama, OpenAI-compatible servers, OpenAI, Anthropic or Gemini for constrained planning while deterministic code owns policy decisions, execution and postcondition verification.

## Desktop app (Windows preview)

The `desktop-application` branch adds the approved conversation interface using Electron, React and TypeScript, with the existing Python agent in a separate process. This is our chosen architecture, not a claim about the complete proprietary stack of Claude or Codex.

From the repository root, with Node.js 22.12+ (tested with 24) and Python 3.11+:

```powershell
python -m pip install -e ".[desktop,documents,dev]"
cd desktop
npm ci
npm start
```

In **Models & setup**, enter an Ollama server URL (normally `http://127.0.0.1:11434`), discover installed models, choose a vision-capable model ID, and save/test the profile. Local OpenAI-compatible servers such as LM Studio use their `/v1` endpoint. Cloud profiles support OpenAI-compatible, Anthropic and Gemini endpoints. Provider selection does not infer a URL or model: fill both explicitly. Anthropic/Gemini testing reports credential configuration only, not verified authentication.

Ollama executable selection is optional: browse to `ollama.exe`, auto-detect it, or paste an absolute path. **Save path** then **Start Ollama** requests confirmation before launching that executable on loopback. An already-running server is not stopped. Models are installed separately through Ollama; the app does not silently download model weights. Tesseract on PATH is optional for OCR.

Select an already-open permitted window, enter an instruction, and start. Single-step is enabled by default. Review each proposed input, then approve once or decline. **Expected result text** optionally verifies completion from the target's observed content. **Stop task** requests cancellation and cannot undo dispatched input or immediately interrupt a hung Windows accessibility provider. Switching windows and app launching remain CLI workflows. The desktop interface exposes only native-window and real document workflows; simulator mode is rejected by the bridge.

### Word, Excel and PDF in the desktop conversation

**Auto** routes instructions containing a .docx, .xlsx or .pdf filename to local document operations. You can also select **Create or edit documents**. These operations need neither a model server nor a target window:

```text
create report.docx with content Weekly project summary
append report.docx with content Next steps
create report.pdf with content Project overview
append report.pdf with content Appendix
create results.xlsx with content {"headers":["Item","Value"],"rows":[["Sales",42]]}
set B3 to 99 in results.xlsx
```

Review the operation, contents and destination, then **Approve once** or **Decline**. Word edits append paragraphs; PDF edits append pages; Excel edits set cells (including formulas, without recalculating them). The compact command parser currently uses simple filenames and the default Sheet1 sheet for cell edits; it does not implement arbitrary Word layout editing.

**Import document** copies an existing file into the output folder and leaves its original unchanged. **Output folder**, **Open file** and **Show output folder** expose the real generated files. Outputs stay in the app's agent/artifacts folder. Existing files are backed up before atomic replacement, changed files require a fresh review, and each output is reopened with its format library before success is reported. A write already approved and running finishes atomically before Stop completes. No document content is sent to a model for these commands.

The backend uses portable Python libraries (python-docx, openpyxl, reportlab, pypdf), bundled by the runtime packaging script. This path does not depend on Codex-installed tools. Verification checks file structure and readable content; it is not a visual layout review in Microsoft Office.

Portable builds store profiles, history, credentials and Chromium session data in **`SentinelData` beside the executable**. Move the executable and that folder together, with the app closed, to keep your setup. The folder must be writable. Development/unpacked builds retain per-user AppData; `SENTINEL_DESKTOP_DATA` explicitly overrides either location. Existing AppData is not automatically moved or deleted. API keys are Windows-encrypted ciphertext, never in profile JSON, and normally need re-entering on a different Windows account or PC. Undecryptable keys no longer prevent local-model startup; originals are preserved. Leaving the key blank preserves an existing saved key. Do not treat OS encryption as protection against software already running as your Windows user. Explicit per-task consent is required before cloud observation transfer. Task goals and outcome summaries are stored locally; screen images and full action traces are not saved in desktop history.

The renderer has no Node access, remote content, generic shell IPC or network fetch capability. A sandboxed preload exposes an allowlisted bridge; the Python child communicates over private stdin/stdout. The controller's own approval window is excluded from native targets. Existing egress and native-action safeguards still apply; this is an experimental agent, not OS-wide network isolation.

### Desktop checks and Windows packaging

```powershell
# From desktop/
npm run build
npm test
npm run smoke
# Browser-only UI fixture; install Playwright separately or set PLAYWRIGHT_MODULE.
npm run test:ui
# Real private bridge and real files, without a model:
npm run test:documents
# Full Electron conversation, real Python and real document files:
npm run test:documents:ui
# From the repository root with Windows unlocked and local Ollama:
python -m benchmark.native_service_smoke

# Build an unsigned portable app with a bundled Python runtime:
python -m pip install pyinstaller
npm run runtime
$env:ELECTRON_BUILDER_COMPRESSION_LEVEL = "3" # faster local packaging
npm run package
npm run test:package
```

The portable build goes into `desktop/release/`. Run the `.exe` directly without installing Python, Node.js or Sentinel. It includes Python but not Ollama, model weights or Tesseract. Re-select any machine-specific Ollama executable path after moving computers, or connect to an existing model server. The launcher extracts its runtime into a temporary directory while running; portable does not mean zero traces outside the folder. Windows may warn about the unsigned binary; no signing certificate or auto-updater is configured. Build output, local test data and dependencies are ignored by Git.

Validation: document integration tests create and edit real DOCX, XLSX and PDF files through the desktop service, and the Electron test drives the actual conversation, IPC bridge, approvals and file outputs. Separate renderer fixtures cover native-task UI events. An opt-in native smoke harness uses a dedicated Win32 input fixture, local Ollama and expected-text readback; it requires Windows to be unlocked. Native drawing across arbitrary applications is not certified by document or renderer tests. The existing local-model accuracy limitations still apply.

## Where a local 7B/8B model fits

The model does **not** receive unlimited control of the computer. It selects from a small action vocabulary and returns a typed plan. DOM/accessibility data, OCR and explicit tool results provide observations; deterministic policy and verification code decide whether actions are allowed and successful. This makes a quantized 7B/8B model viable for a focused domain.

This workspace defaults to the already-installed `qwen2.5-coder:7b` (7.6B parameters, Q4_K_M, about 4.8 GB). Parameter count alone does not establish reliable computer use. The general desktop experiment uses `qwen3-vl:2b-instruct` with optional local OCR, or current accessibility observations without images. On this machine, OCR-assisted 2B proposals passed three Calculator target checks; screenshot-only targeting still failed. See the measured results below before choosing a model or enabling native input.

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

## Experimental general desktop agent (`agentic-computer`)

This branch adds a shared **observe -> propose one action -> approve -> act ->
observe again** loop for an operator-selected Windows window. It does not have
Paint/Calculator/Notepad task scripts or fixed button coordinates. The model
sees current accessibility labels, roles, bounds and content; a vision-capable
model also receives a screenshot of that window. It can propose click,
double-click, hover, drag, literal text entry, allowlisted keys, scroll and wait.
Targets are current observed IDs or screenshot-relative pixels, not generated
shell commands or Python code.

Model output coordinates and executor coordinates are separate. Official
Qwen3-VL names automatically use the model's normalized `0..1000` convention;
the adapter validates the response, converts each axis to window pixels, then
checks bounds and sensitive targets again. Other models default to pixels.
For a renamed model, explicitly set `--desktop-coordinates normalized_1000`
or `--desktop-coordinates pixels` when adding its profile. Never reinterpret
an old pixel-mode result as a normalized result. See the
[Qwen3-VL coordinate specification](https://arxiv.org/html/2511.21631v1#S3.SS2.SSS4).

Install the Windows dependencies, open a harmless scratch app, and select its
window explicitly:

```powershell
python -m pip install -e ".[desktop,dev]"
sentinel-agent desktop --list-windows
sentinel-agent desktop "Calculate 7 plus 5" --window-id 123456
```

Replace `123456` with the actual returned ID. The initial prototype binds to
**one existing window**; it does not autonomously launch or switch arbitrary
apps. Use the existing allowlisted `open_app` workflow or open an app yourself.
Restore maximized windows if their invisible borders extend outside the screen.
Keep the selected window visible and avoid moving the mouse during a drag.
Every input requires terminal approval. Approval explicitly returns focus to
the bound window, then its identity and observation are checked again. Ctrl+C
stops the runner; noninteractive `--json` mode denies all input.

Model configuration remains provider-independent. New default installations
include `local-vision` (`qwen3-vl:2b-instruct` through local Ollama); the existing active
text model is unchanged. If you already have a saved registry, add a profile:

```powershell
sentinel-agent models add local-vision --provider ollama --model qwen3-vl:2b-instruct --base-url http://127.0.0.1:11434 --vision
sentinel-agent desktop "Select the rectangle tool without drawing" --window-id 123456 --model-profile local-vision
sentinel-agent desktop "Enter hello into the document" --window-id 123456 --model-profile local-ollama --no-vision --expect-text "hello"
# Optional alternative: installed separately, never replaces the active text profile.
ollama pull qwen3-vl:4b-instruct
sentinel-agent models add local-vision-4b --provider ollama --model qwen3-vl:4b-instruct --base-url http://127.0.0.1:11434 --vision --timeout 180
# Optional local OCR when labels are missing; Tesseract must already be on PATH.
sentinel-agent desktop "Click the digit 7" --window-id 123456 --model-profile local-vision --ocr
```

`--ocr` adds screenshot-derived word locations, not fabricated accessibility
controls. It uses local Tesseract with generic grayscale/contrast processing,
bounded upscaling and two text-segmentation passes. Word boxes are mapped back
to the original screenshot, then expressed in the model's coordinate space.
OCR cannot establish button roles, editability, safe actions or task completion;
false/missing text is possible. Its input/output sizes and runtime are bounded;
there is no download, remote OCR fallback or temporary screenshot file. Missing
Tesseract fails closed. OCR requires vision mode and does not enable typing into
unobserved controls. Screenshots sent to the model remain unchanged.

`--vision` declares a model capability, not a performance guarantee. The model
must actually be installed and accept images. Ollama, OpenAI-compatible,
Anthropic and Gemini image payloads are supported. Only loopback model endpoints
are allowed by default. Remote/LAN observations require explicit
`--allow-cloud-observations`; remote transport must use HTTPS. That opt-in
permits sending the selected window's **text and pixels**. Existing text DLP
does **not** inspect secrets inside images. Screenshots are not saved by the
normal runner, and model/desktop errors do not echo pixel payloads. The egress
gateway governs Sentinel's model requests, not all network traffic of native
applications.

Known Ollama cloud model tags also require observation opt-in even if Ollama is
on localhost. This is not a proof that an arbitrary renamed local server/model
never relays requests: use trusted local weights/runtime. Desktop requests grant
only the selected canonical model endpoint, not every endpoint in the registry.

The executor checks process identity, current controls, bounds, focus, DPI,
occlusion and screenshot freshness. Known credential/security/terminal scopes
are refused, and text input requires a non-password editable control. These
guards are defense-in-depth, not an OS sandbox or a guarantee that unknown
custom interfaces contain no sensitive data. Use disposable documents and
supervise the prototype.

`--expect-text` checks Document/Edit content, not static text/button labels or the model's
claim. It is a narrow operator-supplied condition, not proof of every part of a
complex task. Visual-only goals return `needs_review` when the model finishes;
they are not falsely reported as verified. Calculator displays exposed only as
static text also require review. Native top-level tooltips/popups are not yet
merged into observations and may stop the run; hover is an input primitive,
not a guarantee of tooltip understanding. Blinking cursors/animations can
invalidate strict screenshot checks. Native UIA calls keep COM thread affinity
but a hung third-party UIA provider cannot currently be preempted by asyncio's
deadline. Generalization across **every** application is not claimed.

### Action feedback and non-text icons

Use `--single-step` to propose one input, ask for approval, execute it, capture
the result and stop for review instead of requesting another model action:

```powershell
sentinel-agent desktop --list-windows
# Replace 123456 with the selected harmless window's ID.
sentinel-agent models add local-vision-4b --provider ollama --model qwen3-vl:4b-instruct --base-url http://127.0.0.1:11434 --vision --timeout 240
sentinel-agent desktop "Click the square root button" --window-id 123456 --model-profile local-vision-4b --ocr --single-step --max-seconds 300
```

Inspect the proposed target before approving it; small-model icon recognition
is not reliable. A single-step run normally returns `needs_review` with a nonzero
exit status, not a false task-success claim. `--json` still denies live inputs.

The desktop loop records `action_feedback` after dispatch and re-observation:
whether document text, accessible controls, window dimensions or screenshot
bytes changed. It ignores regenerated observation/control IDs. Missing image
evidence is marked unknown, not unchanged. This feedback is available to the
next planning step, but a change does **not** mean the requested task succeeded.
An identical consecutive click, key, scroll, drag or text-entry proposal on an
observably unchanged interface stops with `needs_review: repeated-unchanged-action`
before dispatching it again. Every permitted input still requires approval.
Animations and cursor changes can defeat this conservative no-op check; it is
not a semantic verifier or a general undo mechanism. Delayed effects can also
require operator review instead of another click.
Failures/timeouts during dispatch are explicitly logged as an uncertain action
outcome and never automatically retried; a stale-before-input rejection is
distinguished from a potentially executed input.

OCR is optional supplementary text evidence. The vision model still sees the
whole screenshot, including unlabeled shapes, mathematical symbols and icons.
Accessibility can expose a semantic name even when a button has no visible text.
However, neither access to pixels nor OCR guarantees that this small model can
identify an unfamiliar icon. There are no hardcoded square-root coordinates or
Calculator workflows in the production planner. Unknown/incorrect targets must
not be accepted just because the model produced valid JSON.

Tests and read-only model probes:

```powershell
python -m pytest tests/test_desktop_agent.py tests/test_desktop_planner.py tests/test_desktop_windows.py tests/test_desktop_cli.py tests/test_desktop_ocr.py tests/test_desktop_probe.py tests/test_model_profiles.py -q
sentinel-agent desktop "Click the digit 7" --snapshot artifacts/desktop-probe/calculator-zero.json --propose-only --json
python -m benchmark.desktop_probe artifacts/desktop-probe/calculator-zero.json "Click the digit 7 button." --model qwen3-vl:2b-instruct
# Expected bounds are evaluator-only; the model never sees this ground truth.
python -m benchmark.desktop_probe artifacts/desktop-probe/calculator-zero.json "Click the digit 7 button." --model qwen3-vl:2b-instruct --ocr --expect-box 4 320 80 370
# Synthetic accessibility contract fixture, NOT a captured app or an end-to-end test.
python -m benchmark.desktop_probe benchmark/fixtures/desktop-labels.json "Select the Rectangle tool." --no-vision --expect-element f520a86c
```

Snapshots are local, user-supplied `DesktopSnapshot` JSON fixtures. `--snapshot`
requires `--propose-only` and never constructs a desktop executor. The probe
records model, snapshot hash, elapsed time and the proposed action separately
from execution evidence. Never equate a valid proposal with a completed UI task.
`--expect-box` and `--expect-element` check the requested `--expect-kind` (default
`click`) separately from JSON validity. A wrong target exits nonzero. An empty
accessibility fixture is rejected in `--no-vision` mode. `--coordinates` can
override the model convention for comparisons; scored proposals always use
window pixels. `--base-url` allows a separate loopback Ollama test server, with
an exact per-run gateway grant. Known cloud tags are refused by this benchmark.
`--output` exclusively creates an evidence file before inference, refusing an
existing path without spending another model request. Keep private screenshots
in ignored `artifacts/`; only the synthetic fixture is committed.
Wire formats follow [Ollama vision](https://docs.ollama.com/capabilities/vision)
and [structured output](https://docs.ollama.com/capabilities/structured-outputs)
documentation.

### Initial desktop test results (9 October 2026, before coordinate/OCR changes)

Full automated regression: **453 passed, 2 skipped in 75.48 seconds**.
The skips are existing opt-in integration tests; no live model accuracy claim
is implied by the unit-test count.

The local vision experiment did **not** establish reliable autonomous control.
Real screenshots were captured through the computer-use tool; model requests
used this project's planner and egress gateway. The native Python input backend
has **mock-only** coverage (45 tests), not a completed live end-to-end run.
Calculator and the existing Paint drawing were not edited by model actions.

| Check | Observed result |
| --- | --- |
| Initial 2B Paint screenshot probe, 1536 x 864 | Timed out at 180 seconds |
| Initial 2B Calculator screenshot probe, 322 x 534 | Timed out at 120 seconds |
| Compact prompt with old permissive grammar | Invalid action, 41.50 seconds |
| Compact prompt with exact-field grammar, 2B Instruct | Valid click proposal in 2.33 seconds on a warm cached run, but **wrong target** |
| Same final grammar, installed 4B model | No valid action within the 250-token output budget; 88.52 seconds |

For the 2B result, the requested Calculator `7` button occupied approximately
x=4..80, y=320..370 in the 322 x 534 capture. The model proposed (174,324),
outside that button. That proposal was **not executed**. Valid JSON and
in-window coordinates do not establish that a target is semantically correct.
The 2.33-second warm result is not a cold-start latency guarantee. The first
2B result was improved by removing duplicate schema text and requiring exact
action fields during decoding, but this did not fix visual grounding accuracy.

The installed 4B tag advertises thinking support; it was tested with
`think=false`, but did not yield a valid final action within this bounded run.
An Instruct profile is the lightweight default for further experiments, not a
claim that 2B vision is sufficient. Larger/GUI-specialized models and richer
accessibility targeting still need comparative testing. No models were
downloaded during that initial experiment and no cloud screenshot requests were made.

Local evidence is under `artifacts/desktop-probe/` (ignored by Git), including
`calculator-strict-2b.json`, `calculator-strict-4b.json`, snapshots and earlier
failed probes. Do not publish screenshot fixtures without reviewing them.

### Coordinate and local OCR follow-up (9 October 2026)

Full automated regression: **662 passed, 2 skipped in 50.32 seconds**. The
skips are the opt-in public YouTube and installed local browser-model tests.
This includes bounded OCR, coordinate conversion, model/profile compatibility,
privacy guards, independent target scoring and cancellation evidence tests.
Automated test counts are separate from the real-model proposal checks below.

Machine: Intel Core Ultra 5 125H, approximately 15.43 GiB usable RAM, Intel Arc
integrated graphics with shared system memory. Runtime: Ollama 0.13.5 and local
Tesseract 5.4.0. The `qwen3-vl:4b-instruct` weights were downloaded for this
comparison; existing models and the active text-model profile were preserved.
Inference used loopback Ollama, not a cloud API.

All Calculator probes below used the same real 322 x 534 screenshot. Targets
were independently specified pixel rectangles, never supplied to the model.
The model used normalized coordinates; results were scored after conversion
to window pixels. Each row is a single run, not a throughput or reliability
benchmark. No proposed click was executed.

| Input / runtime | Requested target | Proposal / result | Seconds |
| --- | --- | --- | --- |
| 2B Instruct, screenshot only, CPU | Calculator 7 | (60, 171), wrong button | 35.87 |
| 2B Instruct, screenshot only, Vulkan | Calculator 7 | (50, 176), wrong button | 29.17 |
| 4B Instruct, screenshot only, Vulkan | Calculator 7 | (32, 160), wrong button | 31.33 |
| 2B Instruct, screenshot + OCR, CPU | Calculator 7 | (37, 336), inside target | 66.59 |
| 2B Instruct, screenshot + OCR, CPU | Calculator 5 | (116, 390), inside target | 49.20 |
| 2B Instruct, screenshot + OCR, CPU | Calculator 3 | (195, 442), inside target | 48.49 |
| 4B Instruct, screenshot + OCR, CPU | Calculator 7 | (37, 336), inside target | 136.50 |
| 2B Instruct, synthetic labels only, CPU | Rectangle tool | Correct observed ID | 11.84 |
| 2B Instruct, screenshot + OCR, Vulkan | Calculator 7 | Native inference runtime crashed; no action | 32.00 |
| 4B Instruct, screenshot + OCR, Vulkan | Calculator 7 | Native inference runtime crashed; no action | 27.31 |

Local OCR took about 0.47 seconds in a separate extraction check, returning 31
words including the keypad digits. It also misread a header icon as a digit:
OCR is helpful evidence, not an infallible control map. Most CPU latency came
from model prompt evaluation, not OCR. Only three targets in one app were
scored successfully; these results do not establish drawing, arbitrary-app
generalization, task completion or safe unattended execution. The 4B CPU model
also selected 7 correctly with OCR, but took 136.50 seconds versus 66.59 for 2B
on that target. This single-target comparison does not rank overall model quality.

Live accessibility inspection through the available computer-use tool returned
no accessibility tree. The label-only check therefore used the explicitly
**synthetic** committed fixture, not a live app capture. It tests label-to-ID
selection only; the native input backend still has mock-only coverage.

An isolated, temporary Vulkan server detected the Intel GPU and offloaded the
language-model layers, but both OCR-assisted requests caused native runtime
exceptions and HTTP 500 responses. The precise crash cause was not established.
The 4B GPU run left only about 675 MiB of system RAM free in one observation;
integrated graphics does not provide a separate large pool of dedicated VRAM.
GPU acceleration was not enabled globally, and the temporary server was stopped.
For the current tested setup, use 2B Instruct + `--ocr` on CPU for supervised
experiments, expecting tens of seconds per proposal. A runtime/driver upgrade
would need a fresh comparison, not an assumed speed or accuracy improvement.

Evidence files remain local under `artifacts/desktop-probe/`, including
`calculator-ocr-2b-cpu.json`, `calculator-ocr-2b-cpu-digit5.json`,
`calculator-ocr-2b-cpu-digit3.json`, `calculator-ocr-4b-cpu.json`, and the failed
Vulkan probes. The benchmark
records correctness separately from JSON validity and returns a failure exit
code for wrong targets or inference errors.

### Symbol and supervised action follow-up (9 October 2026)

Full regression after the action-loop changes: **679 passed, 2 skipped in
44.93 seconds**. Tests cover single-step re-observation, approval preservation,
unchanged-action stopping, private-value-free feedback, uncertain dispatch and
the distinction between visual changes and verified completion.

Square root was tested on a new real Calculator screenshot showing 9, with no
accessibility tree available. Expected target bounds were evaluator-only.

| Local model input | Proposed point | Target correct? | Seconds |
| --- | --- | --- | --- |
| 2B Instruct, screenshot + OCR | (194, 390), digit 6 | No; not executed | 65.60 |
| 2B Instruct, screenshot only | (207, 200), memory row | No; not executed | 12.83 |
| 4B Instruct, screenshot + OCR | (195, 282), square root | Yes | 158.19 |

After the 4B proposal, the assistant inspected a fresh live screenshot, checked
the point and executed one click through the computer-use tool (`@oai/sky`).
The next screenshot visibly showed **3** and the expression **square root of 9**.
Setting up the input 9 was an assistant-selected click, not a local-model action.
This is a **supervised local-model targeting + live desktop-tool action** result,
not an autonomous run of Sentinel's native Python executor. That backend still
has mock-only coverage; the computer-use skill governed the actual Windows input.
Visual review, not the project's automated verifier, confirmed the result.

One successful symbol does not establish general icon understanding. The 4B
result supports trying it on difficult targets, with much higher CPU latency;
the cheaper 2B model failed this particular symbol both with and without OCR.
No app-specific coordinates, recipes, icon labels or corrected model answers
were added to production code. Screenshots and `sqrt-action-evidence.json` remain
local in ignored `artifacts/desktop-probe/`; all three model probes are retained.

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

## Experimental adaptive browser — `adaptive-browser` branch

This opt-in mode adds a closed feedback loop instead of executing one up-front
plan. It is a browser-only research prototype, not Codex/Claude feature parity.
The normal `sentinel-agent` commands remain unchanged.

Each turn captures bounded top-level page text and controls, asks the selected
model for exactly one action, validates its observation/control IDs, requests
approval where required, dispatches, and observes again. Supported actions are
click, fill, scroll, brief wait and finish. A model's `finish` does not establish
success: the current rendered text must match the user's `--expect-text`, and
the exact final URL must match `--expect-url` when supplied. Use a specific,
meaningful marker; this checks the declared predicate, not arbitrary task meaning.
The visible marker is page-supplied evidence, not a cryptographic proof of an
external transaction. Input values and hidden DOM text do not satisfy it.

Native Ollama uses a dedicated single-action JSON schema, including the current
snapshot ID and an enum of current element IDs, through `/api/chat`. It follows
[Ollama's structured-output interface](https://docs.ollama.com/capabilities/structured-outputs).
Application-side validation remains mandatory. Other existing profile adapters
use the one-action prompt and reject multi-action output; those providers have
not been live-evaluated for this experimental mode. There is no deterministic
fallback disguising a model failure as success.

| Module under `sentinel_agent/computer_use/` | Responsibility |
|---|---|
| `schema.py` | Strict actions, observations, bounded task settings and results |
| `browser.py` | Private authenticated context, observed DOM handles and guarded dispatch |
| `planner.py` | Existing model profiles adapted to a single next action, no silent fallback |
| `agent.py` | Observe/decide/approve/act loop, independent completion checks, bounded recovery |
| `cli.py` | Opt-in terminal interface, approvals, cloud-data consent and cleanup |

Install the existing browser extra and inspect the new command:

```powershell
python -m pip install -e ".[browser,dev]"
sentinel-agent browse --help
```

Command shape (replace the example URL and expected marker with your own test site):

```powershell
sentinel-agent browse "Search for hello world" `
  --url "https://your-test-site.example/search" `
  --expect-text "Results for hello world" `
  --model-profile local-ollama --max-steps 10 --max-seconds 360
```

The destination still needs to pass the existing egress policy; eligible unknown
public hosts require run-scoped approval. Private-network/IP restrictions cannot
be overridden by that approval. Arbitrary localhost sites are **not** exempted.
The automated tests create a precisely scoped loopback exception only for their
own temporary test server. Choose `--browser edge` to use installed Edge, or
`--headless` to hide the managed window. This mode always enables egress control.

Clicks and fills require fresh explicit approval, including an escaped target
label, origin and DLP-processed proposed fill text. `--json` or non-terminal stdin
declines those approvals; there is no production `--auto-approve` switch. Deadline
and Ctrl+C cancellation stop pending planning/approval/IO, followed by cleanup.
The wall-clock deadline includes human approval time. Managed pages close when
the run ends. Local-only defaults apply to extracted observations: selecting a
remote model also requires `--allow-cloud-observations`. That flag permits page
text to be sent through the existing prompt-DLP boundary; it does not guarantee
complete data sanitization. No screenshots or image uploads are implemented.

Security and reliability boundaries:

- Model actions cannot supply JavaScript, CSS selectors or arbitrary coordinates.
  Random IDs refer only to captured DOM handles. Old observations, replaced nodes,
  changed form values, URL changes and DOM mutations invalidate the action.
- Staleness detected before dispatch permits at most two re-observation/replanning
  attempts by default. Every replacement action needs approval again. Uncertain
  failures after dispatch stop the run; consequential actions are not blindly retried.
- Fixed driver code uses guarded synthetic DOM click/input events. This is not
  native mouse/keyboard control. Interfaces requiring trusted events, shadow DOM,
  iframes, file uploads/downloads, multi-tab workflows or visual grounding are not
  supported. Dynamic pages that keep mutating can exhaust the stale-state budget.
- Password, file, recognized credential/payment fields and associated forms are
  excluded. Ordinary hidden CSRF/locale fields remain unexposed without disabling
  otherwise usable forms. Non-web links/form actions are excluded. Field-name
  heuristics are not a complete classifier; arbitrary site scripts are not an OS
  sandbox and can behave differently from their labels. Use isolated test sites.
- Observations are capped at 12,000 text characters and 60 controls; large pages
  may omit relevant content. The local Ollama prompt further limits this to 3,000
  text characters and 20 controls to fit its context budget. Page text is treated as untrusted data in
  the planner prompt. That instruction is not proof of prompt-injection immunity;
  deterministic action constraints and approvals remain necessary.
- Trace output stores action kinds, opaque IDs, counts and outcomes, not raw page
  contents or typed values. The usual egress audit remains separate. Adaptive runs
  extend proxy idle timeout to the model profile's response timeout, bounded by
  the task budget with a 30-second floor, so CPU inference can return its first byte.
  Other gateway defaults and the overall task deadline remain in effect.

Reproduce the offline control tests and optional installed-model check:

```powershell
python -m pytest tests/test_computer_agent.py tests/test_computer_cli.py tests/test_computer_browser.py tests/test_computer_integration.py -q
# Optional: uses the installed qwen2.5-coder:7b on local Ollama, not a cloud API.
$env:SENTINEL_LIVE_MODEL_TEST = "1"
python -m pytest tests/test_computer_integration.py -k local_7b -q
$env:SENTINEL_LIVE_MODEL_TEST = "0"
```

The normal integration tests use actual headless Chrome with a deterministic
test planner and local HTML fixture. They verify the feedback loop and recovery,
not language-model intelligence. Test-only approvals are scoped to that fixture's
search controls. Optional Ollama evaluation measures a separate real-model path.

Full regression run on 8 October 2026: `239 passed, 2 skipped in 50.48s`.
The skips are the opt-in public YouTube test and local-model evaluation; the
latter was also run separately as described below. No cloud API was used.

Verified on 8 October 2026: the opt-in local `qwen2.5-coder:7b` test completed the
real Chrome fixture workflow (`1 passed, 2 deselected in 142.86s`). It ran on CPU
and used an explicit 180-second per-request timeout with a 360-second run budget.
This is one small workflow, not a general desktop benchmark. Initial attempts
failed closed: the old 30-second proxy idle timeout cut off inference, and the
general Plan adapter returned two actions instead of one. The adaptive timeout
and single-action schema changes address those observed failures. No browser
actions were executed from a rejected model plan.

For similarly slow CPU inference, configure a separate local profile rather than
silently changing your existing default:

```powershell
sentinel-agent models add adaptive-local --provider ollama `
  --model qwen2.5-coder:7b --base-url http://127.0.0.1:11434 --timeout 180
# Then use --model-profile adaptive-local --max-seconds 360 with browse.
```

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

### Zero Trust egress gateway

The gateway defaults on for agent-owned model HTTP calls. It combines endpoint
prompt inspection with an authenticated loopback forward proxy. This is an
application-scoped research control, **not an OS firewall or a full DLP product**.
Managed Chrome/Edge sessions supply proxy credentials in memory through Playwright;
see browser setup and verification limits below before a live demo.

```text
LLM adapter -> EgressClient -> destination pre-check -> JSON DLP
                                    |
                                    v
               authenticated HTTP / HTTPS CONNECT proxy
               -> destination policy -> DNS validation -> pinned IP
               -> ClientHello SNI comparison -> byte relay
                                    |
                                    v
                          permitted model endpoint

Desktop URL -> same policy + query DLP -> managed Chromium/Edge session
                                        (authenticated proxy; isolated context)

Client decisions + proxy connections -> per-run hash-chained JSONL
```

#### Files and responsibilities

| File under `sentinel_agent/egress/` | Responsibility |
|---|---|
| `config.py` | Normalize endpoints and derive exact allowlists from model profiles |
| `policy.py` | Destination decisions, DNS/IP checks, IP pinning, temporary approval grants |
| `dlp.py` | Recursive JSON scanning, validators, severity decisions and typed redaction |
| `sni.py` | Bounded TLS ClientHello parsing, including fragmented records |
| `proxy.py` | Authenticated HTTP forwarding, CONNECT tunnels, limits and byte counters |
| `client.py` | Model HTTP boundary and per-run gateway lifecycle on a dedicated event-loop thread |
| `browser.py` | Managed Chrome/Edge lifecycle, in-memory proxy credentials and page-request policy |
| `telemetry.py` | Canonical JSON, hash chaining, verification and summaries |
| `lab.py`, `redteam.py`, `measure.py` | Offline TLS fixture, security scenarios and benchmark |

`llm.py` uses `EgressClient` for model calls and health checks. `agent.py` owns
run lifetime, publishes DLP/policy events and handles eligible browser approvals.
`desktop.py` checks URLs before launching. No model decides network policy.

#### Destination rules

Local model exceptions are exact `(hostname, port, scheme)` tuples from trusted
operator-owned profiles. Only these permit local HTTP. All other raw IP literals
are denied. Malformed/non-ASCII hosts are rejected; punycode requires explicit
allowlisting. Matching uses normalized exact hostnames, never suffixes/wildcards.
Remote connections require HTTPS on port 443. Remote HTTP on port 80 returns
`tls-required`; other disallowed ports return `port`.

Every DNS answer must pass `ipaddress` checks: private, loopback, link-local,
multicast, reserved and unspecified destinations are denied. The proxy connects
to a validated numeric `pinned_ip`, not a freshly resolved hostname. Model-provider
hosts come from profiles; approved sites default to YouTube. Everything else is
denied. An unknown public browser destination can use the existing approval flow
to grant its exact host/port/scheme for that run only; approval cannot override
private-IP, syntax or transport restrictions. Model requests fail closed rather
than presenting a browser-action approval. Editing profiles is a trusted action.

The proxy binds only to `127.0.0.1`; port 0 chooses a free port per run. A random
token is required using Basic proxy authentication. `httpx` ignores environment
proxies (`trust_env=False`) and redirects are not followed. The proxy checks SNI
against CONNECT authority when SNI is present, without terminating TLS. No-SNI
ClientHellos are accepted under destination policy. After CONNECT returns 200,
an SNI rejection closes the tunnel and logs its reason; it cannot return a new
plaintext HTTP 403 inside a TLS handshake. Earlier policy denials return 403 and
`X-Sentinel-Block`; missing authentication returns 407.

Defaults: 10-second connect timeout, 30-second idle timeout, 64 connections,
16 KiB headers and 4 MiB plain-HTTP request bodies. HTTP forwarding rejects
ambiguous framing, rewrites Host and forwards one request per connection.
CONNECT relays both directions with backpressure and bounded ClientHello buffering.

#### Prompt DLP

DLP runs before encryption because the proxy cannot read encrypted request bodies.
It scans JSON strings/keys recursively; password-valued keys receive special handling.
Findings contain detector IDs, severity and scalar-relative offsets, not matched values.

| Detectors | Severity / validation |
|---|---|
| Private keys; OpenAI, Google, GitHub, AWS and Slack key patterns | High; marker/prefix/length |
| JWTs | High; decoded JSON header/payload and base64url signature (signatures are not JSON) |
| Password assignments and password-valued JSON keys | High; key-name pattern |
| Payment cards | High; digit count and Luhn checksum |
| Aadhaar | High; 12 digits and Verhoeff checksum |
| PAN | Medium; format pattern |
| Email and formatted phone numbers | Low; configurable pattern matching |

| Destination | High | Medium | Low |
|---|---|---|---|
| Local model | Allow, record findings | Allow | Allow |
| Model provider | Block, or configured redaction | Redact | Allow and record, or configured redaction |
| Approved site / other | Block | Redact | Redact |

Redaction inserts typed placeholders such as `[REDACTED:aws-key]`. Overlapping
matches prefer higher severity, and redacted JSON-key collisions fail closed.
These checks do not guarantee detection of encoded, split, novel or image-borne
secrets. They do not scan arbitrary TLS streams or authentication headers. Local
models intentionally receive original prompts. Audit logs exclude bodies and
credentials; they still expose destination metadata and need local access controls.

#### Run and inspect

```powershell
python -m pip install -e ".[dev]"
sentinel-agent --egress on "Check attendance and create a report" --no-model
sentinel-agent egress status
sentinel-agent egress report RUN_ID
sentinel-agent egress verify RUN_ID
python -m pytest -q
python -m sentinel_agent.egress.redteam --output benchmark/egress-redteam.json
python -m sentinel_agent.egress.measure --samples 50 --output benchmark/egress-measurements.json
```

Replace `RUN_ID` with the ID in the run's printed audit path. `egress status`
shows configured defaults, not a probe for a running proxy. Logs are saved as
`artifacts/egress/<run_id>.jsonl`, including an empty file for a no-network run.
Reports count decisions by rule, connection destinations, bytes and p50/p95
connection durations. Those durations are **not** the benchmark's added latency.
Request-level DLP decisions and connection-level records are separate events.

Environment settings: `SENTINEL_EGRESS_ENABLED=true`, `SENTINEL_EGRESS_PORT=0`,
`SENTINEL_EGRESS_CLOUD_LOW=allow`, `SENTINEL_EGRESS_REDACT_HIGH=false`, and
`SENTINEL_EGRESS_LOW_ENABLED=true`. Approved sites can be configured with the
JSON-list setting `SENTINEL_EGRESS_APPROVED_SITES`. `--egress off` deliberately
bypasses gateway policy/DLP for task execution; do not use it for security demos.
Standalone model health checks use the configured default gateway mode.

For managed browser actions, install `python -m pip install -e ".[browser,dev]"`.
Chrome or Edge must already be installed at the allowlisted executable path.
Playwright's supported proxy username/password options provide authentication
without placing the token in a URL, process arguments or a generated extension.
See the [Playwright proxy documentation](https://playwright.dev/python/docs/network#http-proxy).
No personal browser profile is reused, and no trusted root certificate is installed.
Missing browser dependencies fail the action; there is no unprotected fallback.

```powershell
sentinel-agent "Open Chrome, open YouTube, and search hello world" --no-model --live-desktop
```

An interactive terminal keeps the managed browser and proxy alive until Enter;
interactive chat mode keeps sessions until `:quit`. JSON or redirected-stdin runs
close sessions when the command exits. Managed browser windows close with the
gateway; other applications and existing personal browser windows are untouched.

The spec's DLP table allows low-severity cloud email, but its A3 scenario expects
redaction. The default follows the table; A3 explicitly uses `cloud_low="redact"`.
Set `SENTINEL_EGRESS_CLOUD_LOW=redact` to reproduce that stricter behavior in runs.

#### Threat model and limits

- Covers cooperating agent-owned model HTTP paths, accidental prompt-secret
  disclosure, unapproved destinations, private-address SSRF, DNS rebinding and
  visible SNI/CONNECT mismatch. It does not sandbox malicious Python code.
- A static test rejects `httpx` imports outside `egress/`. This is a regression
  guard, not runtime enforcement against raw sockets, another HTTP library or
  modified source. A firewall/WFP/VPN-style boundary would be needed for that.
- No TLS interception, certificate substitution, HTTPS body inspection or
  OS-wide capture. ECH can hide the inner SNI; matching visible SNI does not prove
  the encrypted HTTP Host matches, and cannot prevent all domain-fronting schemes.
- **Managed browser scope:** Chrome/Edge use a separate Playwright-owned process
  and isolated context, authenticated proxy, disabled QUIC, disabled implicit
  loopback bypass and non-proxied WebRTC UDP disabled. Service workers are blocked;
  page requests are destination-checked as well as checked at the proxy. Other
  browsers and personal/existing sessions are unsupported and not captured.
  Enterprise policies can interfere. This remains application routing, not OS
  enforcement. YouTube subresources can require separately reviewed allowlist
  entries; the implementation does not silently allow all Google domains.
- Navigation succeeds only after a successful document response and DOM readiness.
  A YouTube action additionally waits for a visible video-result entry and rejects
  an unexpected final URL. This does not prove video playback. Consent/CAPTCHA
  pages or a zero-results search fail verification instead of reporting success.
  Browser tests distinguish local authentication from an opt-in rendered YouTube
  search test. No authentication dialog is clicked and proxy auth stays enabled.
- The proxy and managed browsers are run-scoped and close together at shutdown.
  Existing browser traffic is not captured retroactively. Arbitrary browser form
  contents are not covered by endpoint prompt DLP; generated search text is.
- Hash chaining detects edits/reordering that invalidate the chain, but without
  an externally stored signed head it cannot detect suffix deletion or an attacker
  recomputing the entire chain. It is not an immutable audit ledger.
- Pattern DLP is not document classification, OCR or comprehensive exfiltration
  prevention. Local endpoints and operator-owned profiles are trusted configuration.
  Simulated email does not implement real outbound delivery.

Keep experiments on local fixtures and test accounts. Payments, CAPTCHAs and
security bypasses remain outside project scope.

#### Measured verification — 7 October 2026

Actual output from this implementation session:

```text
python -m pytest -q
144 passed in 7.64s

Red team:
attacks_passed: 14 / 14
block_cases_passed: 11 / 11
block_rate_percent: 100.0
false_positive_rate_percent: 0.0
legitimate_total: 4

TLS benchmark:
samples: 50
warmup_pairs: 3
direct_p50_ms: 6.361
direct_p95_ms: 6.972
proxy_p50_ms: 9.023
proxy_p95_ms: 9.895
added_p50_ms: 2.699
added_p95_ms: 3.582
payload_bytes: 4194304
throughput_MB_s: 75.268
```

Machine: HP, Windows 11 build 26200, Python 3.13.2, Intel64 Family 6 Model 170
Stepping 4, 18 logical CPUs. The benchmark uses IPv4 loopback TLS, fresh connections,
three warm-up pairs, then 50 paired direct/proxy samples with alternating order.
Added percentiles are computed from paired differences, not by subtracting the
two marginal percentiles. The single 4 MiB throughput check includes connection
setup; it is a sanity check, not sustained capacity. DLP and internet/provider
latency are excluded. A preliminary localhost-name run was discarded because
IPv6 fallback penalized only the direct path; both final paths use the same IPv4
address. Numbers are machine/run-specific, not a production performance guarantee.

Raw samples and complete scenario output are committed in
`benchmark/egress-measurements.json` and `benchmark/egress-redteam.json`.
The tests use fake DNS and loopback HTTP/TLS servers with temporary test certificates;
they do not send secrets or requests to real providers. Existing baseline tests
remain included, with mocked provider/desktop fixtures isolated from real egress.

| Case | Check / observed outcome |
|---|---|
| A1–A2 | Synthetic AWS key and checksum-valid card blocked for cloud prompts |
| A3 | Cloud email redacted under explicitly enabled strict-low configuration |
| A4 | Unknown public host denied |
| A5 | Host resolving to loopback/private ranges denied |
| A6 | Metadata IP literal denied |
| A7 | Public answer pinned; later private DNS answer denied |
| A8–A9 | Raw IP and remote HTTP denied |
| A10 | Real loopback CONNECT with mismatched ClientHello SNI closed and logged |
| A11–A12 | Unapproved punycode and nonstandard port denied |
| A13 | Real proxy rejects missing authentication with 407 |
| A14 | Static HTTP-client bypass guard detects a prohibited import |
| L1–L4 | Local prompt, clean cloud prompt, YouTube URL and invalid-checksum numbers allowed |

The 100% rate is **11/11 blocking scenarios**, not 14 blocked requests: A3 is
redaction, A7 is pinning, and A14 is a static check. Most red-team scenarios are
policy/DLP unit evaluations; A10/A13 exercise real proxy sockets. Four legitimate
fixtures are too small to estimate a real-world false-positive rate.

#### Browser authentication follow-up — 7 October 2026

Final default-suite output: `150 passed, 1 skipped in 44.24s`, with no warnings.
The skipped test is the explicitly opt-in public YouTube smoke test below.

Real headless Chrome and Edge loaded a local HTML document through the authenticated
proxy. Tests verified HTTP 200, the rendered page title, a matching allowed proxy
connection, no Proxy-Authorization header at the destination, no token in audit
logs, private-IP rejection and shutdown. Unit coverage checks in-memory credential
configuration, session reuse, result readiness, sanitized errors and CLI lifetime.
The browser extra is optional: local browser integration tests skip if its runtime
or the matching installed executable is missing.

An opt-in public-network test runs the actual `open_app` and `youtube_search`
desktop methods using headless Chrome. It checks HTTP 200, the final search URL,
the YouTube title, visible video-result entries and an allowed CONNECT audit record.
The test passed (`1 passed, 3 deselected in 16.81s`). An earlier combined-suite run
timed out on YouTube despite successful local authentication; its standalone rerun
passed. Public-network timing and site changes remain possible failure modes, not
reasons to disable policy or report an unverified success. Thumbnails, fonts and
ads on non-allowlisted domains were blocked; video playback was not tested.

```powershell
python -m pytest tests/test_egress_browser.py -q
# Optional: contacts YouTube with the non-sensitive query "hello world".
$env:SENTINEL_LIVE_BROWSER_TEST = "1"
python -m pytest tests/test_egress_browser.py -k youtube -q
$env:SENTINEL_LIVE_BROWSER_TEST = "0"
```

These browser checks do not change the earlier proxy latency measurement. The
proxy's expected initial 407 authentication challenges can appear in audit logs
before the browser retries with credentials; they are not evidence of a failed
navigation when the document and result checks subsequently pass.

#### Resume wording

- Built an asyncio HTTP/CONNECT egress proxy with DNS pinning and visible-SNI checks, measuring 3.582 ms p95 added loopback TLS latency.
- Implemented pre-encryption prompt DLP with checksum-validated card/Aadhaar detection, typed redaction, and destination-specific policies for local and cloud model calls.
- Passed 14 security scenarios, including 11/11 blocking cases and zero false positives across four legitimate fixtures; added hash-chained connection auditing.

Stack: Python, asyncio, TLS/SNI, TCP sockets, httpx, Ollama, FastAPI, pytest.

#### Interview guide

| Topic | Explain using this implementation |
|---|---|
| Forward vs reverse proxy | This is client-side outbound mediation; a reverse proxy fronts inbound server traffic. |
| CONNECT | The client requests a host/port tunnel. After 200, TLS bytes replace HTTP proxy framing. |
| TLS and SNI | Parse the visible ClientHello without decrypting application data. Missing SNI and ECH limit what is observable. |
| SNI comparison | Reject visible SNI differing from CONNECT authority; do not claim inspection of encrypted HTTP Host. |
| DNS/private addresses | An innocent hostname can resolve internally. Validate every returned address before connecting to prevent private-network SSRF. |
| Rebinding | Connect to the validated numeric IP. A changed later DNS result cannot change that socket's target. |
| Endpoint DLP | Scan JSON while plaintext exists inside the client, then let httpx perform ordinary end-to-end TLS. |
| Checksums | Luhn doubles alternating card digits and sums decimal digits; Verhoeff uses permutation/multiplication tables. Neither proves a number is issued or real. |
| Zero Trust vs VPN | Default-deny, per-connection policy is different from encrypted network transport; a VPN can coexist with Zero Trust controls. |
| asyncio | Stream readers/writers share an event loop; paired relay tasks, drain backpressure and timeouts bound resources. A dedicated thread keeps synchronous approvals from freezing it. |
| Hash chains | Each canonical record includes the prior digest; verification recomputes it. An external trusted head is needed to detect truncation or full rewrites. |
| Existing safety | Action approvals, process allowlists, confined paths and independent postcondition verification govern execution; egress adds destination/data checks. |

**1. Without HTTPS decryption, how do you stop leaks?** Destination enforcement uses
CONNECT authority, DNS/IP/port and visible SNI. Endpoint DLP inspects model JSON
before TLS. This does not inspect arbitrary browser traffic or guarantee all secrets
are detected; the two layers have separate, limited responsibilities.

**2. Why check IPs after DNS?** A public-looking allowed name can return a private
address, including cloud metadata/link-local addresses. All answers must be safe,
and the selected address is pinned. Checking only the hostname misses that attack.

**3. How could someone bypass it?** Modified code could use raw sockets or another
library; a browser could ignore its flag. The import guard catches accidental httpx
regressions, `trust_env=False` ignores system proxy configuration, and the token
restricts proxy use. None is an OS sandbox; stronger enforcement needs a network
boundary outside the agent process.

**4. Walk through an OpenAI request.** The client prechecks the URL and scans JSON.
httpx connects to loopback and sends CONNECT plus Proxy-Authorization. The proxy
authenticates, validates DNS and policy, returns 200 and buffers ClientHello. It
compares visible SNI, opens the pinned IP on port 443, forwards the buffered bytes
and relays the remaining handshake/application data. httpx validates the upstream
certificate end-to-end. Byte counts and the outcome are logged on connection close;
the earlier DLP decision has its own request record.

**5. How does this relate to a VPN or Zscaler?** A VPN provides an encrypted transport
path; this prototype adds application-specific destination policy and endpoint DLP.
It demonstrates forward-proxy and least-privilege concepts, not equivalence to an
enterprise security platform. It lacks fleet identity, managed-device enforcement,
enterprise TLS inspection and production-scale operations.
