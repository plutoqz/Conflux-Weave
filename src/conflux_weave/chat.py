"""Lightweight direct-chat service (W3.0 模式 A：直接问答).

直接问答不创建 durable Run、不产出报告工件：问题原样发送到 LLM，仅将
对话记录持久化到 chat_messages 表。模型知识回答，无证据引用，由前端
显式标注。
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from conflux_weave.provider import OpenAICompatibleChatAdapter
from conflux_weave.runtime.artifacts import LocalArtifactStore


CHAT_SCHEMA_VERSION = "conflux-weave.chat-message.v1"
DIRECT_SYSTEM_PROMPT = (
    "你是 Conflux-Weave 研究工作台的直接问答助手。直接、准确地回答用户问题，"
    "可以使用你的通用知识；不要编造引用、来源或具体统计数字。回答使用与问题"
    "相同的语言，适当使用 Markdown 组织内容，保持简洁。"
)
HISTORY_MESSAGE_LIMIT = 8
MAX_QUESTION_CHARS = 8000
MAX_CONTENT_CHARS = 32_000
RAG_SNIPPET_LIMIT = 6
RAG_SNIPPET_CHARS = 1200
RAG_SYSTEM_PROMPT = (
    "你是 Conflux-Weave 工作台的知识库问答助手。只使用提供的知识库片段回答问题；"
    "引用某个片段的内容时，在句末标注其编号（如 [1]）。片段未覆盖的部分，明确"
    "说明知识库未覆盖，不要编造。回答使用与问题相同的语言，简洁、结构清晰。"
)


@dataclass(frozen=True, slots=True)
class ChatMessage:
    message_id: str
    conversation_id: str
    role: str
    mode: str
    content: str
    created_at: str
    context_artifact_id: str | None = None


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class ChatService:
    """直接问答：LLM 原样应答 + 对话记录持久化（W3.0 模式 A）。"""

    def __init__(
        self,
        chat_adapter: OpenAICompatibleChatAdapter,
        database: Path | str,
        *,
        retrieval=None,
        artifact_store: LocalArtifactStore | None = None,
    ) -> None:
        self._chat = chat_adapter
        self._database = Path(database)
        self._retrieval = retrieval
        self._store = artifact_store
        self._ensure_table()

    @property
    def has_rag(self) -> bool:
        return self._retrieval is not None

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._database, timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_table(self) -> None:
        conn = self._connect()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS chat_messages (
                    message_id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_chat_messages_created "
                "ON chat_messages(created_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_chat_messages_conversation "
                "ON chat_messages(conversation_id, created_at)"
            )
            try:
                conn.execute("ALTER TABLE chat_messages ADD COLUMN context_artifact_id TEXT")
            except sqlite3.OperationalError:
                pass  # 列已存在
            conn.commit()
        finally:
            conn.close()

    def direct_answer(self, question: str, conversation_id: str | None) -> dict:
        normalized = (question or "").strip()
        if not normalized:
            raise ValueError("question must not be empty")
        if len(normalized) > MAX_QUESTION_CHARS:
            raise ValueError(f"question must be at most {MAX_QUESTION_CHARS} characters")
        conversation = (conversation_id or "").strip() or f"conv-{uuid4().hex}"
        history = self.conversation(conversation, limit=HISTORY_MESSAGE_LIMIT)

        now = _utc_now()
        self._append(
            ChatMessage(f"msg-{uuid4().hex}", conversation, "user", "direct", normalized, now)
        )

        context_blocks = [
            f"{message.role}: {message.content}" for message in history
        ] + [f"user: {normalized}"]
        completion = self._chat.complete(
            system_prompt=DIRECT_SYSTEM_PROMPT,
            user_prompt="\n\n".join(context_blocks),
            max_output_tokens=4096,
            temperature=0.3,
            json_object=False,
            enable_thinking=False,
            producer_step_id="chat-direct",
        )
        answer = (completion.content or "").strip()[:MAX_CONTENT_CHARS] or "(空回答)"
        assistant = ChatMessage(
            f"msg-{uuid4().hex}", conversation, "assistant", "direct", answer, _utc_now()
        )
        self._append(assistant)
        return {
            "message_id": assistant.message_id,
            "conversation_id": conversation,
            "role": assistant.role,
            "mode": assistant.mode,
            "content": assistant.content,
            "created_at": assistant.created_at,
            "provider_response_id": completion.response_id,
        }

    def rag_answer(self, question: str, conversation_id: str | None) -> dict:
        """W3.1 模式 B：本地语料检索 → 一次成文，片段引用（未核验聚合）。"""
        if self._retrieval is None:
            raise RuntimeError("knowledge corpus is not available")
        normalized = (question or "").strip()
        if not normalized:
            raise ValueError("question must not be empty")
        if len(normalized) > MAX_QUESTION_CHARS:
            raise ValueError(f"question must be at most {MAX_QUESTION_CHARS} characters")
        conversation = (conversation_id or "").strip() or f"conv-{uuid4().hex}"

        run = self._retrieval.search(normalized)
        hits = run.final.hits[:RAG_SNIPPET_LIMIT]
        snippets = []
        for index, hit in enumerate(hits, 1):
            document = self._retrieval.document_by_id[hit.document_id]
            snippets.append(
                {
                    "index": index,
                    "chunk_id": hit.document_id,
                    "score": hit.score,
                    "source_snapshot_id": hit.source_snapshot_id or "",
                    "locator": hit.locator or {},
                    "text": document.text[:RAG_SNIPPET_CHARS],
                }
            )
        if not snippets:
            raise ValueError("knowledge corpus returned no matching chunks")

        self._append(
            ChatMessage(
                f"msg-{uuid4().hex}", conversation, "user", "rag", normalized, _utc_now()
            )
        )
        history = self.conversation(conversation, limit=HISTORY_MESSAGE_LIMIT)
        context_blocks = [f"{message.role}: {message.content}" for message in history]
        snippet_blocks = [
            f"[{item['index']}] chunk `{item['chunk_id']}` "
            f"(snapshot `{item['source_snapshot_id']}`, 定位 "
            f"{json.dumps(item['locator'], ensure_ascii=False)})\n{item['text']}"
            for item in snippets
        ]
        user_prompt = (
            ("会话历史：\n" + "\n\n".join(context_blocks) + "\n\n" if context_blocks else "")
            + "知识库片段：\n" + "\n\n".join(snippet_blocks)
            + "\n\n问题：" + normalized
        )
        completion = self._chat.complete(
            system_prompt=RAG_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            max_output_tokens=4096,
            temperature=0.2,
            json_object=False,
            enable_thinking=False,
            producer_step_id="chat-rag",
        )
        answer = (completion.content or "").strip()[:MAX_CONTENT_CHARS] or "(空回答)"
        source_lines = [
            f"- [{item['index']}] chunk `{item['chunk_id']}` · snapshot "
            f"`{item['source_snapshot_id']}` · 定位 "
            f"{json.dumps(item['locator'], ensure_ascii=False)}"
            for item in snippets
        ]
        content = (
            answer
            + "\n\n---\n**来源（知识库片段 · 未核验聚合）**\n"
            + "\n".join(source_lines)
        )
        context_artifact_id = None
        if self._store is not None:
            context_ref = self._store.put_json(
                {
                    "schema_version": "conflux-weave.chat-rag-context.v1",
                    "question": normalized,
                    "conversation_id": conversation,
                    "hits": [
                        {**{k: v for k, v in item.items() if k != "text"}, "text": item["text"][:400]}
                        for item in snippets
                    ],
                },
                producer_step_id="chat-rag",
                schema_version="conflux-weave.chat-rag-context.v1",
            )
            context_artifact_id = context_ref.artifact_id
        assistant = ChatMessage(
            f"msg-{uuid4().hex}", conversation, "assistant", "rag", content, _utc_now(),
            context_artifact_id,
        )
        self._append(assistant)
        return {
            "message_id": assistant.message_id,
            "conversation_id": conversation,
            "role": assistant.role,
            "mode": assistant.mode,
            "content": assistant.content,
            "created_at": assistant.created_at,
            "provider_response_id": completion.response_id,
            "citations": [
                {
                    "index": item["index"],
                    "chunk_id": item["chunk_id"],
                    "source_snapshot_id": item["source_snapshot_id"],
                    "locator": item["locator"],
                }
                for item in snippets
            ],
        }

    def history(self, limit: int = 20) -> list[ChatMessage]:
        limit = max(1, min(int(limit), 100))
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT message_id, conversation_id, role, mode, content, created_at, context_artifact_id "
                "FROM chat_messages ORDER BY created_at DESC, message_id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        finally:
            conn.close()
        return [
            ChatMessage(
                row["message_id"], row["conversation_id"], row["role"], row["mode"],
                row["content"], row["created_at"], row["context_artifact_id"],
            )
            for row in reversed(rows)
        ]

    def conversation(self, conversation_id: str, limit: int = HISTORY_MESSAGE_LIMIT) -> list[ChatMessage]:
        limit = max(1, min(int(limit), 100))
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT message_id, conversation_id, role, mode, content, created_at, context_artifact_id "
                "FROM chat_messages WHERE conversation_id = ? "
                "ORDER BY created_at DESC, message_id DESC LIMIT ?",
                (conversation_id, limit),
            ).fetchall()
        finally:
            conn.close()
        return [
            ChatMessage(
                row["message_id"], row["conversation_id"], row["role"], row["mode"],
                row["content"], row["created_at"], row["context_artifact_id"],
            )
            for row in reversed(rows)
        ]

    def _append(self, message: ChatMessage) -> None:
        conn = self._connect()
        try:
            conn.execute(
                "INSERT INTO chat_messages "
                "(message_id, conversation_id, role, mode, content, created_at, context_artifact_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    message.message_id,
                    message.conversation_id,
                    message.role,
                    message.mode,
                    message.content,
                    message.created_at,
                    message.context_artifact_id,
                ),
            )
            conn.commit()
        finally:
            conn.close()
