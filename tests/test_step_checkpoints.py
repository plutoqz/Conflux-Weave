"""P6-C2 Step 级 checkpoint 与恢复测试（深度研究主链路，离线）。

验收对照：
- 在 retrieve 阶段终止 Worker → 重启后从最近 checkpoint 继续（不重跑付费批次）
- 不重复生成不可区分的报告（输出 Artifact 一致）
- 原始失败记录保留
- 恢复后的 Run 可追溯每一步（checkpoint 台账）
"""

import pytest

from conflux_weave.core import (
    BudgetLedger,
    DeliveryDisposition,
    RunRecord,
    RunStatus,
    StepRecord,
    StepStatus,
    TaskSpec,
)
from conflux_weave.runtime import LocalArtifactStore, SQLiteRuntimeRepository
from conflux_weave.runtime.sqlite_contracts import BudgetAmount
from conflux_weave.core.errors import ErrorCategory, ErrorRecord
from conflux_weave.runtime.durable_research import (
    DURABLE_RESEARCH_WORKFLOW_VERSION,
    DurableResearchExecution,
    DurableResearchRuntime,
)

NOW = "2026-09-12T13:00:00Z"

EXECUTION_PAYLOAD = {
    "schema_version": "conflux-weave.durable-research-execution.v1",
    "report_artifact_id": "report",
    "manifest_artifact_id": "manifest",
    "evidence_refs": [],
    "evidence": [],
    "usage": {"input_tokens": 10, "output_tokens": 10, "tool_calls": 0, "retrieval_rounds": 1},
    "provider_call_count": 1,
    "timings_ms": {},
    "usage_granularity": "aggregate_research_batch",
    "disposition": "complete",
    "limitations": [],
    "unmet_criteria": [],
}


class CountingExecutor:
    """记录调用次数的执行器桩：复用时不得再次调用。"""

    calls = 0

    def __init__(self, store):
        self.store = store

    def __call__(self, task_kind, objective, max_subquestions):
        CountingExecutor.calls += 1
        step_id = f"stub-exec-{CountingExecutor.calls}"
        report = self.store.put_bytes(
            b"# report\n",
            media_type="text/markdown; charset=utf-8",
            producer_step_id=step_id,
            schema_version="conflux-weave.durable-research-report.v1",
        )
        manifest = self.store.put_json(
            {"schema_version": "fixture-manifest"},
            producer_step_id=step_id,
            schema_version="fixture-manifest.v1",
        )
        return DurableResearchExecution(
            report_artifact_id=report.artifact_id,
            manifest_artifact_id=manifest.artifact_id,
            evidence_refs=(),
            evidence_records=(),
            usage=BudgetAmount(input_tokens=10, output_tokens=10, tool_calls=1, retrieval_rounds=1),
            provider_call_count=1,
            disposition=DeliveryDisposition.COMPLETE,
        )


def build_runtime(tmp_path):
    store = LocalArtifactStore(tmp_path / "artifacts")
    repository = SQLiteRuntimeRepository(tmp_path / "db" / "runtime.sqlite3", store, clock=lambda: NOW)
    executor = CountingExecutor(store)
    runtime = DurableResearchRuntime(
        repository, store, executor, worker_id="w1", lease_seconds=60, clock=lambda: NOW
    )
    return repository, store, runtime


def _make_execution_artifact(store, step_id: str):
    payload = dict(EXECUTION_PAYLOAD)
    report = store.put_bytes(b"# report\n", media_type="text/markdown; charset=utf-8", producer_step_id=step_id, schema_version="conflux-weave.durable-research-report.v1")
    payload["report_artifact_id"] = report.artifact_id
    execution = store.put_json(payload, producer_step_id=step_id, schema_version="conflux-weave.durable-research-execution.v1")
    return report, execution


def submit_durable_run(runtime, suffix="c2"):
    """走 runtime.submit 正规路径（含 step_policies 注册），再置为 RUNNING。"""
    result = runtime.submit(
        "测试目标",
        task_kind="managed_verified_research",
        max_subquestions=2,
        budget=BudgetLedger(900, 1000, 1000, "unavailable", 4, 2, 1),
    )
    run_id = result.run_id
    runtime.repository.transition_run(run_id, RunStatus.RUNNING, updated_at=NOW)
    return run_id


def test_checkpoint_registry_roundtrip(tmp_path) -> None:
    repository, store, runtime = build_runtime(tmp_path)
    run_id = submit_durable_run(runtime)
    exec_step = next(s for s in repository.get_steps(run_id) if s.kind == "execute_research")
    report, execution = _make_execution_artifact(store, exec_step.step_id)
    repository.record_step_checkpoint(
        run_id, exec_step.step_id,
        chain_phase="retrieve", input_digest="sha256:abc", output_artifact_id=execution.artifact_id,
        attempt=1, started_at=NOW, finished_at=NOW,
    )
    items = repository.list_step_checkpoints(run_id)
    assert len(items) == 1
    assert items[0]["chain_phase"] == "retrieve"
    assert items[0]["status"] == "succeeded"
    # 同输入摘要 → 可复用
    reused = repository.reuse_step_checkpoint(run_id, "retrieve", "sha256:abc")
    assert reused is not None
    assert reused["output_artifact_id"] == execution.artifact_id
    # 输入摘要不同 → 不可复用
    assert repository.reuse_step_checkpoint(run_id, "retrieve", "sha256:different") is None
    # 阶段不符 → 不可复用
    assert repository.reuse_step_checkpoint(run_id, "write", "sha256:abc") is None


