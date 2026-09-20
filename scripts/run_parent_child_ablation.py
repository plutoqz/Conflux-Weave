"""Phase 3 Topic 2 Ablation Runner: Parent-Child Hierarchical Chunking Ablation.

Single-Variable Controlled Experiment:
- Evaluates 10 academic literature questions requiring formula derivation and contextual reasoning.
- Compares:
  1. Flat Single-Layer Chunking: Injects top-3 raw atomic text chunks (~400 chars each).
  2. Parent-Child Hierarchical Chunking: Injects expanded section-level parent contexts (~1200 chars).
- Measures:
  - Factuality score against ground-truth key facts.
  - Prompt tokens, completion tokens, and cost.
  - Token overhead percentage.
- Tests Hypothesis:
  Parent-child chunking relatively improves factuality on academic derivations by >= 10%.
- Evaluates Kill-Switch:
  If Token overhead > 50% and factuality improvement < 3%, retain flat chunking.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from conflux_weave.hierarchical_chunking import (
    build_parent_child_hierarchy,
    resolve_parent_passages,
)
from conflux_weave.indexing import load_chunks
from conflux_weave.multimodal_evaluation import (
    MultimodalEndToEndBenchmarkRunner,
    MultimodalEvaluationCase,
)
from conflux_weave.multimodal_faithfulness import evaluate_visual_faithfulness
from conflux_weave.retrieval import BM25Retriever, RetrievalDocument
from conflux_weave.runtime.artifacts import LocalArtifactStore


def generate_llm_answer(
    query: str,
    passages: list[str],
    base_url: str,
    api_key: str,
    model: str,
) -> tuple[str, dict[str, Any]]:
    """Generate answer from retrieved passages using VLM/LLM."""
    formatted_passages = "\n\n".join(
        f"Academic Literature Context [{idx}]:\n{p.strip()}"
        for idx, p in enumerate(passages, 1)
    )

    system_prompt = (
        "You are an expert academic researcher. Answer the question strictly grounded in the provided academic literature contexts.\n"
        "Be mathematically and factually precise, cite [1], [2] where appropriate, and do not hallucinate beyond the provided text."
    )
    user_prompt = f"{formatted_passages}\n\nQuestion: {query}\n\nPlease provide a comprehensive, factually accurate answer citing the contexts."

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": 500,
        "temperature": 0.1,
    }

    req = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )

    t0 = time.perf_counter()
    answer = ""
    telemetry = {"latency_ms": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            answer = data["choices"][0]["message"]["content"].strip()
            usage = data.get("usage", {})
            telemetry["prompt_tokens"] = usage.get("prompt_tokens", 0)
            telemetry["completion_tokens"] = usage.get("completion_tokens", 0)
            telemetry["total_tokens"] = usage.get("total_tokens", 0)
    except Exception as exc:
        print(f"[Generation Error]: {exc}")
        answer = "Error generating response from provider."

    telemetry["latency_ms"] = int((time.perf_counter() - t0) * 1000)
    return answer, telemetry


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Topic 2 Parent-Child Chunking Ablation")
    parser.add_argument(
        "--output-dir",
        type=str,
        default=str(ROOT / "var" / "evaluations"),
        help="Directory to save report and scorecard",
    )
    parser.add_argument(
        "--num-cases",
        type=int,
        default=10,
        help="Number of academic cases to evaluate (default: 10)",
    )
    args = parser.parse_args()

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    base_url = os.getenv("CONFLUX_WEAVE_PROVIDER_BASE_URL", "https://www.dmxapi.cn/v1")
    api_key = os.getenv("CONFLUX_WEAVE_PROVIDER_API_KEY", "")
    model = os.getenv("CONFLUX_WEAVE_PROVIDER_MODEL", "qwen3.7-flash")

    # 1. Load documents and build parent-child hierarchy
    store = LocalArtifactStore(ROOT / "var" / "artifacts")
    manifest_path = ROOT / "var" / "acceptance" / "v0.3-s1" / "corpus-import-manifest.json"
    raw_docs = load_chunks(manifest_path, store)

    parents, children, child_to_parent_map = build_parent_child_hierarchy(
        raw_docs,
        child_chunk_words=70,
        child_overlap_words=20,
    )
    print(f"[Hierarchy] Built {len(parents)} parent macro-chunks and {len(children)} child chunks from {len(raw_docs)} source segments.")

    # Convert children to RetrievalDocuments for fine-grained retrieval
    child_retrieval_docs = [
        RetrievalDocument(
            document_id=c.child_id,
            text=c.text,
            source_snapshot_id=c.document_id,
            locator={"page": c.page},
        )
        for c in children
    ]

    retriever_flat = BM25Retriever(raw_docs)
    retriever_child = BM25Retriever(child_retrieval_docs)
    doc_text_map = {d.document_id: d.text for d in raw_docs}

    # 2. Sample 10 academic cases from benchmark v2
    data_dir = ROOT / "datasets" / "regression" / "p2-multimodal-benchmark-v2"
    runner = MultimodalEndToEndBenchmarkRunner(data_dir)
    positives = [
        c for c in runner.cases
        if c.expected_answerable and c.generation_ground_truth and c.generation_ground_truth.get("key_facts")
    ]
    sample_step = max(1, len(positives) // args.num_cases)
    test_cases = positives[::sample_step][: args.num_cases]

    print(f"[Ablation] Evaluating {len(test_cases)} academic cases across Flat vs Parent-Child conditions...")

    flat_results: list[dict[str, Any]] = []
    parent_results: list[dict[str, Any]] = []

    for idx, case in enumerate(test_cases, 1):
        print(f"  Evaluating Case {idx}/{len(test_cases)} ({case.case_id})...")

        # --- Condition A: Flat Single-Layer Retrieval & Generation ---
        flat_res = retriever_flat.search(case.query, top_k=3)
        flat_passages = [doc_text_map[h.document_id][:500] for h in flat_res.hits if h.document_id in doc_text_map]
        ans_flat, tel_flat = generate_llm_answer(case.query, flat_passages, base_url, api_key, model)

        faith_flat = evaluate_visual_faithfulness(
            case_id=case.case_id,
            answer=ans_flat,
            generation_ground_truth=case.generation_ground_truth,
            expected_answerable=case.expected_answerable,
        )

        flat_results.append({
            "case_id": case.case_id,
            "query": case.query,
            "answer": ans_flat,
            "prompt_tokens": tel_flat["prompt_tokens"],
            "completion_tokens": tel_flat["completion_tokens"],
            "total_tokens": tel_flat["total_tokens"],
            "factuality_score": faith_flat.factuality_score,
            "key_facts_found": [fc.expected_fact for fc in faith_flat.fact_checks if fc.is_supported],
            "key_facts_missing": [fc.expected_fact for fc in faith_flat.fact_checks if not fc.is_supported],
            "latency_ms": tel_flat["latency_ms"],
        })

        # --- Condition B: Parent-Child Hierarchical Retrieval & Generation ---
        child_res = retriever_child.search(case.query, top_k=5)
        parent_passages = resolve_parent_passages(
            [h.document_id for h in child_res.hits],
            child_to_parent_map,
            max_parents=3,
        )
        ans_parent, tel_parent = generate_llm_answer(case.query, parent_passages, base_url, api_key, model)

        faith_parent = evaluate_visual_faithfulness(
            case_id=case.case_id,
            answer=ans_parent,
            generation_ground_truth=case.generation_ground_truth,
            expected_answerable=case.expected_answerable,
        )

        parent_results.append({
            "case_id": case.case_id,
            "query": case.query,
            "answer": ans_parent,
            "prompt_tokens": tel_parent["prompt_tokens"],
            "completion_tokens": tel_parent["completion_tokens"],
            "total_tokens": tel_parent["total_tokens"],
            "factuality_score": faith_parent.factuality_score,
            "key_facts_found": [fc.expected_fact for fc in faith_parent.fact_checks if fc.is_supported],
            "key_facts_missing": [fc.expected_fact for fc in faith_parent.fact_checks if not fc.is_supported],
            "latency_ms": tel_parent["latency_ms"],
        })

    # 3. Aggregate Metrics & Comparisons
    n = len(test_cases)
    mean_fact_flat = sum(r["factuality_score"] for r in flat_results) / n
    mean_fact_parent = sum(r["factuality_score"] for r in parent_results) / n

    mean_prompt_flat = sum(r["prompt_tokens"] for r in flat_results) / n
    mean_prompt_parent = sum(r["prompt_tokens"] for r in parent_results) / n

    mean_total_flat = sum(r["total_tokens"] for r in flat_results) / n
    mean_total_parent = sum(r["total_tokens"] for r in parent_results) / n

    mean_lat_flat = sum(r["latency_ms"] for r in flat_results) / n
    mean_lat_parent = sum(r["latency_ms"] for r in parent_results) / n

    # Deltas
    token_overhead_pct = ((mean_prompt_parent - mean_prompt_flat) / max(1, mean_prompt_flat)) * 100.0
    abs_factuality_delta = mean_fact_parent - mean_fact_flat
    rel_factuality_improvement_pct = (abs_factuality_delta / max(0.01, mean_fact_flat)) * 100.0

    # Hypothesis: Relative Factuality Improvement >= 10%
    hypothesis_supported = rel_factuality_improvement_pct >= 10.0

    # Kill Switch: Token overhead > 50% AND factuality improvement < 3%
    kill_switch_triggered = (token_overhead_pct > 50.0) and (abs_factuality_delta < 0.03)

    verdict_text = (
        "KILL-SWITCH TRIGGERED (Token 开销增加 > 50% 且事实性无显著提升，放弃回溯分级，保持单层扁平架构)"
        if kill_switch_triggered
        else ("HYPOTHESIS CONFIRMED (父子分级切片使长篇学术推导事实忠实度相对提升 >= 10%)" if hypothesis_supported
              else "KILL-SWITCH AVOIDED (未触发熔断，但事实性相对提升未达 10% 假设)")
    )

    # 4. Generate Markdown Scorecard
    md_lines = [
        "# 阶段三消融课题 2：父子分级切片架构 (Parent-Child) 独立消融报告",
        "",
        f"- **实验时间**：{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}",
        "- **单一变量**：保持检索模型（BM25）与生成模型（qwen3.7-flash）完全一致，对比【单层扁平切片】vs【父子分级切片】",
        f"- **测试样本**：{n} 例真实学术论文关键推导与长篇推理用例",
        "",
        "## 1. 核心消融对比总览",
        "",
        "| 切片架构策略 | 事实忠实度 (Factuality) | 平均 Prompt Tokens | 平均 Total Tokens | Token 开销增幅 | 平均生成耗时 (ms) |",
        "| :--- | :---: | :---: | :---: | :---: | :---: |",
        f"| **`Flat Single-Layer (单层扁平)`** | {mean_fact_flat:.2f} | {mean_prompt_flat:.0f} | {mean_total_flat:.0f} | 基准 (0.0%) | {mean_lat_flat:.0f} |",
        f"| **`Parent-Child (父子分级)`** | {mean_fact_parent:.2f} | {mean_prompt_parent:.0f} | {mean_total_parent:.0f} | {token_overhead_pct:+.1f}% | {mean_lat_parent:.0f} |",
        f"| **差异增益 (Delta)** | **{abs_factuality_delta:+.2f} ({rel_factuality_improvement_pct:+.1f}%)** | **{mean_prompt_parent - mean_prompt_flat:+.0f}** | **{mean_total_parent - mean_total_flat:+.0f}** | **{token_overhead_pct:+.1f}%** | **{mean_lat_parent - mean_lat_flat:+.0f}** |",
        "",
        "## 2. 假设检验与退出熔断裁决 (Kill Switch Evaluation)",
        "",
        f"- **可证伪假设**：父子切片使长篇学术推导的事实忠实度相对提升 $\\ge 10\\%$；",
        f"  - 实测相对事实性提升：`{rel_factuality_improvement_pct:+.1f}%`（绝对增益 `{abs_factuality_delta:+.2f}`）",
        f"  - 假设判定：**{'SUPPORTED (成立)' if hypothesis_supported else 'NOT MET (未达10%相对提升)'}**",
        f"- **Kill Switch 退出条件**：若 Token 开销增加 $> 50\\%$ 且事实性无显著提升（绝对提升 $< 3\\%$），放弃回溯分级；",
        f"  - 实测 Prompt Token 增幅：`{token_overhead_pct:+.1f}%`",
        f"  - 实测事实性绝对提升：`{abs_factuality_delta:+.1%}`",
        f"  - 熔断判定：**{'TRIGGERED (触发退出熔断)' if kill_switch_triggered else 'PASSED (未触发熔断)'}**",
        "",
        f"### 最终裁决：**{verdict_text}**",
        "",
    ]

    report_md = "\n".join(md_lines)
    print("\n" + report_md)

    report_file = out_dir / "parent_child_ablation_report.md"
    report_file.write_text(report_md, encoding="utf-8")
    print(f"[Ablation] Markdown report saved to {report_file}")

    json_payload = {
        "schema_version": "conflux-weave.parent-child-ablation-report.v1",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "hypothesis": "Parent-child chunking relatively improves factuality on academic derivations by >= 10%",
        "hypothesis_supported": hypothesis_supported,
        "kill_switch_triggered": kill_switch_triggered,
        "verdict": verdict_text,
        "metrics": {
            "flat": {
                "mean_factuality": round(mean_fact_flat, 4),
                "mean_prompt_tokens": round(mean_prompt_flat, 1),
                "mean_total_tokens": round(mean_total_flat, 1),
                "mean_latency_ms": round(mean_lat_flat, 1),
            },
            "parent_child": {
                "mean_factuality": round(mean_fact_parent, 4),
                "mean_prompt_tokens": round(mean_prompt_parent, 1),
                "mean_total_tokens": round(mean_total_parent, 1),
                "mean_latency_ms": round(mean_lat_parent, 1),
            },
            "deltas": {
                "factuality_absolute": round(abs_factuality_delta, 4),
                "factuality_relative_pct": round(rel_factuality_improvement_pct, 2),
                "token_overhead_pct": round(token_overhead_pct, 2),
                "latency_delta_ms": round(mean_lat_parent - mean_lat_flat, 1),
            },
        },
        "flat_cases": flat_results,
        "parent_cases": parent_results,
    }

    json_file = out_dir / "parent_child_ablation_report.json"
    json_file.write_text(json.dumps(json_payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[Ablation] JSON report saved to {json_file}")


if __name__ == "__main__":
    main()
