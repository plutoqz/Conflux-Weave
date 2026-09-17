"""Traceable S1.3 BM25 + LanceDB + RRF + rerank pipeline."""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
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

    @classmethod
    def _normalize_id(cls, identifier: str) -> set[str]:
        text = str(identifier or "").strip()
        if not text:
            return set()
        variants = {text}
        lower = text.lower()
        variants.add(lower)

        if "#" in lower:
            variants.add(lower.split("#", 1)[0])

        for item in list(variants):
            stem = Path(item).stem
            if stem:
                variants.add(stem)

        for item in list(variants):
            unprefixed = re.sub(r"^(?:doc|document|paper|snap|snapshot)[-_]", "", item)
            if unprefixed:
                variants.add(unprefixed)

        for item in list(variants):
            unversioned = re.sub(r"v\d+$", "", item)
            if unversioned:
                variants.add(unversioned)

        return {v for v in variants if v}

    @classmethod
    def _matches_scope(cls, document: RetrievalDocument, allowed_ids: set[str]) -> bool:
        locator = document.locator if isinstance(document.locator, dict) else {}
        candidate_strings = (
            document.document_id,
            document.source_snapshot_id or "",
            str(locator.get("document_id") or ""),
            str(locator.get("paper_id") or ""),
            str(locator.get("source_id") or ""),
            str(locator.get("snapshot_id") or ""),
            str(locator.get("filename") or ""),
            str(locator.get("file_name") or ""),
            str(locator.get("relative_path") or ""),
        )
        candidates: set[str] = set()
        for c in candidate_strings:
            candidates.update(cls._normalize_id(c))
        norm_allowed: set[str] = set()
        for a in allowed_ids:
            norm_allowed.update(cls._normalize_id(a))
        return bool(candidates & norm_allowed)

    def resolve_scope(
        self, document_ids: tuple[str, ...] | list[str] | None
    ) -> tuple[RetrievalDocument, ...]:
        """Resolve user-facing document IDs to the indexed chunks they own."""
        if not document_ids:
            return self.documents
        norm_allowed: set[str] = set()
        for item in document_ids:
            norm_allowed.update(self._normalize_id(item))
        if not norm_allowed:
            return ()
        return tuple(
            document
            for document in self.documents
            if self._matches_scope(document, norm_allowed)
        )

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

    def remove_documents(self, document_ids: tuple[str, ...]) -> dict[str, Any]:
        """Remove indexed chunks and rebuild the sparse view from the survivors."""
        ids = tuple(dict.fromkeys(str(item) for item in document_ids if str(item)))
        if not ids:
            return {"status": "already_absent", "deleted_count": 0}
        with self._index_lock:
            existing = set(self.document_by_id)
            removed = tuple(item for item in ids if item in existing)
            if not removed:
                return {"status": "already_absent", "deleted_count": 0}
            remaining = tuple(document for document in self.documents if document.document_id not in set(removed))
            if not remaining:
                raise ValueError("知识库至少需要保留一份资料")
            result = self.dense_index.delete(removed)
            self.documents = remaining
            self.document_by_id = {document.document_id: document for document in remaining}
            self.bm25 = BM25Retriever(remaining)
            return {**result, "deleted_count": len(removed)}

    def search(
        self,
        query: str,
        *,
        sparse_k: int = 50,
        dense_k: int = 50,
        fusion_k: int = 30,
        rerank_k: int = 12,
        document_ids: tuple[str, ...] | list[str] | None = None,
    ) -> HybridRetrievalRun:
        if not query.strip():
            raise ValueError("query must not be empty")
        scoped_documents = self.resolve_scope(document_ids)
        if document_ids and not scoped_documents:
            empty = RetrievalQueryResult(query, RetrievalStrategy.HYBRID, ())
            return HybridRetrievalRun(
                query,
                RetrievalQueryResult(query, RetrievalStrategy.BM25, ()),
                RetrievalQueryResult(query, RetrievalStrategy.DENSE, ()),
                empty,
                empty,
                "scope_empty",
                "",
                "",
                None,
                None,
            )
        embedded = self.embedding.embed([query], producer_step_id="s1-query-embedding")
        with self._index_lock:
            scoped_bm25 = self.bm25 if scoped_documents == self.documents else BM25Retriever(scoped_documents)
            bm25 = scoped_bm25.search(query, top_k=min(sparse_k, len(scoped_documents)))
            dense_where = None
            if document_ids:
                escaped_chunk_ids = ", ".join(
                    json.dumps(document.document_id) for document in scoped_documents
                )
                dense_where = f"chunk_id IN ({escaped_chunk_ids})"
            dense = self.dense_index.search(
                embedded.vectors[0],
                top_k=min(dense_k, len(scoped_documents)),
                where=dense_where,
            )
            hybrid = reciprocal_rank_fusion(bm25, dense, top_k=fusion_k)
            candidates = [self.document_by_id[hit.document_id] for hit in hybrid.hits]
        if not candidates:
            return HybridRetrievalRun(
                query,
                bm25,
                dense,
                hybrid,
                hybrid,
                "skipped_no_candidates",
                embedded.request_artifact.artifact_id,
                embedded.response_artifact.artifact_id,
                None,
                None,
            )
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
