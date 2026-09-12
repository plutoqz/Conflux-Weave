"""Single-process ASGI boundary for the W5 local runtime."""

from __future__ import annotations

import asyncio
import json
import mimetypes
import os
import re
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4
import subprocess
from datetime import UTC, datetime

from dotenv import dotenv_values
from fastapi import Body, FastAPI, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from conflux_weave.chat import ChatMessage, ChatService
from conflux_weave.conversation_router import ConversationRouter, RouterResult
from conflux_weave.api_contracts import (
    ChatAnswerChecks,
    ChatAnswerResponse,
    ChatCitationRecord,
    ChatHistoryResponse,
    ChatMessageRecord,
    ChatMessageRequest,
    RouterRequest,
    RouterResultResponse,
    ConversationDetail,
    ConversationListResponse,
    ConversationSummary,
    ResearchConversationMessageRequest,
    DeepResearchTaskRequest,
    ApiErrorResponse,
    ArtifactContentResponse,
    DocumentAnalyzeRequest,
    DocumentAssetDetailResponse,
    DocumentAssetsResponse,
    DocumentNoteResponse,
    DocumentNoteSectionResponse,
    NotePatchRequest,
    NoteRevisionItem,
    NoteRevisionsResponse,
    MultimodalFusionHitResponse,
    MultimodalRetrievalResultResponse,
    ProjectSummaryResponse,
    ProjectDetailResponse,
    ProjectRegisterRequest,
    ProjectTreeResponse,
    ProjectFileContentResponse,
    ProjectAskRequest,
    ProjectAskResponse,
    CodingProposalRequest,
    CodingProposalResponse,
    CodingApplyRequest,
    CodingApplyResponse,
    SemanticBranchDiffResponse,
    TheoryMappingItem,
    ArchitectureComponentItem,
    ArchitectureWalkthroughResponse,
    AuditFindingItem,
    ProjectAuditReportResponse,
    MemoryItemResponse,
    MemoryListResponse,
    MemoryCandidateResponse,
    MemoryCandidateListResponse,
    CreateMemoryRequest,
    MemoryCandidateActionRequest,
    SkillBudgetResponse,
    SkillSummaryResponse,
    SkillDetailResponse,
    SkillListResponse,
    SkillExecuteApiRequest,
    SkillExecuteApiResponse,
    MCPToolInfoResponse,
    MCPServerResponse,
    MCPServerListResponse,
    CreateMCPServerRequest,
    MCPToolCallApiRequest,
    MCPToolCallApiResponse,
    DAGTaskNodeApiRequest,
    DAGTaskNodeApiResponse,
    DAGPlanApiRequest,
    DAGPlanValidationResponse,
    DAGExecutionResultApiResponse,
    AgentEventApiResponse,
    AgentEventListResponse,
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
from conflux_weave.runtime.memory_store import (
    HierarchicalMemoryStore,
    MemoryCategory,
    MemoryScope,
    MemoryStatus,
    CandidateStatus,
)
from conflux_weave.memory_agent import MemoryAgent
from conflux_weave.skills import SkillRegistry, SkillRunner, SkillExecutionRequest
from conflux_weave.mcp import (
    MCPServerConfig,
    MCPServerCore,
    MCPServerManager,
    MCPSSEManager,
    MCPToolInfo,
    MCPTransportType,
)
from conflux_weave.orchestrator import (
    AgentEvent,
    AsyncAgentEventBus,
    DAGCycleError,
    DAGDependencyError,
    DAGExecutionResult,
    DAGPlan,
    DAGTaskNode,
    DAGTaskScheduler,
    DAGTaskStatus,
)
from conflux_weave.projects import GitInspector, Project, ProjectScanner, ProjectStore
from conflux_weave.project_agents import CodeProposal, CodingAgent, ProjectAgent, ProjectAnswer
from conflux_weave.document_agent import DocumentAgent
from conflux_weave.document_notes import (
    NOTE_SCHEMA_VERSION,
    DocumentNote,
    NotePatch,
    NoteSection,
    PatchOperation,
    load_note_artifact,
)
from conflux_weave.documents import LocalDocumentImporter
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
from conflux_weave.documents import LocalDocumentImporter, UnsupportedDocumentError
from conflux_weave.library_papers import (
    OpenAccessPdfFetcher,
    OpenAlexSearchAdapter,
    PaperRecord,
    PaperSourceError,
    PdfCandidate,
    UnpaywallResolver,
    arxiv_record,
    merge_and_rank,
    paper_from_dict,
)
from conflux_weave.provider import OpenAICompatibleChatAdapter, ProviderConfig
from conflux_weave.retrieval import RetrievalDocument
from conflux_weave.runtime import (
    DurablePaperDiscoveryRuntime,
    DurableResearchRuntime,
    LocalArtifactStore,
    RecoveryDecision,
    SQLiteRuntimeRepository,
    VerifiedWorkflowExecutorAdapter,
)


_LIBRARY_TITLE_CACHE: dict[str, str] = {}


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


class LibraryPaperRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["metadata", "fulltext"] = "fulltext"
    paper: dict[str, Any]


class LibraryBatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_ids: list[str] = Field(min_length=1, max_length=100)
    action: Literal["index", "remove"] = "index"


class LibraryResearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    objective: str | None = Field(default=None, max_length=4_000)


class LibraryRestoreRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version_index: int = Field(ge=0, le=19)


WORKBENCH_ROOT = Path(__file__).with_name("workbench")
mimetypes.add_type("text/javascript", ".js")

ALLOWED_IMAGE_MIMES: set[str] = {"image/png", "image/jpeg", "image/webp"}
MAX_IMAGE_SIZE_BYTES: int = 20_000_000


def _validate_image_magic_bytes(content: bytes, media_type: str) -> bool:
    if media_type == "image/png":
        return content.startswith(b"\x89PNG\r\n\x1a\n")
    if media_type in {"image/jpeg", "image/jpg"}:
        return content.startswith(b"\xff\xd8\xff")
    if media_type == "image/webp":
        return content[:4] == b"RIFF" and len(content) >= 12 and content[8:12] == b"WEBP"
    return False


def _build_asset_detail_response(asset: dict[str, Any]) -> DocumentAssetDetailResponse:
    asset_id = str(asset["asset_id"])
    status = str(asset.get("extraction_status", "extracted"))
    has_art = bool(asset.get("artifact_ref"))
    content_url = f"/api/v1/library/assets/{asset_id}/content" if (has_art and status != "failed") else None
    has_thumb = bool(asset.get("thumbnail_artifact_ref"))
    thumbnail_url = f"/api/v1/library/assets/{asset_id}/content?variant=thumbnail" if has_thumb else None
    return DocumentAssetDetailResponse(
        schema_version=asset.get("schema_version", "conflux-weave.document-asset.v1"),
        asset_id=asset_id,
        document_id=str(asset.get("document_id", "")),
        source_snapshot_id=str(asset.get("source_snapshot_id", "")),
        page=int(asset.get("page", 1)),
        asset_kind=str(asset.get("asset_kind", "embedded_image")),
        artifact_ref=asset.get("artifact_ref"),
        thumbnail_artifact_ref=asset.get("thumbnail_artifact_ref"),
        media_type=str(asset.get("media_type", "image/png")),
        content_hash=asset.get("content_hash"),
        width_px=int(asset.get("width_px", 0) or 0),
        height_px=int(asset.get("height_px", 0) or 0),
        bbox=asset.get("bbox"),
        coordinate_space=str(asset.get("coordinate_space", "pdf_page_points_top_left")),
        page_width=float(asset.get("page_width", 0.0) or 0.0),
        page_height=float(asset.get("page_height", 0.0) or 0.0),
        page_rotation=int(asset.get("page_rotation", 0) or 0),
        caption=asset.get("caption"),
        caption_locator=asset.get("caption_locator"),
        parent_segment_ids=tuple(asset.get("parent_segment_ids", ())),
        extraction_method=str(asset.get("extraction_method", "pymupdf-v1")),
        extraction_status=status,
        duplicate_of_asset_id=asset.get("duplicate_of_asset_id"),
        warnings=tuple(asset.get("warnings", ())),
        content_url=content_url,
        thumbnail_url=thumbnail_url,
    )


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
    retrieval_pipeline: Any | None = None,
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
    db_path = getattr(repository, "database_path", ":memory:")
    memory_store = HierarchicalMemoryStore(db_path if db_path else ":memory:")
    chat_adapter = getattr(chat_service, "_chat", None) if chat_service else None
    memory_agent = MemoryAgent(memory_store, chat_adapter=chat_adapter)
    conversation_router = ConversationRouter(chat_adapter=chat_adapter)
    skill_registry = SkillRegistry(db_path if db_path and db_path != ":memory:" else None)
    skill_runner = SkillRunner(skill_registry, provider=chat_adapter)
    mcp_manager = MCPServerManager(db_path if db_path and db_path != ":memory:" else None)
    project_root = Path(db_path).parent if db_path and db_path != ":memory:" else None
    mcp_server_core = MCPServerCore(
        repository=repository,
        memory_store=memory_store,
        project_root=project_root,
    )
    mcp_sse_manager = MCPSSEManager(mcp_server_core)
    event_bus = AsyncAgentEventBus(db_path if db_path and db_path != ":memory:" else None)
    dag_scheduler = DAGTaskScheduler(event_bus=event_bus)
    if chat_service is not None and not getattr(chat_service, "_memory_agent", None):
        chat_service._memory_agent = memory_agent
    app.state.repository = repository
    app.state.orchestrator = orchestrator
    app.state.worker = worker_loop
    app.state.memory_store = memory_store
    app.state.memory_agent = memory_agent
    app.state.conversation_router = conversation_router
    app.state.skill_registry = skill_registry
    app.state.skill_runner = skill_runner
    app.state.mcp_manager = mcp_manager
    app.state.mcp_server_core = mcp_server_core
    app.state.mcp_sse_manager = mcp_sse_manager
    app.state.event_bus = event_bus
    app.state.dag_scheduler = dag_scheduler

    def third_party_setting(name: str) -> str:
        value = os.environ.get(name)
        if not value and dotenv_path and dotenv_path.is_file():
            value = (dotenv_values(dotenv_path) or {}).get(name)
        return value.strip() if isinstance(value, str) else ""

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

    @app.post("/api/v1/chat/route", response_model=RouterResultResponse)
    def route_chat(request: RouterRequest):
        result = conversation_router.route(
            request.query,
            current_mode=request.current_mode,
            conversation_id=request.conversation_id,
        )
        return RouterResultResponse(
            target_mode=result.target_mode,
            is_fast_path=result.is_fast_path,
            confidence=result.confidence,
            intent_summary=result.intent_summary,
            extracted_entities=result.extracted_entities,
            suggested_run_kind=result.suggested_run_kind,
        )

    @app.post("/api/v1/chat", response_model=ChatAnswerResponse)
    def submit_chat_message(request: ChatMessageRequest):
        """统一全能对话入口：智能路由分流（快慢双通道、实体提取、记忆感知）。"""
        route_result = conversation_router.route(
            request.question,
            current_mode=request.mode,
            conversation_id=request.conversation_id,
        )
        effective_mode = route_result.target_mode

        # 慢通道：深度学术调研任务 (Deep Research Durable Run)
        if effective_mode == "deep":
            try:
                task_kind = route_result.suggested_run_kind or "managed_verified_research"
                sub_result = orchestrator.submit(
                    TaskSubmission(
                        task_kind=task_kind,
                        input={
                            "objective": request.question,
                            "max_subquestions": 3,
                        },
                        requested_agent="durable_verified_research@v1",
                    )
                )
                conv_id = request.conversation_id or f"conv-{uuid4().hex}"
                content = f"已为您启动深度研究任务（Run ID: {sub_result.run_id}），正在后台持续执行文献检索与多源交叉论证..."
                now = datetime.now(UTC).isoformat()
                if chat_service is not None:
                    try:
                        chat_service.record_research_message(
                            conversation_id=conv_id,
                            role="user",
                            content=request.question,
                            run_id=sub_result.run_id,
                            mode="deep",
                            conversation_mode=request.mode,
                        )
                        chat_service.record_research_message(
                            conversation_id=conv_id,
                            role="assistant",
                            content=content,
                            run_id=sub_result.run_id,
                            mode="deep",
                            conversation_mode=request.mode,
                        )
                    except Exception:
                        pass
                return ChatAnswerResponse(
                    message_id=f"msg-{uuid4().hex}",
                    conversation_id=conv_id,
                    role="assistant",
                    mode="deep",
                    content=content,
                    created_at=now,
                    verification="durable-run-dispatched",
                    routed_mode="deep",
                    is_fast_path=False,
                    intent_summary=route_result.intent_summary,
                    run_id=sub_result.run_id,
                )
            except Exception as exc:
                return error_response(exc)

        # 记忆通道：查询或总结记忆与偏好
        if effective_mode == "memory":
            try:
                active_mems = memory_store.list_memories(status=MemoryStatus.ACTIVE)
                candidates = memory_store.list_candidates(status=CandidateStatus.PENDING)
                summary_lines = ["【分层记忆中心当前状态】"]
                if active_mems:
                    summary_lines.append(f"已生效记忆 ({len(active_mems)} 条):")
                    for m in active_mems[:10]:
                        summary_lines.append(f"- [{m.scope.value}/{m.category.value}] {m.statement}")
                else:
                    summary_lines.append("当前暂无已生效的长期偏好或约定。")
                if candidates:
                    summary_lines.append(f"\n待核准候选 ({len(candidates)} 条):")
                    for c in candidates[:5]:
                        summary_lines.append(f"- [{c.scope.value}] {c.statement} (ID: {c.candidate_id})")
                summary_lines.append("\n您可以在设置中心（#/settings）核准或管理记忆。")
                content = "\n".join(summary_lines)
                conv_id = request.conversation_id or f"conv-{uuid4().hex}"
                now = datetime.now(UTC).isoformat()
                if chat_service is not None:
                    try:
                        chat_service._ensure_conversation(conv_id, request.question, request.mode)
                        turn_id, seq = chat_service._next_turn(conv_id)
                        chat_service._append(ChatMessage(f"msg-{uuid4().hex}", conv_id, "user", "memory", request.question, now, turn_id=turn_id, sequence=seq), conversation_mode=request.mode)
                        chat_service._append(ChatMessage(f"msg-{uuid4().hex}", conv_id, "assistant", "memory", content, now, turn_id=turn_id, sequence=seq), conversation_mode=request.mode)
                    except Exception:
                        pass
                return ChatAnswerResponse(
                    message_id=f"msg-{uuid4().hex}",
                    conversation_id=conv_id,
                    role="assistant",
                    mode="memory",
                    content=content,
                    created_at=now,
                    verification="model-knowledge",
                    routed_mode="memory",
                    is_fast_path=True,
                    intent_summary=route_result.intent_summary,
                )
            except Exception as exc:
                return error_response(exc)

        # 项目通道：项目治理引导
        if effective_mode == "project":
            try:
                entities = route_result.extracted_entities
                proj_id = entities.get("project_id", "当前工程")
                content = f"已识别到项目治理意图（项目：{proj_id}）。建议前往「项目工作台」查看深度架构拓扑、理论映射与代码健康度契约体检结果。"
                conv_id = request.conversation_id or f"conv-{uuid4().hex}"
                now = datetime.now(UTC).isoformat()
                if chat_service is not None:
                    try:
                        chat_service._ensure_conversation(conv_id, request.question, request.mode)
                        turn_id, seq = chat_service._next_turn(conv_id)
                        chat_service._append(ChatMessage(f"msg-{uuid4().hex}", conv_id, "user", "project", request.question, now, turn_id=turn_id, sequence=seq), conversation_mode=request.mode)
                        chat_service._append(ChatMessage(f"msg-{uuid4().hex}", conv_id, "assistant", "project", content, now, turn_id=turn_id, sequence=seq), conversation_mode=request.mode)
                    except Exception:
                        pass
                return ChatAnswerResponse(
                    message_id=f"msg-{uuid4().hex}",
                    conversation_id=conv_id,
                    role="assistant",
                    mode="project",
                    content=content,
                    created_at=now,
                    verification="model-knowledge",
                    routed_mode="project",
                    is_fast_path=False,
                    intent_summary=route_result.intent_summary,
                )
            except Exception as exc:
                return error_response(exc)

        # 文档通道：单篇文献精读引导
        if effective_mode == "document":
            try:
                entities = route_result.extracted_entities
                target_doc = entities.get("paper_id") or entities.get("note_id") or "指定文档"
                content = f"已识别到文档研读与权威笔记意图（目标：{target_doc}）。建议前往「资料库」查看单篇精读解析、多模态图表与段落证据链。"
                conv_id = request.conversation_id or f"conv-{uuid4().hex}"
                now = datetime.now(UTC).isoformat()
                if chat_service is not None:
                    try:
                        chat_service._ensure_conversation(conv_id, request.question, request.mode)
                        turn_id, seq = chat_service._next_turn(conv_id)
                        chat_service._append(ChatMessage(f"msg-{uuid4().hex}", conv_id, "user", "document", request.question, now, turn_id=turn_id, sequence=seq), conversation_mode=request.mode)
                        chat_service._append(ChatMessage(f"msg-{uuid4().hex}", conv_id, "assistant", "document", content, now, turn_id=turn_id, sequence=seq), conversation_mode=request.mode)
                    except Exception:
                        pass
                return ChatAnswerResponse(
                    message_id=f"msg-{uuid4().hex}",
                    conversation_id=conv_id,
                    role="assistant",
                    mode="document",
                    content=content,
                    created_at=now,
                    verification="model-knowledge",
                    routed_mode="document",
                    is_fast_path=False,
                    intent_summary=route_result.intent_summary,
                )
            except Exception as exc:
                return error_response(exc)

        # 快通道：Direct 或 RAG 模式
        if chat_service is None:
            return JSONResponse(
                status_code=503,
                content={
                    "code": "provider_not_configured",
                    "message": "模型服务未配置，对话不可用。",
                    "recovery_action": "在设置中完成模型服务配置后重试。",
                },
            )
        if effective_mode == "rag" and not chat_service.has_rag:
            return JSONResponse(
                status_code=503,
                content={
                    "code": "corpus_not_ready",
                    "message": "本地知识库未就绪，知识库问答不可用。",
                    "recovery_action": "先在研究视图导入语料，或改用直接问答。",
                },
            )
        try:
            if effective_mode == "rag":
                result = chat_service.rag_answer(request.question, request.conversation_id, conversation_mode=request.mode)
                mark_library_usage([str(item.get("source_snapshot_id", "")) for item in result.get("citations", ())])
            else:
                result = chat_service.direct_answer(request.question, request.conversation_id, conversation_mode=request.mode)
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
            routed_mode=effective_mode,
            is_fast_path=True,
            intent_summary=route_result.intent_summary,
            memory_candidates=tuple(result.get("memory_candidates", ())),
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

    @app.get("/api/v1/memories", response_model=MemoryListResponse)
    async def list_memories(
        scope: str | None = None,
        target_id: str | None = None,
        category: str | None = None,
        status: str = "active",
        limit: int = Query(default=50, ge=1, le=200),
    ):
        try:
            items = memory_store.list_memories(
                scope=scope,
                target_id=target_id,
                category=category,
                status=status,
                limit=limit,
            )
            response_items = tuple(
                MemoryItemResponse(
                    memory_id=item.memory_id,
                    scope=item.scope.value,
                    target_id=item.target_id,
                    category=item.category.value,
                    statement=item.statement,
                    confidence=item.confidence,
                    status=item.status.value,
                    source_type=item.source_type,
                    source_id=item.source_id,
                    created_at=item.created_at,
                    updated_at=item.updated_at,
                )
                for item in items
            )
            return MemoryListResponse(items=response_items, total=len(response_items))
        except Exception as exc:
            return error_response(exc)

    @app.post("/api/v1/memories", response_model=MemoryItemResponse)
    async def create_memory(request: CreateMemoryRequest):
        try:
            item = memory_store.create_memory(
                scope=request.scope,
                target_id=request.target_id,
                category=request.category,
                statement=request.statement,
                confidence=request.confidence,
                source_type="manual",
                source_id="user",
            )
            return MemoryItemResponse(
                memory_id=item.memory_id,
                scope=item.scope.value,
                target_id=item.target_id,
                category=item.category.value,
                statement=item.statement,
                confidence=item.confidence,
                status=item.status.value,
                source_type=item.source_type,
                source_id=item.source_id,
                created_at=item.created_at,
                updated_at=item.updated_at,
            )
        except Exception as exc:
            return error_response(exc)

    @app.delete("/api/v1/memories/{memory_id}")
    async def delete_memory(memory_id: str):
        try:
            success = memory_store.delete_memory(memory_id)
            if not success:
                return JSONResponse(status_code=404, content={"code": "memory_not_found", "message": "指定记忆不存在。"})
            return {"ok": True, "memory_id": memory_id}
        except Exception as exc:
            return error_response(exc)

    @app.get("/api/v1/memories/candidates", response_model=MemoryCandidateListResponse)
    async def list_memory_candidates(
        status: str = "pending",
        scope: str | None = None,
        target_id: str | None = None,
        limit: int = Query(default=50, ge=1, le=100),
    ):
        try:
            candidates = memory_store.list_candidates(
                status=status,
                scope=scope,
                target_id=target_id,
                limit=limit,
            )
            response_items = tuple(
                MemoryCandidateResponse(
                    candidate_id=cand.candidate_id,
                    scope=cand.scope.value,
                    target_id=cand.target_id,
                    category=cand.category.value,
                    statement=cand.statement,
                    confidence=cand.confidence,
                    conflict_with_memory_id=cand.conflict_with_memory_id,
                    status=cand.status.value,
                    source_type=cand.source_type,
                    source_id=cand.source_id,
                    created_at=cand.created_at,
                )
                for cand in candidates
            )
            return MemoryCandidateListResponse(items=response_items, total=len(response_items))
        except Exception as exc:
            return error_response(exc)

    @app.post("/api/v1/memories/candidates/{candidate_id}/action")
    async def memory_candidate_action(candidate_id: str, request: MemoryCandidateActionRequest):
        try:
            if request.action == "approve":
                item = memory_store.approve_candidate(candidate_id)
                return {
                    "ok": True,
                    "action": "approved",
                    "memory_id": item.memory_id,
                    "memory": MemoryItemResponse(
                        memory_id=item.memory_id,
                        scope=item.scope.value,
                        target_id=item.target_id,
                        category=item.category.value,
                        statement=item.statement,
                        confidence=item.confidence,
                        status=item.status.value,
                        source_type=item.source_type,
                        source_id=item.source_id,
                        created_at=item.created_at,
                        updated_at=item.updated_at,
                    ).model_dump(mode="json"),
                }
            elif request.action == "reject":
                memory_store.reject_candidate(candidate_id)
                return {"ok": True, "action": "rejected", "candidate_id": candidate_id}
            else:
                return JSONResponse(status_code=400, content={"code": "invalid_action", "message": f"不支持的操作: {request.action}"})
        except KeyError:
            return JSONResponse(status_code=404, content={"code": "candidate_not_found", "message": "未找到指定记忆候选。"})
        except Exception as exc:
            return error_response(exc)

    @app.get("/api/v1/skills", response_model=SkillListResponse)
    async def list_skills_endpoint(category: str | None = None, status: str = "active"):
        try:
            skills = skill_registry.list_skills(category=category, status=status)
            items = tuple(
                SkillSummaryResponse(
                    skill_id=s.skill_id,
                    version=s.version,
                    name=s.name,
                    description=s.description,
                    category=s.category.value,
                    author=s.author,
                    required_tools=s.required_tools,
                    default_budget=SkillBudgetResponse(
                        max_tokens=s.default_budget.max_tokens,
                        max_steps=s.default_budget.max_steps,
                        estimated_time_seconds=s.default_budget.estimated_time_seconds,
                    ),
                    is_builtin=s.is_builtin,
                    status=s.status.value,
                )
                for s in skills
            )
            return SkillListResponse(items=items, total=len(items))
        except Exception as exc:
            return error_response(exc)

    @app.get("/api/v1/skills/{skill_id}", response_model=SkillDetailResponse)
    async def get_skill_endpoint(skill_id: str):
        skill = skill_registry.get_skill(skill_id)
        if skill is None:
            return JSONResponse(status_code=404, content={"code": "skill_not_found", "message": f"未找到指定的 Skill: {skill_id}"})
        return SkillDetailResponse(
            skill_id=skill.skill_id,
            version=skill.version,
            name=skill.name,
            description=skill.description,
            category=skill.category.value,
            author=skill.author,
            required_tools=skill.required_tools,
            default_budget=SkillBudgetResponse(
                max_tokens=skill.default_budget.max_tokens,
                max_steps=skill.default_budget.max_steps,
                estimated_time_seconds=skill.default_budget.estimated_time_seconds,
            ),
            is_builtin=skill.is_builtin,
            status=skill.status.value,
            input_schema=skill.input_schema,
            prompt_template=skill.prompt_template,
            rules=skill.rules,
            created_at=skill.created_at,
            updated_at=skill.updated_at,
        )

    @app.post("/api/v1/skills/{skill_id}/execute", response_model=SkillExecuteApiResponse)
    async def execute_skill_endpoint(skill_id: str, request: SkillExecuteApiRequest):
        try:
            req = SkillExecutionRequest(
                skill_id=skill_id,
                inputs=request.inputs,
                conversation_id=request.conversation_id,
                project_id=request.project_id,
            )
            result = skill_runner.execute_skill(req)
            if result.status == "failed":
                return JSONResponse(
                    status_code=400,
                    content={
                        "code": "skill_execution_failed",
                        "message": result.error or result.summary,
                        "skill_id": skill_id,
                    },
                )
            return SkillExecuteApiResponse(
                skill_id=result.skill_id,
                status=result.status,
                summary=result.summary,
                content=result.content,
                structured_data=result.structured_data,
                elapsed_seconds=result.elapsed_seconds,
                tokens_consumed=result.tokens_consumed,
                error=result.error,
            )
        except Exception as exc:
            return error_response(exc)

    def _server_config_to_response(s: MCPServerConfig) -> MCPServerResponse:
        return MCPServerResponse(
            server_id=s.server_id,
            name=s.name,
            transport_type=s.transport_type.value,
            command=s.command,
            args=s.args,
            url=s.url,
            enabled=s.enabled,
            timeout_seconds=s.timeout_seconds,
            tools_cache=tuple(
                MCPToolInfoResponse(
                    name=t.name,
                    description=t.description,
                    input_schema=t.input_schema,
                )
                for t in s.tools_cache
            ),
            last_connected_at=s.last_connected_at,
            created_at=s.created_at,
            updated_at=s.updated_at,
        )

    @app.get("/api/v1/mcp/servers", response_model=MCPServerListResponse)
    async def list_mcp_servers_endpoint(enabled_only: bool = False):
        try:
            servers = mcp_manager.list_servers(enabled_only=enabled_only)
            items = tuple(_server_config_to_response(s) for s in servers)
            return MCPServerListResponse(items=items, total=len(items))
        except Exception as exc:
            return error_response(exc)

    @app.post("/api/v1/mcp/servers", response_model=MCPServerResponse)
    async def register_mcp_server_endpoint(request: CreateMCPServerRequest):
        try:
            config = MCPServerConfig(
                server_id=request.server_id,
                name=request.name,
                transport_type=MCPTransportType(request.transport_type),
                command=request.command,
                args=request.args,
                url=request.url,
                env_vars=request.env_vars,
                enabled=request.enabled,
                timeout_seconds=request.timeout_seconds,
            )
            saved = mcp_manager.register_server(config)
            return _server_config_to_response(saved)
        except Exception as exc:
            return error_response(exc)

    @app.get("/api/v1/mcp/servers/{server_id}", response_model=MCPServerResponse)
    async def get_mcp_server_endpoint(server_id: str):
        server = mcp_manager.get_server(server_id)
        if server is None:
            return JSONResponse(
                status_code=404,
                content={"code": "mcp_server_not_found", "message": f"未找到指定的 MCP Server: {server_id}"},
            )
        return _server_config_to_response(server)

    @app.delete("/api/v1/mcp/servers/{server_id}")
    async def delete_mcp_server_endpoint(server_id: str):
        deleted = mcp_manager.delete_server(server_id)
        if not deleted:
            return JSONResponse(
                status_code=404,
                content={"code": "mcp_server_not_found", "message": f"未找到指定的 MCP Server: {server_id}"},
            )
        return {"ok": True, "server_id": server_id}

    @app.post("/api/v1/mcp/servers/{server_id}/sync", response_model=MCPServerResponse)
    async def sync_mcp_server_endpoint(server_id: str):
        server = mcp_manager.get_server(server_id)
        if server is None:
            return JSONResponse(
                status_code=404,
                content={"code": "mcp_server_not_found", "message": f"未找到指定的 MCP Server: {server_id}"},
            )
        mcp_manager.sync_server_tools(server_id)
        updated = mcp_manager.get_server(server_id)
        return _server_config_to_response(updated or server)

    @app.post("/api/v1/mcp/servers/{server_id}/tools/{tool_name}/call", response_model=MCPToolCallApiResponse)
    async def call_mcp_tool_endpoint(server_id: str, tool_name: str, request: MCPToolCallApiRequest):
        try:
            result = mcp_manager.call_tool(server_id, tool_name, request.arguments)
            if result.is_error:
                return JSONResponse(
                    status_code=400,
                    content={
                        "code": "mcp_tool_call_failed",
                        "message": result.raw_text,
                        "tool_name": tool_name,
                        "server_id": server_id,
                    },
                )
            return MCPToolCallApiResponse(
                tool_name=result.tool_name,
                is_error=result.is_error,
                content=result.content,
                raw_text=result.raw_text,
            )
        except Exception as exc:
            return error_response(exc)

    @app.get("/api/v1/mcp/sse")
    async def mcp_sse_endpoint(max_events: int = 0):
        session_id, queue = mcp_sse_manager.create_session()

        async def event_generator():
            post_url = f"/api/v1/mcp/messages?session_id={session_id}"
            yield f"event: endpoint\ndata: {post_url}\n\n"
            events_sent = 1
            if max_events > 0 and events_sent >= max_events:
                mcp_sse_manager.remove_session(session_id)
                return

            try:
                while True:
                    try:
                        msg = await asyncio.wait_for(queue.get(), timeout=15.0)
                        data_str = json.dumps(msg, ensure_ascii=False)
                        yield f"event: message\ndata: {data_str}\n\n"
                        events_sent += 1
                        if max_events > 0 and events_sent >= max_events:
                            break
                    except asyncio.TimeoutError:
                        yield ": ping\n\n"
            except asyncio.CancelledError:
                pass
            finally:
                mcp_sse_manager.remove_session(session_id)

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @app.post("/api/v1/mcp/messages")
    async def mcp_post_message_endpoint(
        session_id: str = Query(...),
        request_data: dict[str, Any] = Body(...),
    ):
        if session_id not in mcp_sse_manager._sessions:
            return JSONResponse(
                status_code=404,
                content={"code": "session_not_found", "message": f"未找到活动的 SSE 会话: {session_id}"},
            )
        res = await mcp_sse_manager.handle_post_message(session_id, request_data)
        return JSONResponse(status_code=202, content={"status": "accepted", "response": res})

    @app.post("/api/v1/mcp/rpc")
    async def mcp_rpc_endpoint(request_data: dict[str, Any] = Body(...)):
        res = mcp_server_core.handle_jsonrpc(request_data)
        if res is None:
            return Response(status_code=204)
        return JSONResponse(content=res)

    @app.post("/api/v1/dag/plans/validate", response_model=DAGPlanValidationResponse)
    async def validate_dag_plan_endpoint(request: DAGPlanApiRequest):
        try:
            plan = DAGPlan(
                plan_id=request.plan_id,
                run_id=request.run_id,
                objective=request.objective,
                nodes=tuple(
                    DAGTaskNode(
                        node_id=n.node_id,
                        agent_type=n.agent_type,
                        objective=n.objective,
                        depends_on=n.depends_on,
                        skill_id=n.skill_id,
                        input_payload=n.input_payload,
                        budget=n.budget,
                    )
                    for n in request.nodes
                ),
            )
            order = dag_scheduler.validate_plan(plan)
            return DAGPlanValidationResponse(
                valid=True,
                plan_id=plan.plan_id,
                node_count=len(plan.nodes),
                topological_order=tuple(order),
            )
        except (DAGCycleError, DAGDependencyError) as exc:
            return DAGPlanValidationResponse(
                valid=False,
                plan_id=request.plan_id,
                node_count=len(request.nodes),
                topological_order=(),
                error=str(exc),
            )
        except Exception as exc:
            return error_response(exc)

    @app.post("/api/v1/dag/plans/execute", response_model=DAGExecutionResultApiResponse)
    async def execute_dag_plan_endpoint(request: DAGPlanApiRequest):
        try:
            plan = DAGPlan(
                plan_id=request.plan_id,
                run_id=request.run_id,
                objective=request.objective,
                nodes=tuple(
                    DAGTaskNode(
                        node_id=n.node_id,
                        agent_type=n.agent_type,
                        objective=n.objective,
                        depends_on=n.depends_on,
                        skill_id=n.skill_id,
                        input_payload=n.input_payload,
                        budget=n.budget,
                    )
                    for n in request.nodes
                ),
            )
            res = await dag_scheduler.execute_plan(plan, max_concurrency=request.max_concurrency)
            return DAGExecutionResultApiResponse(
                plan_id=res.plan_id,
                run_id=res.run_id,
                status=res.status,
                completed_nodes=res.completed_nodes,
                failed_nodes=res.failed_nodes,
                cancelled_nodes=res.cancelled_nodes,
                skipped_nodes=res.skipped_nodes,
                node_results=res.node_results,
                events_count=res.events_count,
                duration_seconds=res.duration_seconds,
                error=res.error,
            )
        except (DAGCycleError, DAGDependencyError) as exc:
            return JSONResponse(
                status_code=400,
                content={"code": "invalid_dag_plan", "message": str(exc), "plan_id": request.plan_id},
            )
        except Exception as exc:
            return error_response(exc)

    @app.get("/api/v1/runs/{run_id}/agent-events", response_model=AgentEventListResponse)
    async def list_agent_events_endpoint(run_id: str, event_type: str | None = None):
        try:
            events = event_bus.list_events(run_id=run_id, event_type=event_type)
            items = tuple(
                AgentEventApiResponse(
                    event_id=e.event_id,
                    run_id=e.run_id,
                    agent_id=e.agent_id,
                    event_type=e.event_type,
                    payload=e.payload,
                    causation_event_id=e.causation_event_id,
                    created_at=e.created_at,
                )
                for e in events
            )
            return AgentEventListResponse(items=items, total=len(items))
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

    @app.get("/api/v1/library")
    async def library_overview() -> dict[str, Any]:
        manifest_path = Path((config_paths or {}).get("corpus_manifest", ""))
        registry_path = repository.database_path.with_name("library-registry.json")
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {}
            rows = [{**row, "_manifest_indexed": True} for row in payload.get("files", [])]
            registry = json.loads(registry_path.read_text(encoding="utf-8")) if registry_path.is_file() else []
            rows.extend(registry)
        except (OSError, ValueError):
            return {"total": 0, "imported": 0, "index_status": "清单不可读", "items": []}
        items = []
        seen = set()
        for row in rows:
            identity = row.get("document_id") or row.get("source_snapshot_id") or row.get("relative_path")
            if identity in seen:
                continue
            seen.add(identity)
            relative = str(row.get("relative_path", ""))
            segment_count = int(row.get("segment_count", 0) or 0)
            characters = int(row.get("character_count", 0) or 0)
            segments_ref = row.get("segments_artifact_id", "")
            title = row.get("title")
            stem = Path(relative).stem
            is_numeric_id = bool(re.match(r"^(?:arXiv:)?(?:\d{4}\.\d{4,5}|[a-z-]+(?:\.[a-z]{2})?/\d{7})(?:v\d+)?$", stem, re.IGNORECASE)) or stem.isdigit()
            if segments_ref.startswith("artifact-sha256-"):
                try:
                    segment_path = repository.artifact_store.path_for_digest(segments_ref.removeprefix("artifact-sha256-"))
                    segment_payload = json.loads(segment_path.read_text(encoding="utf-8"))
                    segments = segment_payload.get("segments", [])
                    segment_count = len(segments) or segment_count
                    characters = sum(len(str(segment.get("text", ""))) for segment in segments)
                    if is_numeric_id:
                        if segments_ref in _LIBRARY_TITLE_CACHE:
                            title = _LIBRARY_TITLE_CACHE[segments_ref]
                        elif segments:
                            first_text = str(segments[0].get("text", "") or "")
                            lines = [l.strip() for l in first_text.splitlines() if l.strip()]
                            if lines:
                                cand = lines[0].lstrip("#").strip().strip("*").strip()
                                if len(lines) > 1 and len(cand) < 120:
                                    next_l = lines[1].strip().lstrip("#").strip().strip("*").strip()
                                    if next_l and not any(next_l.lower().startswith(x) for x in ["abstract", "author", "by ", "http", "doi", "dept", "university", "institute", "arxiv:"]) and (
                                        cand.endswith(":") or cand.endswith("-") or cand.endswith("and") or next_l[0].islower() or next_l.lower().startswith(("with ", "for ", "and ", "in ", "a ", "on ", "using ", "of "))
                                    ):
                                        cand = f"{cand} {next_l}"
                                if 0 < len(cand) <= 250:
                                    title = cand
                                    _LIBRARY_TITLE_CACHE[segments_ref] = cand
                except (OSError, ValueError, json.JSONDecodeError):
                    pass
            if not title:
                title = stem or relative
            item_status = row.get("status", "unknown")
            if item_status == "imported" and not row.get("_manifest_indexed"):
                item_status = "parsed"
            items.append({
                "title": title,
                "relative_path": relative,
                "path": row.get("path", ""),
                "sha256": row.get("sha256", ""),
                "record_id": row.get("document_id") or row.get("paper_id") or identity,
                "paper_id": row.get("paper_id", ""),
                "document_id": row.get("document_id", ""),
                "source_artifact_id": row.get("source_artifact_id", ""),
                "segments_artifact_id": row.get("segments_artifact_id", ""),
                "assets_artifact_id": row.get("assets_artifact_id", ""),
                "asset_count": int(row.get("asset_count", 0) or 0),
                "source": row.get("source_snapshot_id") or row.get("document_id") or relative,
                "media_type": "PDF" if row.get("document_id") and (relative.lower().endswith(".pdf") or row.get("paper_id")) else "论文元数据" if row.get("paper_id") else "Markdown",
                "status": item_status,
                "segment_count": segment_count,
                "chunk_count": segment_count,
                "character_count": characters,
                "size_bytes": int(row.get("size_bytes", 0) or 0),
                "source_type": row.get("source_type") or ("网络论文" if row.get("paper_id") or str(row.get("source", "")).startswith("arXiv:") or str(row.get("relative_path", "")).startswith("arXiv:") else "本地文档"),
                "authors": row.get("authors", []),
                "year": row.get("year"),
                "doi": row.get("doi"),
                "arxiv_id": row.get("arxiv_id"),
                "error": row.get("error"),
                "usage_count": int(row.get("usage_count", 0) or 0),
                "usage_records": row.get("usage_records", [])[-10:],
                "fetch_attempts": row.get("fetch_attempts", []),
                "fetch_failure_artifact_id": row.get("fetch_failure_artifact_id"),
                "manifest_indexed": bool(row.get("_manifest_indexed")),
            })
        imported = sum(1 for item in items if item["status"] == "knowledge_ready" or item.get("manifest_indexed"))
        return {
            "total": len(items),
            "imported": imported,
            "processing": sum(1 for item in items if item["status"] in {"pending", "processing", "fetch_queued", "fetching", "parsing", "indexing"}),
            "failed": sum(1 for item in items if item["status"] in {"failed", "fetch_failed", "parse_failed", "index_failed"}),
            "character_count": sum(item["character_count"] for item in items),
            "size_bytes": sum(item["size_bytes"] for item in items),
            "source_counts": {source: sum(1 for item in items if item["source_type"] == source) for source in {item["source_type"] for item in items}},
            "index_status": f"{imported} 份资料可用于问答" if imported else "未就绪",
            "manifest": manifest_path.name if manifest_path.is_file() else "",
            "items": items,
        }

    def _query_tokens(value: str) -> tuple[str, ...]:
        return tuple(dict.fromkeys(re.findall(r"[\u3400-\u9fff]+|[a-zA-Z0-9][a-zA-Z0-9._:/-]*", value.lower())))

    def _token_span(text: str, token: str) -> tuple[int, int] | None:
        if re.search(r"[\u3400-\u9fff]", token):
            index = text.lower().find(token)
            return (index, index + len(token)) if index >= 0 else None
        match = re.search(rf"(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])", text, re.IGNORECASE)
        return match.span() if match else None

    def _tokens_match_nearby(text: str, tokens: tuple[str, ...], max_span: int = 220) -> tuple[bool, list[tuple[int, int]]]:
        spans = [_token_span(text, token) for token in tokens]
        present = [span for span in spans if span is not None]
        if len(present) != len(tokens):
            return False, present
        return max(span[1] for span in present) - min(span[0] for span in present) <= max_span, present

    @app.get("/api/v1/library/search")
    async def search_library_documents(
        q: str = Query("", max_length=200),
        status: Literal["", "ready", "metadata", "processing", "failed"] = "",
        offset: int = Query(0, ge=0),
        limit: int = Query(30, ge=1, le=100),
    ) -> dict[str, Any]:
        overview = await library_overview()
        groups = {
            "ready": {"knowledge_ready", "imported"},
            "metadata": {"metadata_saved", "oa_unavailable", "parsed"},
            "processing": {"pending", "processing", "fetch_queued", "fetching", "parsing", "indexing"},
            "failed": {"failed", "fetch_failed", "parse_failed", "index_failed"},
        }
        tokens = _query_tokens(q)
        matches = []
        for item in overview.get("items", []):
            if status and item.get("status") not in groups[status]:
                continue
            metadata = " ".join(str(value) for value in (
                item.get("title", ""), item.get("relative_path", ""), item.get("source_type", ""),
                item.get("media_type", ""), item.get("year", ""), item.get("doi", ""), item.get("arxiv_id", ""),
                " ".join(item.get("authors", [])),
            ) if value)
            metadata_spans = [_token_span(metadata, token) for token in tokens]
            match_kind = "all" if not tokens else "metadata" if all(metadata_spans) else None
            snippet = ""
            content_score = 0
            if tokens and match_kind is None:
                artifact_id = str(item.get("segments_artifact_id", ""))
                if artifact_id.startswith("artifact-sha256-"):
                    try:
                        path = repository.artifact_store.path_for_digest(artifact_id.removeprefix("artifact-sha256-"))
                        payload = json.loads(path.read_text(encoding="utf-8"))
                        for segment in payload.get("segments", []):
                            text = str(segment.get("text", ""))
                            nearby, spans = _tokens_match_nearby(text, tokens)
                            if nearby:
                                anchor = min(span[0] for span in spans)
                                start = max(0, anchor - 90)
                                end = min(len(text), start + 420)
                                snippet = re.sub(r"\s+", " ", text[start:end]).strip()
                                content_score = sum(len(re.findall(rf"(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])", text, re.IGNORECASE)) if not re.search(r"[\u3400-\u9fff]", token) else text.count(token) for token in tokens)
                                match_kind = "content"
                                break
                    except (OSError, ValueError, json.JSONDecodeError):
                        pass
            if match_kind:
                score = 200 if match_kind == "metadata" else 100 + content_score if match_kind == "content" else 0
                matches.append({**item, "match_kind": match_kind, "match_snippet": snippet, "search_score": score})
        matches.sort(key=lambda item: (-item["search_score"], str(item.get("title", "")).casefold()))
        page = matches[offset:offset + limit]
        return {"query": q, "offset": offset, "limit": limit, "total": len(matches), "has_more": offset + len(page) < len(matches), "items": page}

    @app.get("/api/v1/library/documents/{document_id}")
    async def library_document_detail(document_id: str) -> dict[str, Any]:
        overview = await library_overview()
        item = next((row for row in overview["items"] if row.get("document_id") == document_id or row.get("paper_id") == document_id), None)
        if item is None:
            return JSONResponse(status_code=404, content={"code": "document_not_found", "message": "资料不存在。"})
        artifact_id = item.get("segments_artifact_id", "")
        if not artifact_id.startswith("artifact-sha256-"):
            return {"document_id": document_id, "metadata": item, "segments": [], "versions": item.get("versions", [])}
        try:
            path = repository.artifact_store.path_for_digest(artifact_id.removeprefix("artifact-sha256-"))
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            return {"document_id": document_id, "segments": []}
        return {"document_id": document_id, "metadata": item, "segments": payload.get("segments", []), "versions": item.get("versions", [])}

    @app.post("/api/v1/library/documents/{document_id}/research", response_model=ResearchTaskAcceptedResponse)
    async def research_from_library_document(document_id: str, request: LibraryResearchRequest | None = None):
        overview = await library_overview()
        item = next((row for row in overview["items"] if row.get("document_id") == document_id or row.get("paper_id") == document_id), None)
        if item is None:
            return JSONResponse(status_code=404, content={"code": "document_not_found", "message": "资料不存在。"})
        title = item.get("title") or document_id
        objective = (request.objective if request else None) or f"请基于资料《{title}》开展研究，概括其核心问题、方法、证据和局限。"
        conversation_id = f"conv-{uuid4().hex}"
        try:
            result = orchestrator.submit(TaskSubmission(task_kind="deep_research", input={"objective": objective, "conversation_id": conversation_id, "library_document_id": document_id}, requested_agent="durable_verified_research@v1"))
            if chat_service is not None:
                chat_service.record_research_message(conversation_id, "user", objective, result.run_id)
            return ResearchTaskAcceptedResponse(task_id=result.task_id, run_id=result.run_id, created=result.created, state=query_service.get_run(result.run_id).state)
        except Exception as exc:
            return error_response(exc)

    @app.post("/api/v1/library/documents/{document_id}/restore")
    async def restore_library_document(document_id: str, request: LibraryRestoreRequest):
        path, rows = paper_registry()
        row = next((item for item in rows if item.get("document_id") == document_id or item.get("paper_id") == document_id), None)
        if row is None:
            return JSONResponse(status_code=404, content={"code": "document_not_found", "message": "资料不存在。"})
        versions = list(row.get("versions", []))
        if request.version_index >= len(versions):
            return JSONResponse(status_code=404, content={"code": "document_version_not_found", "message": "历史版本不存在。"})
        version = versions[request.version_index]
        restored = {**row}
        for key in ("status", "segments_artifact_id", "source_artifact_id", "document_id", "error"):
            if key in version and version[key] is not None:
                restored[key] = version[key]
        restored["restored_from_version"] = request.version_index
        restored["restored_at"] = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        save_document_row(restored)
        return {"status": restored.get("status", "parsed"), "document_id": document_id, "restored_from_version": request.version_index}

    def retrieval_documents(document: Any) -> tuple[RetrievalDocument, ...]:
        return tuple(
            RetrievalDocument(
                segment.segment_id,
                segment.text,
                document.source_snapshot.source_id,
                segment.locator,
            )
            for segment in document.segments
        )

    async def index_document(document: Any) -> dict[str, Any]:
        if retrieval_pipeline is None:
            raise RuntimeError("知识库索引服务未就绪")
        res = await asyncio.to_thread(
            retrieval_pipeline.add_documents,
            retrieval_documents(document),
            producer_step_id="step-library-incremental-index",
        )
        if (
            getattr(retrieval_pipeline, "is_multimodal_active", lambda: False)()
            and getattr(document, "assets", None)
            and getattr(retrieval_pipeline, "image_embedding", None) is not None
            and getattr(retrieval_pipeline, "image_index", None) is not None
        ):
            try:
                from conflux_weave.multimodal_indexing import build_image_index
                await asyncio.to_thread(
                    build_image_index,
                    document.assets,
                    retrieval_pipeline.image_embedding,
                    repository.artifact_store,
                    retrieval_pipeline.image_index,
                    producer_step_id="step-library-asset-index",
                )
            except Exception as asset_exc:
                pass
        return res

    async def index_segments(source_snapshot_id: str, segments: list[dict[str, Any]]) -> dict[str, Any]:
        if retrieval_pipeline is None:
            raise RuntimeError("知识库索引服务未就绪")
        documents = tuple(
            RetrievalDocument(
                str(segment["segment_id"]),
                str(segment["text"]),
                source_snapshot_id,
                dict(segment.get("locator") or {}),
            )
            for segment in segments
            if str(segment.get("text", "")).strip()
        )
        if not documents:
            raise ValueError("资料没有可索引的正文片段")
        return await asyncio.to_thread(
            retrieval_pipeline.add_documents,
            documents,
            producer_step_id="step-library-reindex",
        )

    def save_document_row(row: dict[str, Any]) -> None:
        registry_path = repository.database_path.with_name("library-registry.json")
        try:
            existing = json.loads(registry_path.read_text(encoding="utf-8")) if registry_path.is_file() else []
        except (OSError, ValueError):
            existing = []
        identity = row.get("document_id") or row.get("relative_path")
        prior = next((item for item in existing if (item.get("document_id") or item.get("relative_path")) == identity), None)
        if prior and prior != row:
            history = list(prior.get("versions", []))
            history.append({key: prior.get(key) for key in ("status", "segments_artifact_id", "source_artifact_id", "updated_at", "error") if key in prior})
            row = {**row, "versions": history[-20:]}
        row.setdefault("updated_at", datetime.now(UTC).isoformat().replace("+00:00", "Z"))
        existing = [item for item in existing if (item.get("document_id") or item.get("relative_path")) != identity]
        existing.append(row)
        registry_path.parent.mkdir(parents=True, exist_ok=True)
        registry_path.write_text(json.dumps(existing, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def mark_library_usage(source_snapshot_ids: list[str], *, run_id: str | None = None) -> None:
        wanted = {item for item in source_snapshot_ids if item}
        if not wanted:
            return
        path, rows = paper_registry()
        changed = False
        for row in rows:
            identity = str(row.get("source_snapshot_id") or row.get("document_id") or "")
            if identity not in wanted:
                continue
            row["usage_count"] = int(row.get("usage_count", 0) or 0) + 1
            records = list(row.get("usage_records", []))
            records.append({"used_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"), "run_id": run_id})
            row["usage_records"] = records[-50:]
            changed = True
        if changed:
            path.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    _manifest_cache: dict[str, dict[str, Any]] = {}

    def _load_manifest_payload(art_id: str) -> dict[str, Any] | None:
        if art_id in _manifest_cache:
            return _manifest_cache[art_id]
        if not str(art_id).startswith("artifact-sha256-"):
            return None
        try:
            p = repository.artifact_store.path_for_digest(str(art_id).removeprefix("artifact-sha256-"))
            if p.is_file():
                payload = json.loads(p.read_text(encoding="utf-8"))
                _manifest_cache[art_id] = payload
                return payload
        except Exception:
            pass
        return None

    async def get_or_extract_document_assets(item: dict[str, Any]) -> dict[str, Any] | None:
        art_id = item.get("assets_artifact_id")
        if art_id and str(art_id).startswith("artifact-sha256-"):
            payload = _load_manifest_payload(str(art_id))
            if payload:
                return payload

        source_art_id = item.get("source_artifact_id")
        media_type = str(item.get("media_type", ""))
        rel = str(item.get("relative_path", ""))
        is_pdf = media_type == "PDF" or rel.lower().endswith(".pdf") or item.get("paper_id")
        if source_art_id and str(source_art_id).startswith("artifact-sha256-") and is_pdf:
            try:
                source_path = repository.artifact_store.path_for_digest(str(source_art_id).removeprefix("artifact-sha256-"))
                if source_path.is_file():
                    from conflux_weave.document_assets import PDFAssetExtractor
                    raw = source_path.read_bytes()
                    extractor = PDFAssetExtractor(repository.artifact_store)
                    doc_id = str(item.get("document_id") or item.get("record_id") or "doc")
                    snap_id = str(item.get("source") or item.get("source_snapshot_id") or doc_id)
                    segments_art_id = str(item.get("segments_artifact_id", ""))
                    segments = ()
                    if segments_art_id.startswith("artifact-sha256-"):
                        try:
                            sp = repository.artifact_store.path_for_digest(segments_art_id.removeprefix("artifact-sha256-"))
                            seg_payload = json.loads(sp.read_text(encoding="utf-8"))
                            segments = tuple(seg_payload.get("segments", ()))
                        except Exception:
                            pass
                    manifest, art = extractor.extract_document_assets(
                        raw,
                        document_id=doc_id,
                        source_snapshot_id=snap_id,
                        source_artifact_id=str(source_art_id),
                        parent_segments=segments,
                    )
                    item["assets_artifact_id"] = art.artifact_id
                    item["asset_count"] = manifest.asset_count
                    save_document_row(item)
                    manifest_dict = manifest.to_dict()
                    _manifest_cache[art.artifact_id] = manifest_dict
                    return manifest_dict
            except Exception:
                pass
        return None

    async def resolve_registered_asset(asset_id: str) -> dict[str, Any] | None:
        overview = await library_overview()
        for doc in overview.get("items", []):
            art_id = doc.get("assets_artifact_id")
            if not art_id:
                continue
            payload = _load_manifest_payload(str(art_id))
            if not payload:
                continue
            for a in payload.get("assets", []):
                if a.get("asset_id") == asset_id:
                    return a
        return None
    @app.get("/api/v1/library/assets")
    async def library_all_assets(
        document_id: str | None = None,
        asset_type: str | None = None,
        limit: int = Query(60, ge=1, le=300),
    ):
        overview = await library_overview()
        items = overview.get("items", [])
        all_assets = []
        for item in items:
            doc_id = str(item.get("document_id") or item.get("paper_id") or item.get("record_id") or "")
            if document_id and doc_id != document_id:
                continue
            doc_title = item.get("title") or doc_id
            payload = await get_or_extract_document_assets(item)
            if not payload:
                continue
            for a in payload.get("assets", []):
                if asset_type and a.get("asset_type") != asset_type:
                    continue
                detail = _build_asset_detail_response(a).model_dump()
                detail["document_title"] = doc_title
                all_assets.append(detail)
                if len(all_assets) >= limit:
                    break
            if len(all_assets) >= limit:
                break
        return {
            "total": len(all_assets),
            "items": all_assets,
        }

    @app.get("/api/v1/library/documents/{document_id}/assets", response_model=DocumentAssetsResponse)
    async def library_document_assets(document_id: str):
        overview = await library_overview()
        item = next((row for row in overview["items"] if row.get("document_id") == document_id or row.get("paper_id") == document_id or row.get("record_id") == document_id), None)
        if item is None:
            return JSONResponse(status_code=404, content={"code": "document_not_found", "message": "资料不存在。"})

        assets_payload = await get_or_extract_document_assets(item)
        if not assets_payload:
            return DocumentAssetsResponse(
                document_id=document_id,
                assets_artifact_id=None,
                asset_count=0,
                unique_content_count=0,
                status_counts={},
                items=(),
            )

        items = tuple(_build_asset_detail_response(a) for a in assets_payload.get("assets", []))
        return DocumentAssetsResponse(
            document_id=document_id,
            assets_artifact_id=item.get("assets_artifact_id") or assets_payload.get("assets_artifact_id"),
            asset_count=len(items),
            unique_content_count=int(assets_payload.get("unique_content_count", len(items))),
            status_counts=assets_payload.get("status_counts", {}),
            items=items,
        )

    @app.get("/api/v1/library/assets/{asset_id}", response_model=DocumentAssetDetailResponse)
    async def library_asset_detail(asset_id: str):
        asset = await resolve_registered_asset(asset_id)
        if asset is None:
            return JSONResponse(status_code=404, content={"code": "asset_not_found", "message": "图片资产不存在。"})
        return _build_asset_detail_response(asset)

    @app.get("/api/v1/library/assets/{asset_id}/content")
    async def library_asset_content(asset_id: str, variant: Literal["original", "thumbnail"] = "original"):
        asset = await resolve_registered_asset(asset_id)
        if asset is None:
            return JSONResponse(status_code=404, content={"code": "asset_not_found", "message": "图片资产不存在。"})

        if variant == "thumbnail":
            ref = asset.get("thumbnail_artifact_ref")
            if not ref:
                return JSONResponse(status_code=404, content={"code": "thumbnail_not_found", "message": "该资产无缩略图。"})
            media_type = "image/png"
        else:
            if asset.get("extraction_status") == "failed" or not asset.get("artifact_ref"):
                return JSONResponse(
                    status_code=422,
                    content={
                        "code": "asset_content_unavailable",
                        "message": "该图片资产未成功提取原始图片或无有效内容。",
                        "asset_id": asset_id,
                        "extraction_status": asset.get("extraction_status", "failed"),
                        "warnings": asset.get("warnings", []),
                    },
                )
            ref = asset.get("artifact_ref")
            media_type = asset.get("media_type", "image/png")

        if media_type not in ALLOWED_IMAGE_MIMES:
            return JSONResponse(
                status_code=415,
                content={"code": "unsupported_media_type", "message": f"不支持的图片类型: {media_type}"},
            )

        if not str(ref).startswith("artifact-sha256-"):
            return JSONResponse(status_code=404, content={"code": "artifact_file_missing", "message": "图片底层文件标识异常。"})

        digest = str(ref).removeprefix("artifact-sha256-")
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            return JSONResponse(status_code=400, content={"code": "invalid_artifact_digest", "message": "非法的摘要格式。"})

        try:
            file_path = repository.artifact_store.path_for_digest(digest)
        except Exception:
            return JSONResponse(status_code=404, content={"code": "artifact_file_missing", "message": "图片底层文件缺失。"})

        if not file_path.is_file():
            return JSONResponse(status_code=404, content={"code": "artifact_file_missing", "message": "图片底层文件缺失。"})

        file_size = file_path.stat().st_size
        if file_size > MAX_IMAGE_SIZE_BYTES:
            return JSONResponse(
                status_code=413,
                content={"code": "asset_too_large", "message": f"图片资产超过最大允许大小 ({MAX_IMAGE_SIZE_BYTES // (1024*1024)}MB)。"},
            )

        raw_bytes = file_path.read_bytes()
        if not _validate_image_magic_bytes(raw_bytes, media_type):
            return JSONResponse(
                status_code=422,
                content={"code": "corrupted_asset", "message": "图片内容已损坏或格式与元数据不匹配。"},
            )

        headers = {
            "Content-Type": media_type,
            "Content-Disposition": "inline",
            "Cache-Control": "public, max-age=31536000, immutable",
            "X-Content-Type-Options": "nosniff",
            "Content-Length": str(file_size),
        }
        return Response(content=raw_bytes, media_type=media_type, headers=headers)


    @app.post("/api/v1/library/documents/{document_id}/index")
    async def reindex_library_document(document_id: str) -> dict[str, Any]:
        registry_path = repository.database_path.with_name("library-registry.json")
        try:
            rows = json.loads(registry_path.read_text(encoding="utf-8")) if registry_path.is_file() else []
        except (OSError, ValueError):
            rows = []
        row = next((item for item in rows if item.get("document_id") == document_id or item.get("paper_id") == document_id), None)
        if row is None:
            return JSONResponse(status_code=404, content={"code": "document_not_found", "message": "资料不存在。"})
        artifact_id = str(row.get("segments_artifact_id", ""))
        if not artifact_id.startswith("artifact-sha256-"):
            return JSONResponse(status_code=422, content={"code": "document_segments_unavailable", "message": "资料没有可用于索引的正文分段。"})
        try:
            path = repository.artifact_store.path_for_digest(artifact_id.removeprefix("artifact-sha256-"))
            payload = json.loads(path.read_text(encoding="utf-8"))
            save_document_row({**row, "status": "indexing", "error": None})
            result = await index_segments(str(row.get("source_snapshot_id") or row.get("document_id")), list(payload.get("segments", [])))
        except Exception as exc:
            save_document_row({**row, "status": "index_failed", "error": str(exc)[:500]})
            return JSONResponse(status_code=502, content={"code": "document_index_failed", "message": f"加入知识库失败：{exc}"})
        save_document_row({**row, "status": "knowledge_ready", "error": None, "index_added_count": result.get("added_count", 0), "embedding_artifacts": result.get("embedding_artifacts", [])})
        return {"status": "knowledge_ready", "document_id": row.get("document_id"), "added_count": result.get("added_count", 0)}

    @app.post("/api/v1/library/documents/batch")
    async def batch_library_documents(request: LibraryBatchRequest) -> dict[str, Any]:
        """Apply one explicit lifecycle action to a bounded set of documents."""
        registry_path, rows = paper_registry()
        requested = tuple(dict.fromkeys(item.strip() for item in request.document_ids if item.strip()))
        if not requested:
            return JSONResponse(status_code=422, content={"code": "document_ids_empty", "message": "至少选择一份资料。"})
        selected = [row for row in rows if row.get("document_id") in requested or row.get("paper_id") in requested]
        if not selected:
            return JSONResponse(status_code=404, content={"code": "document_not_found", "message": "未找到可操作的资料。"})
        if request.action == "remove":
            if retrieval_pipeline is None:
                return JSONResponse(status_code=503, content={"code": "index_unavailable", "message": "知识库索引服务未就绪。"})
            chunk_ids: list[str] = []
            for row in selected:
                artifact_id = str(row.get("segments_artifact_id", ""))
                if not artifact_id.startswith("artifact-sha256-"):
                    continue
                try:
                    path = repository.artifact_store.path_for_digest(artifact_id.removeprefix("artifact-sha256-"))
                    payload = json.loads(path.read_text(encoding="utf-8"))
                    chunk_ids.extend(str(segment.get("segment_id")) for segment in payload.get("segments", []) if segment.get("segment_id"))
                except (OSError, ValueError, json.JSONDecodeError):
                    continue
            try:
                result = await asyncio.to_thread(retrieval_pipeline.remove_documents, tuple(chunk_ids))
            except Exception as exc:
                return JSONResponse(status_code=422, content={"code": "document_remove_failed", "message": str(exc)})
            for row in selected:
                save_document_row({**row, "status": "parsed", "removed_from_knowledge_base": True, "error": None})
            return {"status": "parsed", "action": "remove", "document_count": len(selected), "deleted_count": result.get("deleted_count", 0)}

        indexed = 0
        failures: list[dict[str, str]] = []
        for row in selected:
            identifier = str(row.get("document_id") or row.get("paper_id"))
            try:
                response = await reindex_library_document(identifier)
                if isinstance(response, JSONResponse) and response.status_code >= 400:
                    raise RuntimeError(f"索引失败（HTTP {response.status_code}）")
                indexed += 1
            except Exception as exc:
                failures.append({"document_id": identifier, "error": str(exc)})
        return {"status": "partial" if failures else "knowledge_ready", "action": "index", "document_count": len(selected), "indexed_count": indexed, "failures": failures}

    @app.post("/api/v1/library/documents")
    async def import_library_document(request: Request, filename: str = Query(..., min_length=1, max_length=240)) -> dict[str, Any]:
        safe_name = Path(filename).name
        if Path(safe_name).suffix.lower() not in {".pdf", ".md", ".markdown"}:
            return JSONResponse(status_code=400, content={"code": "unsupported_document", "message": "仅支持 PDF 或 Markdown 文件。"})
        payload = await request.body()
        if not payload:
            return JSONResponse(status_code=400, content={"code": "empty_document", "message": "文件内容为空。"})
        import tempfile
        with tempfile.NamedTemporaryFile(prefix="library-", suffix=Path(safe_name).suffix, delete=False) as handle:
            handle.write(payload)
            temporary_path = Path(handle.name)
        try:
            imported = LocalDocumentImporter(repository.artifact_store, extract_assets=True).import_path(temporary_path)
        except (OSError, UnicodeDecodeError, UnsupportedDocumentError, ValueError) as exc:
            registry_path = repository.database_path.with_name("library-registry.json")
            try:
                existing = json.loads(registry_path.read_text(encoding="utf-8")) if registry_path.is_file() else []
            except (OSError, ValueError): existing = []
            existing.append({"relative_path": safe_name, "status": "failed", "error": str(exc), "size_bytes": len(payload)})
            registry_path.write_text(json.dumps(existing, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            return JSONResponse(status_code=400, content={"code": "document_invalid", "message": str(exc)})
        finally:
            temporary_path.unlink(missing_ok=True)
        if imported.media_type == "application/pdf" and not imported.segments:
            registry_path = repository.database_path.with_name("library-registry.json")
            try:
                existing = json.loads(registry_path.read_text(encoding="utf-8")) if registry_path.is_file() else []
            except (OSError, ValueError): existing = []
            existing.append({"relative_path": safe_name, "document_id": imported.document_id, "source_snapshot_id": imported.source_snapshot.source_id, "source_artifact_id": imported.source_artifact.artifact_id, "status": "parse_failed", "error": "PDF 未提取到有效正文。", "size_bytes": len(payload)})
            registry_path.write_text(json.dumps(existing, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            return JSONResponse(status_code=422, content={"code": "document_parse_quality_failed", "message": "PDF 已保存，但未提取到有效正文。"})
        row = {"relative_path": safe_name, "document_id": imported.document_id, "source_snapshot_id": imported.source_snapshot.source_id, "source_artifact_id": imported.source_artifact.artifact_id, "segments_artifact_id": imported.segments_artifact.artifact_id, "status": "parsed", "segment_count": len(imported.segments), "character_count": sum(len(segment.text) for segment in imported.segments), "size_bytes": len(payload)}
        if getattr(imported, "assets_artifact", None) is not None:
            row["assets_artifact_id"] = imported.assets_artifact.artifact_id
            row["asset_count"] = len(getattr(imported, "assets", ()))
        save_document_row(row)
        if retrieval_pipeline is None:
            return {"status": "parsed", "document_id": imported.document_id, "source_snapshot_id": imported.source_snapshot.source_id, "segment_count": len(imported.segments), "media_type": imported.media_type, "message": "文档已解析，但知识库索引服务未就绪。"}
        save_document_row({**row, "status": "indexing"})
        try:
            index_result = await index_document(imported)
        except Exception as exc:
            save_document_row({**row, "status": "index_failed", "error": str(exc)[:500]})
            return JSONResponse(status_code=502, content={"code": "document_index_failed", "message": f"文档已解析，但加入知识库失败：{exc}", "document_id": imported.document_id})
        save_document_row({**row, "status": "knowledge_ready", "index_added_count": index_result.get("added_count", 0), "embedding_artifacts": index_result.get("embedding_artifacts", [])})
        return {"status": "knowledge_ready", "document_id": imported.document_id, "source_snapshot_id": imported.source_snapshot.source_id, "segment_count": len(imported.segments), "media_type": imported.media_type}

    @app.post("/api/v1/library/search", response_model=MultimodalRetrievalResultResponse)
    async def library_multimodal_search(
        query: str = Query(..., min_length=1, max_length=400),
        top_k: int = Query(10, ge=1, le=50),
        image_k: int = Query(5, ge=0, le=20),
    ):
        if retrieval_pipeline is None:
            return JSONResponse(status_code=503, content={"code": "index_unavailable", "message": "知识库检索服务未就绪。"})
        try:
            if hasattr(retrieval_pipeline, "search"):
                run = await asyncio.to_thread(
                    retrieval_pipeline.search,
                    query,
                    fusion_k=top_k,
                    image_k=image_k,
                )
                if hasattr(run, "fused_hits"):
                    hit_responses = tuple(
                        MultimodalFusionHitResponse(
                            hit_id=h.hit_id,
                            score=h.score,
                            rank=h.rank,
                            modality=h.modality,
                            source_snapshot_id=h.source_snapshot_id,
                            locator=h.locator,
                            text=h.text,
                            asset_id=h.asset_id,
                            artifact_ref=h.artifact_ref,
                            thumbnail_artifact_ref=h.thumbnail_artifact_ref,
                            page=h.page,
                            bbox=h.bbox,
                            coordinate_space=h.coordinate_space,
                            parent_chunk_ids=h.parent_chunk_ids,
                            embedding_model=h.embedding_model,
                            index_version=h.index_version,
                        )
                        for h in run.fused_hits
                    )
                    return MultimodalRetrievalResultResponse(
                        query=query,
                        text_hits_count=len(run.text_run.final.hits) if run.text_run else 0,
                        image_hits_count=len(run.image_hits),
                        fusion_strategy=run.fusion_strategy,
                        fused_hits=hit_responses,
                    )
            text_run = await asyncio.to_thread(retrieval_pipeline.search, query)
            hit_responses = tuple(
                MultimodalFusionHitResponse(
                    hit_id=h.document_id,
                    score=h.score,
                    rank=h.rank,
                    modality="text",
                    source_snapshot_id=h.source_snapshot_id or "",
                    locator=h.locator or {},
                    text="",
                )
                for h in text_run.final.hits[:top_k]
            )
            return MultimodalRetrievalResultResponse(
                query=query,
                text_hits_count=len(text_run.final.hits),
                image_hits_count=0,
                fusion_strategy="text_only",
                fused_hits=hit_responses,
            )
        except Exception as exc:
            return JSONResponse(status_code=500, content={"code": "retrieval_failed", "message": str(exc)})

    @app.post("/api/v1/library/papers/search")
    async def search_library_papers(
        query: str = Query(..., min_length=1, max_length=400),
        max_results: int | None = Query(None, ge=1, le=100),
        sources: str = Query("openalex,arxiv"),
        year_from: int | None = Query(None, ge=1900, le=2100),
        year_to: int | None = Query(None, ge=1900, le=2100),
        oa_only: bool = False,
        sort: Literal["relevance", "newest", "impact"] = "relevance",
        limit: int | None = Query(None, ge=1, le=100),
    ) -> dict[str, Any]:
        val_limit = limit if isinstance(limit, int) else None
        val_max = max_results if isinstance(max_results, int) else None
        target_results = val_limit or val_max or 20
        target_results = max(1, min(target_results, 100))

        val_year_from = year_from if isinstance(year_from, int) else None
        val_year_to = year_to if isinstance(year_to, int) else None
        if val_year_from and val_year_to and val_year_from > val_year_to:
            return JSONResponse(status_code=400, content={"code": "invalid_year_range", "message": "起始年份不能晚于结束年份。"})

        def contains_cjk(value: str) -> bool:
            return bool(re.search(r"[\u3400-\u9fff]", value))

        queries = [query]
        openalex_query = query
        required_terms: tuple[str, ...] = ()
        required_concepts: tuple[tuple[str, ...], ...] = ()
        identifier_kind = "doi" if re.fullmatch(r"(?:https?://(?:dx\.)?doi\.org/|doi:\s*)?10\.\d{4,9}/\S+", query.strip(), re.IGNORECASE) else "arxiv" if re.fullmatch(r"(?:arxiv:\s*)?(?:\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Z]{2})?/\d{7})(?:v\d+)?", query.strip(), re.IGNORECASE) else None
        understanding: dict[str, Any] = {"status": "identifier" if identifier_kind else "direct", "queries": queries, "identifier_kind": identifier_kind}

        stopwords = {
            "a", "an", "and", "for", "in", "of", "the", "to", "with", "based", "systems",
            "i", "me", "my", "we", "our", "you", "your", "want", "would", "like", "find",
            "looking", "look", "search", "searching", "about", "regarding", "discussing",
            "paper", "papers", "article", "articles", "study", "studies", "literature",
            "show", "give", "tell", "need", "can", "could", "please", "help", "how", "what",
            "which", "where", "when", "why", "who", "is", "are", "was", "were", "be", "been",
            "being", "have", "has", "had", "do", "does", "did", "recent", "latest", "new",
            "novel", "current", "state", "art", "overview", "survey", "review", "on", "from",
            "by", "at", "into", "through", "during", "before", "after", "above", "below",
        }

        # Natural language query understanding (Chinese or descriptive English sentences)
        is_natural_language = identifier_kind is None and (
            contains_cjk(query) or len(re.findall(r"[a-zA-Z0-9\u4e00-\u9fa5]+", query)) >= 4
        ) and not re.search(r"(?:^|\s)(?:all|ti|au|abs|cat|id):", query, re.IGNORECASE)

        if is_natural_language and identifier_kind is None:
            has_llm = chat_service is not None and getattr(chat_service, "_chat", None) is not None
            if has_llm:
                prompt = (
                    "将用户的学术研究意图或自然语言描述转换为学术数据库检索意图。"
                    "只返回 JSON 对象，格式必须是 {\"openalex_query\":\"...\",\"queries\":[\"...\"],\"required_concepts\":[[\"...\",\"...\"],[\"...\",\"...\"]]}。"
                    "openalex_query 是一个精炼准确的英文学术主题短语（去除所有闲聊引导词，如'我想找'、'find papers on'等）；"
                    "queries 返回 1 到 3 个互补的英文关键词短语；"
                    "required_concepts 返回恰好 2 个核心概念组，每组给出 2 到 4 个可替代的英文词或短语；相关论文应从每组至少命中一个表达；"
                    "不要解释、不要非英文字符、不要布尔语法；"
                    "保留领域含义，优先使用学术论文常见术语。用户主题：" + query
                )
                try:
                    completion = await asyncio.wait_for(
                        asyncio.to_thread(
                            chat_service._chat.complete,
                            system_prompt="你是学术检索查询理解器，只输出符合要求的 JSON。",
                            user_prompt=prompt,
                            max_output_tokens=256,
                            temperature=0,
                            json_object=True,
                            enable_thinking=False,
                            producer_step_id="step-library-paper-query-understanding",
                        ),
                        timeout=5.0,
                    )
                    raw = json.loads(completion.content)
                    candidate_queries = raw.get("queries") if isinstance(raw, dict) else None
                    if isinstance(candidate_queries, list):
                        parsed_queries = [str(item).strip() for item in candidate_queries if isinstance(item, str) and item.strip()]
                        parsed_queries = list(dict.fromkeys(parsed_queries))[:3]
                        if parsed_queries:
                            queries = parsed_queries
                    proposed_openalex = raw.get("openalex_query") if isinstance(raw, dict) else None
                    if isinstance(proposed_openalex, str) and proposed_openalex.strip():
                        openalex_query = proposed_openalex.strip()
                    elif queries:
                        openalex_query = queries[0]
                    proposed_concepts = raw.get("required_concepts") if isinstance(raw, dict) else None
                    concept_groups = []
                    if isinstance(proposed_concepts, list):
                        for group in proposed_concepts[:2]:
                            if not isinstance(group, list):
                                continue
                            alternatives = tuple(dict.fromkeys(str(item).strip().lower() for item in group if isinstance(item, str) and re.fullmatch(r"[a-zA-Z][a-zA-Z0-9 -]{1,40}", item.strip())))[:4]
                            if alternatives:
                                concept_groups.append(alternatives)
                    required_concepts = tuple(concept_groups)
                    if len(required_concepts) < 2:
                        fallback_terms = [term.lower() for term in re.findall(r"[a-zA-Z][a-zA-Z0-9-]{1,}", queries[0]) if term.lower() not in stopwords]
                        required_terms = tuple(dict.fromkeys(fallback_terms))[:2]
                    understanding = {"status": "provider_translated", "queries": queries, "openalex_query": openalex_query, "required_concepts": [list(group) for group in required_concepts], "required_terms": list(required_terms), "identifier_kind": None}
                except Exception:
                    has_llm = False

            if not has_llm:
                raw_terms = [term.lower() for term in re.findall(r"[a-zA-Z][a-zA-Z0-9-]{1,}", query) if term.lower() not in stopwords]
                if raw_terms:
                    openalex_query = " ".join(raw_terms[:6])
                    queries = [" ".join(raw_terms[:4])]
                    if len(raw_terms) >= 2:
                        required_terms = tuple(dict.fromkeys(raw_terms[:2]))
                else:
                    cleaned_cjk = re.sub(r"(我想找|请帮我找|寻找|检索|关于|相关的|最新|综述|论文|研究|探讨|基于|在|中的|应用)", " ", query).strip()
                    openalex_query = cleaned_cjk or query
                    queries = [cleaned_cjk or query]
                understanding = {"status": "heuristic_fallback", "queries": queries, "openalex_query": openalex_query, "required_terms": list(required_terms), "identifier_kind": None}

        requested_sources = tuple(dict.fromkeys(item.strip().lower() for item in sources.split(",") if item.strip()))
        invalid_sources = set(requested_sources) - {"openalex", "arxiv"}
        if invalid_sources or not requested_sources:
            return JSONResponse(status_code=400, content={"code": "invalid_paper_sources", "message": "论文来源只支持 openalex 和 arxiv。"})

        retrieval_queries = []
        if identifier_kind == "arxiv":
            retrieval_queries = ["id:" + re.sub(r"^arxiv:\s*", "", query.strip(), flags=re.IGNORECASE)]
        elif re.search(r"(?:^|\s)(?:all|ti|au|abs|cat|id):", query, re.IGNORECASE):
            retrieval_queries = [query]
        else:
            for phrase in queries:
                terms = [term.lower() for term in re.findall(r"[a-zA-Z][a-zA-Z0-9-]{1,}", phrase) if term.lower() not in stopwords]
                if len(terms) >= 4:
                    retrieval_queries.append(" AND ".join(f"all:{term}" for term in dict.fromkeys(terms[:3])))
                elif len(terms) >= 2:
                    retrieval_queries.append(" AND ".join(f"all:{term}" for term in dict.fromkeys(terms)))
                elif terms:
                    retrieval_queries.append(f"all:{terms[0]}")
                elif not contains_cjk(phrase):
                    retrieval_queries.append(phrase)
        retrieval_queries = list(dict.fromkeys(retrieval_queries))
        source_states = []
        records = []
        contact_email = third_party_setting("CONFLUX_WEAVE_CONTACT_EMAIL")
        source_max_results = min(max(target_results, 20), 100)

        if "openalex" in requested_sources:
            try:
                result = await asyncio.to_thread(OpenAlexSearchAdapter(repository.artifact_store, contact_email=contact_email).search, openalex_query, max_results=source_max_results, year_from=year_from, year_to=year_to, oa_only=oa_only)
                records.extend(result.papers)
                source_states.append({"source": "openalex", "status": "success" if result.papers else "no_results", "query": result.query, "count": len(result.papers), "cache_hit": result.cache_hit})
            except Exception as exc:
                source_states.append({"source": "openalex", "status": "failed", "query": openalex_query, "count": 0, "code": getattr(exc, "code", "openalex_search_failed"), "message": str(exc), "retryable": bool(getattr(exc, "retryable", True)), "recovery_action": "仅重试 OpenAlex 来源。"})

        if "arxiv" in requested_sources:
            if not retrieval_queries and contains_cjk(query):
                source_states.append({"source": "arxiv", "status": "no_results", "query": query, "count": 0, "message": "arXiv 不支持纯中文字符检索，已使用 OpenAlex 检索。"})
            else:
                adapter = ArxivSearchAdapter(repository.artifact_store)
                arxiv_count = 0
                arxiv_cache_hits = 0
                arxiv_error = None
                arxiv_failures = []
                for search_query in retrieval_queries:
                    try:
                        result = await asyncio.to_thread(adapter.search, search_query, max_results=source_max_results)
                        arxiv_cache_hits += int(result.cache_hit)
                        converted = [arxiv_record(paper, rank) for rank, paper in enumerate(result.papers)]
                        converted = [paper for paper in converted if (not year_from or (paper.year or 0) >= year_from) and (not year_to or (paper.year or 9999) <= year_to) and (not oa_only or paper.is_oa)]
                        records.extend(converted)
                        arxiv_count += len(converted)
                    except Exception as exc:
                        arxiv_error = exc
                        arxiv_failures.append({"query": search_query, "code": getattr(exc, "code", "arxiv_search_failed"), "message": str(exc), "retryable": bool(getattr(exc, "retryable", True))})
                if arxiv_count:
                    source_states.append({"source": "arxiv", "status": "partial" if arxiv_error else "success", "query": " OR ".join(retrieval_queries), "count": arxiv_count, "cache_hit": arxiv_cache_hits == len(retrieval_queries), **({"message": str(arxiv_error), "failures": arxiv_failures, "recovery_action": "仅重试 arXiv 来源。"} if arxiv_error else {})})
                elif arxiv_error:
                    source_states.append({"source": "arxiv", "status": "failed", "query": " OR ".join(retrieval_queries), "count": 0, "code": getattr(arxiv_error, "code", "arxiv_search_failed"), "message": str(arxiv_error), "failures": arxiv_failures, "recovery_action": "仅重试 arXiv 来源。"})
                else:
                    source_states.append({"source": "arxiv", "status": "no_results", "query": " OR ".join(retrieval_queries), "count": 0, "cache_hit": arxiv_cache_hits == len(retrieval_queries)})

        if all(item["status"] == "failed" for item in source_states):
            return JSONResponse(status_code=502, content={"code": "all_paper_sources_failed", "message": "所有论文来源均请求失败。", "sources": source_states})
        query_terms = () if identifier_kind else tuple(term for term in re.findall(r"[a-zA-Z][a-zA-Z0-9-]{1,}", " ".join(queries).lower()) if term not in stopwords)
        if not identifier_kind and not required_terms and not required_concepts and len(query_terms) >= 2:
            required_terms = tuple(dict.fromkeys(query_terms[:2]))
        merged = merge_and_rank(records, query_terms=query_terms, max_results=max(1, len(records)), sort=sort, required_terms=required_terms, required_concepts=required_concepts)
        papers = merged[:target_results]
        overall = "partial" if any(item["status"] in {"failed", "partial"} for item in source_states) else "success"
        items = []
        for paper in papers:
            value = paper.as_dict()
            searchable = re.sub(r"[^a-zA-Z0-9-]+", " ", " ".join((paper.title, paper.summary, *paper.topics)).lower())
            value["matched_terms"] = list(dict.fromkeys(term for term in query_terms if term in searchable))
            items.append(value)
        return {"query": query, "status": overall, "query_understanding": {**understanding, "retrieval_queries": retrieval_queries}, "sources": source_states, "raw_count": len(records), "deduplicated_count": len(merged), "returned_count": len(papers), "items": items}

    def paper_registry() -> tuple[Path, list[dict[str, Any]]]:
        path = repository.database_path.with_name("library-registry.json")
        try:
            rows = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else []
            return path, rows if isinstance(rows, list) else []
        except (OSError, ValueError):
            return path, []

    def save_paper_row(paper: PaperRecord, status: str, **values: Any) -> dict[str, Any]:
        path, rows = paper_registry()
        row = {
            "paper_id": paper.paper_id,
            "relative_path": f"paper:{paper.paper_id}",
            "title": paper.title,
            "authors": list(paper.authors),
            "year": paper.year,
            "doi": paper.doi,
            "arxiv_id": paper.arxiv_id,
            "openalex_id": paper.openalex_id,
            "source": " + ".join(paper.sources) or "论文元数据",
            "source_type": "网络论文",
            "status": status,
            "paper": paper.as_dict(),
            "updated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            **values,
        }
        identity_keys = {paper.paper_id, paper.doi, re.sub(r"v\d+$", "", paper.arxiv_id or "", flags=re.IGNORECASE)} - {None, ""}
        kept = []
        prior = None
        for existing in rows:
            existing_keys = {existing.get("paper_id"), existing.get("doi"), re.sub(r"v\d+$", "", str(existing.get("arxiv_id") or ""), flags=re.IGNORECASE)} - {None, ""}
            if identity_keys.isdisjoint(existing_keys):
                kept.append(existing)
            else:
                prior = existing
        if prior and prior.get("status") != status:
            history = list(prior.get("versions", []))
            history.append({key: prior.get(key) for key in ("status", "document_id", "segments_artifact_id", "updated_at", "error") if key in prior})
            row["versions"] = history[-20:]
        kept.append(row)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(kept, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return row

    @app.post("/api/v1/library/papers")
    async def save_or_import_library_paper(request: LibraryPaperRequest) -> dict[str, Any]:
        try:
            paper = paper_from_dict(request.paper)
        except Exception as exc:
            return JSONResponse(status_code=400, content={"code": "paper_record_invalid", "message": f"论文记录无效：{exc}"})
        if not paper.paper_id or not paper.title:
            return JSONResponse(status_code=400, content={"code": "paper_record_invalid", "message": "论文记录缺少 paper_id 或标题。"})
        if request.action == "metadata":
            save_paper_row(paper, "metadata_saved")
            return {"status": "metadata_saved", "paper_id": paper.paper_id}

        candidates = list(paper.pdf_candidates)
        if paper.arxiv_id:
            candidates.extend((PdfCandidate(f"https://arxiv.org/pdf/{paper.arxiv_id}", "arxiv"), PdfCandidate(f"https://export.arxiv.org/pdf/{paper.arxiv_id}.pdf", "arxiv_mirror")))
        unpaywall_error = None
        contact_email = third_party_setting("CONFLUX_WEAVE_CONTACT_EMAIL")
        if paper.doi and contact_email:
            try:
                candidates.extend(await asyncio.to_thread(UnpaywallResolver(repository.artifact_store, email=contact_email).resolve, paper.doi))
            except Exception as exc:
                unpaywall_error = str(exc)
        if not candidates:
            save_paper_row(paper, "oa_unavailable", error="未找到合法开放全文地址。")
            return {"status": "oa_unavailable", "paper_id": paper.paper_id, "message": "已保存论文元数据，但未找到合法开放全文。"}

        save_paper_row(paper, "fetching")
        try:
            fetched = await asyncio.to_thread(OpenAccessPdfFetcher(repository.artifact_store).fetch, tuple(candidates))
        except Exception as exc:
            attempts = list(getattr(exc, "details", ()))
            failure_artifact_id = getattr(exc, "artifact_id", None)
            save_paper_row(paper, "fetch_failed", error=str(exc), fetch_attempts=attempts, fetch_failure_artifact_id=failure_artifact_id, unpaywall_error=unpaywall_error)
            return JSONResponse(status_code=502, content={"code": getattr(exc, "code", "paper_fetch_failed"), "message": str(exc), "attempts": attempts, "failure_artifact_id": failure_artifact_id})

        import tempfile
        with tempfile.NamedTemporaryFile(prefix=f"paper-{paper.paper_id[-12:]}-", suffix=".pdf", delete=False) as handle:
            handle.write(fetched.content)
            temporary_path = Path(handle.name)
        try:
            document = await asyncio.to_thread(LocalDocumentImporter(repository.artifact_store, extract_assets=True).import_path, temporary_path, producer_step_id="step-library-paper-parse")
            character_count = sum(len(segment.text) for segment in document.segments)
            if not document.segments or character_count < 200:
                save_paper_row(paper, "parse_failed", error=f"正文提取质量不足：{len(document.segments)} 个有效页面，{character_count} 个字符。", document_id=document.document_id, source_artifact_id=document.source_artifact.artifact_id, size_bytes=len(fetched.content), page_count=fetched.page_count, final_pdf_url=fetched.final_url)
                return JSONResponse(status_code=422, content={"code": "paper_parse_quality_failed", "message": "PDF 已保存，但正文提取质量不足，未标记为解析完成。"})
        except Exception as exc:
            save_paper_row(paper, "parse_failed", error=str(exc), size_bytes=len(fetched.content), page_count=fetched.page_count, final_pdf_url=fetched.final_url)
            return JSONResponse(status_code=422, content={"code": "paper_parse_failed", "message": f"PDF 已保存，但解析失败：{exc}"})
        finally:
            temporary_path.unlink(missing_ok=True)
        indexed_values = {"document_id": document.document_id, "source_snapshot_id": document.source_snapshot.source_id, "source_artifact_id": document.source_artifact.artifact_id, "segments_artifact_id": document.segments_artifact.artifact_id, "segment_count": len(document.segments), "character_count": character_count, "size_bytes": len(fetched.content), "page_count": fetched.page_count, "final_pdf_url": fetched.final_url, "pdf_source": fetched.source, "fetch_attempt_artifact_id": fetched.attempt_artifact_id}
        if getattr(document, "assets_artifact", None) is not None:
            indexed_values["assets_artifact_id"] = document.assets_artifact.artifact_id
            indexed_values["asset_count"] = len(getattr(document, "assets", ()))
        if retrieval_pipeline is None:
            save_paper_row(paper, "parsed", **indexed_values)
            return {"status": "parsed", "paper_id": paper.paper_id, "document_id": document.document_id, "segment_count": len(document.segments), "character_count": character_count, "message": "论文已解析，但知识库索引服务未就绪。"}
        save_paper_row(paper, "indexing", **indexed_values)
        try:
            index_result = await index_document(document)
        except Exception as exc:
            save_paper_row(paper, "index_failed", error=str(exc)[:500], **indexed_values)
            return JSONResponse(status_code=502, content={"code": "paper_index_failed", "message": f"论文已解析，但加入知识库失败：{exc}", "paper_id": paper.paper_id, "document_id": document.document_id})
        save_paper_row(paper, "knowledge_ready", index_added_count=index_result.get("added_count", 0), embedding_artifacts=index_result.get("embedding_artifacts", []), **indexed_values)
        return {"status": "knowledge_ready", "paper_id": paper.paper_id, "document_id": document.document_id, "segment_count": len(document.segments), "character_count": character_count}

    @app.post("/api/v1/library/papers/{arxiv_id}/import")
    async def import_legacy_arxiv_paper(arxiv_id: str, request: Request) -> dict[str, Any]:
        body = await request.json()
        legacy = PaperRecord(
            paper_id="paper-arxiv-" + re.sub(r"v\d+$", "", arxiv_id, flags=re.IGNORECASE).replace("/", "-"),
            title=str(body.get("title") or f"arXiv {arxiv_id}"), summary="", authors=(), year=None, published=None, updated=None, venue="arXiv", doi=None, arxiv_id=arxiv_id, openalex_id=None, sources=("arxiv",), landing_urls=(), pdf_candidates=(PdfCandidate(str(body.get("pdf_url") or f"https://arxiv.org/pdf/{arxiv_id}"), "arxiv"),), topics=(), cited_by_count=0, is_oa=True,
        )
        return await save_or_import_library_paper(LibraryPaperRequest(action="fulltext", paper=legacy.as_dict()))

    def _provider_view() -> ProviderConfigResponse:
        view = ProviderConfigView.from_env(dotenv_path) if dotenv_path else None
        if view is None:
            return ProviderConfigResponse(
                base_url="", model="", embedding_model="", reranker_model="",
                engine_model="", image_embedding_model="", contact_email="", api_key_configured=False, api_key_hint=None,
            )
        return ProviderConfigResponse(
            base_url=view.base_url,
            model=view.model,
            embedding_model=view.embedding_model,
            reranker_model=view.reranker_model,
            engine_model=view.engine_model,
            image_embedding_model=getattr(view, "image_embedding_model", ""),
            contact_email=view.contact_email,
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
                image_embedding_model=request.image_embedding_model,
                contact_email=request.contact_email,
            )
            return ProviderConfigUpdateResponse(
                provider=ProviderConfigResponse(
                    base_url=view.base_url,
                    model=view.model,
                    embedding_model=view.embedding_model,
                    reranker_model=view.reranker_model,
                    engine_model=view.engine_model,
                    image_embedding_model=getattr(view, "image_embedding_model", ""),
                    contact_email=view.contact_email,
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

    def _get_notes_registry_path() -> Path | None:
        db_path = getattr(repository, "database_path", None)
        if db_path is not None:
            return Path(db_path).with_name("notes-registry.json")
        return None

    def _get_document_agent() -> DocumentAgent | None:
        store = getattr(repository, "artifact_store", None)
        if store is None:
            return None
        return DocumentAgent(
            store,
            chat_adapter=getattr(chat_service, "_chat", None) if chat_service is not None else None,
        )

    def load_notes_registry() -> list[dict[str, Any]]:
        reg_path = _get_notes_registry_path()
        if not reg_path or not reg_path.is_file():
            return []
        try:
            return json.loads(reg_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []

    def save_note_entry(note: DocumentNote) -> None:
        reg_path = _get_notes_registry_path()
        if not reg_path:
            return
        notes = load_notes_registry()
        entry = {
            "note_id": note.note_id,
            "document_id": note.document_id,
            "title": note.title,
            "version": note.version,
            "parent_note_id": note.parent_note_id,
            "applied_patch_id": note.applied_patch_id,
            "instruction": note.metadata.get("revision_instruction", ""),
            "created_at": note.created_at,
        }
        existing_idx = next((i for i, item in enumerate(notes) if item.get("note_id") == note.note_id), -1)
        if existing_idx >= 0:
            notes[existing_idx] = entry
        else:
            notes.append(entry)
        reg_path.parent.mkdir(parents=True, exist_ok=True)
        reg_path.write_text(json.dumps(notes, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    @app.post("/api/v1/documents/analyze", response_model=DocumentNoteResponse)
    async def analyze_document_endpoint(request: DocumentAnalyzeRequest):
        store = getattr(repository, "artifact_store", None)
        doc_agent = _get_document_agent()
        if store is None or doc_agent is None:
            return JSONResponse(status_code=503, content={"code": "service_unavailable", "message": "文档分析服务未配置存储。"})
        importer = LocalDocumentImporter(store)
        doc_path: Path | None = None
        doc_suffix: str | None = None

        # 1. Direct path check
        if request.path:
            p = Path(request.path)
            if p.is_file():
                doc_path = p
            elif (Path.cwd() / p).is_file():
                doc_path = (Path.cwd() / p).resolve()
            elif config_paths and config_paths.get("workspace_root") and (Path(config_paths["workspace_root"]) / p).is_file():
                doc_path = (Path(config_paths["workspace_root"]) / p).resolve()

        # 2. Library lookup by document_id or path
        if not doc_path:
            overview = await library_overview()
            items = overview.get("items", [])
            search_keys = [k for k in (request.document_id, request.path) if k]
            target_item = None
            for key in search_keys:
                target_item = next(
                    (
                        it for it in items
                        if it.get("document_id") == key
                        or it.get("record_id") == key
                        or it.get("paper_id") == key
                        or it.get("relative_path") == key
                        or str(key).endswith(str(it.get("relative_path", "")))
                    ),
                    None,
                )
                if target_item:
                    break

            if target_item:
                # Check status: if metadata-only without fulltext
                status = target_item.get("status")
                if status in {"metadata_saved", "oa_unavailable", "fetch_failed"}:
                    return JSONResponse(
                        status_code=400,
                        content={
                            "code": "fulltext_unavailable",
                            "message": "该资料目前仅有元数据，尚未获取到全文 PDF。请先在资料库点击“获取全文并加入知识库”。",
                        },
                    )

                # Check if item has explicit file path
                explicit_path = target_item.get("path")
                if explicit_path and Path(explicit_path).is_file():
                    doc_path = Path(explicit_path)

                # Check if sha256 or source_artifact_id in artifact store
                if not doc_path:
                    source_art = str(target_item.get("source_artifact_id", ""))
                    digest = ""
                    if source_art.startswith("artifact-sha256-"):
                        digest = source_art.removeprefix("artifact-sha256-")
                    elif target_item.get("sha256"):
                        digest = str(target_item["sha256"])

                    if digest:
                        try:
                            p = store.path_for_digest(digest)
                            if p.is_file():
                                doc_path = p
                                media = str(target_item.get("media_type", "")).lower()
                                rel = str(target_item.get("relative_path", "")).lower()
                                doc_suffix = ".pdf" if ("pdf" in media or rel.endswith(".pdf")) else (".md" if ("markdown" in media or rel.endswith(".md")) else ".pdf")
                        except Exception:
                            pass

                # Check relative_path candidates if not found in store
                if not doc_path:
                    rel = target_item.get("relative_path")
                    if rel and not rel.startswith("paper:"):
                        ws_root = Path(config_paths.get("workspace_root", "")) if config_paths and config_paths.get("workspace_root") else Path.cwd()
                        for root_dir in [
                            Path.cwd(),
                            ws_root,
                            Path("var") / "acceptance" / "v0.3-s1" / "corpus",
                            Path("var") / "workspace",
                            Path((config_paths or {}).get("corpus_manifest", "")).parent,
                        ]:
                            cand = (root_dir / rel).resolve()
                            if cand.is_file():
                                doc_path = cand
                                break

                # Fallback to synthesized markdown from segments_artifact_id if raw file not present
                if not doc_path:
                    segments_art = str(target_item.get("segments_artifact_id", ""))
                    if segments_art.startswith("artifact-sha256-"):
                        seg_digest = segments_art.removeprefix("artifact-sha256-")
                        try:
                            p = store.path_for_digest(seg_digest)
                            if p.is_file():
                                seg_data = json.loads(p.read_text(encoding="utf-8"))
                                segs = seg_data.get("segments", [])
                                if segs:
                                    content = f"# {target_item.get('title', 'Document')}\n\n"
                                    for s in segs:
                                        t = s.get("text", "").strip()
                                        if t:
                                            content += f"{t}\n\n"
                                    temp_dir = Path("var") / "cache" / "notes_extracted"
                                    temp_dir.mkdir(parents=True, exist_ok=True)
                                    temp_file = temp_dir / f"{target_item.get('document_id') or seg_digest}.md"
                                    temp_file.write_text(content, encoding="utf-8")
                                    doc_path = temp_file
                                    doc_suffix = ".md"
                        except Exception:
                            pass

        if not doc_path or not doc_path.is_file():
            return JSONResponse(status_code=404, content={"code": "document_not_found", "message": "未找到指定文档，请检查路径或文档ID。"})

        try:
            imported = await asyncio.to_thread(importer.import_path, doc_path, suffix=doc_suffix)
            note = await asyncio.to_thread(
                doc_agent.analyze_document,
                imported,
                focus=request.focus,
                title=request.title,
            )
            save_note_entry(note)
            return DocumentNoteResponse(
                note_id=note.note_id,
                document_id=note.document_id,
                title=note.title,
                version=note.version,
                parent_note_id=note.parent_note_id,
                applied_patch_id=note.applied_patch_id,
                executive_summary=note.executive_summary,
                sections=tuple(
                    DocumentNoteSectionResponse(
                        section_id=s.section_id,
                        title=s.title,
                        level=s.level,
                        content=s.content,
                        source_segments=s.source_segments,
                        citations=s.citations,
                        asset_refs=s.asset_refs,
                    )
                    for s in note.sections
                ),
                key_concepts=note.key_concepts,
                visual_assets=note.visual_assets,
                metadata=note.metadata,
                markdown_content=note.markdown_content,
                html_content=note.html_content,
                created_at=note.created_at,
            )
        except Exception as exc:
            return error_response(exc)

    @app.get("/api/v1/notes/{note_id}", response_model=DocumentNoteResponse)
    async def get_note_endpoint(note_id: str):
        store = getattr(repository, "artifact_store", None)
        if store is None:
            return JSONResponse(status_code=503, content={"code": "service_unavailable", "message": "笔记存储服务未就绪。"})
        try:
            try:
                note_obj = load_note_artifact(note_id, store)
            except (KeyError, ValueError):
                note_obj = None
            if note_obj is None:
                return JSONResponse(status_code=404, content={"code": "note_not_found", "message": f"笔记 {note_id} 不存在。"})

            return DocumentNoteResponse(
                note_id=note_obj.note_id,
                document_id=note_obj.document_id,
                title=note_obj.title,
                version=note_obj.version,
                parent_note_id=note_obj.parent_note_id,
                applied_patch_id=note_obj.applied_patch_id,
                executive_summary=note_obj.executive_summary,
                sections=tuple(
                    DocumentNoteSectionResponse(
                        section_id=s.section_id,
                        title=s.title,
                        level=s.level,
                        content=s.content,
                        source_segments=s.source_segments,
                        citations=s.citations,
                        asset_refs=s.asset_refs,
                    )
                    for s in note_obj.sections
                ),
                key_concepts=note_obj.key_concepts,
                visual_assets=note_obj.visual_assets,
                metadata=note_obj.metadata,
                markdown_content=note_obj.markdown_content,
                html_content=note_obj.html_content,
                created_at=note_obj.created_at,
            )
        except Exception as exc:
            return error_response(exc)

    @app.post("/api/v1/notes/{note_id}/patch", response_model=DocumentNoteResponse)
    async def patch_note_endpoint(note_id: str, request: NotePatchRequest):
        store = getattr(repository, "artifact_store", None)
        doc_agent = _get_document_agent()
        if store is None or doc_agent is None:
            return JSONResponse(status_code=503, content={"code": "service_unavailable", "message": "笔记修订服务未就绪。"})
        try:
            try:
                note_obj = load_note_artifact(note_id, store)
            except (KeyError, ValueError):
                note_obj = None
            if note_obj is None:
                return JSONResponse(status_code=404, content={"code": "note_not_found", "message": f"目标笔记 {note_id} 不存在。"})

            if request.target_version != note_obj.version:
                return JSONResponse(
                    status_code=409,
                    content={
                        "code": "version_conflict",
                        "message": f"目标版本 {request.target_version} 与当前笔记版本 {note_obj.version} 不一致。",
                    },
                )

            doc_context = None
            try:
                overview = await library_overview()
                doc_item = next((row for row in overview.get("items", []) if row.get("document_id") == note_obj.document_id or row.get("paper_id") == note_obj.document_id), None)
                if doc_item and doc_item.get("segments_artifact_id"):
                    art_id = str(doc_item["segments_artifact_id"])
                    if art_id.startswith("artifact-sha256-"):
                        sp = repository.artifact_store.path_for_digest(art_id.removeprefix("artifact-sha256-"))
                        if sp.is_file():
                            seg_data = json.loads(sp.read_text(encoding="utf-8"))
                            segs = seg_data.get("segments", [])
                            if segs:
                                doc_context = "\n\n".join(s.get("text", "")[:800] for s in segs[:8])
            except Exception:
                doc_context = None

            patch_ops = [PatchOperation.from_dict(op) for op in request.operations] if request.operations else None
            patch, new_note = await asyncio.to_thread(
                doc_agent.revise_note,
                note_obj,
                request.instruction,
                patch_ops=patch_ops,
                document_context=doc_context,
            )
            save_note_entry(new_note)
            return DocumentNoteResponse(
                note_id=new_note.note_id,
                document_id=new_note.document_id,
                title=new_note.title,
                version=new_note.version,
                parent_note_id=new_note.parent_note_id,
                applied_patch_id=new_note.applied_patch_id,
                executive_summary=new_note.executive_summary,
                sections=tuple(
                    DocumentNoteSectionResponse(
                        section_id=s.section_id,
                        title=s.title,
                        level=s.level,
                        content=s.content,
                        source_segments=s.source_segments,
                        citations=s.citations,
                        asset_refs=s.asset_refs,
                    )
                    for s in new_note.sections
                ),
                key_concepts=new_note.key_concepts,
                visual_assets=new_note.visual_assets,
                metadata=new_note.metadata,
                markdown_content=new_note.markdown_content,
                html_content=new_note.html_content,
                created_at=new_note.created_at,
            )
        except Exception as exc:
            return error_response(exc)

    @app.get("/api/v1/notes/{note_id}/revisions", response_model=NoteRevisionsResponse)
    async def get_note_revisions_endpoint(note_id: str):
        notes = load_notes_registry()
        target = next((n for n in notes if n.get("note_id") == note_id), None)
        doc_id = target.get("document_id") if target else None
        matching = [n for n in notes if (doc_id and n.get("document_id") == doc_id) or n.get("note_id") == note_id or n.get("parent_note_id") == note_id]
        matching.sort(key=lambda x: int(x.get("version", 1)))

        items = [
            NoteRevisionItem(
                note_id=str(m.get("note_id")),
                version=int(m.get("version", 1)),
                parent_note_id=m.get("parent_note_id"),
                applied_patch_id=m.get("applied_patch_id"),
                instruction=str(m.get("instruction", "")),
                created_at=str(m.get("created_at", "")),
            )
            for m in matching
        ]
        curr_ver = target.get("version", len(items)) if target else (items[-1].version if items else 1)
        return NoteRevisionsResponse(
            note_id=note_id,
            current_version=int(curr_ver),
            revisions=tuple(items),
        )

    @app.post("/api/v1/notes/{note_id}/save-to-research")
    async def save_note_to_research(note_id: str):
        store = getattr(repository, "artifact_store", None)
        if store is None:
            return JSONResponse(status_code=503, content={"code": "service_unavailable", "message": "存储服务未就绪。"})
        try:
            from datetime import datetime, UTC
            from uuid import uuid4
            from conflux_weave.core import (
                TaskSpec,
                RunRecord,
                RunStatus,
                StepRecord,
                StepStatus,
                DeliveryRecord,
                DeliveryDisposition,
                BudgetLedger,
            )

            try:
                note_obj = load_note_artifact(note_id, store)
            except (KeyError, ValueError):
                note_obj = None
            if note_obj is None:
                return JSONResponse(status_code=404, content={"code": "note_not_found", "message": f"笔记 {note_id} 不存在。"})

            task_id = f"task-note-{uuid4().hex[:12]}"
            run_id = f"run-note-{uuid4().hex[:12]}"
            step_id = f"{run_id}:publish_delivery"
            now = datetime.now(UTC).isoformat().replace("+00:00", "Z")

            report_text = note_obj.markdown_content or ""
            if not report_text.strip():
                lines = [
                    f"# 文献精读报告：{note_obj.title}",
                    "",
                    f"> **来源文献**：`{note_obj.document_id}`",
                    f"> **研读版本**：v{note_obj.version}",
                    "",
                    "## 一、执行摘要",
                    "",
                    note_obj.executive_summary or "针对该文献的核心论点、方法与关键结论进行了系统研读与梳理。",
                    "",
                ]
                for s in note_obj.sections:
                    lines.extend([f"### {s.title}", "", s.content or "", ""])
                report_text = "\n".join(lines).strip()

            report_ref = store.put_bytes(
                report_text.encode("utf-8"),
                media_type="text/markdown; charset=utf-8",
                producer_step_id=step_id,
                schema_version="conflux-weave.verified-research-report.v2",
            )
            cfg_ref = store.put_json(
                {"source_note_id": note_id, "document_id": note_obj.document_id, "version": note_obj.version},
                producer_step_id=step_id,
                schema_version="conflux-weave.config.v1",
            )

            task = TaskSpec(
                task_id,
                "document_reading",
                {
                    "note_id": note_id,
                    "document_id": note_obj.document_id,
                    "title": note_obj.title,
                    "objective": f"文献研读精读归档：《{note_obj.title}》",
                },
                requested_policy="default",
                idempotency_key=task_id,
            )
            budget = BudgetLedger(300, 10000, 2000, "free", 1, 0, 1)
            run = RunRecord(
                run_id,
                task_id,
                RunStatus.ACCEPTED,
                "document-reading-v1",
                config_snapshot_ref=cfg_ref.artifact_id,
                budget=budget,
                created_at=now,
                updated_at=now,
            )
            step = StepRecord(step_id, run_id, "publish_delivery", 1, StepStatus.PENDING)

            repository.submit_task(task, run, (step,))
            repository.transition_run(run_id, RunStatus.QUEUED, updated_at=now)
            repository.transition_run(run_id, RunStatus.RUNNING, updated_at=now)

            evidence_refs = tuple(f"note-sec-{s.section_id}" for s in note_obj.sections) or ("note-evidence-001",)
            delivery = DeliveryRecord(
                run_id,
                DeliveryDisposition.COMPLETE,
                artifact_refs=(report_ref.artifact_id,),
                evidence_refs=evidence_refs,
                limitations=("本报告由文献研读精读工坊自动归档生成，全文包含学术论述、章节引述与结构化论据闭环。",),
                unmet_criteria=(),
                recovery_actions=(),
            )
            repository.publish_delivery(run_id, RunStatus.SUCCEEDED, delivery, (report_ref,))

            return {
                "task_id": task_id,
                "run_id": run_id,
                "status": "succeeded",
                "report_artifact_id": report_ref.artifact_id,
                "title": note_obj.title,
                "message": f"研读报告《{note_obj.title}》已成功保存至深度研究。",
            }
        except Exception as exc:
            return error_response(exc)

    def _get_project_store() -> ProjectStore:
        db_path = getattr(repository, "database_path", None)
        base_dir = Path(db_path).parent if db_path is not None else Path("var") / "data"
        reg_path = base_dir / "projects-registry.json"
        ws_root = None
        if config_paths and config_paths.get("workspace_root"):
            ws_root = Path(config_paths["workspace_root"])
        return ProjectStore(reg_path, default_workspace=ws_root)

    def _get_project_agent() -> ProjectAgent:
        chat_adapter = getattr(chat_service, "_chat", None) if chat_service is not None else None
        return ProjectAgent(provider=chat_adapter, memory_agent=memory_agent)

    def _get_coding_agent() -> CodingAgent:
        chat_adapter = getattr(chat_service, "_chat", None) if chat_service is not None else None
        return CodingAgent(provider=chat_adapter)

    _active_proposals: dict[str, CodeProposal] = {}

    @app.post("/api/v1/projects/browse-folder")
    async def browse_folder_endpoint(initial_dir: str | None = Query(None)):
        def _open_picker():
            try:
                import tkinter as tk
                from tkinter import filedialog
                root = tk.Tk()
                root.withdraw()
                root.attributes("-topmost", True)
                picked = filedialog.askdirectory(
                    title="选择工程项目目录",
                    initialdir=initial_dir if initial_dir and Path(initial_dir).is_dir() else None,
                )
                root.destroy()
                return str(Path(picked).resolve()) if picked else None
            except Exception as e:
                logger.warning(f"Native folder picker failed: {e}")
                return None

        folder = await asyncio.to_thread(_open_picker)
        return {"path": folder}

    @app.get("/api/v1/projects", response_model=list[ProjectSummaryResponse])
    async def list_projects_endpoint():
        store = _get_project_store()
        projects = store.list_projects()
        return [
            ProjectSummaryResponse(
                project_id=p.project_id,
                name=p.name,
                root_path=p.root_path,
                root_paths=tuple(p.root_paths) if p.root_paths else (p.root_path,),
                description=p.description,
                created_at=p.created_at,
                updated_at=p.updated_at,
            )
            for p in projects
        ]

    @app.post("/api/v1/projects", response_model=ProjectDetailResponse)
    async def register_project_endpoint(request: ProjectRegisterRequest):
        store = _get_project_store()
        try:
            proj = store.register(
                request.name,
                request.root_path,
                request.description,
                root_paths=request.root_paths,
            )
            git_status = GitInspector.get_status(Path(proj.root_path))
            return ProjectDetailResponse(
                project_id=proj.project_id,
                name=proj.name,
                root_path=proj.root_path,
                root_paths=tuple(proj.root_paths) if proj.root_paths else (proj.root_path,),
                description=proj.description,
                git_status=git_status.to_dict(),
                created_at=proj.created_at,
                updated_at=proj.updated_at,
            )
        except Exception as exc:
            return error_response(exc)

    @app.get("/api/v1/projects/{project_id}", response_model=ProjectDetailResponse)
    async def get_project_endpoint(project_id: str):
        store = _get_project_store()
        proj = store.get_project(project_id)
        if proj is None:
            return JSONResponse(status_code=404, content={"code": "project_not_found", "message": f"项目 {project_id} 不存在。"})
        git_status = GitInspector.get_status(Path(proj.root_path))
        return ProjectDetailResponse(
            project_id=proj.project_id,
            name=proj.name,
            root_path=proj.root_path,
            root_paths=tuple(proj.root_paths) if proj.root_paths else (proj.root_path,),
            description=proj.description,
            git_status=git_status.to_dict(),
            created_at=proj.created_at,
            updated_at=proj.updated_at,
        )

    @app.get("/api/v1/projects/{project_id}/tree", response_model=ProjectTreeResponse)
    async def get_project_tree_endpoint(project_id: str):
        store = _get_project_store()
        proj = store.get_project(project_id)
        if proj is None:
            return JSONResponse(status_code=404, content={"code": "project_not_found", "message": f"项目 {project_id} 不存在。"})
        nodes = ProjectScanner.scan_project_tree(proj)
        return ProjectTreeResponse(
            project_id=project_id,
            items=tuple(n.to_dict() for n in nodes),
        )

    @app.get("/api/v1/projects/{project_id}/file", response_model=ProjectFileContentResponse)
    async def get_project_file_endpoint(project_id: str, path: str = Query(..., min_length=1)):
        store = _get_project_store()
        proj = store.get_project(project_id)
        if proj is None:
            return JSONResponse(status_code=404, content={"code": "project_not_found", "message": f"项目 {project_id} 不存在。"})
        try:
            content, file_sha, sz = ProjectScanner.read_file_safe(proj, path)
            return ProjectFileContentResponse(
                project_id=project_id,
                path=path,
                content=content,
                sha256=file_sha,
                size_bytes=sz,
            )
        except PermissionError as pe:
            return JSONResponse(status_code=403, content={"code": "path_escape_rejected", "message": str(pe)})
        except FileNotFoundError as fe:
            return JSONResponse(status_code=404, content={"code": "file_not_found", "message": str(fe)})
        except ValueError as ve:
            return JSONResponse(status_code=400, content={"code": "file_too_large", "message": str(ve)})
        except Exception as exc:
            return error_response(exc)

    @app.post("/api/v1/projects/{project_id}/ask", response_model=ProjectAskResponse)
    async def ask_project_endpoint(project_id: str, request: ProjectAskRequest):
        store = _get_project_store()
        proj = store.get_project(project_id)
        if proj is None:
            return JSONResponse(status_code=404, content={"code": "project_not_found", "message": f"项目 {project_id} 不存在。"})
        agent = _get_project_agent()
        ans = await asyncio.to_thread(agent.ask, proj, request.question)
        return ProjectAskResponse(
            project_id=project_id,
            answer_markdown=ans.answer_markdown,
            cited_files=tuple(ans.cited_files),
            git_evidence=ans.git_evidence,
            risks_and_recommendations=tuple(ans.risks_and_recommendations),
        )

    @app.post("/api/v1/projects/{project_id}/coding/propose", response_model=CodingProposalResponse)
    async def propose_coding_patch_endpoint(project_id: str, request: CodingProposalRequest):
        store = _get_project_store()
        proj = store.get_project(project_id)
        if proj is None:
            return JSONResponse(status_code=404, content={"code": "project_not_found", "message": f"项目 {project_id} 不存在。"})
        coding_agent = _get_coding_agent()
        try:
            proposal = await asyncio.to_thread(
                coding_agent.propose_patch,
                proj,
                request.instruction,
                request.target_file,
                custom_replacement=request.custom_replacement,
            )
            _active_proposals[proposal.proposal_id] = proposal
            return CodingProposalResponse(
                proposal_id=proposal.proposal_id,
                project_id=proposal.project_id,
                title=proposal.title,
                rationale=proposal.rationale,
                risk_level=proposal.risk_level,
                target_file=proposal.target_file,
                original_hash=proposal.original_hash,
                diff=proposal.diff,
                proposed_content=proposal.proposed_content,
                verification_commands=tuple(proposal.verification_commands),
                status=proposal.status,
                created_at=proposal.created_at,
            )
        except PermissionError as pe:
            return JSONResponse(status_code=403, content={"code": "path_escape_rejected", "message": str(pe)})
        except Exception as exc:
            return error_response(exc)

    @app.post("/api/v1/projects/{project_id}/coding/apply", response_model=CodingApplyResponse)
    async def apply_coding_patch_endpoint(project_id: str, request: CodingApplyRequest):
        store = _get_project_store()
        proj = store.get_project(project_id)
        if proj is None:
            return JSONResponse(status_code=404, content={"code": "project_not_found", "message": f"项目 {project_id} 不存在。"})
        coding_agent = _get_coding_agent()
        proposal = _active_proposals.get(request.proposal_id)
        if proposal is None:
            proposal = CodeProposal(
                proposal_id=request.proposal_id,
                project_id=project_id,
                title="User Approved Patch",
                rationale="Reconstructed proposal from client request",
                risk_level="medium",
                target_file=request.target_file,
                original_hash=request.expected_hash,
                diff="",
                proposed_content=request.proposed_content,
            )
        else:
            if request.expected_hash:
                proposal.original_hash = request.expected_hash
            if request.proposed_content:
                proposal.proposed_content = request.proposed_content

        success, msg = await asyncio.to_thread(coding_agent.apply_patch, proj, proposal)
        if not success:
            if "revision_conflict" in msg:
                return JSONResponse(
                    status_code=409,
                    content={"code": "version_conflict", "message": msg},
                )
            return JSONResponse(status_code=400, content={"code": "patch_failed", "message": msg})

        return CodingApplyResponse(
            success=True,
            message=msg,
            proposal_id=proposal.proposal_id,
        )

    @app.get("/api/v1/projects/{project_id}/walkthrough", response_model=ArchitectureWalkthroughResponse)
    async def get_project_walkthrough_endpoint(project_id: str):
        store = _get_project_store()
        proj = store.get_project(project_id)
        if proj is None:
            return JSONResponse(status_code=404, content={"code": "project_not_found", "message": f"项目 {project_id} 不存在。"})
        agent = _get_project_agent()
        walkthrough = await asyncio.to_thread(agent.generate_walkthrough, proj)
        return ArchitectureWalkthroughResponse(
            project_id=walkthrough.project_id,
            overview=walkthrough.overview,
            components=tuple(
                ArchitectureComponentItem(
                    name=c.name,
                    layer=c.layer,
                    files=tuple(c.files),
                    responsibilities=c.responsibilities,
                    dependencies=tuple(c.dependencies),
                )
                for c in walkthrough.components
            ),
            mermaid_topology=walkthrough.mermaid_topology,
            data_flow_description=walkthrough.data_flow_description,
            theory_mappings=tuple(
                TheoryMappingItem(
                    concept=m.concept,
                    paper_reference=m.paper_reference,
                    code_symbol=m.code_symbol,
                    file_path=m.file_path,
                    line_number=m.line_number,
                    description=m.description,
                    design_rationale=m.design_rationale,
                )
                for m in walkthrough.theory_mappings
            ),
            dependencies_analysis=walkthrough.dependencies_analysis,
        )

    @app.get("/api/v1/projects/{project_id}/audit", response_model=ProjectAuditReportResponse)
    async def get_project_audit_endpoint(project_id: str):
        store = _get_project_store()
        proj = store.get_project(project_id)
        if proj is None:
            return JSONResponse(status_code=404, content={"code": "project_not_found", "message": f"项目 {project_id} 不存在。"})
        agent = _get_project_agent()
        report = await asyncio.to_thread(agent.generate_audit_report, proj)
        return ProjectAuditReportResponse(
            report_id=report.report_id,
            project_id=report.project_id,
            summary=report.summary,
            implementation_score=report.implementation_score,
            health_score=report.health_score,
            status_counts=report.status_counts,
            findings=tuple(
                AuditFindingItem(
                    finding_id=f.finding_id,
                    category=f.category,
                    severity=f.severity,
                    title=f.title,
                    description=f.description,
                    target_file=f.target_file,
                    line_number=f.line_number,
                    snippet=f.snippet,
                    recommendation=f.recommendation,
                    implementation_status=f.implementation_status,
                )
                for f in report.findings
            ),
            checked_rules=tuple(report.checked_rules),
            created_at=report.created_at,
        )

    @app.get("/api/v1/projects/{project_id}/git/semantic-diff", response_model=SemanticBranchDiffResponse)
    async def get_project_semantic_diff_endpoint(project_id: str, compare_branch: str = "main"):
        store = _get_project_store()
        proj = store.get_project(project_id)
        if proj is None:
            return JSONResponse(status_code=404, content={"code": "project_not_found", "message": f"项目 {project_id} 不存在。"})
        diff = await asyncio.to_thread(GitInspector.get_semantic_diff, Path(proj.root_path), compare_branch)
        return SemanticBranchDiffResponse(
            current_branch=diff.current_branch,
            compare_branch=diff.compare_branch,
            experiment_intent=diff.experiment_intent,
            changed_areas=tuple(diff.changed_areas),
            impact_level=diff.impact_level,
            file_diff_summaries=tuple(diff.file_diff_summaries),
            total_additions=diff.total_additions,
            total_deletions=diff.total_deletions,
        )

    @app.get("/api/v1/health/ready")
    async def ready_health():
        return query_service.readiness(provider_configured=provider_configured)

    workbench_dist = WORKBENCH_ROOT / "dist"
    workbench_dist_assets = workbench_dist / "assets"

    class WorkbenchStaticFiles(StaticFiles):
        def lookup_path(self, path: str):
            if workbench_dist_assets.is_dir():
                dist_target = (workbench_dist_assets / path).resolve()
                if str(dist_target).startswith(str(workbench_dist_assets.resolve())):
                    try:
                        return str(dist_target), os.stat(dist_target)
                    except (FileNotFoundError, NotADirectoryError):
                        pass
            return super().lookup_path(path)

    app.mount("/assets", WorkbenchStaticFiles(directory=WORKBENCH_ROOT), name="workbench-assets")

    @app.get("/", include_in_schema=False)
    async def workbench_index() -> FileResponse:
        dist_index = WORKBENCH_ROOT / "dist" / "index.html"
        if (
            dist_index.is_file()
            and not os.environ.get("PYTEST_CURRENT_TEST")
            and os.environ.get("CONFLUX_LEGACY_UI") != "1"
        ):
            return FileResponse(dist_index, media_type="text/html")
        return FileResponse(WORKBENCH_ROOT / "index.html", media_type="text/html")

    @app.get("/modern", include_in_schema=False)
    async def workbench_modern() -> FileResponse:
        dist_index = WORKBENCH_ROOT / "dist" / "index.html"
        if dist_index.is_file():
            return FileResponse(dist_index, media_type="text/html")
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
    retrieval_pipeline = None
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
        try:
            from conflux_weave.hybrid_retrieval import HybridRetrievalPipeline
            from conflux_weave.indexing import LanceDBDenseIndex, load_chunks
            from conflux_weave.managed_research import ManagedVerifiedResearchWorkflow
            from conflux_weave.provider import (
                OpenAICompatibleEmbeddingAdapter,
                OpenAICompatibleRerankerAdapter,
            )
            from conflux_weave.research_agents import VerifiedResearchWorkflow

            documents = load_chunks(
                corpus_manifest,
                store,
                repository.database_path.with_name("library-registry.json"),
            )
            text_pipeline = HybridRetrievalPipeline(
                documents,
                LanceDBDenseIndex(lancedb_root, table_name="paper_chunks"),
                OpenAICompatibleEmbeddingAdapter(store, config),
                OpenAICompatibleRerankerAdapter(store, config),
            )

            # Multimodal Pipeline integration (P2.1 & P2.2)
            from conflux_weave.multimodal_indexing import (
                LanceDBImageIndex,
                OpenAICompatibleImageEmbeddingAdapter,
            )
            from conflux_weave.multimodal_retrieval import (
                MultimodalRetrievalPipeline,
                is_multimodal_env_enabled,
            )

            image_index = LanceDBImageIndex(
                lancedb_root, table_name="image_assets_v1", artifact_store=store
            )
            image_model = (
                getattr(config, "image_embedding_model", None)
                or os.environ.get("CONFLUX_WEAVE_PROVIDER_IMAGE_EMBEDDING_MODEL")
                or os.environ.get("CONFLUX_WEAVE_IMAGE_EMBEDDING_MODEL")
            )
            image_embedding = None
            if image_model:
                image_embedding = OpenAICompatibleImageEmbeddingAdapter(
                    store, config, model=image_model
                )
            else:
                from conflux_weave.multimodal_indexing import (
                    DeterministicImageEmbeddingAdapter,
                )
                image_embedding = DeterministicImageEmbeddingAdapter(store)

            multimodal_pipeline = MultimodalRetrievalPipeline(
                text_pipeline,
                image_index=image_index,
                image_embedding=image_embedding,
                artifact_store=store,
                enabled=is_multimodal_env_enabled(),
            )
            retrieval = multimodal_pipeline
            retrieval_pipeline = multimodal_pipeline
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
        retrieval_pipeline=retrieval_pipeline,
    )


__all__ = ["WorkerLoop", "build_local_app", "create_app"]
