"""Deterministic Run state transitions."""

from conflux_weave.core.contracts import RunStatus


_ALLOWED_TRANSITIONS: dict[RunStatus, frozenset[RunStatus]] = {
    RunStatus.ACCEPTED: frozenset({RunStatus.QUEUED}),
    RunStatus.QUEUED: frozenset({RunStatus.RUNNING}),
    RunStatus.RUNNING: frozenset(
        {
            RunStatus.WAITING_FOR_USER,
            RunStatus.CANCELLING,
            RunStatus.SUCCEEDED,
            RunStatus.PARTIAL,
            RunStatus.FAILED,
        }
    ),
    RunStatus.WAITING_FOR_USER: frozenset(
        {RunStatus.QUEUED, RunStatus.CANCELLED, RunStatus.EXPIRED}
    ),
    RunStatus.CANCELLING: frozenset({RunStatus.CANCELLED, RunStatus.FAILED}),
    RunStatus.SUCCEEDED: frozenset(),
    RunStatus.PARTIAL: frozenset(),
    RunStatus.FAILED: frozenset(),
    RunStatus.CANCELLED: frozenset(),
    RunStatus.EXPIRED: frozenset(),
}


class InvalidRunTransition(ValueError):
    def __init__(self, current: RunStatus, target: RunStatus) -> None:
        self.current = current
        self.target = target
        super().__init__(f"invalid Run transition: {current.value} -> {target.value}")


def allowed_targets(current: RunStatus) -> frozenset[RunStatus]:
    return _ALLOWED_TRANSITIONS[current]


def can_transition(current: RunStatus, target: RunStatus) -> bool:
    return target in allowed_targets(current)


def require_transition(current: RunStatus, target: RunStatus) -> None:
    if not can_transition(current, target):
        raise InvalidRunTransition(current, target)
