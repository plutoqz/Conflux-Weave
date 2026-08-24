import hashlib
import json
from pathlib import Path

from conflux_weave.evaluation import (
    DeliveryExpectation,
    DeliveryObservation,
    EvaluationRetrievalCase,
    compare_bm25_tokenization,
    evaluate_delivery,
    evaluate_report_readability,
)
from conflux_weave.retrieval import RetrievalDocument


ROOT = Path(__file__).parents[1]
DATASET = ROOT / "datasets" / "regression" / "w2-retrieval-evidence-v1.0.0"


def load_jsonl(name: str) -> list[dict]:
    return [
        json.loads(line)
        for line in (DATASET / name).read_text(encoding="utf-8").splitlines()
        if line
    ]


def normalized_sha256(path: Path) -> str:
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(text.encode()).hexdigest()


def test_w2_manifest_preserves_all_legacy_cases_and_boundary_coverage() -> None:
    manifest = json.loads((DATASET / "manifest.json").read_text(encoding="utf-8"))
    cases = load_jsonl("cases.jsonl")

    assert manifest["status"] == "provisional"
    assert manifest["case_count"] == len(cases) == 35
    assert manifest["retrieval_case_count"] == 30
    assert manifest["executable_retrieval_case_count"] == 27
    assert manifest["coverage"]["split"] == {
        "regression": 24,
        "held_out": 6,
        "boundary": 5,
    }
    assert manifest["review"]["decision"] == "awaiting_independent_human_review"
    assert not any(manifest["execution_authorization"].values())
    assert {case["expected_support_status"] for case in cases if case["case_kind"] == "outcome"} == {
        "partial_support", "cited", "uncited_context", "conflicting", "unsupported_claim"
    }


def test_w2_manifest_hashes_are_current() -> None:
    manifest = json.loads((DATASET / "manifest.json").read_text(encoding="utf-8"))
    for name, digest in manifest["file_hashes"].items():
        assert normalized_sha256(DATASET / name) == digest


def test_w2_cases_match_declared_schema_keys_and_status() -> None:
    schema = json.loads((DATASET / "schema.json").read_text(encoding="utf-8"))
    allowed = set(schema["properties"])
    required = set(schema["required"])
    for case in load_jsonl("cases.jsonl"):
        assert set(case) == allowed
        assert required <= set(case)
        assert case["annotation_status"] == "provisional"


def test_cjk_tokenization_improves_regression_and_held_out_retrieval() -> None:
    cases = load_jsonl("cases.jsonl")
    corpus = load_jsonl("corpus.jsonl")
    documents = tuple(
        RetrievalDocument(item["source_id"], item["text"])
        for item in corpus
        if item["source_available"]
    )
    retrieval_cases = tuple(
        EvaluationRetrievalCase(
            item["case_id"], item["query"], frozenset(item["relevant_source_ids"]), item["split"], item["language"]
        )
        for item in cases
        if item["case_kind"] == "retrieval" and item["source_status"] == "available"
    )

    comparison = compare_bm25_tokenization(documents, retrieval_cases, k=3)

    assert comparison.after["overall"].recall_at_k >= comparison.before["overall"].recall_at_k
    assert comparison.after["split:held_out"].recall_at_k >= comparison.before["split:held_out"].recall_at_k
    assert comparison.after["language:zh-zh"].mean_reciprocal_rank > comparison.before["language:zh-zh"].mean_reciprocal_rank


def test_boundary_evaluator_keeps_outcome_support_and_trust_separate() -> None:
    boundary = [case for case in load_jsonl("cases.jsonl") if case["case_kind"] == "outcome"]
    expectations = tuple(
        DeliveryExpectation(
            case["case_id"], case["expected_outcome"], case["expected_support_status"], tuple(case["expected_source_trust"])
        )
        for case in boundary
    )
    observations = tuple(
        DeliveryObservation(item.case_id, item.outcome, item.support_status, item.source_trust)
        for item in expectations
    )

    metrics = evaluate_delivery(expectations, observations)

    assert metrics.case_count == 5
    assert metrics.outcome_exact_match == 1.0
    assert metrics.support_exact_match == 1.0
    assert metrics.trust_exact_match == 1.0


def test_readability_metric_detects_inline_citation_regression() -> None:
    before = evaluate_report_readability("## 回答\n事实 [1]，另一事实 [2]\n## Evidence 汇总\n[1] source")
    after = evaluate_report_readability(
        "> 图例：● 有声明级证据\n> 来源：A 官方/一手\n## 回答\n### ● A 结论\n事实。\n## Evidence 汇总\n[1] source"
    )

    assert before.inline_citation_count == 2
    assert after.inline_citation_count == 0
    assert after.has_support_legend and after.has_trust_legend and after.has_evidence_summary
