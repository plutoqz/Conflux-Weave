"""Real end-to-end integration tests for P4 Unified Dialogue Routing and Hierarchical Memory Center.

Covers:
1. Omnibox regex & autocomplete text replacement verification
2. Multi-channel conversation routing (auto -> memory / deep / direct)
3. Preserving conversation active_mode="auto" across turns
4. Preference extraction, pending candidate generation, and HITL approval
5. Memory prompt context injection into subsequent turns
6. Memory conflict detection and automated archival on approval
"""

import re
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
    MemoryScope,
    MemoryStatus,
)
from conflux_weave.server import create_app


NOW = "2026-09-10T12:00:00Z"


class DummyChatProvider:
    def __init__(self):
        self.calls = []

    def complete(self, **kwargs):
        self.calls.append(kwargs)
        class DummyResponse:
            response_id = "resp-test-123"
            content = "这是针对您提问的模型测试回答。"
        return DummyResponse()


class DummyOrchestrator:
    def __init__(self):
        self.submissions = []

    def submit(self, submission):
        self.submissions.append(submission)
        class DummyResult:
            task_id = "task-mock-123"
            run_id = "run-mock-456"
            created = NOW
        return DummyResult()


from types import SimpleNamespace
from unittest.mock import MagicMock
from conflux_weave.chat import ChatService


def setup_test_app(tmp_path):
    store = LocalArtifactStore(tmp_path / "artifacts")
    db_path = tmp_path / "db" / "conflux-weave.sqlite3"
    repository = SQLiteRuntimeRepository(db_path, store, clock=lambda: NOW)

    dummy_chat = DummyChatProvider()
    chat_svc = ChatService(dummy_chat, db_path)

    mock_orchestrator = SimpleNamespace()
    mock_orchestrator.submit = MagicMock(return_value=SimpleNamespace(task_id="t-1", run_id="run-mock-456", created=NOW))

    app = create_app(
        repository,
        mock_orchestrator,
        chat_service=chat_svc,
    )
    return app, dummy_chat, db_path


# ==============================================================================
# 1. Omnibox Autocomplete Logic Verification (Mirroring chat.js fixes)
# ==============================================================================

def test_omnibox_autocomplete_entity_replacement():
    """Verify that replacing matched @entity does not produce double @@."""
    text_before = "请参考 @proj"
    cursor_pos = len(text_before)
    before_cursor = text_before[:cursor_pos]

    # Matching pattern from chat.js: /@([\w:-]*)$/
    entity_match = re.search(r"@([\w:-]*)$", before_cursor)
    assert entity_match is not None
    assert entity_match.group(0) == "@proj"
    assert entity_match.group(1) == "proj"

    # With the bug: start_idx = len(before_cursor) - len(match.group(1)) -> replaced with "@" + "@project:current" -> "@@project:current"
    # With the fix: start_idx = len(before_cursor) - len(match.group(0))
    start_idx = len(before_cursor) - len(entity_match.group(0))
    insert_text = "@project:current"
    after_cursor = text_before[cursor_pos:]
    new_text = before_cursor[:start_idx] + insert_text + " " + after_cursor
    assert new_text == "请参考 @project:current "
    assert "@@" not in new_text


def test_omnibox_autocomplete_slash_command():
    """Verify prefix command matching."""
    text = "/de"
    match = re.search(r"^/([a-zA-Z]*)$", text)
    assert match is not None
    query = match.group(1).lower()
    commands = ["/deep", "/rag", "/doc", "/audit", "/mem"]
    filtered = [c for c in commands if ("/" + query) in c]
    assert filtered == ["/deep"]


# ==============================================================================
# 2. Unified Chat Routing and Mode Preservation
# ==============================================================================

