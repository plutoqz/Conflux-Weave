"""Comprehensive test suite for P4.1 Hierarchical Memory Center and MemoryAgent."""

import sqlite3
import pytest
from starlette.testclient import TestClient

from conflux_weave.core import (
    BudgetLedger,
    RunRecord,
    RunStatus,
    StepRecord,
    StepStatus,
    TaskSpec,
)
from conflux_weave.harness.orchestration import (
    CompositeOrchestrator,
    UnavailableTaskRuntime,
)
from conflux_weave.memory_agent import MemoryAgent
from conflux_weave.runtime import (
    LocalArtifactStore,
    SQLiteRuntimeRepository,
)
from conflux_weave.runtime.memory_store import (
    CandidateStatus,
    HierarchicalMemoryStore,
    MemoryCategory,
    MemoryItem,
    MemoryScope,
    MemoryStatus,
)
from conflux_weave.server import create_app


NOW = "2026-09-10T12:00:00Z"


def build_test_repository(tmp_path):
    store = LocalArtifactStore(tmp_path / "artifacts")
    db_path = tmp_path / "db" / "conflux-weave.sqlite3"
    repository = SQLiteRuntimeRepository(db_path, store, clock=lambda: NOW)
    return repository, store, db_path


# ==============================================================================
# 1. HierarchicalMemoryStore Tests
# ==============================================================================

def test_memory_store_three_layer_isolation_and_crud(tmp_path):
    _, _, db_path = build_test_repository(tmp_path)
    store = HierarchicalMemoryStore(db_path)

    # 1. User memory
    user_mem = store.create_memory(
        scope=MemoryScope.USER,
        target_id="user_alice",
        category=MemoryCategory.PREFERENCE,
        statement="文风偏好：严谨客观，输出中文学术术语",
        confidence=0.95,
        source_type="manual",
        source_id="user",
    )
    assert user_mem.memory_id.startswith("mem-")
    assert user_mem.scope == MemoryScope.USER
    assert user_mem.status == MemoryStatus.ACTIVE

    # 2. Project memory
    proj_mem = store.create_memory(
        scope=MemoryScope.PROJECT,
        target_id="proj-conflux-weave",
        category=MemoryCategory.DECISION,
        statement="项目技术约定：统一采用 RRF 倒数排序融合机制",
        confidence=1.0,
        source_type="manual",
        source_id="user",
    )
    assert proj_mem.scope == MemoryScope.PROJECT
    assert proj_mem.target_id == "proj-conflux-weave"

    # 3. Session memory
    sess_mem = store.create_memory(
        scope=MemoryScope.SESSION,
        target_id="conv-123",
        category=MemoryCategory.FACT,
        statement="会话约束：本次仅讨论 Section 3 检索机制",
        confidence=0.9,
        source_type="conversation",
        source_id="conv-123",
    )
    assert sess_mem.scope == MemoryScope.SESSION

    # 4. Filter by scope & target
    alice_mems = store.list_memories(scope=MemoryScope.USER, target_id="user_alice")
    assert len(alice_mems) == 1
    assert alice_mems[0].memory_id == user_mem.memory_id

    bob_mems = store.list_memories(scope=MemoryScope.USER, target_id="user_bob")
    assert len(bob_mems) == 0

    proj_mems = store.list_memories(scope=MemoryScope.PROJECT, target_id="proj-conflux-weave")
    assert len(proj_mems) == 1

    # 5. Update & archive
    updated = store.update_memory(user_mem.memory_id, statement="文风偏好：严谨客观，优先给出代码行号")
    assert updated is not None
    assert updated.statement == "文风偏好：严谨客观，优先给出代码行号"

    archived = store.archive_memory(sess_mem.memory_id)
    assert archived is True
    active_sess = store.list_memories(scope=MemoryScope.SESSION, target_id="conv-123", status=MemoryStatus.ACTIVE)
    assert len(active_sess) == 0
    all_sess = store.list_memories(scope=MemoryScope.SESSION, target_id="conv-123", status=MemoryStatus.ARCHIVED)
    assert len(all_sess) == 1

    # 6. Delete
    deleted = store.delete_memory(proj_mem.memory_id)
    assert deleted is True
    assert store.get_memory(proj_mem.memory_id) is None


def test_memory_store_dedup_and_conflict_detection(tmp_path):
    _, _, db_path = build_test_repository(tmp_path)
    store = HierarchicalMemoryStore(db_path)

    store.create_memory(
        scope=MemoryScope.USER,
        target_id="user_default",
        category=MemoryCategory.PREFERENCE,
        statement="语言偏好：仅用中文回答",
    )

    # 1. Exact Duplicate detection (whitespace / punctuation normalized)
    dup_id, conflict_id = store.find_conflicts_and_duplicates(
        MemoryScope.USER,
        "user_default",
        MemoryCategory.PREFERENCE,
        "语言偏好：仅用中文回答！",
    )
    assert dup_id is not None
    assert conflict_id is None

    # 2. Semantic opposing conflict detection ("中文" vs "英文")
    dup_id, conflict_id = store.find_conflicts_and_duplicates(
        MemoryScope.USER,
        "user_default",
        MemoryCategory.PREFERENCE,
        "语言偏好：仅用英文回答",
    )
    assert dup_id is None
    assert conflict_id is not None


