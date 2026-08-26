"""Paper-level retrieval metrics for the frozen S1 real-corpus cases."""
from __future__ import annotations

import math
from dataclasses import dataclass

from conflux_weave.retrieval import RetrievalQueryResult


@dataclass(frozen=True, slots=True)
class PaperRetrievalMetrics:
    case_count: int
    recall_at_k: float
    mean_reciprocal_rank: float
    ndcg_at_k: float


def paper_rank(result: RetrievalQueryResult, relevant_sources: frozenset[str], *, k: int) -> int | None:
    seen: set[str] = set()
    paper_rank_value = 0
    for hit in result.hits:
        source = hit.source_snapshot_id
        if not source or source in seen:
            continue
        seen.add(source); paper_rank_value += 1
        if source in relevant_sources:
            return paper_rank_value
        if paper_rank_value >= k:
            break
    return None


def aggregate_paper_metrics(ranks: list[int | None], *, k: int) -> PaperRetrievalMetrics:
    if not ranks or k <= 0:
        raise ValueError("ranks and k must be positive")
    hits = [rank for rank in ranks if rank is not None and rank <= k]
    return PaperRetrievalMetrics(len(ranks), len(hits) / len(ranks), sum(0.0 if rank is None or rank > k else 1 / rank for rank in ranks) / len(ranks), sum(0.0 if rank is None or rank > k else 1 / math.log2(rank + 1) for rank in ranks) / len(ranks))


def predicts_answer(result: RetrievalQueryResult, *, threshold: float) -> bool:
    return bool(result.hits) and result.hits[0].score >= threshold
