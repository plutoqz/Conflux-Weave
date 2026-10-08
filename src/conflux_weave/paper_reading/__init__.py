"""Conflux-Weave Paper Reading V2 Package.

Includes:
1. Adaptive Paper Paradigm Classifier & Outline Generator
2. Physical Visual Slice Inliner & Deep Visual Analysis
3. Zero-Key Dual Citation Graph Engine (OpenAlex / arXiv / Local Regex Fallback)
4. Cross-Paper Horizontal Comparative Matrix & Evolutionary Lineage Generator
5. End-to-End Paper Reading V2 Workflow
"""

from __future__ import annotations

from conflux_weave.paper_reading.citation_engine import (
    build_dual_citation_graph,
    build_mermaid_citation_graph,
    extract_references_via_regex,
    query_openalex_citations,
)
from conflux_weave.paper_reading.classifier import (
    build_adaptive_chapters,
    build_adaptive_reading_outline,
    classify_paradigm_heuristic,
)
from conflux_weave.paper_reading.cross_paper_matrix import (
    build_lineage_mermaid_timeline,
    build_markdown_comparison_table,
    build_synthesis_narrative,
    extract_comparison_entity,
    generate_cross_paper_matrix,
)
from conflux_weave.paper_reading.spec import (
    AdaptiveReadingOutline,
    CitationItem,
    CitationType,
    CrossPaperComparativeMatrix,
    DualCitationGraph,
    PaperComparisonEntity,
    PaperParadigm,
    PaperReadingReportV2,
    PhysicalVisualSlice,
    ReadingChapterSpec,
    VisualAssetType,
)
from conflux_weave.paper_reading.visual_inliner import (
    classify_visual_asset_type,
    extract_visual_slices_from_pdf,
    generate_deep_visual_analysis,
    inline_visual_slices_into_markdown,
)
from conflux_weave.paper_reading.workflow import (
    run_paper_reading_v2,
    synthesize_chapter_markdown,
)

__all__ = [
    "AdaptiveReadingOutline",
    "CitationItem",
    "CitationType",
    "CrossPaperComparativeMatrix",
    "DualCitationGraph",
    "PaperComparisonEntity",
    "PaperParadigm",
    "PaperReadingReportV2",
    "PhysicalVisualSlice",
    "ReadingChapterSpec",
    "VisualAssetType",
    "build_adaptive_chapters",
    "build_adaptive_reading_outline",
    "build_dual_citation_graph",
    "build_lineage_mermaid_timeline",
    "build_markdown_comparison_table",
    "build_mermaid_citation_graph",
    "build_synthesis_narrative",
    "classify_paradigm_heuristic",
    "classify_visual_asset_type",
    "extract_comparison_entity",
    "extract_references_via_regex",
    "extract_visual_slices_from_pdf",
    "generate_cross_paper_matrix",
    "generate_deep_visual_analysis",
    "inline_visual_slices_into_markdown",
    "query_openalex_citations",
    "run_paper_reading_v2",
    "synthesize_chapter_markdown",
]
