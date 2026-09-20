"""Deterministic PDF document asset extraction, lineage, and manifest building (v0.3 P2.0-A)."""

from __future__ import annotations

import hashlib
import io
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import fitz  # PyMuPDF

from conflux_weave.evidence import ArtifactRef
from conflux_weave.runtime.artifacts import LocalArtifactStore

EXTRACTOR_NAME = "pymupdf"
EXTRACTOR_VERSION = "pymupdf-v1"
SCHEMA_DOCUMENT_ASSET = "conflux-weave.document-asset.v1"
SCHEMA_DOCUMENT_ASSETS = "conflux-weave.document-assets.v1"
SCHEMA_EXTRACTION_MANIFEST = "conflux-weave.asset-extraction-manifest.v1"
SCHEMA_IMAGE_ASSET = "conflux-weave.image-asset.v1"
SCHEMA_THUMBNAIL_ASSET = "conflux-weave.thumbnail-asset.v1"

CAPTION_PREFIX_PATTERN = re.compile(
    r"^(?:Fig(?:ure)?\.?|Table)\s*(\d+[a-zA-Z]?(?:[:.]|\s+-|\s+).*)$",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class BoundingBox:
    x: float
    y: float
    width: float
    height: float

    def to_dict(self) -> dict[str, float]:
        return {
            "x": round(self.x, 2),
            "y": round(self.y, 2),
            "width": round(self.width, 2),
            "height": round(self.height, 2),
        }


@dataclass(frozen=True, slots=True)
class DocumentAsset:
    asset_id: str
    document_id: str
    source_snapshot_id: str
    page: int
    asset_kind: str  # "embedded_image" | "rendered_region" | "scanned_page"
    artifact_ref: str | None
    media_type: str
    content_hash: str | None
    width_px: int
    height_px: int
    bbox: BoundingBox | None
    page_width: float
    page_height: float
    page_rotation: int = 0
    coordinate_space: str = "pdf_page_points_top_left"
    thumbnail_artifact_ref: str | None = None
    caption: str | None = None
    caption_locator: dict[str, Any] | None = None
    parent_segment_ids: tuple[str, ...] = ()
    referencing_contexts: tuple[str, ...] = ()
    ocr_text: str | None = None
    extraction_method: str = EXTRACTOR_VERSION
    extraction_status: str = "extracted"  # "extracted" | "degraded" | "failed" | "duplicate"
    duplicate_of_asset_id: str | None = None
    warnings: tuple[str, ...] = ()
    schema_version: str = SCHEMA_DOCUMENT_ASSET

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "asset_id": self.asset_id,
            "document_id": self.document_id,
            "source_snapshot_id": self.source_snapshot_id,
            "page": self.page,
            "asset_kind": self.asset_kind,
            "artifact_ref": self.artifact_ref,
            "thumbnail_artifact_ref": self.thumbnail_artifact_ref,
            "media_type": self.media_type,
            "content_hash": self.content_hash,
            "width_px": self.width_px,
            "height_px": self.height_px,
            "bbox": self.bbox.to_dict() if self.bbox is not None else None,
            "coordinate_space": self.coordinate_space,
            "page_width": round(self.page_width, 2),
            "page_height": round(self.page_height, 2),
            "page_rotation": self.page_rotation,
            "caption": self.caption,
            "caption_locator": self.caption_locator,
            "parent_segment_ids": list(self.parent_segment_ids),
            "referencing_contexts": list(self.referencing_contexts),
            "ocr_text": self.ocr_text,
            "extraction_method": self.extraction_method,
            "extraction_status": self.extraction_status,
            "duplicate_of_asset_id": self.duplicate_of_asset_id,
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True, slots=True)
class DocumentAssetsManifest:
    document_id: str
    source_snapshot_id: str
    source_artifact_id: str
    generated_at: str
    asset_count: int
    unique_content_count: int
    status_counts: dict[str, int]
    assets: tuple[DocumentAsset, ...]
    schema_version: str = SCHEMA_DOCUMENT_ASSETS

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "document_id": self.document_id,
            "source_snapshot_id": self.source_snapshot_id,
            "source_artifact_id": self.source_artifact_id,
            "generated_at": self.generated_at,
            "asset_count": self.asset_count,
            "unique_content_count": self.unique_content_count,
            "status_counts": dict(self.status_counts),
            "assets": [asset.to_dict() for asset in self.assets],
        }


