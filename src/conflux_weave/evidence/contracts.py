"""Draft W0 contracts for immutable artifacts and claim-level evidence."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class EvidenceRelation(StrEnum):
    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    CONTEXT = "context"
    INSUFFICIENT = "insufficient"


class AssessmentVerdict(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    UNCERTAIN = "uncertain"


@dataclass(frozen=True, slots=True)
class ArtifactRef:
    artifact_id: str
    media_type: str
    content_hash: str
    storage_uri: str
    producer_step_id: str
    schema_version: str


@dataclass(frozen=True, slots=True)
class SourceSnapshot:
    source_id: str
    source_type: str
    canonical_uri: str | None
    acquired_at: str
    content_hash: str
    artifact_ref: str


@dataclass(frozen=True, slots=True)
class EvidenceRef:
    evidence_id: str
    source_snapshot_id: str
    locator: dict[str, Any]
    quote: str
    extraction_method: str


@dataclass(frozen=True, slots=True)
class Claim:
    claim_id: str
    text: str
    claim_type: str
    importance: str
    generated_by_step: str


@dataclass(frozen=True, slots=True)
class ClaimAssessment:
    claim_id: str
    evidence_ids: tuple[str, ...]
    relation: EvidenceRelation
    verdict: AssessmentVerdict
    rationale: str
    evaluator_ref: str


@dataclass(frozen=True, slots=True)
class Citation:
    citation_id: str
    claim_id: str
    evidence_id: str
    display_index: int


@dataclass(frozen=True, slots=True)
class ReportParagraph:
    text: str
    claim_ids: tuple[str, ...]
    unverified: bool = False
    # W3.5 融合报告：段落引用的网络来源（SourceSnapshot id）；legacy 路径为空。
    web_source_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ReportBackground:
    """Model-knowledge supplement; never claim-linked and never audited (W1.5)."""

    heading: str
    text: str


@dataclass(frozen=True, slots=True)
class ReportSection:
    heading: str
    paragraphs: tuple[ReportParagraph, ...]


@dataclass(frozen=True, slots=True)
class ReportDocument:
    objective: str
    summary: ReportParagraph
    sections: tuple[ReportSection, ...]
    open_questions: tuple[str, ...] = ()
    background: tuple[ReportBackground, ...] = ()
    unreferenced_claim_ids: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
