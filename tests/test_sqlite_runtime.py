from concurrent.futures import ThreadPoolExecutor
import sqlite3

import pytest

from conflux_weave.core import (
    BudgetLedger,
    DeliveryDisposition,
    DeliveryRecord,
    RunRecord,
    RunStatus,
    StepRecord,
    StepStatus,
    TaskSpec,
)
from conflux_weave.evidence import ArtifactRef
from conflux_weave.runtime import (
    ArtifactIntegrityError,
    ArtifactMetadataConflict,
    IdempotencyConflict,
    LocalArtifactStore,
    MigrationChecksumError,
    PersistenceInvariantError,
    RecordNotFound,
    SQLiteRuntimeRepository,
)


NOW = "2026-08-24T12:00:00Z"


def build_submission(suffix: str, *, key: str = "task-key", query: str = "query"):
    task = TaskSpec(
        task_id=f"task-{suffix}",
        kind="paper_discovery",
        input={"query": query, "search_query": "durable runtime"},
        requested_policy="paper-discovery-fixed-v1",
        idempotency_key=key,
    )
    budget = BudgetLedger(180, 20_000, 2_048, "unavailable", 2, 1, 1)
    run = RunRecord(
        run_id=f"run-{suffix}",
        task_id=task.task_id,
        status=RunStatus.ACCEPTED,
        workflow_version="paper-discovery-fixed-v1",
        config_snapshot_ref="artifact-config",
        budget=budget,
        created_at=NOW,
        updated_at=NOW,
    )
    steps = (
        StepRecord(
            step_id=f"step-{suffix}-search",
            run_id=run.run_id,
            kind="search_arxiv",
            attempt=1,
            status=StepStatus.PENDING,
        ),
        StepRecord(
            step_id=f"step-{suffix}-publish",
            run_id=run.run_id,
            kind="publish_delivery",
            attempt=1,
            status=StepStatus.PENDING,
        ),
    )
    return task, run, steps


def build_repository(tmp_path):
    store = LocalArtifactStore(tmp_path / "artifacts")
    repository = SQLiteRuntimeRepository(
        tmp_path / "db" / "conflux-weave.sqlite3",
        store,
        clock=lambda: NOW,
    )
    return repository, store


def advance_to_running(repository: SQLiteRuntimeRepository, run_id: str) -> None:
    repository.transition_run(run_id, RunStatus.QUEUED, updated_at=NOW)
    repository.transition_run(run_id, RunStatus.RUNNING, updated_at=NOW)


def put_artifact(store, content: bytes, producer_step_id: str) -> ArtifactRef:
    return store.put_bytes(
        content,
        media_type="application/json",
        producer_step_id=producer_step_id,
        schema_version="fixture.v1",
    )


