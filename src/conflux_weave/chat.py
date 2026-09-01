"""Lightweight direct-chat service (W3.0 模式 A / W3.1 模式 B).

两种模式都不创建 durable Run、不产出报告工件，仅将对话记录持久化到
chat_messages 表。模式 A 问题原样发送到 LLM，回答即模型知识，无证据引用；
模式 B 检索本地语料后由模型综合成文（片段是写作素材而非答案结构），并做
确定性后检（引用合法性与覆盖率、正文长度底线），未通过时重试一次，仍不
通过则按原文交付并显式标注降级。两者都在响应元数据携带 verification 标记
（内容本身不加脚注，避免脚注随会话历史回流进模型上下文）。
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from conflux_weave.documents import document_page_label, document_title_from_segments
from conflux_weave.provider import OpenAICompatibleChatAdapter
from conflux_weave.runtime.artifacts import LocalArtifactStore


CHAT_SCHEMA_VERSION = "conflux-weave.chat-message.v1"
# 响应元数据中的证据基准标记（W3.3）：前端角标仍按 mode 渲染，该字段供
# 导出与非工作台消费方在脱离 UI 后仍能读到"未核验"语义。
VERIFICATION_MODEL_KNOWLEDGE = "model-knowledge"
VERIFICATION_UNVERIFIED_AGGREGATION = "unverified-aggregation"
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
    "你是 Conflux-Weave 工作台的知识库问答助手。只使用提供的知识库片段回答"
    "问题；片段是供你综合写作的素材，不是逐条转写的清单。组织方式：第一段"
    "用一两句话直接回答问题本身（先给结论）；正文围绕回答该问题所需的 2-4 "
    "个论点分段展开，每个论点是一段连贯分析，综合多个片段的素材来支撑它；"
    "引用编号 [n] 标注在依托该片段做出判断的那句话句末，不要只在段尾集中"
    "标注。片段未覆盖的方面，在结尾用一小段明确说明知识库未覆盖，不要编造。"
    "回答使用与问题相同的语言，不写与片段无关的客套话。"
)
# 确定性后检（零模型调用）：首答未通过时带反馈重试一次，仍未通过则按原文
# 交付并在响应与上下文工件中显式标注降级。
RAG_MAX_ATTEMPTS = 2
RAG_MIN_BODY_CHARS = 120  # 2-3 个片段时的正文长度底线
RAG_MIN_BODY_CHARS_RICH = 300  # ≥4 个片段时的正文长度底线
CITATION_INDEX_PATTERN = re.compile(r"\[(\d+)\]")


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


def _check_rag_answer(answer: str, snippet_count: int) -> tuple[str, ...]:
    """模式 B 确定性后检：返回违规列表（空 = 通过），零模型调用。

    三项检查：引用编号必须是所给片段的编号；被实际引用的片段至少占一半
    （防止回答绕开检索素材自说自话）；正文长度须与素材量相称。首段结论、
    论点组织等不可确定性判定的要求交给提示词，不在这里伪造校验。
    """
    stripped = answer.strip()
    indexes = {int(value) for value in CITATION_INDEX_PATTERN.findall(answer)}
    violations = []
    invalid = sorted(index for index in indexes if not 1 <= index <= snippet_count)
    if invalid:
        violations.append(f"引用编号超出片段范围：{invalid}")
    cited = len(indexes) - len(invalid)
    required = (snippet_count + 1) // 2
    if snippet_count >= 2 and cited < required:
        violations.append(f"仅实际引用 {cited}/{snippet_count} 个片段，至少需要 {required} 个")
    if snippet_count >= 4:
        floor = RAG_MIN_BODY_CHARS_RICH
    elif snippet_count >= 2:
        floor = RAG_MIN_BODY_CHARS
    else:
        floor = 0
    if len(stripped) < floor:
        violations.append(f"正文长度 {len(stripped)} 低于底线 {floor}")
    return tuple(violations)


def _rag_retry_feedback(violations: tuple[str, ...]) -> str:
    return (
        "\n\n【重试】上一次回答未通过校验：" + "；".join(violations)
        + "。请重新回答：第一段直接给结论，正文按论点综合片段成文；"
        "引用编号只能是所提供片段的编号，标注在依托该片段的判断句句末，"
        "并确保至少一半片段被实际引用；正文保持充实。"
    )


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
            "verification": VERIFICATION_MODEL_KNOWLEDGE,
        }

    def rag_answer(self, question: str, conversation_id: str | None) -> dict:
        """W3.1 模式 B：本地语料检索 → 综合成文 → 确定性后检（未核验聚合）。"""
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
        base_user_prompt = (
            ("会话历史：\n" + "\n\n".join(context_blocks) + "\n\n" if context_blocks else "")
            + "知识库片段：\n" + "\n\n".join(snippet_blocks)
            + "\n\n问题：" + normalized
        )
        violations: tuple[str, ...] = ()
        completion = None
        answer = ""
        for attempt in range(RAG_MAX_ATTEMPTS):
            completion = self._chat.complete(
                system_prompt=RAG_SYSTEM_PROMPT,
                user_prompt=(
                    base_user_prompt if attempt == 0
                    else base_user_prompt + _rag_retry_feedback(violations)
                ),
                max_output_tokens=4096,
                temperature=0.2,
                json_object=False,
                enable_thinking=False,
                producer_step_id="chat-rag" if attempt == 0 else "chat-rag-retry",
            )
            answer = (completion.content or "").strip()[:MAX_CONTENT_CHARS] or "(空回答)"
            violations = _check_rag_answer(answer, len(snippets))
            if not violations:
                break
        # W3.5：来源脚注压缩为紧凑引用（文档标题+页码），与深度研究报告的
        # 来源引用同一排版语义；chunk/snapshot/定位 JSON 留在 API citations
        # 与上下文工件中，不再进入用户视图。
        source_lines = []
        for item in snippets:
            title = document_title_from_segments(
                self._retrieval.document_by_id, item["chunk_id"], item["chunk_id"]
            )
            source_lines.append(
                f"- [{item['index']}] 《{title}》[本地], {document_page_label(item['locator'])}"
            )
        if violations:
            source_lines.append(
                "- ⚠ 回答未通过确定性校验（" + "；".join(violations)
                + "），已按模型原文交付。"
            )
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
                    "answer_checks": {
                        "status": "passed" if not violations else "degraded",
                        "violations": list(violations),
                        "attempts": 1 if not violations else RAG_MAX_ATTEMPTS,
                    },
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
            "verification": VERIFICATION_UNVERIFIED_AGGREGATION,
            "checks": {
                "status": "passed" if not violations else "degraded",
                "violations": list(violations),
            },
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
