"""Build the deterministic UX-0.2 state-matrix fixture database.

Zero network, zero Provider calls. Produces a SQLite database with one Run per
user-visible state so the browser matrix can assert allowed/forbidden actions.

Usage:
    uv run --frozen python scripts/make_ux0_state_fixture.py
"""

from __future__ import annotations

import shutil
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
from conflux_weave.runtime import LocalArtifactStore, SQLiteRuntimeRepository
from conflux_weave.runtime.durable_paper_shared import RANK_CHECKPOINT

ROOT = Path("tmp/ux0-states")
DB = ROOT / "states.sqlite3"
ARTIFACTS = ROOT / "artifacts"

BASE_TS = "2026-08-28T03:{minute:02d}:00Z"


def ts(minute: int) -> str:
    return BASE_TS.format(minute=minute)


def submit(repository, *, name: str, kind: str, task_input: dict, minute: int):
    task = TaskSpec(
        task_id=f"task-ux0-{name}",
        kind=kind,
        input=task_input,
        requested_policy="ux0-fixture-v1",
        idempotency_key=f"ux0-fixture-{name}",
    )
    run = RunRecord(
        run_id=f"run-ux0-{name}",
        task_id=task.task_id,
        status=RunStatus.ACCEPTED,
        workflow_version="ux0-fixture-v1",
        config_snapshot_ref="ux0-fixture-config",
        budget=BudgetLedger(180, 20_000, 2_048, "unavailable", 4, 2, 1),
        created_at=ts(minute),
        updated_at=ts(minute),
    )
    steps = (
        StepRecord(
            step_id=f"step-ux0-{name}-rank",
            run_id=run.run_id,
            kind="rank_candidates",
            attempt=1,
            status=StepStatus.PENDING,
        ),
        StepRecord(
            step_id=f"step-ux0-{name}-publish",
            run_id=run.run_id,
            kind="publish_delivery",
            attempt=1,
            status=StepStatus.PENDING,
        ),
    )
    repository.submit_task(task, run, steps)
    return run


def walk(repository, run_id: str, path: tuple[RunStatus, ...], minute: int) -> None:
    for target in path:
        repository.transition_run(run_id, target, updated_at=ts(minute))


def publish(repository, store, run, name: str, minute: int, *, disposition, evidence, limitations=(), unmet=(), actions=(), report_text: str):
    walk(repository, run.run_id, (RunStatus.QUEUED, RunStatus.RUNNING), minute)
    rank_claim = repository.claim_next_step("ux0-fixture-worker", lease_seconds=600, now=ts(minute))
    assert rank_claim is not None and rank_claim.run_id == run.run_id
    checkpoint = store.put_json(
        {"schema_version": RANK_CHECKPOINT, "evidence": evidence},
        producer_step_id=rank_claim.step_id,
        schema_version=RANK_CHECKPOINT,
    )
    repository.complete_attempt(rank_claim, (checkpoint,), now=ts(minute))
    publish_claim = repository.claim_next_step("ux0-fixture-worker", lease_seconds=600, now=ts(minute))
    assert publish_claim is not None and publish_claim.run_id == run.run_id
    report = store.put_bytes(
        report_text.encode("utf-8"),
        media_type="text/markdown; charset=utf-8",
        producer_step_id=publish_claim.step_id,
        schema_version="conflux-weave.paper-discovery-report.v1",
    )
    target = RunStatus.SUCCEEDED if disposition == DeliveryDisposition.COMPLETE else RunStatus.PARTIAL
    delivery = DeliveryRecord(
        run_id=run.run_id,
        disposition=disposition,
        artifact_refs=(report.artifact_id,),
        evidence_refs=tuple(item["evidence_id"] for item in evidence),
        limitations=limitations,
        unmet_criteria=unmet,
        recovery_actions=actions,
    )
    repository.publish_delivery(
        run.run_id, target, delivery, (report,), claim=publish_claim, published_at=ts(minute)
    )


