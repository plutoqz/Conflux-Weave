"""Lineage and artifact closure tests for DocumentAsset (v0.3 P2.0-A)."""

import hashlib
from pathlib import Path
import fitz

from conflux_weave.documents import LocalDocumentImporter
from conflux_weave.runtime.artifacts import LocalArtifactStore


def _build_test_pdf_with_segments() -> bytes:
    doc = fitz.open()
    p1 = doc.new_page(width=595, height=842)
    p1.insert_text((50, 50), "Page 1 Content\nSection 1 text here.")
    # add image
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 100, 100), 0)
    pix.set_rect(pix.irect, (50, 150, 50))
    p1.insert_image(fitz.Rect(50, 100, 150, 200), stream=pix.tobytes("png"))

    p2 = doc.new_page(width=595, height=842)
    p2.insert_text((50, 50), "Page 2 Content\nSection 2 text here.")
    raw = doc.tobytes()
    doc.close()
    return raw


def test_asset_lineage_closure(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path / "artifacts")
    importer = LocalDocumentImporter(store, extract_assets=True)

    pdf_bytes = _build_test_pdf_with_segments()
    pdf_path = tmp_path / "paper_with_lineage.pdf"
    pdf_path.write_bytes(pdf_bytes)

    doc = importer.import_path(pdf_path)
    assert doc.assets_artifact is not None
    assert len(doc.assets) == 1

    asset = doc.assets[0]
    # Verify page match
    assert asset.page == 1
    # Verify parent segment association
    assert len(asset.parent_segment_ids) == 1
    parent_seg_id = asset.parent_segment_ids[0]

    # Verify that the parent segment exists in doc.segments and is on page 1
    matching_segments = [s for s in doc.segments if s.segment_id == parent_seg_id]
    assert len(matching_segments) == 1
    assert matching_segments[0].locator["page"] == 1

    # Verify image artifact integrity
    img_bytes = store.read_bytes_by_id(asset.artifact_ref)
    assert f"sha256:{hashlib.sha256(img_bytes).hexdigest()}" == asset.content_hash

    # Verify thumbnail artifact integrity and dimensions
    assert asset.thumbnail_artifact_ref is not None
    thumb_bytes = store.read_bytes_by_id(asset.thumbnail_artifact_ref)
    thumb_pix = fitz.Pixmap(thumb_bytes)
    assert max(thumb_pix.width, thumb_pix.height) <= 320
    assert thumb_pix.width > 0
    assert thumb_pix.height > 0
