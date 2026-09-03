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

S1.6-D is `implemented_and_validated_offline`. The repair keeps both strict parsers and all
delivery/state semantics unchanged, but makes their Provider contracts unambiguous. Manager
plan prompts now enumerate the exact `coverage_requirements[].coverage_id/objective_quote`
and `subquestions[].question/coverage_ids` object shape and explicitly prohibit the three
observed aliases; the Manager profile is version `v3`. Its coverage-audit prompt likewise
enumerates the exact assessment object. The discovery verifier prompt now requires an
`assessments` root with `claim_id`, `evidence_ids`, `relation`, `verdict` and `rationale`,
explicitly forbids a `claims` root, and advances to prompt version
`arxiv-relevance-claims-zh-v5-explicit-verification-schema`.

The three sealed failure response shapes were copied without semantic edits into
`tests/fixtures/s16c_contract_failures.json` (SHA-256
`e30a5cc2c8a28f51e1615eb86fb716385b15330c93e76917ddb35e2c598f9fbd`). Offline replay
proves both observed Manager alias shapes and the discovery `claims` root still fail closed;
canonical Manager responses for both frozen objectives parse successfully, and tests inspect
the actual outgoing prompts for every required field. Focused Manager/discovery tests passed
(`29 passed`), and the full regression passed (`330 passed in 142.15s`). Pytest emitted only
the existing Windows cache and temporary-directory cleanup permission warnings after its
successful exit.

Direct wheel build succeeded. Root-directory sdist selection stalled while scanning the
large ignored runtime/evidence tree, so that process was terminated without deleting any
evidence; an equivalent clean source-only export containing all current changes then built
both sdist and wheel successfully. This is offline implementation and replay evidence only:
no Provider was called, no sealed S1.6-C Run changed, and S1.6-C remains
`blocked_unknown_outcome`. The next acceptance point is to freeze a fresh post-repair live
protocol and idempotency namespace before creating any new immutable discovery/Manager Runs.

S1.6-E adopts affected-surface closeout rather than another eight-case matrix. The current
implementation plan now requires schema-specific canaries before paid workflows, focused
reruns for only changed capabilities, one full regression per commit checkpoint, and a clear
separation between engineering completion and research acceptance. Narrow Prompt/parser
contract defects remain ordinary development work unless they change shared Runtime,
retrieval or Evidence semantics.

The frozen `s16-contract-closeout-live-v1` protocol contains only the failed surface from
S1.6-C: one discovery and the two Manager objectives. It uses a new `s16e` idempotency
namespace and SQLite database, pins the existing local/mixed corpus hashes, requires the
production Prompt/parser schema canary before Runs, and explicitly excludes repeated
single-Agent/no-answer/UI/retrieval acceptance. Focused protocol and contract tests passed
(`34 passed`). No S1.6-E Provider call or Run has occurred at this checkpoint.

S1.6-E executed on clean revision `1f5a601`. Its production-schema canary passed
with no automatic Provider retry, and the new discovery record closed the original
`assessments`-root failure (`partial`, 8 Claims, 8 Evidence, 8 Citations, citation
closure 1.0). The local Manager Run `run-31124df7c520491bb38c89cb983b94f4`
succeeded with a complete Delivery and all four required coverage phrases marked
`covered`. This proves the original discovery and Manager plan shape repairs on live
paths; it does not establish a Manager quality, cost or latency benefit.

The sealed S1.6-E mixed Manager Run `run-18d1c0f436df443095e84a85c488e762`
exposed a distinct referential-integrity failure after its JSON shape had parsed. The
worker Verifier cited supplied `evidence-0002` and `evidence-0007` but also invented
`evidence-0014`, although the request contained only `evidence-0001` through
`evidence-0008`. The Run correctly stopped as `research_batch_outcome_unknown`; it
was not resumed or retried. Its request, response and technical-detail Artifact IDs
are frozen in `tests/fixtures/s16e_referential_failure.json` (SHA-256
`917607e82f61e9c19c272b0864a2457570eca661c14e22d74920ffb1895ee282`).

S1.6-F repairs that second-layer contract at the untrusted Provider boundary. Verifier
outputs now require the exact root, fields, enums, known Claim IDs and exactly one
assessment per Claim. When an assessment contains both valid Evidence IDs and extra
unknown IDs, only the known-set intersection is admitted and the discarded IDs are
recorded in `normalization_warnings`; an empty intersection still fails closed. Raw
Provider responses remain immutable. The implementation plan now requires both schema
shape and referential-integrity canaries and documents this narrow, auditable
normalization rule rather than relying on retries, aliases or inferred replacements.

