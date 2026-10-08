"""Reflection and evidence gap auditing engine for long-horizon research."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Callable, Coroutine
import uuid

from conflux_weave.long_horizon_research.search_graph import DiscoveredSource, SearchGraphCrawler
from conflux_weave.long_horizon_research.spec import ResearchOutlineTree, ResearchProbe, SurveyChapterPlan
from conflux_weave.structured_output import safe_json_loads

logger = logging.getLogger(__name__)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class EvidenceGapFinding:
    """Identifies a chapter/probe where existing source evidence is thin or missing."""

    gap_id: str
    chapter_id: str
    probe_id: str
    aspect_description: str
    severity: str  # "high" | "medium" | "low"
    remedy_query: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EvidenceGapFinding:
        return cls(
            gap_id=str(data.get("gap_id") or f"gap-{uuid.uuid4().hex[:6]}"),
            chapter_id=str(data.get("chapter_id", "")),
            probe_id=str(data.get("probe_id", "")),
            aspect_description=str(data.get("aspect_description", "")),
            severity=str(data.get("severity", "medium")),
            remedy_query=str(data.get("remedy_query", "")),
        )


@dataclass(frozen=True)
class ContradictionFinding:
    """Captures a contradiction or controversy across sources that must be explicitly analyzed."""

    contradiction_id: str
    topic: str
    claim_a: str
    source_a_url: str
    claim_b: str
    source_b_url: str
    arbitration_guideline: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ContradictionFinding:
        return cls(
            contradiction_id=str(data.get("contradiction_id") or f"contra-{uuid.uuid4().hex[:6]}"),
            topic=str(data.get("topic", "")),
            claim_a=str(data.get("claim_a", "")),
            source_a_url=str(data.get("source_a_url", "")),
            claim_b=str(data.get("claim_b", "")),
            source_b_url=str(data.get("source_b_url", "")),
            arbitration_guideline=str(data.get("arbitration_guideline", "")),
        )


@dataclass(frozen=True)
class ReflectionAuditReport:
    """Full self-reflection audit assessment across all survey chapters."""

    audit_id: str
    gap_findings: tuple[EvidenceGapFinding, ...]
    contradiction_findings: tuple[ContradictionFinding, ...]
    executed_remedy_queries: tuple[str, ...]
    audit_passed: bool
    created_at: str = field(default_factory=_utc_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "audit_id": self.audit_id,
            "gap_findings": [g.to_dict() for g in self.gap_findings],
            "contradiction_findings": [c.to_dict() for c in self.contradiction_findings],
            "executed_remedy_queries": list(self.executed_remedy_queries),
            "audit_passed": self.audit_passed,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ReflectionAuditReport:
        gaps = tuple(EvidenceGapFinding.from_dict(g) for g in (data.get("gap_findings") or []))
        contras = tuple(ContradictionFinding.from_dict(c) for c in (data.get("contradiction_findings") or []))
        remedies = tuple(str(q) for q in (data.get("executed_remedy_queries") or []))
        return cls(
            audit_id=str(data.get("audit_id") or f"audit-{uuid.uuid4().hex[:8]}"),
            gap_findings=gaps,
            contradiction_findings=contras,
            executed_remedy_queries=remedies,
            audit_passed=bool(data.get("audit_passed", True)),
            created_at=str(data.get("created_at") or _utc_now()),
        )


AUDIT_SYSTEM_PROMPT = """你是一位严苛的学术综述特约审稿人（Peer Reviewer）。
你的职责是审查当前收集到的文献证据池是否能够充分支撑整篇综述大纲树，并识别证据缝隙与数据冲突。

【审查职责】：
1. 【证据贫瘠审计 (Evidence Starvation)】：逐章检查大纲中的各个探针。若某个论点探针缺乏充分的实证数据或权威文献支撑，必须标记为证据缝隙，并给出精准的定向补捞搜索词（remedy_query）。
2. 【学术争议与对立观点检测 (Contradiction Detection)】：识别不同文献之间是否存在指标差距、技术路线优劣评判分歧或测试基准口径不一致。给出中立裁决指引。

