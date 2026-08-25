"""Single-process ASGI boundary for the W5 local runtime."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Protocol

from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict

from conflux_weave.api_contracts import (
    ApiErrorResponse,
    ResearchTaskAcceptedResponse,
    ResearchTaskRequest,
    RunDetailResponse,
    RunEventPageResponse,
    RunPageResponse,
    WorkbenchQueryService,
    map_exception,
)
from conflux_weave.paper_discovery import ArxivSearchAdapter
from conflux_weave.provider import OpenAICompatibleChatAdapter, ProviderConfig
from conflux_weave.runtime import (
    DurablePaperDiscoveryRuntime,
    LocalArtifactStore,
    RecoveryDecision,
    SQLiteRuntimeRepository,
)


class RuntimePort(Protocol):
    def submit(self, query: str, *, search_query: str, max_results: int = 15) -> Any: ...

    def work_once(self, *, now: str | None = None) -> Any: ...

    def request_cancel(self, run_id: str, *, now: str | None = None) -> Any: ...

    def resume(self, run_id: str, decision: RecoveryDecision | None = None, *, now: str | None = None) -> Any: ...


class WorkerLoop:
    """Run exactly one injected Runtime worker loop for an ASGI lifespan."""

    def __init__(self, runtime: RuntimePort, *, interval_seconds: float = 0.25) -> None:
        if interval_seconds <= 0:
            raise ValueError("worker interval must be positive")
        self.runtime = runtime
        self.interval_seconds = interval_seconds
        self._task: asyncio.Task[None] | None = None
        self.iterations = 0

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="conflux-weave-worker")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is None:
            return
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def _run(self) -> None:
        while True:
            try:
                await asyncio.to_thread(self.runtime.work_once)
                self.iterations += 1
            except asyncio.CancelledError:
                raise
            except Exception:
                # Runtime persists a structured failure where possible. The loop itself
                # remains available for other queued Runs and never retries a Step here.
                pass
            await asyncio.sleep(self.interval_seconds)


class RecoveryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    decision: RecoveryDecision | None = None


def create_app(
    repository: SQLiteRuntimeRepository,
    runtime: RuntimePort,
    *,
    provider_configured: bool = False,
    worker: WorkerLoop | None = None,
    poll_interval_seconds: float = 0.25,
) -> FastAPI:
    """Build the one ASGI application around injected authoritative components."""

    query_service = WorkbenchQueryService(repository)
    worker_loop = worker or WorkerLoop(runtime, interval_seconds=poll_interval_seconds)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        await worker_loop.start()
        try:
            yield
        finally:
            await worker_loop.stop()

    app = FastAPI(title="Conflux-Weave", version="0.0.1", lifespan=lifespan)
    app.state.repository = repository
    app.state.runtime = runtime
    app.state.worker = worker_loop

    def error_response(exc: Exception) -> JSONResponse:
        status, error = map_exception(exc)
        return JSONResponse(status_code=status, content=error.model_dump(mode="json"))

    @app.post("/api/v1/tasks/research", response_model=ResearchTaskAcceptedResponse)
    async def submit_task(request: ResearchTaskRequest):
        try:
            search_query = " ".join(request.topics) if request.topics else request.query
            result = runtime.submit(
                request.query,
                search_query=search_query,
                max_results=request.max_results,
            )
            state = query_service.get_run(result.run_id).state
            return ResearchTaskAcceptedResponse(
                task_id=result.task_id,
                run_id=result.run_id,
                created=result.created,
                state=state,
            )
        except Exception as exc:
            return error_response(exc)

    @app.get("/api/v1/runs", response_model=RunPageResponse)
    async def list_runs(cursor: str | None = None, limit: int = Query(default=20, ge=1, le=100)):
        try:
            return query_service.list_runs(cursor=cursor, limit=limit)
        except Exception as exc:
            return error_response(exc)

    @app.get("/api/v1/runs/{run_id}", response_model=RunDetailResponse)
    async def get_run(run_id: str):
        try:
            return query_service.get_run(run_id)
        except Exception as exc:
            return error_response(exc)

    @app.post("/api/v1/runs/{run_id}/cancel", response_model=RunDetailResponse)
    async def cancel_run(run_id: str):
        try:
            runtime.request_cancel(run_id)
            return query_service.get_run(run_id)
        except Exception as exc:
            return error_response(exc)

    @app.post("/api/v1/runs/{run_id}/resume", response_model=RunDetailResponse)
    async def resume_run(run_id: str, request: RecoveryRequest | None = None):
        try:
            runtime.resume(run_id, request.decision if request else None)
            return query_service.get_run(run_id)
        except Exception as exc:
            return error_response(exc)

    @app.get("/api/v1/runs/{run_id}/events", response_class=StreamingResponse)
    async def stream_events(
        run_id: str,
        after: int = Query(default=0, ge=0),
        poll_seconds: float = Query(default=0.25, gt=0, le=10),
    ):
        try:
            repository.get_run(run_id)
        except Exception as exc:
            return error_response(exc)

        async def event_stream() -> AsyncIterator[str]:
            cursor = after
            while True:
                page: RunEventPageResponse = query_service.get_events(run_id, after=cursor)
                for event in page.items:
                    cursor = event.cursor
                    payload = json.dumps(event.model_dump(mode="json"), ensure_ascii=False)
                    yield f"id: {event.cursor}\nevent: {event.kind.value}\ndata: {payload}\n\n"
                if page.items and page.items[-1].state.value in {
                    "complete", "partial", "failed", "cancelled", "expired"
                }:
                    return
                snapshot = query_service.get_run(run_id)
                if snapshot.is_terminal:
                    return
                await asyncio.sleep(poll_seconds)

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/v1/runs/{run_id}/artifacts")
    async def list_artifacts(run_id: str):
        try:
            return {"items": query_service.get_delivery_artifacts(run_id)}
        except Exception as exc:
            return error_response(exc)

    @app.get("/api/v1/evidence/{evidence_id}")
    async def get_evidence(evidence_id: str, run_id: str):
        try:
            return query_service.get_evidence(run_id, evidence_id)
        except Exception as exc:
            return error_response(exc)

    @app.get("/api/v1/health/live")
    async def live_health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/v1/health/ready")
    async def ready_health():
        return query_service.readiness(provider_configured=provider_configured)

    return app


def build_local_app(
    *,
    database: Path = Path("var") / "db" / "conflux-weave.sqlite3",
    artifact_root: Path = Path("var") / "artifacts" / "sha256",
    dotenv_path: Path | None = Path(".env"),
) -> FastAPI:
    """Construct the production-shaped local app; no external call occurs here."""
    store = LocalArtifactStore(artifact_root)
    repository = SQLiteRuntimeRepository(database, store)
    try:
        config = ProviderConfig.from_environment(dotenv_path)
    except Exception:
        return create_app(repository, _UnavailableRuntime(), provider_configured=False)
    runtime = DurablePaperDiscoveryRuntime(
        repository,
        store,
        ArxivSearchAdapter(store),
        OpenAICompatibleChatAdapter(store, config),
    )
    return create_app(repository, runtime, provider_configured=True)


class _UnavailableRuntime:
    def submit(self, *args: Any, **kwargs: Any) -> Any:
        raise ValueError("Provider configuration is incomplete")

    def work_once(self, *, now: str | None = None) -> None:
        return None

    def request_cancel(self, run_id: str, *, now: str | None = None) -> Any:
        raise ValueError("Runtime is unavailable")

    def resume(self, run_id: str, decision: RecoveryDecision | None = None, *, now: str | None = None) -> Any:
        raise ValueError("Runtime is unavailable")


__all__ = ["WorkerLoop", "build_local_app", "create_app"]
