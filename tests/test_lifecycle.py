"""P6-A2 数据生命周期测试：归档 / 软删除 / 恢复 / 重命名（后端 + API）。"""

import json

import pytest
from starlette.testclient import TestClient

from conflux_weave.chat import ChatService
from conflux_weave.provider import OpenAICompatibleChatAdapter, ProviderConfig
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
from conflux_weave.runtime import (
    LocalArtifactStore,
    RecordNotFound,
    SQLiteRuntimeRepository,
)
from conflux_weave.runtime.durable_paper_shared import RANK_CHECKPOINT
from conflux_weave.server import WorkerLoop, create_app

NOW = "2026-09-12T09:00:00Z"


class PassiveRuntime:
    executor_id = "passive@v1"
    task_kinds = ("paper_discovery",)

    def work_once(self, **kwargs):
        return None


class NoopTransport:
    def post(self, *args, **kwargs):
        raise AssertionError("provider must not be called in lifecycle tests")


def build_repository(tmp_path):
    store = LocalArtifactStore(tmp_path / "artifacts")
    repository = SQLiteRuntimeRepository(tmp_path / "db" / "runtime.sqlite3", store, clock=lambda: NOW)
    return repository, store


def _submit_run(repository, suffix: str):
    task = TaskSpec(
        task_id=f"task-{suffix}",
        kind="paper_discovery",
        input={"query": f"query {suffix}"},
        requested_policy="p",
        idempotency_key=f"key-{suffix}",
    )
    run = RunRecord(
        run_id=f"run-{suffix}",
        task_id=task.task_id,
        status=RunStatus.ACCEPTED,
        workflow_version="v",
        config_snapshot_ref="c",
        budget=BudgetLedger(1, 1, 1, "unavailable", 1, 1, 1),
        created_at=NOW,
        updated_at=NOW,
    )
    repository.submit_task(
        task, run,
        [StepRecord(step_id=f"step-{suffix}", run_id=run.run_id, kind="publish_delivery", attempt=1, status=StepStatus.PENDING)],
    )
    return run.run_id


def _build_chat_service(tmp_path):
    store = LocalArtifactStore(tmp_path / "chat-artifacts")
    adapter = OpenAICompatibleChatAdapter(store, ProviderConfig("https://provider.example/v1", "secret", "chat"), transport=NoopTransport())
    return ChatService(adapter, tmp_path / "chat.sqlite3", artifact_store=store)


# ------------------------------------------------------------- Repository 层


def test_migration_9_adds_lifecycle_columns_and_document_table(tmp_path) -> None:
    repository, _ = build_repository(tmp_path)
    with repository._connect() as conn:
        run_columns = {row[1] for row in conn.execute("PRAGMA table_info(runs)")}
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "archived_at" in run_columns
    assert "deleted_at" in run_columns
    assert "document_lifecycle" in tables
    assert repository.get_run_lifecycle_map([]) == {}


def test_run_lifecycle_transitions_are_idempotent_and_reversible(tmp_path) -> None:
    repository, _ = build_repository(tmp_path)
    run_id = _submit_run(repository, "l1")
    assert repository.get_run_lifecycle(run_id) == "active"

    assert repository.set_run_lifecycle(run_id, "archive") == "archived"
    assert repository.set_run_lifecycle(run_id, "archive") == "archived"  # 幂等
    assert repository.set_run_lifecycle(run_id, "restore") == "active"
    assert repository.set_run_lifecycle(run_id, "delete") == "deleted"
    assert repository.set_run_lifecycle(run_id, "delete") == "deleted"
    assert repository.set_run_lifecycle(run_id, "restore") == "active"
    with pytest.raises(RecordNotFound):
        repository.set_run_lifecycle("run-missing", "archive")


def test_list_runs_filters_by_lifecycle(tmp_path) -> None:
    repository, _ = build_repository(tmp_path)
    active = _submit_run(repository, "active1")
    archived = _submit_run(repository, "arch1")
    deleted = _submit_run(repository, "del1")
    repository.set_run_lifecycle(archived, "archive")
    repository.set_run_lifecycle(deleted, "delete")

    def ids(state):
        return {item.run.run_id for item in repository.list_runs(lifecycle=state).items}

    assert ids("active") == {active}
    assert ids("archived") == {archived}
    assert ids("deleted") == {deleted}
    assert ids("all") == {active, archived, deleted}
    # 默认必须排除归档与删除
    default = {item.run.run_id for item in repository.list_runs().items}
    assert default == {active}


