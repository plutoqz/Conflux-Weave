"""Tests for P7-B3: Procedural Memory Extraction and Eligibility Governance.

Covers:
1. Strict eligibility gates (run status == succeeded, user_marked_useful, complete delivery, valid report artifact, evidence cards)
2. Ineligibility rejection with explicit reasons and missing criteria (HTTP 422)
3. Procedural candidate staging (status == pending, source_type='procedural_run', source_id=run_id)
4. Human-in-the-Loop (HITL) approval workflow
5. Deduplication and conflict handling
6. FastAPI REST endpoint: POST /api/v1/memories/procedural/extract
"""

from __future__ import annotations

import json
from pathlib import Path
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
from conflux_weave.runtime.artifacts import LocalArtifactStore
from conflux_weave.runtime.memory_store import (
    CandidateStatus,
    HierarchicalMemoryStore,
    MemoryCategory,
    MemoryScope,
    MemoryStatus,
)
from conflux_weave.runtime.sqlite import SQLiteRuntimeRepository
from conflux_weave.procedural_memory import (
    ProceduralEligibilityCheckResult,
    ProceduralMemoryExtractor,
    check_procedural_eligibility,
)
from conflux_weave.server import create_app


NOW = "2026-10-06T12:00:00Z"


def _setup_test_env(tmp_path: Path):
    db_path = tmp_path / "runtime.sqlite3"
    artifact_store = LocalArtifactStore(tmp_path / "artifacts")
    repository = SQLiteRuntimeRepository(db_path, artifact_store, clock=lambda: NOW)
    memory_store = HierarchicalMemoryStore(tmp_path / "memory.sqlite3")
    return db_path, repository, artifact_store, memory_store


def _create_mock_run(
    repository: SQLiteRuntimeRepository,
    artifact_store: LocalArtifactStore,
    *,
    run_id: str = "run-proc-1",
    status: RunStatus = RunStatus.SUCCEEDED,
    disposition: DeliveryDisposition = DeliveryDisposition.COMPLETE,
    has_delivery: bool = True,
    has_report: bool = True,
    evidence_count: int = 2,
    query: str = "深度研究 Agent 架构评估",
):
    task_id = f"task-{run_id}"
    task = TaskSpec(task_id, "deep_research", {"query": query}, "v0.3", f"idem-{run_id}")
    run = RunRecord(
        run_id,
        task_id,
        RunStatus.ACCEPTED,
        "v0.3",
        "cfg-ref",
        BudgetLedger(30, 0, 0, "0", 1, 0, 1),
        NOW,
        NOW,
    )
    step = StepRecord(f"step-{run_id}-publish", run_id, "publish_delivery", 1, StepStatus.PENDING)
    repository.submit_task(task, run, (step,))
    repository.transition_run(run.run_id, RunStatus.QUEUED, updated_at=NOW)

    if not has_delivery:
        if status != RunStatus.QUEUED:
            conn = repository._connect()
            try:
                conn.execute("UPDATE runs SET status = ? WHERE run_id = ?", (status.value, run_id))
                conn.commit()
            finally:
                conn.close()
        return run_id

    publish_claim = repository.claim_next_step("worker", lease_seconds=60, now=NOW)
    assert publish_claim is not None

    report_content = f"# {query}\n\n## 核心结论\n根据核验，架构采用事件总线与 SQLite lease 可靠性最高。".encode("utf-8")
    report = artifact_store.put_bytes(
        report_content,
        media_type="text/markdown",
        producer_step_id=publish_claim.step_id,
        schema_version="report-v1",
    )

    evidence_refs = tuple(f"ev-{i}" for i in range(evidence_count))
    unmet = ("部分目标未完成",) if disposition == DeliveryDisposition.PARTIAL else ()

    delivery = DeliveryRecord(
        run_id=run.run_id,
        disposition=disposition,
        artifact_refs=(report.artifact_id,),
        evidence_refs=evidence_refs,
        unmet_criteria=unmet,
    )
    target_status = RunStatus.PARTIAL if disposition == DeliveryDisposition.PARTIAL else RunStatus.SUCCEEDED
    repository.publish_delivery(
        run.run_id,
        target_status,
        delivery,
        (report,),
        claim=publish_claim,
        published_at=NOW,
    )

    if not has_report:
        # Corrupt / delete artifact on disk after publication to test corrupted artifact in store
        digest = report.artifact_id.removeprefix("artifact-sha256-")
        art_path = artifact_store.path_for_digest(digest)
        if art_path.exists():
            art_path.unlink()
    if status != target_status:
        conn = repository._connect()
        try:
            conn.execute("UPDATE runs SET status = ? WHERE run_id = ?", (status.value, run_id))
            conn.commit()
        finally:
            conn.close()

    return run_id


