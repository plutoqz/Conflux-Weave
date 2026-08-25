"""Fixture-only ASGI app for W5.3 browser acceptance."""

from __future__ import annotations

import os
from pathlib import Path

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
    RecoveryDecision,
    SQLiteRuntimeRepository,
)
from conflux_weave.runtime.durable_paper_shared import RANK_CHECKPOINT
from conflux_weave.server import create_app


NOW = "2026-08-25T12:00:00Z"
ROOT = Path(os.environ["W5_WORKBENCH_FIXTURE_ROOT"])
STORE = LocalArtifactStore(ROOT / "artifacts")
REPOSITORY = SQLiteRuntimeRepository(
    ROOT / "db" / "runtime.sqlite3", STORE, clock=lambda: NOW
)


class BrowserFixtureRuntime:
    def __init__(self) -> None:
        self.submission_count = 0

    def submit(self, query: str, *, search_query: str, max_results: int = 15):
        self.submission_count += 1
        suffix = self.submission_count
        task = TaskSpec(
            task_id=f"task-browser-{suffix}",
            kind="paper_discovery",
            input={
                "query": query,
                "search_query": search_query,
                "max_results": max_results,
            },
            requested_policy="offline-browser-fixture-v1",
            idempotency_key=f"browser-fixture-{suffix}",
        )
        run = RunRecord(
            run_id=f"run-browser-{suffix}",
            task_id=task.task_id,
            status=RunStatus.ACCEPTED,
            workflow_version="offline-browser-fixture-v1",
            config_snapshot_ref="offline-browser-fixture",
            budget=BudgetLedger(60, 100, 100, "unavailable", 1, 1, 1),
            created_at=NOW,
            updated_at=NOW,
        )
        steps = (
            StepRecord(
                step_id=f"step-browser-{suffix}",
                run_id=run.run_id,
                kind="fixture_step",
                attempt=1,
                status=StepStatus.PENDING,
            ),
        )
        result = REPOSITORY.submit_task(task, run, steps)
        REPOSITORY.transition_run(result.run_id, RunStatus.QUEUED, updated_at=NOW)
        return result

    def work_once(self, *, now: str | None = None) -> None:
        return None

    def request_cancel(self, run_id: str, *, now: str | None = None):
        return REPOSITORY.request_cancel(run_id, now=now or NOW)

    def resume(
        self,
        run_id: str,
        decision: RecoveryDecision | None = None,
        *,
        now: str | None = None,
    ):
        return REPOSITORY.resume_run(run_id, decision, now=now or NOW)


def _seed_completed_run() -> None:
    task = TaskSpec(
        task_id="task-browser-complete",
        kind="paper_discovery",
        input={
            "query": "持久化运行如何在中断后保持证据可追溯？",
            "search_query": "durable runtime evidence recovery",
            "max_results": 5,
        },
        requested_policy="offline-browser-fixture-v1",
        idempotency_key="browser-complete-fixture",
    )
    run = RunRecord(
        run_id="run-browser-complete",
        task_id=task.task_id,
        status=RunStatus.ACCEPTED,
        workflow_version="offline-browser-fixture-v1",
        config_snapshot_ref="offline-browser-fixture",
        budget=BudgetLedger(60, 1000, 400, "unavailable", 3, 2, 1),
        created_at=NOW,
        updated_at=NOW,
    )
    steps = (
        StepRecord(
            step_id="step-browser-rank",
            run_id=run.run_id,
            kind="rank_candidates",
            attempt=1,
            status=StepStatus.PENDING,
        ),
        StepRecord(
            step_id="step-browser-publish",
            run_id=run.run_id,
            kind="publish_delivery",
            attempt=1,
            status=StepStatus.PENDING,
        ),
    )
    result = REPOSITORY.submit_task(task, run, steps)
    if not result.created:
        return
    REPOSITORY.transition_run(run.run_id, RunStatus.QUEUED, updated_at=NOW)
    rank_claim = REPOSITORY.claim_next_step("browser-fixture", lease_seconds=60, now=NOW)
    if rank_claim is None:
        raise RuntimeError("fixture rank step was not claimable")
    checkpoint = STORE.put_json(
        {
            "schema_version": RANK_CHECKPOINT,
            "evidence": [
                {
                    "evidence_id": "evidence-browser-1",
                    "source_snapshot_id": "snapshot-runtime-paper",
                    "locator": {"section": "abstract", "sentence": 3},
                    "quote": "Persisted state and content-addressed evidence remain available across process restarts.",
                    "extraction_method": "offline-browser-fixture",
                },
                {
                    "evidence_id": "evidence-browser-2",
                    "source_snapshot_id": "snapshot-recovery-paper",
                    "locator": {"section": "results", "paragraph": 2},
                    "quote": "Fencing prevents an interrupted attempt from publishing stale effects after recovery.",
                    "extraction_method": "offline-browser-fixture",
                },
            ],
        },
        producer_step_id=rank_claim.step_id,
        schema_version=RANK_CHECKPOINT,
    )
    REPOSITORY.complete_attempt(rank_claim, (checkpoint,), now=NOW)
    publish_claim = REPOSITORY.claim_next_step(
        "browser-fixture", lease_seconds=60, now=NOW
    )
    if publish_claim is None:
        raise RuntimeError("fixture publish step was not claimable")
    report = STORE.put_bytes(
        (
            "# 持久化恢复与证据边界\n\n"
            "运行状态写入 SQLite，交付文件采用内容寻址保存，因此服务刷新或重启后仍可重新构建结果视图 [1]。\n\n"
            "Lease fencing 会阻止旧尝试在恢复后发布过期结果，未知的付费外部调用不会自动重放 [2]。\n"
        ).encode("utf-8"),
        media_type="text/markdown; charset=utf-8",
        producer_step_id=publish_claim.step_id,
        schema_version="conflux-weave.paper-discovery-report.v1",
    )
    delivery = DeliveryRecord(
        run_id=run.run_id,
        disposition=DeliveryDisposition.PARTIAL,
        artifact_refs=(report.artifact_id,),
        evidence_refs=("evidence-browser-1", "evidence-browser-2"),
        limitations=("本结果来自离线 fixture，不代表真实论文检索能力。",),
        unmet_criteria=("尚未执行真实 Provider 与公共研究来源验证。",),
        recovery_actions=("在单独授权后创建真实 Run。",),
    )
    REPOSITORY.publish_delivery(
        run.run_id,
        RunStatus.PARTIAL,
        delivery,
        (report,),
        claim=publish_claim,
        published_at=NOW,
    )


_seed_completed_run()
app = create_app(
    REPOSITORY,
    BrowserFixtureRuntime(),
    provider_configured=True,
    poll_interval_seconds=1,
)
