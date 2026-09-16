"""G2 Automated Test Suite: Navigation deep-links, task cancel/recovery continuity, memory candidates, and chat citations."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any
import pytest
from starlette.testclient import TestClient

from conflux_weave.api_contracts import (
    UserRunState,
    map_run_state,
)
from conflux_weave.core import (
    RunRecord,
    RunStatus,
    StepRecord,
    StepStatus,
    TaskSpec,
)
from conflux_weave.harness.contracts import TaskSubmission
from conflux_weave.harness.orchestration import (
    CompositeOrchestrator,
    RecoveryDecision,
)
from conflux_weave.harness.fixture_runtime import ResearchFixtureRuntime
from conflux_weave.runtime.artifacts import LocalArtifactStore
from conflux_weave.runtime import SQLiteRuntimeRepository
from conflux_weave.runtime.memory_store import (
    CandidateStatus,
    HierarchicalMemoryStore,
    MemoryCategory,
    MemoryScope,
    MemoryStatus,
)
from conflux_weave.server import WorkerLoop, create_app
from conflux_weave.chat import ChatService
from conflux_weave.provider import (
    OpenAICompatibleChatAdapter,
    ProviderConfig,
    ProviderHttpResponse,
)
from conflux_weave.global_search import GlobalSearchService


NOW = "2026-09-16T12:00:00Z"


class _SequenceChatTransport:
    def __init__(self, payloads):
        self.payloads = iter(payloads)
        self.requests = []

    def post(self, *args, **kwargs):
        self.requests.append(json.loads(kwargs.get("body", "{}")))
        payload = next(self.payloads)
        return ProviderHttpResponse(200, json.dumps(payload).encode(), {"Content-Type": "application/json"})


def _mock_chat_payload(content: str, response_id: str = "resp-001"):
    return {
        "id": response_id,
        "model": "fixture-chat",
        "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
    }


def _build_g2_test_environment(tmp_path: Path):
    store = LocalArtifactStore(tmp_path / "artifacts")
    repository = SQLiteRuntimeRepository(
        tmp_path / "db" / "runtime.sqlite3", store, clock=lambda: NOW
    )
    fixture_runtime = ResearchFixtureRuntime(repository, store, tmp_path / "workspace")
    orchestrator = CompositeOrchestrator(repository, (fixture_runtime,))
    payload = _mock_chat_payload("这是关于拓扑量子纠错的详细阐述。")
    transport = _SequenceChatTransport([payload])
    config = ProviderConfig("https://provider.example/v1", "secret", "chat")
    chat_adapter = OpenAICompatibleChatAdapter(store, config, transport=transport)
    chat_db_path = tmp_path / "db" / "chat.sqlite3"
    chat_db_path.parent.mkdir(parents=True, exist_ok=True)
    chat_service = ChatService(chat_adapter, chat_db_path, artifact_store=store)

    search_service = GlobalSearchService(repository.database_path)

    app = create_app(
        repository,
        orchestrator,
        provider_configured=True,
        chat_service=chat_service,
        search_service=search_service,
        worker=WorkerLoop(orchestrator, interval_seconds=10),
    )
    return app, repository, store, orchestrator, search_service


# =========================================================================
# 1. G2-A: 全局搜索深链生成格式一致性 (U11)
# =========================================================================

def test_g2_search_deep_links_format(tmp_path: Path) -> None:
    """U11: Deep links for all object types carry rich query parameters."""
    app, repository, store, orchestrator, search_service = _build_g2_test_environment(tmp_path)

    # 1. chat_message deep link
    link_chat = search_service._deep_link("chat_message", "msg-001", {"conversation_id": "conv-100"})
    assert link_chat == "#/chat?conversation_id=conv-100&message_id=msg-001"

    link_chat_no_conv = search_service._deep_link("chat_message", "msg-002", {})
    assert link_chat_no_conv == "#/chat?message_id=msg-002"

    # 2. note deep link
    link_note = search_service._deep_link("note", "note-001", {"document_id": "doc-001"})
    assert link_note == "#/library?document_id=doc-001&note_id=note-001"

    # 3. document deep link
    link_doc = search_service._deep_link("document", "doc-001", {})
    assert link_doc == "#/library?document_id=doc-001"

    # 4. run deep link
    link_run = search_service._deep_link("run", "run-001", {})
    assert link_run == "#/research?run_id=run-001"

    # 5. evidence deep link
    link_ev = search_service._deep_link("evidence", "ev-001", {"run_id": "run-001"})
    assert link_ev == "#/research?run_id=run-001&evidence_id=ev-001"


# =========================================================================
# 2. G2-B: 运行取消接口与状态流转保证 (U09)
# =========================================================================

def test_g2_run_cancel_endpoint(tmp_path: Path) -> None:
    """U09: Calling cancel on an active run transitions state to cancelled, not failed."""
    app, repository, store, orchestrator, search_service = _build_g2_test_environment(tmp_path)
    client = TestClient(app)

    # 1. Submit a fixture task
    res_sub = client.post("/api/v1/tasks/research-fixture", json={"objective": "验证任务取消流转"})
    assert res_sub.status_code == 200
    run_id = res_sub.json()["run_id"]

    # 2. Request cancel
    res_cancel = client.post(f"/api/v1/runs/{run_id}/cancel")
    assert res_cancel.status_code == 200
    detail = res_cancel.json()
    assert detail["state"] in ("cancelled", "cancelling")
    assert detail["state"] != "failed"

    # 3. Query run detail again
    res_get = client.get(f"/api/v1/runs/{run_id}")
    assert res_get.status_code == 200
    assert res_get.json()["state"] in ("cancelled", "cancelling")


# =========================================================================
# 3. G2-B: 运行事件 SSE 端点与游标回放 (U09)
# =========================================================================

def test_g2_run_events_sse_endpoint(tmp_path: Path) -> None:
    """U09: Run events SSE endpoint returns text/event-stream."""
    app, repository, store, orchestrator, search_service = _build_g2_test_environment(tmp_path)
    client = TestClient(app)

    # 1. Submit a fixture task and cancel it so stream finishes upon terminal state
    res_sub = client.post("/api/v1/tasks/research-fixture", json={"objective": "验证事件流订阅"})
    assert res_sub.status_code == 200
    run_id = res_sub.json()["run_id"]
    client.post(f"/api/v1/runs/{run_id}/cancel")

    # 2. GET /api/v1/runs/{run_id}/events?after=0
    resp = client.get(f"/api/v1/runs/{run_id}/events?after=0&poll_seconds=0.01")
    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers["content-type"]
    assert "data:" in resp.text


# =========================================================================
# 4. G2-D: 记忆候选批准与拒绝双向决策闭环 (U12)
# =========================================================================

def test_g2_memory_candidates_approve_and_reject_decisions(tmp_path: Path) -> None:
    """U12: Memory candidates can be individually approved or rejected, updating candidate status."""
    app, repository, store, orchestrator, search_service = _build_g2_test_environment(tmp_path)
    client = TestClient(app)
    memory_store: HierarchicalMemoryStore = app.state.memory_store

    # 1. Create candidate A for approve
    cand_a = memory_store.create_candidate(
        scope=MemoryScope.USER,
        target_id="user_test",
        category=MemoryCategory.FACT,
        statement="表面码晶格手术可实现非阿贝尔任意子编织",
        confidence=0.92,
        source_type="dialogue",
        source_id="msg-101",
    )
    assert cand_a is not None
    assert cand_a.status == CandidateStatus.PENDING

    # 2. Create candidate B for reject
    cand_b = memory_store.create_candidate(
        scope=MemoryScope.USER,
        target_id="user_test",
        category=MemoryCategory.PREFERENCE,
        statement="临时测试数据，无需沉淀",
        confidence=0.3,
        source_type="dialogue",
        source_id="msg-102",
    )
    assert cand_b is not None
    assert cand_b.status == CandidateStatus.PENDING

    # 3. Approve A
    res_a = client.post(f"/api/v1/memories/candidates/{cand_a.candidate_id}/action", json={"action": "approve"})
    assert res_a.status_code == 200
    data_a = res_a.json()
    assert data_a["ok"] is True
    assert data_a["action"] == "approved"
    assert data_a.get("memory_id")

    # 4. Reject B
    res_b = client.post(f"/api/v1/memories/candidates/{cand_b.candidate_id}/action", json={"action": "reject"})
    assert res_b.status_code == 200
    data_b = res_b.json()
    assert data_b["ok"] is True
    assert data_b["action"] == "rejected"

    # 5. Check memory store: A is active memory, B is not
    mems = memory_store.list_memories()
    assert any(m.statement == "表面码晶格手术可实现非阿贝尔任意子编织" for m in mems)
    assert not any(m.statement == "临时测试数据，无需沉淀" for m in mems)


# =========================================================================
# 5. G2-C: 对话回答保留 citations 与 verification 结构 (U13)
# =========================================================================

def test_g2_chat_response_contract_citations_and_verification(tmp_path: Path) -> None:
    """U13: ChatAnswerResponse returns citations and verification metadata."""
    app, repository, store, orchestrator, search_service = _build_g2_test_environment(tmp_path)
    client = TestClient(app)

    res = client.post("/api/v1/chat", json={"question": "什么是表面码？", "mode": "direct"})
    assert res.status_code == 200
    data = res.json()
    assert "citations" in data
    assert "verification" in data
    assert data["verification"] in ("model-knowledge", "unverified-aggregation", "durable-run-dispatched")
    assert data["message_id"]
