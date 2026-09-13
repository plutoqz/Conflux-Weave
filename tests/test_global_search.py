"""A1 全局搜索：FTS5 索引、跨类型检索、生命周期过滤、定位与 API 契约。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from conflux_weave.global_search import GlobalSearchService
from conflux_weave.chat import ChatService
from conflux_weave.runtime import SQLiteRuntimeRepository
from conflux_weave.runtime.artifacts import LocalArtifactStore
from conflux_weave.server import WorkerLoop, create_app


class _PassiveRuntime:
    executor_id = "passive-paper@v1"
    task_kinds = ("paper_discovery",)

    def work_once(self, *, now: str | None = None) -> None:
        return None


def _make_service(tmp_path: Path) -> tuple[GlobalSearchService, SQLiteRuntimeRepository, LocalArtifactStore]:
    store = LocalArtifactStore(tmp_path / "artifacts")
    repository = SQLiteRuntimeRepository(
        tmp_path / "db" / "runtime.sqlite3", store, clock=lambda: "2026-09-13T12:00:00Z"
    )
    # conversations/chat_messages 由 ChatService 自建（不在 runtime 迁移中）
    ChatService(None, repository.database_path)
    service = GlobalSearchService(repository.database_path)
    return service, repository, store


def test_trigram_cjk_search_across_types(tmp_path):
    service, _, _ = _make_service(tmp_path)
    service.index_object(
        "chat_message", "msg-1",
        title="研究方法讨论",
        body="深度研究代理的架构演进包括任务规划与检索聚合。",
        metadata={"conversation_id": "conv-1"},
        updated_at="2026-09-13T10:00:00Z",
    )
    service.index_object(
        "run", "run-1",
        title="深度研究 Agent 架构演进报告",
        body="正文：多智能体协作与事实核查闭环。",
        metadata={},
        updated_at="2026-09-13T11:00:00Z",
    )
    service.index_object(
        "paper", "paper-1",
        title="An autonomous GIS agent framework",
        body="Huan Ning 2025 geospatial retrieval",
        metadata={},
        updated_at="2026-09-13T09:00:00Z",
    )

    hits = service.search("架构")
    assert {hit.object_type for hit in hits} == {"chat_message", "run"}
    assert all(hit.type_label for hit in hits)
    assert all(hit.match_reason for hit in hits)

    # 类型过滤
    only_runs = service.search("架构", types=("run",))
    assert {hit.object_type for hit in only_runs} == {"run"}
    # 两字短查询走 LIKE 回退路径（trigram 最小 3 元）
    short = service.search("架构", types=("chat_message",))
    assert len(short) == 1
    assert short[0].object_id == "msg-1"
    assert short[0].snippet


def test_match_reason_and_locate_deep_links(tmp_path):
    service, _, _ = _make_service(tmp_path)
    service.index_object(
        "run", "run-9", title="RAG 评估报告", body="正文引用支撑", metadata={},
        updated_at="2026-09-13T10:00:00Z",
    )
    service.index_object(
        "chat_message", "msg-9", title="会话", body="关于 RAG 的问题", metadata={"conversation_id": "conv-9"},
        updated_at="2026-09-13T10:00:00Z",
    )
    title_hit = next(h for h in service.search("RAG 评估") if h.object_type == "run")
    assert "标题" in title_hit.match_reason
    assert title_hit.deep_link == "#/research?run_id=run-9"
    assert title_hit.locator == {"object_type": "run", "object_id": "run-9", "run_id": "run-9"}

    located = service.locate("run:run-9")
    assert located is not None
    assert located["deep_link"] == "#/research?run_id=run-9"
    assert service.locate("bogus") is None
    assert service.locate("unknown_type:x") is None


def test_lifecycle_filtering_archived_and_deleted(tmp_path):
    service, repository, _ = _make_service(tmp_path)
    conn = repository.database_path
    import sqlite3

    db = sqlite3.connect(conn)
    db.execute(
        "INSERT INTO conversations(conversation_id,title,created_at,updated_at,last_message_preview,message_count,active_mode,archived_at,deleted_at) "
        "VALUES('conv-arch','归档会话','2026-09-13T08:00:00Z','2026-09-13T08:00:00Z','p',1,'chat','2026-09-13T09:00:00Z',NULL)"
    )
    db.execute(
        "INSERT INTO conversations(conversation_id,title,created_at,updated_at,last_message_preview,message_count,active_mode,archived_at,deleted_at) "
        "VALUES('conv-del','删除会话','2026-09-13T08:00:00Z','2026-09-13T08:00:00Z','p',1,'chat',NULL,'2026-09-13T09:00:00Z')"
    )
    db.commit()
    db.close()

    service.index_object(
        "chat_message", "msg-a", title="归档会话", body="归档消息里的量子纠错讨论",
        metadata={"conversation_id": "conv-arch"}, updated_at="2026-09-13T08:30:00Z",
    )
    service.index_object(
        "chat_message", "msg-d", title="删除会话", body="删除消息里的量子纠错讨论",
        metadata={"conversation_id": "conv-del"}, updated_at="2026-09-13T08:30:00Z",
    )

    default_hits = service.search("量子纠错")
    assert [hit.object_id for hit in default_hits] == []

    archived_visible = service.search("量子纠错", include_archived=True)
    assert [hit.object_id for hit in archived_visible] == ["msg-a"]

    # 软删除消息被移除出索引后同样不可见
    service.remove_object("chat_message", "msg-a")
    assert service.search("量子纠错", include_archived=True) == []


def test_project_filter_zero_leak(tmp_path):
    service, _, _ = _make_service(tmp_path)
    service.index_object("run", "run-p", title="项目内报告", body="独特量子内容", metadata={},
                         updated_at="2026-09-13T10:00:00Z")
    # 第一批索引对象不携带 project 归属：项目过滤下零结果、零泄漏
    assert service.search("量子", project_id="proj-a") == []
    assert service.search("量子", project_id=None) != []


def test_reindex_backfills_from_authority(tmp_path):
    service, repository, store = _make_service(tmp_path)
    import sqlite3

    db = sqlite3.connect(repository.database_path)
    now = "2026-09-13T08:00:00Z"
    db.execute(
        "INSERT INTO conversations(conversation_id,title,created_at,updated_at,last_message_preview,message_count,active_mode) "
        "VALUES('conv-1','对话标题','" + now + "','" + now + "','p',1,'chat')"
    )
    db.execute(
        "INSERT INTO chat_messages(message_id,conversation_id,role,mode,content,created_at,context_artifact_id,turn_id,sequence,run_id) "
        "VALUES('msg-1','conv-1','user','chat','混合检索与重排的讨论','" + now + "','t1',1,1,NULL)"
    )
    db.execute(
        "INSERT INTO tasks(task_id,kind,input_json,requested_policy,idempotency_key,created_at) "
        "VALUES('task-1','deep_research','{\"objective\": \"冻结基准评估报告\"}','auto','idem-1','" + now + "')"
    )
    db.execute(
        "INSERT INTO runs(run_id,task_id,status,workflow_version,config_snapshot_ref,budget_json,created_at,updated_at) "
        "VALUES('run-1','task-1','succeeded','v1','cfg','{}','" + now + "','" + now + "')"
    )
    db.commit()
    db.close()

    counts = service.reindex(
        notes_registry=[{"note_id": "note-1", "document_id": "doc-1", "title": "精读笔记", "instruction": "补充", "created_at": now}],
        library_registry=[
            {"paper_id": "paper-1", "title": "Frozen Benchmark Paper", "authors": ["A"], "year": 2026},
        ],
        artifact_store=None,
    )
    assert counts["chat_message"] == 1
    assert counts["run"] == 1
    assert counts["note"] == 1
    assert counts["paper"] == 1

    hits = service.search("混合检索")
    assert hits and hits[0].object_type == "chat_message"
    assert hits[0].locator["conversation_id"] == "conv-1"
    run_hits = service.search("冻结基准")
    assert run_hits and run_hits[0].object_type == "run"
    note_hits = service.search("精读笔记")
    assert note_hits and note_hits[0].object_type == "note"
    paper_hits = service.search("Frozen Benchmark")
    assert paper_hits and paper_hits[0].object_type == "paper"


def test_index_delivery_hook(tmp_path):
    service, repository, store = _make_service(tmp_path)
    report = store.put_bytes(
        "# 深度研究报告\n\n正文内容：Token 用于预算记账。".encode("utf-8"),
        media_type="text/markdown",
        producer_step_id="test",
        schema_version="conflux-weave.durable-research-report.v1",
    )
    evidence = store.put_json(
        {
            "schema_version": "conflux-weave.durable-research-evidence.v1",
            "evidence": [
                {
                    "evidence_id": "evidence-0001",
                    "quote": "检索增强生成在 2026 年的评估基准演进",
                    "locator": {"title": "评估基准综述", "url": "https://example.org/a"},
                }
            ],
        },
        producer_step_id="test",
        schema_version="conflux-weave.durable-research-evidence.v1",
    )
    service.index_delivery(
        run_id="run-hook",
        objective="深度研究报告",
        report_artifact_id=report.artifact_id,
        evidence_artifact_id=evidence.artifact_id,
        artifact_store=store,
        updated_at="2026-09-13T12:00:00Z",
    )
    run_hits = service.search("预算记账")
    assert run_hits and run_hits[0].object_type == "run" and run_hits[0].object_id == "run-hook"
    ev_hits = service.search("评估基准演进")
    assert ev_hits and ev_hits[0].object_type == "evidence"
    assert ev_hits[0].locator == {"object_type": "evidence", "object_id": "evidence-0001", "run_id": "run-hook", "evidence_id": "evidence-0001"}


def test_search_api_endpoints(tmp_path):
    store = LocalArtifactStore(tmp_path / "artifacts")
    repository = SQLiteRuntimeRepository(
        tmp_path / "db" / "runtime.sqlite3", store, clock=lambda: "2026-09-13T12:00:00Z"
    )
    ChatService(None, repository.database_path)
    search_service = GlobalSearchService(repository.database_path)
    app = create_app(
        repository,
        _PassiveRuntime(),
        provider_configured=False,
        worker=WorkerLoop(_PassiveRuntime(), interval_seconds=10),
        search_service=search_service,
    )
    client = TestClient(app)
    search_service.index_object(
        "run", "run-api", title="全局搜索验收报告", body="跨对象类型检索正文", metadata={},
        updated_at="2026-09-13T12:00:00Z",
    )

    res = client.get("/api/v1/search", params={"q": "全局搜索"})
    assert res.status_code == 200
    data = res.json()
    assert data["total"] == 1
    hit = data["items"][0]
    assert hit["object_type"] == "run"
    assert hit["type_label"] == "研究报告"
    assert hit["deep_link"]
    assert hit["locator"]

    located = client.get(f"/api/v1/search/{hit['result_id']}/locate")
    assert located.status_code == 200
    assert located.json()["deep_link"] == hit["deep_link"]

    missing = client.get("/api/v1/search/run:nope/locate")
    assert missing.status_code == 404

    empty = client.get("/api/v1/search", params={"q": "完全不存在的查询词组"})
    assert empty.status_code == 200
    assert empty.json()["total"] == 0

    rebuilt = client.post("/api/v1/search/reindex")
    assert rebuilt.status_code == 200
    assert "run" in rebuilt.json()["indexed"]
