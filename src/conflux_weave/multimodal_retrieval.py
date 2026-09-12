"""Traceable Multimodal Retrieval Pipeline for P2.2.

Provides:
1. Text query -> Image retrieval via ImageEmbeddingPort + LanceDBImageIndex.
2. Image query -> Text retrieval via ImageEmbeddingPort + LanceDBDenseIndex.
3. Multimodal rank fusion via multimodal_reciprocal_rank_fusion (RRF).
4. EvidenceRef conversion with image modality, asset_id, artifact_ref, and pure caption quote.
5. Graceful fallback when multimodal is disabled via CONFLUX_WEAVE_MULTIMODAL_ENABLED=false.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from conflux_weave.evidence.contracts import EvidenceRef
from conflux_weave.hybrid_retrieval import HybridRetrievalPipeline, HybridRetrievalRun
from conflux_weave.multimodal_indexing import (
    ImageEmbeddingPort,
    ImageEmbeddingRequest,
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
    ) -> None:
        self.text_pipeline = text_pipeline
        self.image_index = image_index
        self.image_embedding = image_embedding
        self.artifact_store = artifact_store
        self.enabled = is_multimodal_env_enabled() if enabled is None else enabled
        self.joint_multimodal_space = joint_multimodal_space
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
        producer_step_id: str = "step-image-retrieval",
    ) -> tuple[MultimodalRetrievalHit, ...]:
        """Retrieve relevant images for a text query."""
        if not query or not query.strip():
            raise ValueError("query must not be empty")
        if top_k <= 0:
            raise ValueError("top_k must be positive")
        if not self.is_multimodal_active():
            return ()

        assert self.image_embedding is not None
        assert self.image_index is not None

        embedded = self.image_embedding.embed_query_text(
            query, producer_step_id=producer_step_id
        )
        if not embedded.vectors:
            return ()
        query_vector = embedded.vectors[0]
        return self.image_index.search_vector(query_vector, top_k=top_k, where=where)

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
        )

        image_hits: tuple[MultimodalRetrievalHit, ...] = ()
        req_art = None
        resp_art = None
        if self.is_multimodal_active():
            assert self.image_embedding is not None
            assert self.image_index is not None
            embedded = self.image_embedding.embed_query_text(
                query, producer_step_id="step-image-retrieval"
            )
            req_art = embedded.request_artifact.artifact_id
            resp_art = embedded.response_artifact.artifact_id
            if embedded.vectors:
                image_hits = self.image_index.search_vector(
                    embedded.vectors[0], top_k=image_k
                )

        text_by_id = {doc.document_id: doc.text for doc in self.documents}
        fused_hits = multimodal_reciprocal_rank_fusion(
            text_run.final.hits,
            image_hits,
            text_by_id=text_by_id,
            top_k=fusion_k,
            text_weight=text_weight,
            image_weight=image_weight,
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
