"""Data models and specifications for DAG task orchestration and Agent events (P5.4)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class DAGTaskStatus(StrEnum):
    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


class DAGCycleError(ValueError):
    """Raised when a dependency cycle is detected in a DAG plan."""


class DAGDependencyError(ValueError):
    """Raised when a task node depends on a non-existent node."""


@dataclass(frozen=True, slots=True)
class DAGTaskNode:
    node_id: str
    agent_type: str
    objective: str
    depends_on: tuple[str, ...] = ()
    skill_id: str | None = None
    input_payload: dict[str, Any] = field(default_factory=dict)
    budget: dict[str, Any] = field(default_factory=dict)
    status: DAGTaskStatus = DAGTaskStatus.PENDING
    result: dict[str, Any] | None = None
    error: str | None = None
    started_at: str | None = None
    completed_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DAGTaskNode:
        return cls(
            node_id=str(data["node_id"]),
            agent_type=str(data.get("agent_type", "research")),
            objective=str(data.get("objective", "")),
            depends_on=tuple(str(d) for d in data.get("depends_on", ())),
            skill_id=data.get("skill_id"),
            input_payload=dict(data.get("input_payload", {})),
            budget=dict(data.get("budget", {})),
            status=DAGTaskStatus(data.get("status", "pending")),
            result=data.get("result"),
            error=data.get("error"),
            started_at=data.get("started_at"),
            completed_at=data.get("completed_at"),
        )


@dataclass(frozen=True, slots=True)
class DAGPlan:
    plan_id: str
    run_id: str
    objective: str
    nodes: tuple[DAGTaskNode, ...] = ()
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "run_id": self.run_id,
            "objective": self.objective,
            "nodes": [n.to_dict() for n in self.nodes],
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DAGPlan:
        return cls(
            plan_id=str(data["plan_id"]),
            run_id=str(data.get("run_id", "")),
            objective=str(data.get("objective", "")),
            nodes=tuple(DAGTaskNode.from_dict(n) for n in data.get("nodes", ())),
            created_at=str(data.get("created_at") or _utc_now()),
        )


@dataclass(frozen=True, slots=True)
class AgentEvent:
    event_id: str
    run_id: str
    agent_id: str
    event_type: str
    payload: dict[str, Any] = field(default_factory=dict)
    causation_event_id: str | None = None
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentEvent:
        return cls(
            event_id=str(data["event_id"]),
            run_id=str(data["run_id"]),
            agent_id=str(data["agent_id"]),
            event_type=str(data["event_type"]),
            payload=dict(data.get("payload", {})),
            causation_event_id=data.get("causation_event_id"),
            created_at=str(data.get("created_at") or _utc_now()),
        )


@dataclass(frozen=True, slots=True)
class DAGExecutionResult:
    plan_id: str
    run_id: str
    status: str
    completed_nodes: tuple[str, ...] = ()
    failed_nodes: tuple[str, ...] = ()
    cancelled_nodes: tuple[str, ...] = ()
    skipped_nodes: tuple[str, ...] = ()
    node_results: dict[str, Any] = field(default_factory=dict)
    events_count: int = 0
    duration_seconds: float = 0.0
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
