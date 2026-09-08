"""Manifest schema conformance and batch aggregation tests (v0.3 P2.0-A)."""

import json
from pathlib import Path

from conflux_weave.document_assets import (
    BoundingBox,
    DocumentAsset,
    DocumentAssetsManifest,
    AssetExtractionManifest,
    SCHEMA_DOCUMENT_ASSET,
    SCHEMA_DOCUMENT_ASSETS,
    SCHEMA_EXTRACTION_MANIFEST,
)
from conflux_weave.runtime.artifacts import LocalArtifactStore


def test_document_assets_manifest_serialization(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path / "artifacts")

    asset = DocumentAsset(
        asset_id="asset-sha256-test0001",
        document_id="doc-test-1",
        source_snapshot_id="snap-test-1",
        page=1,
        asset_kind="embedded_image",
        artifact_ref="art-img-1",
        thumbnail_artifact_ref="art-thumb-1",
        media_type="image/png",
        content_hash="sha256:abc",
        width_px=100,
        height_px=100,
        bbox=BoundingBox(x=10, y=20, width=50, height=50),
        page_width=612,
        page_height=792,
        extraction_status="extracted",
    )

    manifest = DocumentAssetsManifest(
        document_id="doc-test-1",
        source_snapshot_id="snap-test-1",
        source_artifact_id="art-src-1",
        generated_at="2026-09-08T12:00:00Z",
        asset_count=1,
        unique_content_count=1,
        status_counts={"extracted": 1, "degraded": 0, "failed": 0, "duplicate": 0},
        assets=(asset,),
    )

    d = manifest.to_dict()
    assert d["schema_version"] == SCHEMA_DOCUMENT_ASSETS
    assert d["asset_count"] == 1
    assert len(d["assets"]) == 1
    assert d["assets"][0]["schema_version"] == SCHEMA_DOCUMENT_ASSET

    # Test round-trip through artifact store JSON serialization
    ref = store.put_json(
        d,
        producer_step_id="test-manifest",
        schema_version=SCHEMA_DOCUMENT_ASSETS,
    )
    loaded = json.loads(store.read_bytes(ref).decode("utf-8"))
    assert loaded["document_id"] == "doc-test-1"
    assert loaded["assets"][0]["asset_id"] == "asset-sha256-test0001"


def test_batch_extraction_manifest_aggregation() -> None:
    batch = AssetExtractionManifest(
        batch_id="batch-001",
        extractor_name="pymupdf",
        extractor_version="pymupdf-v1",
        config_hash="sha256:cfg123",
        documents=[
            {"document_id": "doc-1", "asset_count": 5, "status": "succeeded"},
            {"document_id": "doc-2", "asset_count": 0, "status": "succeeded"},
            {"document_id": "doc-3", "asset_count": 2, "status": "partial"},
        ],
        total_assets=7,
        total_unique_contents=6,
        status_counts={"extracted": 6, "degraded": 1, "failed": 0, "duplicate": 0},
        started_at="2026-09-08T12:00:00Z",
        completed_at="2026-09-08T12:01:00Z",
        failures=[{"document_id": "doc-3", "error": "bbox_unavailable"}],
    )

    d = batch.to_dict()
    assert d["schema_version"] == SCHEMA_EXTRACTION_MANIFEST
    assert d["total_assets"] == 7
    assert len(d["failures"]) == 1
    assert d["failures"][0]["document_id"] == "doc-3"
