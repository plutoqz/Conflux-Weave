"""Framework-independent runtime contracts."""

from conflux_weave.core.contracts import (
    BudgetLedger,
    RunRecord,
    RunStatus,
    StepRecord,
    StepStatus,
    TaskSpec,
)
from conflux_weave.core.delivery import (
    DeliveryDisposition,
    DeliveryRecord,
    UserInputKind,
    UserInputRequest,
)
from conflux_weave.core.errors import ErrorCategory, ErrorRecord
from conflux_weave.core.state_machine import (
    InvalidRunTransition,
    allowed_targets,
    can_transition,
    require_transition,
)

__all__ = [
    "BudgetLedger",
    "DeliveryDisposition",
    "DeliveryRecord",
    "ErrorCategory",
    "ErrorRecord",
    "RunRecord",
    "RunStatus",
    "StepRecord",
    "StepStatus",
    "TaskSpec",
    "UserInputKind",
    "UserInputRequest",
    "InvalidRunTransition",
    "allowed_targets",
    "can_transition",
    "require_transition",
]
