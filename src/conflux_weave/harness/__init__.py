"""Public v0.3 Harness contracts and local adapters."""

from conflux_weave.harness.contracts import (
    AgentProfile,
    AgentResult,
    AgentResultStatus,
    AgentTask,
    ContextBundle,
    HARNESS_SCHEMA_VERSION,
    MessageEnvelope,
    MessageType,
    RouteDecision,
    TaskSubmission,
    ToolResult,
    ToolResultStatus,
    ToolSideEffect,
    ToolSpec,
    WorkspaceKind,
    WorkspaceRef,
    contract_to_dict,
    contract_to_json,
)
__all__ = [
    "AgentProfile",
    "AgentResult",
    "AgentResultStatus",
    "AgentTask",
    "ContextBundle",
    "HARNESS_SCHEMA_VERSION",
    "LocalWorkspaceAdapter",
    "MessageEnvelope",
    "MessageType",
    "RouteDecision",
    "TaskSubmission",
    "CompositeOrchestrator",
    "AgentExecutorPort",
    "DeterministicRouter",
    "LegacyPaperRuntimeAdapter",
    "OrchestratorPort",
    "TaskRuntimePort",
    "UnavailableTaskRuntime",
    "ToolResult",
    "ToolResultStatus",
    "ToolSideEffect",
    "ToolSpec",
    "WorkspaceAccess",
    "WorkspaceAccessDenied",
    "WorkspaceConflict",
    "WorkspaceError",
    "WorkspaceKind",
    "WorkspaceNotFound",
    "WorkspaceRef",
    "contract_to_dict",
    "contract_to_json",
]


def __getattr__(name: str):
    if name in {
        "AgentExecutorPort",
        "CompositeOrchestrator",
        "DeterministicRouter",
        "LegacyPaperRuntimeAdapter",
        "OrchestratorPort",
        "TaskRuntimePort",
        "UnavailableTaskRuntime",
    }:
        from conflux_weave.harness import orchestration

        return getattr(orchestration, name)
    if name in {
        "LocalWorkspaceAdapter",
        "WorkspaceAccess",
        "WorkspaceAccessDenied",
        "WorkspaceConflict",
        "WorkspaceError",
        "WorkspaceNotFound",
    }:
        from conflux_weave.harness import workspace

        return getattr(workspace, name)
    raise AttributeError(name)