def test_memory_store_candidate_hitl_approval_and_conflict_archiving(tmp_path):
    _, _, db_path = build_test_repository(tmp_path)
    store = HierarchicalMemoryStore(db_path)

    # Initial memory: Chinese only
    initial_mem = store.create_memory(
        scope=MemoryScope.USER,
        target_id="user_default",
        category=MemoryCategory.PREFERENCE,
        statement="语言偏好：仅用中文回答",
    )

    # New candidate: English only (conflicts with initial_mem)
    cand = store.create_candidate(
        scope=MemoryScope.USER,
        target_id="user_default",
        category=MemoryCategory.PREFERENCE,
        statement="语言偏好：仅用英文回答",
        confidence=0.9,
        source_type="conversation",
        source_id="conv-999",
        conflict_with_memory_id=initial_mem.memory_id,
    )
    assert cand.status == CandidateStatus.PENDING
    assert cand.conflict_with_memory_id == initial_mem.memory_id

    # List candidates
    pending = store.list_candidates(status=CandidateStatus.PENDING, scope=MemoryScope.USER)
    assert len(pending) == 1
    assert pending[0].candidate_id == cand.candidate_id

    # Approve candidate (HITL)
    new_mem = store.approve_candidate(cand.candidate_id)
    assert new_mem.statement == "语言偏好：仅用英文回答"
    assert new_mem.status == MemoryStatus.ACTIVE

    # Check that initial_mem was automatically archived due to conflict resolution
    old_mem = store.get_memory(initial_mem.memory_id)
    assert old_mem is not None
    assert old_mem.status == MemoryStatus.ARCHIVED

    # Check candidate status
    updated_cand = store.get_candidate(cand.candidate_id)
    assert updated_cand.status == CandidateStatus.APPROVED

    # Check active memories list
    active_mems = store.list_memories(scope=MemoryScope.USER, target_id="user_default", status=MemoryStatus.ACTIVE)
    assert len(active_mems) == 1
    assert active_mems[0].statement == "语言偏好：仅用英文回答"


def test_memory_store_candidate_rejection(tmp_path):
    _, _, db_path = build_test_repository(tmp_path)
    store = HierarchicalMemoryStore(db_path)

    cand = store.create_candidate(
        scope=MemoryScope.USER,
        target_id="user_default",
        category=MemoryCategory.PREFERENCE,
        statement="临时偏好：不要任何格式",
        source_type="conversation",
        source_id="conv-111",
    )

    rejected = store.reject_candidate(cand.candidate_id)
    assert rejected is True

    updated_cand = store.get_candidate(cand.candidate_id)
    assert updated_cand.status == CandidateStatus.REJECTED

    # Should not appear in pending list
    pending = store.list_candidates(status=CandidateStatus.PENDING)
    assert len(pending) == 0


# ==============================================================================
# 2. MemoryAgent Tests
# ==============================================================================

def test_memory_agent_heuristic_extraction(tmp_path):
    _, _, db_path = build_test_repository(tmp_path)
    store = HierarchicalMemoryStore(db_path)
    agent = MemoryAgent(store)

    # 1. User preference detection
    input_text = "以后回答请保持客观严谨，优先给出代码行号"
    candidates = agent.extract_heuristics(input_text, user_id="user_alice")
    assert len(candidates) >= 1
    stmts = [c.statement for c in candidates]
    assert any("客观" in s or "严谨" in s for s in stmts)

    # 2. Project convention detection
    proj_text = "本项目统一采用 RRF 倒数排序融合机制"
    proj_cands = agent.extract_heuristics(proj_text, user_id="user_alice", project_id="proj-conflux")
    assert len(proj_cands) >= 1
    assert any("RRF" in c.statement for c in proj_cands)

    # 3. Irrelevant / ordinary question -> no candidates extracted
    normal_q = "请问 Transformer 的多头注意力机制是怎么计算的？"
    normal_cands = agent.extract_heuristics(normal_q, user_id="user_alice", project_id="proj-conflux")
    assert len(normal_cands) == 0


