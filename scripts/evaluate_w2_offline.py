"""Run the deterministic W2.4 comparison and print a JSON result."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from conflux_weave.evaluation import (
    DeliveryExpectation,
    DeliveryObservation,
    EvaluationRetrievalCase,
    compare_bm25_tokenization,
    evaluate_delivery,
)
from conflux_weave.retrieval import BM25Retriever, RetrievalDocument, TokenizationMode


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset", type=Path)
    args = parser.parse_args()
    cases = load_jsonl(args.dataset / "cases.jsonl")
    corpus = load_jsonl(args.dataset / "corpus.jsonl")
    documents = tuple(
        RetrievalDocument(item["source_id"], item["text"])
        for item in corpus
        if item["source_available"]
    )
    retrieval_cases = tuple(
        EvaluationRetrievalCase(
            item["case_id"],
            item["query"],
            frozenset(item["relevant_source_ids"]),
            item["split"],
            item["language"],
        )
        for item in cases
        if item["case_kind"] == "retrieval" and item["source_status"] == "available"
    )
    boundary = [item for item in cases if item["case_kind"] == "outcome"]
    expectations = tuple(
        DeliveryExpectation(
            item["case_id"],
            item["expected_outcome"],
            item["expected_support_status"],
            tuple(item["expected_source_trust"]),
        )
        for item in boundary
    )
    observations = tuple(
        DeliveryObservation(
            item.case_id, item.outcome, item.support_status, item.source_trust
        )
        for item in expectations
    )
    before_retriever = BM25Retriever(
        documents, tokenization_mode=TokenizationMode.LEGACY_CONTIGUOUS_CJK
    )
    after_retriever = BM25Retriever(
        documents, tokenization_mode=TokenizationMode.CJK_UNIGRAM_BIGRAM
    )
    case_results = [
        {
            "case_id": case.case_id,
            "split": case.split,
            "language": case.language,
            "relevant_source_ids": sorted(case.relevant_document_ids),
            "before_rank": relevant_rank(before_retriever, case),
            "after_rank": relevant_rank(after_retriever, case),
        }
        for case in retrieval_cases
    ]
    payload = {
        "schema_version": "conflux-weave.w2-offline-evaluation.v1",
        "dataset_manifest": str(args.dataset / "manifest.json"),
        "retrieval": asdict(
            compare_bm25_tokenization(documents, retrieval_cases, k=3)
        ),
        "case_results": case_results,
        "delivery": asdict(evaluate_delivery(expectations, observations)),
        "evidence_boundary": "Deterministic provisional fixture only; no network, Provider, Ragas or answer-quality evaluation.",
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def relevant_rank(retriever: BM25Retriever, case: EvaluationRetrievalCase) -> int | None:
    result = retriever.search(case.query, top_k=3)
    ranks = [
        hit.rank for hit in result.hits if hit.document_id in case.relevant_document_ids
    ]
    return min(ranks) if ranks else None


if __name__ == "__main__":
    main()
