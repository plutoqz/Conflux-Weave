"""Single-process ASGI boundary for the W5 local runtime."""

from __future__ import annotations

import asyncio
import json
import mimetypes
import os
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4
import subprocess

from dotenv import dotenv_values
from fastapi import FastAPI, Query
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict

from conflux_weave.chat import ChatService
from conflux_weave.api_contracts import (
    ChatAnswerChecks,
    ChatAnswerResponse,
    ChatCitationRecord,
    ChatHistoryResponse,
    ChatMessageRecord,
    ChatMessageRequest,
    ConversationDetail,
    ConversationListResponse,
    ConversationSummary,
    ResearchConversationMessageRequest,
    DeepResearchTaskRequest,
    ApiErrorResponse,
    ArtifactContentResponse,
    FixtureResearchTaskRequest,
    FollowUpResearchTaskRequest,
    ProviderConfigResponse,
    ProviderConfigTestRequest,
    ProviderConfigTestResponse,
    ProviderConfigUpdateRequest,
    ProviderConfigUpdateResponse,
    ResearchTaskAcceptedResponse,
    ResearchTaskRequest,
    RunDetailResponse,
    RunEventPageResponse,
    RunPageResponse,
    VerifiedResearchTaskRequest,
    WorkbenchConfigResponse,
    WorkbenchQueryService,
    map_exception,
)
from conflux_weave.config_store import (
    ConfigValidationError,
    ProviderConfigView,
    _read_values,
    update_provider,
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
    dotenv_path: Path | None = None,
    config_paths: dict[str, str] | None = None,
    chat_service: ChatService | None = None,
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

    @app.post("/api/v1/chat", response_model=ChatAnswerResponse)
    def submit_chat_message(request: ChatMessageRequest):
        """W3.0 模式 A：直接问答——无 Run、无报告工件，仅对话记录。"""
        if chat_service is None:
            return JSONResponse(
                status_code=503,
                content={
                    "code": "provider_not_configured",
                    "message": "模型服务未配置，对话不可用。",
                    "recovery_action": "在设置中完成模型服务配置后重试。",
                },
            )
        if request.mode == "rag" and not chat_service.has_rag:
            return JSONResponse(
                status_code=503,
                content={
                    "code": "corpus_not_ready",
                    "message": "本地知识库未就绪，知识库问答不可用。",
                    "recovery_action": "先在研究视图导入语料，或改用直接问答。",
                },
            )
        try:
            if request.mode == "rag":
                result = chat_service.rag_answer(request.question, request.conversation_id)
            else:
                result = chat_service.direct_answer(request.question, request.conversation_id)
        except Exception as exc:
            return error_response(exc)
        return ChatAnswerResponse(
            message_id=result["message_id"],
            conversation_id=result["conversation_id"],
            role=result["role"],
            mode=result["mode"],
            content=result["content"],
            created_at=result["created_at"],
            provider_response_id=result["provider_response_id"],
            verification=result["verification"],
            checks=(
                ChatAnswerChecks(
                    status=result["checks"]["status"],
                    violations=tuple(result["checks"]["violations"]),
                )
                if result.get("checks")
                else None
            ),
            timings_ms=result.get("timings_ms", {}),
            citations=tuple(
                ChatCitationRecord(
                    index=item["index"],
                    chunk_id=item["chunk_id"],
                    source_snapshot_id=item["source_snapshot_id"],
                    locator=item["locator"],
                )
                for item in result.get("citations", ())
            ),
        )

    @app.get("/api/v1/chat/messages", response_model=ChatHistoryResponse)
    async def list_chat_messages(limit: int = Query(default=20, ge=1, le=100)):
        if chat_service is None:
            return ChatHistoryResponse(items=())
        return ChatHistoryResponse(
            items=tuple(
                ChatMessageRecord(
                    message_id=message.message_id,
                    conversation_id=message.conversation_id,
                    role=message.role,
                    mode=message.mode,
                    content=message.content,
                    created_at=message.created_at,
                )
                for message in chat_service.history(limit=limit)
            )
        )

    @app.get("/api/v1/conversations", response_model=ConversationListResponse)
    async def list_conversations(limit: int = Query(default=50, ge=1, le=100)):
        if chat_service is None:
            return ConversationListResponse(items=())
        return ConversationListResponse(items=tuple(ConversationSummary(**item) for item in chat_service.conversations(limit)))

    @app.get("/api/v1/conversations/{conversation_id}", response_model=ConversationDetail)
    async def get_conversation(conversation_id: str):
        if chat_service is None:
            return JSONResponse(status_code=404, content={"code": "conversation_not_found", "message": "对话记录不存在。"})
        record = chat_service.conversation_record(conversation_id)
        messages = tuple(ChatMessageRecord(message_id=m.message_id, conversation_id=m.conversation_id, role=m.role, mode=m.mode, content=m.content, created_at=m.created_at) for m in record.pop("messages", ()))
        return ConversationDetail(**record, messages=messages)

    @app.post("/api/v1/conversations/{conversation_id}/messages", response_model=ChatMessageRecord)
    async def record_research_message(conversation_id: str, request: ResearchConversationMessageRequest):
        if chat_service is None:
            return JSONResponse(status_code=503, content={"code": "provider_not_configured", "message": "对话记录不可用。"})
        message = chat_service.record_research_message(conversation_id, request.role, request.content, request.run_id, mode=request.mode)
        return ChatMessageRecord(message_id=message.message_id, conversation_id=message.conversation_id, role=message.role, mode=message.mode, content=message.content, created_at=message.created_at)

    @app.post("/api/v1/tasks/deep-research", response_model=ResearchTaskAcceptedResponse)
    async def submit_deep_research(request: DeepResearchTaskRequest):
        """W3.2 模式 C：GPT Researcher 发现聚合 + 本地证据链（durable Run）。"""
        try:
            conversation_id = request.conversation_id or f"conv-{uuid4().hex}"
            result = orchestrator.submit(
                TaskSubmission(
                    task_kind="deep_research",
                    input={"objective": request.objective, "conversation_id": conversation_id},
                    requested_agent="durable_verified_research@v1",
                )
            )
            if chat_service is not None:
                chat_service.record_research_message(conversation_id, "user", request.objective, result.run_id)
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
                "deep_research",
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
                        "conversation_id": task.input.get("conversation_id"),
                    },
                    idempotency_key=f"follow-up:{run_id}:{uuid4().hex}",
                )
            )
            conversation_id = task.input.get("conversation_id")
            if task.kind == "deep_research" and isinstance(conversation_id, str) and chat_service is not None:
                chat_service.record_research_message(conversation_id, "user", request.question, result.run_id)
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

    def _provider_view() -> ProviderConfigResponse:
        view = ProviderConfigView.from_env(dotenv_path) if dotenv_path else None
        if view is None:
            return ProviderConfigResponse(
                base_url="", model="", embedding_model="", reranker_model="",
                engine_model="", api_key_configured=False, api_key_hint=None,
            )
        return ProviderConfigResponse(
            base_url=view.base_url,
            model=view.model,
            embedding_model=view.embedding_model,
            reranker_model=view.reranker_model,
            engine_model=view.engine_model,
            api_key_configured=view.api_key_configured,
            api_key_hint=view.api_key_hint,
        )

    @app.get("/api/v1/config", response_model=WorkbenchConfigResponse)
    async def get_config():
        try:
            return WorkbenchConfigResponse(
                provider=_provider_view(),
                provider_active=provider_configured,
                paths=config_paths or {},
            )
        except Exception as exc:
            return error_response(exc)

    @app.put(
        "/api/v1/config/provider",
        response_model=ProviderConfigUpdateResponse,
    )
    async def put_provider_config(request: ProviderConfigUpdateRequest):
        try:
            if dotenv_path is None:
                raise ConfigValidationError("此实例未启用配置持久化，无法保存。")
            view = update_provider(
                dotenv_path,
                base_url=request.base_url,
                api_key=request.api_key,
                model=request.model,
                embedding_model=request.embedding_model,
                reranker_model=request.reranker_model,
                engine_model=request.engine_model,
            )
            return ProviderConfigUpdateResponse(
                provider=ProviderConfigResponse(
                    base_url=view.base_url,
                    model=view.model,
                    embedding_model=view.embedding_model,
                    reranker_model=view.reranker_model,
                    engine_model=view.engine_model,
                    api_key_configured=view.api_key_configured,
                    api_key_hint=view.api_key_hint,
                ),
                requires_restart=True,
                message="Provider 配置已保存。",
            )
        except Exception as exc:
            return error_response(exc)

    @app.post(
        "/api/v1/config/provider/test",
        response_model=ProviderConfigTestResponse,
    )
    async def test_provider_config(request: ProviderConfigTestRequest):
        stored = _read_values(dotenv_path) if dotenv_path else {}
        base_url = request.base_url or stored.get("CONFLUX_WEAVE_PROVIDER_BASE_URL", "")
        api_key = request.api_key or stored.get("CONFLUX_WEAVE_PROVIDER_API_KEY", "")
        model = request.model or stored.get("CONFLUX_WEAVE_PROVIDER_MODEL", "")
        missing = [
            name for name, value in (
                ("服务地址", base_url), ("API Key", api_key), ("Chat 模型", model),
            ) if not value.strip()
        ]
        if missing:
            return ProviderConfigTestResponse(
                ok=False,
                message="请先完整填写：" + "、".join(missing) + "。",
            )
        try:
            from conflux_weave.provider import OpenAICompatibleChatAdapter, ProviderConfig

            config = ProviderConfig(
                base_url=base_url.strip().rstrip("/"),
                api_key=api_key.strip(),
                model=model.strip(),
            )
            adapter = OpenAICompatibleChatAdapter(
                repository.artifact_store,
                config,
                timeout_seconds=20.0,
            )
            started = time.monotonic()
            await asyncio.to_thread(
                adapter.complete,
                system_prompt="You are a connection test for the Conflux-Weave workbench.",
                user_prompt="Reply with the single word: ok",
                # 思考型模型（如 glm-5.3-flash）会先把输出预算花在 reasoning 上，
                # 8 token 连正文都放不下；512 足够思考 + 一个词的回答。
                max_output_tokens=512,
                producer_step_id="step-provider-config-test",
            )
            latency_ms = int((time.monotonic() - started) * 1000)
            return ProviderConfigTestResponse(
                ok=True,
                message="模型服务可连通。",
                latency_ms=latency_ms,
            )
        except Exception as exc:
            return ProviderConfigTestResponse(ok=False, message=str(exc) or "连接失败。")

    @app.get("/api/v1/health/ready")
    async def ready_health():
        return query_service.readiness(provider_configured=provider_configured)

    app.mount("/assets", StaticFiles(directory=WORKBENCH_ROOT), name="workbench-assets")

    @app.get("/", include_in_schema=False)
    async def workbench_index() -> FileResponse:
        return FileResponse(WORKBENCH_ROOT / "index.html", media_type="text/html")

    return app


