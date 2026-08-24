"""Normalize the reviewed legacy 30-case RAG input into a W2 fixture.

This is a one-way dataset export. It requires PyYAML in the migration
environment but does not add PyYAML to the product runtime.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

import yaml


BOUNDARY_CASES = (
    {
        "case_id": "W2-BND-001",
        "origin": "w2_scope_freeze",
        "case_kind": "outcome",
        "split": "boundary",
        "language": "zh-zh",
        "query": "查找同时满足冻结硬约束、但当前语料没有覆盖的研究结果。",
        "relevant_source_ids": [],
        "must_contain": [],
        "source_status": "not_applicable",
        "expected_outcome": "no_answer",
        "expected_support_status": "unsupported_claim",
        "expected_source_trust": [],
        "annotation_status": "provisional",
    },
    {
        "case_id": "W2-BND-002",
        "origin": "w2_scope_freeze",
        "case_kind": "outcome",
        "split": "boundary",
        "language": "zh-zh",
        "query": "部分必需来源不可访问，但已有一个来源支持部分结论。",
        "relevant_source_ids": ["available-source"],
        "must_contain": [],
        "source_status": "partial",
        "expected_outcome": "partial",
        "expected_support_status": "partial_support",
        "expected_source_trust": ["general_source"],
        "annotation_status": "provisional",
    },
    {
        "case_id": "W2-BND-003",
        "origin": "w2_scope_freeze",
        "case_kind": "outcome",
        "split": "boundary",
        "language": "zh-zh",
        "query": "来源可以定位，但维护关系尚未独立核验。",
        "relevant_source_ids": ["unverified-source"],
        "must_contain": [],
        "source_status": "available",
        "expected_outcome": "partial",
        "expected_support_status": "cited",
        "expected_source_trust": ["unverified_source"],
        "annotation_status": "provisional",
    },
    {
        "case_id": "W2-BND-004",
        "origin": "w2_scope_freeze",
        "case_kind": "outcome",
        "split": "boundary",
        "language": "zh-zh",
        "query": "两个可定位来源对同一事实给出冲突结果。",
        "relevant_source_ids": ["source-a", "source-b"],
        "must_contain": [],
        "source_status": "available",
        "expected_outcome": "partial",
        "expected_support_status": "conflicting",
        "expected_source_trust": ["credible_secondary"],
        "annotation_status": "provisional",
    },
    {
        "case_id": "W2-BND-005",
        "origin": "w2_scope_freeze",
        "case_kind": "outcome",
        "split": "boundary",
        "language": "zh-zh",
        "query": "回答包含帮助理解的方法建议，但不声称来自当前来源。",
        "relevant_source_ids": [],
        "must_contain": [],
        "source_status": "not_applicable",
        "expected_outcome": "complete",
        "expected_support_status": "uncited_context",
        "expected_source_trust": [],
        "annotation_status": "provisional",
    },
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--documents", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    cases: list[dict[str, object]] = []
    source_terms: dict[str, set[str]] = defaultdict(set)
    source_paths: dict[str, Path | None] = {}
    source_hashes: dict[str, str | None] = {}
    source_file_hashes: dict[str, str] = {}

    for yaml_path in sorted(args.source.glob("*.yaml")):
        source_file_hashes[yaml_path.name] = hashlib.sha256(
            yaml_path.read_bytes()
        ).hexdigest()
        records = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
        for record in records:
            language = str(record["language"])
            legacy_id = str(record["id"])
            source_ids = tuple(str(value) for value in record["relevant_sources"])
            available = True
            for source_id in source_ids:
                matches = sorted(args.documents.rglob(source_id))
                source_path = matches[0] if matches else None
                source_paths[source_id] = source_path
                source_hashes[source_id] = (
                    hashlib.sha256(source_path.read_bytes()).hexdigest()
                    if source_path
                    else None
                )
                available = available and source_path is not None
                source_terms[source_id].update(str(value) for value in record["must_contain"])
            numeric_id = int(legacy_id.rsplit("_", 1)[1])
            cases.append(
                {
                    "case_id": f"W2-RAG-{language}-{numeric_id:03d}",
                    "origin": "legacy.rag_eval.multilingual_v1",
                    "case_kind": "retrieval",
                    "split": "held_out" if numeric_id in {9, 10} else "regression",
                    "language": language,
                    "query": str(record["query"]),
                    "relevant_source_ids": list(source_ids),
                    "must_contain": [str(value) for value in record["must_contain"]],
                    "source_status": "available" if available else "missing",
                    "expected_outcome": "retrievable" if available else "partial",
                    "expected_support_status": "cited" if available else "partial_support",
                    "expected_source_trust": ["general_source"],
                    "annotation_status": "provisional",
                }
            )

    cases.extend(BOUNDARY_CASES)
    corpus = [
        {
            "source_id": source_id,
            "text": " ".join([source_id, *sorted(source_terms[source_id])]),
            "fixture_kind": "legacy_annotation_terms",
            "legacy_source_path": (
                source_paths[source_id]
                .relative_to(args.documents.parents[1])
                .as_posix()
                if source_paths[source_id]
                else None
            ),
            "legacy_source_sha256": source_hashes[source_id],
            "source_available": source_paths[source_id] is not None,
        }
        for source_id in sorted(source_terms)
    ]

    write_jsonl(args.output / "cases.jsonl", cases)
    write_jsonl(args.output / "corpus.jsonl", corpus)
    (args.output / "schema.json").write_text(
        json.dumps(schema(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (args.output / "README.md").write_text(readme(), encoding="utf-8")
    file_hashes = {
        name: normalized_sha256(args.output / name)
        for name in ("README.md", "cases.jsonl", "corpus.jsonl", "schema.json")
    }
    retrieval = [case for case in cases if case["case_kind"] == "retrieval"]
    manifest = {
        "dataset_id": "w2-retrieval-evidence",
        "version": "1.0.0-provisional",
        "status": "provisional",
        "layer": "regression",
        "schema_version": "1.0.0",
        "created_at": "2026-08-24",
        "purpose": "W2.4 deterministic retrieval and evidence-delivery comparison.",
        "evidence_boundary": "The 30 legacy records are candidate annotations, not Weave answer-quality gold. corpus.jsonl is constructed from legacy source ids and must_contain terms and proves only deterministic ranking mechanics.",
        "case_count": len(cases),
        "retrieval_case_count": len(retrieval),
        "executable_retrieval_case_count": sum(case["source_status"] == "available" for case in retrieval),
        "boundary_case_count": len(BOUNDARY_CASES),
        "coverage": {
            "case_kind": dict(Counter(str(case["case_kind"]) for case in cases)),
            "split": dict(Counter(str(case["split"]) for case in cases)),
            "language": dict(Counter(str(case["language"]) for case in cases)),
            "source_status": dict(Counter(str(case["source_status"]) for case in cases)),
        },
        "source_lineage": {
            "asset_id": "legacy.rag_eval.multilingual_v1",
            "inventory_hash": "a9ca3f27953a4a35cb6febe866cd8c9305355a7a256539d5c0adadf4f5067f04",
            "source_file_hashes": source_file_hashes,
            "direct_import_allowed_at_w0": False,
            "migration_review": "schema normalized; source existence and hashes recorded; semantic labels still require independent human review",
        },
        "evaluation_admission": {
            "Feature": "W2 BM25 retrieval and evidence-delivery contracts",
            "Risk": "Chinese lexical tokenization, missing sources, or display changes may produce silent retrieval and evidence regressions.",
            "Question": "Does the W2 implementation improve deterministic regression and held-out retrieval while preserving explicit outcome and trust semantics?",
            "Metric": "Recall@3, MRR, outcome exact match, support exact match, trust exact match and inline-citation count.",
            "Decision": "Keep the smallest strategy that improves held-out cases; admit Dense/RRF/Ragas only when deterministic evidence cannot answer the product question.",
            "ActionOnFail": "Attribute failure to corpus, tokenization, retrieval, Evidence or rendering before changing strategy or adding a framework.",
            "StopCondition": "Metrics answer the current BM25 and delivery decision; further framework work cannot change it.",
        },
        "execution_authorization": {
            "provider_calls": False,
            "network_retrieval": False,
            "paid_evaluation": False,
            "live_acceptance": False,
        },
        "hash_policy": "sha256 over UTF-8 text normalized from CRLF or CR to LF",
        "file_hashes": file_hashes,
        "review": {
            "required": True,
            "reviewed_by": None,
            "reviewed_at": None,
            "decision": "awaiting_independent_human_review",
        },
    }
    (args.output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def write_jsonl(path: Path, records: list[dict[str, object]]) -> None:
    path.write_text(
        "".join(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n" for record in records),
        encoding="utf-8",
    )


def normalized_sha256(path: Path) -> str:
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def schema() -> dict[str, object]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "Conflux-Weave W2 retrieval/evidence case",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "case_id", "origin", "case_kind", "split", "language", "query",
            "relevant_source_ids", "must_contain", "source_status", "expected_outcome",
            "expected_support_status", "expected_source_trust", "annotation_status"
        ],
        "properties": {
            "case_id": {"type": "string", "pattern": "^W2-(RAG|BND)-"},
            "origin": {"type": "string"},
            "case_kind": {"enum": ["retrieval", "outcome"]},
            "split": {"enum": ["regression", "held_out", "boundary"]},
            "language": {"enum": ["en-en", "zh-en", "zh-zh"]},
            "query": {"type": "string", "minLength": 1},
            "relevant_source_ids": {"type": "array", "items": {"type": "string"}},
            "must_contain": {"type": "array", "items": {"type": "string"}},
            "source_status": {"enum": ["available", "missing", "partial", "not_applicable"]},
            "expected_outcome": {"enum": ["retrievable", "complete", "partial", "no_answer"]},
            "expected_support_status": {"enum": ["cited", "partial_support", "uncited_context", "conflicting", "unsupported_claim"]},
            "expected_source_trust": {"type": "array", "items": {"enum": ["authoritative", "credible_secondary", "general_source", "unverified_source"]}},
            "annotation_status": {"const": "provisional"},
        },
    }


def readme() -> str:
    return """# W2 retrieval and evidence cases v1.0.0-provisional

Status: `provisional`, awaiting independent human review.

This dataset normalizes all 30 records from the legacy multilingual RAG input
and adds five W2 outcome-boundary cases. It does not promote historical answers,
keywords, scores, or source documents to Weave evidence.

`corpus.jsonl` is a constructed deterministic fixture made only from legacy
source identifiers and `must_contain` annotations. It supports tokenizer and
ranking regression tests; it is not a real corpus and cannot prove answer quality.

Cases 009 and 010 in each language are held out from implementation decisions.
Three legacy English cases reference a missing source and are retained as
explicit partial/missing-source records rather than removed or scored.

The five boundary cases cover no-answer, partial evidence, unverified source,
conflicting evidence and uncited context. No network, Provider or paid evaluator
is authorized by this dataset.
"""


if __name__ == "__main__":
    main()
