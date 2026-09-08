"""PDF image asset extraction tests across fixture scenarios (v0.3 P2.0-A)."""

import hashlib
import io
import json
from pathlib import Path
import fitz
import pytest

from conflux_weave.document_assets import PDFAssetExtractor, EXTRACTOR_VERSION
from conflux_weave.documents import LocalDocumentImporter
from conflux_weave.runtime.artifacts import LocalArtifactStore

REAL_PAPERS_DIR = Path(r"F:\vscode\AcademyHunter\academy_hunter\papers")
PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_MANIFEST = PROJECT_ROOT / "tests" / "fixtures" / "multimodal" / "p2_fixture_manifest.json"


def _create_sample_image_png(width: int = 100, height: int = 60, color=(200, 50, 50)) -> bytes:
    """Create a minimal PNG byte stream using PyMuPDF pixmap."""
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, width, height), 0)
    pix.set_rect(pix.irect, color)
    return pix.tobytes("png")


def _build_synthetic_multimodal_pdf() -> bytes:
    """Create a 3-page synthetic PDF with normal image, duplicate image, and text-only page."""
    doc = fitz.open()

    # Page 1: normal image with caption
    p1 = doc.new_page(width=595, height=842)
    p1.insert_text((50, 50), "Introduction\nHere is a description of the methodology.")
    img1_bytes = _create_sample_image_png(150, 100, (10, 120, 200))
    img1_rect = fitz.Rect(50, 80, 200, 180)
    p1.insert_image(img1_rect, stream=img1_bytes)
    p1.insert_text((50, 200), "Figure 1: Architectural diagram of the pipeline.")

    # Page 2: duplicate image (same bytes as img1) placed at different location
    p2 = doc.new_page(width=595, height=842)
    p2.insert_text((50, 50), "Evaluation Details\n")
    img2_rect = fitz.Rect(100, 100, 250, 200)
    p2.insert_image(img2_rect, stream=img1_bytes)
    p2.insert_text((50, 220), "Figure 2: Repeated architecture for comparison.")

    # Page 3: text only
    p3 = doc.new_page(width=595, height=842)
    p3.insert_text((50, 50), "Conclusion\nIn conclusion, the proposed method works effectively.")

    raw = doc.tobytes()
    doc.close()
    return raw


def _build_synthetic_scanned_pdf() -> bytes:
    """Create a 1-page scanned PDF with 0 text and 1 full-page raster image."""
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    img_bytes = _create_sample_image_png(400, 600, (240, 240, 240))
    page.insert_image(page.rect, stream=img_bytes)
    raw = doc.tobytes()
    doc.close()
    return raw


def test_synthetic_pdf_asset_extraction(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path / "artifacts")
    extractor = PDFAssetExtractor(store)

    pdf_bytes = _build_synthetic_multimodal_pdf()
    doc_id = "doc-synthetic-01"
    snap_id = "snap-synthetic-01"
    src_art_id = "art-source-01"

    manifest, manifest_artifact = extractor.extract_document_assets(
        pdf_bytes,
        document_id=doc_id,
        source_snapshot_id=snap_id,
        source_artifact_id=src_art_id,
    )

    assert manifest.document_id == doc_id
    assert manifest.asset_count == 2
    assert manifest.unique_content_count == 1
    assert manifest.status_counts["extracted"] == 1
    assert manifest.status_counts["duplicate"] == 1

    # Asset 1 (Page 1): normal extracted
    a1 = manifest.assets[0]
    assert a1.page == 1
    assert a1.asset_kind == "embedded_image"
    assert a1.extraction_status == "extracted"
    assert a1.duplicate_of_asset_id is None
    assert a1.bbox is not None
    assert a1.caption is not None
    assert "Figure 1:" in a1.caption

    # Asset 2 (Page 2): duplicate of Asset 1
    a2 = manifest.assets[1]
    assert a2.page == 2
    assert a2.asset_kind == "embedded_image"
    assert a2.extraction_status == "duplicate"
    assert a2.duplicate_of_asset_id == a1.asset_id
    assert a2.content_hash == a1.content_hash

    # Check that image artifact exists in store
    img_bytes = store.read_bytes_by_id(a1.artifact_ref)
    assert f"sha256:{hashlib.sha256(img_bytes).hexdigest()}" == a1.content_hash


