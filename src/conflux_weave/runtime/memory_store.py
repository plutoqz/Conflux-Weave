"""Hierarchical memory store and lifecycle management (P4.1).

Supports three strictly isolated scopes:
- session: ephemeral, bounded to a single conversation/run, auto-decays;
- project: bound to project_id, technical conventions and architecture decisions;
- user: global across user conversations, preferences, language, output style.

Enforces:
- Atomicity and deduplication;
- Conflict detection between new candidate statements and existing active memories;
- Human-in-the-loop (HITL) candidate staging and explicit approval workflow.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
import re
import sqlite3
from typing import Any
from uuid import uuid4


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class MemoryScope(str, Enum):
    SESSION = "session"
    PROJECT = "project"
    USER = "user"


class MemoryCategory(str, Enum):
    PREFERENCE = "preference"
    FACT = "fact"
    CONSTRAINT = "constraint"
    DECISION = "decision"


class MemoryStatus(str, Enum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class CandidateStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class MemoryItem:
    memory_id: str
    scope: MemoryScope
    target_id: str
    category: MemoryCategory
    statement: str
    confidence: float
    status: MemoryStatus
    source_type: str
    source_id: str
    created_at: str
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "memory_id": self.memory_id,
            "scope": self.scope.value if isinstance(self.scope, Enum) else str(self.scope),
            "target_id": self.target_id,
            "category": self.category.value if isinstance(self.category, Enum) else str(self.category),
            "statement": self.statement,
            "confidence": self.confidence,
            "status": self.status.value if isinstance(self.status, Enum) else str(self.status),
            "source_type": self.source_type,
            "source_id": self.source_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True, slots=True)
class MemoryCandidate:
    candidate_id: str
    scope: MemoryScope
    target_id: str
    category: MemoryCategory
    statement: str
    confidence: float
    conflict_with_memory_id: str | None
    status: CandidateStatus
    source_type: str
    source_id: str
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "scope": self.scope.value if isinstance(self.scope, Enum) else str(self.scope),
            "target_id": self.target_id,
            "category": self.category.value if isinstance(self.category, Enum) else str(self.category),
            "statement": self.statement,
            "confidence": self.confidence,
            "conflict_with_memory_id": self.conflict_with_memory_id,
            "status": self.status.value if isinstance(self.status, Enum) else str(self.status),
            "source_type": self.source_type,
            "source_id": self.source_id,
            "created_at": self.created_at,
        }


def _normalize_text(text: str) -> str:
    """Normalize text for exact/fuzzy deduplication."""
    return re.sub(r"[\s\.,!?;:，。！？；：、_—\-]+", "", text.strip().lower())


class HierarchicalMemoryStore:
    """Authority storage for active memories and pending candidates."""

    def __init__(self, database: Path | str) -> None:
        self.database = Path(database)
        self._is_in_memory = str(database) == ":memory:"
        if self._is_in_memory:
            self._uri = f"file:mem_{uuid4().hex}?mode=memory&cache=shared"
            self._anchor = sqlite3.connect(self._uri, uri=True)
            self._anchor.execute("PRAGMA foreign_keys = ON;")
        else:
            self._uri = None
            self._anchor = None
        self._ensure_tables()

    def _connect(self) -> sqlite3.Connection:
        if self._is_in_memory and self._uri:
            conn = sqlite3.connect(self._uri, uri=True, timeout=30)
        else:
            conn = sqlite3.connect(self.database, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        return conn

    def _ensure_tables(self) -> None:
        conn = self._connect()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS memories (
                    memory_id TEXT PRIMARY KEY,
                    scope TEXT NOT NULL CHECK (scope IN ('session', 'project', 'user')),
                    target_id TEXT NOT NULL,
                    category TEXT NOT NULL CHECK (category IN ('preference', 'fact', 'constraint', 'decision')),
                    statement TEXT NOT NULL,
                    confidence REAL NOT NULL DEFAULT 1.0,
                    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'archived')),
                    source_type TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_memories_lookup
                ON memories(scope, target_id, status, category)
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_candidates (
                    candidate_id TEXT PRIMARY KEY,
                    scope TEXT NOT NULL CHECK (scope IN ('session', 'project', 'user')),
                    target_id TEXT NOT NULL,
                    category TEXT NOT NULL CHECK (category IN ('preference', 'fact', 'constraint', 'decision')),
                    statement TEXT NOT NULL,
                    confidence REAL NOT NULL DEFAULT 0.8,
                    conflict_with_memory_id TEXT REFERENCES memories(memory_id) ON DELETE SET NULL,
                    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected')),
                    source_type TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_memory_candidates_status
                ON memory_candidates(status, scope, target_id)
                """
            )
            conn.commit()
        finally:
            conn.close()

    # --- Active Memory Operations ---

    def create_memory(
        self,
        scope: str | MemoryScope,
        target_id: str,
        category: str | MemoryCategory,
        statement: str,
        *,
        confidence: float = 1.0,
        source_type: str = "manual",
        source_id: str = "user",
        memory_id: str | None = None,
    ) -> MemoryItem:
        scope_str = scope.value if isinstance(scope, Enum) else str(scope)
        cat_str = category.value if isinstance(category, Enum) else str(category)
        mid = memory_id or f"mem-{uuid4().hex}"
        now = _utc_now()
        stmt = statement.strip()
        if not stmt:
            raise ValueError("memory statement must not be empty")

        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT INTO memories(
                    memory_id, scope, target_id, category, statement,
                    confidence, status, source_type, source_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?)
                """,
                (mid, scope_str, target_id, cat_str, stmt, float(confidence), source_type, source_id, now, now),
            )
            conn.commit()
        finally:
            conn.close()

        return MemoryItem(
            memory_id=mid,
            scope=MemoryScope(scope_str),
            target_id=target_id,
            category=MemoryCategory(cat_str),
            statement=stmt,
            confidence=float(confidence),
            status=MemoryStatus.ACTIVE,
            source_type=source_type,
            source_id=source_id,
            created_at=now,
            updated_at=now,
        )

    def get_memory(self, memory_id: str) -> MemoryItem | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT memory_id, scope, target_id, category, statement, confidence, status, source_type, source_id, created_at, updated_at FROM memories WHERE memory_id = ?",
                (memory_id,),
            ).fetchone()
        finally:
            conn.close()

        if row is None:
            return None

        return MemoryItem(
            memory_id=row["memory_id"],
            scope=MemoryScope(row["scope"]),
            target_id=row["target_id"],
            category=MemoryCategory(row["category"]),
            statement=row["statement"],
            confidence=row["confidence"],
            status=MemoryStatus(row["status"]),
            source_type=row["source_type"],
            source_id=row["source_id"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def list_memories(
        self,
        scope: str | MemoryScope | None = None,
        target_id: str | None = None,
        category: str | MemoryCategory | None = None,
        status: str | MemoryStatus = "active",
        limit: int = 50,
    ) -> list[MemoryItem]:
        query = "SELECT memory_id, scope, target_id, category, statement, confidence, status, source_type, source_id, created_at, updated_at FROM memories WHERE 1=1"
        params: list[Any] = []

        if scope is not None:
            scope_str = scope.value if isinstance(scope, Enum) else str(scope)
            query += " AND scope = ?"
            params.append(scope_str)

        if target_id is not None:
            query += " AND target_id = ?"
            params.append(target_id)

        if category is not None:
            cat_str = category.value if isinstance(category, Enum) else str(category)
            query += " AND category = ?"
            params.append(cat_str)

        if status is not None:
            stat_str = status.value if isinstance(status, Enum) else str(status)
            query += " AND status = ?"
            params.append(stat_str)

        query += " ORDER BY updated_at DESC, created_at DESC LIMIT ?"
        params.append(max(1, min(int(limit), 200)))

        conn = self._connect()
        try:
            rows = conn.execute(query, params).fetchall()
        finally:
            conn.close()

        return [
            MemoryItem(
                memory_id=row["memory_id"],
                scope=MemoryScope(row["scope"]),
                target_id=row["target_id"],
                category=MemoryCategory(row["category"]),
                statement=row["statement"],
                confidence=row["confidence"],
                status=MemoryStatus(row["status"]),
                source_type=row["source_type"],
                source_id=row["source_id"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
            for row in rows
        ]

    def update_memory(
        self,
        memory_id: str,
        statement: str | None = None,
        confidence: float | None = None,
        status: str | MemoryStatus | None = None,
    ) -> MemoryItem | None:
        item = self.get_memory(memory_id)
        if item is None:
            return None

        now = _utc_now()
        new_stmt = statement.strip() if statement is not None else item.statement
        new_conf = float(confidence) if confidence is not None else item.confidence
        new_stat = (status.value if isinstance(status, Enum) else str(status)) if status is not None else item.status.value

        conn = self._connect()
        try:
            conn.execute(
                "UPDATE memories SET statement = ?, confidence = ?, status = ?, updated_at = ? WHERE memory_id = ?",
                (new_stmt, new_conf, new_stat, now, memory_id),
            )
            conn.commit()
        finally:
            conn.close()

        return self.get_memory(memory_id)

    def archive_memory(self, memory_id: str) -> bool:
        return self.update_memory(memory_id, status=MemoryStatus.ARCHIVED) is not None

    def delete_memory(self, memory_id: str) -> bool:
        conn = self._connect()
        try:
            cursor = conn.execute("DELETE FROM memories WHERE memory_id = ?", (memory_id,))
            conn.commit()
            return cursor.rowcount > 0
        finally:
            conn.close()

    # --- Candidate Operations (HITL) ---

    def create_candidate(
        self,
        scope: str | MemoryScope,
        target_id: str,
        category: str | MemoryCategory,
        statement: str,
        *,
        confidence: float = 0.8,
        source_type: str = "conversation",
        source_id: str = "system",
        conflict_with_memory_id: str | None = None,
        candidate_id: str | None = None,
    ) -> MemoryCandidate:
        scope_str = scope.value if isinstance(scope, Enum) else str(scope)
        cat_str = category.value if isinstance(category, Enum) else str(category)
        cid = candidate_id or f"cand-{uuid4().hex}"
        now = _utc_now()
        stmt = statement.strip()
        if not stmt:
            raise ValueError("candidate statement must not be empty")

        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT INTO memory_candidates(
                    candidate_id, scope, target_id, category, statement,
                    confidence, conflict_with_memory_id, status, source_type, source_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)
                """,
                (cid, scope_str, target_id, cat_str, stmt, float(confidence), conflict_with_memory_id, source_type, source_id, now),
            )
            conn.commit()
        finally:
            conn.close()

        return MemoryCandidate(
            candidate_id=cid,
            scope=MemoryScope(scope_str),
            target_id=target_id,
            category=MemoryCategory(cat_str),
            statement=stmt,
            confidence=float(confidence),
            conflict_with_memory_id=conflict_with_memory_id,
            status=CandidateStatus.PENDING,
            source_type=source_type,
            source_id=source_id,
            created_at=now,
        )

    def get_candidate(self, candidate_id: str) -> MemoryCandidate | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT candidate_id, scope, target_id, category, statement, confidence, conflict_with_memory_id, status, source_type, source_id, created_at FROM memory_candidates WHERE candidate_id = ?",
                (candidate_id,),
            ).fetchone()
        finally:
            conn.close()

        if row is None:
            return None

        return MemoryCandidate(
            candidate_id=row["candidate_id"],
            scope=MemoryScope(row["scope"]),
            target_id=row["target_id"],
            category=MemoryCategory(row["category"]),
            statement=row["statement"],
            confidence=row["confidence"],
            conflict_with_memory_id=row["conflict_with_memory_id"],
            status=CandidateStatus(row["status"]),
            source_type=row["source_type"],
            source_id=row["source_id"],
            created_at=row["created_at"],
        )

    def list_candidates(
        self,
        status: str | CandidateStatus = "pending",
        scope: str | MemoryScope | None = None,
        target_id: str | None = None,
        limit: int = 50,
    ) -> list[MemoryCandidate]:
        query = "SELECT candidate_id, scope, target_id, category, statement, confidence, conflict_with_memory_id, status, source_type, source_id, created_at FROM memory_candidates WHERE 1=1"
        params: list[Any] = []

        if status is not None:
            stat_str = status.value if isinstance(status, Enum) else str(status)
            query += " AND status = ?"
            params.append(stat_str)

        if scope is not None:
            scope_str = scope.value if isinstance(scope, Enum) else str(scope)
            query += " AND scope = ?"
            params.append(scope_str)

        if target_id is not None:
            query += " AND target_id = ?"
            params.append(target_id)

        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(max(1, min(int(limit), 100)))

        conn = self._connect()
        try:
            rows = conn.execute(query, params).fetchall()
        finally:
            conn.close()

        return [
            MemoryCandidate(
                candidate_id=row["candidate_id"],
                scope=MemoryScope(row["scope"]),
                target_id=row["target_id"],
                category=MemoryCategory(row["category"]),
                statement=row["statement"],
                confidence=row["confidence"],
                conflict_with_memory_id=row["conflict_with_memory_id"],
                status=CandidateStatus(row["status"]),
                source_type=row["source_type"],
                source_id=row["source_id"],
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def approve_candidate(self, candidate_id: str) -> MemoryItem:
        """Approve candidate into active memory, archiving conflicting memory if present."""
        cand = self.get_candidate(candidate_id)
        if cand is None:
            raise KeyError(f"candidate {candidate_id} not found")
        if cand.status != CandidateStatus.PENDING:
            raise ValueError(f"candidate {candidate_id} is already {cand.status.value}")

        conn = self._connect()
        try:
            # 1. Archive conflicting memory if exists
            if cand.conflict_with_memory_id:
                now = _utc_now()
                conn.execute(
                    "UPDATE memories SET status = 'archived', updated_at = ? WHERE memory_id = ?",
                    (now, cand.conflict_with_memory_id),
                )

            # 2. Mark candidate as approved
            conn.execute(
                "UPDATE memory_candidates SET status = 'approved' WHERE candidate_id = ?",
                (candidate_id,),
            )
            conn.commit()
        finally:
            conn.close()

        # 3. Create or update active memory
        return self.create_memory(
            scope=cand.scope,
            target_id=cand.target_id,
            category=cand.category,
            statement=cand.statement,
            confidence=cand.confidence,
            source_type=cand.source_type,
            source_id=cand.source_id,
        )

    def reject_candidate(self, candidate_id: str) -> bool:
        """Reject and dismiss a memory candidate."""
        cand = self.get_candidate(candidate_id)
        if cand is None:
            raise KeyError(f"candidate {candidate_id} not found")

        conn = self._connect()
        try:
            cursor = conn.execute(
                "UPDATE memory_candidates SET status = 'rejected' WHERE candidate_id = ?",
                (candidate_id,),
            )
            conn.commit()
            return cursor.rowcount > 0
        finally:
            conn.close()

    # --- Conflict Detection and Deduplication ---

    def find_conflicts_and_duplicates(
        self,
        scope: str | MemoryScope,
        target_id: str,
        category: str | MemoryCategory,
        statement: str,
    ) -> tuple[str | None, str | None]:
        """Check against active memories in the same scope and target.

        Returns (duplicate_memory_id, conflict_memory_id).
        - duplicate: same normalized statement.
        - conflict: opposing polarities (e.g. '仅用中文' vs '仅用英文', '开启' vs '禁用').
        """
        active_list = self.list_memories(scope=scope, target_id=target_id, category=category, status=MemoryStatus.ACTIVE)
        norm_stmt = _normalize_text(statement)

        duplicate_id = None
        conflict_id = None

        OPPOSING_PAIRS = [
            ("中文", "英文"),
            ("精简", "详细"),
            ("长篇", "简短"),
            ("启用", "禁用"),
            ("开启", "关闭"),
            ("包含代码", "不要代码"),
            ("优先代码", "不展示代码"),
            ("严肃", "活泼"),
        ]

        for mem in active_list:
            norm_mem = _normalize_text(mem.statement)
            if norm_stmt == norm_mem:
                duplicate_id = mem.memory_id
                return duplicate_id, None

            # Check semantic opposing keywords
            for word_a, word_b in OPPOSING_PAIRS:
                if (word_a in statement and word_b in mem.statement) or (
                    word_b in statement and word_a in mem.statement
                ):
                    conflict_id = mem.memory_id
                    break

            if conflict_id:
                break

        return duplicate_id, conflict_id
