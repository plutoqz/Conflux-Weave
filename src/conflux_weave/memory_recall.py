"""P6-B1 语义记忆召回：结构化过滤 → 向量/词法候选 → recency/usefulness 重排 → 召回原因。

权威元数据仍只有 SQLite（HierarchicalMemoryStore）；LanceDB 仅作为已批准记忆的
独立向量索引缓存（召回集规模小，评分用进程内向量暴力内积即可，索引用于持久化
与跨进程复用）。Embedding 或索引不可用时自动退回确定性词法+recency 召回路径，
保证任何情况下召回都有结果且不超出既定注入上限。跨项目隔离由结构化过滤
（scope/target_id 精确匹配）保证：召回候选集永远先经过结构限定再打分。
"""

from __future__ import annotations

import math
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from conflux_weave.runtime.memory_store import (
    MemoryCategory,
    MemoryItem,
    MemoryScope,
    MemoryStatus,
)

# 每层注入配额与 P4.1 的 L0/L1 预算上限保持一致（用户5 / 项目5 / 会话3）。
SCOPE_QUOTA: dict[str, int] = {"user": 5, "project": 5, "session": 3}

CATEGORY_LABELS: dict[str, str] = {
    "preference": "偏好",
    "fact": "事实",
    "constraint": "限制",
    "decision": "约定",
}

_WEIGHT_SEMANTIC = 0.55
_WEIGHT_LEXICAL = 0.25
_WEIGHT_RECENCY = 0.12
_WEIGHT_USEFULNESS = 0.08

_RECENCY_HALF_LIFE_DAYS = 21.0

_CJK_RE = re.compile(r"[\u3400-\u9fff]+")
_LATIN_RE = re.compile(r"[a-zA-Z0-9]+")


def canonical_memory_text(item: MemoryItem) -> str:
    """MemoryRecord 的文本化表示（索引与词法评分共用同一表示）。"""
    category = item.category.value if isinstance(item.category, MemoryCategory) else str(item.category)
    label = CATEGORY_LABELS.get(category, "记忆")
    return f"{label}：{item.statement}"


def lexical_tokens(text: str) -> tuple[str, ...]:
    """CJK 二元 + 拉丁小写词的轻量分词（零依赖确定性）。"""
    tokens: list[str] = []
    for chunk in _CJK_RE.findall(text or ""):
        if len(chunk) == 1:
            tokens.append(chunk)
        else:
            tokens.extend(chunk[i : i + 2] for i in range(len(chunk) - 1))
    tokens.extend(chunk.lower() for chunk in _LATIN_RE.findall(text or ""))
    return tuple(tokens)


@dataclass(frozen=True, slots=True)
class RecallRecord:
    """一条召回结果：记忆本体 + 综合分 + 可解释召回原因。"""

    memory_id: str
    scope: str
    target_id: str
    category: str
    statement: str
    confidence: float
    score: float
    reason: str
    semantic_score: float | None
    lexical_score: float
    recency_score: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "memory_id": self.memory_id,
            "scope": self.scope,
            "target_id": self.target_id,
            "category": self.category,
            "statement": self.statement,
            "confidence": self.confidence,
            "score": round(self.score, 4),
            "reason": self.reason,
            "semantic_score": None if self.semantic_score is None else round(self.semantic_score, 4),
            "lexical_score": round(self.lexical_score, 4),
            "recency_score": round(self.recency_score, 4),
        }


@dataclass
class _Scored:
    item: MemoryItem
    semantic: float | None = None
    lexical: float = 0.0
    recency: float = 0.0
    usefulness: float = 0.0
    reason: str = "确定性兜底(recency)"
    final_score: float = 0.0


