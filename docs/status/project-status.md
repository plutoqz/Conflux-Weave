# Project status

Updated: 2026-08-24

| Area | Status | Evidence boundary |
|---|---|---|
| Design v0.2 | `proposed` | Current design basis; not implementation proof |
| Repository framework | `validated_offline` | Python 3.12.9; 146 tests passed; W3.2 Worker lease and fencing tests included |
| W0 | `validated_offline` | W0.1-W0.5 passed; 12 frozen cases, runtime contracts, and read-only legacy inventory are consistent |
| W1 | `validated_live` | W1.0-W1.5 passed at frozen boundaries; LIVE-01 review note and LIVE-02 repository identity both delivered explicit partial results with raw evidence retained |
| W2 | `validated_live` | BM25/Web Fetch/Evidence delivery validated offline; three accepted live tasks across paper discovery and project evidence Q&A produced bounded partial deliveries |
| W3 | `w3_2_validated_offline` | W3.1 persistence plus single Worker, Attempt/Lease, heartbeat, expiry takeover, and fencing passed local deterministic acceptance |
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
network, or Provider execution was performed. Current next acceptance point:
W3.3 paper-discovery Step checkpoint, cancel, and recovery.

Latest framework verification:

```text
uv sync --frozen --python 3.12       passed
uv run --frozen pytest               146 passed
uv run --frozen python -m compileall passed
uv build                             sdist and wheel built
```
