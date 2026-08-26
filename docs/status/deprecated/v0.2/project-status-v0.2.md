# Project status

Updated: 2026-08-25

| Area | Status | Evidence boundary |
|---|---|---|
| Design v0.2 | `proposed` | Current design basis; not implementation proof |
| Repository framework | `validated_live` | Python 3.12.9; 262 tests passed; W3 paired live boundary remains unchanged by offline W4/W5.0-W5.4 additions |
| W0 | `validated_offline` | W0.1-W0.5 passed; 12 frozen cases, runtime contracts, and read-only legacy inventory are consistent |
| W1 | `validated_live` | W1.0-W1.5 passed at frozen boundaries; LIVE-01 review note and LIVE-02 repository identity both delivered explicit partial results with raw evidence retained |
| W2 | `validated_live` | BM25/Web Fetch/Evidence delivery validated offline; three accepted live tasks across paper discovery and project evidence Q&A produced bounded partial deliveries |
| W3 | `validated_live` | Persistence/recovery passed offline; original W3.6 and post-refactor R1 each passed 2/2 frozen paired live Runs through the durable CLI |
| W4 | `closed_negative_fixed_default` | W4.5 rejected C at 1/3 versus the required 2/3; W4.6 stayed blocked with zero live/paid calls, and W5.0 closed the candidate while retaining Fixed as default |
| W5 | `w5_7_r1_source_reliability_validated_reaudit_pending` | Bounded arXiv request governance and normal/cache live paths passed; the prior five-task release decision remains rejected pending a fresh audit |
| W6 | `pending` | No second product slice has been implemented |
| Live capability | `bounded_retrieval_evidence` | qwen3.7-flash completed bounded arXiv metadata/abstract discovery and GitHub evidence Q&A; failures and raw responses retained |

W1.5 is validated live within its frozen scope. The authorized review PDF run
produced 19 claims and 26 closed citations from 16 selected pages; the earlier
uncited/truncated attempt is retained as rejected evidence. The user's explicit
request for a detailed compact reading note is recorded as the use decision,
without claiming later human content approval.

W2 is validated live within its frozen source and task boundaries. Accepted W2.5
runs are LIVE-01, LIVE-02, and LIVE-03-R3; the original GIS run and R1/R2 are
retained as rejected or failed evidence. This does not prove cross-database review,
full-text verification, general multi-source RAG, Dense/RRF/Reranker, or production
capability.

W3.1 is validated offline at implementation revision `7c6df6f`. The SQLite
Repository persists Task, initial Run/Step, Delivery, Artifact metadata, and
producer lineage; successful/partial terminal publication is transactional with
readable Artifact validation. Artifact files use same-directory temporary writes,
fsync, atomic replace, and integrity checks. These tests do not prove a durable
`discover-papers` workflow or cross-process recovery.

W3.2 is validated offline at implementation revision `6069491`. The single
Worker path enforces global concurrency 1, ordered Step claims, heartbeat,
lease-expiry takeover, monotonic fencing tokens, and current-Attempt ownership
for state, Artifact, and Delivery publication. Tests used in-process Workers;
no subprocess kill, workflow checkpoint, cancel/resume, Budget ledger, Trace,
network, or Provider execution was performed in that acceptance point.

W3.3 is validated offline at implementation revision `df6fe7e`. The durable
paper-discovery path persists five Artifact checkpoints and external-call intent.
Committed search and Provider responses are reused; an unknown Provider outcome
enters `waiting_for_user` with zero automatic replay until an explicit retry or
fail decision. Queued and in-flight cancellation start no subsequent external
Step. Validation used fixture transports and simulated process exit, not real
subprocess kill, network, Provider, or paid calls.

W3.4 is validated offline at implementation revision `5f31dfc`. External-call
authorization and worst-case reservation are transactional; reported actual usage
is settled with explicit release entries. Insufficient reservation or expired wall
clock starts zero external calls. Actual output overage stops the Run before later
Steps. Structured errors retain technical-detail and affected-Artifact lineage after
Repository reopen. Monetary enforcement remains unavailable without a frozen price
snapshot. Validation used fixtures only.

