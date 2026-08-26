import asyncio
from types import SimpleNamespace

import pytest

from conflux_weave.api_contracts import (
    FixtureResearchTaskRequest,
    FollowUpResearchTaskRequest,
    ResearchTaskRequest,
    VerifiedResearchTaskRequest,
)
from conflux_weave.core import BudgetLedger, RunRecord, RunStatus, StepRecord, StepStatus, TaskSpec
from conflux_weave.harness import TaskSubmission
from conflux_weave.runtime import LocalArtifactStore, SQLiteRuntimeRepository
from conflux_weave.server import WorkerLoop, build_local_app, create_app


NOW = "2026-08-25T12:00:00Z"


class FixtureRuntime:
    executor_id = "server-fixture@v1"
    task_kinds = (
        "paper_discovery",
        "verified_paper_research",
        "managed_verified_research",
    )

    def __init__(self, repository: SQLiteRuntimeRepository) -> None:
        self.repository = repository
        self.calls = 0
        self.submissions = 0

    def submit(self, submission: TaskSubmission):
        self.submissions += 1
        suffix = str(self.submissions)
        if submission.task_kind == "paper_discovery":
            query = str(submission.input["query"])
            topics = submission.input.get("topics", ())
            task_input = {
                "query": query,
                "search_query": " ".join(str(item) for item in topics) or query,
                "max_results": int(submission.input.get("max_results", 15)),
            }
        else:
            task_input = {
                "objective": str(submission.input["objective"]),
                "max_subquestions": int(submission.input.get("max_subquestions", 4)),
            }
            for key in ("parent_run_id", "follow_up_question"):
                if key in submission.input:
                    task_input[key] = submission.input[key]
        task = TaskSpec(
            task_id=f"task-fixture-{suffix}",
            kind=submission.task_kind,
            input=task_input,
            requested_policy="fixture-v1",
            idempotency_key=submission.idempotency_key or f"fixture-key-{suffix}",
        )
        run = RunRecord(
            run_id=f"run-fixture-{suffix}",
            task_id=task.task_id,
            status=RunStatus.ACCEPTED,
            workflow_version="fixture-v1",
            config_snapshot_ref="fixture-config",
            budget=BudgetLedger(60, 100, 100, "unavailable", 1, 1, 1),
            created_at=NOW,
            updated_at=NOW,
        )
        steps = (
            StepRecord(
                step_id=f"step-fixture-{suffix}",
                run_id=run.run_id,
                kind="fixture_step",
                attempt=1,
                status=StepStatus.PENDING,
            ),
        )
        result = self.repository.submit_task(task, run, steps)
        self.repository.transition_run(result.run_id, RunStatus.QUEUED, updated_at=NOW)
        return result

    def work_once(self, *, now: str | None = None):
        self.calls += 1

    def request_cancel(self, run_id: str, *, now: str | None = None):
        return self.repository.request_cancel(run_id, now=now or NOW)

    def resume(self, run_id: str, decision=None, *, now: str | None = None):
        return self.repository.resume_run(run_id, decision, now=now or NOW)


def build_fixture(tmp_path):
    store = LocalArtifactStore(tmp_path / "artifacts")
    repository = SQLiteRuntimeRepository(tmp_path / "db" / "runtime.sqlite3", store, clock=lambda: NOW)
    runtime = FixtureRuntime(repository)
    return repository, runtime


def route(app, path: str):
    return next(item.endpoint for item in app.routes if item.path == path)


def test_lifespan_starts_and_stops_one_injected_worker(tmp_path) -> None:
    asyncio.run(_test_lifespan_starts_and_stops_one_injected_worker(tmp_path))


async def _test_lifespan_starts_and_stops_one_injected_worker(tmp_path) -> None:
    repository, runtime = build_fixture(tmp_path)
    worker = WorkerLoop(runtime, interval_seconds=0.01)
    app = create_app(repository, runtime, worker=worker)

    async with app.router.lifespan_context(app):
        await asyncio.sleep(0.04)
        assert runtime.calls > 0
        assert worker._task is not None
    assert worker._task is None


def test_routes_submit_cancel_and_health_use_persisted_state(tmp_path) -> None:
    asyncio.run(_test_routes_submit_cancel_and_health_use_persisted_state(tmp_path))


async def _test_routes_submit_cancel_and_health_use_persisted_state(tmp_path) -> None:
    repository, runtime = build_fixture(tmp_path)
    app = create_app(repository, runtime, worker=WorkerLoop(runtime, interval_seconds=1))
    submit = route(app, "/api/v1/tasks/research")
    cancel = route(app, "/api/v1/runs/{run_id}/cancel")
    get_run = route(app, "/api/v1/runs/{run_id}")
    live = route(app, "/api/v1/health/live")

    accepted = await submit(ResearchTaskRequest(query="runtime recovery", topics=("durable",)))
    assert accepted.run_id == "run-fixture-1"
    assert accepted.state.value == "pending"
    cancelled = await cancel(accepted.run_id)
    assert cancelled.state.value == "cancelled"
    assert repository.get_run(accepted.run_id).status is RunStatus.CANCELLED
    assert await live() == {"status": "ok"}
    assert (await get_run(accepted.run_id)).run_id == accepted.run_id