def test_document_lifecycle_roundtrip(tmp_path) -> None:
    repository, _ = build_repository(tmp_path)
    assert repository.get_document_lifecycle("doc-1") == "active"
    repository.set_document_lifecycle("doc-1", "delete")
    assert repository.get_document_lifecycle("doc-1") == "deleted"
    repository.set_document_lifecycle("doc-1", "restore")
    assert repository.get_document_lifecycle("doc-1") == "active"
    repository.set_document_lifecycle("doc-1", "archive")
    assert repository.get_document_lifecycle_map()["doc-1"] == "archived"


def test_soft_deleted_run_keeps_delivery_and_evidence(tmp_path) -> None:
    """软删除不触碰交付关系：Evidence/Artifact/报告仍可读取与导出。"""
    repository, store = build_repository(tmp_path)
    task = TaskSpec(
        task_id="task-keep", kind="paper_discovery", input={"query": "q"},
        requested_policy="p", idempotency_key="key-keep",
    )
    run = RunRecord(
        run_id="run-keep", task_id=task.task_id, status=RunStatus.ACCEPTED,
        workflow_version="v", config_snapshot_ref="c", budget=BudgetLedger(1, 1, 1, "unavailable", 1, 1, 1),
        created_at=NOW, updated_at=NOW,
    )
    repository.submit_task(task, run, [
        StepRecord(step_id="step-keep-rank", run_id=run.run_id, kind="rank_candidates", attempt=1, status=StepStatus.PENDING),
        StepRecord(step_id="step-keep-publish", run_id=run.run_id, kind="publish_delivery", attempt=1, status=StepStatus.PENDING),
    ])
    repository.transition_run(run.run_id, RunStatus.QUEUED, updated_at=NOW)
    run_id = run.run_id
    claim = repository.claim_next_step("worker", lease_seconds=60, now=NOW)
    assert claim is not None
    checkpoint = store.put_json(
        {"schema_version": RANK_CHECKPOINT, "evidence": [
            {"evidence_id": "evidence-0001", "source_snapshot_id": "snap", "locator": {}, "quote": "q", "extraction_method": "fixture"}
        ]},
        producer_step_id=claim.step_id,
        schema_version=RANK_CHECKPOINT,
    )
    repository.complete_attempt(claim, (checkpoint,), now=NOW)
    publish_claim = repository.claim_next_step("worker", lease_seconds=60, now=NOW)
    assert publish_claim is not None
    report = store.put_bytes(
        "# 报告\n\n正文\n".encode("utf-8"),
        media_type="text/markdown; charset=utf-8",
        producer_step_id=publish_claim.step_id,
        schema_version="conflux-weave.durable-research-report.v1",
    )
    repository.publish_delivery(
        run_id, RunStatus.SUCCEEDED,
        DeliveryRecord(run_id=run_id, disposition=DeliveryDisposition.COMPLETE, artifact_refs=(report.artifact_id,), evidence_refs=("evidence-0001",)),
        (report,), claim=publish_claim, published_at=NOW,
    )
    repository.set_run_lifecycle(run_id, "delete")

    from conflux_weave.export_bundle import ExportService

    doc = ExportService(repository).collect_run_export(run_id)
    assert doc.evidence[0]["evidence_id"] == "evidence-0001"


# --------------------------------------------------------------- 对话生命周期


def test_conversation_rename_archive_delete_restore(tmp_path) -> None:
    service = _build_chat_service(tmp_path)
    conversation_id = service._ensure_conversation(None, "原始标题", "direct")
    service._append(
        __import__("conflux_weave.chat", fromlist=["ChatMessage"]).ChatMessage(
            "msg-1", conversation_id, "user", "direct", "你好", NOW
        ),
    )

    renamed = service.rename_conversation(conversation_id, "  新标题  ")
    assert renamed is not None and renamed["title"] == "新标题"
    with pytest.raises(ValueError):
        service.rename_conversation(conversation_id, "   ")

    archived = service.set_conversation_lifecycle(conversation_id, "archive")
    assert archived["lifecycle"] == "archived"
    assert service.conversations(status="active") == []
    assert len(service.conversations(status="archived")) == 1

    deleted = service.set_conversation_lifecycle(conversation_id, "delete")
    assert deleted["lifecycle"] == "deleted"
    assert len(service.conversations(status="deleted")) == 1

    # 软删除后消息保留；恢复后原 ID 不变
    assert service.load_message("msg-1") is not None
    restored = service.set_conversation_lifecycle(conversation_id, "restore")
    assert restored["lifecycle"] == "active"
    assert service.conversation_record(conversation_id)["conversation_id"] == conversation_id

    # 已删除对话禁止改名
    service.set_conversation_lifecycle(conversation_id, "delete")
    assert service.rename_conversation(conversation_id, "不允许") is None


