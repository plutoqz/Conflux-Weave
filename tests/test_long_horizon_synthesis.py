"""Unit tests for Gate 1.4: Long-form survey synthesis, timeline, and comparison matrix."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

from conflux_weave.long_horizon_research import (
    ComprehensiveSurveyReport,
    DiscoveredSource,
    ResearchOutlineTree,
    SurveyReportSection,
    SurveySynthesizer,
    build_deterministic_outline,
)


def test_deterministic_chapter_and_full_survey_synthesis():
    outline = build_deterministic_outline("大模型自适应推理调度机制综述")
    sources = [
        DiscoveredSource(
            source_id="src-1",
            url="https://arxiv.org/abs/2401.0001",
            title="Adaptive Speculative Decoding",
            snippet="Overview of speedups",
            full_text="Evaluation on A100 benchmarks showing 2.8x speedup.",
            authority_score=0.92,
            hop_depth=0,
        ),
        DiscoveredSource(
            source_id="src-2",
            url="https://github.com/vllm/vllm",
            title="vLLM PagedAttention",
            snippet="Memory optimization",
            full_text="Continuous batching system trade-offs and latency.",
            authority_score=0.88,
            hop_depth=0,
        ),
    ]

    synthesizer = SurveySynthesizer(chat_adapter=None)

    # 1. Test chapter synthesis
    ch1 = outline.chapters[0]
    section1 = synthesizer.synthesize_chapter(ch1, outline, sources, prior_chapter_summaries=[])
    assert isinstance(section1, SurveyReportSection)
    assert section1.chapter_id == ch1.chapter_id
    assert section1.word_count > 400
    assert "立论背景" in section1.content_markdown
    assert len(section1.cited_source_urls) > 0

    # 2. Test executive summary, timeline, matrix
    takeaways = synthesizer.synthesize_executive_summary(outline, [section1])
    assert len(takeaways) >= 3

    timeline = synthesizer.render_evolution_timeline(outline)
    assert "timeline" in timeline
    assert "技术演进谱系" in timeline

    matrix = synthesizer.render_comparison_matrix(outline)
    assert "| 技术流派" in matrix
    assert "|---|---|" in matrix

    # 3. Assemble and test Master Report
    report = ComprehensiveSurveyReport(
        report_id="test-rep-1",
        objective=outline.objective,
        executive_summary=takeaways,
        sections=(section1,),
        evolution_timeline_mermaid=timeline,
        comparison_matrix_markdown=matrix,
        total_word_count=section1.word_count,
        total_sources_cited=len(sources),
        citations_ledger=(
            {"title": s.title, "url": s.url, "source_channel": s.source_channel}
            for s in sources
        ),
    )

    full_md = report.render_full_markdown()
    assert "# 大模型自适应推理调度机制综述" in full_md
    assert "## 核心执行要点" in full_md
    assert "## 技术沿革谱系图" in full_md
    assert "## 主流技术流派全景横向对比矩阵" in full_md
    assert "## 参考文献与可溯源证据台账" in full_md
    assert "https://arxiv.org/abs/2401.0001" in full_md

    # Test serialization roundtrip
    data = report.to_dict()
    restored = ComprehensiveSurveyReport.from_dict(data)
    assert restored.report_id == report.report_id
    assert len(restored.sections) == 1
    assert restored.total_word_count == report.total_word_count


def test_llm_chapter_synthesis_with_mock():
    mock_adapter = MagicMock()
    mock_adapter.chat.return_value = (
        "### 1.1 核心理论机制展开\n\n"
        "本节深入探讨自适应量化算法的本质约束。根据 [Adaptive Speculative Decoding](https://arxiv.org/abs/2401.0001)，"
        "在多卡连续批处理负载下，显存墙瓶颈成为限制大模型长上下文扩展的主要因素。\n\n"
        "实验结果表明，通过动态精度调度能够将端到端延迟显著降低 40% 以上。"
    )

    synthesizer = SurveySynthesizer(chat_adapter=mock_adapter)
    outline = build_deterministic_outline("大模型自适应推理调度机制综述")
    ch = outline.chapters[0]
    sources = [
        DiscoveredSource(
            source_id="src-1",
            url="https://arxiv.org/abs/2401.0001",
            title="Adaptive Speculative Decoding",
            snippet="Overview",
            full_text="Full text content",
            authority_score=0.9,
            hop_depth=0,
        )
    ]

    sec = synthesizer.synthesize_chapter(ch, outline, sources, prior_chapter_summaries=[])
    assert sec.chapter_id == ch.chapter_id
    assert "https://arxiv.org/abs/2401.0001" in sec.content_markdown
    assert "https://arxiv.org/abs/2401.0001" in sec.cited_source_urls
