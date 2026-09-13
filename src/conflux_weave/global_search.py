"""A1 全局搜索：以 SQLite FTS5 为权威的跨对象类型搜索（P7-A）。

设计要点（依据 v0.3-P7 方案 4.A1）：
- 结构化过滤 → SQLite FTS5(trigram, BM25) → 类型/时间重排 → 原文定位跳转；
- trigram 分词器支持中文子串匹配（unicode61 会把连续 CJK 当作单 token）；
- LanceDB 不参与本批：语义召回留作可选后续，不复制对象生命周期；
- 归档/软删除在查询期按各权威表过滤，索引不持有生命周期副本；
- 项目过滤第一批按 metadata.project_id 精确匹配——当前索引对象不携带项目
  归属，因此项目过滤下结果为空且不伪造归属（诚实的零泄漏边界）；
- 检索路径零模型调用：无结果时返回空集，不触发任何 Provider。
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, Sequence

SEARCH_OBJECT_TYPES: tuple[str, ...] = (
    "chat_message",
    "run",
    "note",
    "document",
    "paper",
    "evidence",
)

_RESULT_TYPE_LABELS = {
    "chat_message": "对话",
    "run": "研究报告",
    "note": "笔记",
    "document": "文档",
    "paper": "论文",
    "evidence": "证据",
}


@dataclass(frozen=True, slots=True)
class SearchHit:
    result_id: str
    object_type: str
    object_id: str
    type_label: str
    title: str
    snippet: str
    match_reason: str
    updated_at: str
    locator: dict[str, Any]
    deep_link: str
    score: float


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _fts_query(query: str) -> str:
    """Escape a user query into an FTS5 phrase query (trigram safe)."""
    escaped = query.replace('"', '""')
    return f'"{escaped}"'


class GlobalSearchService:
    """SQLite FTS5 全文索引的写入、重建与查询门面。

    索引与权威数据的写读分离：索引行可以在任何时刻全量重建（reindex），
    单条写入失败不允许影响宿主业务路径（调用方负责 try/except 包裹）。
    """

    def __init__(self, database: Path | str) -> None:
        self._database = Path(database)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._database, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        self._ensure_schema(conn)
        return conn

    def _ensure_schema(self, conn: sqlite3.Connection) -> None:
        """幂等自建 FTS 表（与 migration 12 同 DDL）。

        服务可能在任意 SQLite 状态上被构造（单测、未迁移库、运行时库），
        索引表必须自持；与迁移路径重复执行无冲突（IF NOT EXISTS）。
        """
        have = conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','view') AND name IN ('search_index','search_sequence')"
        ).fetchall()
        names = {row["name"] for row in have}
        if "search_index" not in names:
            conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS search_index USING fts5("
                "object_id UNINDEXED, object_type UNINDEXED, title, body, "
                "metadata_json UNINDEXED, updated_at UNINDEXED, tokenize='trigram')"
            )
        if "search_sequence" not in names:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS search_sequence ("
                "object_type TEXT NOT NULL, object_id TEXT NOT NULL, fts_rowid INTEGER NOT NULL, "
                "PRIMARY KEY (object_type, object_id))"
            )

    @staticmethod
    def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
        return (
            conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
            ).fetchone()
            is not None
        )

    # --- convenience indexers for write-path hooks ---

    def index_chat_message(
        self,
        *,
        message_id: str,
        conversation_id: str,
        content: str,
        created_at: str,
        run_id: str | None = None,
    ) -> None:
        """ChatService._append 钩子：消息落库即索引（带会话标题）。"""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT title FROM conversations WHERE conversation_id=?",
                (conversation_id,),
            ).fetchone()
            self._index_within(
                conn, "chat_message", message_id,
                title=(row["title"] if row else "") or "",
                body=content or "",
                metadata={"conversation_id": conversation_id, "run_id": run_id},
                updated_at=created_at or _utc_now(),
            )
            conn.commit()
        finally:
            conn.close()

    def index_note(self, note: Any) -> None:
        """笔记保存钩子：标题 + 全部小节正文即时入索引。"""
        sections = getattr(note, "sections", ()) or ()
        body = "\n\n".join(
            str(getattr(section, "content", "") or "") for section in sections
        )
        conn = self._connect()
        try:
            self._index_within(
                conn, "note", str(note.note_id),
                title=str(note.title or ""),
                body=body,
                metadata={
                    "document_id": getattr(note, "document_id", None),
                    "version": getattr(note, "version", None),
                },
                updated_at=str(getattr(note, "created_at", "") or _utc_now()),
            )
            conn.commit()
        finally:
            conn.close()

    # --- indexing ---

    def index_object(
        self,
        object_type: str,
        object_id: str,
        *,
        title: str,
        body: str,
        metadata: dict[str, Any] | None = None,
        updated_at: str | None = None,
    ) -> None:
        if object_type not in SEARCH_OBJECT_TYPES:
            raise ValueError(f"unsupported search object type: {object_type}")
        conn = self._connect()
        try:
            self._index_within(
                conn,
                object_type,
                object_id,
                title=title,
                body=body,
                metadata=metadata,
                updated_at=updated_at or _utc_now(),
            )
            conn.commit()
        finally:
            conn.close()

    def _index_within(
        self,
        conn: sqlite3.Connection,
        object_type: str,
        object_id: str,
        *,
        title: str,
        body: str,
        metadata: dict[str, Any] | None,
        updated_at: str,
    ) -> None:
        prior = conn.execute(
            "SELECT fts_rowid FROM search_sequence WHERE object_type=? AND object_id=?",
            (object_type, object_id),
        ).fetchone()
        if prior is not None:
            conn.execute("DELETE FROM search_index WHERE rowid=?", (prior["fts_rowid"],))
            conn.execute(
                "DELETE FROM search_sequence WHERE object_type=? AND object_id=?",
                (object_type, object_id),
            )
        cursor = conn.execute(
            "INSERT INTO search_index(object_id, object_type, title, body, metadata_json, updated_at) "
            "VALUES (?,?,?,?,?,?)",
            (
                object_id,
                object_type,
                title or "",
                body or "",
                json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True),
                updated_at,
            ),
        )
        conn.execute(
            "INSERT INTO search_sequence(object_type, object_id, fts_rowid) VALUES (?,?,?)",
            (object_type, object_id, cursor.lastrowid),
        )

    def remove_object(self, object_type: str, object_id: str) -> None:
        conn = self._connect()
        try:
            prior = conn.execute(
                "SELECT fts_rowid FROM search_sequence WHERE object_type=? AND object_id=?",
                (object_type, object_id),
            ).fetchone()
            if prior is not None:
                conn.execute("DELETE FROM search_index WHERE rowid=?", (prior["fts_rowid"],))
                conn.execute(
                    "DELETE FROM search_sequence WHERE object_type=? AND object_id=?",
                    (object_type, object_id),
                )
            conn.commit()
        finally:
            conn.close()

    # --- query ---

    def search(
        self,
        query: str,
        *,
        types: Sequence[str] | None = None,
        from_at: str | None = None,
        to_at: str | None = None,
        project_id: str | None = None,
        limit: int = 20,
        include_archived: bool = False,
    ) -> list[SearchHit]:
        query = (query or "").strip()
        if not query:
            return []
        selected_types = [t for t in (types or SEARCH_OBJECT_TYPES) if t in SEARCH_OBJECT_TYPES]
        if not selected_types:
            return []

        conn = self._connect()
        try:
            rows = self._query_rows(
                conn, query, selected_types,
                from_at=from_at, to_at=to_at,
                project_id=project_id, include_archived=include_archived,
                limit=limit,
            )
        finally:
            conn.close()

        hits: list[SearchHit] = []
        terms = [term for term in query.split() if term] or [query]
        casefold_terms = [term.casefold() for term in terms]
        for row in rows[: max(1, min(int(limit), 100))]:
            metadata = json.loads(row["metadata_json"] or "{}")
            object_type = row["object_type"]
            object_id = row["object_id"]
            title = row["title"] or ""
            body = row["body"] or ""
            in_title = all(term in title.casefold() for term in casefold_terms)
            in_body = all(term in body.casefold() for term in casefold_terms)
            reason = "标题+内容匹配" if in_title and in_body else ("标题匹配" if in_title else "内容匹配")
            hits.append(
                SearchHit(
                    result_id=f"{object_type}:{object_id}",
                    object_type=object_type,
                    object_id=object_id,
                    type_label=_RESULT_TYPE_LABELS.get(object_type, object_type),
                    title=title or "(无标题)",
                    snippet=row["snippet"] or body[:200],
                    match_reason=reason,
                    updated_at=row["updated_at"] or "",
                    locator=self._locator(object_type, object_id, metadata),
                    deep_link=self._deep_link(object_type, object_id, metadata),
                    score=float(row["rank"]) if row["rank"] is not None else 0.0,
                )
            )
        return hits

    def _query_rows(
        self,
        conn: sqlite3.Connection,
        query: str,
        types: Sequence[str],
        *,
        from_at: str | None,
        to_at: str | None,
        project_id: str | None,
        include_archived: bool,
        limit: int = 20,
    ) -> list[sqlite3.Row]:
        type_marks = ",".join("?" for _ in types)
        conditions = [f"search_index.object_type IN ({type_marks})"]
        params: list[Any] = [*types]

        if from_at:
            conditions.append("search_index.updated_at >= ?")
            params.append(from_at)
        if to_at:
            conditions.append("search_index.updated_at <= ?")
            params.append(to_at)
        if project_id:
            # 第一批：仅匹配显式携带 project 归属的对象（当前无对象携带 → 结果为空，
            # 不伪造归属）；跨项目零泄漏由精确匹配保证。
            conditions.append("json_extract(search_index.metadata_json, '$.project_id') = ?")
            params.append(project_id)

        lifecycle = []
        conversations_exist = self._table_exists(conn, "conversations")
        runs_exist = self._table_exists(conn, "runs")
        if conversations_exist:
            if not include_archived:
                lifecycle.append(
                    "(search_index.object_type='chat_message' AND EXISTS ("
                    " SELECT 1 FROM conversations c WHERE c.conversation_id ="
                    " json_extract(search_index.metadata_json, '$.conversation_id')"
                    " AND (c.deleted_at IS NOT NULL OR c.archived_at IS NOT NULL)))"
                )
            lifecycle.append(
                "(search_index.object_type='chat_message' AND EXISTS ("
                " SELECT 1 FROM conversations c WHERE c.conversation_id ="
                " json_extract(search_index.metadata_json, '$.conversation_id')"
                " AND c.deleted_at IS NOT NULL))"
            )
        if runs_exist:
            lifecycle.append(
                "(search_index.object_type='run' AND EXISTS ("
                " SELECT 1 FROM runs r WHERE r.run_id = search_index.object_id"
                " AND (r.deleted_at IS NOT NULL"
                + ("" if include_archived else " OR r.archived_at IS NOT NULL") + ")))"
            )
        if lifecycle:
            conditions.append("NOT (" + " OR ".join(lifecycle) + ")")

        where = " AND ".join(conditions)
        terms = [term for term in query.split() if term] or [query]
        # trigram 分词器要求词长 ≥3；多词查询用 AND 短语（词间可被其他内容分隔），
        # 含短词（中文两字词等）时整体回退 LIKE 全词 AND 匹配。
        if all(len(term) >= 3 for term in terms):
            match_expression = " ".join(_fts_query(term) for term in terms)
            try:
                rows = conn.execute(
                    "SELECT object_id, object_type, title, body, metadata_json, updated_at, "
                    "snippet(search_index, 3, '[[', ']]', '…', 16) AS snippet, "
                    "bm25(search_index) AS rank "
                    f"FROM search_index WHERE search_index MATCH ? AND {where} "
                    "ORDER BY rank LIMIT ?",
                    (match_expression, *params, max(1, min(int(limit), 100))),
                ).fetchall()
                if rows:
                    return rows
            except sqlite3.OperationalError:
                pass  # trigram MATCH 无法接受的查询形态 → 回退 LIKE
        like_conditions = []
        like_params: list[Any] = []
        for term in terms:
            escaped = term.replace("\\", r"\\").replace("%", r"\%").replace("_", r"\_")
            like_conditions.append("(title LIKE ? ESCAPE '\\' OR body LIKE ? ESCAPE '\\')")
            like_params.extend([f"%{escaped}%", f"%{escaped}%"])
        return conn.execute(
            "SELECT object_id, object_type, title, body, metadata_json, updated_at, "
            "substr(body, max(1, instr(body, ?) - 60), 200) AS snippet, "
            "0.0 AS rank "
            f"FROM search_index WHERE {' AND '.join(like_conditions)} AND {where} "
            "ORDER BY updated_at DESC LIMIT ?",
            (terms[0], *like_params, *params, max(1, min(int(limit), 100))),
        ).fetchall()

    # --- locate ---

    def locate(self, result_id: str) -> dict[str, Any] | None:
        object_type, sep, object_id = result_id.partition(":")
        if not sep or object_type not in SEARCH_OBJECT_TYPES or not object_id:
            return None
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT metadata_json, updated_at FROM search_index "
                "WHERE object_type=? AND object_id=?",
                (object_type, object_id),
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            return None
        metadata = json.loads(row["metadata_json"] or "{}")
        return {
            "result_id": result_id,
            "object_type": object_type,
            "object_id": object_id,
            "type_label": _RESULT_TYPE_LABELS.get(object_type, object_type),
            "locator": self._locator(object_type, object_id, metadata),
            "deep_link": self._deep_link(object_type, object_id, metadata),
        }

    def _locator(self, object_type: str, object_id: str, metadata: dict[str, Any]) -> dict[str, Any]:
        locator: dict[str, Any] = {"object_type": object_type, "object_id": object_id}
        if object_type == "chat_message":
            if metadata.get("conversation_id"):
                locator["conversation_id"] = metadata["conversation_id"]
            locator["message_id"] = object_id
        elif object_type == "run":
            locator["run_id"] = object_id
        elif object_type == "note":
            locator["note_id"] = object_id
            if metadata.get("document_id"):
                locator["document_id"] = metadata["document_id"]
        elif object_type in ("document", "paper"):
            locator["document_id"] = object_id
        elif object_type == "evidence":
            if metadata.get("run_id"):
                locator["run_id"] = metadata["run_id"]
            locator["evidence_id"] = object_id
        return locator

    def _deep_link(self, object_type: str, object_id: str, metadata: dict[str, Any]) -> str:
        if object_type == "run":
            return f"#/research?run_id={object_id}"
        if object_type == "evidence" and metadata.get("run_id"):
            return f"#/research?run_id={metadata['run_id']}"
        if object_type == "chat_message":
            return "#/chat"
        if object_type in ("note", "document", "paper"):
            document_id = metadata.get("document_id") if object_type == "note" else object_id
            if object_type == "note" and not document_id:
                return "#/library"
            return f"#/library?document_id={document_id}"
        return "#/overview"

    # --- rebuild ---

    def reindex(
        self,
        *,
        notes_registry: Sequence[dict[str, Any]] = (),
        library_registry: Sequence[dict[str, Any]] = (),
        artifact_store: Any | None = None,
    ) -> dict[str, int]:
        """从权威存储全量重建索引（不触碰 Provider，不修改业务数据）。"""
        conn = self._connect()
        counts = {t: 0 for t in SEARCH_OBJECT_TYPES}
        try:
            # 对话消息（LEFT JOIN 会话标题；软删除会话的消息不入索引，
            # 归档会话保留索引、查询期过滤）
            rows = conn.execute(
                "SELECT m.message_id, m.conversation_id, m.content, m.created_at, m.run_id, "
                "c.title AS conversation_title, c.deleted_at AS conv_deleted "
                "FROM chat_messages m LEFT JOIN conversations c ON c.conversation_id = m.conversation_id"
            ).fetchall()
            for row in rows:
                if row["conv_deleted"]:
                    continue
                self._index_within(
                    conn, "chat_message", row["message_id"],
                    title=row["conversation_title"] or "",
                    body=row["content"] or "",
                    metadata={"conversation_id": row["conversation_id"], "run_id": row["run_id"]},
                    updated_at=row["created_at"],
                )
                counts["chat_message"] += 1

            # 研究报告：目标（task input）+ 交付报告正文
            run_rows = conn.execute(
                "SELECT r.run_id, r.updated_at, t.kind, t.input_json, "
                "(SELECT d.disposition FROM deliveries d WHERE d.run_id = r.run_id LIMIT 1) AS disposition "
                "FROM runs r JOIN tasks t ON t.task_id = r.task_id "
                "WHERE r.deleted_at IS NULL"
            ).fetchall()
            for row in run_rows:
                objective = ""
                try:
                    payload = json.loads(row["input_json"] or "{}")
                    objective = str(payload.get("objective") or payload.get("query") or "")
                except ValueError:
                    objective = ""
                report_text = ""
                if artifact_store is not None:
                    report_text = self._load_run_report_text(conn, artifact_store, row["run_id"])
                body = "\n\n".join(part for part in (objective, report_text) if part)
                if not body:
                    continue
                self._index_within(
                    conn, "run", row["run_id"],
                    title=objective[:160] or row["run_id"],
                    body=body,
                    metadata={"task_kind": row["kind"], "disposition": row["disposition"]},
                    updated_at=row["updated_at"],
                )
                counts["run"] += 1

            # 笔记（注册表：标题 + 修订说明；正文第一批以保存钩子即时索引）
            for note in notes_registry:
                note_id = str(note.get("note_id") or "")
                if not note_id:
                    continue
                self._index_within(
                    conn, "note", note_id,
                    title=str(note.get("title") or ""),
                    body=str(note.get("instruction") or ""),
                    metadata={"document_id": note.get("document_id"), "version": note.get("version")},
                    updated_at=str(note.get("created_at") or ""),
                )
                counts["note"] += 1

            # 文档 / 论文（library registry 元数据）
            for item in library_registry:
                title = str(item.get("title") or "")
                if not title:
                    continue
                paper_id = item.get("paper_id")
                document_id = item.get("document_id")
                if document_id:
                    object_type, object_id = "document", str(document_id)
                elif paper_id:
                    object_type, object_id = "paper", str(paper_id)
                else:
                    continue
                body = " ".join(
                    str(part)
                    for part in (
                        item.get("summary") or item.get("abstract") or "",
                        " ".join(item.get("authors") or []) if isinstance(item.get("authors"), list) else "",
                        str(item.get("year") or ""),
                        str(item.get("doi") or ""),
                    )
                    if part
                )
                updated_at = str(item.get("updated_at") or item.get("created_at") or "")
                self._index_within(
                    conn, object_type, object_id,
                    title=title, body=body,
                    metadata={"source": item.get("source")},
                    updated_at=updated_at or _utc_now(),
                )
                counts[object_type] += 1

            # Evidence：随交付发布钩子索引 + 从既有交付回填
            if artifact_store is not None:
                delivery_rows = conn.execute(
                    "SELECT r.run_id, r.updated_at, d.evidence_refs_json FROM deliveries d "
                    "JOIN runs r ON r.run_id = d.run_id WHERE r.deleted_at IS NULL"
                ).fetchall()
                for row in delivery_rows:
                    added = self._index_run_evidence_within(conn, artifact_store, row["run_id"], row["updated_at"])
                    counts["evidence"] += added
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        return counts

    def _load_run_report_text(self, conn: sqlite3.Connection, artifact_store: Any, run_id: str) -> str:
        row = conn.execute(
            "SELECT ar.artifact_id FROM delivery_artifacts d "
            "JOIN artifact_registrations ar ON ar.registration_id = d.registration_id "
            "WHERE d.run_id = ? ORDER BY d.ordinal LIMIT 1",
            (run_id,),
        ).fetchone()
        if row is None:
            return ""
        artifact_id = row["artifact_id"]
        try:
            digest = str(artifact_id).removeprefix("artifact-sha256-")
            return artifact_store.path_for_digest(digest).read_text(encoding="utf-8", errors="replace")
        except Exception:
            return ""

    def _index_run_evidence_within(
        self, conn: sqlite3.Connection, artifact_store: Any, run_id: str, updated_at: str
    ) -> int:
        rows = conn.execute(
            "SELECT ar.artifact_id FROM delivery_artifacts d "
            "JOIN artifact_registrations ar ON ar.registration_id = d.registration_id "
            "WHERE d.run_id = ? ORDER BY d.ordinal",
            (run_id,),
        ).fetchall()
        added = 0
        for art in rows:
            try:
                digest = str(art["artifact_id"]).removeprefix("artifact-sha256-")
                payload = json.loads(
                    artifact_store.path_for_digest(digest).read_text(encoding="utf-8", errors="replace")
                )
            except Exception:
                continue
            if not isinstance(payload, dict) or "evidence" not in payload:
                continue
            for item in payload.get("evidence") or []:
                evidence_id = str(item.get("evidence_id") or "")
                quote = str(item.get("quote") or "")
                if not evidence_id:
                    continue
                locator = item.get("locator") or {}
                title = str(locator.get("title") or "")
                self._index_within(
                    conn, "evidence", evidence_id,
                    title=title,
                    body=quote,
                    metadata={"run_id": run_id, "url": locator.get("url")},
                    updated_at=updated_at,
                )
                added += 1
        return added

    def index_delivery(
        self,
        *,
        run_id: str,
        objective: str,
        report_artifact_id: str | None,
        evidence_artifact_id: str | None,
        artifact_store: Any,
        updated_at: str | None = None,
    ) -> None:
        """交付发布钩子：索引研究报告正文与证据引用（尽力而为，由调用方兜底异常）。"""
        stamp = updated_at or _utc_now()
        conn = self._connect()
        try:
            report_text = ""
            if report_artifact_id:
                try:
                    digest = str(report_artifact_id).removeprefix("artifact-sha256-")
                    report_text = artifact_store.path_for_digest(digest).read_text(
                        encoding="utf-8", errors="replace"
                    )
                except Exception:
                    report_text = ""
            body = "\n\n".join(part for part in (objective, report_text) if part)
            if body:
                self._index_within(
                    conn, "run", run_id,
                    title=objective[:160] or run_id,
                    body=body,
                    metadata={"source": "delivery_hook"},
                    updated_at=stamp,
                )
            if evidence_artifact_id:
                try:
                    digest = str(evidence_artifact_id).removeprefix("artifact-sha256-")
                    payload = json.loads(
                        artifact_store.path_for_digest(digest).read_text(encoding="utf-8", errors="replace")
                    )
                    for item in payload.get("evidence") or []:
                        evidence_id = str(item.get("evidence_id") or "")
                        if not evidence_id:
                            continue
                        locator = item.get("locator") or {}
                        self._index_within(
                            conn, "evidence", evidence_id,
                            title=str(locator.get("title") or ""),
                            body=str(item.get("quote") or ""),
                            metadata={"run_id": run_id, "url": locator.get("url")},
                            updated_at=stamp,
                        )
                except Exception:
                    pass
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
