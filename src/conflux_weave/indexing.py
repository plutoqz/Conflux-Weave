"""Versioned sparse/dense index construction for the S1 corpus."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from conflux_weave.documents import DocumentSegment
from conflux_weave.provider import OpenAICompatibleEmbeddingAdapter
from conflux_weave.retrieval import BM25Retriever, RetrievalDocument
from conflux_weave.runtime.artifacts import LocalArtifactStore


def load_chunks(import_manifest: Path, store: LocalArtifactStore) -> tuple[RetrievalDocument, ...]:
    manifest = json.loads(import_manifest.read_text(encoding="utf-8"))
    documents: list[RetrievalDocument] = []
    for row in manifest["files"]:
        if row["status"] != "imported":
            continue
        ref = store.path_for_digest(row["segments_artifact_id"].removeprefix("artifact-sha256-"))
        payload = json.loads(ref.read_text(encoding="utf-8"))
        for segment in payload["segments"]:
            documents.append(RetrievalDocument(segment["segment_id"], segment["text"], row["source_snapshot_id"], segment["locator"]))
    return tuple(documents)


def build_sparse_index(documents: tuple[RetrievalDocument, ...], *, output: Path) -> dict[str, Any]:
    retriever = BM25Retriever(documents)
    corpus_hash = hashlib.sha256("\n".join(f"{doc.document_id}:{doc.text}" for doc in documents).encode()).hexdigest()
    result = {"schema_version": "conflux-weave.index-manifest.v1", "index_type": "bm25", "corpus_hash": f"sha256:{corpus_hash}", "document_count": len(documents), "tokenization_mode": retriever.tokenization_mode.value, "k1": retriever.k1, "b": retriever.b, "status": "published"}
    output.parent.mkdir(parents=True, exist_ok=True); output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def build_dense_index(documents: tuple[RetrievalDocument, ...], adapter: OpenAICompatibleEmbeddingAdapter, *, output: Path, batch_size: int = 20) -> dict[str, Any]:
    if batch_size <= 0: raise ValueError("batch_size must be positive")
    vectors: list[list[float]] = []
    batches: list[str] = []
    for start in range(0, len(documents), batch_size):
        result = adapter.embed([doc.text for doc in documents[start:start + batch_size]], producer_step_id=f"s1-embedding-batch-{start // batch_size:04d}")
        vectors.extend([list(vector) for vector in result.vectors]); batches.append(result.response_artifact.artifact_id)
    if len(vectors) != len(documents): raise RuntimeError("embedding index incomplete")
    payload = {"schema_version": "conflux-weave.dense-index.v1", "document_ids": [doc.document_id for doc in documents], "vectors": vectors, "embedding_model": adapter.model, "dimensions": len(vectors[0]), "batch_response_artifacts": batches}
    index_bytes = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    index_artifact = adapter.artifact_store.put_bytes(index_bytes, media_type="application/json", producer_step_id="s1-dense-index", schema_version="conflux-weave.dense-index.v1")
    manifest = {"schema_version": "conflux-weave.index-manifest.v1", "index_type": "dense", "document_count": len(documents), "embedding_model": adapter.model, "dimensions": len(vectors[0]), "index_artifact": index_artifact.artifact_id, "status": "published"}
    output.parent.mkdir(parents=True, exist_ok=True); output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest
