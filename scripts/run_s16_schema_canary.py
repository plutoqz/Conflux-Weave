from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import UTC, datetime
import json
from pathlib import Path
import subprocess
from time import perf_counter

from conflux_weave.evidence import Citation, Claim, EvidenceRef
from conflux_weave.managed_research import (
    MANAGER_PLAN_SYSTEM_PROMPT,
    ManagedVerifiedResearchWorkflow,
)
from conflux_weave.paper_discovery import (
    DISCOVERY_VERIFICATION_SYSTEM_PROMPT,
    _parse_claim_assessments,
)
from conflux_weave.provider import OpenAICompatibleChatAdapter, ProviderConfig
from conflux_weave.research_agents import VerifiedResearchWorkflow
from conflux_weave.runtime import LocalArtifactStore


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path("datasets/regression/s16-contract-closeout-live-v1"),
    )
    parser.add_argument("--artifact-root", type=Path, default=Path("var/artifacts/sha256"))
    parser.add_argument("--dotenv", type=Path, default=Path(".env"))
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("var/acceptance/v0.3-s1/s16e-schema-canary.json"),
    )
    parser.add_argument("--execute-live", action="store_true")
    args = parser.parse_args()
    if not args.execute_live:
        parser.error("--execute-live is required because the canary calls the live Chat Provider")

    cases = _read_jsonl(args.dataset / "cases.jsonl")
    store = LocalArtifactStore(args.artifact_root)
    config = ProviderConfig.from_environment(args.dotenv)
    chat = OpenAICompatibleChatAdapter(store, config)
    revision = _revision()
    dirty = _dirty()
    results = []
    for case in cases[1:]:
        started = perf_counter()
        completion = chat.complete(
            system_prompt=MANAGER_PLAN_SYSTEM_PROMPT,
            user_prompt=json.dumps(
                {"objective": case["objective"], "max_subquestions": case["max_subquestions"]},
                ensure_ascii=False,
            ),
            max_output_tokens=800,
            temperature=0,
            json_object=True,
            enable_thinking=False,
            producer_step_id="s16e-canary-manager-plan",
        )
        requirements, subquestions = ManagedVerifiedResearchWorkflow._parse_plan(
            case["objective"], completion.content, case["max_subquestions"]
        )
        results.append(
            {
                "case_id": case["case_id"],
                "status": "validated_live",
                "model": completion.model,
                "request_artifact": completion.request_artifact.artifact_id,
                "response_artifact": completion.response_artifact.artifact_id,
                "usage": {
                    "input_tokens": completion.input_tokens,
                    "output_tokens": completion.output_tokens,
                    "total_tokens": completion.total_tokens,
                },
                "elapsed_ms": round((perf_counter() - started) * 1000, 1),
                "coverage_requirements": [asdict(item) for item in requirements],
                "subquestions": [asdict(item) for item in subquestions],
            }
        )

    results.append(_worker_verifier_canary(chat, store))
    results.append(_discovery_canary(chat, store))
    payload = {
        "schema_version": "conflux-weave.s16-schema-canary.v2",
        "created_at": _now(),
        "source_revision": revision,
        "source_dirty_at_start": dirty,
        "provider_automatic_retry": False,
        "status": "validated_live",
        "checks": results,
        "evidence_boundary": "Four schema and referential-integrity calls using the production prompts and parsers; no retrieval, research Delivery, or quality claim.",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(args.output.read_text(encoding="utf-8"))


def _discovery_canary(chat, store):
    claim = Claim(
        "paper-claim-0001",
        "The paper studies context management for long-horizon language-model agents.",
        "paper_relevance",
        "primary",
        "s16e-canary-discovery",
    )
    evidence = EvidenceRef(
        "arxiv-paper-01",
        "s16e-canary-source",
        {"type": "atom_entry", "arxiv_id": "fixture-canary"},
        "Title: Context Management for Long-Horizon Language-Model Agents. Abstract: We study context management for long-horizon language-model agents.",
        "s16e-canary",
    )
    citation = Citation(
        "paper-citation-0001", claim.claim_id, evidence.evidence_id, 1
    )
    started = perf_counter()
    completion = chat.complete(
        system_prompt=DISCOVERY_VERIFICATION_SYSTEM_PROMPT,
        user_prompt=json.dumps(
            {"claims": [asdict(claim)], "evidence": [asdict(evidence)]},
            ensure_ascii=False,
        ),
        max_output_tokens=2048,
        temperature=0,
        json_object=True,
        enable_thinking=False,
        producer_step_id="s16e-canary-discovery",
    )
    assessments, assessment_artifact = _parse_claim_assessments(
        completion.content,
        (claim,),
        (citation,),
        (evidence,),
        completion.response_artifact.artifact_id,
        store,
        "s16e-canary-discovery",
    )
    return {
        "case_id": "s16e-discovery-verifier-canary",
        "status": "validated_live",
        "model": completion.model,
        "request_artifact": completion.request_artifact.artifact_id,
        "response_artifact": completion.response_artifact.artifact_id,
        "assessment_artifact": assessment_artifact.artifact_id,
        "usage": {
            "input_tokens": completion.input_tokens,
            "output_tokens": completion.output_tokens,
            "total_tokens": completion.total_tokens,
        },
        "elapsed_ms": round((perf_counter() - started) * 1000, 1),
        "assessments": [asdict(item) for item in assessments],
    }


def _worker_verifier_canary(chat, store):
    claim = Claim(
        "claim-0001",
        "The method reduces context pressure.",
        "research_finding",
        "primary",
        "s16-worker-verifier-canary",
    )
    evidence = EvidenceRef(
        "evidence-0001",
        "s16-worker-verifier-canary-source",
        {"type": "pdf_page", "page": 1},
        "The method reduces context pressure.",
        "s16-worker-verifier-canary",
    )
    started = perf_counter()
    workflow = VerifiedResearchWorkflow(store, None, chat)
    assessments, artifact_refs = workflow._verify(
        (claim,), (evidence,), round_number=0
    )
    assessment_payload = _read_artifact(store, artifact_refs[2])
    return {
        "case_id": "s16-worker-verifier-canary",
        "status": "validated_live",
        "request_artifact": artifact_refs[0],
        "response_artifact": artifact_refs[1],
        "assessment_artifact": artifact_refs[2],
        "elapsed_ms": round((perf_counter() - started) * 1000, 1),
        "assessments": [asdict(item) for item in assessments],
        "normalization_warnings": assessment_payload["normalization_warnings"],
    }


def _read_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _read_artifact(store, artifact_id):
    digest = artifact_id.removeprefix("artifact-sha256-")
    return json.loads(store.path_for_digest(digest).read_text(encoding="utf-8"))


def _revision():
    return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()


def _dirty():
    return bool(subprocess.check_output(["git", "status", "--porcelain"], text=True).strip())


def _now():
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


if __name__ == "__main__":
    main()
