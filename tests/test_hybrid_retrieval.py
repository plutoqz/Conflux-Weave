import json
from types import SimpleNamespace

from conflux_weave.hybrid_retrieval import HybridRetrievalPipeline
from conflux_weave.indexing import LanceDBDenseIndex
from conflux_weave.provider import (
    OpenAICompatibleEmbeddingAdapter,
    OpenAICompatibleRerankerAdapter,
    ProviderConfig,
    ProviderHttpResponse,
)
from conflux_weave.retrieval import RetrievalDocument
from conflux_weave.runtime import LocalArtifactStore


class SequenceTransport:
    def __init__(self, responses): self.responses = iter(responses)
    def post(self, *args, **kwargs): return next(self.responses)


def response(payload, status=200):
    return ProviderHttpResponse(status, json.dumps(payload).encode(), {"Content-Type": "application/json"})


def test_pipeline_preserves_all_stages_and_rerank_lineage(tmp_path):
    documents = (RetrievalDocument("a", "agent context", "s-a", {"page": 1}), RetrievalDocument("b", "cooking", "s-b", {"page": 2}))
    index = LanceDBDenseIndex(tmp_path / "db"); index.publish(documents, ((1.0, 0.0), (0.0, 1.0)))
    store = LocalArtifactStore(tmp_path / "artifacts"); config = ProviderConfig("https://provider.example/v1", "secret", "chat")
    embedding = OpenAICompatibleEmbeddingAdapter(store, config, transport=SequenceTransport([response({"data": [{"index": 0, "embedding": [1.0, 0.0]}], "usage": {"prompt_tokens": 2}})]))
    reranker = OpenAICompatibleRerankerAdapter(store, config, transport=SequenceTransport([response({"results": [{"index": 0, "relevance_score": 0.9}, {"index": 1, "relevance_score": 0.1}]})]))
    run = HybridRetrievalPipeline(documents, index, embedding, reranker).search("agent", sparse_k=2, dense_k=2, fusion_k=2, rerank_k=2)
    assert run.rerank_status == "reranked"
    assert run.bm25.hits[0].document_id == "a"
    assert run.dense.hits[0].document_id == "a"
    assert run.final.hits[0].source_snapshot_id == "s-a"
    assert run.embedding_response_artifact and run.rerank_response_artifact


def test_pipeline_degrades_to_hybrid_when_reranker_fails(tmp_path):
    documents = (RetrievalDocument("a", "agent context", "s-a", {"page": 1}),)
    index = LanceDBDenseIndex(tmp_path / "db"); index.publish(documents, ((1.0, 0.0),))
    store = LocalArtifactStore(tmp_path / "artifacts"); config = ProviderConfig("https://provider.example/v1", "secret", "chat")
    embedding = OpenAICompatibleEmbeddingAdapter(store, config, transport=SequenceTransport([response({"data": [{"index": 0, "embedding": [1.0, 0.0]}]})]))
    reranker = OpenAICompatibleRerankerAdapter(store, config, transport=SequenceTransport([response({"error": "down"}, 503)]))
    run = HybridRetrievalPipeline(documents, index, embedding, reranker).search("agent", sparse_k=1, dense_k=1, fusion_k=1, rerank_k=1)
    assert run.rerank_status == "degraded_to_hybrid"
    assert run.final == run.hybrid
    assert run.rerank_error_code == "provider_http_failed"


def test_incremental_documents_are_added_to_sparse_and_dense_indexes(tmp_path):
    documents = (RetrievalDocument("chunk-1", "existing geographic content", "source-1", {}),)
    index = LanceDBDenseIndex(tmp_path / "db")
    index.publish(documents, ((1.0, 0.0),))

    class IncrementalEmbedding:
        def embed(self, texts, *, producer_step_id):
            artifact = SimpleNamespace(artifact_id="artifact-test")
            vectors = tuple((0.0, 1.0) if "urecom" in text.lower() else (1.0, 0.0) for text in texts)
            return SimpleNamespace(vectors=vectors, request_artifact=artifact, response_artifact=artifact)

    pipeline = HybridRetrievalPipeline(documents, index, IncrementalEmbedding(), SimpleNamespace())
    added = (RetrievalDocument("chunk-2", "UReCoM is a user-relayed context manipulation attack.", "source-2", {"page": 1}),)

    result = pipeline.add_documents(added)

    assert result["added_count"] == 1
    assert pipeline.bm25.search("UReCoM", top_k=5).hits[0].document_id == "chunk-2"
    assert index.search((0.0, 1.0), top_k=1).hits[0].document_id == "chunk-2"


