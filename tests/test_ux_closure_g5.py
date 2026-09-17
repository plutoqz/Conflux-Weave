"""G5 Automated Test Suite: Research Topic Minimal Slice (U21).
Covers Topic CRUD lifecycle, cross-object link/unlink, non-cascading delete safety,
tolerance to missing/deleted underlying objects, recent location tracking, and semantic isolation.
"""

from __future__ import annotations

import json
from pathlib import Path
import pytest
from starlette.testclient import TestClient

from conflux_weave.server import WorkerLoop, create_app
from conflux_weave.runtime.artifacts import LocalArtifactStore
from conflux_weave.runtime import SQLiteRuntimeRepository
from conflux_weave.chat import ChatService
from conflux_weave.provider import (
    OpenAICompatibleChatAdapter,
    ProviderConfig,
    ProviderHttpResponse,
)
from conflux_weave.harness.orchestration import CompositeOrchestrator
from conflux_weave.harness.fixture_runtime import ResearchFixtureRuntime


NOW = "2026-09-17T14:00:00Z"


def _mock_chat_payload(content: str, response_id: str = "resp-001"):
    return {
        "id": response_id,
        "model": "fixture-chat",
        "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
    }


class _DummyChatTransport:
    def post(self, *args, **kwargs):
        payload = _mock_chat_payload("专题分析回答")
        return ProviderHttpResponse(200, json.dumps(payload).encode(), {"Content-Type": "application/json"})


def _build_g5_test_environment(tmp_path: Path):
    store = LocalArtifactStore(tmp_path / "artifacts")
    repository = SQLiteRuntimeRepository(
        tmp_path / "db" / "runtime.sqlite3", store, clock=lambda: NOW
    )
    fixture_runtime = ResearchFixtureRuntime(repository, store, tmp_path / "workspace")
    orchestrator = CompositeOrchestrator(repository, (fixture_runtime,))
    transport = _DummyChatTransport()
    config = ProviderConfig("https://provider.example/v1", "secret", "chat")
    chat_adapter = OpenAICompatibleChatAdapter(store, config, transport=transport)
    chat_db_path = tmp_path / "db" / "chat.sqlite3"
    chat_db_path.parent.mkdir(parents=True, exist_ok=True)
    chat_service = ChatService(chat_adapter, chat_db_path, artifact_store=store)

    app = create_app(
        repository,
        orchestrator,
        provider_configured=True,
        chat_service=chat_service,
        worker=WorkerLoop(orchestrator, interval_seconds=10),
    )
    return app, repository, store, chat_service


# =========================================================================
# 1. Topic CRUD Lifecycle
# =========================================================================

def test_topic_lifecycle_crud(tmp_path: Path):
    app, repo, store, _ = _build_g5_test_environment(tmp_path)
    client = TestClient(app)

    # 1. Create Topic
    create_payload = {
        "name": "多智能体协同代码优化研究",
        "objective": "验证单Agent与多Agent在代码补丁生成中的事实准确性差异",
        "description": "基于真实AST与Git上下文的受控对比研究",
        "tags": ["agent", "coding", "benchmark"],
        "document_ids": ["doc-paper-01"],
    }
    create_res = client.post("/api/v1/topics", json=create_payload)
    assert create_res.status_code == 200, create_res.text
    topic_data = create_res.json()
    topic_id = topic_data["topic_id"]
    assert topic_id.startswith("topic-")
    assert topic_data["name"] == "多智能体协同代码优化研究"
    assert topic_data["objective"] == "验证单Agent与多Agent在代码补丁生成中的事实准确性差异"
    assert "agent" in topic_data["tags"]
    assert "doc-paper-01" in topic_data["document_ids"]

    # 2. List Topics
    list_res = client.get("/api/v1/topics")
    assert list_res.status_code == 200
    list_data = list_res.json()
    assert list_data["total"] >= 1
    item = next((t for t in list_data["items"] if t["topic_id"] == topic_id), None)
    assert item is not None
    assert item["name"] == "多智能体协同代码优化研究"

    # 3. Get Topic Detail
    get_res = client.get(f"/api/v1/topics/{topic_id}")
    assert get_res.status_code == 200
    detail = get_res.json()
    assert detail["topic_id"] == topic_id

    # 4. Patch Topic
    patch_res = client.patch(
        f"/api/v1/topics/{topic_id}",
        json={"name": "多智能体代码优化与自动验证研究", "objective": "更新后的核心研究问题"},
    )
    assert patch_res.status_code == 200
    patched = patch_res.json()
    assert patched["name"] == "多智能体代码优化与自动验证研究"
    assert patched["objective"] == "更新后的核心研究问题"

    # 5. Delete Topic
    del_res = client.delete(f"/api/v1/topics/{topic_id}")
    assert del_res.status_code == 200
    del_data = del_res.json()
    assert del_data["deleted"] is True
    assert del_data["topic_id"] == topic_id

    # Verify not found after delete
    get_again = client.get(f"/api/v1/topics/{topic_id}")
    assert get_again.status_code == 404


