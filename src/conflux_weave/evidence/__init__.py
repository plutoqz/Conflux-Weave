"""Evidence-native domain contracts."""

from conflux_weave.evidence.contracts import (
    AssessmentVerdict,
    ArtifactRef,
    Citation,
    Claim,
    ClaimAssessment,
    EvidenceRef,
    EvidenceRelation,
    ReportBackground,
    ReportDocument,
    ReportParagraph,
    ReportSection,
    SourceSnapshot,
)
from conflux_weave.evidence.validation import (
    CitationValidationError,
    ReportDocumentValidationError,
    require_closed_citations,
    require_closed_report_document,
    unreferenced_claim_ids,
)
from conflux_weave.evidence.delivery import (
    AnswerBlock,
    EvidenceSupportStatus,
    SourceTrustLevel,
    render_evidence_report,
    render_report_document,
)

__all__ = [
    "AssessmentVerdict",
    "AnswerBlock",
    "ArtifactRef",
    "Citation",
    "CitationValidationError",
    "Claim",
    "ClaimAssessment",
    "EvidenceRef",
    "EvidenceRelation",
    "EvidenceSupportStatus",
    "ReportBackground",
    "ReportDocument",
    "ReportDocumentValidationError",
    "ReportParagraph",
    "ReportSection",
    "SourceSnapshot",
    "SourceTrustLevel",
    "render_evidence_report",
    "render_report_document",
    "require_closed_citations",
    "require_closed_report_document",
    "unreferenced_claim_ids",
]
