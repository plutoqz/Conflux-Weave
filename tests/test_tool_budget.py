"""P6-B4 预算硬限制测试：预留/入账/释放/超限停止/兜底不可绕过。"""

import pytest

from conflux_weave.compute_tools import ComputeToolRequest
from conflux_weave.core import BudgetLedger, RunRecord, RunStatus, StepRecord, StepStatus, TaskSpec
from conflux_weave.runtime import (
    LocalArtifactStore,
    RecordNotFound,
    SQLiteRuntimeRepository,
)
from conflux_weave.runtime.sqlite_tool_budget import ToolBudgetExceeded

NOW = "2026-09-12T11:00:00Z"


class _PassiveRuntime:
    executor_id = "passive@v1"
    task_kinds = ("paper_discovery",)

    def work_once(self, **kwargs):
        return None


def build_repository(tmp_path) -> SQLiteRuntimeRepository:
    store = LocalArtifactStore(tmp_path / "artifacts")
    return SQLiteRuntimeRepository(tmp_path / "db" / "runtime.sqlite3", store, clock=lambda: NOW)


def _submit_run(repository, suffix: str, ledger: BudgetLedger | None = None):
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
        budget=ledger or BudgetLedger(180, 20_000, 2_048, "unavailable", 4, 2, 1),
        created_at=NOW,
        updated_at=NOW,
    )
    repository.submit_task(
        task, run,
        [StepRecord(step_id=f"step-{suffix}", run_id=run.run_id, kind="publish_delivery", attempt=1, status=StepStatus.PENDING)],
    )
    return run.run_id


def test_reserve_and_settle_tool_budget(tmp_path) -> None:
    repository = build_repository(tmp_path)
    run_id = _submit_run(repository, "b1")
    reservation = repository.reserve_tool_budget(run_id, tool_calls=1, wall_clock_seconds=5)
    assert reservation.startswith("tool-res-")
    usage = repository.get_tool_budget_usage(run_id)
    assert usage["tool_calls_reserved"] == 1
    assert usage["tool_calls_used"] == 0

    assert repository.settle_tool_budget(reservation, actual_tool_calls=1, actual_seconds=3) is True
    usage = repository.get_tool_budget_usage(run_id)
    assert usage["tool_calls_used"] == 1 and usage["tool_calls_reserved"] == 0
    assert usage["wall_clock_seconds_used"] == 3

    # 已结算的预留不可重复结算
    assert repository.settle_tool_budget(reservation, actual_tool_calls=1, actual_seconds=3) is False


def test_reserve_denied_when_tool_budget_exhausted(tmp_path) -> None:
    repository = build_repository(tmp_path)
    run_id = _submit_run(repository, "b2")  # tool_calls 限额 4
    for _ in range(4):
        reservation = repository.reserve_tool_budget(run_id, tool_calls=1, wall_clock_seconds=1)
        repository.settle_tool_budget(reservation, actual_tool_calls=1, actual_seconds=1)
    with pytest.raises(ToolBudgetExceeded):
        repository.reserve_tool_budget(run_id, tool_calls=1, wall_clock_seconds=1)


def test_reserve_denied_after_wall_clock_deadline(tmp_path) -> None:
    repository = build_repository(tmp_path)
    run_id = _submit_run(repository, "b3")  # wall clock 180s
    with pytest.raises(ToolBudgetExceeded):
        repository.reserve_tool_budget(
            run_id, tool_calls=1, wall_clock_seconds=1,
            now="2026-09-12T11:04:00Z",  # 已超过 180s 预算窗口
        )


def test_stop_budget_blocks_every_further_call(tmp_path) -> None:
    """验收：超限后停止后续调用——不存在绕过路径。"""
    repository = build_repository(tmp_path)
    run_id = _submit_run(repository, "b4")
    reservation = repository.reserve_tool_budget(run_id, tool_calls=1, wall_clock_seconds=1)
    repository.settle_tool_budget(reservation, actual_tool_calls=1, actual_seconds=1)
    assert repository.stop_tool_budget(run_id) is True
    for _ in range(3):
        with pytest.raises(ToolBudgetExceeded):
            repository.reserve_tool_budget(run_id, tool_calls=1, wall_clock_seconds=1)


