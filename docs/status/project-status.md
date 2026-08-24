# Project status

Updated: 2026-08-24

| Area | Status | Evidence boundary |
|---|---|---|
| Design v0.2 | `proposed` | Current design basis; not implementation proof |
| Repository framework | `validated_live` | Python 3.12.9; 172 tests passed; post-refactor W3 durable CLI paired live non-regression included |
| W0 | `validated_offline` | W0.1-W0.5 passed; 12 frozen cases, runtime contracts, and read-only legacy inventory are consistent |
| W1 | `validated_live` | W1.0-W1.5 passed at frozen boundaries; LIVE-01 review note and LIVE-02 repository identity both delivered explicit partial results with raw evidence retained |
| W2 | `validated_live` | BM25/Web Fetch/Evidence delivery validated offline; three accepted live tasks across paper discovery and project evidence Q&A produced bounded partial deliveries |
| W3 | `validated_live` | Persistence/recovery passed offline; original W3.6 and post-refactor R1 each passed 2/2 frozen paired live Runs through the durable CLI |
| W4-W6 | `pending` | No later product slice has been implemented |
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

Latest framework verification:

```text
uv sync --frozen --python 3.12       passed
uv run --frozen pytest               172 passed
uv run --frozen python -m compileall passed
uv build                             sdist and wheel built
```
