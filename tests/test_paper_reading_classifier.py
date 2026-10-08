"""Unit tests for adaptive paper paradigm classifier and specialized outline generator."""

from __future__ import annotations

from unittest.mock import MagicMock

from conflux_weave.paper_reading.classifier import (
    build_adaptive_chapters,
    build_adaptive_reading_outline,
    classify_paradigm_heuristic,
)
from conflux_weave.paper_reading.spec import PaperParadigm, VisualAssetType


def test_classify_algorithm_architecture_heuristic() -> None:
    paradigm, conf, rationale = classify_paradigm_heuristic(
        title="Attention Is All You Need",
        abstract="We propose a new simple network architecture, the Transformer, based solely on attention mechanisms.",
        section_headings=["Introduction", "Background", "Model Architecture", "Training", "Results"],
        full_text_sample="The dominant sequence transduction models are based on complex recurrent or convolutional neural networks...",
    )
    assert paradigm == PaperParadigm.ALGORITHM_ARCHITECTURE
    assert conf >= 0.7
    assert "核心算法与系统架构" in rationale or "算法" in rationale


def test_classify_survey_taxonomy_heuristic() -> None:
    paradigm, conf, rationale = classify_paradigm_heuristic(
        title="A Comprehensive Survey on Large Language Models: Advances and Challenges",
        abstract="In this survey, we provide a systematic overview and categorize recent developments into a structured taxonomy.",
        section_headings=["Overview", "Taxonomy of Architectures", "Evolution Timeline", "Open Challenges"],
    )
    assert paradigm == PaperParadigm.SURVEY_TAXONOMY
    assert conf >= 0.8
    assert "综述" in rationale or "分类学" in rationale


def test_classify_benchmark_dataset_heuristic() -> None:
    paradigm, conf, rationale = classify_paradigm_heuristic(
        title="MMLU: Measuring Massive Multitask Language Understanding Benchmark",
        abstract="We introduce a new benchmark for evaluating models across 57 subjects with comprehensive leaderboard results.",
        section_headings=["Dataset Construction", "Annotation Protocol", "Evaluation Setup", "Leaderboard Results", "Error Analysis"],
    )
    assert paradigm == PaperParadigm.BENCHMARK_DATASET
    assert conf >= 0.8
    assert "评测" in rationale or "数据集" in rationale


def test_build_adaptive_chapters_specs() -> None:
    # Algorithm specs
    algo_chaps = build_adaptive_chapters(PaperParadigm.ALGORITHM_ARCHITECTURE)
    assert len(algo_chaps) == 5
    assert any(VisualAssetType.ARCHITECTURE_DIAGRAM in c.expected_visual_types for c in algo_chaps)
    assert any(VisualAssetType.PERFORMANCE_TABLE in c.expected_visual_types for c in algo_chaps)
    assert any(VisualAssetType.ABLATION_CHART in c.expected_visual_types for c in algo_chaps)

    # Survey specs
    survey_chaps = build_adaptive_chapters(PaperParadigm.SURVEY_TAXONOMY)
    assert len(survey_chaps) == 5
    assert "分类学" in survey_chaps[1].title or "Taxonomy" in survey_chaps[1].title

    # Benchmark specs
    bm_chaps = build_adaptive_chapters(PaperParadigm.BENCHMARK_DATASET)
    assert len(bm_chaps) == 5
    assert "天梯" in bm_chaps[3].title or "Leaderboard" in bm_chaps[3].title


def test_build_adaptive_reading_outline_with_mock_adapter() -> None:
    adapter = MagicMock()
    adapter.chat.return_value = {
        "content": '{"paradigm": "survey_taxonomy", "confidence": 0.95, "rationale": "LLM 判定为领域综述文献"}'
    }

    outline = build_adaptive_reading_outline(
        title="Recent Advances in Generative AI",
        abstract="This paper reviews recent generative models.",
        adapter=adapter,
    )
    assert outline.paradigm == PaperParadigm.SURVEY_TAXONOMY
    assert outline.confidence == 0.95
    assert len(outline.chapters) == 5


def test_build_adaptive_reading_outline_fallback_on_adapter_error() -> None:
    adapter = MagicMock()
    adapter.chat.side_effect = RuntimeError("Provider timeout")

    outline = build_adaptive_reading_outline(
        title="FlashAttention: Fast and Memory-Efficient Exact Attention with IO-Awareness",
        abstract="We introduce FlashAttention, an IO-aware exact attention algorithm.",
        adapter=adapter,
    )
    # Should safely fallback to heuristic algorithm architecture
    assert outline.paradigm == PaperParadigm.ALGORITHM_ARCHITECTURE
    assert outline.confidence > 0.6
    assert len(outline.chapters) == 5
