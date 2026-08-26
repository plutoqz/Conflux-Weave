import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from conflux_weave.core import BudgetLedger, RunRecord, RunStatus, StepRecord, StepStatus, TaskSpec
from conflux_weave.harness import MessageEnvelope, MessageType
from conflux_weave.runtime import (
    IdempotencyConflict,
    LocalArtifactStore,
    PersistenceInvariantError,
    RecordNotFound,
    SQLiteRuntimeRepository,
)


NOW = "2026-08-26T08:00:00Z"


def repository(tmp_path: Path) -> tuple[SQLiteRuntimeRepository, LocalArtifactStore]:
    store = LocalArtifactStore(tmp_path / "artifacts")
    repo = SQLiteRuntimeRepository(
        tmp_path / "db" / "runtime.sqlite3", store, clock=lambda: NOW
    )
    task = TaskSpec("task-1", "research_fixture", {"query": "fixture"}, "fixture-v1", "task-key")
    run = RunRecord(
        "run-1",
        task.task_id,
        RunStatus.ACCEPTED,
        "fixture-v1",
        "config-ref",
        BudgetLedger(30, 0, 0, "0", 1, 0, 1),
        NOW,
        NOW,
    )
    step = StepRecord("step-1", "run-1", "research_fixture", 1, StepStatus.PENDING)
    repo.submit_task(task, run, (step,))
    return repo, store


def envelope(
    payload_ref: str,
    *,
    message_id: str = "message-1",
    key: str = "message-key",
    message_type: MessageType = MessageType.TASK_ASSIGNED,
    created_at: str = NOW,
) -> MessageEnvelope:
    return MessageEnvelope(
        message_id=message_id,
        run_id="run-1",
        agent_task_id="agent-task-1",
        sender="orchestrator",
        recipient="research_fixture@v1",
        message_type=message_type,
        causation_id=None,
        correlation_id="run-1",
        payload_ref=payload_ref,
        idempotency_key=key,
        created_at=created_at,
    )


def payload(store: LocalArtifactStore, value: bytes = b'{}\n'):
    return store.put_bytes(
        value,
        media_type="application/json",
        producer_step_id="step-1",
        schema_version="fixture-message.v1",
    )


def test_append_is_atomic_and_projects_run_event(tmp_path: Path) -> None:
    repo, store = repository(tmp_path)
    artifact = payload(store)

    appended = repo.append_agent_message(envelope(artifact.artifact_id), artifact)

    assert appended.created is True
    assert repo.get_agent_message("message-1") == appended.message
    assert repo.get_artifact_registrations(artifact.artifact_id) == (artifact,)
    events = repo.get_run_events("run-1")
    assert events[-1].event_type == "agent_task_assigned"
    assert events[-1].detail["payload_ref"] == artifact.artifact_id


def test_duplicate_is_reused_and_content_collision_is_rejected(tmp_path: Path) -> None:
    repo, store = repository(tmp_path)
    first_payload = payload(store)
    first = repo.append_agent_message(envelope(first_payload.artifact_id), first_payload)
    duplicate = repo.append_agent_message(
        envelope(first_payload.artifact_id, message_id="message-retry"), first_payload
    )

    assert first.created is True
    assert duplicate.created is False
    assert duplicate.message.message_id == "message-1"
    assert len(repo.get_agent_messages("run-1")) == 1
    assert len(repo.get_run_events("run-1")) == 1

    other_payload = payload(store, b'{"different":true}\n')
    with pytest.raises(IdempotencyConflict, match="different content"):
        repo.append_agent_message(
            envelope(other_payload.artifact_id, message_id="message-other"),
            other_payload,
        )


def test_recipient_filter_cursor_and_restart_replay(tmp_path: Path) -> None:
    repo, store = repository(tmp_path)
    first_payload = payload(store, b'{"n":1}\n')
    second_payload = payload(store, b'{"n":2}\n')
    first = envelope(first_payload.artifact_id)
    second = envelope(
        second_payload.artifact_id,
        message_id="message-2",
        key="message-key-2",
        message_type=MessageType.STATUS_UPDATE,
        created_at="2026-08-26T08:00:01Z",
    )
    repo.append_agent_message(first, first_payload)
    repo.append_agent_message(second, second_payload)

    reopened = SQLiteRuntimeRepository(repo.database_path, store)

    assert reopened.get_agent_messages(
        "run-1", recipient="research_fixture@v1"
    ) == (first, second)
    assert reopened.get_agent_messages("run-1", after_message_id="message-1") == (
        second,
    )


def test_invalid_run_rolls_back_artifact_registration_and_event(tmp_path: Path) -> None:
    repo, store = repository(tmp_path)
    artifact = payload(store)
    invalid = replace(envelope(artifact.artifact_id), run_id="run-missing")

    with pytest.raises(RecordNotFound, match="Run not found"):
        repo.append_agent_message(invalid, artifact)

    with sqlite3.connect(repo.database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM agent_messages").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM run_events").fetchone()[0] == 0


def test_payload_mismatch_is_rejected_before_write(tmp_path: Path) -> None:
    repo, store = repository(tmp_path)
    artifact = payload(store)

    with pytest.raises(PersistenceInvariantError, match="payload_ref"):
        repo.append_agent_message(envelope("artifact-other"), artifact)


def test_existing_version_five_database_upgrades_without_checksum_changes(tmp_path: Path) -> None:
    repo, store = repository(tmp_path)
    original = repo.migration_records()[:5]
    with sqlite3.connect(repo.database_path) as connection:
        connection.execute("DROP TABLE agent_messages")
        connection.execute("DELETE FROM schema_migrations WHERE version = 6")
        connection.execute("PRAGMA user_version = 5")

    upgraded = SQLiteRuntimeRepository(repo.database_path, store)

    assert upgraded.migration_records()[:5] == original
    assert upgraded.migration_records()[-1].name == "v03_agent_messages"
