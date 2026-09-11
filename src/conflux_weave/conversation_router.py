"""Unified Conversation Router (ConversationRouter) for Conflux-Weave (P4.2).

Responsibilities:
1. Two-stage intent classification:
   - Stage 1: Deterministic rules (command prefixes, @entity mentions, manual mode override);
   - Stage 2: Semantic intent classification (via LLM or deterministic offline heuristics).
2. Fast / Slow dual-path determination:
   - Fast path (direct, rag, memory): light streaming response, zero durable Run overhead;
   - Slow path (deep, document, project): standard durable Run, Harness orchestration, SSE event tracking.
3. Graceful fallback to direct answer under exceptions or unresolvable ambiguity.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import re
from typing import Any

from conflux_weave.provider import OpenAICompatibleChatAdapter


COMMAND_PREFIXES = {
    "/direct": ("direct", True, "命令前缀强制进入直接提问模式"),
    "/rag": ("rag", True, "命令前缀强制进入本地知识库检索问答模式"),
    "/deep": ("deep", False, "命令前缀强制创建深度研究任务"),
    "/doc": ("document", False, "命令前缀强制进入文档研读与权威笔记模式"),
    "/document": ("document", False, "命令前缀强制进入文档研读与权威笔记模式"),
    "/project": ("project", False, "命令前缀强制进入项目认知与代码治理模式"),
    "/audit": ("project", False, "命令前缀强制进入项目代码契约审计模式"),
    "/mem": ("memory", True, "命令前缀强制进入记忆中心查询偏好"),
    "/memory": ("memory", True, "命令前缀强制进入记忆中心查询偏好"),
}

ENTITY_PATTERNS = [
    (re.compile(r"@proj(?:ect)?:([A-Za-z0-9_\-\.]+)", re.IGNORECASE), "project_id", "project", False, "检测到显式项目引用：分流至项目认知与治理工作台"),
    (re.compile(r"@paper:([A-Za-z0-9_\-\.]+)", re.IGNORECASE), "paper_id", "document", False, "检测到显式论文引用：分流至文档研读与权威笔记"),
    (re.compile(r"@note:([A-Za-z0-9_\-\.]+)", re.IGNORECASE), "note_id", "document", False, "检测到显式笔记引用：分流至文档研读与权威笔记"),
    (re.compile(r"@run:([A-Za-z0-9_\-\.]+)", re.IGNORECASE), "run_id", "deep", False, "检测到显式研究 Run 引用：分流至深度研究追问"),
]

PROJECT_KEYWORDS = [
    "代码架构", "架构解构", "架构拓扑", "模块拓扑", "理论映射", "代码体检",
    "契约审计", "实现审计", "git差异", "语义差异", "实验分支", "脏工作区",
    "代码坏味道", "上帝类", "解耦建议", "codingagent", "项目健康度",
]

DEEP_RESEARCH_KEYWORDS = [
    "深度研究", "深度调研", "行业综述", "多源交叉", "对比分析报告", "全面综述",
    "全面调研", "技术演进综述", "系统性综述", "交叉比对", "生成深度报告",
    "研究报告", "查阅多篇论文并总结", "多源论证",
]

DOCUMENT_KEYWORDS = [
    "分析这篇论文", "精读论文", "提炼笔记", "阅读这篇文档", "生成笔记", "修订笔记",
    "权威笔记", "文档分析", "解析pdf", "段落修订",
]

MEMORY_KEYWORDS = [
    "你记住了什么", "我的偏好", "修改偏好", "记住我的", "查看记忆", "已保存的约定",
    "偏好设置", "记忆中心", "清除偏好",
]

RAG_KEYWORDS = [
    "本地知识库", "本地资料库", "已收录论文", "检索资料库", "知识库片段", "根据文献",
    "知识库问答", "查一下本地库", "资料库里有没有",
]

ROUTER_SYSTEM_PROMPT = """你是一个专业的学术工作台意图路由器。
根据用户的提问，判断应当由哪种模式来承接。

可用模式：
- "direct": 常规概念问答、闲聊、常识解释、语法调整，无需检索本地资料库；
- "rag": 依赖本地收录的论文或文档资料库片段进行问答；
- "deep": 复杂多源交叉深度调研、撰写系统综述报告、全网与本地多源论证；
- "document": 针对特定论文/文档的深度解析、提炼结构化权威笔记；
- "project": 分析本地代码库、Git实验分支差异、理论映射、架构解构或代码契约审计；
- "memory": 询问或管理用户的个人偏好、项目记忆。

