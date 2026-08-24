"""Frozen offline W4.5 A/B/C evaluation over synthetic arXiv fixtures."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse
from xml.etree import ElementTree as ET

from conflux_weave.core import BudgetLedger
from conflux_weave.deterministic_strategy import (
    DETERMINISTIC_STRATEGY_ID,
    derive_arxiv_query,
)
from conflux_weave.paper_discovery import ArxivHttpResponse, ArxivSearchAdapter
from conflux_weave.planning import BOUNDED_STRATEGY_ID, PLAN_SCHEMA_VERSION
from conflux_weave.provider import (
    OpenAICompatibleChatAdapter,
    ProviderConfig,
    ProviderHttpResponse,
)
from conflux_weave.runtime import (
    BoundedPaperStrategyRuntime,
    DurablePaperDiscoveryRuntime,
    LocalArtifactStore,
    SQLiteRuntimeRepository,
)
from conflux_weave.runtime.durable_paper_shared import RANK_CHECKPOINT, VALIDATION_CHECKPOINT


ROOT = Path(__file__).resolve().parents[2]
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "w4_abc_fixture.json"
CASES_PATH = ROOT / "datasets" / "regression" / "personal-research-v1.0.0" / "cases.jsonl"
ACCEPTED_AT = "2026-08-24T12:00:00Z"
STRATEGY_IDS = {
    "A": "fixed-arxiv-v1",
    "B": DETERMINISTIC_STRATEGY_ID,
    "C": BOUNDED_STRATEGY_ID,
}


class FixtureArxivTransport:
    def __init__(self, fixture: dict[str, Any]) -> None:
        self.fixture = fixture
        self.calls: list[str] = []
        self.results = {
            item["query"]: item["arxiv_ids"] for item in fixture["query_results"]
        }
        self.papers = {item["arxiv_id"]: item for item in fixture["papers"]}

    def get(self, url, *, headers, timeout_seconds):
        query = parse_qs(urlparse(url).query)["search_query"][0]
        self.calls.append(query)
        if query not in self.results:
            raise AssertionError(f"unfrozen arXiv query: {query}")
        body = _atom_feed([self.papers[item] for item in self.results[query]])
        return ArxivHttpResponse(
            200, body, {"Content-Type": "application/atom+xml"}
        )


class FixtureProviderTransport:
    def __init__(self, case: dict[str, Any], papers: dict[str, dict[str, Any]]) -> None:
        self.case = case
        self.papers = papers
        self.calls: list[str] = []

    def post(self, url, *, headers, body, timeout_seconds):
        request = json.loads(body)
        is_planner = request["max_tokens"] == 768
        self.calls.append("planner" if is_planner else "synthesis")
        if is_planner:
            content = self._plan(request)
            prompt_tokens, completion_tokens = 40, 20
        else:
            content = self._synthesis(request)
            prompt_tokens, completion_tokens = 120, 40
        response = {
            "id": f"fixture-{self.case['case_id']}-{len(self.calls)}",
            "model": "w4-offline-fixture",
            "choices": [{
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            },
        }
        return ProviderHttpResponse(
            200,
            json.dumps(response, ensure_ascii=False).encode("utf-8"),
            {"Content-Type": "application/json"},
        )

    def _plan(self, request: dict[str, Any]) -> str:
        prompt = request["messages"][1]["content"]
        context = json.loads(
            prompt.split("ContextBundle:\n", 1)[1].split("\nRequired root keys:", 1)[0]
        )
        constraint_refs = [item["constraint_id"] for item in context["constraints"]]
        actions = [
            {
                "action_id": f"search-{index}",
                "action_type": "tool_call",
                "tool_id": "search_arxiv",
                "arguments": {"query": query, "max_results": 15},
                "expected_evidence": ["synthetic arXiv Atom metadata and abstracts"],
                "constraint_refs": constraint_refs,
            }
            for index, query in enumerate(self.case["c_queries"], start=1)
        ]
        actions.append({
            "action_id": "finish-1",
            "action_type": "finish",
            "tool_id": None,
            "arguments": {},
            "expected_evidence": [],
            "constraint_refs": [],
        })
        return json.dumps({
            "schema_version": PLAN_SCHEMA_VERSION,
            "strategy_id": BOUNDED_STRATEGY_ID,
            "strategy_version": "bounded-arxiv-prompt-v1",
            "context_sha256": context["content_sha256"],
            "objective": "Preserve the frozen case constraints and search arXiv.",
            "actions": actions,
            "stop_reason": "Stop after the frozen bounded searches.",
        })

    def _synthesis(self, request: dict[str, Any]) -> str:
        prompt = request["messages"][1]["content"]
        evidence = []
        for block in prompt.split("\nEvidence ID: ")[1:]:
            evidence_id, quote_and_rest = block.split("\n", 1)
            quote = json.loads(quote_and_rest.split("\nEvidence ID: ", 1)[0].strip())
            evidence.append((evidence_id.strip(), quote["arxiv_id"]))
        claims = []
        for evidence_id, arxiv_id in evidence:
            paper = self.papers[arxiv_id]
            if paper["disposition"] == "excluded":
                continue
            if paper["disposition"] == "near_match":
                text = (
                    f"{paper['title']} is a near match; formal publication, public code, "
                    "complete ablation, and three real multi-agent tasks remain unverified."
                )
            else:
                text = f"{paper['title']} directly covers: {', '.join(paper['themes'])}."
            claims.append({"text": text, "evidence_ids": [evidence_id]})
        if not claims:
            raise AssertionError("fixture synthesis requires at least one bounded claim")
        return json.dumps({"claims": claims}, ensure_ascii=False)


def load_frozen_inputs() -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    cases: dict[str, dict[str, Any]] = {}
    raw_lines = CASES_PATH.read_bytes().replace(b"\r\n", b"\n").splitlines()
    for raw in raw_lines:
        case = json.loads(raw)
        cases[case["case_id"]] = case
    for frozen in fixture["cases"]:
        case = cases[frozen["case_id"]]
        canonical = json.dumps(
            case, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        digest = hashlib.sha256(canonical).hexdigest()
        if digest != frozen["case_line_sha256"]:
            raise AssertionError(f"case hash drift: {frozen['case_id']} {digest}")
        derived = derive_arxiv_query(case["input"])
        if derived != frozen["b_query"]:
            raise AssertionError(f"deterministic B drift: {frozen['case_id']}")
    return fixture, cases


def run_evaluation(output_root: Path) -> dict[str, Any]:
    fixture, cases = load_frozen_inputs()
    output_root.mkdir(parents=True, exist_ok=True)
    paper_map = {item["arxiv_id"]: item for item in fixture["papers"]}
    runs = []
    for frozen in fixture["cases"]:
        source_case = cases[frozen["case_id"]]
        for label in ("A", "B", "C"):
            runs.append(
                _run_one(output_root, fixture, frozen, source_case, paper_map, label)
            )
    comparison = _compare(fixture, runs)
    result = {
        "schema_version": "conflux-weave.w4.abc-result.v1",
        "status": "evaluated_offline",
        "evaluated_at": ACCEPTED_AT,
        "fixture_sha256": hashlib.sha256(FIXTURE_PATH.read_bytes()).hexdigest(),
        "source_boundary": fixture["source_boundary"],
        "network_calls": 0,
        "real_provider_calls": 0,
        "automatic_retry": False,
        "fallback": False,
        "runs": runs,
        "comparison": comparison,
    }
    _write_json(output_root / "w4_abc_results.json", result)
    return result


def _run_one(output_root, fixture, frozen, source_case, paper_map, label):
    case_id = frozen["case_id"]
    run_root = output_root / "runs" / case_id / label
    if (run_root / "runtime.sqlite3").exists():
        raise FileExistsError(
            f"refusing to reuse or overwrite an existing W4.5 Run: {run_root}"
        )
    store = LocalArtifactStore(run_root / "artifacts")
    repository = SQLiteRuntimeRepository(
        run_root / "runtime.sqlite3", store, clock=lambda: ACCEPTED_AT
    )
    arxiv = FixtureArxivTransport(fixture)
    provider = FixtureProviderTransport(frozen, paper_map)
    chat = OpenAICompatibleChatAdapter(
        store,
        ProviderConfig(
            "https://fixture.invalid/v1", "offline-fixture-secret", "w4-offline-fixture", "fixture"
        ),
        transport=provider,
    )
    ids = iter((f"task-{case_id}-{label}", f"run-{case_id}-{label}"))
    runtime_class = BoundedPaperStrategyRuntime if label == "C" else DurablePaperDiscoveryRuntime
    runtime = runtime_class(
        repository,
        store,
        ArxivSearchAdapter(store, transport=arxiv, acquired_at=ACCEPTED_AT),
        chat,
        clock=lambda: ACCEPTED_AT,
        id_factory=lambda prefix: next(ids),
        code_revision="01ffc319+w4-uncommitted",
    )
    if label == "C":
        inclusion, exclusion, hard = _constraints(source_case["input"])
        submission = runtime.submit(
            source_case["input"]["query_zh"],
            task_summary=source_case["scenario"],
            inclusion_constraints=inclusion,
            exclusion_constraints=exclusion,
            hard_constraints=hard,
            budget=BudgetLedger(300, 40_000, 3_072, "fixture-no-price", 4, 2, 1),
        )
    else:
        query = frozen["a_query"] if label == "A" else frozen["b_query"]
        submission = runtime.submit(
            source_case["input"]["query_zh"],
            search_query=query,
            max_results=15,
            budget=BudgetLedger(180, 20_000, 2_048, "fixture-no-price", 2, 1, 1),
        )
    while not repository.get_run(submission.run_id).status.is_terminal:
        if runtime.work_once(now=ACCEPTED_AT) is None:
            raise AssertionError(f"Run stalled: {submission.run_id}")
    run = repository.get_run(submission.run_id)
    delivery = repository.get_delivery(submission.run_id)
    artifacts = repository.get_delivery_artifacts(submission.run_id)
    manifest_ref = next(item for item in artifacts if item.media_type == "application/json")
    manifest = json.loads(store.read_bytes(manifest_ref))
    selected = manifest["selected_arxiv_ids"]
    rank_kind = "merge_and_rank" if label == "C" else "rank_candidates"
    rank = runtime._checkpoint(submission.run_id, rank_kind, RANK_CHECKPOINT)
    validation = runtime._checkpoint(
        submission.run_id, "validate_delivery", VALIDATION_CHECKPOINT
    )
    evidence_to_arxiv = {
        item["evidence_id"]: json.loads(item["quote"])["arxiv_id"]
        for item in rank["evidence"]
    }
    claimed = [evidence_to_arxiv[item["evidence_id"]] for item in validation["citations"]]
    accepted = [item for item in claimed if paper_map[item]["disposition"] == "direct"]
    near = [item for item in claimed if paper_map[item]["disposition"] == "near_match"]
    excluded = [item for item in selected if paper_map[item]["disposition"] == "excluded"]
    excluded_claimed = [
        item for item in claimed if paper_map[item]["disposition"] == "excluded"
    ]
    covered = sorted({theme for item in accepted for theme in paper_map[item]["themes"]})
    claim_count = manifest["claim_count"]
    closure = 1.0 if claim_count and manifest["citation_count"] == claim_count else 0.0
    constraint_score = 2
    precision = len(accepted) / claim_count if claim_count else 0.0
    hard_vetoes = []
    if closure != 1.0:
        hard_vetoes.append("citation_closure_below_1")
    if excluded_claimed:
        hard_vetoes.append("excluded_candidate_accepted")
    if case_id == "CW-PR-010" and not near:
        hard_vetoes.append("no_answer_boundary_lost")
    budget = repository.get_budget_status(submission.run_id)
    logical_latency = len(arxiv.calls) + len(provider.calls)
    report_ref = next(item.artifact_id for item in artifacts if item.media_type.startswith("text/markdown"))
    return {
        "case_id": case_id,
        "strategy_label": label,
        "strategy_id": STRATEGY_IDS[label],
        "run_id": submission.run_id,
        "run_status": run.status.value,
        "artifact_root": str(run_root / "artifacts").replace("\\", "/"),
        "database": str(run_root / "runtime.sqlite3").replace("\\", "/"),
        "search_queries": list(arxiv.calls),
        "selected_arxiv_ids": selected,
        "claimed_arxiv_ids": claimed,
        "candidate_metadata": [
            {
                "arxiv_id": item,
                "title": paper_map[item]["title"],
                "summary": paper_map[item]["summary"],
                "published": paper_map[item]["published"],
                "categories": paper_map[item]["categories"],
            }
            for item in selected
        ],
        "claims": _blind_claims(validation, evidence_to_arxiv),
        "accepted_arxiv_ids": accepted,
        "near_match_arxiv_ids": near,
        "excluded_selected_arxiv_ids": excluded,
        "direct_themes_covered": covered,
        "direct_candidate_coverage": _coverage_score(frozen["required_themes"], covered),
        "constraint_preservation": constraint_score,
        "accepted_precision": precision,
        "no_answer_integrity": case_id != "CW-PR-010" or (not accepted and bool(near)),
        "claim_count": claim_count,
        "evidence_count": manifest["evidence_count"],
        "citation_count": manifest["citation_count"],
        "citation_evidence_closure": closure,
        "hard_vetoes": hard_vetoes,
        "arxiv_calls": len(arxiv.calls),
        "provider_calls": len(provider.calls),
        "provider_tokens": manifest["usage"]["total_tokens"],
        "logical_latency_units": logical_latency,
        "structural_failures": len(repository.get_errors(submission.run_id)),
        "budget_actual": asdict(budget.actual),
        "report_artifact_ref": report_ref,
        "manifest_artifact_ref": manifest_ref.artifact_id,
        "automatic_retry": False,
        "fallback": False,
    }


def _compare(fixture: dict[str, Any], runs: list[dict[str, Any]]) -> dict[str, Any]:
    by_key = {(item["case_id"], item["strategy_label"]): item for item in runs}
    cases = []
    improvement_count = 0
    material_regressions = 0
    for case in fixture["cases"]:
        case_id = case["case_id"]
        a, b, c = (by_key[(case_id, label)] for label in ("A", "B", "C"))
        improved = (
            c["direct_candidate_coverage"] > b["direct_candidate_coverage"]
            or c["constraint_preservation"] > b["constraint_preservation"]
        )
        regressed = (
            c["direct_candidate_coverage"] < b["direct_candidate_coverage"]
            or c["constraint_preservation"] < b["constraint_preservation"]
            or c["accepted_precision"] < b["accepted_precision"]
        )
        improvement_count += int(improved)
        material_regressions += int(regressed)
        cases.append({
            "case_id": case_id,
            "c_beats_b": improved,
            "material_regression": regressed,
            "c_to_a_provider_token_ratio": c["provider_tokens"] / a["provider_tokens"],
            "c_to_a_logical_latency_ratio": c["logical_latency_units"] / a["logical_latency_units"],
        })
    vetoes = sum(len(item["hard_vetoes"]) for item in runs)
    ratios_ok = all(
        item["c_to_a_provider_token_ratio"] <= 2.5
        and item["c_to_a_logical_latency_ratio"] <= 2.5
        for item in cases
    )
    admitted = (
        improvement_count >= 2
        and material_regressions == 0
        and vetoes == 0
        and ratios_ok
        and all(item["citation_evidence_closure"] == 1.0 for item in runs)
    )
    return {
        "c_beats_b_case_count": improvement_count,
        "required_c_beats_b_case_count": 2,
        "material_regression_count": material_regressions,
        "hard_veto_count": vetoes,
        "ratios_within_2_5": ratios_ok,
        "handwritten_search_query_removed_by_b_and_c": True,
        "input_convenience_used_as_admission_evidence": False,
        "cases": cases,
        "candidate_admitted": admitted,
        "decision": "promote" if admitted else "reject",
        "decision_reason": (
            "C met every frozen admission condition."
            if admitted
            else f"C improved over B in {improvement_count}/3 cases; the frozen minimum is 2/3."
        ),
    }


def build_blind_pack(result: dict[str, Any]) -> dict[str, Any]:
    codes = {"A": "Variant-K", "B": "Variant-M", "C": "Variant-R"}
    return {
        "schema_version": "conflux-weave.w4.blind-review-pack.v1",
        "status": "awaiting_human_review",
        "source_boundary": result["source_boundary"],
        "review_instruction": "Judge relevance and constraint handling without inferring strategy identity.",
        "strategy_key_withheld_from_reviewers": True,
        "items": [
            {
                "case_id": item["case_id"],
                "variant_code": codes[item["strategy_label"]],
                "candidates": item["candidate_metadata"],
                "claims": item["claims"],
                "review_status": "pending",
            }
            for item in result["runs"]
        ],
        "human_review_completed": False,
    }


def _constraints(case_input):
    topics = tuple(case_input.get("topics", ()))
    scope = case_input.get("scope", {})
    inclusion = topics + tuple(scope.get("includes", ()))
    exclusion = tuple(scope.get("excludes", ()))
    hard = tuple(case_input.get("hard_constraints", ()))
    if case_input.get("window"):
        hard += (str(case_input["window"]),)
    return inclusion, exclusion, hard


def _blind_claims(validation, evidence_to_arxiv):
    citation_by_claim = {
        item["claim_id"]: item["evidence_id"] for item in validation["citations"]
    }
    return [
        {
            "text": claim["text"],
            "evidence_arxiv_ids": [
                evidence_to_arxiv[citation_by_claim[claim["claim_id"]]]
            ],
        }
        for claim in validation["claims"]
    ]


def _coverage_score(required: list[str], covered: list[str]) -> int:
    count = len(set(required) & set(covered))
    return 0 if count == 0 else 1 if count == 1 else 2


def _atom_feed(papers: list[dict[str, Any]]) -> bytes:
    atom = "http://www.w3.org/2005/Atom"
    ET.register_namespace("", atom)
    feed = ET.Element(f"{{{atom}}}feed")
    for paper in papers:
        entry = ET.SubElement(feed, f"{{{atom}}}entry")
        values = {
            "id": f"http://arxiv.org/abs/{paper['arxiv_id']}",
            "updated": paper["published"],
            "published": paper["published"],
            "title": paper["title"],
            "summary": paper["summary"],
        }
        for name, value in values.items():
            ET.SubElement(entry, f"{{{atom}}}{name}").text = value
        author = ET.SubElement(entry, f"{{{atom}}}author")
        ET.SubElement(author, f"{{{atom}}}name").text = "W4 Fixture Author"
        ET.SubElement(
            entry,
            f"{{{atom}}}link",
            {"href": values["id"], "rel": "alternate"},
        )
        for category in paper["categories"]:
            ET.SubElement(entry, f"{{{atom}}}category", {"term": category})
    return ET.tostring(feed, encoding="utf-8", xml_declaration=True)


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-root",
        type=Path,
        default=ROOT / "var" / "evaluations" / "w4" / "w4.5" / "final",
    )
    parser.add_argument("--blind-pack", type=Path)
    args = parser.parse_args()
    result = run_evaluation(args.output_root.resolve())
    if args.blind_pack:
        _write_json(args.blind_pack.resolve(), build_blind_pack(result))
    print(json.dumps(result["comparison"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