W3.5 is validated offline at implementation revision `e85fdc1`. Optional Trace
exports Task/Run/Step/Attempt, workflow, Provider/model, Budget, status, Artifact,
and OpenInference span-kind fields without owning business state. Missing OTel and
Exporter timeout produce sanitized persistent drops while the same deterministic
Delivery completes. Five independent child Workers were terminated with real
`process.kill()` at frozen call/checkpoint/publication boundaries; recovery reused
committed effects, did not replay an unknown Provider call, and preserved Citation
closure. No network or real Provider was used in W3.5.

W3.6 is validated live at execution revision `9a26fbf`. The durable CLI prerequisite
was implemented at `f975755` and verified with 14 focused tests plus 172 full
regression tests. `W3.6-LIVE-01` and `W3.6-LIVE-03-R3` each completed five first
Attempts and five released Leases, with one arXiv call and one qwen3.7-flash call
per Run and no retry/fallback. Both delivered usable `partial`; all 11 Claims close
through Citations to Evidence and all 16 Evidence records resolve to SourceSnapshots.
The reports retain arXiv metadata/abstract and unverified publication/full-text/
cross-database limitations. Raw SQLite, requests, responses, reports, manifests,
hashes, and the secret scan are retained under the ignored W3 live evidence root.
This proves only the two frozen live tasks, not production or general Agent capability.

W3.6-R1 is validated live at execution revision `89019d2` after the responsibility-based
CLI, SQLite Repository, and Durable Runtime split in `ecc5ffc`. It did not inherit the old
source hashes: 20 current source files were frozen again and matched at execution. Both
paired Runs completed five first Attempts and five released Leases with one arXiv and one
qwen3.7-flash call each, no retry/fallback, and usable `partial` reports. The two Runs contain
12 closed Claims, 16 Evidence records resolving to SourceSnapshots, 32 hash-valid Artifact
files, no SQLite foreign-key failures, no Error/telemetry drop, and no Secret scan hit. This
revalidates the refactored current implementation only for the same two bounded tasks.

W4.2 is validated offline against base revision `01ffc319`. Framework-independent
Context, strict Plan parsing, deterministic validation, and Tool Gateway contracts passed
23 focused tests and the then-current 195-test full regression. This proves contract and
fake-dispatch behavior only, not Provider planning or durable execution.

W4.3 is validated offline against the same uncommitted W4 working tree. The bounded
strategy prebuilds nine ordered Steps and reuses W3 SQLite, Lease/Fencing, Budget,
Artifact, unknown-Provider, Evidence and Delivery semantics. Fixture tests passed one-
and two-search plans, deterministic second-slot skip, structured Plan rejection, budget
denial, committed-response reuse, unknown Planner protection, strategy queue isolation,
and bounded CLI submission. The focused result is 9 passed and the full regression is
204 passed. No public network, real arXiv, real Provider or paid call was made; quality
comparison, broader fault matrices and live capability remain unverified.

W4.4 is validated offline against the same W4 working tree. Six adversarial Plan/
injection cases were rejected before arXiv, four budget/usage stop cases started no
later external call, and queued/in-flight cancellation preserved the same boundary.
Seven local child Workers were terminated at paid-unknown, replayable-read, committed-
response and pre-publication points. Paid unknown results had zero automatic replay;
the interrupted search replayed once under fencing; committed effects were reused;
recovered Deliveries retained readable Artifacts and Citation/Evidence closure. Trace
failure remained non-authoritative. The matrix found and fixed bounded span-kind and
Prompt-version classification. Results were 23 focused, 83 W3/W4 combined and 227 full
tests. All calls used fixtures; this is not live or production reliability evidence.

W4.5 completed the frozen offline A/B/C decision against the same uncommitted W4 working
tree. Nine independent SQLite/Artifact roots executed A expert fixed, B deterministic and
C bounded Planner strategies over `CW-PR-005/009/010`. C matched B on 005 and 010 and
improved direct GIS theme coverage only on 009, so it achieved 1/3 against a frozen 2/3
minimum. All Runs retained `partial`, closure 1.0, zero hard vetoes, zero structural
failures, Provider-token ratio 1.375 and logical fixture latency ratios 1.5-2.0. The
mechanical decision is `reject`; fixed remains default and new bounded submissions are
disabled while implementation and evidence remain readable. The blind pack is honestly
marked `awaiting_human_review`; no human approval is claimed. No public network, real
arXiv, Provider, paid call or W4.6 execution occurred.

