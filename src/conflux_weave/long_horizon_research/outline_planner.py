"""Outline and hypothesis planning engine for exhaustive literature surveys."""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Protocol

from conflux_weave.long_horizon_research.spec import (
    ResearchOutlineTree,
    ResearchProbe,
    SurveyChapterPlan,
    _utc_now,
)
from conflux_weave.structured_output import safe_json_loads

logger = logging.getLogger(__name__)


SURVEY_PLANNER_SYSTEM_PROMPT = """你是一位顶尖科研机构的资深学术主任与特约综述主编。
你的任务是将用户提供的深度研究主题（Objective）解构为一份极具深度、论证严密、无任何关键遗漏的【万字级学术文献综述与架构调研大纲树 (Research Outline Tree)】。

【设计原则与标准】：
1. 【章节深度】：大纲必须包含 5 到 7 个递进性章节，全面覆盖理论基石、演进沿革、主流流派、机制横向对比、实证基准评测、核心争议与未来路线。
2. 【论证探针】：每个章节必须下发 3 到 4 个具体的微观研究切面与实证核查探针（Research Probes），全篇探针总数不得少于 18 项。每个探针必须包含具体的实据检索意图与预期数据类型。
3. 【依赖拓扑】：各章节必须建立合理的逻辑依赖关系（depends_on 仅引用前序已立论的 chapter_id，严禁循环依赖）。
4. 【立论假设】：每个章节必须明确指出本章的核心学术/工程立论假设（core_thesis）。

【输出格式】：
严格返回 JSON 格式，不得包含外部解释或多余字段：
{
  "background_summary": "该调研主题的行业/学术背景概述...",
  "target_word_count_range": [8000, 15000],
  "chapters": [
    {
      "chapter_id": "ch-1",
      "chapter_index": 1,
      "title": "一、研究背景与核心问题界定",
      "core_thesis": "立论核心假设说明...",
      "depends_on": [],
      "probes": [
        {
          "probe_id": "probe-1-1",
          "target_aspect": "该领域最初产生的核心技术瓶颈与根本驱动力",
          "query_intent": "核心技术背景 痛点 驱动力",
          "expected_data_type": "historical_milestone"
        },
        ...
      ]
    },
    ...
  ]
}
"""


class ChatCompletionCallable(Protocol):
    def __call__(self, messages: list[dict[str, str]], **kwargs: Any) -> str: ...


