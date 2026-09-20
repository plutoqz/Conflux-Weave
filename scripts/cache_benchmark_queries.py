"""Pre-embed and cache query vectors for all 150 benchmark cases using jina-clip-v2."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from conflux_weave.multimodal_indexing import (
    ImageEmbeddingRequest,
    OpenAICompatibleImageEmbeddingAdapter,
)
from conflux_weave.provider import ProviderConfig, UrllibProviderTransport
from conflux_weave.runtime.artifacts import LocalArtifactStore

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    store = LocalArtifactStore(ROOT / "var" / "artifacts")
    config = ProviderConfig(
        base_url=os.getenv("CONFLUX_WEAVE_PROVIDER_BASE_URL"),
        api_key=os.getenv("CONFLUX_WEAVE_PROVIDER_API_KEY"),
        model=os.getenv("CONFLUX_WEAVE_PROVIDER_MODEL"),
        image_embedding_model="jina-clip-v2",
    )
    transport = UrllibProviderTransport()
    adapter = OpenAICompatibleImageEmbeddingAdapter(
        config=config,
        transport=transport,
        artifact_store=store,
        model="jina-clip-v2",
        dimensions=1024,
    )

    cache_file = ROOT / "var" / "cache" / "benchmark_v2_query_vectors.json"
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cached: dict[str, list[float]] = {}
    if cache_file.exists():
        try:
            cached = json.loads(cache_file.read_text(encoding="utf-8"))
        except Exception:
            cached = {}

    cases_file = ROOT / "datasets" / "regression" / "p2-multimodal-benchmark-v2" / "cases.jsonl"
    cases = [json.loads(line) for line in open(cases_file, encoding="utf-8")]

    neg_manifest_file = (
        ROOT / "datasets" / "regression" / "p2-multimodal-benchmark-v2" / "near_domain_negatives_manifest.json"
    )
    if neg_manifest_file.is_file():
        neg_cases = json.loads(neg_manifest_file.read_text(encoding="utf-8")).get("cases", [])
        cases.extend(neg_cases)

    to_embed = [c["query"] for c in cases if c["query"] not in cached]
    print(f"Total queries: {len(cases)}, already cached: {len(cached)}, needing embedding: {len(to_embed)}")

    batch_size = 10
    total_batches = (len(to_embed) + batch_size - 1) // batch_size
    for b_idx in range(total_batches):
        chunk = to_embed[b_idx * batch_size : (b_idx + 1) * batch_size]
        reqs = [ImageEmbeddingRequest(text=q) for q in chunk]
        res = adapter.embed_images(reqs)
        for q, vec in zip(chunk, res.vectors):
            cached[q] = list(vec)
        print(f"Embedded query batch {b_idx + 1}/{total_batches} ({len(chunk)} queries). Cached {len(cached)} total.")
        cache_file.write_text(json.dumps(cached), encoding="utf-8")
        time.sleep(0.5)

    print(f"Query vector cache ready: {len(cached)} queries cached at {cache_file}")


if __name__ == "__main__":
    main()
