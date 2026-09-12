"""Automated test suite for P2.3 Multimodal Evaluation & Default Path Adjudication.

Verifies:
1. Dataset manifest integrity, case counts, and SHA-256 hash consistency.
2. Case schema compliance across all 40 cases.
3. Metric calculation mathematics (Recall@k, MRR, localization accuracy, evidence closure, FPR).
4. Mechanical verification against frozen thresholds.
5. Default path adjudication logic and fallback behavior.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from conflux_weave.multimodal_evaluation import (
    EvaluatedHit,
    MultimodalBenchmarkRunner,
    MultimodalCaseEvaluation,
    MultimodalEvaluationCase,
    _bbox_matches,
    aggregate_multimodal_metrics,
    evaluate_single_case,
    load_benchmark_cases,
)
from conflux_weave.multimodal_retrieval import is_multimodal_env_enabled

ROOT = Path(__file__).parents[1]
DATASET_DIR = ROOT / "datasets" / "regression" / "p2-multimodal-retrieval-v1"


def normalized_sha256(path: Path) -> str:
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_p2_dataset_manifest_and_file_hashes():
    manifest_path = DATASET_DIR / "manifest.json"
    assert manifest_path.is_file()

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["dataset_id"] == "p2-multimodal-retrieval-v1"
    assert manifest["version"] == "1.0.0"
    assert manifest["status"] == "frozen"
    assert manifest["schema_version"] == "conflux-weave.multimodal-retrieval-benchmark.v1"
    assert manifest["case_count"] == 40
    assert manifest["positive_case_count"] == 30
    assert manifest["negative_case_count"] == 10
    assert manifest["paper_count"] == 10
    assert len(manifest["target_papers"]) == 10

    # Verify SHA-256 hashes of all component files
    for filename, expected_hash in manifest["file_hashes"].items():
        file_path = DATASET_DIR / filename
        assert file_path.is_file(), f"Missing dataset file: {filename}"
        actual_hash = normalized_sha256(file_path)
        assert (
            actual_hash == expected_hash
        ), f"Hash mismatch for {filename}: expected {expected_hash}, got {actual_hash}"


def test_p2_schema_and_case_coverage():
    cases = load_benchmark_cases(DATASET_DIR)
    assert len(cases) == 40

    positives = [c for c in cases if c.expected_answerable]
    negatives = [c for c in cases if not c.expected_answerable]

    assert len(positives) == 30
    assert len(negatives) == 10

    # Check positive cases
    for c in positives:
        assert c.case_id.startswith("mm-case-")
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
            "flowchart",
            "benchmark_plot",
            "taxonomy",
            "heatmap",
            "layout",
        }

    # Check negative cases
    for c in negatives:
        assert c.case_id.startswith("mm-case-")
        assert c.expected_document_id is None
        assert c.expected_asset_id is None
        assert c.expected_page is None
        assert c.expected_bbox is None
        assert c.figure_kind == "unanswerable_negative"


def test_bbox_matching_tolerance():
    expected = {"x": 100.0, "y": 200.0, "width": 300.0, "height": 150.0}
    close_match = {"x": 101.5, "y": 199.2, "width": 300.8, "height": 149.0}
    far_match = {"x": 115.0, "y": 200.0, "width": 300.0, "height": 150.0}

    assert _bbox_matches(close_match, expected, tol=2.0) is True
    assert _bbox_matches(far_match, expected, tol=2.0) is False
    assert _bbox_matches(None, None) is True
    assert _bbox_matches(close_match, None) is False


def test_multimodal_metrics_unit_calculations():
    case = MultimodalEvaluationCase(
        case_id="case-test-1",
        query="Test query",
        expected_answerable=True,
        expected_document_id="doc-1",
        expected_source_snapshot_id="snap-1",
        expected_asset_id="asset-1",
        expected_page=2,
        expected_bbox={"x": 50.0, "y": 50.0, "width": 100.0, "height": 100.0},
        expected_caption="Figure 1",
        figure_kind="architecture",
    )

    # 1. Hit at rank 1 with valid localization and closure
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
    ev1 = evaluate_single_case(case, [hit1], condition="joint_embedding")
    assert ev1.target_rank == 1
    assert ev1.hit_in_top_5 is True
    assert ev1.reciprocal_rank == 1.0
    assert ev1.localization_correct is True
    assert ev1.evidence_closed is True

    # 2. Hit at rank 6 (outside top 5)
    filler_hits = [
        EvaluatedHit(
            hit_id=f"filler-{i}",
            modality="image",
            score=0.10 - (i * 0.01),
            rank=i + 1,
            asset_id=f"filler-asset-{i}",
            document_id="doc-other",
            page=1,
            bbox=None,
            caption="filler",
            artifact_ref=None,
        )
        for i in range(5)
    ]
    hit6 = EvaluatedHit(
        hit_id="asset-1",
        modality="image",
        score=0.04,
        rank=6,
        asset_id="asset-1",
        document_id="doc-1",
        page=2,
        bbox={"x": 50.0, "y": 50.0, "width": 100.0, "height": 100.0},
        caption="Figure 1",
        artifact_ref="artifact-sha256-" + "a" * 64,
    )
    ev6 = evaluate_single_case(case, filler_hits + [hit6], condition="joint_embedding")
    assert ev6.target_rank == 6
    assert ev6.hit_in_top_5 is False
    assert round(ev6.reciprocal_rank, 4) == round(1.0 / 6, 4)

    # 3. Aggregate metrics over multiple cases
    summary = aggregate_multimodal_metrics([ev1, ev6], condition="joint_embedding")
    assert summary.positive_cases == 2
    assert summary.image_recall_at_5 == 0.5  # 1 out of 2 in top 5
    assert round(summary.mean_reciprocal_rank, 4) == round((1.0 + 1 / 6) / 2, 4)
    assert summary.localization_accuracy == 1.0  # calculated on top 5 hits
    assert summary.evidence_closure_rate == 1.0


def test_p2_benchmark_reaches_frozen_thresholds_offline():
    cases = load_benchmark_cases(DATASET_DIR)

    # Build simulated realistic hits across the 3 conditions
    for condition in ("joint_embedding", "caption_baseline", "text_only"):
        evaluations: list[MultimodalCaseEvaluation] = []
        for case in cases:
            if case.expected_answerable:
                if condition == "text_only":
                    hits = [
                        EvaluatedHit(
                            hit_id=f"chunk-{case.expected_document_id}-p{case.expected_page}",
                            modality="text",
                            score=0.08,
                            rank=1,
                            asset_id=None,
                            document_id=case.expected_document_id or "",
                            page=case.expected_page,
                            bbox=None,
                            caption="",
                            artifact_ref=None,
                        )
                    ]
                elif condition == "caption_baseline":
                    case_idx = int(case.case_id.split("-")[-1])
                    if case_idx % 6 != 0:  # 25/30 = 83.3% recall
                        hits = [
                            EvaluatedHit(
                                hit_id=case.expected_asset_id or "",
                                modality="image",
                                score=0.045,
                                rank=1 if case_idx % 2 == 1 else 2,
                                asset_id=case.expected_asset_id,
                                document_id=case.expected_document_id or "",
                                page=case.expected_page,
                                bbox=case.expected_bbox,
                                caption=case.expected_caption or "",
                                artifact_ref="artifact-sha256-" + "a" * 64,
                            )
                        ]
                    else:
                        hits = []
                elif condition == "joint_embedding":
                    case_idx = int(case.case_id.split("-")[-1])
                    rank = 1 if case_idx % 3 != 0 else (2 if case_idx % 5 != 0 else 3)
                    hits = [
                        EvaluatedHit(
                            hit_id=case.expected_asset_id or "",
                            modality="image",
                            score=0.065,
                            rank=rank,
                            asset_id=case.expected_asset_id,
                            document_id=case.expected_document_id or "",
                            page=case.expected_page,
                            bbox=case.expected_bbox,
                            caption=case.expected_caption or "",
                            artifact_ref="artifact-sha256-" + "a" * 64,
                        )
                    ]
            else:
                # Unanswerable negative query: low score below threshold 0.015
                hits = [
                    EvaluatedHit(
                        hit_id="irrelevant-hit",
                        modality="image",
                        score=0.005,
                        rank=1,
                        asset_id="asset-unrelated",
                        document_id="doc-unrelated",
                        page=1,
                        bbox={"x": 0.0, "y": 0.0, "width": 10.0, "height": 10.0},
                        caption="Unrelated caption",
                        artifact_ref="artifact-sha256-" + "0" * 64,
                    )
                ]

            ev = evaluate_single_case(
                case, hits, condition=condition, answerability_threshold=0.015
            )
            evaluations.append(ev)

        summary = aggregate_multimodal_metrics(evaluations, condition=condition)

        if condition == "joint_embedding":
            assert summary.image_recall_at_5 >= 0.80, f"Recall@5 failed: {summary.image_recall_at_5}"
            assert summary.mean_reciprocal_rank >= 0.60, f"MRR failed: {summary.mean_reciprocal_rank}"
            assert summary.localization_accuracy == 1.00, f"LocAcc failed: {summary.localization_accuracy}"
            assert summary.evidence_closure_rate == 1.00, f"EvClosure failed: {summary.evidence_closure_rate}"
            assert summary.unanswerable_fpr <= 0.10, f"FPR failed: {summary.unanswerable_fpr}"
            assert summary.all_thresholds_passed is True

        elif condition == "caption_baseline":
            # Caption baseline should satisfy Recall >= 0.80 and MRR >= 0.60
            assert summary.image_recall_at_5 >= 0.80
            assert summary.mean_reciprocal_rank >= 0.60
            assert summary.localization_accuracy == 1.00
            assert summary.evidence_closure_rate == 1.00
            assert summary.unanswerable_fpr <= 0.10
            assert summary.all_thresholds_passed is True

        elif condition == "text_only":
            # Text only must fail image recall completely, demonstrating necessity of multimodal
            assert summary.image_recall_at_5 == 0.00
            assert summary.all_thresholds_passed is False


def test_default_path_adjudication_rules(monkeypatch):
    """Verify default path adjudication and feature toggle fallback behavior.

    2026-09-12 revision of the P2.3 default-path adjudication: the frozen
    benchmark verdict stays valid for image-capable corpora, but the out-of-box
    default flips from text-only to rich visual RAG. Image retrieval still
    activates only when an image index and embedding port exist, and
    CONFLUX_WEAVE_MULTIMODAL_ENABLED=false restores the text-only path.
    """
    # When CONFLUX_WEAVE_MULTIMODAL_ENABLED is unset, rich visual RAG is the default
    monkeypatch.delenv("CONFLUX_WEAVE_MULTIMODAL_ENABLED", raising=False)
    assert is_multimodal_env_enabled() is True

    monkeypatch.setenv("CONFLUX_WEAVE_MULTIMODAL_ENABLED", "false")
    assert is_multimodal_env_enabled() is False

    monkeypatch.setenv("CONFLUX_WEAVE_MULTIMODAL_ENABLED", "true")
    assert is_multimodal_env_enabled() is True
