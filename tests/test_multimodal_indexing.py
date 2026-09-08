"""Comprehensive test suite for P2.1 Multimodal Image Embedding and Indexing."""

import hashlib
import json
import math
from pathlib import Path

import pytest

from conflux_weave.api_contracts import (
    MultimodalIndexManifestResponse,
    MultimodalRetrievalHitResponse,
)
from conflux_weave.document_assets import BoundingBox, DocumentAsset, PDFAssetExtractor
from conflux_weave.indexing import LanceDBDenseIndex
from conflux_weave.multimodal_indexing import (
    DeterministicImageEmbeddingAdapter,
    ImageEmbeddingPort,
    ImageEmbeddingRequest,
    ImageEmbeddingResult,
    LanceDBImageIndex,
    MultimodalIndexManifest,
    MultimodalIndexRecord,
    MultimodalRetrievalHit,
    OpenAICompatibleImageEmbeddingAdapter,
    build_image_index,
    cosine_similarity,
)
from conflux_weave.provider import (
    ProviderConfig,
    ProviderHttpResponse,
    ProviderHttpTransport,
    ProviderPortError,
)
from conflux_weave.retrieval import RetrievalDocument
from conflux_weave.runtime import LocalArtifactStore


class MockTransport(ProviderHttpTransport):
    def __init__(self, handler):
        self.handler = handler
        self.calls = []

    def post(self, url, headers=None, body=None, timeout_seconds=60):
        self.calls.append({"url": url, "headers": headers, "body": body})
        return self.handler(url, headers, body)


def _create_test_image_bytes(color: tuple[int, int, int] = (255, 0, 0)) -> bytes:
    import fitz

    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 16, 16), 0)
    for y in range(16):
        for x in range(16):
            pix.set_pixel(x, y, color)
    png_bytes = pix.tobytes("png")
    return png_bytes


def _create_minimal_pdf_with_image() -> bytes:
    import fitz

    doc = fitz.open()
    page = doc.new_page(width=300, height=300)
    png_bytes = _create_test_image_bytes((0, 128, 255))
    rect = fitz.Rect(40, 40, 160, 160)
    page.insert_image(rect, stream=png_bytes)
    page.insert_text((40, 180), "Figure 1: Test architecture overview.")
    pdf_bytes = doc.tobytes()
    doc.close()
    return pdf_bytes


# =========================================================================
# 1. ImageEmbeddingRequest & Validation
# =========================================================================


def test_image_embedding_request_validation():
    img_bytes = _create_test_image_bytes()
    req1 = ImageEmbeddingRequest(asset_id="asset-1", image_bytes=img_bytes, media_type="image/png")
    assert req1.asset_id == "asset-1"
    assert req1.image_bytes == img_bytes
    assert req1.media_type == "image/png"

    req2 = ImageEmbeddingRequest(text="architecture diagram")
    assert req2.text == "architecture diagram"
    assert req2.image_bytes is None

    # Must provide either image_bytes or non-empty text
    with pytest.raises(ValueError, match="either image_bytes or non-empty text"):
        ImageEmbeddingRequest()

    with pytest.raises(ValueError, match="either image_bytes or non-empty text"):
        ImageEmbeddingRequest(text="   ")


# =========================================================================
# 2. DeterministicImageEmbeddingAdapter
# =========================================================================


def test_deterministic_adapter_vectors_and_artifacts(tmp_path: Path):
    store = LocalArtifactStore(tmp_path / "artifacts")
    adapter = DeterministicImageEmbeddingAdapter(store, dimensions=64)
    assert adapter.dimensions == 64
    assert adapter.model == "deterministic-multimodal-v1"

    img_red = _create_test_image_bytes((255, 0, 0))
    img_blue = _create_test_image_bytes((0, 0, 255))

    reqs = [
        ImageEmbeddingRequest(asset_id="asset-red", image_bytes=img_red, text="Figure 1: Red"),
        ImageEmbeddingRequest(asset_id="asset-blue", image_bytes=img_blue, text="Figure 2: Blue"),
    ]

    result = adapter.embed_images(reqs, producer_step_id="step-test-embed")
    assert isinstance(result, ImageEmbeddingResult)
    assert result.model == "deterministic-multimodal-v1"
    assert len(result.vectors) == 2
    assert result.dimensions == 64
    assert result.latency_ms >= 0.0

    # Assert L2 normalization for each vector
    for vec in result.vectors:
        assert len(vec) == 64
        norm = math.sqrt(sum(v * v for v in vec))
        assert math.isclose(norm, 1.0, rel_tol=1e-5)

    # Distinct inputs produce distinct vectors
    sim = cosine_similarity(result.vectors[0], result.vectors[1])
    assert sim < 0.99

    # Determinism: same input gives identical vector
    repeat_res = adapter.embed_images([reqs[0]], producer_step_id="step-test-repeat")
    assert repeat_res.vectors[0] == result.vectors[0]

    # Artifact persistence
    assert result.request_artifact.artifact_id.startswith("artifact-sha256-")
    assert result.response_artifact.artifact_id.startswith("artifact-sha256-")
    req_data = json.loads(store.read_bytes_by_id(result.request_artifact.artifact_id))
    assert req_data["schema_version"] == "conflux-weave.image-embedding.v1"
    assert len(req_data["items"]) == 2
    resp_data = json.loads(store.read_bytes_by_id(result.response_artifact.artifact_id))
    assert resp_data["schema_version"] == "conflux-weave.image-embedding.v1.response"
    assert len(resp_data["vectors"]) == 2


