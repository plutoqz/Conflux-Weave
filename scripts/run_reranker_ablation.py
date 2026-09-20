"""Phase 3 Topic 3 Ablation Runner: BGE-Reranker Cross-Modal Association Ablation.

Controlled Single-Variable Experiment:
- Evaluates 20 Near-Domain Negatives (near_domain_negatives_manifest.json)
  and 20 Hard Positive Cases (hard_subset_manifest.json).
- Candidate generation (RRF fusion) is 100% frozen between conditions.
- Compares:
  1. Condition A (Heuristic Baseline): DefaultMultimodalCrossReranker
  2. Condition B (Neural Cross-Encoder): BgeMultimodalCrossReranker (bge-reranker-v2-m3-free)
- Tests Hypothesis:
  Cross-Encoder improves negative interception rate by >= 20% on near-domain distractors.
- Evaluates Kill-Switch:
  If Latency increase > 500ms and FPR improvement < 5%, retain lightweight rules.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from conflux_weave.multimodal_indexing import LanceDBImageIndex, MultimodalRetrievalHit
from conflux_weave.multimodal_reranker_bge import BgeMultimodalCrossReranker
from conflux_weave.multimodal_retrieval import (
    DefaultMultimodalCrossReranker,
    MultimodalCrossReranker,
    intra_modal_image_rrf,
)
from conflux_weave.runtime.artifacts import LocalArtifactStore


def compute_ndcg_at_k(ranked_asset_ids: list[str], target_asset_id: str, k: int = 5) -> float:
    """Compute nDCG@K for single relevant target item."""
    for rank, aid in enumerate(ranked_asset_ids[:k], 1):
        if aid == target_asset_id:
            return 1.0 / math.log2(rank + 1)
    return 0.0


def evaluate_reranker_on_subsets(
    reranker: MultimodalCrossReranker,
    reranker_name: str,
    hard_cases: list[dict[str, Any]],
    negative_cases: list[dict[str, Any]],
    image_index: LanceDBImageIndex,
    query_vectors: dict[str, list[float]],
    top_k: int = 5,
) -> dict[str, Any]:
    """Execute evaluation for one reranker across both subsets with strict latency tracking."""
    latencies_ms: list[float] = []

    # 1. Evaluate 20 Near-Domain Negatives
    neg_results: list[dict[str, Any]] = []
    intercepted_count = 0

    for c in negative_cases:
        query = c["query"]
        qvec = query_vectors.get(query)
        if qvec is None:
            raise RuntimeError(f"Missing query vector for negative query: {query}")

        # Frozen candidate generation
        vec_hits = list(image_index.search_vector(qvec, top_k=top_k * 2))
        cap_hits = list(image_index.search_caption_text(query, top_k=top_k * 2))
        fused = intra_modal_image_rrf(vec_hits, cap_hits, k=60, top_k=top_k * 2)
        candidates = list(fused) + vec_hits + cap_hits

        # Rerank with latency measurement
        t0 = time.perf_counter()
        reranked = reranker.rerank(query, candidates, top_k=top_k)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        latencies_ms.append(elapsed_ms)

        # An unanswerable query is successfully intercepted if 0 hits are returned
        is_intercepted = len(reranked) == 0
        if is_intercepted:
            intercepted_count += 1

        neg_results.append({
            "case_id": c["case_id"],
            "query": query,
            "distractor_concept": c.get("distractor_concept", ""),
            "returned_hits_count": len(reranked),
            "is_intercepted": is_intercepted,
            "top_hit_score": round(reranked[0].score, 4) if reranked else None,
            "latency_ms": round(elapsed_ms, 2),
        })

    # 2. Evaluate 20 Hard Positive Cases
    pos_results: list[dict[str, Any]] = []
    recall_at_1_count = 0
    recall_at_3_count = 0
    recall_at_5_count = 0
    rr_sum = 0.0
    ndcg_sum = 0.0

    for c in hard_cases:
        query = c["query"]
        target_asset_id = c["expected_asset_id"]
        qvec = query_vectors.get(query)
        if qvec is None:
            raise RuntimeError(f"Missing query vector for hard query: {query}")

        vec_hits = list(image_index.search_vector(qvec, top_k=top_k * 2))
        cap_hits = list(image_index.search_caption_text(query, top_k=top_k * 2))
        fused = intra_modal_image_rrf(vec_hits, cap_hits, k=60, top_k=top_k * 2)
        candidates = list(fused) + vec_hits + cap_hits

        t0 = time.perf_counter()
        reranked = reranker.rerank(query, candidates, top_k=top_k)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        latencies_ms.append(elapsed_ms)

        ranked_ids = [h.asset_id for h in reranked]
        r1 = 1 if (ranked_ids and ranked_ids[0] == target_asset_id) else 0
        r3 = 1 if target_asset_id in ranked_ids[:3] else 0
        r5 = 1 if target_asset_id in ranked_ids[:5] else 0

        rr = 0.0
        for rank, aid in enumerate(ranked_ids[:top_k], 1):
            if aid == target_asset_id:
                rr = 1.0 / rank
                break

        ndcg = compute_ndcg_at_k(ranked_ids, target_asset_id, k=top_k)

        recall_at_1_count += r1
        recall_at_3_count += r3
        recall_at_5_count += r5
        rr_sum += rr
        ndcg_sum += ndcg

        pos_results.append({
            "case_id": c["case_id"],
            "query": query,
            "target_asset_id": target_asset_id,
            "found_rank": (ranked_ids.index(target_asset_id) + 1) if target_asset_id in ranked_ids else None,
            "recall_at_1": r1,
            "recall_at_5": r5,
            "rr": round(rr, 4),
            "ndcg": round(ndcg, 4),
            "latency_ms": round(elapsed_ms, 2),
        })

    # Percentiles
    sorted_latencies = sorted(latencies_ms)
    n = len(sorted_latencies)

    def pctl(p: float) -> float:
        idx = int(round((p / 100.0) * (n - 1)))
        return sorted_latencies[min(n - 1, max(0, idx))]

    num_neg = len(negative_cases)
    num_pos = len(hard_cases)

    interception_rate = intercepted_count / max(1, num_neg)
    fpr = 1.0 - interception_rate

    return {
        "reranker_name": reranker_name,
        "negative_metrics": {
            "total_cases": num_neg,
            "intercepted_count": intercepted_count,
            "false_positive_count": num_neg - intercepted_count,
            "interception_rate": round(interception_rate, 4),
            "false_positive_rate": round(fpr, 4),
        },
        "positive_metrics": {
            "total_cases": num_pos,
            "recall_at_1": round(recall_at_1_count / max(1, num_pos), 4),
            "recall_at_3": round(recall_at_3_count / max(1, num_pos), 4),
            "recall_at_5": round(recall_at_5_count / max(1, num_pos), 4),
            "mrr": round(rr_sum / max(1, num_pos), 4),
            "ndcg_at_5": round(ndcg_sum / max(1, num_pos), 4),
        },
        "latency_stats_ms": {
            "mean": round(sum(sorted_latencies) / n, 2),
            "p50": round(pctl(50.0), 2),
            "p90": round(pctl(90.0), 2),
            "p99": round(pctl(99.0), 2),
            "min": round(sorted_latencies[0], 2),
            "max": round(sorted_latencies[-1], 2),
        },
        "negative_case_details": neg_results,
        "positive_case_details": pos_results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Topic 3 BGE-Reranker Cross-Modal Ablation")
    parser.add_argument(
        "--output-dir",
        type=str,
        default=str(ROOT / "var" / "evaluations"),
        help="Directory to save report and scorecard",
    )
    args = parser.parse_args()

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1. Load manifests
    data_dir = ROOT / "datasets" / "regression" / "p2-multimodal-benchmark-v2"
    hard_manifest_file = data_dir / "hard_subset_manifest.json"
    neg_manifest_file = data_dir / "near_domain_negatives_manifest.json"

    if not hard_manifest_file.is_file() or not neg_manifest_file.is_file():
        raise FileNotFoundError("Missing frozen ablation manifests in dataset directory.")

    hard_cases = json.loads(hard_manifest_file.read_text(encoding="utf-8"))["cases"]
    neg_cases = json.loads(neg_manifest_file.read_text(encoding="utf-8"))["cases"]

    print(f"[Ablation] Loaded {len(hard_cases)} hard cases and {len(neg_cases)} near-domain negative cases.")

    # 2. Load physical index & query vectors
    store = LocalArtifactStore(ROOT / "var" / "artifacts")
    index_path = ROOT / "var" / "lancedb" / "benchmark_v2_physical"
    image_index = LanceDBImageIndex(index_path, table_name="benchmark_v2_images")

    qvec_path = ROOT / "var" / "cache" / "benchmark_v2_query_vectors.json"
    if not qvec_path.is_file():
        raise RuntimeError(f"Query vectors file {qvec_path} missing.")
    query_vectors = json.loads(qvec_path.read_text(encoding="utf-8"))

    # 3. Setup Rerankers
    reranker_heuristic = DefaultMultimodalCrossReranker(min_relevance_threshold=0.30)
    reranker_neural = BgeMultimodalCrossReranker(
        model="bge-reranker-v2-m3-free",
        min_relevance_threshold=0.10,
    )

    print("\n--- Evaluating Condition A: Heuristic Baseline (DefaultMultimodalCrossReranker) ---")
    res_heuristic = evaluate_reranker_on_subsets(
        reranker=reranker_heuristic,
        reranker_name="heuristic_baseline",
        hard_cases=hard_cases,
        negative_cases=neg_cases,
        image_index=image_index,
        query_vectors=query_vectors,
    )
    print(f"  Heuristic Neg Interception: {res_heuristic['negative_metrics']['interception_rate']:.1%}, "
          f"Pos Recall@5: {res_heuristic['positive_metrics']['recall_at_5']:.2f}, "
          f"Mean Latency: {res_heuristic['latency_stats_ms']['mean']}ms")

    print("\n--- Evaluating Condition B: Neural Cross-Encoder (BgeMultimodalCrossReranker) ---")
    res_neural = evaluate_reranker_on_subsets(
        reranker=reranker_neural,
        reranker_name="neural_bge_m3",
        hard_cases=hard_cases,
        negative_cases=neg_cases,
        image_index=image_index,
        query_vectors=query_vectors,
    )
    print(f"  Neural Neg Interception: {res_neural['negative_metrics']['interception_rate']:.1%}, "
          f"Pos Recall@5: {res_neural['positive_metrics']['recall_at_5']:.2f}, "
          f"Mean Latency: {res_neural['latency_stats_ms']['mean']}ms")

    # 4. Deltas and Hypothesis Testing
    delta_interception = (
        res_neural["negative_metrics"]["interception_rate"]
        - res_heuristic["negative_metrics"]["interception_rate"]
    )
    delta_fpr = (
        res_heuristic["negative_metrics"]["false_positive_rate"]
        - res_neural["negative_metrics"]["false_positive_rate"]
    )
    delta_latency_mean = (
        res_neural["latency_stats_ms"]["mean"] - res_heuristic["latency_stats_ms"]["mean"]
    )
    delta_recall_5 = (
        res_neural["positive_metrics"]["recall_at_5"] - res_heuristic["positive_metrics"]["recall_at_5"]
    )
    delta_mrr = (
        res_neural["positive_metrics"]["mrr"] - res_heuristic["positive_metrics"]["mrr"]
    )

    # Hypothesis: Interception Rate improvement >= 20%
    hypothesis_supported = delta_interception >= 0.20

    # Kill Switch: If Latency increase > 500ms AND FPR improvement < 5%
    kill_switch_triggered = (delta_latency_mean > 500.0) and (delta_fpr < 0.05)

    verdict_text = (
        "KILL-SWITCH TRIGGERED (单 Query 延迟增加超过 500ms 且 FPR 改善小于 5%，放弃外部模型，保持轻量规则)"
        if kill_switch_triggered
        else ("HYPOTHESIS CONFIRMED (神经重排在近领域负样本上拦截率显著提升且未触发熔断)" if hypothesis_supported
              else "KILL-SWITCH AVOIDED (未触发熔断，但拦截率提升未达 20% 假设)")
    )

    # 5. Format Scorecard & Report
    md_lines = [
        "# 阶段三消融课题 3：BGE-Reranker 跨模态文本关联性独立消融报告",
        "",
        f"- **实验时间**：{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}",
        "- **单一变量**：粗排完全冻结，仅替换重排器（轻量启发式规则 vs BGE-Reranker-v2-m3 神经重排）",
        f"- **测试样本**：20 例已冻结近领域负样本 + 20 例已冻结表格/热图硬样本",
        "",
        "## 1. 核心消融对比总览",
        "",
        "| 重排器策略 | 近领域负样本拦截率 | 假阳率 (FPR) | 硬样本 Recall@1 | 硬样本 Recall@5 | 硬样本 MRR | nDCG@5 | 平均延迟 (ms) | P50 (ms) | P90 (ms) |",
        "| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |",
        f"| **`Heuristic Baseline`** | {res_heuristic['negative_metrics']['interception_rate']:.1%} | {res_heuristic['negative_metrics']['false_positive_rate']:.1%} | {res_heuristic['positive_metrics']['recall_at_1']:.2f} | {res_heuristic['positive_metrics']['recall_at_5']:.2f} | {res_heuristic['positive_metrics']['mrr']:.2f} | {res_heuristic['positive_metrics']['ndcg_at_5']:.2f} | {res_heuristic['latency_stats_ms']['mean']:.1f} | {res_heuristic['latency_stats_ms']['p50']:.1f} | {res_heuristic['latency_stats_ms']['p90']:.1f} |",
        f"| **`Neural BGE-Reranker`** | {res_neural['negative_metrics']['interception_rate']:.1%} | {res_neural['negative_metrics']['false_positive_rate']:.1%} | {res_neural['positive_metrics']['recall_at_1']:.2f} | {res_neural['positive_metrics']['recall_at_5']:.2f} | {res_neural['positive_metrics']['mrr']:.2f} | {res_neural['positive_metrics']['ndcg_at_5']:.2f} | {res_neural['latency_stats_ms']['mean']:.1f} | {res_neural['latency_stats_ms']['p50']:.1f} | {res_neural['latency_stats_ms']['p90']:.1f} |",
        f"| **差异增益 (Delta)** | **{delta_interception:+.1%}** | **{-delta_fpr:+.1%}** | **{res_neural['positive_metrics']['recall_at_1'] - res_heuristic['positive_metrics']['recall_at_1']:+.2f}** | **{delta_recall_5:+.2f}** | **{delta_mrr:+.2f}** | **{res_neural['positive_metrics']['ndcg_at_5'] - res_heuristic['positive_metrics']['ndcg_at_5']:+.2f}** | **{delta_latency_mean:+.1f}** | **{res_neural['latency_stats_ms']['p50'] - res_heuristic['latency_stats_ms']['p50']:+.1f}** | **{res_neural['latency_stats_ms']['p90'] - res_heuristic['latency_stats_ms']['p90']:+.1f}** |",
        "",
        "## 2. 假设检验与退出熔断裁决 (Kill Switch Evaluation)",
        "",
        f"- **可证伪假设**：Cross-Encoder 在 20 个近领域干扰负样本上的拦截率提升 $\\ge 20\\%$；",
        f"  - 实测拦截率提升：`{delta_interception:+.1%}`",
        f"  - 假设判定：**{'SUPPORTED (成立)' if hypothesis_supported else 'NOT MET (未达20%提升)'}**",
        f"- **Kill Switch 退出条件**：若单 Query 延迟增加 $> 500\\text{{ms}}$ 且近领域 FPR 改善 $< 5\\%$，则保持轻量规则；",
        f"  - 实测平均延迟增加：`{delta_latency_mean:+.1f} ms`",
        f"  - 实测 FPR 绝对改善：`{delta_fpr:+.1%}`",
        f"  - 熔断判定：**{'TRIGGERED (触发退出熔断)' if kill_switch_triggered else 'PASSED (未触发熔断)'}**",
        "",
        f"### 最终裁决：**{verdict_text}**",
        "",
    ]
    report_md = "\n".join(md_lines)
    print("\n" + report_md)

    # Save outputs
    report_file = out_dir / "reranker_ablation_report.md"
    report_file.write_text(report_md, encoding="utf-8")
    print(f"\n[Ablation] Markdown report saved to {report_file}")

    json_payload = {
        "schema_version": "conflux-weave.reranker-ablation-report.v1",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "hypothesis": "Cross-Encoder improves negative interception rate by >= 20% on near-domain distractors",
        "hypothesis_supported": hypothesis_supported,
        "kill_switch_triggered": kill_switch_triggered,
        "verdict": verdict_text,
        "delta": {
            "interception_rate": round(delta_interception, 4),
            "fpr_reduction": round(delta_fpr, 4),
            "recall_at_5": round(delta_recall_5, 4),
            "mrr": round(delta_mrr, 4),
            "latency_mean_ms": round(delta_latency_mean, 2),
        },
        "heuristic_baseline": res_heuristic,
        "neural_bge": res_neural,
    }
    json_file = out_dir / "reranker_ablation_report.json"
    json_file.write_text(json.dumps(json_payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[Ablation] JSON scorecard saved to {json_file}")


if __name__ == "__main__":
    main()
