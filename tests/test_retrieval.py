import pytest

from conflux_weave.retrieval import (
    BM25Retriever,
    RetrievalCase,
    RetrievalDocument,
    RetrievalStrategy,
    evaluate_retrieval,
)


DOCUMENTS = (
    RetrievalDocument(
        "agent-context",
        "Agent memory and context compression for long-context LLM systems.",
        source_snapshot_id="source-agent",
        locator={"type": "fixture", "record": 1},
    ),
    RetrievalDocument(
        "gis-agent",
        "GIS spatial analysis agents evaluate geospatial planning tasks.",
        source_snapshot_id="source-gis",
        locator={"type": "fixture", "record": 2},
    ),
    RetrievalDocument(
        "messaging",
        "Kafka and RabbitMQ provide messaging workflows for distributed systems.",
        source_snapshot_id="source-messaging",
        locator={"type": "fixture", "record": 3},
    ),
)


def test_bm25_is_deterministic_and_preserves_locator_metadata() -> None:
    retriever = BM25Retriever(DOCUMENTS)

    first = retriever.search("context compression agent", top_k=2)
    second = retriever.search("context compression agent", top_k=2)

    assert first == second
    assert first.strategy is RetrievalStrategy.BM25
    assert first.hits[0].document_id == "agent-context"
    assert first.hits[0].rank == 1
    assert first.hits[0].source_snapshot_id == "source-agent"
    assert first.hits[0].locator == {"type": "fixture", "record": 1}


def test_evaluation_records_recall_and_mrr_for_frozen_cases() -> None:
    evaluation = evaluate_retrieval(
        BM25Retriever(DOCUMENTS),
        (
            RetrievalCase(
                "case-context", "long-context memory", frozenset({"agent-context"})
            ),
            RetrievalCase(
                "case-messaging", "Kafka messaging", frozenset({"messaging"})
            ),
        ),
        k=2,
    )

    assert evaluation == evaluation.__class__(
        strategy=RetrievalStrategy.BM25,
        case_count=2,
        recall_at_k=1.0,
        mean_reciprocal_rank=1.0,
        hit_count=2,
        k=2,
    )


@pytest.mark.parametrize(
    ("factory", "message"),
    [
        (lambda: BM25Retriever(()), "documents must not be empty"),
        (
            lambda: BM25Retriever((RetrievalDocument("same", "one"), RetrievalDocument("same", "two"))),
            "document_id values must be unique",
        ),
    ],
)
def test_index_rejects_invalid_documents(factory, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        factory()


def test_search_rejects_invalid_query_and_top_k() -> None:
    retriever = BM25Retriever(DOCUMENTS)
    with pytest.raises(ValueError, match="query must not be empty"):
        retriever.search(" ")
    with pytest.raises(ValueError, match="top_k must be positive"):
        retriever.search("agent", top_k=0)


def test_search_does_not_return_zero_score_documents() -> None:
    result = BM25Retriever(DOCUMENTS).search("unmatched-token")

    assert result.hits == ()
