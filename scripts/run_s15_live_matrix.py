from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime
import hashlib
import json
from pathlib import Path
import subprocess

from conflux_weave.hybrid_retrieval import HybridRetrievalPipeline
from conflux_weave.indexing import LanceDBDenseIndex, load_chunks
from conflux_weave.managed_research import ManagedVerifiedResearchWorkflow
from conflux_weave.provider import (
    OpenAICompatibleChatAdapter,
    OpenAICompatibleEmbeddingAdapter,
    OpenAICompatibleRerankerAdapter,
    ProviderConfig,
)
from conflux_weave.research_agents import VerifiedResearchWorkflow
from conflux_weave.runtime import (
    DurableResearchRuntime,
    LocalArtifactStore,
    SQLiteRuntimeRepository,
    VerifiedWorkflowExecutorAdapter,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=Path("datasets/regression/s15-live-research-v1"))
    parser.add_argument("--artifact-root", type=Path, default=Path("var/artifacts/sha256"))
    parser.add_argument("--dotenv", type=Path, default=Path(".env"))
    parser.add_argument("--database", type=Path, default=Path("var/acceptance/v0.3-s1/s15c-matrix.sqlite3"))
    parser.add_argument("--output", type=Path, default=Path("var/acceptance/v0.3-s1/s15c-matrix-summary.json"))
    parser.add_argument("--discovery-manifest-artifact", required=True)
    parser.add_argument("--execute-live", action="store_true")
    args = parser.parse_args()
    if not args.execute_live:
        parser.error("--execute-live is required because the matrix calls live Providers")

    dataset_manifest = _read_object(args.dataset / "manifest.json")
    cases = _read_jsonl(args.dataset / "cases.jsonl")
    if len(cases) != dataset_manifest["case_count"]:
        raise ValueError("dataset case count mismatch")

    store = LocalArtifactStore(args.artifact_root)
    config = ProviderConfig.from_environment(args.dotenv)
    repository = SQLiteRuntimeRepository(args.database, store)
    revision = _revision()
    protocol_hash = _dataset_hash(args.dataset)
    records = [_discovery_record(store, args.discovery_manifest_artifact, cases[0])]

    scope_paths = {
        "local": (
            Path("var/acceptance/v0.3-s1/corpus-import-manifest.json"),
            Path("var/acceptance/v0.3-s1/lancedb"),
        ),
        "new": (
            Path("var/acceptance/v0.3-s1/s15c-new-import-manifest.json"),
            Path("var/acceptance/v0.3-s1/s15c-corpora/new-lancedb"),
        ),
        "mixed": (
            Path("var/acceptance/v0.3-s1/s15c-corpora/mixed-import-manifest.json"),
            Path("var/acceptance/v0.3-s1/s15c-corpora/mixed-lancedb"),
        ),
    }
    runtimes = {
        scope: _runtime(repository, store, config, revision, manifest, lancedb)
        for scope, (manifest, lancedb) in scope_paths.items()
    }

    for case in cases[1:]:
        scope = case["corpus_scope"]
        records.append(
            _execute_case(
                case,
                runtimes[scope],
                repository,
                protocol_hash=protocol_hash,
                corpus_manifest=scope_paths[scope][0],
                lancedb_root=scope_paths[scope][1],
            )
        )
        _write_summary(args.output, dataset_manifest, protocol_hash, revision, config, records)

    _write_summary(args.output, dataset_manifest, protocol_hash, revision, config, records)
    print(args.output.read_text(encoding="utf-8"))


def _runtime(repository, store, config, revision, manifest, lancedb):
    documents = load_chunks(manifest, store)
    retrieval = HybridRetrievalPipeline(
        documents,
        LanceDBDenseIndex(lancedb, table_name="paper_chunks"),
        OpenAICompatibleEmbeddingAdapter(store, config),
        OpenAICompatibleRerankerAdapter(store, config),
    )
    verified = VerifiedResearchWorkflow(store, retrieval, OpenAICompatibleChatAdapter(store, config))
    managed = ManagedVerifiedResearchWorkflow(store, verified, OpenAICompatibleChatAdapter(store, config))
    return DurableResearchRuntime(
        repository,
        store,
        VerifiedWorkflowExecutorAdapter(store, verified, managed),
        worker_id="s15c-live-matrix-worker",
        code_revision=revision,
    )


