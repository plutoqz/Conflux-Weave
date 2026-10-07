"""DAG Task Scheduler for concurrent multi-agent plan execution and lifecycle control (P5.4 + Gate C3)."""

from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable
import json
from pathlib import Path
import sqlite3
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


def _json_default(obj: Any) -> Any:
    if hasattr(obj, "to_dict"):
        return obj.to_dict()
    if hasattr(obj, "__dict__"):
        return obj.__dict__
    return str(obj)


def evaluate_condition(condition: Any, node_results: dict[str, Any]) -> bool:
    """Evaluate a conditional branching rule against upstream node execution outputs."""
    if condition is None:
        return True
    if isinstance(condition, bool):
        return condition
    if isinstance(condition, dict):
        node_id = condition.get("node")
        if node_id is None:
            return True
        node_res = node_results.get(node_id)
        if node_res is None:
            return False

        target_val = node_res
        field_name = condition.get("field")
        if field_name and isinstance(node_res, dict):
            target_val = node_res.get(field_name)

        if "equals" in condition:
            return target_val == condition["equals"]

        op = str(condition.get("operator", condition.get("op", "=="))).lower()
        expected = condition.get("value")

        if op in ("==", "eq"):
            return target_val == expected
        elif op in ("!=", "ne"):
            return target_val != expected
        elif op in (">", "gt"):
            return target_val > expected if (target_val is not None and expected is not None) else False
        elif op in (">=", "gte"):
            return target_val >= expected if (target_val is not None and expected is not None) else False
        elif op in ("<", "lt"):
            return target_val < expected if (target_val is not None and expected is not None) else False
        elif op in ("<=", "lte"):
            return target_val <= expected if (target_val is not None and expected is not None) else False
        elif op in ("in",):
            return target_val in expected if expected is not None else False
        elif op in ("contains",):
            return expected in target_val if target_val is not None else False
        return bool(target_val)
    return True