class MemoryRecallService:
    """混合召回服务。embedder 为 None 或任何环节失败时退回确定性路径，绝不抛出。"""

    def __init__(
        self,
        store,
        embedder: Callable[[list[str]], list[list[float]]] | None = None,
        *,
        lancedb_root: Path | str | None = None,
        table_name: str = "memory_index",
    ) -> None:
        self.store = store
        self.embedder = embedder
        self.semantic_available = False
        self.last_index_error: str | None = None
        self._lancedb_root = Path(lancedb_root) if lancedb_root else None
        self._table_name = table_name
        self._table = None
        self._embedding_cache: dict[str, list[float]] = {}

    # ------------------------------------------------------------- 向量索引
    def _ensure_indexed(self, items: list[MemoryItem]) -> bool:
        """把候选记忆文本嵌入并增量写入 LanceDB；失败时置错误并返回 False。"""
        if self.embedder is None or self._lancedb_root is None:
            self.last_index_error = None if self.embedder is not None else "embedder unavailable"
            return False
        missing = [item for item in items if item.memory_id not in self._embedding_cache]
        if missing:
            try:
                vectors = self.embedder([canonical_memory_text(item) for item in missing])
            except Exception as exc:  # noqa: BLE001 - provider 故障必须退回确定性路径
                self.last_index_error = f"embed failed: {type(exc).__name__}: {exc}"
                return False
            for item, vector in zip(missing, vectors):
                self._embedding_cache[item.memory_id] = [float(x) for x in vector]
        if self._table is None:
            if not self._embedding_cache:
                self.last_index_error = "no embeddings available to initialize index"
                return False
            try:
                import lancedb

                db = lancedb.connect(str(self._lancedb_root))
                names = [str(t) for t in db.list_tables()]
                if self._table_name in names:
                    self._table = db.open_table(self._table_name)
                else:
                    sample = next(iter(self._embedding_cache.values()))
                    self._table = db.create_table(self._table_name, data=[{"memory_id": "", "text": "", "vector": sample}])
                    self._table.delete("memory_id = ''")
            except Exception as exc:  # noqa: BLE001
                self._table = None
                self.last_index_error = f"lancedb unavailable: {type(exc).__name__}: {exc}"
                return False
        try:
            existing = {row["memory_id"] for row in self._table.to_arrow().to_pylist()}
            new_rows = [
                {
                    "memory_id": item.memory_id,
                    "text": canonical_memory_text(item),
                    "vector": self._embedding_cache[item.memory_id],
                }
                for item in items
                if item.memory_id not in existing
            ]
            if new_rows:
                self._table.add(new_rows)
            return True
        except Exception as exc:  # noqa: BLE001
            self.last_index_error = f"index write failed: {type(exc).__name__}: {exc}"
            return False

    # ------------------------------------------------------------------ 召回
    def recall(
        self,
        query: str,
        *,
        user_id: str = "user_default",
        project_id: str | None = None,
        conversation_id: str | None = None,
        total_limit: int = 8,
    ) -> list[RecallRecord]:
        """混合召回入口。结构化过滤 → 打分 → 层级配额 → 总量上限。"""
        normalized = (query or "").strip()
        scope_targets: list[tuple[str, str]] = [("user", user_id)]
        if project_id:
            scope_targets.append(("project", project_id))
        if conversation_id:
            scope_targets.append(("session", conversation_id))

        try:
            candidates: list[MemoryItem] = []
            for scope, target_id in scope_targets:
                candidates.extend(
                    self.store.list_memories(
                        scope=MemoryScope(scope),
                        target_id=target_id,
                        status=MemoryStatus.ACTIVE,
                        limit=200,
                    )
                )
        except Exception:  # noqa: BLE001 - 权威库异常时返回空，不阻塞主链路
            return []

        if not candidates:
            return []

        scored = self._score_all(normalized, candidates)

        if normalized:
            # 相关性门槛：查询存在时只注入真正相关的记忆（词法命中或语义达阈值）。
            relevant = [
                entry
                for entry in scored
                if entry.lexical > 0.0 or (entry.semantic is not None and entry.semantic >= 0.35)
            ]
        else:
            relevant = list(scored)

        if relevant:
            ranked = sorted(relevant, key=lambda entry: (-entry.final_score, entry.item.memory_id))
        elif self.embedder is None or self.last_index_error:
            # 召回系统不可用（无 embedder / 索引或嵌入失败）→ 退回 recency 确定性路径
            ranked = sorted(
                scored,
                key=lambda entry: (
                    -entry.recency,
                    -entry.usefulness,
                    entry.item.memory_id,
                ),
            )
            for entry in ranked:
                entry.reason = "确定性兜底(recency)"
        else:
            # 召回系统正常但无相关记忆：不强行注入
            return []

        per_scope: dict[str, int] = {}
        results: list[_Scored] = []
        for entry in ranked:
            scope = entry.item.scope.value if isinstance(entry.item.scope, MemoryScope) else str(entry.item.scope)
            if per_scope.get(scope, 0) >= SCOPE_QUOTA.get(scope, 3):
                continue
            per_scope[scope] = per_scope.get(scope, 0) + 1
            results.append(entry)
            if len(results) >= max(1, int(total_limit)):
                break

        return [
            RecallRecord(
                memory_id=entry.item.memory_id,
                scope=entry.item.scope.value if isinstance(entry.item.scope, MemoryScope) else str(entry.item.scope),
                target_id=entry.item.target_id,
                category=entry.item.category.value if isinstance(entry.item.category, MemoryCategory) else str(entry.item.category),
                statement=entry.item.statement,
                confidence=entry.item.confidence,
                score=entry.final_score,
                reason=entry.reason,
                semantic_score=entry.semantic,
                lexical_score=entry.lexical,
                recency_score=entry.recency,
            )
            for entry in results
        ]

    # ------------------------------------------------------------------ 内部
    def _score_all(self, query: str, candidates: list[MemoryItem]) -> list[_Scored]:
        semantic_ready = False
        query_vector: list[float] | None = None
        if self.embedder is not None and query:
            if self._ensure_indexed(candidates):
                try:
                    query_vector = [float(x) for x in self.embedder([query])[0]]
                    semantic_ready = True
                except Exception:  # noqa: BLE001
                    semantic_ready = False
        self.semantic_available = semantic_ready

        query_tokens = set(lexical_tokens(query))
        now = time.time()
        scored: list[_Scored] = []
        for item in candidates:
            entry = _Scored(item=item)
            entry.lexical = _lexical_score(query_tokens, canonical_memory_text(item))
            entry.recency = _recency_score(item.updated_at, now)
            entry.usefulness = max(0.0, min(1.0, float(item.confidence)))
            if semantic_ready and query_vector is not None:
                vector = self._embedding_cache.get(item.memory_id)
                entry.semantic = _cosine(query_vector, vector)
            entry.reason = _reason_of(entry, semantic_ready)
            semantic_component = entry.semantic if entry.semantic is not None else 0.0
            if semantic_ready:
                entry.final_score = (
                    _WEIGHT_SEMANTIC * semantic_component
                    + _WEIGHT_LEXICAL * entry.lexical
                    + _WEIGHT_RECENCY * entry.recency
                    + _WEIGHT_USEFULNESS * entry.usefulness
                )
            else:
                # 语义不可用：语义权重并入词法，避免分数整体塌缩
                entry.final_score = (
                    (_WEIGHT_LEXICAL + _WEIGHT_SEMANTIC) * entry.lexical
                    + _WEIGHT_RECENCY * entry.recency
                    + _WEIGHT_USEFULNESS * entry.usefulness
                )
            scored.append(entry)
        return scored


