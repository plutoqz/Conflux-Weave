"""DAG Task Scheduler for concurrent multi-agent plan execution and lifecycle control (P5.4)."""

from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable
import time
from typing import Any

from conflux_weave.orchestrator.event_bus import AsyncAgentEventBus
from conflux_weave.orchestrator.spec import (
    DAGCycleError,
    DAGDependencyError,
    DAGExecutionResult,
    DAGPlan,
    DAGTaskNode,
    DAGTaskStatus,
    _utc_now,
)


class DAGTaskScheduler:
    """Schedules and executes directed acyclic graph (DAG) task plans with bounded concurrency."""

    def __init__(
        self,
        event_bus: AsyncAgentEventBus | None = None,
        default_concurrency: int = 3,
    ) -> None:
        self.event_bus = event_bus or AsyncAgentEventBus()
        self.default_concurrency = max(default_concurrency, 1)

    def validate_plan(self, plan: DAGPlan) -> list[str]:
        """Validate dependencies and cycle-free topology. Returns topographically ordered node IDs."""
        node_map = {n.node_id: n for n in plan.nodes}

        # 1. Dependency existence check
        for node in plan.nodes:
            for dep in node.depends_on:
                if dep not in node_map:
                    raise DAGDependencyError(
                        f"Task '{node.node_id}' depends on non-existent task '{dep}'"
                    )

        # 2. Cycle detection via Kahn's algorithm
        in_degree: dict[str, int] = {n.node_id: 0 for n in plan.nodes}
        adj_list: dict[str, list[str]] = defaultdict(list)

        for node in plan.nodes:
            in_degree[node.node_id] = len(node.depends_on)
            for dep in node.depends_on:
                adj_list[dep].append(node.node_id)

        queue: deque[str] = deque([nid for nid, deg in in_degree.items() if deg == 0])
        ordered: list[str] = []

        while queue:
            curr = queue.popleft()
            ordered.append(curr)
            for neighbor in adj_list[curr]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        if len(ordered) != len(plan.nodes):
            cycle_nodes = [nid for nid, deg in in_degree.items() if deg > 0]
            raise DAGCycleError(
                f"Cyclic dependency detected among nodes: {cycle_nodes}"
            )

        return ordered

    def compute_ready_nodes(
        self,
        nodes: dict[str, DAGTaskNode],
        completed_ids: set[str],
    ) -> list[DAGTaskNode]:
        """Find pending nodes whose all upstream dependencies are satisfied."""
        ready: list[DAGTaskNode] = []
        for node in nodes.values():
            if node.status == DAGTaskStatus.PENDING:
                if all(dep in completed_ids for dep in node.depends_on):
                    ready.append(node)
        return ready

    async def execute_plan(
        self,
        plan: DAGPlan,
        node_executor: Callable[[DAGTaskNode], Awaitable[dict[str, Any]]] | None = None,
        max_concurrency: int | None = None,
        cancellation_token: asyncio.Event | None = None,
    ) -> DAGExecutionResult:
        """Execute the DAG plan concurrently with topological dependency gating and graceful cancellation."""
        start_time = time.monotonic()
        concurrency = max_concurrency or self.default_concurrency
        semaphore = asyncio.Semaphore(concurrency)

        # Validate DAG upfront
        self.validate_plan(plan)

        # Initialize tracking dictionaries
        node_states: dict[str, DAGTaskNode] = {n.node_id: n for n in plan.nodes}
        completed_ids: set[str] = {
            n.node_id for n in plan.nodes if n.status == DAGTaskStatus.COMPLETED
        }
        failed_ids: set[str] = set()
        cancelled_ids: set[str] = set()
        skipped_ids: set[str] = set()
        node_results: dict[str, Any] = {
            n.node_id: n.result for n in plan.nodes if n.result is not None
        }

        self.event_bus.publish(
            run_id=plan.run_id,
            agent_id="dag_scheduler",
            event_type="plan_started",
            payload={"plan_id": plan.plan_id, "nodes_total": len(plan.nodes)},
        )

        running_tasks: dict[str, asyncio.Task[tuple[str, bool, Any, str | None]]] = {}

        async def _run_node(node: DAGTaskNode) -> tuple[str, bool, Any, str | None]:
            async with semaphore:
                try:
                    if node_executor is not None:
                        result = await node_executor(node)
                    else:
                        # Default mock execution
                        await asyncio.sleep(0.01)
                        result = {
                            "summary": f"Completed objective: {node.objective}",
                            "agent_type": node.agent_type,
                        }
                    return node.node_id, True, result, None
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    return node.node_id, False, None, str(exc)

        # Execution loop
        while len(completed_ids) + len(failed_ids) + len(skipped_ids) + len(cancelled_ids) < len(plan.nodes):
            # 1. Check cancellation token
            if cancellation_token is not None and cancellation_token.is_set():
                # Cancel all running tasks
                for task in running_tasks.values():
                    task.cancel()
                # Mark all uncompleted as cancelled
                for nid, n in node_states.items():
                    if n.status in (DAGTaskStatus.PENDING, DAGTaskStatus.RUNNING, DAGTaskStatus.READY):
                        node_states[nid] = DAGTaskNode(
                            node_id=n.node_id,
                            agent_type=n.agent_type,
                            objective=n.objective,
                            depends_on=n.depends_on,
                            skill_id=n.skill_id,
                            input_payload=n.input_payload,
                            budget=n.budget,
                            status=DAGTaskStatus.CANCELLED,
                            error="Cancelled by user signal",
                            started_at=n.started_at,
                            completed_at=_utc_now(),
                        )
                        cancelled_ids.add(nid)

                self.event_bus.publish(
                    run_id=plan.run_id,
                    agent_id="dag_scheduler",
                    event_type="plan_cancelled",
                    payload={"plan_id": plan.plan_id, "cancelled_count": len(cancelled_ids)},
                )
                break

            # 2. Launch newly ready nodes
            ready_nodes = self.compute_ready_nodes(node_states, completed_ids)
            for node in ready_nodes:
                nid = node.node_id
                now = _utc_now()
                node_states[nid] = DAGTaskNode(
                    node_id=node.node_id,
                    agent_type=node.agent_type,
                    objective=node.objective,
                    depends_on=node.depends_on,
                    skill_id=node.skill_id,
                    input_payload=node.input_payload,
                    budget=node.budget,
                    status=DAGTaskStatus.RUNNING,
                    started_at=now,
                )
                self.event_bus.publish(
                    run_id=plan.run_id,
                    agent_id=node.agent_type,
                    event_type="node_started",
                    payload={"node_id": nid, "objective": node.objective},
                )
                running_tasks[nid] = asyncio.create_task(_run_node(node_states[nid]))

            # 3. Wait for at least one running task to complete
            if not running_tasks:
                # No tasks are running and no ready tasks -> deadlocked or downstream skipped
                unresolved = [
                    nid for nid, n in node_states.items()
                    if n.status == DAGTaskStatus.PENDING
                ]
                for nid in unresolved:
                    node_states[nid] = DAGTaskNode(
                        node_id=nid,
                        agent_type=node_states[nid].agent_type,
                        objective=node_states[nid].objective,
                        depends_on=node_states[nid].depends_on,
                        status=DAGTaskStatus.SKIPPED,
                        error="Upstream dependency failed or unresolved",
                    )
                    skipped_ids.add(nid)
                break

            done, _ = await asyncio.wait(
                running_tasks.values(),
                return_when=asyncio.FIRST_COMPLETED,
            )

            for task in done:
                # Find which node this task belonged to
                completed_nid = next(
                    nid for nid, t in running_tasks.items() if t == task
                )
                running_tasks.pop(completed_nid)

                try:
                    nid, success, result, error = task.result()
                    curr_node = node_states[nid]
                    now = _utc_now()

                    if success:
                        node_states[nid] = DAGTaskNode(
                            node_id=curr_node.node_id,
                            agent_type=curr_node.agent_type,
                            objective=curr_node.objective,
                            depends_on=curr_node.depends_on,
                            skill_id=curr_node.skill_id,
                            input_payload=curr_node.input_payload,
                            budget=curr_node.budget,
                            status=DAGTaskStatus.COMPLETED,
                            result=result,
                            started_at=curr_node.started_at,
                            completed_at=now,
                        )
                        completed_ids.add(nid)
                        node_results[nid] = result
                        self.event_bus.publish(
                            run_id=plan.run_id,
                            agent_id=curr_node.agent_type,
                            event_type="node_completed",
                            payload={"node_id": nid, "result": result},
                        )
                    else:
                        node_states[nid] = DAGTaskNode(
                            node_id=curr_node.node_id,
                            agent_type=curr_node.agent_type,
                            objective=curr_node.objective,
                            depends_on=curr_node.depends_on,
                            skill_id=curr_node.skill_id,
                            input_payload=curr_node.input_payload,
                            budget=curr_node.budget,
                            status=DAGTaskStatus.FAILED,
                            error=error,
                            started_at=curr_node.started_at,
                            completed_at=now,
                        )
                        failed_ids.add(nid)
                        self.event_bus.publish(
                            run_id=plan.run_id,
                            agent_id=curr_node.agent_type,
                            event_type="node_failed",
                            payload={"node_id": nid, "error": error},
                        )
                        # Mark transitive dependencies as SKIPPED
                        self._skip_downstream(nid, node_states, skipped_ids, plan.run_id)

                except asyncio.CancelledError:
                    cancelled_ids.add(completed_nid)

        duration = time.monotonic() - start_time

        if cancelled_ids:
            status = "cancelled"
        elif failed_ids:
            status = "failed"
            self.event_bus.publish(
                run_id=plan.run_id,
                agent_id="dag_scheduler",
                event_type="plan_failed",
                payload={"plan_id": plan.plan_id, "failed_count": len(failed_ids)},
            )
        else:
            status = "completed"
            self.event_bus.publish(
                run_id=plan.run_id,
                agent_id="dag_scheduler",
                event_type="plan_completed",
                payload={"plan_id": plan.plan_id, "duration_seconds": duration},
            )

        events = self.event_bus.list_events(plan.run_id)

        failed_errors = [
            f"{nid}: {node_states[nid].error}" for nid in sorted(failed_ids) if node_states[nid].error
        ]
        error_msg = "; ".join(failed_errors) if failed_errors else None

        return DAGExecutionResult(
            plan_id=plan.plan_id,
            run_id=plan.run_id,
            status=status,
            completed_nodes=tuple(sorted(completed_ids)),
            failed_nodes=tuple(sorted(failed_ids)),
            cancelled_nodes=tuple(sorted(cancelled_ids)),
            skipped_nodes=tuple(sorted(skipped_ids)),
            node_results=node_results,
            events_count=len(events),
            duration_seconds=round(duration, 3),
            error=error_msg,
        )

    def _skip_downstream(
        self,
        failed_id: str,
        node_states: dict[str, DAGTaskNode],
        skipped_ids: set[str],
        run_id: str,
    ) -> None:
        """Transitively mark all downstream nodes depending on failed_id as SKIPPED."""
        to_check = [failed_id]
        while to_check:
            parent = to_check.pop(0)
            for nid, node in node_states.items():
                if parent in node.depends_on and node.status == DAGTaskStatus.PENDING:
                    node_states[nid] = DAGTaskNode(
                        node_id=node.node_id,
                        agent_type=node.agent_type,
                        objective=node.objective,
                        depends_on=node.depends_on,
                        skill_id=node.skill_id,
                        input_payload=node.input_payload,
                        budget=node.budget,
                        status=DAGTaskStatus.SKIPPED,
                        error=f"Upstream node '{parent}' failed",
                        completed_at=_utc_now(),
                    )
                    skipped_ids.add(nid)
                    self.event_bus.publish(
                        run_id=run_id,
                        agent_id=node.agent_type,
                        event_type="node_skipped",
                        payload={"node_id": nid, "cause": f"Upstream '{parent}' failed"},
                    )
                    to_check.append(nid)