W4.6 preflight was explicitly authorized by the user and then stopped at its candidate
gate. The frozen protocol requires C to pass W4.5 before paired live validation, while
W4.5 mechanically rejected C and disabled new bounded submissions. Local Provider
configuration is complete for `qwen3.7-flash`, but exact new cases, budgets and review
protocol were not frozen because they cannot cure the failed prerequisite. No public
network, real arXiv, Provider or paid call was started. Recovery requires either keeping
fixed as the W4 outcome or freezing a materially revised C and repeating W4.5 first.

W5.0 is validated offline against clean revision `af69a33` and Git tree `5cf39f1`.
It closes W4 as a negative candidate decision while preserving Fixed as default, and
freezes the single FastAPI/static Workbench direction, port 8000, `var/` data layout,
golden path, API boundary, non-goals and later authorization gates. The first explicit
basetemp run retained `102 passed, 127 setup errors` because its ignored parent did not
exist; after creating that parent the unchanged baseline passed 229 tests. With four
W5 scope checks added, the final suite passed 233 tests and offline sdist/wheel build.
No FastAPI/Uvicorn dependency, product code, browser, public network, Provider, paid
call or live Run was introduced; W5.0 does not prove Workbench or first-success ability.

W5.1 is validated offline on the W5.0 working baseline. ADR 0002 accepts FastAPI and
Uvicorn for the future single HTTP boundary; the lock now resolves 22 packages, including
FastAPI 0.141.1, Pydantic 2.13.4 and Uvicorn 0.52.4. The first offline-only lock attempt
failed because the required distributions were not cached; normal dependency acquisition
then accessed the package registry and succeeded. This access is not research-network or
live capability evidence. Read-only contracts now provide stable Run pagination, persisted
Event cursors, user-facing Run details, registered Delivery Artifact reads, fail-closed
Evidence lookup from registered rank checkpoints, sanitized errors and local readiness.
Focused tests passed 24 and the full suite passed 249. No FastAPI app, Uvicorn process,
Worker, SSE transport, Workbench, browser, research source, Provider, paid call or live Run
was executed; at W5.1 acceptance, W5.2 remained separately gated.

W5.2 is validated offline on the same W5 working tree. A single FastAPI ASGI app now
exposes task submission, Run query/cancel/resume, persisted Event SSE, Artifact/Evidence
reads and local health routes. Its lifespan owns one injected WorkerLoop, while the
explicit CLI `serve` command fixes Uvicorn to one worker. Mutations write through the
existing SQLite authority; SSE resumes by persisted `event_id` and ends after terminal
state. Focused tests passed 30 and the full suite passed 255; compileall and direct server
imports passed. No Uvicorn process, Workbench asset, browser, research source, Provider,
paid call or live Run was started. W5.3 is the next acceptance point and is now explicitly
authorized, but its implementation is not part of this committed W5.0-W5.2 baseline.

W5.3 is validated offline on top of committed W5.0-W5.2 revision `2e1219c`.
The single FastAPI app now serves a packaged HTML/CSS/ES-module Workbench for task
submission, Run history/detail, user-facing progress and budget, cancellation, explicit
unknown-effect recovery decisions, Delivery text, Evidence detail, limitations and SSE
updates. Delivery content reads remain registered-Artifact-only with hash verification,
UTF-8 and size gates. The first TestClient approach failed because current Starlette
requires an unavailable `httpx2`; endpoint-direct contracts plus a real local Uvicorn
fixture covered the HTTP/UI boundary without adding that dependency. The first browser
load exposed Windows `.js` as `text/plain`; explicit JavaScript MIME registration and
asset cache busting fixed it. Focused tests passed 32 and the full suite passed 257.
Desktop 1440x900 and mobile 390x844 checks covered answer, two Evidence details, limits,
create/cancel, refresh persistence, no horizontal overflow and zero console errors.
Two sequential fixture Uvicorn/Worker processes were started during diagnosis; research
network, real Provider, paid calls and live Runs remained zero. W5.3 was then authorized
and completed before this W5.4 acceptance.

