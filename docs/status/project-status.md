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

S1.6-A is `implemented_and_validated_offline`. It replaces the research workflow's
exception-based zero-Claim behavior with an explicit result contract: both an empty draft
and a fully rejected post-repair Claim set produce a citation-empty `NO_ANSWER` Delivery,
with the retrieval boundary retained in Artifacts and no fake supporting Evidence. Durable
publication maps this disposition to a successful Run, while validating that no-answer
results explain their corpus boundary. Managed aggregation now preserves no-answer and
partial subrun outcomes instead of requiring every subrun to contain a Claim.

Research workflows also receive their configured corpus scope; reports and manifests no
longer hard-code the frozen local corpus when the Run uses new or mixed indexes. Focused
workflow/runtime/API coverage passed (`39 passed`), and the full regression passed
(`316 passed in 82.19s`). These are offline mechanism results only: no S1.5-C Run was
replayed, no fresh live acceptance has occurred, and the prior `reject` remains authoritative.
The next acceptance point is S1.6-B: structured Manager objective-coverage obligations and
a discovery Claim-support gate, followed by frozen failure replay before any new live Run.

S1.6-B is `implemented_and_validated_offline`. Manager planning now emits a versioned
coverage contract: every requirement contains an exact quote from the original objective,
every subquestion maps to known coverage IDs, and all requirements must be assigned before
worker execution. Workers receive the original objective and their assigned obligations.
After Claim/Evidence verification, a separate coverage auditor may reference only existing
accepted Claim IDs; missing requirements produce a `PARTIAL` Delivery with explicit unmet
criteria even when every subrun returned at least one supported Claim. Manager plan plus
coverage auditing reserves two orchestration calls, so durable budgets now record
`2 + 6 * max_subquestions` as the bounded call limit.

The fixed arXiv discovery workflow is now schema v2 and independently assesses every
generated relevance Claim against title/abstract Evidence. Only `supports + accepted`
Claims and their cited Evidence enter the report; rejected or uncertain candidates remain
in draft and assessment Artifacts. An invalid or incomplete assessment fails closed, while
an all-rejected set produces a readable partial report with zero Claims rather than an
unsupported conclusion. The additional verification call is reflected in the frozen
workflow budget and aggregate usage.

S1.5-C failure-shaped fixtures passed for omitted Manager evaluation coverage and an
overstated discovery Claim. Focused workflow/runtime/discovery coverage passed (`61 passed`),
and the full regression passed (`323 passed in 80.13s`). These are replay/offline mechanism
results. The LLM-assisted coverage/support gates do not replace manual semantic review, no
fresh Provider run has occurred, no Manager quality gain is claimed, and S1.5-C remains
`reject`. The next acceptance point is S1.6-C: freeze the post-remediation live protocol and
preflight, then create new immutable Runs rather than modifying the first matrix.

S1.6-C protocol freeze is `implemented_pending_live_preflight`. The new
`s16-post-remediation-live-v1` dataset keeps the same eight question shapes for direct
comparison while changing every case ID to `s16c-*`. It pins the local, new and mixed corpus
manifest hashes and freezes exact Manager coverage quotes, explicit no-answer Delivery
requirements, manual Claim support review, and token/latency reporting without making
Manager quality gain an acceptance requirement. The matrix runner now accepts a dataset,
summary schema and idempotency namespace while preserving all S1.5-C defaults; the S1.6-C
wrapper uses a new SQLite database, summary path, schema and `s16c:` idempotency keys.

A separate S1.6-C preflight entry point requires `--execute-live` and records one Chat,
Embedding, Reranker and fresh arXiv GET/Atom parse attempt with Artifact references, models,
usage, elapsed time and source attempt/cache state. It does not retry Provider calls and it
does not authorize or execute the formal eight-case matrix. The next acceptance point is to
commit the protocol, then run this lightweight preflight against that clean committed
revision. Protocol/workflow/discovery-focused tests passed (`36 passed`), the full regression
passed (`325 passed in 118.74s`), and wheel/sdist builds succeeded. Pytest emitted only the
existing Windows cache and temporary-directory cleanup permission warnings after its
successful exit. These are offline protocol/mechanism results; the rejected S1.5-C matrix
remains immutable and authoritative.

The committed S1.6-C protocol revision `4ea3ba9` passed its lightweight live preflight
(`validated_live`) with `source_dirty_at_start=false`. Chat used `qwen3.7-flash` (33 total
tokens), Embedding used `text-embedding-v4` (1 vector, 1,024 dimensions), and Reranker used
`qwen3-rerank` (2 documents). The arXiv source check returned two parsed papers from one
fresh GET with `cache_hit=false`, `attempt_count=1` and no retry delay. Provider automatic
retry was disabled. The machine-readable evidence is at
`var/acceptance/v0.3-s1/s16c-preflight/preflight-summary.json`; request, response, source
snapshot, attempt and manifest Artifact IDs are recorded there.

