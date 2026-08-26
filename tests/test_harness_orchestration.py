from types import SimpleNamespace

import pytest

from conflux_weave.harness import TaskSubmission
from conflux_weave.harness.orchestration import (
    CompositeOrchestrator,
    DeterministicRouter,
    LegacyPaperRuntimeAdapter,
)


class Runtime:
    def __init__(self, executor_id: str, task_kind: str, result: str | None = None):
        self.executor_id = executor_id
        self.task_kinds = (task_kind,)
        self.result = result
        self.submissions = []

    def submit(self, submission):
        self.submissions.append(submission)
        return SimpleNamespace(task_id="task-1", run_id="run-1", created=True)

    def work_once(self, *, now=None):
        result, self.result = self.result, None
        return result

    def request_cancel(self, run_id, *, now=None):
        return ("cancel", run_id)

    def resume(self, run_id, decision=None, *, now=None):
        return ("resume", run_id, decision)


class Repository:
    def __init__(self, kind="kind-a"):
        self.kind = kind

    def get_task_for_run(self, run_id):
        return SimpleNamespace(kind=self.kind)


def test_router_rejects_unknown_kind_and_wrong_requested_agent() -> None:
    runtime = Runtime("agent-a", "kind-a")
    router = DeterministicRouter({"kind-a": runtime})

    assert router.route(TaskSubmission("kind-a", {})).executor_id == "agent-a"
    with pytest.raises(ValueError, match="unsupported task kind"):
        router.route(TaskSubmission("unknown", {}))
    with pytest.raises(ValueError, match="requested Agent"):
        router.route(TaskSubmission("kind-a", {}, requested_agent="agent-b"))


def test_composite_routes_submit_work_and_run_mutations() -> None:
    first = Runtime("agent-a", "kind-a")
    second = Runtime("agent-b", "kind-b", result="worked")
    repository = Repository()
    orchestrator = CompositeOrchestrator(repository, (first, second))

    accepted = orchestrator.submit(TaskSubmission("kind-b", {"value": 1}))

    assert accepted.run_id == "run-1"
    assert second.submissions[0].input == {"value": 1}
    assert orchestrator.work_once() == "worked"
    assert orchestrator.request_cancel("run-a") == ("cancel", "run-a")
    assert orchestrator.resume("run-a") == ("resume", "run-a", None)


def test_legacy_adapter_contains_paper_specific_translation() -> None:
    class Legacy:
        def submit(self, query, *, search_query, max_results):
            return query, search_query, max_results

        def work_once(self, *, now=None):
            return None

        def request_cancel(self, run_id, *, now=None):
            return run_id

        def resume(self, run_id, decision=None, *, now=None):
            return run_id

    adapter = LegacyPaperRuntimeAdapter(Legacy())

    assert adapter.submit(
        TaskSubmission(
            "paper_discovery",
            {"query": "question", "topics": ("rag", "agent"), "max_results": 8},
        )
    ) == ("question", "rag agent", 8)
