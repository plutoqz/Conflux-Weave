"""Unit tests for zero-key dual citation graph engine and regex extraction."""

from __future__ import annotations

from unittest.mock import patch

from conflux_weave.paper_reading.citation_engine import (
    build_dual_citation_graph,
    build_mermaid_citation_graph,
    extract_references_via_regex,
    query_openalex_citations,
)
from conflux_weave.paper_reading.spec import CitationItem, CitationType


SAMPLE_BIBLIOGRAPHY_TEXT = """
Introduction and related work details here...

References
[1] Vaswani, A., Shazeer, N., Parmar, N., "Attention is all you need", NeurIPS, 2017.
[2] Ba, J. L., Kiros, J. R., Hinton, G. E., "Layer normalization", arXiv preprint, 2016.
[3] Devlin, J., Chang, M. W., Lee, K., "BERT: Pre-training of deep bidirectional transformers", NAACL, 2018.
"""


def test_extract_references_via_regex() -> None:
    items = extract_references_via_regex(SAMPLE_BIBLIOGRAPHY_TEXT)
    assert len(items) == 3
    assert items[0].title == "Attention is all you need"
    assert items[0].year == 2017
    assert "Vaswani" in items[0].authors[0]
    assert items[1].year == 2016
    assert items[2].year == 2018


def test_build_mermaid_citation_graph() -> None:
    founds = (
        CitationItem(
            paper_id="f1",
            title="Layer Normalization",
            authors=("Ba et al.",),
            year=2016,
            citation_type=CitationType.BACKWARD_FOUNDATION,
        ),
    )
    derivs = (
        CitationItem(
            paper_id="d1",
            title="BERT",
            authors=("Devlin et al.",),
            year=2018,
            citation_type=CitationType.FORWARD_DERIVATIVE,
        ),
    )

    graph_code = build_mermaid_citation_graph("Attention Is All You Need", founds, derivs)
    assert "```mermaid" in graph_code
    assert "graph LR" in graph_code
    assert "理论源流与奠基性工作" in graph_code
    assert "后序衍生与突破" in graph_code
    assert "Attention Is All You Need" in graph_code


def test_build_dual_citation_graph_offline_fallback() -> None:
    # Explicitly disable network to test offline pure regex fallback
    graph = build_dual_citation_graph(
        seed_paper_id="paper_123",
        seed_paper_title="Transformer Network",
        raw_text=SAMPLE_BIBLIOGRAPHY_TEXT,
        allow_network=False,
    )
    assert graph.seed_paper_title == "Transformer Network"
    assert len(graph.backward_foundations) >= 2
    assert len(graph.forward_derivatives) >= 1
    assert "```mermaid" in graph.mermaid_graph


def test_query_openalex_citations_swallows_errors() -> None:
    with patch("conflux_weave.paper_reading.citation_engine._safe_fetch_json", return_value=None):
        foundations, derivatives, oid = query_openalex_citations("Sample Title")
        assert foundations == ()
        assert derivatives == ()
        assert oid is None
