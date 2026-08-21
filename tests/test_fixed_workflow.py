import json

import pytest

from conflux_weave.core import RunStatus, StepStatus
from conflux_weave.runtime import FixedValidationWorkflow, LocalArtifactStore


def stable_id(prefix: str) -> str:
    return f"{prefix}-fixed"


def fixed_clock() -> str:
    return "2026-08-21T00:00:00Z"


def test_fixed_workflow_records_successful_lifecycle_without_delivery(tmp_path) -> None:
    store = LocalArtifactStore(tmp_path)
    execution = FixedValidationWorkflow(
        store,
        clock=fixed_clock,
        id_factory=stable_id,
    ).execute("验证固定工作流")

    assert [record.status for record in execution.run_history] == [
        RunStatus.ACCEPTED,
        RunStatus.QUEUED,
        RunStatus.RUNNING,
        RunStatus.SUCCEEDED,
    ]
    assert [record.status for record in execution.step_history] == [
        StepStatus.PENDING,
        StepStatus.RUNNING,
        StepStatus.SUCCEEDED,
    ]
    assert execution.task.kind == "workflow_validation"
    assert execution.task.input["validation_only"] is True
    assert execution.validation_only is True
    assert execution.error is None

    artifact = json.loads(store.read_bytes(execution.artifact))
    assert artifact["capability_claim"] == "none"
    assert artifact["adapter_id"] == "deterministic-validation-v1"
    assert artifact["validation_only"] is True
    assert "未生成研究答案" in artifact["adapter_output"]["message_zh"]
    assert "no source" in artifact["evidence_boundary"]
    assert "delivery" not in artifact


class FailingAdapter:
    adapter_id = "failing-v1"

    def execute(self, query: str) -> dict[str, object]:
        raise RuntimeError("fixture failure")


def test_fixed_workflow_persists_failure_without_retry_or_fallback(tmp_path) -> None:
    store = LocalArtifactStore(tmp_path)
    execution = FixedValidationWorkflow(
        store,
        adapter=FailingAdapter(),
        clock=fixed_clock,
        id_factory=stable_id,
    ).execute("触发失败")

    assert execution.final_run.status is RunStatus.FAILED
    assert execution.final_step.status is StepStatus.FAILED
    assert execution.error is not None
    assert execution.error.code == "deterministic_adapter_failed"
    assert execution.error.retryable is False
    assert execution.final_step.error_ref == execution.artifact.artifact_id

    artifact = json.loads(store.read_bytes(execution.artifact))
    assert artifact["error_type"] == "RuntimeError"
    assert artifact["error_message"] == "fixture failure"
    assert artifact["adapter_id"] == "failing-v1"
    assert artifact["capability_claim"] == "none"


def test_fixed_workflow_rejects_empty_query_before_creating_artifacts(tmp_path) -> None:
    store = LocalArtifactStore(tmp_path)
    workflow = FixedValidationWorkflow(store)

    with pytest.raises(ValueError, match="query must not be empty"):
        workflow.execute("  ")
    assert not tmp_path.exists() or not any(tmp_path.rglob("*"))