def test_document_scope_is_applied_before_sparse_dense_and_rerank(tmp_path):
    documents = (
        RetrievalDocument("chunk-a", "agent evidence", "doc-a", {"document_id": "doc-a"}),
        RetrievalDocument("chunk-b", "agent evidence", "doc-b", {"document_id": "doc-b"}),
        RetrievalDocument("chunk-c", "agent evidence", "doc-c", {"document_id": "doc-c"}),
    )
    index = LanceDBDenseIndex(tmp_path / "db")
    index.publish(documents, ((1.0, 0.0), (1.0, 0.0), (1.0, 0.0)))
    store = LocalArtifactStore(tmp_path / "artifacts")
    config = ProviderConfig("https://provider.example/v1", "secret", "chat")
    embedding = OpenAICompatibleEmbeddingAdapter(
        store,
        config,
        transport=SequenceTransport([
            response({"data": [{"index": 0, "embedding": [1.0, 0.0]}]})
        ]),
    )
    reranker = OpenAICompatibleRerankerAdapter(
        store,
        config,
        transport=SequenceTransport([
            response({"results": [
                {"index": 0, "relevance_score": 0.9},
                {"index": 1, "relevance_score": 0.8},
            ]})
        ]),
    )

    run = HybridRetrievalPipeline(documents, index, embedding, reranker).search(
        "agent",
        document_ids=("doc-a", "doc-b"),
        sparse_k=10,
        dense_k=10,
        fusion_k=10,
        rerank_k=10,
    )

    for result in (run.bm25, run.dense, run.hybrid, run.final):
        assert {hit.document_id for hit in result.hits} <= {"chunk-a", "chunk-b"}
    assert "chunk-c" not in {hit.document_id for hit in run.final.hits}


def test_missing_document_scope_returns_empty_without_provider_calls(tmp_path):
    documents = (
        RetrievalDocument("chunk-a", "agent evidence", "doc-a", {}),
    )
    index = LanceDBDenseIndex(tmp_path / "db")
    index.publish(documents, ((1.0, 0.0),))
    store = LocalArtifactStore(tmp_path / "artifacts")
    config = ProviderConfig("https://provider.example/v1", "secret", "chat")
    embedding = OpenAICompatibleEmbeddingAdapter(
        store, config, transport=SequenceTransport([])
    )
    reranker = OpenAICompatibleRerankerAdapter(
        store, config, transport=SequenceTransport([])
    )

    run = HybridRetrievalPipeline(documents, index, embedding, reranker).search(
        "agent",
        document_ids=("doc-missing",),
    )

    assert run.rerank_status == "scope_empty"
    assert run.bm25.hits == run.dense.hits == run.final.hits == ()
    assert run.embedding_request_artifact == ""
    assert run.rerank_request_artifact is None


def test_document_scope_identifier_normalization(tmp_path):
    documents = (
        RetrievalDocument(
            "chunk-1",
            "first paper text",
            "paper-2401.12345v1",
            {"paper_id": "2401.12345v1", "filename": "2401.12345v1.pdf"},
        ),
        RetrievalDocument(
            "chunk-2",
            "second paper text",
            "doc-9999.00001",
            {"document_id": "doc-9999.00001"},
        ),
    )
    index = LanceDBDenseIndex(tmp_path / "db")
    index.publish(documents, ((1.0, 0.0), (0.0, 1.0)))
    store = LocalArtifactStore(tmp_path / "artifacts")
    config = ProviderConfig("https://provider.example/v1", "secret", "chat")
    embedding = OpenAICompatibleEmbeddingAdapter(
        store, config, transport=SequenceTransport([])
    )
    reranker = OpenAICompatibleRerankerAdapter(
        store, config, transport=SequenceTransport([])
    )
    pipeline = HybridRetrievalPipeline(documents, index, embedding, reranker)

    # 1. Matching by bare arXiv ID without version or prefix
    resolved = pipeline.resolve_scope(("2401.12345",))
    assert tuple(d.document_id for d in resolved) == ("chunk-1",)

    # 2. Matching with .pdf extension
    resolved = pipeline.resolve_scope(("2401.12345.pdf",))
    assert tuple(d.document_id for d in resolved) == ("chunk-1",)

    # 3. Matching with different prefix (e.g. doc- vs paper-)
    resolved = pipeline.resolve_scope(("doc-2401.12345",))
    assert tuple(d.document_id for d in resolved) == ("chunk-1",)

    # 4. Matching bare id without doc- prefix
    resolved = pipeline.resolve_scope(("9999.00001",))
    assert tuple(d.document_id for d in resolved) == ("chunk-2",)