def build_deterministic_outline(objective: str, background_summary: str = "") -> ResearchOutlineTree:
    """Build a deterministic, highly structured 6-chapter survey outline tree for offline/fallback execution."""
    clean_obj = objective.strip() or "深度技术调研与文献综述"
    summary = background_summary.strip() or f"围绕「{clean_obj}」的系统性技术综述与前沿研究脉络剖析。"

    chapters: list[SurveyChapterPlan] = [
        SurveyChapterPlan(
            chapter_id="ch-1",
            chapter_index=1,
            title="一、研究背景、核心概念与问题界定",
            core_thesis=f"清晰界定「{clean_obj}」的核心内涵、技术边界与根本驱动力，明确解决的关键技术瓶颈。",
            depends_on=(),
            probes=(
                ResearchProbe(
                    probe_id="probe-1-1",
                    target_aspect="核心概念定义、理论起源与解决的本质痛点",
                    query_intent=f"{clean_obj} definition problem formulation background",
                    expected_data_type="theoretical_foundation",
                ),
                ResearchProbe(
                    probe_id="probe-1-2",
                    target_aspect="传统技术路线的固有局限性与边际递减效应",
                    query_intent=f"{clean_obj} traditional approach limitations bottlenecks",
                    expected_data_type="architectural_tradeoff",
                ),
                ResearchProbe(
                    probe_id="probe-1-3",
                    target_aspect="近年催化该领域爆发的关键软硬件基础设施与应用需求",
                    query_intent=f"{clean_obj} hardware catalysts industry demand emergence",
                    expected_data_type="historical_milestone",
                ),
            ),
        ),
        SurveyChapterPlan(
            chapter_id="ch-2",
            chapter_index=2,
            title="二、理论基石与技术演化沿革（编年史脉络）",
            core_thesis="梳理从早期奠基工作到当前主流范式的演化里程碑，提取关键转折点与范式跃迁。",
            depends_on=("ch-1",),
            probes=(
                ResearchProbe(
                    probe_id="probe-2-1",
                    target_aspect="奠基性理论与早期原型系统的架构特征",
                    query_intent=f"{clean_obj} foundational seminal papers history milestones",
                    expected_data_type="historical_milestone",
                ),
                ResearchProbe(
                    probe_id="probe-2-2",
                    target_aspect="第一代到第二代技术范式迭代的质变与代表性突破",
                    query_intent=f"{clean_obj} paradigm shift generation evolution",
                    expected_data_type="architectural_tradeoff",
                ),
                ResearchProbe(
                    probe_id="probe-2-3",
                    target_aspect="领域发展过程中的典型试错尝试与被弃用分支的技术原因",
                    query_intent=f"{clean_obj} deprecated obsolete approaches lessons learned",
                    expected_data_type="architectural_tradeoff",
                ),
            ),
        ),
        SurveyChapterPlan(
            chapter_id="ch-3",
            chapter_index=3,
            title="三、核心架构设计与主流技术流派分类学",
            core_thesis="建立系统化的多维度分类学（Taxonomy），对当前学术界与工业界的主流技术路线进行解构与归类。",
            depends_on=("ch-1", "ch-2"),
            probes=(
                ResearchProbe(
                    probe_id="probe-3-1",
                    target_aspect="主流代表性架构一的实现机制与拓扑结构剖析",
                    query_intent=f"{clean_obj} state of the art architecture taxonomy stream A",
                    expected_data_type="architectural_tradeoff",
                ),
                ResearchProbe(
                    probe_id="probe-3-2",
                    target_aspect="主流代表性架构二的技术路线与设计取向差异",
                    query_intent=f"{clean_obj} competing architecture methodology stream B",
                    expected_data_type="architectural_tradeoff",
                ),
                ResearchProbe(
                    probe_id="probe-3-3",
                    target_aspect="混合型/新兴演进架构的设计原则与跨模态/跨系统融合",
                    query_intent=f"{clean_obj} hybrid unified framework architecture",
                    expected_data_type="architectural_tradeoff",
                ),
                ResearchProbe(
                    probe_id="probe-3-4",
                    target_aspect="分类学维度对齐：算法复杂度、显存/计算消耗与系统假设",
                    query_intent=f"{clean_obj} taxonomy dimensions complexity assumptions",
                    expected_data_type="quantitative_metric",
                ),
            ),
        ),
        SurveyChapterPlan(
            chapter_id="ch-4",
            chapter_index=4,
            title="四、关键实现机制剖析与核心权衡横向对比",
            core_thesis="微观深入各关键子模块算法，横向对比精度、延迟、吞吐与复杂度的物理权衡（Trade-offs）。",
            depends_on=("ch-3",),
            probes=(
                ResearchProbe(
                    probe_id="probe-4-1",
                    target_aspect="核心算法步骤与优化原语的数学/逻辑推导",
                    query_intent=f"{clean_obj} mathematical formulation algorithm optimization",
                    expected_data_type="theoretical_foundation",
                ),
                ResearchProbe(
                    probe_id="probe-4-2",
                    target_aspect="内存与并发访问热点：I/O瓶颈、通信开销与缓存设计",
                    query_intent=f"{clean_obj} memory bottleneck communication overhead cache",
                    expected_data_type="quantitative_metric",
                ),
                ResearchProbe(
                    probe_id="probe-4-3",
                    target_aspect="在极值负载与边界条件下的系统稳定性与退化模式",
                    query_intent=f"{clean_obj} edge cases degradation stability failure modes",
                    expected_data_type="architectural_tradeoff",
                ),
            ),
        ),
        SurveyChapterPlan(
            chapter_id="ch-5",
            chapter_index=5,
            title="五、实证基准评测、消融实验与工业界落地",
            core_thesis="汇总权威学术基准（Benchmarks）评测结果与消融数据，剖析工业级大规模部署的真实表现与代价。",
            depends_on=("ch-3", "ch-4"),
            probes=(
                ResearchProbe(
                    probe_id="probe-5-1",
                    target_aspect="权威公共 Benchmark 测试集上的横向客观度量指标对比（SOTA Matrix）",
                    query_intent=f"{clean_obj} benchmark results SOTA comparison accuracy score",
                    expected_data_type="quantitative_metric",
                ),
                ResearchProbe(
                    probe_id="probe-5-2",
                    target_aspect="关键组件的消融实验（Ablation Studies）结果与各模块收益归因",
                    query_intent=f"{clean_obj} ablation study component contribution gains",
                    expected_data_type="quantitative_metric",
                ),
                ResearchProbe(
                    probe_id="probe-5-3",
                    target_aspect="工业界生产环境落地成本、硬件门槛与维护复杂度",
                    query_intent=f"{clean_obj} production deployment cost hardware requirements",
                    expected_data_type="architectural_tradeoff",
                ),
                ResearchProbe(
                    probe_id="probe-5-4",
                    target_aspect="典型实际应用场景下的踩坑记录与工程调优经验",
                    query_intent=f"{clean_obj} real world deployment pitfalls best practices",
                    expected_data_type="architectural_tradeoff",
                ),
            ),
        ),
        SurveyChapterPlan(
            chapter_id="ch-6",
            chapter_index=6,
            title="六、学术争议焦点、未决挑战与未来演进路线",
            core_thesis="批判性审视当前技术路线的固有限制、社区分歧焦点，并研判下一阶段最具潜力的突破方向。",
            depends_on=("ch-4", "ch-5"),
            probes=(
                ResearchProbe(
                    probe_id="probe-6-1",
                    target_aspect="当前学界与业界针对核心指标/安全性的主要争议与对立观点",
                    query_intent=f"{clean_obj} controversies debates opposing viewpoints limitations",
                    expected_data_type="architectural_tradeoff",
                ),
                ResearchProbe(
                    probe_id="probe-6-2",
                    target_aspect="现有理论框架尚无法完美解释或解决的开放性理论/工程挑战",
                    query_intent=f"{clean_obj} open problems unresolved challenges theoretical gaps",
                    expected_data_type="theoretical_foundation",
                ),
                ResearchProbe(
                    probe_id="probe-6-3",
                    target_aspect="未来 3 到 5 年内最值得关注的新兴技术结合点与演进路线研判",
                    query_intent=f"{clean_obj} future directions emerging paradigms roadmap 2026",
                    expected_data_type="historical_milestone",
                ),
                ResearchProbe(
                    probe_id="probe-6-4",
                    target_aspect="面向研究者与工程师的选题/选型落地综合建议与行动路线图",
                    query_intent=f"{clean_obj} actionable recommendations conclusion synthesis",
                    expected_data_type="architectural_tradeoff",
                ),
            ),
        ),
    ]

    return ResearchOutlineTree(
        outline_id=f"outline-{uuid.uuid4().hex[:8]}",
        objective=clean_obj,
        background_summary=summary,
        target_word_count_range=(8000, 15000),
        chapters=tuple(chapters),
        created_at=_utc_now(),
    )


