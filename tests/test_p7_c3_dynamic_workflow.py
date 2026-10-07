"""Unit and integration tests for Gate C3 Dynamic Durable Workflow.

Tests cover:
- Conditional branching evaluation & transitive skipping
- Dynamic step appending during running plan & dependency validation
- Subtask dynamic fan-out & spawn
- Human-in-the-loop (HITL) approval nodes (approve / reject)
- Subtask convergence (join_policy: all, any, first_success) & upstream results injection
- Workflow pause & durable resume
- REST API endpoints for pause, resume, append-nodes, approve, and status
"""

from __future__ import annotations

import asyncio
from pathlib import Path
import time
from types import SimpleNamespace
from typing import Any

import pytest
from starlette.testclient import TestClient

from conflux_weave.orchestrator.dag_scheduler import DAGTaskScheduler, evaluate_condition
from conflux_weave.orchestrator.event_bus import AsyncAgentEventBus
from conflux_weave.orchestrator.spec import (
    DAGDependencyError,
    DAGPlan,
    DAGTaskNode,
    DAGTaskStatus,
)


def test_evaluate_condition_operators() -> None:
    """Test evaluate_condition with various operators, fields, and edge cases."""
    # 1. Null / boolean conditions
    assert evaluate_condition(None, {}) is True
    assert evaluate_condition(True, {}) is True
    assert evaluate_condition(False, {}) is False

    # 2. Missing node in results
    assert evaluate_condition({"node": "missing_node"}, {}) is False

    # 3. Equals
    results = {"step1": {"status": "success", "count": 10, "tags": ["prod", "v1"]}}
    assert evaluate_condition({"node": "step1", "field": "status", "equals": "success"}, results) is True
    assert evaluate_condition({"node": "step1", "field": "status", "equals": "failed"}, results) is False

    # 4. Comparison operators
    assert evaluate_condition({"node": "step1", "field": "count", "op": "==", "value": 10}, results) is True
    assert evaluate_condition({"node": "step1", "field": "count", "op": "!=", "value": 5}, results) is True
    assert evaluate_condition({"node": "step1", "field": "count", "op": ">", "value": 5}, results) is True
    assert evaluate_condition({"node": "step1", "field": "count", "op": ">=", "value": 10}, results) is True
    assert evaluate_condition({"node": "step1", "field": "count", "op": "<", "value": 20}, results) is True
    assert evaluate_condition({"node": "step1", "field": "count", "op": "<=", "value": 10}, results) is True

    # 5. Collection operators: in & contains
    assert evaluate_condition({"node": "step1", "field": "tags", "op": "contains", "value": "prod"}, results) is True
    assert evaluate_condition({"node": "step1", "field": "tags", "op": "contains", "value": "dev"}, results) is False
    assert evaluate_condition({"node": "step1", "field": "status", "op": "in", "value": ["success", "pending"]}, results) is True


def test_conditional_branching_execution(tmp_path: Path) -> None:
    """Test conditional branching: node condition evaluated to False is skipped and transitively skips downstream."""
    async def _run() -> None:
        db_file = tmp_path / "events.sqlite3"
        event_bus = AsyncAgentEventBus(db_file)
        scheduler = DAGTaskScheduler(event_bus=event_bus)

        # Plan:
        #           -> B_high (cond: risk == high) -> D_high
        # Root (A) -|
        #           -> B_low  (cond: risk == low)  -> D_low
        plan = DAGPlan(
            plan_id="plan-cond-branch",
            run_id="run-cond-1",
            objective="Branching Test",
            nodes=(
                DAGTaskNode(node_id="A", agent_type="analyzer", objective="Analyze Risk"),
                DAGTaskNode(
                    node_id="B_high",
                    agent_type="security",
                    objective="Escalate High Risk",
                    depends_on=("A",),
                    condition={"node": "A", "field": "risk", "op": "==", "value": "high"},
                ),
                DAGTaskNode(
                    node_id="B_low",
                    agent_type="logger",
                    objective="Log Low Risk",
                    depends_on=("A",),
                    condition={"node": "A", "field": "risk", "op": "==", "value": "low"},
                ),
                DAGTaskNode(node_id="D_high", agent_type="manager", objective="Report Incident", depends_on=("B_high",)),
                DAGTaskNode(node_id="D_low", agent_type="manager", objective="Close Ticket", depends_on=("B_low",)),
            ),
        )

        async def executor(node: DAGTaskNode) -> dict[str, Any]:
            if node.node_id == "A":
                return {"risk": "high"}
            return {"handled": node.node_id}

        result = await scheduler.execute_plan(plan, node_executor=executor)

        assert result.status == "completed"
        assert "A" in result.completed_nodes
        assert "B_high" in result.completed_nodes
        assert "D_high" in result.completed_nodes

        # B_low and its downstream D_low must be skipped
        assert "B_low" in result.skipped_nodes
        assert "D_low" in result.skipped_nodes

        # Check emitted skip events
        events = event_bus.list_events("run-cond-1", event_type="node_skipped")
        skipped_node_ids = [e.payload.get("node_id") for e in events]
        assert "B_low" in skipped_node_ids
        assert "D_low" in skipped_node_ids

    asyncio.run(_run())