def _lexical_score(query_tokens: set[str], text: str) -> float:
    if not query_tokens:
        return 0.0
    text_tokens = set(lexical_tokens(text))
    if not text_tokens:
        return 0.0
    overlap = query_tokens & text_tokens
    return len(overlap) / max(1, min(len(query_tokens), 12))


def _recency_score(updated_at: str, now: float) -> float:
    try:
        parsed = datetime.fromisoformat(str(updated_at).replace("Z", "+00:00"))
        age_days = max(0.0, (now - parsed.timestamp()) / 86400.0)
    except (ValueError, OSError, OverflowError):
        return 0.0
    return math.pow(0.5, age_days / _RECENCY_HALF_LIFE_DAYS)


def _cosine(a: list[float], b: list[float]) -> float | None:
    if not a or not b or len(a) != len(b):
        return None
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return None
    return dot / (norm_a * norm_b)


def _reason_of(entry: _Scored, semantic_ready: bool) -> str:
    parts: list[str] = []
    if semantic_ready and entry.semantic is not None and entry.semantic > 0.05:
        parts.append("语义匹配")
    if entry.lexical > 0.05:
        parts.append("词法匹配")
    if entry.recency > 0.6:
        parts.append("近期更新")
    if not parts:
        parts.append("确定性兜底(recency)")
    return "+".join(parts)
