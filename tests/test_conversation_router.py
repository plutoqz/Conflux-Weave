"""Tests for ConversationRouter and unified conversation dispatch (P4.2)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from conflux_weave.api_contracts import (
    ChatMessageRequest,
    RouterRequest,
)
from conflux_weave.chat import ChatService
from conflux_weave.conversation_router import (
    ConversationRouter,
    RouterResult,
)
from conflux_weave.provider import (
    OpenAICompatibleChatAdapter,
    ProviderConfig,
    ProviderHttpResponse,
)
from conflux_weave.runtime import LocalArtifactStore
from conflux_weave.runtime.memory_store import HierarchicalMemoryStore, MemoryCategory, MemoryScope
from conflux_weave.server import create_app


class SequenceTransport:
    def __init__(self, payloads):
        self.payloads = iter(payloads)
        self.requests = []

    def post(self, *args, **kwargs):
        self.requests.append(json.loads(kwargs["body"]))
        payload = next(self.payloads)
        return ProviderHttpResponse(200, json.dumps(payload).encode(), {"Content-Type": "application/json"})


def _chat_payload(content: str, response_id: str = "r-test"):
    return {
        "id": response_id,
        "model": "fixture-model",
        "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
    }


def test_router_empty_input_defaults_to_direct():
    router = ConversationRouter()
    res = router.route("")
    assert res.target_mode == "direct"
    assert res.is_fast_path is True

    res2 = router.route("   ")
    assert res2.target_mode == "direct"


def test_router_explicit_manual_override():
    router = ConversationRouter()
    res_deep = router.route("随便问个概念", current_mode="deep")
    assert res_deep.target_mode == "deep"
    assert res_deep.is_fast_path is False

    res_rag = router.route("随便问个概念", current_mode="rag")
    assert res_rag.target_mode == "rag"
    assert res_rag.is_fast_path is True

    res_proj = router.route("随便问个概念", current_mode="project")
    assert res_proj.target_mode == "project"
    assert res_proj.is_fast_path is False


def test_router_command_prefixes():
    router = ConversationRouter()

    res_deep = router.route("/deep 请调研大语言模型知识蒸馏前沿进展")
    assert res_deep.target_mode == "deep"
    assert res_deep.is_fast_path is False
    assert res_deep.suggested_run_kind == "managed_verified_research"

    res_rag = router.route("/rag 查询本地资料库里关于LoRA的论文")
    assert res_rag.target_mode == "rag"
    assert res_rag.is_fast_path is True

    res_doc = router.route("/doc 精读当前论文的方法章节")
    assert res_doc.target_mode == "document"
    assert res_doc.is_fast_path is False

    res_proj = router.route("/project 梳理架构调用拓扑")
    assert res_proj.target_mode == "project"
    assert res_proj.is_fast_path is False

    res_audit = router.route("/audit 检查契约完整性")
    assert res_audit.target_mode == "project"
    assert res_audit.is_fast_path is False

    res_mem = router.route("/mem 查看我的偏好")
    assert res_mem.target_mode == "memory"
    assert res_mem.is_fast_path is True


def test_router_entity_extraction():
    router = ConversationRouter()

    # Project entity
    res_proj = router.route("@project:cw-runtime 请分析架构解构")
    assert res_proj.target_mode == "project"
    assert res_proj.extracted_entities.get("project_id") == "cw-runtime"
    assert res_proj.is_fast_path is False

    # Paper entity
    res_paper = router.route("@paper:2312.00752 请提炼核心论点")
    assert res_paper.target_mode == "document"
    assert res_paper.extracted_entities.get("paper_id") == "2312.00752"

    # Note entity
    res_note = router.route("@note:note-101 帮我补充结论段落")
    assert res_note.target_mode == "document"
    assert res_note.extracted_entities.get("note_id") == "note-101"

    # Run entity
    res_run = router.route("@run:run-abc 为什么在第二步出现冲突？")
    assert res_run.target_mode == "deep"
    assert res_run.extracted_entities.get("run_id") == "run-abc"


def test_router_heuristics_offline():
    router = ConversationRouter()

    # Deep research heuristics
    res = router.route("针对当前具身智能技术演进进行全面调研并生成对比分析报告")
    assert res.target_mode == "deep"
    assert res.is_fast_path is False

    # Project heuristics
    res = router.route("请对当前仓库进行代码体检与代码架构解构")
    assert res.target_mode == "project"

    # Document heuristics
    res = router.route("请分析这篇论文并生成权威笔记")
    assert res.target_mode == "document"

    # Memory heuristics
    res = router.route("查看记忆中心里已保存的约定")
    assert res.target_mode == "memory"
    assert res.is_fast_path is True

    # RAG heuristics
    res = router.route("在已收录论文与本地知识库中查一下")
    assert res.target_mode == "rag"
    assert res.is_fast_path is True

    # Direct fallback
    res = router.route("什么是余弦相似度？")
    assert res.target_mode == "direct"
    assert res.is_fast_path is True


def test_router_model_routing_and_fallback(tmp_path: Path):
    # LLM returns valid classification
    model_json = json.dumps({"mode": "deep", "confidence": 0.95, "reason": "需要综合调研"})
    transport = SequenceTransport([_chat_payload(model_json, "resp-1")])
    config = ProviderConfig("https://provider.example/v1", "secret", "model-router")
    adapter = OpenAICompatibleChatAdapter(
        LocalArtifactStore(tmp_path / "artifacts"), config, transport=transport
    )
    router = ConversationRouter(chat_adapter=adapter)
    res = router.route("能否详细分析近期多模态大模型的最新动态？")
    assert res.target_mode == "deep"
    assert res.confidence == 0.95

    # LLM returns invalid json -> gracefully falls back to heuristics
    transport_bad = SequenceTransport([_chat_payload("NOT A JSON STRING", "resp-2")])
    adapter_bad = OpenAICompatibleChatAdapter(
        LocalArtifactStore(tmp_path / "artifacts2"), config, transport=transport_bad
    )
    router_bad = ConversationRouter(chat_adapter=adapter_bad)
    res_fallback = router_bad.route("什么是卷积神经网络？")
    assert res_fallback.target_mode == "direct"


def test_server_route_endpoint(tmp_path: Path):
    repository = SimpleNamespace(database_path=tmp_path / "test.db")
    orchestrator = SimpleNamespace()
    app = create_app(repository, orchestrator)
    client = TestClient(app)

    # 1. Test route preflight endpoint
    resp = client.post(
        "/api/v1/chat/route",
        json={"query": "@project:Conflux-Weave 分析理论映射与代码架构", "current_mode": "auto"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["target_mode"] == "project"
    assert data["is_fast_path"] is False
    assert data["extracted_entities"].get("project_id") == "Conflux-Weave"

    # 2. Test prefix route
    resp_prefix = client.post(
        "/api/v1/chat/route",
        json={"query": "/deep 对比分析近期 RAG 发展", "current_mode": "auto"},
    )
    assert resp_prefix.status_code == 200
    assert resp_prefix.json()["target_mode"] == "deep"
    assert resp_prefix.json()["is_fast_path"] is False


def test_server_auto_chat_dispatch(tmp_path: Path):
    transport = SequenceTransport([
        _chat_payload(json.dumps({"mode": "direct", "confidence": 0.95, "reason": "常规概念问答"}), "r-router"),
        _chat_payload("这是关于强化学习的直接解释。", "r-direct"),
    ])
    config = ProviderConfig("https://provider.example/v1", "secret", "chat")
    adapter = OpenAICompatibleChatAdapter(
        LocalArtifactStore(tmp_path / "artifacts"), config, transport=transport
    )
    chat_svc = ChatService(adapter, tmp_path / "chat.sqlite3")

    mock_orchestrator = SimpleNamespace()
    mock_orchestrator.submit = MagicMock(return_value=SimpleNamespace(task_id="t-1", run_id="run-auto-123", created="2026-09-10T00:00:00Z"))

    repository = SimpleNamespace(database_path=tmp_path / "test.db")
    app = create_app(repository, mock_orchestrator, chat_service=chat_svc)
    client = TestClient(app)

    # 1. Auto mode routed to direct
    res_direct = client.post(
        "/api/v1/chat",
        json={"question": "什么是强化学习？", "mode": "auto"},
    )
    assert res_direct.status_code == 200
    d_data = res_direct.json()
    assert d_data["routed_mode"] == "direct"
    assert d_data["is_fast_path"] is True
    assert "这是关于强化学习的直接解释" in d_data["content"]

    # 2. Auto mode routed to deep research (Slow path)
    res_deep = client.post(
        "/api/v1/chat",
        json={"question": "/deep 请对多智能体协同机制开展系统性综述", "mode": "auto"},
    )
    assert res_deep.status_code == 200
    deep_data = res_deep.json()
    assert deep_data["routed_mode"] == "deep"
    assert deep_data["is_fast_path"] is False
    assert deep_data["run_id"] == "run-auto-123"
    assert deep_data["verification"] == "durable-run-dispatched"
    assert mock_orchestrator.submit.called

    # 3. Auto mode routed to memory query
    # First write a memory
    memory_store = app.state.memory_store
    memory_store.create_memory(
        scope=MemoryScope.USER,
        target_id="user_default",
        category=MemoryCategory.PREFERENCE,
        statement="偏好使用简体中文输出学术术语",
        confidence=1.0,
        source_type="manual",
        source_id="test",
    )
    res_mem = client.post(
        "/api/v1/chat",
        json={"question": "/mem 查看我的偏好", "mode": "auto"},
    )
    assert res_mem.status_code == 200
    mem_data = res_mem.json()
    assert mem_data["routed_mode"] == "memory"
    assert "偏好使用简体中文输出学术术语" in mem_data["content"]

    # 4. Auto mode routed to project
    res_proj = client.post(
        "/api/v1/chat",
        json={"question": "@project:Conflux-Weave 分析架构", "mode": "auto"},
    )
    assert res_proj.status_code == 200
    proj_data = res_proj.json()
    assert proj_data["routed_mode"] == "project"
    assert "项目工作台" in proj_data["content"]


def test_server_chat_memory_candidate_extraction(tmp_path: Path):
    transport = SequenceTransport([
        _chat_payload("好的，已收到您的要求。", "r-pref"),
    ])
    config = ProviderConfig("https://provider.example/v1", "secret", "chat")
    adapter = OpenAICompatibleChatAdapter(
        LocalArtifactStore(tmp_path / "artifacts"), config, transport=transport
    )
    chat_svc = ChatService(adapter, tmp_path / "chat.sqlite3")

    repository = SimpleNamespace(database_path=tmp_path / "test.db")
    app = create_app(repository, SimpleNamespace(), chat_service=chat_svc)
    client = TestClient(app)

    # Express an explicit preference
    res = client.post(
        "/api/v1/chat",
        json={"question": "以后请都用简体中文回答", "mode": "direct"},
    )
    assert res.status_code == 200
    data = res.json()
    assert len(data["memory_candidates"]) >= 1
    assert "简体中文" in data["memory_candidates"][0]["statement"]