def test_deterministic_adapter_query_text_embedding(tmp_path: Path):
    store = LocalArtifactStore(tmp_path / "artifacts")
    adapter = DeterministicImageEmbeddingAdapter(store, dimensions=64)

    query_res = adapter.embed_query_text("pipeline workflow diagram")
    assert len(query_res.vectors) == 1
    assert len(query_res.vectors[0]) == 64
    norm = math.sqrt(sum(v * v for v in query_res.vectors[0]))
    assert math.isclose(norm, 1.0, rel_tol=1e-5)

    with pytest.raises(ValueError, match="query must not be empty"):
        adapter.embed_query_text("   ")


# =========================================================================
# 3. OpenAICompatibleImageEmbeddingAdapter
# =========================================================================


def test_openai_compatible_adapter_success_and_artifacts(tmp_path: Path):
    store = LocalArtifactStore(tmp_path / "artifacts")
    config = ProviderConfig(base_url="https://api.example.com/v1", api_key="sk-test", model="gpt-4o")

    def mock_handler(url, headers, body):
        payload = json.loads(body.decode())
        count = len(payload["input"])
        data = [{"index": i, "embedding": [0.1 * (i + 1)] * 32} for i in range(count)]
        resp_body = json.dumps({"data": data, "usage": {"prompt_tokens": 128}}).encode()
        return ProviderHttpResponse(200, resp_body, {"Content-Type": "application/json"})

    transport = MockTransport(mock_handler)
    adapter = OpenAICompatibleImageEmbeddingAdapter(
        store, config, model="openai/clip-test", dimensions=32, transport=transport
    )

    img = _create_test_image_bytes()
    reqs = [
        ImageEmbeddingRequest(asset_id="asset-1", image_bytes=img),
        ImageEmbeddingRequest(asset_id="asset-2", text="A caption"),
    ]
    result = adapter.embed_images(reqs)

    assert result.model == "openai/clip-test"
    assert len(result.vectors) == 2
    assert result.dimensions == 32
    assert result.input_tokens == 128
    assert len(transport.calls) == 1
    assert transport.calls[0]["url"] == "https://api.example.com/v1/embeddings"


def test_openai_compatible_adapter_http_error(tmp_path: Path):
    store = LocalArtifactStore(tmp_path / "artifacts")
    config = ProviderConfig(base_url="https://api.example.com/v1", api_key="sk-test", model="gpt-4o")

    def error_handler(url, headers, body):
        return ProviderHttpResponse(502, b'{"error": "Bad Gateway"}', {})

    transport = MockTransport(error_handler)
    adapter = OpenAICompatibleImageEmbeddingAdapter(store, config, transport=transport)

    img = _create_test_image_bytes()
    with pytest.raises(ProviderPortError) as exc_info:
        adapter.embed_images([ImageEmbeddingRequest(asset_id="a1", image_bytes=img)])

    err = exc_info.value
    assert err.code == "embedding_provider_failed"
    assert err.status_code == 502
    assert err.request_artifact_ref.startswith("artifact-sha256-")
    assert err.response_artifact_ref.startswith("artifact-sha256-")


