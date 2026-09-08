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
from fastapi import FastAPI, Query, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

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
    app.state.repository = repository
    app.state.orchestrator = orchestrator
    app.state.worker = worker_loop

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
            if request.mode == "rag":
                mark_library_usage([str(item.get("source_snapshot_id", "")) for item in result.get("citations", ())])
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
            if segments_ref.startswith("artifact-sha256-"):
                try:
                    segment_path = repository.artifact_store.path_for_digest(segments_ref.removeprefix("artifact-sha256-"))
                    segment_payload = json.loads(segment_path.read_text(encoding="utf-8"))
                    segments = segment_payload.get("segments", [])
                    segment_count = len(segments) or segment_count
                    characters = sum(len(str(segment.get("text", ""))) for segment in segments)
                except (OSError, ValueError, json.JSONDecodeError):
                    pass
            item_status = row.get("status", "unknown")
            if item_status == "imported" and not row.get("_manifest_indexed"):
                item_status = "parsed"
            items.append({
                "title": row.get("title") or Path(relative).stem or relative,
                "relative_path": relative,
                "record_id": row.get("document_id") or row.get("paper_id") or identity,
                "paper_id": row.get("paper_id", ""),
                "document_id": row.get("document_id", ""),
                "segments_artifact_id": row.get("segments_artifact_id", ""),
                "source": row.get("source_snapshot_id") or row.get("document_id") or relative,
                "media_type": "PDF" if row.get("document_id") and (relative.lower().endswith(".pdf") or row.get("paper_id")) else "论文元数据" if row.get("paper_id") else "Markdown",
                "status": item_status,
                "segment_count": segment_count,
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
        return await asyncio.to_thread(
            retrieval_pipeline.add_documents,
            retrieval_documents(document),
            producer_step_id="step-library-incremental-index",
        )

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
            imported = LocalDocumentImporter(repository.artifact_store).import_path(temporary_path)
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

    @app.post("/api/v1/library/papers/search")
    async def search_library_papers(
        query: str = Query(..., min_length=1, max_length=400),
        max_results: int = Query(20, ge=1, le=50),
        sources: str = Query("openalex,arxiv"),
        year_from: int | None = Query(None, ge=1900, le=2100),
        year_to: int | None = Query(None, ge=1900, le=2100),
        oa_only: bool = False,
        sort: Literal["relevance", "newest", "impact"] = "relevance",
    ) -> dict[str, Any]:
        if year_from and year_to and year_from > year_to:
            return JSONResponse(status_code=400, content={"code": "invalid_year_range", "message": "起始年份不能晚于结束年份。"})

        def contains_cjk(value: str) -> bool:
            return bool(re.search(r"[\u3400-\u9fff]", value))

        queries = [query]
        openalex_query = query
        required_terms: tuple[str, ...] = ()
        required_concepts: tuple[tuple[str, ...], ...] = ()
        identifier_kind = "doi" if re.fullmatch(r"(?:https?://(?:dx\.)?doi\.org/|doi:\s*)?10\.\d{4,9}/\S+", query.strip(), re.IGNORECASE) else "arxiv" if re.fullmatch(r"(?:arxiv:\s*)?(?:\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Z]{2})?/\d{7})(?:v\d+)?", query.strip(), re.IGNORECASE) else None
        understanding: dict[str, Any] = {"status": "identifier" if identifier_kind else "direct", "queries": queries, "identifier_kind": identifier_kind}
        if contains_cjk(query) and identifier_kind is None:
            if chat_service is None or not getattr(chat_service, "_chat", None):
                return JSONResponse(status_code=503, content={"code": "paper_query_understanding_unavailable", "message": "中文论文主题需要模型服务进行查询理解，请先配置模型服务。"})
            prompt = (
                "将用户的中文论文主题转换为学术数据库检索意图。"
                "只返回 JSON 对象，格式必须是 {\"openalex_query\":\"...\",\"queries\":[\"...\"],\"required_concepts\":[[\"...\",\"...\"],[\"...\",\"...\"]]}。"
                "openalex_query 是一个精确的英文主题短语；queries 返回 1 到 3 个互补英文短语。"
                "required_concepts 返回恰好 2 个核心概念组，每组给出 2 到 4 个可替代的英文词或短语；相关论文应从每组至少命中一个表达；"
                "不要解释、不要中文、不要布尔语法；"
                "保留领域含义，优先使用学术论文常见术语。用户主题：" + query
            )
            try:
                completion = await asyncio.to_thread(
                    chat_service._chat.complete,
                    system_prompt="你是学术检索查询理解器，只输出符合要求的 JSON。",
                    user_prompt=prompt,
                    max_output_tokens=256,
                    temperature=0,
                    json_object=True,
                    enable_thinking=False,
                    producer_step_id="step-library-paper-query-understanding",
                )
                raw = json.loads(completion.content)
                candidate_queries = raw.get("queries") if isinstance(raw, dict) else None
                if not isinstance(candidate_queries, list):
                    raise ValueError("Provider 未返回 queries 数组")
                queries = [str(item).strip() for item in candidate_queries if isinstance(item, str) and item.strip()]
                queries = list(dict.fromkeys(queries))[:3]
                if not queries:
                    raise ValueError("Provider 返回了空查询")
                proposed_openalex = raw.get("openalex_query") if isinstance(raw, dict) else None
                openalex_query = str(proposed_openalex).strip() if isinstance(proposed_openalex, str) and proposed_openalex.strip() else queries[0]
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
                    fallback_terms = [term.lower() for term in re.findall(r"[a-zA-Z][a-zA-Z0-9-]{1,}", queries[0]) if term.lower() not in {"a", "an", "and", "for", "in", "of", "the", "to", "with", "based", "systems", "intelligent", "intelligence"}]
                    required_terms = tuple(dict.fromkeys(fallback_terms))[:2]
                understanding = {"status": "provider_translated", "queries": queries, "openalex_query": openalex_query, "required_concepts": [list(group) for group in required_concepts], "required_terms": list(required_terms), "identifier_kind": None}
            except Exception as exc:
                return JSONResponse(status_code=502, content={"code": "paper_query_understanding_failed", "message": f"中文主题查询理解失败：{exc}"})

        requested_sources = tuple(dict.fromkeys(item.strip().lower() for item in sources.split(",") if item.strip()))
        invalid_sources = set(requested_sources) - {"openalex", "arxiv"}
        if invalid_sources or not requested_sources:
            return JSONResponse(status_code=400, content={"code": "invalid_paper_sources", "message": "论文来源只支持 openalex 和 arxiv。"})

        stopwords = {"a", "an", "and", "for", "in", "of", "the", "to", "with", "based", "systems"}
        retrieval_queries = []
        if identifier_kind == "arxiv":
            retrieval_queries = ["id:" + re.sub(r"^arxiv:\s*", "", query.strip(), flags=re.IGNORECASE)]
        elif re.search(r"(?:^|\s)(?:all|ti|au|abs|cat|id):", query, re.IGNORECASE):
            retrieval_queries = [query]
        else:
            for phrase in queries:
                terms = [term.lower() for term in re.findall(r"[a-zA-Z][a-zA-Z0-9-]{1,}", phrase) if term.lower() not in stopwords]
                retrieval_queries.append(" AND ".join(f"all:{term}" for term in dict.fromkeys(terms)) if len(terms) >= 2 else phrase)
        retrieval_queries = list(dict.fromkeys(retrieval_queries))
        source_states = []
        records = []
        contact_email = third_party_setting("CONFLUX_WEAVE_CONTACT_EMAIL")
        source_max_results = min(max_results, 25)

        if "openalex" in requested_sources:
            try:
                result = await asyncio.to_thread(OpenAlexSearchAdapter(repository.artifact_store, contact_email=contact_email).search, openalex_query, max_results=source_max_results, year_from=year_from, year_to=year_to, oa_only=oa_only)
                records.extend(result.papers)
                source_states.append({"source": "openalex", "status": "success" if result.papers else "no_results", "query": result.query, "count": len(result.papers), "cache_hit": result.cache_hit})
            except Exception as exc:
                source_states.append({"source": "openalex", "status": "failed", "query": openalex_query, "count": 0, "code": getattr(exc, "code", "openalex_search_failed"), "message": str(exc), "retryable": bool(getattr(exc, "retryable", True)), "recovery_action": "仅重试 OpenAlex 来源。"})

        if "arxiv" in requested_sources:
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
        if not identifier_kind and not required_terms and len(query_terms) >= 2:
            # Direct English topics need a small precision guard too; without it
            # arXiv can satisfy a broad geographic token while dropping the
            # method/agent concept that defines the user's query.
            required_terms = tuple(dict.fromkeys(query_terms[:2]))
        merged = merge_and_rank(records, query_terms=query_terms, max_results=max(1, len(records)), sort=sort, required_terms=required_terms, required_concepts=required_concepts)
        papers = merged[:max_results]
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
            document = await asyncio.to_thread(LocalDocumentImporter(repository.artifact_store).import_path, temporary_path, producer_step_id="step-library-paper-parse")
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
                engine_model="", contact_email="", api_key_configured=False, api_key_hint=None,
            )
        return ProviderConfigResponse(
            base_url=view.base_url,
            model=view.model,
            embedding_model=view.embedding_model,
            reranker_model=view.reranker_model,
            engine_model=view.engine_model,
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
                contact_email=request.contact_email,
            )
            return ProviderConfigUpdateResponse(
                provider=ProviderConfigResponse(
                    base_url=view.base_url,
                    model=view.model,
                    embedding_model=view.embedding_model,
                    reranker_model=view.reranker_model,
                    engine_model=view.engine_model,
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
        retrieval_pipeline=retrieval_pipeline,
    )


__all__ = ["WorkerLoop", "build_local_app", "create_app"]
