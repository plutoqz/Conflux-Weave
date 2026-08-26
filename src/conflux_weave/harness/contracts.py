"""Framework-independent contracts for the v0.3 Agent Harness."""

from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
from enum import StrEnum
import json
from typing import Any

from conflux_weave.core import BudgetLedger


HARNESS_SCHEMA_VERSION = "conflux-weave.harness.v1"


class AgentResultStatus(StrEnum):
    COMPLETED = "completed"
    PARTIAL = "partial"
    NEEDS_INPUT = "needs_input"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TERMINATED = "terminated"


class MessageType(StrEnum):
    TASK_ASSIGNED = "task_assigned"
    STATUS_UPDATE = "status_update"
    RESULT_SUBMITTED = "result_submitted"
    NEEDS_INPUT = "needs_input"
    FAILURE_REPORTED = "failure_reported"
    TERMINATED = "terminated"


class ToolResultStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ToolSideEffect(StrEnum):
    NONE = "none"
    REPLAYABLE_EXTERNAL_READ = "replayable_external_read"
    PAID_EXTERNAL_UNKNOWN = "paid_external_unknown"
    IDEMPOTENT_LOCAL_WRITE = "idempotent_local_write"


class WorkspaceKind(StrEnum):
    FILE = "file"
    DIRECTORY = "directory"


@dataclass(frozen=True, slots=True)
class TaskSubmission:
    task_kind: str
    input: dict[str, Any]
    requested_agent: str | None = None
    idempotency_key: str | None = None
    schema_version: str = HARNESS_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require_text(self.task_kind, "task_kind")
        _require_json_object(self.input, "input")
        if self.requested_agent is not None:
            _require_text(self.requested_agent, "requested_agent")
        if self.idempotency_key is not None:
            _require_text(self.idempotency_key, "idempotency_key")
        _require_schema(self.schema_version)


@dataclass(frozen=True, slots=True)
class RouteDecision:
    task_kind: str
    executor_id: str
    reason: str
    schema_version: str = HARNESS_SCHEMA_VERSION

    def __post_init__(self) -> None:
        for name in ("task_kind", "executor_id", "reason"):
            _require_text(getattr(self, name), name)
        _require_schema(self.schema_version)


@dataclass(frozen=True, slots=True)
class WorkspaceRef:
    uri: str
    kind: WorkspaceKind
    revision: str | None
    media_type: str | None
    read_only: bool

    def __post_init__(self) -> None:
        _require_prefixed(self.uri, "weave://", "uri")
        if self.kind is WorkspaceKind.FILE and not self.media_type:
            raise ValueError("file WorkspaceRef requires media_type")


@dataclass(frozen=True, slots=True)
class AgentProfile:
    agent_type: str
    version: str
    description: str
    accepted_task_kinds: tuple[str, ...]
    allowed_tool_ids: tuple[str, ...]
    default_budget: BudgetLedger
    schema_version: str = HARNESS_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require_text(self.agent_type, "agent_type")
        _require_text(self.version, "version")
        _require_text(self.description, "description")
        _require_nonempty_tuple(self.accepted_task_kinds, "accepted_task_kinds")
        _require_unique(self.accepted_task_kinds, "accepted_task_kinds")
        _require_unique(self.allowed_tool_ids, "allowed_tool_ids")
        _require_schema(self.schema_version)


@dataclass(frozen=True, slots=True)
class AgentTask:
    agent_task_id: str
    run_id: str
    step_id: str
    task_kind: str
    objective: str
    completion_criteria: tuple[str, ...]
    context_ref: str
    input_refs: tuple[str, ...]
    allowed_tool_ids: tuple[str, ...]
    budget: BudgetLedger
    idempotency_key: str
    schema_version: str = HARNESS_SCHEMA_VERSION

    def __post_init__(self) -> None:
        for name in (
            "agent_task_id",
            "run_id",
            "step_id",
            "task_kind",
            "objective",
            "context_ref",
            "idempotency_key",
        ):
            _require_text(getattr(self, name), name)
        _require_nonempty_tuple(self.completion_criteria, "completion_criteria")
        _require_unique(self.input_refs, "input_refs")
        _require_unique(self.allowed_tool_ids, "allowed_tool_ids")
        _require_schema(self.schema_version)


@dataclass(frozen=True, slots=True)
class AgentResult:
    agent_task_id: str
    status: AgentResultStatus
    summary: str
    output_refs: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    error_ref: str | None = None
    stop_reason: str | None = None
    schema_version: str = HARNESS_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require_text(self.agent_task_id, "agent_task_id")
        _require_text(self.summary, "summary")
        _require_unique(self.output_refs, "output_refs")
        _require_unique(self.evidence_refs, "evidence_refs")
        if self.status is AgentResultStatus.FAILED and not self.error_ref:
            raise ValueError("failed AgentResult requires error_ref")
        if self.status in {
            AgentResultStatus.NEEDS_INPUT,
            AgentResultStatus.TERMINATED,
        } and not self.stop_reason:
            raise ValueError(f"{self.status.value} AgentResult requires stop_reason")
        _require_schema(self.schema_version)


