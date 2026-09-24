"""Tests for P7-B: 记忆生命周期治理与物理向量清理.

验收点：
1. Migration 13 增加 is_pinned, user_feedback, expires_at 字段与默认值
2. MemoryStore 治理接口（pin_memory, set_feedback, set_expiry, list_active_memory_ids）
3. 召回过滤过期记忆（expires_at <= now 自动剔除）
4. 召回排序生命周期加权：置顶优先 (+0.50), 用户赞同 (+0.25), 用户降权 (-0.35) 与 reason 说明
5. LanceDB 物理向量删除（delete_memory 联动 delete_memory_vector）
6. 孤儿向量物理清理（purge_deleted_vectors / vacuum 接口）
7. REST API 端点验证（/pin, /feedback, /expire, /vacuum, DELETE 联动）
"""

from __future__ import annotations

import json
from pathlib import Path
import pytest

from conflux_weave.runtime.memory_store import (
    HierarchicalMemoryStore,
    MemoryCategory,
    MemoryScope,
    MemoryStatus,
)
from conflux_weave.memory_recall import MemoryRecallService, RecallRecord


class DeterministicStubEmbedder:
    """确定性维度向量生成器，便于在离线测试中触发 LanceDB 物理索引路径。"""

    def __init__(self, dim: int = 4):
        self.dim = dim

    def __call__(self, texts: list[str]) -> list[list[float]]:
        results = []
        for text in texts:
            val = float(len(text) % 10) / 10.0 + 0.1
            results.append([val] * self.dim)
        return results


def test_sqlite_migration_13_columns(tmp_path: Path):
    """验证 Migration 13 增加的治理字段与其默认值。"""
    store = HierarchicalMemoryStore(tmp_path / "memory.sqlite3")
    item = store.create_memory(
        scope=MemoryScope.USER,
        target_id="user_1",
        category=MemoryCategory.PREFERENCE,
        statement="默认优先使用中文回复",
    )
    assert item.is_pinned is False
    assert item.user_feedback == 0
    assert item.expires_at is None

    conn = store._connect()
    try:
        row = conn.execute(
            "SELECT is_pinned, user_feedback, expires_at FROM memories WHERE memory_id = ?",
            (item.memory_id,),
        ).fetchone()
        assert row["is_pinned"] == 0
        assert row["user_feedback"] == 0
        assert row["expires_at"] is None
    finally:
        conn.close()


def test_memory_store_governance_operations(tmp_path: Path):
    """验证 MemoryStore pin, feedback, expire 及 list_active_memory_ids 操作。"""
    store = HierarchicalMemoryStore(tmp_path / "memory.sqlite3")
    item1 = store.create_memory(
        scope=MemoryScope.USER,
        target_id="user_1",
        category=MemoryCategory.PREFERENCE,
        statement="偏好简洁代码",
    )
    item2 = store.create_memory(
        scope=MemoryScope.PROJECT,
        target_id="proj_1",
        category=MemoryCategory.DECISION,
        statement="架构采用事件总线",
    )

    # 1. pin_memory
    pinned = store.pin_memory(item1.memory_id, is_pinned=True)
    assert pinned is not None
    assert pinned.is_pinned is True
    unpinned = store.pin_memory(item1.memory_id, is_pinned=False)
    assert unpinned is not None
    assert unpinned.is_pinned is False

    # 2. set_feedback
    fb1 = store.set_feedback(item1.memory_id, feedback=1)
    assert fb1 is not None
    assert fb1.user_feedback == 1
    fb_neg = store.set_feedback(item1.memory_id, feedback=-1)
    assert fb_neg is not None
    assert fb_neg.user_feedback == -1

    # 3. set_expiry
    exp_iso = "2030-01-01T00:00:00Z"
    exp = store.set_expiry(item2.memory_id, expires_at=exp_iso)
    assert exp is not None
    assert exp.expires_at == exp_iso
    cleared = store.set_expiry(item2.memory_id, expires_at=None)
    assert cleared is not None
    assert cleared.expires_at is None

    # 4. list_active_memory_ids
    active_ids = store.list_active_memory_ids()
    assert item1.memory_id in active_ids
    assert item2.memory_id in active_ids

    # 5. 不存在的 ID 返回 None
    assert store.pin_memory("non-existent") is None
    assert store.set_feedback("non-existent", 1) is None
    assert store.set_expiry("non-existent", "2030-01-01T00:00:00Z") is None


