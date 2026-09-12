"""Unit and integration tests for Phase P2.2: 有限跨模态检索与 Fusion."""

import json
import os
from pathlib import Path
import pytest

from conflux_weave.api_contracts import (
    MultimodalFusionHitResponse,
    MultimodalRetrievalResultResponse,
)
from conflux_weave.document_assets import DocumentAsset
from conflux_weave.evidence.contracts import Citation, Claim, EvidenceRef
from conflux_weave.evidence.delivery import render_report_document
from conflux_weave.hybrid_retrieval import HybridRetrievalPipeline
from conflux_weave.indexing import LanceDBDenseIndex
from conflux_weave.multimodal_indexing import (
    DeterministicImageEmbeddingAdapter,
    ImageEmbeddingRequest,
    LanceDBImageIndex,
    MultimodalRetrievalHit,
    build_image_index,
)
from conflux_weave.multimodal_retrieval import (
    MULTIMODAL_ENV_FLAG,
    MultimodalRetrievalPipeline,
    MultimodalRetrievalRun,
    is_multimodal_env_enabled,
)
from conflux_weave.provider import (
    OpenAICompatibleChatAdapter,
    OpenAICompatibleEmbeddingAdapter,
    OpenAICompatibleRerankerAdapter,
    ProviderConfig,
    ProviderHttpResponse,
)
from conflux_weave.research_agents import VerifiedResearchWorkflow
from conflux_weave.retrieval import (
    MultimodalFusionHit,
    RetrievalDocument,
    RetrievalHit,
    RetrievalQueryResult,
    RetrievalStrategy,
    multimodal_reciprocal_rank_fusion,
)
from conflux_weave.runtime import LocalArtifactStore


class SequenceTransport:
    def __init__(self, payloads):
        self.payloads = iter(payloads)
        self.requests = []

    def post(self, *args, **kwargs):
        self.requests.append(json.loads(kwargs["body"]))
        payload = next(self.payloads)
        return ProviderHttpResponse(
            200, json.dumps(payload).encode(), {"Content-Type": "application/json"}
        )


