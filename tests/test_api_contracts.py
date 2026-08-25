import json
import sqlite3

import pytest
from pydantic import ValidationError

from conflux_weave.api_contracts import (
    ResearchTaskRequest,
    WorkbenchQueryService,
    decode_run_cursor,
    encode_run_cursor,
    map_exception,
)
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
    LocalArtifactStore,
    PersistenceInvariantError,
    RecordNotFound,
    RunCursor,
    SQLiteRuntimeRepository,
)
from conflux_weave.runtime.durable_paper_shared import RANK_CHECKPOINT


NOW = "2026-08-24T12:00:00Z"


def build_repository(tmp_path):
    store = LocalArtifactStore(tmp_path / "artifacts")
    repository = SQLiteRuntimeRepository(
        tmp_path / "db" / "runtime.sqlite3", store, clock=lambda: NOW
    )
    return repository, store


def submit_run(
    repository, suffix: str, created_at: str = NOW, *, second_rank: bool = False
):
    task = TaskSpec(
        task_id=f"task-{suffix}",
        kind="paper_discovery",
        input={"query": f"research question {suffix}", "internal": "not public"},
        requested_policy="fixed-arxiv-v1",
        idempotency_key=f"key-{suffix}",
    )
    run = RunRecord(
        run_id=f"run-{suffix}",
        task_id=task.task_id,
        status=RunStatus.ACCEPTED,
        workflow_version="fixed-arxiv-v1",
        config_snapshot_ref="file:///secret/config.json",
        budget=BudgetLedger(180, 20_000, 2_048, "unavailable", 4, 2, 1),
        created_at=created_at,
        updated_at=created_at,
    )
    steps = [
        StepRecord(
            step_id=f"step-{suffix}-rank",
            run_id=run.run_id,
            kind="rank_candidates",
            attempt=1,
            status=StepStatus.PENDING,
        ),
    ]
    if second_rank:
        steps.append(
            StepRecord(
                step_id=f"step-{suffix}-merge",
                run_id=run.run_id,
                kind="merge_and_rank",
                attempt=1,
                status=StepStatus.PENDING,
            )
        )
    steps.append(
        StepRecord(
            step_id=f"step-{suffix}-publish",
            run_id=run.run_id,
            kind="publish_delivery",
            attempt=1,
            status=StepStatus.PENDING,
        )
    )
    return repository.submit_task(task, run, steps)


def complete_run(repository, store, suffix: str, evidence_payload: object):
    result = submit_run(repository, suffix)
    repository.transition_run(result.run_id, RunStatus.QUEUED, updated_at=NOW)
    claim = repository.claim_next_step("worker", lease_seconds=60, now=NOW)
    assert claim is not None and claim.step_id == f"step-{suffix}-rank"
    checkpoint = store.put_json(
        evidence_payload,
        producer_step_id=claim.step_id,
        schema_version=RANK_CHECKPOINT,
    )
    repository.complete_attempt(claim, (checkpoint,), now=NOW)
    publish_claim = repository.claim_next_step("worker", lease_seconds=60, now=NOW)
    assert publish_claim is not None
    report = store.put_json(
        {"answer": "bounded"},
        producer_step_id=publish_claim.step_id,
        schema_version="conflux-weave.delivery.v1",
    )
    delivery = DeliveryRecord(
        run_id=result.run_id,
        disposition=DeliveryDisposition.COMPLETE,
        artifact_refs=(report.artifact_id,),
        evidence_refs=("evidence-1",),
    )
    repository.publish_delivery(
        result.run_id,
        RunStatus.SUCCEEDED,
        delivery,
        (report,),
        claim=publish_claim,
        published_at=NOW,
    )
    return result.run_id, checkpoint, report


def evidence_checkpoint(quote: str = "quoted source") -> dict:
    return {
        "schema_version": RANK_CHECKPOINT,
        "evidence": [
            {
                "evidence_id": "evidence-1",
                "source_snapshot_id": "source-1",
                "locator": {"section": "abstract"},
                "quote": quote,
                "extraction_method": "structured-fixture",
            }
        ],
    }


