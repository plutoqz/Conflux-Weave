"""Thin command handler for the durable paper-discovery runtime."""

from __future__ import annotations

from dataclasses import asdict

from conflux_weave.cli_commands.shared import git_revision
from conflux_weave.paper_discovery import ArxivSearchAdapter
from conflux_weave.provider import (
    OpenAICompatibleChatAdapter,
    ProviderConfig,
    ProviderConfigurationError,
)
from conflux_weave.runtime import (
    BoundedPaperStrategyRuntime,
    DurablePaperDiscoveryRuntime,
    LocalArtifactStore,
    RecordNotFound,
    SQLiteRuntimeRepository,
)


def run(args, print_json) -> int:
    if (
        args.durable_command == "submit"
        and getattr(args, "strategy", "fixed") == "bounded"
    ):
        print_json(
            {
                "status": "rejected",
                "error_code": "strategy_rejected",
                "message": (
                    "bounded-arxiv-planner-v1 failed the frozen W4.5 offline "
                    "admission rule; new bounded submissions are disabled"
                ),
                "decision": "reject",
                "network_called": False,
                "provider_called": False,
            }
        )
        return 2
    store = LocalArtifactStore(args.artifact_root)
    repository = SQLiteRuntimeRepository(args.database, store)
    if args.durable_command == "status":
        try:
            output = status(repository, args.run_id)
        except RecordNotFound as exc:
            print_json({"status": "not_found", "message": str(exc)})
            return 2
        print_json(output)
        return 1 if output["run"]["status"] == "failed" else 0

    try:
        config = ProviderConfig.from_environment(args.dotenv)
    except ProviderConfigurationError as exc:
        print_json(
            {
                "status": "failed",
                "error_code": "provider_configuration_invalid",
                "message": str(exc),
                "network_called": False,
                "provider_called": False,
            }
        )
        return 2
    runtime_class = (
        BoundedPaperStrategyRuntime
        if getattr(args, "strategy", "fixed") == "bounded"
        else DurablePaperDiscoveryRuntime
    )
    runtime = runtime_class(
        repository,
        store,
        ArxivSearchAdapter(store),
        OpenAICompatibleChatAdapter(store, config),
        code_revision=git_revision(),
    )
    if args.durable_command == "submit":
        try:
            if args.strategy == "bounded":
                if args.search_query:
                    raise ValueError("--search-query is only valid for --strategy fixed")
                result = runtime.submit(
                    args.query,
                    task_summary=args.task_summary,
                    inclusion_constraints=tuple(args.include),
                    exclusion_constraints=tuple(args.exclude),
                    hard_constraints=tuple(args.hard_constraint),
                )
            else:
                if not args.search_query:
                    raise ValueError("--search-query is required for --strategy fixed")
                result = runtime.submit(
                    args.query,
                    search_query=args.search_query,
                    max_results=args.max_results,
                )
        except ValueError as exc:
            print_json(
                {"status": "failed", "error_code": "input_invalid", "message": str(exc)}
            )
            return 2
        print_json(
            {
                "status": repository.get_run(result.run_id).status.value,
                "task_id": result.task_id,
                "run_id": result.run_id,
                "created": result.created,
                "network_called": False,
                "provider_called": False,
            }
        )
        return 0
    result = runtime.work_once()
    print_json(
        {
            "status": result.status if result else "idle",
            "run_id": result.run_id if result else None,
            "step_kind": result.step_kind if result else None,
            "automatic_retry": False,
            "fallback": False,
        }
    )
    return 1 if result and result.status in {"failed", "waiting_for_user"} else 0


def status(repository: SQLiteRuntimeRepository, run_id: str) -> dict:
    run = repository.get_run(run_id)
    steps = repository.get_steps(run_id)
    budget = repository.get_budget_status(run_id)
    errors = repository.get_errors(run_id)
    drops = repository.get_telemetry_drops(run_id)
    try:
        delivery = repository.get_delivery(run_id)
        delivery_artifacts = repository.get_delivery_artifacts(run_id)
        delivery_output = {
            "disposition": delivery.disposition.value,
            "artifact_refs": list(delivery.artifact_refs),
            "artifacts": [asdict(item) for item in delivery_artifacts],
            "evidence_refs": list(delivery.evidence_refs),
            "limitations": list(delivery.limitations),
            "unmet_criteria": list(delivery.unmet_criteria),
            "recovery_actions": list(delivery.recovery_actions),
        }
    except RecordNotFound:
        delivery_output = None
    return {
        "run": {
            "run_id": run.run_id,
            "task_id": run.task_id,
            "status": run.status.value,
            "workflow_version": run.workflow_version,
            "config_snapshot_ref": run.config_snapshot_ref,
            "created_at": run.created_at,
            "updated_at": run.updated_at,
        },
        "steps": [
            {
                **asdict(step),
                "status": step.status.value,
                "attempts": [
                    asdict(item) for item in repository.get_attempts(step.step_id)
                ],
            }
            for step in steps
        ],
        "budget": asdict(budget),
        "budget_entries": [
            asdict(item) for item in repository.get_budget_entries(run_id)
        ],
        "errors": [
            {
                "error_id": item.error_id,
                "step_id": item.step_id,
                "attempt_id": item.attempt_id,
                "created_at": item.created_at,
                "code": item.record.code,
                "category": item.record.category.value,
                "stage": item.record.stage,
                "retryable": item.record.retryable,
                "user_message": item.record.user_message,
                "technical_detail_ref": item.record.technical_detail_ref,
                "affected_artifact_refs": list(item.record.affected_artifact_refs),
                "recovery_action": item.record.recovery_action,
            }
            for item in errors
        ],
        "telemetry_drop_count": len(drops),
        "telemetry_drops": [asdict(item) for item in drops],
        "delivery": delivery_output,
    }
