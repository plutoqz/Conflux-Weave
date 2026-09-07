"""Traceable S1.3 BM25 + LanceDB + RRF + rerank pipeline."""
from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from typing import Any

from conflux_weave.indexing import LanceDBDenseIndex
from conflux_weave.provider import (
    OpenAICompatibleEmbeddingAdapter,
    OpenAICompatibleRerankerAdapter,
    ProviderPortError,
)
from conflux_weave.retrieval import (
    BM25Retriever,
    RetrievalDocument,
    RetrievalHit,
    RetrievalQueryResult,
    RetrievalStrategy,
    reciprocal_rank_fusion,
)


@dataclass(frozen=True, slots=True)
class HybridRetrievalRun:
    query: str
    bm25: RetrievalQueryResult
    dense: RetrievalQueryResult
    hybrid: RetrievalQueryResult
    final: RetrievalQueryResult
    rerank_status: str
    embedding_request_artifact: str
    embedding_response_artifact: str
    rerank_request_artifact: str | None
    rerank_response_artifact: str | None
    rerank_error_code: str | None = None


class HybridRetrievalPipeline:
    def __init__(
        self,
        documents: tuple[RetrievalDocument, ...],
        dense_index: LanceDBDenseIndex,
        embedding: OpenAICompatibleEmbeddingAdapter,
        reranker: OpenAICompatibleRerankerAdapter,
    ) -> None:
        if not documents:
            raise ValueError("documents must not be empty")
        self.documents = documents
        self.document_by_id = {document.document_id: document for document in documents}
        self.bm25 = BM25Retriever(documents)
        self.dense_index = dense_index
        self.embedding = embedding
        self.reranker = reranker
        self._index_lock = RLock()

    def add_documents(
        self,
        documents: tuple[RetrievalDocument, ...],
        *,
        batch_size: int = 10,
        producer_step_id: str = "step-library-incremental-index",
    ) -> dict[str, Any]:
        """Embed and publish new chunks, then expose them to sparse retrieval."""
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")
        if not documents:
            raise ValueError("documents must not be empty")
        with self._index_lock:
            pending = tuple(document for document in documents if document.document_id not in self.document_by_id)
            if not pending:
                return {"status": "already_indexed", "added_count": 0, "embedding_artifacts": []}
            if len({document.document_id for document in pending}) != len(pending):
                raise ValueError("document_id values must be unique")
            vectors: list[tuple[float, ...]] = []
            artifacts: list[dict[str, str]] = []
            for start in range(0, len(pending), batch_size):
                embedded = self.embedding.embed(
                    [document.text for document in pending[start:start + batch_size]],
                    producer_step_id=f"{producer_step_id}-{start // batch_size:04d}",
                )
                vectors.extend(embedded.vectors)
                artifacts.append({
                    "request": embedded.request_artifact.artifact_id,
                    "response": embedded.response_artifact.artifact_id,
                })
            update = self.dense_index.add(pending, tuple(vectors))
            combined = self.documents + pending
            self.documents = combined
            self.document_by_id = {document.document_id: document for document in combined}
            self.bm25 = BM25Retriever(combined)
            return {**update, "added_count": len(pending), "embedding_artifacts": artifacts}

    def search(
        self,
        query: str,
        *,
        sparse_k: int = 50,
        dense_k: int = 50,
        fusion_k: int = 30,
        rerank_k: int = 12,
    ) -> HybridRetrievalRun:
        if not query.strip():
            raise ValueError("query must not be empty")
        embedded = self.embedding.embed([query], producer_step_id="s1-query-embedding")
        with self._index_lock:
            bm25 = self.bm25.search(query, top_k=sparse_k)
            dense = self.dense_index.search(embedded.vectors[0], top_k=dense_k)
            hybrid = reciprocal_rank_fusion(bm25, dense, top_k=fusion_k)
            candidates = [self.document_by_id[hit.document_id] for hit in hybrid.hits]
        try:
            reranked = self.reranker.rerank(query, [item.text for item in candidates], top_n=min(rerank_k, len(candidates)), producer_step_id="s1-query-rerank")
            final_hits = tuple(
                RetrievalHit(candidates[index].document_id, score, rank, candidates[index].source_snapshot_id, candidates[index].locator)
                for rank, (index, score) in enumerate(zip(reranked.ranked_indices, reranked.scores), 1)
            )
            final = RetrievalQueryResult(query, RetrievalStrategy.HYBRID, final_hits)
            return HybridRetrievalRun(query, bm25, dense, hybrid, final, "reranked", embedded.request_artifact.artifact_id, embedded.response_artifact.artifact_id, reranked.request_artifact.artifact_id, reranked.response_artifact.artifact_id)
        except ProviderPortError as exc:
            return HybridRetrievalRun(query, bm25, dense, hybrid, hybrid, "degraded_to_hybrid", embedded.request_artifact.artifact_id, embedded.response_artifact.artifact_id, exc.request_artifact_ref, exc.response_artifact_ref, exc.code)