def test_recall_filters_expired_memories(tmp_path: Path):
    """验收：已过期的记忆不会被召回。"""
    store = HierarchicalMemoryStore(tmp_path / "memory.sqlite3")
    service = MemoryRecallService(store)

    # 创建一条已过期的记忆 (expires_at 设为过去)
    past_iso = "2020-01-01T00:00:00Z"
    store.create_memory(
        scope=MemoryScope.USER,
        target_id="user_default",
        category=MemoryCategory.PREFERENCE,
        statement="过期的偏好设置",
        expires_at=past_iso,
    )

    # 创建一条未过期的记忆 (expires_at 设为未来)
    future_iso = "2035-01-01T00:00:00Z"
    valid_item = store.create_memory(
        scope=MemoryScope.USER,
        target_id="user_default",
        category=MemoryCategory.PREFERENCE,
        statement="有效的偏好设置",
        expires_at=future_iso,
    )

    records = service.recall("偏好设置", user_id="user_default")
    assert len(records) == 1
    assert records[0].memory_id == valid_item.memory_id
    assert records[0].statement == "有效的偏好设置"


def test_recall_scoring_pinned_and_feedback_weights(tmp_path: Path):
    """验收：置顶优先 (+0.50) 与用户反馈 (+0.25 / -0.35) 权重调整及可解释 reason。"""
    store = HierarchicalMemoryStore(tmp_path / "memory.sqlite3")
    service = MemoryRecallService(store)

    item_normal = store.create_memory(
        scope=MemoryScope.USER,
        target_id="user_default",
        category=MemoryCategory.FACT,
        statement="普通事实知识",
    )
    item_pinned = store.create_memory(
        scope=MemoryScope.USER,
        target_id="user_default",
        category=MemoryCategory.FACT,
        statement="置顶事实知识",
        is_pinned=True,
    )
    item_disliked = store.create_memory(
        scope=MemoryScope.USER,
        target_id="user_default",
        category=MemoryCategory.FACT,
        statement="被降权事实知识",
        user_feedback=-1,
    )
    item_liked = store.create_memory(
        scope=MemoryScope.USER,
        target_id="user_default",
        category=MemoryCategory.FACT,
        statement="被赞同事实知识",
        user_feedback=1,
    )

    records = service.recall("事实知识", user_id="user_default")
    rec_by_id = {r.memory_id: r for r in records}

    # 1. 验证置顶项排在第一位，且 reason 包含 '置顶优先'
    assert records[0].memory_id == item_pinned.memory_id
    assert "置顶优先" in records[0].reason
    assert records[0].is_pinned is True

    # 2. 验证点赞项得分高于普通项
    pinned_rec = rec_by_id[item_pinned.memory_id]
    liked_rec = rec_by_id[item_liked.memory_id]
    normal_rec = rec_by_id[item_normal.memory_id]
    disliked_rec = rec_by_id[item_disliked.memory_id]

    assert "用户赞同" in liked_rec.reason
    assert "用户降权" in disliked_rec.reason
    assert liked_rec.user_feedback == 1
    assert disliked_rec.user_feedback == -1

    assert liked_rec.score > normal_rec.score
    assert normal_rec.score > disliked_rec.score
    assert pinned_rec.score > liked_rec.score


def test_lancedb_physical_vector_lifecycle(tmp_path: Path):
    """验收：删除记忆时物理同步删除 LanceDB 向量，vacuum 能够清除孤儿向量。"""
    store = HierarchicalMemoryStore(tmp_path / "memory.sqlite3")
    embedder = DeterministicStubEmbedder()
    lancedb_dir = tmp_path / "lancedb"
    service = MemoryRecallService(store, embedder=embedder, lancedb_root=lancedb_dir)

    item1 = store.create_memory(
        scope=MemoryScope.USER,
        target_id="user_default",
        category=MemoryCategory.FACT,
        statement="测试向量一",
    )
    item2 = store.create_memory(
        scope=MemoryScope.USER,
        target_id="user_default",
        category=MemoryCategory.FACT,
        statement="测试向量二",
    )

    # 触发索引构建
    service.recall("测试向量", user_id="user_default")
    assert service._table is not None

    table_records = service._table.to_arrow().to_pylist()
    mids_in_lancedb = {r["memory_id"] for r in table_records}
    assert item1.memory_id in mids_in_lancedb
    assert item2.memory_id in mids_in_lancedb

    # 1. 显式删除 item1 的向量
    deleted = service.delete_memory_vector(item1.memory_id)
    assert deleted is True
    assert item1.memory_id not in service._embedding_cache

    table_records_after = service._table.to_arrow().to_pylist()
    mids_after = {r["memory_id"] for r in table_records_after}
    assert item1.memory_id not in mids_after
    assert item2.memory_id in mids_after

    # 2. 模拟孤儿向量清理：直接从 SQLite 删除 item2，但 LanceDB 中仍存有
    store.delete_memory(item2.memory_id)
    active_ids = store.list_active_memory_ids()
    assert item2.memory_id not in active_ids

    # 此时 LanceDB 仍有 item2 向量
    assert item2.memory_id in {r["memory_id"] for r in service._table.to_arrow().to_pylist()}

    # 执行 purge_deleted_vectors (Vacuum)
    purged_count = service.purge_deleted_vectors(active_ids)
    assert purged_count == 1

    table_records_final = service._table.to_arrow().to_pylist()
    mids_final = {r["memory_id"] for r in table_records_final if r["memory_id"]}
    assert len(mids_final) == 0