def test_fixture_manifest_matches_available_files() -> None:
    payload = json.loads(FIXTURE_MANIFEST.read_text(encoding="utf-8"))
    fixtures = payload["fixtures"]
    assert len(fixtures) >= 6
    assert len({item["category"] for item in fixtures}) == len(fixtures)

    validated = 0
    for item in fixtures:
        assert len(item["sha256"]) == 64
        assert all(char in "0123456789abcdef" for char in item["sha256"])
        path = Path(item["file_path"])
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        if not path.is_file():
            continue
        raw = path.read_bytes()
        assert hashlib.sha256(raw).hexdigest() == item["sha256"]
        assert len(raw) == item["size_bytes"]
        with fitz.open(stream=raw, filetype="pdf") as document:
            assert len(document) == item["page_count"]
        validated += 1

    assert validated >= 1


def test_synthetic_scanned_page_detection(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path / "artifacts")
    extractor = PDFAssetExtractor(store)

    pdf_bytes = _build_synthetic_scanned_pdf()
    manifest, _ = extractor.extract_document_assets(
        pdf_bytes,
        document_id="doc-scan-01",
        source_snapshot_id="snap-scan-01",
        source_artifact_id="art-scan-01",
    )

    assert manifest.asset_count == 1
    asset = manifest.assets[0]
    assert asset.asset_kind == "scanned_page"
    assert "scanned_page_detected" in asset.warnings
    assert asset.parent_segment_ids == ()


def test_local_document_importer_with_assets(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path / "artifacts")
    importer = LocalDocumentImporter(store, extract_assets=True)

    pdf_bytes = _build_synthetic_multimodal_pdf()
    pdf_path = tmp_path / "test_paper.pdf"
    pdf_path.write_bytes(pdf_bytes)

    imported = importer.import_path(pdf_path)
    assert imported.assets_artifact is not None
    assert len(imported.assets) == 2
    assert len(imported.segments) >= 2  # Text segments parsed normally


@pytest.mark.skipif(not REAL_PAPERS_DIR.exists(), reason="Real papers directory not available")
def test_real_fixture_f1_normal_embedded(tmp_path: Path) -> None:
    f1_path = REAL_PAPERS_DIR / "2606.13053.pdf"
    if not f1_path.exists():
        pytest.skip(f"{f1_path} not found")

    store = LocalArtifactStore(tmp_path / "artifacts")
    extractor = PDFAssetExtractor(store)
    raw = f1_path.read_bytes()

    manifest, _ = extractor.extract_document_assets(
        raw,
        document_id="doc-f1",
        source_snapshot_id="snap-f1",
        source_artifact_id="art-f1",
    )

    assert manifest.asset_count == 5
    assert manifest.status_counts["extracted"] == 5
    # Check that Fig 2 on Page 12 was extracted with correct caption and bbox
    p12_assets = [a for a in manifest.assets if a.page == 12]
    assert len(p12_assets) >= 1
    fig2 = p12_assets[0]
    assert fig2.caption is not None
    assert "Figure 2" in fig2.caption
    assert fig2.bbox is not None
    assert fig2.bbox.width > 300


@pytest.mark.skipif(not REAL_PAPERS_DIR.exists(), reason="Real papers directory not available")
def test_real_fixture_f2_duplicate_images(tmp_path: Path) -> None:
    f2_path = REAL_PAPERS_DIR / "2606.07299.pdf"
    if not f2_path.exists():
        pytest.skip(f"{f2_path} not found")

    store = LocalArtifactStore(tmp_path / "artifacts")
    extractor = PDFAssetExtractor(store)
    raw = f2_path.read_bytes()

    manifest, _ = extractor.extract_document_assets(
        raw,
        document_id="doc-f2",
        source_snapshot_id="snap-f2",
        source_artifact_id="art-f2",
    )

    assert manifest.asset_count == 56
    # Soft-mask composition hashes the final visible image, so distinct masks
    # applied to a shared base xref remain distinct visual contents.
    assert manifest.unique_content_count == 18
    assert manifest.status_counts["duplicate"] > 0


@pytest.mark.skipif(not REAL_PAPERS_DIR.exists(), reason="Real papers directory not available")
def test_real_fixture_f6_text_only_negative(tmp_path: Path) -> None:
    f6_path = REAL_PAPERS_DIR / "2606.08151.pdf"
    if not f6_path.exists():
        pytest.skip(f"{f6_path} not found")

    store = LocalArtifactStore(tmp_path / "artifacts")
    extractor = PDFAssetExtractor(store)
    raw = f6_path.read_bytes()

    manifest, _ = extractor.extract_document_assets(
        raw,
        document_id="doc-f6",
        source_snapshot_id="snap-f6",
        source_artifact_id="art-f6",
    )

    assert manifest.asset_count == 0
    assert manifest.unique_content_count == 0
    assert len(manifest.assets) == 0