请以 JSON 格式输出：
{
  "mode": "direct|rag|deep|document|project|memory",
  "confidence": 0.95,
  "reason": "简明人话理由"
}
"""


@dataclass(frozen=True, slots=True)
class RouterResult:
    target_mode: str
    is_fast_path: bool
    confidence: float
    intent_summary: str
    extracted_entities: dict[str, str] = field(default_factory=dict)
    suggested_run_kind: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ConversationRouter:
    """Intelligent intent classifier and fast/slow path dispatcher."""

    def __init__(self, chat_adapter: OpenAICompatibleChatAdapter | None = None) -> None:
        self.chat = chat_adapter

    def route(
        self,
        query: str,
        *,
        current_mode: str = "auto",
        conversation_id: str | None = None,
    ) -> RouterResult:
        text = (query or "").strip()
        if not text:
            return RouterResult(
                target_mode="direct",
                is_fast_path=True,
                confidence=1.0,
                intent_summary="输入为空，默认进入直接问答",
            )

        # 1. Manual mode override (user explicitly chose non-auto mode)
        if current_mode and current_mode != "auto":
            is_fast = current_mode in {"direct", "rag", "memory"}
            mode_labels = {
                "direct": "直接提问",
                "rag": "知识库问答",
                "deep": "深度研究",
                "document": "文档研读",
                "project": "项目工作台",
                "memory": "记忆中心",
            }
            return RouterResult(
                target_mode=current_mode,
                is_fast_path=is_fast,
                confidence=1.0,
                intent_summary=f"用户显式选定模式：{mode_labels.get(current_mode, current_mode)}",
                suggested_run_kind="managed_verified_research" if current_mode == "deep" else None,
            )

        # 2. Command prefix check (e.g. /deep, /rag, /project, /doc)
        for prefix, (mode, is_fast, reason) in COMMAND_PREFIXES.items():
            if text.lower().startswith(prefix):
                return RouterResult(
                    target_mode=mode,
                    is_fast_path=is_fast,
                    confidence=1.0,
                    intent_summary=reason,
                    suggested_run_kind="managed_verified_research" if mode == "deep" else None,
                )

        # 3. Entity mentions extraction (@project, @paper, @note, @run)
        entities: dict[str, str] = {}
        target_mode_by_entity = None
        is_fast_by_entity = True
        entity_reason = ""

        for pattern, entity_key, target_mode, is_fast, reason in ENTITY_PATTERNS:
            match = pattern.search(text)
            if match:
                entities[entity_key] = match.group(1).strip()
                if target_mode_by_entity is None:
                    target_mode_by_entity = target_mode
                    is_fast_by_entity = is_fast
                    entity_reason = f"{reason} ({entity_key}={match.group(1)})"

        if target_mode_by_entity:
            return RouterResult(
                target_mode=target_mode_by_entity,
                is_fast_path=is_fast_by_entity,
                confidence=0.98,
                intent_summary=entity_reason,
                extracted_entities=entities,
                suggested_run_kind="managed_verified_research" if target_mode_by_entity == "deep" else None,
            )

        # 4. Deterministic keyword heuristics (offline native, fast 0ms)
        heuristic = self._match_heuristics(text)
        if heuristic is not None:
            return heuristic

        # 5. Semantic classification via LLM (if chat_adapter is present and query is ambiguous)
        if self.chat is not None:
            llm_result = self._route_with_model(text)
            if llm_result is not None and llm_result.confidence >= 0.7:
                return llm_result

        # 6. Default fallback: direct answer
        return RouterResult(
            target_mode="direct",
            is_fast_path=True,
            confidence=0.80,
            intent_summary="常规问答与通用概念咨询，分流至直接提问快通道",
        )

    def _match_heuristics(self, text: str) -> RouterResult | None:
        lower = text.lower()

        # Check project intent
        if any(k in lower for k in PROJECT_KEYWORDS):
            return RouterResult(
                target_mode="project",
                is_fast_path=False,
                confidence=0.92,
                intent_summary="检测到代码认知、架构拓扑或工程契约审计意图，分流至项目治理慢通道",
            )

        # Check deep research intent
        if any(k in lower for k in DEEP_RESEARCH_KEYWORDS):
            return RouterResult(
                target_mode="deep",
                is_fast_path=False,
                confidence=0.90,
                intent_summary="检测到复杂学术综述或多源深度调研需求，分流至深度研究慢通道",
                suggested_run_kind="managed_verified_research",
            )

        # Check document analysis intent
        if any(k in lower for k in DOCUMENT_KEYWORDS):
            return RouterResult(
                target_mode="document",
                is_fast_path=False,
                confidence=0.88,
                intent_summary="检测到单篇论文精读或权威笔记提炼意图，分流至文档分析慢通道",
            )

        # Check memory center intent
        if any(k in lower for k in MEMORY_KEYWORDS):
            return RouterResult(
                target_mode="memory",
                is_fast_path=True,
                confidence=0.95,
                intent_summary="检测到个人偏好或项目记忆查询意图，分流至记忆中心",
            )

        # Check RAG intent
        if any(k in lower for k in RAG_KEYWORDS):
            return RouterResult(
                target_mode="rag",
                is_fast_path=True,
                confidence=0.86,
                intent_summary="检测到本地收录资料或论文片段查询意图，分流至知识库问答快通道",
            )

        return None

    def _route_with_model(self, text: str) -> RouterResult | None:
        if not self.chat:
            return None
        try:
            completion = self.chat.complete(
                system_prompt=ROUTER_SYSTEM_PROMPT,
                user_prompt=f"用户提问：{text}",
                max_output_tokens=256,
                temperature=0.1,
                json_object=True,
                producer_step_id="router-intent-classify",
            )
            raw = (completion.content or "").strip()
            data = json.loads(raw)
            mode = str(data.get("mode", "direct")).lower()
            confidence = float(data.get("confidence", 0.8))
            reason = str(data.get("reason", "模型意图分类结果"))

            valid_modes = {"direct", "rag", "deep", "document", "project", "memory"}
            if mode not in valid_modes:
                mode = "direct"

            is_fast = mode in {"direct", "rag", "memory"}
            return RouterResult(
                target_mode=mode,
                is_fast_path=is_fast,
                confidence=confidence,
                intent_summary=reason,
                suggested_run_kind="managed_verified_research" if mode == "deep" else None,
            )
        except Exception:
            return None
