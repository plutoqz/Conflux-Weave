"""G3 Automated Test Suite: Scoped document retrieval, long-text reading coverage & section grounding, and library batch/reindex lifecycle."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
import pytest
from starlette.testclient import TestClient

from conflux_weave.api_contracts import (
    ChatMessageRequest,
    VerifiedResearchTaskRequest,
    DeepResearchTaskRequest,
)
from conflux_weave.server import LibraryBatchRequest, WorkerLoop, create_app
from conflux_weave.runtime.artifacts import LocalArtifactStore
from conflux_weave.runtime import SQLiteRuntimeRepository
from conflux_weave.documents import LocalDocumentImporter
from conflux_weave.document_agent import DocumentAgent
from conflux_weave.chat import (
    ChatService,
    VERIFICATION_UNVERIFIED_AGGREGATION,
)
from conflux_weave.provider import (
    OpenAICompatibleChatAdapter,
    ProviderConfig,
    ProviderHttpResponse,
)
from conflux_weave.harness.orchestration import CompositeOrchestrator
from conflux_weave.harness.fixture_runtime import ResearchFixtureRuntime


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


class FakeHit:
    def __init__(self, document_id: str, score: float, snapshot: str, locator: dict | None = None):
        self.document_id = document_id
        self.score = score
        self.rank = 1
        self.source_snapshot_id = snapshot
        self.locator = locator or {"page": 1, "document_id": snapshot}


class FakeRetrieval:
    def __init__(self, doc_specs: list[dict]):
        # doc_specs: [{"id": "chunk-1", "snapshot": "snap-A", "text": "...", "locator": {...}}]
        self.document_by_id = {
            spec["id"]: SimpleNamespace(
                text=spec["text"],
                source_snapshot_id=spec.get("snapshot", ""),
                locator=spec.get("locator", {}),
            )
            for spec in doc_specs
        }
        self.doc_specs = doc_specs
        self.searched = None

    def search(self, query: str):
        self.searched = query
        hits = [
            FakeHit(
                spec["id"],
                0.95 - idx * 0.1,
                spec.get("snapshot", ""),
                spec.get("locator", {}),
            )
            for idx, spec in enumerate(self.doc_specs)
        ]
        return SimpleNamespace(final=SimpleNamespace(hits=hits), fused_hits=())


def _build_rag_test_service(tmp_path: Path, chat_payloads: list[dict], doc_specs: list[dict]):
    store = LocalArtifactStore(tmp_path / "artifacts")
    transport = _SequenceChatTransport(chat_payloads)
    config = ProviderConfig("https://provider.example/v1", "secret", "chat")
    chat_adapter = OpenAICompatibleChatAdapter(store, config, transport=transport)
    chat_db_path = tmp_path / "db" / "chat.sqlite3"
    chat_db_path.parent.mkdir(parents=True, exist_ok=True)
    service = ChatService(chat_adapter, chat_db_path, artifact_store=store)
    retrieval = FakeRetrieval(doc_specs)
    service._retrieval = retrieval
    service._store = LocalArtifactStore(tmp_path / "ctx")
    return service, transport, retrieval, store


# =========================================================================
# 1. Scoped Document Retrieval (U18 & V11) Tests
# =========================================================================

GOOD_SCOPED_RAG_ANSWER = (
    "关于表面码阈值的分析表明，在现象学噪声模型下容错阈值约为 1% [1]。"
    "在更现实的电路级噪声模型下，物理纠错阈值通常降至 0.5% 至 0.7% [1]。"
    "因此硬件层必须达到低于 10^-3 的双量子比特门错误率方可实现逻辑保真度增益。"
)


def test_scoped_rag_matching_document(tmp_path: Path):
    """V11: When scoped to a document that contains the answer, RAG retrieves only from that document."""
    specs = [
        {
            "id": "chunk-101",
            "snapshot": "doc-surface-code",
            "text": "Surface code threshold is approximately 1.0% under phenomenological noise.",
            "locator": {"page": 4, "document_id": "doc-surface-code"},
        },
        {
            "id": "chunk-201",
            "snapshot": "doc-bosonic-code",
            "text": "GKP cat codes leverage infinite dimensional Hilbert space.",
            "locator": {"page": 2, "document_id": "doc-bosonic-code"},
        },
    ]
    service, transport, retrieval, _ = _build_rag_test_service(
        tmp_path, [_mock_chat_payload(GOOD_SCOPED_RAG_ANSWER)], specs
    )

    result = service.rag_answer(
        "表面码容错阈值是多少？",
        None,
        document_ids=["doc-surface-code"],
    )

    assert result["mode"] == "rag"
    assert result["verification"] == "unverified-aggregation"
    assert result["checks"]["status"] == "passed"
    # Citations should strictly originate from the scoped document
    assert len(result["citations"]) == 1
    assert result["citations"][0]["chunk_id"] == "chunk-101"
    assert result["citations"][0]["source_snapshot_id"] == "doc-surface-code"

    # Verify prompt sent to LLM contains chunk-101 but NOT chunk-201
    sent_prompt = transport.requests[0]["messages"][1]["content"]
    assert "chunk-101" in sent_prompt
    assert "Surface code threshold" in sent_prompt
    assert "chunk-201" not in sent_prompt
    assert "GKP cat codes" not in sent_prompt


def test_scoped_rag_out_of_scope_no_hallucination(tmp_path: Path):
    """V11: When scoped to a document that does NOT contain the answer, returns clean degraded explanation without hallucination or 400/500."""
    specs = [
        {
            "id": "chunk-101",
            "snapshot": "doc-surface-code",
            "text": "Surface code threshold is approximately 1.0%.",
            "locator": {"page": 4, "document_id": "doc-surface-code"},
        },
    ]
    # No LLM call should be made when scoped document yields no matching chunks
    service, transport, retrieval, _ = _build_rag_test_service(
        tmp_path, [], specs
    )

    result = service.rag_answer(
        "玻色量子码的纠错原理是什么？",
        None,
        document_ids=["doc-other-unrelated"],
    )

    assert result["mode"] == "rag"
    assert result["verification"] == VERIFICATION_UNVERIFIED_AGGREGATION
    assert result["checks"]["status"] == "degraded"
    assert "insufficient_evidence_in_specified_scope" in result["checks"]["violations"]
    assert "在您限定的文献集合中，未检索到与提问相关的有效支撑内容" in result["content"]
    assert "已避免超出选定文献范围进行推测" in result["content"]
    assert result["citations"] == []
    # Zero external LLM calls made
    assert len(transport.requests) == 0


def test_scoped_rag_via_http_api(tmp_path: Path):
    """V11 API integration: Scoped retrieval via POST /api/v1/chat endpoint."""
    specs = [
        {
            "id": "chunk-1",
            "snapshot": "paper-alpha",
            "text": "Alpha paper introduces transformer self-attention.",
            "locator": {"page": 1, "document_id": "paper-alpha"},
        },
    ]
    service, transport, retrieval, store = _build_rag_test_service(
        tmp_path, [_mock_chat_payload(GOOD_SCOPED_RAG_ANSWER)], specs
    )
    repository = SQLiteRuntimeRepository(tmp_path / "db" / "runtime.sqlite3", store, clock=lambda: NOW)
    fixture_runtime = ResearchFixtureRuntime(repository, store, tmp_path / "workspace")
    orchestrator = CompositeOrchestrator(repository, (fixture_runtime,))

    app = create_app(
        repository,
        orchestrator,
        provider_configured=True,
        chat_service=service,
        worker=WorkerLoop(orchestrator, interval_seconds=10),
    )
    client = TestClient(app)

    # Scoped request with matching document
    resp = client.post(
        "/api/v1/chat",
        json={
            "question": "介绍 self-attention",
            "mode": "rag",
            "document_ids": ["paper-alpha"],
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["mode"] == "rag"
    assert len(data.get("citations", [])) > 0

    # Scoped request with out-of-scope document -> 200 OK with clean explanation
    resp_empty = client.post(
        "/api/v1/chat",
        json={
            "question": "介绍 self-attention",
            "mode": "rag",
            "document_ids": ["paper-beta-nonexistent"],
        },
    )
    assert resp_empty.status_code == 200
    data_empty = resp_empty.json()
    assert "未检索到与提问相关的有效支撑内容" in data_empty["content"]
    assert data_empty.get("citations") == []


def test_api_contracts_support_document_ids():
    """Verify document_ids is accepted by Chat, VerifiedResearch, and DeepResearch request contracts."""
    chat_req = ChatMessageRequest(question="test", mode="rag", document_ids=("doc-1", "doc-2"))
    assert chat_req.document_ids == ("doc-1", "doc-2")

    v_req = VerifiedResearchTaskRequest(objective="test", document_ids=("doc-a",))
    assert v_req.document_ids == ("doc-a",)

    d_req = DeepResearchTaskRequest(objective="test", document_ids=("doc-x", "doc-y"))
    assert d_req.document_ids == ("doc-x", "doc-y")


# =========================================================================
# 2. Long-text Reading Coverage & Section Grounding (U15 & V12) Tests
# =========================================================================

def test_document_agent_reading_coverage_and_grounding(tmp_path: Path):
    """V12: DocumentAgent computes reading_coverage metadata, prepends coverage banner, and anchors section source_segments."""
    source = tmp_path / "quantum_fault_tolerance.md"
    source.write_text(
        "# 拓扑量子纠错与容错架构体系\n\n"
        "拓扑量子计算利用非阿贝尔任意子的编织操作实现硬件级拓扑保护。\n\n"
        "## 1. 表面码纠错基准\n\n"
        "表面码具有高达 1% 的容错阈值，且仅需二维近邻耦合几何拓扑，成为超导量子芯片主流方案。\n\n"
        "## 2. 伴随子解码算法\n\n"
        "最小权重完美匹配 (MWPM) 与 Union-Find 解码器在实时伴随子图上快速配对激发任意子。\n\n"
        "## 3. 容错逻辑门实现\n\n"
        "通过晶格手术 (Lattice Surgery) 实现逻辑 CNOT 门，结合状态蒸馏制备高质量魔态 |T>。\n\n"
        "## 4. 体系展望与工程挑战\n\n"
        "从当前百物理比特规模迈向百万逻辑比特需要解决低温测控线缆热负载与微波串扰难题。\n",
        encoding="utf-8",
    )
    store = LocalArtifactStore(tmp_path / "artifacts")
    imported = LocalDocumentImporter(store).import_path(source)
    assert len(imported.segments) >= 5

    agent = DocumentAgent(store)
    note = agent.analyze_document(
        imported,
        focus="解码算法与晶格手术",
        title="拓扑量子容错深度研读笔记",
    )

    # 1. Reading coverage metadata assertions
    coverage = note.metadata.get("reading_coverage")
    assert coverage is not None, "reading_coverage must be recorded in note.metadata"
    assert coverage["mode"] in ("section_focused", "full_text", "sampled_overview")
    assert coverage["total_segments"] == len(imported.segments)
    assert coverage["covered_segments_count"] > 0
    assert coverage["coverage_percentage"] > 0
    assert isinstance(coverage["covered_headings"], list)
    assert isinstance(coverage["uncovered_headings"], list)

    # 2. Reading coverage banner assertion
    assert "【研读覆盖度:" in note.executive_summary
    assert coverage["mode_label"] in note.executive_summary
    assert f"{coverage['coverage_percentage']}%" in note.executive_summary

    # 3. Section source_segments grounding assertions
    all_imported_segment_ids = {s.segment_id for s in imported.segments}
    assert len(note.sections) >= 3
    for sec in note.sections:
        assert sec.source_segments, f"Section '{sec.heading}' must anchor to source_segments"
        for seg_id in sec.source_segments:
            assert seg_id in all_imported_segment_ids, (
                f"Section source_segment '{seg_id}' must belong to imported segments"
            )


# =========================================================================
# 3. Library Lifecycle & Status Precision (U14 & V10) Tests
# =========================================================================

def test_library_batch_and_overview_api(tmp_path: Path):
    """V10: Verify library batch actions, lifecycle state vs indexing status precision, and overview statistics."""
    store = LocalArtifactStore(tmp_path / "artifacts")
    repository = SQLiteRuntimeRepository(tmp_path / "db" / "runtime.sqlite3", store, clock=lambda: NOW)
    fixture_runtime = ResearchFixtureRuntime(repository, store, tmp_path / "workspace")
    orchestrator = CompositeOrchestrator(repository, (fixture_runtime,))

    # Seed registry with documents in different states
    registry_file = repository.database_path.with_name("library-registry.json")
    registry_file.parent.mkdir(parents=True, exist_ok=True)
    registry_data = [
        {
            "document_id": "doc-001",
            "title": "Quantum Error Correction Handbook",
            "source_type": "pdf",
            "status": "knowledge_ready",
            "character_count": 50000,
            "created_at": NOW,
            "lifecycle": "active",
        },
        {
            "document_id": "doc-002",
            "title": "Topological Orders in Cold Atoms",
            "source_type": "markdown",
            "status": "parsed",
            "character_count": 25000,
            "created_at": NOW,
            "lifecycle": "active",
        },
    ]
    registry_file.write_text(json.dumps(registry_data, ensure_ascii=False), encoding="utf-8")

    app = create_app(
        repository,
        orchestrator,
        provider_configured=False,
        worker=WorkerLoop(orchestrator, interval_seconds=10),
    )
    client = TestClient(app)

    # 1. Test GET /api/v1/library?status=active
    resp = client.get("/api/v1/library?status=active")
    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data
    assert len(data["items"]) == 2
    # Verify separation of lifecycle and indexing status
    doc1 = next(d for d in data["items"] if d["document_id"] == "doc-001")
    assert doc1["lifecycle"] == "active"
    assert doc1["status"] == "knowledge_ready"

    doc2 = next(d for d in data["items"] if d["document_id"] == "doc-002")
    assert doc2["lifecycle"] == "active"
    assert doc2["status"] == "parsed"

    # 2. Test POST /api/v1/library/documents/batch validation
    empty_batch = client.post("/api/v1/library/documents/batch", json={"document_ids": [], "action": "index"})
    assert empty_batch.status_code == 422

    notfound_batch = client.post("/api/v1/library/documents/batch", json={"document_ids": ["non-existent"], "action": "index"})
    assert notfound_batch.status_code == 404
    assert notfound_batch.json()["code"] == "document_not_found"


# =========================================================================
# 4. Settings Task Capability Matrix Contract (U17) Tests
# =========================================================================

def test_settings_config_and_effective_matrix(tmp_path: Path):
    """U17: Verify GET /api/v1/config returns provider and provider_effective to power the capability check matrix."""
    store = LocalArtifactStore(tmp_path / "artifacts")
    repository = SQLiteRuntimeRepository(tmp_path / "db" / "runtime.sqlite3", store, clock=lambda: NOW)
    fixture_runtime = ResearchFixtureRuntime(repository, store, tmp_path / "workspace")
    orchestrator = CompositeOrchestrator(repository, (fixture_runtime,))

    provider_config = ProviderConfig(
        base_url="https://api.openai.com/v1",
        api_key="secret-key",
        model="gpt-4o",
        embedding_model="text-embedding-3-small",
        reranker_model="bge-reranker-large",
        engine_model="o1-preview",
    )

    app = create_app(
        repository,
        orchestrator,
        provider_configured=True,
        provider_effective=provider_config,
        worker=WorkerLoop(orchestrator, interval_seconds=10),
    )
    client = TestClient(app)

    resp = client.get("/api/v1/config")
    assert resp.status_code == 200
    data = resp.json()
    assert "provider_effective" in data
    eff = data["provider_effective"]
    assert eff is not None
    assert eff["model"] == "gpt-4o"
    assert eff["embedding_model"] == "text-embedding-3-small"
    assert eff["reranker_model"] == "bge-reranker-large"
    assert eff["engine_model"] == "o1-preview"
    assert eff["api_key_configured"] is True