def test_procedural_eligibility_rejections(tmp_path: Path):
    """验证当 Run 未满足全部门禁时，严格拒绝并给出具体缺失项。"""
    _, repository, artifact_store, _ = _setup_test_env(tmp_path)

    # 1. Run 状态为 failed
    run_failed = _create_mock_run(repository, artifact_store, run_id="run-failed", status=RunStatus.FAILED)
    check = check_procedural_eligibility(run_failed, repository, artifact_store, user_marked_useful=True)
    assert check.eligible is False
    assert "run_status_succeeded" in check.missing_criteria

    # 2. 未经用户明确标记有用 (user_marked_useful=False)
    run_ok = _create_mock_run(repository, artifact_store, run_id="run-ok", status=RunStatus.SUCCEEDED)
    check_no_feedback = check_procedural_eligibility(run_ok, repository, artifact_store, user_marked_useful=False)
    assert check_no_feedback.eligible is False
    assert "user_explicit_feedback" in check_no_feedback.missing_criteria

    # 3. Delivery disposition 为 partial (部分失败)
    run_partial = _create_mock_run(
        repository,
        artifact_store,
        run_id="run-partial",
        status=RunStatus.SUCCEEDED,
        disposition=DeliveryDisposition.PARTIAL,
    )
    check_partial = check_procedural_eligibility(run_partial, repository, artifact_store, user_marked_useful=True)
    assert check_partial.eligible is False
    assert "delivery_disposition_complete" in check_partial.missing_criteria

    # 4. 缺少关键报告 Artifact
    run_no_art = _create_mock_run(
        repository,
        artifact_store,
        run_id="run-no-art",
        status=RunStatus.SUCCEEDED,
        has_report=False,
    )
    check_no_art = check_procedural_eligibility(run_no_art, repository, artifact_store, user_marked_useful=True)
    assert check_no_art.eligible is False
    assert "valid_report_artifact" in check_no_art.missing_criteria

    # 5. 证据卡为 0
    run_no_ev = _create_mock_run(
        repository,
        artifact_store,
        run_id="run-no-ev",
        status=RunStatus.SUCCEEDED,
        evidence_count=0,
    )
    check_no_ev = check_procedural_eligibility(run_no_ev, repository, artifact_store, user_marked_useful=True)
    assert check_no_ev.eligible is False
    assert "complete_evidence_cards" in check_no_ev.missing_criteria