def test_memory_agent_context_formatting_and_budget_control(tmp_path):
    _, _, db_path = build_test_repository(tmp_path)
    store = HierarchicalMemoryStore(db_path)
    agent = MemoryAgent(store)

    store.create_memory(
        scope=MemoryScope.USER,
        target_id="user_alice",
        category=MemoryCategory.PREFERENCE,
        statement="文风偏好：严谨客观，避免夸大",
    )
    store.create_memory(
        scope=MemoryScope.PROJECT,
        target_id="proj-weave",
        category=MemoryCategory.DECISION,
        statement="架构决策：使用 SQLite 权威存储",
    )
    store.create_memory(
        scope=MemoryScope.SESSION,
        target_id="conv-888",
        category=MemoryCategory.FACT,
        statement="当前关注：测试用例编写",
    )

    # Format context with all three scopes
    context_block = agent.format_prompt_context(
        user_id="user_alice",
        project_id="proj-weave",
        conversation_id="conv-888",
        max_chars=1200,
    )
    assert "【用户偏好与习惯约定】" in context_block
    assert "严谨客观" in context_block
    assert "【项目架构与技术约定】" in context_block
    assert "SQLite 权威存储" in context_block
    assert "【会话当前事实与约束】" in context_block
    assert "测试用例编写" in context_block

    # Scope filtering: if project_id is None, project memories are omitted
    context_user_only = agent.format_prompt_context(
        user_id="user_alice",
        project_id=None,
        conversation_id=None,
    )
    assert "【项目架构与技术约定】" not in context_user_only
    assert "严谨客观" in context_user_only

    # Strict token / character budget truncation
    short_block = agent.format_prompt_context(
        user_id="user_alice",
        project_id="proj-weave",
        max_chars=40,
    )
    assert len(short_block) <= 80
    assert "已截断超出预算的记忆" in short_block


# ==============================================================================
# 3. FastAPI REST Endpoints Tests
# ==============================================================================

def test_memory_rest_api_endpoints(tmp_path):
    repository, store, db_path = build_test_repository(tmp_path)
    dummy_runtime = UnavailableTaskRuntime(
        repository,
        executor_id="dummy",
        task_kinds=("paper_discovery",),
        message="dummy",
    )
    orchestrator = CompositeOrchestrator(repository, (dummy_runtime,))

    app = create_app(repository, orchestrator)
    client = TestClient(app)

    # 1. Initially empty memories
    res = client.get("/api/v1/memories")
    assert res.status_code == 200
    data = res.json()
    assert data["total"] == 0
    assert data["items"] == []

    # 2. Create memory via POST
    create_res = client.post(
        "/api/v1/memories",
        json={
            "scope": "user",
            "target_id": "user_default",
            "category": "preference",
            "statement": "语言偏好：统一使用中文学术术语",
            "confidence": 0.95,
        },
    )
    assert create_res.status_code == 200
    created_item = create_res.json()
    assert created_item["memory_id"].startswith("mem-")
    assert created_item["statement"] == "语言偏好：统一使用中文学术术语"
    assert created_item["status"] == "active"

    # 3. List memories with scope filter
    list_res = client.get("/api/v1/memories?scope=user&target_id=user_default")
    assert list_res.status_code == 200
    assert list_res.json()["total"] == 1

    # 4. Candidates endpoint: manually create candidate in store
    mem_store: HierarchicalMemoryStore = app.state.memory_store
    cand = mem_store.create_candidate(
        scope=MemoryScope.USER,
        target_id="user_default",
        category=MemoryCategory.PREFERENCE,
        statement="排版偏好：采用简洁无衬线字体",
        source_id="conv-api-test",
    )

    cands_res = client.get("/api/v1/memories/candidates?status=pending")
    assert cands_res.status_code == 200
    cand_data = cands_res.json()
    assert cand_data["total"] == 1
    assert cand_data["items"][0]["candidate_id"] == cand.candidate_id

    # 5. Approve candidate via action endpoint
    action_res = client.post(
        f"/api/v1/memories/candidates/{cand.candidate_id}/action",
        json={"action": "approve"},
    )
    assert action_res.status_code == 200
    assert action_res.json()["ok"] is True
    assert action_res.json()["action"] == "approved"

    # Verify that memory is now in active list
    list_after_res = client.get("/api/v1/memories?scope=user&target_id=user_default")
    assert list_after_res.json()["total"] == 2

    # 6. Reject another candidate
    cand_reject = mem_store.create_candidate(
        scope=MemoryScope.USER,
        target_id="user_default",
        category=MemoryCategory.PREFERENCE,
        statement="临时偏好：快速回答",
        source_id="conv-api-test",
    )
    reject_res = client.post(
        f"/api/v1/memories/candidates/{cand_reject.candidate_id}/action",
        json={"action": "reject"},
    )
    assert reject_res.status_code == 200
    assert reject_res.json()["action"] == "rejected"

    # 7. Delete memory
    del_res = client.delete(f"/api/v1/memories/{created_item['memory_id']}")
    assert del_res.status_code == 200
    assert del_res.json()["ok"] is True

    # Check list after delete
    list_del_res = client.get("/api/v1/memories?scope=user&target_id=user_default")
    assert list_del_res.json()["total"] == 1