The focused `s16-worker-verifier-closeout-live-v1` protocol used new namespace `s16f`
and executed only the affected mixed Manager case; the discovery row is a sealed
reference and the already successful local Manager was not repeated. Its canary passed
on clean revision `1317d9b` with three live contract calls and zero normalization
warnings. The new Run `run-fc547714b12c4607b1da910f82c1f0ad` succeeded in 30.542
seconds with a complete Delivery, 10 Claims, 12 Evidence, 17 Citations, citation closure
1.0, all four required coverage phrases `covered`, and no errors. Both worker assessment
Artifacts contain an empty `normalization_warnings` list.

Agent-assisted review checked all eight sealed discovery Claims against their exact Atom
entries and found 8/8 directly supported. The review records the CaSKG scope wording and
clarifies that PolyMemDB `polyglot` means multiple data models/storage paradigms, not
natural-language multilingual storage. Evidence is at
`var/acceptance/v0.3-s1/s16f-schema-canary.json`,
`var/acceptance/v0.3-s1/s16f-closeout-summary.json` and
`var/acceptance/v0.3-s1/s16f-closeout-review.json`, with SHA-256 values
`f8d3df6de354bf455788c2e13db68b88453ae8d06f98d260ec0ea32e4696c472`,
`17949e555f5573ca4c75bee5d5ee14288c9f0d27a153e0bacc0d57e8612558b8`
and `d19f47dd0b2c4aa8c106bb1acee6287f149468c90c4997f1738db5c391906efc`.

S1.6 repair implementation is complete and validated live on the affected surface.
Focused tests passed (`22 passed`), full regression passed (`336 passed in 182.66s`),
and fresh sdist/wheel builds succeeded. Pytest emitted only the existing Windows cache
and temporary-directory cleanup permission warnings after successful execution. Final
S1.6 protocol disposition remains `pending_protocol_owner_sign_off` because the new
8-Claim review is `passed_agent_assisted` with `human_sign_off: pending`. No Manager
quality, cost or latency gain is claimed, and no sealed failed Run is authorized for replay.

On 2026-08-27, the human protocol owner approved the S1.6-F worker Verifier
referential-integrity closeout and the agent-assisted 8-Claim discovery review. The signed
review now records `human_sign_off.status: approved` and `overall_acceptance: accepted`;
its post-sign-off SHA-256 is
`b4ddd345bb5174e9db873855d77a4146daeed600e377d4b29787a93fce44e3e4`.
This approval closes S1.6 as `accepted`: the original discovery/Manager shape failures and
the later worker Verifier reference failure are repaired and validated live on their affected
surfaces. The approval does not establish or approve a Manager quality, cost or latency
benefit, and it does not authorize replay or resume of any sealed failed Run.

S1 Final Closure completed its agent-assisted evidence audit against all seven completion
criteria in the completed S1 plan. The signed final disposition is `validated_live`:
179 local SourceSnapshots and two newly acquired PDFs are versioned; BM25, LanceDB Dense,
Hybrid/RRF and qwen3-rerank have traceable live evaluation; ResearchAgent, Verifier and
Manager have citation-closed live deliveries; failed evaluations remain preserved; all three
real model identities and usage are recorded; persisted Workbench desktop/mobile replay
passed; and the latest full regression (`336 passed in 182.66s`) plus sdist/wheel build passed.

This is a composite affected-surface closure, not a rewritten S1.6-C matrix. The sealed S1.6-C
decision remains `blocked_unknown_outcome`; later S1.6-E/F evidence closes only its discovery,
Manager schema and worker Verifier reference failures under fresh namespaces. The machine-
readable review is `var/acceptance/v0.3-s1/s1-final-closure-review.json`, SHA-256
`1d5cbb086a98d487ae02b350f6557df234089a62f04ec4dac2cc8a6e67534935`.
It records 7/7 criteria as passed. On 2026-08-27, the human protocol owner approved the
entire S1 Final Closure, so the final S1 disposition is `validated_live` and the review's
`overall_acceptance` is `accepted`. Its post-sign-off SHA-256 is shown above. This approval
does not rewrite the historical S1.6-C disposition or broaden the bounded capability claims.

