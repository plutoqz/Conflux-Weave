# Project status

Updated: 2026-08-24

| Area | Status | Evidence boundary |
|---|---|---|
| Design v0.2 | `proposed` | Current design basis; not implementation proof |
| Repository framework | `validated_live` | Python 3.12.9; 229 tests passed; W3 paired live boundary remains unchanged by offline W4 additions |
| W0 | `validated_offline` | W0.1-W0.5 passed; 12 frozen cases, runtime contracts, and read-only legacy inventory are consistent |
| W1 | `validated_live` | W1.0-W1.5 passed at frozen boundaries; LIVE-01 review note and LIVE-02 repository identity both delivered explicit partial results with raw evidence retained |
| W2 | `validated_live` | BM25/Web Fetch/Evidence delivery validated offline; three accepted live tasks across paper discovery and project evidence Q&A produced bounded partial deliveries |
| W3 | `validated_live` | Persistence/recovery passed offline; original W3.6 and post-refactor R1 each passed 2/2 frozen paired live Runs through the durable CLI |
| W4 | `w4_6_preflight_blocked` | User entered W4.6, but W4.5 rejected C at 1/3 versus the required 2/3; no live or paid calls were started |
| W5 | `proposed_for_scope_freeze` | Execution plan is refined; no W5 product code, dependency, browser, network, or Provider work has started |
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

Latest framework verification:

```text
uv sync --frozen --python 3.12       passed
uv run --frozen pytest               229 passed
uv run --frozen python -m compileall passed
uv build                             sdist and wheel built
```
