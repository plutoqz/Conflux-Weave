import pytest

from conflux_weave.evidence import (
    Citation,
    CitationValidationError,
    Claim,
    EvidenceRef,
    require_closed_citations,
)


def claim(identifier: str) -> Claim:
    return Claim(identifier, "claim text", "direct", "high", "step-1")


def evidence(identifier: str) -> EvidenceRef:
    return EvidenceRef(
        identifier,
        "source-1",
        {"type": "markdown_lines", "start_line": 1, "end_line": 2},
        "source quote",
        "fixture-parser-v1",
    )


def test_closed_citations_accept_contiguous_known_mappings() -> None:
    require_closed_citations(
        (claim("claim-1"), claim("claim-2")),
        (evidence("evidence-1"), evidence("evidence-2")),
        (
            Citation("citation-1", "claim-1", "evidence-1", 1),
            Citation("citation-2", "claim-2", "evidence-2", 2),
        ),
    )


@pytest.mark.parametrize(
    ("citations", "message"),
    [
        ((Citation("citation-1", "unknown", "evidence-1", 1),), "unknown claim"),
        ((Citation("citation-1", "claim-1", "unknown", 1),), "unknown evidence"),
        ((Citation("citation-1", "claim-1", "evidence-1", 2),), "contiguous"),
    ],
)
def test_invalid_citation_mappings_fail_closed(citations, message) -> None:
    with pytest.raises(CitationValidationError, match=message):
        require_closed_citations(
            (claim("claim-1"),),
            (evidence("evidence-1"),),
            citations,
        )


def test_claim_without_citation_is_rejected() -> None:
    with pytest.raises(CitationValidationError, match="claims without citations"):
        require_closed_citations((claim("claim-1"),), (), ())
