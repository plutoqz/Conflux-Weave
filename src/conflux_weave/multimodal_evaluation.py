"""Multimodal retrieval benchmark evaluation engine (P2.3).

Computes frozen acceptance metrics across three experimental conditions:
1. text_only (baseline pure text RAG)
2. caption_baseline (text search over extracted captions and parent chunks)
3. joint_embedding (multimodal image embedding + text hybrid RRF fusion)
"""
from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Sequence

from conflux_weave.multimodal_retrieval import (
    MultimodalFusionHit,
    MultimodalRetrievalPipeline,
    MultimodalRetrievalRun,
)
from conflux_weave.retrieval import RetrievalQueryResult


@dataclass(frozen=True, slots=True)
class MultimodalEvaluationCase:
    case_id: str
    query: str
    expected_answerable: bool
    expected_document_id: str | None
    expected_source_snapshot_id: str | None
    expected_asset_id: str | None
    expected_page: int | None
    expected_bbox: dict[str, float] | None
    expected_caption: str | None
    figure_kind: str
    split: str = "test"
    label_source: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MultimodalEvaluationCase:
        return cls(
            case_id=data["case_id"],
            query=data["query"],
            expected_answerable=data["expected_answerable"],
            expected_document_id=data.get("expected_document_id"),
            expected_source_snapshot_id=data.get("expected_source_snapshot_id"),
            expected_asset_id=data.get("expected_asset_id"),
            expected_page=data.get("expected_page"),
            expected_bbox=data.get("expected_bbox"),
            expected_caption=data.get("expected_caption"),
            figure_kind=data.get("figure_kind", "unknown"),
            split=data.get("split", "test"),
            label_source=data.get("label_source", ""),
        )


@dataclass(frozen=True, slots=True)
class EvaluatedHit:
    hit_id: str
    modality: str
    score: float
    rank: int
    asset_id: str | None
    document_id: str
    page: int | None
    bbox: dict[str, float] | None
    caption: str
    artifact_ref: str | None


@dataclass(frozen=True, slots=True)
class MultimodalCaseEvaluation:
    case_id: str
    query: str
    condition: str
    expected_answerable: bool
    predicted_answerable: bool
    target_rank: int | None  # 1-based rank of expected asset in hits, or None
    hit_in_top_5: bool
    reciprocal_rank: float
    localization_correct: bool
    evidence_closed: bool
    top_score: float
    hits: tuple[EvaluatedHit, ...]


@dataclass(frozen=True, slots=True)
class MultimodalConditionSummary:
    condition: str
    total_cases: int
    positive_cases: int
    negative_cases: int
    image_recall_at_5: float
    mean_reciprocal_rank: float
    localization_accuracy: float
    evidence_closure_rate: float
    unanswerable_fpr: float
    thresholds_met: dict[str, bool]
    all_thresholds_passed: bool
    cases: tuple[MultimodalCaseEvaluation, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "condition": self.condition,
            "total_cases": self.total_cases,
            "positive_cases": self.positive_cases,
            "negative_cases": self.negative_cases,
            "image_recall_at_5": round(self.image_recall_at_5, 4),
            "mean_reciprocal_rank": round(self.mean_reciprocal_rank, 4),
            "localization_accuracy": round(self.localization_accuracy, 4),
            "evidence_closure_rate": round(self.evidence_closure_rate, 4),
            "unanswerable_fpr": round(self.unanswerable_fpr, 4),
            "thresholds_met": self.thresholds_met,
            "all_thresholds_passed": self.all_thresholds_passed,
        }


def load_benchmark_cases(dataset_path: Path) -> tuple[MultimodalEvaluationCase, ...]:
    cases_file = dataset_path / "cases.jsonl"
    if not cases_file.exists():
        raise FileNotFoundError(f"cases.jsonl not found at {cases_file}")
    cases = []
    for line in cases_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            cases.append(MultimodalEvaluationCase.from_dict(json.loads(line)))
    return tuple(cases)


def _bbox_matches(
    actual: dict[str, float] | None, expected: dict[str, float] | None, tol: float = 2.0
) -> bool:
    if actual is None and expected is None:
        return True
    if actual is None or expected is None:
        return False
    for k in ("x", "y", "width", "height"):
        if abs(float(actual.get(k, 0.0)) - float(expected.get(k, 0.0))) > tol:
            return False
    return True


