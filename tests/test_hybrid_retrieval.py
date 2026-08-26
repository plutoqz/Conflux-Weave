import json

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