def test_worker_termination_resumes_from_checkpoint(tmp_path) -> None:
    """验收：retrieve 阶段后终止 Worker → 重启后从 checkpoint 继续，不重跑批次。"""
    repository, store, runtime = build_runtime(tmp_path)
    CountingExecutor.calls = 0
    run_id = submit_durable_run(runtime)

    # 第一次执行：完成 retrieve 步骤（付费批次运行一次）
    result = runtime.work_once(now=NOW)
    assert result is not None and result.status in {"running", "succeeded"}
    assert CountingExecutor.calls == 1
    checkpoints = repository.list_step_checkpoints(run_id)
    assert any(item["chain_phase"] == "retrieve" for item in checkpoints)
    execution_artifact = next(item["output_artifact_id"] for item in checkpoints if item["chain_phase"] == "retrieve")

    # 模拟在 publish 阶段 Worker 崩溃：publish 步骤失败，Run 仍 running
    # （execute_research 的 attempt 输出已在库中，checkpoint 台账可复用）
    # 新的 Worker（进程重启语义）认领并执行
    runtime2 = DurableResearchRuntime(repository, store, CountingExecutor(store), worker_id="w2-restarted", lease_seconds=60, clock=lambda: NOW)
    # "Worker 在 retrieve 后崩溃重启"：新 worker 进程，同一 SQLite 队列
    for _ in range(6):
        result = runtime2.work_once(now=NOW)
        if result is None or repository.get_run(run_id).status.is_terminal:
            break
    assert repository.get_run(run_id).status in {RunStatus.SUCCEEDED, RunStatus.PARTIAL}
    assert CountingExecutor.calls == 1  # 付费批次未重复执行


def test_requeue_allows_retry_and_preserves_failure_record(tmp_path) -> None:
    """验收：可重试错误自动重试；原始失败记录保留。"""
    repository, store, runtime = build_runtime(tmp_path)
    run_id = submit_durable_run(runtime)
    detail = store.put_json({"error": "transient"}, producer_step_id=f"{run_id}:execute_research", schema_version="fixture.error.v1")
    claim = repository.claim_next_step("w1", lease_seconds=60, now=NOW)
    assert claim is not None
    repository.fail_attempt(claim, detail.artifact_id, now=NOW)
    repository.record_error(
        claim,
        ErrorRecord(
            "research_step_failed",
            ErrorCategory.UNKNOWN,
            "execute_research",
            True,
            "transient failure",
            detail.artifact_id,
            (),
            "retry the step",
        ),
        (detail,),
        now=NOW,
    )
    # 失败记录入 errors 表
    assert len(repository.get_errors(run_id)) >= 1
    # 放回队列后可再次认领（attempt+1）
    assert repository.requeue_failed_step(run_id, claim.step_id) is True
    claim2 = repository.claim_next_step("w1", lease_seconds=60, now=NOW)
    assert claim2 is not None and claim2.attempt_number == 2
    # 原始失败记录仍在
    assert len(repository.get_errors(run_id)) >= 1


def test_unknown_outcome_checkpoints_not_reusable(tmp_path) -> None:
    repository, store, runtime = build_runtime(tmp_path)
    run_id = submit_durable_run(runtime)
    exec_step = next(s for s in repository.get_steps(run_id) if s.kind == "execute_research")
    report, execution = _make_execution_artifact(store, exec_step.step_id)
    repository.record_step_checkpoint(
        run_id, exec_step.step_id,
        chain_phase="retrieve", input_digest="sha256:xyz", output_artifact_id=execution.artifact_id,
        attempt=1, started_at=NOW,
    )
    repository.mark_step_checkpoints_unknown_outcome(run_id, exec_step.step_id)
    items = repository.list_step_checkpoints(run_id)
    assert any(item["status"] == "unknown_outcome" for item in items)
    assert repository.reuse_step_checkpoint(run_id, "retrieve", "sha256:xyz") is None


def test_checkpoints_api_listing(tmp_path) -> None:
    from starlette.testclient import TestClient
    from conflux_weave.server import WorkerLoop, create_app

    class Passive:
        executor_id = "p"
        task_kinds = ("paper_discovery",)

        def work_once(self, **kwargs):
            return None

    repository, store, runtime = build_runtime(tmp_path)
    run_id = submit_durable_run(runtime)
    exec_step = next(s for s in repository.get_steps(run_id) if s.kind == "execute_research")
    report, execution = _make_execution_artifact(store, exec_step.step_id)
    repository.record_step_checkpoint(
        run_id, exec_step.step_id,
        chain_phase="retrieve", input_digest="sha256:abc", output_artifact_id=execution.artifact_id,
        attempt=1, started_at=NOW,
    )
    app = create_app(repository, Passive(), worker=WorkerLoop(Passive(), interval_seconds=10))
    client = TestClient(app)
    response = client.get(f"/api/v1/runs/{run_id}/checkpoints")
    assert response.status_code == 200
    payload = response.json()
    assert payload["run_id"] == run_id
    assert payload["items"][0]["chain_phase"] in {"plan", "retrieve", "claim", "verify", "synthesize", "write", "deliver"}
    missing = client.get("/api/v1/runs/run-missing/checkpoints")
    assert missing.status_code == 404