def evaluate_single_case(
    case: MultimodalEvaluationCase,
    hits: Sequence[EvaluatedHit],
    *,
    condition: str,
    answerability_threshold: float = 0.015,
) -> MultimodalCaseEvaluation:
    top_score = hits[0].score if hits else 0.0
    predicted_answerable = bool(hits) and (top_score >= answerability_threshold)

    target_rank: int | None = None
    hit_in_top_5 = False
    reciprocal_rank = 0.0
    localization_correct = False
    evidence_closed = False

    if case.expected_answerable:
        for idx, h in enumerate(hits):
            rank = idx + 1
            # Target asset must be an image hit
            is_match = False
            if h.modality == "image":
                if case.expected_asset_id and h.asset_id == case.expected_asset_id:
                    is_match = True
                elif h.document_id == case.expected_document_id and h.page == case.expected_page:
                    is_match = True

            if is_match and target_rank is None:
                target_rank = rank
                reciprocal_rank = 1.0 / rank
                if rank <= 5:
                    hit_in_top_5 = True
                    # Check localization: page match, bbox match, valid artifact_ref
                    page_ok = h.page == case.expected_page
                    bbox_ok = _bbox_matches(h.bbox, case.expected_bbox)
                    art_ok = bool(h.artifact_ref and h.artifact_ref.startswith("artifact-sha256-"))
                    localization_correct = page_ok and bbox_ok and art_ok

                    # Evidence closure: must have asset_id, artifact_ref, page & bbox
                    evidence_closed = bool(
                        h.asset_id and h.artifact_ref and h.page is not None and h.bbox is not None
                    )
                break
    else:
        # Negative / unanswerable case
        localization_correct = True
        evidence_closed = True

    return MultimodalCaseEvaluation(
        case_id=case.case_id,
        query=case.query,
        condition=condition,
        expected_answerable=case.expected_answerable,
        predicted_answerable=predicted_answerable,
        target_rank=target_rank,
        hit_in_top_5=hit_in_top_5,
        reciprocal_rank=reciprocal_rank,
        localization_correct=localization_correct,
        evidence_closed=evidence_closed,
        top_score=top_score,
        hits=tuple(hits),
    )


def aggregate_multimodal_metrics(
    evaluations: Sequence[MultimodalCaseEvaluation],
    *,
    condition: str,
    thresholds: dict[str, float] | None = None,
) -> MultimodalConditionSummary:
    if thresholds is None:
        thresholds = {
            "image_recall_at_5": 0.80,
            "mrr": 0.60,
            "localization_accuracy": 1.00,
            "evidence_closure": 1.00,
            "unanswerable_fpr": 0.10,
        }

    total_cases = len(evaluations)
    positives = [e for e in evaluations if e.expected_answerable]
    negatives = [e for e in evaluations if not e.expected_answerable]

    pos_count = len(positives)
    neg_count = len(negatives)

    if pos_count > 0:
        recall_at_5 = sum(1 for e in positives if e.hit_in_top_5) / pos_count
        mrr = sum(e.reciprocal_rank for e in positives) / pos_count
        top_5_pos = [e for e in positives if e.hit_in_top_5]
        if top_5_pos:
            loc_acc = sum(1 for e in top_5_pos if e.localization_correct) / len(top_5_pos)
            ev_closure = sum(1 for e in top_5_pos if e.evidence_closed) / len(top_5_pos)
        else:
            loc_acc = 0.0
            ev_closure = 0.0
    else:
        recall_at_5 = 0.0
        mrr = 0.0
        loc_acc = 1.0
        ev_closure = 1.0

    if neg_count > 0:
        false_positives = sum(1 for e in negatives if e.predicted_answerable)
        unanswerable_fpr = false_positives / neg_count
    else:
        unanswerable_fpr = 0.0

    thresholds_met = {
        "image_recall_at_5": recall_at_5 >= thresholds.get("image_recall_at_5", 0.80),
        "mrr": mrr >= thresholds.get("mrr", 0.60),
        "localization_accuracy": loc_acc >= thresholds.get("localization_accuracy", 1.00),
        "evidence_closure": ev_closure >= thresholds.get("evidence_closure", 1.00),
        "unanswerable_fpr": unanswerable_fpr <= thresholds.get("unanswerable_fpr", 0.10),
    }

    all_passed = all(thresholds_met.values())

    return MultimodalConditionSummary(
        condition=condition,
        total_cases=total_cases,
        positive_cases=pos_count,
        negative_cases=neg_count,
        image_recall_at_5=recall_at_5,
        mean_reciprocal_rank=mrr,
        localization_accuracy=loc_acc,
        evidence_closure_rate=ev_closure,
        unanswerable_fpr=unanswerable_fpr,
        thresholds_met=thresholds_met,
        all_thresholds_passed=all_passed,
        cases=tuple(evaluations),
    )


class MultimodalBenchmarkRunner:
    """Runs the 40 benchmark cases across the three specified experimental conditions."""

    def __init__(
        self,
        cases: Sequence[MultimodalEvaluationCase],
        *,
        multimodal_pipeline: MultimodalRetrievalPipeline | None = None,
        caption_pipeline: Any | None = None,
        text_pipeline: Any | None = None,
        no_answer_threshold: float = 0.015,
    ) -> None:
        self.cases = tuple(cases)
        self.multimodal_pipeline = multimodal_pipeline
        self.caption_pipeline = caption_pipeline
        self.text_pipeline = text_pipeline
        self.no_answer_threshold = no_answer_threshold

    def run_condition(self, condition: str) -> MultimodalConditionSummary:
        evaluations: list[MultimodalCaseEvaluation] = []
        for case in self.cases:
            hits = self._execute_query(case.query, condition=condition)
            ev = evaluate_single_case(
                case, hits, condition=condition, answerability_threshold=self.no_answer_threshold
            )
            evaluations.append(ev)
        return aggregate_multimodal_metrics(evaluations, condition=condition)

    def run_all(self) -> dict[str, MultimodalConditionSummary]:
        return {
            "text_only": self.run_condition("text_only"),
            "caption_baseline": self.run_condition("caption_baseline"),
            "joint_embedding": self.run_condition("joint_embedding"),
        }

    def _execute_query(self, query: str, *, condition: str) -> list[EvaluatedHit]:
        if condition == "text_only":
            return self._execute_text_only(query)
        elif condition == "caption_baseline":
            return self._execute_caption_baseline(query)
        elif condition == "joint_embedding":
            return self._execute_joint_embedding(query)
        else:
            raise ValueError(f"Unknown evaluation condition: {condition}")

    def _execute_text_only(self, query: str) -> list[EvaluatedHit]:
        """In pure text-only condition, no image assets are indexed or returned."""
        if self.text_pipeline is None:
            return []
        res = self.text_pipeline.search(query)
        hits: list[EvaluatedHit] = []
        for idx, h in enumerate(res.final.hits if hasattr(res, "final") else res.hits):
            hits.append(
                EvaluatedHit(
                    hit_id=h.document_id,
                    modality="text",
                    score=float(h.score),
                    rank=idx + 1,
                    asset_id=None,
                    document_id=h.document_id,
                    page=h.locator.get("page") if h.locator else None,
                    bbox=None,
                    caption="",
                    artifact_ref=None,
                )
            )
        return hits

    def _execute_caption_baseline(self, query: str) -> list[EvaluatedHit]:
        """Search image assets based strictly on matching against their captions."""
        if self.caption_pipeline is not None:
            return self.caption_pipeline(query)
        if self.multimodal_pipeline is not None:
            raw_hits = self.multimodal_pipeline.search_images_by_text(query, top_k=5)
            hits: list[EvaluatedHit] = []
            for idx, h in enumerate(raw_hits):
                hits.append(
                    EvaluatedHit(
                        hit_id=h.asset_id,
                        modality="image",
                        score=float(h.score),
                        rank=idx + 1,
                        asset_id=h.asset_id,
                        document_id=h.document_id,
                        page=h.page,
                        bbox=h.bbox,
                        caption=h.caption,
                        artifact_ref=h.artifact_ref,
                    )
                )
            return hits
        return []

    def _execute_joint_embedding(self, query: str) -> list[EvaluatedHit]:
        """Full multimodal pipeline with image vector retrieval and text hybrid RRF fusion."""
        if self.multimodal_pipeline is None:
            return []
        run: MultimodalRetrievalRun = self.multimodal_pipeline.search(query, top_k=10)
        hits: list[EvaluatedHit] = []
        for idx, h in enumerate(run.fused_hits):
            hits.append(
                EvaluatedHit(
                    hit_id=h.hit_id,
                    modality=h.modality,
                    score=float(h.score),
                    rank=idx + 1,
                    asset_id=h.asset_id,
                    document_id=h.document_id,
                    page=h.page,
                    bbox=h.bbox,
                    caption=h.caption,
                    artifact_ref=h.artifact_ref,
                )
            )
        return hits