# ------------------------------------------------------------------ API 层


def _api_app(tmp_path):
    repository, store = build_repository(tmp_path)
    run_id = _submit_run(repository, "api")
    chat_service = _build_chat_service(tmp_path)
    conversation_id = chat_service._ensure_conversation(None, "API 对话", "direct")
    chat_service._append(
        __import__("conflux_weave.chat", fromlist=["ChatMessage"]).ChatMessage(
            "msg-1", conversation_id, "user", "direct", "内容", NOW
        ),
    )
    # 文库注册表（与运行库同目录）
    registry_path = repository.database_path.with_name("library-registry.json")
    registry_path.write_text(
        json.dumps([{"document_id": "document-sha256-" + "a" * 64, "relative_path": "papers/demo.pdf", "status": "imported", "title": "演示论文"}]),
        encoding="utf-8",
    )
    app = create_app(repository, PassiveRuntime(), chat_service=chat_service, worker=WorkerLoop(PassiveRuntime(), interval_seconds=10))
    return TestClient(app), run_id, conversation_id


def test_api_conversation_lifecycle_endpoints(tmp_path) -> None:
    client, _run_id, conversation_id = _api_app(tmp_path)

    renamed = client.patch(f"/api/v1/conversations/{conversation_id}", json={"title": "改名后"})
    assert renamed.status_code == 200
    assert renamed.json()["title"] == "改名后"

    deleted = client.delete(f"/api/v1/conversations/{conversation_id}")
    assert deleted.status_code == 200
    assert deleted.json()["lifecycle"] == "deleted"

    active_list = client.get("/api/v1/conversations").json()
    assert all(item["conversation_id"] != conversation_id for item in active_list["items"])
    deleted_list = client.get("/api/v1/conversations?status=deleted").json()
    assert any(item["conversation_id"] == conversation_id for item in deleted_list["items"])
    assert all(item["lifecycle"] == "deleted" for item in deleted_list["items"])

    restored = client.post(f"/api/v1/conversations/{conversation_id}/restore")
    assert restored.status_code == 200
    assert restored.json()["lifecycle"] == "active"
    assert any(item["conversation_id"] == conversation_id for item in client.get("/api/v1/conversations").json()["items"])

    missing = client.delete("/api/v1/conversations/conv-missing")
    assert missing.status_code == 404
    bad_title = client.patch(f"/api/v1/conversations/{conversation_id}", json={"title": ""})
    assert bad_title.status_code == 422


def test_api_run_lifecycle_endpoints(tmp_path) -> None:
    client, run_id, _ = _api_app(tmp_path)

    archived = client.post(f"/api/v1/runs/{run_id}/lifecycle", json={"action": "archive"})
    assert archived.status_code == 200
    assert archived.json()["lifecycle"] == "archived"

    default_page = client.get("/api/v1/runs").json()
    assert all(item["run_id"] != run_id for item in default_page["items"])
    archived_page = client.get("/api/v1/runs?lifecycle=archived").json()
    match = next(item for item in archived_page["items"] if item["run_id"] == run_id)
    assert match["lifecycle"] == "archived"
    assert match["archived_at"] is not None

    assert client.post(f"/api/v1/runs/{run_id}/lifecycle", json={"action": "restore"}).json()["lifecycle"] == "active"
    missing = client.post("/api/v1/runs/run-missing/lifecycle", json={"action": "archive"})
    assert missing.status_code == 404


def test_api_document_lifecycle_endpoint(tmp_path) -> None:
    client, _run_id, _ = _api_app(tmp_path)
    document_id = "document-sha256-" + "a" * 64

    assert client.get("/api/v1/library").json()["total"] == 1
    archived = client.post(f"/api/v1/library/documents/{document_id}/lifecycle", json={"action": "archive"})
    assert archived.status_code == 200

    active = client.get("/api/v1/library").json()
    assert active["total"] == 0
    archived_view = client.get("/api/v1/library?status=archived").json()
    assert archived_view["total"] == 1
    assert archived_view["items"][0]["lifecycle"] == "archived"

    assert client.post(f"/api/v1/library/documents/{document_id}/lifecycle", json={"action": "restore"}).json()["lifecycle"] == "active"
    assert client.get("/api/v1/library").json()["total"] == 1