def test_procedural_candidate_staging_and_hitl_approval(tmp_path: Path):
    """验证符合条件的 Run 成功提取出候选，处于 pending 状态，并在 HITL 审批后转正。"""
    _, repository, artifact_store, memory_store = _setup_test_env(tmp_path)
    run_id = _create_mock_run(repository, artifact_store, run_id="run-eligible-1", query="多模态 RAG 检索管线设计")

    extractor = ProceduralMemoryExtractor(
        repository=repository,
        artifact_store=artifact_store,
        memory_store=memory_store,
    )

    check, candidates = extractor.extract_candidates_from_run(
        run_id=run_id,
        project_id="proj-rag",
        user_marked_useful=True,
    )

    assert check.eligible is True
    assert len(candidates) >= 1

    # 验证候选属性符合规约
    cand = candidates[0]
    assert cand.status == CandidateStatus.PENDING  # 严禁直接自动转正，必须为 pending
    assert cand.source_type == "procedural_run"
    assert cand.source_id == run_id
    assert cand.scope == MemoryScope.PROJECT
    assert cand.target_id == "proj-rag"
    assert cand.category == MemoryCategory.DECISION
    assert "多模态 RAG 检索管线设计" in cand.statement

    # 此时 active 记忆中应当为空
    active_mems = memory_store.list_memories(scope=MemoryScope.PROJECT, target_id="proj-rag")
    assert len(active_mems) == 0

    # 执行 HITL 核准
    approved_item = memory_store.approve_candidate(cand.candidate_id)
    assert approved_item.status == MemoryStatus.ACTIVE
    assert approved_item.source_type == "procedural_run"
    assert approved_item.source_id == run_id

    # 验证 active 记忆已正式收录
    reloaded_mems = memory_store.list_memories(scope=MemoryScope.PROJECT, target_id="proj-rag")
    assert len(reloaded_mems) == 1
    assert reloaded_mems[0].statement == cand.statement

    # 再次对同一 Run 提取：已有同内容 active 记忆，排重机制生效不重复创建候选
    check2, candidates2 = extractor.extract_candidates_from_run(
        run_id=run_id,
        project_id="proj-rag",
        user_marked_useful=True,
    )
    assert check2.eligible is True
    # 相同陈述已在库，跳过创建
    assert all(c.statement != approved_item.statement for c in candidates2)


def test_procedural_extract_rest_api(tmp_path: Path):
    """验证 POST /api/v1/memories/procedural/extract 端点的成功与 422 校验分支。"""
    db_path, repository, artifact_store, memory_store = _setup_test_env(tmp_path)
    run_succeeded = _create_mock_run(repository, artifact_store, run_id="run-api-ok", query="长上下文推理评估")
    run_failed = _create_mock_run(repository, artifact_store, run_id="run-api-fail", status=RunStatus.FAILED)

    from conflux_weave.harness.orchestration import CompositeOrchestrator, UnavailableTaskRuntime

    dummy_runtime = UnavailableTaskRuntime(
        repository,
        executor_id="dummy",
        task_kinds=("paper_discovery",),
        message="dummy",
    )
    orchestrator = CompositeOrchestrator(repository, (dummy_runtime,))

    app = create_app(
        repository,
        orchestrator,
        enable_worker=False,
    )
    client = TestClient(app)

    # 1. 针对失败 Run 提取 -> 422 Unprocessable Entity
    resp_fail = client.post(
        "/api/v1/memories/procedural/extract",
        json={"run_id": run_failed, "user_marked_useful": True},
    )
    assert resp_fail.status_code == 422
    data_fail = resp_fail.json()
    assert data_fail["eligible"] is False
    assert "run_status_succeeded" in data_fail["missing_criteria"]

    # 2. 针对未经用户反馈的 Run 提取 -> 422
    resp_no_fb = client.post(
        "/api/v1/memories/procedural/extract",
        json={"run_id": run_succeeded, "user_marked_useful": False},
    )
    assert resp_no_fb.status_code == 422
    data_no_fb = resp_no_fb.json()
    assert data_no_fb["eligible"] is False
    assert "user_explicit_feedback" in data_no_fb["missing_criteria"]

    # 3. 针对合格 Run 提取 -> 200 OK 且返回 pending 候选列表
    resp_ok = client.post(
        "/api/v1/memories/procedural/extract",
        json={
            "run_id": run_succeeded,
            "project_id": "proj-eval",
            "user_marked_useful": True,
        },
    )
    assert resp_ok.status_code == 200
    data_ok = resp_ok.json()
    assert data_ok["eligible"] is True
    assert len(data_ok["candidates"]) >= 1
    cid = data_ok["candidates"][0]["candidate_id"]
    assert data_ok["candidates"][0]["status"] == "pending"

    # 4. 通过现有 HITL 端点审批
    resp_approve = client.post(f"/api/v1/memories/candidates/{cid}/action", json={"action": "approve"})
    assert resp_approve.status_code == 200
    assert resp_approve.json()["ok"] is True
    assert resp_approve.json()["action"] == "approved"