This preflight proves current port connectivity and one bounded source parse only. It does
not prove post-remediation research quality, Manager gain or S1 completion. The next
acceptance point is to execute the frozen S1.6-C eight-case protocol once, creating new
immutable Runs and preserving any Provider or acceptance failures without automatic retry.

The first S1.6-C formal discovery attempt on revision `08c4852` is an immutable failed case,
not a retry candidate. The draft call succeeded, but the independent support verifier returned
`claims` instead of the required `assessments` root, so the workflow failed closed with
`paper_claim_verification_invalid`. The failure manifest, both model request/response pairs
and source response remain in the Artifact Store. Provider automatic retry was disabled.
The matrix runner is being minimally extended to admit this frozen failure Artifact as the
discovery case and continue the seven independent research cases; no protocol question,
acceptance criterion or previous Run is changed.

The S1.6-C matrix is now sealed as `blocked_unknown_outcome`, not accepted. The runner
revision is `4067355`, the protocol hash is
`49bfe0c4a6dbff93c97f359e04f12178f976f15fbe93169dc664b0eef8d13f15`, and the source was
clean at start. All eight cases have immutable records: five Runs succeeded, discovery
failed, and both Manager Runs are `waiting_for_user`. Four answerable single-Agent reports
delivered 19 Claims with mechanical citation closure 1.0; the no-answer case succeeded with
an explicit `no_answer` Delivery, zero Claims and the mixed-corpus boundary. Manual support
review of the 19 Claims remains pending, and hard monetary enforcement remains unavailable.

Both Manager failures are confirmed workflow/schema contract failures. Their Provider
responses were valid JSON but used `text`, `subquestion_id` and `mapped_coverage_ids`, while
the parser requires `objective_quote`, `question` and `coverage_ids`; the live system prompt
did not enumerate that exact schema. The Runs therefore froze with
`research_batch_outcome_unknown`, no Delivery and zero durable-ledger usage, although the
preserved responses show 390 and 436 actually consumed Provider tokens. Neither Run may be
automatically resumed or replayed, and no Manager comparison or gain can be claimed.

The persisted matrix, SQLite state and structured mechanical review are at
`var/acceptance/v0.3-s1/s16c-matrix-summary.json`,
`var/acceptance/v0.3-s1/s16c-matrix.sqlite3` and
`var/acceptance/v0.3-s1/s16c-mechanical-review.json`; the review freezes their SHA-256 hashes
and the relevant raw response Artifact IDs. The next acceptance point is a bounded manual
Claim/Evidence audit of the five successful deliveries, followed by an offline Manager plan
contract repair/replay decision. It must not modify or retry any sealed S1.6-C Run.

The bounded Claim/Evidence audit was completed on 2026-08-27 and is recorded in
`var/acceptance/v0.3-s1/s16c-manual-claim-review.json`. All 19 Claims across the four
answerable single reports (5 + 5 + 5 + 4) were checked against the exact retrieved page-level
quotes in their sealed Evidence Artifacts: every Claim directly restates text present in the
sealed chunk, every numeric value is supported verbatim or recomputes correctly, zero Claims
lacked direct support, and the `no_answer` Delivery fabricated none. Corpus scope held on
all five cases: each SourceSnapshot resolves to a document listed in that case's frozen
corpus import manifest (`2606.10209`, `2606.08151` and `2606.13177` for local-only,
`2608.24188v1` for new-only, and exactly the local-plus-new pair for mixed), and boundary
limitations were retained everywhere. The audit is agent-assisted output; final S1.6-C
sign-off remains reserved for the human protocol owner.

On 2026-08-27, the human protocol owner approved the bounded Claim/Evidence review. This
sign-off confirms the 19/19 direct-support result, zero fabricated no-answer Claims and the
recorded corpus-scope checks. It does not approve S1.6-C as a whole and does not authorize
replay or replacement of any sealed Run. The signed review remains in the ignored acceptance
evidence directory; its post-sign-off SHA-256 is
`684106853b53460f8377de21ed097197a2810121ad78fb8fbf9f677ce171ff3c`.

S1.6-C therefore remains `blocked_unknown_outcome`, not accepted. Passing the Claim audit
does not remedy the discovery workflow contract failure
(`paper_claim_verification_invalid`) or either Manager schema failure
(`text`/`subquestion_id`/`mapped_coverage_ids` versus the parser-required
`objective_quote`/`question`/`coverage_ids`), so Manager token/latency comparison and
coverage acceptance stay unmeasurable in this matrix. The next step is unchanged: perform
the offline Manager plan contract repair with fixture validation (covering both observed
plan shapes and the discovery verifier `assessments` root), then decide whether to create
new immutable Runs under a fresh acceptance protocol. Sealed S1.6-C Runs, including the
failed discovery and both frozen Manager Runs, must not be retried, replayed or resumed.