@dataclass(frozen=True, slots=True)
class AssetExtractionManifest:
    batch_id: str
    extractor_name: str
    extractor_version: str
    config_hash: str
    documents: list[dict[str, Any]]
    total_assets: int
    total_unique_contents: int
    status_counts: dict[str, int]
    started_at: str
    completed_at: str
    failures: list[dict[str, Any]] = field(default_factory=list)
    schema_version: str = SCHEMA_EXTRACTION_MANIFEST

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "batch_id": self.batch_id,
            "extractor_name": self.extractor_name,
            "extractor_version": self.extractor_version,
            "config_hash": self.config_hash,
            "documents": self.documents,
            "total_assets": self.total_assets,
            "total_unique_contents": self.total_unique_contents,
            "status_counts": dict(self.status_counts),
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "failures": self.failures,
        }


def derive_asset_id(
    document_id: str,
    page: int,
    occurrence_index: int = 0,
    bbox: BoundingBox | None = None,
    content_hash: str | None = None,
    extractor_version: str = EXTRACTOR_VERSION,
) -> str:
    """Deterministically derive stable asset_id for an image occurrence."""
    bbox_str = (
        f"{bbox.x:.2f},{bbox.y:.2f},{bbox.width:.2f},{bbox.height:.2f}"
        if bbox is not None
        else "none"
    )
    entropy = f"{document_id}|{page}|{occurrence_index}|{bbox_str}|{content_hash or 'none'}|{extractor_version}"
    digest = hashlib.sha256(entropy.encode("utf-8")).hexdigest()[:32]
    return f"asset-sha256-{digest}"


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _find_caption_for_bbox(page: fitz.Page, bbox: BoundingBox | None) -> tuple[str | None, dict[str, Any] | None]:
    """Find the closest figure/table caption on the page adjacent to the bounding box."""
    if bbox is None:
        return None, None

    # Small decorative icons, symbols, and sub-figure crops should not match figure/table captions
    if (bbox.width < 140 and bbox.height < 120) or (bbox.width * bbox.height < 15000):
        return None, None

    blocks = page.get_text("blocks")
    candidates: list[tuple[float, str, dict[str, Any]]] = []

    for block in blocks:
        if len(block) < 7 or block[6] != 0:
            continue
        bx0, by0, bx1, by1, text, bno, _ = block[:7]
        cleaned_text = " ".join(text.split()).strip()
        if not cleaned_text:
            continue

        match = CAPTION_PREFIX_PATTERN.match(cleaned_text)
        if match:
            # Check horizontal overlap / column alignment
            img_x0 = bbox.x
            img_x1 = bbox.x + bbox.width
            h_overlap = min(img_x1, bx1) - max(img_x0, bx0)
            if h_overlap <= 0:
                h_dist = min(abs(img_x0 - bx1), abs(bx0 - img_x1))
                if h_dist > 60:
                    # In a different column or too far horizontally
                    continue

            img_bottom = bbox.y + bbox.height
            if by0 >= img_bottom - 5:
                v_dist = by0 - img_bottom
            elif by1 <= bbox.y + 5:
                v_dist = bbox.y - by1
            else:
                v_dist = 0.0

            if v_dist <= 150:
                locator = {
                    "type": "pdf_page_block",
                    "page": page.number + 1,
                    "block_no": bno,
                    "bbox": {
                        "x": round(bx0, 2),
                        "y": round(by0, 2),
                        "width": round(bx1 - bx0, 2),
                        "height": round(by1 - by0, 2),
                    },
                }
                candidates.append((v_dist, cleaned_text, locator))

    if not candidates:
        return None, None

    candidates.sort(key=lambda item: item[0])
    _, best_caption, best_locator = candidates[0]
    return best_caption, best_locator


