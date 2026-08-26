"""W1 runtime shell exports."""

from conflux_weave.runtime.artifacts import (
    ArtifactIntegrityError,
    LocalArtifactStore,
)
from conflux_weave.runtime.fixed_workflow import (
    DeterministicValidationAdapter,
    FixedValidationWorkflow,
    ValidationAdapter,
    WorkflowExecution,
)
from conflux_weave.runtime.outcome_workflow import (
    FixedOutcomeWorkflow,
    OutcomeExecution,
    OutcomeScenario,
)
from conflux_weave.runtime.sqlite import (
    AgentMessageAppendResult,
    AttemptEffectRecord,
    AttemptRecord,
    ArtifactMetadataConflict,
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
    SQLiteRuntimeRepository,
    StepPolicy,
    StoredErrorRecord,
    SubmissionResult,
    TelemetryDropRecord,
)
from conflux_weave.runtime.telemetry import (
    OpenTelemetryTraceExporter,
    SafeTraceExporter,
    TraceDependencyUnavailable,
    TraceExporter,
    TraceRecord,
)
from conflux_weave.runtime.worker import SQLiteStepWorker
__all__ = [
    "AgentMessageAppendResult",
    "ArtifactIntegrityError",
    "AttemptEffectRecord",
    "AttemptRecord",
    "ArtifactMetadataConflict",
    "BudgetAmount",
    "BudgetEntryRecord",
    "BudgetStatus",
    "BOUNDED_WORKFLOW_VERSION",
    "BoundedPaperStrategyRuntime",
    "DeterministicValidationAdapter",
    "DURABLE_WORKFLOW_VERSION",
    "DurablePaperDiscoveryRuntime",
    "DurableWorkResult",
    "FixedValidationWorkflow",
    "FixedOutcomeWorkflow",
    "IdempotencyConflict",
    "LeaseClaim",
    "LeaseConflict",
    "LocalArtifactStore",
    "MigrationChecksumError",
    "MigrationRecord",
    "OutcomeExecution",
    "OutcomeScenario",
    "PersistenceError",
    "PersistenceInvariantError",
    "RecoveryDecision",
    "RecoveryDecisionRequired",
    "RecordNotFound",
    "RunCursor",
    "RunEventRecord",
    "RunListPage",
    "RunOverviewRecord",
    "SQLiteRuntimeRepository",
    "SQLiteStepWorker",
    "SideEffectClass",
    "StepPolicy",
    "StoredErrorRecord",
    "SubmissionResult",
    "TelemetryDropRecord",
    "OpenTelemetryTraceExporter",
    "SafeTraceExporter",
    "TraceDependencyUnavailable",
    "TraceExporter",
    "TraceRecord",
    "ValidationAdapter",
    "WorkflowExecution",
]


def __getattr__(name: str):
    if name in {"BOUNDED_WORKFLOW_VERSION", "BoundedPaperStrategyRuntime"}:
        from conflux_weave.runtime import bounded_paper_strategy

        return getattr(bounded_paper_strategy, name)
    if name in {
        "DURABLE_WORKFLOW_VERSION",
        "DurablePaperDiscoveryRuntime",
        "DurableWorkResult",
    }:
        from conflux_weave.runtime import durable_paper_discovery

        return getattr(durable_paper_discovery, name)
    raise AttributeError(name)
