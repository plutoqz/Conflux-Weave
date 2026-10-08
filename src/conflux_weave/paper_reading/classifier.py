"""Adaptive Paper Paradigm Classifier and Custom Outline Generator."""

from __future__ import annotations

import json
import re
from typing import Any

from conflux_weave.paper_reading.spec import (
    AdaptiveReadingOutline,
    PaperParadigm,
    ReadingChapterSpec,
    VisualAssetType,
)
from conflux_weave.provider import OpenAICompatibleChatAdapter


SURVEY_PATTERNS = [
    r"\b(?:survey|review|systematic review|taxonomy|advances in|an overview of|a comprehensive survey|a comprehensive review)\b",
]

BENCHMARK_PATTERNS = [
    r"\b(?:benchmark|benchmarking|dataset|datasets|evaluating|evaluation of|leaderboard|measuring|suite|data collection)\b",
]

ALGORITHM_PATTERNS = [
    r"\b(?:architecture|algorithm|framework|model|neural|network|attention|transformer|diffusion|mamba|loss|optimizer|learning|representation)\b",
]


def _match_patterns(text: str, patterns: list[str]) -> int:
    score = 0
    for p in patterns:
        matches = re.findall(p, text, re.IGNORECASE)
        score += len(matches)
    return score


def classify_paradigm_heuristic(
    title: str,
    abstract: str = "",
    section_headings: tuple[str, ...] | list[str] = (),
    full_text_sample: str = "",
) -> tuple[PaperParadigm, float, str]:
    """Classify paper paradigm using high-confidence multi-facet heuristics."""
    title_norm = title.lower()
    abstract_norm = abstract.lower()
    headings_norm = " ".join(s.lower() for s in section_headings)
    sample_norm = full_text_sample[:4000].lower()

    survey_score = 0
    benchmark_score = 0
    algo_score = 0

    # 1. Title analysis (highest priority)
    if _match_patterns(title_norm, SURVEY_PATTERNS) > 0:
        survey_score += 15
    if _match_patterns(title_norm, BENCHMARK_PATTERNS) > 0:
        benchmark_score += 15
    if _match_patterns(title_norm, ALGORITHM_PATTERNS) > 0:
        algo_score += 6

    # 2. Section headings analysis
    survey_headings = [
        "taxonomy", "categorization", "evolution", "history", "timeline",
        "open challenges", "future directions", "applications and frontiers",
        "taxonomic", "classification"
    ]
    for sh in survey_headings:
        if sh in headings_norm:
            survey_score += 5

    benchmark_headings = [
        "data collection", "dataset construction", "annotation protocol",
        "data quality", "benchmark setup", "evaluation protocol", "metrics",
        "baseline models", "human evaluation", "leaderboard", "failure cases",
        "dataset statistics", "error analysis"
    ]
    for bh in benchmark_headings:
        if bh in headings_norm:
            benchmark_score += 5

    algo_headings = [
        "architecture", "method", "methodology", "proposed method", "model architecture",
        "formulation", "algorithm", "training setup", "ablation study", "ablations",
        "implementation details"
    ]
    for ah in algo_headings:
        if ah in headings_norm:
            algo_score += 5

    # 3. Abstract and text sample matches
    survey_score += _match_patterns(abstract_norm, [r"\b(?:we categorize|we provide a comprehensive|taxonom|systematic overview|in this survey|in this review)\b"]) * 6
    benchmark_score += _match_patterns(abstract_norm, [r"\b(?:we introduce a new benchmark|we construct a dataset|we curate|evaluation suite|we release the dataset)\b"]) * 6
    algo_score += _match_patterns(abstract_norm, [r"\b(?:we propose|we introduce a novel architecture|our method|we present a new framework|outperforms previous sota)\b"]) * 4

    combined_scores = {
        PaperParadigm.SURVEY_TAXONOMY: survey_score,
        PaperParadigm.BENCHMARK_DATASET: benchmark_score,
        PaperParadigm.ALGORITHM_ARCHITECTURE: algo_score,
    }

    best_paradigm = max(combined_scores, key=lambda k: combined_scores[k])
    best_score = combined_scores[best_paradigm]

    # If scores are close or zero, fallback to ALGORITHM_ARCHITECTURE with default confidence
    if best_score < 4:
        return (
            PaperParadigm.ALGORITHM_ARCHITECTURE,
            0.75,
            "标题与章节特征未呈现显著综述或基准测试偏置，自适应收敛至核心算法/系统架构类范式。",
        )

    total_score = max(1, sum(combined_scores.values()))
    confidence = min(0.99, max(0.65, best_score / total_score + 0.35))

    rationales = {
        PaperParadigm.SURVEY_TAXONOMY: f"论文标题与章节显著命中综述/分类学特征（特征分: {survey_score}），重点包含流派梳理、分类学拓扑与演化分析。",
        PaperParadigm.BENCHMARK_DATASET: f"论文标题与章节显著命中评测/数据集构建特征（特征分: {benchmark_score}），重点包含数据收集清洗、基准协议与天梯排行榜。",
        PaperParadigm.ALGORITHM_ARCHITECTURE: f"论文显著命中核心算法与系统架构特征（特征分: {algo_score}），重点聚焦方法形式化、架构拓扑与消融归因。",
    }

    return best_paradigm, confidence, rationales[best_paradigm]


