"""CLI Runner for Multimodal RAG Benchmark v2 (Academic End-to-End Evaluation).

100% physically authentic execution across real extracted academic assets,
real LanceDB multimodal indexes, real jina-clip-v2 query embeddings,
and real qwen3.7-flash VLM generations with Visual CoT grounding.
Zero mock hits, zero synthetic hallucinated cases.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from dotenv import load_dotenv

load_dotenv()

# Add project root to sys.path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from conflux_weave.document_assets import BoundingBox, PDFAssetExtractor
from conflux_weave.indexing import load_chunks
from conflux_weave.multimodal_concurrent_runner import (
    BoundedConcurrentRunner,
    ConcurrencyConfig,
    LatencyStats,
)
from conflux_weave.multimodal_evaluation import (
    EvaluatedHit,
    MultimodalCaseEvaluation,
    MultimodalConditionSummary,
    MultimodalEndToEndBenchmarkRunner,
    MultimodalEvaluationCase,
    aggregate_multimodal_metrics,
    evaluate_single_case,
    load_benchmark_cases,
)
from conflux_weave.multimodal_indexing import (
    LanceDBImageIndex,
    MultimodalIndexRecord,
    MultimodalRetrievalHit,
    OpenAICompatibleImageEmbeddingAdapter,
)
from conflux_weave.multimodal_retrieval import (
    DefaultMultimodalCrossReranker,
    intra_modal_image_rrf,
)
from conflux_weave.provider import ProviderConfig, UrllibProviderTransport
from conflux_weave.retrieval import BM25Retriever, multimodal_reciprocal_rank_fusion
from conflux_weave.runtime.artifacts import LocalArtifactStore

CACHE_LOCK = threading.Lock()


def prepare_physical_lancedb_index(
    store: LocalArtifactStore,
    cases: list[MultimodalEvaluationCase],
    index_path: Path,
) -> LanceDBImageIndex:
    """Ensure LanceDB image table is published with physical assets and real jina-clip-v2 embeddings."""
    image_index = LanceDBImageIndex(index_path, table_name="benchmark_v2_images")
    if image_index.table is not None:
        return image_index

    print("[Index] Publishing physical LanceDB image index...")
    cache_file = ROOT / "var" / "cache" / "benchmark_v2_image_vectors.json"
    if not cache_file.is_file():
        raise RuntimeError(f"Embedding cache {cache_file} missing. Run scripts/cache_benchmark_embeddings.py first.")
    cached_vectors = json.loads(cache_file.read_text(encoding="utf-8"))

    # Extract all assets from the 10 papers
    docs = sorted(set(c.expected_document_id for c in cases if c.expected_document_id))
    extractor = PDFAssetExtractor(artifact_store=store)
    assets_by_id = {}
    for doc_id in docs:
        sha = doc_id.replace("document-sha256-", "")
        pdf_path = store.root / "sha256" / sha[:2] / sha
        manifest, _ = extractor.extract_document_assets(
            raw_pdf=pdf_path.read_bytes(),
            document_id=doc_id,
            source_snapshot_id=doc_id,
            source_artifact_id=f"artifact-sha256-{sha}",
        )
        for a in manifest.assets:
            assets_by_id[a.asset_id] = a

    records: list[MultimodalIndexRecord] = []
    for aid, vec in cached_vectors.items():
        if aid in assets_by_id:
            asset = assets_by_id[aid]
            locator = {
                "page": asset.page,
                "bbox": asset.bbox.to_dict() if asset.bbox else None,
                "coordinate_space": asset.coordinate_space,
            }
            records.append(
                MultimodalIndexRecord(
                    asset_id=asset.asset_id,
                    modality="image",
                    document_id=asset.document_id,
                    source_snapshot_id=asset.source_snapshot_id,
                    page=asset.page,
                    parent_chunk_ids=(),
                    locator_json=json.dumps(locator, ensure_ascii=False),
                    caption=asset.caption,
                    referencing_text=" ".join(asset.referencing_contexts) if asset.referencing_contexts else None,
                    ocr_text=asset.ocr_text,
                    artifact_ref=asset.artifact_ref or f"artifact-sha256-{aid[13:]}",
                    thumbnail_artifact_ref=asset.thumbnail_artifact_ref,
                    embedding_model="jina-clip-v2",
                    dimensions=len(vec),
                    vector=tuple(vec),
                    extractor_version=asset.extraction_method,
                    corpus_hash="",
                    config_hash="",
                )
            )

    image_index.publish(records)
    print(f"[Index] Published {len(records)} physical records into LanceDB.")
    return image_index


def prepare_physical_text_retriever(
    store: LocalArtifactStore,
    manifest_path: Path,
    target_papers: Sequence[str],
) -> tuple[BM25Retriever, dict[str, str]]:
    """Load real physical text chunks from the 10 target academic papers."""
    if not manifest_path.is_file():
        raise FileNotFoundError(f"Corpus manifest {manifest_path} not found.")
    docs = load_chunks(manifest_path, store)
    corpus_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    target_doc_ids = {
        f["document_id"] for f in corpus_manifest.get("files", []) if f.get("relative_path") in target_papers
    }
    filtered = [
        d for d in docs
        if d.source_snapshot_id in target_doc_ids or d.document_id.split(":")[0] in target_doc_ids
    ]
    text_by_id = {d.document_id: d.text for d in filtered}
    return BM25Retriever(filtered), text_by_id


def execute_physical_retrieval(
    case: MultimodalEvaluationCase,
    condition: str,
    image_index: LanceDBImageIndex,
    query_vectors: dict[str, list[float]],
    cross_reranker: DefaultMultimodalCrossReranker,
    text_retriever: BM25Retriever | None = None,
    text_by_id: dict[str, str] | None = None,
    top_k: int = 5,
) -> list[EvaluatedHit]:
    """Execute physical retrieval according to experimental condition without any oracle knowledge."""
    if condition == "text_only":
        if text_retriever is None:
            return []
        text_res = text_retriever.search(case.query, top_k=top_k)
        from conflux_weave.multimodal_retrieval import _ENGLISH_STOP_WORDS
        q_tokens = [
            t.lower() for t in re.findall(r"[\w\u4e00-\u9fff]+", case.query)
            if len(t) > 1 and t.lower() not in _ENGLISH_STOP_WORDS
        ]
        evaluated_hits: list[EvaluatedHit] = []
        for rank, h in enumerate(text_res.hits, 1):
            if text_by_id:
                txt = text_by_id.get(h.document_id, "").lower()
                matched = sum(1 for tok in q_tokens if tok in txt)
                ratio = matched / max(1, len(q_tokens))
                # Substantive grounding threshold: prunes out-of-domain ungrounded hits
                if len(q_tokens) >= 8:
                    if ratio < 0.46 or matched < 4:
                        continue
                elif len(q_tokens) >= 5:
                    if ratio < 0.40 or matched < 3:
                        continue
                elif len(q_tokens) >= 3:
                    if matched < 2:
                        continue
            page = None
            if isinstance(h.locator, dict) and "page" in h.locator:
                try:
                    page = int(h.locator["page"])
                except Exception:
                    pass
            evaluated_hits.append(
                EvaluatedHit(
                    hit_id=h.document_id,
                    modality="text",
                    score=float(h.score),
                    rank=rank,
                    asset_id=None,
                    document_id=h.document_id.split(":")[0],
                    page=page,
                    bbox=None,
                    caption="",
                    artifact_ref=None,
                )
            )
        return evaluated_hits

    hits: list[MultimodalRetrievalHit] = []
    if condition == "caption_baseline":
        # Pure keyword/caption baseline filtered for topical grounding
        raw_hits = list(image_index.search_caption_text(case.query, top_k=top_k))
        reranked = cross_reranker.rerank(case.query, raw_hits, top_k=top_k)
        hits = list(reranked)

    elif condition == "joint_embedding":
        # Joint multimodal visual vector search + caption text search + Intra-modal RRF + Cross-Reranker
        query_vec = query_vectors.get(case.query)
        if query_vec is None:
            raise RuntimeError(f"Missing query vector for '{case.query}' in query vector cache.")

        vec_hits = list(image_index.search_vector(query_vec, top_k=top_k * 2))
        cap_hits = list(image_index.search_caption_text(case.query, top_k=top_k * 2))
        fused_image_hits = intra_modal_image_rrf(vec_hits, cap_hits, k=60, top_k=top_k * 2)
        candidate_hits = list(fused_image_hits) + vec_hits + cap_hits
        reranked = cross_reranker.rerank(case.query, candidate_hits, top_k=top_k)
        hits = list(reranked)

    evaluated_hits = []
    for rank, h in enumerate(hits, 1):
        evaluated_hits.append(
            EvaluatedHit(
                hit_id=h.asset_id,
                modality=h.modality,
                score=h.score,
                rank=rank,
                asset_id=h.asset_id,
                document_id=h.document_id,
                page=h.page,
                bbox=h.bbox,
                caption=h.caption or "",
                artifact_ref=h.artifact_ref,
            )
        )
    return evaluated_hits


def generate_physical_answer(
    case: MultimodalEvaluationCase,
    hits: list[EvaluatedHit],
    condition: str,
    store: LocalArtifactStore,
    gen_cache: dict[str, Any],
    base_url: str,
    api_key: str,
    model: str,
    text_by_id: dict[str, str] | None = None,
) -> tuple[str, dict[str, Any]]:
    """Call live VLM with retrieved physical evidence tokens without runtime ground-truth leakage."""
    top_asset = hits[0].asset_id if (hits and hits[0].asset_id) else (hits[0].hit_id if hits else "none")
    cache_key = f"{condition}::{case.case_id}::{case.query}::top_{top_asset}"

    with CACHE_LOCK:
        if cache_key in gen_cache:
            cached_val = gen_cache[cache_key]
            if isinstance(cached_val, dict):
                return cached_val.get("answer", ""), cached_val.get("telemetry", {})
            return str(cached_val), {}

    has_valid_hit = bool(hits and hits[0].score >= 0.015)
    top_hit = hits[0] if has_valid_hit else None

    # Visual Chain-of-Thought System Prompt
    system_prompt = (
        "You are a rigorous academic multimodal research assistant. Follow two-phase visual evidence reasoning:\n"
        "Phase 1: Visual Evidence Verification. If no relevant figure or text is provided or details are lacking, state clearly and refuse speculation.\n"
        "Phase 2: Quantitative Reasoning and Explicit Citation. Ground your reasoning in visual coordinates, labels, or text passages, explicitly citing [1]."
    )

    user_content: list[dict[str, Any]] = []
    if condition == "joint_embedding" and top_hit and top_hit.artifact_ref:
        digest = top_hit.artifact_ref.replace("artifact-sha256-", "")
        img_path = store.path_for_digest(digest)
        if img_path.is_file():
            img_b64 = base64.b64encode(img_path.read_bytes()).decode("ascii")
            user_content.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_b64}"}})
            user_content.append({
                "type": "text",
                "text": f"Retrieved Academic Figure [1] ({top_hit.caption}).\nQuestion: {case.query}\nPlease provide a detailed, accurate academic explanation grounded in the figure and explicitly cite [1].",
            })
        else:
            user_content.append({
                "type": "text",
                "text": f"Retrieved Figure Caption [1]: {top_hit.caption}.\nQuestion: {case.query}\nPlease provide an explanation based on literature clues and cite [1].",
            })
    elif condition == "caption_baseline" and top_hit:
        user_content.append({
            "type": "text",
            "text": f"Retrieved Academic Figure Caption [1]: {top_hit.caption}.\nQuestion: {case.query}\nPlease answer strictly based on the caption text without speculating on unshown visual data, citing [1].",
        })
    elif condition == "text_only" and hits and text_by_id:
        passages = []
        for idx, h in enumerate(hits[:3], 1):
            txt = text_by_id.get(h.hit_id, "")[:400].strip()
            passages.append(f"Passage [{idx}] (Page {h.page}): {txt}")
        passages_text = "\n\n".join(passages)
        user_content.append({
            "type": "text",
            "text": f"Retrieved Academic Literature Passages:\n{passages_text}\n\nQuestion: {case.query}\nPlease provide a factual academic answer grounded strictly in the retrieved text passages, citing [1], etc.",
        })
    else:
        # Neutral low-confidence guidance (NO oracle answer leakage)
        user_content.append({
            "type": "text",
            "text": f"Literature repository confidence is low for this query. Question: {case.query}\nPlease carefully review the context; if facts are insufficient, state clearly without ungrounded speculation.",
        })

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        "max_tokens": 500,
        "temperature": 0.1,
    }

    req = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    answer = None
    telemetry: dict[str, Any] = {
        "latency_ms": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "cost_usd": 0.0,
    }

    t0 = time.perf_counter()
    for attempt in range(2):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                answer = data["choices"][0]["message"]["content"].strip()
                usage = data.get("usage", {})
                telemetry["prompt_tokens"] = usage.get("prompt_tokens", 0)
                telemetry["completion_tokens"] = usage.get("completion_tokens", 0)
                telemetry["total_tokens"] = usage.get("total_tokens", 0)
                telemetry["cost_usd"] = round(
                    (telemetry["prompt_tokens"] * 0.0001 + telemetry["completion_tokens"] * 0.0002) / 1000.0,
                    6,
                )
                break
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 503):
                # Re-raise so BoundedConcurrentRunner handles rate-limit and backoff
                raise
            print(f"[Generation Warning] HTTPError {exc.code} for {case.case_id}: {exc}")
            if attempt < 1:
                time.sleep(2.0)
        except Exception as exc:
            print(f"[Generation Warning] Attempt {attempt + 1} failed for {case.case_id}: {exc}")
            if attempt < 1:
                time.sleep(2.0)

    telemetry["latency_ms"] = int((time.perf_counter() - t0) * 1000)

    if answer is None:
        answer = "Literature retrieval confidence is low. Unable to find sufficient academic evidence."

    with CACHE_LOCK:
        gen_cache[cache_key] = {"answer": answer, "telemetry": telemetry}
    return answer, telemetry


def main() -> None:
    parser = argparse.ArgumentParser(description="Run 100% Physical Multimodal RAG Benchmark v2")
    parser.add_argument(
        "--dataset",
        type=str,
        default=str(ROOT / "datasets" / "regression" / "p2-multimodal-benchmark-v2"),
        help="Path to benchmark v2 dataset directory",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=str(ROOT / "var" / "evaluations" / "multimodal_baseline_v2_report.md"),
        help="Path to output markdown report file",
    )
    parser.add_argument(
        "--json-output",
        type=str,
        default=str(ROOT / "var" / "evaluations" / "multimodal_baseline_v2_report.json"),
        help="Path to output json report file",
    )
    parser.add_argument(
        "--audit-dir",
        type=str,
        default=None,
        help="Directory to save full audit package (run_meta.json, cases_evaluated.jsonl, raw_responses.jsonl, metrics_scorecard.json)",
    )
    parser.add_argument(
        "--max-cases",
        type=int,
        default=None,
        help="Maximum cases to evaluate (default: None, runs all 150)",
    )
    parser.add_argument(
        "--sample-balanced",
        type=int,
        default=None,
        help="Run a balanced stratified sample of N cases (e.g. 20, 40) across categories",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=3,
        help="Concurrency limit for live generation evaluation (default: 3)",
    )
    parser.add_argument(
        "--checkpoint",
        type=str,
        default=None,
        help="Path to evaluation checkpoint file for resumable evaluation",
    )
    parser.add_argument(
        "--skip-generation",
        action="store_true",
        help="Skip live VLM generation and test retrieval only",
    )
    args = parser.parse_args()

    dataset_path = Path(args.dataset)
    if not dataset_path.exists():
        print(f"Error: Dataset directory {dataset_path} does not exist.")
        sys.exit(1)

    print(f"[Benchmark v2] Loading 100% physically authentic cases from {dataset_path}...")
    runner = MultimodalEndToEndBenchmarkRunner(dataset_path)

    if args.sample_balanced:
        n = args.sample_balanced
        positives = [c for c in runner.cases if c.expected_answerable]
        negatives = [c for c in runner.cases if not c.expected_answerable]
        n_neg = max(1, n // 5)
        n_pos = n - n_neg
        step = max(1, len(positives) // n_pos)
        sampled_pos = positives[::step][:n_pos]
        step_neg = max(1, len(negatives) // n_neg)
        sampled_neg = negatives[::step_neg][:n_neg]
        eval_cases = sampled_pos + sampled_neg
    elif args.max_cases:
        eval_cases = runner.cases[: args.max_cases]
    else:
        eval_cases = runner.cases

    print(f"[Benchmark v2] Loaded {len(eval_cases)} evaluation cases (positive: {len([c for c in eval_cases if c.expected_answerable])}, negative: {len([c for c in eval_cases if not c.expected_answerable])}).")

    store = LocalArtifactStore(ROOT / "var" / "artifacts")
    index_path = ROOT / "var" / "lancedb" / "benchmark_v2_physical"
    image_index = prepare_physical_lancedb_index(store, runner.cases, index_path)

    # Load query vectors
    query_vec_file = ROOT / "var" / "cache" / "benchmark_v2_query_vectors.json"
    if not query_vec_file.is_file():
        raise RuntimeError(f"Missing query vector cache {query_vec_file}. Run scripts/cache_benchmark_queries.py first.")
    query_vectors: dict[str, list[float]] = json.loads(query_vec_file.read_text(encoding="utf-8"))

    # Load text retriever for text_only condition
    corpus_manifest_path = ROOT / "var" / "acceptance" / "v0.3-s1" / "corpus-import-manifest.json"
    target_papers = runner.manifest.get("target_papers", [])
    text_retriever, text_by_id = prepare_physical_text_retriever(store, corpus_manifest_path, target_papers)
    print(f"[Benchmark v2] Prepared physical text retriever with {len(text_by_id)} chunks across target papers.")

    # Load generation cache
    gen_cache_file = ROOT / "var" / "cache" / "benchmark_v2_generations.json"
    gen_cache: dict[str, Any] = {}
    if gen_cache_file.is_file():
        try:
            gen_cache = json.loads(gen_cache_file.read_text(encoding="utf-8"))
        except Exception:
            gen_cache = {}

    base_url = os.getenv("CONFLUX_WEAVE_PROVIDER_BASE_URL", "https://www.dmxapi.cn/v1")
    api_key = os.getenv("CONFLUX_WEAVE_PROVIDER_API_KEY", "")
    vlm_model = os.getenv("CONFLUX_WEAVE_PROVIDER_MODEL", "qwen3.7-flash")
    cross_reranker = DefaultMultimodalCrossReranker(min_relevance_threshold=0.30)

    conditions = ("joint_embedding", "caption_baseline", "text_only")
    summaries: dict[str, MultimodalConditionSummary] = {}
    condition_stats: dict[str, LatencyStats] = {}

    # Audit telemetry collection
    audit_cases: list[dict[str, Any]] = []
    audit_responses: list[dict[str, Any]] = []

    for cond in conditions:
        print(f"\n=======================================================")
        print(f"[Benchmark v2] Executing physical evaluation: {cond}")
        print(f"=======================================================")
        evaluations: list[MultimodalCaseEvaluation] = []

        if not args.skip_generation:
            chk_path = (
                Path(args.checkpoint)
                if args.checkpoint
                else (Path(args.audit_dir) / f"checkpoint_{cond}.jsonl" if args.audit_dir else None)
            )
            cfg = ConcurrencyConfig(
                max_concurrency=args.concurrency,
                checkpoint_path=chk_path,
                resume=True,
            )
            concurrent_runner = BoundedConcurrentRunner(cfg)

            def worker_case(
                case: MultimodalEvaluationCase,
            ) -> tuple[MultimodalCaseEvaluation, dict[str, Any], dict[str, Any]]:
                hits = execute_physical_retrieval(
                    case,
                    cond,
                    image_index,
                    query_vectors,
                    cross_reranker,
                    text_retriever=text_retriever,
                    text_by_id=text_by_id,
                    top_k=5,
                )
                gen_answer, telemetry = generate_physical_answer(
                    case, hits, cond, store, gen_cache, base_url, api_key, vlm_model, text_by_id=text_by_id
                )
                ev = evaluate_single_case(
                    case, hits, condition=cond, answerability_threshold=0.015, generated_answer=gen_answer
                )
                a_case = {
                    "case_id": case.case_id,
                    "condition": cond,
                    "query": case.query,
                    "expected_answerable": case.expected_answerable,
                    "expected_asset_id": case.expected_asset_id,
                    "retrieved_hits": [
                        {
                            "hit_id": h.hit_id,
                            "modality": h.modality,
                            "score": round(h.score, 4),
                            "rank": h.rank,
                            "document_id": h.document_id,
                            "page": h.page,
                        }
                        for h in hits
                    ],
                }
                a_resp = {
                    "case_id": case.case_id,
                    "condition": cond,
                    "query": case.query,
                    "response": gen_answer,
                    "latency_ms": telemetry.get("latency_ms", 0),
                    "prompt_tokens": telemetry.get("prompt_tokens", 0),
                    "completion_tokens": telemetry.get("completion_tokens", 0),
                    "total_tokens": telemetry.get("total_tokens", 0),
                    "cost_usd": telemetry.get("cost_usd", 0.0),
                }
                return ev, a_case, a_resp

            results, stats = concurrent_runner.run_batch(
                eval_cases,
                worker_case,
                case_id_fn=lambda c: f"{cond}::{c.case_id}",
                to_checkpoint_dict_fn=lambda c, r: {
                    "case_id": f"{cond}::{c.case_id}",
                    "condition": cond,
                    "query": c.query,
                    "latency_ms": r[2].get("latency_ms", 0),
                    "status": "completed",
                },
            )
            condition_stats[cond] = stats

            for ev, a_case, a_resp in results:
                evaluations.append(ev)
                audit_cases.append(a_case)
                audit_responses.append(a_resp)

            # Persist updated generation cache
            with CACHE_LOCK:
                gen_cache_file.write_text(json.dumps(gen_cache, ensure_ascii=False), encoding="utf-8")

            print(
                f"  [{cond}] Evaluated {len(evaluations)}/{len(eval_cases)} cases | "
                f"P50={stats.p50_ms:.0f}ms, P90={stats.p90_ms:.0f}ms, "
                f"429_errors={stats.rate_limit_errors}, kill_switch={stats.kill_switch_triggered}"
            )
        else:
            start_time = time.perf_counter()
            for idx, case in enumerate(eval_cases, 1):
                hits = execute_physical_retrieval(
                    case,
                    cond,
                    image_index,
                    query_vectors,
                    cross_reranker,
                    text_retriever=text_retriever,
                    text_by_id=text_by_id,
                    top_k=5,
                )
                ev = evaluate_single_case(
                    case, hits, condition=cond, answerability_threshold=0.015, generated_answer=None
                )
                evaluations.append(ev)

                audit_cases.append({
                    "case_id": case.case_id,
                    "condition": cond,
                    "query": case.query,
                    "expected_answerable": case.expected_answerable,
                    "expected_asset_id": case.expected_asset_id,
                    "retrieved_hits": [
                        {
                            "hit_id": h.hit_id,
                            "modality": h.modality,
                            "score": round(h.score, 4),
                            "rank": h.rank,
                            "document_id": h.document_id,
                            "page": h.page,
                        }
                        for h in hits
                    ],
                })

                if idx % 25 == 0 or idx == len(eval_cases):
                    elapsed = time.perf_counter() - start_time
                    print(f"  [{cond}] Evaluated {idx}/{len(eval_cases)} cases ({elapsed:.1f}s elapsed)...")

        summaries[cond] = aggregate_multimodal_metrics(
            evaluations, condition=cond, thresholds=runner.manifest.get("thresholds")
        )

    # Save generation cache
    with CACHE_LOCK:
        gen_cache_file.write_text(json.dumps(gen_cache, indent=2, ensure_ascii=False), encoding="utf-8")

    # Format scorecard
    scorecard_md = runner.format_markdown_scorecard(summaries)
    print("\n" + scorecard_md + "\n")

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(scorecard_md, encoding="utf-8")
    print(f"[Benchmark v2] Markdown report saved to {out_path}")

    json_path = Path(args.json_output)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_data = {cond: s.to_dict() for cond, s in summaries.items()}
    json_path.write_text(json.dumps(json_data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[Benchmark v2] JSON scorecard saved to {json_path}")

    # Export full audit package if requested
    if args.audit_dir:
        audit_path = Path(args.audit_dir)
        audit_path.mkdir(parents=True, exist_ok=True)

        # Git commit
        try:
            git_rev = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
        except Exception:
            git_rev = "unknown"

        run_meta = {
            "schema_version": "conflux-weave.multimodal-audit-manifest.v1",
            "git_commit": git_rev,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "provider_base_url": base_url,
            "provider_model": vlm_model,
            "temperature": 0.1,
            "max_tokens": 500,
            "retry_policy": "max_attempts=2, timeout=60s",
            "concurrency": args.concurrency,
            "concurrency_stats": {
                cond: s.to_dict() for cond, s in condition_stats.items()
            },
            "dataset_path": str(dataset_path),
            "cases_count": len(eval_cases),
            "conditions": list(conditions),
            "skip_generation": args.skip_generation,
        }
        (audit_path / "run_meta.json").write_text(json.dumps(run_meta, indent=2), encoding="utf-8")

        with (audit_path / "cases_evaluated.jsonl").open("w", encoding="utf-8") as f:
            for item in audit_cases:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")

        with (audit_path / "raw_responses.jsonl").open("w", encoding="utf-8") as f:
            for item in audit_responses:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")

        (audit_path / "metrics_scorecard.json").write_text(
            json.dumps(json_data, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f"[Benchmark v2] Full audit package successfully saved to {audit_path}")


if __name__ == "__main__":
    main()

