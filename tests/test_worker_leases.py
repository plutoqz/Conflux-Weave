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
from conflux_weave.runtime import (
    LeaseConflict,
    LocalArtifactStore,
    RecordNotFound,
    SQLiteRuntimeRepository,
    SQLiteStepWorker,
)


T0 = "2026-08-24T12:00:00Z"
T5 = "2026-08-24T12:00:05Z"
T10 = "2026-08-24T12:00:10Z"
T15 = "2026-08-24T12:00:15Z"
T16 = "2026-08-24T12:00:16Z"


def build_repository(tmp_path):
    store = LocalArtifactStore(tmp_path / "artifacts")
    repository = SQLiteRuntimeRepository(
        tmp_path / "db" / "conflux-weave.sqlite3",
        store,
        clock=lambda: T0,
    )
    submit_run(repository)
    return repository, store


def submit_run(repository: SQLiteRuntimeRepository, suffix: str = "worker") -> None:
    task = TaskSpec(
        task_id=f"task-{suffix}",
        kind="paper_discovery",
        input={"query": "lease recovery"},
        requested_policy="paper-discovery-fixed-v1",
        idempotency_key=f"{suffix}-key",
    )
    run = RunRecord(
        run_id=f"run-{suffix}",
        task_id=task.task_id,
        status=RunStatus.ACCEPTED,
        workflow_version="paper-discovery-fixed-v1",
        config_snapshot_ref="artifact-config",
        budget=BudgetLedger(180, 20_000, 2_048, "unavailable", 2, 1, 1),
        created_at=T0,
        updated_at=T0,
    )
    steps = (
        StepRecord(
            step_id=f"step-{suffix}-search" if suffix != "worker" else "step-search",
            run_id=run.run_id,
            kind="search_arxiv",
            attempt=1,
            status=StepStatus.PENDING,
        ),
        StepRecord(
            step_id=f"step-{suffix}-rank" if suffix != "worker" else "step-rank",
            run_id=run.run_id,
            kind="rank_candidates",
            attempt=1,
            status=StepStatus.PENDING,
        ),
    )
    repository.submit_task(task, run, steps)
    repository.transition_run(run.run_id, RunStatus.QUEUED, updated_at=T0)


def test_worker_claim_is_ordered_and_persisted(tmp_path) -> None:
    repository, store = build_repository(tmp_path)
    worker = SQLiteStepWorker(repository, "worker-a", lease_seconds=10)

    claim = worker.claim_next(now=T0)

    assert claim is not None
    assert claim.step_id == "step-search"
    assert claim.attempt_number == 1
    assert claim.fencing_token == 1
    assert claim.expires_at == T10
    assert repository.get_run("run-worker").status is RunStatus.RUNNING
    assert worker.claim_next(now=T5) is None
    reopened = SQLiteRuntimeRepository(repository.database_path, store)
    assert reopened.get_attempts("step-search")[0].status == "running"


def test_concurrent_claim_creates_one_attempt_and_lease(tmp_path) -> None:
    repository, _ = build_repository(tmp_path)
    submit_run(repository, "other")
    workers = [
        SQLiteStepWorker(repository, f"worker-{index}", lease_seconds=10)
        for index in range(8)
    ]

    with ThreadPoolExecutor(max_workers=8) as executor:
        claims = list(executor.map(lambda worker: worker.claim_next(now=T0), workers))

    claimed = [claim for claim in claims if claim is not None]
    assert len(claimed) == 1
    with sqlite3.connect(repository.database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM attempts").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM leases").fetchone()[0] == 1


