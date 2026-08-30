"""Serve the UX-1 workbench verification fixture on 127.0.0.1:8767.

Offline environment for the UX-1 browser verification:

- ``research_fixture`` Runs execute for real through the offline fixture
  runtime (no network, no Provider).
- Chat submissions to ``/api/v1/tasks/verified-research`` are rewritten to
  ``research_fixture`` at the orchestrator boundary so the unified-entry flow
  is exercisable without a corpus or model credentials. The stored Run stays an
  honest ``research_fixture`` Run — family labels and metadata are not faked.
- Provider is deliberately unconfigured (``provider_configured=False``) so the
  overview alert and settings guidance render their not-ready states.
- A deterministic verified-thread history (root -> follow-up, plus one Run
  whose parent is absent) is seeded on first start so the UX-1.1 read-only
  chat thread view has stable acceptance data. No Run is ever deleted.

Usage:
    uv run --frozen python scripts/serve_ux1_fixture.py
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import uvicorn
from dotenv import dotenv_values

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
from conflux_weave.harness.contracts import TaskSubmission
from conflux_weave.harness.fixture_runtime import (
    FIXTURE_TASK_KIND,
    ResearchFixtureRuntime,
)
from conflux_weave.harness.orchestration import CompositeOrchestrator
from conflux_weave.harness.workspace import LocalWorkspaceAdapter
from conflux_weave.runtime import LocalArtifactStore, SQLiteRuntimeRepository
from conflux_weave.runtime.durable_paper_shared import RANK_CHECKPOINT
from conflux_weave.server import create_app

ROOT = Path("tmp/ux1-fixture")
DOTENV_PATH = ROOT / "settings.env"

_VERIFIED_KINDS = {"verified_paper_research", "managed_verified_research"}

SEED_BASE_TS = "2026-08-28T09:{minute:02d}:00Z"


def _seed_ts(minute: int) -> str:
    return SEED_BASE_TS.format(minute=minute)


def _seed_submit(repository, *, name: str, task_input: dict, minute: int):
    task = TaskSpec(
        task_id=f"task-ux1-seed-{name}",
        kind="verified_paper_research",
        input=task_input,
        requested_policy="ux1-seed-fixture-v1",
        idempotency_key=f"ux1-seed-{name}",
    )
    run = RunRecord(
        run_id=f"run-ux1-seed-{name}",
        task_id=task.task_id,
        status=RunStatus.ACCEPTED,
        workflow_version="ux1-seed-fixture-v1",
        config_snapshot_ref="ux1-seed-fixture-config",
        budget=BudgetLedger(180, 20_000, 2_048, "unavailable", 4, 1, 1),
        created_at=_seed_ts(minute),
        updated_at=_seed_ts(minute),
    )
    steps = (
        StepRecord(
            step_id=f"step-ux1-seed-{name}-rank",
            run_id=run.run_id,
            kind="rank_candidates",
            attempt=1,
            status=StepStatus.PENDING,
        ),
        StepRecord(
            step_id=f"step-ux1-seed-{name}-publish",
            run_id=run.run_id,
            kind="publish_delivery",
            attempt=1,
            status=StepStatus.PENDING,
        ),
    )
    repository.submit_task(task, run, steps)
    return run


def _seed_publish(repository, store, run, *, minute, disposition, evidence, limitations=(), unmet=(), actions=(), report_text=""):
    for target in (RunStatus.QUEUED, RunStatus.RUNNING):
        repository.transition_run(run.run_id, target, updated_at=_seed_ts(minute))
    rank_claim = repository.claim_next_step("ux1-seed-worker", lease_seconds=600, now=_seed_ts(minute))
    assert rank_claim is not None and rank_claim.run_id == run.run_id
    checkpoint = store.put_json(
        {"schema_version": RANK_CHECKPOINT, "evidence": evidence},
        producer_step_id=rank_claim.step_id,
        schema_version=RANK_CHECKPOINT,
    )
    repository.complete_attempt(rank_claim, (checkpoint,), now=_seed_ts(minute))
    publish_claim = repository.claim_next_step("ux1-seed-worker", lease_seconds=600, now=_seed_ts(minute))
    assert publish_claim is not None and publish_claim.run_id == run.run_id
    report = store.put_bytes(
        report_text.encode("utf-8"),
        media_type="text/markdown; charset=utf-8",
        producer_step_id=publish_claim.step_id,
        schema_version="conflux-weave.verified-research-report.v2",
    )
    target = RunStatus.SUCCEEDED if disposition is DeliveryDisposition.COMPLETE else RunStatus.PARTIAL
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
        run.run_id, target, delivery, (report,), claim=publish_claim, published_at=_seed_ts(minute)
    )


def seed_thread_history(repository, store) -> None:
    """Seed one verified thread (root -> follow-up) and one truncated thread once."""

    if repository.list_runs().items:
        return
    root = _seed_submit(
        repository,
        name="root",
        task_input={"objective": "智能体的记忆架构应该怎么设计？", "max_subquestions": 4},
        minute=40,
    )
    _seed_publish(
        repository,
        store,
        root,
        minute=40,
        disposition=DeliveryDisposition.PARTIAL,
        evidence=[
            {
                "evidence_id": "ev-ux1-seed-root-1",
                "source_snapshot_id": "document-sha256-seedroot1",
                "locator": {"page": 12, "section": "memory"},
                "quote": "Agent memory layers separate episodic writes from semantic consolidation.",
                "extraction_method": "structured-fixture",
            },
        ],
        limitations=("仅核验单篇文献的记忆章节，覆盖面不足。",),
        unmet=("未覆盖检索阶段与写入阶段的联合评估标准。",),
        actions=("扩大记忆架构语料后重新研究。",),
        report_text=(
            "# 智能体的记忆架构应该怎么设计？\n\n"
            "## 回答摘要\n\n"
            "记忆层把情景写入与语义固化分开组织 [1]。\n\n"
            "## 限制\n\n"
            "- 仅核验单篇文献的记忆章节，覆盖面不足。\n"
        ),
    )
    follow_up = _seed_submit(
        repository,
        name="followup",
        task_input={
            "objective": (
                "Original research objective: 智能体的记忆架构应该怎么设计？\n"
                "Follow-up question: 记忆写入如何去重与检索？"
            ),
            "max_subquestions": 4,
            "parent_run_id": "run-ux1-seed-root",
            "follow_up_question": "记忆写入如何去重与检索？",
        },
        minute=55,
    )
    _seed_publish(
        repository,
        store,
        follow_up,
        minute=55,
        disposition=DeliveryDisposition.COMPLETE,
        evidence=[
            {
                "evidence_id": "ev-ux1-seed-followup-1",
                "source_snapshot_id": "document-sha256-seedfollow1",
                "locator": {"page": 14, "paragraph": 2},
                "quote": "Writes are deduplicated by content hash before the retrieval index is updated.",
                "extraction_method": "structured-fixture",
            },
        ],
        limitations=("Evidence is limited to retrieved page-level PDF chunks from: seeded fixture corpus.",),
        report_text=(
            "# 记忆写入如何去重与检索？\n\n"
            "## 回答摘要\n\n"
            "写入在检索索引更新前按内容哈希去重 [1]。\n"
        ),
    )
    orphan = _seed_submit(
        repository,
        name="orphan",
        task_input={
            "objective": "地理智能体涉及哪些GIS方面的应用？",
            "max_subquestions": 4,
            "parent_run_id": "run-ux1-seed-archived-root",
            "follow_up_question": "地理智能体在路径规划上有哪些进展？",
        },
        minute=30,
    )
    _seed_publish(
        repository,
        store,
        orphan,
        minute=30,
        disposition=DeliveryDisposition.COMPLETE,
        evidence=[
            {
                "evidence_id": "ev-ux1-seed-orphan-1",
                "source_snapshot_id": "document-sha256-seedorphan1",
                "locator": {"page": 3, "section": "gis-agents"},
                "quote": "Geographic agents combine spatial measurement with cross-source reasoning.",
                "extraction_method": "structured-fixture",
            },
        ],
        limitations=("Evidence is limited to retrieved page-level PDF chunks from: seeded fixture corpus.",),
        report_text=(
            "# 地理智能体在路径规划上有哪些进展？\n\n"
            "## 回答摘要\n\n"
            "地理智能体把空间量测与跨源推理结合在统一管线中 [1]。\n"
        ),
    )


class ChatFixtureOrchestrator(CompositeOrchestrator):
    """Route chat submissions of verified kinds into the offline fixture."""

    def submit(self, submission: TaskSubmission):
        if submission.task_kind in _VERIFIED_KINDS:
            objective = str(submission.input.get("objective", "")).strip()
            submission = replace(
                submission,
                task_kind=FIXTURE_TASK_KIND,
                input={"objective": objective},
                requested_agent=None,
            )
        return super().submit(submission)


def main() -> None:
    store = LocalArtifactStore(ROOT / "artifacts")
    repository = SQLiteRuntimeRepository(ROOT / "db" / "fixture.sqlite3", store)
    workspace = LocalWorkspaceAdapter(
        ROOT / "workspace",
        Path("src/conflux_weave/system"),
        store,
    )
    fixture = ResearchFixtureRuntime(repository, store, workspace)
    seed_thread_history(repository, store)
    if not DOTENV_PATH.exists():
        DOTENV_PATH.parent.mkdir(parents=True, exist_ok=True)
        DOTENV_PATH.write_text(
            "# UX-1 fixture provider configuration\n"
            "CONFLUX_WEAVE_PROVIDER_BASE_URL=https://127.0.0.1:1/v1\n"
            "CONFLUX_WEAVE_PROVIDER_API_KEY=sk-fixture-abcdef\n"
            "CONFLUX_WEAVE_PROVIDER_MODEL=qwen3.7-flash\n",
            encoding="utf-8",
        )
    app = create_app(
        repository,
        ChatFixtureOrchestrator(repository, (fixture,)),
        provider_configured=False,
        dotenv_path=DOTENV_PATH,
        config_paths={
            "database": str(ROOT / "db" / "fixture.sqlite3"),
            "artifact_root": str(ROOT / "artifacts"),
            "workspace_root": str(ROOT / "workspace"),
            "corpus_manifest": "未导入",
            "lancedb_root": "未导入",
            "dotenv": str(DOTENV_PATH),
        },
    )
    uvicorn.run(app, host="127.0.0.1", port=8767, workers=1)


if __name__ == "__main__":
    main()
