"""Synchronous deterministic workflow used to validate the W1 runtime shell."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import uuid4

from conflux_weave.core import (
    BudgetLedger,
    ErrorCategory,
    ErrorRecord,
    RunRecord,
    RunStatus,
    StepRecord,
    StepStatus,
    TaskSpec,
    require_transition,
)
from conflux_weave.evidence import ArtifactRef
from conflux_weave.runtime.artifacts import LocalArtifactStore


WORKFLOW_VERSION = "fixed-validation-v1"
RESULT_SCHEMA_VERSION = "conflux-weave.fixed-validation-result.v1"
EVIDENCE_BOUNDARY = (
    "Deterministic workflow validation only; no source, Evidence, Provider, "
    "network, or research answer was produced."
)


class ValidationAdapter(Protocol):
    adapter_id: str

    def execute(self, query: str) -> dict[str, Any]: ...


class DeterministicValidationAdapter:
    adapter_id = "deterministic-validation-v1"

    def execute(self, query: str) -> dict[str, Any]:
        return {
            "adapter_id": self.adapter_id,
            "normalized_query": query,
            "message_zh": "固定工作流离线验证完成；未生成研究答案。",
        }


@dataclass(frozen=True, slots=True)
class WorkflowExecution:
    task: TaskSpec
    run_history: tuple[RunRecord, ...]
    step_history: tuple[StepRecord, ...]
    artifact: ArtifactRef
    error: ErrorRecord | None
    validation_only: bool = True
    evidence_boundary: str = EVIDENCE_BOUNDARY

    @property
    def final_run(self) -> RunRecord:
        return self.run_history[-1]

    @property
    def final_step(self) -> StepRecord:
        return self.step_history[-1]


class FixedValidationWorkflow:
    def __init__(
        self,
        artifact_store: LocalArtifactStore,
        *,
        adapter: ValidationAdapter | None = None,
        clock: Callable[[], str] | None = None,
        id_factory: Callable[[str], str] | None = None,
    ) -> None:
        self.artifact_store = artifact_store
        self.adapter = adapter or DeterministicValidationAdapter()
        self.clock = clock or _utc_now
        self.id_factory = id_factory or _new_id

    def execute(self, query: str) -> WorkflowExecution:
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
            kind="workflow_validation",
            input={"query": normalized_query, "validation_only": True},
            requested_policy=WORKFLOW_VERSION,
            idempotency_key=_query_key(normalized_query),
        )
        run_history = [
            self._run_record(
                run_id=run_id,
                task_id=task_id,
                status=RunStatus.ACCEPTED,
                budget=budget,
                created_at=created_at,
            )
        ]
        self._transition(run_history, RunStatus.QUEUED)
        self._transition(run_history, RunStatus.RUNNING)

        step_history = [
            StepRecord(
                step_id=step_id,
                run_id=run_id,
                kind="deterministic_validation",
                attempt=1,
                status=StepStatus.PENDING,
            ),
            StepRecord(
                step_id=step_id,
                run_id=run_id,
                kind="deterministic_validation",
                attempt=1,
                status=StepStatus.RUNNING,
            ),
        ]

        try:
            adapter_output = self.adapter.execute(normalized_query)
        except Exception as exc:
            artifact = self.artifact_store.put_json(
                {
                    "schema_version": RESULT_SCHEMA_VERSION,
                    "task_id": task_id,
                    "run_id": run_id,
                    "step_id": step_id,
                    "validation_only": True,
                    "capability_claim": "none",
                    "evidence_boundary": EVIDENCE_BOUNDARY,
                    "adapter_id": self.adapter.adapter_id,
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                },
                producer_step_id=step_id,
                schema_version=RESULT_SCHEMA_VERSION,
            )
            error = ErrorRecord(
                code="deterministic_adapter_failed",
                category=ErrorCategory.TOOL,
                stage="deterministic_validation",
                retryable=False,
                user_message="离线验证适配器失败，请查看诊断产物。",
                technical_detail_ref=artifact.artifact_id,
                recovery_action="修复确定性适配器后重新运行验证。",
            )
            step_history.append(
                StepRecord(
                    step_id=step_id,
                    run_id=run_id,
                    kind="deterministic_validation",
                    attempt=1,
                    status=StepStatus.FAILED,
                    error_ref=artifact.artifact_id,
                )
            )
            self._transition(run_history, RunStatus.FAILED)
            return WorkflowExecution(
                task=task,
                run_history=tuple(run_history),
                step_history=tuple(step_history),
                artifact=artifact,
                error=error,
            )

        artifact = self.artifact_store.put_json(
            {
                "schema_version": RESULT_SCHEMA_VERSION,
                "task_id": task_id,
                "run_id": run_id,
                "step_id": step_id,
                "validation_only": True,
                "capability_claim": "none",
                "evidence_boundary": EVIDENCE_BOUNDARY,
                "adapter_id": self.adapter.adapter_id,
                "adapter_output": adapter_output,
            },
            producer_step_id=step_id,
            schema_version=RESULT_SCHEMA_VERSION,
        )
        step_history.append(
            StepRecord(
                step_id=step_id,
                run_id=run_id,
                kind="deterministic_validation",
                attempt=1,
                status=StepStatus.SUCCEEDED,
                output_refs=(artifact.artifact_id,),
            )
        )
        self._transition(run_history, RunStatus.SUCCEEDED)
        return WorkflowExecution(
            task=task,
            run_history=tuple(run_history),
            step_history=tuple(step_history),
            artifact=artifact,
            error=None,
        )

    def _transition(
        self, run_history: list[RunRecord], target: RunStatus
    ) -> None:
        current = run_history[-1]
        require_transition(current.status, target)
        run_history.append(
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

    def _run_record(
        self,
        *,
        run_id: str,
        task_id: str,
        status: RunStatus,
        budget: BudgetLedger,
        created_at: str,
    ) -> RunRecord:
        return RunRecord(
            run_id=run_id,
            task_id=task_id,
            status=status,
            workflow_version=WORKFLOW_VERSION,
            config_snapshot_ref=f"inline:{WORKFLOW_VERSION}",
            budget=budget,
            created_at=created_at,
            updated_at=created_at,
        )


def _query_key(query: str) -> str:
    return "sha256:" + hashlib.sha256(query.encode("utf-8")).hexdigest()


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex}"


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