def test_dynamic_step_appending_in_flight(tmp_path: Path) -> None:
    """Test dynamically appending new nodes during an active running execution."""
    async def _run() -> None:
        db_file = tmp_path / "events.sqlite3"
        event_bus = AsyncAgentEventBus(db_file)
        scheduler = DAGTaskScheduler(event_bus=event_bus)

        plan = DAGPlan(
            plan_id="plan-dynamic-append",
            run_id="run-dyn-1",
            objective="Dynamic Append Test",
            nodes=(
                DAGTaskNode(node_id="init", agent_type="manager", objective="Initial node"),
            ),
        )

        executed_nodes: list[str] = []

        async def dynamic_executor(node: DAGTaskNode) -> dict[str, Any]:
            executed_nodes.append(node.node_id)
            if node.node_id == "init":
                # Dynamically append 2 more nodes to the active plan
                new_nodes = [
                    DAGTaskNode(node_id="appended_1", agent_type="coder", objective="Step 2", depends_on=("init",)),
                    DAGTaskNode(node_id="appended_2", agent_type="verifier", objective="Step 3", depends_on=("appended_1",)),
                ]
                appended_ids = scheduler.append_nodes(plan.plan_id, new_nodes)
                assert appended_ids == ["appended_1", "appended_2"]
            return {"done": node.node_id}

        result = await scheduler.execute_plan(plan, node_executor=dynamic_executor)

        assert result.status == "completed"
        assert set(result.completed_nodes) == {"init", "appended_1", "appended_2"}
        assert executed_nodes == ["init", "appended_1", "appended_2"]

        # Verification of appended dependency validation error
        with pytest.raises(DAGDependencyError):
            scheduler.append_nodes(
                plan.plan_id,
                [DAGTaskNode(node_id="bad_node", agent_type="tester", objective="Bad", depends_on=("non_existent",))],
            )

    asyncio.run(_run())


def test_dynamic_subtask_fanout_spawn(tmp_path: Path) -> None:
    """Test dynamic subtask fan-out: node returns __spawn_nodes__ to generate child nodes automatically."""
    async def _run() -> None:
        event_bus = AsyncAgentEventBus(tmp_path / "events.sqlite3")
        scheduler = DAGTaskScheduler(event_bus=event_bus)

        plan = DAGPlan(
            plan_id="plan-fanout",
            run_id="run-fanout-1",
            objective="Subtask Fanout Test",
            nodes=(
                DAGTaskNode(node_id="generator", agent_type="decomposer", objective="Generate work items"),
            ),
        )

        executed_nodes: list[str] = []

        async def fanout_executor(node: DAGTaskNode) -> dict[str, Any]:
            executed_nodes.append(node.node_id)
            if node.node_id == "generator":
                return {
                    "count": 3,
                    "__spawn_nodes__": [
                        DAGTaskNode(node_id=f"worker_{i}", agent_type="worker", objective=f"Item {i}", depends_on=("generator",))
                        for i in range(1, 4)
                    ],
                }
            return {"worker_result": f"processed_{node.node_id}"}

        result = await scheduler.execute_plan(plan, node_executor=fanout_executor)

        assert result.status == "completed"
        assert set(result.completed_nodes) == {"generator", "worker_1", "worker_2", "worker_3"}
        assert "worker_result" in result.node_results["worker_1"]
        assert result.node_results["worker_1"]["worker_result"] == "processed_worker_1"

        # Check fanout events
        events = event_bus.list_events("run-fanout-1", event_type="node_appended")
        assert len(events) == 3

    asyncio.run(_run())