def _execute_case(case, runtime, repository, *, protocol_hash, corpus_manifest, lancedb_root):
    submission = runtime.submit(
        case["objective"],
        task_kind=case["task_kind"],
        max_subquestions=int(case.get("max_subquestions", 4)),
        idempotency_key=f"s15c:{protocol_hash}:{case['case_id']}",
    )
    observed_steps = []
    while not repository.get_run(submission.run_id).status.is_terminal:
        result = runtime.work_once()
        if result is None:
            break
        observed_steps.append(result.step_kind)
    run = repository.get_run(submission.run_id)
    steps = repository.get_steps(run.run_id)
    budget = repository.get_budget_status(run.run_id)
    errors = repository.get_errors(run.run_id)
    delivery = None
    if run.status.value in {"succeeded", "partial"}:
        delivery = repository.get_delivery(run.run_id)
    result_metrics = _delivery_metrics(runtime.artifact_store, delivery)
    return {
        **case,
        "task_id": submission.task_id,
        "run_id": submission.run_id,
        "submission_created": submission.created,
        "run_status": run.status.value,
        "created_at": run.created_at,
        "updated_at": run.updated_at,
        "elapsed_seconds": _elapsed(run.created_at, run.updated_at),
        "observed_steps": [item.kind for item in steps],
        "steps_executed_this_invocation": observed_steps,
        "step_statuses": {item.kind: item.status.value for item in steps},
        "corpus_manifest": str(corpus_manifest),
        "corpus_manifest_sha256": _file_hash(corpus_manifest),
        "lancedb_root": str(lancedb_root),
        "budget": {
            "limit": asdict(budget.limit),
            "actual": asdict(budget.actual),
            "cost_enforcement": budget.cost_enforcement,
        },
        "delivery": asdict(delivery) if delivery else None,
        "result_metrics": result_metrics,
        "errors": [
            {
                "code": item.record.code,
                "category": item.record.category.value,
                "technical_detail_ref": item.record.technical_detail_ref,
                "retryable": item.record.retryable,
            }
            for item in errors
        ],
        "mechanical_acceptance": (
            "pending_manual_review"
            if delivery
            else "needs_attention"
            if run.status.value == "waiting_for_user"
            else "failed_no_delivery"
        ),
    }


def _discovery_record(store, artifact_id, case):
    payload = _read_artifact(store, artifact_id)
    if payload.get("schema_version") != "conflux-weave.arxiv-paper-discovery-live.v1":
        raise ValueError("discovery manifest schema mismatch")
    required = {"2608.24188v1", "2608.24876v1"}
    if not required.issubset(set(payload.get("selected_arxiv_ids", []))):
        raise ValueError("discovery result does not contain the frozen new sources")
    claims = int(payload.get("claim_count", 0))
    citations = int(payload.get("citation_count", 0))
    return {
        **case,
        "run_id": payload["run_id"],
        "run_status": payload["status"],
        "manifest_artifact": artifact_id,
        "report_artifact": payload["report_artifact_ref"],
        "provider_request_artifact": payload["provider_request_artifact_ref"],
        "provider_response_artifact": payload["provider_response_artifact_ref"],
        "source_response_artifact": payload["search_response_artifact_ref"],
        "selected_arxiv_ids": payload["selected_arxiv_ids"],
        "claim_count": claims,
        "citation_count": citations,
        "citation_closure": citations / claims if claims else 0.0,
        "budget": {"actual": payload.get("usage", {}), "cost_enforcement": "unavailable"},
        "mechanical_acceptance": "pending_manual_review",
    }


def _delivery_metrics(store, delivery):
    if delivery is None:
        return None
    manifest = _read_artifact(store, delivery.artifact_refs[1])
    coverage = manifest.get("coverage") if isinstance(manifest.get("coverage"), dict) else {}
    return {
        "schema_version": manifest.get("schema_version"),
        "claim_count": manifest.get("claim_count", coverage.get("accepted_claim_count")),
        "evidence_count": manifest.get("evidence_count", coverage.get("evidence_count")),
        "citation_count": manifest.get("citation_count"),
        "citation_closure": manifest.get("citation_closure"),
        "repair_rounds": manifest.get("repair_rounds", coverage.get("repair_rounds")),
        "stop_reason": manifest.get("stop_reason", coverage.get("stop_reason")),
    }


def _write_summary(path, dataset_manifest, protocol_hash, revision, config, records):
    all_recorded = len(records) == dataset_manifest["case_count"]
    needs_attention = any(item.get("run_status") == "waiting_for_user" for item in records)
    complete = all_recorded and not needs_attention
    payload = {
        "schema_version": "conflux-weave.s15c-live-matrix-summary.v1",
        "dataset_id": dataset_manifest["dataset_id"],
        "protocol_sha256": protocol_hash,
        "source_revision": revision,
        "source_dirty_at_start": _dirty(),
        "models": {
            "chat": config.model,
            "embedding": config.embedding_model or "text-embedding-v4",
            "reranker": config.reranker_model or "qwen3-rerank",
        },
        "case_count": len(records),
        "execution_complete": complete,
        "acceptance_status": (
            "blocked_unknown_outcome"
            if needs_attention
            else "pending_manual_review"
            if complete
            else "in_progress"
        ),
        "cases": records,
        "evidence_boundary": "Live Provider and real PDF execution. Mechanical citation closure does not replace manual Claim/Evidence support review.",
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _read_artifact(store, artifact_id):
    digest = artifact_id.removeprefix("artifact-sha256-")
    return json.loads(store.path_for_digest(digest).read_text(encoding="utf-8"))


def _read_object(path):
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected object: {path}")
    return value


def _read_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _dataset_hash(root):
    digest = hashlib.sha256()
    for name in ("manifest.json", "cases.jsonl"):
        digest.update((root / name).read_bytes())
    return digest.hexdigest()


def _file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _revision():
    return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()


def _dirty():
    return bool(subprocess.check_output(["git", "status", "--porcelain"], text=True).strip())


def _elapsed(start, end):
    return round((datetime.fromisoformat(end.replace("Z", "+00:00")) - datetime.fromisoformat(start.replace("Z", "+00:00"))).total_seconds(), 3)


if __name__ == "__main__":
    main()
