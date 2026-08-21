"""User-visible delivery and interaction contracts."""

from dataclasses import dataclass, field
from enum import StrEnum


class DeliveryDisposition(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    NO_ANSWER = "no_answer"


class UserInputKind(StrEnum):
    CLARIFICATION = "clarification"
    APPROVAL = "approval"


@dataclass(frozen=True, slots=True)
class UserInputRequest:
    request_id: str
    run_id: str
    step_id: str
    kind: UserInputKind
    reason_code: str
    prompt: str
    requested_inputs: tuple[str, ...]
    created_at: str
    expires_at: str | None = None

    def __post_init__(self) -> None:
        if not self.prompt.strip():
            raise ValueError("UserInputRequest.prompt must not be empty")
        if not self.requested_inputs:
            raise ValueError("UserInputRequest.requested_inputs must not be empty")


@dataclass(frozen=True, slots=True)
class DeliveryRecord:
    run_id: str
    disposition: DeliveryDisposition
    artifact_refs: tuple[str, ...]
    evidence_refs: tuple[str, ...] = field(default_factory=tuple)
    limitations: tuple[str, ...] = field(default_factory=tuple)
    unmet_criteria: tuple[str, ...] = field(default_factory=tuple)
    recovery_actions: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not self.artifact_refs:
            raise ValueError("a delivery requires at least one committed Artifact")
        if self.disposition is DeliveryDisposition.COMPLETE and self.unmet_criteria:
            raise ValueError("a complete delivery cannot have unmet criteria")
        if self.disposition is DeliveryDisposition.PARTIAL and not self.unmet_criteria:
            raise ValueError("a partial delivery must identify unmet criteria")
        if self.disposition is DeliveryDisposition.NO_ANSWER and not self.limitations:
            raise ValueError("a no-answer delivery must explain its evidence boundary")