def test_migration_is_versioned_idempotent_and_checksum_guarded(tmp_path) -> None:
    repository, store = build_repository(tmp_path)

    records = repository.migration_records()
    assert len(records) == 6
    assert records[0].version == 1
    assert records[0].name == "w3_runtime_authority"
    assert records[0].checksum.startswith("sha256:")
    assert records[1].version == 2
    assert records[1].name == "w3_worker_leases"
    assert records[2].version == 3
    assert records[2].name == "w3_workflow_checkpoints"
    assert records[3].version == 4
    assert records[3].name == "w3_budget_diagnostics"
    assert records[4].version == 5
    assert records[4].name == "w3_trace_diagnostics"
    assert records[5].version == 6
    assert records[5].name == "v03_agent_messages"

    reopened = SQLiteRuntimeRepository(repository.database_path, store)
    assert reopened.migration_records() == records
    with sqlite3.connect(repository.database_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 6
        connection.execute(
            "UPDATE schema_migrations SET checksum = 'sha256:tampered' WHERE version = 1"
        )

    with pytest.raises(MigrationChecksumError, match="checksum mismatch"):
        SQLiteRuntimeRepository(repository.database_path, store)


def test_submission_is_idempotent_and_rejects_payload_collision(tmp_path) -> None:
    repository, _ = build_repository(tmp_path)
    first = build_submission("first")
    duplicate = build_submission("duplicate")

    created = repository.submit_task(*first)
    reused = repository.submit_task(*duplicate)

    assert created.created is True
    assert reused.created is False
    assert (reused.task_id, reused.run_id) == (created.task_id, created.run_id)
    assert repository.get_task_by_idempotency_key("task-key") == first[0]
    assert [step.kind for step in repository.get_steps(created.run_id)] == [
        "search_arxiv",
        "publish_delivery",
    ]

    conflicting = build_submission("conflict", query="different query")
    with pytest.raises(IdempotencyConflict, match="different task input"):
        repository.submit_task(*conflicting)


def test_concurrent_duplicate_submission_creates_one_logical_task(tmp_path) -> None:
    repository, _ = build_repository(tmp_path)
    submissions = [build_submission(str(index)) for index in range(8)]

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda item: repository.submit_task(*item), submissions))

    assert sum(result.created for result in results) == 1
    assert len({result.task_id for result in results}) == 1
    assert len({result.run_id for result in results}) == 1
    with sqlite3.connect(repository.database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM steps").fetchone()[0] == 2


def test_successful_terminal_state_requires_delivery_publication(tmp_path) -> None:
    repository, _ = build_repository(tmp_path)
    result = repository.submit_task(*build_submission("terminal"))
    advance_to_running(repository, result.run_id)

    with pytest.raises(PersistenceInvariantError, match="Delivery publication"):
        repository.transition_run(result.run_id, RunStatus.SUCCEEDED)

    assert repository.get_run(result.run_id).status is RunStatus.RUNNING


def test_delivery_artifacts_and_partial_terminal_state_publish_atomically(tmp_path) -> None:
    repository, store = build_repository(tmp_path)
    result = repository.submit_task(*build_submission("publish"))
    advance_to_running(repository, result.run_id)
    report = put_artifact(store, b'{"report":true}\n', "step-publish-report")
    manifest = put_artifact(store, b'{"manifest":true}\n', "step-publish-manifest")
    delivery = DeliveryRecord(
        run_id=result.run_id,
        disposition=DeliveryDisposition.PARTIAL,
        artifact_refs=(report.artifact_id, manifest.artifact_id),
        evidence_refs=("evidence-1",),
        limitations=("abstract only",),
        unmet_criteria=("full text not verified",),
        recovery_actions=("authorize full-text verification in a new Run",),
    )

    final_run = repository.publish_delivery(
        result.run_id,
        RunStatus.PARTIAL,
        delivery,
        (report, manifest),
        published_at=NOW,
    )

    assert final_run.status is RunStatus.PARTIAL
    assert repository.get_delivery(result.run_id) == delivery
    assert repository.get_delivery_artifacts(result.run_id) == (report, manifest)
    reopened = SQLiteRuntimeRepository(repository.database_path, store)
    assert reopened.get_run(result.run_id).status is RunStatus.PARTIAL
    assert reopened.get_delivery(result.run_id) == delivery


def test_no_answer_delivery_maps_to_succeeded_run(tmp_path) -> None:
    repository, store = build_repository(tmp_path)
    result = repository.submit_task(*build_submission("no-answer", key="no-answer"))
    advance_to_running(repository, result.run_id)
    report = put_artifact(store, b'{"answer":null}\n', "step-no-answer")
    delivery = DeliveryRecord(
        run_id=result.run_id,
        disposition=DeliveryDisposition.NO_ANSWER,
        artifact_refs=(report.artifact_id,),
        limitations=("no source met the frozen constraints",),
    )

    final_run = repository.publish_delivery(
        result.run_id, RunStatus.SUCCEEDED, delivery, (report,)
    )

    assert final_run.status is RunStatus.SUCCEEDED
    assert repository.get_delivery(result.run_id).disposition is DeliveryDisposition.NO_ANSWER


def test_missing_artifact_cannot_create_delivery_or_terminal_state(tmp_path) -> None:
    repository, store = build_repository(tmp_path)
    result = repository.submit_task(*build_submission("missing", key="missing"))
    advance_to_running(repository, result.run_id)
    digest = "0" * 64
    missing = ArtifactRef(
        artifact_id=f"artifact-sha256-{digest}",
        media_type="application/json",
        content_hash=f"sha256:{digest}",
        storage_uri=store.path_for_digest(digest).resolve().as_uri(),
        producer_step_id="step-missing",
        schema_version="fixture.v1",
    )
    delivery = DeliveryRecord(
        run_id=result.run_id,
        disposition=DeliveryDisposition.COMPLETE,
        artifact_refs=(missing.artifact_id,),
    )

    with pytest.raises(ArtifactIntegrityError, match="not a file"):
        repository.publish_delivery(
            result.run_id, RunStatus.SUCCEEDED, delivery, (missing,)
        )

    assert repository.get_run(result.run_id).status is RunStatus.RUNNING
    with pytest.raises(RecordNotFound, match="Delivery not found"):
        repository.get_delivery(result.run_id)


def test_artifact_metadata_conflict_rolls_back_entire_publication(tmp_path) -> None:
    repository, store = build_repository(tmp_path)
    result = repository.submit_task(*build_submission("rollback", key="rollback"))
    advance_to_running(repository, result.run_id)
    first = put_artifact(store, b'{"first":true}\n', "step-first")
    second = put_artifact(store, b'{"second":true}\n', "step-second")
    with sqlite3.connect(repository.database_path) as connection:
        connection.execute(
            "INSERT INTO artifacts(artifact_id, content_hash, storage_uri) VALUES (?, ?, ?)",
            (second.artifact_id, second.content_hash, "file:///injected-conflict"),
        )
    delivery = DeliveryRecord(
        run_id=result.run_id,
        disposition=DeliveryDisposition.COMPLETE,
        artifact_refs=(first.artifact_id, second.artifact_id),
    )

    with pytest.raises(ArtifactMetadataConflict, match="conflicting metadata"):
        repository.publish_delivery(
            result.run_id, RunStatus.SUCCEEDED, delivery, (first, second)
        )

    assert repository.get_run(result.run_id).status is RunStatus.RUNNING
    with pytest.raises(RecordNotFound):
        repository.get_delivery(result.run_id)
    with sqlite3.connect(repository.database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM artifacts WHERE artifact_id = ?", (first.artifact_id,)
        ).fetchone()[0] == 0


def test_same_content_keeps_distinct_producer_lineage_across_runs(tmp_path) -> None:
    repository, store = build_repository(tmp_path)
    first_result = repository.submit_task(*build_submission("lineage-a", key="lineage-a"))
    second_result = repository.submit_task(*build_submission("lineage-b", key="lineage-b"))
    advance_to_running(repository, first_result.run_id)
    advance_to_running(repository, second_result.run_id)
    first = put_artifact(store, b"same report\n", "step-lineage-a")
    second = put_artifact(store, b"same report\n", "step-lineage-b")
    assert first.artifact_id == second.artifact_id

    for result, artifact in ((first_result, first), (second_result, second)):
        delivery = DeliveryRecord(
            run_id=result.run_id,
            disposition=DeliveryDisposition.COMPLETE,
            artifact_refs=(artifact.artifact_id,),
        )
        repository.publish_delivery(
            result.run_id, RunStatus.SUCCEEDED, delivery, (artifact,)
        )

    assert repository.get_delivery_artifacts(first_result.run_id)[0] == first
    assert repository.get_delivery_artifacts(second_result.run_id)[0] == second
    with sqlite3.connect(repository.database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM artifact_registrations"
        ).fetchone()[0] == 2