def main() -> None:
    if ROOT.exists():
        shutil.rmtree(ROOT)
    ROOT.mkdir(parents=True)
    store = LocalArtifactStore(ARTIFACTS)
    repository = SQLiteRuntimeRepository(DB, store, clock=lambda: ts(0))

    verified_input = {"objective": "How do agents preserve evidence across recovery?", "max_subquestions": 4}
    discovery_input = {"query": "durable agent evidence recovery", "topics": ["agents"], "max_results": 15}

    # 1. complete（列表最前，默认选中）
    run = submit(repository, name="complete", kind="verified_paper_research", task_input=verified_input, minute=50)
    publish(
        repository, store, run, "complete", 50,
        disposition=DeliveryDisposition.COMPLETE,
        evidence=[
            {
                "evidence_id": "ev-ux0-complete-1",
                "source_snapshot_id": "document-sha256-aaa111",
                "locator": {"page": 3, "section": "method"},
                "quote": "Durable runs keep evidence lineage inspectable after restart.",
                "extraction_method": "structured-fixture",
            },
            {
                "evidence_id": "ev-ux0-complete-2",
                "source_snapshot_id": "document-sha256-bbb222",
                "locator": {"page": 7, "paragraph": 2},
                "quote": "Recovery replays persisted terminal state instead of paid calls.",
                "extraction_method": "structured-fixture",
            },
        ],
        report_text="# 核验研究完成\n\n证据链闭合，两条引用均可回溯 [1][2]。\n",
    )

    # 2. partial（boundary 三色：limitation/unmet/action）
    run = submit(repository, name="partial", kind="verified_paper_research", task_input=verified_input, minute=40)
    publish(
        repository, store, run, "partial", 40,
        disposition=DeliveryDisposition.PARTIAL,
        evidence=[
            {
                "evidence_id": "ev-ux0-partial-1",
                "source_snapshot_id": "document-sha256-ccc333",
                "locator": {"page": 1},
                "quote": "Only one source could be verified within budget.",
                "extraction_method": "structured-fixture",
            },
        ],
        limitations=("仅核验单条来源，覆盖面不足。",),
        unmet=("未达到双来源交叉验证标准。",),
        actions=("提高检索预算后重新研究。",),
        report_text="# 部分完成\n\n单来源结论，限制见下。\n",
    )

    # 3. working（1/2 步完成，进行中）
    run = submit(repository, name="working", kind="paper_discovery", task_input=discovery_input, minute=30)
    walk(repository, run.run_id, (RunStatus.QUEUED, RunStatus.RUNNING), 30)
    rank_claim = repository.claim_next_step("ux0-fixture-worker", lease_seconds=600, now=ts(30))
    assert rank_claim is not None and rank_claim.run_id == run.run_id
    repository.complete_attempt(rank_claim, (), now=ts(30))

    # 4. pending
    run = submit(repository, name="pending", kind="paper_discovery", task_input=discovery_input, minute=25)
    walk(repository, run.run_id, (RunStatus.QUEUED,), 25)

    # 5. needs_attention（retry/fail 决策）
    run = submit(repository, name="needs-attention", kind="verified_paper_research", task_input=verified_input, minute=20)
    walk(repository, run.run_id, (RunStatus.QUEUED, RunStatus.RUNNING, RunStatus.WAITING_FOR_USER), 20)

    # 6. cancelling
    run = submit(repository, name="cancelling", kind="paper_discovery", task_input=discovery_input, minute=15)
    walk(repository, run.run_id, (RunStatus.QUEUED, RunStatus.RUNNING, RunStatus.CANCELLING), 15)

    # 7. failed
    run = submit(repository, name="failed", kind="paper_discovery", task_input=discovery_input, minute=10)
    walk(repository, run.run_id, (RunStatus.QUEUED, RunStatus.RUNNING, RunStatus.FAILED), 10)

    # 8. cancelled
    run = submit(repository, name="cancelled", kind="paper_discovery", task_input=discovery_input, minute=5)
    walk(repository, run.run_id, (RunStatus.QUEUED, RunStatus.RUNNING, RunStatus.CANCELLING, RunStatus.CANCELLED), 5)

    # 9. expired
    run = submit(repository, name="expired", kind="paper_discovery", task_input=discovery_input, minute=1)
    walk(repository, run.run_id, (RunStatus.QUEUED, RunStatus.RUNNING, RunStatus.WAITING_FOR_USER, RunStatus.EXPIRED), 1)

    print(f"fixture ready: {DB}")
    for row in repository.list_runs().items:
        print(f"  {row.run.run_id}  {row.run.status.value}")


if __name__ == "__main__":
    main()
