import pytest

from conflux_weave.core import (
    DeliveryDisposition,
    DeliveryRecord,
    InvalidRunTransition,
    RunStatus,
    StepStatus,
    UserInputKind,
    UserInputRequest,
    allowed_targets,
    can_transition,
    require_transition,
)


def test_run_state_machine_matches_frozen_design() -> None:
    assert allowed_targets(RunStatus.ACCEPTED) == {RunStatus.QUEUED}
    assert allowed_targets(RunStatus.QUEUED) == {RunStatus.RUNNING}
    assert allowed_targets(RunStatus.RUNNING) == {
        RunStatus.WAITING_FOR_USER,
        RunStatus.CANCELLING,
        RunStatus.SUCCEEDED,
        RunStatus.PARTIAL,
        RunStatus.FAILED,
    }
    assert allowed_targets(RunStatus.WAITING_FOR_USER) == {
        RunStatus.QUEUED,
        RunStatus.CANCELLED,
        RunStatus.EXPIRED,
    }
    assert allowed_targets(RunStatus.CANCELLING) == {
        RunStatus.CANCELLED,
        RunStatus.FAILED,
    }


@pytest.mark.parametrize("terminal", [status for status in RunStatus if status.is_terminal])
def test_terminal_run_cannot_be_reopened(terminal: RunStatus) -> None:
    assert allowed_targets(terminal) == set()
    assert not can_transition(terminal, RunStatus.QUEUED)


def test_invalid_transition_fails_closed() -> None:
    with pytest.raises(InvalidRunTransition, match="accepted -> succeeded"):
        require_transition(RunStatus.ACCEPTED, RunStatus.SUCCEEDED)


def test_no_answer_is_a_delivery_not_a_run_failure() -> None:
    delivery = DeliveryRecord(
        run_id="run-010",
        disposition=DeliveryDisposition.NO_ANSWER,
        artifact_refs=("artifact-no-answer-report",),
        evidence_refs=("evidence-search-manifest",),
        limitations=("no source satisfied every hard constraint",),
    )
    assert RunStatus.SUCCEEDED.is_terminal
    assert delivery.disposition is DeliveryDisposition.NO_ANSWER


def test_partial_delivery_requires_unmet_criteria() -> None:
    with pytest.raises(ValueError, match="must identify unmet criteria"):
        DeliveryRecord(
            run_id="run-012",
            disposition=DeliveryDisposition.PARTIAL,
            artifact_refs=("artifact-partial-report",),
        )


def test_complete_delivery_rejects_unmet_criteria() -> None:
    with pytest.raises(ValueError, match="cannot have unmet criteria"):
        DeliveryRecord(
            run_id="run-invalid",
            disposition=DeliveryDisposition.COMPLETE,
            artifact_refs=("artifact-report",),
            unmet_criteria=("source unavailable",),
        )


def test_user_input_request_requires_actionable_input() -> None:
    request = UserInputRequest(
        request_id="input-011",
        run_id="run-011",
        step_id="step-read-document",
        kind=UserInputKind.CLARIFICATION,
        reason_code="review_document_missing",
        prompt="请提供综述文件、DOI 或 URL。",
        requested_inputs=("review_document",),
        created_at="2026-08-21T00:00:00Z",
    )
    assert request.requested_inputs == ("review_document",)


def test_step_can_explicitly_wait_for_user_input() -> None:
    assert StepStatus.WAITING_FOR_USER.value == "waiting_for_user"
