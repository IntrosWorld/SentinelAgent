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
