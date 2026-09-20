"""BGE-Reranker Neural Cross-Encoder Adapter for Multimodal Retrieval (Phase 3).

Implements BgeMultimodalCrossReranker conforming to MultimodalCrossReranker protocol.
Replaces lightweight heuristic rules with deep neural cross-attention over
(query, caption + referencing_text + ocr_text) pairs.
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request
from typing import Any, Sequence

from conflux_weave.multimodal_indexing import MultimodalRetrievalHit
from conflux_weave.multimodal_retrieval import (
    DefaultMultimodalCrossReranker,
    MultimodalCrossReranker,
)

logger = logging.getLogger("conflux_weave.multimodal_reranker_bge")

DEFAULT_BGE_RERANKER_MODEL = "bge-reranker-v2-m3-free"


class BgeMultimodalCrossReranker:
    """Neural cross-encoder reranker utilizing BGE-Reranker (or compatible OpenAI rerank API).

    Evaluates deep cross-modal textual relevance for image candidate assets,
    effectively suppressing near-domain distractors that deceive lexical/BM25 matching.
    """

    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        min_relevance_threshold: float = 0.10,
        timeout_seconds: float = 15.0,
        max_retries: int = 2,
        fallback_reranker: MultimodalCrossReranker | None = None,
    ) -> None:
        self.base_url = (
            base_url
            or os.getenv("CONFLUX_WEAVE_PROVIDER_BASE_URL", "https://www.dmxapi.cn/v1")
        ).rstrip("/")
        self.api_key = api_key or os.getenv("CONFLUX_WEAVE_PROVIDER_API_KEY", "")
        self.model = (
            model
            or os.getenv("CONFLUX_WEAVE_PROVIDER_RERANKER_MODEL")
            or DEFAULT_BGE_RERANKER_MODEL
        )
        self.min_relevance_threshold = min_relevance_threshold
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.fallback_reranker = fallback_reranker or DefaultMultimodalCrossReranker()

        # Telemetry tracking for ablation benchmarks
        self.last_latency_ms: float = 0.0
        self.last_call_success: bool = False
        self.last_raw_scores: list[float] = []

    def _format_candidate_document(self, hit: MultimodalRetrievalHit) -> str:
        """Compose a structured document string representing the multimodal asset."""
        parts: list[str] = []
        if hit.caption:
            parts.append(f"Caption: {hit.caption.strip()}")
        if hit.referencing_text:
            # Keep representative referencing window
            ref_snippet = hit.referencing_text.strip()[:400]
            parts.append(f"In-text citation context: {ref_snippet}")
        if hit.ocr_text:
            ocr_snippet = hit.ocr_text.strip()[:300]
            parts.append(f"Visual text content: {ocr_snippet}")

        if not parts:
            return f"Academic figure asset on page {hit.page} of document {hit.document_id}."
        return "\n".join(parts)

    def rerank(
        self,
        query: str,
        candidates: Sequence[MultimodalRetrievalHit],
        *,
        top_k: int = 5,
        min_relevance_threshold: float | None = None,
    ) -> tuple[MultimodalRetrievalHit, ...]:
        """Rerank candidates using neural cross-encoder with fallback protection."""
        if not candidates or not query.strip():
            return ()

        thresh = (
            min_relevance_threshold
            if min_relevance_threshold is not None
            else self.min_relevance_threshold
        )

        # Deduplicate candidates by asset_id to avoid redundant API tokens
        candidates_by_id: dict[str, list[MultimodalRetrievalHit]] = {}
        for hit in candidates:
            candidates_by_id.setdefault(hit.asset_id, []).append(hit)

        unique_asset_ids = list(candidates_by_id.keys())
        rep_hits: list[MultimodalRetrievalHit] = []
        doc_strings: list[str] = []

        for aid in unique_asset_ids:
            hits = candidates_by_id[aid]
            # Select hit with longest caption or highest base score
            rep = max(hits, key=lambda h: (len(h.caption or ""), float(getattr(h, "score", 0.0))))
            rep_hits.append(rep)
            doc_strings.append(self._format_candidate_document(rep))

        # Call the neural rerank API
        scores_by_asset_id: dict[str, float] = {}
        t0 = time.perf_counter()
        success = False

        if self.api_key:
            payload = {
                "model": self.model,
                "query": query,
                "documents": doc_strings,
                "top_n": len(doc_strings),
            }
            req = urllib.request.Request(
                f"{self.base_url}/rerank",
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
            )

            for attempt in range(self.max_retries):
                try:
                    with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                        data = json.loads(resp.read().decode("utf-8"))
                        results = data.get("results", [])
                        self.last_raw_scores = []
                        for item in results:
                            idx = int(item["index"])
                            score = float(item["relevance_score"])
                            aid = unique_asset_ids[idx]
                            scores_by_asset_id[aid] = score
                            self.last_raw_scores.append(score)
                        success = True
                        break
                except Exception as exc:
                    logger.warning(
                        f"BgeMultimodalCrossReranker API call attempt {attempt + 1} failed: {exc}"
                    )
                    if attempt < self.max_retries - 1:
                        time.sleep(1.0)

        self.last_latency_ms = (time.perf_counter() - t0) * 1000.0
        self.last_call_success = success

        # If API call failed or no API key, fall back gracefully
        if not success:
            logger.info("Falling back to lightweight heuristic cross-reranker.")
            return self.fallback_reranker.rerank(
                query,
                candidates,
                top_k=top_k,
                min_relevance_threshold=min_relevance_threshold,
            )

        # Filter by threshold and rank
        scored_hits: list[tuple[float, MultimodalRetrievalHit]] = []
        for aid, rep in zip(unique_asset_ids, rep_hits):
            score = scores_by_asset_id.get(aid, 0.0)
            if score >= thresh:
                scored_hits.append((score, rep))

        scored_hits.sort(key=lambda x: x[0], reverse=True)

        reranked: list[MultimodalRetrievalHit] = []
        for rank, (score, hit) in enumerate(scored_hits[:top_k], 1):
            reranked.append(
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

        return tuple(reranked)
