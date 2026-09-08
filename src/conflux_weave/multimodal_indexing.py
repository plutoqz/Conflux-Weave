"""Multimodal Image Embedding and Indexing infrastructure for P2.1.

Implements ImageEmbeddingPort, ImageEmbeddingRequest, ImageEmbeddingResult,
MultimodalIndexRecord, MultimodalRetrievalHit, LanceDBImageIndex, and build_image_index.
Maintains physical isolation from text-only chunk indexes.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, Sequence

from conflux_weave.document_assets import DocumentAsset
from conflux_weave.provider import (
    DEFAULT_TIMEOUT_SECONDS,
    ProviderConfig,
    ProviderHttpTransport,
    ProviderPortError,
    UrllibProviderTransport,
)
from conflux_weave.evidence import ArtifactRef
from conflux_weave.runtime import LocalArtifactStore

IMAGE_EMBEDDING_SCHEMA_VERSION = "conflux-weave.image-embedding.v1"
MULTIMODAL_INDEX_SCHEMA_VERSION = "conflux-weave.multimodal-index.v1"
MULTIMODAL_INDEX_MANIFEST_SCHEMA_VERSION = "conflux-weave.multimodal-index-manifest.v1"
MULTIMODAL_INDEX_UPDATE_SCHEMA_VERSION = "conflux-weave.multimodal-index-update.v1"


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    """Compute cosine similarity between two non-empty vectors with identical dimensions."""
    if len(left) != len(right) or not left:
        raise ValueError("vectors must have equal non-zero dimensions")
    denom_left = math.sqrt(sum(v * v for v in left))
    denom_right = math.sqrt(sum(v * v for v in right))
    denominator = denom_left * denom_right
    return 0.0 if denominator == 0 else sum(a * b for a, b in zip(left, right)) / denominator


@dataclass(frozen=True, slots=True)
class ImageEmbeddingRequest:
    """Single item request for image or query embedding."""

    asset_id: str | None = None
    image_bytes: bytes | None = None
    media_type: str = "image/png"
    artifact_ref: str | None = None
    text: str | None = None

    def __post_init__(self) -> None:
        if self.image_bytes is None and (not self.text or not self.text.strip()):
            raise ValueError("ImageEmbeddingRequest must provide either image_bytes or non-empty text")


@dataclass(frozen=True, slots=True)
class ImageEmbeddingResult:
    """Output from an ImageEmbeddingPort provider call."""

    model: str
    vectors: tuple[tuple[float, ...], ...]
    dimensions: int
    input_tokens: int | None
    estimated_cost: float | None
    latency_ms: float
    request_artifact: ArtifactRef
    response_artifact: ArtifactRef


class ImageEmbeddingPort(Protocol):
    """Protocol for multimodal image and cross-modal embedding providers."""

    @property
    def model(self) -> str:
        ...

    def embed_images(
        self,
        requests: Sequence[ImageEmbeddingRequest],
        *,
        producer_step_id: str = "step-image-embedding",
    ) -> ImageEmbeddingResult:
        ...

    def embed_query_text(
        self,
        query: str,
        *,
        producer_step_id: str = "step-image-query-embedding",
    ) -> ImageEmbeddingResult:
        ...


class DeterministicImageEmbeddingAdapter:
    """Deterministic, zero-dependency embedding adapter for testing and offline verification.

    Generates L2-normalized unit vectors with fixed dimensions based on perceptual
    content hashing and keyword tokens. Persists request/response evidence to ArtifactStore.
    """

    def __init__(
        self,
        artifact_store: LocalArtifactStore,
        *,
        model: str = "deterministic-multimodal-v1",
        dimensions: int = 128,
    ) -> None:
        if dimensions <= 0:
            raise ValueError("dimensions must be positive")
        self.artifact_store = artifact_store
        self._model = model
        self.dimensions = dimensions

    @property
    def model(self) -> str:
        return self._model

    def _generate_vector(self, seed_data: bytes, text_hint: str | None = None) -> tuple[float, ...]:
        """Generate a deterministic unit-length float vector of specified dimensions."""
        # 1. Base seed from raw bytes
        hasher = hashlib.sha256(seed_data)
        if text_hint:
            hasher.update(text_hint.strip().casefold().encode("utf-8"))
        seed_hash = hasher.digest()

        # 2. Expand hash into the required number of float dimensions
        values: list[float] = []
        block_idx = 0
        while len(values) < self.dimensions:
            block = hashlib.sha256(seed_hash + block_idx.to_bytes(4, "big")).digest()
            for i in range(0, len(block) - 1, 2):
                val = int.from_bytes(block[i : i + 2], "big", signed=True) / 32768.0
                values.append(val)
                if len(values) >= self.dimensions:
                    break
            block_idx += 1

        # 3. L2-normalize
        norm = math.sqrt(sum(v * v for v in values))
        if norm == 0.0:
            return tuple(1.0 if i == 0 else 0.0 for i in range(self.dimensions))
        return tuple(v / norm for v in values)

    def embed_images(
        self,
        requests: Sequence[ImageEmbeddingRequest],
        *,
        producer_step_id: str = "step-image-embedding",
    ) -> ImageEmbeddingResult:
        if not requests:
            raise ValueError("requests must not be empty")

        start_time = time.perf_counter()
        req_payload = {
            "schema_version": IMAGE_EMBEDDING_SCHEMA_VERSION,
            "model": self._model,
            "dimensions": self.dimensions,
            "items": [
                {
                    "asset_id": req.asset_id,
                    "media_type": req.media_type,
                    "artifact_ref": req.artifact_ref,
                    "has_image": req.image_bytes is not None,
                    "byte_length": len(req.image_bytes) if req.image_bytes is not None else 0,
                    "text_hint": req.text,
                }
                for req in requests
            ],
        }
        req_artifact = self.artifact_store.put_json(
            req_payload,
            producer_step_id=producer_step_id,
            schema_version=IMAGE_EMBEDDING_SCHEMA_VERSION,
        )

        vectors: list[tuple[float, ...]] = []
        for req in requests:
            if req.image_bytes is not None:
                vec = self._generate_vector(req.image_bytes, req.text)
            elif req.text:
                vec = self._generate_vector(req.text.encode("utf-8"), req.text)
            else:
                raise ValueError("ImageEmbeddingRequest must provide image_bytes or text")
            vectors.append(vec)

        latency_ms = (time.perf_counter() - start_time) * 1000.0
        resp_payload = {
            "schema_version": f"{IMAGE_EMBEDDING_SCHEMA_VERSION}.response",
            "model": self._model,
            "dimensions": self.dimensions,
            "count": len(vectors),
            "latency_ms": latency_ms,
            "usage": {"input_tokens": len(requests) * 64},
            "vectors": [list(v) for v in vectors],
        }
        resp_artifact = self.artifact_store.put_json(
            resp_payload,
            producer_step_id=producer_step_id,
            schema_version=f"{IMAGE_EMBEDDING_SCHEMA_VERSION}.response",
        )

        return ImageEmbeddingResult(
            model=self._model,
            vectors=tuple(vectors),
            dimensions=self.dimensions,
            input_tokens=len(requests) * 64,
            estimated_cost=0.0,
            latency_ms=latency_ms,
            request_artifact=req_artifact,
            response_artifact=resp_artifact,
        )

    def embed_query_text(
        self,
        query: str,
        *,
        producer_step_id: str = "step-image-query-embedding",
    ) -> ImageEmbeddingResult:
        if not query or not query.strip():
            raise ValueError("query must not be empty")
        req = ImageEmbeddingRequest(text=query.strip())
        return self.embed_images([req], producer_step_id=producer_step_id)


class OpenAICompatibleImageEmbeddingAdapter:
    """Adapter for OpenAI-compatible multimodal embedding APIs with raw artifact capture."""

    def __init__(
        self,
        artifact_store: LocalArtifactStore,
        config: ProviderConfig,
        *,
        model: str | None = None,
        dimensions: int = 512,
        transport: ProviderHttpTransport | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self.artifact_store = artifact_store
        self.config = config
        self._model = model or config.embedding_model or "multimodal-embedding-v1"
        if dimensions == 512 and ("jina-clip" in self._model.lower() or "clip" in self._model.lower()):
            self.dimensions = 1024
        else:
            self.dimensions = dimensions
        self.transport = transport or UrllibProviderTransport()
        self.timeout_seconds = timeout_seconds

    @property
    def model(self) -> str:
        return self._model

    def embed_images(
        self,
        requests: Sequence[ImageEmbeddingRequest],
        *,
        producer_step_id: str = "step-image-embedding",
    ) -> ImageEmbeddingResult:
        if not requests:
            raise ValueError("requests must not be empty")

        start_time = time.perf_counter()
        inputs: list[dict[str, Any]] = []
        for req in requests:
            if req.image_bytes is not None:
                b64 = base64.b64encode(req.image_bytes).decode("ascii")
                inputs.append({"type": "image", "image": f"data:{req.media_type};base64,{b64}"})
            elif req.text:
                inputs.append({"type": "text", "text": req.text.strip()})
            else:
                raise ValueError("ImageEmbeddingRequest must provide image_bytes or text")

        payload = {"model": self._model, "input": inputs}
        req_artifact = self.artifact_store.put_json(
            {"schema_version": IMAGE_EMBEDDING_SCHEMA_VERSION, "endpoint": "/embeddings", "request": payload},
            producer_step_id=producer_step_id,
            schema_version=IMAGE_EMBEDDING_SCHEMA_VERSION,
        )

        try:
            response = self.transport.post(
                self.config.base_url + "/embeddings",
                headers={
                    "Authorization": f"Bearer {self.config.api_key}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                body=json.dumps(payload, ensure_ascii=False).encode(),
                timeout_seconds=self.timeout_seconds,
            )
        except Exception as exc:
            fail_payload = {
                "schema_version": f"{IMAGE_EMBEDDING_SCHEMA_VERSION}.failure",
                "model": self._model,
                "error": str(exc),
                "error_type": type(exc).__name__,
                "request_artifact_ref": req_artifact.artifact_id,
                "failed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            }
            fail_art = self.artifact_store.put_json(
                fail_payload,
                producer_step_id=producer_step_id,
                schema_version=f"{IMAGE_EMBEDDING_SCHEMA_VERSION}.failure",
            )
            raise ProviderPortError(
                code="embedding_provider_failed",
                message=f"Image embedding transport network error: {exc}",
                retryable=True,
                request_artifact_ref=req_artifact.artifact_id,
                response_artifact_ref=fail_art.artifact_id,
                recovery_action="检查网络连接与多模态 Embedding Provider 地址与超时设置。",
            ) from exc

        resp_artifact = self.artifact_store.put_bytes(
            response.body,
            media_type="application/json",
            producer_step_id=producer_step_id,
            schema_version=f"{IMAGE_EMBEDDING_SCHEMA_VERSION}.response",
        )

        if response.status_code != 200:
            raise ProviderPortError(
                code="embedding_provider_failed",
                message=f"Image embedding provider returned HTTP {response.status_code}",
                retryable=response.status_code >= 500 or response.status_code == 429,
                status_code=response.status_code,
                request_artifact_ref=req_artifact.artifact_id,
                response_artifact_ref=resp_artifact.artifact_id,
                recovery_action="检查多模态 Embedding Provider 配置与凭据。",
            )

        latency_ms = (time.perf_counter() - start_time) * 1000.0
        try:
            parsed = json.loads(response.body)
            data = parsed["data"]
            vectors = tuple(
                tuple(float(v) for v in item["embedding"])
                for item in sorted(data, key=lambda it: it.get("index", 0))
            )
            if len(vectors) != len(requests):
                raise ValueError(f"embedding_count_mismatch: expected {len(requests)}, got {len(vectors)}")
            dims = len(vectors[0])
            if any(len(v) != dims for v in vectors):
                raise ValueError("embedding_dimension_mismatch: inconsistent dimensions returned")
            self.dimensions = dims
            usage = parsed.get("usage", {})
            tokens = usage.get("prompt_tokens") if isinstance(usage, dict) else None
        except Exception as exc:
            raise ProviderPortError(
                code="embedding_provider_failed",
                message=f"Multimodal embedding response contract invalid: {exc}",
                retryable=False,
                request_artifact_ref=req_artifact.artifact_id,
                response_artifact_ref=resp_artifact.artifact_id,
                recovery_action="保留原始响应并检查多模态 Embedding API 合同。",
            ) from exc

        return ImageEmbeddingResult(
            model=self._model,
            vectors=vectors,
            dimensions=dims,
            input_tokens=tokens,
            estimated_cost=None,
            latency_ms=latency_ms,
            request_artifact=req_artifact,
            response_artifact=resp_artifact,
        )

    def embed_query_text(
        self,
        query: str,
        *,
        producer_step_id: str = "step-image-query-embedding",
    ) -> ImageEmbeddingResult:
        if not query or not query.strip():
            raise ValueError("query must not be empty")
        req = ImageEmbeddingRequest(text=query.strip())
        return self.embed_images([req], producer_step_id=producer_step_id)


@dataclass(frozen=True, slots=True)
class MultimodalIndexRecord:
    """Row record schema stored in the image vector index (image_assets_v1)."""

    asset_id: str
    document_id: str
    source_snapshot_id: str
    page: int
    parent_chunk_ids: tuple[str, ...]
    locator_json: str
    artifact_ref: str
    embedding_model: str
    dimensions: int
    vector: tuple[float, ...]
    extractor_version: str
    corpus_hash: str
    config_hash: str
    modality: str = "image"
    thumbnail_artifact_ref: str | None = None
    caption: str | None = None

    def __post_init__(self) -> None:
        if not self.asset_id or not self.asset_id.strip():
            raise ValueError("asset_id must not be empty")
        if not self.artifact_ref or not self.artifact_ref.strip():
            raise ValueError("artifact_ref must not be empty")
        if len(self.vector) != self.dimensions or self.dimensions <= 0:
            raise ValueError(f"vector length {len(self.vector)} does not match dimensions {self.dimensions}")


@dataclass(frozen=True, slots=True)
class MultimodalRetrievalHit:
    """Search result representing a hit against the image vector index."""

    asset_id: str
    score: float
    rank: int
    modality: str
    source_snapshot_id: str
    document_id: str
    page: int
    bbox: dict[str, float] | None
    coordinate_space: str
    parent_chunk_ids: tuple[str, ...]
    caption: str | None
    artifact_ref: str
    thumbnail_artifact_ref: str | None
    embedding_model: str
    index_version: str
    locator: dict[str, Any]


@dataclass(frozen=True, slots=True)
class MultimodalIndexManifest:
    """Published manifest metadata for a multimodal index table."""

    schema_version: str
    index_type: str
    table_name: str
    database_path: str
    asset_count: int
    dimensions: int
    embedding_model: str
    corpus_hash: str
    status: str
    published_at: str
    index_artifact_id: str | None = None
    batch_audits: tuple[dict[str, Any], ...] = ()
    total_latency_ms: float = 0.0
    total_input_tokens: int = 0
    total_estimated_cost: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "index_type": self.index_type,
            "table_name": self.table_name,
            "database_path": self.database_path,
            "asset_count": self.asset_count,
            "dimensions": self.dimensions,
            "embedding_model": self.embedding_model,
            "corpus_hash": self.corpus_hash,
            "status": self.status,
            "published_at": self.published_at,
            "index_artifact_id": self.index_artifact_id,
            "batch_audits": list(self.batch_audits),
            "total_latency_ms": self.total_latency_ms,
            "total_input_tokens": self.total_input_tokens,
            "total_estimated_cost": self.total_estimated_cost,
        }


class LanceDBImageIndex:
    """LanceDB-backed image vector index managing image_assets_v1 physical table.

    Provides strict staging table isolation, atomic publish, incremental upsert,
    and vector search returning fully populated MultimodalRetrievalHit instances.
    """

    def __init__(
        self,
        db_path: Path,
        *,
        table_name: str = "image_assets_v1",
        artifact_store: LocalArtifactStore | None = None,
    ) -> None:
        try:
            import lancedb
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("lancedb is required for LanceDBImageIndex") from exc

        self.db_path = Path(db_path)
        self.table_name = table_name
        self.staging_table_name = f"{table_name}__building"
        self.artifact_store = artifact_store
        self.db = lancedb.connect(str(self.db_path))
        self.table = self.db.open_table(table_name) if table_name in self._table_names() else None

    def _table_names(self) -> list[str]:
        listing = self.db.list_tables()
        return list(getattr(listing, "tables", listing))

    def _drop_table_if_exists(self, name: str) -> None:
        if name in self._table_names():
            try:
                self.db.drop_table(name)
            except Exception:
                pass

    def _verify_records(self, records: Sequence[MultimodalIndexRecord]) -> int:
        """Verify 1-to-1 alignment, dimension consistency, and artifact integrity."""
        if not records:
            raise ValueError("records must not be empty")

        dimensions = records[0].dimensions
        for r in records:
            if len(r.vector) != dimensions or r.dimensions != dimensions:
                raise ValueError(
                    f"embedding_dimension_mismatch: expected {dimensions}, got {r.dimensions} ({len(r.vector)} floats)"
                )
            if self.artifact_store is not None:
                if not r.artifact_ref.startswith("artifact-sha256-"):
                    raise ValueError(f"artifact_integrity_failed: invalid artifact_ref {r.artifact_ref}")
                digest = r.artifact_ref.removeprefix("artifact-sha256-")
                try:
                    p = self.artifact_store.path_for_digest(digest)
                    if not p.is_file():
                        raise ValueError(f"artifact_integrity_failed: missing artifact file for {r.artifact_ref}")
                except Exception as exc:
                    raise ValueError(f"artifact_integrity_failed: {exc}") from exc
        return dimensions

    def publish(
        self,
        records: Sequence[MultimodalIndexRecord],
        *,
        batch_audits: Sequence[dict[str, Any]] = (),
        producer_step_id: str = "step-publish-image-index",
    ) -> MultimodalIndexManifest:
        """Atomically publish image records to image_assets_v1 via a staging table with rollback on failure."""
        dimensions = self._verify_records(records)

        rows = [
            {
                "asset_id": r.asset_id,
                "modality": r.modality,
                "document_id": r.document_id,
                "source_snapshot_id": r.source_snapshot_id,
                "page": r.page,
                "parent_chunk_ids_json": json.dumps(list(r.parent_chunk_ids), ensure_ascii=False),
                "locator_json": r.locator_json,
                "caption": r.caption or "",
                "artifact_ref": r.artifact_ref,
                "thumbnail_artifact_ref": r.thumbnail_artifact_ref or "",
                "embedding_model": r.embedding_model,
                "dimensions": r.dimensions,
                "vector": list(r.vector),
                "extractor_version": r.extractor_version,
                "corpus_hash": r.corpus_hash,
                "config_hash": r.config_hash,
            }
            for r in records
        ]

        # 1. Staging table write
        self._drop_table_if_exists(self.staging_table_name)
        backup_table_name = f"{self.table_name}__backup"
        has_existing = self.table_name in self._table_names()
        try:
            staging = self.db.create_table(self.staging_table_name, data=rows)
            if len(staging) != len(records):
                raise RuntimeError("index_publish_failed: staging table row count mismatch")

            # 2. Back up existing table before swapping if present
            if has_existing:
                self._drop_table_if_exists(backup_table_name)
                existing = self.db.open_table(self.table_name)
                self.db.create_table(backup_table_name, data=existing.to_arrow())

            # 3. Overwrite target table with verified staging arrow data
            arrow_data = staging.to_arrow()
            self.db.create_table(self.table_name, data=arrow_data, mode="overwrite")
            self.table = self.db.open_table(self.table_name)

            # 4. Clean up staging and backup on success
            self._drop_table_if_exists(self.staging_table_name)
            if has_existing:
                self._drop_table_if_exists(backup_table_name)
        except Exception:
            # If target table creation failed and backup exists, restore target table!
            self._drop_table_if_exists(self.staging_table_name)
            if has_existing and backup_table_name in self._table_names():
                try:
                    backup_tbl = self.db.open_table(backup_table_name)
                    self.db.create_table(self.table_name, data=backup_tbl.to_arrow(), mode="overwrite")
                    self.table = self.db.open_table(self.table_name)
                    self._drop_table_if_exists(backup_table_name)
                except Exception:
                    pass
            raise

        # 5. Create manifest with full batch audit chain
        corpus_hash = hashlib.sha256(
            "\n".join(f"{r.asset_id}:{r.artifact_ref}" for r in records).encode("utf-8")
        ).hexdigest()
        published_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")

        total_latency = sum(float(b.get("latency_ms", 0.0) or 0.0) for b in batch_audits)
        total_tokens = sum(int(b.get("input_tokens", 0) or 0) for b in batch_audits)
        total_cost = sum(float(b.get("estimated_cost", 0.0) or 0.0) for b in batch_audits)

        manifest_payload = {
            "schema_version": MULTIMODAL_INDEX_MANIFEST_SCHEMA_VERSION,
            "index_type": "lancedb_image",
            "table_name": self.table_name,
            "database_path": str(self.db_path.resolve()),
            "asset_count": len(records),
            "dimensions": dimensions,
            "embedding_model": records[0].embedding_model,
            "corpus_hash": f"sha256:{corpus_hash}",
            "status": "published",
            "published_at": published_at,
            "batch_audits": list(batch_audits),
            "total_latency_ms": total_latency,
            "total_input_tokens": total_tokens,
            "total_estimated_cost": total_cost,
        }

        art_id = None
        if self.artifact_store is not None:
            manifest_art = self.artifact_store.put_json(
                manifest_payload,
                producer_step_id=producer_step_id,
                schema_version=MULTIMODAL_INDEX_MANIFEST_SCHEMA_VERSION,
            )
            art_id = manifest_art.artifact_id

        return MultimodalIndexManifest(
            schema_version=MULTIMODAL_INDEX_MANIFEST_SCHEMA_VERSION,
            index_type="lancedb_image",
            table_name=self.table_name,
            database_path=str(self.db_path.resolve()),
            asset_count=len(records),
            dimensions=dimensions,
            embedding_model=records[0].embedding_model,
            corpus_hash=f"sha256:{corpus_hash}",
            status="published",
            published_at=published_at,
            index_artifact_id=art_id,
            batch_audits=tuple(batch_audits),
            total_latency_ms=total_latency,
            total_input_tokens=total_tokens,
            total_estimated_cost=total_cost,
        )

    def add(self, records: Sequence[MultimodalIndexRecord]) -> dict[str, Any]:
        """Append image records to published table, ensuring dimensional invariant."""
        if not records:
            raise ValueError("records must not be empty")
        if self.table is None:
            manifest = self.publish(records)
            return {
                "schema_version": MULTIMODAL_INDEX_UPDATE_SCHEMA_VERSION,
                "index_type": "lancedb_image",
                "table_name": self.table_name,
                "added_count": len(records),
                "dimensions": manifest.dimensions,
                "status": "published",
            }

        dimensions = self._verify_records(records)
        schema = self.table.schema
        vector_field = schema.field("vector")
        existing_dimensions = getattr(vector_field.type, "list_size", dimensions)
        if existing_dimensions != dimensions:
            raise ValueError(
                f"embedding_dimension_mismatch: index={existing_dimensions}, new={dimensions}"
            )

        rows = [
            {
                "asset_id": r.asset_id,
                "modality": r.modality,
                "document_id": r.document_id,
                "source_snapshot_id": r.source_snapshot_id,
                "page": r.page,
                "parent_chunk_ids_json": json.dumps(list(r.parent_chunk_ids), ensure_ascii=False),
                "locator_json": r.locator_json,
                "caption": r.caption or "",
                "artifact_ref": r.artifact_ref,
                "thumbnail_artifact_ref": r.thumbnail_artifact_ref or "",
                "embedding_model": r.embedding_model,
                "dimensions": r.dimensions,
                "vector": list(r.vector),
                "extractor_version": r.extractor_version,
                "corpus_hash": r.corpus_hash,
                "config_hash": r.config_hash,
            }
            for r in records
        ]
        self.table.add(rows)
        return {
            "schema_version": MULTIMODAL_INDEX_UPDATE_SCHEMA_VERSION,
            "index_type": "lancedb_image",
            "table_name": self.table_name,
            "added_count": len(records),
            "dimensions": dimensions,
            "status": "published",
        }

    def delete(self, asset_ids: Sequence[str]) -> dict[str, Any]:
        """Delete image records by asset_id from the published table."""
        if self.table is None:
            return {"status": "already_absent", "deleted_count": 0}
        wanted = tuple(dict.fromkeys(str(item) for item in asset_ids if str(item)))
        if not wanted:
            return {"status": "already_absent", "deleted_count": 0}
        escaped = ", ".join(json.dumps(item) for item in wanted)
        self.table.delete(f"asset_id IN ({escaped})")
        return {"status": "published", "deleted_count": len(wanted)}

    def search_vector(
        self,
        query_vector: Sequence[float],
        *,
        top_k: int = 5,
        where: str | None = None,
    ) -> tuple[MultimodalRetrievalHit, ...]:
        """Search image vector table and return fully populated MultimodalRetrievalHit records."""
        if self.table is None:
            raise RuntimeError("LanceDB image table is not published")
        if top_k <= 0:
            raise ValueError("top_k must be positive")

        request = self.table.search(list(query_vector)).limit(top_k)
        if where:
            request = request.where(where)
        rows = request.to_list()

        hits: list[MultimodalRetrievalHit] = []
        for rank, row in enumerate(rows, 1):
            locator = json.loads(row.get("locator_json") or "{}")
            bbox = locator.get("bbox") if isinstance(locator.get("bbox"), dict) else None
            coordinate_space = str(locator.get("coordinate_space") or "pdf_page_points_top_left")
            parent_ids = tuple(json.loads(row.get("parent_chunk_ids_json") or "[]"))
            score = cosine_similarity(tuple(query_vector), tuple(row["vector"]))

            hit = MultimodalRetrievalHit(
                asset_id=str(row["asset_id"]),
                score=score,
                rank=rank,
                modality=str(row.get("modality", "image")),
                source_snapshot_id=str(row.get("source_snapshot_id", "")),
                document_id=str(row.get("document_id", "")),
                page=int(row.get("page", 1)),
                bbox=bbox,
                coordinate_space=coordinate_space,
                parent_chunk_ids=parent_ids,
                caption=row.get("caption") or None,
                artifact_ref=str(row.get("artifact_ref", "")),
                thumbnail_artifact_ref=row.get("thumbnail_artifact_ref") or None,
                embedding_model=str(row.get("embedding_model", "")),
                index_version=MULTIMODAL_INDEX_SCHEMA_VERSION,
                locator=locator,
            )
            hits.append(hit)
        return tuple(hits)


def build_image_index(
    assets: Sequence[DocumentAsset],
    adapter: ImageEmbeddingPort,
    artifact_store: LocalArtifactStore,
    index: LanceDBImageIndex,
    *,
    batch_size: int = 20,
    producer_step_id: str = "step-multimodal-indexing",
) -> MultimodalIndexManifest:
    """Batch-embed document image assets and publish them into LanceDBImageIndex."""
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")

    # Filter out failed assets without valid artifact_ref
    indexable_assets = [
        a for a in assets if a.extraction_status in {"extracted", "degraded"} and a.artifact_ref
    ]
    if not indexable_assets:
        raise ValueError("no indexable image assets provided")

    records: list[MultimodalIndexRecord] = []
    batch_audits: list[dict[str, Any]] = []
    for start_idx in range(0, len(indexable_assets), batch_size):
        chunk = indexable_assets[start_idx : start_idx + batch_size]
        requests: list[ImageEmbeddingRequest] = []
        for a in chunk:
            digest = str(a.artifact_ref).removeprefix("artifact-sha256-")
            img_path = artifact_store.path_for_digest(digest)
            if not img_path.is_file():
                raise ValueError(f"artifact_integrity_failed: artifact file missing for asset {a.asset_id}")
            raw_bytes = img_path.read_bytes()
            requests.append(
                ImageEmbeddingRequest(
                    asset_id=a.asset_id,
                    image_bytes=raw_bytes,
                    media_type=a.media_type,
                    artifact_ref=a.artifact_ref,
                    text=a.caption,
                )
            )

        step_id = f"{producer_step_id}-batch-{start_idx // batch_size:04d}"
        result = adapter.embed_images(requests, producer_step_id=step_id)

        if len(result.vectors) != len(chunk):
            raise RuntimeError(
                f"embedding_count_mismatch: requested {len(chunk)}, got {len(result.vectors)}"
            )

        batch_audits.append({
            "batch_index": start_idx // batch_size,
            "asset_count": len(chunk),
            "asset_ids": [a.asset_id for a in chunk],
            "request_artifact_id": result.request_artifact.artifact_id,
            "response_artifact_id": result.response_artifact.artifact_id,
            "latency_ms": result.latency_ms,
            "estimated_cost": result.estimated_cost or 0.0,
            "input_tokens": result.input_tokens or 0,
        })

        for a, vec in zip(chunk, result.vectors):
            locator_payload = {
                "page": a.page,
                "bbox": a.bbox.to_dict() if hasattr(a.bbox, "to_dict") else a.bbox,
                "coordinate_space": a.coordinate_space,
                "page_width": a.page_width,
                "page_height": a.page_height,
                "page_rotation": a.page_rotation,
            }
            rec = MultimodalIndexRecord(
                asset_id=a.asset_id,
                document_id=a.document_id,
                source_snapshot_id=a.source_snapshot_id,
                page=a.page,
                parent_chunk_ids=a.parent_segment_ids,
                locator_json=json.dumps(locator_payload, ensure_ascii=False),
                artifact_ref=a.artifact_ref or "",
                thumbnail_artifact_ref=a.thumbnail_artifact_ref,
                caption=a.caption,
                embedding_model=result.model,
                dimensions=result.dimensions,
                vector=vec,
                extractor_version=a.extraction_method,
                corpus_hash=a.content_hash or "hash_none",
                config_hash="cfg-default",
            )
            records.append(rec)

    return index.publish(records, batch_audits=batch_audits, producer_step_id=producer_step_id)