def test_openai_compatible_adapter_transport_network_error_generates_failure_artifact(tmp_path: Path):
    store = LocalArtifactStore(tmp_path / "artifacts")
    config = ProviderConfig(base_url="https://api.example.com/v1", api_key="sk-test", model="gpt-4o")

    def network_timeout_handler(url, headers, body):
        raise TimeoutError("connection to embedding provider timed out")

    transport = MockTransport(network_timeout_handler)
    adapter = OpenAICompatibleImageEmbeddingAdapter(store, config, transport=transport)

    img = _create_test_image_bytes()
    with pytest.raises(ProviderPortError) as exc_info:
        adapter.embed_images([ImageEmbeddingRequest(asset_id="a1", image_bytes=img)])

    err = exc_info.value
    assert err.code == "embedding_provider_failed"
    assert "network error" in err.message.lower()
    assert err.request_artifact_ref.startswith("artifact-sha256-")
    assert err.response_artifact_ref.startswith("artifact-sha256-")

    # Read the failure artifact and verify structured schema
    failure_bytes = store.read_bytes_by_id(err.response_artifact_ref)
    failure_payload = json.loads(failure_bytes)
    assert failure_payload["schema_version"] == "conflux-weave.image-embedding.v1.failure"
    assert "TimeoutError" in failure_payload["error_type"]
    assert "timed out" in failure_payload["error"]
    assert failure_payload["request_artifact_ref"] == err.request_artifact_ref


def test_openai_compatible_adapter_contract_mismatch(tmp_path: Path):
    store = LocalArtifactStore(tmp_path / "artifacts")
    config = ProviderConfig(base_url="https://api.example.com/v1", api_key="sk-test", model="gpt-4o")

    # Returns 1 vector when 2 requested
    def bad_count_handler(url, headers, body):
        data = [{"index": 0, "embedding": [0.5] * 16}]
        return ProviderHttpResponse(200, json.dumps({"data": data}).encode(), {"Content-Type": "application/json"})

    transport = MockTransport(bad_count_handler)
    adapter = OpenAICompatibleImageEmbeddingAdapter(store, config, transport=transport)

    img = _create_test_image_bytes()
    reqs = [
        ImageEmbeddingRequest(asset_id="a1", image_bytes=img),
        ImageEmbeddingRequest(asset_id="a2", image_bytes=img),
    ]
    with pytest.raises(ProviderPortError) as exc_info:
        adapter.embed_images(reqs)
    assert exc_info.value.code == "embedding_provider_failed"


# =========================================================================
# 4. MultimodalIndexRecord Validation
# =========================================================================


def test_multimodal_index_record_validation():
    valid = MultimodalIndexRecord(
        asset_id="asset-1",
        document_id="doc-1",
        source_snapshot_id="snap-1",
        page=1,
        parent_chunk_ids=("chunk-1",),
        locator_json="{}",
        artifact_ref="artifact-sha256-abc",
        embedding_model="model-v1",
        dimensions=3,
        vector=(1.0, 0.0, 0.0),
        extractor_version="pymupdf-v1",
        corpus_hash="hash-1",
        config_hash="cfg-1",
    )
    assert valid.asset_id == "asset-1"
    assert valid.modality == "image"

    # Dimension mismatch
    with pytest.raises(ValueError, match="does not match dimensions"):
        MultimodalIndexRecord(
            asset_id="asset-1",
            document_id="doc-1",
            source_snapshot_id="snap-1",
            page=1,
            parent_chunk_ids=(),
            locator_json="{}",
            artifact_ref="artifact-sha256-abc",
            embedding_model="model-v1",
            dimensions=4,
            vector=(1.0, 0.0, 0.0),
            extractor_version="pymupdf-v1",
            corpus_hash="hash-1",
            config_hash="cfg-1",
        )

    # Empty asset_id
    with pytest.raises(ValueError, match="asset_id must not be empty"):
        MultimodalIndexRecord(
            asset_id="  ",
            document_id="doc-1",
            source_snapshot_id="snap-1",
            page=1,
            parent_chunk_ids=(),
            locator_json="{}",
            artifact_ref="artifact-sha256-abc",
            embedding_model="model-v1",
            dimensions=3,
            vector=(1.0, 0.0, 0.0),
            extractor_version="pymupdf-v1",
            corpus_hash="hash-1",
            config_hash="cfg-1",
        )


# =========================================================================
# 5. LanceDBImageIndex Publish, Staging, & Manifest
# =========================================================================


def _setup_store_and_records(tmp_path: Path, count: int = 3, dims: int = 4):
    store = LocalArtifactStore(tmp_path / "artifacts")
    records: list[MultimodalIndexRecord] = []
    for i in range(count):
        raw = _create_test_image_bytes((i * 40, 100, 200))
        art = store.put_bytes(
            raw,
            media_type="image/png",
            producer_step_id="step-test",
            schema_version="conflux-weave.image-asset.v1",
        )
        vec = [0.0] * dims
        vec[i % dims] = 1.0
        rec = MultimodalIndexRecord(
            asset_id=f"asset-{i + 1}",
            document_id=f"doc-{i + 1}",
            source_snapshot_id=f"snap-{i + 1}",
            page=i + 1,
            parent_chunk_ids=(f"chunk-{i + 1}",),
            locator_json=json.dumps({"page": i + 1, "bbox": {"x": 10.0, "y": 20.0, "width": 100.0, "height": 80.0}}),
            artifact_ref=art.artifact_id,
            embedding_model="test-model",
            dimensions=dims,
            vector=tuple(vec),
            extractor_version="pymupdf-v1",
            corpus_hash="chash",
            config_hash="cfghash",
            caption=f"Figure {i + 1}: Caption text",
        )
        records.append(rec)
    return store, records


def test_lancedb_image_index_publish_staging_and_manifest(tmp_path: Path):
    store, records = _setup_store_and_records(tmp_path, count=3, dims=4)
    index = LanceDBImageIndex(tmp_path / "lancedb", table_name="image_assets_v1", artifact_store=store)

    manifest = index.publish(records)
    assert isinstance(manifest, MultimodalIndexManifest)
    assert manifest.status == "published"
    assert manifest.table_name == "image_assets_v1"
    assert manifest.asset_count == 3
    assert manifest.dimensions == 4
    assert manifest.index_artifact_id is not None
    assert manifest.index_artifact_id.startswith("artifact-sha256-")

    # Manifest payload in store
    m_data = json.loads(store.read_bytes_by_id(manifest.index_artifact_id))
    assert m_data["schema_version"] == "conflux-weave.multimodal-index-manifest.v1"
    assert m_data["asset_count"] == 3

    # Staging table is removed, published table exists
    table_names = index._table_names()
    assert "image_assets_v1" in table_names
    assert "image_assets_v1__building" not in table_names


def test_lancedb_image_index_artifact_integrity_failure(tmp_path: Path):
    store, records = _setup_store_and_records(tmp_path, count=2, dims=4)
    # Corrupt one record's artifact_ref to point to missing digest
    bad_rec = MultimodalIndexRecord(
        asset_id="asset-corrupt",
        document_id="doc-c",
        source_snapshot_id="snap-c",
        page=1,
        parent_chunk_ids=(),
        locator_json="{}",
        artifact_ref="artifact-sha256-0000000000000000000000000000000000000000000000000000000000000000",
        embedding_model="test-model",
        dimensions=4,
        vector=(1.0, 0.0, 0.0, 0.0),
        extractor_version="pymupdf-v1",
        corpus_hash="chash",
        config_hash="cfghash",
    )
    records.append(bad_rec)

    index = LanceDBImageIndex(tmp_path / "lancedb", table_name="image_assets_v1", artifact_store=store)
    with pytest.raises(ValueError, match="artifact_integrity_failed"):
        index.publish(records)

    # Neither published table nor staging table remains
    assert "image_assets_v1" not in index._table_names()
    assert "image_assets_v1__building" not in index._table_names()


def test_lancedb_image_index_dimension_mismatch_fails_fast(tmp_path: Path):
    store = LocalArtifactStore(tmp_path / "artifacts")
    raw = _create_test_image_bytes()
    art = store.put_bytes(raw, media_type="image/png", producer_step_id="test", schema_version="test.v1")

    rec1 = MultimodalIndexRecord(
        asset_id="asset-1", document_id="d1", source_snapshot_id="s1", page=1,
        parent_chunk_ids=(), locator_json="{}", artifact_ref=art.artifact_id,
        embedding_model="m", dimensions=4, vector=(1.0, 0.0, 0.0, 0.0),
        extractor_version="v1", corpus_hash="h", config_hash="c",
    )
    rec2 = MultimodalIndexRecord(
        asset_id="asset-2", document_id="d2", source_snapshot_id="s2", page=2,
        parent_chunk_ids=(), locator_json="{}", artifact_ref=art.artifact_id,
        embedding_model="m", dimensions=8, vector=(0.5,) * 8,
        extractor_version="v1", corpus_hash="h", config_hash="c",
    )

    index = LanceDBImageIndex(tmp_path / "lancedb", table_name="image_assets_v1", artifact_store=store)
    with pytest.raises(ValueError, match="embedding_dimension_mismatch"):
        index.publish([rec1, rec2])

    assert "image_assets_v1" not in index._table_names()


