"""P6-B1 语义记忆召回测试：混合排序、跨项目隔离、配额上限、召回原因与确定性兜底。

全部离线：语义路径用确定性 stub embedder，不发生任何外部调用。
"""

import json

import pytest

from conflux_weave.memory_agent import MemoryAgent
from conflux_weave.memory_recall import (
    MemoryRecallService,
    canonical_memory_text,
    lexical_tokens,
)
from conflux_weave.runtime.memory_store import (
    HierarchicalMemoryStore,
    MemoryCategory,
    MemoryScope,
    MemoryStatus,
)

NOW = "2026-09-12T10:00:00Z"


def build_store(tmp_path) -> HierarchicalMemoryStore:
    return HierarchicalMemoryStore(tmp_path / "memory.sqlite3")


def seed(store: HierarchicalMemoryStore, *, scope, target, category, statement, confidence=0.8, updated_at=None):
    item = store.create_memory(
        scope=scope,
        target_id=target,
        category=category,
        statement=statement,
        confidence=confidence,
        source_type="test",
        source_id="seed",
    )
    if updated_at:
        conn = store._connect()
        try:
            conn.execute("UPDATE memories SET updated_at = ? WHERE memory_id = ?", (updated_at, item.memory_id))
            conn.commit()
        finally:
            conn.close()
    return item


class StubEmbedder:
    """确定性 embedder：按关键词给出正交向量，验证语义路径可注入与排序。"""

    def __init__(self, vocabulary: dict[str, int]):
        self.vocabulary = vocabulary

    def __call__(self, texts: list[str]) -> list[list[float]]:
        vectors = []
        for text in texts:
            vector = [0.0] * len(self.vocabulary)
            for word, index in self.vocabulary.items():
                if word in text:
                    vector[index] = 1.0
            vectors.append(vector)
        return vectors


def test_lexical_tokens_handles_cjk_and_latin() -> None:
    tokens = lexical_tokens("向量索引 vector")
    assert "向量" in tokens and "量索" in tokens and "索引" in tokens
    assert "vector" in tokens


def test_relevant_memory_beats_newer_irrelevant_one(tmp_path) -> None:
    """验收：相关记忆优先于单纯最新记忆。"""
    store = build_store(tmp_path)
    relevant = seed(store, scope=MemoryScope.USER, target="user_default",
                    category=MemoryCategory.PREFERENCE, statement="输出必须使用中文",
                    updated_at="2026-08-01T00:00:00Z")
    newer = seed(store, scope=MemoryScope.USER, target="user_default",
                 category=MemoryCategory.FACT, statement="用户上周测试了 GIS 数据管线",
                 updated_at="2026-09-12T00:00:00Z")

    service = MemoryRecallService(store)  # 无 embedder：词法+recency 确定性路径
    records = service.recall("写作的语言偏好", user_id="user_default")

    ids = [record.memory_id for record in records]
    assert relevant.memory_id in ids
    assert ids[0] == relevant.memory_id
    # 无关的新记忆被相关性门槛挡住，不参与注入
    assert newer.memory_id not in ids
    assert "词法匹配" in records[0].reason


def test_semantic_match_beats_recency_with_stub_embedder(tmp_path) -> None:
    store = build_store(tmp_path)
    semantic_hit = seed(store, scope=MemoryScope.USER, target="user_default",
                        category=MemoryCategory.PREFERENCE, statement="偏好简洁回答",
                        updated_at="2026-07-01T00:00:00Z")
    newer_noise = seed(store, scope=MemoryScope.USER, target="user_default",
                       category=MemoryCategory.FACT, statement="正在研究稀疏注意力",
                       updated_at="2026-09-12T00:00:00Z")

    embedder = StubEmbedder({"偏好": 0, "简洁": 1, "回答": 2, "稀疏": 3, "注意力": 4})
    service = MemoryRecallService(store, embedder, lancedb_root=tmp_path / "lancedb")
    records = service.recall("我希望回答保持简洁", user_id="user_default")

    assert service.semantic_available is True
    ids = [record.memory_id for record in records]
    assert ids[0] == semantic_hit.memory_id
    assert newer_noise.memory_id not in ids  # 无关且词法不命中的记忆不注入
    assert records[0].semantic_score is not None and records[0].semantic_score > 0.8
    assert "语义匹配" in records[0].reason


def test_no_cross_project_leakage(tmp_path) -> None:
    """验收：不发生跨项目记忆泄漏。"""
    store = build_store(tmp_path)
    seed(store, scope=MemoryScope.PROJECT, target="project-a",
         category=MemoryCategory.CONSTRAINT, statement="项目A必须用 PostgreSQL")
    seed(store, scope=MemoryScope.PROJECT, target="project-b",
         category=MemoryCategory.CONSTRAINT, statement="项目B必须用 MySQL")

    service = MemoryRecallService(store)
    records = service.recall("数据库 选型 约束", user_id="user_default", project_id="project-a")
    assert all(record.target_id == "project-a" for record in records)
    assert all("MySQL" not in record.statement for record in records)