class OutlinePlanner:
    """Plans comprehensive, multi-chapter research outline trees using LLM with deterministic fallback."""

    def __init__(self, chat_adapter: Any | None = None) -> None:
        self.chat_adapter = chat_adapter

    def plan_outline(
        self,
        objective: str,
        *,
        background_context: str = "",
        min_chapters: int = 5,
        max_chapters: int = 7,
    ) -> ResearchOutlineTree:
        """Decompose a complex research objective into an outline tree with deep probes."""
        clean_obj = objective.strip()
        if not clean_obj:
            raise ValueError("Objective cannot be empty for outline planning.")

        if self.chat_adapter is None:
            logger.info("No chat adapter provided; using deterministic outline planner.")
            return build_deterministic_outline(clean_obj, background_context)

        messages = [
            {"role": "system", "content": SURVEY_PLANNER_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"【待综述研究主题】：\n{clean_obj}\n\n"
                    f"【已知背景/特定关注重点】：\n{background_context or '无特定限制，全面展开。'}\n\n"
                    f"请生成包含 {min_chapters} 到 {max_chapters} 个章节、且全篇包含不少于 18 项实据探针的完整 JSON 大纲树。"
                ),
            },
        ]

        try:
            raw_response = self._invoke_llm(messages)
            parsed = safe_json_loads(raw_response)
            if not isinstance(parsed, dict) or "chapters" not in parsed:
                logger.warning("LLM response did not contain valid chapters; falling back to deterministic.")
                return build_deterministic_outline(clean_obj, background_context)

            outline = self._sanitize_and_construct(clean_obj, background_context, parsed)
            # Enforce minimum chapter threshold
            if len(outline.chapters) < min_chapters or outline.total_probes_count() < 12:
                logger.warning("LLM produced an outline that was too sparse; falling back to deterministic.")
                return build_deterministic_outline(clean_obj, background_context)

            return outline
        except Exception as exc:  # noqa: BLE001
            logger.warning("Error invoking LLM for outline planning: %s. Falling back to deterministic.", exc)
            return build_deterministic_outline(clean_obj, background_context)

    def _invoke_llm(self, messages: list[dict[str, str]]) -> str:
        """Invoke chat adapter handling both callable and OpenAICompatibleChatAdapter signatures."""
        if hasattr(self.chat_adapter, "chat"):
            response = self.chat_adapter.chat(messages, temperature=0.2, max_tokens=4000)
            if hasattr(response, "content"):
                return str(response.content)
            return str(response)
        if callable(self.chat_adapter):
            return str(self.chat_adapter(messages))
        raise TypeError(f"Unsupported chat adapter type: {type(self.chat_adapter)}")

    def _sanitize_and_construct(
        self, objective: str, background_summary: str, parsed: dict[str, Any]
    ) -> ResearchOutlineTree:
        """Sanitize raw parsed dict, resolve ID conflicts, and guarantee DAG validity."""
        raw_chapters = parsed.get("chapters") or []
        chapters: list[SurveyChapterPlan] = []
        seen_chapter_ids: set[str] = set()

        for idx, rc in enumerate(raw_chapters, start=1):
            if not isinstance(rc, dict):
                continue
            raw_cid = str(rc.get("chapter_id") or f"ch-{idx}").strip()
            cid = raw_cid if raw_cid not in seen_chapter_ids else f"ch-{idx}-{uuid.uuid4().hex[:4]}"
            seen_chapter_ids.add(cid)

            title = str(rc.get("title") or f"第{idx}章、未命名章节").strip()
            core_thesis = str(rc.get("core_thesis") or "本章深入探讨该维度机制。").strip()

            # Sanitize probes
            raw_probes = rc.get("probes") or []
            probes: list[ResearchProbe] = []
            for p_idx, rp in enumerate(raw_probes, start=1):
                if not isinstance(rp, dict):
                    continue
                pid = str(rp.get("probe_id") or f"probe-{idx}-{p_idx}")
                aspect = str(rp.get("target_aspect") or f"切面 {p_idx}").strip()
                intent = str(rp.get("query_intent") or f"{title} {aspect}").strip()
                dtype = str(rp.get("expected_data_type") or "architectural_tradeoff").strip()
                probes.append(
                    ResearchProbe(
                        probe_id=pid,
                        target_aspect=aspect,
                        query_intent=intent,
                        expected_data_type=dtype,
                    )
                )

            # Sanitize depends_on: only allowed to reference already seen chapter_ids to strictly guarantee a DAG
            raw_deps = rc.get("depends_on") or []
            valid_deps = tuple(str(d) for d in raw_deps if str(d) in seen_chapter_ids and str(d) != cid)

            chapters.append(
                SurveyChapterPlan(
                    chapter_id=cid,
                    chapter_index=idx,
                    title=title,
                    core_thesis=core_thesis,
                    probes=tuple(probes),
                    depends_on=valid_deps,
                )
            )

        bg = str(parsed.get("background_summary") or background_summary).strip()
        twc_raw = parsed.get("target_word_count_range") or [8000, 15000]
        twc = (int(twc_raw[0]), int(twc_raw[1]))

        outline = ResearchOutlineTree(
            outline_id=f"outline-{uuid.uuid4().hex[:8]}",
            objective=objective,
            background_summary=bg,
            target_word_count_range=twc,
            chapters=tuple(chapters),
            created_at=_utc_now(),
        )

        return outline
