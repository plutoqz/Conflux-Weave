"""Deterministic local-document import and citation preparation for W1.2."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser
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
    assets_artifact: ArtifactRef | None = None
    assets: tuple[Any, ...] = ()


@dataclass(frozen=True, slots=True)
class DocumentReport:
    report_artifact: ArtifactRef
    claims: tuple[Claim, ...]
    evidence: tuple[EvidenceRef, ...]
    citations: tuple[Citation, ...]


class LocalDocumentImporter:
    def __init__(
        self,
        artifact_store: LocalArtifactStore,
        *,
        acquired_at: str | None = None,
        extract_assets: bool = False,
    ) -> None:
        self.artifact_store = artifact_store
        self.acquired_at = acquired_at or _utc_now()
        self.extract_assets = extract_assets

    def import_path(
        self,
        path: Path,
        *,
        producer_step_id: str = "step-document-import",
        extract_assets: bool | None = None,
        suffix: str | None = None,
    ) -> ImportedDocument:
        if not path.is_file():
            raise FileNotFoundError(f"document not found: {path}")
        resolved_suffix = (suffix or path.suffix).lower()
        supported = {".md", ".markdown", ".pdf", ".html", ".htm", ".docx"}
        if resolved_suffix not in supported:
            raise UnsupportedDocumentError(
                f"unsupported document type: {resolved_suffix or '<none>'}; expected .md, .markdown, or .pdf"
            )
        raw = path.read_bytes()
        content_hash = hashlib.sha256(raw).hexdigest()
        document_id = f"document-sha256-{content_hash}"
        media_type = (
            "application/pdf"
            if resolved_suffix == ".pdf"
            else "text/html"
            if resolved_suffix in {".html", ".htm"}
            else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            if resolved_suffix == ".docx"
            else "text/markdown"
        )
        source_artifact = self.artifact_store.put_bytes(
            raw,
            media_type=media_type,
            producer_step_id=producer_step_id,
            schema_version="conflux-weave.source-document.v1",
        )
        if resolved_suffix == ".pdf":
            text_segments = _parse_pdf(raw, document_id)
        elif resolved_suffix in {".html", ".htm"}:
            text_segments = _parse_html(raw.decode("utf-8", errors="replace"), document_id)
        elif resolved_suffix == ".docx":
            text_segments = _parse_docx(raw, document_id)
        else:
            text_segments = _parse_markdown(raw.decode("utf-8", errors="replace"), document_id)
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

        assets_artifact = None
        assets: tuple[Any, ...] = ()
        should_extract = self.extract_assets if extract_assets is None else extract_assets
        if resolved_suffix == ".pdf" and should_extract:
            from conflux_weave.document_assets import PDFAssetExtractor

            extractor = PDFAssetExtractor(self.artifact_store)
            assets_manifest, assets_artifact = extractor.extract_document_assets(
                raw,
                document_id=document_id,
                source_snapshot_id=snapshot.source_id,
                source_artifact_id=source_artifact.artifact_id,
                parent_segments=segments,
                generated_at=self.acquired_at,
                producer_step_id=producer_step_id,
            )
            assets = assets_manifest.assets

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
        if assets_artifact is not None:
            snapshot_payload["assets_artifact_ref"] = assets_artifact.artifact_id

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
            assets_artifact=assets_artifact,
            assets=assets,
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
        text = _clean_extracted_text(page.extract_text() or "")
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


class _HTMLStructureParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.sections: list[dict[str, Any]] = []
        self._current_heading = "文档开头"
        self._current_level = 0
        self._current_text: list[str] = []
        self._tag_stack: list[str] = []
        self._ignore = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag_lower = tag.lower()
        self._tag_stack.append(tag_lower)
        if tag_lower in {"script", "style", "noscript", "head"}:
            self._ignore = True

    def handle_endtag(self, tag: str) -> None:
        tag_lower = tag.lower()
        if self._tag_stack and self._tag_stack[-1] == tag_lower:
            self._tag_stack.pop()
        if tag_lower in {"script", "style", "noscript", "head"}:
            self._ignore = False
        if tag_lower in {"p", "div", "article", "section", "li", "blockquote", "pre", "h1", "h2", "h3", "h4", "h5", "h6"}:
            self._current_text.append("\n")

    def handle_data(self, data: str) -> None:
        if self._ignore:
            return
        cleaned = data.strip()
        if not cleaned:
            return
        parent_heading = next((t for t in reversed(self._tag_stack) if re.match(r"^h[1-6]$", t)), None)
        if parent_heading:
            body = " ".join(" ".join(self._current_text).split()).strip()
            if body:
                self.sections.append({
                    "heading": self._current_heading,
                    "level": self._current_level,
                    "text": body,
                })
            self._current_text = []
            self._current_heading = cleaned
            self._current_level = int(parent_heading[1])
        else:
            self._current_text.append(cleaned)

    def close(self) -> None:
        super().close()
        body = " ".join(" ".join(self._current_text).split()).strip()
        if body:
            self.sections.append({
                "heading": self._current_heading,
                "level": self._current_level,
                "text": body,
            })


def _parse_html(html_text: str, document_id: str) -> list[DocumentSegment]:
    parser = _HTMLStructureParser()
    parser.feed(html_text)
    parser.close()
    segments: list[DocumentSegment] = []
    for item in parser.sections:
        ordinal = len(segments) + 1
        segments.append(
            DocumentSegment(
                segment_id=f"{document_id}:segment-{ordinal:04d}",
                document_id=document_id,
                ordinal=ordinal,
                text=item["text"],
                locator={
                    "type": "html_section",
                    "heading": item["heading"],
                    "heading_level": item["level"],
                    "ordinal": ordinal,
                },
            )
        )
    return segments


def _parse_docx(raw: bytes, document_id: str) -> list[DocumentSegment]:
    import io
    import xml.etree.ElementTree as ET
    import zipfile

    segments: list[DocumentSegment] = []
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            if "word/document.xml" not in zf.namelist():
                return segments
            xml_content = zf.read("word/document.xml")
    except Exception:
        return segments

    root = ET.fromstring(xml_content)
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}

    current_heading = "文档开头"
    current_level = 0
    current_paras: list[str] = []

    def flush() -> None:
        nonlocal current_paras
        body = "\n".join(p for p in current_paras if p).strip()
        if body:
            ordinal = len(segments) + 1
            segments.append(
                DocumentSegment(
                    segment_id=f"{document_id}:segment-{ordinal:04d}",
                    document_id=document_id,
                    ordinal=ordinal,
                    text=body,
                    locator={
                        "type": "docx_paragraph",
                        "heading": current_heading,
                        "heading_level": current_level,
                        "paragraph_index": ordinal,
                    },
                )
            )
        current_paras = []

    for p in root.iter(f"{{{ns['w']}}}p"):
        p_text = "".join(t.text or "" for t in p.iter(f"{{{ns['w']}}}t")).strip()
        if not p_text:
            continue
        style_el = p.find(f".//{{{ns['w']}}}pStyle")
        style_val = style_el.attrib.get(f"{{{ns['w']}}}val", "") if style_el is not None else ""
        heading_match = re.search(r"heading\s*(\d)", style_val, re.IGNORECASE) or re.search(r"标题\s*(\d)", style_val)
        if heading_match:
            flush()
            current_heading = p_text
            current_level = int(heading_match.group(1))
        else:
            current_paras.append(p_text)

    flush()
    return segments


def _clean_extracted_text(text: str) -> str:
    """Normalize malformed surrogate code points emitted by some PDF fonts."""
    return text.encode("utf-8", errors="replace").decode("utf-8").strip()


def _format_locator(locator: dict[str, int | str]) -> str:
    loc_type = locator.get("type")
    if loc_type == "pdf_page":
        return f"PDF page {locator.get('page')}"
    if loc_type == "html_section":
        return f"HTML section <{locator.get('heading')}>"
    if loc_type == "docx_paragraph":
        return f"DOCX paragraph {locator.get('paragraph_index', '')} ({locator.get('heading', '')})"
    return f"Markdown lines {locator.get('start_line')}-{locator.get('end_line')}"


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


def import_pdf_corpus(
    root: Path,
    importer: LocalDocumentImporter,
    *,
    output: Path | None = None,
    producer_step_id: str = "step-corpus-import",
) -> dict[str, object]:
    """Import a PDF directory with content-hash deduplication and failure retention."""
    manifest = build_pdf_manifest(root)
    seen: dict[str, str] = {}
    rows: list[dict[str, object]] = []
    for item in manifest["files"]:
        path = Path(str(item["path"]))
        row = dict(item)
        content_hash = str(item["sha256"])
        if content_hash in seen:
            row.update(status="duplicate", duplicate_of=seen[content_hash])
        else:
            try:
                imported = importer.import_path(path, producer_step_id=producer_step_id)
                seen[content_hash] = str(item["relative_path"])
                row.update(status="imported", document_id=imported.document_id,
                           source_snapshot_id=imported.source_snapshot.source_id,
                           source_artifact_id=imported.source_artifact.artifact_id,
                           segments_artifact_id=imported.segments_artifact.artifact_id,
                           segment_count=len(imported.segments))
                if imported.assets_artifact is not None:
                    row.update(
                        assets_artifact_id=imported.assets_artifact.artifact_id,
                        asset_count=len(imported.assets),
                    )
            except Exception as exc:
                row.update(status="parse_failed", error_type=type(exc).__name__, error=str(exc)[:500])
        rows.append(row)
    result = {
        "schema_version": "conflux-weave.corpus-import-manifest.v1",
        "root": str(root.resolve()),
        "file_count": len(rows),
        "status_counts": {status: sum(1 for row in rows if row["status"] == status) for status in sorted({str(row["status"]) for row in rows})},
        "files": rows,
    }
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def document_title(text: str, fallback: str) -> str:
    """本地来源标题解析（W3.5 紧凑引用）：文档首行标题，兜底给定名称。

    取首个非空行；Markdown 井号与强调记号剥掉；超过 80 字符或为空视为
    无有效标题，返回 fallback（调用方传文件名主干或快照 id）。
    """
    for line in text.splitlines():
        candidate = line.strip()
        if not candidate:
            continue
        candidate = candidate.lstrip("#").strip().strip("*").strip()
        if 0 < len(candidate) <= 80:
            return candidate
        break
    return fallback


def document_page_label(locator: dict) -> str:
    """locator → 页码标签：优先 page 数字，其次 heading 文本，兜底“全文”。"""
    page = locator.get("page")
    if page is not None:
        return f"第{page}页"
    heading = str(locator.get("heading", "") or "").strip()
    return heading or "全文"


def document_title_from_segments(document_by_id, document_id: str, fallback: str) -> str:
    """从检索索引的分段表解析文档标题（W3.5 紧凑引用）。

    索引以分段为条目（id 形如 "<文档id>:segment-NNNN"），单段首行往往不是
    标题；因此聚合同一文档的全部分段，取最小序号段（首页）的首行解析标题，
    解析不出时返回 fallback。
    """
    base = document_id.split(":segment-", 1)[0]
    candidates = []
    for key, doc in document_by_id.items():
        if key != base and not key.startswith(base + ":segment-"):
            continue
        tail = key.rsplit("-", 1)[-1]
        ordinal = int(tail) if tail.isdigit() else 0
        text = getattr(doc, "text", "") or ""
        if text.strip():
            candidates.append((ordinal, text))
    if not candidates:
        return fallback
    candidates.sort()
    return document_title(candidates[0][1], fallback)
