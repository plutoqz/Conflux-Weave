"""Single-process ASGI boundary for the W5 local runtime."""

from __future__ import annotations

import asyncio
import base64
import json
import mimetypes
import os
import re
import sys
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4
import logging
import sqlite3
import subprocess
import tempfile
from datetime import UTC, datetime

logger = logging.getLogger("conflux_weave.server")

from dotenv import dotenv_values
from fastapi import Body, FastAPI, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from conflux_weave.chat import ChatMessage, ChatService
from conflux_weave.memory_recall import MemoryRecallService
from conflux_weave.compute_tools import (
    COMPUTE_TOOL_RESULT_SCHEMA,
    ComputeToolRequest,
    RestrictedComputeSandbox,
    ToolClass,
    ToolDecision,
    ToolPolicy,
)
from conflux_weave.runtime.sqlite_tool_budget import ToolBudgetExceeded
from conflux_weave.core import RunStatus
from conflux_weave.export_bundle import (
    ExportService,
    build_export_json,
    build_export_zip,
    render_export_bibtex,
    render_export_markdown,
)
from urllib.parse import quote
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
    ConversationRenameRequest,
    ConversationSummary,
    DocumentLifecycleRequest,
    ResearchConversationMessageRequest,
    RunLifecycleRequest,
    DeepResearchTaskRequest,
    ApiErrorResponse,
    ArtifactContentResponse,
    DocumentAnalyzeRequest,
    DocumentAssetDetailResponse,
    DocumentAssetsResponse,
    DocumentNoteResponse,
    DocumentNoteSectionResponse,
    NotePatchRequest,
    NoteFromChatRequest,
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
    ProjectMessageRecord,
    ProjectMessagesResponse,
    CodingProposalRequest,
    CodingProposalResponse,
    CodingApplyRequest,
    CodingApplyResponse,
    CodingVerifyRequest,
    CodingVerifyResponse,
    CodingRevertRequest,
    CodingRevertResponse,
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
    PinMemoryRequest,
    MemoryFeedbackRequest,
    ExpireMemoryRequest,
    MemoryVacuumResponse,
    MemoryCandidateActionRequest,
    SearchHitResponse,
    SearchResponse,
    SearchLocateResponse,
    SearchReindexResponse,
    SkillBudgetResponse,
    SkillSummaryResponse,
    SkillDetailResponse,
    SkillCreateRequest,
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
    TopicCreateRequest,
    TopicUpdateRequest,
    TopicLinkRequest,
    TopicLocationRequest,
    TopicSummaryResponse,
    TopicDetailResponse,
    TopicListResponse,
    FixtureResearchTaskRequest,
    FollowUpResearchTaskRequest,
    ProviderConfigResponse,
    ProviderConfigTestRequest,
    ProviderConfigTestResponse,
    ProviderConfigUpdateRequest,
    ProviderConfigUpdateResponse,
    ProviderEmbeddingProbe,
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
from conflux_weave.topics import Topic, TopicStore
from conflux_weave.project_agents import CodeProposal, CodingAgent, ProjectAgent, ProjectAnswer
from conflux_weave.document_agent import DocumentAgent
from conflux_weave.document_notes import (
    NOTE_SCHEMA_VERSION,
    DocumentNote,
    NotePatch,
    NoteSection,
    PatchOperation,
    load_note_artifact,
    save_note_artifact,
)
from conflux_weave.documents import LocalDocumentImporter
from conflux_weave.global_search import GlobalSearchService
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
    thumbnail_url = (
        f"/api/v1/library/assets/{asset_id}/content?variant=thumbnail"
        if has_thumb
        else content_url
    )
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
    enable_worker: bool = True,
    provider_effective: Any | None = None,
    search_service: Any | None = None,
) -> FastAPI:
    """Build the one ASGI application around injected authoritative components."""

    query_service = WorkbenchQueryService(repository)
    worker_loop = worker or WorkerLoop(
        orchestrator, interval_seconds=poll_interval_seconds
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        # C1 进程分离：enable_worker=False 时 API 进程不执行 Worker 循环，
        # 任务由独立的 `conflux-weave worker` 进程经 SQLite 队列认领执行。
        if enable_worker:
            await worker_loop.start()
        try:
            yield
        finally:
            await worker_loop.stop()

    app = FastAPI(title="Conflux-Weave", version="0.0.1", lifespan=lifespan)
    db_path = getattr(repository, "database_path", ":memory:")
    memory_store = HierarchicalMemoryStore(db_path if db_path else ":memory:")
    chat_adapter = getattr(chat_service, "_chat", None) if chat_service else None
    # P6-B1：语义记忆召回（embedder/索引不可用时内部退回确定性路径）
    # 注意 retrieval_pipeline 现为 MultimodalRetrievalPipeline 包装层（P2.3），
    # embedding 适配器在其 text_pipeline 上；直接 getattr 会得到 None 并使
    # 记忆语义召回静默断裂——两种管线形态都要解析。
    _memory_embedder = None
    _pipeline_embedding = getattr(retrieval_pipeline, "embedding", None)
    if _pipeline_embedding is None and retrieval_pipeline is not None:
        _pipeline_embedding = getattr(
            getattr(retrieval_pipeline, "text_pipeline", retrieval_pipeline), "embedding", None
        )
    if _pipeline_embedding is not None:
        # B1 验收发现：embedding 适配器缺省模型会静默回退（text-embedding-v4），
        # 导致未配置 embedding_model 的部署在每次召回时仍发起真实付费嵌入调用。
        # 生产路径（provider_effective 已传入）按配置判定；测试路径未传时维持原行为。
        _embedding_model_configured = True
        if provider_effective is not None:
            _embedding_model_configured = bool(getattr(provider_effective, "embedding_model", None))
        if _embedding_model_configured:
            def _memory_embedder(texts):
                return [list(v) for v in _pipeline_embedding.embed(texts, producer_step_id="memory-recall").vectors]
    memory_recall_service = MemoryRecallService(
        memory_store,
        _memory_embedder,
        lancedb_root=Path(db_path).parent / "lancedb-memory" if db_path and db_path != ":memory:" else None,
    )
    memory_agent = MemoryAgent(memory_store, chat_adapter=chat_adapter, recall_service=memory_recall_service)
    conversation_router = ConversationRouter(chat_adapter=chat_adapter)
    skill_registry = SkillRegistry(db_path if db_path and db_path != ":memory:" else None)
    _base_dir = Path(db_path).parent if db_path and db_path != ":memory:" else Path("var") / "data"
    _reg_path = _base_dir / "projects-registry.json"
    _ws_root = Path(config_paths["workspace_root"]) if config_paths and config_paths.get("workspace_root") else None
    project_store = ProjectStore(_reg_path, default_workspace=_ws_root)
    _topics_reg_path = _base_dir / "topics-registry.json"
    topic_store = TopicStore(_topics_reg_path)
    skill_runner = SkillRunner(
        skill_registry,
        provider=chat_adapter,
        project_store=project_store,
        artifact_store=getattr(repository, "artifact_store", getattr(repository, "store", None)),
        retrieval_pipeline=retrieval_pipeline,
        repository=repository,
    )
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
                        **(
                            {"document_ids": list(request.document_ids)}
                            if request.document_ids
                            else {}
                        ),
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
                            **({"document_ids": list(request.document_ids)} if request.document_ids else {}),
                        },
                        requested_agent="durable_verified_research@v1",
                    )
                )
                conv_id = request.conversation_id or f"conv-{uuid4().hex}"
                now = datetime.now(UTC).isoformat()
                content = f"已为您启动深度研究任务（Run ID: {sub_result.run_id}），正在后台持续执行文献检索与多源交叉论证..."
                assistant_msg_id = f"msg-{uuid4().hex}"
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
                        record = chat_service.record_research_message(
                            conversation_id=conv_id,
                            role="assistant",
                            content=content,
                            run_id=sub_result.run_id,
                            mode="deep",
                            conversation_mode=request.mode,
                        )
                        if record and getattr(record, "message_id", None):
                            assistant_msg_id = record.message_id
                    except Exception:
                        pass
                return ChatAnswerResponse(
                    message_id=assistant_msg_id,
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
                assistant_msg_id = f"msg-{uuid4().hex}"
                if chat_service is not None:
                    try:
                        chat_service._ensure_conversation(conv_id, request.question, request.mode)
                        turn_id, seq = chat_service._next_turn(conv_id)
                        chat_service._append(ChatMessage(f"msg-{uuid4().hex}", conv_id, "user", "memory", request.question, now, turn_id=turn_id, sequence=seq), conversation_mode=request.mode)
                        chat_service._append(ChatMessage(assistant_msg_id, conv_id, "assistant", "memory", content, now, turn_id=turn_id, sequence=seq), conversation_mode=request.mode)
                    except Exception:
                        pass
                return ChatAnswerResponse(
                    message_id=assistant_msg_id,
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
                assistant_msg_id = f"msg-{uuid4().hex}"
                if chat_service is not None:
                    try:
                        chat_service._ensure_conversation(conv_id, request.question, request.mode)
                        turn_id, seq = chat_service._next_turn(conv_id)
                        chat_service._append(ChatMessage(f"msg-{uuid4().hex}", conv_id, "user", "project", request.question, now, turn_id=turn_id, sequence=seq), conversation_mode=request.mode)
                        chat_service._append(ChatMessage(assistant_msg_id, conv_id, "assistant", "project", content, now, turn_id=turn_id, sequence=seq), conversation_mode=request.mode)
                    except Exception:
                        pass
                return ChatAnswerResponse(
                    message_id=assistant_msg_id,
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
                assistant_msg_id = f"msg-{uuid4().hex}"
                if chat_service is not None:
                    try:
                        chat_service._ensure_conversation(conv_id, request.question, request.mode)
                        turn_id, seq = chat_service._next_turn(conv_id)
                        chat_service._append(ChatMessage(f"msg-{uuid4().hex}", conv_id, "user", "document", request.question, now, turn_id=turn_id, sequence=seq), conversation_mode=request.mode)
                        chat_service._append(ChatMessage(assistant_msg_id, conv_id, "assistant", "document", content, now, turn_id=turn_id, sequence=seq), conversation_mode=request.mode)
                    except Exception:
                        pass
                return ChatAnswerResponse(
                    message_id=assistant_msg_id,
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
                result = chat_service.rag_answer(
                    request.question,
                    request.conversation_id,
                    conversation_mode=request.mode,
                    web_search=request.web_search,
                    thinking_depth=request.thinking_depth,
                    document_ids=request.document_ids,
                )
                mark_library_usage([str(item.get("source_snapshot_id", "")) for item in result.get("citations", ())])
            else:
                result = chat_service.direct_answer(
                    request.question,
                    request.conversation_id,
                    conversation_mode=request.mode,
                    web_search=request.web_search,
                    thinking_depth=request.thinking_depth,
                )
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
            image_assets=tuple(result.get("image_assets", ())),
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
                    run_id=message.run_id,
                )
                for message in chat_service.history(limit=limit)
            )
        )

    @app.get("/api/v1/conversations", response_model=ConversationListResponse)
    async def list_conversations(
        limit: int = Query(default=50, ge=1, le=100),
        status: str = Query(default="active"),
    ):
        if chat_service is None:
            return ConversationListResponse(items=())
        return ConversationListResponse(
            items=tuple(ConversationSummary(**item) for item in chat_service.conversations(limit, status=status))
        )

    @app.patch("/api/v1/conversations/{conversation_id}", response_model=None)
    async def rename_conversation(conversation_id: str, request: ConversationRenameRequest):
        """P6-A2：对话重命名（软删除的对话禁止改名）。"""
        if chat_service is None:
            return JSONResponse(status_code=503, content={"code": "service_unavailable", "message": "对话服务未就绪。"})
        try:
            updated = chat_service.rename_conversation(conversation_id, request.title)
        except ValueError as exc:
            return JSONResponse(status_code=422, content={"code": "invalid_request", "message": str(exc)})
        except Exception as exc:
            return error_response(exc)
        if updated is None:
            return JSONResponse(status_code=404, content={"code": "conversation_not_found", "message": "对话不存在或已删除。"})
        return {"conversation_id": updated["conversation_id"], "title": updated["title"], "updated_at": updated["updated_at"]}

    @app.delete("/api/v1/conversations/{conversation_id}")
    async def delete_conversation(conversation_id: str):
        """P6-A2：对话软删除（消息与关联 Run 保留，可从回收站恢复）。"""
        if chat_service is None:
            return JSONResponse(status_code=503, content={"code": "service_unavailable", "message": "对话服务未就绪。"})
        try:
            record = chat_service.set_conversation_lifecycle(conversation_id, "delete")
        except Exception as exc:
            return error_response(exc)
        if record is None:
            return JSONResponse(status_code=404, content={"code": "conversation_not_found", "message": "对话不存在。"})
        return record

    @app.post("/api/v1/conversations/{conversation_id}/archive")
    async def archive_conversation(conversation_id: str):
        """P6-A2：对话归档（默认列表不再显示，可恢复）。"""
        if chat_service is None:
            return JSONResponse(status_code=503, content={"code": "service_unavailable", "message": "对话服务未就绪。"})
        try:
            record = chat_service.set_conversation_lifecycle(conversation_id, "archive")
        except Exception as exc:
            return error_response(exc)
        if record is None:
            return JSONResponse(status_code=404, content={"code": "conversation_not_found", "message": "对话不存在。"})
        return record

    @app.post("/api/v1/conversations/{conversation_id}/restore")
    async def restore_conversation(conversation_id: str):
        """P6-A2：从归档/回收站恢复对话（幂等）。"""
        if chat_service is None:
            return JSONResponse(status_code=503, content={"code": "service_unavailable", "message": "对话服务未就绪。"})
        try:
            record = chat_service.set_conversation_lifecycle(conversation_id, "restore")
        except Exception as exc:
            return error_response(exc)
        if record is None:
            return JSONResponse(status_code=404, content={"code": "conversation_not_found", "message": "对话不存在。"})
        return record

    @app.get("/api/v1/conversations/{conversation_id}", response_model=ConversationDetail)
    async def get_conversation(conversation_id: str):
        if chat_service is None:
            return JSONResponse(status_code=404, content={"code": "conversation_not_found", "message": "对话记录不存在。"})
        record = chat_service.conversation_record(conversation_id)
        raw_messages = record.pop("messages", ())
        message_records = []
        for m in raw_messages:
            m_content = m.content
            # Auto-sync finished deep research report into message content if available
            if m.run_id and m.role == "assistant" and "已为您启动深度研究任务" in m_content and query_service is not None:
                try:
                    run_info = query_service.get_run(m.run_id)
                    if run_info and run_info.state in ("complete", "partial"):
                        artifacts = query_service.get_delivery_artifacts(m.run_id)
                        if artifacts:
                            _, art_bytes = query_service.read_delivery_artifact(m.run_id, artifacts[0].artifact_id)
                            report_str = art_bytes.decode("utf-8")
                            if report_str.strip():
                                m_content = report_str
                                conn = chat_service._connect()
                                try:
                                    conn.execute(
                                        "UPDATE chat_messages SET content = ? WHERE message_id = ?",
                                        (report_str, m.message_id),
                                    )
                                    conn.commit()
                                finally:
                                    conn.close()
                except Exception:
                    pass
            message_records.append(
                ChatMessageRecord(
                    message_id=m.message_id,
                    conversation_id=m.conversation_id,
                    role=m.role,
                    mode=m.mode,
                    content=m_content,
                    created_at=m.created_at,
                    run_id=m.run_id,
                )
            )
        return ConversationDetail(**record, messages=tuple(message_records))

    @app.post("/api/v1/conversations/{conversation_id}/messages", response_model=ChatMessageRecord)
    async def record_research_message(conversation_id: str, request: ResearchConversationMessageRequest):
        if chat_service is None:
            return JSONResponse(status_code=503, content={"code": "provider_not_configured", "message": "对话记录不可用。"})
        message = chat_service.record_research_message(conversation_id, request.role, request.content, request.run_id, mode=request.mode)
        return ChatMessageRecord(message_id=message.message_id, conversation_id=message.conversation_id, role=message.role, mode=message.mode, content=message.content, created_at=message.created_at, run_id=message.run_id)

    @app.post("/api/v1/tasks/deep-research", response_model=ResearchTaskAcceptedResponse)
    async def submit_deep_research(request: DeepResearchTaskRequest):
        """W3.2 模式 C：GPT Researcher 发现聚合 + 本地证据链（durable Run）。"""
        try:
            conversation_id = request.conversation_id or f"conv-{uuid4().hex}"
            result = orchestrator.submit(
                TaskSubmission(
                    task_kind="deep_research",
                    input={
                        "objective": request.objective,
                        "conversation_id": conversation_id,
                        **(
                            {"document_ids": list(request.document_ids)}
                            if request.document_ids
                            else {}
                        ),
                    },
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

    @app.get("/api/v1/memories/recall")
    async def recall_memories(
        query: str = Query(..., min_length=1, max_length=2000),
        user_id: str = "user_default",
        project_id: str | None = None,
        conversation_id: str | None = None,
        limit: int = Query(default=8, ge=1, le=13),
    ):
        """P6-B1：召回预览——返回带 score/reason 的混合召回结果（Memory Studio「为什么召回」）。"""
        try:
            records = memory_recall_service.recall(
                query,
                user_id=user_id,
                project_id=project_id,
                conversation_id=conversation_id,
                total_limit=limit,
            )
            return {
                "query": query,
                "items": [record.to_dict() for record in records],
                "semantic_available": memory_recall_service.semantic_available,
                # B1 验收可观测性：语义路径未就绪时给出最后一跳的真实原因
                # （embedder unavailable / embed failed / lancedb unavailable）。
                "index_error": memory_recall_service.last_index_error,
            }
        except Exception as exc:
            return error_response(exc)

    @app.get("/api/v1/search", response_model=SearchResponse)
    async def global_search(
        q: str = Query(..., min_length=1, max_length=400),
        types: str | None = None,
        project_id: str | None = None,
        from_at: str | None = Query(default=None, alias="from"),
        to_at: str | None = Query(default=None, alias="to"),
        limit: int = Query(default=20, ge=1, le=100),
        include_archived: bool = False,
    ):
        """A1 全局搜索：跨对话/报告/笔记/文档/论文/Evidence 的 FTS5 检索。

        纯检索路径，无结果时返回空集、不调用任何生成模型；
        归档与软删除对象默认排除，include_archived=true 时归档可见、软删除恒不可见。
        """
        if search_service is None:
            return JSONResponse(status_code=503, content={"code": "search_unavailable", "message": "全局搜索服务未配置。"})
        try:
            type_list = [t.strip() for t in types.split(",") if t.strip()] if types else None
            hits = search_service.search(
                q,
                types=type_list,
                from_at=from_at,
                to_at=to_at,
                project_id=project_id,
                limit=limit,
                include_archived=include_archived,
            )
            items = tuple(
                SearchHitResponse(
                    result_id=hit.result_id,
                    object_type=hit.object_type,
                    object_id=hit.object_id,
                    type_label=hit.type_label,
                    title=hit.title,
                    snippet=hit.snippet,
                    match_reason=hit.match_reason,
                    updated_at=hit.updated_at,
                    locator=hit.locator,
                    deep_link=hit.deep_link,
                    score=hit.score,
                )
                for hit in hits
            )
            return SearchResponse(query=q, total=len(items), items=items)
        except Exception as exc:
            return error_response(exc)

    @app.get("/api/v1/search/{result_id}/locate", response_model=SearchLocateResponse)
    async def search_locate(result_id: str):
        """A1 原文定位：result_id → 深链与 locator（消息/笔记段落/Run/证据）。"""
        if search_service is None:
            return JSONResponse(status_code=503, content={"code": "search_unavailable", "message": "全局搜索服务未配置。"})
        located = search_service.locate(result_id)
        if located is None:
            return JSONResponse(status_code=404, content={"code": "search_result_not_found", "message": f"搜索结果 {result_id} 不在索引中。"})
        return SearchLocateResponse(**located)

    @app.post("/api/v1/search/reindex", response_model=SearchReindexResponse)
    async def search_reindex():
        """A1 索引重建：从 SQLite + 注册表 + 产物库全量回填（零 Provider 调用）。"""
        if search_service is None:
            return JSONResponse(status_code=503, content={"code": "search_unavailable", "message": "全局搜索服务未配置。"})
        try:
            notes_registry = load_notes_registry()
            library_path = repository.database_path.with_name("library-registry.json")
            library_registry: list[dict[str, Any]] = []
            if library_path.is_file():
                try:
                    library_registry = json.loads(library_path.read_text(encoding="utf-8"))
                except ValueError:
                    library_registry = []
            indexed = search_service.reindex(
                notes_registry=notes_registry,
                library_registry=library_registry,
                artifact_store=getattr(repository, "artifact_store", None),
            )
            return SearchReindexResponse(indexed=indexed)
        except Exception as exc:
            return error_response(exc)

    def _to_memory_item_response(item) -> MemoryItemResponse:
        scope_val = item.scope.value if hasattr(item.scope, "value") else str(item.scope)
        cat_val = item.category.value if hasattr(item.category, "value") else str(item.category)
        status_val = item.status.value if hasattr(item.status, "value") else str(item.status)
        return MemoryItemResponse(
            memory_id=item.memory_id,
            scope=scope_val,
            target_id=item.target_id,
            category=cat_val,
            statement=item.statement,
            confidence=item.confidence,
            status=status_val,
            source_type=item.source_type,
            source_id=item.source_id,
            created_at=item.created_at,
            updated_at=item.updated_at,
            is_pinned=bool(getattr(item, "is_pinned", False)),
            user_feedback=int(getattr(item, "user_feedback", 0)),
            expires_at=getattr(item, "expires_at", None),
        )

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
            response_items = tuple(_to_memory_item_response(item) for item in items)
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
                is_pinned=request.is_pinned,
                user_feedback=request.user_feedback,
                expires_at=request.expires_at,
            )
            return _to_memory_item_response(item)
        except Exception as exc:
            return error_response(exc)

    @app.delete("/api/v1/memories/{memory_id}")
    async def delete_memory(memory_id: str):
        try:
            success = memory_store.delete_memory(memory_id)
            if not success:
                return JSONResponse(status_code=404, content={"code": "memory_not_found", "message": "指定记忆不存在。"})
            if memory_recall_service is not None:
                memory_recall_service.delete_memory_vector(memory_id)
            return {"ok": True, "memory_id": memory_id}
        except Exception as exc:
            return error_response(exc)

    @app.post("/api/v1/memories/{memory_id}/pin", response_model=MemoryItemResponse)
    async def pin_memory(memory_id: str, request: PinMemoryRequest | None = None):
        try:
            pinned = request.is_pinned if request is not None else True
            item = memory_store.pin_memory(memory_id, is_pinned=pinned)
            if item is None:
                return JSONResponse(status_code=404, content={"code": "memory_not_found", "message": "指定记忆不存在。"})
            return _to_memory_item_response(item)
        except Exception as exc:
            return error_response(exc)

    @app.post("/api/v1/memories/{memory_id}/feedback", response_model=MemoryItemResponse)
    async def feedback_memory(memory_id: str, request: MemoryFeedbackRequest):
        try:
            item = memory_store.set_feedback(memory_id, feedback=request.user_feedback)
            if item is None:
                return JSONResponse(status_code=404, content={"code": "memory_not_found", "message": "指定记忆不存在。"})
            return _to_memory_item_response(item)
        except Exception as exc:
            return error_response(exc)

    @app.post("/api/v1/memories/{memory_id}/expire", response_model=MemoryItemResponse)
    async def expire_memory(memory_id: str, request: ExpireMemoryRequest):
        try:
            item = memory_store.set_expiry(memory_id, expires_at=request.expires_at)
            if item is None:
                return JSONResponse(status_code=404, content={"code": "memory_not_found", "message": "指定记忆不存在。"})
            return _to_memory_item_response(item)
        except Exception as exc:
            return error_response(exc)

    @app.post("/api/v1/memories/vacuum", response_model=MemoryVacuumResponse)
    async def vacuum_memories():
        try:
            active_ids = memory_store.list_active_memory_ids()
            purged_count = 0
            if memory_recall_service is not None:
                purged_count = memory_recall_service.purge_deleted_vectors(active_ids)
            return MemoryVacuumResponse(
                purged_vectors_count=purged_count,
                active_memories_count=len(active_ids),
            )
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
                    "memory": _to_memory_item_response(item).model_dump(mode="json"),
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

    @app.post("/api/v1/skills", response_model=SkillDetailResponse)
    async def create_skill_endpoint(request: SkillCreateRequest):
        try:
            from conflux_weave.skills.registry import SkillSpec, SkillCategory, SkillStatus, SkillBudget
            cat = SkillCategory(request.category)
            stat = SkillStatus(request.status)
            budget = SkillBudget(
                max_tokens=request.default_budget.max_tokens,
                max_steps=request.default_budget.max_steps,
                estimated_time_seconds=request.default_budget.estimated_time_seconds,
            )
            spec = SkillSpec(
                skill_id=request.skill_id.strip(),
                version=request.version.strip() or "1.0.0",
                name=request.name.strip(),
                description=request.description.strip(),
                category=cat,
                author=request.author.strip() or "custom",
                input_schema=request.input_schema,
                required_tools=tuple(request.required_tools),
                prompt_template=request.prompt_template,
                rules=tuple(request.rules),
                default_budget=budget,
                is_builtin=request.is_builtin,
                status=stat,
            )
            registered = skill_registry.register_skill(spec)
            return SkillDetailResponse(
                skill_id=registered.skill_id,
                version=registered.version,
                name=registered.name,
                description=registered.description,
                category=registered.category.value,
                author=registered.author,
                required_tools=registered.required_tools,
                default_budget=SkillBudgetResponse(
                    max_tokens=registered.default_budget.max_tokens,
                    max_steps=registered.default_budget.max_steps,
                    estimated_time_seconds=registered.default_budget.estimated_time_seconds,
                ),
                is_builtin=registered.is_builtin,
                status=registered.status.value,
                input_schema=registered.input_schema,
                prompt_template=registered.prompt_template,
                rules=registered.rules,
                created_at=registered.created_at,
                updated_at=registered.updated_at,
            )
        except Exception as exc:
            return error_response(exc)

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
            tool_traces = tuple(result.structured_data.get("tool_traces", ()))
            return SkillExecuteApiResponse(
                skill_id=result.skill_id,
                status=result.status,
                summary=result.summary,
                content=result.content,
                structured_data=result.structured_data,
                artifacts=tuple(result.artifacts),
                tool_traces=tool_traces,
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

    @app.get("/api/v1/overview/stats")
    async def get_overview_stats():
        db_path = repository.database_path
        runs_stats = {
            "total": 0,
            "complete": 0,
            "completed": 0,
            "partial": 0,
            "working": 0,
            "active": 0,
            "failed": 0,
            "cancelled": 0,
            "success_rate": 100,
        }
        if db_path.is_file():
            try:
                conn = sqlite3.connect(db_path)
                cursor = conn.cursor()
                cursor.execute("SELECT status, count(*) FROM runs WHERE deleted_at IS NULL GROUP BY status")
                rows = cursor.fetchall()
                state_counts = {r[0]: r[1] for r in rows}
                total = sum(state_counts.values())
                completed = (
                    state_counts.get("succeeded", 0)
                    + state_counts.get("complete", 0)
                    + state_counts.get("completed", 0)
                )
                partial = state_counts.get("partial", 0)
                active = (
                    state_counts.get("running", 0)
                    + state_counts.get("accepted", 0)
                    + state_counts.get("working", 0)
                    + state_counts.get("queued", 0)
                    + state_counts.get("pending", 0)
                )
                failed = (
                    state_counts.get("failed", 0)
                    + state_counts.get("expired", 0)
                )
                cancelled = (
                    state_counts.get("cancelled", 0)
                    + state_counts.get("canceled", 0)
                )
                success_rate = (
                    round(((completed + partial) / total) * 100) if total > 0 else 100
                )
                runs_stats = {
                    "total": total,
                    "complete": completed,
                    "completed": completed,
                    "partial": partial,
                    "working": active,
                    "active": active,
                    "failed": failed + cancelled,
                    "cancelled": cancelled,
                    "success_rate": success_rate,
                }
                conn.close()
            except Exception as db_err:
                logger.warning(f"overview stats db query failed: {db_err}")

        overview = await library_overview()
        items = overview.get("items", [])
        total_docs = len(items)
        arxiv_cnt = sum(1 for d in items if d.get("arxiv_id") or "arxiv" in str(d.get("source", "")).lower())
        local_pdf_cnt = sum(1 for d in items if str(d.get("relative_path", "")).lower().endswith(".pdf") or d.get("media_type") == "PDF")
        local_md_cnt = sum(1 for d in items if str(d.get("relative_path", "")).lower().endswith(".md") or d.get("media_type") == "MARKDOWN")

        visual_assets_cnt = len(_asset_by_id_cache)
        if visual_assets_cnt == 0:
            try:
                import lancedb
                ldb_path = Path("var") / "acceptance" / "v0.3-s1" / "lancedb"
                if ldb_path.is_dir():
                    tbl = lancedb.connect(str(ldb_path)).open_table("image_assets_v1")
                    visual_assets_cnt = len(tbl)
            except Exception:
                pass

        return {
            "runs": runs_stats,
            "corpus": {
                "total_documents": total_docs,
                "arxiv_papers": arxiv_cnt,
                "local_pdf": local_pdf_cnt,
                "local_md": local_md_cnt,
                "structured_knowledge": total_docs * 4,
                "visual_assets": visual_assets_cnt,
            },
            "corpus_scope": config_paths.get("corpus_scope", "arxiv-oa") if config_paths else "arxiv-oa",
            "provider_ok": provider_configured,
        }


    @app.get("/api/v1/runs", response_model=RunPageResponse)
    async def list_runs(
        cursor: str | None = None,
        limit: int = Query(default=20, ge=1, le=100),
        lifecycle: str = Query(default="active"),
    ):
        try:
            return query_service.list_runs(cursor=cursor, limit=limit, lifecycle=lifecycle)
        except Exception as exc:
            return error_response(exc)

    @app.post("/api/v1/runs/{run_id}/lifecycle")
    async def set_run_lifecycle(run_id: str, request: RunLifecycleRequest):
        """P6-A2：Run 归档/软删除/恢复（不触碰 Evidence、Artifact 与交付关系）。"""
        try:
            state = repository.set_run_lifecycle(run_id, request.action)
            detail = query_service.get_run(run_id)
            return {"run_id": run_id, "lifecycle": state, "state": detail.state}
        except Exception as exc:
            return error_response(exc)

    @app.get("/api/v1/runs/{run_id}", response_model=RunDetailResponse)
    async def get_run(run_id: str):
        try:
            return query_service.get_run(run_id)
        except Exception as exc:
            return error_response(exc)

    @app.get("/api/v1/runs/{run_id}/steps")
    async def list_run_steps(run_id: str):
        """返回指定 Run 的完整底层执行步骤与状态轨迹。"""
        try:
            repository.get_run(run_id)
            steps = repository.get_steps(run_id)
            items = [
                {
                    "step_id": s.step_id,
                    "kind": s.kind,
                    "status": s.status.value if hasattr(s.status, "value") else str(s.status),
                    "attempt": s.attempt,
                    "created_at": getattr(s, "created_at", None),
                }
                for s in steps
            ]
            return {"run_id": run_id, "items": items, "count": len(items)}
        except Exception as exc:
            return error_response(exc)

    @app.get("/api/v1/runs/{run_id}/checkpoints")
    async def list_run_checkpoints(run_id: str):
        """P6-C2：Step 级 checkpoint 台账——恢复后的 Run 可追溯每一步。"""
        try:
            repository.get_run(run_id)
        except Exception as exc:
            return error_response(exc)
        return {"run_id": run_id, "items": repository.list_step_checkpoints(run_id)}

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

    @app.post("/api/v1/runs/{run_id}/retry_unknown_external", response_model=RunDetailResponse)
    async def retry_unknown_external_run(run_id: str):
        try:
            orchestrator.resume(run_id, RecoveryDecision.RETRY_UNKNOWN_EXTERNAL)
            return query_service.get_run(run_id)
        except Exception as exc:
            return error_response(exc)

    @app.post("/api/v1/runs/{run_id}/fail_unknown_external", response_model=RunDetailResponse)
    async def fail_unknown_external_run(run_id: str):
        try:
            orchestrator.resume(run_id, RecoveryDecision.FAIL_UNKNOWN_EXTERNAL)
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

    # ------------------------------------------------------- P6-A1 成果导出
    def _export_title_resolver(document_id: str):
        lookup = getattr(retrieval_pipeline, "document_by_id", None)
        if not lookup:
            return None
        from conflux_weave.documents import document_title_from_segments

        return document_title_from_segments(lookup, document_id, "")

    _corpus_manifest_path = Path(config_paths["corpus_manifest"]) if (config_paths or {}).get("corpus_manifest") else None
    export_service = ExportService(
        repository,
        chat_service,
        title_resolver=_export_title_resolver,
        corpus_manifest_path=_corpus_manifest_path,
    )
    app.state.export_service = export_service

    # ------------------------------------------------------ P6-B2/B3 受限计算工具
    compute_workspace = Path(db_path).parent / "workspace" / "compute" if db_path and db_path != ":memory:" else Path(tempfile.gettempdir()) / "cw-compute"
    _compute_store = getattr(repository, "artifact_store", None)
    compute_sandbox = (
        RestrictedComputeSandbox(_compute_store, workspace_root=compute_workspace)
        if _compute_store is not None
        else None
    )
    app.state.compute_sandbox = compute_sandbox

    @app.post("/api/v1/tools/compute")
    async def execute_compute_tool(request: Request):
        """P6-B2/B3：受限计算工具（compute 类，满足资源限制后自动执行）。

        工具调用与产物关系通过 agent_events 进入 Run 可见面（携带 run_id 时）。
        非法输入折叠为 422；沙箱内部错误不影响 API 进程。
        """
        try:
            payload = await request.json()
        except Exception:
            return JSONResponse(status_code=422, content={"code": "invalid_request", "message": "请求体必须是 JSON。"})
        try:
            tool_request = ComputeToolRequest.from_payload(payload)
        except ValueError as exc:
            return JSONResponse(status_code=422, content={"code": "invalid_request", "message": str(exc)})
        policy = ToolPolicy.from_payload(payload.get("tool_policy"))
        run_id = payload.get("run_id")

        # B3 决策：compute 类自动执行（策略显式禁止除外）
        if compute_sandbox is None:
            return JSONResponse(status_code=503, content={"code": "service_unavailable", "message": "计算工具存储未就绪。"})
        decision = policy.decide(str(payload.get("tool_name") or "python.compute"), ToolClass.COMPUTE)
        if decision is ToolDecision.DENIED:
            return JSONResponse(
                status_code=403,
                content={"code": "tool_denied", "message": "该工具被当前策略禁止。", "tool_policy": policy.to_dict()},
            )

        # P6-B4 预算硬限制：预留 → 执行 → 记录实际/释放；超限后不得执行亦不得经兜底绕过。
        reservation_id = None
        if run_id:
            try:
                reservation_id = repository.reserve_tool_budget(
                    run_id,
                    tool_calls=1,
                    wall_clock_seconds=int(min(tool_request.timeout_seconds, policy.max_timeout_seconds)),
                )
            except ToolBudgetExceeded as exc:
                repository.stop_tool_budget(run_id)
                try:
                    repository.transition_run(run_id, RunStatus.FAILED)
                except Exception:
                    pass
                return {
                    "schema_version": COMPUTE_TOOL_RESULT_SCHEMA,
                    "status": "budget_exceeded",
                    "stdout_artifact_id": None,
                    "output_artifact_ids": [],
                    "duration_ms": 0,
                    "resource_usage": {},
                    "error": f"budget_exceeded: {exc}",
                    "tool_contract": {"run_id": run_id},
                }

        result = compute_sandbox.execute(tool_request, policy=policy, run_id=run_id)
        if run_id and reservation_id:
            if result.status in {"timeout", "budget_exceeded"}:
                repository.release_tool_budget(reservation_id)
            else:
                repository.settle_tool_budget(
                    reservation_id,
                    actual_tool_calls=1,
                    actual_seconds=max(1, result.duration_ms // 1000),
                )
        return result.to_dict()
    _EXPORT_FORMATS = ("markdown", "bibtex", "json", "zip")

    def _export_response(document, export_format: str, download_stem: str) -> Response:
        if export_format == "markdown":
            payload = render_export_markdown(document)
            media_type = "text/markdown; charset=utf-8"
            filename = f"{download_stem}.md"
        elif export_format == "bibtex":
            payload = render_export_bibtex(document)
            media_type = "application/x-bibtex; charset=utf-8"
            filename = f"{download_stem}.bib"
        elif export_format == "json":
            payload = build_export_json(document)
            media_type = "application/json; charset=utf-8"
            filename = f"{download_stem}.json"
        else:
            payload = build_export_zip(document)
            media_type = "application/zip"
            filename = f"{download_stem}.zip"
        quoted = quote(filename, safe="")
        return Response(
            content=payload,
            media_type=media_type,
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quoted}"},
        )

    def _export_download_stem(document) -> str:
        raw = str(document.metadata.get("run_id") or document.object_id)
        stem = re.sub(r"[^\w\u4e00-\u9fff.-]+", "-", raw.strip())
        return (stem[:80] or "conflux-weave-export") + f"-{document.object_kind}"

    def _export_download_stem(document) -> str:
        raw = str(document.metadata.get("run_id") or document.object_id)
        stem = re.sub(r"[^\w\u4e00-\u9fff.-]+", "-", raw.strip())
        return (stem[:80] or "conflux-weave-export") + f"-{document.object_kind}"

    @app.get("/api/v1/runs/{run_id}/export")
    async def export_run(run_id: str, format: str = "markdown"):
        export_format = format.lower().strip()
        if export_format not in _EXPORT_FORMATS:
            return JSONResponse(
                status_code=422,
                content={
                    "code": "invalid_request",
                    "message": f"不支持的导出格式：{format}（可选 {', '.join(_EXPORT_FORMATS)}）。",
                },
            )
        try:
            document = export_service.collect_run_export(run_id)
            return _export_response(document, export_format, _export_download_stem(document))
        except Exception as exc:
            return error_response(exc)

    @app.get("/api/v1/runs/{run_id}/evidence/{evidence_id}/export")
    async def export_run_evidence(run_id: str, evidence_id: str, format: str = "markdown"):
        export_format = format.lower().strip()
        if export_format not in ("markdown", "json"):
            return JSONResponse(
                status_code=422,
                content={
                    "code": "invalid_request",
                    "message": "单条 Evidence 仅支持 markdown 与 json 导出。",
                },
            )
        try:
            document = export_service.collect_evidence_export(run_id, evidence_id)
            return _export_response(document, export_format, _export_download_stem(document))
        except Exception as exc:
            return error_response(exc)

    @app.get("/api/v1/chat/messages/{message_id}/export")
    async def export_chat_answer(message_id: str, format: str = "markdown"):
        export_format = format.lower().strip()
        if export_format not in _EXPORT_FORMATS:
            return JSONResponse(
                status_code=422,
                content={
                    "code": "invalid_request",
                    "message": f"不支持的导出格式：{format}（可选 {', '.join(_EXPORT_FORMATS)}）。",
                },
            )
        try:
            document = export_service.collect_chat_answer_export(message_id)
            return _export_response(document, export_format, _export_download_stem(document))
        except Exception as exc:
            return error_response(exc)

    @app.get("/api/v1/notes/{note_id}/export")
    async def export_note(note_id: str, format: str = "markdown"):
        export_format = format.lower().strip()
        if export_format not in ("markdown", "json"):
            return JSONResponse(
                status_code=422,
                content={
                    "code": "invalid_request",
                    "message": "笔记仅支持 markdown 与 json 导出。",
                },
            )
        try:
            document = export_service.collect_note_export(note_id)
            return _export_response(document, export_format, _export_download_stem(document))
        except Exception as exc:
            return error_response(exc)



    @app.get("/api/v1/health/live")
    async def live_health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/v1/library")
    async def library_overview(status: str = Query(default="active")):
        manifest_path = Path((config_paths or {}).get("corpus_manifest", ""))
        registry_path = repository.database_path.with_name("library-registry.json")
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {}
            registry = json.loads(registry_path.read_text(encoding="utf-8")) if registry_path.is_file() else []
            merged_dict: dict[str, dict[str, Any]] = {}
            for row in payload.get("files", []):
                ident = row.get("document_id") or row.get("source_snapshot_id") or row.get("relative_path")
                if ident:
                    merged_dict[ident] = {**row, "_manifest_indexed": True}
            for row in registry:
                ident = row.get("document_id") or row.get("source_snapshot_id") or row.get("relative_path")
                if ident:
                    if ident in merged_dict:
                        merged_dict[ident].update(row)
                    else:
                        merged_dict[ident] = row
            rows = list(merged_dict.values())
        except (OSError, ValueError):
            return {"total": 0, "imported": 0, "index_status": "清单不可读", "items": []}
        items = []
        for row in rows:
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
        # P6-A2：文档生命周期（SQLite document_lifecycle 表），默认隐藏归档/删除
        lifecycle_map = repository.get_document_lifecycle_map()
        for item in items:
            item["lifecycle"] = lifecycle_map.get(str(item.get("document_id") or ""), "active")
        if status in {"active", "archived", "deleted"}:
            items = [item for item in items if item["lifecycle"] == status]
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

    @app.post("/api/v1/library/documents/{document_id}/lifecycle")
    async def set_document_lifecycle(document_id: str, request: DocumentLifecycleRequest):
        """P6-A2：文档归档/软删除/恢复（SQLite 权威，不物理删除任何资料记录）。"""
        try:
            state = repository.set_document_lifecycle(document_id, request.action)
            return {"document_id": document_id, "lifecycle": state}
        except Exception as exc:
            return error_response(exc)

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
                figure_assets = [a for a in document.assets if getattr(a, "asset_kind", "") != "icon"]
                if figure_assets:
                    await asyncio.to_thread(
                        build_image_index,
                        figure_assets,
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
    _asset_by_id_cache: dict[str, dict[str, Any]] = {}

    def _index_manifest_assets(payload: dict[str, Any]) -> None:
        for a in payload.get("assets", []):
            aid = a.get("asset_id")
            if aid:
                _asset_by_id_cache[aid] = a

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
                _index_manifest_assets(payload)
                return payload
        except Exception:
            pass
        return None

    async def get_or_extract_document_assets(item: dict[str, Any]) -> dict[str, Any] | None:
        art_id = item.get("assets_artifact_id")
        if art_id and str(art_id).startswith("artifact-sha256-"):
            payload = _load_manifest_payload(str(art_id))
            if payload:
                _index_manifest_assets(payload)
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
                    _index_manifest_assets(manifest_dict)
                    return manifest_dict
            except Exception:
                pass
        return None

    async def resolve_registered_asset(asset_id: str) -> dict[str, Any] | None:
        manifest_path = Path((config_paths or {}).get("corpus_manifest", ""))
        registry_path = repository.database_path.with_name("library-registry.json")
        registry_configured = registry_path.is_file() or manifest_path.is_file()

        overview = await library_overview(status="")
        valid_doc_ids = {
            str(val)
            for doc in overview.get("items", [])
            for key in ("document_id", "paper_id", "record_id", "source_snapshot_id", "relative_path")
            if (val := doc.get(key))
        }

        if registry_configured and not valid_doc_ids:
            _asset_by_id_cache.pop(asset_id, None)
            return None

        def is_authorized(doc_identifier: str) -> bool:
            if not registry_configured:
                return True
            return doc_identifier in valid_doc_ids

        if asset_id in _asset_by_id_cache:
            cached = _asset_by_id_cache[asset_id]
            doc_id = str(cached.get("document_id") or cached.get("source_snapshot_id") or "")
            if is_authorized(doc_id):
                return cached
            _asset_by_id_cache.pop(asset_id, None)

        # 1. Direct query against LanceDB image_assets_v1 table
        if retrieval_pipeline is not None and getattr(retrieval_pipeline, "image_index", None) is not None:
            try:
                tbl = retrieval_pipeline.image_index.table
                clean_id = asset_id.replace("'", "''")
                res = tbl.search().where(f"asset_id = '{clean_id}'").limit(1).to_list()
                if res:
                    r = res[0]
                    doc_id = str(r.get("document_id", ""))
                    if is_authorized(doc_id):
                        loc = {}
                        if r.get("locator_json"):
                            try:
                                loc = json.loads(r["locator_json"])
                            except Exception:
                                pass
                        asset_dict = {
                            "schema_version": "conflux-weave.document-asset.v1",
                            "asset_id": r["asset_id"],
                            "document_id": doc_id,
                            "source_snapshot_id": r.get("source_snapshot_id", ""),
                            "page": r.get("page", 1),
                            "asset_kind": "embedded_image",
                            "artifact_ref": r.get("artifact_ref", ""),
                            "thumbnail_artifact_ref": r.get("thumbnail_artifact_ref", ""),
                            "media_type": "image/png",
                            "bbox": loc.get("bbox"),
                            "coordinate_space": loc.get("coordinate_space", "pdf_page_points_top_left"),
                            "page_width": loc.get("page_width", 0.0),
                            "page_height": loc.get("page_height", 0.0),
                            "page_rotation": loc.get("page_rotation", 0),
                            "caption": r.get("caption", ""),
                            "extraction_method": r.get("extractor_version", "pymupdf-v1"),
                            "extraction_status": "extracted",
                            "warnings": [],
                        }
                        _asset_by_id_cache[asset_id] = asset_dict
                        return asset_dict
            except Exception:
                pass

        for doc in overview.get("items", []):
            art_id = doc.get("assets_artifact_id")
            if not art_id:
                continue
            _load_manifest_payload(str(art_id))
            if asset_id in _asset_by_id_cache:
                cached = _asset_by_id_cache[asset_id]
                doc_id = str(cached.get("document_id") or cached.get("source_snapshot_id") or "")
                if doc_id in valid_doc_ids:
                    return cached

        return None

    @app.get("/api/v1/library/assets")
    async def library_all_assets(
        document_id: str | None = None,
        asset_type: str | None = None,
        limit: int = Query(60, ge=1, le=300),
    ):
        overview = await library_overview()
        items = overview.get("items", [])
        doc_title_map = {}
        for item in items:
            did = str(item.get("document_id") or item.get("paper_id") or item.get("record_id") or "")
            if did:
                doc_title_map[did] = item.get("title") or did

        all_assets = []
        seen_asset_ids = set()

        # 1. First priority: Direct query from LanceDB image_assets_v1 table if available (fast & pre-indexed)
        if retrieval_pipeline is not None and getattr(retrieval_pipeline, "image_index", None) is not None:
            try:
                tbl = retrieval_pipeline.image_index.table
                where_clause = None
                if document_id:
                    clean_doc_id = document_id.replace("'", "''")
                    where_clause = f"document_id = '{clean_doc_id}'"
                search_builder = tbl.search()
                if where_clause:
                    search_builder = search_builder.where(where_clause)
                rows = search_builder.limit(limit).to_list()
                for r in rows:
                    aid = r["asset_id"]
                    if aid in seen_asset_ids:
                        continue
                    loc = {}
                    if r.get("locator_json"):
                        try:
                            loc = json.loads(r["locator_json"])
                        except Exception:
                            pass
                    asset_dict = {
                        "schema_version": "conflux-weave.document-asset.v1",
                        "asset_id": aid,
                        "document_id": r.get("document_id", ""),
                        "source_snapshot_id": r.get("source_snapshot_id", ""),
                        "page": r.get("page", 1),
                        "asset_kind": "embedded_image",
                        "artifact_ref": r.get("artifact_ref", ""),
                        "thumbnail_artifact_ref": r.get("thumbnail_artifact_ref", ""),
                        "media_type": "image/png",
                        "bbox": loc.get("bbox"),
                        "coordinate_space": loc.get("coordinate_space", "pdf_page_points_top_left"),
                        "page_width": loc.get("page_width", 0.0),
                        "page_height": loc.get("page_height", 0.0),
                        "page_rotation": loc.get("page_rotation", 0),
                        "caption": r.get("caption", ""),
                        "extraction_method": r.get("extractor_version", "pymupdf-v1"),
                        "extraction_status": "extracted",
                        "warnings": [],
                    }
                    _asset_by_id_cache[aid] = asset_dict
                    detail = _build_asset_detail_response(asset_dict).model_dump()
                    detail["document_title"] = doc_title_map.get(r.get("document_id", ""), r.get("document_id", ""))
                    all_assets.append(detail)
                    seen_asset_ids.add(aid)
                    if len(all_assets) >= limit:
                        break
            except Exception:
                pass

        # 2. Also check cached manifests for already-extracted document assets
        if len(all_assets) < limit:
            for item in items:
                art_id = item.get("assets_artifact_id")
                if not art_id or not str(art_id).startswith("artifact-sha256-"):
                    continue
                doc_id = str(item.get("document_id") or item.get("paper_id") or item.get("record_id") or "")
                if document_id and doc_id != document_id:
                    continue
                doc_title = item.get("title") or doc_id
                payload = _load_manifest_payload(str(art_id))
                if not payload:
                    continue
                for a in payload.get("assets", []):
                    aid = a.get("asset_id")
                    if aid in seen_asset_ids:
                        continue
                    if a.get("asset_kind") == "icon":
                        continue
                    bbox = a.get("bbox") or {}
                    w_pt = float(bbox.get("width", 0) or 0)
                    h_pt = float(bbox.get("height", 0) or 0)
                    w_px = int(a.get("width_px", 0) or 0)
                    h_px = int(a.get("height_px", 0) or 0)
                    if (w_pt > 0 and h_pt > 0 and ((w_pt <= 120 and h_pt <= 120) or (w_pt * h_pt < 10000))):
                        continue
                    if (w_px > 0 and h_px > 0 and ((w_px <= 130 and h_px <= 130) or (w_px * h_px < 15000 and (w_pt == 0 or w_pt * h_pt < 12000)))):
                        continue
                    if asset_type and a.get("asset_type") != asset_type:
                        continue
                    detail = _build_asset_detail_response(a).model_dump()
                    detail["document_title"] = doc_title
                    all_assets.append(detail)
                    seen_asset_ids.add(aid)
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
                ref = asset.get("artifact_ref")
                variant = "original"
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
                    cjk_map = [
                        ("多模态", "multimodal"),
                        ("大语言模型", "large language models"),
                        ("大模型", "large language models"),
                        ("智能体", "autonomous agents"),
                        ("强化学习", "reinforcement learning"),
                        ("深度学习", "deep learning"),
                        ("图神经网络", "graph neural networks"),
                        ("图网络", "graph neural networks"),
                        ("知识图谱", "knowledge graphs"),
                        ("注意力机制", "attention mechanism"),
                        ("注意力", "attention"),
                        ("长文本", "long context"),
                        ("对齐", "alignment"),
                        ("微调", "fine-tuning"),
                        ("检索增强", "retrieval augmented generation"),
                        ("联邦学习", "federated learning"),
                        ("自监督", "self-supervised"),
                        ("扩散模型", "diffusion models"),
                        ("机器翻译", "machine translation"),
                        ("计算机视觉", "computer vision"),
                        ("自然语言处理", "natural language processing"),
                        ("语义分割", "semantic segmentation"),
                        ("目标检测", "object detection"),
                        ("推荐系统", "recommender systems"),
                        ("时间序列", "time series"),
                        ("代码生成", "code generation"),
                    ]
                    translated_phrases = [en for zh, en in cjk_map if zh in query]
                    cleaned_cjk = re.sub(r"(我想找|请帮我找|寻找|检索|关于|相关的|最新|综述|论文|研究|探讨|基于|在|中的|应用)", " ", query).strip()
                    if translated_phrases:
                        openalex_query = " ".join(translated_phrases)
                        queries = translated_phrases[:3]
                        required_terms = tuple(dict.fromkeys(term for p in translated_phrases[:2] for term in p.split()))[:2]
                    else:
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
                if len(terms) >= 3:
                    retrieval_queries.append(" AND ".join(f"all:{term}" for term in dict.fromkeys(terms[:2])))
                    retrieval_queries.append(f"all:{terms[0]}")
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
        if len(merged) < target_results and (required_terms or required_concepts):
            relaxed = merge_and_rank(records, query_terms=query_terms, max_results=max(1, len(records)), sort=sort)
            existing_ids = {p.paper_id for p in merged}
            for p in relaxed:
                if p.paper_id not in existing_ids:
                    merged = merged + (p,)
                    existing_ids.add(p.paper_id)
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

    def _effective_provider_view() -> ProviderConfigResponse | None:
        """A5：当前进程实际生效的 Provider 配置（来自启动时装配的适配器）。"""
        if provider_effective is None:
            return None
        return ProviderConfigResponse(
            base_url=getattr(provider_effective, "base_url", ""),
            model=getattr(provider_effective, "model", ""),
            embedding_model=getattr(provider_effective, "embedding_model", "") or "",
            reranker_model=getattr(provider_effective, "reranker_model", "") or "",
            engine_model=getattr(provider_effective, "engine_model", "") or "",
            image_embedding_model=getattr(provider_effective, "image_embedding_model", "") or "",
            contact_email="",
            api_key_configured=bool(getattr(provider_effective, "api_key", "")),
            api_key_hint=None,
        )

    @app.get("/api/v1/config", response_model=WorkbenchConfigResponse)
    async def get_config():
        try:
            return WorkbenchConfigResponse(
                provider=_provider_view(),
                provider_active=provider_configured,
                paths=config_paths or {},
                provider_effective=_effective_provider_view(),
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
        embedding_model = (
            request.embedding_model
            or stored.get("CONFLUX_WEAVE_PROVIDER_EMBEDDING_MODEL", "")
        ).strip()
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
        embedding_probe: ProviderEmbeddingProbe | None = None
        if embedding_model:
            # B1：配置了 embedding 模型时同步探测 /embeddings 连通性；
            # 探测失败不影响 chat 探测结论，只如实报告给设置页。
            embedding_probe = ProviderEmbeddingProbe(attempted=True, ok=None, message="")
            try:
                from conflux_weave.provider import (
                    OpenAICompatibleEmbeddingAdapter,
                    ProviderConfig,
                )

                embedding_adapter = OpenAICompatibleEmbeddingAdapter(
                    repository.artifact_store,
                    ProviderConfig(
                        base_url=base_url.strip().rstrip("/"),
                        api_key=api_key.strip(),
                        model=model.strip(),
                        embedding_model=embedding_model,
                    ),
                    timeout_seconds=20.0,
                )
                started = time.monotonic()
                result = await asyncio.to_thread(
                    embedding_adapter.embed,
                    ["Conflux-Weave 连通性探测"],
                    producer_step_id="step-provider-config-embedding-test",
                )
                embedding_probe = ProviderEmbeddingProbe(
                    attempted=True,
                    ok=True,
                    message=f"Embedding 服务可连通（模型 {result.model}）。",
                    latency_ms=int((time.monotonic() - started) * 1000),
                    dimensions=len(result.vectors[0]) if result.vectors else None,
                    input_tokens=result.input_tokens,
                )
            except Exception as exc:
                embedding_probe = ProviderEmbeddingProbe(
                    attempted=True,
                    ok=False,
                    message=str(exc) or "Embedding 连接失败。",
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
                embedding=embedding_probe,
            )
        except Exception as exc:
            return ProviderConfigTestResponse(ok=False, message=str(exc) or "连接失败。", embedding=embedding_probe)

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
        # A1 全局搜索：笔记保存即入索引（尽力而为，失败不影响笔记落盘）。
        if search_service is not None:
            try:
                search_service.index_note(note)
            except Exception:
                pass

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

    @app.get("/api/v1/documents/{document_id}/note", response_model=DocumentNoteResponse)
    async def get_document_latest_note_endpoint(document_id: str):
        store = getattr(repository, "artifact_store", None)
        if store is None:
            return JSONResponse(status_code=503, content={"code": "service_unavailable", "message": "笔记存储服务未就绪。"})
        try:
            entries = [item for item in load_notes_registry() if item.get("document_id") == document_id]
            if not entries:
                entries = [
                    item for item in load_notes_registry()
                    if item.get("document_id") and (
                        Path(item.get("document_id", "")).stem == Path(document_id).stem
                        or item.get("document_id") == Path(document_id).name
                    )
                ]
            if not entries:
                return JSONResponse(status_code=404, content={"code": "note_not_found", "message": f"文档 {document_id} 尚未生成分析笔记。"})

            tip_entry = max(entries, key=lambda x: int(x.get("version") or 0))
            note_id = tip_entry.get("note_id")
            if not note_id:
                return JSONResponse(status_code=404, content={"code": "note_not_found", "message": f"文档 {document_id} 笔记索引异常。"})

            try:
                note_obj = load_note_artifact(note_id, store)
            except (KeyError, ValueError):
                note_obj = None

            if note_obj is None:
                return JSONResponse(status_code=404, content={"code": "note_not_found", "message": f"笔记产物 {note_id} 不存在。"})

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

            # P7-V 真实验证修复：链式不可变笔记的 note_id 按文档+版本确定性生成，
            # 对旧修订（非 tip）发起 patch 会通过版本校验并以同 ID 新产物静默覆盖
            # 较新修订，409 永不可达。此处将"非 tip 修订"显式判为版本冲突，
            # latest_version 指向真实 tip，前端据此刷新基线后重应用。
            entries = [
                item for item in load_notes_registry()
                if item.get("document_id") == note_obj.document_id
            ]
            tip_entry = (
                max(entries, key=lambda x: int(x.get("version") or 0))
                if entries
                else None
            )
            tip_version = int(tip_entry.get("version") or 0) if tip_entry else note_obj.version
            tip_note_id = str(tip_entry.get("note_id") or note_obj.note_id) if tip_entry else note_obj.note_id

            if note_obj.version < tip_version:
                return JSONResponse(
                    status_code=409,
                    content={
                        "code": "version_conflict",
                        "message": f"笔记 {note_obj.note_id} 已不是最新修订（当前最新版本 {tip_version}），已拒绝以避免静默覆盖。",
                        "latest_version": tip_version,
                        "latest_note_id": tip_note_id,
                    },
                )

            if request.target_version != note_obj.version:
                return JSONResponse(
                    status_code=409,
                    content={
                        "code": "version_conflict",
                        "message": f"目标版本 {request.target_version} 与当前笔记版本 {note_obj.version} 不一致。",
                        # A5：附最新版本号与最新笔记 ID，前端可据此刷新基线并自动重应用一次。
                        "latest_version": tip_version,
                        "latest_note_id": tip_note_id,
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
            # A2 研读联动：引用锚点校验——document_id + quote 必填；
            # 无页码/分段/资产定位时显式 unanchored=true（不伪造定位）。
            quote_anchor = None
            if request.quote_anchor:
                anchor = dict(request.quote_anchor)
                if not anchor.get("document_id") or not str(anchor.get("quote") or "").strip():
                    return JSONResponse(
                        status_code=422,
                        content={"code": "invalid_quote_anchor", "message": "quote_anchor 需要 document_id 与非空 quote。"},
                    )
                if not anchor.get("page") and not anchor.get("segment_id") and not anchor.get("asset_id"):
                    anchor["unanchored"] = True
                quote_anchor = anchor
            patch, new_note = await asyncio.to_thread(
                doc_agent.revise_note,
                note_obj,
                request.instruction,
                patch_ops=patch_ops,
                document_context=doc_context,
                quote_anchor=quote_anchor,
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

    @app.post("/api/v1/notes/from-chat", response_model=DocumentNoteResponse)
    async def create_note_from_chat_endpoint(request: NoteFromChatRequest):
        store = getattr(repository, "artifact_store", None)
        if store is None:
            return JSONResponse(status_code=503, content={"code": "service_unavailable", "message": "笔记存储服务未就绪。"})

        content = request.content.strip()
        if not content:
            return JSONResponse(status_code=400, content={"code": "empty_content", "message": "笔记内容不能为空。"})

        # 1. 确定关联文档与文献快照
        doc_id = request.document_id
        if not doc_id and request.citations:
            for c in request.citations:
                snap = c.get("source_snapshot_id") or c.get("chunk_id")
                loc = c.get("locator") or {}
                cand = snap or (loc.get("document_id") if isinstance(loc, dict) else None)
                if cand:
                    doc_id = str(cand)
                    break

        if not doc_id and request.conversation_id and chat_service is not None:
            try:
                hist = chat_service.conversation(request.conversation_id, limit=10)
                for msg in hist:
                    if getattr(msg, "document_ids", None):
                        doc_id = msg.document_ids[0]
                        break
            except Exception:
                pass

        if not doc_id:
            doc_id = f"chat-synthesis-{uuid4().hex[:8]}"

        # 2. 生成学术笔记标题
        title = request.title
        if not title:
            first_line = content.splitlines()[0].lstrip("#").strip().strip("*")
            if first_line and len(first_line) <= 60:
                title = f"研读笔记：{first_line}"
            else:
                title = f"学术对话研读结论 ({doc_id[:16]})"

        # 3. 提取引用与片段
        citation_labels = []
        source_segments = []
        for c in request.citations:
            idx = c.get("index")
            chunk = c.get("chunk_id")
            snap = c.get("source_snapshot_id")
            loc = c.get("locator") or {}
            label = f"[{idx}] {snap or chunk}" if idx else str(snap or chunk or "")
            if isinstance(loc, dict) and loc.get("page"):
                label += f" (第 {loc['page']} 页)"
            citation_labels.append(label)
            if chunk:
                source_segments.append(str(chunk))

        # 4. 确定版本 (同一 document_id 若已有笔记，递增版本并链接 parent_note_id)
        entries = [item for item in load_notes_registry() if item.get("document_id") == doc_id]
        tip_version = max((int(x.get("version") or 0) for x in entries), default=0)
        version = tip_version + 1
        note_id = f"note-{uuid4().hex[:12]}"
        parent_note_id = None
        if tip_version > 0:
            parent_entry = next((x for x in entries if int(x.get("version") or 0) == tip_version), None)
            if parent_entry:
                parent_note_id = parent_entry.get("note_id")

        # 5. 构建 DocumentNote 结构
        sec_title = "核心研讨结论与论证"
        sections = (
            NoteSection(
                section_id=f"{doc_id}:note-sec-001",
                title=sec_title,
                level=2,
                content=content,
                source_segments=tuple(source_segments),
                citations=tuple(citation_labels),
            ),
        )

        metadata = {
            "source": "chat",
            "conversation_id": request.conversation_id,
            "message_id": request.message_id,
            "created_from": "chat_synthesis",
        }

        note_obj = DocumentNote(
            note_id=note_id,
            document_id=doc_id,
            title=title,
            version=version,
            parent_note_id=parent_note_id,
            executive_summary=content[:300].strip(),
            sections=sections,
            metadata=metadata,
        )

        # 6. 持久化至 ArtifactStore 与 notes-registry
        save_note_artifact(note_obj, store)
        save_note_entry(note_obj)

        # 7. 若提供了 topic_id，自动关联到该专题工作区
        if request.topic_id:
            try:
                t_store = _get_topic_store()
                t_store.link_object(request.topic_id, "note", note_obj.note_id)
                if not doc_id.startswith("chat-synthesis-"):
                    t_store.link_object(request.topic_id, "document", doc_id)
            except Exception:
                pass

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

            note_meta = note_obj.metadata or {}
            input_tokens = int(note_meta.get("input_tokens", 0) or 0)
            output_tokens = int(note_meta.get("output_tokens", 0) or 0)
            tokens_consumed = int(note_meta.get("tokens_consumed", 0) or (input_tokens + output_tokens))
            if input_tokens <= 0 and output_tokens <= 0:
                report_chars = len(report_text)
                input_tokens = max(180, report_chars // 4)
                output_tokens = max(120, report_chars // 3)
                tokens_consumed = input_tokens + output_tokens

            elapsed_seconds = int(round(float(note_meta.get("elapsed_seconds", 0) or 0)))
            if elapsed_seconds <= 0:
                elapsed_seconds = max(1, min(120, int(len(report_text) // 250)))

            if hasattr(repository, "_connect"):
                try:
                    with repository._connect() as conn:
                        conn.execute(
                            """
                            INSERT INTO budget_entries(
                                run_id, step_id, attempt_id, reservation_id, entry_kind,
                                input_tokens, output_tokens, tool_calls, retrieval_rounds,
                                source, created_at
                            ) VALUES (?, ?, ?, ?, 'actual', ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                run_id,
                                step_id,
                                f"{step_id}:att-1",
                                f"res-{run_id}-actual",
                                input_tokens,
                                output_tokens,
                                1,
                                1,
                                "document_reading",
                                now,
                            ),
                        )
                        conn.execute(
                            """
                            INSERT INTO tool_budget_usage (run_id, tool_calls, wall_clock_seconds, source, created_at)
                            VALUES (?, ?, ?, 'document_reading', ?)
                            """,
                            (
                                run_id,
                                1,
                                elapsed_seconds,
                                now,
                            ),
                        )
                except Exception:
                    pass

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
        return project_store

    def _get_topic_store() -> TopicStore:
        return topic_store

    def _get_project_agent() -> ProjectAgent:
        chat_adapter = getattr(chat_service, "_chat", None) if chat_service is not None else None
        return ProjectAgent(provider=chat_adapter, memory_agent=memory_agent)

    def _get_coding_agent() -> CodingAgent:
        chat_adapter = getattr(chat_service, "_chat", None) if chat_service is not None else None
        return CodingAgent(provider=chat_adapter)

    _active_proposals: dict[str, CodeProposal] = {}

    @app.post("/api/v1/projects/browse-folder")
    async def browse_folder_endpoint(initial_dir: str | None = Query(None)):
        def _open_picker() -> str | None:
            if os.name == "nt":
                # Priority 1: Tkinter native dialog via a clean, isolated subprocess with current python interpreter
                try:
                    init_dir = str(Path(initial_dir).resolve()) if initial_dir and Path(initial_dir).is_dir() else ""
                    py_code = (
                        "import tkinter as tk, tkinter.filedialog as fd, sys, os\n"
                        "root = tk.Tk()\n"
                        "root.withdraw()\n"
                        "root.wm_attributes('-topmost', 1)\n"
                        "root.focus_force()\n"
                        f"initial = {repr(init_dir)}\n"
                        "path = fd.askdirectory(parent=root, title='选择工程项目根目录', initialdir=initial if initial and os.path.isdir(initial) else None)\n"
                        "root.destroy()\n"
                        "if path:\n"
                        "    sys.stdout.buffer.write(path.encode('utf-8'))\n"
                    )
                    res = subprocess.run(
                        [sys.executable, "-c", py_code],
                        capture_output=True,
                        timeout=120,
                    )
                    if res.returncode == 0 and res.stdout:
                        selected = res.stdout.decode("utf-8", errors="replace").strip()
                        if selected and Path(selected).is_dir():
                            return str(Path(selected).resolve())
                except Exception as tk_err:
                    logger.warning(f"Tkinter folder picker failed: {tk_err}")

                # Priority 2: PowerShell with TopMost WinForms owner Form
                try:
                    init_dir = str(Path(initial_dir).resolve()).replace("'", "''") if initial_dir and Path(initial_dir).is_dir() else ""
                    init_clause = f"$f.SelectedPath = '{init_dir}';" if init_dir else ""
                    ps_script = (
                        "Add-Type -AssemblyName System.Windows.Forms | Out-Null\n"
                        "$form = New-Object System.Windows.Forms.Form\n"
                        "$form.TopMost = $true\n"
                        "$form.Width = 0\n"
                        "$form.Height = 0\n"
                        "$form.ShowInTaskbar = $false\n"
                        "$f = New-Object System.Windows.Forms.FolderBrowserDialog\n"
                        "$f.Description = '选择工程项目根目录'\n"
                        "$f.ShowNewFolderButton = $true\n"
                        f"{init_clause}\n"
                        "if ($f.ShowDialog($form) -eq [System.Windows.Forms.DialogResult]::OK) {\n"
                        "    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8\n"
                        "    Write-Output $f.SelectedPath\n"
                        "}\n"
                        "$f.Dispose()\n"
                        "$form.Dispose()\n"
                    )
                    encoded_cmd = base64.b64encode(ps_script.encode("utf-16le")).decode("ascii")
                    res = subprocess.run(
                        ["powershell.exe", "-NoProfile", "-Sta", "-ExecutionPolicy", "Bypass", "-EncodedCommand", encoded_cmd],
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        timeout=90,
                    )
                    path = res.stdout.strip().splitlines()[-1].strip() if res.stdout.strip() else ""
                    if path and Path(path).is_dir():
                        return str(Path(path).resolve())
                except Exception as ps_err:
                    logger.warning(f"PowerShell folder picker fallback returned: {ps_err}")

            return None

        try:
            folder = await asyncio.to_thread(_open_picker)
            return {"path": folder}
        except Exception as exc:
            logger.error(f"browse_folder_endpoint error: {exc}")
            return {"path": None}

    @app.get("/api/v1/projects/fs-drives")
    async def get_fs_drives_endpoint():
        drives = []
        if os.name == "nt":
            import string
            for letter in string.ascii_uppercase:
                drive = f"{letter}:\\"
                if os.path.exists(drive):
                    drives.append(drive)
            if not drives:
                drives.append("C:\\")
        else:
            drives = ["/"]
        quick_roots = []
        try:
            home = str(Path.home().resolve())
            if home and home not in drives:
                quick_roots.append(home)
            cwd = str(Path.cwd().resolve())
            if cwd and cwd not in drives and cwd not in quick_roots:
                quick_roots.append(cwd)
        except Exception:
            pass
        return {"drives": drives, "quick_roots": quick_roots}

    @app.get("/api/v1/projects/fs-dirs")
    async def get_fs_dirs_endpoint(path: str = Query(...)):
        try:
            p = Path(path).resolve()
            if not p.is_dir():
                return {"path": str(p), "exists": False, "dirs": []}
            dirs = []
            for entry in os.scandir(p):
                try:
                    if entry.is_dir(follow_symlinks=False):
                        if entry.name.startswith(".") or entry.name.startswith("$") or entry.name.lower() in (
                            "system volume information", "$recycle.bin", "recovery", "node_modules", "target", "build", "dist", "venv", ".venv", "__pycache__"
                        ):
                            continue
                        dirs.append({
                            "name": entry.name,
                            "path": entry.path,
                        })
                except Exception:
                    continue
            dirs.sort(key=lambda d: d["name"].lower())
            parent = str(p.parent) if p.parent != p else None
            return {"path": str(p), "parent": parent, "exists": True, "dirs": dirs}
        except Exception as exc:
            return {"path": path, "exists": False, "error": str(exc), "dirs": []}

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

    @app.get("/api/v1/projects/{project_id}/learning-guide")
    async def project_learning_guide(project_id: str):
        store = _get_project_store()
        proj = store.get_project(project_id)
        if not proj:
            return JSONResponse(status_code=404, content={"code": "project_not_found", "message": f"项目 {project_id} 不存在。"})
        from conflux_weave.project_dissection import ProjectDissector
        try:
            dissector = ProjectDissector(proj.root_path, project_id=proj.project_id, project_name=proj.name)
            report = await asyncio.to_thread(dissector.dissect)
            return report.to_dict()
        except Exception as exc:
            return error_response(exc)

    @app.post("/api/v1/projects/{project_id}/ask-learning")
    async def ask_project_learning(project_id: str, payload: dict[str, Any] = Body(...)):
        store = _get_project_store()
        proj = store.get_project(project_id)
        if not proj:
            return JSONResponse(status_code=404, content={"code": "project_not_found", "message": f"项目 {project_id} 不存在。"})
        question = str(payload.get("question", "")).strip()
        if not question:
            return JSONResponse(status_code=400, content={"code": "empty_question", "message": "提问内容不能为空"})

        from conflux_weave.project_dissection import ProjectDissector, answer_project_learning_question
        try:
            dissector = ProjectDissector(proj.root_path, project_id=proj.project_id, project_name=proj.name)
            report = await asyncio.to_thread(dissector.dissect)
            chat_adapter = getattr(chat_service, "_chat", None) if chat_service is not None else None
            answer = await asyncio.to_thread(
                answer_project_learning_question,
                report,
                question,
                chat_adapter=chat_adapter,
            )
            return {"project_id": project_id, "question": question, "answer": answer}
        except Exception as exc:
            return error_response(exc)

    @app.get("/api/v1/projects/{project_id}/messages", response_model=ProjectMessagesResponse)
    async def get_project_messages_endpoint(project_id: str):
        store = _get_project_store()
        proj = store.get_project(project_id)
        if proj is None:
            return JSONResponse(status_code=404, content={"code": "project_not_found", "message": f"项目 {project_id} 不存在。"})
        raw_msgs = store.get_project_messages(project_id)
        items = tuple(
            ProjectMessageRecord(
                message_id=m.get("message_id", f"pmsg-{uuid4().hex[:10]}"),
                project_id=project_id,
                role=m.get("role", "user"),
                content=m.get("content", ""),
                created_at=m.get("created_at", datetime.now(UTC).isoformat().replace("+00:00", "Z")),
                target_tab=m.get("target_tab"),
                cited_files=tuple(m.get("cited_files", ())),
                selected_snippet=m.get("selected_snippet"),
            )
            for m in raw_msgs
        )
        return ProjectMessagesResponse(project_id=project_id, items=items)

    @app.post("/api/v1/projects/{project_id}/messages", response_model=ProjectMessageRecord)
    async def append_project_message_endpoint(project_id: str, payload: dict[str, Any] = Body(...)):
        store = _get_project_store()
        proj = store.get_project(project_id)
        if proj is None:
            return JSONResponse(status_code=404, content={"code": "project_not_found", "message": f"项目 {project_id} 不存在。"})
        saved = store.append_project_message(project_id, payload)
        return ProjectMessageRecord(
            message_id=saved["message_id"],
            project_id=project_id,
            role=saved.get("role", "user"),
            content=saved.get("content", ""),
            created_at=saved.get("created_at", datetime.now(UTC).isoformat().replace("+00:00", "Z")),
            target_tab=saved.get("target_tab"),
            cited_files=tuple(saved.get("cited_files", ())),
            selected_snippet=saved.get("selected_snippet"),
        )

    @app.post("/api/v1/projects/{project_id}/ask", response_model=ProjectAskResponse)
    async def ask_project_endpoint(project_id: str, request: ProjectAskRequest):
        store = _get_project_store()
        proj = store.get_project(project_id)
        if proj is None:
            return JSONResponse(status_code=404, content={"code": "project_not_found", "message": f"项目 {project_id} 不存在。"})
        agent = _get_project_agent()
        ans = await asyncio.to_thread(
            agent.ask,
            proj,
            request.question,
            request.current_file,
            request.selected_snippet,
            request.source_version,
        )

        # Durable Q&A persistence
        store.append_project_message(project_id, {
            "role": "user",
            "content": request.question,
            "cited_files": [request.current_file] if request.current_file else [],
            "selected_snippet": request.selected_snippet,
        })
        store.append_project_message(project_id, {
            "role": "assistant",
            "content": ans.answer_markdown,
            "cited_files": list(ans.cited_files),
        })

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
        instruction = request.instruction or request.prompt or "代码治理与架构改进"
        target_file = request.target_file
        if not target_file and request.prompt:
            match = re.search(r"(?:目标文件|文件|file)[:：\s]+([^\s\n,，;]+)", request.prompt, re.I)
            if match:
                target_file = match.group(1).strip("`'\"")

        # Gate 4: Strictly prohibit silent fallback when target_file is missing
        if not target_file:
            return JSONResponse(
                status_code=400,
                content={
                    "code": "target_file_required",
                    "message": "未能确定补丁目标文件，请显式指定或在文件树中选中目标文件后再发起提案。",
                },
            )

        target_file = re.sub(r"[:#]L?\d+$", "", target_file).strip()

        try:
            proposal = await asyncio.to_thread(
                coding_agent.propose_patch,
                proj,
                instruction,
                target_file,
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
        except ValueError as ve:
            return JSONResponse(status_code=400, content={"code": "invalid_file_type", "message": str(ve)})
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
        clean_target = re.sub(r"[:#]L?\d+$", "", request.target_file or "").strip()
        if proposal is None:
            if not clean_target or not request.proposed_content:
                return JSONResponse(
                    status_code=400,
                    content={"code": "proposal_expired", "message": "补丁提案不存在或已过期，请重新发起提案。"},
                )
            proposal = CodeProposal(
                proposal_id=request.proposal_id,
                project_id=project_id,
                title="User Approved Patch",
                rationale="Reconstructed proposal from client request",
                risk_level="medium",
                target_file=clean_target,
                original_hash=request.expected_hash or "",
                diff="",
                proposed_content=request.proposed_content,
            )
        else:
            if proposal.target_file:
                proposal.target_file = re.sub(r"[:#]L?\d+$", "", proposal.target_file).strip()
            if clean_target:
                proposal.target_file = clean_target
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

    @app.post("/api/v1/projects/{project_id}/coding/verify", response_model=CodingVerifyResponse)
    async def verify_coding_patch_endpoint(project_id: str, request: CodingVerifyRequest):
        store = _get_project_store()
        proj = store.get_project(project_id)
        if proj is None:
            return JSONResponse(status_code=404, content={"code": "project_not_found", "message": f"项目 {project_id} 不存在。"})

        proposal = _active_proposals.get(request.proposal_id)
        cmd = (request.command or "").strip()
        if not cmd and proposal and proposal.verification_commands:
            cmd = proposal.verification_commands[0]
        if not cmd:
            cmd = "pytest -q"

        # Security check: whitelist safe verification commands
        allowed_prefixes = (
            "pytest",
            "python -m pytest",
            "python -m unittest",
            "ruff",
            "mypy",
            "npm test",
            "npm run test",
            "node --test",
            "tsc --noEmit",
        )
        clean_cmd = cmd.strip()
        if not any(clean_cmd.startswith(p) for p in allowed_prefixes):
            return JSONResponse(
                status_code=400,
                content={
                    "code": "disallowed_command",
                    "message": f"不支持执行非受信任的验证命令: {cmd}。仅允许 pytest, python -m unittest, ruff, mypy, npm test 等验证指令。",
                },
            )

        start = time.monotonic()
        root = Path(proj.root_path).resolve()
        try:
            res = await asyncio.to_thread(
                subprocess.run,
                cmd,
                shell=True,
                cwd=root,
                capture_output=True,
                text=True,
                timeout=45.0,
            )
            dur = time.monotonic() - start
            return CodingVerifyResponse(
                proposal_id=request.proposal_id,
                command=cmd,
                success=(res.returncode == 0),
                exit_code=res.returncode,
                stdout=res.stdout,
                stderr=res.stderr,
                duration_seconds=round(dur, 3),
            )
        except subprocess.TimeoutExpired:
            return JSONResponse(
                status_code=408,
                content={"code": "verification_timeout", "message": f"验证命令执行超时 (45s): {cmd}"},
            )
        except Exception as exc:
            return error_response(exc)

    @app.post("/api/v1/projects/{project_id}/coding/revert", response_model=CodingRevertResponse)
    async def revert_coding_patch_endpoint(project_id: str, request: CodingRevertRequest):
        store = _get_project_store()
        proj = store.get_project(project_id)
        if proj is None:
            return JSONResponse(status_code=404, content={"code": "project_not_found", "message": f"项目 {project_id} 不存在。"})

        proposal = _active_proposals.get(request.proposal_id)
        if proposal is None:
            return JSONResponse(
                status_code=400,
                content={"code": "proposal_not_found", "message": "未找到待回滚的补丁提案，可能已失效或被清理。"},
            )

        coding_agent = _get_coding_agent()
        success, msg = await asyncio.to_thread(coding_agent.revert_patch, proj, proposal)
        if not success:
            if "revision_conflict" in msg:
                return JSONResponse(
                    status_code=409,
                    content={"code": "version_conflict", "message": msg},
                )
            return JSONResponse(status_code=400, content={"code": "revert_failed", "message": msg})

        return CodingRevertResponse(
            proposal_id=proposal.proposal_id,
            success=True,
            message=msg,
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
            diff=diff.diff,
        )

    async def _resolve_topic_detail(topic: Topic) -> TopicDetailResponse:
        docs_list = []
        try:
            overview = await library_overview()
            items_by_id = {row.get("document_id") or row.get("paper_id"): row for row in overview.get("items", [])}
        except Exception:
            items_by_id = {}

        for doc_id in topic.document_ids:
            if doc_id in items_by_id:
                row = items_by_id[doc_id]
                docs_list.append({
                    "document_id": doc_id,
                    "title": row.get("title", doc_id),
                    "lifecycle": row.get("lifecycle", "active"),
                    "process_state": row.get("process_state", "unknown"),
                    "available": True,
                })
            else:
                docs_list.append({
                    "document_id": doc_id,
                    "title": doc_id,
                    "lifecycle": "missing",
                    "process_state": "unavailable",
                    "available": False,
                })

        notes_list = []
        try:
            notes_reg = load_notes_registry()
            notes_by_id = {item.get("note_id"): item for item in notes_reg if item.get("note_id")}
        except Exception:
            notes_by_id = {}

        for note_id in topic.note_ids:
            if note_id in notes_by_id:
                item = notes_by_id[note_id]
                notes_list.append({
                    "note_id": note_id,
                    "document_id": item.get("document_id", ""),
                    "title": item.get("title", note_id),
                    "version": item.get("version", 1),
                    "available": True,
                })
            else:
                notes_list.append({
                    "note_id": note_id,
                    "document_id": "",
                    "title": note_id,
                    "version": 0,
                    "available": False,
                })

        runs_list = []
        for run_id in topic.run_ids:
            try:
                run_obj = query_service.get_run(run_id) if query_service else None
                if run_obj:
                    runs_list.append({
                        "run_id": run_id,
                        "title": getattr(run_obj, "title", None) or getattr(run_obj, "query", run_id) or run_id,
                        "state": getattr(run_obj, "state", getattr(run_obj, "status", "unknown")),
                        "created_at": getattr(run_obj, "created_at", ""),
                        "available": True,
                    })
                else:
                    runs_list.append({
                        "run_id": run_id,
                        "title": run_id,
                        "state": "missing",
                        "created_at": "",
                        "available": False,
                    })
            except Exception:
                runs_list.append({
                    "run_id": run_id,
                    "title": run_id,
                    "state": "missing",
                    "created_at": "",
                    "available": False,
                })

        projects_list = []
        p_store = _get_project_store()
        for project_id in topic.project_ids:
            proj = p_store.get_project(project_id)
            if proj:
                projects_list.append({
                    "project_id": project_id,
                    "name": proj.name,
                    "root_path": proj.root_path,
                    "description": proj.description,
                    "available": True,
                })
            else:
                projects_list.append({
                    "project_id": project_id,
                    "name": project_id,
                    "root_path": "",
                    "description": "",
                    "available": False,
                })

        convs_list = []
        for conv_id in topic.conversation_ids:
            try:
                if chat_service:
                    rec = chat_service.conversation_record(conv_id)
                    convs_list.append({
                        "conversation_id": conv_id,
                        "title": rec.get("title", conv_id),
                        "message_count": rec.get("message_count", 0),
                        "available": True,
                    })
                else:
                    convs_list.append({
                        "conversation_id": conv_id,
                        "title": conv_id,
                        "message_count": 0,
                        "available": False,
                    })
            except Exception:
                convs_list.append({
                    "conversation_id": conv_id,
                    "title": conv_id,
                    "message_count": 0,
                    "available": False,
                })

        return TopicDetailResponse(
            topic_id=topic.topic_id,
            name=topic.name,
            objective=topic.objective,
            description=topic.description,
            tags=tuple(topic.tags),
            document_ids=tuple(topic.document_ids),
            note_ids=tuple(topic.note_ids),
            run_ids=tuple(topic.run_ids),
            project_ids=tuple(topic.project_ids),
            conversation_ids=tuple(topic.conversation_ids),
            recent_location=topic.recent_location,
            created_at=topic.created_at,
            updated_at=topic.updated_at,
            documents=tuple(docs_list),
            notes=tuple(notes_list),
            runs=tuple(runs_list),
            projects=tuple(projects_list),
            conversations=tuple(convs_list),
        )

    @app.get("/api/v1/topics", response_model=TopicListResponse)
    async def list_topics_endpoint():
        t_store = _get_topic_store()
        topics = t_store.list_topics()
        summaries = [
            TopicSummaryResponse(
                topic_id=t.topic_id,
                name=t.name,
                objective=t.objective,
                description=t.description,
                tags=tuple(t.tags),
                document_ids=tuple(t.document_ids),
                note_ids=tuple(t.note_ids),
                run_ids=tuple(t.run_ids),
                project_ids=tuple(t.project_ids),
                conversation_ids=tuple(t.conversation_ids),
                recent_location=t.recent_location,
                created_at=t.created_at,
                updated_at=t.updated_at,
            )
            for t in topics
        ]
        return TopicListResponse(items=tuple(summaries), total=len(summaries))

    @app.post("/api/v1/topics", response_model=TopicDetailResponse)
    async def create_topic_endpoint(request: TopicCreateRequest):
        t_store = _get_topic_store()
        try:
            topic = t_store.create_topic(
                name=request.name,
                objective=request.objective,
                description=request.description,
                tags=request.tags,
                document_ids=request.document_ids,
                note_ids=request.note_ids,
                run_ids=request.run_ids,
                project_ids=request.project_ids,
                conversation_ids=request.conversation_ids,
            )
            return await _resolve_topic_detail(topic)
        except ValueError as ve:
            return JSONResponse(status_code=400, content={"code": "invalid_topic_request", "message": str(ve)})
        except Exception as exc:
            return error_response(exc)

    @app.get("/api/v1/topics/{topic_id}", response_model=TopicDetailResponse)
    async def get_topic_endpoint(topic_id: str):
        t_store = _get_topic_store()
        topic = t_store.get_topic(topic_id)
        if topic is None:
            return JSONResponse(status_code=404, content={"code": "topic_not_found", "message": f"研究专题 {topic_id} 不存在。"})
        return await _resolve_topic_detail(topic)

    @app.patch("/api/v1/topics/{topic_id}", response_model=TopicSummaryResponse)
    async def update_topic_endpoint(topic_id: str, request: TopicUpdateRequest):
        t_store = _get_topic_store()
        updated = t_store.update_topic(
            topic_id=topic_id,
            name=request.name,
            objective=request.objective,
            description=request.description,
            tags=request.tags,
        )
        if updated is None:
            return JSONResponse(status_code=404, content={"code": "topic_not_found", "message": f"研究专题 {topic_id} 不存在。"})
        return TopicSummaryResponse(
            topic_id=updated.topic_id,
            name=updated.name,
            objective=updated.objective,
            description=updated.description,
            tags=tuple(updated.tags),
            document_ids=tuple(updated.document_ids),
            note_ids=tuple(updated.note_ids),
            run_ids=tuple(updated.run_ids),
            project_ids=tuple(updated.project_ids),
            conversation_ids=tuple(updated.conversation_ids),
            recent_location=updated.recent_location,
            created_at=updated.created_at,
            updated_at=updated.updated_at,
        )

    @app.delete("/api/v1/topics/{topic_id}")
    async def delete_topic_endpoint(topic_id: str):
        t_store = _get_topic_store()
        deleted = t_store.delete_topic(topic_id)
        if not deleted:
            return JSONResponse(status_code=404, content={"code": "topic_not_found", "message": f"研究专题 {topic_id} 不存在。"})
        return {"deleted": True, "topic_id": topic_id, "message": "专题已解除，底层资料与成果完好保留。"}

    @app.post("/api/v1/topics/{topic_id}/link", response_model=TopicDetailResponse)
    async def link_topic_object_endpoint(topic_id: str, request: TopicLinkRequest):
        t_store = _get_topic_store()
        try:
            if request.action == "link":
                topic = t_store.link_object(topic_id, request.object_type, request.object_id)
            else:
                topic = t_store.unlink_object(topic_id, request.object_type, request.object_id)
            if topic is None:
                return JSONResponse(status_code=404, content={"code": "topic_not_found", "message": f"研究专题 {topic_id} 不存在。"})
            return await _resolve_topic_detail(topic)
        except ValueError as ve:
            return JSONResponse(status_code=400, content={"code": "invalid_link_request", "message": str(ve)})
        except Exception as exc:
            return error_response(exc)

    @app.post("/api/v1/topics/{topic_id}/location", response_model=TopicSummaryResponse)
    async def update_topic_location_endpoint(topic_id: str, request: TopicLocationRequest):
        t_store = _get_topic_store()
        topic = t_store.update_recent_location(
            topic_id,
            {"section": request.section, "object_id": request.object_id, "label": request.label},
        )
        if topic is None:
            return JSONResponse(status_code=404, content={"code": "topic_not_found", "message": f"研究专题 {topic_id} 不存在。"})
        return TopicSummaryResponse(
            topic_id=topic.topic_id,
            name=topic.name,
            objective=topic.objective,
            description=topic.description,
            tags=tuple(topic.tags),
            document_ids=tuple(topic.document_ids),
            note_ids=tuple(topic.note_ids),
            run_ids=tuple(topic.run_ids),
            project_ids=tuple(topic.project_ids),
            conversation_ids=tuple(topic.conversation_ids),
            recent_location=topic.recent_location,
            created_at=topic.created_at,
            updated_at=topic.updated_at,
        )

    @app.get("/api/v1/notes")
    async def list_notes_endpoint():
        notes = load_notes_registry()
        return {"items": notes, "total": len(notes)}

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


class LocalRuntimeStack:
    """C1 进程分离：API 与 Worker 共用的权威运行时栈（SQLite + 产物库 + 编排器）。"""

    def __init__(
        self,
        *,
        orchestrator: OrchestratorPort,
        repository: SQLiteRuntimeRepository,
        store: LocalArtifactStore,
        chat_service: ChatService | None,
        retrieval_pipeline: Any | None,
        provider_configured: bool,
        provider_effective: Any | None = None,
        search_service: Any | None = None,
    ) -> None:
        self.orchestrator = orchestrator
        self.repository = repository
        self.store = store
        self.chat_service = chat_service
        self.retrieval_pipeline = retrieval_pipeline
        self.provider_configured = provider_configured
        # A5：启动时装配的 Provider 配置快照（进程生效值），供 /api/v1/config 展示。
        self.provider_effective = provider_effective
        # A1：SQLite FTS5 全局搜索服务（API 路由与写入点钩子共用）。
        self.search_service = search_service


def build_research_runtimes(
    *,
    database: Path = Path("var") / "db" / "conflux-weave.sqlite3",
    artifact_root: Path = Path("var") / "artifacts" / "sha256",
    workspace_root: Path = Path("var") / "workspace",
    dotenv_path: Path | None = Path(".env"),
    corpus_manifest: Path = Path("var") / "acceptance" / "v0.3-s1" / "corpus-import-manifest.json",
    lancedb_root: Path = Path("var") / "acceptance" / "v0.3-s1" / "lancedb",
) -> LocalRuntimeStack:
    """构造 API/Worker 共用的执行平面（无 HTTP 依赖，可独立进程使用）。"""
    store = LocalArtifactStore(artifact_root)
    repository = SQLiteRuntimeRepository(database, store)
    search_service = GlobalSearchService(database)
    _inject_third_party_keys(dotenv_path)
    workspace = LocalWorkspaceAdapter(
        workspace_root,
        Path(__file__).with_name("system"),
        store,
    )
    retrieval_pipeline = None
    fixture_runtime = ResearchFixtureRuntime(repository, store, workspace)
    provider_effective = None
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
            task_kinds=("verified_paper_research", "managed_verified_research", "deep_research"),
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
                dim = image_index.vector_dimensions() or 1024
                logger.warning(
                    "[MULTIMODAL DEGRADATION] Neither image_embedding_model nor "
                    "CONFLUX_WEAVE_IMAGE_EMBEDDING_MODEL is configured. Multimodal retrieval is falling back "
                    "to DeterministicImageEmbeddingAdapter (offline hash pseudo-features). "
                    "Visual semantic search is NOT grounded in real vision embeddings!"
                )
                image_embedding = DeterministicImageEmbeddingAdapter(store, dimensions=dim)

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
                    search_indexer=search_service,
                ),
                deep_research_enabled=deep_enabled,
            )
        except Exception as exc:
            research_runtime = UnavailableTaskRuntime(
                repository,
                executor_id="durable_verified_research@v1",
                task_kinds=("verified_paper_research", "managed_verified_research", "deep_research"),
                message=f"Research corpus or LanceDB is unavailable: {exc}",
            )
        chat_service = ChatService(
            OpenAICompatibleChatAdapter(store, config),
            database,
            retrieval=retrieval_pipeline,
            artifact_store=store,
        )
        chat_service.on_message_persisted = lambda message: search_service.index_chat_message(
            message_id=message.message_id,
            conversation_id=message.conversation_id,
            content=message.content,
            created_at=message.created_at,
            run_id=message.run_id,
        )
        provider_effective = config
    orchestrator = CompositeOrchestrator(
        repository,
        (fixture_runtime, paper_runtime, research_runtime),
    )
    return LocalRuntimeStack(
        orchestrator=orchestrator,
        repository=repository,
        store=store,
        chat_service=chat_service,
        retrieval_pipeline=retrieval_pipeline,
        provider_configured=provider_configured,
        provider_effective=provider_effective,
        search_service=search_service,
    )


def build_local_app(
    *,
    database: Path = Path("var") / "db" / "conflux-weave.sqlite3",
    artifact_root: Path = Path("var") / "artifacts" / "sha256",
    workspace_root: Path = Path("var") / "workspace",
    dotenv_path: Path | None = Path(".env"),
    corpus_manifest: Path = Path("var") / "acceptance" / "v0.3-s1" / "corpus-import-manifest.json",
    lancedb_root: Path = Path("var") / "acceptance" / "v0.3-s1" / "lancedb",
    enable_worker: bool = True,
) -> FastAPI:
    """Construct the production-shaped local app; no external call occurs here."""
    stack = build_research_runtimes(
        database=database,
        artifact_root=artifact_root,
        workspace_root=workspace_root,
        dotenv_path=dotenv_path,
        corpus_manifest=corpus_manifest,
        lancedb_root=lancedb_root,
    )
    return create_app(
        stack.repository,
        stack.orchestrator,
        provider_configured=stack.provider_configured,
        dotenv_path=dotenv_path,
        config_paths={
            "database": str(database),
            "artifact_root": str(artifact_root),
            "workspace_root": str(workspace_root),
            "corpus_manifest": str(corpus_manifest),
            "lancedb_root": str(lancedb_root),
            "dotenv": str(dotenv_path) if dotenv_path else "",
        },
        chat_service=stack.chat_service,
        retrieval_pipeline=stack.retrieval_pipeline,
        enable_worker=enable_worker,
        provider_effective=stack.provider_effective,
        search_service=stack.search_service,
    )


__all__ = ["WorkerLoop", "LocalRuntimeStack", "build_local_app", "build_research_runtimes", "create_app"]