def _code_revision() -> str:
    """Code identity for run idempotency: same code dedupes, changed code reruns."""

    try:
        head = subprocess.check_output(
            ["git", "rev-parse", "--short=12", "HEAD"],
            text=True,
            cwd=Path(__file__).parent,
        ).strip()
    except Exception:
        return "unknown"
    try:
        dirty = bool(
            subprocess.check_output(
                ["git", "status", "--porcelain"],
                text=True,
                cwd=Path(__file__).parent,
            ).strip()
        )
    except Exception:
        dirty = False
    return f"{head}{'-dirty' if dirty else ''}"


def _inject_third_party_keys(dotenv_path: Path | None) -> None:
    """把 dotenv 里的第三方服务 key（TAVILY_API_KEY 等）注入进程环境。

    GPT Researcher 的检索器直接读 os.environ，而这些 key 不属于
    ProviderConfig；只注入进程尚未设置的键（已有环境变量优先），不触碰
    CONFLUX_WEAVE_PROVIDER_*（那套走 dotenv_values 显式读取）。
    """
    if dotenv_path is None or not dotenv_path.exists():
        return
    for key in ("TAVILY_API_KEY", "TAVILY_BASE_URL"):
        if os.environ.get(key):
            continue
        value = (dotenv_values(dotenv_path) or {}).get(key)
        if isinstance(value, str) and value.strip():
            os.environ[key] = value.strip()


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
    _inject_third_party_keys(dotenv_path)
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
        chat_service = None
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
        retrieval_pipeline = None
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
            retrieval_pipeline = retrieval
            try:
                from conflux_weave.deep_research import (
                    DeepResearchWorkflow,
                    GPTResearcherBridge,
                )

                deep_workflow = DeepResearchWorkflow(
                    store,
                    OpenAICompatibleChatAdapter(store, config),
                    GPTResearcherBridge(config, retrieval),
                    code_revision=_code_revision(),
                )
                deep_enabled = True
            except Exception as deep_exc:
                deep_workflow = None
                deep_enabled = False
                print(f"deep research engine unavailable: {deep_exc}")
            research_runtime = DurableResearchRuntimeAdapter(
                DurableResearchRuntime(
                    repository,
                    store,
                    VerifiedWorkflowExecutorAdapter(store, verified, managed, deep_workflow),
                    code_revision=_code_revision(),
                    # W3.5 融合交付的深度研究批次（引擎+规划+融合写作+审计）
                    # 可超过 15 分钟；本地单 worker 下放宽租约，避免长批次
                    # 被判为 worker 失联进入 needs_attention。
                    lease_seconds=3600,
                ),
                deep_research_enabled=deep_enabled,
            )
        except Exception as exc:
            research_runtime = UnavailableTaskRuntime(
                repository,
                executor_id="durable_verified_research@v1",
                task_kinds=("verified_paper_research", "managed_verified_research"),
                message=f"Research corpus or LanceDB is unavailable: {exc}",
            )
        chat_service = ChatService(
            OpenAICompatibleChatAdapter(store, config),
            database,
            retrieval=retrieval_pipeline,
            artifact_store=store,
        )
    orchestrator = CompositeOrchestrator(
        repository,
        (fixture_runtime, paper_runtime, research_runtime),
    )
    return create_app(
        repository,
        orchestrator,
        provider_configured=provider_configured,
        dotenv_path=dotenv_path,
        config_paths={
            "database": str(database),
            "artifact_root": str(artifact_root),
            "workspace_root": str(workspace_root),
            "corpus_manifest": str(corpus_manifest),
            "lancedb_root": str(lancedb_root),
            "dotenv": str(dotenv_path) if dotenv_path else "",
        },
        chat_service=chat_service,
    )


__all__ = ["WorkerLoop", "build_local_app", "create_app"]
