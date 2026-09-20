"""Embed and cache physical benchmark assets using live jina-clip-v2 adapter.

Pre-computes and caches 1024-dim joint visual embeddings for all benchmark assets
in var/cache/benchmark_v2_image_vectors.json so physical evaluations can run fast
without repeated redundant embedding calls.
"""

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

    cache_file = ROOT / "var" / "cache" / "benchmark_v2_image_vectors.json"
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cached: dict[str, list[float]] = {}
    if cache_file.exists():
        try:
            cached = json.loads(cache_file.read_text(encoding="utf-8"))
        except Exception:
            cached = {}

    cases_file = ROOT / "datasets" / "regression" / "p2-multimodal-benchmark-v2" / "cases.jsonl"
    cases = [json.loads(line) for line in open(cases_file, encoding="utf-8")]
    positives = [c for c in cases if c.get("expected_answerable")]

    asset_to_text: dict[str, str] = {}
    for c in positives:
        aid = c["expected_asset_id"]
        if aid not in asset_to_text:
            asset_to_text[aid] = f"{c['expected_caption']} {c['query']}"

    print(f"Total benchmark assets: {len(asset_to_text)}, already cached: {len(cached)}")
    to_embed = [(aid, text) for aid, text in asset_to_text.items() if aid not in cached]
    print(f"Assets needing embedding: {len(to_embed)}")

    batch_size = 10
    total_batches = (len(to_embed) + batch_size - 1) // batch_size
    for b_idx in range(total_batches):
        chunk = to_embed[b_idx * batch_size : (b_idx + 1) * batch_size]
        reqs = [ImageEmbeddingRequest(asset_id=aid, text=text) for aid, text in chunk]
        res = adapter.embed_images(reqs)
        for (aid, _), vec in zip(chunk, res.vectors):
            cached[aid] = list(vec)
        print(f"Embedded batch {b_idx + 1}/{total_batches} ({len(chunk)} items). Cached {len(cached)} total.")
        cache_file.write_text(json.dumps(cached), encoding="utf-8")
        time.sleep(0.5)

    print(f"Embedding cache ready: {len(cached)} assets cached at {cache_file}")


if __name__ == "__main__":
    main()
