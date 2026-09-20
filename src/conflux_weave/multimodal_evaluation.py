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

from conflux_weave.multimodal_faithfulness import (
    MultimodalFaithfulnessResult,
    evaluate_visual_faithfulness,
)
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
    generation_ground_truth: dict[str, Any] | None = None

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
            generation_ground_truth=data.get("generation_ground_truth"),
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
    hit_in_top_1: bool = False
    hit_in_top_3: bool = False
    ndcg_at_5: float = 0.0
    text_hit_in_top_5: bool = False
    text_reciprocal_rank: float = 0.0
    generated_answer: str | None = None
    faithfulness: MultimodalFaithfulnessResult | None = None


@dataclass(frozen=True, slots=True)
class MultimodalConditionSummary:
    condition: str
    total_cases: int
    positive_cases: int
    negative_cases: int
    image_recall_at_5: float | None
    mean_reciprocal_rank: float | None
    localization_accuracy: float | None
    evidence_closure_rate: float | None
    unanswerable_fpr: float
    thresholds_met: dict[str, bool]
    all_thresholds_passed: bool
    cases: tuple[MultimodalCaseEvaluation, ...]
    image_recall_at_1: float | None = None
    image_recall_at_3: float | None = None
    ndcg_at_5: float | None = None
    text_recall_at_5: float | None = None
    text_mrr: float | None = None
    mean_factuality_score: float = 0.0
    mean_citation_precision: float = 0.0
    unanswerable_rejection_rate: float = 0.0
    end_to_end_utility_score: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "condition": self.condition,
            "total_cases": self.total_cases,
            "positive_cases": self.positive_cases,
            "negative_cases": self.negative_cases,
            "image_recall_at_1": round(self.image_recall_at_1, 4) if self.image_recall_at_1 is not None else "N/A",
            "image_recall_at_3": round(self.image_recall_at_3, 4) if self.image_recall_at_3 is not None else "N/A",
            "image_recall_at_5": round(self.image_recall_at_5, 4) if self.image_recall_at_5 is not None else "N/A",
            "mean_reciprocal_rank": round(self.mean_reciprocal_rank, 4) if self.mean_reciprocal_rank is not None else "N/A",
            "ndcg_at_5": round(self.ndcg_at_5, 4) if self.ndcg_at_5 is not None else "N/A",
            "localization_accuracy": round(self.localization_accuracy, 4) if self.localization_accuracy is not None else "N/A",
            "evidence_closure_rate": round(self.evidence_closure_rate, 4) if self.evidence_closure_rate is not None else "N/A",
            "text_recall_at_5": round(self.text_recall_at_5, 4) if self.text_recall_at_5 is not None else "N/A",
            "text_mrr": round(self.text_mrr, 4) if self.text_mrr is not None else "N/A",
            "unanswerable_fpr": round(self.unanswerable_fpr, 4),
            "mean_factuality_score": round(self.mean_factuality_score, 4),
            "mean_citation_precision": round(self.mean_citation_precision, 4),
            "unanswerable_rejection_rate": round(self.unanswerable_rejection_rate, 4),
            "end_to_end_utility_score": round(self.end_to_end_utility_score, 4),
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


def compute_bbox_iou(box1: dict[str, float] | None, box2: dict[str, float] | None) -> float:
    """Calculate Intersection over Union (IoU) between two bounding boxes."""
    if not box1 or not box2:
        return 0.0
    x1 = max(float(box1.get("x", 0)), float(box2.get("x", 0)))
    y1 = max(float(box1.get("y", 0)), float(box2.get("y", 0)))
    x2 = min(
        float(box1.get("x", 0)) + float(box1.get("width", 0)),
        float(box2.get("x", 0)) + float(box2.get("width", 0)),
    )
    y2 = min(
        float(box1.get("y", 0)) + float(box1.get("height", 0)),
        float(box2.get("y", 0)) + float(box2.get("height", 0)),
    )
    if x2 <= x1 or y2 <= y1:
        return 0.0
    intersection = (x2 - x1) * (y2 - y1)
    area1 = float(box1.get("width", 0)) * float(box1.get("height", 0))
    area2 = float(box2.get("width", 0)) * float(box2.get("height", 0))
    union = area1 + area2 - intersection
    return intersection / union if union > 0 else 0.0


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
    generated_answer: str | None = None,
) -> MultimodalCaseEvaluation:
    top_score = hits[0].score if hits else 0.0
    predicted_answerable = bool(hits) and (top_score >= answerability_threshold)

    target_rank: int | None = None
    hit_in_top_1 = False
    hit_in_top_3 = False
    hit_in_top_5 = False
    reciprocal_rank = 0.0
    ndcg_at_5 = 0.0
    localization_correct = False
    evidence_closed = False
    text_hit_in_top_5 = False
    text_reciprocal_rank = 0.0

    if case.expected_answerable:
        if condition == "text_only":
            # Pure text condition: evaluate text chunk recall against expected paper
            for idx, h in enumerate(hits):
                rank = idx + 1
                if h.modality == "text" and h.document_id and case.expected_document_id:
                    h_doc = h.document_id.split(":")[0]
                    if h_doc == case.expected_document_id or h.document_id == case.expected_document_id:
                        if target_rank is None:
                            target_rank = rank
                            reciprocal_rank = 1.0 / rank
                            text_reciprocal_rank = 1.0 / rank
                            if rank <= 5:
                                text_hit_in_top_5 = True
                                hit_in_top_5 = True
                        break
        else:
            for idx, h in enumerate(hits):
                rank = idx + 1
                # Target asset must be an image hit
                is_match = False
                if h.modality == "image":
                    if case.expected_asset_id:
                        # 严谨评测：期望图片 ID 明确时，必须精确匹配预期图片 ID 或高精度 BBox，严禁同页错图误判为命中
                        is_match = (h.asset_id == case.expected_asset_id) or (
                            h.document_id == case.expected_document_id
                            and h.page == case.expected_page
                            and _bbox_matches(h.bbox, case.expected_bbox, tol=0.05)
                        )
                    elif case.expected_document_id and case.expected_page is not None:
                        # 未指定具体 asset_id 时，至少需文献、页码与 BBox 一致
                        bbox_ok = _bbox_matches(h.bbox, case.expected_bbox, tol=0.08) if case.expected_bbox else True
                        is_match = (h.document_id == case.expected_document_id and h.page == case.expected_page and bbox_ok)

                if is_match and target_rank is None:
                    target_rank = rank
                    reciprocal_rank = 1.0 / rank
                    if rank == 1:
                        hit_in_top_1 = True
                    if rank <= 3:
                        hit_in_top_3 = True
                    if rank <= 5:
                        hit_in_top_5 = True
                        ndcg_at_5 = 1.0 / math.log2(rank + 1)
                        # Check localization: page match, bbox match (or IoU >= 0.5), valid artifact_ref
                        page_ok = h.page == case.expected_page
                        bbox_ok = _bbox_matches(h.bbox, case.expected_bbox)
                        art_ok = bool(h.artifact_ref and h.artifact_ref.startswith("artifact-sha256-"))
                        localization_correct = page_ok and bbox_ok and art_ok

                        # 严谨证据闭环：必须包含合法 asset_id, artifact_ref, 有效页码, 以及有效 BBox
                        has_valid_bbox = (
                            isinstance(h.bbox, dict)
                            and float(h.bbox.get("width", 0.0)) > 0
                            and float(h.bbox.get("height", 0.0)) > 0
                        )
                        evidence_closed = bool(
                            h.asset_id
                            and art_ok
                            and h.page is not None
                            and h.page >= 1
                            and has_valid_bbox
                        )
                    break
    else:
        # Negative / unanswerable case
        localization_correct = True
        evidence_closed = True

    # Generation & Faithfulness Evaluation
    faithfulness: MultimodalFaithfulnessResult | None = None
    if generated_answer is not None:
        faithfulness = evaluate_visual_faithfulness(
            case_id=case.case_id,
            answer=generated_answer,
            generation_ground_truth=case.generation_ground_truth,
            expected_answerable=case.expected_answerable,
            expected_asset_id=case.expected_asset_id,
        )

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
        hit_in_top_1=hit_in_top_1,
        hit_in_top_3=hit_in_top_3,
        ndcg_at_5=ndcg_at_5,
        text_hit_in_top_5=text_hit_in_top_5,
        text_reciprocal_rank=text_reciprocal_rank,
        generated_answer=generated_answer,
        faithfulness=faithfulness,
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

    if condition == "text_only":
        # Pure text control: Multimodal-specific metrics are N/A (decoupled contract)
        recall_at_1 = None
        recall_at_3 = None
        recall_at_5 = None
        mrr = None
        ndcg = None
        loc_acc = None
        ev_closure = None
        text_recall_5 = (
            sum(1 for e in positives if e.text_hit_in_top_5) / pos_count if pos_count > 0 else 0.0
        )
        text_mrr = (
            sum(e.text_reciprocal_rank for e in positives) / pos_count if pos_count > 0 else 0.0
        )
    elif pos_count > 0:
        recall_at_1 = sum(1 for e in positives if e.hit_in_top_1) / pos_count
        recall_at_3 = sum(1 for e in positives if e.hit_in_top_3) / pos_count
        recall_at_5 = sum(1 for e in positives if e.hit_in_top_5) / pos_count
        mrr = sum(e.reciprocal_rank for e in positives) / pos_count
        ndcg = sum(e.ndcg_at_5 for e in positives) / pos_count

        top_5_pos = [e for e in positives if e.hit_in_top_5]
        if top_5_pos:
            loc_acc = sum(1 for e in top_5_pos if e.localization_correct) / len(top_5_pos)
            ev_closure = sum(1 for e in top_5_pos if e.evidence_closed) / len(top_5_pos)
        else:
            loc_acc = 0.0
            ev_closure = 0.0

        text_recall_5 = None
        text_mrr = None
    else:
        recall_at_1 = 0.0
        recall_at_3 = 0.0
        recall_at_5 = 0.0
        mrr = 0.0
        ndcg = 0.0
        loc_acc = 1.0
        ev_closure = 1.0
        text_recall_5 = None
        text_mrr = None

    pos_faith = [e.faithfulness for e in positives if e.faithfulness is not None]
    mean_factuality = (
        sum(f.factuality_score for f in pos_faith) / len(pos_faith) if pos_faith else 0.0
    )
    mean_citation_prec = (
        sum(f.citation_precision for f in pos_faith) / len(pos_faith) if pos_faith else 0.0
    )

    if neg_count > 0:
        false_positives = sum(1 for e in negatives if e.predicted_answerable)
        unanswerable_fpr = false_positives / neg_count
        neg_faith = [e.faithfulness for e in negatives if e.faithfulness is not None]
        rejection_rate = (
            sum(f.unanswerable_rejection_score for f in neg_faith) / len(neg_faith)
            if neg_faith
            else (1.0 - unanswerable_fpr)
        )
    else:
        unanswerable_fpr = 0.0
        rejection_rate = 1.0

    # Composite End-to-End Utility Score:
    if condition == "text_only":
        end_to_end_utility = (
            0.30 * (text_recall_5 or 0.0)
            + 0.20 * (text_mrr or 0.0)
            + 0.25 * mean_factuality
            + 0.15 * mean_citation_prec
            + 0.10 * rejection_rate
        )
        thresholds_met = {
            "image_recall_at_5": True,  # N/A for text_only: does not penalize
            "mrr": True,                # N/A for text_only
            "localization_accuracy": True,  # N/A for text_only
            "evidence_closure": True,       # N/A for text_only
            "unanswerable_fpr": unanswerable_fpr <= thresholds.get("unanswerable_fpr", 0.10),
        }
    else:
        end_to_end_utility = (
            0.30 * (recall_at_5 or 0.0)
            + 0.20 * (mrr or 0.0)
            + 0.25 * mean_factuality
            + 0.15 * mean_citation_prec
            + 0.10 * rejection_rate
        )
        thresholds_met = {
            "image_recall_at_5": (recall_at_5 or 0.0) >= thresholds.get("image_recall_at_5", 0.80),
            "mrr": (mrr or 0.0) >= thresholds.get("mrr", 0.60),
            "localization_accuracy": (loc_acc or 0.0) >= thresholds.get("localization_accuracy", 1.00),
            "evidence_closure": (ev_closure or 0.0) >= thresholds.get("evidence_closure", 1.00),
            "unanswerable_fpr": unanswerable_fpr <= thresholds.get("unanswerable_fpr", 0.10),
        }

    if "factuality_score" in thresholds:
        thresholds_met["factuality_score"] = mean_factuality >= thresholds["factuality_score"]
    if "citation_precision" in thresholds:
        thresholds_met["citation_precision"] = mean_citation_prec >= thresholds["citation_precision"]

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
        image_recall_at_1=recall_at_1,
        image_recall_at_3=recall_at_3,
        ndcg_at_5=ndcg,
        text_recall_at_5=text_recall_5,
        text_mrr=text_mrr,
        mean_factuality_score=mean_factuality,
        mean_citation_precision=mean_citation_prec,
        unanswerable_rejection_rate=rejection_rate,
        end_to_end_utility_score=end_to_end_utility,
    )


class MultimodalBenchmarkRunner:
    """Runs the benchmark cases across experimental conditions."""

    def __init__(
        self,
        cases: Sequence[MultimodalEvaluationCase],
        *,
        multimodal_pipeline: MultimodalRetrievalPipeline | None = None,
        caption_pipeline: Any | None = None,
        text_pipeline: Any | None = None,
        no_answer_threshold: float = 0.015,
        generation_engine: Any | None = None,
    ) -> None:
        self.cases = tuple(cases)
        self.multimodal_pipeline = multimodal_pipeline
        self.caption_pipeline = caption_pipeline
        self.text_pipeline = text_pipeline
        self.no_answer_threshold = no_answer_threshold
        self.generation_engine = generation_engine

    def run_condition(
        self,
        condition: str,
        *,
        run_generation: bool | None = None,
    ) -> MultimodalConditionSummary:
        should_generate = run_generation if run_generation is not None else (self.generation_engine is not None)
        evaluations: list[MultimodalCaseEvaluation] = []
        for case in self.cases:
            hits = self._execute_query(case.query, condition=condition)
            answer = None
            if should_generate and self.generation_engine is not None:
                try:
                    if callable(self.generation_engine):
                        answer = self.generation_engine(case.query, hits)
                    elif hasattr(self.generation_engine, "generate"):
                        answer = self.generation_engine.generate(case.query, hits)
                except Exception:
                    answer = None
            ev = evaluate_single_case(
                case,
                hits,
                condition=condition,
                answerability_threshold=self.no_answer_threshold,
                generated_answer=answer,
            )
            evaluations.append(ev)
        return aggregate_multimodal_metrics(evaluations, condition=condition)

    def run_all(self) -> dict[str, MultimodalConditionSummary]:
        return {
            "text_only": self.run_condition("text_only"),
            "caption_baseline": self.run_condition("caption_baseline"),
            "joint_embedding": self.run_condition("joint_embedding"),
        }


class MultimodalEndToEndBenchmarkRunner(MultimodalBenchmarkRunner):
    """Enhanced benchmark runner for Benchmark v2 supporting full end-to-end evaluation."""

    def __init__(
        self,
        dataset_path: Path,
        *,
        multimodal_pipeline: MultimodalRetrievalPipeline | None = None,
        caption_pipeline: Any | None = None,
        text_pipeline: Any | None = None,
        generation_engine: Any | None = None,
        no_answer_threshold: float = 0.015,
    ) -> None:
        self.dataset_path = Path(dataset_path)
        cases = load_benchmark_cases(self.dataset_path)
        super().__init__(
            cases,
            multimodal_pipeline=multimodal_pipeline,
            caption_pipeline=caption_pipeline,
            text_pipeline=text_pipeline,
            no_answer_threshold=no_answer_threshold,
            generation_engine=generation_engine,
        )
        self.manifest = self._load_manifest()

    def _load_manifest(self) -> dict[str, Any]:
        manifest_file = self.dataset_path / "manifest.json"
        if manifest_file.exists():
            try:
                return json.loads(manifest_file.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {}

    def run_benchmark_report(
        self,
        conditions: Sequence[str] = ("text_only", "caption_baseline", "joint_embedding"),
    ) -> dict[str, MultimodalConditionSummary]:
        results = {}
        for cond in conditions:
            results[cond] = self.run_condition(cond)
        return results

    def format_markdown_scorecard(
        self,
        summaries: dict[str, MultimodalConditionSummary],
    ) -> str:
        """Format an academic benchmark scoreboard comparing conditions with N/A support."""
        def _fmt(val: float | None) -> str:
            return f"{val:.2f}" if val is not None else "N/A"

        lines = [
            "# Multimodal RAG Benchmark v2 Evaluation Scorecard",
            "",
            "| Condition | Recall@1 | Recall@3 | Recall@5 | MRR | nDCG@5 | Loc Acc | Ev Closure | Unanswerable FPR | Factuality | Citation Prec | Utility | Verdict |",
            "| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |",
        ]
        for cond, s in summaries.items():
            verdict = "PASSED" if s.all_thresholds_passed else "FAILED"
            lines.append(
                f"| `{cond}` | {_fmt(s.image_recall_at_1)} | {_fmt(s.image_recall_at_3)} | {_fmt(s.image_recall_at_5)} | "
                f"{_fmt(s.mean_reciprocal_rank)} | {_fmt(s.ndcg_at_5)} | {_fmt(s.localization_accuracy)} | "
                f"{_fmt(s.evidence_closure_rate)} | {s.unanswerable_fpr:.2f} | {s.mean_factuality_score:.2f} | "
                f"{s.mean_citation_precision:.2f} | {s.end_to_end_utility_score:.2f} | **{verdict}** |"
            )
        return "\n".join(lines)


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
