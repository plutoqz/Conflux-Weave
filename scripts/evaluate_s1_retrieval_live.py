from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from conflux_weave.hybrid_retrieval import HybridRetrievalPipeline
from conflux_weave.indexing import LanceDBDenseIndex, load_chunks
from conflux_weave.provider import ProviderConfig, OpenAICompatibleEmbeddingAdapter, OpenAICompatibleRerankerAdapter
from conflux_weave.retrieval_evaluation import aggregate_paper_metrics, paper_rank, predicts_answer
from conflux_weave.runtime import LocalArtifactStore


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=Path("datasets/regression/s1-real-paper-retrieval-v1"))
    parser.add_argument("--import-manifest", type=Path, default=Path("var/acceptance/v0.3-s1/corpus-import-manifest.json"))
    parser.add_argument("--artifact-root", type=Path, default=Path("var/artifacts/sha256"))
    parser.add_argument("--lancedb", type=Path, default=Path("var/acceptance/v0.3-s1/lancedb"))
    parser.add_argument("--dotenv", type=Path, default=Path(".env"))
    parser.add_argument("--output", type=Path, default=Path("var/acceptance/v0.3-s1/retrieval-evaluation-live.json"))
    parser.add_argument("--no-answer-threshold", type=float, default=0.5)
    args = parser.parse_args()
    cases = [json.loads(line) for line in (args.dataset / "cases.jsonl").read_text(encoding="utf-8").splitlines() if line]
    store = LocalArtifactStore(args.artifact_root); config = ProviderConfig.from_environment(args.dotenv); documents = load_chunks(args.import_manifest, store)
    pipeline = HybridRetrievalPipeline(documents, LanceDBDenseIndex(args.lancedb, table_name="paper_chunks"), OpenAICompatibleEmbeddingAdapter(store, config), OpenAICompatibleRerankerAdapter(store, config))
    records = []
    for case in cases:
        run = pipeline.search(case["query"])
        expected = frozenset(case["expected_source_snapshot_ids"])
        records.append({"case_id": case["case_id"], "expected_answerable": case["expected_answerable"], "ranks": {name: paper_rank(getattr(run, name), expected, k=10) if expected else None for name in ("bm25", "dense", "hybrid", "final")}, "top_rerank_score": run.final.hits[0].score if run.final.hits else None, "predicted_answerable": predicts_answer(run.final, threshold=args.no_answer_threshold), "rerank_status": run.rerank_status, "artifacts": {"embedding_response": run.embedding_response_artifact, "rerank_response": run.rerank_response_artifact}})
    positives = [record for record in records if record["expected_answerable"]]
    metrics = {stage: asdict(aggregate_paper_metrics([record["ranks"][stage] for record in positives], k=10)) for stage in ("bm25", "dense", "hybrid", "final")}
    no_answer_accuracy = sum(record["predicted_answerable"] == record["expected_answerable"] for record in records) / len(records)
    payload = {"schema_version": "conflux-weave.s1-retrieval-evaluation-live.v1", "dataset_manifest": str(args.dataset / "manifest.json"), "source_revision": _revision(), "no_answer_threshold": args.no_answer_threshold, "metrics": metrics, "answerability_accuracy": no_answer_accuracy, "cases": records, "evidence_boundary": "Real Provider and corpus run. Positive labels identify one target paper per query and are not exhaustive relevance judgments."}
    args.output.parent.mkdir(parents=True, exist_ok=True); args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"); print(json.dumps({"metrics": metrics, "answerability_accuracy": no_answer_accuracy}, ensure_ascii=False, indent=2))


def _revision() -> str:
    import subprocess
    return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()


if __name__ == "__main__": main()