Workbench UX optimization is feasible as a bounded bridge between S1 and P2. The current
same-origin Vanilla HTML/CSS/JavaScript surface has stable Run/API/SSE/Evidence boundaries
and existing responsive replay evidence, so an information-architecture and interaction pass
does not require a framework migration or Runtime change. The proposed
`v0.3-UX0-Workbench前端体验优化-细化实施方案.md` inserts UX-0 before P2.0 and freezes
information hierarchy, complete Run-state coverage, evidence inspection, responsive layout,
keyboard accessibility and browser verification. It explicitly excludes API/Runtime changes,
image extraction, OCR, image embedding and multimodal retrieval. S1 is archived under
`docs/plans/completed/v0.3/`; the next single acceptance point is UX-0.0 baseline and design
freeze, not visual implementation. At that checkpoint UX-0 execution still required an explicit
user entry instruction; it was subsequently authorized and completed as recorded below.

UX-0 completed on 2026-08-28 with status `validated_browser_and_regression` at implementation
revision `2cac2fe`. The Workbench now provides the frozen compact information hierarchy,
complete Run action matrix, desktop Evidence inspector and mobile dialog, responsive history,
HUD, local Lucide icon subset, mutation protection, keyboard tab navigation and focus return.
An asynchronous SSE guard prevents an old Run detail response from replacing a newly selected
Run. Runtime, API, SQLite and Agent/Evidence authority contracts are unchanged.

Focused Workbench/API contracts passed (`18 passed`), followed by the full regression
(`336 passed in 174.20s`). Deterministic browser acceptance covered nine Run states, a real
fixture cancellation transition, four keyboard paths, 200% zoom and five viewports. Persisted
S1.6-C history replay verified both an evidence-backed complete Run
`run-cfe157759ab24bea9c1efb28f0e30899` and no-answer Run
`run-cdc8cdc1ca3c4ac6b379e5716c5aecad` without creating, replaying or resuming a Provider Run.
Horizontal overflow, console errors and DOM occlusions were all zero. The wheel/sdist SHA-256
values are `cdc3b532abb164a3f5648b12dddb9a6c96d6bff17a002073aa99180b8f64c861` and
`5e74f49b2a72761e900b6ee6983ea55b53f82d4938d518e9d0fab556ef1c3c83`.

The ignored machine-readable record is `var/acceptance/v0.3-ux0/ux0-final-closure.json`;
its SHA-256 is `524cc7b815a4539fffcb28ffd8a6b4d01c38de29b912987f9a282f587b6a3049`.
UX-0 is archived under
`docs/plans/completed/v0.3/`. Firefox/WebKit and native screen-reader validation remain
uncovered; image extraction, OCR, image embedding and multimodal retrieval remain P2 work.
The next acceptance point is P2.0 image-asset and lineage scope freeze.

UX-1 (workbench overview, unified conversation entry and configuration center) is
`implemented_and_validated_browser_and_regression` as of 2026-08-28. The user approved
three decisions up front: top-bar section navigation (overview/chat/research/settings,
non-research sections hide the left column), a usable conversation prototype rather than
a placeholder (a question creates a real verified-research Run with SSE progress and
delivery rendering in the chat stream), and future sections (documents/projects/knowledge)
stay hidden until they ship. New hash routes are `#/overview` (default), `#/chat`,
`#/research`, `#/research/<run_id>` and `#/settings`; selecting a Run syncs the address
and refresh/share restores the view. The overview shows the three real readiness checks
with recovery guidance, a not-ready configuration alert linking to settings, usage steps
and the five most recent Runs; Knowledge/Projects stay absent rather than faked. The
settings view edits Provider base URL, API key (write-only, masked hint, blank keeps the
current value) and Chat/Embedding/Reranker model IDs, offers a connection test, and
states explicitly that a restart of `conflux-weave serve` is required because Provider
configuration is read once at process start.

The backend gains `src/conflux_weave/config_store.py` plus three additive routes:
`GET /api/v1/config` (sanitized provider view, `provider_active`, read-only data paths),
`PUT /api/v1/config/provider` (HTTPS validation, atomic dotenv write preserving
comments/unrelated lines, `requires_restart: true`) and `POST /api/v1/config/provider/test`
(one small chat call reusing the existing adapter; the response never contains the API
key). The existing 16 endpoints, SSE protocol, RunStatus enums, SQLite schema and
Agent/Provider/Orchestrator paths are unchanged. The workbench frontend is now split into
local ES modules (`modules/shared|router|overview|chat|settings.js`) with `app.js`
remaining the research view and entry; all prior DOM IDs and test anchors are preserved
and the frozen Moss design tokens/print details are reused. No framework, component
library or CDN was introduced.

