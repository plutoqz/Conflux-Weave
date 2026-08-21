"""Evidence-native domain contracts."""

from conflux_weave.evidence.contracts import (
    AssessmentVerdict,
    ArtifactRef,
    Citation,
    Claim,
    ClaimAssessment,
    EvidenceRef,
    EvidenceRelation,
    SourceSnapshot,
)
from conflux_weave.evidence.validation import (
    CitationValidationError,
    require_closed_citations,
)

__all__ = [
    "AssessmentVerdict",
    "ArtifactRef",
    "Citation",
    "CitationValidationError",
    "Claim",
    "ClaimAssessment",
    "EvidenceRef",
    "EvidenceRelation",
    "SourceSnapshot",
    "require_closed_citations",
]
