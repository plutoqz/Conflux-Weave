"""Single-process ASGI boundary for the W5 local runtime."""

from __future__ import annotations

import asyncio
import json
import mimetypes
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, Query
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict

from conflux_weave.api_contracts import (
    ApiErrorResponse,
    ArtifactContentResponse,
    FixtureResearchTaskRequest,
    FollowUpResearchTaskRequest,
    ResearchTaskAcceptedResponse,
    ResearchTaskRequest,
    RunDetailResponse,
    RunEventPageResponse,
    RunPageResponse,
    WorkbenchQueryService,
    VerifiedResearchTaskRequest,
    map_exception,
)
from conflux_weave.harness.contracts import TaskSubmission
from conflux_weave.harness.fixture_runtime import ResearchFixtureRuntime
from conflux_weave.harness.orchestration import (
    CompositeOrchestrator,
    DurableResearchRuntimeAdapter,
    LegacyPaperRuntimeAdapter,
    OrchestratorPort,
    UnavailableTaskRuntime,
)
from conflux_weave.harness.workspace import LocalWorkspaceAdapter
from conflux_weave.paper_discovery import ArxivSearchAdapter
from conflux_weave.provider import OpenAICompatibleChatAdapter, ProviderConfig
from conflux_weave.runtime import (
    DurablePaperDiscoveryRuntime,
    DurableResearchRuntime,
    LocalArtifactStore,
    RecoveryDecision,
    SQLiteRuntimeRepository,
    VerifiedWorkflowExecutorAdapter,
)


