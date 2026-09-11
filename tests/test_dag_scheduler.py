"""Unit and integration tests for Multi-Agent Event Bus and DAG Task Scheduler (P5.4)."""

from __future__ import annotations

import asyncio
from pathlib import Path
import time
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from conflux_weave.orchestrator.dag_scheduler import DAGTaskScheduler
from conflux_weave.orchestrator.event_bus import AsyncAgentEventBus
from conflux_weave.orchestrator.spec import (
    DAGCycleError,
    DAGDependencyError,
    DAGPlan,
    DAGTaskNode,
    DAGTaskStatus,
)


def test_dag_scheduler_validation_and_cycle_detection() -> None:
    scheduler = DAGTaskScheduler()

    # 1. Valid Diamond Plan: A -> B, A -> C, (B, C) -> D
    plan_valid = DAGPlan(
        plan_id="plan-diamond",
        run_id="run-1",
        objective="Literature and Code Synthesis",
        nodes=(
            DAGTaskNode(node_id="A", agent_type="manager", objective="Plan"),
            DAGTaskNode(node_id="B", agent_type="researcher", objective="Survey", depends_on=("A",)),
            DAGTaskNode(node_id="C", agent_type="coder", objective="Audit", depends_on=("A",)),
            DAGTaskNode(node_id="D", agent_type="verifier", objective="Synthesize", depends_on=("B", "C")),
        ),
    )
    ordered = scheduler.validate_plan(plan_valid)
    assert ordered[0] == "A"
    assert set(ordered[1:3]) == {"B", "C"}
    assert ordered[3] == "D"

    # 2. Missing dependency
    plan_missing = DAGPlan(
        plan_id="plan-missing",
        run_id="run-1",
        objective="Missing Dep",
        nodes=(
            DAGTaskNode(node_id="A", agent_type="researcher", objective="Survey", depends_on=("NonExistent",)),
        ),
    )
    with pytest.raises(DAGDependencyError) as exc_info:
        scheduler.validate_plan(plan_missing)
    assert "depends on non-existent task" in str(exc_info.value)

    # 3. Cyclic dependency: A -> B -> C -> A
    plan_cyclic = DAGPlan(
        plan_id="plan-cyclic",
        run_id="run-1",
        objective="Cyclic Tasks",
        nodes=(
            DAGTaskNode(node_id="A", agent_type="researcher", objective="A", depends_on=("C",)),
            DAGTaskNode(node_id="B", agent_type="coder", objective="B", depends_on=("A",)),
            DAGTaskNode(node_id="C", agent_type="verifier", objective="C", depends_on=("B",)),
        ),
    )
    with pytest.raises(DAGCycleError) as exc_info:
        scheduler.validate_plan(plan_cyclic)
    assert "Cyclic dependency detected" in str(exc_info.value)


def test_dag_scheduler_concurrent_execution_and_events(tmp_path: Path) -> None:
    async def _run() -> None:
        db_file = tmp_path / "events.sqlite3"
        event_bus = AsyncAgentEventBus(db_file)
        scheduler = DAGTaskScheduler(event_bus=event_bus, default_concurrency=3)

        # 3 Parallel Branches then Join:
        # Root -> (P1, P2, P3) -> Join
        plan = DAGPlan(
            plan_id="plan-concurrent",
            run_id="run-concurrent-1",
            objective="Multi-Agent Literature Survey",
            nodes=(
                DAGTaskNode(node_id="root", agent_type="manager", objective="Query Decomposition"),
                DAGTaskNode(node_id="p1", agent_type="researcher", objective="Search arXiv", depends_on=("root",)),
                DAGTaskNode(node_id="p2", agent_type="researcher", objective="Search CrossRef", depends_on=("root",)),
                DAGTaskNode(node_id="p3", agent_type="researcher", objective="Search Local Index", depends_on=("root",)),
                DAGTaskNode(node_id="join", agent_type="verifier", objective="Merge Findings", depends_on=("p1", "p2", "p3")),
            ),
        )

        async def custom_executor(node: DAGTaskNode) -> dict[str, str]:
            if node.node_id.startswith("p"):
                await asyncio.sleep(0.05)  # simulate parallel work
            return {"data": f"Output from {node.node_id}"}

        result = await scheduler.execute_plan(plan, node_executor=custom_executor, max_concurrency=3)

        assert result.status == "completed"
        assert set(result.completed_nodes) == {"root", "p1", "p2", "p3", "join"}
        assert result.failed_nodes == ()
        assert result.skipped_nodes == ()
        assert len(result.node_results) == 5
        assert result.node_results["join"]["data"] == "Output from join"

        # Verify persistent events in SQLite
        events = event_bus.list_events("run-concurrent-1")
        assert len(events) >= 12
        event_types = [e.event_type for e in events]
        assert event_types[0] == "plan_started"
        assert "node_started" in event_types
        assert "node_completed" in event_types
        assert event_types[-1] == "plan_completed"

    asyncio.run(_run())


