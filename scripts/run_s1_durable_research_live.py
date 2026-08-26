from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import subprocess

from conflux_weave.hybrid_retrieval import HybridRetrievalPipeline
from conflux_weave.indexing import LanceDBDenseIndex, load_chunks
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
    parser.add_argument(
        "--objective",
        default=(
            "How do recent long-horizon LLM agent systems reduce context usage, "
            "and how do they evaluate the resulting agent behavior?"
        ),
    )
    parser.add_argument(
        "--import-manifest",
        type=Path,
        default=Path("var/acceptance/v0.3-s1/corpus-import-manifest.json"),
    )
    parser.add_argument(
        "--artifact-root", type=Path, default=Path("var/artifacts/sha256")
    )
    parser.add_argument(
        "--lancedb", type=Path, default=Path("var/acceptance/v0.3-s1/lancedb")
    )
    parser.add_argument("--dotenv", type=Path, default=Path(".env"))
    parser.add_argument(
        "--database",
        type=Path,
        default=Path("var/acceptance/v0.3-s1/durable-research-live.sqlite3"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("var/acceptance/v0.3-s1/durable-research-live-summary.json"),
    )
    args = parser.parse_args()

    revision = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], text=True
    ).strip()
    dirty = bool(
        subprocess.check_output(
            ["git", "status", "--porcelain"], text=True
        ).strip()
    )
    store = LocalArtifactStore(args.artifact_root)
    config = ProviderConfig.from_environment(args.dotenv)
    documents = load_chunks(args.import_manifest, store)
    retrieval = HybridRetrievalPipeline(
        documents,
        LanceDBDenseIndex(args.lancedb, table_name="paper_chunks"),
        OpenAICompatibleEmbeddingAdapter(store, config),
        OpenAICompatibleRerankerAdapter(store, config),
    )
    workflow = VerifiedResearchWorkflow(
        store,
        retrieval,
        OpenAICompatibleChatAdapter(store, config),
    )
    repository = SQLiteRuntimeRepository(args.database, store)
    runtime = DurableResearchRuntime(
        repository,
        store,
        VerifiedWorkflowExecutorAdapter(store, workflow),
        worker_id="s1-durable-live-worker",
        code_revision=revision,
    )
    submission = runtime.submit(args.objective)
    observed_steps = []
    while not repository.get_run(submission.run_id).status.is_terminal:
        result = runtime.work_once()
        if result is None:
            break
        observed_steps.append(result.step_kind)

    run = repository.get_run(submission.run_id)
    budget = repository.get_budget_status(submission.run_id)
    errors = repository.get_errors(submission.run_id)
    delivery = repository.get_delivery(submission.run_id) if run.status.is_terminal and run.status.value in {"succeeded", "partial"} else None
    payload = {
        "schema_version": "conflux-weave.s1-durable-research-live-summary.v1",
        "source_revision": revision,
        "source_dirty_at_start": dirty,
        "task_id": submission.task_id,
        "run_id": submission.run_id,
        "submission_created": submission.created,
        "run_status": run.status.value,
        "observed_steps": observed_steps,
        "step_statuses": {
            step.kind: step.status.value for step in repository.get_steps(run.run_id)
        },
        "budget": {
            "limit": asdict(budget.limit),
            "actual": asdict(budget.actual),
            "reserved": asdict(budget.reserved),
            "cost_enforcement": budget.cost_enforcement,
        },
        "delivery": asdict(delivery) if delivery is not None else None,
        "errors": [
            {
                "code": item.record.code,
                "category": item.record.category.value,
                "technical_detail_ref": item.record.technical_detail_ref,
            }
            for item in errors
        ],
        "evidence_boundary": (
            "Real local corpus, LanceDB, Embedding, Reranker and Chat Provider run. "
            "SQLite durability is validated at an opaque paid research-batch boundary; "
            "individual Provider-call recovery is not implemented."
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