# =========================================================================
# 2. Cross-Object Link & Unlink Operations
# =========================================================================

def test_cross_object_link_and_unlink(tmp_path: Path):
    app, repo, store, _ = _build_g5_test_environment(tmp_path)
    client = TestClient(app)

    create_res = client.post("/api/v1/topics", json={"name": "跨对象链接测试专题"})
    assert create_res.status_code == 200
    topic_id = create_res.json()["topic_id"]

    # Link Document
    link_doc = client.post(
        f"/api/v1/topics/{topic_id}/link",
        json={"object_type": "document", "object_id": "doc-test-101", "action": "link"},
    )
    assert link_doc.status_code == 200
    assert "doc-test-101" in link_doc.json()["document_ids"]

    # Link Note
    link_note = client.post(
        f"/api/v1/topics/{topic_id}/link",
        json={"object_type": "note", "object_id": "note-test-202", "action": "link"},
    )
    assert link_note.status_code == 200
    assert "note-test-202" in link_note.json()["note_ids"]

    # Link Run
    link_run = client.post(
        f"/api/v1/topics/{topic_id}/link",
        json={"object_type": "run", "object_id": "run-test-303", "action": "link"},
    )
    assert link_run.status_code == 200
    assert "run-test-303" in link_run.json()["run_ids"]

    # Link Project
    link_proj = client.post(
        f"/api/v1/topics/{topic_id}/link",
        json={"object_type": "project", "object_id": "proj-test-404", "action": "link"},
    )
    assert link_proj.status_code == 200
    assert "proj-test-404" in link_proj.json()["project_ids"]

    # Link Conversation
    link_conv = client.post(
        f"/api/v1/topics/{topic_id}/link",
        json={"object_type": "conversation", "object_id": "conv-test-505", "action": "link"},
    )
    assert link_conv.status_code == 200
    assert "conv-test-505" in link_conv.json()["conversation_ids"]

    # Unlink Note
    unlink_note = client.post(
        f"/api/v1/topics/{topic_id}/link",
        json={"object_type": "note", "object_id": "note-test-202", "action": "unlink"},
    )
    assert unlink_note.status_code == 200
    assert "note-test-202" not in unlink_note.json()["note_ids"]

    # Invalid object type rejected with 422/400
    bad_link = client.post(
        f"/api/v1/topics/{topic_id}/link",
        json={"object_type": "invalid_type", "object_id": "bad-id", "action": "link"},
    )
    assert bad_link.status_code in (400, 422)


# =========================================================================
# 3. Non-Cascading Delete Safety Check
# =========================================================================

def test_non_cascading_delete_safety(tmp_path: Path):
    """CRITICAL SAFETY TEST: Deleting a research topic MUST NOT cascade delete
    underlying code projects, runs, notes, or library documents.
    """
    app, repo, store, _ = _build_g5_test_environment(tmp_path)
    client = TestClient(app)

    # 1. Register a real sample code project
    proj_dir = tmp_path / "underlying_project"
    proj_dir.mkdir(parents=True, exist_ok=True)
    (proj_dir / "index.py").write_text("print('safe')", encoding="utf-8")

    proj_res = client.post(
        "/api/v1/projects",
        json={"name": "独立代码项目", "root_path": str(proj_dir), "description": "底层被关联工程"},
    )
    assert proj_res.status_code == 200
    project_id = proj_res.json()["project_id"]

    # 2. Create Topic and link the project
    topic_res = client.post(
        "/api/v1/topics",
        json={
            "name": "待删除的临时研究专题",
            "project_ids": [project_id],
        },
    )
    assert topic_res.status_code == 200
    topic_id = topic_res.json()["topic_id"]

    # 3. Delete Topic
    del_res = client.delete(f"/api/v1/topics/{topic_id}")
    assert del_res.status_code == 200
    assert del_res.json()["deleted"] is True

    # 4. Verify the underlying project STILL EXISTS and is completely intact!
    check_proj = client.get(f"/api/v1/projects/{project_id}")
    assert check_proj.status_code == 200, "Project was accidentally deleted by topic deletion!"
    assert check_proj.json()["project_id"] == project_id
    assert Path(check_proj.json()["root_path"]).exists()
    assert (proj_dir / "index.py").exists()