# =========================================================================
# 6. Incremental Add and Delete
# =========================================================================


def test_lancedb_image_index_incremental_add_and_delete(tmp_path: Path):
    store, records = _setup_store_and_records(tmp_path, count=2, dims=4)
    index = LanceDBImageIndex(tmp_path / "lancedb", table_name="image_assets_v1", artifact_store=store)

    index.publish([records[0]])
    assert len(index.table) == 1

    # Append second record
    add_res = index.add([records[1]])
    assert add_res["added_count"] == 1
    assert len(index.table) == 2

    # Attempt to append record with wrong dimension
    bad_rec = MultimodalIndexRecord(
        asset_id="asset-bad", document_id="d", source_snapshot_id="s", page=3,
        parent_chunk_ids=(), locator_json="{}", artifact_ref=records[0].artifact_ref,
        embedding_model="m", dimensions=16, vector=(0.25,) * 16,
        extractor_version="v1", corpus_hash="h", config_hash="c",
    )
    with pytest.raises(ValueError, match="embedding_dimension_mismatch"):
        index.add([bad_rec])

    # Delete asset-1
    del_res = index.delete(["asset-1"])
    assert del_res["deleted_count"] == 1
    assert len(index.table) == 1

    # Remaining record is asset-2
    hits = index.search_vector(records[1].vector, top_k=2)
    assert len(hits) == 1
    assert hits[0].asset_id == "asset-2"


# =========================================================================
# 7. Vector Search & MultimodalRetrievalHit Contract
# =========================================================================


def test_lancedb_image_index_vector_search(tmp_path: Path):
    store, records = _setup_store_and_records(tmp_path, count=3, dims=4)
    index = LanceDBImageIndex(tmp_path / "lancedb", table_name="image_assets_v1", artifact_store=store)
    index.publish(records)

    # Query with exact vector of asset-2 (0.0, 1.0, 0.0, 0.0)
    query_vec = (0.0, 1.0, 0.0, 0.0)
    hits = index.search_vector(query_vec, top_k=2)

    assert len(hits) == 2
    top_hit = hits[0]
    assert isinstance(top_hit, MultimodalRetrievalHit)
    assert top_hit.asset_id == "asset-2"
    assert math.isclose(top_hit.score, 1.0, rel_tol=1e-4)
    assert top_hit.rank == 1
    assert top_hit.modality == "image"
    assert top_hit.page == 2
    assert top_hit.document_id == "doc-2"
    assert top_hit.source_snapshot_id == "snap-2"
    assert top_hit.parent_chunk_ids == ("chunk-2",)
    assert top_hit.caption == "Figure 2: Caption text"
    assert top_hit.bbox == {"x": 10.0, "y": 20.0, "width": 100.0, "height": 80.0}
    assert top_hit.coordinate_space == "pdf_page_points_top_left"
    assert top_hit.artifact_ref.startswith("artifact-sha256-")

    # Validate against Pydantic response contract
    response_model = MultimodalRetrievalHitResponse(
        asset_id=top_hit.asset_id,
        score=top_hit.score,
        rank=top_hit.rank,
        modality=top_hit.modality,
        source_snapshot_id=top_hit.source_snapshot_id,
        document_id=top_hit.document_id,
        page=top_hit.page,
        bbox=top_hit.bbox,
        coordinate_space=top_hit.coordinate_space,
        parent_chunk_ids=top_hit.parent_chunk_ids,
        caption=top_hit.caption,
        artifact_ref=top_hit.artifact_ref,
        thumbnail_artifact_ref=top_hit.thumbnail_artifact_ref,
        embedding_model=top_hit.embedding_model,
        index_version=top_hit.index_version,
        locator=top_hit.locator,
    )
    assert response_model.asset_id == "asset-2"


# =========================================================================
# 8. build_image_index Pipeline & Text Table Physical Isolation
# =========================================================================


