"""Durable paper-discovery lifecycle and Step orchestration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict
import json

from conflux_weave.core import (
    BudgetLedger,
    ErrorCategory,
    ErrorRecord,
    RunRecord,
    RunStatus,
    StepRecord,
    StepStatus,
    TaskSpec,
)
from conflux_weave.paper_discovery import (
    MAX_OUTPUT_TOKENS,
    MAX_SELECTED,
    PROMPT_VERSION,
    SCHEMA_VERSION,
    WORKFLOW_VERSION,
    PaperSearchPort,
)
from conflux_weave.provider import OpenAICompatibleChatAdapter
from conflux_weave.runtime.artifacts import LocalArtifactStore
from conflux_weave.runtime.durable_paper_shared import (
    DURABLE_WORKFLOW_VERSION,
    STEP_KINDS,
    DurableWorkResult,
    _idempotency_key,
    _new_id,
)
from conflux_weave.runtime.sqlite import (
    LeaseClaim,
    RecoveryDecision,
    SideEffectClass,
    SQLiteRuntimeRepository,
    StepPolicy,
    SubmissionResult,
)
from conflux_weave.runtime.telemetry import SafeTraceExporter, TraceExporter
from conflux_weave.runtime.worker import SQLiteStepWorker
from conflux_weave.runtime.durable_paper_steps import DurablePaperStepMixin
from conflux_weave.runtime.durable_paper_trace import DurablePaperTraceMixin


class DurablePaperDiscoveryRuntime(DurablePaperStepMixin, DurablePaperTraceMixin):
    def __init__(
        self,
        repository: SQLiteRuntimeRepository,
        artifact_store: LocalArtifactStore,
        search_adapter: PaperSearchPort,
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
        self.worker = SQLiteStepWorker(
            repository, worker_id, lease_seconds, DURABLE_WORKFLOW_VERSION
        )
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
                    "automatic_retry": bool(getattr(exc, "retry_delays", ())),
                    "automatic_retry_scope": "read_only_source_get_only",
                    "source_retry_delays": list(getattr(exc, "retry_delays", ())),
                    "provider_automatic_retry": False,
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
                category=(
                    ErrorCategory.PROVIDER
                    if unknown_provider
                    else ErrorCategory.UNKNOWN
                ),
                stage=step.kind,
                retryable=False,
                user_message=(
                    "Provider outcome is unknown; no automatic replay was started."
                    if unknown_provider
                    else "The workflow Step failed; no fallback or automatic Provider replay was started."
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
            if unknown_provider:
                self.repository.block_unknown_external_outcome(claim, detail, now=now)
                return self._result(claim, step.kind, "waiting_for_user", now=now)
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

    def _fault(self, point: str) -> None:
        if self.fault_hook is not None:
            self.fault_hook(point)

    def _checkpoint(self, run_id: str, step_kind: str, schema: str) -> dict:
        step = next(
            item for item in self.repository.get_steps(run_id) if item.kind == step_kind
        )
        artifacts = self.repository.get_step_artifacts(step.step_id)
        matches = [
            artifact for artifact in artifacts if artifact.schema_version == schema
        ]
        if len(matches) != 1:
            raise ValueError(f"expected one {schema} Artifact for {step_kind}")
        return json.loads(self.artifact_store.read_bytes(matches[0]))

    def _intent_artifact(self, claim: LeaseClaim, operation: str, parameters: dict):
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
