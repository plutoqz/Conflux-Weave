"""Versioned sparse/dense index construction for the S1 corpus."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from conflux_weave.documents import DocumentSegment
from conflux_weave.provider import OpenAICompatibleEmbeddingAdapter
from conflux_weave.retrieval import BM25Retriever, RetrievalDocument
from conflux_weave.retrieval import RetrievalHit, RetrievalQueryResult, RetrievalStrategy, cosine_similarity
from conflux_weave.runtime.artifacts import LocalArtifactStore


def load_chunks(import_manifest: Path, store: LocalArtifactStore, registry_path: Path | None = None) -> tuple[RetrievalDocument, ...]:
    manifest = json.loads(import_manifest.read_text(encoding="utf-8"))
    documents: list[RetrievalDocument] = []
    rows = list(manifest["files"])
    if registry_path and registry_path.is_file():
        try:
            rows.extend(json.loads(registry_path.read_text(encoding="utf-8")))
        except (OSError, ValueError, json.JSONDecodeError):
            pass
    seen: set[str] = set()
    for row in rows:
        if row["status"] != "imported":
            if row.get("status") != "knowledge_ready":
                continue
        artifact_id = str(row.get("segments_artifact_id", ""))
        source_snapshot_id = str(row.get("source_snapshot_id") or row.get("document_id") or "")
        if not artifact_id or not source_snapshot_id:
            continue
        ref = store.path_for_digest(artifact_id.removeprefix("artifact-sha256-"))
        payload = json.loads(ref.read_text(encoding="utf-8"))
        for segment in payload["segments"]:
            segment_id = segment["segment_id"]
            if segment_id in seen:
                continue
            seen.add(segment_id)
            documents.append(RetrievalDocument(segment_id, segment["text"], source_snapshot_id, segment["locator"]))
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


class LanceDBDenseIndex:
    """LanceDB-backed DenseIndexPort with stable Chunk ID lineage."""

    def __init__(self, db_path: Path, *, table_name: str = "chunks") -> None:
        try:
            import lancedb
        except ImportError as exc:  # pragma: no cover - dependency contract
            raise RuntimeError("lancedb is required for the LanceDB dense index") from exc
        self.db_path = db_path
        self.table_name = table_name
        self.db = lancedb.connect(str(db_path))
        self.table = self.db.open_table(table_name) if table_name in self._table_names() else None

    def _table_names(self) -> list[str]:
        listing = self.db.list_tables()
        return list(getattr(listing, "tables", listing))

    def publish(self, documents: tuple[RetrievalDocument, ...], vectors: tuple[tuple[float, ...], ...]) -> dict[str, Any]:
        if len(documents) != len(vectors) or not documents:
            raise ValueError("documents and vectors must be non-empty and aligned")
        dimensions = len(vectors[0])
        if not dimensions or any(len(vector) != dimensions for vector in vectors):
            raise ValueError("vectors must have consistent dimensions")
        rows = [{"chunk_id": doc.document_id, "source_snapshot_id": doc.source_snapshot_id or "", "locator_json": json.dumps(doc.locator or {}, ensure_ascii=False, sort_keys=True), "text": doc.text, "vector": list(vector)} for doc, vector in zip(documents, vectors)]
        temp_name = f"{self.table_name}__building"
        if temp_name in self._table_names():
            self.db.drop_table(temp_name)
        temp = self.db.create_table(temp_name, data=rows)
        if self.table_name in self._table_names():
            self.db.drop_table(self.table_name)
        self.db.create_table(self.table_name, data=temp.to_arrow())
        self.db.drop_table(temp_name)
        self.table = self.db.open_table(self.table_name)
        corpus_hash = hashlib.sha256("\n".join(f"{doc.document_id}:{doc.text}" for doc in documents).encode()).hexdigest()
        return {"schema_version": "conflux-weave.index-manifest.v1", "index_type": "lancedb", "database_path": str(self.db_path.resolve()), "table_name": self.table_name, "corpus_hash": f"sha256:{corpus_hash}", "document_count": len(documents), "dimensions": dimensions, "status": "published"}

    def add(self, documents: tuple[RetrievalDocument, ...], vectors: tuple[tuple[float, ...], ...]) -> dict[str, Any]:
        """Append previously unseen chunks without rebuilding the whole table."""
        if len(documents) != len(vectors) or not documents:
            raise ValueError("documents and vectors must be non-empty and aligned")
        dimensions = len(vectors[0])
        if not dimensions or any(len(vector) != dimensions for vector in vectors):
            raise ValueError("vectors must have consistent dimensions")
        rows = [{"chunk_id": doc.document_id, "source_snapshot_id": doc.source_snapshot_id or "", "locator_json": json.dumps(doc.locator or {}, ensure_ascii=False, sort_keys=True), "text": doc.text, "vector": list(vector)} for doc, vector in zip(documents, vectors)]
        if self.table is None:
            return self.publish(documents, vectors)
        schema = self.table.schema
        vector_field = schema.field("vector")
        existing_dimensions = getattr(vector_field.type, "list_size", dimensions)
        if existing_dimensions != dimensions:
            raise ValueError(f"embedding dimensions changed: index={existing_dimensions}, new={dimensions}")
        self.table.add(rows)
        return {
            "schema_version": "conflux-weave.index-update.v1",
            "index_type": "lancedb",
            "database_path": str(self.db_path.resolve()),
            "table_name": self.table_name,
            "added_count": len(documents),
            "dimensions": dimensions,
            "status": "published",
        }

    def delete(self, document_ids: tuple[str, ...]) -> dict[str, Any]:
        """Remove chunks by stable chunk id from the published table."""
        if self.table is None:
            return {"status": "already_absent", "deleted_count": 0}
        ids = tuple(dict.fromkeys(str(item) for item in document_ids if str(item)))
        if not ids:
            return {"status": "already_absent", "deleted_count": 0}
        escaped = ", ".join(json.dumps(item) for item in ids)
        self.table.delete(f"chunk_id IN ({escaped})")
        return {"status": "published", "deleted_count": len(ids)}

    def search(self, query_vector: tuple[float, ...], *, top_k: int = 10, where: str | None = None) -> RetrievalQueryResult:
        if self.table is None:
            raise RuntimeError("LanceDB table is not published")
        request = self.table.search(list(query_vector)).limit(top_k)
        if where:
            request = request.where(where)
        rows = request.to_list()
        hits = tuple(RetrievalHit(row["chunk_id"], cosine_similarity(query_vector, tuple(row["vector"])), rank, row.get("source_snapshot_id") or None, json.loads(row.get("locator_json", "{}"))) for rank, row in enumerate(rows, 1))
        return RetrievalQueryResult("<embedding>", RetrievalStrategy.DENSE, hits)