def test_research_request_normalizes_input_and_rejects_extra_or_invalid_fields() -> None:
    request = ResearchTaskRequest(
        query="  durable research runtime  ", topics=[" agent   runtime "]
    )
    assert request.query == "durable research runtime"
    assert request.topics == ("agent runtime",)

    with pytest.raises(ValidationError):
        ResearchTaskRequest(query="question", search_query="arxiv syntax")
    with pytest.raises(ValidationError):
        ResearchTaskRequest(query=" ")
    with pytest.raises(ValidationError):
        ResearchTaskRequest(query="question", topics=["Agent", "agent"])
    with pytest.raises(ValidationError):
        ResearchTaskRequest(query="question", topics=[str(index) for index in range(13)])


def test_run_cursor_round_trip_and_malformed_rejection() -> None:
    cursor = RunCursor(created_at=NOW, run_id="run-1")
    assert decode_run_cursor(encode_run_cursor(cursor)) == cursor
    for malformed in ("", "not-json", "x" * 1_025):
        with pytest.raises(ValueError, match="cursor is invalid"):
            decode_run_cursor(malformed)


def test_run_pagination_is_stable_and_has_no_duplicates(tmp_path) -> None:
    repository, _ = build_repository(tmp_path)
    for suffix in ("a", "c", "b"):
        submit_run(repository, suffix)
    service = WorkbenchQueryService(repository)

    first = service.list_runs(limit=2)
    second = service.list_runs(cursor=first.next_cursor, limit=2)

    assert [item.run_id for item in first.items] == ["run-c", "run-b"]
    assert [item.run_id for item in second.items] == ["run-a"]
    assert set(item.run_id for item in first.items).isdisjoint(
        item.run_id for item in second.items
    )
    assert second.next_cursor is None


def test_event_cursor_continues_from_persisted_event_id(tmp_path) -> None:
    repository, store = build_repository(tmp_path)
    run_id, _, _ = complete_run(repository, store, "events", evidence_checkpoint())
    service = WorkbenchQueryService(repository)

    first = service.get_events(run_id, limit=2)
    second = service.get_events(run_id, after=first.next_after, limit=10)

    assert first.items
    assert all(item.cursor <= first.next_after for item in first.items)
    assert all(item.cursor > first.next_after for item in second.items)
    assert {item.run_id for item in first.items + second.items} == {run_id}


def test_run_detail_exposes_user_contract_without_internal_configuration(tmp_path) -> None:
    repository, _ = build_repository(tmp_path)
    result = submit_run(repository, "detail")

    payload = WorkbenchQueryService(repository).get_run(result.run_id).model_dump()
    serialized = json.dumps(payload, ensure_ascii=False)

    assert payload["query"] == "research question detail"
    assert payload["state"] == "pending"
    assert "config_snapshot" not in serialized
    assert "file:///secret" not in serialized
    assert "rank_candidates" not in serialized
    assert "publish_delivery" not in serialized
    assert "internal" not in serialized


def test_exception_mapping_is_sanitized() -> None:
    secret = "D:/private/runtime.sqlite3"
    status, response = map_exception(RecordNotFound(secret))
    assert status == 404
    assert secret not in response.model_dump_json()

    status, response = map_exception(RuntimeError(f"provider key at {secret}"))
    assert status == 500
    assert secret not in response.model_dump_json()


def test_delivery_artifact_reads_only_artifacts_published_for_run(tmp_path) -> None:
    repository, store = build_repository(tmp_path)
    run_id, checkpoint, report = complete_run(
        repository, store, "artifact", evidence_checkpoint()
    )
    service = WorkbenchQueryService(repository)

    metadata, content = service.read_delivery_artifact(run_id, report.artifact_id)
    assert metadata.artifact_id == report.artifact_id
    assert json.loads(content) == {"answer": "bounded"}
    with pytest.raises(RecordNotFound):
        service.read_delivery_artifact(run_id, checkpoint.artifact_id)
    with pytest.raises(RecordNotFound):
        service.read_delivery_artifact(run_id, "artifact-unregistered")


