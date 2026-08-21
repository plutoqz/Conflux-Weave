import json

from pypdf import PdfWriter

from conflux_weave.documents import LocalDocumentImporter
from conflux_weave.runtime import LocalArtifactStore


def test_markdown_import_creates_snapshot_segments_and_citations(tmp_path) -> None:
    source = tmp_path / "review.md"
    source.write_text(
        "# 主线\n\n第一段结论。\n\n## 方法\n\n方法段落。\n",
        encoding="utf-8",
    )
    store = LocalArtifactStore(tmp_path / "artifacts")
    imported = LocalDocumentImporter(
        store, acquired_at="2026-08-21T00:00:00Z"
    ).import_path(source)
    report = LocalDocumentImporter(
        store, acquired_at="2026-08-21T00:00:00Z"
    ).build_report(imported, title="阅读笔记")

    assert imported.media_type == "text/markdown"
    assert imported.source_snapshot.artifact_ref == imported.source_artifact.artifact_id
    assert len(imported.segments) == 2
    assert imported.segments[0].locator == {
        "type": "markdown_lines",
        "heading": "主线",
        "heading_level": 1,
        "start_line": 2,
        "end_line": 4,
    }
    assert len(report.claims) == len(report.evidence) == len(report.citations) == 2
    assert report.claims[0].claim_id == report.citations[0].claim_id
    assert report.citations[0].evidence_id == report.evidence[0].evidence_id
    markdown = store.read_bytes(report.report_artifact).decode("utf-8")
    assert "阅读笔记" in markdown
    assert "Markdown lines 2-4" in markdown
    assert imported.snapshot_artifact.artifact_id in markdown

    segments = json.loads(store.read_bytes(imported.segments_artifact))
    assert segments["segments"][1]["locator"]["heading"] == "方法"


def test_pdf_import_uses_page_locators(tmp_path) -> None:
    source = tmp_path / "paper.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    with source.open("wb") as handle:
        writer.write(handle)

    imported = LocalDocumentImporter(LocalArtifactStore(tmp_path / "artifacts")).import_path(source)

    assert imported.media_type == "application/pdf"
    assert imported.segments == ()
    assert imported.source_snapshot.source_type == "local_document"


def test_unsupported_document_type_is_rejected(tmp_path) -> None:
    source = tmp_path / "input.txt"
    source.write_text("not admitted", encoding="utf-8")

    try:
        LocalDocumentImporter(LocalArtifactStore(tmp_path / "artifacts")).import_path(source)
    except ValueError as exc:
        assert "expected .md" in str(exc)
    else:
        raise AssertionError("unsupported document was accepted")
