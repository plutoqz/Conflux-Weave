"""Contracts and schema conformance tests for DocumentAsset (v0.3 P2.0-A)."""

import re
from conflux_weave.document_assets import (
    BoundingBox,
    DocumentAsset,
    DocumentAssetsManifest,
    AssetExtractionManifest,
    derive_asset_id,
    SCHEMA_DOCUMENT_ASSET,
    SCHEMA_DOCUMENT_ASSETS,
    SCHEMA_EXTRACTION_MANIFEST,
)
from conflux_weave.evidence import EvidenceRef


def test_bounding_box_rounding_and_dict() -> None:
    bbox = BoundingBox(x=10.1234, y=20.5678, width=100.999, height=200.001)
    d = bbox.to_dict()
    assert d == {"x": 10.12, "y": 20.57, "width": 101.0, "height": 200.0}


def test_derive_asset_id_format_and_determinism() -> None:
    doc_id = "document-sha256-abc123"
    bbox = BoundingBox(x=100.0, y=200.0, width=300.0, height=150.0)
    hash1 = "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"

    id1 = derive_asset_id(doc_id, page=1, bbox=bbox, content_hash=hash1)
    id2 = derive_asset_id(doc_id, page=1, bbox=bbox, content_hash=hash1)
    assert id1 == id2
    assert re.match(r"^asset-sha256-[0-9a-f]{32}$", id1)

    # Different page yields different ID
    id_p2 = derive_asset_id(doc_id, page=2, bbox=bbox, content_hash=hash1)
    assert id_p2 != id1

    # None bbox yields valid ID
    id_nobbox = derive_asset_id(doc_id, page=1, bbox=None, content_hash=hash1)
    assert re.match(r"^asset-sha256-[0-9a-f]{32}$", id_nobbox)


def test_document_asset_to_dict_schema() -> None:
    bbox = BoundingBox(x=50.0, y=100.0, width=400.0, height=250.0)
    asset = DocumentAsset(
        asset_id="asset-sha256-0123456789abcdef0123456789abcdef",
        document_id="document-sha256-test",
        source_snapshot_id="document-sha256-test",
        page=3,
        asset_kind="embedded_image",
        artifact_ref="artifact-sha256-img01",
        thumbnail_artifact_ref="artifact-sha256-thumb01",
        media_type="image/png",
        content_hash="sha256:1111222233334444555566667777888899990000aaaabbbbccccddddeeeeffff",
        width_px=800,
        height_px=500,
        bbox=bbox,
        page_width=612.0,
        page_height=792.0,
        page_rotation=0,
        caption="Figure 1: Example architecture.",
        caption_locator={"type": "pdf_page_block", "page": 3, "block_no": 4},
        parent_segment_ids=("document-sha256-test:segment-0003",),
        warnings=("has_soft_mask:123",),
    )

    data = asset.to_dict()
    assert data["schema_version"] == SCHEMA_DOCUMENT_ASSET
    assert data["asset_id"] == asset.asset_id
    assert data["bbox"] == {"x": 50.0, "y": 100.0, "width": 400.0, "height": 250.0}
    assert data["coordinate_space"] == "pdf_page_points_top_left"
    assert data["parent_segment_ids"] == ["document-sha256-test:segment-0003"]
    assert data["warnings"] == ["has_soft_mask:123"]


def test_evidence_ref_multimodal_compatibility() -> None:
    # Text evidence default
    text_ev = EvidenceRef(
        evidence_id="ev-01",
        source_snapshot_id="snap-01",
        locator={"type": "pdf_page", "page": 1},
        quote="Text quote",
        extraction_method="text-parser",
    )
    assert text_ev.modality == "text"
    assert text_ev.asset_id is None
    assert text_ev.artifact_ref is None

    # Image evidence with optional fields
    img_ev = EvidenceRef(
        evidence_id="ev-02",
        source_snapshot_id="snap-01",
        locator={"type": "pdf_page", "page": 2, "bbox": {"x": 10, "y": 20, "width": 100, "height": 50}},
        quote="Figure 1: Diagram",
        extraction_method="pymupdf-v1",
        modality="image",
        asset_id="asset-sha256-1234",
        artifact_ref="artifact-sha256-img1234",
    )
    assert img_ev.modality == "image"
    assert img_ev.asset_id == "asset-sha256-1234"
    assert img_ev.artifact_ref == "artifact-sha256-img1234"