def build_adaptive_chapters(paradigm: PaperParadigm) -> tuple[ReadingChapterSpec, ...]:
    """Generate specialized reading chapters tailored to the specific paradigm."""
    if paradigm == PaperParadigm.ALGORITHM_ARCHITECTURE:
        return (
            ReadingChapterSpec(
                key="core_painpoints_and_formulation",
                title="一、核心痛点与问题形式化 (Problem Formulation & Painpoints)",
                target_aspect="传统技术路线的理论瓶颈、计算/显存约束以及本文严格的数学/系统形式化定义",
                expected_visual_types=(),
            ),
            ReadingChapterSpec(
                key="theoretical_foundations_and_hypotheses",
                title="二、理论基石与系统先验假设 (Theoretical Foundations & Hypotheses)",
                target_aspect="算法成立的前提假设、数学推导证明、归纳偏置与理论上界分析",
                expected_visual_types=(),
            ),
            ReadingChapterSpec(
                key="core_architecture_and_algorithmic_flow",
                title="三、核心架构拓扑与计算流程 (Core Architecture & Algorithmic Workflow)",
                target_aspect="端到端网络架构、模块拓扑连接、张量计算流转、核心算法伪代码剖析",
                expected_visual_types=(VisualAssetType.ARCHITECTURE_DIAGRAM,),
            ),
            ReadingChapterSpec(
                key="main_benchmarks_and_ablation_attribution",
                title="四、主实验基准评测与消融归因 (Main Benchmarks & Ablation Attribution)",
                target_aspect="主流权威评测基准成绩、关键模块消融实验量化收益与超参数敏感性归因",
                expected_visual_types=(VisualAssetType.PERFORMANCE_TABLE, VisualAssetType.ABLATION_CHART),
            ),
            ReadingChapterSpec(
                key="reproducibility_engineering_tradeoffs",
                title="五、复现风险、工程边界与落地权衡 (Engineering Boundaries & Trade-offs)",
                target_aspect="硬件算力与显存峰值开销、数值稳定性、推理吞吐延迟及工业化复现陷阱",
                expected_visual_types=(),
            ),
        )
    elif paradigm == PaperParadigm.SURVEY_TAXONOMY:
        return (
            ReadingChapterSpec(
                key="domain_landscape_and_evolution_drivers",
                title="一、领域全景背景与演进驱动力 (Domain Landscape & Evolution Drivers)",
                target_aspect="技术演进历史脉络、驱动范式迁移的底层计算/数据/应用矛盾与突破节点",
                expected_visual_types=(),
            ),
            ReadingChapterSpec(
                key="multidimensional_taxonomy_tree",
                title="二、多维体系化分类学演化树 (Taxonomy Tree & Categorization)",
                target_aspect="领域技术路线的层级分类准则、体系树结构划分及核心设计理念映射",
                expected_visual_types=(VisualAssetType.ARCHITECTURE_DIAGRAM,),
            ),
            ReadingChapterSpec(
                key="horizontal_dissection_of_competing_schools",
                title="三、主流技术流派横向机制解构 (Horizontal Dissection of Competing Schools)",
                target_aspect="不同流派代表性方案的核心原理、优劣势对比、算力开销及场景适配性",
                expected_visual_types=(VisualAssetType.PERFORMANCE_TABLE,),
            ),
            ReadingChapterSpec(
                key="tensions_bottlenecks_and_breakthroughs",
                title="四、核心技术矛盾与演进突破点 (Tensions, Bottlenecks & Breakthroughs)",
                target_aspect="效率与表现、泛化与过拟合等核心技术博弈，以及近期的关键突破路径",
                expected_visual_types=(VisualAssetType.ABLATION_CHART,),
            ),
            ReadingChapterSpec(
                key="open_challenges_and_future_frontiers",
                title="五、开放性挑战、未决难题与未来方向 (Open Challenges & Future Frontiers)",
                target_aspect="当前理论盲区、工程落地阻力以及未来 3~5 年最具潜力的技术攻坚方向",
                expected_visual_types=(),
            ),
        )
    else:  # BENCHMARK_DATASET
        return (
            ReadingChapterSpec(
                key="evaluation_blindspots_and_new_objectives",
                title="一、评测盲区剖析与新度量目标 (Evaluation Blindspots & New Objectives)",
                target_aspect="现有评测基准的数据饱和、作弊泄漏或盲区问题，本文设立新基准的核心诉求",
                expected_visual_types=(),
            ),
            ReadingChapterSpec(
                key="data_construction_and_quality_pipeline",
                title="二、数据构建清洗管线与质量严谨性 (Data Construction & Quality Pipeline)",
                target_aspect="数据源选取、采样协议、标注流水线、质检校验与防泄漏对抗过滤机制",
                expected_visual_types=(VisualAssetType.ARCHITECTURE_DIAGRAM,),
            ),
            ReadingChapterSpec(
                key="benchmark_protocol_and_metric_rigor",
                title="三、评测协议、基准任务与度量体系 (Protocol, Benchmark Tasks & Metric Rigor)",
                target_aspect="评测输入输出规范、Prompt 模板、难度分级、评分度量指标与人机一致性验证",
                expected_visual_types=(),
            ),
            ReadingChapterSpec(
                key="main_leaderboard_and_empirical_ranking",
                title="四、主流模型全景天梯榜与性能分析 (Main Leaderboard & Empirical Ranking)",
                target_aspect="主流代表性模型的评测排名、细分任务表现断层、开源与闭源方案横向比拼",
                expected_visual_types=(VisualAssetType.PERFORMANCE_TABLE,),
            ),
            ReadingChapterSpec(
                key="failure_diagnosis_and_systemic_vulnerabilities",
                title="五、典型失败案例诊断与系统脆弱性 (Failure Mode Diagnosis & Vulnerabilities)",
                target_aspect="复杂/失败用例错误归因、模型幻觉与推理脆弱性、基准局限性与改进空间",
                expected_visual_types=(VisualAssetType.ABLATION_CHART,),
            ),
        )


