"""Unit tests for cross-paper comparative matrix and evolutionary lineage generator."""

from __future__ import annotations

from conflux_weave.paper_reading.cross_paper_matrix import (
    build_lineage_mermaid_timeline,
    build_markdown_comparison_table,
    build_synthesis_narrative,
    extract_comparison_entity,
    generate_cross_paper_matrix,
)
from conflux_weave.paper_reading.spec import PaperComparisonEntity, PaperParadigm


def test_extract_comparison_entity() -> None:
    entity = extract_comparison_entity(
        paper_id="p1",
        title="Attention Is All You Need",
        raw_text="The Transformer is based solely on attention mechanisms...",
        year=2017,
    )
    assert entity.paper_id == "p1"
    assert entity.title == "Attention Is All You Need"
    assert entity.year == 2017
    assert entity.paradigm == PaperParadigm.ALGORITHM_ARCHITECTURE
    assert "RNN" in entity.core_problem or "序列" in entity.core_problem
    assert "点积" in entity.key_architecture_mechanism or "注意力" in entity.key_architecture_mechanism


def test_extract_comparison_entity_evidence_fidelity() -> None:
    # Arbitrary paper containing attention in title without benchmark text must NOT fabricate 28.4 BLEU
    arbitrary = extract_comparison_entity(
        paper_id="p_arb",
        title="Visual Attention in Autonomous Driving",
        raw_text="This paper investigates visual attention for vehicle control.",
    )
    assert "28.4 BLEU" not in arbitrary.main_benchmark_metrics
    assert "WMT 2014" not in arbitrary.main_benchmark_metrics

    # Paper with real metric in text should extract that metric
    with_metric = extract_comparison_entity(
        paper_id="p_metric",
        title="Novel Model",
        raw_text="Our method achieves 94.5% accuracy on ImageNet benchmarks.",
    )
    assert "94.5%" in with_metric.main_benchmark_metrics or "ImageNet" in with_metric.main_benchmark_metrics


def test_build_markdown_comparison_table() -> None:
    e1 = extract_comparison_entity("p1", "Attention Is All You Need", year=2017)
    e2 = extract_comparison_entity("p2", "FlashAttention: Fast and Memory-Efficient Attention", year=2022)
    e3 = extract_comparison_entity("p3", "Mamba: Linear-Time Sequence Modeling with Selective State Spaces", year=2023)

    table_md = build_markdown_comparison_table([e1, e2, e3])
    assert "| 对比维度 |" in table_md
    assert "《Attention Is All You Need》 (2017)" in table_md
    assert "《FlashAttention: Fast and Memory-Efficient Attention》 (2022)" in table_md
    assert "《Mamba: Linear-Time Sequence Modeling with Selective State Spaces》 (2023)" in table_md
    assert "**解决核心痛点**" in table_md
    assert "**核心架构与机制**" in table_md
    assert "**复杂度与资源开销**" in table_md


def test_build_lineage_mermaid_timeline() -> None:
    e1 = extract_comparison_entity("p1", "Attention Is All You Need", year=2017)
    e2 = extract_comparison_entity("p2", "FlashAttention", year=2022)
    e3 = extract_comparison_entity("p3", "Mamba", year=2023)

    mermaid_code = build_lineage_mermaid_timeline([e3, e1, e2])  # Pass out of order
    assert "```mermaid" in mermaid_code
    assert "flowchart TD" in mermaid_code
    # Chronological ordering check: 2017 must appear before 2022, which appears before 2023
    pos_2017 = mermaid_code.find("2017")
    pos_2022 = mermaid_code.find("2022")
    pos_2023 = mermaid_code.find("2023")
    assert pos_2017 < pos_2022 < pos_2023
    assert "-->|技术演进" in mermaid_code


def test_generate_cross_paper_matrix_end_to_end() -> None:
    papers = [
        {"paper_id": "p1", "title": "Attention Is All You Need", "year": 2017},
        {"paper_id": "p2", "title": "FlashAttention", "year": 2022},
    ]
    matrix = generate_cross_paper_matrix(papers)
    assert len(matrix.entities) == 2
    assert "| 对比维度 |" in matrix.dimension_matrix_markdown
    assert "```mermaid" in matrix.lineage_mermaid_timeline
    assert "跨文献技术演进与设计权衡全景剖析" in matrix.synthesis_narrative