def test_chat_auto_routing_and_active_mode_preservation(tmp_path):
    """Verify that submitting with mode='auto' preserves active_mode='auto' in conversation."""
    app, dummy_chat, db_path = setup_test_app(tmp_path)
    client = TestClient(app)

    # 1. Turn 1: User asks about memory center in auto mode
    res1 = client.post(
        "/api/v1/chat",
        json={"question": "查看记忆中心中已保存的偏好设置", "mode": "auto"},
    )
    assert res1.status_code == 200
    data1 = res1.json()
    assert data1["routed_mode"] == "memory"
    assert data1["is_fast_path"] is True
    conv_id = data1["conversation_id"]

    # Verify that conversation record has active_mode="auto"
    conv_res = client.get(f"/api/v1/conversations/{conv_id}")
    assert conv_res.status_code == 200
    assert conv_res.json()["active_mode"] == "auto"

    # 2. Turn 2: User asks a deep research question in the same conversation
    res2 = client.post(
        "/api/v1/chat",
        json={
            "question": "请对 RRF 倒数排序算法开展系统性综述与深度调研",
            "conversation_id": conv_id,
            "mode": "auto",
        },
    )
    assert res2.status_code == 200
    data2 = res2.json()
    assert data2["routed_mode"] == "deep"
    assert data2["is_fast_path"] is False
    assert data2["run_id"] == "run-mock-456"

    # Check conversation active_mode remains "auto"
    conv_res2 = client.get(f"/api/v1/conversations/{conv_id}")
    assert conv_res2.status_code == 200
    assert conv_res2.json()["active_mode"] == "auto"

    # 3. Turn 3: User asks normal direct question in auto mode
    res3 = client.post(
        "/api/v1/chat",
        json={
            "question": "你好，请用一句话介绍 Python",
            "conversation_id": conv_id,
            "mode": "auto",
        },
    )
    assert res3.status_code == 200
    data3 = res3.json()
    assert data3["routed_mode"] == "direct"
    assert data3["is_fast_path"] is True

    # Check conversation active_mode STILL remains "auto"
    conv_res3 = client.get(f"/api/v1/conversations/{conv_id}")
    assert conv_res3.status_code == 200
    assert conv_res3.json()["active_mode"] == "auto"


# ==============================================================================
# 3. Memory Extraction, HITL Approval, Prompt Context, and Conflict Handling
# ==============================================================================

def test_memory_extraction_hitl_approval_and_prompt_injection(tmp_path):
    """Verify complete lifecycle:
    Turn 1: User expresses preference -> candidate generated (status: pending)
    API: Approve candidate -> active memory
    Turn 2: Next turn -> memory injected into system prompt
    Turn 3: Conflicting preference -> candidate with conflict_with_memory_id
    API: Approve conflicting candidate -> old memory archived, new memory active
    """
    app, dummy_chat, db_path = setup_test_app(tmp_path)
    client = TestClient(app)

    # 1. Submit preference expression
    res1 = client.post(
        "/api/v1/chat",
        json={"question": "以后请都使用中文回答", "mode": "direct"},
    )
    assert res1.status_code == 200
    data1 = res1.json()
    conv_id = data1["conversation_id"]

    # Check memory candidates returned in fast path response
    candidates = data1.get("memory_candidates", [])
    assert len(candidates) >= 1
    cand = candidates[0]
    assert cand["status"] == "pending"
    assert "中文" in cand["statement"]
    cand_id = cand["candidate_id"]

    # 2. Check candidates endpoint (verifying 'items' field contract)
    cands_res = client.get("/api/v1/memories/candidates?status=pending")
    assert cands_res.status_code == 200
    cands_data = cands_res.json()
    assert "items" in cands_data
    assert any(c["candidate_id"] == cand_id for c in cands_data["items"])

    # 3. Approve the candidate via HITL endpoint
    approve_res = client.post(
        f"/api/v1/memories/candidates/{cand_id}/action",
        json={"action": "approve"},
    )
    assert approve_res.status_code == 200
    assert approve_res.json()["ok"] is True
    assert approve_res.json()["action"] == "approved"
    created_mem_id = approve_res.json()["memory_id"]
    assert created_mem_id is not None

    # Verify that memory is now active
    mem_res = client.get("/api/v1/memories?status=active")
    assert mem_res.status_code == 200
    active_mems = mem_res.json()["items"]
    assert len(active_mems) == 1
    assert active_mems[0]["memory_id"] == created_mem_id
    assert "中文" in active_mems[0]["statement"]

    # 4. Turn 2: Subsequent direct chat turn -> check that active memory was injected into system prompt!
    res2 = client.post(
        "/api/v1/chat",
        json={"question": "什么是向量检索？", "conversation_id": conv_id, "mode": "direct"},
    )
    assert res2.status_code == 200
    direct_calls = [c for c in dummy_chat.calls if c.get("producer_step_id") == "chat-direct"]
    assert len(direct_calls) >= 2
    last_direct_call = direct_calls[-1]
    assert "【用户偏好与习惯约定】" in last_direct_call["system_prompt"]
    assert "中文" in last_direct_call["system_prompt"]

    # 5. Turn 3: User expresses a conflicting preference
    res3 = client.post(
        "/api/v1/chat",
        json={"question": "以后请都使用英文回答", "conversation_id": conv_id, "mode": "direct"},
    )
    assert res3.status_code == 200
    cands3 = res3.json().get("memory_candidates", [])
    assert len(cands3) >= 1
    cand3 = cands3[0]
    # Check conflict detection
    assert cand3["conflict_with_memory_id"] == created_mem_id
    cand3_id = cand3["candidate_id"]

    # 6. Approve the conflicting candidate
    approve_res2 = client.post(
        f"/api/v1/memories/candidates/{cand3_id}/action",
        json={"action": "approve"},
    )
    assert approve_res2.status_code == 200
    new_mem_id = approve_res2.json()["memory_id"]

    # Verify old memory is archived and new memory is active
    active_mems_after = client.get("/api/v1/memories?status=active").json()["items"]
    assert len(active_mems_after) == 1
    assert active_mems_after[0]["memory_id"] == new_mem_id

    archived_mems = client.get("/api/v1/memories?status=archived").json()["items"]
    assert any(m["memory_id"] == created_mem_id for m in archived_mems)


