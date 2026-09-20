"""Unit tests for BgeMultimodalCrossReranker (Phase 3 Topic 3).

Verifies:
1. Conformance to MultimodalCrossReranker protocol.
2. Candidate document formatting with caption, references, and OCR.
3. Neural score ordering and threshold pruning with mocked API responses.
4. Fallback resilience to DefaultMultimodalCrossReranker upon network failure.
5. Live API execution with bge-reranker-v2-m3-free when API key is present.
"""

from __future__ import annotations

import json
import os
from unittest.mock import MagicMock, patch
import pytest

from conflux_weave.multimodal_indexing import MultimodalRetrievalHit
from conflux_weave.multimodal_reranker_bge import BgeMultimodalCrossReranker
from conflux_weave.multimodal_retrieval import (
    DefaultMultimodalCrossReranker,
    MultimodalCrossReranker,
)


def make_sample_hit(
    asset_id: str,
    caption: str = "",
    referencing_text: str = "",
    ocr_text: str = "",
    score: float = 0.5,
) -> MultimodalRetrievalHit:
    return MultimodalRetrievalHit(
        asset_id=asset_id,
        score=score,
        rank=1,
        modality="image",
        source_snapshot_id="snap-1",
        document_id="doc-1",
        page=1,
        bbox={"x": 10.0, "y": 10.0, "width": 100.0, "height": 100.0},
        coordinate_space="pdf_points",
        parent_chunk_ids=(),
        caption=caption,
        artifact_ref=f"art-{asset_id}",
        thumbnail_artifact_ref=None,
        embedding_model="jina-clip-v2",
        index_version="v1",
        locator={},
        referencing_text=referencing_text,
        ocr_text=ocr_text,
    )


def test_bge_reranker_protocol_compliance():
    reranker = BgeMultimodalCrossReranker()
    assert isinstance(reranker, MultimodalCrossReranker)


def test_bge_reranker_document_formatting():
    reranker = BgeMultimodalCrossReranker()
    hit = make_sample_hit(
        "asset-1",
        caption="Figure 1: Convolutional architecture",
        referencing_text="As seen in Fig. 1, features are extracted...",
        ocr_text="Layer 1: Conv2D 64 filters",
    )
    doc = reranker._format_candidate_document(hit)
    assert "Caption: Figure 1: Convolutional architecture" in doc
    assert "In-text citation context: As seen in Fig. 1" in doc
    assert "Visual text content: Layer 1: Conv2D 64 filters" in doc


def test_bge_reranker_neural_ordering_and_pruning():
    hit_rel = make_sample_hit("asset-rel", caption="Figure 3: PPO training curve and reward")
    hit_distractor = make_sample_hit("asset-dist", caption="Figure 1: Overview of corporate company building")

    mock_response = {
        "results": [
            {"index": 0, "relevance_score": 0.85},
            {"index": 1, "relevance_score": 0.00005},
        ]
    }

    mock_resp_obj = MagicMock()
    mock_resp_obj.__enter__.return_value = mock_resp_obj
    mock_resp_obj.read.return_value = json.dumps(mock_response).encode("utf-8")

    reranker = BgeMultimodalCrossReranker(
        api_key="test-key",
        min_relevance_threshold=0.001,
    )

    with patch("urllib.request.urlopen", return_value=mock_resp_obj):
        reranked = reranker.rerank(
            "What is the PPO reinforcement learning reward curve?",
            [hit_rel, hit_distractor],
            top_k=5,
        )

    # Relevant hit should be kept with updated neural score
    assert len(reranked) == 1
    assert reranked[0].asset_id == "asset-rel"
    assert reranked[0].score == 0.85
    assert reranked[0].rank == 1
    assert reranker.last_call_success is True


def test_bge_reranker_fallback_on_network_failure():
    hit = make_sample_hit("asset-rel", caption="Figure 2: Architecture diagram")
    fallback = DefaultMultimodalCrossReranker()
    reranker = BgeMultimodalCrossReranker(
        api_key="test-key",
        fallback_reranker=fallback,
    )

    # Simulate network exception
    with patch("urllib.request.urlopen", side_effect=RuntimeError("Connection refused")):
        reranked = reranker.rerank(
            "Architecture diagram",
            [hit],
            top_k=5,
        )

    assert reranker.last_call_success is False
    # Fallback reranker should have executed safely
    assert len(reranked) == 1
    assert reranked[0].asset_id == "asset-rel"


def test_live_bge_reranker_call():
    from dotenv import load_dotenv
    load_dotenv()
    api_key = os.getenv("CONFLUX_WEAVE_PROVIDER_API_KEY")
    if not api_key:
        pytest.skip("No CONFLUX_WEAVE_PROVIDER_API_KEY set for live reranker test")

    reranker = BgeMultimodalCrossReranker(
        api_key=api_key,
        model="bge-reranker-v2-m3-free",
        min_relevance_threshold=0.001,
    )

    hit_ml = make_sample_hit("asset-ml", caption="Figure 1: Deep neural network training loss")
    hit_unrelated = make_sample_hit("asset-culinary", caption="Figure 2: Italian pizza culinary ingredients")

    reranked = reranker.rerank(
        "machine learning loss convergence",
        [hit_ml, hit_unrelated],
        top_k=5,
    )

    assert reranker.last_call_success is True
    assert reranker.last_latency_ms > 0
    assert len(reranked) >= 1
    assert reranked[0].asset_id == "asset-ml"
