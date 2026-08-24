# Project status

Updated: 2026-08-24

| Area | Status | Evidence boundary |
|---|---|---|
| Design v0.2 | `proposed` | Current design basis; not implementation proof |
| Repository framework | `validated_offline` | Python 3.12.9; 130 tests passed; W0-W3 scope contracts, governance, and CLI paths pass |
| W0 | `validated_offline` | W0.1-W0.5 passed; 12 frozen cases, runtime contracts, and read-only legacy inventory are consistent |
| W1 | `validated_live` | W1.0-W1.5 passed at frozen boundaries; LIVE-01 review note and LIVE-02 repository identity both delivered explicit partial results with raw evidence retained |
| W2 | `validated_live` | BM25/Web Fetch/Evidence delivery validated offline; three accepted live tasks across paper discovery and project evidence Q&A produced bounded partial deliveries |
| W3 | `scope_frozen` | Durable paper-discovery consumer, recovery, budget, side-effect, fault-injection and authorization contracts frozen; no Runtime implementation |
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

W3.0 has frozen the single-SQLite, single-Worker recovery scope against pushed
W2 revision `e9137f3`. No SQLite Repository, Worker, checkpoint, Trace adapter,
network call or Provider call has been implemented or authorized by this freeze.
Current next acceptance point: W3.1 SQLite Repository, idempotent Task creation,
minimal migration, and atomic Artifact/final-state publication.

Latest framework verification:

```text
uv sync --frozen --python 3.12       passed
uv run --frozen pytest               130 passed
uv run --frozen python -m compileall passed
uv build                             sdist and wheel built
```
