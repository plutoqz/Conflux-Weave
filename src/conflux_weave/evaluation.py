"""Deterministic W2 quality support tied to retrieval and delivery decisions."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from conflux_weave.retrieval import (
    BM25Retriever,
    RetrievalCase,
    RetrievalDocument,
    TokenizationMode,
    evaluate_retrieval,
)


@dataclass(frozen=True, slots=True)
class EvaluationRetrievalCase:
    case_id: str
    query: str
    relevant_document_ids: frozenset[str]
    split: str
    language: str


@dataclass(frozen=True, slots=True)
class RetrievalSliceMetrics:
    case_count: int
    recall_at_k: float
    mean_reciprocal_rank: float


@dataclass(frozen=True, slots=True)
class RetrievalComparison:
    k: int
    before: dict[str, RetrievalSliceMetrics]
    after: dict[str, RetrievalSliceMetrics]


@dataclass(frozen=True, slots=True)
class DeliveryExpectation:
    case_id: str
    outcome: str
    support_status: str
    source_trust: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DeliveryObservation:
    case_id: str
    outcome: str
    support_status: str
    source_trust: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DeliveryMetrics:
    case_count: int
    outcome_exact_match: float
    support_exact_match: float
    trust_exact_match: float


@dataclass(frozen=True, slots=True)
class ReadabilityMetrics:
    inline_citation_count: int
    has_support_legend: bool
    has_trust_legend: bool
    has_evidence_summary: bool


def compare_bm25_tokenization(
    documents: Iterable[RetrievalDocument],
    cases: Iterable[EvaluationRetrievalCase],
    *,
    k: int = 3,
) -> RetrievalComparison:
    frozen_documents = tuple(documents)
    frozen_cases = tuple(cases)
    if not frozen_cases:
        raise ValueError("cases must not be empty")
    before = BM25Retriever(
        frozen_documents,
        tokenization_mode=TokenizationMode.LEGACY_CONTIGUOUS_CJK,
    )
    after = BM25Retriever(
        frozen_documents,
        tokenization_mode=TokenizationMode.CJK_UNIGRAM_BIGRAM,
    )
    return RetrievalComparison(
        k=k,
        before=_evaluate_slices(before, frozen_cases, k),
        after=_evaluate_slices(after, frozen_cases, k),
    )


def evaluate_delivery(
    expectations: Iterable[DeliveryExpectation],
    observations: Iterable[DeliveryObservation],
) -> DeliveryMetrics:
    expected = {item.case_id: item for item in expectations}
    observed = {item.case_id: item for item in observations}
    if not expected or set(expected) != set(observed):
        raise ValueError("expectations and observations must contain identical case ids")
    count = len(expected)
    return DeliveryMetrics(
        case_count=count,
        outcome_exact_match=sum(
            observed[key].outcome == item.outcome for key, item in expected.items()
        )
        / count,
        support_exact_match=sum(
            observed[key].support_status == item.support_status
            for key, item in expected.items()
        )
        / count,
        trust_exact_match=sum(
            tuple(sorted(observed[key].source_trust)) == tuple(sorted(item.source_trust))
            for key, item in expected.items()
        )
        / count,
    )


def evaluate_report_readability(report: str) -> ReadabilityMetrics:
    body, separator, _ = report.partition("## Evidence 汇总")
    return ReadabilityMetrics(
        inline_citation_count=len(re.findall(r"\[\d+\]", body)),
        has_support_legend="图例：●" in report,
        has_trust_legend="来源：A" in report,
        has_evidence_summary=bool(separator),
    )


def _evaluate_slices(
    retriever: BM25Retriever,
    cases: tuple[EvaluationRetrievalCase, ...],
    k: int,
) -> dict[str, RetrievalSliceMetrics]:
    groups: dict[str, tuple[EvaluationRetrievalCase, ...]] = {"overall": cases}
    for field in ("split", "language"):
        values = sorted({getattr(case, field) for case in cases})
        groups.update(
            {
                f"{field}:{value}": tuple(
                    case for case in cases if getattr(case, field) == value
                )
                for value in values
            }
        )
    result: dict[str, RetrievalSliceMetrics] = {}
    for name, items in groups.items():
        metrics = evaluate_retrieval(
            retriever,
            (
                RetrievalCase(case.case_id, case.query, case.relevant_document_ids)
                for case in items
            ),
            k=k,
        )
        result[name] = RetrievalSliceMetrics(
            metrics.case_count,
            metrics.recall_at_k,
            metrics.mean_reciprocal_rank,
        )
    return result
