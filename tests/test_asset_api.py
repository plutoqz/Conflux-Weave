"""Tests for P2.0-B library assets API, binary streaming, and Evidence Inspector contracts."""

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient

from conflux_weave.api_contracts import (
    DocumentAssetDetailResponse,
    DocumentAssetsResponse,
    EvidenceResponse,
)
from conflux_weave.document_assets import (
    BoundingBox,
    DocumentAsset,
    DocumentAssetsManifest,
    PDFAssetExtractor,
)
from conflux_weave.runtime import LocalArtifactStore, SQLiteRuntimeRepository
from conflux_weave.server import WorkerLoop, create_app


class PassiveRuntime:
    executor_id = "passive@v1"
    task_kinds = ("paper_discovery",)

    def work_once(self, **kwargs):
        return None


def _create_minimal_pdf_with_image() -> bytes:
    """Create a minimal valid 1-page PDF containing a PNG image using PyMuPDF."""
    import fitz

    doc = fitz.open()
    page = doc.new_page(width=300, height=300)
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 20, 20), 0)
    pix.set_pixel(0, 0, (255, 0, 0))
    png_bytes = pix.tobytes("png")
    rect = fitz.Rect(50, 50, 150, 150)
    page.insert_image(rect, stream=png_bytes)
    page.insert_text((50, 170), "Figure 1: Test tiny diagram.")
    pdf_bytes = doc.tobytes()
    doc.close()
    return pdf_bytes


