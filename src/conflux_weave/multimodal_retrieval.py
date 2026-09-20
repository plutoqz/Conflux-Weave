"""Traceable Multimodal Retrieval Pipeline for P2.2.

Provides:
1. Text query -> Image retrieval via ImageEmbeddingPort + LanceDBImageIndex.
2. Image query -> Text retrieval via ImageEmbeddingPort + LanceDBDenseIndex.
3. Multimodal rank fusion via multimodal_reciprocal_rank_fusion (RRF).
4. EvidenceRef conversion with image modality, asset_id, artifact_ref, and pure caption quote.
5. Graceful fallback when multimodal is disabled via CONFLUX_WEAVE_MULTIMODAL_ENABLED=false.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, Sequence, runtime_checkable

from conflux_weave.evidence.contracts import EvidenceRef
from conflux_weave.hybrid_retrieval import HybridRetrievalPipeline, HybridRetrievalRun
from conflux_weave.multimodal_indexing import (
    ImageEmbeddingPort,
    ImageEmbeddingRequest,
    ImageVectorDimensionMismatch,
    LanceDBImageIndex,
    MultimodalRetrievalHit,
)
from conflux_weave.retrieval import (
    MultimodalFusionHit,
    RetrievalDocument,
    RetrievalHit,
    RetrievalQueryResult,
    RetrievalStrategy,
    multimodal_reciprocal_rank_fusion,
)
from conflux_weave.runtime import LocalArtifactStore

MULTIMODAL_ENV_FLAG = "CONFLUX_WEAVE_MULTIMODAL_ENABLED"
EVIDENCE_QUOTE_CHARS = 2400


def is_multimodal_env_enabled() -> bool:
    """Check if multimodal features are enabled via environment variable. Default is true for rich visual RAG."""
    val = os.environ.get(MULTIMODAL_ENV_FLAG, "true").strip().casefold()
    return val in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class MultimodalRetrievalRun:
    """Execution trace of a multimodal search query."""

    query: str
    text_run: HybridRetrievalRun | None
    image_hits: tuple[MultimodalRetrievalHit, ...]
    fused_hits: tuple[MultimodalFusionHit, ...]
    final: RetrievalQueryResult
    fusion_strategy: str
    query_image_embedding_request_artifact: str | None = None
    query_image_embedding_response_artifact: str | None = None
    # P7-V 实证修复：图片索引与查询向量维度不兼容时图文分支显式降级，
    # 而不是让 lancedb 的 "no vector column" 误报冻结整个检索/研究批次。
    image_degradation: str | None = None


@runtime_checkable
class MultimodalCrossReranker(Protocol):
    """Protocol for cross-modal relevance reranking and negative candidate pruning."""

    def rerank(
        self,
        query: str,
        candidates: Sequence[MultimodalRetrievalHit],
        *,
        top_k: int = 5,
        min_relevance_threshold: float = 0.30,
    ) -> tuple[MultimodalRetrievalHit, ...]:
        ...


_ENGLISH_STOP_WORDS = frozenset({
    "a", "an", "the", "and", "or", "but", "if", "then", "else", "when", "at",
    "by", "for", "with", "about", "against", "between", "into", "through",
    "during", "before", "after", "above", "below", "to", "from", "up", "down",
    "in", "out", "on", "off", "over", "under", "again", "further", "once",
    "here", "there", "all", "any", "both", "each", "few", "more", "most",
    "other", "some", "such", "no", "nor", "not", "only", "own", "same", "so",
    "than", "too", "very", "can", "will", "just", "should", "now", "of",
    "is", "are", "was", "were", "be", "been", "being", "have", "has", "had",
})


class DefaultMultimodalCrossReranker:
    """Lightweight cross-modal reranker and unanswerable candidate pruner.

    Combines vector similarity, caption/OCR/referencing lexical grounding,
    and penalizes purely incidental page-cooccurrence hits that lack topical relevance.
    """

    def __init__(
        self,
        *,
        min_relevance_threshold: float = 0.30,
        page_hit_penalty_without_lexical: float = 0.45,
    ) -> None:
        self.min_relevance_threshold = min_relevance_threshold
        self.page_hit_penalty_without_lexical = page_hit_penalty_without_lexical

    def rerank(
        self,
        query: str,
        candidates: Sequence[MultimodalRetrievalHit],
        *,
        top_k: int = 5,
        min_relevance_threshold: float | None = None,
    ) -> tuple[MultimodalRetrievalHit, ...]:
        thresh = min_relevance_threshold if min_relevance_threshold is not None else self.min_relevance_threshold
        query_tokens = [
            t.lower() for t in re.findall(r"[\w\u4e00-\u9fff]+", query)
            if len(t) > 1 and t.lower() not in _ENGLISH_STOP_WORDS
        ]
        if not query_tokens:
            query_tokens = [
                t.lower() for t in re.findall(r"[\w\u4e00-\u9fff]+", query)
                if len(t) > 1
            ]

        # Group hits by asset_id to combine multi-modal signals (vector, caption, page)
        candidates_by_id: dict[str, list[MultimodalRetrievalHit]] = {}
        for hit in candidates:
            candidates_by_id.setdefault(hit.asset_id, []).append(hit)

        scored_candidates: list[tuple[float, MultimodalRetrievalHit]] = []
        for asset_id, hits in candidates_by_id.items():
            rep_hit = max(hits, key=lambda h: (1 if h.caption else 0, getattr(h, "score", 0.0)))
            max_base_score = max(float(getattr(h, "score", 0.0)) for h in hits)

            text_corpus = f"{rep_hit.caption or ''} {rep_hit.ocr_text or ''} {rep_hit.referencing_text or ''}".lower()
            corpus_tokens = set(re.findall(r"[\w\u4e00-\u9fff]+", text_corpus))
            lexical_matches = sum(1 for tok in query_tokens if tok in corpus_tokens)
            lexical_ratio = lexical_matches / max(1, len(query_tokens))

            # Prune ungrounded out-of-domain noise candidates
            max_vec_score = max((float(h.score) for h in hits if getattr(h, "embedding_model", None) and float(h.score) <= 1.0), default=0.0)
            if len(query_tokens) >= 3 and lexical_ratio < 0.25 and max_vec_score < 0.45:
                continue

            adjusted_score = max_base_score
            # Penalize incidental page hits with zero lexical overlap
            if lexical_matches == 0 and max_base_score <= 0.65:
                adjusted_score *= self.page_hit_penalty_without_lexical
            elif lexical_ratio >= 0.25:
                adjusted_score += min(0.3, lexical_matches * 0.08)

            if adjusted_score >= thresh:
                scored_candidates.append((adjusted_score, rep_hit))

        scored_candidates.sort(key=lambda x: x[0], reverse=True)

        reranked_hits: list[MultimodalRetrievalHit] = []
        for rank, (score, hit) in enumerate(scored_candidates[:top_k], 1):
            reranked_hits.append(
                MultimodalRetrievalHit(
                    asset_id=hit.asset_id,
                    score=score,
                    rank=rank,
                    modality=hit.modality,
                    source_snapshot_id=hit.source_snapshot_id,
                    document_id=hit.document_id,
                    page=hit.page,
                    bbox=hit.bbox,
                    coordinate_space=hit.coordinate_space,
                    parent_chunk_ids=hit.parent_chunk_ids,
                    caption=hit.caption,
                    artifact_ref=hit.artifact_ref,
                    thumbnail_artifact_ref=hit.thumbnail_artifact_ref,
                    embedding_model=hit.embedding_model,
                    index_version=hit.index_version,
                    locator=hit.locator,
                    referencing_text=hit.referencing_text,
                    ocr_text=hit.ocr_text,
                )
            )
        return tuple(reranked_hits)


def intra_modal_image_rrf(
    vector_hits: Sequence[MultimodalRetrievalHit],
    caption_hits: Sequence[MultimodalRetrievalHit],
    *,
    k: int = 60,
    top_k: int = 10,
) -> tuple[MultimodalRetrievalHit, ...]:
    """Fuse intra-modal image candidate streams (vector similarity + caption lexical) using Reciprocal Rank Fusion.

    Eliminates scale mismatch between cosine similarity [-1, 1] and BM25 scores [0, inf).
    Computes RRF score = sum(1 / (k + rank)) across streams, merges identical asset_ids
    while preserving the richest metadata (caption, OCR, referencing text, bbox),
    and outputs top_k fused MultimodalRetrievalHits with rank-normalized scores.
    """
    if top_k <= 0 or k <= 0:
        raise ValueError("top_k and k must be positive")

    rrf_scores: dict[str, float] = {}
    best_hit: dict[str, MultimodalRetrievalHit] = {}

    for rank, h in enumerate(vector_hits, 1):
        rrf_scores[h.asset_id] = rrf_scores.get(h.asset_id, 0.0) + 1.0 / (k + rank)
        best_hit[h.asset_id] = h

    for rank, h in enumerate(caption_hits, 1):
        rrf_scores[h.asset_id] = rrf_scores.get(h.asset_id, 0.0) + 1.0 / (k + rank)
        if h.asset_id not in best_hit or not best_hit[h.asset_id].caption:
            best_hit[h.asset_id] = h
        else:
            cur = best_hit[h.asset_id]
            if (not cur.referencing_text and h.referencing_text) or (not cur.ocr_text and h.ocr_text):
                best_hit[h.asset_id] = MultimodalRetrievalHit(
                    asset_id=cur.asset_id,
                    score=cur.score,
                    rank=cur.rank,
                    modality=cur.modality,
                    source_snapshot_id=cur.source_snapshot_id,
                    document_id=cur.document_id,
                    page=cur.page,
                    bbox=cur.bbox,
                    coordinate_space=cur.coordinate_space,
                    parent_chunk_ids=cur.parent_chunk_ids,
                    caption=cur.caption or h.caption,
                    artifact_ref=cur.artifact_ref,
                    thumbnail_artifact_ref=cur.thumbnail_artifact_ref,
                    embedding_model=cur.embedding_model,
                    index_version=cur.index_version,
                    locator=cur.locator,
                    referencing_text=cur.referencing_text or h.referencing_text,
                    ocr_text=cur.ocr_text or h.ocr_text,
                )

    sorted_ids = sorted(rrf_scores.keys(), key=lambda aid: (-rrf_scores[aid], aid))
    fused_image_hits: list[MultimodalRetrievalHit] = []
    for rank, aid in enumerate(sorted_ids[:top_k], 1):
        h = best_hit[aid]
        fused_image_hits.append(
            MultimodalRetrievalHit(
                asset_id=h.asset_id,
                score=rrf_scores[aid],
                rank=rank,
                modality="image",
                source_snapshot_id=h.source_snapshot_id,
                document_id=h.document_id,
                page=h.page,
                bbox=h.bbox,
                coordinate_space=h.coordinate_space,
                parent_chunk_ids=h.parent_chunk_ids,
                caption=h.caption,
                artifact_ref=h.artifact_ref,
                thumbnail_artifact_ref=h.thumbnail_artifact_ref,
                embedding_model=h.embedding_model,
                index_version=h.index_version,
                locator=h.locator,
                referencing_text=h.referencing_text,
                ocr_text=h.ocr_text,
            )
        )
    return tuple(fused_image_hits)



class MultimodalRetrievalPipeline:
    """Unified retrieval pipeline coordinating text hybrid search and multimodal image search."""

    def __init__(
        self,
        text_pipeline: HybridRetrievalPipeline,
        image_index: LanceDBImageIndex | None = None,
        image_embedding: ImageEmbeddingPort | None = None,
        artifact_store: LocalArtifactStore | None = None,
        *,
        enabled: bool | None = None,
        joint_multimodal_space: bool | None = None,
        cross_reranker: MultimodalCrossReranker | None = None,
        score_weighted_rrf: bool = False,
    ) -> None:
        self.text_pipeline = text_pipeline
        self.image_index = image_index
        self.image_embedding = image_embedding
        self.artifact_store = artifact_store
        self.enabled = is_multimodal_env_enabled() if enabled is None else enabled
        self.joint_multimodal_space = joint_multimodal_space
        self.cross_reranker = cross_reranker
        self.score_weighted_rrf = score_weighted_rrf
        self.documents = text_pipeline.documents
        self.document_by_id = text_pipeline.document_by_id

    def is_multimodal_active(self) -> bool:
        """Return whether multimodal image retrieval is enabled and operational."""
        return (
            self.enabled
            and self.image_index is not None
            and self.image_embedding is not None
            and self.image_index.table is not None
        )

    def _scope_values(self, document_ids: Sequence[str] | None) -> tuple[str, ...]:
        if not document_ids:
            return ()
        values = {str(item).strip() for item in document_ids if str(item).strip()}
        for document in self.text_pipeline.resolve_scope(tuple(values)):
            values.add(document.document_id)
            if document.source_snapshot_id:
                values.add(document.source_snapshot_id)
            locator = document.locator if isinstance(document.locator, dict) else {}
            for key in ("document_id", "paper_id", "source_id"):
                value = str(locator.get(key) or "").strip()
                if value:
                    values.add(value)
        return tuple(sorted(values))

    @staticmethod
    def _scope_where(scope_values: Sequence[str]) -> str | None:
        if not scope_values:
            return None
        escaped = ", ".join(json.dumps(item) for item in scope_values)
        return (
            f"(document_id IN ({escaped}) OR "
            f"source_snapshot_id IN ({escaped}))"
        )

    def _image_dimension_conflict(self) -> str | None:
        """Return a degradation reason when the embedder and index dimensions are known and incompatible.

        Both adapters advertise ``dimensions``; the physical index dimension is read
        from the LanceDB schema. Unknown values (custom ports) return None so the
        embed-time typed check in ``search_vector`` remains the safety net.
        """
        embedder_dimensions = getattr(self.image_embedding, "dimensions", None)
        index_dimensions = self.image_index.vector_dimensions() if self.image_index else None
        if (
            embedder_dimensions is not None
            and index_dimensions is not None
            and embedder_dimensions != index_dimensions
        ):
            return (
                f"image_dimension_mismatch(index={index_dimensions}, "
                f"embedder={embedder_dimensions}); degraded to text-only retrieval"
            )
        return None

    def is_joint_space_active(self) -> bool:
        """Check if image embedding and text embedding share a verified joint multimodal vector space."""
        if self.joint_multimodal_space is not None:
            return self.joint_multimodal_space
        if self.image_embedding is None:
            return False
        if getattr(self.image_embedding, "is_joint_multimodal", False):
            return True
        from conflux_weave.multimodal_indexing import DeterministicImageEmbeddingAdapter
        if isinstance(self.image_embedding, DeterministicImageEmbeddingAdapter):
            return True
        text_model = getattr(
            self.text_pipeline, "embedding_model",
            getattr(getattr(self.text_pipeline, "embedding", None), "model", None)
        )
        if text_model and text_model == self.image_embedding.model:
            return True
        return False

    def add_documents(
        self,
        documents: tuple[RetrievalDocument, ...],
        *,
        batch_size: int = 10,
        producer_step_id: str = "step-library-incremental-index",
    ) -> dict[str, Any]:
        """Delegate text document indexing and keep document mapping synchronized."""
        res = self.text_pipeline.add_documents(
            documents, batch_size=batch_size, producer_step_id=producer_step_id
        )
        self.documents = self.text_pipeline.documents
        self.document_by_id = self.text_pipeline.document_by_id
        return res

    def remove_documents(self, document_ids: tuple[str, ...]) -> dict[str, Any]:
        """Delegate text document removal and keep document mapping synchronized."""
        res = self.text_pipeline.remove_documents(document_ids)
        self.documents = self.text_pipeline.documents
        self.document_by_id = self.text_pipeline.document_by_id
        return res

    def search_images_by_text(
        self,
        query: str,
        *,
        top_k: int = 5,
        where: str | None = None,
        document_ids: Sequence[str] | None = None,
        producer_step_id: str = "step-image-retrieval",
    ) -> tuple[MultimodalRetrievalHit, ...]:
        """Retrieve relevant images for a text query."""
        if not query or not query.strip():
            raise ValueError("query must not be empty")
        if top_k <= 0:
            raise ValueError("top_k must be positive")
        if not self.is_multimodal_active():
            return ()
        scope_values = self._scope_values(document_ids)
        scope_where = self._scope_where(scope_values)
        effective_where = (
            f"({where}) AND ({scope_where})"
            if where and scope_where
            else where or scope_where
        )
        conflict = self._image_dimension_conflict()

        vector_hits: list[MultimodalRetrievalHit] = []
        if conflict is None and self.image_embedding is not None and self.image_index is not None:
            try:
                embedded = self.image_embedding.embed_query_text(
                    query, producer_step_id=producer_step_id
                )
                if embedded.vectors:
                    vector_hits = list(
                        self.image_index.search_vector(
                            embedded.vectors[0], top_k=top_k, where=effective_where
                        )
                    )
            except ImageVectorDimensionMismatch:
                pass
            except Exception:
                pass

        caption_hits: list[MultimodalRetrievalHit] = []
        if self.image_index is not None and hasattr(self.image_index, "search_caption_text"):
            caption_hits = list(
                self.image_index.search_caption_text(
                    query,
                    top_k=top_k,
                    where=effective_where,
                    document_ids=scope_values,
                )
            )

        merged: list[MultimodalRetrievalHit] = []
        seen = set()
        for h in vector_hits + caption_hits:
            if h.asset_id not in seen:
                seen.add(h.asset_id)
                merged.append(h)
                if len(merged) >= top_k:
                    break
        return tuple(merged)

    def search_text_by_image(
        self,
        image_query: ImageEmbeddingRequest | bytes | str,
        *,
        top_k: int = 5,
        where: str | None = None,
        producer_step_id: str = "step-text-by-image",
    ) -> RetrievalQueryResult:
        """Retrieve relevant text chunks for an image query."""
        if top_k <= 0:
            raise ValueError("top_k must be positive")
        if not self.enabled or self.image_embedding is None:
            raise RuntimeError("Multimodal image embedding is not available")

        # Strict Semantic Vector Space Invariant Check (Frozen Scope section 3.3)
        text_model = getattr(
            self.text_pipeline, "embedding_model",
            getattr(getattr(self.text_pipeline, "embedding", None), "model", "text-dense-model")
        )
        if not self.is_joint_space_active():
            raise ValueError(
                f"cross_modal_space_mismatch: image embedding model '{self.image_embedding.model}' "
                f"and text dense index model '{text_model}' do not share an admitted joint multimodal vector space. "
                f"Direct cross-space cosine comparison is forbidden by frozen scope section 3.3."
            )

        if isinstance(image_query, ImageEmbeddingRequest):
            req = image_query
        elif isinstance(image_query, bytes):
            req = ImageEmbeddingRequest(image_bytes=image_query)
        elif isinstance(image_query, str):
            image_str = image_query.strip()
            if not image_str:
                raise ValueError("image_query string must not be empty")
            # Try reading from artifact store if digest/id
            resolved_bytes: bytes | None = None
            if self.artifact_store is not None:
                digest = image_str.removeprefix("artifact-sha256-")
                path = self.artifact_store.path_for_digest(digest)
                if path.is_file():
                    resolved_bytes = path.read_bytes()
            if resolved_bytes is None:
                path = Path(image_str)
                if path.is_file():
                    resolved_bytes = path.read_bytes()
            if resolved_bytes is None:
                raise ValueError(f"Cannot resolve image content from '{image_query}'")
            req = ImageEmbeddingRequest(
                image_bytes=resolved_bytes, artifact_ref=image_str
            )
        else:
            raise TypeError(f"Unsupported image_query type: {type(image_query)}")

        embedded = self.image_embedding.embed_images(
            [req], producer_step_id=producer_step_id
        )
        query_vector = embedded.vectors[0]

        # Dimension validation against text dense index
        if self.text_pipeline.dense_index.table is not None:
            schema = self.text_pipeline.dense_index.table.schema
            vector_field = schema.field("vector")
            text_dims = getattr(vector_field.type, "list_size", len(query_vector))
            if text_dims != len(query_vector):
                raise ValueError(
                    f"embedding_dimension_mismatch: text_index={text_dims}, image_query={len(query_vector)}"
                )

        return self.text_pipeline.dense_index.search(
            query_vector, top_k=top_k, where=where
        )

    def search(
        self,
        query: str,
        *,
        text_sparse_k: int = 50,
        text_dense_k: int = 50,
        text_fusion_k: int = 30,
        text_rerank_k: int = 12,
        image_k: int = 5,
        fusion_k: int = 15,
        text_weight: float = 1.0,
        image_weight: float = 1.0,
        document_ids: Sequence[str] | None = None,
        cross_rerank: bool = True,
        score_weighted: bool | None = None,
        score_threshold: float | None = None,
    ) -> MultimodalRetrievalRun:
        """Execute text hybrid search + image search, then combine results via RRF."""
        if not query or not query.strip():
            raise ValueError("query must not be empty")

        text_run = self.text_pipeline.search(
            query,
            sparse_k=text_sparse_k,
            dense_k=text_dense_k,
            fusion_k=text_fusion_k,
            rerank_k=text_rerank_k,
            document_ids=document_ids,
        )

        scope_values = self._scope_values(document_ids)
        image_where = self._scope_where(scope_values)

        image_hits: tuple[MultimodalRetrievalHit, ...] = ()
        req_art = None
        resp_art = None
        image_degradation = None
        if self.is_multimodal_active():
            conflict = self._image_dimension_conflict()
            vector_hits: list[MultimodalRetrievalHit] = []
            if conflict is None:
                assert self.image_embedding is not None
                assert self.image_index is not None
                try:
                    embedded = self.image_embedding.embed_query_text(
                        query, producer_step_id="step-image-retrieval"
                    )
                    req_art = embedded.request_artifact.artifact_id
                    resp_art = embedded.response_artifact.artifact_id
                    if embedded.vectors:
                        vector_hits = list(self.image_index.search_vector(
                            embedded.vectors[0], top_k=image_k, where=image_where
                        ))
                except ImageVectorDimensionMismatch as mismatch:
                    image_degradation = (
                        f"image_dimension_mismatch(index={mismatch.index_dimensions}, "
                        f"embedder={mismatch.query_dimensions}); degraded to text-only retrieval"
                    )
                except Exception:
                    pass
            else:
                image_degradation = conflict

            caption_hits: list[MultimodalRetrievalHit] = []
            if self.image_index is not None and hasattr(self.image_index, "search_caption_text"):
                caption_hits = list(
                    self.image_index.search_caption_text(
                        query,
                        top_k=image_k,
                        where=image_where,
                        document_ids=scope_values,
                    )
                )

            page_hits: list[MultimodalRetrievalHit] = []
            if self.image_index is not None and hasattr(self.image_index, "search_by_document_pages") and text_run and text_run.final.hits:
                doc_pages = []
                for th in text_run.final.hits[:10]:
                    p = None
                    if isinstance(th.locator, dict) and "page" in th.locator:
                        try:
                            p = int(th.locator["page"])
                        except Exception:
                            pass
                    doc_pages.append((th.document_id, p or 1))
                page_hits = list(self.image_index.search_by_document_pages(doc_pages, top_k=image_k))

            # Stage 1: Intra-modal image RRF fusion between vector stream and caption stream
            intra_fused = intra_modal_image_rrf(vector_hits, caption_hits, k=60, top_k=image_k * 3)
            candidate_hits = list(intra_fused) + vector_hits + caption_hits + page_hits

            reranker = self.cross_reranker if cross_rerank else None
            if reranker is None and cross_rerank:
                reranker = DefaultMultimodalCrossReranker()

            if reranker is not None:
                image_hits = reranker.rerank(query, candidate_hits, top_k=image_k)
            else:
                image_hits = intra_fused[:image_k]

        text_by_id = {doc.document_id: doc.text for doc in self.documents}
        use_score_weighted = self.score_weighted_rrf if score_weighted is None else score_weighted
        fused_hits = multimodal_reciprocal_rank_fusion(
            text_run.final.hits,
            image_hits,
            text_by_id=text_by_id,
            top_k=fusion_k,
            text_weight=text_weight,
            image_weight=image_weight,
            score_weighted=use_score_weighted,
            score_threshold=score_threshold,
        )

        # Build duck-typed RetrievalQueryResult for consumers expecting RetrievalHit sequence
        final_text_hits = tuple(
            RetrievalHit(
                h.hit_id, h.score, rank, h.source_snapshot_id, h.locator
            )
            for rank, h in enumerate(fused_hits, 1)
        )
        final = RetrievalQueryResult(query, RetrievalStrategy.HYBRID, final_text_hits)

        return MultimodalRetrievalRun(
            query=query,
            text_run=text_run,
            image_hits=image_hits,
            fused_hits=fused_hits,
            final=final,
            fusion_strategy="reciprocal_rank_fusion",
            query_image_embedding_request_artifact=req_art,
            query_image_embedding_response_artifact=resp_art,
            image_degradation=image_degradation,
        )

    def to_evidence_refs(
        self,
        fused_hits: Sequence[MultimodalFusionHit],
        *,
        limit: int = 10,
    ) -> tuple[EvidenceRef, ...]:
        """Convert fused hits to verified EvidenceRef instances.

        Image Evidence quote preserves ONLY the raw page caption or empty string,
        strictly forbidding unverified model descriptions.
        """
        evidence: list[EvidenceRef] = []
        for index, hit in enumerate(fused_hits[:limit], 1):
            if hit.modality == "image":
                evidence.append(
                    EvidenceRef(
                        evidence_id=f"evidence-{index:04d}",
                        source_snapshot_id=hit.source_snapshot_id,
                        locator=hit.locator,
                        quote=hit.text or "",
                        extraction_method="multimodal-image-retrieval-v1",
                        modality="image",
                        asset_id=hit.asset_id,
                        artifact_ref=hit.artifact_ref,
                    )
                )
            else:
                text_snippet = (hit.text or "")[:EVIDENCE_QUOTE_CHARS]
                evidence.append(
                    EvidenceRef(
                        evidence_id=f"evidence-{index:04d}",
                        source_snapshot_id=hit.source_snapshot_id,
                        locator=hit.locator,
                        quote=text_snippet,
                        extraction_method="hybrid-lancedb-rerank-page-chunk-v2",
                        modality="text",
                    )
                )
        return tuple(evidence)
