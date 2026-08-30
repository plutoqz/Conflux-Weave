"""Deterministic integrity checks for claim-level citations."""

from collections import Counter

from conflux_weave.evidence.contracts import (
    Citation,
    Claim,
    EvidenceRef,
    ReportDocument,
    ReportParagraph,
)


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


class ReportDocumentValidationError(ValueError):
    """Raised when a report document breaks paragraph-level claim closure."""


def require_closed_report_document(
    document: ReportDocument, claims: tuple[Claim, ...]
) -> None:
    """Enforce paragraph-level claim closure (spec W1 C1/C2 + structure)."""
    if not document.objective.strip():
        raise ReportDocumentValidationError("report document objective must not be empty")
    known_claim_ids = {claim.claim_id for claim in claims}
    _require_closed_paragraph(document.summary, known_claim_ids, "summary")
    if not document.sections:
        raise ReportDocumentValidationError("report document requires at least one section")
    for index, section in enumerate(document.sections):
        if not section.heading.strip():
            raise ReportDocumentValidationError(f"section {index} heading must not be empty")
        if not section.paragraphs:
            raise ReportDocumentValidationError(f"section {index} requires at least one paragraph")
        for paragraph_index, paragraph in enumerate(section.paragraphs):
            _require_closed_paragraph(
                paragraph,
                known_claim_ids,
                f"section {index} paragraph {paragraph_index}",
            )
    for index, question in enumerate(document.open_questions):
        if not question.strip():
            raise ReportDocumentValidationError(f"open question {index} must not be empty")
    for index, item in enumerate(document.background):
        if not item.heading.strip():
            raise ReportDocumentValidationError(f"background {index} heading must not be empty")
        if not item.text.strip():
            raise ReportDocumentValidationError(f"background {index} text must not be empty")


def _require_closed_paragraph(
    paragraph: ReportParagraph, known_claim_ids: set[str], label: str
) -> None:
    if not paragraph.text.strip():
        raise ReportDocumentValidationError(f"{label} text must not be empty")
    if not paragraph.claim_ids:
        raise ReportDocumentValidationError(f"{label} must reference at least one Claim")
    if len(paragraph.claim_ids) != len(set(paragraph.claim_ids)):
        raise ReportDocumentValidationError(f"{label} repeats a Claim reference")
    unknown = [item for item in paragraph.claim_ids if item not in known_claim_ids]
    if unknown:
        raise ReportDocumentValidationError(
            f"{label} references unknown Claims: {', '.join(unknown)}"
        )


def unreferenced_claim_ids(
    document: ReportDocument, claims: tuple[Claim, ...]
) -> tuple[str, ...]:
    referenced: set[str] = set(document.summary.claim_ids)
    for section in document.sections:
        for paragraph in section.paragraphs:
            referenced.update(paragraph.claim_ids)
    return tuple(claim.claim_id for claim in claims if claim.claim_id not in referenced)
