import json

import pytest

from conflux_weave.core import (
    DeliveryDisposition,
    ErrorCategory,
    RunStatus,
    StepStatus,
)
from conflux_weave.runtime import (
    FixedOutcomeWorkflow,
    LocalArtifactStore,
    OutcomeScenario,
)


def stable_id(prefix: str) -> str:
    return f"{prefix}-fixed"


def fixed_clock() -> str:
    return "2026-08-21T00:00:00Z"


def execute(tmp_path, scenario: OutcomeScenario):
    store = LocalArtifactStore(tmp_path / scenario.value)
    result = FixedOutcomeWorkflow(
        store,
        clock=fixed_clock,
        id_factory=stable_id,
    ).execute("验证失败语义", scenario)
    artifact = json.loads(store.read_bytes(result.artifact))
    return result, artifact


def test_missing_input_waits_without_delivery_or_citations(tmp_path) -> None:
    result, artifact = execute(tmp_path, OutcomeScenario.MISSING_INPUT)

    assert result.final_run.status is RunStatus.WAITING_FOR_USER
    assert result.final_step.status is StepStatus.WAITING_FOR_USER
    assert result.delivery is None
    assert result.error is None
    assert result.user_input_request is not None
    assert result.user_input_request.requested_inputs == ("review_document",)
    assert artifact["delivery_created"] is False
    assert artifact["citations"] == []


def test_no_answer_is_successful_delivery_without_fake_citations(tmp_path) -> None:
    result, artifact = execute(tmp_path, OutcomeScenario.NO_ANSWER)

    assert result.final_run.status is RunStatus.SUCCEEDED
    assert result.final_step.status is StepStatus.SUCCEEDED
    assert result.delivery is not None
    assert result.delivery.disposition is DeliveryDisposition.NO_ANSWER
    assert result.delivery.evidence_refs == ()
    assert result.error is None
    assert artifact["claims"] == artifact["evidence"] == artifact["citations"] == []


def test_source_partial_requires_existing_evidence_and_unmet_criteria(tmp_path) -> None:
    result, artifact = execute(tmp_path, OutcomeScenario.SOURCE_PARTIAL)

    assert result.final_run.status is RunStatus.PARTIAL
    assert result.final_step.status is StepStatus.SUCCEEDED
    assert result.delivery is not None
    assert result.delivery.disposition is DeliveryDisposition.PARTIAL
    assert result.delivery.evidence_refs == ("fixture-evidence-available-source",)
    assert result.delivery.unmet_criteria
    assert artifact["evidence"][0]["evidence_id"] == (
        "fixture-evidence-available-source"
    )
    assert artifact["evidence"][0]["source_snapshot_id"] == (
        "fixture-source-available"
    )
    assert artifact["citations"] == []


@pytest.mark.parametrize(
    ("scenario", "code", "category"),
    [
        (OutcomeScenario.SOURCE_FAILURE, "source_unavailable", ErrorCategory.NETWORK),
        (OutcomeScenario.BUDGET_FAILURE, "budget_exhausted", ErrorCategory.BUDGET),
    ],
)
def test_hard_failures_have_no_delivery_retry_or_fallback(
    tmp_path, scenario, code, category
) -> None:
    result, artifact = execute(tmp_path, scenario)

    assert result.final_run.status is RunStatus.FAILED
    assert result.final_step.status is StepStatus.FAILED
    assert result.delivery is None
    assert result.user_input_request is None
    assert result.error is not None
    assert result.error.code == code
    assert result.error.category is category
    assert result.error.retryable is False
    assert artifact["delivery_created"] is False
    assert artifact["retry_attempts"] == 0
    assert artifact["fallback_used"] is False
    assert artifact["citations"] == []


def test_all_outcome_artifacts_explicitly_disable_external_calls(tmp_path) -> None:
    for scenario in OutcomeScenario:
        _, artifact = execute(tmp_path, scenario)
        assert artifact["validation_only"] is True
        assert artifact["capability_claim"] == "none"
        assert artifact["network_called"] is False
        assert artifact["provider_called"] is False
