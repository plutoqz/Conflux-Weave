"""Deterministic W1.4 workflow for user-visible outcome and failure semantics."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
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
    UserInputKind,
    UserInputRequest,
    require_transition,
)
from conflux_weave.evidence import ArtifactRef, EvidenceRef
from conflux_weave.runtime.artifacts import LocalArtifactStore


OUTCOME_WORKFLOW_VERSION = "fixed-outcome-validation-v1"
OUTCOME_SCHEMA_VERSION = "conflux-weave.outcome-validation.v1"


class OutcomeScenario(StrEnum):
    MISSING_INPUT = "missing_input"
    NO_ANSWER = "no_answer"
    SOURCE_FAILURE = "source_failure"
    SOURCE_PARTIAL = "source_partial"
    BUDGET_FAILURE = "budget_failure"


@dataclass(frozen=True, slots=True)
class OutcomeExecution:
    task: TaskSpec
    run_history: tuple[RunRecord, ...]
    step_history: tuple[StepRecord, ...]
    artifact: ArtifactRef
    delivery: DeliveryRecord | None = None
    user_input_request: UserInputRequest | None = None
    error: ErrorRecord | None = None
    validation_only: bool = True

    @property
    def final_run(self) -> RunRecord:
        return self.run_history[-1]

    @property
    def final_step(self) -> StepRecord:
        return self.step_history[-1]


class FixedOutcomeWorkflow:
    def __init__(
        self,
        artifact_store: LocalArtifactStore,
        *,
        clock: Callable[[], str] | None = None,
        id_factory: Callable[[str], str] | None = None,
    ) -> None:
        self.artifact_store = artifact_store
        self.clock = clock or _utc_now
        self.id_factory = id_factory or _new_id

    def execute(self, query: str, scenario: OutcomeScenario) -> OutcomeExecution:
        normalized_query = query.strip()
        if not normalized_query:
            raise ValueError("query must not be empty")
        task_id = self.id_factory("task")
        run_id = self.id_factory("run")
        step_id = self.id_factory("step")
        created_at = self.clock()
        budget = BudgetLedger(
            wall_clock_seconds=30,
            input_tokens=0,
            output_tokens=0,
            estimated_cost="0",
            tool_calls=0,
            retrieval_rounds=0,
            concurrency=1,
        )
        task = TaskSpec(
            task_id=task_id,
            kind="outcome_validation",
            input={
                "query": normalized_query,
                "scenario": scenario.value,
                "validation_only": True,
            },
            requested_policy=OUTCOME_WORKFLOW_VERSION,
            idempotency_key=_idempotency_key(normalized_query, scenario),
        )
        runs = [
            RunRecord(
                run_id=run_id,
                task_id=task_id,
                status=RunStatus.ACCEPTED,
                workflow_version=OUTCOME_WORKFLOW_VERSION,
                config_snapshot_ref=f"inline:{OUTCOME_WORKFLOW_VERSION}",
                budget=budget,
                created_at=created_at,
                updated_at=created_at,
            )
        ]
        self._transition(runs, RunStatus.QUEUED)
        self._transition(runs, RunStatus.RUNNING)
        steps = [
            self._step(step_id, run_id, StepStatus.PENDING),
            self._step(step_id, run_id, StepStatus.RUNNING),
        ]

        if scenario is OutcomeScenario.MISSING_INPUT:
            return self._missing_input(task, runs, steps, step_id, run_id)
        if scenario is OutcomeScenario.NO_ANSWER:
            return self._no_answer(task, runs, steps, step_id, run_id)
        if scenario is OutcomeScenario.SOURCE_PARTIAL:
            return self._source_partial(task, runs, steps, step_id, run_id)
        if scenario is OutcomeScenario.SOURCE_FAILURE:
            return self._failure(
                task,
                runs,
                steps,
                step_id,
                run_id,
                code="source_unavailable",
                category=ErrorCategory.NETWORK,
                user_message="登记来源不可用，且没有足够 Evidence 形成部分交付。",
                recovery_action="检查来源访问条件或提供授权的本地快照后重新运行。",
            )
        return self._failure(
            task,
            runs,
            steps,
            step_id,
            run_id,
            code="budget_exhausted",
            category=ErrorCategory.BUDGET,
            user_message="本次任务的冻结预算已耗尽。",
            recovery_action="确认新的预算上限后创建新 Run；不要自动扩容或重试。",
        )

    def _missing_input(
        self,
        task: TaskSpec,
        runs: list[RunRecord],
        steps: list[StepRecord],
        step_id: str,
        run_id: str,
    ) -> OutcomeExecution:
        request = UserInputRequest(
            request_id=self.id_factory("input"),
            run_id=run_id,
            step_id=step_id,
            kind=UserInputKind.CLARIFICATION,
            reason_code="review_document_missing",
            prompt="请提供需要处理的综述 PDF、Markdown、DOI 或 URL。",
            requested_inputs=("review_document",),
            created_at=self.clock(),
        )
        artifact = self._artifact(
            step_id,
            {
                "scenario": OutcomeScenario.MISSING_INPUT.value,
                "request_id": request.request_id,
                "reason_code": request.reason_code,
                "prompt": request.prompt,
                "requested_inputs": list(request.requested_inputs),
                "delivery_created": False,
                "citations": [],
            },
        )
        steps.append(
            self._step(
                step_id,
                run_id,
                StepStatus.WAITING_FOR_USER,
                output_refs=(artifact.artifact_id,),
            )
        )
        self._transition(runs, RunStatus.WAITING_FOR_USER)
        return OutcomeExecution(
            task=task,
            run_history=tuple(runs),
            step_history=tuple(steps),
            artifact=artifact,
            user_input_request=request,
        )

    def _no_answer(
        self,
        task: TaskSpec,
        runs: list[RunRecord],
        steps: list[StepRecord],
        step_id: str,
        run_id: str,
    ) -> OutcomeExecution:
        limitation = "没有来源满足全部冻结硬约束；未放宽约束，也未生成引用。"
        artifact = self._artifact(
            step_id,
            {
                "scenario": OutcomeScenario.NO_ANSWER.value,
                "disposition": DeliveryDisposition.NO_ANSWER.value,
                "limitations": [limitation],
                "claims": [],
                "evidence": [],
                "citations": [],
            },
        )
        delivery = DeliveryRecord(
            run_id=run_id,
            disposition=DeliveryDisposition.NO_ANSWER,
            artifact_refs=(artifact.artifact_id,),
            limitations=(limitation,),
            recovery_actions=("由用户明确选择是否放宽某一硬约束。",),
        )
        steps.append(
            self._step(
                step_id,
                run_id,
                StepStatus.SUCCEEDED,
                output_refs=(artifact.artifact_id,),
            )
        )
        self._transition(runs, RunStatus.SUCCEEDED)
        return OutcomeExecution(
            task=task,
            run_history=tuple(runs),
            step_history=tuple(steps),
            artifact=artifact,
            delivery=delivery,
        )

    def _source_partial(
        self,
        task: TaskSpec,
        runs: list[RunRecord],
        steps: list[StepRecord],
        step_id: str,
        run_id: str,
    ) -> OutcomeExecution:
        evidence_id = "fixture-evidence-available-source"
        evidence = EvidenceRef(
            evidence_id=evidence_id,
            source_snapshot_id="fixture-source-available",
            locator={"type": "fixture", "record": 1},
            quote="一个已登记来源仍然可用，另一必需来源不可达。",
            extraction_method="deterministic-outcome-fixture-v1",
        )
        unmet = "另一个必需来源不可达，完整比较无法完成。"
        artifact = self._artifact(
            step_id,
            {
                "scenario": OutcomeScenario.SOURCE_PARTIAL.value,
                "disposition": DeliveryDisposition.PARTIAL.value,
                "verified_scope": "仅保留已登记且可用来源的验证片段。",
                "unmet_criteria": [unmet],
                "evidence": [
                    {
                        "evidence_id": evidence.evidence_id,
                        "source_snapshot_id": evidence.source_snapshot_id,
                        "locator": evidence.locator,
                        "quote": evidence.quote,
                        "extraction_method": evidence.extraction_method,
                    }
                ],
                "citations": [],
            },
        )
        delivery = DeliveryRecord(
            run_id=run_id,
            disposition=DeliveryDisposition.PARTIAL,
            artifact_refs=(artifact.artifact_id,),
            evidence_refs=(evidence_id,),
            limitations=("未对不可达来源作实现层推断。",),
            unmet_criteria=(unmet,),
            recovery_actions=("提供不可达来源的授权快照后创建新 Run。",),
        )
        steps.append(
            self._step(
                step_id,
                run_id,
                StepStatus.SUCCEEDED,
                output_refs=(artifact.artifact_id,),
            )
        )
        self._transition(runs, RunStatus.PARTIAL)
        return OutcomeExecution(
            task=task,
            run_history=tuple(runs),
            step_history=tuple(steps),
            artifact=artifact,
            delivery=delivery,
        )

    def _failure(
        self,
        task: TaskSpec,
        runs: list[RunRecord],
        steps: list[StepRecord],
        step_id: str,
        run_id: str,
        *,
        code: str,
        category: ErrorCategory,
        user_message: str,
        recovery_action: str,
    ) -> OutcomeExecution:
        artifact = self._artifact(
            step_id,
            {
                "scenario": task.input["scenario"],
                "error_code": code,
                "error_category": category.value,
                "user_message": user_message,
                "recovery_action": recovery_action,
                "delivery_created": False,
                "citations": [],
                "retry_attempts": 0,
                "fallback_used": False,
            },
        )
        error = ErrorRecord(
            code=code,
            category=category,
            stage="fixed_outcome_validation",
            retryable=False,
            user_message=user_message,
            technical_detail_ref=artifact.artifact_id,
            recovery_action=recovery_action,
        )
        steps.append(
            self._step(
                step_id,
                run_id,
                StepStatus.FAILED,
                error_ref=artifact.artifact_id,
            )
        )
        self._transition(runs, RunStatus.FAILED)
        return OutcomeExecution(
            task=task,
            run_history=tuple(runs),
            step_history=tuple(steps),
            artifact=artifact,
            error=error,
        )

    def _artifact(self, step_id: str, payload: dict[str, object]) -> ArtifactRef:
        return self.artifact_store.put_json(
            {
                "schema_version": OUTCOME_SCHEMA_VERSION,
                "validation_only": True,
                "capability_claim": "none",
                "provider_called": False,
                "network_called": False,
                **payload,
            },
            producer_step_id=step_id,
            schema_version=OUTCOME_SCHEMA_VERSION,
        )

    @staticmethod
    def _step(
        step_id: str,
        run_id: str,
        status: StepStatus,
        *,
        output_refs: tuple[str, ...] = (),
        error_ref: str | None = None,
    ) -> StepRecord:
        return StepRecord(
            step_id=step_id,
            run_id=run_id,
            kind="fixed_outcome_validation",
            attempt=1,
            status=status,
            output_refs=output_refs,
            error_ref=error_ref,
        )

    def _transition(self, runs: list[RunRecord], target: RunStatus) -> None:
        current = runs[-1]
        require_transition(current.status, target)
        runs.append(
            RunRecord(
                run_id=current.run_id,
                task_id=current.task_id,
                status=target,
                workflow_version=current.workflow_version,
                config_snapshot_ref=current.config_snapshot_ref,
                budget=current.budget,
                created_at=current.created_at,
                updated_at=self.clock(),
            )
        )


def _idempotency_key(query: str, scenario: OutcomeScenario) -> str:
    payload = f"{scenario.value}\0{query}".encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex}"


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
