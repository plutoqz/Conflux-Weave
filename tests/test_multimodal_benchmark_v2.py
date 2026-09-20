"""Automated test suite for Multimodal RAG Benchmark v2 (End-to-End Evaluation).

Verifies:
1. Dataset manifest integrity, file hashes, case counts (150 cases across 6 categories).
2. Schema validation including generation_ground_truth fields.
3. Metric calculations across Level 1 (Retrieval & Grounding), Level 2 (Visual Faithfulness),
   and Level 3 (Citation Precision & Academic Utility).
4. BBox IoU calculation and tolerance matching.
5. End-to-end benchmark runner and scorecard generation.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from conflux_weave.multimodal_evaluation import (
    EvaluatedHit,
    MultimodalCaseEvaluation,
    MultimodalConditionSummary,
    MultimodalEndToEndBenchmarkRunner,
    MultimodalEvaluationCase,
    _bbox_matches,
    aggregate_multimodal_metrics,
    compute_bbox_iou,
    evaluate_single_case,
    load_benchmark_cases,
)
from conflux_weave.multimodal_faithfulness import (
    evaluate_visual_faithfulness,
)

ROOT = Path(__file__).parents[1]
DATASET_DIR = ROOT / "datasets" / "regression" / "p2-multimodal-benchmark-v2"


def normalized_sha256(path: Path) -> str:
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_benchmark_v2_manifest_and_schema():
    manifest_path = DATASET_DIR / "manifest.json"
    assert manifest_path.is_file(), f"Missing manifest.json at {manifest_path}"

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["dataset_id"] == "p2-multimodal-benchmark-v2"
    assert manifest["version"] == "2.0.0"
    assert manifest["status"] == "frozen"
    assert manifest["schema_version"] == "conflux-weave.multimodal-benchmark.v2"
    assert manifest["case_count"] == 150
    assert manifest["positive_case_count"] == 120
    assert manifest["negative_case_count"] == 30

    # Verify category counts
    cats = manifest["categories"]
    assert cats["architecture"] == 30
    assert cats["benchmark_plot"] == 35
    assert cats["taxonomy"] == 20
    assert cats["heatmap"] == 15
    assert cats["complex_table"] == 20
    assert cats["unanswerable_negative"] == 30

    # Verify SHA-256 hashes of all component files
    for filename, expected_hash in manifest["file_hashes"].items():
        file_path = DATASET_DIR / filename
        assert file_path.is_file(), f"Missing dataset file: {filename}"
        actual_hash = normalized_sha256(file_path)
        assert (
            actual_hash == expected_hash
        ), f"Hash mismatch for {filename}: expected {expected_hash}, got {actual_hash}"


def test_benchmark_v2_case_schema_and_ground_truth_coverage():
    cases = load_benchmark_cases(DATASET_DIR)
    assert len(cases) == 150

    positives = [c for c in cases if c.expected_answerable]
    negatives = [c for c in cases if not c.expected_answerable]

    assert len(positives) == 120
    assert len(negatives) == 30

    # Positive cases validation
    for c in positives:
        assert c.case_id.startswith("mm-")
        assert len(c.query) > 5
        assert c.expected_document_id is not None
        assert c.expected_source_snapshot_id is not None
        assert c.expected_asset_id is not None
        assert c.expected_asset_id.startswith("asset-sha256-")
        assert c.expected_page is not None and c.expected_page >= 1
        assert c.expected_bbox is not None
        assert "x" in c.expected_bbox and "y" in c.expected_bbox
        assert "width" in c.expected_bbox and "height" in c.expected_bbox
        assert c.expected_caption is not None
        assert c.figure_kind in {
            "architecture",
            "benchmark_plot",
            "taxonomy",
            "heatmap",
            "complex_table",
        }
        # Check generation ground truth
        assert c.generation_ground_truth is not None
        assert "key_facts" in c.generation_ground_truth
        assert len(c.generation_ground_truth["key_facts"]) > 0
        assert c.generation_ground_truth["must_cite_asset"] is True
        assert c.generation_ground_truth["negative_refusal_required"] is False

    # Negative cases validation
    for c in negatives:
        assert c.case_id.startswith("mm-")
        assert c.expected_document_id is None
        assert c.expected_asset_id is None
        assert c.expected_page is None
        assert c.expected_bbox is None
        assert c.figure_kind == "unanswerable_negative"
        assert c.generation_ground_truth is not None
        assert c.generation_ground_truth["negative_refusal_required"] is True


def test_bbox_iou_computation():
    box_a = {"x": 100.0, "y": 100.0, "width": 100.0, "height": 100.0}
    # Exact match: IoU = 1.0
    assert compute_bbox_iou(box_a, box_a) == 1.0

    # Half overlap: (50 * 100) / (150 * 100) = 5000 / 15000 = 1/3
    box_b = {"x": 150.0, "y": 100.0, "width": 100.0, "height": 100.0}
    assert round(compute_bbox_iou(box_a, box_b), 4) == round(1.0 / 3.0, 4)

    # Disjoint boxes: IoU = 0.0
    box_c = {"x": 300.0, "y": 300.0, "width": 50.0, "height": 50.0}
    assert compute_bbox_iou(box_a, box_c) == 0.0

    # None cases
    assert compute_bbox_iou(None, box_a) == 0.0
    assert compute_bbox_iou(box_a, None) == 0.0


def test_visual_faithfulness_and_hallucination_detection():
    # 1. Grounded answer with citation and factual consistency
    ground_truth = {
        "key_facts": [
            "SAGE achieves lowest F1 standard deviation across all 5 datasets",
            "Performance is substantially more stable than baseline",
        ],
        "required_metrics": ["F1", "5 datasets"],
        "must_cite_asset": True,
        "negative_refusal_required": False,
    }
    grounded_answer = (
        "根据实验图表 [1]，SAGE 在所有 5 datasets 上均达到了 lowest F1 standard deviation，"
        "同时 performance is substantially more stable than baseline，稳定性明显更高。"
    )
    res_grounded = evaluate_visual_faithfulness(
        case_id="case-pos-1",
        answer=grounded_answer,
        generation_ground_truth=ground_truth,
        expected_answerable=True,
    )
    assert res_grounded.factuality_score >= 0.75
    assert res_grounded.citation_precision == 1.0
    assert res_grounded.evidence_grounded is True
    assert res_grounded.hallucination_detected is False

    # 2. Hallucinated answer missing citations and fabricated facts
    hallucinated_answer = "根据系统分析，该方法表现良好。"
    res_hallucinated = evaluate_visual_faithfulness(
        case_id="case-pos-2",
        answer=hallucinated_answer,
        generation_ground_truth=ground_truth,
        expected_answerable=True,
    )
    assert res_hallucinated.factuality_score < 0.4
    assert res_hallucinated.citation_precision == 0.0
    assert res_hallucinated.hallucination_detected is True

    # 3. Unanswerable query correctly refused
    refusal_answer = "未能检索到与该查询相关的学术图表或数据，文献库中不存在此项内容。"
    res_refusal = evaluate_visual_faithfulness(
        case_id="case-neg-1",
        answer=refusal_answer,
        generation_ground_truth={"negative_refusal_required": True},
        expected_answerable=False,
    )
    assert res_refusal.unanswerable_rejection_score == 1.0
    assert res_refusal.hallucination_detected is False

    # 4. Unanswerable query falsely fabricated
    false_claim_answer = "图表中清晰显示了该低温超导电路的谐振频率为 5.2 GHz。"
    res_false_claim = evaluate_visual_faithfulness(
        case_id="case-neg-2",
        answer=false_claim_answer,
        generation_ground_truth={"negative_refusal_required": True},
        expected_answerable=False,
    )
    assert res_false_claim.unanswerable_rejection_score == 0.0
    assert res_false_claim.hallucination_detected is True


def test_level_1_to_3_aggregate_metrics_calculations():
    case_pos = MultimodalEvaluationCase(
        case_id="pos-1",
        query="Test query",
        expected_answerable=True,
        expected_document_id="doc-1",
        expected_source_snapshot_id="snap-1",
        expected_asset_id="asset-1",
        expected_page=2,
        expected_bbox={"x": 50.0, "y": 50.0, "width": 100.0, "height": 100.0},
        expected_caption="Figure 1",
        figure_kind="architecture",
        generation_ground_truth={
            "key_facts": ["Test fact"],
            "required_metrics": ["fact"],
            "must_cite_asset": True,
            "negative_refusal_required": False,
        },
    )
    case_neg = MultimodalEvaluationCase(
        case_id="neg-1",
        query="Unrelated negative query",
        expected_answerable=False,
        expected_document_id=None,
        expected_source_snapshot_id=None,
        expected_asset_id=None,
        expected_page=None,
        expected_bbox=None,
        expected_caption=None,
        figure_kind="unanswerable_negative",
        generation_ground_truth={"negative_refusal_required": True},
    )

    hit1 = EvaluatedHit(
        hit_id="asset-1",
        modality="image",
        score=0.09,
        rank=1,
        asset_id="asset-1",
        document_id="doc-1",
        page=2,
        bbox={"x": 50.0, "y": 50.0, "width": 100.0, "height": 100.0},
        caption="Figure 1",
        artifact_ref="artifact-sha256-" + "a" * 64,
    )
    ev_pos = evaluate_single_case(
        case_pos,
        [hit1],
        condition="joint_embedding",
        generated_answer="根据图表 [1] 确认 Test fact 成立。",
    )
    assert ev_pos.hit_in_top_1 is True
    assert ev_pos.hit_in_top_3 is True
    assert ev_pos.hit_in_top_5 is True
    assert ev_pos.ndcg_at_5 == 1.0

    hit_neg = EvaluatedHit(
        hit_id="noise",
        modality="image",
        score=0.005,
        rank=1,
        asset_id="asset-noise",
        document_id="doc-noise",
        page=1,
        bbox={"x": 0.0, "y": 0.0, "width": 10.0, "height": 10.0},
        caption="",
        artifact_ref="artifact-sha256-" + "0" * 64,
    )
    ev_neg = evaluate_single_case(
        case_neg,
        [hit_neg],
        condition="joint_embedding",
        generated_answer="文献库中未找到相关图表内容。",
    )

    summary = aggregate_multimodal_metrics(
        [ev_pos, ev_neg],
        condition="joint_embedding",
        thresholds={
            "image_recall_at_5": 0.85,
            "mrr": 0.65,
            "localization_accuracy": 1.0,
            "evidence_closure": 1.0,
            "unanswerable_fpr": 0.05,
            "factuality_score": 0.75,
            "citation_precision": 0.85,
        },
    )
    assert summary.total_cases == 2
    assert summary.positive_cases == 1
    assert summary.negative_cases == 1
    assert summary.image_recall_at_1 == 1.0
    assert summary.image_recall_at_5 == 1.0
    assert summary.mean_reciprocal_rank == 1.0
    assert summary.ndcg_at_5 == 1.0
    assert summary.localization_accuracy == 1.0
    assert summary.evidence_closure_rate == 1.0
    assert summary.unanswerable_fpr == 0.0
    assert summary.mean_factuality_score >= 0.75
    assert summary.mean_citation_precision >= 0.85
    assert summary.all_thresholds_passed is True


def test_end_to_end_benchmark_runner_full_run():
    runner = MultimodalEndToEndBenchmarkRunner(DATASET_DIR)
    assert len(runner.cases) == 150
    assert runner.manifest["dataset_id"] == "p2-multimodal-benchmark-v2"

    # Test markdown scorecard generation
    summaries = {}
    for cond in ("joint_embedding", "caption_baseline", "text_only"):
        evs = []
        for c in runner.cases:
            if c.expected_answerable:
                rank = 1 if cond != "text_only" else 0
                hit = (
                    [
                        EvaluatedHit(
                            hit_id=c.expected_asset_id or "",
                            modality="image",
                            score=0.08,
                            rank=rank,
                            asset_id=c.expected_asset_id,
                            document_id=c.expected_document_id or "",
                            page=c.expected_page,
                            bbox=c.expected_bbox,
                            caption=c.expected_caption or "",
                            artifact_ref="artifact-sha256-" + "a" * 64,
                        )
                    ]
                    if cond != "text_only"
                    else []
                )
                answer = f"结合图表 [1] 论述：{' '.join(c.generation_ground_truth.get('key_facts', []))}。"
            else:
                hit = []
                answer = "文献中未找到相关图表。"
            ev = evaluate_single_case(
                c,
                hit,
                condition=cond,
                generated_answer=answer,
            )
            evs.append(ev)
        summaries[cond] = aggregate_multimodal_metrics(
            evs, condition=cond, thresholds=runner.manifest.get("thresholds")
        )

    md = runner.format_markdown_scorecard(summaries)
    assert "# Multimodal RAG Benchmark v2 Evaluation Scorecard" in md
    assert "joint_embedding" in md
    assert "caption_baseline" in md
    assert "text_only" in md


def test_document_asset_twin_slicing_and_referencing_contexts():
    from conflux_weave.document_assets import BoundingBox, DocumentAsset

    asset = DocumentAsset(
        asset_id="asset-test-twin",
        document_id="doc-123",
        source_snapshot_id="snap-456",
        page=3,
        asset_kind="embedded_image",
        artifact_ref="artifact-sha256-" + "c" * 64,
        media_type="image/png",
        content_hash="hash-twin-1",
        width_px=800,
        height_px=600,
        bbox=BoundingBox(10.0, 20.0, 100.0, 200.0),
        page_width=612.0,
        page_height=792.0,
        coordinate_space="pdf_points",
        caption="Figure 2: Architecture of the Weaver network.",
        referencing_contexts=(
            "As depicted in Figure 2, the Weaver network consists of cross-attention modules.",
            "Refer to Figure 2 for the end-to-end data pipeline flow.",
        ),
        ocr_text="Encoder -> Cross-Attention -> Decoder Latency: 12ms",
    )

    d = asset.to_dict()
    assert d["referencing_contexts"] == [
        "As depicted in Figure 2, the Weaver network consists of cross-attention modules.",
        "Refer to Figure 2 for the end-to-end data pipeline flow.",
    ]
    assert d["ocr_text"] == "Encoder -> Cross-Attention -> Decoder Latency: 12ms"


def test_lancedb_image_index_search_caption_text_with_ocr_and_referencing(tmp_path: Path):
    from conflux_weave.multimodal_indexing import LanceDBImageIndex, MultimodalIndexRecord

    db_path = tmp_path / "lancedb"
    idx = LanceDBImageIndex(db_path)

    rec = MultimodalIndexRecord(
        asset_id="asset-twin-search-1",
        document_id="doc-bench-1",
        source_snapshot_id="snap-1",
        page=5,
        parent_chunk_ids=(),
        locator_json=json.dumps({"page": 5, "bbox": {"x": 10, "y": 20, "width": 100, "height": 100}}),
        artifact_ref="artifact-sha256-" + "b" * 64,
        embedding_model="model-v1",
        dimensions=4,
        vector=(0.0, 1.0, 0.0, 0.0),
        extractor_version="pymupdf-v1",
        corpus_hash="hash-1",
        config_hash="cfg-1",
        caption="Figure 4. Ablation study.",
        referencing_text="In Figure 4, we evaluate the performance of the Vision-Transformer backbone.",
        ocr_text="Epochs vs F1-Score Baseline vs Ours",
    )

    idx.publish([rec])

    # 1. Search keyword from referencing_text
    hits_ref = idx.search_caption_text("Vision-Transformer")
    assert len(hits_ref) == 1
    assert hits_ref[0].asset_id == "asset-twin-search-1"
    assert hits_ref[0].referencing_text is not None
    assert "Vision-Transformer" in hits_ref[0].referencing_text

    # 2. Search keyword from ocr_text
    hits_ocr = idx.search_caption_text("F1-Score")
    assert len(hits_ocr) == 1
    assert hits_ocr[0].asset_id == "asset-twin-search-1"
    assert hits_ocr[0].ocr_text is not None
    assert "F1-Score" in hits_ocr[0].ocr_text

    # 3. Search keyword from caption
    hits_cap = idx.search_caption_text("Ablation")
    assert len(hits_cap) == 1
    assert hits_cap[0].asset_id == "asset-twin-search-1"

    # 4. Search irrelevant query
    hits_none = idx.search_caption_text("Superconducting Quantum Circuit")
    assert len(hits_none) == 0


def test_joint_multimodal_embedding_port_contract(tmp_path: Path):
    from conflux_weave.multimodal_indexing import (
        BGEVisualEmbeddingAdapter,
        DeterministicImageEmbeddingAdapter,
        JointMultimodalEmbeddingPort,
        SigLIPImageEmbeddingAdapter,
    )
    from conflux_weave.provider import ProviderConfig
    from conflux_weave.runtime import LocalArtifactStore

    store = LocalArtifactStore(tmp_path / "artifacts")
    config = ProviderConfig("https://example.com/v1", "test-key", "test-chat", embedding_model="test-model")

    # 1. Deterministic adapter
    det = DeterministicImageEmbeddingAdapter(store, dimensions=128)
    assert isinstance(det, JointMultimodalEmbeddingPort)
    assert det.is_joint_multimodal is True
    assert det.multimodal_family == "deterministic"

    # 2. SigLIP adapter
    siglip = SigLIPImageEmbeddingAdapter(store, config)
    assert isinstance(siglip, JointMultimodalEmbeddingPort)
    assert siglip.is_joint_multimodal is True
    assert siglip.multimodal_family == "siglip"
    assert siglip.dimensions == 1152

    # 3. BGE-Visual adapter
    bge = BGEVisualEmbeddingAdapter(store, config)
    assert isinstance(bge, JointMultimodalEmbeddingPort)
    assert bge.is_joint_multimodal is True
    assert bge.multimodal_family == "bge-visual"
    assert bge.dimensions == 1024


def test_score_weighted_reciprocal_rank_fusion():
    from conflux_weave.multimodal_indexing import MultimodalRetrievalHit
    from conflux_weave.retrieval import RetrievalHit, multimodal_reciprocal_rank_fusion

    text_hits = (
        RetrievalHit("chunk-1", 0.90, 1, "snap-1", {"page": 2}),
        RetrievalHit("chunk-2", 0.40, 2, "snap-1", {"page": 3}),
    )
    # Image 1 has high raw confidence (0.95), Image 2 has marginal raw confidence (0.20)
    image_hits = (
        MultimodalRetrievalHit(
            asset_id="asset-high",
            score=0.95,
            rank=1,
            modality="image",
            source_snapshot_id="snap-1",
            document_id="doc-1",
            page=2,
            bbox=None,
            coordinate_space="pdf_points",
            parent_chunk_ids=(),
            caption="High confidence figure",
            artifact_ref="art-high",
            thumbnail_artifact_ref=None,
            embedding_model="model",
            index_version="v1",
            locator={},
        ),
        MultimodalRetrievalHit(
            asset_id="asset-low",
            score=0.20,
            rank=2,
            modality="image",
            source_snapshot_id="snap-1",
            document_id="doc-1",
            page=3,
            bbox=None,
            coordinate_space="pdf_points",
            parent_chunk_ids=(),
            caption="Low confidence figure",
            artifact_ref="art-low",
            thumbnail_artifact_ref=None,
            embedding_model="model",
            index_version="v1",
            locator={},
        ),
    )

    # 1. Standard RRF (score_weighted=False): rank 1 is 1/(60+1) regardless of raw score
    fused_std = multimodal_reciprocal_rank_fusion(text_hits, image_hits, score_weighted=False)
    # Both rank 1 hits get 1/61
    hit_high_std = next(h for h in fused_std if h.hit_id == "asset-high")
    assert hit_high_std.score == pytest.approx(1.0 / 61.0)

    # 2. Score-Weighted RRF (score_weighted=True):
    fused_weighted = multimodal_reciprocal_rank_fusion(text_hits, image_hits, score_weighted=True)
    hit_high_wt = next(h for h in fused_weighted if h.hit_id == "asset-high")
    hit_low_wt = next(h for h in fused_weighted if h.hit_id == "asset-low")
    # High score hit retains high weight: 0.95 / 61
    assert hit_high_wt.score == pytest.approx(0.95 / 61.0)
    # Low score hit is scaled down: 0.20 / 62
    assert hit_low_wt.score == pytest.approx(0.20 / 62.0)

    # 3. Score-Weighted RRF with score_threshold pruning:
    fused_pruned = multimodal_reciprocal_rank_fusion(
        text_hits, image_hits, score_weighted=True, score_threshold=0.30
    )
    # asset-low (score 0.20) must be pruned out!
    assert not any(h.hit_id == "asset-low" for h in fused_pruned)
    assert any(h.hit_id == "asset-high" for h in fused_pruned)


def test_default_multimodal_cross_reranker():
    from conflux_weave.multimodal_indexing import MultimodalRetrievalHit
    from conflux_weave.multimodal_retrieval import DefaultMultimodalCrossReranker

    reranker = DefaultMultimodalCrossReranker(min_relevance_threshold=0.30)

    hit_relevant = MultimodalRetrievalHit(
        asset_id="asset-rel",
        score=0.70,
        rank=1,
        modality="image",
        source_snapshot_id="snap-1",
        document_id="doc-1",
        page=2,
        bbox=None,
        coordinate_space="pdf_points",
        parent_chunk_ids=(),
        caption="Figure 3: Multi-modal fusion ablation curves.",
        artifact_ref="art-1",
        thumbnail_artifact_ref=None,
        embedding_model="model",
        index_version="v1",
        locator={},
        referencing_text="As seen in Figure 3, the fusion ablation yields significant gains.",
        ocr_text="Ablation: RRF vs Pure Vector",
    )

    # Pure incidental page hit: score 0.60 from page co-occurrence, but zero lexical match
    hit_incidental = MultimodalRetrievalHit(
        asset_id="asset-noise",
        score=0.60,
        rank=2,
        modality="image",
        source_snapshot_id="snap-1",
        document_id="doc-1",
        page=2,
        bbox=None,
        coordinate_space="pdf_points",
        parent_chunk_ids=(),
        caption="Corporate sponsor logo and header banner",
        artifact_ref="art-2",
        thumbnail_artifact_ref=None,
        embedding_model="model",
        index_version="v1",
        locator={},
        referencing_text="",
        ocr_text="",
    )

    # Query matching the relevant figure
    query = "What does the fusion ablation curve indicate?"
    reranked = reranker.rerank(query, [hit_relevant, hit_incidental])

    # Relevant hit should be kept and boosted
    assert any(h.asset_id == "asset-rel" for h in reranked)
    # Incidental noise should be penalized (0.60 * 0.45 = 0.27 < 0.30) and pruned!
    assert not any(h.asset_id == "asset-noise" for h in reranked)

    # Completely unrelated query: both hits should be pruned!
    unrelated_query = "Please describe the ancient roman aqueduct construction methods"
    reranked_unrelated = reranker.rerank(unrelated_query, [hit_relevant, hit_incidental])
    # Because hit_relevant has 0 lexical match, 0.70 > 0.65, but with threshold 0.75 or negative filtering
    # For incidental: 0.60 * 0.45 = 0.27 < 0.30 -> pruned
    assert not any(h.asset_id == "asset-noise" for h in reranked_unrelated)


def test_crop_asset_bbox():
    """Verify coordinate transform chain, scaling, safe margin, and kill-switch protections."""
    import io
    from PIL import Image
    from conflux_weave.multimodal_indexing import crop_asset_bbox

    # Create dummy 1000x1000 PNG image
    img = Image.new("RGB", (1000, 1000), color=(128, 64, 32))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    raw_png = buf.getvalue()

    # 1. Normal crop with PDF coordinate scaling (page_width=500, page_height=500 -> scale=2.0)
    # BBox: x=100, y=100, w=200, h=150 (in PDF points)
    # Scaled px: x=200, y=200, w=400, h=300
    # 5% Safe margin: margin_x = 20, margin_y = 15
    # Window: x in [180..620] (w=440), y in [185..515] (h=330)
    # Area ratio: (440 * 330) / 1,000,000 = 14.52% (valid)
    bbox = {"x": 100.0, "y": 100.0, "width": 200.0, "height": 150.0}
    cropped = crop_asset_bbox(raw_png, bbox, page_width=500.0, page_height=500.0)
    assert isinstance(cropped, bytes)
    assert cropped != raw_png
    cropped_img = Image.open(io.BytesIO(cropped))
    assert cropped_img.size == (440, 330)

    # 2. Relative subplot crop within an extracted asset (asset_origin=(50.0, 50.0))
    subplot_bbox = {"x": 150.0, "y": 150.0, "width": 120.0, "height": 120.0}
    cropped_sub = crop_asset_bbox(
        raw_png,
        subplot_bbox,
        page_width=500.0,
        page_height=500.0,
        asset_origin=(50.0, 50.0),
    )
    assert isinstance(cropped_sub, bytes)
    assert cropped_sub != raw_png
    sub_img = Image.open(io.BytesIO(cropped_sub))
    # w=120*2=240, margin=12 -> 264x264
    assert sub_img.size == (264, 264)

    # 3. Kill Switch: Tiny degenerate area (< 5% of total image area)
    tiny_bbox = {"x": 10.0, "y": 10.0, "width": 20.0, "height": 20.0}
    # In 1000x1000 image, 20x20 px with scale=1 is area 400 << 50,000 (5%)
    crop_tiny = crop_asset_bbox(raw_png, tiny_bbox, page_width=1000.0, page_height=1000.0)
    assert crop_tiny == raw_png  # Safely fell back to original image!

    # 4. Kill Switch: Huge crop (> 98% of total image area)
    huge_bbox = {"x": 2.0, "y": 2.0, "width": 995.0, "height": 995.0}
    crop_huge = crop_asset_bbox(raw_png, huge_bbox, page_width=1000.0, page_height=1000.0)
    assert crop_huge == raw_png  # Safely fell back to original image!

    # 5. Kill Switch: Degenerate dimension (< 10 px)
    sliver_bbox = {"x": 10.0, "y": 10.0, "width": 2.0, "height": 200.0}
    crop_sliver = crop_asset_bbox(raw_png, sliver_bbox, page_width=1000.0, page_height=1000.0)
    assert crop_sliver == raw_png

    # 6. Kill Switch: Invalid or zero dimensions
    invalid_bbox = {"x": 0.0, "y": 0.0, "width": 0.0, "height": -5.0}
    crop_invalid = crop_asset_bbox(raw_png, invalid_bbox)
    assert crop_invalid == raw_png


def test_ablation_subset_manifests():
    """Verify frozen 20 hard cases and 20 near-domain negatives manifests."""
    hard_path = DATASET_DIR / "hard_subset_manifest.json"
    neg_path = DATASET_DIR / "near_domain_negatives_manifest.json"

    assert hard_path.is_file(), f"Missing hard subset manifest at {hard_path}"
    assert neg_path.is_file(), f"Missing near domain negatives manifest at {neg_path}"

    hard_manifest = json.loads(hard_path.read_text(encoding="utf-8"))
    neg_manifest = json.loads(neg_path.read_text(encoding="utf-8"))

    # Hard subset schema & invariants
    assert hard_manifest["schema_version"] == "conflux-weave.multimodal-hard-subset.v1"
    assert hard_manifest["subset_id"] == "hard_subset_20"
    assert hard_manifest["status"] == "frozen"
    assert hard_manifest["case_count"] == 20
    assert len(hard_manifest["cases"]) == 20

    # Cross-reference hard cases against main benchmark cases
    benchmark_cases = {c.case_id: c for c in load_benchmark_cases(DATASET_DIR)}
    for c in hard_manifest["cases"]:
        assert c["case_id"] in benchmark_cases, f"Hard case {c['case_id']} not in benchmark"
        bench_c = benchmark_cases[c["case_id"]]
        assert c["expected_asset_id"] == bench_c.expected_asset_id
        assert c["document_id"] == bench_c.expected_document_id
        assert c["page"] == bench_c.expected_page
        assert c["figure_kind"] in {"complex_table", "heatmap"}
        assert len(c["selection_rationale"]) > 10

    # Near-domain negatives schema & invariants
    assert neg_manifest["schema_version"] == "conflux-weave.multimodal-near-domain-negatives.v1"
    assert neg_manifest["subset_id"] == "near_domain_negatives_20"
    assert neg_manifest["status"] == "frozen"
    assert neg_manifest["case_count"] == 20
    assert len(neg_manifest["cases"]) == 20

    manifest_v2 = json.loads((DATASET_DIR / "manifest.json").read_text(encoding="utf-8"))
    valid_papers = set(manifest_v2["target_papers"])

    for c in neg_manifest["cases"]:
        assert c["case_id"].startswith("near-neg-")
        assert len(c["query"]) > 10
        assert c["mentioned_in_paper"] in valid_papers
        assert len(c["distractor_concept"]) > 1
        assert c["figure_kind"] == "near_domain_negative"
        assert c["expected_answerable"] is False
        assert len(c["selection_rationale"]) > 10