def test_dag_scheduler_failure_isolation_and_transitive_skip() -> None:
    async def _run() -> None:
        scheduler = DAGTaskScheduler()

        # Diamond with failure on B:
        # A -> B (fails) -> D (skipped)
        # A -> C (succeeds)
        plan = DAGPlan(
            plan_id="plan-failure",
            run_id="run-fail-1",
            objective="Fault Isolation Test",
            nodes=(
                DAGTaskNode(node_id="A", agent_type="manager", objective="Start"),
                DAGTaskNode(node_id="B", agent_type="coder", objective="Failing Task", depends_on=("A",)),
                DAGTaskNode(node_id="C", agent_type="researcher", objective="Success Task", depends_on=("A",)),
                DAGTaskNode(node_id="D", agent_type="verifier", objective="Downstream of B", depends_on=("B",)),
            ),
        )

        async def faulty_executor(node: DAGTaskNode) -> dict[str, str]:
            if node.node_id == "B":
                raise RuntimeError("Database connection timed out")
            return {"done": node.node_id}

        result = await scheduler.execute_plan(plan, node_executor=faulty_executor)

        assert result.status == "failed"
        assert set(result.completed_nodes) == {"A", "C"}
        assert result.failed_nodes == ("B",)
        assert result.skipped_nodes == ("D",)
        assert "Database connection timed out" in str(result.error or "")

    asyncio.run(_run())


def test_dag_scheduler_graceful_cancellation() -> None:
    async def _run() -> None:
        scheduler = DAGTaskScheduler()
        cancel_token = asyncio.Event()

        # Long running branch
        plan = DAGPlan(
            plan_id="plan-cancel",
            run_id="run-cancel-1",
            objective="Long Running DAG",
            nodes=(
                DAGTaskNode(node_id="T1", agent_type="manager", objective="Step 1"),
                DAGTaskNode(node_id="T2", agent_type="researcher", objective="Long step 2", depends_on=("T1",)),
                DAGTaskNode(node_id="T3", agent_type="verifier", objective="Pending step 3", depends_on=("T2",)),
            ),
        )

        async def slow_executor(node: DAGTaskNode) -> dict[str, str]:
            if node.node_id == "T1":
                return {"step": 1}
            elif node.node_id == "T2":
                # Signal cancel while T2 is executing
                cancel_token.set()
                await asyncio.sleep(0.1)
                return {"step": 2}
            return {"step": 3}

        result = await scheduler.execute_plan(
            plan,
            node_executor=slow_executor,
            cancellation_token=cancel_token,
        )

        assert result.status == "cancelled"
        assert "T1" in result.completed_nodes
        assert "T3" in result.cancelled_nodes

    asyncio.run(_run())


