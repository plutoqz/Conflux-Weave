import json

import pytest

from conflux_weave.core import BudgetLedger
from conflux_weave.harness import (
    AgentProfile,
    AgentResult,
    AgentResultStatus,
    ContextBundle,
    HARNESS_SCHEMA_VERSION,
    MessageEnvelope,
    MessageType,
    ToolSideEffect,
    ToolSpec,
    WorkspaceKind,
    WorkspaceRef,
    contract_to_json,
)


def budget() -> BudgetLedger:
    return BudgetLedger(30, 100, 50, "0", 1, 0, 1)


def test_contracts_serialize_deterministically() -> None:
    profile = AgentProfile(
        agent_type="research_fixture",
        version="v1",
        description="Deterministic research fixture",
        accepted_task_kinds=("research_fixture",),
        allowed_tool_ids=("fixture_lookup",),
        default_budget=budget(),
    )
    first = contract_to_json(profile)
    second = contract_to_json(profile)

    assert first == second
    assert json.loads(first) == {
        "accepted_task_kinds": ["research_fixture"],
        "agent_type": "research_fixture",
        "allowed_tool_ids": ["fixture_lookup"],
        "default_budget": {
            "concurrency": 1,
            "estimated_cost": "0",
            "input_tokens": 100,
            "output_tokens": 50,
            "retrieval_rounds": 0,
            "tool_calls": 1,
            "wall_clock_seconds": 30,
        },
        "description": "Deterministic research fixture",
        "schema_version": HARNESS_SCHEMA_VERSION,
        "version": "v1",
    }


def test_context_bundle_serializes_nested_workspace_refs() -> None:
    bundle = ContextBundle(
        context_id="context-1",
        agent_task_id="agent-task-1",
        identity="research_fixture@v1",
        objective="Return a deterministic research fixture result",
        state_snapshot={"run_id": "run-1", "status": "running"},
        input_refs=("artifact-input",),
        evidence_refs=(),
        workspace_refs=(
            WorkspaceRef(
                uri="weave://runs/run-1/context/input.json",
                kind=WorkspaceKind.FILE,
                revision="sha256:abc",
                media_type="application/json",
                read_only=True,
            ),
        ),
        available_tool_ids=("fixture_lookup",),
        constraints=("offline only",),
        completion_criteria=("publish one result",),
        created_at="2026-08-26T00:00:00Z",
    )

    restored = json.loads(contract_to_json(bundle))

    assert restored["workspace_refs"][0]["kind"] == "file"
    assert restored["state_snapshot"]["status"] == "running"


def test_failed_result_requires_error_reference() -> None:
    with pytest.raises(ValueError, match="requires error_ref"):
        AgentResult(
            agent_task_id="agent-task-1",
            status=AgentResultStatus.FAILED,
            summary="failed",
        )


def test_tool_spec_rejects_nonpositive_timeout() -> None:
    with pytest.raises(ValueError, match="timeout_seconds"):
        ToolSpec(
            tool_id="fixture_lookup",
            version="v1",
            description="fixture",
            input_schema={"type": "object"},
            output_schema={"type": "object"},
            side_effect_class=ToolSideEffect.NONE,
            required_permissions=(),
            timeout_seconds=0,
        )


def test_message_requires_nonempty_payload_reference() -> None:
    with pytest.raises(ValueError, match="payload_ref"):
        MessageEnvelope(
            message_id="message-1",
            run_id="run-1",
            agent_task_id="agent-task-1",
            sender="orchestrator",
            recipient="research_fixture@v1",
            message_type=MessageType.TASK_ASSIGNED,
            causation_id=None,
            correlation_id="run-1",
            payload_ref="",
            idempotency_key="assign:agent-task-1",
            created_at="2026-08-26T00:00:00Z",
        )