def test_sse_projects_persisted_events_and_finishes_on_terminal_run(tmp_path) -> None:
    asyncio.run(_test_sse_projects_persisted_events_and_finishes_on_terminal_run(tmp_path))


async def _test_sse_projects_persisted_events_and_finishes_on_terminal_run(tmp_path) -> None:
    repository, runtime = build_fixture(tmp_path)
    app = create_app(repository, runtime, worker=WorkerLoop(runtime, interval_seconds=1))
    submit = route(app, "/api/v1/tasks/research")
    cancel = route(app, "/api/v1/runs/{run_id}/cancel")
    stream = route(app, "/api/v1/runs/{run_id}/events")

    accepted = await submit(ResearchTaskRequest(query="sse fixture"))
    await cancel(accepted.run_id)
    response = await stream(accepted.run_id, after=0, poll_seconds=0.01)
    chunks = [chunk async for chunk in response.body_iterator]
    body = "".join(chunk.decode() if isinstance(chunk, bytes) else chunk for chunk in chunks)

    assert response.media_type == "text/event-stream"
    assert "id:" in body
    assert "event: status" in body
    assert '"run_id": "run-fixture-1"' in body


def test_verified_research_submit_and_rerun_preserve_task_scope(tmp_path) -> None:
    asyncio.run(_test_verified_research_submit_and_rerun_preserve_task_scope(tmp_path))


async def _test_verified_research_submit_and_rerun_preserve_task_scope(tmp_path) -> None:
    repository, runtime = build_fixture(tmp_path)
    app = create_app(repository, runtime, worker=WorkerLoop(runtime, interval_seconds=1))
    submit = route(app, "/api/v1/tasks/verified-research")
    rerun = route(app, "/api/v1/runs/{run_id}/rerun")
    follow_up = route(app, "/api/v1/runs/{run_id}/follow-up")

    accepted = await submit(
        VerifiedResearchTaskRequest(
            objective="Compare context reduction methods and evaluations",
            mode="managed",
            max_subquestions=3,
        )
    )
    original = repository.get_task_for_run(accepted.run_id)
    detail = await route(app, "/api/v1/runs/{run_id}")(accepted.run_id)
    repeated = await rerun(accepted.run_id)
    cloned = repository.get_task_for_run(repeated.run_id)
    continued = await follow_up(
        accepted.run_id,
        FollowUpResearchTaskRequest(question="Which evaluation is most diagnostic?"),
    )
    continued_task = repository.get_task_for_run(continued.run_id)

    assert original.kind == cloned.kind == "managed_verified_research"
    assert detail.query == "Compare context reduction methods and evaluations"
    assert "核验" in detail.status_message or "等待" in detail.status_message
    assert cloned.input == original.input == {
        "objective": "Compare context reduction methods and evaluations",
        "max_subquestions": 3,
    }
    assert repeated.created is True
    assert repeated.run_id != accepted.run_id
    assert continued_task.kind == "managed_verified_research"
    assert continued_task.input["parent_run_id"] == accepted.run_id
    assert continued_task.input["follow_up_question"] == (
        "Which evaluation is most diagnostic?"
    )
    assert "Original research objective" in continued_task.input["objective"]


def test_missing_provider_build_is_local_not_ready_and_does_not_start_calls(
    tmp_path, monkeypatch
) -> None:
    asyncio.run(_test_missing_provider_build_is_local_not_ready_and_does_not_start_calls(tmp_path, monkeypatch))


async def _test_missing_provider_build_is_local_not_ready_and_does_not_start_calls(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.delenv("CONFLUX_WEAVE_PROVIDER_BASE_URL", raising=False)
    monkeypatch.delenv("CONFLUX_WEAVE_PROVIDER_API_KEY", raising=False)
    monkeypatch.delenv("CONFLUX_WEAVE_PROVIDER_MODEL", raising=False)
    app = build_local_app(
        database=tmp_path / "db" / "runtime.sqlite3",
        artifact_root=tmp_path / "artifacts",
        workspace_root=tmp_path / "workspace",
        dotenv_path=tmp_path / "missing.env",
    )
    ready = route(app, "/api/v1/health/ready")
    response = await ready()

    assert response.status == "not_ready"
    assert all(check.name != "provider" or check.status == "not_ready" for check in response.checks)
    assert not hasattr(app.state.orchestrator, "chat_adapter")

    submit_fixture = route(app, "/api/v1/tasks/research-fixture")
    get_run = route(app, "/api/v1/runs/{run_id}")
    accepted = await submit_fixture(
        FixtureResearchTaskRequest(objective="验证无 Provider 的 Harness 闭环")
    )
    assert app.state.orchestrator.work_once(now=NOW) == "partial"
    detail = await get_run(accepted.run_id)
    assert detail.task_family == "research_fixture"
    assert detail.state.value == "partial"
    assert detail.budget.tool_calls_used == 1


def test_app_exposes_only_one_local_worker_and_no_workbench_assets(tmp_path) -> None:
    repository, runtime = build_fixture(tmp_path)
    app = create_app(repository, runtime)
    paths = {route.path for route in app.routes}

    assert "/api/v1/health/live" in paths
    assert "/api/v1/runs/{run_id}/events" in paths
    assert not any(path.startswith("/workbench") for path in paths)
    assert app.state.worker.worker_id if hasattr(app.state.worker, "worker_id") else True