def test_scope_quota_caps_injection_volume(tmp_path) -> None:
    """验收：注入总量不超过既定上限（用户5/项目5/会话3，总数≤8）。"""
    store = build_store(tmp_path)
    for index in range(9):
        seed(store, scope=MemoryScope.USER, target="user_default",
             category=MemoryCategory.PREFERENCE, statement=f"用户偏好条目 {index} 偏好")
    service = MemoryRecallService(store)
    records = service.recall("偏好", user_id="user_default")
    assert len(records) <= 8
    user_count = sum(1 for record in records if record.scope == "user")
    assert user_count == 5


def test_no_relevant_memory_means_no_forced_injection(tmp_path) -> None:
    """验收：无答案时不强行注入（召回系统正常但无相关记忆 → 空结果）。"""
    store = build_store(tmp_path)
    seed(store, scope=MemoryScope.USER, target="user_default",
         category=MemoryCategory.PREFERENCE, statement="输出使用中文")
    embedder = StubEmbedder({"偏好": 0})
    service = MemoryRecallService(store, embedder, lancedb_root=tmp_path / "lancedb")
    records = service.recall("量子纠错表面码的最新阈值", user_id="user_default")
    assert records == []


def test_embedder_failure_falls_back_to_recency(tmp_path) -> None:
    """验收：召回失败时退回确定性路径（recency 兜底，不抛错）。"""
    store = build_store(tmp_path)
    older = seed(store, scope=MemoryScope.USER, target="user_default",
                 category=MemoryCategory.FACT, statement="记忆甲", updated_at="2026-06-01T00:00:00Z")
    newer = seed(store, scope=MemoryScope.USER, target="user_default",
                 category=MemoryCategory.FACT, statement="记忆乙", updated_at="2026-09-11T00:00:00Z")

    class ExplodingEmbedder:
        def __call__(self, texts):
            raise RuntimeError("provider down")

    service = MemoryRecallService(store, ExplodingEmbedder(), lancedb_root=tmp_path / "lancedb")
    records = service.recall("完全无关的查询", user_id="user_default")
    assert [record.memory_id for record in records] == [newer.memory_id, older.memory_id]
    assert all(record.reason == "确定性兜底(recency)" for record in records)


def test_recall_records_carry_id_scope_score_reason(tmp_path) -> None:
    """验收：召回记录包含 memory ID、scope、score 和 reason。"""
    store = build_store(tmp_path)
    seed(store, scope=MemoryScope.SESSION, target="conv-1",
         category=MemoryCategory.DECISION, statement="本会话约定使用 Python")
    service = MemoryRecallService(store)
    records = service.recall("会话约定 Python", user_id="user_default", conversation_id="conv-1")
    assert len(records) == 1
    record = records[0]
    assert record.memory_id.startswith("mem-")
    assert record.scope == "session"
    assert record.score > 0.0
    assert record.reason


def test_format_prompt_context_recall_path_respects_budget(tmp_path) -> None:
    """注入块 ≤ max_chars，且召回路径带 layer 标题与配额截断。"""
    store = build_store(tmp_path)
    for index in range(9):
        seed(store, scope=MemoryScope.USER, target="user_default",
             category=MemoryCategory.PREFERENCE, statement=f"很长的用户偏好描述第 {index} 条，包含大量文字" * 3)
    agent = MemoryAgent(store, recall_service=MemoryRecallService(store))
    block = agent.format_prompt_context(user_id="user_default", query="偏好", max_chars=400)
    assert len(block) <= 400
    assert "【用户偏好与习惯约定】" in block
    assert block.count("- [偏好]") == 5  # 用户层配额 5


def test_format_prompt_context_falls_back_without_recall(tmp_path) -> None:
    store = build_store(tmp_path)
    seed(store, scope=MemoryScope.USER, target="user_default",
         category=MemoryCategory.PREFERENCE, statement="输出使用中文")
    agent = MemoryAgent(store, recall_service=None)
    block = agent.format_prompt_context(user_id="user_default", query="偏好")
    assert "输出使用中文" in block


def test_canonical_text_and_recall_api_contract(tmp_path) -> None:
    store = build_store(tmp_path)
    item = seed(store, scope=MemoryScope.USER, target="user_default",
                category=MemoryCategory.CONSTRAINT, statement="禁止上传原始数据")
    assert canonical_memory_text(item) == "限制：禁止上传原始数据"
    service = MemoryRecallService(store)
    payload = [record.to_dict() for record in service.recall("上传 禁止", user_id="user_default")]
    assert set(payload[0].keys()) >= {
        "memory_id", "scope", "target_id", "category", "statement",
        "score", "reason", "semantic_score", "lexical_score", "recency_score",
    }
    assert json.dumps(payload, ensure_ascii=False)
