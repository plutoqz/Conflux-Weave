"""Durable SQLite lifecycle for verified S1 research executions."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass
import hashlib
import json
from typing import Any, Protocol
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
from conflux_weave.runtime.artifacts import LocalArtifactStore
from conflux_weave.runtime.durable_paper_shared import DurableWorkResult
from conflux_weave.runtime.sqlite import (
    BudgetAmount,
    LeaseClaim,
    SideEffectClass,
    SQLiteRuntimeRepository,
    StepPolicy,
    SubmissionResult,
)
from conflux_weave.runtime.worker import SQLiteStepWorker


DURABLE_RESEARCH_WORKFLOW_VERSION = "durable-verified-research-v1"
VERIFIED_RESEARCH_TASK = "verified_paper_research"
MANAGED_RESEARCH_TASK = "managed_verified_research"
RESEARCH_TASK_KINDS = (VERIFIED_RESEARCH_TASK, MANAGED_RESEARCH_TASK)
STEP_KINDS = ("execute_research", "publish_delivery")
EXECUTION_SCHEMA = "conflux-weave.durable-research-execution.v1"
DURABLE_RESEARCH_EVIDENCE_SCHEMA = "conflux-weave.durable-research-evidence.v1"


@dataclass(frozen=True, slots=True)
class DurableResearchExecution:
    """Executor result needed by the durable boundary.

    Usage is aggregate usage for the entire opaque research batch. Individual
    Provider call recovery remains the responsibility of a future staged
    executor; this runtime only guarantees that an interrupted batch is not
    replayed automatically.
    """

    report_artifact_id: str
    manifest_artifact_id: str
    evidence_refs: tuple[str, ...]
    evidence_records: tuple[dict[str, Any], ...]
    usage: BudgetAmount
    provider_call_count: int


class ResearchExecutor(Protocol):
    def __call__(
        self,
        task_kind: str,
        objective: str,
        max_subquestions: int,
    ) -> DurableResearchExecution: ...


class VerifiedWorkflowExecutorAdapter:
    """Adapt the existing verified workflows to the durable executor contract."""

    def __init__(
        self,
        artifact_store: LocalArtifactStore,
        verified_workflow: Any,
        managed_workflow: Any | None = None,
    ) -> None:
        self.artifact_store = artifact_store
        self.verified_workflow = verified_workflow
        self.managed_workflow = managed_workflow

    def __call__(
        self,
        task_kind: str,
        objective: str,
        max_subquestions: int,
    ) -> DurableResearchExecution:
        if task_kind == VERIFIED_RESEARCH_TASK:
            result = self.verified_workflow.execute(objective)
            evidence_refs = tuple(item.evidence_id for item in result.evidence)
            evidence_records = tuple(asdict(item) for item in result.evidence)
            retrieval_rounds = 1
        elif task_kind == MANAGED_RESEARCH_TASK:
            if self.managed_workflow is None:
                raise ValueError("managed research workflow is not configured")
            result = self.managed_workflow.execute(
                objective, max_subquestions=max_subquestions
            )
            evidence_refs = tuple(
                f"sq{sub_index}-{item.evidence_id}"
                for sub_index, subrun in enumerate(result.subruns, 1)
                for item in subrun.evidence
            )
            evidence_records = tuple(
                {
                    **asdict(item),
                    "evidence_id": f"sq{sub_index}-{item.evidence_id}",
                }
                for sub_index, subrun in enumerate(result.subruns, 1)
                for item in subrun.evidence
            )
            retrieval_rounds = len(result.subruns)
        else:
            raise ValueError(f"unsupported research task kind: {task_kind}")
        input_tokens, output_tokens, provider_calls = self._collect_usage(
            result.manifest_artifact_id
        )
        if provider_calls < 1:
            raise ValueError("research manifest contains no traceable Provider response")
        return DurableResearchExecution(
            result.report_artifact_id,
            result.manifest_artifact_id,
            evidence_refs,
            evidence_records,
            BudgetAmount(
                input_tokens,
                output_tokens,
                provider_calls,
                retrieval_rounds,
            ),
            provider_calls,
        )

    def _collect_usage(self, root_artifact_id: str) -> tuple[int, int, int]:
        pending = [root_artifact_id]
        visited: set[str] = set()
        input_tokens = 0
        output_tokens = 0
        provider_calls = 0
        while pending:
            artifact_id = pending.pop()
            if artifact_id in visited:
                continue
            visited.add(artifact_id)
            digest = DurableResearchRuntime._artifact_digest(artifact_id)
            path = self.artifact_store.path_for_digest(digest)
            if not path.is_file():
                raise ValueError(f"manifest references missing Artifact: {artifact_id}")
            try:
                payload = json.loads(path.read_bytes())
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            pending.extend(self._artifact_ids(payload))
            if not isinstance(payload, dict):
                continue
            usage = payload.get("usage")
            if payload.get("endpoint") in {
                "/chat/completions",
                "/embeddings",
                "/rerank",
            } and isinstance(payload.get("request"), dict):
                provider_calls += 1
            if "choices" in payload and isinstance(usage, dict):
                input_tokens += self._usage_int(usage, "prompt_tokens")
                output_tokens += self._usage_int(usage, "completion_tokens")
            elif "data" in payload and isinstance(payload["data"], list):
                if isinstance(usage, dict):
                    input_tokens += self._usage_int(usage, "prompt_tokens")
            elif "results" in payload and isinstance(payload["results"], list):
                if isinstance(usage, dict):
                    input_tokens += self._usage_int(usage, "prompt_tokens")
                    output_tokens += self._usage_int(usage, "completion_tokens")
        return input_tokens, output_tokens, provider_calls

    @classmethod
    def _artifact_ids(cls, value: Any) -> list[str]:
        if isinstance(value, dict):
            return [
                artifact_id
                for item in value.values()
                for artifact_id in cls._artifact_ids(item)
            ]
        if isinstance(value, list):
            return [
                artifact_id for item in value for artifact_id in cls._artifact_ids(item)
            ]
        if isinstance(value, str) and value.startswith("artifact-sha256-"):
            return [value]
        return []

    @staticmethod
    def _usage_int(usage: dict, key: str) -> int:
        value = usage.get(key, 0)
        return value if isinstance(value, int) and value >= 0 else 0


class DurableResearchRuntime:
    def __init__(
        self,
        repository: SQLiteRuntimeRepository,
        artifact_store: LocalArtifactStore,
        executor: ResearchExecutor,
        *,
        worker_id: str = "verified-research-worker",
        lease_seconds: int = 900,
        clock: Callable[[], str] | None = None,
        id_factory: Callable[[str], str] | None = None,
        code_revision: str = "unknown",
    ) -> None:
        self.repository = repository
        self.artifact_store = artifact_store
        self.executor = executor
        self.worker = SQLiteStepWorker(
            repository,
            worker_id,
            lease_seconds,
            DURABLE_RESEARCH_WORKFLOW_VERSION,
        )
        self.clock = clock or repository.clock
        self.id_factory = id_factory or (lambda prefix: f"{prefix}-{uuid4().hex}")
        self.code_revision = code_revision

    def submit(
        self,
        objective: str,
        *,
        task_kind: str = VERIFIED_RESEARCH_TASK,
        max_subquestions: int = 4,
        budget: BudgetLedger | None = None,
        idempotency_key: str | None = None,
    ) -> SubmissionResult:
        normalized = objective.strip()
        if not normalized:
            raise ValueError("objective must not be empty")
        if task_kind not in RESEARCH_TASK_KINDS:
            raise ValueError(f"unsupported research task kind: {task_kind}")
        if not 2 <= max_subquestions <= 4:
            raise ValueError("max_subquestions must be between 2 and 4")

        provider_call_limit = 6 if task_kind == VERIFIED_RESEARCH_TASK else 1 + 6 * max_subquestions
        retrieval_round_limit = 1 if task_kind == VERIFIED_RESEARCH_TASK else max_subquestions
        frozen_budget = budget or BudgetLedger(
            900,
            160_000,
            32_000,
            "provider-price-not-frozen",
            provider_call_limit,
            retrieval_round_limit,
            1,
        )
        reservation = BudgetAmount(
            input_tokens=frozen_budget.input_tokens,
            output_tokens=frozen_budget.output_tokens,
            tool_calls=frozen_budget.tool_calls,
            retrieval_rounds=frozen_budget.retrieval_rounds,
        )
        task_id = self.id_factory("task")
        run_id = self.id_factory("run")
        step_ids = {kind: f"{run_id}:{kind}" for kind in STEP_KINDS}
        created_at = self.clock()
        frozen_input = {
            "objective": normalized,
            "task_kind": task_kind,
            "max_subquestions": max_subquestions,
            "workflow_version": DURABLE_RESEARCH_WORKFLOW_VERSION,
            "code_revision": self.code_revision,
            "budget": asdict(frozen_budget),
            "reservation": asdict(reservation),
            "recovery_granularity": "opaque_paid_research_batch",
            "automatic_replay_after_unknown_outcome": False,
        }
        config = self.artifact_store.put_json(
            {
                "schema_version": "conflux-weave.durable-research-config.v1",
                **frozen_input,
                "provider_call_recovery": "batch_boundary_only",
                "individual_provider_call_recovery": "not_implemented",
                "cost_enforcement": "unavailable",
            },
            producer_step_id=step_ids["execute_research"],
            schema_version="conflux-weave.durable-research-config.v1",
        )
        task = TaskSpec(
            task_id,
            task_kind,
            frozen_input,
            DURABLE_RESEARCH_WORKFLOW_VERSION,
            idempotency_key or self._idempotency_key(frozen_input),
        )
        run = RunRecord(
            run_id,
            task_id,
            RunStatus.ACCEPTED,
            DURABLE_RESEARCH_WORKFLOW_VERSION,
            config.artifact_id,
            frozen_budget,
            created_at,
            created_at,
        )
        steps = tuple(
            StepRecord(
                step_ids[kind],
                run_id,
                kind,
                1,
                StepStatus.PENDING,
                (config.artifact_id,) if kind == "execute_research" else (),
            )
            for kind in STEP_KINDS
        )
        policies = {
            step_ids["execute_research"]: StepPolicy(
                SideEffectClass.PAID_EXTERNAL_UNKNOWN,
                "treat the research batch as unknown after intent; never replay automatically",
            ),
            step_ids["publish_delivery"]: StepPolicy(
                SideEffectClass.IDEMPOTENT_LOCAL_WRITE,
                "atomic local Delivery publication",
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
            return DurableWorkResult(claim.run_id, step.kind, "cancelled")
        try:
            getattr(self, f"_execute_{step.kind}")(claim, now=now)
        except Exception as exc:
            if self.repository.is_cancel_requested(claim.run_id):
                self.repository.cancel_claim(claim, now=now)
                return DurableWorkResult(claim.run_id, step.kind, "cancelled")
            effect = self.repository.get_attempt_effect(claim.attempt_id)
            detail = self.artifact_store.put_json(
                {
                    "schema_version": "conflux-weave.durable-research-failure.v1",
                    "run_id": claim.run_id,
                    "step_id": claim.step_id,
                    "step_kind": step.kind,
                    "exception_type": type(exc).__name__,
                    "message": str(exc),
                    "automatic_replay": False,
                    "recovery_granularity": "opaque_paid_research_batch",
                },
                producer_step_id=claim.step_id,
                schema_version="conflux-weave.durable-research-failure.v1",
            )
            unknown = (
                effect.side_effect is SideEffectClass.PAID_EXTERNAL_UNKNOWN
                and effect.effect_state == "request_started"
            )
            error = ErrorRecord(
                "research_batch_outcome_unknown" if unknown else "research_step_failed",
                ErrorCategory.PROVIDER if unknown else ErrorCategory.UNKNOWN,
                step.kind,
                False,
                (
                    "Research execution stopped after its paid-call intent; no automatic replay was started."
                    if unknown
                    else "The durable research Step failed."
                ),
                detail.artifact_id,
                ((effect.intent_artifact_ref,) if effect.intent_artifact_ref else ()),
                (
                    "Inspect the batch intent and choose an explicit retry or fail decision."
                    if unknown
                    else "Inspect the failure Artifact and submit a corrected Run."
                ),
            )
            self.repository.record_error(claim, error, (detail,), now=now)
            if unknown:
                self.repository.block_unknown_external_outcome(claim, detail, now=now)
                return DurableWorkResult(claim.run_id, step.kind, "waiting_for_user")
            self.worker.fail(claim, detail.artifact_id, now=now)
            self.repository.transition_run(
                claim.run_id, RunStatus.FAILED, updated_at=now or self.clock()
            )
            return DurableWorkResult(claim.run_id, step.kind, "failed")
        if self.repository.is_cancel_requested(claim.run_id):
            self.repository.finalize_cancellation(claim.run_id, now=now)
            return DurableWorkResult(claim.run_id, step.kind, "cancelled")
        return DurableWorkResult(
            claim.run_id,
            step.kind,
            self.repository.get_run(claim.run_id).status.value,
        )

    def request_cancel(self, run_id: str, *, now: str | None = None) -> RunRecord:
        return self.repository.request_cancel(run_id, now=now)

    def resume(self, run_id: str, decision=None, *, now: str | None = None) -> RunRecord:
        return self.repository.resume_run(run_id, decision, now=now)

    def _execute_execute_research(
        self, claim: LeaseClaim, *, now: str | None
    ) -> None:
        task = self.repository.get_task_for_run(claim.run_id)
        intent = self.artifact_store.put_json(
            {
                "schema_version": "conflux-weave.durable-research-intent.v1",
                "run_id": claim.run_id,
                "task_kind": task.kind,
                "objective": task.input["objective"],
                "max_subquestions": task.input["max_subquestions"],
                "automatic_replay": False,
            },
            producer_step_id=claim.step_id,
            schema_version="conflux-weave.durable-research-intent.v1",
        )
        denial_detail = self.artifact_store.put_json(
            {
                "schema_version": "conflux-weave.budget-denial.v1",
                "run_id": claim.run_id,
                "reason": "insufficient durable research budget",
            },
            producer_step_id=claim.step_id,
            schema_version="conflux-weave.budget-denial.v1",
        )
        denial_error = ErrorRecord(
            "research_budget_denied",
            ErrorCategory.BUDGET,
            "execute_research",
            False,
            "The research batch did not start because its budget reservation was denied.",
            denial_detail.artifact_id,
            (),
            "Submit a new Run with a sufficient frozen budget.",
        )
        reservation = BudgetAmount(**task.input["reservation"])
        authorized = self.repository.authorize_external_call(
            claim,
            intent,
            reservation,
            denial_detail,
            denial_error,
            now=now,
        )
        if not authorized:
            return

        execution = self.executor(
            task.kind,
            str(task.input["objective"]),
            int(task.input["max_subquestions"]),
        )
        self._validate_execution(execution)
        response = self.artifact_store.put_json(
            {
                "schema_version": EXECUTION_SCHEMA,
                "report_artifact_id": execution.report_artifact_id,
                "manifest_artifact_id": execution.manifest_artifact_id,
                "evidence_refs": list(execution.evidence_refs),
                "evidence": list(execution.evidence_records),
                "usage": asdict(execution.usage),
                "provider_call_count": execution.provider_call_count,
                "usage_granularity": "aggregate_research_batch",
            },
            producer_step_id=claim.step_id,
            schema_version=EXECUTION_SCHEMA,
        )
        overage = self.artifact_store.put_json(
            {
                "schema_version": "conflux-weave.budget-overage.v1",
                "run_id": claim.run_id,
                "actual_usage": asdict(execution.usage),
            },
            producer_step_id=claim.step_id,
            schema_version="conflux-weave.budget-overage.v1",
        )
        overage_error = ErrorRecord(
            "research_budget_actual_exceeded",
            ErrorCategory.BUDGET,
            "execute_research",
            False,
            "Reported research usage exceeded the frozen Run budget.",
            overage.artifact_id,
            (response.artifact_id,),
            "Inspect the completed response and submit a new Run with a corrected budget.",
        )
        self.repository.complete_external_attempt(
            claim,
            (intent, response),
            request_artifact_ref=intent.artifact_id,
            response_artifact_ref=response.artifact_id,
            external_response_id=execution.manifest_artifact_id,
            actual_usage=execution.usage,
            overage_detail=overage,
            overage_error=overage_error,
            now=now,
        )

    def _execute_publish_delivery(
        self, claim: LeaseClaim, *, now: str | None
    ) -> None:
        execute_step = next(
            step
            for step in self.repository.get_steps(claim.run_id)
            if step.kind == "execute_research"
        )
        artifacts = self.repository.get_step_artifacts(execute_step.step_id)
        response_ref = next(
            artifact for artifact in artifacts if artifact.schema_version == EXECUTION_SCHEMA
        )
        response = json.loads(self.artifact_store.read_bytes(response_ref))
        report = self._copy_artifact(
            response["report_artifact_id"],
            claim.step_id,
            "text/markdown; charset=utf-8",
            "conflux-weave.durable-research-report.v1",
        )
        manifest = self._copy_artifact(
            response["manifest_artifact_id"],
            claim.step_id,
            "application/json",
            "conflux-weave.durable-research-manifest.v1",
        )
        evidence = self.artifact_store.put_json(
            {
                "schema_version": DURABLE_RESEARCH_EVIDENCE_SCHEMA,
                "evidence": response["evidence"],
            },
            producer_step_id=claim.step_id,
            schema_version=DURABLE_RESEARCH_EVIDENCE_SCHEMA,
        )
        delivery = DeliveryRecord(
            claim.run_id,
            DeliveryDisposition.COMPLETE,
            (report.artifact_id, manifest.artifact_id, evidence.artifact_id),
            tuple(response["evidence_refs"]),
            (
                "Durability is enforced at an opaque paid research-batch boundary; individual Provider-call recovery is not implemented.",
            ),
        )
        self.repository.publish_delivery(
            claim.run_id,
            RunStatus.SUCCEEDED,
            delivery,
            (report, manifest, evidence),
            claim=claim,
            published_at=now,
        )

    def _copy_artifact(
        self,
        artifact_id: str,
        producer_step_id: str,
        media_type: str,
        schema_version: str,
    ):
        digest = self._artifact_digest(artifact_id)
        source = self.artifact_store.path_for_digest(digest)
        if not source.is_file():
            raise ValueError(f"executor Artifact is missing: {artifact_id}")
        return self.artifact_store.put_bytes(
            source.read_bytes(),
            media_type=media_type,
            producer_step_id=producer_step_id,
            schema_version=schema_version,
        )

    def _validate_execution(self, execution: DurableResearchExecution) -> None:
        if execution.provider_call_count < 1:
            raise ValueError("research execution must declare at least one Provider call")
        if execution.usage.tool_calls != execution.provider_call_count:
            raise ValueError("tool_calls must equal provider_call_count")
        if tuple(item.get("evidence_id") for item in execution.evidence_records) != (
            execution.evidence_refs
        ):
            raise ValueError("evidence records must match declared evidence refs")
        for artifact_id in (
            execution.report_artifact_id,
            execution.manifest_artifact_id,
        ):
            digest = self._artifact_digest(artifact_id)
            if not self.artifact_store.path_for_digest(digest).is_file():
                raise ValueError(f"executor Artifact is missing: {artifact_id}")

    @staticmethod
    def _artifact_digest(artifact_id: str) -> str:
        prefix = "artifact-sha256-"
        if not artifact_id.startswith(prefix):
            raise ValueError(f"invalid Artifact id: {artifact_id}")
        digest = artifact_id[len(prefix) :]
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise ValueError(f"invalid Artifact id: {artifact_id}")
        return digest

    @staticmethod
    def _idempotency_key(payload: dict) -> str:
        canonical = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return "durable-research:" + hashlib.sha256(canonical).hexdigest()