def test_heartbeat_extends_lease_and_expiry_fences_old_attempt(tmp_path) -> None:
    repository, store = build_repository(tmp_path)
    old_worker = SQLiteStepWorker(repository, "worker-old", lease_seconds=10)
    new_worker = SQLiteStepWorker(repository, "worker-new", lease_seconds=10)
    old_claim = old_worker.claim_next(now=T0)
    assert old_claim is not None

    renewed = old_worker.heartbeat(old_claim, now=T5)
    assert renewed.expires_at == T15
    with pytest.raises(LeaseConflict, match="expired, released, or fenced"):
        old_worker.heartbeat(renewed, now=T0)
    assert new_worker.claim_next(now=T10) is None

    new_claim = new_worker.claim_next(now=T15)
    assert new_claim is not None
    assert new_claim.step_id == old_claim.step_id
    assert new_claim.attempt_number == 2
    assert new_claim.fencing_token > old_claim.fencing_token

    stale_artifact = store.put_bytes(
        b"stale worker output\n",
        media_type="application/json",
        producer_step_id=old_claim.step_id,
        schema_version="fixture.v1",
    )
    with pytest.raises(LeaseConflict, match="expired, released, or fenced"):
        old_worker.heartbeat(old_claim, now=T16)
    with pytest.raises(LeaseConflict, match="expired, released, or fenced"):
        old_worker.complete(old_claim, (stale_artifact,), now=T16)

    with sqlite3.connect(repository.database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM attempt_artifacts").fetchone()[0] == 0

    current_artifact = store.put_bytes(
        b"current worker output\n",
        media_type="application/json",
        producer_step_id=new_claim.step_id,
        schema_version="fixture.v1",
    )
    completed = new_worker.complete(new_claim, (current_artifact,), now=T16)
    assert completed.status is StepStatus.SUCCEEDED
    assert completed.output_refs == (current_artifact.artifact_id,)
    assert [attempt.status for attempt in repository.get_attempts("step-search")] == [
        "fenced",
        "succeeded",
    ]
    with sqlite3.connect(repository.database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM attempt_artifacts").fetchone()[0] == 1
        event_types = [
            row[0]
            for row in connection.execute(
                "SELECT event_type FROM run_events ORDER BY event_id"
            )
        ]
    assert event_types == [
        "step_claimed",
        "attempt_fenced",
        "step_claimed",
        "step_succeeded",
    ]

    next_claim = new_worker.claim_next(now=T16)
    assert next_claim is not None
    assert next_claim.step_id == "step-rank"
    assert next_claim.fencing_token > new_claim.fencing_token


def test_worker_identity_cannot_use_another_workers_claim(tmp_path) -> None:
    repository, _ = build_repository(tmp_path)
    owner = SQLiteStepWorker(repository, "worker-owner", lease_seconds=10)
    other = SQLiteStepWorker(repository, "worker-other", lease_seconds=10)
    claim = owner.claim_next(now=T0)
    assert claim is not None

    with pytest.raises(ValueError, match="different Worker"):
        other.heartbeat(claim, now=T5)


def test_lease_managed_delivery_requires_current_fencing_token(tmp_path) -> None:
    repository, store = build_repository(tmp_path)
    old_worker = SQLiteStepWorker(repository, "worker-old", lease_seconds=10)
    new_worker = SQLiteStepWorker(repository, "worker-new", lease_seconds=10)
    old_claim = old_worker.claim_next(now=T0)
    assert old_claim is not None
    new_claim = new_worker.claim_next(now=T10)
    assert new_claim is not None

    stale = store.put_bytes(
        b"stale final report\n",
        media_type="application/json",
        producer_step_id=old_claim.step_id,
        schema_version="fixture.v1",
    )
    stale_delivery = DeliveryRecord(
        run_id=old_claim.run_id,
        disposition=DeliveryDisposition.COMPLETE,
        artifact_refs=(stale.artifact_id,),
    )
    with pytest.raises(LeaseConflict, match="expired, released, or fenced"):
        repository.publish_delivery(
            old_claim.run_id,
            RunStatus.SUCCEEDED,
            stale_delivery,
            (stale,),
            claim=old_claim,
            published_at=T16,
        )
    with pytest.raises(LeaseConflict, match="requires the current claim"):
        repository.publish_delivery(
            old_claim.run_id,
            RunStatus.SUCCEEDED,
            stale_delivery,
            (stale,),
            published_at=T16,
        )
    with pytest.raises(RecordNotFound, match="Delivery not found"):
        repository.get_delivery(old_claim.run_id)

    current = store.put_bytes(
        b"current final report\n",
        media_type="application/json",
        producer_step_id=new_claim.step_id,
        schema_version="fixture.v1",
    )
    delivery = DeliveryRecord(
        run_id=new_claim.run_id,
        disposition=DeliveryDisposition.COMPLETE,
        artifact_refs=(current.artifact_id,),
    )
    final_run = repository.publish_delivery(
        new_claim.run_id,
        RunStatus.SUCCEEDED,
        delivery,
        (current,),
        claim=new_claim,
        published_at=T16,
    )
    assert final_run.status is RunStatus.SUCCEEDED
    assert repository.get_delivery(new_claim.run_id) == delivery
    reopened = SQLiteRuntimeRepository(repository.database_path, store)
    assert reopened.get_run(new_claim.run_id).status is RunStatus.SUCCEEDED
    assert reopened.get_delivery(new_claim.run_id) == delivery


def test_failed_attempt_releases_lease_and_persists_error_reference(tmp_path) -> None:
    repository, _ = build_repository(tmp_path)
    worker = SQLiteStepWorker(repository, "worker-a", lease_seconds=10)
    claim = worker.claim_next(now=T0)
    assert claim is not None

    step = worker.fail(claim, "artifact-error-detail", now=T5)

    assert step.status is StepStatus.FAILED
    assert step.error_ref == "artifact-error-detail"
    attempt = repository.get_attempts(step.step_id)[0]
    assert attempt.status == "failed"
    assert attempt.finished_at == T5
    assert attempt.error_ref == "artifact-error-detail"
    assert worker.claim_next(now=T16) is None
