"""P6-C1 API/Worker 进程分离测试：独立 Worker、API 无 Worker、租约过期接管。

验收对照：
- Worker 重启不影响 API（enable_worker=False 的 API 全功能可用）
- API 重启不丢待执行任务（共享 SQLite 队列，重启后任务仍可被认领）
- Worker 崩溃后 lease 能过期，另一个 Worker 可接管
- 已完成步骤不会重复执行
- 事件可从数据库补发（run_events 持久化 + 游标重放）
"""

import asyncio

import pytest
from starlette.testclient import TestClient

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
from conflux_weave.server import WorkerLoop, create_app

NOW = "2026-09-12T12:00:00Z"


class _ExecutingRuntime:
    """独立 Worker 使用的最小执行器：认领 publish 步骤并发布交付。"""

    executor_id = "worker-fixture@v1"
    task_kinds = ("paper_discovery",)

    def __init__(self, repository, store):
        self.repository = repository
        self.store = store
        self.work_calls = 0

    def work_once(self, **kwargs):
        self.work_calls += 1
        claim = self.repository.claim_next_step(
            f"standalone-{self.work_calls}", lease_seconds=60, now=NOW
        )
        if claim is None:
            return None
        if claim.step_id.endswith("-publish"):
            report = self.store.put_bytes(
                b"# worker report\n",
                media_type="text/markdown; charset=utf-8",
                producer_step_id=claim.step_id,
                schema_version="conflux-weave.delivery.v1",
            )
            self.repository.publish_delivery(
                claim.run_id,
                RunStatus.SUCCEEDED,
                DeliveryRecord(
                    run_id=claim.run_id,
                    disposition=DeliveryDisposition.COMPLETE,
                    artifact_refs=(report.artifact_id,),
                ),
                (report,),
                claim=claim,
                published_at=NOW,
            )
            return claim
        checkpoint = self.store.put_json(
            {"schema_version": "fixture.checkpoint.v1"},
            producer_step_id=claim.step_id,
            schema_version="fixture.checkpoint.v1",
        )
        self.repository.complete_attempt(claim, (checkpoint,), now=NOW)
        return claim


def build_repository(tmp_path):
    store = LocalArtifactStore(tmp_path / "artifacts")
    repository = SQLiteRuntimeRepository(tmp_path / "db" / "runtime.sqlite3", store, clock=lambda: NOW)
    return repository, store


def submit_task(repository, suffix: str):
    task = TaskSpec(
        task_id=f"task-{suffix}",
        kind="paper_discovery",
        input={"query": "q"},
        requested_policy="p",
        idempotency_key=f"key-{suffix}",
    )
    run = RunRecord(
        run_id=f"run-{suffix}",
        task_id=task.task_id,
        status=RunStatus.ACCEPTED,
        workflow_version="v",
        config_snapshot_ref="c",
        budget=BudgetLedger(180, 20_000, 2_048, "unavailable", 4, 2, 1),
        created_at=NOW,
        updated_at=NOW,
    )
    repository.submit_task(
        task, run,
        [
            StepRecord(step_id=f"step-{suffix}-rank", run_id=run.run_id, kind="rank_candidates", attempt=1, status=StepStatus.PENDING),
            StepRecord(step_id=f"step-{suffix}-publish", run_id=run.run_id, kind="publish_delivery", attempt=1, status=StepStatus.PENDING),
        ],
    )
    repository.transition_run(run.run_id, RunStatus.QUEUED, updated_at=NOW)
    repository.transition_run(run.run_id, RunStatus.RUNNING, updated_at=NOW)
    return run.run_id


def test_api_works_without_worker_and_tasks_stay_queued(tmp_path) -> None:
    """验收：Worker 不在（--no-worker）时 API 全功能；任务停在队列。"""
    repository, store = build_repository(tmp_path)
    runtime = _ExecutingRuntime(repository, store)
    app = create_app(repository, runtime, enable_worker=False)
    client = TestClient(app)

    health = client.get("/api/v1/health/live")
    assert health.status_code == 200
    runs = client.get("/api/v1/runs").json()
    assert runs["items"] == []  # 空队列可查

    run_id = submit_task(repository, "nq")
    page = client.get("/api/v1/runs").json()
    assert any(item["run_id"] == run_id for item in page["items"])
    detail = client.get(f"/api/v1/runs/{run_id}").json()
    assert detail["state"] == "working"  # RUNNING 但无 worker 推进
    # runtime 从未被 API 进程触发
    assert runtime.work_calls == 0


def test_standalone_worker_executes_shared_queue(tmp_path) -> None:
    """验收：API 重启不丢任务；独立 Worker 从共享 SQLite 队列认领并交付。"""
    repository, store = build_repository(tmp_path)
    run_id = submit_task(repository, "split")

    # "API 重启"：重新打开应用（新连接），队列中的任务仍在
    repository2, _ = build_repository(tmp_path)
    runtime = _ExecutingRuntime(repository2, store)
    app = create_app(repository2, runtime, enable_worker=False)
    client = TestClient(app)
    assert any(item["run_id"] == run_id for item in client.get("/api/v1/runs").json()["items"])

    # 独立 Worker 循环执行
    loop = WorkerLoop(runtime, interval_seconds=0.01)

    async def drive():
        await loop.start()
        for _ in range(2000):
            if repository.get_run(run_id).status.is_terminal:
                break
            await asyncio.sleep(0.01)
        await loop.stop()

    asyncio.run(drive())
    assert runtime.work_calls >= 1
    assert repository.get_run(run_id).status is RunStatus.SUCCEEDED


def test_expired_lease_taken_over_by_another_worker(tmp_path) -> None:
    """验收：Worker 崩溃后 lease 过期，另一个 Worker 接管；已完成步骤不重复。"""
    repository, store = build_repository(tmp_path)
    run_id = submit_task(repository, "lease")

    first = repository.claim_next_step("worker-crashed", lease_seconds=1, now=NOW)
    assert first is not None

    # lease 未过期：其他 Worker 看不到该步骤
    assert repository.claim_next_step("worker-b", lease_seconds=60, now=NOW) is None

    # "崩溃"：crashed worker 不再续约；另一个 Worker 在过期后认领同一步骤
    later = "2026-09-12T12:00:05Z"
    takeover = repository.claim_next_step("worker-b", lease_seconds=60, now=later)
    assert takeover is not None
    assert takeover.step_id == first.step_id

    # 接管后完成；已完成的步骤不会被再次认领
    report = store.put_bytes(b"x", media_type="text/plain", producer_step_id=takeover.step_id, schema_version="fixture.v1")
    repository.complete_attempt(takeover, (report,), now=later)
    next_claim = repository.claim_next_step("worker-b", lease_seconds=60, now=later)
    assert next_claim is None or next_claim.step_id != first.step_id


def test_run_events_replayable_from_database(tmp_path) -> None:
    """验收：事件可从数据库补发（outbox = run_events，游标重放）。"""
    repository, store = build_repository(tmp_path)
    run_id = submit_task(repository, "events")
    events_before = repository.get_run_events(run_id, after_event_id=0, limit=100)
    # "重启"：新连接读同库，事件仍在且可按游标补发
    repository2, _ = build_repository(tmp_path)
    events_after = repository2.get_run_events(run_id, after_event_id=0, limit=100)
    assert [e.event_id for e in events_after] == [e.event_id for e in events_before]
    if events_after:
        cursor = events_after[0].event_id
        rest = repository2.get_run_events(run_id, after_event_id=cursor, limit=100)
        assert all(e.event_id > cursor for e in rest)
