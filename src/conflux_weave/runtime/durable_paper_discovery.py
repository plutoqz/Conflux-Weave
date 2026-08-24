"""Checkpointed five-Step paper discovery runtime for W3.3."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass
import hashlib
import json
from uuid import uuid4

from conflux_weave.core import (
    BudgetLedger,
    DeliveryDisposition,
    DeliveryRecord,
    ErrorCategory,
    ErrorRecord,
    RunRecord,
    RunStatus,
    StepRecord,
    StepStatus,
    TaskSpec,
)
from conflux_weave.evidence import (
    AnswerBlock,
    ArtifactRef,
    Citation,
    Claim,
    EvidenceRef,
    EvidenceSupportStatus,
    SourceSnapshot,
    SourceTrustLevel,
    render_evidence_report,
)
from conflux_weave.paper_discovery import (
    MAX_OUTPUT_TOKENS,
    MAX_SELECTED,
    PROMPT_VERSION,
    SCHEMA_VERSION,
    SYSTEM_PROMPT,
    WORKFLOW_VERSION,
    ArxivPaper,
    ArxivSearchAdapter,
    _paper_evidence,
    _paper_prompt,
    _parse_claims,
)
from conflux_weave.provider import OpenAICompatibleChatAdapter
from conflux_weave.retrieval import BM25Retriever, RetrievalDocument
from conflux_weave.runtime.artifacts import LocalArtifactStore
from conflux_weave.runtime.sqlite import (
    BudgetAmount,
    LeaseClaim,
    RecoveryDecision,
    SideEffectClass,
    SQLiteRuntimeRepository,
    StepPolicy,
    SubmissionResult,
)
from conflux_weave.runtime.worker import SQLiteStepWorker
from conflux_weave.runtime.telemetry import (
    SafeTraceExporter,
    TraceExporter,
    TraceRecord,
)


DURABLE_WORKFLOW_VERSION = "fixed-arxiv-paper-discovery-durable-v1"
STEP_KINDS = (
    "search_arxiv",
    "rank_candidates",
    "synthesize_claims",
    "validate_delivery",
    "publish_delivery",
)
SEARCH_CHECKPOINT = "conflux-weave.w3.search-checkpoint.v1"
RANK_CHECKPOINT = "conflux-weave.w3.rank-checkpoint.v1"
SYNTHESIS_CHECKPOINT = "conflux-weave.w3.synthesis-checkpoint.v1"
VALIDATION_CHECKPOINT = "conflux-weave.w3.validation-checkpoint.v1"
FINAL_LIMITATION = (
    "仅检索 arXiv 元数据和摘要；未核验正式发表版本、全文实验或跨数据库召回。"
)
UNMET_CRITERION = "尚未覆盖出版社、Crossref、会议页面和全文实验核验。"


@dataclass(frozen=True, slots=True)
class DurableWorkResult:
    run_id: str
    step_kind: str | None
    status: str


class DurablePaperDiscoveryRuntime:
    def __init__(
        self,
        repository: SQLiteRuntimeRepository,
        artifact_store: LocalArtifactStore,
        search_adapter: ArxivSearchAdapter,
        chat_adapter: OpenAICompatibleChatAdapter,
        *,
        worker_id: str = "paper-discovery-worker",
        lease_seconds: int = 30,
        clock: Callable[[], str] | None = None,
        id_factory: Callable[[str], str] | None = None,
        code_revision: str = "unknown",
        trace_exporter: TraceExporter | None = None,
        fault_hook: Callable[[str], None] | None = None,
    ) -> None:
        self.repository = repository
        self.artifact_store = artifact_store
        self.search_adapter = search_adapter
        self.chat_adapter = chat_adapter
        self.worker = SQLiteStepWorker(repository, worker_id, lease_seconds)
        self.clock = clock or repository.clock
        self.id_factory = id_factory or _new_id
        self.code_revision = code_revision
        self.trace = (
            SafeTraceExporter(trace_exporter, on_drop=self._record_trace_drop)
            if trace_exporter is not None
            else None
        )
        self.fault_hook = fault_hook

    def submit(
        self,
        query: str,
        *,
        search_query: str,
        max_results: int = 15,
        budget: BudgetLedger | None = None,
    ) -> SubmissionResult:
        normalized_query = query.strip()
        normalized_search = search_query.strip()
        if not normalized_query or not normalized_search:
            raise ValueError("query and search_query must not be empty")
        if not 1 <= max_results <= 25:
            raise ValueError("max_results must be between 1 and 25")
        task_id = self.id_factory("task")
        run_id = self.id_factory("run")
        step_ids = {kind: f"{run_id}:{kind}" for kind in STEP_KINDS}
        created_at = self.clock()
        frozen_budget = budget or BudgetLedger(
            180, 20_000, MAX_OUTPUT_TOKENS, "provider-price-not-frozen", 2, 1, 1
        )
        config = self.artifact_store.put_json(
            {
                "schema_version": SCHEMA_VERSION,
                "workflow_version": DURABLE_WORKFLOW_VERSION,
                "source_workflow_version": WORKFLOW_VERSION,
                "prompt_version": PROMPT_VERSION,
                "code_revision": self.code_revision,
                "query": normalized_query,
                "search_query": normalized_search,
                "max_results": max_results,
                "selected_limit": MAX_SELECTED,
                "provider": self.chat_adapter.config.provider_name,
                "model": self.chat_adapter.config.model,
                "automatic_retry": False,
                "fallback": False,
                "secret_recorded": False,
                "budget": asdict(frozen_budget),
                "cost_enforcement": "unavailable",
            },
            producer_step_id=step_ids["search_arxiv"],
            schema_version=SCHEMA_VERSION,
        )
        frozen_input = {
            "query": normalized_query,
            "search_query": normalized_search,
            "max_results": max_results,
            "workflow_version": DURABLE_WORKFLOW_VERSION,
            "source_workflow_version": WORKFLOW_VERSION,
            "prompt_version": PROMPT_VERSION,
            "code_revision": self.code_revision,
            "provider": self.chat_adapter.config.provider_name,
            "model": self.chat_adapter.config.model,
            "parameters": {
                "temperature": 0.0,
                "max_output_tokens": MAX_OUTPUT_TOKENS,
                "enable_thinking": False,
            },
            "budget": asdict(frozen_budget),
        }
        task = TaskSpec(
            task_id=task_id,
            kind="paper_discovery",
            input=frozen_input,
            requested_policy=DURABLE_WORKFLOW_VERSION,
            idempotency_key=_idempotency_key(frozen_input),
        )
        run = RunRecord(
            run_id=run_id,
            task_id=task_id,
            status=RunStatus.ACCEPTED,
            workflow_version=DURABLE_WORKFLOW_VERSION,
            config_snapshot_ref=config.artifact_id,
            budget=frozen_budget,
            created_at=created_at,
            updated_at=created_at,
        )
        steps = tuple(
            StepRecord(
                step_id=step_ids[kind],
                run_id=run_id,
                kind=kind,
                attempt=1,
                status=StepStatus.PENDING,
                input_refs=(config.artifact_id,) if kind == "search_arxiv" else (),
            )
            for kind in STEP_KINDS
        )
        policies = {
            step_ids["search_arxiv"]: StepPolicy(
                SideEffectClass.REPLAYABLE_EXTERNAL_READ,
                "reuse committed response; replay only after interrupted uncommitted read",
            ),
            step_ids["rank_candidates"]: StepPolicy(
                SideEffectClass.NONE, "deterministic replay"
            ),
            step_ids["synthesize_claims"]: StepPolicy(
                SideEffectClass.PAID_EXTERNAL_UNKNOWN,
                "never automatically replay request_started without committed response",
            ),
            step_ids["validate_delivery"]: StepPolicy(
                SideEffectClass.NONE, "deterministic replay"
            ),
            step_ids["publish_delivery"]: StepPolicy(
                SideEffectClass.IDEMPOTENT_LOCAL_WRITE,
                "atomic local publication",
            ),
        }
        result = self.repository.submit_task(
            task,
            run,
            steps,
            step_policies=policies,
            submission_artifacts=(config,),
        )
        if result.created:
            self.repository.transition_run(
                result.run_id, RunStatus.QUEUED, updated_at=created_at
            )
        return result

    def work_once(self, *, now: str | None = None) -> DurableWorkResult | None:
        claim = self.worker.claim_next(now=now)
        if claim is None:
            return None
        step = next(
            item
            for item in self.repository.get_steps(claim.run_id)
            if item.step_id == claim.step_id
        )
        if self.repository.is_cancel_requested(claim.run_id):
            self.repository.cancel_claim(claim, now=now)
            return self._result(claim, step.kind, "cancelled", now=now)
        try:
            getattr(self, f"_execute_{step.kind}")(claim, now=now)
        except Exception as exc:
            if self.repository.is_cancel_requested(claim.run_id):
                self.repository.cancel_claim(claim, now=now)
                return self._result(claim, step.kind, "cancelled", now=now)
            effect = self.repository.get_attempt_effect(claim.attempt_id)
            detail = self.artifact_store.put_json(
                {
                    "schema_version": "conflux-weave.w3.step-failure.v1",
                    "run_id": claim.run_id,
                    "step_id": claim.step_id,
                    "step_kind": step.kind,
                    "exception_type": type(exc).__name__,
                    "message": str(exc),
                    "automatic_retry": False,
                    "fallback": False,
                },
                producer_step_id=claim.step_id,
                schema_version="conflux-weave.w3.step-failure.v1",
            )
            unknown_provider = (
                effect.side_effect is SideEffectClass.PAID_EXTERNAL_UNKNOWN
                and effect.effect_state == "request_started"
            )
            error = ErrorRecord(
                code=(
                    "provider_outcome_unknown"
                    if unknown_provider
                    else "step_execution_failed"
                ),
                category=(ErrorCategory.PROVIDER if unknown_provider else ErrorCategory.UNKNOWN),
                stage=step.kind,
                retryable=False,
                user_message=(
                    "Provider outcome is unknown; no automatic replay was started."
                    if unknown_provider
                    else "The workflow Step failed; no automatic retry or fallback was started."
                ),
                technical_detail_ref=detail.artifact_id,
                affected_artifact_refs=(
                    (effect.intent_artifact_ref,) if effect.intent_artifact_ref else ()
                ),
                recovery_action=(
                    "Choose an explicit retry or fail decision after inspecting the request intent."
                    if unknown_provider
                    else "Inspect the technical detail Artifact and create a new Run after correcting the cause."
                ),
            )
            self.repository.record_error(claim, error, (detail,), now=now)
            if (
                unknown_provider
            ):
                self.repository.block_unknown_external_outcome(
                    claim, detail, now=now
                )
                return self._result(
                    claim, step.kind, "waiting_for_user", now=now
                )
            self.worker.fail(claim, detail.artifact_id, now=now)
            self.repository.transition_run(
                claim.run_id, RunStatus.FAILED, updated_at=now or self.clock()
            )
            return self._result(claim, step.kind, "failed", now=now)
        if self.repository.is_cancel_requested(claim.run_id):
            self.repository.finalize_cancellation(claim.run_id, now=now)
            return self._result(claim, step.kind, "cancelled", now=now)
        return self._result(
            claim,
            step.kind,
            self.repository.get_run(claim.run_id).status.value,
            now=now,
        )

    def request_cancel(self, run_id: str, *, now: str | None = None) -> RunRecord:
        return self.repository.request_cancel(run_id, now=now)

    def resume(
        self,
        run_id: str,
        decision: RecoveryDecision | None = None,
        *,
        now: str | None = None,
    ) -> RunRecord:
        return self.repository.resume_run(run_id, decision, now=now)

    def _execute_search_arxiv(
        self, claim: LeaseClaim, *, now: str | None
    ) -> None:
        task = self.repository.get_task_for_run(claim.run_id)
        intent = self._intent_artifact(
            claim,
            "arxiv_search",
            {
                "search_query": task.input["search_query"],
                "max_results": task.input["max_results"],
            },
        )
        self._fault("before_search_external_call")
        if not self._authorize_external_call(
            claim,
            intent,
            BudgetAmount(tool_calls=1, retrieval_rounds=1),
            stage="search_arxiv",
            now=now,
        ):
            return
        result = self.search_adapter.search(
            str(task.input["search_query"]),
            max_results=int(task.input["max_results"]),
            producer_step_id=claim.step_id,
        )
        checkpoint = self.artifact_store.put_json(
            {
                "schema_version": SEARCH_CHECKPOINT,
                "papers": [asdict(paper) for paper in result.papers],
                "snapshot": asdict(result.snapshot),
                "response_artifact_ref": result.response_artifact.artifact_id,
                "snapshot_artifact_ref": result.snapshot_artifact.artifact_id,
                "manifest_artifact_ref": result.manifest_artifact.artifact_id,
            },
            producer_step_id=claim.step_id,
            schema_version=SEARCH_CHECKPOINT,
        )
        self.repository.complete_external_attempt(
            claim,
            (
                intent,
                result.response_artifact,
                result.snapshot_artifact,
                result.manifest_artifact,
                checkpoint,
            ),
            request_artifact_ref=intent.artifact_id,
            response_artifact_ref=result.response_artifact.artifact_id,
            actual_usage=BudgetAmount(tool_calls=1, retrieval_rounds=1),
            now=now,
        )
        self._fault("search_response_committed")

    def _execute_rank_candidates(
        self, claim: LeaseClaim, *, now: str | None
    ) -> None:
        task = self.repository.get_task_for_run(claim.run_id)
        search = self._checkpoint(claim.run_id, "search_arxiv", SEARCH_CHECKPOINT)
        papers = tuple(_paper_from_json(item) for item in search["papers"])
        if not papers:
            raise ValueError("arXiv checkpoint has no candidates")
        retriever = BM25Retriever(
            RetrievalDocument(paper.arxiv_id, paper.title + " " + paper.summary)
            for paper in papers
        )
        hits = retriever.search(
            str(task.input["search_query"]), top_k=min(MAX_SELECTED, len(papers))
        ).hits
        by_id = {paper.arxiv_id: paper for paper in papers}
        selected = tuple(by_id[hit.document_id] for hit in hits)
        if not selected:
            raise ValueError("BM25 produced no positive-score candidates")
        snapshot = SourceSnapshot(**search["snapshot"])
        evidence = _paper_evidence(selected, snapshot.source_id)
        checkpoint = self.artifact_store.put_json(
            {
                "schema_version": RANK_CHECKPOINT,
                "selected_papers": [asdict(paper) for paper in selected],
                "evidence": [asdict(item) for item in evidence],
            },
            producer_step_id=claim.step_id,
            schema_version=RANK_CHECKPOINT,
        )
        self.worker.complete(claim, (checkpoint,), now=now)

    def _execute_synthesize_claims(
        self, claim: LeaseClaim, *, now: str | None
    ) -> None:
        task = self.repository.get_task_for_run(claim.run_id)
        rank = self._checkpoint(claim.run_id, "rank_candidates", RANK_CHECKPOINT)
        evidence = tuple(EvidenceRef(**item) for item in rank["evidence"])
        intent = self._intent_artifact(
            claim,
            "provider_chat_completion",
            {
                "prompt_version": PROMPT_VERSION,
                "model": self.chat_adapter.config.model,
                "evidence_ids": [item.evidence_id for item in evidence],
            },
        )
        if not self._authorize_external_call(
            claim,
            intent,
            BudgetAmount(output_tokens=MAX_OUTPUT_TOKENS, tool_calls=1),
            stage="synthesize_claims",
            now=now,
        ):
            return
        completion = self.chat_adapter.complete(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=_paper_prompt(str(task.input["query"]), evidence),
            max_output_tokens=MAX_OUTPUT_TOKENS,
            temperature=0.0,
            json_object=True,
            enable_thinking=False,
            producer_step_id=claim.step_id,
        )
        checkpoint = self.artifact_store.put_json(
            {
                "schema_version": SYNTHESIS_CHECKPOINT,
                "response_id": completion.response_id,
                "model": completion.model,
                "content": completion.content,
                "finish_reason": completion.finish_reason,
                "input_tokens": completion.input_tokens,
                "output_tokens": completion.output_tokens,
                "total_tokens": completion.total_tokens,
                "request_artifact_ref": completion.request_artifact.artifact_id,
                "response_artifact_ref": completion.response_artifact.artifact_id,
            },
            producer_step_id=claim.step_id,
            schema_version=SYNTHESIS_CHECKPOINT,
        )
        actual_usage = BudgetAmount(
            input_tokens=completion.input_tokens,
            output_tokens=completion.output_tokens,
            tool_calls=1,
        )
        budget = self.repository.get_budget_status(claim.run_id)
        will_exceed = any(
            getattr(budget.actual, dimension) + getattr(actual_usage, dimension)
            > getattr(budget.limit, dimension)
            for dimension in (
                "input_tokens", "output_tokens", "tool_calls", "retrieval_rounds"
            )
        )
        if will_exceed:
            overage_detail, overage_error = self._budget_error(
                claim,
                code="budget_actual_exceeded",
                stage="synthesize_claims",
                message=(
                    "Provider reported usage exceeded the frozen Run budget; "
                    "no subsequent external call is allowed."
                ),
                affected=(
                    intent.artifact_id,
                    completion.request_artifact.artifact_id,
                    completion.response_artifact.artifact_id,
                    checkpoint.artifact_id,
                ),
                recovery_action="Inspect usage and partial Artifacts; create a new Run with an explicitly frozen budget.",
            )
        else:
            overage_detail = overage_error = None
        self.repository.complete_external_attempt(
            claim,
            (
                intent,
                completion.request_artifact,
                completion.response_artifact,
                checkpoint,
            ),
            request_artifact_ref=completion.request_artifact.artifact_id,
            response_artifact_ref=completion.response_artifact.artifact_id,
            external_response_id=completion.response_id,
            actual_usage=actual_usage,
            overage_detail=overage_detail,
            overage_error=overage_error,
            now=now,
        )
        self._fault("provider_response_committed")

    def _authorize_external_call(
        self,
        claim: LeaseClaim,
        intent: ArtifactRef,
        reservation: BudgetAmount,
        *,
        stage: str,
        now: str | None,
    ) -> bool:
        detail, error = self._budget_error(
            claim,
            code="budget_reservation_denied",
            stage=stage,
            message="The frozen Run budget cannot cover this external call; the call was not started.",
            affected=(self.repository.get_run(claim.run_id).config_snapshot_ref, intent.artifact_id),
            recovery_action="Inspect the budget ledger; create a new Run with an explicitly frozen budget if the task should continue.",
        )
        return self.repository.authorize_external_call(
            claim, intent, reservation, detail, error, now=now
        )

    def _budget_error(
        self,
        claim: LeaseClaim,
        *,
        code: str,
        stage: str,
        message: str,
        affected: tuple[str, ...],
        recovery_action: str,
    ) -> tuple[ArtifactRef, ErrorRecord]:
        detail = self.artifact_store.put_json(
            {
                "schema_version": "conflux-weave.w3.budget-error.v1",
                "run_id": claim.run_id,
                "step_id": claim.step_id,
                "attempt_id": claim.attempt_id,
                "code": code,
                "stage": stage,
                "automatic_retry": False,
                "automatic_budget_expansion": False,
                "cost_enforcement": "unavailable",
            },
            producer_step_id=claim.step_id,
            schema_version="conflux-weave.w3.budget-error.v1",
        )
        return detail, ErrorRecord(
            code=code,
            category=ErrorCategory.BUDGET,
            stage=stage,
            retryable=False,
            user_message=message,
            technical_detail_ref=detail.artifact_id,
            affected_artifact_refs=affected,
            recovery_action=recovery_action,
        )

    def _execute_validate_delivery(
        self, claim: LeaseClaim, *, now: str | None
    ) -> None:
        rank = self._checkpoint(claim.run_id, "rank_candidates", RANK_CHECKPOINT)
        synthesis = self._checkpoint(
            claim.run_id, "synthesize_claims", SYNTHESIS_CHECKPOINT
        )
        evidence = tuple(EvidenceRef(**item) for item in rank["evidence"])
        claims, citations, model_limitations = _parse_claims(
            str(synthesis["content"]), evidence, claim.step_id
        )
        checkpoint = self.artifact_store.put_json(
            {
                "schema_version": VALIDATION_CHECKPOINT,
                "claims": [asdict(item) for item in claims],
                "citations": [asdict(item) for item in citations],
                "limitations": [*model_limitations, FINAL_LIMITATION],
                "unmet_criteria": [UNMET_CRITERION],
            },
            producer_step_id=claim.step_id,
            schema_version=VALIDATION_CHECKPOINT,
        )
        self.worker.complete(claim, (checkpoint,), now=now)

    def _execute_publish_delivery(
        self, claim: LeaseClaim, *, now: str | None
    ) -> None:
        task = self.repository.get_task_for_run(claim.run_id)
        run = self.repository.get_run(claim.run_id)
        search = self._checkpoint(claim.run_id, "search_arxiv", SEARCH_CHECKPOINT)
        rank = self._checkpoint(claim.run_id, "rank_candidates", RANK_CHECKPOINT)
        synthesis = self._checkpoint(
            claim.run_id, "synthesize_claims", SYNTHESIS_CHECKPOINT
        )
        validation = self._checkpoint(
            claim.run_id, "validate_delivery", VALIDATION_CHECKPOINT
        )
        evidence = tuple(EvidenceRef(**item) for item in rank["evidence"])
        claims = tuple(Claim(**item) for item in validation["claims"])
        citations = tuple(Citation(**item) for item in validation["citations"])
        limitations = tuple(str(item) for item in validation["limitations"])
        unmet = tuple(str(item) for item in validation["unmet_criteria"])
        report = render_evidence_report(
            title="arXiv 论文发现",
            intro_lines=(
                f"> 研究问题：{task.input['query']}",
                f"> arXiv 检索式：`{task.input['search_query']}`",
            ),
            blocks=(
                AnswerBlock(
                    "候选论文及相关性",
                    "\n".join(f"- {claim_item.text}" for claim_item in claims),
                    EvidenceSupportStatus.PARTIAL_SUPPORT,
                    tuple(item.claim_id for item in claims),
                ),
            ),
            claims=claims,
            evidence=evidence,
            citations=citations,
            evidence_trust={
                item.evidence_id: SourceTrustLevel.GENERAL_SOURCE
                for item in evidence
            },
            limitations=limitations,
        )
        report_artifact = self.artifact_store.put_bytes(
            report.encode("utf-8"),
            media_type="text/markdown; charset=utf-8",
            producer_step_id=claim.step_id,
            schema_version="conflux-weave.paper-discovery-report.v1",
        )
        manifest = self.artifact_store.put_json(
            {
                "schema_version": SCHEMA_VERSION,
                "run_id": claim.run_id,
                "status": RunStatus.PARTIAL.value,
                "query": task.input["query"],
                "search_query": task.input["search_query"],
                "config_artifact_ref": run.config_snapshot_ref,
                "search_response_artifact_ref": search["response_artifact_ref"],
                "search_snapshot_artifact_ref": search["snapshot_artifact_ref"],
                "search_manifest_artifact_ref": search["manifest_artifact_ref"],
                "selected_arxiv_ids": [
                    item["arxiv_id"] for item in rank["selected_papers"]
                ],
                "provider_request_artifact_ref": synthesis[
                    "request_artifact_ref"
                ],
                "provider_response_artifact_ref": synthesis[
                    "response_artifact_ref"
                ],
                "provider_response_id": synthesis["response_id"],
                "usage": {
                    "input_tokens": synthesis["input_tokens"],
                    "output_tokens": synthesis["output_tokens"],
                    "total_tokens": synthesis["total_tokens"],
                },
                "report_artifact_ref": report_artifact.artifact_id,
                "claim_count": len(claims),
                "evidence_count": len(evidence),
                "citation_count": len(citations),
                "limitations": list(limitations),
                "unmet_criteria": list(unmet),
                "automatic_retry": False,
                "fallback": False,
                "secret_recorded": False,
            },
            producer_step_id=claim.step_id,
            schema_version=SCHEMA_VERSION,
        )
        delivery = DeliveryRecord(
            run_id=claim.run_id,
            disposition=DeliveryDisposition.PARTIAL,
            artifact_refs=(report_artifact.artifact_id, manifest.artifact_id),
            evidence_refs=tuple(item.evidence_id for item in evidence),
            limitations=limitations,
            unmet_criteria=unmet,
            recovery_actions=(
                "如需完整综述，授权出版社/Crossref 检索和全文核验后创建新 Run。",
            ),
        )
        self._fault("publish_artifacts_written")
        self.repository.publish_delivery(
            claim.run_id,
            RunStatus.PARTIAL,
            delivery,
            (report_artifact, manifest),
            claim=claim,
            published_at=now,
        )

    def _result(
        self,
        claim: LeaseClaim,
        step_kind: str,
        status: str,
        *,
        now: str | None,
    ) -> DurableWorkResult:
        self._emit_trace(claim, step_kind, status, now=now)
        return DurableWorkResult(claim.run_id, step_kind, status)

    def _emit_trace(
        self,
        claim: LeaseClaim,
        step_kind: str,
        status: str,
        *,
        now: str | None,
    ) -> None:
        if self.trace is None:
            return
        try:
            record = self._trace_record(claim, step_kind, status)
            self.trace.export(record)
        except Exception as exc:
            try:
                self.repository.record_telemetry_drop(
                    claim.run_id,
                    step_id=claim.step_id,
                    attempt_id=claim.attempt_id,
                    span_name=f"conflux_weave.{step_kind}",
                    reason=type(exc).__name__,
                    now=now,
                )
            except Exception:
                pass

    def _trace_record(
        self, claim: LeaseClaim, step_kind: str, status: str
    ) -> TraceRecord:
        task = self.repository.get_task_for_run(claim.run_id)
        run = self.repository.get_run(claim.run_id)
        step = next(
            item
            for item in self.repository.get_steps(claim.run_id)
            if item.step_id == claim.step_id
        )
        budget = self.repository.get_budget_status(claim.run_id)
        span_kind = {
            "search_arxiv": "TOOL",
            "synthesize_claims": "LLM",
        }.get(step_kind, "CHAIN")
        return TraceRecord(
            name=f"conflux_weave.{step_kind}",
            attributes={
                "task_id": task.task_id,
                "run_id": claim.run_id,
                "step_id": claim.step_id,
                "attempt_id": claim.attempt_id,
                "attempt": claim.attempt_number,
                "workflow_version": run.workflow_version,
                "provider_model": str(task.input.get("model", "none")),
                "prompt_version": str(task.input.get("prompt_version", "none")),
                "budget_input_tokens_limit": budget.limit.input_tokens,
                "budget_output_tokens_limit": budget.limit.output_tokens,
                "budget_tool_calls_actual": budget.actual.tool_calls,
                "budget_retrieval_rounds_actual": budget.actual.retrieval_rounds,
                "artifact_refs": tuple(step.output_refs),
                "status": status,
                "openinference.span.kind": span_kind,
            },
        )

    def _record_trace_drop(self, record: TraceRecord, reason: str) -> None:
        attributes = record.attributes
        self.repository.record_telemetry_drop(
            str(attributes["run_id"]),
            step_id=str(attributes["step_id"]),
            attempt_id=str(attributes["attempt_id"]),
            span_name=record.name,
            reason=reason,
            now=self.clock(),
        )

    def _fault(self, point: str) -> None:
        if self.fault_hook is not None:
            self.fault_hook(point)

    def _checkpoint(self, run_id: str, step_kind: str, schema: str) -> dict:
        step = next(
            item for item in self.repository.get_steps(run_id) if item.kind == step_kind
        )
        artifacts = self.repository.get_step_artifacts(step.step_id)
        matches = [artifact for artifact in artifacts if artifact.schema_version == schema]
        if len(matches) != 1:
            raise ValueError(f"expected one {schema} Artifact for {step_kind}")
        return json.loads(self.artifact_store.read_bytes(matches[0]))

    def _intent_artifact(
        self, claim: LeaseClaim, operation: str, parameters: dict
    ):
        return self.artifact_store.put_json(
            {
                "schema_version": "conflux-weave.w3.external-call-intent.v1",
                "run_id": claim.run_id,
                "step_id": claim.step_id,
                "attempt_id": claim.attempt_id,
                "operation": operation,
                "parameters": parameters,
                "automatic_retry": False,
                "secret_recorded": False,
            },
            producer_step_id=claim.step_id,
            schema_version="conflux-weave.w3.external-call-intent.v1",
        )


def _paper_from_json(value: dict) -> ArxivPaper:
    return ArxivPaper(
        arxiv_id=str(value["arxiv_id"]),
        title=str(value["title"]),
        summary=str(value["summary"]),
        authors=tuple(value["authors"]),
        published=str(value["published"]),
        updated=str(value["updated"]),
        entry_url=str(value["entry_url"]),
        pdf_url=value["pdf_url"],
        categories=tuple(value["categories"]),
    )


def _idempotency_key(frozen_input: dict) -> str:
    payload = json.dumps(
        frozen_input,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "paper-discovery-durable:" + hashlib.sha256(payload).hexdigest()


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid4()}"