def test_human_in_the_loop_approval_flow(tmp_path: Path) -> None:
    """Test human approval node: transitions to WAITING_APPROVAL, unblocks upon submit_approval."""
    async def _run() -> None:
        event_bus = AsyncAgentEventBus(tmp_path / "events.sqlite3")
        scheduler = DAGTaskScheduler(event_bus=event_bus)

        plan = DAGPlan(
            plan_id="plan-hitl-approve",
            run_id="run-hitl-1",
            objective="Human Approval Approved Flow",
            nodes=(
                DAGTaskNode(node_id="prep", agent_type="researcher", objective="Prepare draft"),
                DAGTaskNode(
                    node_id="review_gate",
                    agent_type="legal_auditor",
                    objective="Approve draft publishing",
                    depends_on=("prep",),
                    requires_approval=True,
                ),
                DAGTaskNode(node_id="publish", agent_type="publisher", objective="Publish paper", depends_on=("review_gate",)),
            ),
        )

        async def hitl_executor(node: DAGTaskNode) -> dict[str, Any]:
            return {"step": node.node_id, "approved": getattr(node, "approval_status", None)}

        async def approver_daemon() -> None:
            # Wait until review_gate is in waiting_approval
            for _ in range(50):
                await asyncio.sleep(0.05)
                active = scheduler._active_executions.get("plan-hitl-approve")
                if active and "review_gate" in active["waiting_approval_ids"]:
                    scheduler.submit_approval(
                        plan_id="plan-hitl-approve",
                        node_id="review_gate",
                        decision="approved",
                        approved_by="chief_editor",
                        comment="High quality draft",
                    )
                    break

        daemon_task = asyncio.create_task(approver_daemon())
        result = await scheduler.execute_plan(plan, node_executor=hitl_executor)
        await daemon_task

        assert result.status == "completed"
        assert set(result.completed_nodes) == {"prep", "review_gate", "publish"}
        assert result.failed_nodes == ()

        # Verify event emissions
        req_events = event_bus.list_events("run-hitl-1", event_type="approval_requested")
        assert len(req_events) == 1
        assert req_events[0].payload["node_id"] == "review_gate"

        grant_events = event_bus.list_events("run-hitl-1", event_type="approval_granted")
        assert len(grant_events) == 1
        assert grant_events[0].payload["approved_by"] == "chief_editor"

    asyncio.run(_run())


def test_human_in_the_loop_rejection_flow(tmp_path: Path) -> None:
    """Test human approval node rejection: marks node FAILED and skips downstream."""
    async def _run() -> None:
        event_bus = AsyncAgentEventBus(tmp_path / "events.sqlite3")
        scheduler = DAGTaskScheduler(event_bus=event_bus)

        plan = DAGPlan(
            plan_id="plan-hitl-reject",
            run_id="run-hitl-2",
            objective="Human Approval Rejection Flow",
            nodes=(
                DAGTaskNode(node_id="prep", agent_type="coder", objective="Draft modification"),
                DAGTaskNode(
                    node_id="review_gate",
                    agent_type="security_reviewer",
                    objective="Approve dangerous deploy",
                    depends_on=("prep",),
                    requires_approval=True,
                ),
                DAGTaskNode(node_id="deploy", agent_type="devops", objective="Deploy to production", depends_on=("review_gate",)),
            ),
        )

        async def rejector_daemon() -> None:
            for _ in range(50):
                await asyncio.sleep(0.05)
                active = scheduler._active_executions.get("plan-hitl-reject")
                if active and "review_gate" in active["waiting_approval_ids"]:
                    scheduler.submit_approval(
                        plan_id="plan-hitl-reject",
                        node_id="review_gate",
                        decision="rejected",
                        approved_by="security_lead",
                        comment="Found severe CVE vulnerability",
                    )
                    break

        daemon_task = asyncio.create_task(rejector_daemon())
        result = await scheduler.execute_plan(plan)
        await daemon_task

        assert result.status == "failed"
        assert "prep" in result.completed_nodes
        assert "review_gate" in result.failed_nodes
        assert "deploy" in result.skipped_nodes
        assert "Found severe CVE vulnerability" in (result.error or "")

        rej_events = event_bus.list_events("run-hitl-2", event_type="approval_rejected")
        assert len(rej_events) == 1
        assert rej_events[0].payload["comment"] == "Found severe CVE vulnerability"

    asyncio.run(_run())