Focused tests (`test_workbench` 4, `test_config_api` 8, plus `test_api_contracts` and
`test_server`) passed, followed by the full regression (`345 passed in 35.82s`; only the
existing Windows cache/temporary-directory warnings). Browser verification
(`scripts/verify_ux1_workbench.mjs` against the offline fixture server
`scripts/serve_ux1_fixture.py`) covered all four sections at five viewports with zero
horizontal overflow and zero console errors, 200% zoom, the not-ready alert, a full
offline fixture conversation flow (submit → SSE → delivery text and boundary summary →
deep link into the research view), settings masked echo/save-restart banner and
deep-link refresh restore. The UX-0 regression script and DOM layout audit were re-run
green after adapting their entry points to `#/research` (the default landing is now the
overview). Evidence is under `var/acceptance/v0.3-ux1/final/`. UX-1 remains a mechanism
delivery: it does not add the P4 unified conversation router, config hot reload,
Memory, Skill/MCP or multimodal capabilities, and Firefox/WebKit plus native
screen-reader validation remain uncovered. The next acceptance point is P2.0
image-asset and lineage scope freeze.

First user feedback on the live system (2026-08-28, same day) produced three fixes,
re-verified with the full regression plus the ux1/ux0/audit browser scripts:
overview guidance copy was reduced to one line and the three-step text guide card was
removed (usability must come from layout, not explanatory paragraphs; the overview now
pairs two action buttons with the systems/recent-cards grid), the chat composer mode
switch no longer collapses (the base `.mode-switch` block later in the stylesheet
overrode the chat variant; the override now uses a higher-specificity selector, a fixed
two-column width and a non-shrinking fieldset), and the new-research/follow-up dialogs'
close and cancel buttons submit with `formnovalidate` so a required empty field no
longer blocks closing. A startup health check now runs on every landing view instead of
only on first research mount, so the topbar status is correct on the overview default
route. Asset cache strings were bumped accordingly (`?v=v0.3-ux1-6`).

## W3 实际开发状态（2026-09-02）

本节覆盖 UX-1 之后已经合入主分支的 W3 实现，避免将旧的“下一步 P2.0”描述误读为当前
代码状态。状态分为工程实现、离线验证和真实验收三类；未执行的真实 Provider/网络运行不
计为验收证据。

| 工作包 | 当前状态 | 证据边界 |
| --- | --- | --- |
| W3.0 直接问答 | `implemented_and_validated` | 独立聊天持久化、上下文注入、API 和浏览器回归已验证；真实 Provider 烟测曾有历史证据 |
| W3.1 知识库问答 | `implemented_and_validated` | Hybrid 检索、片段引用、轻量上下文 Artifact 和无语料失败路径已验证；真实语料烟测曾有历史证据 |
| W3.2 深度研究引擎 | `implemented_offline_pending_user_live_test` | GPT Researcher 证据桥、durable task、SourceSnapshot/Claim/Citation 链路和 fallback 已有离线测试；本轮不执行真实验收 |
| W3.3 可信度与参数化回答 | `implemented_and_validated_offline` | 可信度标记和参数化 RAG 回答已有测试；未单独宣称新的 live 能力 |
| W3.5/W3.6 融合报告 | `implemented_and_validated_offline` | 引擎叙事解析、Merge Plan、融合 Writer、Tavily/model routing 和 fallback 已有离线回归；正式 live 验收待用户执行 |

截至本节更新，完整离线回归为 `439 passed`。测试前新增的 Provider 配置隔离位于
`tests/conftest.py`，只清理测试进程中第三方依赖泄漏的 `CONFLUX_WEAVE_PROVIDER_*`
变量，不改变生产环境变量优先于 dotenv 的配置契约。

本轮工程整改顺序固定为：先同步状态文档，再隔离 GPT Researcher 的进程级配置状态，最后
拆分 Writer 模块。真实深度研究 Run、成本/延迟和报告人工评审不在本轮执行范围，下一唯一验收
点由用户手动执行。