def test_same_page_multiple_occurrences_generates_unique_asset_ids(tmp_path: Path) -> None:
    """Ensure identical image placed multiple times on the SAME page generates unique IDs and correct bboxes."""
    store = LocalArtifactStore(tmp_path / "artifacts")
    extractor = PDFAssetExtractor(store)

    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((50, 30), "Page with two instances of the same image")
    img_bytes = _create_sample_image_png(120, 80, (0, 150, 200))
    rect1 = fitz.Rect(50, 60, 170, 140)
    rect2 = fitz.Rect(250, 300, 370, 380)
    page.insert_image(rect1, stream=img_bytes)
    page.insert_image(rect2, stream=img_bytes)
    raw = doc.tobytes()
    doc.close()

    manifest, _ = extractor.extract_document_assets(
        raw,
        document_id="doc-same-page-dup",
        source_snapshot_id="snap-same-page-dup",
        source_artifact_id="art-same-page-dup",
        generated_at="2026-09-08T12:00:00Z",
    )

    assert manifest.asset_count == 2
    assert manifest.unique_content_count == 1
    assert manifest.status_counts["extracted"] == 1
    assert manifest.status_counts["duplicate"] == 1

    a1, a2 = manifest.assets[0], manifest.assets[1]
    assert a1.page == 1 and a2.page == 1
    assert a1.bbox is not None and a2.bbox is not None
    assert a1.bbox.x != a2.bbox.x or a1.bbox.y != a2.bbox.y
    assert a1.asset_id != a2.asset_id
    assert a1.extraction_status == "extracted"
    assert a1.duplicate_of_asset_id is None
    assert a2.extraction_status == "duplicate"
    assert a2.duplicate_of_asset_id == a1.asset_id
    assert a2.duplicate_of_asset_id != a2.asset_id


def test_manifest_determinism_identical_runs(tmp_path: Path) -> None:
    """Ensure running extraction twice on identical inputs produces identical manifest and artifact IDs."""
    store = LocalArtifactStore(tmp_path / "artifacts")
    extractor = PDFAssetExtractor(store)
    pdf_bytes = _build_synthetic_multimodal_pdf()

    fixed_time = "2026-09-08T12:30:00Z"
    m1, art1 = extractor.extract_document_assets(
        pdf_bytes,
        document_id="doc-det",
        source_snapshot_id="snap-det",
        source_artifact_id="art-src-det",
        generated_at=fixed_time,
    )
    m2, art2 = extractor.extract_document_assets(
        pdf_bytes,
        document_id="doc-det",
        source_snapshot_id="snap-det",
        source_artifact_id="art-src-det",
        generated_at=fixed_time,
    )

    assert m1.to_dict() == m2.to_dict()
    assert art1.artifact_id == art2.artifact_id
    assert art1.content_hash == art2.content_hash


@pytest.mark.skipif(not REAL_PAPERS_DIR.exists(), reason="Real papers directory not available")
def test_real_fixture_f3_masks_and_special_colorspace(tmp_path: Path) -> None:
    f3_path = REAL_PAPERS_DIR / "2606.08146.pdf"
    if not f3_path.exists():
        pytest.skip(f"{f3_path} not found")

    store = LocalArtifactStore(tmp_path / "artifacts")
    extractor = PDFAssetExtractor(store)
    raw = f3_path.read_bytes()

    manifest, _ = extractor.extract_document_assets(
        raw,
        document_id="doc-f3",
        source_snapshot_id="snap-f3",
        source_artifact_id="art-f3",
    )

    assert manifest.asset_count == 8
    # Page 6 has 6 soft masks
    p6_assets = [a for a in manifest.assets if a.page == 6]
    assert len(p6_assets) == 6
    assert any(any("has_soft_mask" in w for w in a.warnings) for a in p6_assets)
    for asset in p6_assets:
        assert asset.extraction_method == f"{EXTRACTOR_VERSION}:smask_composited"
        assert asset.extraction_status == "extracted"
        assert asset.artifact_ref is not None
        pix = fitz.Pixmap(store.read_bytes_by_id(asset.artifact_ref))
        assert pix.alpha == 1
    # Page 13 has Indexed colorspace
    p13_assets = [a for a in manifest.assets if a.page == 13]
    assert len(p13_assets) == 2
    assert any(any("special_colorspace:Indexed" in w for w in a.warnings) for a in p13_assets)