@dataclass(frozen=True, slots=True)
class ContextBundle:
    context_id: str
    agent_task_id: str
    identity: str
    objective: str
    state_snapshot: dict[str, Any]
    input_refs: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    workspace_refs: tuple[WorkspaceRef, ...]
    available_tool_ids: tuple[str, ...]
    constraints: tuple[str, ...]
    completion_criteria: tuple[str, ...]
    created_at: str
    schema_version: str = HARNESS_SCHEMA_VERSION

    def __post_init__(self) -> None:
        for name in ("context_id", "agent_task_id", "identity", "objective", "created_at"):
            _require_text(getattr(self, name), name)
        _require_json_object(self.state_snapshot, "state_snapshot")
        for name in (
            "input_refs",
            "evidence_refs",
            "available_tool_ids",
            "constraints",
            "completion_criteria",
        ):
            _require_unique(getattr(self, name), name)
        _require_nonempty_tuple(self.completion_criteria, "completion_criteria")
        _require_schema(self.schema_version)


@dataclass(frozen=True, slots=True)
class ToolSpec:
    tool_id: str
    version: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    side_effect_class: ToolSideEffect
    required_permissions: tuple[str, ...]
    timeout_seconds: float
    schema_version: str = HARNESS_SCHEMA_VERSION

    def __post_init__(self) -> None:
        for name in ("tool_id", "version", "description"):
            _require_text(getattr(self, name), name)
        _require_json_object(self.input_schema, "input_schema")
        _require_json_object(self.output_schema, "output_schema")
        _require_unique(self.required_permissions, "required_permissions")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        _require_schema(self.schema_version)


@dataclass(frozen=True, slots=True)
class ToolResult:
    tool_call_id: str
    tool_id: str
    agent_task_id: str
    status: ToolResultStatus
    output_refs: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    error_ref: str | None
    started_at: str
    finished_at: str
    schema_version: str = HARNESS_SCHEMA_VERSION

    def __post_init__(self) -> None:
        for name in (
            "tool_call_id",
            "tool_id",
            "agent_task_id",
            "started_at",
            "finished_at",
        ):
            _require_text(getattr(self, name), name)
        _require_unique(self.output_refs, "output_refs")
        _require_unique(self.evidence_refs, "evidence_refs")
        if self.status is ToolResultStatus.FAILED and not self.error_ref:
            raise ValueError("failed ToolResult requires error_ref")
        _require_schema(self.schema_version)


@dataclass(frozen=True, slots=True)
class MessageEnvelope:
    message_id: str
    run_id: str
    agent_task_id: str
    sender: str
    recipient: str
    message_type: MessageType
    causation_id: str | None
    correlation_id: str
    payload_ref: str
    idempotency_key: str
    created_at: str
    schema_version: str = HARNESS_SCHEMA_VERSION

    def __post_init__(self) -> None:
        for name in (
            "message_id",
            "run_id",
            "agent_task_id",
            "sender",
            "recipient",
            "correlation_id",
            "payload_ref",
            "idempotency_key",
            "created_at",
        ):
            _require_text(getattr(self, name), name)
        _require_schema(self.schema_version)


def contract_to_dict(value: Any) -> dict[str, Any]:
    """Convert a Harness dataclass into a deterministic JSON-compatible object."""
    if not is_dataclass(value) or isinstance(value, type):
        raise TypeError("value must be a Harness dataclass instance")
    converted = _json_value(asdict(value))
    if not isinstance(converted, dict):
        raise TypeError("contract must serialize to an object")
    return converted


def contract_to_json(value: Any) -> str:
    return json.dumps(
        contract_to_dict(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _json_value(value: Any) -> Any:
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    return value


def _require_text(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must not be empty")


def _require_prefixed(value: str, prefix: str, name: str) -> None:
    _require_text(value, name)
    if not value.startswith(prefix):
        raise ValueError(f"{name} must start with {prefix}")


def _require_unique(values: tuple[str, ...], name: str) -> None:
    if any(not isinstance(value, str) or not value.strip() for value in values):
        raise ValueError(f"{name} values must not be empty")
    if len(set(values)) != len(values):
        raise ValueError(f"{name} values must be unique")


def _require_nonempty_tuple(values: tuple[str, ...], name: str) -> None:
    if not values:
        raise ValueError(f"{name} must not be empty")
    _require_unique(values, name)


def _require_json_object(value: dict[str, Any], name: str) -> None:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")
    try:
        json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be JSON-compatible") from exc


def _require_schema(value: str) -> None:
    if value != HARNESS_SCHEMA_VERSION:
        raise ValueError(f"unsupported Harness schema_version: {value}")


__all__ = [
    "AgentProfile",
    "AgentResult",
    "AgentResultStatus",
    "AgentTask",
    "ContextBundle",
    "HARNESS_SCHEMA_VERSION",
    "MessageEnvelope",
    "MessageType",
    "RouteDecision",
    "TaskSubmission",
    "ToolResult",
    "ToolResultStatus",
    "ToolSideEffect",
    "ToolSpec",
    "WorkspaceKind",
    "WorkspaceRef",
    "contract_to_dict",
    "contract_to_json",
]
