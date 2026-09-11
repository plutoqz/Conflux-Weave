"""Skill specifications and data models for Conflux-Weave (P5.1)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class SkillCategory(StrEnum):
    RESEARCH = "research"
    GOVERNANCE = "governance"
    WRITING = "writing"
    UTILITY = "utility"


class SkillStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"


@dataclass(frozen=True, slots=True)
class SkillBudget:
    max_tokens: int = 8000
    max_steps: int = 12
    estimated_time_seconds: float = 60.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SkillBudget:
        return cls(
            max_tokens=int(data.get("max_tokens", 8000)),
            max_steps=int(data.get("max_steps", 12)),
            estimated_time_seconds=float(data.get("estimated_time_seconds", 60.0)),
        )


@dataclass(frozen=True, slots=True)
class SkillSpec:
    skill_id: str
    version: str
    name: str
    description: str
    category: SkillCategory
    author: str
    input_schema: dict[str, Any]
    required_tools: tuple[str, ...]
    prompt_template: str
    rules: tuple[str, ...] = ()
    default_budget: SkillBudget = field(default_factory=SkillBudget)
    is_builtin: bool = True
    status: SkillStatus = SkillStatus.ACTIVE
    created_at: str = ""
    updated_at: str = ""
    schema_version: str = "conflux-weave.skill.v1"

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["category"] = self.category.value
        result["status"] = self.status.value
        result["default_budget"] = self.default_budget.to_dict()
        return result


@dataclass(frozen=True, slots=True)
class SkillExecutionRequest:
    skill_id: str
    inputs: dict[str, Any]
    conversation_id: str | None = None
    project_id: str | None = None
    override_budget: SkillBudget | None = None


@dataclass(frozen=True, slots=True)
class SkillExecutionResult:
    skill_id: str
    status: str  # "completed" | "failed" | "needs_input"
    summary: str
    content: str
    structured_data: dict[str, Any] = field(default_factory=dict)
    artifacts: tuple[dict[str, Any], ...] = ()
    elapsed_seconds: float = 0.0
    tokens_consumed: int = 0
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