【返回 JSON 格式】：
{
  "gap_findings": [
    {
      "chapter_id": "ch-1",
      "probe_id": "probe-1-1",
      "aspect_description": "缺少针对该算法硬件能耗比的直接测试数据",
      "severity": "high",
      "remedy_query": "算法名 GPU power consumption energy efficiency measurement"
    }
  ],
  "contradiction_findings": [
    {
      "topic": "吞吐量加速比分歧",
      "claim_a": "论文A宣称取得 3.5 倍端到端加速",
      "source_a_url": "https://arxiv.org/abs/...",
      "claim_b": "工程报告B指出在真实生产并发下仅提升 1.2 倍",
      "source_b_url": "https://github.com/...",
      "arbitration_guideline": "在第四章对比节指出测试是在单Batch还是连续批处理条件下进行，分析差异根源"
    }
  ],
  "audit_passed": false
}
"""


class InformationGapAuditor:
    """Performs reflection auditing over outline chapters and triggers targeted follow-up crawls."""

    def __init__(self, chat_adapter: Any | None = None) -> None:
        self.chat_adapter = chat_adapter

    def audit_and_plan_remedy(
        self,
        outline: ResearchOutlineTree,
        sources: list[DiscoveredSource],
    ) -> tuple[tuple[EvidenceGapFinding, ...], tuple[ContradictionFinding, ...]]:
        """Audit the collected sources against the outline tree to detect gaps and contradictions."""
        if self.chat_adapter is None:
            return self._deterministic_audit(outline, sources)

        # Prepare digest of sources
        source_digest = "\n".join(
            f"[{s.source_id}] {s.title} ({s.url}) - {s.snippet[:200]}" for s in sources[:40]
        )
        outline_digest = "\n".join(
            f"[{c.chapter_id}] {c.title} -> " + ", ".join(f"{p.probe_id}:{p.target_aspect}" for p in c.probes)
            for c in outline.chapters
        )

        messages = [
            {"role": "system", "content": AUDIT_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"【待论证大纲章节与探针】：\n{outline_digest}\n\n"
                    f"【当前已采纳文献摘要库 (共 {len(sources)} 篇)】：\n{source_digest}\n\n"
                    f"请审查哪些探针存在证据空白需要补捞，是否存在关键数据冲突。"
                ),
            },
        ]

        try:
            raw_response = self._invoke_llm(messages)
            parsed = safe_json_loads(raw_response)
            if not isinstance(parsed, dict):
                return self._deterministic_audit(outline, sources)

            gaps = tuple(
                EvidenceGapFinding.from_dict(g)
                for g in (parsed.get("gap_findings") or [])
                if isinstance(g, dict) and g.get("remedy_query")
            )
            contras = tuple(
                ContradictionFinding.from_dict(c)
                for c in (parsed.get("contradiction_findings") or [])
                if isinstance(c, dict)
            )

            # If LLM found no gaps at all, run deterministic baseline sanity check
            if not gaps and len(sources) < outline.total_probes_count():
                return self._deterministic_audit(outline, sources)

            return gaps, contras
        except Exception as exc:  # noqa: BLE001
            logger.warning("LLM reflection audit failed: %s. Using deterministic auditor.", exc)
            return self._deterministic_audit(outline, sources)

    async def execute_reflection_loop(
        self,
        outline: ResearchOutlineTree,
        sources: list[DiscoveredSource],
        crawler: SearchGraphCrawler,
        *,
        max_remedy_queries: int = 6,
    ) -> tuple[list[DiscoveredSource], ReflectionAuditReport]:
        """Perform full reflection audit, execute targeted follow-up queries, and return enriched sources and report."""
        gaps, contras = self.audit_and_plan_remedy(outline, sources)

        executed_queries: list[str] = []
        if gaps:
            selected_remedies = [g.remedy_query for g in gaps[:max_remedy_queries] if g.remedy_query]
            if selected_remedies:
                probes = tuple(
                    ResearchProbe(
                        probe_id=f"remedy-{idx}",
                        target_aspect=f"补捞定向切面: {q}",
                        query_intent=q,
                        expected_data_type="quantitative_metric",
                    )
                    for idx, q in enumerate(selected_remedies, start=1)
                )
                logger.info("Executing %d targeted remedy queries for evidence gap closure...", len(probes))
                new_sources = await crawler.explore_probes(probes)
                # Deduplicate and merge
                existing_urls = {s.url for s in sources}
                for ns in new_sources:
                    if ns.url not in existing_urls:
                        existing_urls.add(ns.url)
                        sources.append(ns)
                executed_queries = selected_remedies

        report = ReflectionAuditReport(
            audit_id=f"audit-{uuid.uuid4().hex[:8]}",
            gap_findings=gaps,
            contradiction_findings=contras,
            executed_remedy_queries=tuple(executed_queries),
            audit_passed=len(gaps) == 0 or len(executed_queries) > 0,
            created_at=_utc_now(),
        )

        return sources, report

    def _invoke_llm(self, messages: list[dict[str, str]]) -> str:
        if hasattr(self.chat_adapter, "chat"):
            response = self.chat_adapter.chat(messages, temperature=0.2, max_tokens=2500)
            if hasattr(response, "content"):
                return str(response.content)
            return str(response)
        if callable(self.chat_adapter):
            return str(self.chat_adapter(messages))
        raise TypeError(f"Unsupported chat adapter type: {type(self.chat_adapter)}")

    def _deterministic_audit(
        self,
        outline: ResearchOutlineTree,
        sources: list[DiscoveredSource],
    ) -> tuple[tuple[EvidenceGapFinding, ...], tuple[ContradictionFinding, ...]]:
        """Heuristic-based evidence density auditing across chapters and keywords."""
        gaps: list[EvidenceGapFinding] = []
        contras: list[ContradictionFinding] = []

        all_text = " ".join(s.full_text + " " + s.title for s in sources).lower()

        # Audit probes
        for ch in outline.chapters:
            for p in ch.probes:
                keywords = [k for k in re.split(r"\s+", p.query_intent) if len(k) >= 2]
                match_count = sum(1 for kw in keywords if kw.lower() in all_text)
                # If keyword coverage is sparse
                if match_count < max(1, len(keywords) // 2):
                    gaps.append(
                        EvidenceGapFinding(
                            gap_id=f"gap-{uuid.uuid4().hex[:6]}",
                            chapter_id=ch.chapter_id,
                            probe_id=p.probe_id,
                            aspect_description=f"探针「{p.target_aspect}」在当前文献库中匹配词频不足",
                            severity="high" if ch.chapter_index <= 3 else "medium",
                            remedy_query=f"{outline.objective} {p.query_intent} benchmark evaluation",
                        )
                    )

        # Detect potential benchmark or speedup contradictions
        speedup_mentions = re.findall(r"(\d+(?:\.\d+)?)\s*(?:x|times|倍)\s*(?:speedup|faster|加速)", all_text)
        if len(set(speedup_mentions)) >= 2:
            s_min, s_max = min(float(s) for s in speedup_mentions), max(float(s) for s in speedup_mentions)
            if s_max - s_min >= 1.5 and sources:
                contras.append(
                    ContradictionFinding(
                        contradiction_id=f"contra-{uuid.uuid4().hex[:6]}",
                        topic="加速比宣称差异",
                        claim_a=f"文献中存在较高加速比声称 ({s_max}x)",
                        source_a_url=sources[0].url,
                        claim_b=f"其他文献报告较保守的加速比 ({s_min}x)",
                        source_b_url=sources[-1].url,
                        arbitration_guideline="在综述第四章中对比单卡 Batch=1 延迟加速与多卡吞吐量加速的测试口径区别。",
                    )
                )

        return tuple(gaps), tuple(contras)