class DAGTaskScheduler:
    """Schedules and executes directed acyclic graph (DAG) task plans with bounded concurrency and Gate C3 dynamics."""

    def __init__(
        self,
        event_bus: AsyncAgentEventBus | None = None,
        default_concurrency: int = 3,
        db_path: Path | str | None = None,
    ) -> None:
        self.event_bus = event_bus or AsyncAgentEventBus()
        self.default_concurrency = max(default_concurrency, 1)
        self.db_path = Path(db_path) if db_path is not None else None

        self._plans: dict[str, DAGPlan] = {}
        self._pause_tokens: dict[str, asyncio.Event] = {}
        self._approval_waiters: dict[tuple[str, str], asyncio.Future[tuple[bool, str]]] = {}
        self._wake_events: dict[str, asyncio.Event] = {}
        self._active_executions: dict[str, dict[str, Any]] = {}

        if self.db_path is not None:
            self._ensure_tables()

    def _ensure_tables(self) -> None:
        if self.db_path is None:
            return
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS dag_plans (
                    plan_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    objective TEXT NOT NULL,
                    status TEXT NOT NULL,
                    is_paused INTEGER NOT NULL DEFAULT 0,
                    version INTEGER NOT NULL DEFAULT 1,
                    nodes_json TEXT NOT NULL,
                    results_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

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
        skipped_ids: set[str] | None = None,
        failed_ids: set[str] | None = None,
    ) -> list[DAGTaskNode]:
        """Find pending nodes whose upstream dependencies are satisfied based on join policy."""
        skipped_ids = skipped_ids or set()
        failed_ids = failed_ids or set()
        ready: list[DAGTaskNode] = []

        for node in nodes.values():
            if node.status not in (DAGTaskStatus.PENDING, DAGTaskStatus.READY):
                continue

            if not node.depends_on:
                ready.append(node)
                continue

            policy = getattr(node, "join_policy", "all") or "all"
            if policy in ("any", "first_success"):
                if any(dep in completed_ids for dep in node.depends_on):
                    ready.append(node)
            else:
                # Default "all": all dependencies resolved in completed or skipped
                if all(dep in completed_ids or dep in skipped_ids for dep in node.depends_on):
                    ready.append(node)

        return ready

    def append_nodes(self, plan_id: str, new_nodes: list[DAGTaskNode]) -> list[str]:
        """Dynamically append new task nodes to an existing or running plan (Gate C3)."""
        appended_ids: list[str] = []
        now = _utc_now()

        execution = self._active_executions.get(plan_id)
        if execution is not None:
            node_states: dict[str, DAGTaskNode] = execution["node_states"]
            for node in new_nodes:
                if node.node_id in node_states:
                    continue
                # Dependency validation against active node pool
                for dep in node.depends_on:
                    if dep not in node_states:
                        raise DAGDependencyError(
                            f"Appended task '{node.node_id}' depends on non-existent task '{dep}'"
                        )
                node_states[node.node_id] = node
                appended_ids.append(node.node_id)

            if appended_ids:
                wake_event = self._wake_events.get(plan_id)
                if wake_event:
                    wake_event.set()
                self.event_bus.publish(
                    run_id=execution["plan"].run_id,
                    agent_id="dag_scheduler",
                    event_type="nodes_appended",
                    payload={"plan_id": plan_id, "new_node_ids": appended_ids},
                )
            return appended_ids

        # If not active, update stored plan
        plan = self._plans.get(plan_id)
        if plan is not None:
            existing_map = {n.node_id: n for n in plan.nodes}
            updated_nodes = list(plan.nodes)
            for node in new_nodes:
                if node.node_id not in existing_map:
                    for dep in node.depends_on:
                        if dep not in existing_map:
                            raise DAGDependencyError(
                                f"Appended task '{node.node_id}' depends on non-existent task '{dep}'"
                            )
                    updated_nodes.append(node)
                    existing_map[node.node_id] = node
                    appended_ids.append(node.node_id)
            updated_plan = DAGPlan(
                plan_id=plan.plan_id,
                run_id=plan.run_id,
                objective=plan.objective,
                nodes=tuple(updated_nodes),
                created_at=plan.created_at,
                status=plan.status,
                is_paused=plan.is_paused,
                version=plan.version + 1,
            )
            self._plans[plan_id] = updated_plan
            self._persist_plan(updated_plan)
        return appended_ids

    def submit_approval(
        self,
        plan_id: str,
        node_id: str,
        decision: str,  # "approved" | "rejected"
        approved_by: str = "user",
        comment: str = "",
    ) -> bool:
        """Submit a human-in-the-loop approval decision for a node waiting approval (Gate C3)."""
        is_approved = decision.lower() in ("approved", "allow", "yes", "true")
        key = (plan_id, node_id)

        execution = self._active_executions.get(plan_id)
        if execution is not None:
            node_states = execution["node_states"]
            node = node_states.get(node_id)
            if node is not None:
                now = _utc_now()
                decision_dict = {
                    "decision": "approved" if is_approved else "rejected",
                    "approved_by": approved_by,
                    "comment": comment,
                    "at": now,
                }
                if is_approved:
                    node_states[node_id] = DAGTaskNode(
                        node_id=node.node_id,
                        agent_type=node.agent_type,
                        objective=node.objective,
                        depends_on=node.depends_on,
                        skill_id=node.skill_id,
                        input_payload=node.input_payload,
                        budget=node.budget,
                        status=DAGTaskStatus.READY,
                        condition=node.condition,
                        requires_approval=False,
                        approval_status="approved",
                        approval_decision=decision_dict,
                        join_policy=node.join_policy,
                    )
                    execution["waiting_approval_ids"].discard(node_id)
                    self.event_bus.publish(
                        run_id=execution["plan"].run_id,
                        agent_id=node.agent_type,
                        event_type="approval_granted",
                        payload={"node_id": node_id, "approved_by": approved_by, "comment": comment},
                    )
                else:
                    node_states[node_id] = DAGTaskNode(
                        node_id=node.node_id,
                        agent_type=node.agent_type,
                        objective=node.objective,
                        depends_on=node.depends_on,
                        skill_id=node.skill_id,
                        input_payload=node.input_payload,
                        budget=node.budget,
                        status=DAGTaskStatus.FAILED,
                        error=f"Rejected by {approved_by}: {comment or 'no reason provided'}",
                        completed_at=now,
                        condition=node.condition,
                        requires_approval=False,
                        approval_status="rejected",
                        approval_decision=decision_dict,
                        join_policy=node.join_policy,
                    )
                    execution["waiting_approval_ids"].discard(node_id)
                    execution["failed_ids"].add(node_id)
                    self.event_bus.publish(
                        run_id=execution["plan"].run_id,
                        agent_id=node.agent_type,
                        event_type="approval_rejected",
                        payload={"node_id": node_id, "approved_by": approved_by, "comment": comment},
                    )
                    self._skip_downstream(node_id, node_states, execution["skipped_ids"], execution["plan"].run_id)

                wake_event = self._wake_events.get(plan_id)
                if wake_event:
                    wake_event.set()
        else:
            plan = self._plans.get(plan_id)
            if plan is not None:
                updated_nodes = []
                found = False
                now = _utc_now()
                decision_dict = {
                    "decision": "approved" if is_approved else "rejected",
                    "approved_by": approved_by,
                    "comment": comment,
                    "at": now,
                }
                for node in plan.nodes:
                    if node.node_id == node_id:
                        found = True
                        if is_approved:
                            updated_nodes.append(
                                DAGTaskNode(
                                    node_id=node.node_id,
                                    agent_type=node.agent_type,
                                    objective=node.objective,
                                    depends_on=node.depends_on,
                                    skill_id=node.skill_id,
                                    input_payload=node.input_payload,
                                    budget=node.budget,
                                    status=DAGTaskStatus.READY,
                                    condition=node.condition,
                                    requires_approval=False,
                                    approval_status="approved",
                                    approval_decision=decision_dict,
                                    join_policy=node.join_policy,
                                )
                            )
                        else:
                            updated_nodes.append(
                                DAGTaskNode(
                                    node_id=node.node_id,
                                    agent_type=node.agent_type,
                                    objective=node.objective,
                                    depends_on=node.depends_on,
                                    skill_id=node.skill_id,
                                    input_payload=node.input_payload,
                                    budget=node.budget,
                                    status=DAGTaskStatus.FAILED,
                                    error=f"Rejected by {approved_by}: {comment or 'no reason provided'}",
                                    completed_at=now,
                                    condition=node.condition,
                                    requires_approval=False,
                                    approval_status="rejected",
                                    approval_decision=decision_dict,
                                    join_policy=node.join_policy,
                                )
                            )
                    else:
                        updated_nodes.append(node)
                if found:
                    updated_plan = DAGPlan(
                        plan_id=plan.plan_id,
                        run_id=plan.run_id,
                        objective=plan.objective,
                        nodes=tuple(updated_nodes),
                        created_at=plan.created_at,
                        status=plan.status,
                        is_paused=plan.is_paused,
                        version=plan.version + 1,
                    )
                    self._plans[plan_id] = updated_plan
                    self._persist_plan(updated_plan)
                    return True

        waiter = self._approval_waiters.pop(key, None)
        if waiter and not waiter.done():
            waiter.set_result((is_approved, comment))
            return True
        return execution is not None

    def pause_plan(self, plan_id: str) -> bool:
        """Pause a running plan execution safely after in-flight steps settle (Gate C3)."""
        pause_token = self._pause_tokens.get(plan_id)
        if pause_token is not None:
            pause_token.set()
            wake_event = self._wake_events.get(plan_id)
            if wake_event:
                wake_event.set()
            return True

        plan = self._plans.get(plan_id)
        if plan is not None:
            paused_plan = DAGPlan(
                plan_id=plan.plan_id,
                run_id=plan.run_id,
                objective=plan.objective,
                nodes=plan.nodes,
                created_at=plan.created_at,
                status="paused",
                is_paused=True,
                version=plan.version,
            )
            self._plans[plan_id] = paused_plan
            self._persist_plan(paused_plan)
            return True
        return False

    async def execute_plan(
        self,
        plan: DAGPlan,
        node_executor: Callable[[DAGTaskNode], Awaitable[dict[str, Any]]] | None = None,
        max_concurrency: int | None = None,
        cancellation_token: asyncio.Event | None = None,
        pause_token: asyncio.Event | None = None,
    ) -> DAGExecutionResult:
        """Execute the DAG plan concurrently with topological gating, conditional branching, approvals, and pause/resume."""
        start_time = time.monotonic()
        concurrency = max_concurrency or self.default_concurrency
        semaphore = asyncio.Semaphore(concurrency)

        # Validate DAG upfront
        self.validate_plan(plan)

        # Pause and wake setup bound to current running event loop
        if pause_token is not None:
            plan_pause_event = pause_token
        else:
            existing_pause = self._pause_tokens.get(plan.plan_id)
            plan_pause_event = asyncio.Event()
            if existing_pause is not None and existing_pause.is_set():
                plan_pause_event.set()
        self._pause_tokens[plan.plan_id] = plan_pause_event

        wake_event = asyncio.Event()
        self._wake_events[plan.plan_id] = wake_event

        # Initialize tracking state
        node_states: dict[str, DAGTaskNode] = {n.node_id: n for n in plan.nodes}
        completed_ids: set[str] = {
            n.node_id for n in plan.nodes if n.status == DAGTaskStatus.COMPLETED
        }
        failed_ids: set[str] = {
            n.node_id for n in plan.nodes if n.status == DAGTaskStatus.FAILED
        }
        cancelled_ids: set[str] = {
            n.node_id for n in plan.nodes if n.status == DAGTaskStatus.CANCELLED
        }
        skipped_ids: set[str] = {
            n.node_id for n in plan.nodes if n.status == DAGTaskStatus.SKIPPED
        }
        waiting_approval_ids: set[str] = {
            n.node_id for n in plan.nodes if n.status == DAGTaskStatus.WAITING_APPROVAL
        }
        node_results: dict[str, Any] = {
            n.node_id: n.result for n in plan.nodes if n.result is not None
        }

        # Track execution context
        self._active_executions[plan.plan_id] = {
            "plan": plan,
            "node_states": node_states,
            "completed_ids": completed_ids,
            "failed_ids": failed_ids,
            "cancelled_ids": cancelled_ids,
            "skipped_ids": skipped_ids,
            "waiting_approval_ids": waiting_approval_ids,
            "node_results": node_results,
        }
        self._plans[plan.plan_id] = plan

        self.event_bus.publish(
            run_id=plan.run_id,
            agent_id="dag_scheduler",
            event_type="plan_started",
            payload={"plan_id": plan.plan_id, "nodes_total": len(node_states)},
        )

        running_tasks: dict[str, asyncio.Task[tuple[str, bool, Any, str | None]]] = {}

        async def _run_node(node: DAGTaskNode) -> tuple[str, bool, Any, str | None]:
            async with semaphore:
                try:
                    if node_executor is not None:
                        result = await node_executor(node)
                    else:
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

        # Main execution loop
        is_paused = False
        while True:
            # Check completion criteria
            terminal_count = len(completed_ids) + len(failed_ids) + len(skipped_ids) + len(cancelled_ids)
            if terminal_count >= len(node_states):
                break

            # 1. Cancellation check
            if cancellation_token is not None and cancellation_token.is_set():
                for task in running_tasks.values():
                    task.cancel()
                for nid, n in node_states.items():
                    if n.status in (DAGTaskStatus.PENDING, DAGTaskStatus.RUNNING, DAGTaskStatus.READY, DAGTaskStatus.WAITING_APPROVAL):
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

            # 2. Pause check
            if plan_pause_event.is_set():
                if not running_tasks:
                    is_paused = True
                    self.event_bus.publish(
                        run_id=plan.run_id,
                        agent_id="dag_scheduler",
                        event_type="plan_paused",
                        payload={"plan_id": plan.plan_id, "pending_count": len(node_states) - terminal_count},
                    )
                    break

            # 3. Schedule newly ready nodes (if not paused)
            if not plan_pause_event.is_set():
                ready_candidates = self.compute_ready_nodes(node_states, completed_ids, skipped_ids, failed_ids)
                for node in ready_candidates:
                    nid = node.node_id

                    # 3a. Conditional Branching Evaluation
                    if node.condition is not None:
                        is_met = evaluate_condition(node.condition, node_results)
                        if not is_met:
                            node_states[nid] = DAGTaskNode(
                                node_id=node.node_id,
                                agent_type=node.agent_type,
                                objective=node.objective,
                                depends_on=node.depends_on,
                                skill_id=node.skill_id,
                                input_payload=node.input_payload,
                                budget=node.budget,
                                status=DAGTaskStatus.SKIPPED,
                                error="Condition evaluated to false",
                                completed_at=_utc_now(),
                                condition=node.condition,
                                join_policy=node.join_policy,
                            )
                            skipped_ids.add(nid)
                            self.event_bus.publish(
                                run_id=plan.run_id,
                                agent_id=node.agent_type,
                                event_type="node_skipped",
                                payload={"node_id": nid, "cause": "condition_false"},
                            )
                            self._skip_downstream(nid, node_states, skipped_ids, plan.run_id)
                            continue

                    # 3b. Human Approval Node Gating
                    if node.requires_approval and node.approval_status != "approved":
                        if node.status != DAGTaskStatus.WAITING_APPROVAL:
                            node_states[nid] = DAGTaskNode(
                                node_id=node.node_id,
                                agent_type=node.agent_type,
                                objective=node.objective,
                                depends_on=node.depends_on,
                                skill_id=node.skill_id,
                                input_payload=node.input_payload,
                                budget=node.budget,
                                status=DAGTaskStatus.WAITING_APPROVAL,
                                condition=node.condition,
                                join_policy=node.join_policy,
                                requires_approval=True,
                                approval_status="pending",
                            )
                            waiting_approval_ids.add(nid)
                            self.event_bus.publish(
                                run_id=plan.run_id,
                                agent_id=node.agent_type,
                                event_type="approval_requested",
                                payload={"node_id": nid, "objective": node.objective, "input_payload": node.input_payload},
                            )
                        continue

                    # 3c. Subtask Convergence / Join Input Preparation
                    upstream_data = {
                        dep: node_results.get(dep) for dep in node.depends_on if dep in node_results
                    }
                    merged_payload = dict(node.input_payload)
                    if upstream_data:
                        merged_payload["upstream_results"] = upstream_data

                    now = _utc_now()
                    node_states[nid] = DAGTaskNode(
                        node_id=node.node_id,
                        agent_type=node.agent_type,
                        objective=node.objective,
                        depends_on=node.depends_on,
                        skill_id=node.skill_id,
                        input_payload=merged_payload,
                        budget=node.budget,
                        status=DAGTaskStatus.RUNNING,
                        started_at=now,
                        condition=node.condition,
                        join_policy=node.join_policy,
                        requires_approval=node.requires_approval,
                        approval_status=node.approval_status,
                        approval_decision=node.approval_decision,
                    )
                    waiting_approval_ids.discard(nid)
                    self.event_bus.publish(
                        run_id=plan.run_id,
                        agent_id=node.agent_type,
                        event_type="node_started",
                        payload={"node_id": nid, "objective": node.objective},
                    )
                    running_tasks[nid] = asyncio.create_task(_run_node(node_states[nid]))

            # 4. Wait for tasks to complete or external signals
            if not running_tasks:
                if waiting_approval_ids:
                    # Waiting for human approval - wait for wake_event or exit if pause requested
                    if plan_pause_event.is_set():
                        is_paused = True
                        break
                    wake_event.clear()
                    try:
                        await asyncio.wait_for(wake_event.wait(), timeout=0.1)
                    except asyncio.TimeoutError:
                        pass
                    continue

                # No running tasks, no waiting approvals -> check unresolved nodes
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

            # Await running tasks or wake signals
            wake_task = asyncio.create_task(wake_event.wait())
            wait_set = set(running_tasks.values())
            wait_set.add(wake_task)

            done, _ = await asyncio.wait(
                wait_set,
                return_when=asyncio.FIRST_COMPLETED,
            )

            if wake_task in done:
                wake_event.clear()

            for task in done:
                if task == wake_task:
                    continue

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
                            condition=curr_node.condition,
                            join_policy=curr_node.join_policy,
                        )
                        completed_ids.add(nid)
                        node_results[nid] = result
                        self.event_bus.publish(
                            run_id=plan.run_id,
                            agent_id=curr_node.agent_type,
                            event_type="node_completed",
                            payload={"node_id": nid, "result": result},
                        )

                        # Dynamic Subtask Fan-Out Appending
                        if isinstance(result, dict) and ("__spawn_nodes__" in result or "__fan_out__" in result):
                            spawn_list = result.get("__spawn_nodes__") or result.get("__fan_out__")
                            if isinstance(spawn_list, list):
                                for item in spawn_list:
                                    if isinstance(item, DAGTaskNode):
                                        new_n = item
                                    elif isinstance(item, dict):
                                        new_n = DAGTaskNode.from_dict(item)
                                    else:
                                        continue
                                    if new_n.node_id not in node_states:
                                        node_states[new_n.node_id] = new_n
                                        self.event_bus.publish(
                                            run_id=plan.run_id,
                                            agent_id="dag_scheduler",
                                            event_type="node_appended",
                                            payload={"node_id": new_n.node_id, "spawned_by": nid},
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
                            condition=curr_node.condition,
                            join_policy=curr_node.join_policy,
                        )
                        failed_ids.add(nid)
                        self.event_bus.publish(
                            run_id=plan.run_id,
                            agent_id=curr_node.agent_type,
                            event_type="node_failed",
                            payload={"node_id": nid, "error": error},
                        )
                        self._skip_downstream(nid, node_states, skipped_ids, plan.run_id)

                except asyncio.CancelledError:
                    cancelled_ids.add(completed_nid)

        duration = time.monotonic() - start_time

        if is_paused:
            status = "paused"
        elif cancelled_ids:
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

        updated_plan = DAGPlan(
            plan_id=plan.plan_id,
            run_id=plan.run_id,
            objective=plan.objective,
            nodes=tuple(node_states.values()),
            created_at=plan.created_at,
            status=status,
            is_paused=is_paused,
            version=plan.version,
        )
        self._plans[plan.plan_id] = updated_plan
        self._persist_plan(updated_plan, node_results)

        # Cleanup active execution if done
        if not is_paused:
            self._active_executions.pop(plan.plan_id, None)
        self._wake_events.pop(plan.plan_id, None)

        return DAGExecutionResult(
            plan_id=plan.plan_id,
            run_id=plan.run_id,
            status=status,
            completed_nodes=tuple(sorted(completed_ids)),
            failed_nodes=tuple(sorted(failed_ids)),
            cancelled_nodes=tuple(sorted(cancelled_ids)),
            skipped_nodes=tuple(sorted(skipped_ids)),
            waiting_approval_nodes=tuple(sorted(waiting_approval_ids)),
            node_results=node_results,
            events_count=len(events),
            duration_seconds=round(duration, 3),
            error=error_msg,
            is_paused=is_paused,
        )

    async def resume_plan(
        self,
        plan_id: str,
        node_executor: Callable[[DAGTaskNode], Awaitable[dict[str, Any]]] | None = None,
        max_concurrency: int | None = None,
        cancellation_token: asyncio.Event | None = None,
    ) -> DAGExecutionResult:
        """Resume execution of a paused DAG plan (Gate C3)."""
        plan = self._plans.get(plan_id)
        if plan is None:
            raise ValueError(f"Plan '{plan_id}' not found for resume")

        pause_token = self._pause_tokens.get(plan_id)
        if pause_token is not None:
            pause_token.clear()

        self.event_bus.publish(
            run_id=plan.run_id,
            agent_id="dag_scheduler",
            event_type="plan_resumed",
            payload={"plan_id": plan_id},
        )
        return await self.execute_plan(
            plan,
            node_executor=node_executor,
            max_concurrency=max_concurrency,
            cancellation_token=cancellation_token,
        )

    def _skip_downstream(
        self,
        failed_or_skipped_id: str,
        node_states: dict[str, DAGTaskNode],
        skipped_ids: set[str],
        run_id: str,
    ) -> None:
        """Transitively mark downstream nodes depending on failed/skipped id as SKIPPED, respecting join policies."""
        to_check = [failed_or_skipped_id]
        while to_check:
            parent = to_check.pop(0)
            for nid, node in node_states.items():
                if parent in node.depends_on and node.status in (DAGTaskStatus.PENDING, DAGTaskStatus.READY):
                    policy = getattr(node, "join_policy", "all") or "all"
                    # If join_policy is 'any', a single failed upstream might not cause skip if others succeed
                    if policy in ("any", "first_success"):
                        continue
                    node_states[nid] = DAGTaskNode(
                        node_id=node.node_id,
                        agent_type=node.agent_type,
                        objective=node.objective,
                        depends_on=node.depends_on,
                        skill_id=node.skill_id,
                        input_payload=node.input_payload,
                        budget=node.budget,
                        status=DAGTaskStatus.SKIPPED,
                        error=f"Upstream node '{parent}' failed or was skipped",
                        completed_at=_utc_now(),
                        condition=node.condition,
                        join_policy=node.join_policy,
                    )
                    skipped_ids.add(nid)
                    self.event_bus.publish(
                        run_id=run_id,
                        agent_id=node.agent_type,
                        event_type="node_skipped",
                        payload={"node_id": nid, "cause": f"Upstream '{parent}' unresolved"},
                    )
                    to_check.append(nid)

    def _persist_plan(self, plan: DAGPlan, results: dict[str, Any] | None = None) -> None:
        if self.db_path is None:
            return
        now = _utc_now()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO dag_plans (
                    plan_id, run_id, objective, status, is_paused, version,
                    nodes_json, results_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(plan_id) DO UPDATE SET
                    status = excluded.status,
                    is_paused = excluded.is_paused,
                    version = excluded.version,
                    nodes_json = excluded.nodes_json,
                    results_json = excluded.results_json,
                    updated_at = excluded.updated_at
                """,
                (
                    plan.plan_id,
                    plan.run_id,
                    plan.objective,
                    plan.status,
                    1 if plan.is_paused else 0,
                    plan.version,
                    json.dumps([n.to_dict() for n in plan.nodes], ensure_ascii=False),
                    json.dumps(results or {}, ensure_ascii=False, default=_json_default),
                    plan.created_at or now,
                    now,
                ),
            )