def test_subtask_convergence_join_policy(tmp_path: Path) -> None:
    """Test subtask convergence and join policies (all vs. any), plus upstream results injection."""
    async def _run() -> None:
        scheduler = DAGTaskScheduler()

        # 1. Policy "all": Join receives all upstream node results
        plan_all = DAGPlan(
            plan_id="plan-join-all",
            run_id="run-join-all",
            objective="Join All Convergence",
            nodes=(
                DAGTaskNode(node_id="A1", agent_type="worker", objective="Calc 1"),
                DAGTaskNode(node_id="A2", agent_type="worker", objective="Calc 2"),
                DAGTaskNode(
                    node_id="join_node",
                    agent_type="reducer",
                    objective="Aggregate",
                    depends_on=("A1", "A2"),
                    join_policy="all",
                ),
            ),
        )

        received_payloads: dict[str, Any] = {}

        async def all_executor(node: DAGTaskNode) -> dict[str, Any]:
            received_payloads[node.node_id] = node.input_payload
            if node.node_id == "A1":
                return {"value": 10}
            elif node.node_id == "A2":
                return {"value": 20}
            return {"sum": 30}

        res_all = await scheduler.execute_plan(plan_all, node_executor=all_executor)
        assert res_all.status == "completed"
        # Join node must have received upstream_results injected
        join_input = received_payloads["join_node"]
        assert "upstream_results" in join_input
        assert join_input["upstream_results"]["A1"]["value"] == 10
        assert join_input["upstream_results"]["A2"]["value"] == 20

        # 2. Policy "any": Join node triggers as soon as any upstream dependency finishes
        plan_any = DAGPlan(
            plan_id="plan-join-any",
            run_id="run-join-any",
            objective="Join Any Convergence",
            nodes=(
                DAGTaskNode(node_id="fast_worker", agent_type="worker", objective="Fast worker"),
                DAGTaskNode(node_id="slow_worker", agent_type="worker", objective="Slow worker"),
                DAGTaskNode(
                    node_id="first_responder",
                    agent_type="verifier",
                    objective="Proceed on first completion",
                    depends_on=("fast_worker", "slow_worker"),
                    join_policy="any",
                ),
            ),
        )

        timings: dict[str, float] = {}
        t0 = time.monotonic()

        async def any_executor(node: DAGTaskNode) -> dict[str, Any]:
            if node.node_id == "fast_worker":
                await asyncio.sleep(0.02)
            elif node.node_id == "slow_worker":
                await asyncio.sleep(0.20)
            timings[node.node_id] = time.monotonic() - t0
            return {"finished": node.node_id}

        res_any = await scheduler.execute_plan(plan_any, node_executor=any_executor)
        assert res_any.status == "completed"
        # first_responder must run before slow_worker finishes!
        assert timings["first_responder"] < timings["slow_worker"]

    asyncio.run(_run())


def test_workflow_pause_and_durable_resume(tmp_path: Path) -> None:
    """Test pausing a running workflow safely and resuming without re-executing completed nodes."""
    async def _run() -> None:
        db_file = tmp_path / "dag_checkpoints.sqlite3"
        scheduler = DAGTaskScheduler(db_path=db_file)

        plan = DAGPlan(
            plan_id="plan-pause-resume",
            run_id="run-pr-1",
            objective="Pause and Resume Test",
            nodes=(
                DAGTaskNode(node_id="step_1", agent_type="coder", objective="Step 1"),
                DAGTaskNode(node_id="step_2", agent_type="coder", objective="Step 2", depends_on=("step_1",)),
                DAGTaskNode(node_id="step_3", agent_type="coder", objective="Step 3", depends_on=("step_2",)),
            ),
        )

        executed_first_run: list[str] = []

        async def pause_on_step1(node: DAGTaskNode) -> dict[str, Any]:
            executed_first_run.append(node.node_id)
            if node.node_id == "step_1":
                # Trigger pause
                scheduler.pause_plan(plan.plan_id)
                await asyncio.sleep(0.05)
            return {"step": node.node_id}

        # 1. First execution pauses after step_1 completes
        result1 = await scheduler.execute_plan(plan, node_executor=pause_on_step1)
        assert result1.status == "paused"
        assert result1.is_paused is True
        assert result1.completed_nodes == ("step_1",)
        assert executed_first_run == ["step_1"]

        # Check saved plan in scheduler memory
        saved_plan = scheduler._plans[plan.plan_id]
        assert saved_plan.is_paused is True
        assert saved_plan.status == "paused"

        # 2. Resume execution
        executed_second_run: list[str] = []

        async def resume_executor(node: DAGTaskNode) -> dict[str, Any]:
            executed_second_run.append(node.node_id)
            return {"step": node.node_id}

        result2 = await scheduler.resume_plan(plan.plan_id, node_executor=resume_executor)
        assert result2.status == "completed"
        assert result2.is_paused is False
        assert set(result2.completed_nodes) == {"step_1", "step_2", "step_3"}
        # step_1 was already completed, so it must NOT be re-executed in second run!
        assert executed_second_run == ["step_2", "step_3"]

    asyncio.run(_run())