class WorkerLoop:
    """Run exactly one injected Runtime worker loop for an ASGI lifespan."""

    def __init__(
        self, orchestrator: OrchestratorPort, *, interval_seconds: float = 0.25
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("worker interval must be positive")
        self.orchestrator = orchestrator
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
                await asyncio.to_thread(self.orchestrator.work_once)
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


WORKBENCH_ROOT = Path(__file__).with_name("workbench")
mimetypes.add_type("text/javascript", ".js")


def create_app(
    repository: SQLiteRuntimeRepository,
    orchestrator: OrchestratorPort,
    *,
    provider_configured: bool = False,
    worker: WorkerLoop | None = None,
    poll_interval_seconds: float = 0.25,
) -> FastAPI:
    """Build the one ASGI application around injected authoritative components."""

    query_service = WorkbenchQueryService(repository)
    worker_loop = worker or WorkerLoop(
        orchestrator, interval_seconds=poll_interval_seconds
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        await worker_loop.start()
        try:
            yield
        finally:
            await worker_loop.stop()

    app = FastAPI(title="Conflux-Weave", version="0.0.1", lifespan=lifespan)
    app.state.repository = repository
    app.state.orchestrator = orchestrator
    app.state.worker = worker_loop

    def error_response(exc: Exception) -> JSONResponse:
        status, error = map_exception(exc)
        return JSONResponse(status_code=status, content=error.model_dump(mode="json"))

    @app.post("/api/v1/tasks/research", response_model=ResearchTaskAcceptedResponse)
    async def submit_task(request: ResearchTaskRequest):
        try:
            result = orchestrator.submit(
                TaskSubmission(
                    task_kind="paper_discovery",
                    input={
                        "query": request.query,
                        "topics": request.topics,
                        "max_results": request.max_results,
                    },
                )
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

    @app.post(
        "/api/v1/tasks/research-fixture",
        response_model=ResearchTaskAcceptedResponse,
    )
    async def submit_fixture_task(request: FixtureResearchTaskRequest):
        try:
            result = orchestrator.submit(
                TaskSubmission(
                    task_kind="research_fixture",
                    input={"objective": request.objective},
                    requested_agent="research_fixture@v1",
                    idempotency_key=request.idempotency_key,
                )
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

    @app.post(
        "/api/v1/tasks/verified-research",
        response_model=ResearchTaskAcceptedResponse,
    )
    async def submit_verified_research(request: VerifiedResearchTaskRequest):
        try:
            task_kind = (
                "managed_verified_research"
                if request.mode == "managed"
                else "verified_paper_research"
            )
            result = orchestrator.submit(
                TaskSubmission(
                    task_kind=task_kind,
                    input={
                        "objective": request.objective,
                        "max_subquestions": request.max_subquestions,
                    },
                    requested_agent="durable_verified_research@v1",
                )
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
            orchestrator.request_cancel(run_id)
            return query_service.get_run(run_id)
        except Exception as exc:
            return error_response(exc)

    @app.post("/api/v1/runs/{run_id}/resume", response_model=RunDetailResponse)
    async def resume_run(run_id: str, request: RecoveryRequest | None = None):
        try:
            orchestrator.resume(run_id, request.decision if request else None)
            return query_service.get_run(run_id)
        except Exception as exc:
            return error_response(exc)

    @app.post(
        "/api/v1/runs/{run_id}/rerun",
        response_model=ResearchTaskAcceptedResponse,
    )
    async def rerun(run_id: str):
        try:
            task = repository.get_task_for_run(run_id)
            result = orchestrator.submit(
                TaskSubmission(
                    task_kind=task.kind,
                    input=task.input,
                    idempotency_key=f"rerun:{run_id}:{uuid4().hex}",
                )
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

    @app.post(
        "/api/v1/runs/{run_id}/follow-up",
        response_model=ResearchTaskAcceptedResponse,
    )
    async def follow_up(run_id: str, request: FollowUpResearchTaskRequest):
        try:
            task = repository.get_task_for_run(run_id)
            if task.kind not in {
                "verified_paper_research",
                "managed_verified_research",
            }:
                raise ValueError("follow-up requires a verified research Run")
            original = task.input.get("objective")
            if not isinstance(original, str) or not original.strip():
                raise ValueError("original research objective is unavailable")
            objective = (
                f"Original research objective: {original.strip()}\n"
                f"Follow-up question: {request.question}"
            )
            result = orchestrator.submit(
                TaskSubmission(
                    task_kind=task.kind,
                    input={
                        "objective": objective,
                        "max_subquestions": task.input.get("max_subquestions", 4),
                        "parent_run_id": run_id,
                        "follow_up_question": request.question,
                    },
                    idempotency_key=f"follow-up:{run_id}:{uuid4().hex}",
                )
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

    @app.get(
        "/api/v1/runs/{run_id}/artifacts/{artifact_id}/content",
        response_model=ArtifactContentResponse,
    )
    async def read_artifact(run_id: str, artifact_id: str):
        try:
            metadata, content = query_service.read_delivery_artifact(run_id, artifact_id)
            if len(content) > 2_000_000:
                raise ValueError("Delivery Artifact exceeds the Workbench display limit")
            return ArtifactContentResponse(
                artifact=metadata,
                content=content.decode("utf-8"),
            )
        except UnicodeDecodeError as exc:
            return error_response(ValueError("Delivery Artifact is not UTF-8 text"))
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

    app.mount("/assets", StaticFiles(directory=WORKBENCH_ROOT), name="workbench-assets")

    @app.get("/", include_in_schema=False)
    async def workbench_index() -> FileResponse:
        return FileResponse(WORKBENCH_ROOT / "index.html", media_type="text/html")

    return app


def build_local_app(
    *,
    database: Path = Path("var") / "db" / "conflux-weave.sqlite3",
    artifact_root: Path = Path("var") / "artifacts" / "sha256",
    workspace_root: Path = Path("var") / "workspace",
    dotenv_path: Path | None = Path(".env"),
    corpus_manifest: Path = Path("var") / "acceptance" / "v0.3-s1" / "corpus-import-manifest.json",
    lancedb_root: Path = Path("var") / "acceptance" / "v0.3-s1" / "lancedb",
) -> FastAPI:
    """Construct the production-shaped local app; no external call occurs here."""
    store = LocalArtifactStore(artifact_root)
    repository = SQLiteRuntimeRepository(database, store)
    workspace = LocalWorkspaceAdapter(
        workspace_root,
        Path(__file__).with_name("system"),
        store,
    )
    fixture_runtime = ResearchFixtureRuntime(repository, store, workspace)
    try:
        config = ProviderConfig.from_environment(dotenv_path)
    except Exception:
        paper_runtime = UnavailableTaskRuntime(
            repository,
            executor_id="legacy_paper_runtime@v1",
            task_kinds=("paper_discovery",),
            message="Provider configuration is incomplete",
        )
        provider_configured = False
        research_runtime = UnavailableTaskRuntime(
            repository,
            executor_id="durable_verified_research@v1",
            task_kinds=("verified_paper_research", "managed_verified_research"),
            message="Provider configuration is incomplete",
        )
    else:
        paper_runtime = LegacyPaperRuntimeAdapter(
            DurablePaperDiscoveryRuntime(
                repository,
                store,
                ArxivSearchAdapter(store),
                OpenAICompatibleChatAdapter(store, config),
            )
        )
        provider_configured = True
        try:
            from conflux_weave.hybrid_retrieval import HybridRetrievalPipeline
            from conflux_weave.indexing import LanceDBDenseIndex, load_chunks
            from conflux_weave.managed_research import ManagedVerifiedResearchWorkflow
            from conflux_weave.provider import (
                OpenAICompatibleEmbeddingAdapter,
                OpenAICompatibleRerankerAdapter,
            )
            from conflux_weave.research_agents import VerifiedResearchWorkflow

            documents = load_chunks(corpus_manifest, store)
            retrieval = HybridRetrievalPipeline(
                documents,
                LanceDBDenseIndex(lancedb_root, table_name="paper_chunks"),
                OpenAICompatibleEmbeddingAdapter(store, config),
                OpenAICompatibleRerankerAdapter(store, config),
            )
            verified = VerifiedResearchWorkflow(
                store,
                retrieval,
                OpenAICompatibleChatAdapter(store, config),
                corpus_scope=f"corpus manifest {corpus_manifest}",
            )
            managed = ManagedVerifiedResearchWorkflow(
                store,
                verified,
                OpenAICompatibleChatAdapter(store, config),
            )
            research_runtime = DurableResearchRuntimeAdapter(
                DurableResearchRuntime(
                    repository,
                    store,
                    VerifiedWorkflowExecutorAdapter(store, verified, managed),
                )
            )
        except Exception as exc:
            research_runtime = UnavailableTaskRuntime(
                repository,
                executor_id="durable_verified_research@v1",
                task_kinds=("verified_paper_research", "managed_verified_research"),
                message=f"Research corpus or LanceDB is unavailable: {exc}",
            )
    orchestrator = CompositeOrchestrator(
        repository,
        (fixture_runtime, paper_runtime, research_runtime),
    )
    return create_app(
        repository,
        orchestrator,
        provider_configured=provider_configured,
    )


__all__ = ["WorkerLoop", "build_local_app", "create_app"]