def _setup_app_with_document(tmp_path: Path):
    store = LocalArtifactStore(tmp_path / "artifacts")
    repo = SQLiteRuntimeRepository(tmp_path / "db" / "runtime.sqlite3", store)
    runtime = PassiveRuntime()
    app = create_app(repo, runtime, worker=WorkerLoop(runtime, interval_seconds=10))
    client = TestClient(app)

    # Generate PDF with image
    pdf_bytes = _create_minimal_pdf_with_image()
    raw_hash = hashlib.sha256(pdf_bytes).hexdigest()
    doc_id = f"document-sha256-{raw_hash}"

    # Extract assets using PDFAssetExtractor
    extractor = PDFAssetExtractor(store)
    source_artifact = store.put_bytes(
        pdf_bytes,
        media_type="application/pdf",
        producer_step_id="step-test-import",
        schema_version="conflux-weave.source-document.v1",
    )
    manifest, assets_artifact = extractor.extract_document_assets(
        pdf_bytes,
        document_id=doc_id,
        source_snapshot_id=doc_id,
        source_artifact_id=source_artifact.artifact_id,
        parent_segments=(),
    )

    # Register in library-registry.json
    registry_path = tmp_path / "db" / "library-registry.json"
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(
        json.dumps(
            [
                {
                    "relative_path": "test_paper.pdf",
                    "document_id": doc_id,
                    "source_snapshot_id": doc_id,
                    "source_artifact_id": source_artifact.artifact_id,
                    "segments_artifact_id": "",
                    "assets_artifact_id": assets_artifact.artifact_id,
                    "asset_count": manifest.asset_count,
                    "status": "parsed",
                    "size_bytes": len(pdf_bytes),
                }
            ],
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    return client, repo, store, doc_id, manifest, assets_artifact


def test_document_assets_endpoint_returns_list(tmp_path: Path):
    client, repo, store, doc_id, manifest, assets_artifact = _setup_app_with_document(tmp_path)

    response = client.get(f"/api/v1/library/documents/{doc_id}/assets")
    assert response.status_code == 200
    data = response.json()
    validated = DocumentAssetsResponse.model_validate(data)

    assert validated.document_id == doc_id
    assert validated.assets_artifact_id == assets_artifact.artifact_id
    assert validated.asset_count == manifest.asset_count
    assert validated.asset_count >= 1
    assert len(validated.items) == validated.asset_count

    first_item = validated.items[0]
    assert first_item.asset_id.startswith("asset-sha256-")
    assert first_item.page == 1
    assert first_item.content_url == f"/api/v1/library/assets/{first_item.asset_id}/content"
    assert first_item.extraction_status == "extracted"


def test_document_assets_endpoint_for_document_without_assets(tmp_path: Path):
    client, repo, store, doc_id, manifest, assets_artifact = _setup_app_with_document(tmp_path)

    # Register another doc with no assets
    registry_path = tmp_path / "db" / "library-registry.json"
    rows = json.loads(registry_path.read_text(encoding="utf-8"))
    rows.append({
        "relative_path": "plain_text.md",
        "document_id": "document-plain-text",
        "source_snapshot_id": "document-plain-text",
        "source_artifact_id": "",
        "segments_artifact_id": "",
        "assets_artifact_id": "",
        "asset_count": 0,
        "status": "parsed",
        "size_bytes": 100,
    })
    registry_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")

    response = client.get("/api/v1/library/documents/document-plain-text/assets")
    assert response.status_code == 200
    data = response.json()
    assert data["asset_count"] == 0
    assert data["items"] == []


def test_document_assets_endpoint_not_found(tmp_path: Path):
    client, _, _, _, _, _ = _setup_app_with_document(tmp_path)
    response = client.get("/api/v1/library/documents/non-existent-doc/assets")
    assert response.status_code == 404
    assert response.json()["code"] == "document_not_found"


def test_asset_detail_endpoint_success_and_not_found(tmp_path: Path):
    client, _, _, _, manifest, _ = _setup_app_with_document(tmp_path)
    asset_id = manifest.assets[0].asset_id

    response = client.get(f"/api/v1/library/assets/{asset_id}")
    assert response.status_code == 200
    data = response.json()
    validated = DocumentAssetDetailResponse.model_validate(data)
    assert validated.asset_id == asset_id
    assert validated.page == 1
    assert validated.media_type in {"image/png", "image/jpeg"}
    assert validated.bbox is not None
    assert validated.coordinate_space == "pdf_page_points_top_left"

    # Non-existent asset
    bad_res = client.get("/api/v1/library/assets/asset-sha256-00000000000000000000000000000000")
    assert bad_res.status_code == 404
    assert bad_res.json()["code"] == "asset_not_found"


def test_asset_content_original_and_thumbnail(tmp_path: Path):
    client, _, _, _, manifest, _ = _setup_app_with_document(tmp_path)
    asset_id = manifest.assets[0].asset_id

    # Original content
    res = client.get(f"/api/v1/library/assets/{asset_id}/content")
    assert res.status_code == 200
    assert res.headers["content-type"] in {"image/png", "image/jpeg"}
    assert res.headers["content-disposition"] == "inline"
    assert res.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert res.headers["x-content-type-options"] == "nosniff"
    assert len(res.content) > 0
    # Must start with PNG or JPEG magic bytes
    assert res.content.startswith(b"\x89PNG\r\n\x1a\n") or res.content.startswith(b"\xff\xd8\xff")

    # Thumbnail variant
    if manifest.assets[0].thumbnail_artifact_ref:
        thumb_res = client.get(f"/api/v1/library/assets/{asset_id}/content?variant=thumbnail")
        assert thumb_res.status_code == 200
        assert thumb_res.headers["content-type"] == "image/png"
        assert thumb_res.headers["x-content-type-options"] == "nosniff"
        assert thumb_res.content.startswith(b"\x89PNG\r\n\x1a\n")


def test_asset_content_degraded_or_failed_asset(tmp_path: Path):
    client, repo, store, doc_id, manifest, assets_artifact = _setup_app_with_document(tmp_path)

    # Create a failed asset entry inside the manifest
    failed_asset = DocumentAsset(
        asset_id="asset-sha256-failed0000000000000000000000",
        document_id=doc_id,
        source_snapshot_id=doc_id,
        page=2,
        asset_kind="embedded_image",
        artifact_ref=None,
        media_type="image/png",
        content_hash=None,
        width_px=0,
        height_px=0,
        bbox=None,
        page_width=300.0,
        page_height=300.0,
        extraction_status="failed",
        warnings=("render_failed", "bbox_unavailable"),
    )

    new_manifest = DocumentAssetsManifest(
        document_id=doc_id,
        source_snapshot_id=doc_id,
        source_artifact_id="art-test",
        generated_at="2026-09-08T12:00:00Z",
        asset_count=manifest.asset_count + 1,
        unique_content_count=manifest.unique_content_count,
        status_counts={"extracted": manifest.asset_count, "failed": 1},
        assets=manifest.assets + (failed_asset,),
    )
    new_assets_art = store.put_json(
        new_manifest.to_dict(),
        producer_step_id="step-test-failed",
        schema_version="conflux-weave.document-assets.v1",
    )

    # Update registry
    registry_path = tmp_path / "db" / "library-registry.json"
    rows = json.loads(registry_path.read_text(encoding="utf-8"))
    rows[0]["assets_artifact_id"] = new_assets_art.artifact_id
    rows[0]["asset_count"] = new_manifest.asset_count
    registry_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")

    # Detail endpoint returns 200 with warnings and failed status
    detail_res = client.get(f"/api/v1/library/assets/{failed_asset.asset_id}")
    assert detail_res.status_code == 200
    detail = detail_res.json()
    assert detail["extraction_status"] == "failed"
    assert "render_failed" in detail["warnings"]
    assert detail["content_url"] is None

    # Content endpoint returns 422 with structured error
    content_res = client.get(f"/api/v1/library/assets/{failed_asset.asset_id}/content")
    assert content_res.status_code == 422
    err = content_res.json()
    assert err["code"] == "asset_content_unavailable"
    assert err["extraction_status"] == "failed"
    assert "render_failed" in err["warnings"]


def test_asset_content_path_traversal_and_unregistered_rejected(tmp_path: Path):
    client, _, _, _, _, _ = _setup_app_with_document(tmp_path)

    # Path traversal attempts
    res1 = client.get("/api/v1/library/assets/..%2F..%2Fetc%2Fpasswd/content")
    assert res1.status_code in {400, 404}

    res2 = client.get("/api/v1/library/assets/../../windows/win.ini/content")
    assert res2.status_code in {400, 404}

    # Arbitrary unregistered hash
    res3 = client.get("/api/v1/library/assets/artifact-sha256-abcdef0123456789/content")
    assert res3.status_code == 404
    assert res3.json()["code"] == "asset_not_found"


def test_asset_content_corrupted_magic_bytes(tmp_path: Path):
    client, repo, store, doc_id, manifest, _ = _setup_app_with_document(tmp_path)

    # Store fake text bytes with image schema
    corrupted_art = store.put_bytes(
        b"<html>This is definitely not an image</html>",
        media_type="image/png",
        producer_step_id="step-corrupt-test",
        schema_version="conflux-weave.image-asset.v1",
    )

    corrupted_asset = DocumentAsset(
        asset_id="asset-sha256-corrupted000000000000000000",
        document_id=doc_id,
        source_snapshot_id=doc_id,
        page=1,
        asset_kind="embedded_image",
        artifact_ref=corrupted_art.artifact_id,
        media_type="image/png",
        content_hash="sha256:fake",
        width_px=100,
        height_px=100,
        bbox=None,
        page_width=300.0,
        page_height=300.0,
        extraction_status="extracted",
    )

    new_manifest = DocumentAssetsManifest(
        document_id=doc_id,
        source_snapshot_id=doc_id,
        source_artifact_id="art-test",
        generated_at="2026-09-08T12:00:00Z",
        asset_count=1,
        unique_content_count=1,
        status_counts={"extracted": 1},
        assets=(corrupted_asset,),
    )
    new_art = store.put_json(
        new_manifest.to_dict(),
        producer_step_id="step-corrupt-test",
        schema_version="conflux-weave.document-assets.v1",
    )

    registry_path = tmp_path / "db" / "library-registry.json"
    rows = json.loads(registry_path.read_text(encoding="utf-8"))
    rows[0]["assets_artifact_id"] = new_art.artifact_id
    registry_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")

    res = client.get(f"/api/v1/library/assets/{corrupted_asset.asset_id}/content")
    assert res.status_code == 422
    assert res.json()["code"] == "corrupted_asset"


def test_asset_content_unsupported_mime(tmp_path: Path):
    client, repo, store, doc_id, manifest, _ = _setup_app_with_document(tmp_path)

    pdf_art = store.put_bytes(
        b"%PDF-1.4...",
        media_type="application/pdf",
        producer_step_id="step-pdf-test",
        schema_version="conflux-weave.image-asset.v1",
    )

    bad_mime_asset = DocumentAsset(
        asset_id="asset-sha256-badmime00000000000000000000",
        document_id=doc_id,
        source_snapshot_id=doc_id,
        page=1,
        asset_kind="embedded_image",
        artifact_ref=pdf_art.artifact_id,
        media_type="application/pdf",
        content_hash="sha256:fake",
        width_px=100,
        height_px=100,
        bbox=None,
        page_width=300.0,
        page_height=300.0,
        extraction_status="extracted",
    )

    new_manifest = DocumentAssetsManifest(
        document_id=doc_id,
        source_snapshot_id=doc_id,
        source_artifact_id="art-test",
        generated_at="2026-09-08T12:00:00Z",
        asset_count=1,
        unique_content_count=1,
        status_counts={"extracted": 1},
        assets=(bad_mime_asset,),
    )
    new_art = store.put_json(
        new_manifest.to_dict(),
        producer_step_id="step-badmime-test",
        schema_version="conflux-weave.document-assets.v1",
    )

    registry_path = tmp_path / "db" / "library-registry.json"
    rows = json.loads(registry_path.read_text(encoding="utf-8"))
    rows[0]["assets_artifact_id"] = new_art.artifact_id
    registry_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")

    res = client.get(f"/api/v1/library/assets/{bad_mime_asset.asset_id}/content")
    assert res.status_code == 415
    assert res.json()["code"] == "unsupported_media_type"


def test_asset_content_oversized_rejected(tmp_path: Path, monkeypatch):
    client, repo, store, doc_id, manifest, _ = _setup_app_with_document(tmp_path)
    asset_id = manifest.assets[0].asset_id

    # Temporarily lower the size limit to test 413
    import conflux_weave.server

    monkeypatch.setattr(conflux_weave.server, "MAX_IMAGE_SIZE_BYTES", 10)

    res = client.get(f"/api/v1/library/assets/{asset_id}/content")
    assert res.status_code == 413
    assert res.json()["code"] == "asset_too_large"


def test_evidence_response_contract_backward_compatibility():
    # Pure text evidence
    text_ev = EvidenceResponse(
        evidence_id="ev-text-1",
        source_snapshot_id="snapshot-1",
        locator={"page": 1, "paragraph": 2},
        quote="A text quote from document.",
        extraction_method="text_parser",
    )
    assert text_ev.modality == "text"
    assert text_ev.asset_id is None
    assert text_ev.artifact_ref is None

    # Image evidence
    image_ev = EvidenceResponse(
        evidence_id="ev-image-1",
        source_snapshot_id="snapshot-1",
        locator={"page": 5, "bbox": {"x": 10.0, "y": 20.0, "width": 200.0, "height": 150.0}},
        quote="Figure 2: Pipeline architecture.",
        extraction_method="pymupdf-v1",
        modality="image",
        asset_id="asset-sha256-abc",
        artifact_ref="artifact-sha256-def",
    )
    assert image_ev.modality == "image"
    assert image_ev.asset_id == "asset-sha256-abc"
    assert image_ev.artifact_ref == "artifact-sha256-def"
    assert image_ev.locator["bbox"]["width"] == 200.0


def test_registry_authorization_cannot_be_bypassed_after_document_removal(tmp_path: Path):
    """Removing a document from library-registry.json must invalidate asset access immediately."""
    client, repo, store, doc_id, manifest, assets_artifact = _setup_app_with_document(tmp_path)
    asset_id = manifest.assets[0].asset_id

    # 1. Access succeeds initially
    detail_res = client.get(f"/api/v1/library/assets/{asset_id}")
    assert detail_res.status_code == 200
    content_res = client.get(f"/api/v1/library/assets/{asset_id}/content")
    assert content_res.status_code == 200

    # 2. Document is removed from registry
    registry_path = tmp_path / "db" / "library-registry.json"
    registry_path.write_text("[]\n", encoding="utf-8")

    # 3. Both endpoints must now return 404 immediately, cannot be bypassed via internal cache
    del_detail_res = client.get(f"/api/v1/library/assets/{asset_id}")
    assert del_detail_res.status_code == 404
    assert del_detail_res.json()["code"] == "asset_not_found"

    del_content_res = client.get(f"/api/v1/library/assets/{asset_id}/content")
    assert del_content_res.status_code == 404
    assert del_content_res.json()["code"] == "asset_not_found"


def test_on_demand_extraction_preserves_parent_chunk_lineage(tmp_path: Path):
    """On-demand asset extraction with document-segments dicts must preserve parent_segment_ids."""
    store = LocalArtifactStore(tmp_path / "artifacts")
    repo = SQLiteRuntimeRepository(tmp_path / "db" / "runtime.sqlite3", store)
    runtime = PassiveRuntime()
    app = create_app(repo, runtime, worker=WorkerLoop(runtime, interval_seconds=10))
    client = TestClient(app)

    # 1. Put raw PDF artifact
    pdf_bytes = _create_minimal_pdf_with_image()
    raw_hash = hashlib.sha256(pdf_bytes).hexdigest()
    doc_id = f"document-sha256-{raw_hash}"
    source_artifact = store.put_bytes(
        pdf_bytes,
        media_type="application/pdf",
        producer_step_id="step-test-import",
        schema_version="conflux-weave.source-document.v1",
    )

    # 2. Put segments artifact with page 1 segment
    seg_payload = {
        "schema_version": "conflux-weave.document-segments.v1",
        "document_id": doc_id,
        "segments": [
            {
                "segment_id": "seg-p1-target-chunk",
                "ordinal": 0,
                "text": "Figure 1 shows the experimental setup.",
                "locator": {"page": 1, "paragraph": 1},
            }
        ],
    }
    seg_artifact = store.put_json(
        seg_payload,
        producer_step_id="step-test-segments",
        schema_version="conflux-weave.document-segments.v1",
    )

    # 3. Register document WITHOUT assets_artifact_id
    registry_path = tmp_path / "db" / "library-registry.json"
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(
        json.dumps(
            [
                {
                    "relative_path": "paper_with_chunks.pdf",
                    "document_id": doc_id,
                    "source_snapshot_id": doc_id,
                    "source_artifact_id": source_artifact.artifact_id,
                    "segments_artifact_id": seg_artifact.artifact_id,
                    "status": "parsed",
                    "size_bytes": len(pdf_bytes),
                }
            ],
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    # 4. Trigger on-demand extraction via document assets endpoint
    resp = client.get(f"/api/v1/library/documents/{doc_id}/assets")
    assert resp.status_code == 200
    data = resp.json()
    assert data["asset_count"] >= 1
    asset_item = data["items"][0]
    assert "seg-p1-target-chunk" in asset_item["parent_segment_ids"]

    # 5. Detail endpoint also retains parent chunk lineage
    detail_res = client.get(f"/api/v1/library/assets/{asset_item['asset_id']}")
    assert detail_res.status_code == 200
    assert "seg-p1-target-chunk" in detail_res.json()["parent_segment_ids"]
