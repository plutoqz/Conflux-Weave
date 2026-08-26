"""Compatibility shell assembling the bounded W3 SQLite Repository."""

from conflux_weave.runtime.sqlite_base import _SQLiteRepositoryBase
from conflux_weave.runtime.sqlite_agent_messages import AgentMessageRepositoryMixin
from conflux_weave.runtime.sqlite_budget import BudgetErrorRepositoryMixin
from conflux_weave.runtime.sqlite_contracts import (
    ArtifactMetadataConflict,
    AgentMessageAppendResult,
    AttemptEffectRecord,
    AttemptRecord,
    BudgetAmount,
    BudgetEntryRecord,
    BudgetStatus,
    IdempotencyConflict,
    LeaseClaim,
    LeaseConflict,
    MigrationChecksumError,
    MigrationRecord,
    PersistenceError,
    PersistenceInvariantError,
    RecordNotFound,
    RecoveryDecision,
    RecoveryDecisionRequired,
    RunCursor,
    RunEventRecord,
    RunListPage,
    RunOverviewRecord,
    SideEffectClass,
    StepPolicy,
    StoredErrorRecord,
    SubmissionResult,
    TelemetryDropRecord,
)
from conflux_weave.runtime.sqlite_delivery import DeliveryArtifactRepositoryMixin
from conflux_weave.runtime.sqlite_leases import LeaseRepositoryMixin
from conflux_weave.runtime.sqlite_task_runs import TaskRunRepositoryMixin
from conflux_weave.runtime.sqlite_telemetry import TelemetryRepositoryMixin


class SQLiteRuntimeRepository(
    TaskRunRepositoryMixin,
    LeaseRepositoryMixin,
    BudgetErrorRepositoryMixin,
    DeliveryArtifactRepositoryMixin,
    AgentMessageRepositoryMixin,
    TelemetryRepositoryMixin,
    _SQLiteRepositoryBase,
):
    """SQLite authority assembled from cohesive persistence domains."""


__all__ = [
    "ArtifactMetadataConflict",
    "AgentMessageAppendResult",
    "AttemptEffectRecord",
    "AttemptRecord",
    "BudgetAmount",
    "BudgetEntryRecord",
    "BudgetStatus",
    "IdempotencyConflict",
    "LeaseClaim",
    "LeaseConflict",
    "MigrationChecksumError",
    "MigrationRecord",
    "PersistenceError",
    "PersistenceInvariantError",
    "RecordNotFound",
    "RecoveryDecision",
    "RecoveryDecisionRequired",
    "RunCursor",
    "RunEventRecord",
    "RunListPage",
    "RunOverviewRecord",
    "SideEffectClass",
    "SQLiteRuntimeRepository",
    "StepPolicy",
    "StoredErrorRecord",
    "SubmissionResult",
    "TelemetryDropRecord",
]
