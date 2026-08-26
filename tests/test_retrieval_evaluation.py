from conflux_weave.retrieval import RetrievalHit, RetrievalQueryResult, RetrievalStrategy
from conflux_weave.retrieval_evaluation import aggregate_paper_metrics, paper_rank, predicts_answer


def test_paper_metrics_deduplicate_chunks_from_same_source():
    result = RetrievalQueryResult("q", RetrievalStrategy.HYBRID, (
        RetrievalHit("a-1", 0.9, 1, "paper-a", {"page": 1}),
        RetrievalHit("a-2", 0.8, 2, "paper-a", {"page": 2}),
        RetrievalHit("b-1", 0.7, 3, "paper-b", {"page": 1}),
    ))
    assert paper_rank(result, frozenset({"paper-b"}), k=3) == 2
    metrics = aggregate_paper_metrics([1, 2, None], k=3)
    assert metrics.recall_at_k == 2 / 3
    assert predicts_answer(result, threshold=0.85) is True