def test_build_image_index_pipeline_and_isolation(tmp_path: Path):
    store = LocalArtifactStore(tmp_path / "artifacts")
    adapter = DeterministicImageEmbeddingAdapter(store, dimensions=64)

    # 1. Generate PDF and extract assets
    pdf_bytes = _create_minimal_pdf_with_image()
    pdf_art = store.put_bytes(
        pdf_bytes,
        media_type="application/pdf",
        producer_step_id="step-test-import",
        schema_version="conflux-weave.source-document.v1",
    )
    doc_id = "doc-pdf-pipeline-test"
    extractor = PDFAssetExtractor(store)
    manifest, _ = extractor.extract_document_assets(
        pdf_bytes,
        document_id=doc_id,
        source_snapshot_id=doc_id,
        source_artifact_id=pdf_art.artifact_id,
        parent_segments=(),
    )
    assert manifest.asset_count >= 1

    # 2. Existing text index in the same database (chunks table)
    lancedb_dir = tmp_path / "lancedb"
    text_index = LanceDBDenseIndex(lancedb_dir, table_name="chunks")
    text_docs = (
        RetrievalDocument("chunk-1", "Text paragraph one", "snap-1", {"page": 1}),
        RetrievalDocument("chunk-2", "Text paragraph two", "snap-1", {"page": 2}),
    )
    text_manifest = text_index.publish(text_docs, ((1.0, 0.0), (0.0, 1.0)))
    assert text_manifest["index_type"] == "lancedb"

    # 3. Build image index into image_assets_v1
    image_index = LanceDBImageIndex(lancedb_dir, table_name="image_assets_v1", artifact_store=store)
    img_manifest = build_image_index(
        manifest.assets, adapter, store, image_index, batch_size=2
    )
    assert img_manifest.asset_count == manifest.asset_count
    assert img_manifest.dimensions == 64

    # 4. Physical isolation check: both tables exist independently
    table_names = image_index._table_names()
    assert "chunks" in table_names
    assert "image_assets_v1" in table_names

    # 5. Text search still works completely intact
    text_res = text_index.search((1.0, 0.0), top_k=1)
    assert text_res.hits[0].document_id == "chunk-1"

    # 6. Image search retrieves the extracted PDF image asset
    query_res = adapter.embed_query_text("architecture overview")
    image_hits = image_index.search_vector(query_res.vectors[0], top_k=1)
    assert len(image_hits) == 1
    assert image_hits[0].document_id == doc_id
    assert image_hits[0].page == 1
    assert image_hits[0].bbox is not None
    assert image_hits[0].artifact_ref.startswith("artifact-sha256-")

    # 7. Audit trail check: verify batch_audits recorded in manifest
    assert len(img_manifest.batch_audits) >= 1
    first_batch = img_manifest.batch_audits[0]
    assert "request_artifact_id" in first_batch
    assert "response_artifact_id" in first_batch
    assert "latency_ms" in first_batch
    assert first_batch["request_artifact_id"].startswith("artifact-sha256-")
    assert first_batch["response_artifact_id"].startswith("artifact-sha256-")


def test_lancedb_image_index_atomic_publish_rollback_on_failure(tmp_path: Path, monkeypatch):
    """Verify that if publish fails while an index already exists, the old index is preserved."""
    store, records = _setup_store_and_records(tmp_path, count=2, dims=4)
    index = LanceDBImageIndex(tmp_path / "lancedb", table_name="image_assets_v1", artifact_store=store)

    # 1. Successful initial publish
    initial_manifest = index.publish(records)
    assert initial_manifest.asset_count == 2
    assert len(index.table) == 2
    orig_hit = index.search_vector(records[0].vector, top_k=1)[0]
    assert orig_hit.asset_id == "asset-1"

    # 2. Attempt publish with a simulated failure during table creation/swap
    original_create_table = index.db.create_table
    failed_once = False

    def failing_create_table(name, *args, **kwargs):
        nonlocal failed_once
        # Allow staging and backup table creation, but fail on the target table swap once!
        if name == "image_assets_v1" and not failed_once:
            failed_once = True
            raise RuntimeError("simulated disk full or arrow conversion error during table swap")
        return original_create_table(name, *args, **kwargs)

    monkeypatch.setattr(index.db, "create_table", failing_create_table)

    # 3. Try to publish new records, which must fail
    new_records = records[:1]
    with pytest.raises(RuntimeError, match="simulated disk full"):
        index.publish(new_records)

    # 4. Verify that the previous published table is NOT destroyed and STILL HAS 2 records!
    monkeypatch.undo()
    assert "image_assets_v1" in index._table_names()
    assert "image_assets_v1__building" not in index._table_names()
    assert "image_assets_v1__backup" not in index._table_names()

    # Table can still be opened and queried, retaining the previous version
    table = index.db.open_table("image_assets_v1")
    assert len(table) == 2
    hits_after_failure = index.search_vector(records[0].vector, top_k=1)
    assert len(hits_after_failure) == 1
    assert hits_after_failure[0].asset_id == "asset-1"
