"""Deterministic local-document import and citation preparation for W1.2."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from pypdf import PdfReader

from conflux_weave.evidence import (
    ArtifactRef,
    Citation,
    Claim,
    EvidenceRef,
    SourceSnapshot,
    require_closed_citations,
)
from conflux_weave.runtime.artifacts import LocalArtifactStore


class UnsupportedDocumentError(ValueError):
    """Raised when a local document format is outside the W1.2 contract."""


@dataclass(frozen=True, slots=True)
class DocumentSegment:
    segment_id: str
    document_id: str
    ordinal: int
    text: str
    locator: dict[str, int | str]


@dataclass(frozen=True, slots=True)
class ImportedDocument:
    document_id: str
    source_snapshot: SourceSnapshot
    source_artifact: ArtifactRef
    snapshot_artifact: ArtifactRef
    segments_artifact: ArtifactRef
    segments: tuple[DocumentSegment, ...]
    media_type: str


@dataclass(frozen=True, slots=True)
class DocumentReport:
    report_artifact: ArtifactRef
    claims: tuple[Claim, ...]
    evidence: tuple[EvidenceRef, ...]
    citations: tuple[Citation, ...]


class LocalDocumentImporter:
    def __init__(self, artifact_store: LocalArtifactStore, *, acquired_at: str | None = None) -> None:
        self.artifact_store = artifact_store
        self.acquired_at = acquired_at or _utc_now()

    def import_path(self, path: Path, *, producer_step_id: str = "step-document-import") -> ImportedDocument:
        if not path.is_file():
            raise FileNotFoundError(f"document not found: {path}")
        suffix = path.suffix.lower()
        if suffix not in {".md", ".markdown", ".pdf"}:
            raise UnsupportedDocumentError(
                f"unsupported document type: {suffix or '<none>'}; expected .md, .markdown, or .pdf"
            )
        raw = path.read_bytes()
        content_hash = hashlib.sha256(raw).hexdigest()
        document_id = f"document-sha256-{content_hash}"
        media_type = "application/pdf" if suffix == ".pdf" else "text/markdown"
        source_artifact = self.artifact_store.put_bytes(
            raw,
            media_type=media_type,
            producer_step_id=producer_step_id,
            schema_version="conflux-weave.source-document.v1",
        )
        text_segments = (
            _parse_pdf(raw, document_id)
            if suffix == ".pdf"
            else _parse_markdown(raw.decode("utf-8"), document_id)
        )
        segments = tuple(text_segments)
        segments_payload = {
            "schema_version": "conflux-weave.document-segments.v1",
            "document_id": document_id,
            "source_artifact_id": source_artifact.artifact_id,
            "segments": [
                {
                    "segment_id": segment.segment_id,
                    "ordinal": segment.ordinal,
                    "text": segment.text,
                    "locator": segment.locator,
                }
                for segment in segments
            ],
        }
        segments_artifact = self.artifact_store.put_json(
            segments_payload,
            producer_step_id=producer_step_id,
            schema_version="conflux-weave.document-segments.v1",
        )
        snapshot = SourceSnapshot(
            source_id=document_id,
            source_type="local_document",
            canonical_uri=path.resolve().as_uri(),
            acquired_at=self.acquired_at,
            content_hash=f"sha256:{content_hash}",
            artifact_ref=source_artifact.artifact_id,
        )
        snapshot_payload = {
            "schema_version": "conflux-weave.source-snapshot.v1",
            "source_id": snapshot.source_id,
            "source_type": snapshot.source_type,
            "canonical_uri": snapshot.canonical_uri,
            "acquired_at": snapshot.acquired_at,
            "content_hash": snapshot.content_hash,
            "artifact_ref": snapshot.artifact_ref,
            "segments_artifact_ref": segments_artifact.artifact_id,
        }
        snapshot_artifact = self.artifact_store.put_json(
            snapshot_payload,
            producer_step_id=producer_step_id,
            schema_version="conflux-weave.source-snapshot.v1",
        )
        return ImportedDocument(
            document_id=document_id,
            source_snapshot=snapshot,
            source_artifact=source_artifact,
            snapshot_artifact=snapshot_artifact,
            segments_artifact=segments_artifact,
            segments=segments,
            media_type=media_type,
        )

    def build_report(
        self,
        document: ImportedDocument,
        *,
        title: str | None = None,
        producer_step_id: str = "step-document-report",
    ) -> DocumentReport:
        claims: list[Claim] = []
        evidence: list[EvidenceRef] = []
        citations: list[Citation] = []
        lines = [
            f"# {title or document.document_id}",
            "",
            "> W1.2 本地文档导入报告。内容来自已登记的单一 SourceSnapshot；未调用网络或模型。",
            "",
            f"- SourceSnapshot: `{document.source_snapshot.source_id}`",
            f"- 原始 Artifact: `{document.source_artifact.artifact_id}`",
            f"- Snapshot Artifact: `{document.snapshot_artifact.artifact_id}`",
            f"- 分段 Artifact: `{document.segments_artifact.artifact_id}`",
            "",
            "## 可定位内容",
            "",
        ]
        for index, segment in enumerate(document.segments, start=1):
            evidence_id = f"{document.document_id}:evidence-{segment.ordinal:04d}"
            claim_id = f"{document.document_id}:claim-{segment.ordinal:04d}"
            citation_id = f"{document.document_id}:citation-{segment.ordinal:04d}"
            claims.append(
                Claim(
                    claim_id=claim_id,
                    text=segment.text,
                    claim_type="source_excerpt",
                    importance="supporting",
                    generated_by_step=producer_step_id,
                )
            )
            evidence.append(
                EvidenceRef(
                    evidence_id=evidence_id,
                    source_snapshot_id=document.source_snapshot.source_id,
                    locator=segment.locator,
                    quote=segment.text,
                    extraction_method="deterministic-local-document-parser-v1",
                )
            )
            citations.append(
                Citation(
                    citation_id=citation_id,
                    claim_id=claim_id,
                    evidence_id=evidence_id,
                    display_index=index,
                )
            )
            lines.extend(
                [
                    f"### [{index}] {segment.locator.get('heading', '文档片段')}",
                    "",
                    segment.text,
                    "",
                    f"引用：`{citation_id}` -> `{evidence_id}`",
                    f"定位：`{_format_locator(segment.locator)}`",
                    "",
                ]
            )
        closed_claims = tuple(claims)
        closed_evidence = tuple(evidence)
        closed_citations = tuple(citations)
        require_closed_citations(closed_claims, closed_evidence, closed_citations)
        report = ("\n".join(lines).rstrip() + "\n").encode("utf-8")
        report_artifact = self.artifact_store.put_bytes(
            report,
            media_type="text/markdown",
            producer_step_id=producer_step_id,
            schema_version="conflux-weave.document-report.v1",
        )
        return DocumentReport(
            report_artifact,
            closed_claims,
            closed_evidence,
            closed_citations,
        )


def _parse_markdown(text: str, document_id: str) -> list[DocumentSegment]:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").splitlines()
    segments: list[DocumentSegment] = []
    current_heading = "文档开头"
    current_level = 0
    current_start = 1
    current_lines: list[str] = []

    def flush(end_line: int) -> None:
        body = "\n".join(current_lines).strip()
        if not body:
            return
        ordinal = len(segments) + 1
        segments.append(
            DocumentSegment(
                segment_id=f"{document_id}:segment-{ordinal:04d}",
                document_id=document_id,
                ordinal=ordinal,
                text=body,
                locator={
                    "type": "markdown_lines",
                    "heading": current_heading,
                    "heading_level": current_level,
                    "start_line": current_start,
                    "end_line": end_line,
                },
            )
        )

    for number, line in enumerate(lines, start=1):
        match = re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", line)
        if match:
            flush(number - 1)
            current_heading = match.group(2)
            current_level = len(match.group(1))
            current_start = number + 1
            current_lines = []
        else:
            current_lines.append(line)
    flush(len(lines))
    return segments


def _parse_pdf(raw: bytes, document_id: str) -> list[DocumentSegment]:
    import io

    reader = PdfReader(io.BytesIO(raw))
    segments: list[DocumentSegment] = []
    for page_number, page in enumerate(reader.pages, start=1):
        text = (page.extract_text() or "").strip()
        if not text:
            continue
        ordinal = len(segments) + 1
        segments.append(
            DocumentSegment(
                segment_id=f"{document_id}:segment-{ordinal:04d}",
                document_id=document_id,
                ordinal=ordinal,
                text=text,
                locator={"type": "pdf_page", "page": page_number, "heading": f"第 {page_number} 页"},
            )
        )
    return segments


def _format_locator(locator: dict[str, int | str]) -> str:
    if locator["type"] == "pdf_page":
        return f"PDF page {locator['page']}"
    return f"Markdown lines {locator['start_line']}-{locator['end_line']}"


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def build_pdf_manifest(root: Path, *, output: Path | None = None) -> dict[str, object]:
    """Create a deterministic read-only inventory for a local PDF corpus."""
    if not root.is_dir():
        raise FileNotFoundError(f"corpus directory not found: {root}")
    entries = []
    for path in sorted(root.rglob("*.pdf"), key=lambda item: str(item).casefold()):
        try:
            raw = path.read_bytes()
            entries.append({"path": str(path), "relative_path": str(path.relative_to(root)), "size_bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(), "status": "importable" if raw.startswith(b"%PDF") else "parse_failed"})
        except OSError as exc:
            entries.append({"path": str(path), "relative_path": str(path.relative_to(root)), "size_bytes": None, "sha256": None, "status": "read_failed", "failure": str(exc)})
    manifest = {"schema_version": "conflux-weave.corpus-manifest.v1", "root": str(root.resolve()), "generated_at": _utc_now(), "file_count": len(entries), "files": entries}
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest
