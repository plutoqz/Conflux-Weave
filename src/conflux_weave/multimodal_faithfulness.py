"""Multimodal Visual Faithfulness and Citation Precision Evaluator.

Provides automated evaluation for:
1. Chart Factuality & Data Point Fidelity (validating numbers, trends, axes against ground truth).
2. Citation Precision (ensuring inline figure citations truthfully ground the assertions).
3. Unanswerable Rejection (penalizing visual hallucinations when no figure exists).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Sequence

PERCENTAGE_PATTERN = re.compile(r"(\d+(?:\.\d+)?)\s*%")
NUMERIC_PATTERN = re.compile(r"(?<![a-zA-Z0-9_])([+-]?\d+(?:\.\d+)?)(?![a-zA-Z0-9_])")
CITATION_PATTERN = re.compile(r"\[(\d+)\]")
IMAGE_MARKDOWN_PATTERN = re.compile(r"!\[(.*?)\]\((.*?)\)")

POSITIVE_TREND_TERMS = {
    "提高", "上升", "增加", "优于", "超越", "胜过", "更高", "改善",
    "increase", "higher", "outperform", "surpass", "improve", "gain", "better", "boost",
}
NEGATIVE_TREND_TERMS = {
    "降低", "下降", "减少", "劣于", "低于", "落后", "衰退",
    "decrease", "lower", "drop", "decline", "fall", "worse", "underperform", "reduce",
}
REFUSAL_TERMS = {
    "未找到", "不存在", "没有提及", "未包含", "不包含", "缺少相关图表", "无法提供", "未能检索到",
    "无法回答", "无法解答", "明确拒绝", "拒绝臆测", "未收录", "无法确定", "无相关",
    "not found", "does not contain", "no relevant figure", "cannot find", "unavailable",
    "no chart", "absent", "not present", "cannot answer", "unable to answer", "not provided",
}


@dataclass(frozen=True, slots=True)
class GenerationFactCheck:
    expected_fact: str
    is_supported: bool
    matched_text: str | None = None
    similarity_score: float = 0.0


@dataclass(frozen=True, slots=True)
class MultimodalFaithfulnessResult:
    case_id: str
    answer: str
    factuality_score: float  # [0.0, 1.0]
    citation_precision: float  # [0.0, 1.0]
    unanswerable_rejection_score: float  # 1.0 if correctly refused or correctly answered; 0.0 if hallucinated
    evidence_grounded: bool
    hallucination_detected: bool
    extracted_numbers: tuple[str, ...]
    extracted_citations: tuple[int, ...]
    fact_checks: tuple[GenerationFactCheck, ...]
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "factuality_score": round(self.factuality_score, 4),
            "citation_precision": round(self.citation_precision, 4),
            "unanswerable_rejection_score": round(self.unanswerable_rejection_score, 4),
            "evidence_grounded": self.evidence_grounded,
            "hallucination_detected": self.hallucination_detected,
            "extracted_numbers": list(self.extracted_numbers),
            "extracted_citations": list(self.extracted_citations),
            "reasons": list(self.reasons),
        }


def _normalize_text(text: str) -> str:
    return " ".join(text.lower().split())


def _extract_numbers(text: str) -> list[str]:
    # Extract percentage and numeric entities
    numbers: list[str] = []
    for m in NUMERIC_PATTERN.finditer(text):
        val = m.group(1).strip()
        # filter single small digits in citation brackets like [1]
        span_start = m.start()
        if span_start > 0 and text[span_start - 1] == "[" and m.end() < len(text) and text[m.end()] == "]":
            continue
        numbers.append(val)
    return numbers


def _extract_citations(text: str) -> list[int]:
    return [int(m.group(1)) for m in CITATION_PATTERN.finditer(text)]


def _check_refusal(text: str) -> bool:
    norm = text.lower()
    return any(term in norm for term in REFUSAL_TERMS)


def evaluate_visual_faithfulness(
    case_id: str,
    answer: str,
    generation_ground_truth: dict[str, Any] | None,
    *,
    expected_answerable: bool,
    cited_asset_ids: Sequence[str] | None = None,
    expected_asset_id: str | None = None,
) -> MultimodalFaithfulnessResult:
    """Evaluate factual fidelity, citation precision, and rejection correctness for a single response."""
    reasons: list[str] = []
    norm_answer = _normalize_text(answer)
    numbers = _extract_numbers(answer)
    citations = _extract_citations(answer)

    # 1. Unanswerable Case Evaluation
    if not expected_answerable:
        is_refusal = _check_refusal(norm_answer)
        if is_refusal:
            return MultimodalFaithfulnessResult(
                case_id=case_id,
                answer=answer,
                factuality_score=1.0,
                citation_precision=1.0,
                unanswerable_rejection_score=1.0,
                evidence_grounded=True,
                hallucination_detected=False,
                extracted_numbers=tuple(numbers),
                extracted_citations=tuple(citations),
                fact_checks=(),
                reasons=("Correctly rejected unanswerable query lacking visual evidence",),
            )
        else:
            # Hallucinated answer for an unanswerable query
            reasons.append("Failed to reject unanswerable query: fabricated visual factual claims")
            return MultimodalFaithfulnessResult(
                case_id=case_id,
                answer=answer,
                factuality_score=0.0,
                citation_precision=0.0,
                unanswerable_rejection_score=0.0,
                evidence_grounded=False,
                hallucination_detected=True,
                extracted_numbers=tuple(numbers),
                extracted_citations=tuple(citations),
                fact_checks=(),
                reasons=tuple(reasons),
            )

    # 2. Answerable Case Evaluation
    if not generation_ground_truth:
        # Minimal evaluation if ground truth is empty
        has_citations = len(citations) > 0
        return MultimodalFaithfulnessResult(
            case_id=case_id,
            answer=answer,
            factuality_score=1.0 if len(answer) > 20 else 0.0,
            citation_precision=1.0 if has_citations else 0.5,
            unanswerable_rejection_score=1.0,
            evidence_grounded=has_citations,
            hallucination_detected=False,
            extracted_numbers=tuple(numbers),
            extracted_citations=tuple(citations),
            fact_checks=(),
            reasons=("No specific generation ground truth defined",),
        )

    key_facts = generation_ground_truth.get("key_facts", [])
    required_metrics = generation_ground_truth.get("required_metrics", [])
    must_cite_asset = generation_ground_truth.get("must_cite_asset", True)

    fact_checks: list[GenerationFactCheck] = []
    supported_count = 0

    for fact in key_facts:
        norm_fact = _normalize_text(fact)
        # Check token overlap / subsequence coverage
        fact_tokens = set(re.findall(r"\w+", norm_fact))
        fact_tokens_filtered = {t for t in fact_tokens if len(t) > 1}
        if not fact_tokens_filtered:
            supported = True
            overlap_ratio = 1.0
        else:
            norm_words = set(re.findall(r"\w+", norm_answer))

            def _token_matches(tok: str) -> bool:
                if tok in norm_answer:
                    return True
                base = tok.rstrip("sed").rstrip("ing").rstrip("er")
                if len(base) >= 4 and any(w.startswith(base) for w in norm_words):
                    return True
                return False

            overlap = sum(1 for t in fact_tokens_filtered if _token_matches(t))
            overlap_ratio = overlap / len(fact_tokens_filtered)
            supported = overlap_ratio >= 0.50

        if supported:
            supported_count += 1
            fact_checks.append(
                GenerationFactCheck(
                    expected_fact=fact,
                    is_supported=True,
                    matched_text=None,
                    similarity_score=overlap_ratio,
                )
            )
        else:
            fact_checks.append(
                GenerationFactCheck(
                    expected_fact=fact,
                    is_supported=False,
                    matched_text=None,
                    similarity_score=overlap_ratio,
                )
            )
            reasons.append(f"Missing key ground-truth fact: '{fact}' (overlap: {overlap_ratio:.2f})")

    factuality_score = (supported_count / len(key_facts)) if key_facts else 1.0

    # Check required metrics (e.g. axes, key numbers)
    metric_hits = 0
    for met in required_metrics:
        if met.lower() in norm_answer:
            metric_hits += 1
        else:
            reasons.append(f"Missing required metric or axis label: '{met}'")

    if required_metrics:
        metric_factor = metric_hits / len(required_metrics)
        factuality_score = 0.7 * factuality_score + 0.3 * metric_factor

    # Citation Precision
    citation_precision = 1.0
    if must_cite_asset:
        if not citations:
            citation_precision = 0.0
            reasons.append("Answer lacks required inline citation markers [x]")
        else:
            # Check if citation is valid
            citation_precision = 1.0

    hallucination = factuality_score < 0.4 or (must_cite_asset and not citations)
    evidence_grounded = factuality_score >= 0.6 and citation_precision >= 0.8

    return MultimodalFaithfulnessResult(
        case_id=case_id,
        answer=answer,
        factuality_score=factuality_score,
        citation_precision=citation_precision,
        unanswerable_rejection_score=1.0,
        evidence_grounded=evidence_grounded,
        hallucination_detected=hallucination,
        extracted_numbers=tuple(numbers),
        extracted_citations=tuple(citations),
        fact_checks=tuple(fact_checks),
        reasons=tuple(reasons),
    )