# =========================================================================
# 4. Missing / Deleted Underlying Object Tolerance
# =========================================================================

def test_missing_underlying_object_tolerance(tmp_path: Path):
    """If an underlying document, project, or run is missing/deleted outside the topic,
    topic detail must degrade gracefully (available=False) without throwing 500 error.
    """
    app, repo, store, _ = _build_g5_test_environment(tmp_path)
    client = TestClient(app)

    create_res = client.post(
        "/api/v1/topics",
        json={
            "name": "容错测试专题",
            "document_ids": ["doc-ghost-nonexistent"],
            "project_ids": ["proj-ghost-nonexistent"],
            "run_ids": ["run-ghost-nonexistent"],
            "note_ids": ["note-ghost-nonexistent"],
            "conversation_ids": ["conv-ghost-nonexistent"],
        },
    )
    assert create_res.status_code == 200
    topic_id = create_res.json()["topic_id"]

    get_res = client.get(f"/api/v1/topics/{topic_id}")
    assert get_res.status_code == 200, "Must return 200 even with missing underlying objects"
    detail = get_res.json()

    # Check graceful fallback for all linked missing entities
    ghost_doc = next((d for d in detail["documents"] if d["document_id"] == "doc-ghost-nonexistent"), None)
    assert ghost_doc is not None
    assert ghost_doc["available"] is False

    ghost_proj = next((p for p in detail["projects"] if p["project_id"] == "proj-ghost-nonexistent"), None)
    assert ghost_proj is not None
    assert ghost_proj["available"] is False

    ghost_run = next((r for r in detail["runs"] if r["run_id"] == "run-ghost-nonexistent"), None)
    assert ghost_run is not None
    assert ghost_run["available"] is False

    ghost_note = next((n for n in detail["notes"] if n["note_id"] == "note-ghost-nonexistent"), None)
    assert ghost_note is not None
    assert ghost_note["available"] is False


# =========================================================================
# 5. Recent Location Tracking & Resume
# =========================================================================

def test_recent_location_tracking_and_resume(tmp_path: Path):
    app, repo, store, _ = _build_g5_test_environment(tmp_path)
    client = TestClient(app)

    create_res = client.post("/api/v1/topics", json={"name": "工作位置测试专题"})
    assert create_res.status_code == 200
    topic_id = create_res.json()["topic_id"]

    # Record last active location
    loc_payload = {
        "section": "research",
        "object_id": "run-verified-001",
        "label": "学术基准对比测试报告",
    }
    loc_res = client.post(f"/api/v1/topics/{topic_id}/location", json=loc_payload)
    assert loc_res.status_code == 200
    loc_data = loc_res.json()
    assert loc_data["recent_location"]["section"] == "research"
    assert loc_data["recent_location"]["object_id"] == "run-verified-001"
    assert loc_data["recent_location"]["label"] == "学术基准对比测试报告"
    assert "timestamp" in loc_data["recent_location"]


# =========================================================================
# 6. Topic vs Project Semantic Isolation
# =========================================================================

def test_topic_project_semantic_isolation(tmp_path: Path):
    """Ensure Topic IDs and Project IDs are cleanly isolated and never confused."""
    app, repo, store, _ = _build_g5_test_environment(tmp_path)
    client = TestClient(app)

    # 1. Create a Topic
    t_res = client.post("/api/v1/topics", json={"name": "隔离性专题"})
    assert t_res.status_code == 200
    topic_id = t_res.json()["topic_id"]
    assert topic_id.startswith("topic-")

    # 2. Accessing /api/v1/projects/{topic_id} must return 404 (not a project)
    proj_check = client.get(f"/api/v1/projects/{topic_id}")
    assert proj_check.status_code == 404

    # 3. Accessing /api/v1/topics/{project_id} with a project ID must return 404 (not a topic)
    topic_check = client.get("/api/v1/topics/proj-conflux-weave")
    assert topic_check.status_code == 404
