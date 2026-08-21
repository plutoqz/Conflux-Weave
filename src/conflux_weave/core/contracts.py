"""Draft W0 contracts for Task, Run, Step, and budget state."""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class RunStatus(StrEnum):
    ACCEPTED = "accepted"
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_FOR_USER = "waiting_for_user"
    CANCELLING = "cancelling"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"

    @property
    def is_terminal(self) -> bool:
        return self in {
            self.SUCCEEDED,
            self.PARTIAL,
            self.FAILED,
            self.CANCELLED,
            self.EXPIRED,
        }


class StepStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING_FOR_USER = "waiting_for_user"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"


@dataclass(frozen=True, slots=True)
class TaskSpec:
    task_id: str
    kind: str
    input: dict[str, Any]
    requested_policy: str
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class BudgetLedger:
    wall_clock_seconds: int
    input_tokens: int
    output_tokens: int
    estimated_cost: str
    tool_calls: int
    retrieval_rounds: int
    concurrency: int


@dataclass(frozen=True, slots=True)
class RunRecord:
    run_id: str
    task_id: str
    status: RunStatus
    workflow_version: str
    config_snapshot_ref: str
    budget: BudgetLedger
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class StepRecord:
    step_id: str
    run_id: str
    kind: str
    attempt: int
    status: StepStatus
    input_refs: tuple[str, ...] = field(default_factory=tuple)
    output_refs: tuple[str, ...] = field(default_factory=tuple)
    error_ref: str | None = None
