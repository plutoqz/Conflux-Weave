# Project status

Updated: 2026-08-26

| Area | Status | Evidence boundary |
|---|---|---|
| Design v0.3 | `proposed` | Current product and architecture direction; not implementation proof |
| v0.2 design generation | `deprecated` | Preserved under versioned deprecated directories |
| Existing runtime | `legacy_v0.2_implemented` | SQLite, Artifact, Evidence, recovery, FastAPI and Workbench assets remain in source |
| Existing live capability | `bounded_retrieval_evidence` | Historical qwen3.7flash arXiv/GitHub runs; not full RAG or multi-Agent proof |
| v0.3 S0/P0 plan | `implemented_and_validated_offline` | S0.0-S0.5 are committed; current work continues on `codex/v0.3-s0-harness` and is pushed to `origin/main` at phase checkpoints |
| v0.3 S1 | `implemented_partial_live_acceptance_failed` | S1.0-S1.5-B mechanisms exist; the frozen S1.5-C first live matrix was executed but rejected after manual support, coverage and abstention review |

The v0.2 W0-W5 plans are no longer active gates. Their implementation and
validation evidence remains available at `docs/plans/deprecated/v0.2/`; the
unmodified detailed status snapshot is at
`docs/status/deprecated/v0.2/project-status-v0.2.md`.

S0 adds a framework-independent Harness contract, scoped `weave://` Local Workspace
Adapter, durable Agent Messages in SQLite migration v6, a deterministic composite
Orchestrator, a legacy paper Runtime Adapter, and an offline ResearchAgent fixture.
The existing `/api/v1/tasks/research` path remains available through the compatibility
Adapter; `/api/v1/tasks/research-fixture` is the new zero-network validation path.

Current S0 evidence:

- full suite: `294 passed in 223.73s`;
- build: wheel and sdist succeeded under `dist/`;
- offline smoke: passed under an explicit isolated data root with zero network and
  Provider calls;
- browser: fixture submit, persisted result and refresh replay passed at `1280x800`
  and `390x844`; screenshots are under `var/acceptance/v0.3-s0/browser/`;
- persistence and safety: v5 -> v6 migration, message idempotency/replay, revision
  conflicts and path-escape rejection are covered by automated tests.

This is an offline mechanism result, not a live research-capability claim. S0 does
not prove real paper retrieval, complete RAG, citation quality, multi-Agent
coordination, qwen3.7flash quality, token/latency targets or production reliability.
Recovery evidence is bounded to persisted terminal replay, idempotent communication
and the existing Runtime recovery contract; arbitrary interruption-point replay is
not claimed. The next product stage is S1, beginning with one real paper-research
vertical slice on this Harness rather than more horizontal infrastructure.

Current S1 evidence is recorded under `var/acceptance/v0.3-s1/`. The 179-PDF corpus
produced 4,043 page-locatable chunks; LanceDB is the default Dense path after frozen
JSON/LanceDB parity checks. BM25, Dense, RRF and qwen3-rerank are traceable, and the
current bounded retrieval evaluation passed its six positive and two no-answer cases.
Verified ResearchAgent and Manager workflows have live citation-closed deliveries;
this does not yet prove a multi-Agent quality benefit over the single-Agent baseline.

S1.4 also adds durable `verified_paper_research` and `managed_verified_research` task
kinds over the existing SQLite Task/Run/Step/Delivery authority. Cancellation before
execution, aggregate budget accounting, terminal replay and no automatic replay after
an unknown paid research-batch outcome are mechanism-tested. The recovery boundary is
the complete research batch, not each internal Provider call; individual-call recovery
remains incomplete. The S1.5-C eight-task live matrix has now executed once, but failed
acceptance as described below.
Image-first multimodal RAG is scheduled for P2 and is not part of S1 capability.

S1.5-A connects both durable research task kinds to the existing FastAPI boundary
without replacing `/api/v1/tasks/research`. S1.5-B exposes paper discovery, single-Agent,
Manager and offline-fixture modes in Workbench; persisted Runs can be cancelled,
refreshed, reopened, or rerun as a new immutable Run. A follow-up creates a new Run with
`parent_run_id` and `follow_up_question`, then performs fresh retrieval and verification;
it does not silently inherit the parent Run's conclusions. Verified Evidence is published
as a Delivery Artifact and the UI exposes page/source lineage, corpus scope, token/tool/
retrieval budgets and the absence of a frozen monetary enforcement limit.

Current S1.5-B evidence:

- focused API/Workbench validation: `34 passed`;
- full regression: `311 passed in 89.18s`; pytest exited 0, with one non-functional
  `.pytest_cache` write warning and a Windows temporary-directory cleanup warning;
- live Run `run-4c766128d74c4e61b9a688e4de19a1a0` completed with five Evidence records;
- persisted-history browser replay passed at `1440x900` and `390x844`, with zero
  horizontal overflow and zero console errors; screenshots and the machine-readable
  summary are under `var/acceptance/v0.3-s1/browser-s15b/`;
- the in-app Browser attempt was unavailable (`No Codex IAB backends were discovered`),
  so the responsive acceptance used bundled Playwright with installed Microsoft Edge.

S1.5-B proves the interactive Workbench mechanism and one live Run's persisted browser
delivery. It does not prove monetary limit enforcement or multi-Agent quality gain.

S1.5-C froze and executed eight live cases without retry: one arXiv discovery, two focused
paper questions, two same-question single/Manager pairs, and one no-answer case. The local,
new and mixed LanceDB corpora contain 4,043, 56 and 4,099 chunks respectively; the mixed
index reused and verified the 4,043 existing local vectors and embedded only the 56 new
chunks. The persisted matrix and manual review are at
`var/acceptance/v0.3-s1/s15c-matrix-summary.json` and
`var/acceptance/v0.3-s1/s15c-manual-review.json`.

The S1.5-C decision is `reject` (`validated_live_failed_acceptance`). Mechanical execution
produced six successful citation-closed research deliveries, one partial discovery report,
and one failed no-answer Run. Manual review supported 41 of 42 Claims, but found one
overstated discovery Claim, incomplete requested evaluation coverage in both local
cross-paper modes and the mixed Manager mode, and an incorrect corpus limitation in the
new-focused report. The no-answer model response contained zero Claims, but the runtime
published no user-visible abstention Delivery and instead froze the Run as failed. In the
paired cases, Manager used more tokens and latency without demonstrated quality gain; hard
monetary enforcement also remains unavailable. The complete gate decision is at
`var/acceptance/v0.3-s1/s15c-final-evaluation.json`.

Post-freeze verification passed the focused S1.5-C contract test and the full regression
suite (`312 passed in 118.09s`). Pytest reported only the existing Windows cache and
temporary-directory cleanup permission warnings after the successful exit.

S1.5 and S1 are therefore not complete. The next acceptance point is S1.6 failure-driven
optimization, bounded to abstention delivery, correct corpus-boundary reporting, objective
coverage and Agent scheduling before a fresh closeout evaluation is authorized.
