import json
from pathlib import Path

from conflux_weave.core import RunStatus
from conflux_weave.harness import MessageType, TaskSubmission
from conflux_weave.harness.fixture_runtime import (
    FIXTURE_REPORT_SCHEMA,
    ResearchFixtureRuntime,
)
from conflux_weave.harness.workspace import LocalWorkspaceAdapter
from conflux_weave.runtime import LocalArtifactStore, SQLiteRuntimeRepository


NOW = "2026-08-26T08:00:00Z"


def build_runtime(tmp_path: Path):
    store = LocalArtifactStore(tmp_path / "artifacts")
    repository = SQLiteRuntimeRepository(
        tmp_path / "db" / "runtime.sqlite3", store, clock=lambda: NOW
    )
    system = tmp_path / "system"
    system.mkdir()
    workspace = LocalWorkspaceAdapter(tmp_path / "workspace", system, store)
    ids = {"task": 0, "run": 0}

    def id_factory(prefix: str) -> str:
        ids[prefix] += 1
        return f"{prefix}-{ids[prefix]}"

    runtime = ResearchFixtureRuntime(
        repository,
        store,
        workspace,
        clock=lambda: NOW,
        id_factory=id_factory,
    )
    return repository, store, workspace, runtime


def test_fixture_closes_context_tool_message_budget_and_delivery(tmp_path: Path) -> None:
    repository, store, _, runtime = build_runtime(tmp_path)
    accepted = runtime.submit(
        TaskSubmission(
            "research_fixture",
            {"objective": "验证研究 Harness"},
            requested_agent="research_fixture@v1",
        )
    )

    assert runtime.work_once(now=NOW) == "partial"

    run = repository.get_run(accepted.run_id)
    messages = repository.get_agent_messages(accepted.run_id)
    delivery = repository.get_delivery(accepted.run_id)
    report = repository.get_delivery_artifacts(accepted.run_id)[0]
    payload = json.loads(store.read_bytes(report))
    budget = repository.get_budget_status(accepted.run_id)

    assert run.status is RunStatus.PARTIAL
    assert {message.message_type for message in messages} == {
        MessageType.TASK_ASSIGNED,
        MessageType.STATUS_UPDATE,
        MessageType.RESULT_SUBMITTED,
    }
    assert len(messages) == 5
    assert [message.message_type for message in messages] == [
        MessageType.TASK_ASSIGNED,
        MessageType.STATUS_UPDATE,
        MessageType.STATUS_UPDATE,
        MessageType.STATUS_UPDATE,
        MessageType.RESULT_SUBMITTED,
    ]
    assert delivery.artifact_refs == (report.artifact_id,)
    assert report.schema_version == FIXTURE_REPORT_SCHEMA
    assert payload["network_calls"] == payload["provider_calls"] == 0
    assert payload["input_tokens"] == payload["output_tokens"] == 0
    assert payload["tool_calls"] == budget.actual.tool_calls == 1
    assert budget.actual.input_tokens == budget.actual.output_tokens == 0
    assert (
        tmp_path
        / "workspace"
        / "runs"
        / accepted.run_id
        / "artifacts"
        / "fixture-result.json"
    ).is_file()


def test_fixture_submission_is_idempotent_and_terminal_restart_does_no_work(
    tmp_path: Path,
) -> None:
    repository, store, workspace, runtime = build_runtime(tmp_path)
    submission = TaskSubmission(
        "research_fixture",
        {"objective": "idempotent fixture"},
        idempotency_key="fixture-idempotency",
    )
    first = runtime.submit(submission)
    duplicate = runtime.submit(submission)
    runtime.work_once(now=NOW)
    reopened = SQLiteRuntimeRepository(repository.database_path, store, clock=lambda: NOW)
    restarted = ResearchFixtureRuntime(
        reopened, store, workspace, clock=lambda: NOW
    )

    assert first.created is True
    assert duplicate.created is False
    assert duplicate.run_id == first.run_id
    assert restarted.work_once(now=NOW) is None
    assert len(reopened.get_agent_messages(first.run_id)) == 5
    assert reopened.get_budget_status(first.run_id).actual.tool_calls == 1