def test_dag_scheduler_durable_resume() -> None:
    async def _run() -> None:
        scheduler = DAGTaskScheduler()

        # Resumed plan where node A was already completed in previous run
        plan = DAGPlan(
            plan_id="plan-resume",
            run_id="run-resume-1",
            objective="Resume execution",
            nodes=(
                DAGTaskNode(
                    node_id="A",
                    agent_type="manager",
                    objective="Already done",
                    status=DAGTaskStatus.COMPLETED,
                    result={"precomputed": True},
                ),
                DAGTaskNode(
                    node_id="B",
                    agent_type="researcher",
                    objective="Remaining work",
                    depends_on=("A",),
                ),
            ),
        )

        executed_nodes: list[str] = []

        async def tracker_executor(node: DAGTaskNode) -> dict[str, str]:
            executed_nodes.append(node.node_id)
            return {"result": f"Executed {node.node_id}"}

        result = await scheduler.execute_plan(plan, node_executor=tracker_executor)

        assert result.status == "completed"
        assert set(result.completed_nodes) == {"A", "B"}
        # Node A was skipped, only B was executed
        assert executed_nodes == ["B"]

    asyncio.run(_run())


def test_dag_rest_api_integration(tmp_path: Path) -> None:
    from conflux_weave.runtime.sqlite import SQLiteRuntimeRepository
    from conflux_weave.runtime import LocalArtifactStore
    from conflux_weave.server import create_app

    db_file = tmp_path / "api_test.sqlite3"
    store = LocalArtifactStore(tmp_path / "artifacts")
    repository = SQLiteRuntimeRepository(db_file, store)
    orchestrator = SimpleNamespace()
    app = create_app(repository, orchestrator)
    client = TestClient(app)

    # 1. POST /api/v1/dag/plans/validate (Valid)
    valid_resp = client.post(
        "/api/v1/dag/plans/validate",
        json={
            "plan_id": "api-plan-1",
            "run_id": "api-run-1",
            "objective": "API Validation Test",
            "nodes": [
                {"node_id": "N1", "agent_type": "researcher", "objective": "Step 1"},
                {"node_id": "N2", "agent_type": "verifier", "objective": "Step 2", "depends_on": ["N1"]},
            ],
        },
    )
    assert valid_resp.status_code == 200
    valid_data = valid_resp.json()
    assert valid_data["valid"] is True
    assert valid_data["node_count"] == 2
    assert valid_data["topological_order"] == ["N1", "N2"]

    # 2. POST /api/v1/dag/plans/validate (Invalid Cycle)
    invalid_resp = client.post(
        "/api/v1/dag/plans/validate",
        json={
            "plan_id": "api-plan-cycle",
            "run_id": "api-run-1",
            "objective": "Cycle Test",
            "nodes": [
                {"node_id": "C1", "objective": "C1", "depends_on": ["C2"]},
                {"node_id": "C2", "objective": "C2", "depends_on": ["C1"]},
            ],
        },
    )
    assert invalid_resp.status_code == 200
    invalid_data = invalid_resp.json()
    assert invalid_data["valid"] is False
    assert "Cyclic dependency" in invalid_data["error"]

    # 3. POST /api/v1/dag/plans/execute (Execute)
    exec_resp = client.post(
        "/api/v1/dag/plans/execute",
        json={
            "plan_id": "api-exec-1",
            "run_id": "api-run-exec",
            "objective": "API Execution Test",
            "max_concurrency": 2,
            "nodes": [
                {"node_id": "TaskA", "agent_type": "manager", "objective": "Decompose"},
                {"node_id": "TaskB", "agent_type": "researcher", "objective": "Collect", "depends_on": ["TaskA"]},
            ],
        },
    )
    assert exec_resp.status_code == 200
    exec_data = exec_resp.json()
    assert exec_data["status"] == "completed"
    assert set(exec_data["completed_nodes"]) == {"TaskA", "TaskB"}
    assert exec_data["events_count"] >= 6

    # 4. GET /api/v1/runs/{run_id}/agent-events
    events_resp = client.get("/api/v1/runs/api-run-exec/agent-events")
    assert events_resp.status_code == 200
    events_data = events_resp.json()
    assert events_data["total"] >= 6
    types = [e["event_type"] for e in events_data["items"]]
    assert "plan_started" in types
    assert "node_completed" in types
    assert "plan_completed" in types
