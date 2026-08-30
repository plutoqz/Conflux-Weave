"""Lightweight direct-chat service (W3.0 模式 A：直接问答).

直接问答不创建 durable Run、不产出报告工件：问题原样发送到 LLM，仅将
对话记录持久化到 chat_messages 表。模型知识回答，无证据引用，由前端
显式标注。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from conflux_weave.provider import OpenAICompatibleChatAdapter


CHAT_SCHEMA_VERSION = "conflux-weave.chat-message.v1"
DIRECT_SYSTEM_PROMPT = (
    "你是 Conflux-Weave 研究工作台的直接问答助手。直接、准确地回答用户问题，"
    "可以使用你的通用知识；不要编造引用、来源或具体统计数字。回答使用与问题"
    "相同的语言，适当使用 Markdown 组织内容，保持简洁。"
)
HISTORY_MESSAGE_LIMIT = 8
MAX_QUESTION_CHARS = 8000
MAX_CONTENT_CHARS = 32_000


@dataclass(frozen=True, slots=True)
class ChatMessage:
    message_id: str
    conversation_id: str
    role: str
    mode: str
    content: str
    created_at: str


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class ChatService:
    """直接问答：LLM 原样应答 + 对话记录持久化（W3.0 模式 A）。"""

    def __init__(
        self,
        chat_adapter: OpenAICompatibleChatAdapter,
        database: Path | str,
    ) -> None:
        self._chat = chat_adapter
        self._database = Path(database)
        self._ensure_table()

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

    def history(self, limit: int = 20) -> list[ChatMessage]:
        limit = max(1, min(int(limit), 100))
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT message_id, conversation_id, role, mode, content, created_at "
                "FROM chat_messages ORDER BY created_at DESC, message_id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        finally:
            conn.close()
        return [
            ChatMessage(
                row["message_id"], row["conversation_id"], row["role"], row["mode"],
                row["content"], row["created_at"],
            )
            for row in reversed(rows)
        ]

    def conversation(self, conversation_id: str, limit: int = HISTORY_MESSAGE_LIMIT) -> list[ChatMessage]:
        limit = max(1, min(int(limit), 100))
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT message_id, conversation_id, role, mode, content, created_at "
                "FROM chat_messages WHERE conversation_id = ? "
                "ORDER BY created_at DESC, message_id DESC LIMIT ?",
                (conversation_id, limit),
            ).fetchall()
        finally:
            conn.close()
        return [
            ChatMessage(
                row["message_id"], row["conversation_id"], row["role"], row["mode"],
                row["content"], row["created_at"],
            )
            for row in reversed(rows)
        ]

    def _append(self, message: ChatMessage) -> None:
        conn = self._connect()
        try:
            conn.execute(
                "INSERT INTO chat_messages "
                "(message_id, conversation_id, role, mode, content, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    message.message_id,
                    message.conversation_id,
                    message.role,
                    message.mode,
                    message.content,
                    message.created_at,
                ),
            )
            conn.commit()
        finally:
            conn.close()