def test_dag_rest_api_dynamic_endpoints(tmp_path: Path) -> None:
    """Test REST API endpoints for pause, resume, append-nodes, approve, and status."""
    from conflux_weave.runtime.sqlite import SQLiteRuntimeRepository
    from conflux_weave.runtime import LocalArtifactStore
    from conflux_weave.server import create_app

    db_file = tmp_path / "api_dag.sqlite3"
    store = LocalArtifactStore(tmp_path / "artifacts")
    repository = SQLiteRuntimeRepository(db_file, store)
    orchestrator = SimpleNamespace()
    app = create_app(repository, orchestrator)
    client = TestClient(app)

    # 1. Execute an initial DAG plan
    exec_resp = client.post(
        "/api/v1/dag/plans/execute",
        json={
            "plan_id": "api-dyn-plan-1",
            "run_id": "api-dyn-run-1",
            "objective": "REST Dynamic DAG Test",
            "nodes": [
                {"node_id": "N1", "agent_type": "researcher", "objective": "Survey"},
            ],
        },
    )
    assert exec_resp.status_code == 200
    exec_data = exec_resp.json()
    assert exec_data["status"] == "completed"

    # 2. Append nodes to stored plan
    append_resp = client.post(
        "/api/v1/dag/plans/api-dyn-plan-1/append-nodes",
        json={
            "nodes": [
                {
                    "node_id": "N2",
                    "agent_type": "verifier",
                    "objective": "Verify Survey",
                    "depends_on": ["N1"],
                    "requires_approval": True,
                }
            ]
        },
    )
    assert append_resp.status_code == 200
    append_data = append_resp.json()
    assert append_data["appended_node_ids"] == ["N2"]

    # 3. GET plan status
    status_resp = client.get("/api/v1/dag/plans/api-dyn-plan-1/status")
    assert status_resp.status_code == 200
    plan_dict = status_resp.json()
    assert plan_dict["plan_id"] == "api-dyn-plan-1"
    node_ids = [n["node_id"] for n in plan_dict["nodes"]]
    assert "N1" in node_ids and "N2" in node_ids

    # 4. Approve node N2
    approve_resp = client.post(
        "/api/v1/dag/plans/api-dyn-plan-1/nodes/N2/approve",
        json={
            "decision": "approved",
            "approved_by": "qa_lead",
            "comment": "LGTM",
        },
    )
    assert approve_resp.status_code == 200
    approve_data = approve_resp.json()
    assert approve_data["approved"] is True
    assert approve_data["decision"] == "approved"

    # 5. Pause plan endpoint (when plan is not running/ended returns True if found in stored)
    pause_resp = client.post("/api/v1/dag/plans/api-dyn-plan-1/pause")
    assert pause_resp.status_code == 200
    assert pause_resp.json()["is_paused"] is True

    # 6. Resume plan endpoint
    resume_resp = client.post(
        "/api/v1/dag/plans/api-dyn-plan-1/resume",
        json={"max_concurrency": 2},
    )
    assert resume_resp.status_code == 200
    resume_data = resume_resp.json()
    assert resume_data["status"] == "completed"
    assert "N2" in resume_data["completed_nodes"]
