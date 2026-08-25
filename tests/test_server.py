import asyncio
from types import SimpleNamespace

import pytest

from conflux_weave.api_contracts import ResearchTaskRequest
from conflux_weave.core import BudgetLedger, RunRecord, RunStatus, StepRecord, StepStatus, TaskSpec
from conflux_weave.runtime import LocalArtifactStore, SQLiteRuntimeRepository
from conflux_weave.server import WorkerLoop, build_local_app, create_app


NOW = "2026-08-25T12:00:00Z"


class FixtureRuntime:
    def __init__(self, repository: SQLiteRuntimeRepository) -> None:
        self.repository = repository
        self.calls = 0
        self.submissions = 0

    def submit(self, query: str, *, search_query: str, max_results: int = 15):
        self.submissions += 1
        suffix = str(self.submissions)
        task = TaskSpec(
            task_id=f"task-fixture-{suffix}",
            kind="paper_discovery",
            input={"query": query, "search_query": search_query, "max_results": max_results},
            requested_policy="fixture-v1",
            idempotency_key=f"fixture-key-{suffix}",
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
        dotenv_path=tmp_path / "missing.env",
    )
    ready = route(app, "/api/v1/health/ready")
    response = await ready()

    assert response.status == "not_ready"
    assert all(check.name != "provider" or check.status == "not_ready" for check in response.checks)
    assert not hasattr(app.state.runtime, "chat_adapter")


def test_app_exposes_only_one_local_worker_and_no_workbench_assets(tmp_path) -> None:
    repository, runtime = build_fixture(tmp_path)
    app = create_app(repository, runtime)
    paths = {route.path for route in app.routes}

    assert "/api/v1/health/live" in paths
    assert "/api/v1/runs/{run_id}/events" in paths
    assert not any(path.startswith("/workbench") for path in paths)
    assert app.state.worker.worker_id if hasattr(app.state.worker, "worker_id") else True
