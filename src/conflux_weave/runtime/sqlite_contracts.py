"""Stable contracts shared by SQLite persistence domains."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from conflux_weave.core import ErrorRecord, RunRecord
from conflux_weave.harness.contracts import MessageEnvelope


class PersistenceError(RuntimeError):
    """Base error for stable runtime persistence failures."""


class MigrationChecksumError(PersistenceError):
    """Raised when an applied migration no longer matches its source."""


class IdempotencyConflict(PersistenceError):
    """Raised when one idempotency key is reused for different task input."""


class PersistenceInvariantError(PersistenceError):
    """Raised when a write would violate a runtime persistence invariant."""


class ArtifactMetadataConflict(PersistenceInvariantError):
    """Raised when one content address is registered with conflicting storage."""


class RecordNotFound(PersistenceError):
    """Raised when a requested authoritative record does not exist."""


class LeaseConflict(PersistenceInvariantError):
    """Raised when an Attempt no longer owns the active Step lease."""


class RecoveryDecisionRequired(PersistenceInvariantError):
    """Raised when recovery requires an explicit decision about unknown effects."""


@dataclass(frozen=True, slots=True)
class BudgetAmount:
    input_tokens: int = 0
    output_tokens: int = 0
    tool_calls: int = 0
    retrieval_rounds: int = 0


@dataclass(frozen=True, slots=True)
class BudgetStatus:
    run_id: str
    state: str
    limit: BudgetAmount
    actual: BudgetAmount
    reserved: BudgetAmount
    wall_clock_seconds: int
    concurrency: int
    estimated_cost_limit: str
    cost_enforcement: str


@dataclass(frozen=True, slots=True)
class BudgetEntryRecord:
    entry_id: int
    run_id: str
    step_id: str
    attempt_id: str
    reservation_id: str
    entry_kind: str
    amount: BudgetAmount
    source: str
    created_at: str


@dataclass(frozen=True, slots=True)
class StoredErrorRecord:
    error_id: str
    run_id: str
    step_id: str | None
    attempt_id: str | None
    record: ErrorRecord
    created_at: str


@dataclass(frozen=True, slots=True)
class TelemetryDropRecord:
    drop_id: int
    run_id: str
    step_id: str | None
    attempt_id: str | None
    span_name: str
    reason: str
    created_at: str


@dataclass(frozen=True, slots=True)
class RunCursor:
    created_at: str
    run_id: str


@dataclass(frozen=True, slots=True)
class RunOverviewRecord:
    run: RunRecord
    task_kind: str
    task_input: dict[str, Any]


@dataclass(frozen=True, slots=True)
class RunListPage:
    items: tuple[RunOverviewRecord, ...]
    next_cursor: RunCursor | None


@dataclass(frozen=True, slots=True)
class RunEventRecord:
    event_id: int
    run_id: str
    step_id: str | None
    attempt_id: str | None
    event_type: str
    detail: dict[str, Any]
    created_at: str


class SideEffectClass(StrEnum):
    NONE = "none"
    REPLAYABLE_EXTERNAL_READ = "replayable_external_read"
    PAID_EXTERNAL_UNKNOWN = "paid_external_unknown"
    IDEMPOTENT_LOCAL_WRITE = "idempotent_local_write"


class RecoveryDecision(StrEnum):
    RETRY_UNKNOWN_EXTERNAL = "retry_unknown_external"
    FAIL_UNKNOWN_EXTERNAL = "fail_unknown_external"


@dataclass(frozen=True, slots=True)
class StepPolicy:
    side_effect: SideEffectClass
    recovery_rule: str


@dataclass(frozen=True, slots=True)
class SubmissionResult:
    task_id: str
    run_id: str
    created: bool


@dataclass(frozen=True, slots=True)
class AgentMessageAppendResult:
    message: MessageEnvelope
    created: bool


@dataclass(frozen=True, slots=True)
class MigrationRecord:
    version: int
    name: str
    checksum: str
    applied_at: str


@dataclass(frozen=True, slots=True)
class LeaseClaim:
    attempt_id: str
    step_id: str
    run_id: str
    worker_id: str
    attempt_number: int
    fencing_token: int
    acquired_at: str
    heartbeat_at: str
    expires_at: str


@dataclass(frozen=True, slots=True)
class AttemptRecord:
    attempt_id: str
    step_id: str
    worker_id: str
    attempt_number: int
    fencing_token: int
    status: str
    started_at: str
    finished_at: str | None
    error_ref: str | None


@dataclass(frozen=True, slots=True)
class AttemptEffectRecord:
    attempt_id: str
    side_effect: SideEffectClass
    effect_state: str
    intent_artifact_ref: str | None
    request_artifact_ref: str | None
    response_artifact_ref: str | None
    external_response_id: str | None
    updated_at: str


_BUDGET_DIMENSIONS = (
    "input_tokens",
    "output_tokens",
    "tool_calls",
    "retrieval_rounds",
)
