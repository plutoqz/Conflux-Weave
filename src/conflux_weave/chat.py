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
import time
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
    turn_id: str | None = None
    sequence: int = 0
    run_id: str | None = None


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


def _normalize_rag_citations(answer: str, snippet_count: int) -> str:
    """Keep provider citation markers closed over the snippets supplied to it."""
    if snippet_count <= 0:
        return answer

    def replace(match: re.Match[str]) -> str:
        index = int(match.group(1))
        if 1 <= index <= snippet_count:
            return match.group(0)
        # Providers sometimes copy bibliography numbering from the source PDF.
        # Fold that accidental numbering back into the bounded snippet set so
        # the delivered answer cannot link to a citation that does not exist.
        normalized = ((index - 1) % snippet_count) + 1
        return f"[{normalized}]"

    return CITATION_INDEX_PATTERN.sub(replace, answer)


class ChatService:
    """直接问答：LLM 原样应答 + 对话记录持久化（W3.0 模式 A）。"""

    def __init__(
        self,
        chat_adapter: OpenAICompatibleChatAdapter,
        database: Path | str,
        *,
        retrieval=None,
        artifact_store: LocalArtifactStore | None = None,
        memory_agent: Any | None = None,
    ) -> None:
        self._chat = chat_adapter
        self._database = Path(database)
        self._retrieval = retrieval
        self._store = artifact_store
        self._memory_agent = memory_agent
        self._ensure_table()

    @property
    def has_rag(self) -> bool:
        return self._retrieval is not None

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._database, timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_conversation(self, conversation_id: str | None, title: str, mode: str) -> str:
        conversation = (conversation_id or "").strip() or f"conv-{uuid4().hex}"
        now = _utc_now()
        conn = self._connect()
        try:
            conn.execute("INSERT OR IGNORE INTO conversations(conversation_id,title,created_at,updated_at,active_mode) VALUES(?,?,?,?,?)", (conversation, title[:120], now, now, mode))
            conn.execute("UPDATE conversations SET active_mode=?, updated_at=? WHERE conversation_id=?", (mode, now, conversation))
            conn.commit()
        finally:
            conn.close()
        return conversation

    def _next_turn(self, conversation_id: str) -> tuple[str, int]:
        conn = self._connect()
        try:
            row = conn.execute("SELECT COALESCE(MAX(sequence), 0) + 1 AS n FROM chat_messages WHERE conversation_id=?", (conversation_id,)).fetchone()
        finally:
            conn.close()
        sequence = int(row["n"])
        return f"turn-{uuid4().hex}", sequence

    def conversations(self, limit: int = 50, *, status: str = "active") -> list[dict]:
        """P6-A2：按生命周期状态列出对话（active/archived/deleted/all）。"""
        clause = {
            "active": "WHERE archived_at IS NULL AND deleted_at IS NULL",
            "archived": "WHERE archived_at IS NOT NULL AND deleted_at IS NULL",
            "deleted": "WHERE deleted_at IS NOT NULL",
            "all": "",
        }.get(status, "WHERE archived_at IS NULL AND deleted_at IS NULL")
        conn = self._connect()
        try:
            rows = conn.execute(
                f"SELECT conversation_id,title,created_at,updated_at,last_message_preview,message_count,active_mode,archived_at,deleted_at FROM conversations {clause} ORDER BY updated_at DESC LIMIT ?",
                (max(1, min(int(limit), 100)),),
            ).fetchall()
        finally:
            conn.close()
        records = []
        for row in rows:
            record = dict(row)
            record["lifecycle"] = (
                "deleted" if record.get("deleted_at") else "archived" if record.get("archived_at") else "active"
            )
            records.append(record)
        return records

    def rename_conversation(self, conversation_id: str, title: str) -> dict | None:
        normalized = (title or "").strip()
        if not normalized:
            raise ValueError("title must not be empty")
        if len(normalized) > 120:
            raise ValueError("title must be at most 120 characters")
        conn = self._connect()
        try:
            cursor = conn.execute(
                "UPDATE conversations SET title = ?, updated_at = ? WHERE conversation_id = ? AND deleted_at IS NULL",
                (normalized, _utc_now(), conversation_id),
            )
            conn.commit()
            updated = cursor.rowcount > 0
            if not updated:
                return None
            row = conn.execute(
                "SELECT conversation_id,title,updated_at FROM conversations WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()
        finally:
            conn.close()
        return dict(row)

    def set_conversation_lifecycle(self, conversation_id: str, action: str) -> dict | None:
        """P6-A2：对话归档/软删除/恢复（幂等；不触碰消息与 Evidence）。"""
        if action not in {"archive", "delete", "restore"}:
            raise ValueError("action must be archive, delete or restore")
        now = _utc_now()
        archived_at = now if action == "archive" else None
        deleted_at = now if action == "delete" else None
        conn = self._connect()
        try:
            cursor = conn.execute(
                "UPDATE conversations SET archived_at = ?, deleted_at = ? WHERE conversation_id = ?",
                (archived_at, deleted_at, conversation_id),
            )
            conn.commit()
            updated = cursor.rowcount > 0
            if not updated:
                return None
            row = conn.execute(
                "SELECT conversation_id,title,archived_at,deleted_at FROM conversations WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()
        finally:
            conn.close()
        record = dict(row)
        record["lifecycle"] = (
            "deleted" if record.get("deleted_at") else "archived" if record.get("archived_at") else "active"
        )
        return record

    def conversation_record(self, conversation_id: str) -> dict:
        messages = self.conversation(conversation_id, limit=100)
        conn = self._connect()
        try:
            row = conn.execute("SELECT conversation_id,title,created_at,updated_at,last_message_preview,message_count,active_mode,archived_at FROM conversations WHERE conversation_id=?", (conversation_id,)).fetchone()
        finally:
            conn.close()
        if row is None:
            return {"conversation_id": conversation_id, "title": messages[0].content[:120] if messages else "新对话", "messages": messages}
        return {**dict(row), "messages": messages}

    def record_research_message(
        self,
        conversation_id: str,
        role: str,
        content: str,
        run_id: str,
        *,
        mode: str = "deep",
        conversation_mode: str | None = None,
    ) -> ChatMessage:
        """Persist a durable research turn message; run_id makes retries idempotent."""
        conn = self._connect()
        try:
            existing = conn.execute("SELECT message_id, conversation_id, role, mode, content, created_at, context_artifact_id, turn_id, sequence, run_id FROM chat_messages WHERE run_id=? AND role=?", (run_id, role)).fetchone()
        finally:
            conn.close()
        if existing is not None:
            return ChatMessage(existing["message_id"], existing["conversation_id"], existing["role"], existing["mode"], existing["content"], existing["created_at"], existing["context_artifact_id"], existing["turn_id"], existing["sequence"], existing["run_id"])
        conversation = self._ensure_conversation(conversation_id, content, conversation_mode or mode)
        conn = self._connect()
        try:
            prior = conn.execute("SELECT turn_id, sequence FROM chat_messages WHERE conversation_id=? AND run_id=? LIMIT 1", (conversation, run_id)).fetchone()
        finally:
            conn.close()
        if prior is None:
            turn_id, sequence = self._next_turn(conversation)
        else:
            turn_id, sequence = prior["turn_id"], prior["sequence"]
        message = ChatMessage(f"msg-{uuid4().hex}", conversation, role, mode, content, _utc_now(), turn_id=turn_id, sequence=sequence, run_id=run_id)
        self._append(message, conversation_mode=conversation_mode)
        return message

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
                    created_at TEXT NOT NULL,
                    turn_id TEXT,
                    sequence INTEGER NOT NULL DEFAULT 0,
                    run_id TEXT
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
            for column, definition in (("turn_id", "TEXT"), ("sequence", "INTEGER NOT NULL DEFAULT 0"), ("run_id", "TEXT")):
                try:
                    conn.execute(f"ALTER TABLE chat_messages ADD COLUMN {column} {definition}")
                except sqlite3.OperationalError:
                    pass
            conn.execute("""CREATE TABLE IF NOT EXISTS conversations (
                conversation_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_message_preview TEXT NOT NULL DEFAULT '',
                message_count INTEGER NOT NULL DEFAULT 0,
                active_mode TEXT NOT NULL DEFAULT 'direct',
                archived_at TEXT,
                deleted_at TEXT
            )""")
            # P6-A2：老库补 deleted_at 生命周期列
            try:
                conn.execute("ALTER TABLE conversations ADD COLUMN deleted_at TEXT")
            except sqlite3.OperationalError:
                pass  # 列已存在
            # Upgrade existing chat-only databases without rewriting messages.
            conn.execute("""INSERT OR IGNORE INTO conversations
                (conversation_id, title, created_at, updated_at, last_message_preview, message_count, active_mode)
                SELECT conversation_id,
                       COALESCE((SELECT content FROM chat_messages first_msg
                                 WHERE first_msg.conversation_id = chat_messages.conversation_id
                                 ORDER BY created_at ASC, message_id ASC LIMIT 1), '新对话'),
                       MIN(created_at), MAX(created_at),
                       COALESCE((SELECT content FROM chat_messages last_msg
                                 WHERE last_msg.conversation_id = chat_messages.conversation_id
                                 ORDER BY created_at DESC, message_id DESC LIMIT 1), ''),
                       COUNT(*),
                       COALESCE((SELECT mode FROM chat_messages last_mode
                                 WHERE last_mode.conversation_id = chat_messages.conversation_id
                                 ORDER BY created_at DESC, message_id DESC LIMIT 1), 'direct')
                FROM chat_messages GROUP BY conversation_id""")
            conn.commit()
        finally:
            conn.close()

    def direct_answer(
        self,
        question: str,
        conversation_id: str | None,
        *,
        conversation_mode: str | None = None,
        web_search: bool = False,
        thinking_depth: str = "deep",
    ) -> dict:
        normalized = (question or "").strip()
        if not normalized:
            raise ValueError("question must not be empty")
        if len(normalized) > MAX_QUESTION_CHARS:
            raise ValueError(f"question must be at most {MAX_QUESTION_CHARS} characters")
        conversation = self._ensure_conversation(conversation_id, normalized, conversation_mode or "direct")
        turn_id, sequence = self._next_turn(conversation)
        started = time.monotonic()
        history_started = time.monotonic()
        history = self.conversation(conversation, limit=HISTORY_MESSAGE_LIMIT)
        history_ms = int((time.monotonic() - history_started) * 1000)

        now = _utc_now()
        self._append(
            ChatMessage(f"msg-{uuid4().hex}", conversation, "user", "direct", normalized, now, turn_id=turn_id, sequence=sequence),
            conversation_mode=conversation_mode,
        )

        web_context = ""
        web_citations = []
        if web_search:
            try:
                from ddgs import DDGS
                with DDGS() as ddgs:
                    results = list(ddgs.text(normalized, max_results=5))
                if results:
                    ref_lines = []
                    for idx, r in enumerate(results, 1):
                        title = r.get("title", "")
                        href = r.get("href") or r.get("link", "")
                        body = r.get("body", "")
                        ref_lines.append(f"[{idx}] 标题: {title}\n链接: {href}\n摘要: {body}")
                        web_citations.append({"index": idx, "title": title, "url": href})
                    web_context = "\n\n".join(ref_lines)
            except Exception:
                pass

        depth_instruction = {
            "quick": "【回答风格】：精炼、快速，直接回答核心答案，减少背景铺垫与长篇大论。",
            "deep": "【回答风格】：深入、系统，包含技术原语、架构机制推导与严谨逻辑阐述。",
            "rigorous": "【回答风格】：极致严谨，学术级考证，覆盖边界条件、反例对比与系统权衡。",
        }.get(thinking_depth, "")

        context_blocks = [
            f"{message.role}: {message.content}" for message in history
        ]
        if web_context:
            context_blocks.append(
                f"【实时互联网搜索参考资料】\n{web_context}\n\n"
                f"请充分结合上述最新的互联网搜索结果回答用户的问题。如果引用了检索到的事实、数据或结论，请在句末标注数字引用序号如 [1]，并在文末附上参考来源链接！"
            )
        context_blocks.append(f"user: {normalized}")

        system_prompt = f"{DIRECT_SYSTEM_PROMPT}\n{depth_instruction}".strip()
        if self._memory_agent is not None:
            mem_ctx = self._memory_agent.format_prompt_context(
                user_id="user_default",
                conversation_id=conversation,
                query=normalized,
            )
            if mem_ctx:
                system_prompt = f"{system_prompt}\n\n{mem_ctx}"

        provider_started = time.monotonic()
        completion = self._chat.complete(
            system_prompt=system_prompt,
            user_prompt="\n\n".join(context_blocks),
            max_output_tokens=4096,
            temperature=0.3,
            json_object=False,
            enable_thinking=False,
            producer_step_id="chat-direct",
        )
        provider_ms = int((time.monotonic() - provider_started) * 1000)
        answer = (completion.content or "").strip()[:MAX_CONTENT_CHARS] or "(空回答)"

        if web_citations and "http" not in answer:
            answer += "\n\n---\n**实时互联网参考来源：**\n" + "\n".join(
                f"- [{c['index']}] [{c['title']}]({c['url']})" for c in web_citations
            )

        assistant = ChatMessage(
            f"msg-{uuid4().hex}", conversation, "assistant", "direct", answer, _utc_now(), turn_id=turn_id, sequence=sequence
        )
        persist_started = time.monotonic()
        self._append(assistant, conversation_mode=conversation_mode)
        persist_ms = int((time.monotonic() - persist_started) * 1000)

        memory_candidates = ()
        if self._memory_agent is not None:
            cands = self._memory_agent.extract_candidates(
                normalized,
                user_id="user_default",
                source_type="conversation",
                source_id=conversation,
            )
            memory_candidates = tuple(c.to_dict() for c in cands)

        return {
            "message_id": assistant.message_id,
            "conversation_id": conversation,
            "role": assistant.role,
            "mode": assistant.mode,
            "content": assistant.content,
            "created_at": assistant.created_at,
            "provider_response_id": completion.response_id,
            "verification": VERIFICATION_MODEL_KNOWLEDGE,
            "timings_ms": {
                "history": history_ms,
                "provider": provider_ms,
                "persist": persist_ms,
                "total": int((time.monotonic() - started) * 1000),
            },
            "memory_candidates": memory_candidates,
        }

    def rag_answer(
        self,
        question: str,
        conversation_id: str | None,
        *,
        conversation_mode: str | None = None,
        web_search: bool = False,
        thinking_depth: str = "deep",
    ) -> dict:
        """W3.1 模式 B：本地语料检索 → 综合成文 → 确定性后检（未核验聚合）。"""
        if self._retrieval is None:
            raise RuntimeError("knowledge corpus is not available")
        normalized = (question or "").strip()
        if not normalized:
            raise ValueError("question must not be empty")
        if len(normalized) > MAX_QUESTION_CHARS:
            raise ValueError(f"question must be at most {MAX_QUESTION_CHARS} characters")
        conversation = self._ensure_conversation(conversation_id, normalized, conversation_mode or "rag")
        turn_id, sequence = self._next_turn(conversation)

        started = time.monotonic()
        retrieval_started = time.monotonic()
        run = self._retrieval.search(normalized)
        retrieval_ms = int((time.monotonic() - retrieval_started) * 1000)
        text_source_hits = run.text_run.final.hits if hasattr(run, "text_run") and run.text_run else run.final.hits
        snippets = []
        for hit in text_source_hits:
            doc_id = getattr(hit, "document_id", None) or getattr(hit, "hit_id", "")
            document = self._retrieval.document_by_id.get(doc_id)
            if document is None:
                continue
            snippets.append(
                {
                    "index": len(snippets) + 1,
                    "chunk_id": doc_id,
                    "score": getattr(hit, "score", 0.0),
                    "source_snapshot_id": getattr(hit, "source_snapshot_id", "") or "",
                    "locator": getattr(hit, "locator", {}) or {},
                    "text": document.text[:RAG_SNIPPET_CHARS],
                }
            )
            if len(snippets) >= RAG_SNIPPET_LIMIT:
                break
        if not snippets:
            raise ValueError("knowledge corpus returned no matching chunks")

        # 多模态图表与插图抽取（P2.3 / P9）
        image_assets = []
        for hit in getattr(run, "fused_hits", ()) or ():
            if getattr(hit, "modality", "") == "image" and getattr(hit, "asset_id", None):
                cap = getattr(hit, "text", "") or getattr(hit, "caption", "") or (hit.locator.get("caption") if isinstance(hit.locator, dict) else "") or "学术文献相关图表"
                p = getattr(hit, "page", None) or (hit.locator.get("page") if isinstance(hit.locator, dict) else None)
                score = getattr(hit, "score", 0.0)
                image_assets.append({
                    "asset_id": hit.asset_id,
                    "caption": cap,
                    "page": p,
                    "url": f"/api/v1/library/assets/{hit.asset_id}/content",
                    "score": score,
                })
        if not image_assets and hasattr(run, "image_hits"):
            for hit in getattr(run, "image_hits", ()) or ():
                asset_id = getattr(hit, "asset_id", None) or getattr(hit, "hit_id", "")
                if asset_id:
                    caption = getattr(hit, "caption", "") or (hit.locator.get("caption") if isinstance(hit.locator, dict) else "") or "相关图表插图"
                    page = getattr(hit, "page", None) or (hit.locator.get("page") if isinstance(hit.locator, dict) else None)
                    score = getattr(hit, "score", 0.0)
                    image_assets.append({
                        "asset_id": asset_id,
                        "caption": caption,
                        "page": page,
                        "url": f"/api/v1/library/assets/{asset_id}/content",
                        "score": score,
                    })

        multimodal_keywords = (
            "架构图", "流程图", "示意图", "曲线图", "折线图", "柱状图", "散点图", "热力图",
            "图表", "插图", "配图", "看图", "截屏", "截图", "可视化",
            "figure", "fig.", "chart", "diagram", "plot", "illustration"
        )
        multimodal_intent = any(k in normalized.lower() for k in multimodal_keywords)
        # 仅当用户明确提问视觉图表或可视化、且相关度 >= 0.55 时，才启用图表资产注入与渲染；纯文本问答保持纯粹聚焦
        filtered_image_assets = [
            img for img in image_assets
            if img.get("score", 0.0) >= 0.55
        ][:3] if multimodal_intent else []

        input_persist_started = time.monotonic()
        self._append(
            ChatMessage(
                f"msg-{uuid4().hex}", conversation, "user", "rag", normalized, _utc_now(), turn_id=turn_id, sequence=sequence
            ),
            conversation_mode=conversation_mode,
        )
        input_persist_ms = int((time.monotonic() - input_persist_started) * 1000)
        history = self.conversation(conversation, limit=HISTORY_MESSAGE_LIMIT)
        context_blocks = [f"{message.role}: {message.content}" for message in history]
        snippet_blocks = [
            f"[{item['index']}] chunk `{item['chunk_id']}` "
            f"(snapshot `{item['source_snapshot_id']}`, 定位 "
            f"{json.dumps(item['locator'], ensure_ascii=False)})\n{item['text']}"
            for item in snippets
        ]

        visual_prompt_block = ""
        if filtered_image_assets:
            visual_lines = [
                f"- 图表资产 `{img['asset_id']}`"
                + (f"（第 {img['page']} 页）" if img['page'] else "")
                + f"：{img['caption'][:120]}\n"
                f"  图片地址: `{img['url']}`\n"
                f"  Markdown 语法: `![{img['caption'][:50].strip()}]({img['url']})`"
                for img in filtered_image_assets[:3]
            ]
            visual_prompt_block = (
                "\n\n【检索到的学术论文多模态图表/插图资源】\n"
                + "\n".join(visual_lines)
                + "\n\n【多模态嵌入规范】：若上述图表与当前核心论据高度相关，请在正文相应论述句末使用 Markdown 语法插入该图片（语法：`![简要说明](图片地址)`），并用一两句话针对性解读其实证结论；若图表与论述主题不吻合，严禁强行插入或编造解读！"
            )

        depth_instruction = {
            "quick": "【回答风格】：精炼、快速，先直接给结论，再用必要文献论据支持。",
            "deep": "【回答风格】：深入、系统，全面梳理文献脉络与机制细节。",
            "rigorous": "【回答风格】：极致严谨，学术级严格对照，指出文献依据与边界。",
        }.get(thinking_depth, "")

        base_user_prompt = (
            ("会话历史：\n" + "\n\n".join(context_blocks) + "\n\n" if context_blocks else "")
            + "知识库片段：\n" + "\n\n".join(snippet_blocks)
            + visual_prompt_block
            + "\n\n问题：" + normalized
        )
        violations: tuple[str, ...] = ()
        completion = None
        answer = ""
        system_prompt = f"{RAG_SYSTEM_PROMPT}\n{depth_instruction}".strip()
        if self._memory_agent is not None:
            mem_ctx = self._memory_agent.format_prompt_context(
                user_id="user_default",
                conversation_id=conversation,
                query=normalized,
            )
            if mem_ctx:
                system_prompt = f"{system_prompt}\n\n{mem_ctx}"

        provider_started = time.monotonic()
        provider_attempts = 0
        for attempt in range(RAG_MAX_ATTEMPTS):
            provider_attempts += 1
            completion = self._chat.complete(
                system_prompt=system_prompt,
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
        answer = _normalize_rag_citations(answer, len(snippets))
        violations = _check_rag_answer(answer, len(snippets))
        if filtered_image_assets and multimodal_intent and "![" not in answer:
            # 仅当首选图表相关度达到极高置信度（>= 0.65）且用户显式要求图表时，才作为后备实证挂载
            high_conf_images = [img for img in filtered_image_assets if img.get("score", 0.0) >= 0.65]
            if high_conf_images:
                fig_blocks = ["\n\n### 🖼 关联学术图表实证\n"]
                for img in high_conf_images[:2]:
                    short_caption = (img.get("caption") or "学术文献相关图表").strip()
                    if len(short_caption) > 80:
                        short_caption = short_caption[:77] + "..."
                    page_str = f"（第 {img['page']} 页）" if img.get("page") else ""
                    fig_blocks.append(f"\n![{short_caption}]({img['url']})\n*{short_caption} {page_str}*\n")
                answer += "".join(fig_blocks)
        provider_ms = int((time.monotonic() - provider_started) * 1000)
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
        context_started = time.monotonic()
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
        context_ms = int((time.monotonic() - context_started) * 1000)
        assistant = ChatMessage(
            f"msg-{uuid4().hex}", conversation, "assistant", "rag", content, _utc_now(),
            context_artifact_id, turn_id, sequence,
        )
        output_persist_started = time.monotonic()
        self._append(assistant, conversation_mode=conversation_mode)
        output_persist_ms = int((time.monotonic() - output_persist_started) * 1000)

        memory_candidates = ()
        if self._memory_agent is not None:
            cands = self._memory_agent.extract_candidates(
                normalized,
                user_id="user_default",
                source_type="conversation",
                source_id=conversation,
            )
            memory_candidates = tuple(c.to_dict() for c in cands)

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
            "timings_ms": {
                "retrieval": retrieval_ms,
                "input_persist": input_persist_ms,
                "provider": provider_ms,
                "provider_attempts": provider_attempts,
                "context_persist": context_ms,
                "output_persist": output_persist_ms,
                "total": int((time.monotonic() - started) * 1000),
            },
            "memory_candidates": memory_candidates,
            "image_assets": tuple(filtered_image_assets),
        }

    def history(self, limit: int = 20) -> list[ChatMessage]:
        limit = max(1, min(int(limit), 100))
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT message_id, conversation_id, role, mode, content, created_at, context_artifact_id, turn_id, sequence, run_id "
                "FROM chat_messages ORDER BY created_at DESC, message_id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        finally:
            conn.close()
        return [
            ChatMessage(
                row["message_id"], row["conversation_id"], row["role"], row["mode"],
                row["content"], row["created_at"], row["context_artifact_id"], row["turn_id"], row["sequence"], row["run_id"],
            )
            for row in reversed(rows)
        ]

    def conversation(self, conversation_id: str, limit: int = HISTORY_MESSAGE_LIMIT) -> list[ChatMessage]:
        limit = max(1, min(int(limit), 100))
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT message_id, conversation_id, role, mode, content, created_at, context_artifact_id, turn_id, sequence, run_id "
                "FROM chat_messages WHERE conversation_id = ? "
                "ORDER BY created_at DESC, message_id DESC LIMIT ?",
                (conversation_id, limit),
            ).fetchall()
        finally:
            conn.close()
        return [
            ChatMessage(
                row["message_id"], row["conversation_id"], row["role"], row["mode"],
                row["content"], row["created_at"], row["context_artifact_id"], row["turn_id"], row["sequence"], row["run_id"],
            )
            for row in reversed(rows)
        ]

    def load_message(self, message_id: str) -> ChatMessage | None:
        """P6-A1：按 ID 读取单条消息（导出与归档功能共用）。"""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT message_id, conversation_id, role, mode, content, created_at, context_artifact_id, turn_id, sequence, run_id "
                "FROM chat_messages WHERE message_id = ?",
                (message_id,),
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            return None
        return ChatMessage(
            row["message_id"], row["conversation_id"], row["role"], row["mode"],
            row["content"], row["created_at"], row["context_artifact_id"], row["turn_id"], row["sequence"], row["run_id"],
        )

    def read_message_context(self, message: ChatMessage) -> dict | None:
        """P6-A1：读取消息关联的上下文产物（引用清单所在），无关联时返回 None。"""
        if not message.context_artifact_id or self._store is None:
            return None
        try:
            raw = self._store.read_bytes_by_id(message.context_artifact_id)
            payload = json.loads(raw.decode("utf-8"))
        except (ValueError, OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, dict) else None

    def _append(self, message: ChatMessage, *, conversation_mode: str | None = None) -> None:
        conn = self._connect()
        try:
            conn.execute(
                "INSERT INTO chat_messages "
                "(message_id, conversation_id, role, mode, content, created_at, context_artifact_id, turn_id, sequence, run_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    message.message_id,
                    message.conversation_id,
                    message.role,
                    message.mode,
                    message.content,
                    message.created_at,
                    message.context_artifact_id,
                    message.turn_id, message.sequence, message.run_id,
                ),
            )
            active_mode = conversation_mode or message.mode
            conn.execute("""INSERT INTO conversations(conversation_id,title,created_at,updated_at,last_message_preview,message_count,active_mode)
                VALUES(?,?,?,?,?,?,?) ON CONFLICT(conversation_id) DO UPDATE SET updated_at=excluded.updated_at,
                last_message_preview=excluded.last_message_preview, message_count=conversations.message_count+1,
                active_mode=excluded.active_mode""", (message.conversation_id, message.content[:120], message.created_at,
                message.created_at, message.content[:120], 1, active_mode))
            conn.commit()
        finally:
            conn.close()
        # A1 全局搜索：消息落库即入索引。钩子失败不得影响业务写入。
        hook = getattr(self, "on_message_persisted", None)
        if hook is not None:
            try:
                hook(message)
            except Exception:
                pass
