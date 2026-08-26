"""Deterministic routing and compatibility ports for Harness task runtimes."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from conflux_weave.evidence import ArtifactRef
from conflux_weave.harness.contracts import (
    AgentResult,
    AgentTask,
    ContextBundle,
    RouteDecision,
    TaskSubmission,
)
from conflux_weave.runtime.sqlite_contracts import RecoveryDecision


class TaskRuntimePort(Protocol):
    executor_id: str
    task_kinds: tuple[str, ...]

    def submit(self, submission: TaskSubmission) -> Any: ...

    def work_once(self, *, now: str | None = None) -> Any: ...

    def request_cancel(self, run_id: str, *, now: str | None = None) -> Any: ...

    def resume(
        self,
        run_id: str,
        decision: RecoveryDecision | None = None,
        *,
        now: str | None = None,
    ) -> Any: ...


class AgentExecutorPort(Protocol):
    executor_id: str

    def execute(
        self, task: AgentTask, context: ContextBundle
    ) -> tuple[AgentResult, tuple[ArtifactRef, ...]]: ...


class OrchestratorPort(Protocol):
    def submit(self, submission: TaskSubmission) -> Any: ...

    def work_once(self, *, now: str | None = None) -> Any: ...

    def request_cancel(self, run_id: str, *, now: str | None = None) -> Any: ...

    def resume(
        self,
        run_id: str,
        decision: RecoveryDecision | None = None,
        *,
        now: str | None = None,
    ) -> Any: ...


class DeterministicRouter:
    def __init__(self, routes: Mapping[str, TaskRuntimePort]) -> None:
        self._routes = dict(routes)
        if not self._routes:
            raise ValueError("Router requires at least one task route")

    def route(self, submission: TaskSubmission) -> RouteDecision:
        runtime = self._routes.get(submission.task_kind)
        if runtime is None:
            raise ValueError(f"unsupported task kind: {submission.task_kind}")
        if (
            submission.requested_agent is not None
            and submission.requested_agent != runtime.executor_id
        ):
            raise ValueError(
                f"requested Agent cannot execute task kind {submission.task_kind}"
            )
        return RouteDecision(
            task_kind=submission.task_kind,
            executor_id=runtime.executor_id,
            reason="deterministic task-kind route",
        )

    def runtime_for(self, task_kind: str) -> TaskRuntimePort:
        try:
            return self._routes[task_kind]
        except KeyError as exc:
            raise ValueError(f"unsupported task kind: {task_kind}") from exc


class CompositeOrchestrator:
    """Route submissions while sharing one worker tick across task runtimes."""

    def __init__(self, repository: Any, runtimes: tuple[TaskRuntimePort, ...]) -> None:
        if not runtimes:
            raise ValueError("CompositeOrchestrator requires at least one runtime")
        routes: dict[str, TaskRuntimePort] = {}
        for runtime in runtimes:
            for task_kind in runtime.task_kinds:
                if task_kind in routes:
                    raise ValueError(f"duplicate task route: {task_kind}")
                routes[task_kind] = runtime
        self.repository = repository
        self.runtimes = runtimes
        self.router = DeterministicRouter(routes)
        self._next_runtime = 0

    def submit(self, submission: TaskSubmission) -> Any:
        decision = self.router.route(submission)
        return self.router.runtime_for(decision.task_kind).submit(submission)

    def work_once(self, *, now: str | None = None) -> Any:
        for offset in range(len(self.runtimes)):
            index = (self._next_runtime + offset) % len(self.runtimes)
            result = self.runtimes[index].work_once(now=now)
            if result is not None:
                self._next_runtime = (index + 1) % len(self.runtimes)
                return result
        self._next_runtime = (self._next_runtime + 1) % len(self.runtimes)
        return None

    def request_cancel(self, run_id: str, *, now: str | None = None) -> Any:
        task = self.repository.get_task_for_run(run_id)
        return self.router.runtime_for(task.kind).request_cancel(run_id, now=now)

    def resume(
        self,
        run_id: str,
        decision: RecoveryDecision | None = None,
        *,
        now: str | None = None,
    ) -> Any:
        task = self.repository.get_task_for_run(run_id)
        return self.router.runtime_for(task.kind).resume(run_id, decision, now=now)


class LegacyPaperRuntimeAdapter:
    executor_id = "legacy_paper_runtime@v1"
    task_kinds = ("paper_discovery",)

    def __init__(self, runtime: Any) -> None:
        self.runtime = runtime

    def submit(self, submission: TaskSubmission) -> Any:
        if submission.task_kind != "paper_discovery":
            raise ValueError("Legacy Paper Runtime only accepts paper_discovery")
        query = submission.input.get("query")
        topics = submission.input.get("topics", ())
        search_query = submission.input.get("search_query")
        max_results = submission.input.get("max_results", 15)
        if not isinstance(query, str):
            raise ValueError("paper_discovery requires query")
        if not isinstance(search_query, str):
            if not isinstance(topics, (tuple, list)):
                raise ValueError("paper_discovery topics must be a sequence")
            search_query = " ".join(str(topic) for topic in topics) or query
        if not isinstance(max_results, int):
            raise ValueError("paper_discovery max_results must be an integer")
        return self.runtime.submit(
            query,
            search_query=search_query,
            max_results=max_results,
        )

    def work_once(self, *, now: str | None = None) -> Any:
        return self.runtime.work_once(now=now)

    def request_cancel(self, run_id: str, *, now: str | None = None) -> Any:
        return self.runtime.request_cancel(run_id, now=now)

    def resume(
        self,
        run_id: str,
        decision: RecoveryDecision | None = None,
        *,
        now: str | None = None,
    ) -> Any:
        return self.runtime.resume(run_id, decision, now=now)


class UnavailableTaskRuntime:
    def __init__(
        self,
        repository: Any,
        *,
        executor_id: str,
        task_kinds: tuple[str, ...],
        message: str,
    ) -> None:
        self.repository = repository
        self.executor_id = executor_id
        self.task_kinds = task_kinds
        self.message = message

    def submit(self, submission: TaskSubmission) -> Any:
        raise ValueError(self.message)

    def work_once(self, *, now: str | None = None) -> None:
        return None

    def request_cancel(self, run_id: str, *, now: str | None = None) -> Any:
        return self.repository.request_cancel(run_id, now=now)

    def resume(
        self,
        run_id: str,
        decision: RecoveryDecision | None = None,
        *,
        now: str | None = None,
    ) -> Any:
        return self.repository.resume_run(run_id, decision, now=now)


__all__ = [
    "AgentExecutorPort",
    "CompositeOrchestrator",
    "DeterministicRouter",
    "LegacyPaperRuntimeAdapter",
    "OrchestratorPort",
    "TaskRuntimePort",
    "UnavailableTaskRuntime",
]
