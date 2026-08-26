from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess

from conflux_weave.indexing import LanceDBDenseIndex, load_chunks
from conflux_weave.provider import OpenAICompatibleEmbeddingAdapter, ProviderConfig
from conflux_weave.runtime import LocalArtifactStore


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-manifest", type=Path, default=Path("var/acceptance/v0.3-s1/corpus-import-manifest.json"))
    parser.add_argument("--base-lancedb", type=Path, default=Path("var/acceptance/v0.3-s1/lancedb"))
    parser.add_argument("--new-manifest", type=Path, default=Path("var/acceptance/v0.3-s1/s15c-new-import-manifest.json"))
    parser.add_argument("--artifact-root", type=Path, default=Path("var/artifacts/sha256"))
    parser.add_argument("--dotenv", type=Path, default=Path(".env"))
    parser.add_argument("--output-root", type=Path, default=Path("var/acceptance/v0.3-s1/s15c-corpora"))
    parser.add_argument("--execute-live", action="store_true")
    args = parser.parse_args()
    if not args.execute_live:
        parser.error("--execute-live is required because new Chunk embeddings call the Provider")

    summary_path = args.output_root / "preparation-summary.json"
    if summary_path.is_file():
        print(summary_path.read_text(encoding="utf-8"))
        return

    store = LocalArtifactStore(args.artifact_root)
    config = ProviderConfig.from_environment(args.dotenv)
    embedding = OpenAICompatibleEmbeddingAdapter(store, config)
    base_documents = load_chunks(args.base_manifest, store)
    new_documents = load_chunks(args.new_manifest, store)
    if not new_documents:
        raise ValueError("new corpus contains no imported chunks")
    if {item.document_id for item in base_documents} & {item.document_id for item in new_documents}:
        raise ValueError("new corpus overlaps the frozen local corpus")

    vectors: list[tuple[float, ...]] = []
    response_artifacts: list[str] = []
    input_tokens = 0
    for start in range(0, len(new_documents), 20):
        batch = new_documents[start : start + 20]
        result = embedding.embed(
            [item.text for item in batch],
            producer_step_id=f"s15c-new-embedding-{start // 20:03d}",
        )
        vectors.extend(result.vectors)
        response_artifacts.append(result.response_artifact.artifact_id)
        input_tokens += result.input_tokens or 0
    new_vectors = tuple(vectors)

    args.output_root.mkdir(parents=True, exist_ok=True)
    new_index = LanceDBDenseIndex(args.output_root / "new-lancedb", table_name="paper_chunks")
    new_index_manifest = new_index.publish(new_documents, new_vectors)
    new_index_manifest.update(
        embedding_model=embedding.model,
        batch_response_artifacts=response_artifacts,
    )
    _write_json(args.output_root / "new-index-manifest.json", new_index_manifest)

    base_index = LanceDBDenseIndex(args.base_lancedb, table_name="paper_chunks")
    base_vectors = _aligned_vectors(base_index, base_documents)
    mixed_documents = base_documents + new_documents
    mixed_index = LanceDBDenseIndex(args.output_root / "mixed-lancedb", table_name="paper_chunks")
    mixed_index_manifest = mixed_index.publish(mixed_documents, base_vectors + new_vectors)
    mixed_index_manifest.update(
        embedding_model=embedding.model,
        reused_base_vector_count=len(base_vectors),
        new_vector_count=len(new_vectors),
        batch_response_artifacts=response_artifacts,
    )
    _write_json(args.output_root / "mixed-index-manifest.json", mixed_index_manifest)

    base_payload = _read_object(args.base_manifest)
    new_payload = _read_object(args.new_manifest)
    mixed_manifest = {
        "schema_version": "conflux-weave.corpus-import-manifest.v1",
        "root": "mixed:s15c-local-plus-new",
        "roots": [base_payload.get("root"), new_payload.get("root")],
        "file_count": len(base_payload["files"]) + len(new_payload["files"]),
        "status_counts": {"imported": len(base_payload["files"]) + len(new_payload["files"])},
        "files": base_payload["files"] + new_payload["files"],
    }
    mixed_manifest_path = args.output_root / "mixed-import-manifest.json"
    _write_json(mixed_manifest_path, mixed_manifest)

    summary = {
        "schema_version": "conflux-weave.s15c-corpus-preparation.v1",
        "source_revision": _revision(),
        "base_manifest": str(args.base_manifest),
        "base_manifest_sha256": _file_hash(args.base_manifest),
        "new_manifest": str(args.new_manifest),
        "new_manifest_sha256": _file_hash(args.new_manifest),
        "mixed_manifest": str(mixed_manifest_path),
        "mixed_manifest_sha256": _file_hash(mixed_manifest_path),
        "document_chunks": {
            "local": len(base_documents),
            "new": len(new_documents),
            "mixed": len(mixed_documents),
        },
        "embedding_model": embedding.model,
        "new_embedding_input_tokens": input_tokens,
        "embedding_response_artifacts": response_artifacts,
        "new_index_manifest": new_index_manifest,
        "mixed_index_manifest": mixed_index_manifest,
        "evidence_boundary": "Only new PDF chunks called the Embedding Provider; local vectors were reused after exact chunk-lineage validation.",
    }
    _write_json(summary_path, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def _aligned_vectors(index: LanceDBDenseIndex, documents) -> tuple[tuple[float, ...], ...]:
    if index.table is None:
        raise ValueError("base LanceDB table is unavailable")
    rows = {row["chunk_id"]: row for row in index.table.to_arrow().to_pylist()}
    if set(rows) != {item.document_id for item in documents}:
        raise ValueError("base LanceDB chunk ids do not match the import manifest")
    vectors = []
    for item in documents:
        row = rows[item.document_id]
        if row["text"] != item.text or row["source_snapshot_id"] != (item.source_snapshot_id or ""):
            raise ValueError(f"base LanceDB lineage mismatch: {item.document_id}")
        if json.loads(row["locator_json"]) != (item.locator or {}):
            raise ValueError(f"base LanceDB locator mismatch: {item.document_id}")
        vectors.append(tuple(float(value) for value in row["vector"]))
    return tuple(vectors)


def _read_object(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _revision() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()


if __name__ == "__main__":
    main()
