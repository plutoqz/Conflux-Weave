import asyncio
import json

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
from conflux_weave.runtime import LocalArtifactStore, SQLiteRuntimeRepository
from conflux_weave.runtime.durable_paper_shared import RANK_CHECKPOINT
from conflux_weave.server import WorkerLoop, create_app


NOW = "2026-08-25T12:00:00Z"


class PassiveRuntime:
    executor_id = "passive-paper@v1"
    task_kinds = ("paper_discovery",)

    def work_once(self, *, now: str | None = None) -> None:
        return None

    def submit(self, *args, **kwargs):
        raise AssertionError("submission is not used by this fixture")

    def request_cancel(self, *args, **kwargs):
        raise AssertionError("cancellation is not used by this fixture")

    def resume(self, *args, **kwargs):
        raise AssertionError("resume is not used by this fixture")


def route(app, path: str):
    return next(item.endpoint for item in app.routes if item.path == path)


def build_completed_app(tmp_path):
    store = LocalArtifactStore(tmp_path / "artifacts")
    repository = SQLiteRuntimeRepository(
        tmp_path / "db" / "runtime.sqlite3", store, clock=lambda: NOW
    )
    task = TaskSpec(
        task_id="task-workbench",
        kind="paper_discovery",
        input={"query": "How does durable recovery preserve evidence?"},
        requested_policy="fixed-arxiv-v1",
        idempotency_key="workbench-fixture",
    )
    run = RunRecord(
        run_id="run-workbench",
        task_id=task.task_id,
        status=RunStatus.ACCEPTED,
        workflow_version="fixed-arxiv-v1",
        config_snapshot_ref="fixture-config",
        budget=BudgetLedger(60, 100, 100, "unavailable", 1, 1, 1),
        created_at=NOW,
        updated_at=NOW,
    )
    steps = (
        StepRecord(
            step_id="step-workbench-rank",
            run_id=run.run_id,
            kind="rank_candidates",
            attempt=1,
            status=StepStatus.PENDING,
        ),
        StepRecord(
            step_id="step-workbench-publish",
            run_id=run.run_id,
            kind="publish_delivery",
            attempt=1,
            status=StepStatus.PENDING,
        ),
    )
    repository.submit_task(task, run, steps)
    repository.transition_run(run.run_id, RunStatus.QUEUED, updated_at=NOW)
    rank_claim = repository.claim_next_step("fixture-worker", lease_seconds=60, now=NOW)
    assert rank_claim is not None
    checkpoint = store.put_json(
        {
            "schema_version": RANK_CHECKPOINT,
            "evidence": [
                {
                    "evidence_id": "evidence-workbench-1",
                    "source_snapshot_id": "source-snapshot-1",
                    "locator": {"section": "abstract", "paragraph": 2},
                    "quote": "Durable state keeps delivery lineage available after restart.",
                    "extraction_method": "structured-fixture",
                }
            ],
        },
        producer_step_id=rank_claim.step_id,
        schema_version=RANK_CHECKPOINT,
    )
    repository.complete_attempt(rank_claim, (checkpoint,), now=NOW)
    publish_claim = repository.claim_next_step("fixture-worker", lease_seconds=60, now=NOW)
    assert publish_claim is not None
    report = store.put_bytes(
        b"# Durable recovery\n\nPersisted evidence remains inspectable [1].\n",
        media_type="text/markdown; charset=utf-8",
        producer_step_id=publish_claim.step_id,
        schema_version="conflux-weave.paper-discovery-report.v1",
    )
    unregistered = store.put_bytes(
        b"not published",
        media_type="text/plain",
        producer_step_id=publish_claim.step_id,
        schema_version="fixture.private.v1",
    )
    delivery = DeliveryRecord(
        run_id=run.run_id,
        disposition=DeliveryDisposition.PARTIAL,
        artifact_refs=(report.artifact_id,),
        evidence_refs=("evidence-workbench-1",),
        limitations=("Fixture evidence only.",),
        unmet_criteria=("No live Provider validation.",),
        recovery_actions=("Create a separately authorized live Run.",),
    )
    repository.publish_delivery(
        run.run_id,
        RunStatus.PARTIAL,
        delivery,
        (report,),
        claim=publish_claim,
        published_at=NOW,
    )
    runtime = PassiveRuntime()
    app = create_app(
        repository,
        runtime,
        provider_configured=False,
        worker=WorkerLoop(runtime, interval_seconds=10),
    )
    return app, report, unregistered


def test_workbench_is_packaged_same_origin_without_external_assets(tmp_path) -> None:
    app, _, _ = build_completed_app(tmp_path)
    index_response = asyncio.run(route(app, "/")())
    index = index_response.path.read_text(encoding="utf-8")
    workbench_root = index_response.path.parent
    styles = (workbench_root / "styles.css").read_text(encoding="utf-8")
    script = (workbench_root / "app.js").read_text(encoding="utf-8")

    assert index_response.media_type == "text/html"
    assert 'id="run-list"' in index
    assert 'id="task-dialog"' in index
    assert 'class="mode-switch"' in index
    assert 'id="retry-run"' in index
    assert 'id="fail-run"' in index
    assert "@media (max-width: 760px)" in styles
    assert "EventSource" in script
    assert "/api/v1/tasks/research" in script
    assert "/api/v1/tasks/research-fixture" in script
    assert "updateTaskMode" in script
    assert "retry_unknown_external" in script
    assert "fail_unknown_external" in script
    assert "eventCursor" in script
    assert "events?after=${state.eventCursor}" in script
    assert "state.eventReconnectTimer" in script
    assert "if (run.is_terminal) return" not in script
    assert "overflow-wrap: anywhere" in styles
    assert "grid-template-columns: 1fr" in styles
    assert "http://" not in index + styles + script
    assert "https://" not in index + styles + script


def test_workbench_w55_layout_and_keyboard_contracts_are_local_and_responsive() -> None:
    from pathlib import Path

    root = Path(__file__).parents[1] / "src" / "conflux_weave" / "workbench"
    index = (root / "index.html").read_text(encoding="utf-8")
    styles = (root / "styles.css").read_text(encoding="utf-8")
    script = (root / "app.js").read_text(encoding="utf-8")

    # These are the DOM/CSS contracts exercised by the fixture browser run at
    # 320px, 390px and 200% zoom; no external browser runtime is required here.
    assert 'aria-label="研究历史"' in index
    assert 'role="tablist"' in index
    assert '<dialog id="task-dialog"' in index
    assert "calc(100vw - 32px)" in styles
    assert "overflow-wrap: anywhere" in styles
    assert "showModal()" in script
    assert "event.preventDefault()" in script


def test_workbench_reads_only_registered_delivery_text(tmp_path) -> None:
    app, report, unregistered = build_completed_app(tmp_path)
    read_artifact = route(
        app, "/api/v1/runs/{run_id}/artifacts/{artifact_id}/content"
    )
    response = asyncio.run(read_artifact("run-workbench", report.artifact_id))
    rejected = asyncio.run(read_artifact("run-workbench", unregistered.artifact_id))

    assert response.artifact.schema_version == (
        "conflux-weave.paper-discovery-report.v1"
    )
    assert "Persisted evidence remains inspectable" in response.content
    assert rejected.status_code == 404
    assert json.loads(rejected.body) == {
        "code": "not_found",
        "message": "请求的记录不存在。",
        "recovery_action": None,
        "retryable": False,
    }