def chat_response(content, response_id):
    return {
        "id": response_id,
        "model": "fixture-chat",
        "choices": [
            {
                "message": {"content": json.dumps(content)},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 10,
            "completion_tokens": 10,
            "total_tokens": 20,
        },
    }


def test_multimodal_reciprocal_rank_fusion_formula():
    """Verify RRF score calculation, tie breaking, and metadata preservation."""
    text_hits = (
        RetrievalHit("chunk-1", 0.95, 1, "snap-1", {"page": 2}),
        RetrievalHit("chunk-2", 0.85, 2, "snap-1", {"page": 3}),
    )
    image_hits = (
        MultimodalRetrievalHit(
            asset_id="asset-1",
            score=0.92,
            rank=1,
            modality="image",
            source_snapshot_id="snap-1",
            document_id="doc-1",
            page=2,
            bbox={"x": 50.0, "y": 100.0, "width": 200.0, "height": 150.0},
            coordinate_space="pdf_page_points_top_left",
            parent_chunk_ids=("chunk-1",),
            caption="Figure 1: Transformer model architecture.",
            artifact_ref="artifact-sha256-img01",
            thumbnail_artifact_ref="artifact-sha256-thumb01",
            embedding_model="test-embed-v1",
            index_version="conflux-weave.multimodal-index.v1",
            locator={"page": 2, "bbox": {"x": 50.0, "y": 100.0, "width": 200.0, "height": 150.0}},
        ),
        MultimodalRetrievalHit(
            asset_id="asset-2",
            score=0.81,
            rank=2,
            modality="image",
            source_snapshot_id="snap-1",
            document_id="doc-1",
            page=5,
            bbox=None,
            coordinate_space="pdf_page_points_top_left",
            parent_chunk_ids=(),
            caption="Figure 2: Attention heatmap.",
            artifact_ref="artifact-sha256-img02",
            thumbnail_artifact_ref=None,
            embedding_model="test-embed-v1",
            index_version="conflux-weave.multimodal-index.v1",
            locator={"page": 5},
        ),
    )

    text_by_id = {
        "chunk-1": "Full text of chunk 1 describing self-attention.",
        "chunk-2": "Full text of chunk 2 describing positional encoding.",
    }

    fused = multimodal_reciprocal_rank_fusion(
        text_hits,
        image_hits,
        text_by_id=text_by_id,
        top_k=4,
        k=60,
    )

    assert len(fused) == 4
    # Rank 1 text and Rank 1 image both have score 1 / (60 + 1) = 1/61
    expected_r1_score = 1.0 / 61.0
    expected_r2_score = 1.0 / 62.0

    # Tied score items are tie-broken by hit_id ascending: "asset-1" < "chunk-1"
    assert fused[0].hit_id == "asset-1"
    assert fused[0].score == pytest.approx(expected_r1_score)
    assert fused[0].rank == 1
    assert fused[0].modality == "image"
    assert fused[0].text == "Figure 1: Transformer model architecture."
    assert fused[0].artifact_ref == "artifact-sha256-img01"
    assert fused[0].parent_chunk_ids == ("chunk-1",)

    assert fused[1].hit_id == "chunk-1"
    assert fused[1].score == pytest.approx(expected_r1_score)
    assert fused[1].rank == 2
    assert fused[1].modality == "text"
    assert fused[1].text == "Full text of chunk 1 describing self-attention."
    assert fused[1].page == 2

    # Tied score items for rank 2: "asset-2" < "chunk-2"
    assert fused[2].hit_id == "asset-2"
    assert fused[2].score == pytest.approx(expected_r2_score)
    assert fused[2].rank == 3
    assert fused[2].modality == "image"

    assert fused[3].hit_id == "chunk-2"
    assert fused[3].score == pytest.approx(expected_r2_score)
    assert fused[3].rank == 4
    assert fused[3].modality == "text"


def test_multimodal_reciprocal_rank_fusion_weights_and_limits():
    """Verify custom weights and parameter boundary conditions."""
    text_hits = (RetrievalHit("chunk-1", 0.9, 1, "snap-1", {}),)
    image_hits = (
        MultimodalRetrievalHit(
            asset_id="asset-1",
            score=0.9,
            rank=1,
            modality="image",
            source_snapshot_id="snap-1",
            document_id="doc-1",
            page=1,
            bbox=None,
            coordinate_space="pdf_page_points_top_left",
            parent_chunk_ids=(),
            caption="Cap",
            artifact_ref="art-1",
            thumbnail_artifact_ref=None,
            embedding_model="test-embed-v1",
            index_version="conflux-weave.multimodal-index.v1",
            locator={},
        ),
    )

    # With text_weight=2.0, image_weight=1.0, chunk-1 must rank higher than asset-1
    fused = multimodal_reciprocal_rank_fusion(
        text_hits,
        image_hits,
        text_weight=2.0,
        image_weight=1.0,
        top_k=5,
    )
    assert len(fused) == 2
    assert fused[0].hit_id == "chunk-1"
    assert fused[0].score == pytest.approx(2.0 / 61.0)
    assert fused[1].hit_id == "asset-1"
    assert fused[1].score == pytest.approx(1.0 / 61.0)

    # Test top_k limiting
    limited = multimodal_reciprocal_rank_fusion(
        text_hits, image_hits, top_k=1
    )
    assert len(limited) == 1

    # Test input validation errors
    with pytest.raises(ValueError, match="top_k and k must be positive"):
        multimodal_reciprocal_rank_fusion(text_hits, image_hits, top_k=0)
    with pytest.raises(ValueError, match="top_k and k must be positive"):
        multimodal_reciprocal_rank_fusion(text_hits, image_hits, k=-1)
    with pytest.raises(ValueError, match="text_weight and image_weight must be positive"):
        multimodal_reciprocal_rank_fusion(text_hits, image_hits, text_weight=0)


def test_multimodal_reciprocal_rank_fusion_single_modality():
    """Verify degradation when one modality is empty."""
    text_hits = (RetrievalHit("chunk-1", 0.9, 1, "snap-1", {}),)
    fused_text = multimodal_reciprocal_rank_fusion(text_hits, ())
    assert len(fused_text) == 1
    assert fused_text[0].modality == "text"

    image_hits = (
        MultimodalRetrievalHit(
            asset_id="asset-1",
            score=0.9,
            rank=1,
            modality="image",
            source_snapshot_id="snap-1",
            document_id="doc-1",
            page=1,
            bbox=None,
            coordinate_space="pdf_page_points_top_left",
            parent_chunk_ids=(),
            caption=None,
            artifact_ref="art-1",
            thumbnail_artifact_ref=None,
            embedding_model="test-embed-v1",
            index_version="conflux-weave.multimodal-index.v1",
            locator={},
        ),
    )
    fused_image = multimodal_reciprocal_rank_fusion((), image_hits)
    assert len(fused_image) == 1
    assert fused_image[0].modality == "image"


def _create_test_pipeline(tmp_path):
    store = LocalArtifactStore(tmp_path / "artifacts")
    config = ProviderConfig("https://provider.example/v1", "secret", "chat")

    # Artifacts for images
    img1_art = store.put_bytes(b"\x89PNG\r\n\x1a\n\x00img1", media_type="image/png", producer_step_id="test", schema_version="raw-binary.v1")
    img2_art = store.put_bytes(b"\x89PNG\r\n\x1a\n\x00img2", media_type="image/png", producer_step_id="test", schema_version="raw-binary.v1")

    # DocumentAssets
    asset1 = DocumentAsset(
        asset_id="asset-transformer-fig1",
        document_id="paper-attn",
        source_snapshot_id="snapshot-paper-attn",
        page=3,
        asset_kind="embedded_image",
        media_type="image/png",
        content_hash=img1_art.content_hash,
        width_px=600,
        height_px=400,
        bbox={"x": 72.0, "y": 144.0, "width": 450.0, "height": 300.0},
        coordinate_space="pdf_page_points_top_left",
        page_width=612.0,
        page_height=792.0,
        page_rotation=0,
        caption="Figure 1: The Transformer model architecture.",
        parent_segment_ids=("chunk-attn-1",),
        extraction_method="pdf_image_xobject",
        extraction_status="extracted",
        artifact_ref=img1_art.artifact_id,
    )
    asset2 = DocumentAsset(
        asset_id="asset-heatmap-fig2",
        document_id="paper-attn",
        source_snapshot_id="snapshot-paper-attn",
        page=5,
        asset_kind="embedded_image",
        media_type="image/png",
        content_hash=img2_art.content_hash,
        width_px=500,
        height_px=500,
        bbox={"x": 50.0, "y": 80.0, "width": 400.0, "height": 400.0},
        coordinate_space="pdf_page_points_top_left",
        page_width=612.0,
        page_height=792.0,
        page_rotation=0,
        caption="Figure 2: Multi-head attention visualizer.",
        parent_segment_ids=("chunk-attn-2",),
        extraction_method="pdf_image_xobject",
        extraction_status="extracted",
        artifact_ref=img2_art.artifact_id,
    )

    # Image index & deterministic adapter (128 dims)
    image_adapter = DeterministicImageEmbeddingAdapter(store, dimensions=128)
    image_index = LanceDBImageIndex(tmp_path / "img_db", artifact_store=store)
    build_image_index([asset1, asset2], image_adapter, store, image_index)

    # Text documents & dense index (128 dims so image query can also query dense text)
    documents = (
        RetrievalDocument(
            "chunk-attn-1",
            "The Transformer uses stacked self-attention and point-wise fully connected layers.",
            "snapshot-paper-attn",
            {"page": 3},
        ),
        RetrievalDocument(
            "chunk-attn-2",
            "Multi-head attention allows the model to jointly attend to information at different positions.",
            "snapshot-paper-attn",
            {"page": 5},
        ),
    )
    text_index = LanceDBDenseIndex(tmp_path / "text_db")
    # Generate 128-dim vectors matching the documents
    v1 = image_adapter._generate_vector(b"chunk-attn-1", "Transformer stacked self-attention")
    v2 = image_adapter._generate_vector(b"chunk-attn-2", "Multi-head attention different positions")
    text_index.publish(documents, (v1, v2))

    # Text pipeline
    text_pipeline = HybridRetrievalPipeline(
        documents,
        text_index,
        OpenAICompatibleEmbeddingAdapter(
            store,
            config,
            transport=SequenceTransport([{"data": [{"index": 0, "embedding": list(v1)}]} for _ in range(20)]),
        ),
        OpenAICompatibleRerankerAdapter(
            store,
            config,
            transport=SequenceTransport([{"results": [{"index": 0, "relevance_score": 0.95}, {"index": 1, "relevance_score": 0.75}]} for _ in range(20)]),
        ),
    )

    pipeline = MultimodalRetrievalPipeline(
        text_pipeline,
        image_index=image_index,
        image_embedding=image_adapter,
        artifact_store=store,
        enabled=True,
    )
    return pipeline, store, asset1, asset2


def test_search_images_by_text(tmp_path):
    """Verify 1. 文本查询查找图片 returns MultimodalRetrievalHit with complete provenance."""
    pipeline, store, asset1, asset2 = _create_test_pipeline(tmp_path)

    # Query text
    hits = pipeline.search_images_by_text("Transformer model architecture", top_k=2)
    assert len(hits) == 2
    assert all(isinstance(h, MultimodalRetrievalHit) for h in hits)
    assert all(h.modality == "image" for h in hits)

    top_hit = hits[0]
    assert top_hit.asset_id in {"asset-transformer-fig1", "asset-heatmap-fig2"}
    assert top_hit.source_snapshot_id == "snapshot-paper-attn"
    assert top_hit.page in {3, 5}
    assert top_hit.artifact_ref.startswith("artifact-sha256-")
    assert top_hit.coordinate_space == "pdf_page_points_top_left"
    assert top_hit.embedding_model == "deterministic-multimodal-v1"
    assert top_hit.index_version == "conflux-weave.multimodal-index.v1"
    assert isinstance(top_hit.locator, dict)

    # Boundary tests
    with pytest.raises(ValueError, match="query must not be empty"):
        pipeline.search_images_by_text("   ")
    with pytest.raises(ValueError, match="top_k must be positive"):
        pipeline.search_images_by_text("test", top_k=0)


def test_search_text_by_image(tmp_path):
    """Verify 2. 图片查询查找文本 executes against dense index with matching dimensions."""
    pipeline, store, asset1, asset2 = _create_test_pipeline(tmp_path)

    # Search with raw bytes
    raw_bytes = b"\x89PNG\r\n\x1a\n\x00sample_query_image"
    res_bytes = pipeline.search_text_by_image(raw_bytes, top_k=2)
    assert isinstance(res_bytes, RetrievalQueryResult)
    assert len(res_bytes.hits) == 2
    assert res_bytes.hits[0].document_id in {"chunk-attn-1", "chunk-attn-2"}

    # Search with ImageEmbeddingRequest
    req = ImageEmbeddingRequest(image_bytes=raw_bytes, text="attention visualizer")
    res_req = pipeline.search_text_by_image(req, top_k=2)
    assert len(res_req.hits) == 2

    # Search with stored asset artifact_ref string
    res_art = pipeline.search_text_by_image(asset1.artifact_ref, top_k=2)
    assert len(res_art.hits) == 2

    # Boundary test
    with pytest.raises(ValueError, match="top_k must be positive"):
        pipeline.search_text_by_image(raw_bytes, top_k=-1)


def test_search_text_by_image_disjoint_space_rejected(tmp_path):
    """Verify 2. Disjoint embedding models without verified joint space are rejected."""
    pipeline, store, asset1, asset2 = _create_test_pipeline(tmp_path)

    # Create an adapter with disjoint model name
    disjoint_adapter = DeterministicImageEmbeddingAdapter(store, model="disjoint-image-encoder-v1", dimensions=128)
    disjoint_pipeline = MultimodalRetrievalPipeline(
        pipeline.text_pipeline,
        image_index=pipeline.image_index,
        image_embedding=disjoint_adapter,
        artifact_store=store,
        enabled=True,
        joint_multimodal_space=False,  # explicitly disjoint
    )

    with pytest.raises(ValueError, match="cross_modal_space_mismatch"):
        disjoint_pipeline.search_text_by_image(b"some_image_bytes")


def test_search_text_by_image_dimension_mismatch(tmp_path):
    """Verify 2. Dimension mismatch raises embedding_dimension_mismatch cleanly."""
    pipeline, store, asset1, asset2 = _create_test_pipeline(tmp_path)

    # Create a 64-dim image embedding adapter
    adapter_64 = DeterministicImageEmbeddingAdapter(store, dimensions=64)
    mismatched_pipeline = MultimodalRetrievalPipeline(
        pipeline.text_pipeline,  # text dense index is 128 dims
        image_index=pipeline.image_index,
        image_embedding=adapter_64,
        artifact_store=store,
        enabled=True,
        joint_multimodal_space=True,
    )

    with pytest.raises(ValueError, match="embedding_dimension_mismatch"):
        mismatched_pipeline.search_text_by_image(b"some_image_bytes")


def test_multimodal_pipeline_search_and_evidence(tmp_path):
    """Verify 3. 文本与图片结果 rank fusion and 4. 图片 Evidence 进入 Context Assembly."""
    pipeline, store, asset1, asset2 = _create_test_pipeline(tmp_path)

    run = pipeline.search(
        "Transformer architecture and multi-head attention",
        image_k=2,
        fusion_k=4,
    )

    assert isinstance(run, MultimodalRetrievalRun)
    assert run.query == "Transformer architecture and multi-head attention"
    assert run.text_run is not None
    assert len(run.image_hits) == 2
    assert len(run.fused_hits) <= 4
    assert any(h.modality == "text" for h in run.fused_hits)
    assert any(h.modality == "image" for h in run.fused_hits)

    # Check request and response artifacts
    assert run.query_image_embedding_request_artifact is not None
    assert run.query_image_embedding_response_artifact is not None

    # Verify to_evidence_refs produces closed EvidenceRef items
    evidence = pipeline.to_evidence_refs(run.fused_hits)
    assert len(evidence) == len(run.fused_hits)

    image_evidence = [e for e in evidence if e.modality == "image"]
    text_evidence = [e for e in evidence if e.modality == "text"]

    assert len(image_evidence) >= 1
    assert len(text_evidence) >= 1

    for img_ev in image_evidence:
        assert img_ev.asset_id is not None
        assert img_ev.artifact_ref is not None
        assert img_ev.extraction_method == "multimodal-image-retrieval-v1"
        # Quote must be ONLY raw caption or empty, no hallucinated model description
        assert "Figure" in img_ev.quote or img_ev.quote == ""
        assert "pdf_page_points_top_left" in str(img_ev.locator) or "page" in img_ev.locator

    for txt_ev in text_evidence:
        assert txt_ev.asset_id is None
        assert txt_ev.modality == "text"
        assert txt_ev.extraction_method == "hybrid-lancedb-rerank-page-chunk-v2"
        assert len(txt_ev.quote) > 0


def test_verified_research_workflow_with_multimodal_evidence(tmp_path):
    """Verify 4 & 5. End-to-end Context Assembly and Citation in report with image evidence."""
    pipeline, store, asset1, asset2 = _create_test_pipeline(tmp_path)
    config = ProviderConfig("https://provider.example/v1", "secret", "chat")

    # Setup chat responses simulating:
    # 1. Draft citing both text (evidence-0001) and image (evidence-0002)
    # 2. Verify accepting both claims
    # 3. Distill & Writer rendering
    chat = OpenAICompatibleChatAdapter(
        store,
        config,
        transport=SequenceTransport([
            chat_response(
                {
                    "claims": [
                        {
                            "text": "Transformer incorporates self-attention mechanism.",
                            "evidence_ids": ["evidence-0001"],
                        },
                        {
                            "text": "The model architecture includes encoder and decoder stacks illustrated in figure 1.",
                            "evidence_ids": ["evidence-0002"],
                        },
                    ]
                },
                "draft",
            ),
            chat_response(
                {
                    "assessments": [
                        {
                            "claim_id": "claim-0001",
                            "evidence_ids": ["evidence-0001"],
                            "relation": "supports",
                            "verdict": "accepted",
                            "rationale": "Directly supported by chunk text.",
                        },
                        {
                            "claim_id": "claim-0002",
                            "evidence_ids": ["evidence-0002"],
                            "relation": "supports",
                            "verdict": "accepted",
                            "rationale": "Directly supported by Figure 1 caption and image asset.",
                        },
                    ]
                },
                "verify",
            ),
        ]),
    )

    workflow = VerifiedResearchWorkflow(
        store,
        pipeline,
        chat,
        corpus_scope="multimodal paper corpus",
    )

    execution = workflow.execute("Analyze Transformer Architecture", enable_writer=False)
    assert execution.coverage.accepted_claim_count == 2
    assert execution.coverage.stop_reason == "verified_delivery"

    # Verify report contains image asset reference
    report_bytes = store.path_for_digest(
        execution.report_artifact_id.removeprefix("artifact-sha256-")
    ).read_bytes()
    report_text = report_bytes.decode("utf-8")

    assert "# Analyze Transformer Architecture" in report_text
    assert "[1] `claim-0001`" in report_text
    assert "[2] `claim-0002`" in report_text
    # Evidence summary must explicitly show 图片资产 for image evidence
    assert "图片资产 `asset-transformer-fig1`" in report_text or "图片资产 `asset-heatmap-fig2`" in report_text


def test_multimodal_feature_toggle_fallback(tmp_path, monkeypatch):
    """Verify system gracefully degrades to text-only when multimodal is disabled."""
    pipeline, store, asset1, asset2 = _create_test_pipeline(tmp_path)

    # 1. Test via pipeline enabled=False parameter
    disabled_pipeline = MultimodalRetrievalPipeline(
        pipeline.text_pipeline,
        image_index=pipeline.image_index,
        image_embedding=pipeline.image_embedding,
        artifact_store=store,
        enabled=False,
    )
    assert not disabled_pipeline.is_multimodal_active()
    assert disabled_pipeline.search_images_by_text("Transformer") == ()

    run_disabled = disabled_pipeline.search("Transformer")
    assert run_disabled.image_hits == ()
    assert all(h.modality == "text" for h in run_disabled.fused_hits)

    # 2. Test via default unset env var (2026-09-12 revision: rich visual RAG is on by default)
    monkeypatch.delenv(MULTIMODAL_ENV_FLAG, raising=False)
    assert is_multimodal_env_enabled()

    # 3. Test via environment variable CONFLUX_WEAVE_MULTIMODAL_ENABLED=false
    monkeypatch.setenv(MULTIMODAL_ENV_FLAG, "false")
    assert not is_multimodal_env_enabled()
    env_pipeline = MultimodalRetrievalPipeline(
        pipeline.text_pipeline,
        image_index=pipeline.image_index,
        image_embedding=pipeline.image_embedding,
        artifact_store=store,
    )
    assert not env_pipeline.is_multimodal_active()

    # 4. Test via environment variable CONFLUX_WEAVE_MULTIMODAL_ENABLED=true
    monkeypatch.setenv(MULTIMODAL_ENV_FLAG, "true")
    assert is_multimodal_env_enabled()


def test_api_contracts_serialization():
    """Verify MultimodalFusionHitResponse and MultimodalRetrievalResultResponse contracts."""
    hit = MultimodalFusionHitResponse(
        hit_id="asset-1",
        score=0.01639,
        rank=1,
        modality="image",
        source_snapshot_id="snap-1",
        locator={"page": 2, "bbox": {"x": 10.0, "y": 20.0, "width": 100.0, "height": 80.0}},
        text="Figure 1",
        asset_id="asset-1",
        artifact_ref="artifact-sha256-abc",
        page=2,
        bbox={"x": 10.0, "y": 20.0, "width": 100.0, "height": 80.0},
    )
    hit_dict = hit.model_dump(mode="json")
    assert hit_dict["hit_id"] == "asset-1"
    assert hit_dict["modality"] == "image"
    assert hit_dict["bbox"]["width"] == 100.0

    res = MultimodalRetrievalResultResponse(
        query="test query",
        text_hits_count=5,
        image_hits_count=2,
        fusion_strategy="reciprocal_rank_fusion",
        fused_hits=(hit,),
    )
    res_dict = res.model_dump(mode="json")
    assert res_dict["query"] == "test query"
    assert len(res_dict["fused_hits"]) == 1
    assert res_dict["fused_hits"][0]["asset_id"] == "asset-1"
