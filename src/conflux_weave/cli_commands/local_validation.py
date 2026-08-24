"""CLI handlers for deterministic local validation workflows."""

from __future__ import annotations

import sys

from conflux_weave.runtime import (
    FixedOutcomeWorkflow,
    FixedValidationWorkflow,
    LocalArtifactStore,
    OutcomeScenario,
)


def run(args, print_json) -> int:
    if args.command == "validate-outcome":
        return _validate_outcome(args, print_json)
    return _validate_workflow(args, print_json)


def _validate_outcome(args, print_json) -> int:
    try:
        execution = FixedOutcomeWorkflow(
            LocalArtifactStore(args.artifact_root)
        ).execute(args.query, OutcomeScenario(args.scenario))
    except ValueError as exc:
        print_json(
            {
                "status": "failed",
                "error_code": "outcome_input_invalid",
                "message": str(exc),
            }
        )
        return 2
    print_json(
        {
            "run_id": execution.final_run.run_id,
            "run_status": execution.final_run.status.value,
            "step_status": execution.final_step.status.value,
            "scenario": args.scenario,
            "artifact_ref": execution.artifact.artifact_id,
            "delivery_disposition": (
                execution.delivery.disposition.value if execution.delivery else None
            ),
            "user_input_request": (
                {
                    "reason_code": execution.user_input_request.reason_code,
                    "prompt": execution.user_input_request.prompt,
                    "requested_inputs": list(
                        execution.user_input_request.requested_inputs
                    ),
                }
                if execution.user_input_request
                else None
            ),
            "error": (
                {
                    "code": execution.error.code,
                    "category": execution.error.category.value,
                    "retryable": execution.error.retryable,
                    "recovery_action": execution.error.recovery_action,
                }
                if execution.error
                else None
            ),
            "validation_only": True,
            "network_called": False,
            "provider_called": False,
        }
    )
    return 1 if execution.error else 0


def _validate_workflow(args, print_json) -> int:
    try:
        execution = FixedValidationWorkflow(
            LocalArtifactStore(args.artifact_root)
        ).execute(args.query)
    except ValueError as exc:
        print(f"input error: {exc}", file=sys.stderr)
        return 2
    print_json(
        {
            "task_id": execution.task.task_id,
            "run_id": execution.final_run.run_id,
            "run_status": execution.final_run.status.value,
            "step_status": execution.final_step.status.value,
            "artifact_id": execution.artifact.artifact_id,
            "artifact_uri": execution.artifact.storage_uri,
            "validation_only": execution.validation_only,
            "evidence_boundary": execution.evidence_boundary,
            "error": execution.error.code if execution.error else None,
        }
    )
    return 0 if execution.error is None else 1