FIGURE_REF_PATTERN = re.compile(
    r"\b(?:Fig(?:ure)?\.?|Table)\s*(\d+[a-zA-Z]?)\b",
    re.IGNORECASE,
)


def _extract_in_figure_text(page: fitz.Page, bbox: BoundingBox | None) -> str | None:
    """Extract vector text elements located within or overlapping the image bbox."""
    if bbox is None or bbox.width <= 0 or bbox.height <= 0:
        return None
    try:
        clip_rect = fitz.Rect(bbox.x, bbox.y, bbox.x + bbox.width, bbox.y + bbox.height)
        text = page.get_text("text", clip=clip_rect)
        cleaned = " ".join(text.split()).strip()
        return cleaned if len(cleaned) > 0 else None
    except Exception:
        return None


def _find_referencing_contexts_for_caption(
    doc: fitz.Document,
    caption: str | None,
    caption_block_no: int | None = None,
    caption_page_no: int | None = None,
    max_contexts: int = 3,
) -> tuple[str, ...]:
    """Scan document text blocks to find paragraphs explicitly referencing this Figure or Table."""
    if not caption:
        return ()
    match = FIGURE_REF_PATTERN.search(caption)
    if not match:
        return ()
    target_num = match.group(1).lower()
    target_kind = "table" if caption.strip().lower().startswith("table") else "fig"

    contexts: list[str] = []
    for page_idx in range(len(doc)):
        p = doc[page_idx]
        p_no = page_idx + 1
        blocks = p.get_text("blocks")
        for b in blocks:
            if len(b) < 7 or b[6] != 0:
                continue
            text = " ".join(b[4].split()).strip()
            if not text or len(text) < 20:
                continue
            # Skip the caption block itself
            if p_no == caption_page_no and b[5] == caption_block_no:
                continue
            # Check if block mentions the target figure/table
            for ref_m in FIGURE_REF_PATTERN.finditer(text):
                ref_kind = "table" if ref_m.group(0).lower().startswith("table") else "fig"
                ref_num = ref_m.group(1).lower()
                if ref_kind == target_kind and ref_num == target_num:
                    contexts.append(text)
                    break
            if len(contexts) >= max_contexts:
                break
        if len(contexts) >= max_contexts:
            break
    return tuple(contexts)


def _generate_thumbnail(
    doc: fitz.Document,
    xref: int,
    bbox: BoundingBox | None,
    page: fitz.Page,
    artifact_store: LocalArtifactStore,
    producer_step_id: str,
    max_dim: int = 320,
) -> str | None:
    """Generate and persist a downscaled thumbnail PNG in the ArtifactStore."""
    try:
        if bbox and bbox.width > 0 and bbox.height > 0:
            rect = fitz.Rect(bbox.x, bbox.y, bbox.x + bbox.width, bbox.y + bbox.height)
            scale = min(1.0, max_dim / max(rect.width, rect.height, 1))
            dpi = max(36, int(72 * scale * 2))
            pix = page.get_pixmap(clip=rect, dpi=dpi)
        else:
            pix = fitz.Pixmap(doc, xref)
            if pix.colorspace and pix.colorspace.n > 3:
                pix = fitz.Pixmap(fitz.csRGB, pix)
            if max(pix.width, pix.height) > max_dim:
                scale = max_dim / max(pix.width, pix.height)
                target_w = max(1, int(pix.width * scale))
                target_h = max(1, int(pix.height * scale))
                pix = fitz.Pixmap(pix, target_w, target_h, None)

        png_bytes = pix.tobytes("png")
        thumb_artifact = artifact_store.put_bytes(
            png_bytes,
            media_type="image/png",
            producer_step_id=producer_step_id,
            schema_version=SCHEMA_THUMBNAIL_ASSET,
        )
        return thumb_artifact.artifact_id
    except Exception:
        return None