def test_api_governance_and_vacuum_e2e(tmp_path: Path):
    """验收：FastAPI 端点 /pin, /feedback, /expire, /vacuum, DELETE 联动。"""
    from starlette.testclient import TestClient
    from conflux_weave.runtime import LocalArtifactStore, SQLiteRuntimeRepository
    from conflux_weave.harness.orchestration import CompositeOrchestrator, UnavailableTaskRuntime
    from conflux_weave.server import create_app

    artifact_store = LocalArtifactStore(tmp_path / "artifacts")
    db_path = tmp_path / "db" / "conflux-weave.sqlite3"
    repository = SQLiteRuntimeRepository(db_path, artifact_store, clock=lambda: "2026-09-10T12:00:00Z")
    dummy_runtime = UnavailableTaskRuntime(
        repository,
        executor_id="dummy",
        task_kinds=("paper_discovery",),
        message="dummy",
    )
    orchestrator = CompositeOrchestrator(repository, (dummy_runtime,))

    app = create_app(repository, orchestrator)
    client = TestClient(app)

    # 1. 创建一条记忆
    res = client.post(
        "/api/v1/memories",
        json={
            "scope": "user",
            "target_id": "user_default",
            "category": "preference",
            "statement": "API 生命周期测试记忆",
            "confidence": 0.9,
        },
    )
    assert res.status_code == 200, res.text
    data = res.json()
    mid = data["memory_id"]
    assert data["is_pinned"] is False
    assert data["user_feedback"] == 0
    assert data["expires_at"] is None

    # 2. Pin memory
    pin_res = client.post(f"/api/v1/memories/{mid}/pin", json={"is_pinned": True})
    assert pin_res.status_code == 200
    assert pin_res.json()["is_pinned"] is True

    # 3. Feedback memory
    fb_res = client.post(f"/api/v1/memories/{mid}/feedback", json={"user_feedback": 1})
    assert fb_res.status_code == 200
    assert fb_res.json()["user_feedback"] == 1

    # 4. Expire memory
    exp_iso = "2029-12-31T23:59:59Z"
    exp_res = client.post(f"/api/v1/memories/{mid}/expire", json={"expires_at": exp_iso})
    assert exp_res.status_code == 200
    assert exp_res.json()["expires_at"] == exp_iso

    # 5. List memories 验证新字段存在
    list_res = client.get("/api/v1/memories?scope=user&target_id=user_default")
    assert list_res.status_code == 200
    items = list_res.json()["items"]
    target = next((item for item in items if item["memory_id"] == mid), None)
    assert target is not None
    assert target["is_pinned"] is True
    assert target["user_feedback"] == 1
    assert target["expires_at"] == exp_iso

    # 6. Recall 验证字段与理由
    rec_res = client.get("/api/v1/memories/recall?query=生命周期")
    assert rec_res.status_code == 200
    rec_items = rec_res.json()["items"]
    assert len(rec_items) >= 1
    rec_target = next((r for r in rec_items if r["memory_id"] == mid), None)
    assert rec_target is not None
    assert rec_target["is_pinned"] is True
    assert rec_target["user_feedback"] == 1
    assert "置顶优先" in rec_target["reason"]
    assert "用户赞同" in rec_target["reason"]

    # 7. Vacuum endpoint
    vac_res = client.post("/api/v1/memories/vacuum")
    assert vac_res.status_code == 200
    vac_data = vac_res.json()
    assert "purged_vectors_count" in vac_data
    assert "active_memories_count" in vac_data
    assert vac_data["active_memories_count"] >= 1

    # 8. Delete memory (cascade vector deletion)
    del_res = client.delete(f"/api/v1/memories/{mid}")
    assert del_res.status_code == 200
    assert del_res.json()["ok"] is True
