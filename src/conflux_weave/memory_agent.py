"""MemoryAgent for candidate extraction, conflict detection, and context injection (P4.1).

Responsibilities:
1. Analyzes user inputs and conversation turns to extract potential memory candidates;
2. Detects conflicts and deduplicates against active memories;
3. Formats active user, project, and session memories into a bounded System Prompt block (L0/L1 budget <= 400 tokens).
"""

from __future__ import annotations

import json
import re
from typing import Any

from conflux_weave.provider import OpenAICompatibleChatAdapter
from conflux_weave.runtime.memory_store import (
    CandidateStatus,
    HierarchicalMemoryStore,
    MemoryCandidate,
    MemoryCategory,
    MemoryItem,
    MemoryScope,
    MemoryStatus,
)


# Heuristic patterns for offline / deterministic candidate extraction
PREFERENCE_PATTERNS = [
    (re.compile(r"(?:以后|今后|每次|请)?\s*(?:都)?\s*(?:用|使用|采用)\s*([\u4e00-\u9fa5A-Za-z0-9_\-\s]{2,30}?)\s*(?:回答|输出|总结)", re.IGNORECASE), "语言与输出偏好：使用 {0} 回答"),
    (re.compile(r"(?:以后|请)?\s*(?:保持|风格要)?\s*(严谨|客观|精炼|详细|生动|学术|活泼|专业)", re.IGNORECASE), "文风偏好：保持{0}"),
    (re.compile(r"(?:不要|别|严禁)\s*(?:贴|展示|输出)?\s*(代码|伪代码|英文|套话|废话)", re.IGNORECASE), "输出限制：避免{0}"),
    (re.compile(r"(?:优先|务必|必须)\s*(?:给出|附带|标注)?\s*(代码行号|文献引用|来源页码|测试用例)", re.IGNORECASE), "交付规范：优先标注{0}"),
    (re.compile(r"我(?:的)?\s*(?:研究方向|研究领域|课题|主要关注)\s*(?:是|在)?\s*([\u4e00-\u9fa5A-Za-z0-9_\-\s]{2,30})", re.IGNORECASE), "研究领域偏好：关注{0}"),
]

PROJECT_CONVENTION_PATTERNS = [
    (re.compile(r"(?:本项目|工程|架构)\s*(?:统一)?\s*(?:采用|使用|基于)\s*([\u4e00-\u9fa5A-Za-z0-9_\-\s]{2,40})", re.IGNORECASE), "项目技术约定：采用 {0}"),
    (re.compile(r"(?:核心算法|核心算子|核心机制)\s*(?:是|为)\s*([\u4e00-\u9fa5A-Za-z0-9_\-\s]{2,40})", re.IGNORECASE), "核心算法决策：{0}"),
    (re.compile(r"(?:严禁|禁止|不能)\s*(?:引入|使用)\s*([\u4e00-\u9fa5A-Za-z0-9_\-\s]{2,40})", re.IGNORECASE), "架构规约：禁止引入 {0}"),
]


EXTRACTION_SYSTEM_PROMPT = """你是一个专业的学术工作台记忆提取助手。
你的任务是从用户给出的输入中，识别用户是否表达了【长期偏好】、【项目约定】或【客观事实】。

分类要求：
- scope: "user"（个人全局文风/习惯/偏好）或 "project"（项目技术栈/规范/决策）
- category: "preference"（偏好）, "fact"（事实）, "constraint"（规约/限制）, "decision"（架构决策）
- statement: 凝练成一句简明陈述，例如："文风偏好：严谨客观，给出代码行号"
- confidence: 0.0 到 1.0 的置信度数值

注意：
1. 仅当用户明确表达了持续有效的偏好或约定（例如"以后都用中文"、"本项目算法采用RRF"）时才提取；
2. 普通问答、一次性临时指令（例如"帮我查一下这篇论文"）不要提取任何记忆，返回空数组；
3. 输出格式必须为 JSON 数组：[{"scope": "user|project", "category": "preference|fact|constraint|decision", "statement": "...", "confidence": 0.9}]
"""