def test_evidence_reads_registered_rank_checkpoint_only(tmp_path) -> None:
    repository, store = build_repository(tmp_path)
    run_id, _, _ = complete_run(repository, store, "evidence", evidence_checkpoint())

    evidence = WorkbenchQueryService(repository).get_evidence(run_id, "evidence-1")

    assert evidence.source_snapshot_id == "source-1"
    assert evidence.quote == "quoted source"
    with pytest.raises(RecordNotFound):
        WorkbenchQueryService(repository).get_evidence(run_id, "not-delivered")


@pytest.mark.parametrize(
    "payload",
    [
        {"schema_version": RANK_CHECKPOINT, "evidence": []},
        [evidence_checkpoint()],
        {"schema_version": "wrong", "evidence": []},
    ],
)
def test_evidence_fails_closed_for_missing_or_invalid_registered_definition(
    tmp_path, payload
) -> None:
    repository, store = build_repository(tmp_path)
    run_id, _, _ = complete_run(repository, store, "invalid", payload)

    with pytest.raises(PersistenceInvariantError):
        WorkbenchQueryService(repository).get_evidence(run_id, "evidence-1")


def test_corrupt_registered_evidence_checkpoint_fails_closed(tmp_path) -> None:
    repository, store = build_repository(tmp_path)
    run_id, checkpoint, _ = complete_run(
        repository, store, "corrupt", evidence_checkpoint()
    )
    checkpoint_path = store.path_for_digest(checkpoint.content_hash.removeprefix("sha256:"))
    checkpoint_path.write_bytes(b"corrupt")

    with pytest.raises(PersistenceInvariantError):
        WorkbenchQueryService(repository).get_evidence(run_id, "evidence-1")


def test_conflicting_registered_evidence_definitions_fail_closed(tmp_path) -> None:
    repository, store = build_repository(tmp_path)
    result = submit_run(repository, "conflict", second_rank=True)
    repository.transition_run(result.run_id, RunStatus.QUEUED, updated_at=NOW)
    for quote in ("first definition", "conflicting definition"):
        claim = repository.claim_next_step("worker", lease_seconds=60, now=NOW)
        assert claim is not None
        checkpoint = store.put_json(
            evidence_checkpoint(quote),
            producer_step_id=claim.step_id,
            schema_version=RANK_CHECKPOINT,
        )
        repository.complete_attempt(claim, (checkpoint,), now=NOW)
    publish_claim = repository.claim_next_step("worker", lease_seconds=60, now=NOW)
    assert publish_claim is not None
    report = store.put_json(
        {"answer": "bounded"},
        producer_step_id=publish_claim.step_id,
        schema_version="conflux-weave.delivery.v1",
    )
    repository.publish_delivery(
        result.run_id,
        RunStatus.SUCCEEDED,
        DeliveryRecord(
            run_id=result.run_id,
            disposition=DeliveryDisposition.COMPLETE,
            artifact_refs=(report.artifact_id,),
            evidence_refs=("evidence-1",),
        ),
        (report,),
        claim=publish_claim,
        published_at=NOW,
    )

    with pytest.raises(PersistenceInvariantError, match="conflicting"):
        WorkbenchQueryService(repository).get_evidence(result.run_id, "evidence-1")


def test_event_reader_rejects_non_object_persisted_detail(tmp_path) -> None:
    repository, _ = build_repository(tmp_path)
    result = submit_run(repository, "bad-event")
    with sqlite3.connect(repository.database_path) as connection:
        connection.execute(
            """
            INSERT INTO run_events(run_id, event_type, detail_json, created_at)
            VALUES (?, 'fixture', '[]', ?)
            """,
            (result.run_id, NOW),
        )

    with pytest.raises(PersistenceInvariantError, match="detail must be an object"):
        repository.get_run_events(result.run_id)


def test_readiness_is_local_and_does_not_expose_paths_or_credentials(tmp_path) -> None:
    repository, _ = build_repository(tmp_path)

    response = WorkbenchQueryService(repository).readiness(provider_configured=False)
    serialized = response.model_dump_json()

    assert response.status == "not_ready"
    assert {check.name for check in response.checks} == {
        "database",
        "artifact_store",
        "provider",
    }
    assert str(tmp_path) not in serialized
    assert "api_key" not in serialized.casefold()
