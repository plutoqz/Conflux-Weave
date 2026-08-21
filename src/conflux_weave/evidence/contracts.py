"""Draft W0 contracts for immutable artifacts and claim-level evidence."""

from dataclasses import dataclass, field
from typing import Any


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
    evidence_ids: tuple[str, ...] = field(default_factory=tuple)
    relation: str = ""
    verdict: str = ""
    rationale: str = ""
    evaluator_ref: str = ""


@dataclass(frozen=True, slots=True)
class Citation:
    citation_id: str
    claim_id: str
    evidence_id: str
    display_index: int
