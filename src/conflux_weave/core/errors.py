"""Structured failure contract exposed by the runtime core."""

from dataclasses import dataclass, field
from enum import StrEnum


class ErrorCategory(StrEnum):
    INPUT = "input"
    CONFIGURATION = "configuration"
    PROVIDER = "provider"
    NETWORK = "network"
    TOOL = "tool"
    BUDGET = "budget"
    STRATEGY = "strategy"
    EVIDENCE = "evidence"
    PERSISTENCE = "persistence"
    CANCELLATION = "cancellation"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ErrorRecord:
    code: str
    category: ErrorCategory
    stage: str
    retryable: bool
    user_message: str
    technical_detail_ref: str
    affected_artifact_refs: tuple[str, ...] = field(default_factory=tuple)
    recovery_action: str = ""