class PDFAssetExtractor:
    """Production-grade deterministic PDF image asset extractor using PyMuPDF."""

    def __init__(self, artifact_store: LocalArtifactStore) -> None:
        self.artifact_store = artifact_store

    def extract_document_assets(
        self,
        raw_pdf: bytes,
        document_id: str,
        source_snapshot_id: str,
        source_artifact_id: str,
        parent_segments: tuple[Any, ...] = (),
        *,
        generated_at: str | None = None,
        producer_step_id: str = "step-asset-extraction",
    ) -> tuple[DocumentAssetsManifest, ArtifactRef]:
        """Extract all image assets from raw PDF bytes and save to ArtifactStore."""
        doc = fitz.open(stream=raw_pdf, filetype="pdf")
        assets: list[DocumentAsset] = []
        seen_content_hashes: dict[str, str] = {}  # content_hash -> first_asset_id
        status_counts = {"extracted": 0, "degraded": 0, "failed": 0, "duplicate": 0}

        page_to_segments: dict[int, list[str]] = defaultdict(list)
        for seg in parent_segments:
            if isinstance(seg, dict):
                loc = seg.get("locator")
                seg_id = seg.get("segment_id")
            else:
                loc = getattr(seg, "locator", None)
                seg_id = getattr(seg, "segment_id", None)
            page_no = loc.get("page") if isinstance(loc, dict) else None
            if page_no is not None and seg_id:
                page_to_segments[int(page_no)].append(str(seg_id))

        for page_idx in range(len(doc)):
            page = doc[page_idx]
            page_no = page_idx + 1
            page_rect = page.rect
            page_width = float(page_rect.width)
            page_height = float(page_rect.height)
            page_rotation = int(page.rotation)
            page_text = page.get_text().strip()
            parent_ids = tuple(page_to_segments.get(page_no, []))

            img_list = page.get_images(full=True)
            is_scanned_page = len(page_text) == 0 and len(img_list) > 0

            # Track occurrences on this page to ensure unique identity
            page_occurrence_count = 0

            # Use unique xrefs preserving order
            seen_page_xrefs: list[tuple[Any, ...]] = []
            seen_xref_ids = set()
            for info in img_list:
                xref = info[0]
                if xref not in seen_xref_ids:
                    seen_xref_ids.add(xref)
                    seen_page_xrefs.append(info)

            for img_info in seen_page_xrefs:
                xref = img_info[0]
                smask = img_info[1]
                colorspace = img_info[5]
                rects = page.get_image_rects(xref)

                # Determine list of occurrences for this image on this page:
                # If rects is non-empty, each rect is a distinct occurrence.
                # If rects is empty, treat as 1 occurrence with bbox=None.
                target_rects: list[fitz.Rect | None] = [r for r in rects] if rects else [None]

                for rect_item in target_rects:
                    occurrence_idx = page_occurrence_count
                    page_occurrence_count += 1

                    warnings: list[str] = []
                    extraction_status = "extracted"
                    asset_kind = "embedded_image"
                    extraction_method = EXTRACTOR_VERSION

                    if is_scanned_page:
                        asset_kind = "scanned_page"
                        warnings.append("scanned_page_detected")
                        if not parent_ids:
                            warnings.append("parent_segment_missing")

                    bbox: BoundingBox | None = None
                    if rect_item is not None:
                        bbox = BoundingBox(
                            x=float(rect_item.x0),
                            y=float(rect_item.y0),
                            width=float(rect_item.width),
                            height=float(rect_item.height),
                        )
                    else:
                        warnings.append("bbox_unavailable")
                        extraction_status = "degraded"

                    # Extraction with fallback to region rendering
                    img_bytes: bytes | None = None
                    media_type = "image/png"
                    width_px = 0
                    height_px = 0

                    try:
                        if smask > 0:
                            base_pix = fitz.Pixmap(doc, xref)
                            mask_pix = fitz.Pixmap(doc, smask)
                            if base_pix.colorspace and base_pix.colorspace.n > 3:
                                base_pix = fitz.Pixmap(fitz.csRGB, base_pix)
                            composed_pix = fitz.Pixmap(base_pix, mask_pix)
                            img_bytes = composed_pix.tobytes("png")
                            media_type = "image/png"
                            width_px = composed_pix.width
                            height_px = composed_pix.height
                            extraction_method = f"{EXTRACTOR_VERSION}:smask_composited"
                        else:
                            base_img = doc.extract_image(xref)
                            img_bytes = base_img["image"]
                            ext = base_img.get("ext", "png").lower()
                            width_px = int(base_img.get("width", 0))
                            height_px = int(base_img.get("height", 0))

                            if ext in {"png", "webp"}:
                                media_type = f"image/{ext}"
                            elif ext in {"jpg", "jpeg"}:
                                media_type = "image/jpeg"
                            else:
                                pix = fitz.Pixmap(doc, xref)
                                if pix.colorspace and pix.colorspace.n > 3:
                                    pix = fitz.Pixmap(fitz.csRGB, pix)
                                img_bytes = pix.tobytes("png")
                                media_type = "image/png"
                                width_px = pix.width
                                height_px = pix.height

                    except Exception as exc:
                        # Raw extraction failed: attempt rendered_region fallback if bbox is available
                        if bbox is not None and bbox.width > 0 and bbox.height > 0:
                            try:
                                clip_rect = fitz.Rect(bbox.x, bbox.y, bbox.x + bbox.width, bbox.y + bbox.height)
                                pix = page.get_pixmap(clip=clip_rect, dpi=150)
                                img_bytes = pix.tobytes("png")
                                media_type = "image/png"
                                width_px = pix.width
                                height_px = pix.height
                                asset_kind = "rendered_region"
                                extraction_method = f"{EXTRACTOR_VERSION}:rendered_region"
                                extraction_status = "degraded"
                                warnings.append(f"extract_image_failed: {exc}; fallback_to_rendered_region")
                            except Exception as render_exc:
                                extraction_status = "failed"
                                warnings.append(f"extract_and_render_failed: {render_exc}")
                        else:
                            extraction_status = "failed"
                            warnings.append(f"extract_image_failed: {exc}")

                    # Detect decorative icons / divider lines / bullet graphics / logos / sub-figure snippets / partial borders
                    is_icon = False
                    if bbox is not None:
                        if (bbox.width <= 120 and bbox.height <= 120) or (bbox.width * bbox.height < 12000):
                            is_icon = True
                        elif (bbox.width / max(bbox.height, 0.1) > 6) or (bbox.height / max(bbox.width, 0.1) > 6):
                            is_icon = True
                        elif min(bbox.width, bbox.height) < 40:
                            is_icon = True
                    if width_px > 0 and height_px > 0:
                        if (width_px <= 140 and height_px <= 140) or (width_px * height_px < 20000):
                            is_icon = True
                        elif (width_px / max(height_px, 1) > 6) or (height_px / max(width_px, 1) > 6):
                            is_icon = True
                        elif min(width_px, height_px) < 50:
                            is_icon = True

                    if is_icon:
                        asset_kind = "icon"
                        warnings.append("small_icon_filtered")

                    # Handle color space and smask warnings
                    if smask > 0:
                        warnings.append(f"has_soft_mask:{smask}")
                    if colorspace and colorspace not in {"DeviceRGB", "DeviceGray", "ICCBased"}:
                        warnings.append(f"special_colorspace:{colorspace}")

                    # Content hash and storage
                    if img_bytes is not None and len(img_bytes) > 0:
                        raw_hash = hashlib.sha256(img_bytes).hexdigest()
                        content_hash = f"sha256:{raw_hash}"

                        # Save image artifact
                        img_artifact = self.artifact_store.put_bytes(
                            img_bytes,
                            media_type=media_type,
                            producer_step_id=producer_step_id,
                            schema_version=SCHEMA_IMAGE_ASSET,
                        )
                        artifact_ref = img_artifact.artifact_id

                        thumb_artifact_id = _generate_thumbnail(
                            doc,
                            xref,
                            bbox,
                            page,
                            self.artifact_store,
                            producer_step_id,
                        )
                    else:
                        # Failed extraction with no bytes
                        content_hash = None
                        artifact_ref = None
                        thumb_artifact_id = None
                        extraction_status = "failed"

                    asset_id = derive_asset_id(document_id, page_no, occurrence_idx, bbox, content_hash)

                    # Deduplication logic (occurrence level)
                    duplicate_of: str | None = None
                    if extraction_status == "failed" or content_hash is None:
                        status_counts["failed"] += 1
                    elif content_hash in seen_content_hashes:
                        duplicate_of = seen_content_hashes[content_hash]
                        extraction_status = "duplicate"
                        status_counts["duplicate"] += 1
                    else:
                        seen_content_hashes[content_hash] = asset_id
                        status_counts[extraction_status] += 1

                    # Caption extraction (icons never match figure/table captions)
                    if asset_kind == "icon":
                        caption, caption_locator = None, None
                        referencing_contexts = ()
                        ocr_text = None
                    else:
                        caption, caption_locator = _find_caption_for_bbox(page, bbox)
                        if caption is None and asset_kind == "embedded_image":
                            warnings.append("caption_not_found")
                        ocr_text = _extract_in_figure_text(page, bbox)
                        cap_bno = caption_locator.get("block_no") if caption_locator else None
                        referencing_contexts = _find_referencing_contexts_for_caption(
                            doc, caption, caption_block_no=cap_bno, caption_page_no=page_no
                        )

                    asset = DocumentAsset(
                        asset_id=asset_id,
                        document_id=document_id,
                        source_snapshot_id=source_snapshot_id,
                        page=page_no,
                        asset_kind=asset_kind,
                        artifact_ref=artifact_ref,
                        thumbnail_artifact_ref=thumb_artifact_id,
                        media_type=media_type,
                        content_hash=content_hash,
                        width_px=width_px,
                        height_px=height_px,
                        bbox=bbox,
                        page_width=page_width,
                        page_height=page_height,
                        page_rotation=page_rotation,
                        coordinate_space="pdf_page_points_top_left",
                        caption=caption,
                        caption_locator=caption_locator,
                        parent_segment_ids=parent_ids,
                        referencing_contexts=referencing_contexts,
                        ocr_text=ocr_text,
                        extraction_method=extraction_method,
                        extraction_status=extraction_status,
                        duplicate_of_asset_id=duplicate_of,
                        warnings=tuple(warnings),
                    )
                    assets.append(asset)

        doc.close()

        # Deduplicate captions on the same page so small sub-images don't hijack figure captions
        page_caption_groups: dict[tuple[int, str], list[int]] = defaultdict(list)
        for idx, a in enumerate(assets):
            if a.caption:
                page_caption_groups[(a.page, a.caption)].append(idx)

        for group_indices in page_caption_groups.values():
            if len(group_indices) > 1:
                def _area(i: int) -> float:
                    item = assets[i]
                    if item.bbox is not None:
                        return item.bbox.width * item.bbox.height
                    return float(item.width_px * item.height_px)
                primary_idx = max(group_indices, key=_area)
                for i in group_indices:
                    if i != primary_idx:
                        orig = assets[i]
                        assets[i] = DocumentAsset(
                            asset_id=orig.asset_id,
                            document_id=orig.document_id,
                            source_snapshot_id=orig.source_snapshot_id,
                            page=orig.page,
                            asset_kind=orig.asset_kind,
                            artifact_ref=orig.artifact_ref,
                            thumbnail_artifact_ref=orig.thumbnail_artifact_ref,
                            media_type=orig.media_type,
                            content_hash=orig.content_hash,
                            width_px=orig.width_px,
                            height_px=orig.height_px,
                            bbox=orig.bbox,
                            page_width=orig.page_width,
                            page_height=orig.page_height,
                            page_rotation=orig.page_rotation,
                            coordinate_space=orig.coordinate_space,
                            caption=None,
                            caption_locator=None,
                            parent_segment_ids=orig.parent_segment_ids,
                            referencing_contexts=orig.referencing_contexts,
                            ocr_text=orig.ocr_text,
                            extraction_method=orig.extraction_method,
                            extraction_status=orig.extraction_status,
                            duplicate_of_asset_id=orig.duplicate_of_asset_id,
                            warnings=orig.warnings,
                            schema_version=orig.schema_version,
                        )

        manifest_timestamp = generated_at or _utc_now()

        manifest = DocumentAssetsManifest(
            document_id=document_id,
            source_snapshot_id=source_snapshot_id,
            source_artifact_id=source_artifact_id,
            generated_at=manifest_timestamp,
            asset_count=len(assets),
            unique_content_count=len(seen_content_hashes),
            status_counts=status_counts,
            assets=tuple(assets),
        )

        manifest_artifact = self.artifact_store.put_json(
            manifest.to_dict(),
            producer_step_id=producer_step_id,
            schema_version=SCHEMA_DOCUMENT_ASSETS,
        )

        return manifest, manifest_artifact

    def extract_batch(
        self,
        items: list[tuple[bytes, str, str, str, tuple[Any, ...]]],
        *,
        batch_id: str = "batch-default",
        generated_at: str | None = None,
        producer_step_id: str = "step-batch-asset-extraction",
    ) -> tuple[list[DocumentAssetsManifest], AssetExtractionManifest, ArtifactRef]:
        """Extract assets for multiple documents and produce an AssetExtractionManifest."""
        started_at = generated_at or _utc_now()
        doc_manifests: list[DocumentAssetsManifest] = []
        doc_summaries: list[dict[str, Any]] = []
        failures: list[dict[str, Any]] = []
        total_assets = 0
        total_unique_contents = 0
        agg_status = {"extracted": 0, "degraded": 0, "failed": 0, "duplicate": 0}

        for raw_pdf, doc_id, snap_id, src_art_id, segments in items:
            try:
                manifest, art = self.extract_document_assets(
                    raw_pdf,
                    doc_id,
                    snap_id,
                    src_art_id,
                    segments,
                    generated_at=started_at,
                    producer_step_id=producer_step_id,
                )
                doc_manifests.append(manifest)
                total_assets += manifest.asset_count
                total_unique_contents += manifest.unique_content_count
                for k, v in manifest.status_counts.items():
                    agg_status[k] = agg_status.get(k, 0) + v
                doc_summaries.append({
                    "document_id": doc_id,
                    "asset_count": manifest.asset_count,
                    "manifest_artifact_id": art.artifact_id,
                    "status": "succeeded" if manifest.status_counts.get("failed", 0) == 0 else "partial",
                })
                failures.extend(
                    {
                        "document_id": doc_id,
                        "asset_id": asset.asset_id,
                        "page": asset.page,
                        "warnings": list(asset.warnings),
                    }
                    for asset in manifest.assets
                    if asset.extraction_status == "failed"
                )
            except Exception as exc:
                failures.append({"document_id": doc_id, "error": str(exc)})
                doc_summaries.append({
                    "document_id": doc_id,
                    "asset_count": 0,
                    "status": "failed",
                })

        completed_at = generated_at or _utc_now()
        config_hash = f"sha256:{hashlib.sha256(f'{EXTRACTOR_NAME}:{EXTRACTOR_VERSION}'.encode('utf-8')).hexdigest()}"

        batch_manifest = AssetExtractionManifest(
            batch_id=batch_id,
            extractor_name=EXTRACTOR_NAME,
            extractor_version=EXTRACTOR_VERSION,
            config_hash=config_hash,
            documents=doc_summaries,
            total_assets=total_assets,
            total_unique_contents=total_unique_contents,
            status_counts=agg_status,
            started_at=started_at,
            completed_at=completed_at,
            failures=failures,
        )

        batch_artifact = self.artifact_store.put_json(
            batch_manifest.to_dict(),
            producer_step_id=producer_step_id,
            schema_version=SCHEMA_EXTRACTION_MANIFEST,
        )

        return doc_manifests, batch_manifest, batch_artifact
