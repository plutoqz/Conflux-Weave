"""Deterministic integrity checks for claim-level citations."""

from collections import Counter

from conflux_weave.evidence.contracts import Citation, Claim, EvidenceRef


class CitationValidationError(ValueError):
    """Raised when citations do not form a closed deterministic mapping."""


def require_closed_citations(
    claims: tuple[Claim, ...],
    evidence: tuple[EvidenceRef, ...],
    citations: tuple[Citation, ...],
) -> None:
    claim_ids = [claim.claim_id for claim in claims]
    evidence_ids = [item.evidence_id for item in evidence]
    citation_ids = [citation.citation_id for citation in citations]
    _require_unique("claim", claim_ids)
    _require_unique("evidence", evidence_ids)
    _require_unique("citation", citation_ids)

    expected_indices = list(range(1, len(citations) + 1))
    actual_indices = [citation.display_index for citation in citations]
    if actual_indices != expected_indices:
        raise CitationValidationError(
            f"citation display indices must be contiguous in order: {expected_indices}"
        )

    claim_id_set = set(claim_ids)
    evidence_id_set = set(evidence_ids)
    pairs: list[tuple[str, str]] = []
    for citation in citations:
        if citation.claim_id not in claim_id_set:
            raise CitationValidationError(
                f"citation references unknown claim: {citation.claim_id}"
            )
        if citation.evidence_id not in evidence_id_set:
            raise CitationValidationError(
                f"citation references unknown evidence: {citation.evidence_id}"
            )
        pairs.append((citation.claim_id, citation.evidence_id))
    if len(pairs) != len(set(pairs)):
        raise CitationValidationError("duplicate claim/evidence citation pair")

    citation_counts = Counter(citation.claim_id for citation in citations)
    missing_claims = [claim_id for claim_id in claim_ids if citation_counts[claim_id] == 0]
    if missing_claims:
        raise CitationValidationError(
            f"claims without citations: {', '.join(missing_claims)}"
        )


def _require_unique(kind: str, identifiers: list[str]) -> None:
    if len(identifiers) != len(set(identifiers)):
        raise CitationValidationError(f"duplicate {kind} identifier")