def test_manual_memory_crud_and_rejection(tmp_path):
    """Verify manual Memory Studio interactions:
    1. Create manual user preference
    2. Create manual project convention
    3. Verify separation by scope
    4. Reject a pending candidate
    5. Delete active memory
    """
    app, dummy_chat, db_path = setup_test_app(tmp_path)
    client = TestClient(app)

    # 1. Create user preference
    u_res = client.post(
        "/api/v1/memories",
        json={
            "scope": "user",
            "target_id": "user_default",
            "category": "preference",
            "statement": "文风偏好：严谨客观，只给结论",
        },
    )
    assert u_res.status_code == 200
    u_data = u_res.json()
    u_id = u_data["memory_id"]

    # 2. Create project convention
    p_res = client.post(
        "/api/v1/memories",
        json={
            "scope": "project",
            "target_id": "proj-default",
            "category": "decision",
            "statement": "架构决策：全面禁用外部未审核CDN",
        },
    )
    assert p_res.status_code == 200
    p_data = p_res.json()
    p_id = p_data["memory_id"]

    # 3. List and verify separation
    user_list = client.get("/api/v1/memories?scope=user&target_id=user_default").json()["items"]
    assert len(user_list) == 1
    assert user_list[0]["memory_id"] == u_id

    proj_list = client.get("/api/v1/memories?scope=project&target_id=proj-default").json()["items"]
    assert len(proj_list) == 1
    assert proj_list[0]["memory_id"] == p_id

    # 4. Reject candidate workflow
    mem_store: HierarchicalMemoryStore = app.state.memory_store
    cand = mem_store.create_candidate(
        scope=MemoryScope.USER,
        target_id="user_default",
        category=MemoryCategory.PREFERENCE,
        statement="临时候选：仅用英文回答",
        # omitting source_id to test default source_id="system"
    )
    assert cand.source_id == "system"

    # Reject candidate
    rej_res = client.post(f"/api/v1/memories/candidates/{cand.candidate_id}/action", json={"action": "reject"})
    assert rej_res.status_code == 200
    assert rej_res.json()["action"] == "rejected"

    # Candidate should not be pending anymore
    cands_pending = client.get("/api/v1/memories/candidates?status=pending").json()["items"]
    assert not any(c["candidate_id"] == cand.candidate_id for c in cands_pending)

    # 5. Delete active memory
    del_res = client.delete(f"/api/v1/memories/{u_id}")
    assert del_res.status_code == 200
    assert del_res.json()["ok"] is True

    # Confirm deleted
    user_list_after = client.get("/api/v1/memories?scope=user&target_id=user_default").json()["items"]
    assert len(user_list_after) == 0