@pytest.mark.skipif(not REAL_PAPERS_DIR.exists(), reason="Real papers directory not available")
def test_real_fixture_f4_drawings_vector(tmp_path: Path) -> None:
    f4_path = REAL_PAPERS_DIR / "2606.08049.pdf"
    if not f4_path.exists():
        pytest.skip(f"{f4_path} not found")

    store = LocalArtifactStore(tmp_path / "artifacts")
    extractor = PDFAssetExtractor(store)
    raw = f4_path.read_bytes()

    manifest, _ = extractor.extract_document_assets(
        raw,
        document_id="doc-f4",
        source_snapshot_id="snap-f4",
        source_artifact_id="art-f4",
    )

    assert manifest.asset_count == 14
    assert manifest.status_counts["extracted"] == 14


@pytest.mark.skipif(not REAL_PAPERS_DIR.exists(), reason="Real papers directory not available")
def test_real_fixture_f5_low_text_edge_case(tmp_path: Path) -> None:
    f5_path = REAL_PAPERS_DIR / "2607.02436.pdf"
    if not f5_path.exists():
        pytest.skip(f"{f5_path} not found")

    store = LocalArtifactStore(tmp_path / "artifacts")
    extractor = PDFAssetExtractor(store)
    raw = f5_path.read_bytes()

    manifest, _ = extractor.extract_document_assets(
        raw,
        document_id="doc-f5",
        source_snapshot_id="snap-f5",
        source_artifact_id="art-f5",
    )

    # Some assets on page 1-22 have no rects placed directly in page content stream
    degraded = [a for a in manifest.assets if a.extraction_status == "degraded"]
    assert len(degraded) > 0
    assert any("bbox_unavailable" in a.warnings for a in degraded)


def test_extract_batch_generates_asset_extraction_manifest(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path / "artifacts")
    extractor = PDFAssetExtractor(store)

    pdf1 = _build_synthetic_multimodal_pdf()
    pdf2 = _build_synthetic_scanned_pdf()

    items = [
        (pdf1, "doc-b1", "snap-b1", "art-b1", ()),
        (pdf2, "doc-b2", "snap-b2", "art-b2", ()),
    ]

    doc_manifests, batch_manifest, batch_artifact = extractor.extract_batch(
        items,
        batch_id="batch-test-01",
        generated_at="2026-09-08T12:00:00Z",
    )

    assert len(doc_manifests) == 2
    assert batch_manifest.batch_id == "batch-test-01"
    assert batch_manifest.total_assets == 3  # 2 from pdf1 + 1 from pdf2
    assert len(batch_manifest.documents) == 2
    assert batch_artifact.schema_version == "conflux-weave.asset-extraction-manifest.v1"


def test_failed_occurrences_are_not_deduplicated_and_reach_batch_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((50, 30), "Two image occurrences with forced extraction failure")
    img_bytes = _create_sample_image_png(120, 80, (80, 90, 100))
    page.insert_image(fitz.Rect(50, 60, 170, 140), stream=img_bytes)
    page.insert_image(fitz.Rect(250, 300, 370, 380), stream=img_bytes)
    raw = doc.tobytes()
    doc.close()

    def fail_extract(*_args, **_kwargs):
        raise RuntimeError("forced raw extraction failure")

    def fail_render(*_args, **_kwargs):
        raise RuntimeError("forced region render failure")

    monkeypatch.setattr(fitz.Document, "extract_image", fail_extract)
    monkeypatch.setattr(fitz.Page, "get_pixmap", fail_render)

    store = LocalArtifactStore(tmp_path / "artifacts")
    extractor = PDFAssetExtractor(store)
    manifests, batch, _ = extractor.extract_batch(
        [(raw, "doc-failed", "snap-failed", "source-failed", ())],
        batch_id="batch-failed",
        generated_at="2026-09-08T12:00:00Z",
    )

    manifest = manifests[0]
    assert manifest.status_counts == {
        "extracted": 0,
        "degraded": 0,
        "failed": 2,
        "duplicate": 0,
    }
    assert manifest.unique_content_count == 0
    assert len({asset.asset_id for asset in manifest.assets}) == 2
    assert all(asset.extraction_status == "failed" for asset in manifest.assets)
    assert all(asset.artifact_ref is None for asset in manifest.assets)
    assert all(asset.content_hash is None for asset in manifest.assets)
    assert len(batch.failures) == 2
    assert {failure["asset_id"] for failure in batch.failures} == {
        asset.asset_id for asset in manifest.assets
    }