def build_adaptive_reading_outline(
    title: str,
    abstract: str = "",
    section_headings: tuple[str, ...] | list[str] = (),
    full_text_sample: str = "",
    adapter: OpenAICompatibleChatAdapter | None = None,
) -> AdaptiveReadingOutline:
    """Classify paper paradigm and construct adaptive reading outline with fallback."""
    # Step 1: Heuristic classification
    h_paradigm, h_confidence, h_rationale = classify_paradigm_heuristic(
        title=title,
        abstract=abstract,
        section_headings=section_headings,
        full_text_sample=full_text_sample,
    )

    final_paradigm = h_paradigm
    final_confidence = h_confidence
    final_rationale = h_rationale

    # Step 2: LLM refinement if available
    if adapter is not None:
        try:
            headings_snippet = ", ".join(section_headings[:15]) if section_headings else "无"
            prompt = (
                "你是一个学术论文范式分类专家。请根据以下论文信息，判断其所属的学术研究范式类别：\n"
                "可选范式仅限以下三种：\n"
                "1. algorithm_architecture（算法/架构类：提出新的神经网络结构、优化算法、数学建模、系统架构等）\n"
                "2. survey_taxonomy（综述/分类学类：梳理领域发展、总结分类学演化树、横向对比各流派等）\n"
                "3. benchmark_dataset（评测/数据集类：构建新数据集、设立评估基准套件、发布评测排行榜等）\n\n"
                f"论文标题: {title}\n"
                f"章节列表: {headings_snippet}\n"
                f"论文摘要: {abstract[:1200]}\n\n"
                "请严格输出 JSON 格式，包含以下字段：\n"
                '{"paradigm": "algorithm_architecture" | "survey_taxonomy" | "benchmark_dataset", '
                '"confidence": 0.0到1.0的浮点数, "rationale": "一句简短判断依据"}'
            )
            resp = adapter.chat([{"role": "user", "content": prompt}], temperature=0.1)
            raw = resp.get("content", "").strip()
            # Strip markdown fence
            if raw.startswith("```"):
                lines = raw.splitlines()
                if lines[0].startswith("```"):
                    lines = lines[1:]
                if lines and lines[-1].startswith("```"):
                    lines = lines[:-1]
                raw = "\n".join(lines).strip()
            parsed = json.loads(raw)
            candidate_p = parsed.get("paradigm", "").strip().lower()
            if candidate_p in {p.value for p in PaperParadigm}:
                final_paradigm = PaperParadigm(candidate_p)
                final_confidence = float(parsed.get("confidence", 0.9))
                final_rationale = parsed.get("rationale", h_rationale)
        except Exception:
            # Safe fallback to heuristic
            pass

    chapters = build_adaptive_chapters(final_paradigm)
    return AdaptiveReadingOutline(
        paradigm=final_paradigm,
        confidence=final_confidence,
        rationale=final_rationale,
        chapters=chapters,
    )