def test_budget_without_limit_row_raises_not_found(tmp_path) -> None:
    repository = build_repository(tmp_path)
    with pytest.raises(RecordNotFound):
        repository.reserve_tool_budget("run-missing", tool_calls=1, wall_clock_seconds=1)


def test_release_returns_budget(tmp_path) -> None:
    repository = build_repository(tmp_path)
    run_id = _submit_run(repository, "b5")  # tool_calls 限额 4
    first = repository.reserve_tool_budget(run_id, tool_calls=3, wall_clock_seconds=10)
    with pytest.raises(ToolBudgetExceeded):
        repository.reserve_tool_budget(run_id, tool_calls=2, wall_clock_seconds=10)
    assert repository.release_tool_budget(first) is True
    second = repository.reserve_tool_budget(run_id, tool_calls=2, wall_clock_seconds=10)
    assert second.startswith("tool-res-")


# ------------------------------------------------------------------ API 层


def test_api_compute_budget_exceeded_stops_run(tmp_path) -> None:
    """验收：预算不足不发起执行；失败记录 budget_exceeded；Run 状态转为 failed。"""
    from starlette.testclient import TestClient
    from conflux_weave.server import WorkerLoop, create_app

    store = LocalArtifactStore(tmp_path / "artifacts")
    repository = build_repository(tmp_path)
    run_id = _submit_run(repository, "api")
    # 预算超限时 Run 处于 RUNNING → 状态机允许 FAILED
    repository.transition_run(run_id, RunStatus.QUEUED, updated_at=NOW)
    repository.transition_run(run_id, RunStatus.RUNNING, updated_at=NOW)
    for _ in range(4):
        reservation = repository.reserve_tool_budget(run_id, tool_calls=1, wall_clock_seconds=1)
        repository.settle_tool_budget(reservation, actual_tool_calls=1, actual_seconds=1)

    app = create_app(repository, _PassiveRuntime(), worker=WorkerLoop(_PassiveRuntime(), interval_seconds=10))
    client = TestClient(app)
    response = client.post("/api/v1/tools/compute", json={
        "script": "print('must not run')",
        "timeout_seconds": 5,
        "run_id": run_id,
    })
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "budget_exceeded"
    assert "budget_exceeded" in payload["error"]
    assert payload["stdout_artifact_id"] is None

    detail = client.get(f"/api/v1/runs/{run_id}").json()
    assert detail["state"] == "failed"
    assert detail["budget"]["state"] == "stopped"

    # 兜底/重试同样不可绕过
    again = client.post("/api/v1/tools/compute", json={
        "script": "print('still must not run')",
        "timeout_seconds": 5,
        "run_id": run_id,
    })
    assert again.json()["status"] == "budget_exceeded"


def test_api_compute_settles_budget_on_success(tmp_path) -> None:
    from starlette.testclient import TestClient
    from conflux_weave.server import WorkerLoop, create_app

    store = LocalArtifactStore(tmp_path / "artifacts")
    repository = build_repository(tmp_path)
    run_id = _submit_run(repository, "ok")

    app = create_app(repository, _PassiveRuntime(), worker=WorkerLoop(_PassiveRuntime(), interval_seconds=10))
    client = TestClient(app)
    ok = client.post("/api/v1/tools/compute", json={
        "script": "print('compute')",
        "timeout_seconds": 5,
        "run_id": run_id,
    })
    assert ok.json()["status"] == "succeeded"
    usage = repository.get_tool_budget_usage(run_id)
    assert usage["tool_calls_used"] == 1

    detail = client.get(f"/api/v1/runs/{run_id}").json()
    budget = detail["budget"]
    # 页面展示字段：limit / reserved / actual / remaining
    assert budget["tool_calls_limit"] == 4
    assert budget["tool_calls_used"] == 1
    assert budget["tool_calls_remaining"] == 3
    assert {"tool_calls_reserved", "wall_clock_seconds_reserved", "wall_clock_seconds_remaining"} <= set(budget.keys())