W5.4 is validated offline on top of W5.3. The new `conflux-weave offline-smoke` command
uses a versioned local fixture to close Task request, SQLite Run, registered Delivery,
Citation/Evidence and packaged Workbench asset reads without reading Provider config or
making any network call. Focused tests passed 37 and the full suite passed 262. Offline
sdist and wheel builds passed; each distribution was installed into an isolated target
and its installed `conflux-weave.exe offline-smoke` entrypoint passed. Dataset file hashes,
package hashes and failure/recovery boundaries are recorded in the W5.4 acceptance JSON.
W5.5 has entered execution on the W5.4 working tree. The Workbench SSE client now
stores the last persisted event cursor, explicitly reconnects with `after=cursor`,
and deduplicates events while retaining the Run snapshot as terminal authority.
Workbench keyboard/semantic and narrow-layout contracts were added to the focused
tests; the local fixture Uvicorn health and seeded Run were reachable with zero
external calls. Focused W5.5 tests passed 8. The current environment has no usable
Playwright/Chrome runtime, so 320px/390px/200% screenshots and real browser keyboard,
restart, cancel, and fault-recovery interaction evidence remain pending and are not
claimed as validated. See `docs/plans/current/W5.5-可用性与故障验收.json`.

W5.6 was executed once under explicit live authorization using a fresh Git clone,
isolated virtual environment, database and Artifact root. Frozen commit `60cffbf`
installed 22 packages in 16.8 seconds; one real Provider/research-source Run completed
in 3.5 seconds after submission as a `partial` delivery with 2 Artifacts, 5 Evidence
records and 12 persisted events. The answer retained source and full-text limitations.
After stopping and restarting the same single-worker service, the Run, history,
Delivery, Artifact and Evidence were recovered from SQLite. No Provider Secret was
recorded in the report, Git, SQLite or Artifact paths. W5.6 remains partial because
the environment still lacks a usable Playwright/Chrome runtime, so browser keyboard,
visual and browser-side ten-minute timing evidence were then bounded with temporary
Playwright driving the installed Microsoft Edge: 1440px, 390px, 320px and 200% viewports
had no horizontal overflow, and Tab/Enter/Escape opened and closed the task dialog.
The full page-to-submit manual timing remains unclaimed. See
`docs/plans/current/W5.6-清洁环境首次成功验收.json`.

W5.7 executed the frozen five-task personal-use matrix without automatic retry or
fallback. It produced two partial deliveries and three retained failures. Of three
Workbench paper Runs, one exposed five Evidence records and two failed at arXiv search
with HTTP 429; browser history and refresh preserved all three. One repository task
selected an unrelated repository and retained that mismatch as a limitation, while the
other returned no valid candidate. Two task families were covered, but only one of two
required successful Workbench paths completed and no delivery has explicit user-use
confirmation. The mechanical release decision is `reject`; see
`docs/plans/current/W5.7-个人持续使用与发布裁决.json`.

W5.7-R1 added a process-local, single-concurrency arXiv request governor, a three-second
minimum interval, bounded retry for read-only HTTP 429/5xx responses with `Retry-After`
and exponential backoff, and a 24-hour persistent cache written only after successful
Atom parsing. Every HTTP attempt retains a content-addressed Artifact reference, while
Provider automatic replay remains disabled. The focused fault matrix passed, including
429/5xx recovery, retry exhaustion, cache reuse, malformed XML, spacing and cancellation.
A source-only live check under `var/tmp/w5_7_r1_source_live_20260825_1` returned three
papers on the first HTTP request and reused the cache with zero HTTP requests on the
second identical query; no Provider was called. A live 429 was not observed, the
governor is not a cross-process lock, and OpenAlex/Crossref are not integrated. The
original W5.7 release decision therefore remains `reject`; a fresh five-task audit is
required and prior failed or partial tasks cannot be recounted. See
`docs/plans/current/W5.7-R1-arXiv来源可靠性整改验收.json`.

Latest framework verification:

```text
uv lock --check                                      passed; 22 packages
uv run --frozen pytest -q -p no:cacheprovider        269 passed
uv run --frozen python -m compileall -q src tests    passed
uv build --offline --out-dir <ignored path>          sdist and wheel built
```