class MemoryAgent:
    """Agent responsible for memory extraction, staging, and context compilation."""

    def __init__(
        self,
        store: HierarchicalMemoryStore,
        chat_adapter: OpenAICompatibleChatAdapter | None = None,
    ) -> None:
        self.store = store
        self.chat = chat_adapter

    def extract_heuristics(
        self,
        user_input: str,
        *,
        user_id: str = "user_default",
        project_id: str | None = None,
        source_type: str = "conversation",
        source_id: str = "conv_current",
    ) -> list[MemoryCandidate]:
        """Extract memory candidates using fast deterministic regex heuristics (zero network)."""
        candidates: list[MemoryCandidate] = []
        text = user_input.strip()
        if not text or len(text) > 4000:
            return []

        # 1. User preferences
        for pattern, template in PREFERENCE_PATTERNS:
            match = pattern.search(text)
            if match:
                val = match.group(1) if match.groups() else ""
                stmt = template.format(val) if "{0}" in template else template
                dup_id, conflict_id = self.store.find_conflicts_and_duplicates(
                    MemoryScope.USER, user_id, MemoryCategory.PREFERENCE, stmt
                )
                if not dup_id:
                    cand = self.store.create_candidate(
                        scope=MemoryScope.USER,
                        target_id=user_id,
                        category=MemoryCategory.PREFERENCE,
                        statement=stmt,
                        confidence=0.9,
                        source_type=source_type,
                        source_id=source_id,
                        conflict_with_memory_id=conflict_id,
                    )
                    candidates.append(cand)

        # 2. Project conventions
        if project_id:
            for pattern, template in PROJECT_CONVENTION_PATTERNS:
                match = pattern.search(text)
                if match:
                    val = match.group(1) if match.groups() else ""
                    stmt = template.format(val) if "{0}" in template else template
                    dup_id, conflict_id = self.store.find_conflicts_and_duplicates(
                        MemoryScope.PROJECT, project_id, MemoryCategory.DECISION, stmt
                    )
                    if not dup_id:
                        cand = self.store.create_candidate(
                            scope=MemoryScope.PROJECT,
                            target_id=project_id,
                            category=MemoryCategory.DECISION,
                            statement=stmt,
                            confidence=0.85,
                            source_type=source_type,
                            source_id=source_id,
                            conflict_with_memory_id=conflict_id,
                        )
                        candidates.append(cand)

        return candidates

    def extract_with_model(
        self,
        user_input: str,
        *,
        user_id: str = "user_default",
        project_id: str | None = None,
        source_type: str = "conversation",
        source_id: str = "conv_current",
    ) -> list[MemoryCandidate]:
        """Use chat adapter to extract memory candidates via LLM completion."""
        if not self.chat:
            return self.extract_heuristics(
                user_input,
                user_id=user_id,
                project_id=project_id,
                source_type=source_type,
                source_id=source_id,
            )

        try:
            completion = self.chat.complete(
                system_prompt=EXTRACTION_SYSTEM_PROMPT,
                user_prompt=f"用户输入：{user_input}",
                max_output_tokens=512,
                temperature=0.1,
                json_object=True,
                producer_step_id="memory-agent-extract",
            )
            raw = (completion.content or "").strip()
            data = json.loads(raw)
            if isinstance(data, dict) and "candidates" in data:
                items = data["candidates"]
            elif isinstance(data, list):
                items = data
            else:
                items = []

            candidates: list[MemoryCandidate] = []
            for item in items:
                stmt = str(item.get("statement", "")).strip()
                if not stmt:
                    continue
                scope_str = str(item.get("scope", "user")).lower()
                scope = MemoryScope.PROJECT if scope_str == "project" and project_id else MemoryScope.USER
                target_id = project_id if scope == MemoryScope.PROJECT and project_id else user_id
                cat_str = str(item.get("category", "preference")).lower()
                try:
                    category = MemoryCategory(cat_str)
                except ValueError:
                    category = MemoryCategory.PREFERENCE

                conf = float(item.get("confidence", 0.85))
                dup_id, conflict_id = self.store.find_conflicts_and_duplicates(scope, target_id, category, stmt)
                if not dup_id:
                    cand = self.store.create_candidate(
                        scope=scope,
                        target_id=target_id,
                        category=category,
                        statement=stmt,
                        confidence=conf,
                        source_type=source_type,
                        source_id=source_id,
                        conflict_with_memory_id=conflict_id,
                    )
                    candidates.append(cand)
            return candidates
        except Exception:
            # Fallback gracefully to heuristics
            return self.extract_heuristics(
                user_input,
                user_id=user_id,
                project_id=project_id,
                source_type=source_type,
                source_id=source_id,
            )

    def extract_candidates(
        self,
        user_input: str,
        *,
        user_id: str = "user_default",
        project_id: str | None = None,
        source_type: str = "conversation",
        source_id: str = "conv_current",
    ) -> list[MemoryCandidate]:
        """Extract memory candidates using model when available, falling back to heuristics."""
        return self.extract_with_model(
            user_input,
            user_id=user_id,
            project_id=project_id,
            source_type=source_type,
            source_id=source_id,
        )

    def format_prompt_context(
        self,
        user_id: str = "user_default",
        project_id: str | None = None,
        conversation_id: str | None = None,
        max_chars: int = 1200,
    ) -> str:
        """Format active memories into a compact System Prompt injection block (L0/L1 budget <= 400 tokens)."""
        lines: list[str] = []

        # 1. User memories (global habits/preferences)
        user_mems = self.store.list_memories(scope=MemoryScope.USER, target_id=user_id, status=MemoryStatus.ACTIVE)
        if user_mems:
            lines.append("【用户偏好与习惯约定】")
            for item in user_mems[:5]:
                cat_label = {
                    MemoryCategory.PREFERENCE: "偏好",
                    MemoryCategory.FACT: "事实",
                    MemoryCategory.CONSTRAINT: "限制",
                    MemoryCategory.DECISION: "约定",
                }.get(item.category, "偏好")
                lines.append(f"- [{cat_label}] {item.statement}")

        # 2. Project conventions
        if project_id:
            proj_mems = self.store.list_memories(scope=MemoryScope.PROJECT, target_id=project_id, status=MemoryStatus.ACTIVE)
            if proj_mems:
                lines.append("【项目架构与技术约定】")
                for item in proj_mems[:5]:
                    lines.append(f"- [约定] {item.statement}")

        # 3. Session memory (ephemeral facts)
        if conversation_id:
            sess_mems = self.store.list_memories(scope=MemoryScope.SESSION, target_id=conversation_id, status=MemoryStatus.ACTIVE)
            if sess_mems:
                lines.append("【会话当前事实与约束】")
                for item in sess_mems[:3]:
                    lines.append(f"- [事实] {item.statement}")

        if not lines:
            return ""

        block = "\n".join(lines)
        if len(block) > max_chars:
            block = block[:max_chars].rsplit("\n", 1)[0] + "\n...(已截断超出预算的记忆)"

        return block
