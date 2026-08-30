"""Stable W5 API models and read-only Workbench queries."""

from __future__ import annotations

from base64 import urlsafe_b64decode, urlsafe_b64encode
from binascii import Error as Base64Error
from enum import StrEnum
import json
import os
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from conflux_weave.core import RunStatus, StepStatus
from conflux_weave.evidence import ArtifactRef
from conflux_weave.runtime.artifacts import ArtifactIntegrityError
from conflux_weave.runtime.durable_paper_shared import RANK_CHECKPOINT
from conflux_weave.runtime.durable_research import DURABLE_RESEARCH_EVIDENCE_SCHEMA
from conflux_weave.runtime.sqlite import SQLiteRuntimeRepository
from conflux_weave.runtime.sqlite_contracts import (
    IdempotencyConflict,
    PersistenceInvariantError,
    RecordNotFound,
    RecoveryDecisionRequired,
    RunCursor,
    RunEventRecord,
    RunOverviewRecord,
)


class _ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class UserRunState(StrEnum):
    PENDING = "pending"
    WORKING = "working"
    NEEDS_ATTENTION = "needs_attention"
    CANCELLING = "cancelling"
    COMPLETE = "complete"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class ResearchTaskRequest(_ApiModel):
    query: str = Field(min_length=1, max_length=4_000)
    topics: tuple[str, ...] = Field(default=(), max_length=12)
    max_results: int = Field(default=15, ge=1, le=50)

    @field_validator("query")
    @classmethod
    def require_nonblank_query(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("query must not be blank")
        return value.strip()

    @field_validator("topics")
    @classmethod
    def normalize_topics(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(" ".join(value.split()) for value in values)
        if any(not value or len(value) > 100 for value in normalized):
            raise ValueError("topics must contain non-empty values up to 100 characters")
        if len({value.casefold() for value in normalized}) != len(normalized):
            raise ValueError("topics must not contain duplicates")
        return normalized


class FixtureResearchTaskRequest(_ApiModel):
    objective: str = Field(min_length=1, max_length=4_000)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=200)

    @field_validator("objective")
    @classmethod
    def require_nonblank_objective(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("objective must not be blank")
        return value.strip()


class ChatMessageRequest(_ApiModel):
    question: str = Field(min_length=1, max_length=8_000)
    conversation_id: str | None = Field(default=None, max_length=64)
    mode: Literal["direct", "rag"] = "direct"


class ChatCitationRecord(_ApiModel):
    index: int
    chunk_id: str
    source_snapshot_id: str
    locator: dict[str, Any]


class ChatMessageRecord(_ApiModel):
    message_id: str
    conversation_id: str
    role: str
    mode: str
    content: str
    created_at: str


class ChatAnswerResponse(ChatMessageRecord):
    provider_response_id: str
    citations: tuple[ChatCitationRecord, ...] = ()


class ChatHistoryResponse(_ApiModel):
    items: tuple[ChatMessageRecord, ...] = ()


class VerifiedResearchTaskRequest(_ApiModel):
    objective: str = Field(min_length=1, max_length=4_000)
    mode: Literal["single", "managed"] = "single"
    max_subquestions: int = Field(default=4, ge=2, le=4)

    @field_validator("objective")
    @classmethod
    def require_nonblank_objective(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("objective must not be blank")
        return value.strip()


class FollowUpResearchTaskRequest(_ApiModel):
    question: str = Field(min_length=1, max_length=2_000)

    @field_validator("question")
    @classmethod
    def require_nonblank_question(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("question must not be blank")
        return value.strip()


class ResearchTaskAcceptedResponse(_ApiModel):
    task_id: str
    run_id: str
    created: bool
    state: UserRunState


class ProgressResponse(_ApiModel):
    completed_steps: int = Field(ge=0)
    total_steps: int = Field(ge=0)
    active: bool


class BudgetResponse(_ApiModel):
    state: str
    input_tokens_used: int = Field(ge=0)
    output_tokens_used: int = Field(ge=0)
    tool_calls_used: int = Field(ge=0)
    retrieval_rounds_used: int = Field(ge=0)
    input_tokens_limit: int = Field(ge=0)
    output_tokens_limit: int = Field(ge=0)
    tool_calls_limit: int = Field(ge=0)
    retrieval_rounds_limit: int = Field(ge=0)
    estimated_cost_limit: str
    cost_enforcement: str


class ResearchRunContextResponse(_ApiModel):
    mode: Literal["discovery", "single", "managed", "fixture"]
    corpus_scope: str
    max_subquestions: int | None = Field(default=None, ge=2, le=4)
    parent_run_id: str | None = None
    follow_up_question: str | None = None


class DeliveryResponse(_ApiModel):
    disposition: Literal["complete", "partial", "no_answer"]
    artifact_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    limitations: tuple[str, ...]
    unmet_criteria: tuple[str, ...]
    recovery_actions: tuple[str, ...]


class ApiErrorResponse(_ApiModel):
    code: str
    message: str
    recovery_action: str | None = None
    retryable: bool = False


class RunSummaryResponse(_ApiModel):
    run_id: str
    task_id: str
    task_family: str
    query: str
    state: UserRunState
    status_message: str
    is_terminal: bool
    created_at: str
    updated_at: str


class RunPageResponse(_ApiModel):
    items: tuple[RunSummaryResponse, ...]
    next_cursor: str | None


class RunDetailResponse(RunSummaryResponse):
    progress: ProgressResponse
    budget: BudgetResponse
    delivery: DeliveryResponse | None = None
    error: ApiErrorResponse | None = None
    research_context: ResearchRunContextResponse


class RunEventKind(StrEnum):
    PROGRESS = "progress"
    STATUS = "status"
    RECOVERY = "recovery"


class RunEventResponse(_ApiModel):
    cursor: int = Field(ge=1)
    run_id: str
    kind: RunEventKind
    state: UserRunState
    message: str
    created_at: str


class RunEventPageResponse(_ApiModel):
    items: tuple[RunEventResponse, ...]
    next_after: int = Field(ge=0)


class ArtifactMetadataResponse(_ApiModel):
    artifact_id: str
    media_type: str
    content_hash: str
    schema_version: str


class ArtifactContentResponse(_ApiModel):
    artifact: ArtifactMetadataResponse
    content: str


class EvidenceResponse(_ApiModel):
    evidence_id: str
    source_snapshot_id: str
    locator: dict[str, Any]
    quote: str
    extraction_method: str


class ReadinessCheckResponse(_ApiModel):
    name: Literal["database", "artifact_store", "provider"]
    status: Literal["ready", "not_ready"]
    message: str
    recovery_action: str | None = None


class ReadinessResponse(_ApiModel):
    status: Literal["ready", "not_ready"]
    checks: tuple[ReadinessCheckResponse, ...]


class ProviderConfigResponse(_ApiModel):
    base_url: str
    model: str
    embedding_model: str
    reranker_model: str
    api_key_configured: bool
    api_key_hint: str | None = None


class ProviderConfigUpdateRequest(_ApiModel):
    base_url: str = Field(min_length=1, max_length=2_000)
    api_key: str | None = Field(default=None, min_length=1, max_length=1_000)
    model: str = Field(min_length=1, max_length=200)
    embedding_model: str | None = Field(default=None, max_length=200)
    reranker_model: str | None = Field(default=None, max_length=200)

    @field_validator("base_url")
    @classmethod
    def require_https_base_url(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("base_url must not be blank")
        return value.strip()

    @field_validator("model")
    @classmethod
    def require_nonblank_model(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("model must not be blank")
        return value.strip()


class ProviderConfigUpdateResponse(_ApiModel):
    provider: ProviderConfigResponse
    requires_restart: bool
    message: str


class ProviderConfigTestRequest(_ApiModel):
    base_url: str | None = Field(default=None, min_length=1, max_length=2_000)
    api_key: str | None = Field(default=None, min_length=1, max_length=1_000)
    model: str | None = Field(default=None, min_length=1, max_length=200)


class ProviderConfigTestResponse(_ApiModel):
    ok: bool
    message: str
    latency_ms: int | None = None


class WorkbenchConfigResponse(_ApiModel):
    provider: ProviderConfigResponse
    provider_active: bool
    paths: dict[str, str]


class _RunCursorPayload(_ApiModel):
    version: Literal[1] = 1
    created_at: str = Field(min_length=1)
    run_id: str = Field(min_length=1)


_STATE_MAP = {
    RunStatus.ACCEPTED: UserRunState.PENDING,
    RunStatus.QUEUED: UserRunState.PENDING,
    RunStatus.RUNNING: UserRunState.WORKING,
    RunStatus.WAITING_FOR_USER: UserRunState.NEEDS_ATTENTION,
    RunStatus.CANCELLING: UserRunState.CANCELLING,
    RunStatus.SUCCEEDED: UserRunState.COMPLETE,
    RunStatus.PARTIAL: UserRunState.PARTIAL,
    RunStatus.FAILED: UserRunState.FAILED,
    RunStatus.CANCELLED: UserRunState.CANCELLED,
    RunStatus.EXPIRED: UserRunState.EXPIRED,
}

_STATE_MESSAGES = {
    UserRunState.PENDING: "任务已保存，正在等待处理。",
    UserRunState.WORKING: "正在检索来源并整理证据。",
    UserRunState.NEEDS_ATTENTION: "任务需要你的恢复决定。",
    UserRunState.CANCELLING: "正在停止任务，不会启动新的外部调用。",
    UserRunState.COMPLETE: "任务已完成。",
    UserRunState.PARTIAL: "已返回可用结果，仍有明确限制。",
    UserRunState.FAILED: "任务未能完成，请查看恢复建议。",
    UserRunState.CANCELLED: "任务已取消。",
    UserRunState.EXPIRED: "任务已过期，请创建新的任务。",
}

_FIXTURE_STATE_MESSAGES = {
    UserRunState.PENDING: "离线验证已保存，正在等待处理。",
    UserRunState.WORKING: "正在执行离线 Harness 验证。",
    UserRunState.PARTIAL: "离线 Harness 闭环已完成，真实研究能力尚未验证。",
    UserRunState.FAILED: "离线 Harness 验证失败，请查看恢复建议。",
}

_VERIFIED_STATE_MESSAGES = {
    UserRunState.PENDING: "研究任务已保存，正在等待处理。",
    UserRunState.WORKING: "正在检索、核验并整理引用闭合的研究结果。",
    UserRunState.NEEDS_ATTENTION: "付费研究批次结果未知，需要你的恢复决定。",
    UserRunState.CANCELLING: "正在停止研究，不会启动新的模型调用。",
    UserRunState.COMPLETE: "引用核验后的研究结果已完成。",
    UserRunState.PARTIAL: "已返回经过核验的部分研究结果。",
    UserRunState.FAILED: "研究任务未完成，请查看错误与恢复建议。",
    UserRunState.CANCELLED: "研究任务已取消。",
    UserRunState.EXPIRED: "研究任务已过期，请创建新的 Run。",
}

_EVENT_MESSAGES: dict[str, tuple[RunEventKind, str]] = {
    "step_claimed": (RunEventKind.PROGRESS, "已开始处理下一阶段。"),
    "step_succeeded": (RunEventKind.PROGRESS, "已完成一个处理阶段。"),
    "step_failed": (RunEventKind.STATUS, "处理阶段失败，请查看恢复建议。"),
    "attempt_fenced": (RunEventKind.RECOVERY, "检测到中断，旧执行已被隔离。"),
    "cancel_requested": (RunEventKind.STATUS, "已收到取消请求。"),
    "attempt_cancelled": (RunEventKind.STATUS, "当前执行已取消。"),
    "recovery_decision": (RunEventKind.RECOVERY, "已记录你的恢复决定。"),
    "agent_task_assigned": (RunEventKind.PROGRESS, "研究任务已分配给 Agent。"),
    "agent_status_update": (RunEventKind.PROGRESS, "Agent 已提交新的状态或工具结果。"),
    "agent_result_submitted": (RunEventKind.PROGRESS, "Agent 已提交结构化结果。"),
    "agent_needs_input": (RunEventKind.STATUS, "Agent 需要补充输入。"),
    "agent_failure_reported": (RunEventKind.STATUS, "Agent 已报告执行失败。"),
    "agent_terminated": (RunEventKind.STATUS, "Agent 已终止。"),
}


def encode_run_cursor(cursor: RunCursor) -> str:
    payload = _RunCursorPayload(
        created_at=cursor.created_at,
        run_id=cursor.run_id,
    ).model_dump_json()
    return urlsafe_b64encode(payload.encode("utf-8")).rstrip(b"=").decode("ascii")


def decode_run_cursor(value: str) -> RunCursor:
    if not value or len(value) > 1_024:
        raise ValueError("Run cursor is invalid")
    try:
        padding = "=" * (-len(value) % 4)
        decoded = urlsafe_b64decode((value + padding).encode("ascii"))
        payload = _RunCursorPayload.model_validate_json(decoded)
    except (Base64Error, UnicodeEncodeError, ValidationError, ValueError) as exc:
        raise ValueError("Run cursor is invalid") from exc
    return RunCursor(created_at=payload.created_at, run_id=payload.run_id)


def map_run_state(status: RunStatus) -> UserRunState:
    return _STATE_MAP[status]


def map_exception(exc: Exception) -> tuple[int, ApiErrorResponse]:
    if isinstance(exc, RecordNotFound):
        return 404, ApiErrorResponse(
            code="not_found",
            message="请求的记录不存在。",
        )
    if isinstance(exc, IdempotencyConflict):
        return 409, ApiErrorResponse(
            code="idempotency_conflict",
            message="该重复请求标识已用于不同的任务输入。",
            recovery_action="使用新的重复请求标识后重新提交。",
        )
    if isinstance(exc, RecoveryDecisionRequired):
        return 409, ApiErrorResponse(
            code="recovery_decision_required",
            message="外部调用结果未知，需要明确选择重试或失败。",
            recovery_action="查看运行详情并选择一个允许的恢复动作。",
        )
    if isinstance(exc, PersistenceInvariantError):
        return 409, ApiErrorResponse(
            code="invalid_run_state",
            message="当前运行状态不允许此操作。",
            recovery_action="刷新运行状态后按页面提供的动作继续。",
        )
    if isinstance(exc, ArtifactIntegrityError):
        return 503, ApiErrorResponse(
            code="artifact_unavailable",
            message="交付文件无法通过完整性检查。",
            recovery_action="保留当前运行并查看技术详情。",
        )
    if isinstance(exc, ValueError):
        return 422, ApiErrorResponse(
            code="invalid_request",
            message="请求参数无效。",
        )
    return 500, ApiErrorResponse(
        code="internal_error",
        message="服务暂时无法完成请求。",
        recovery_action="保留运行标识并查看本地日志。",
    )


class WorkbenchQueryService:
    """Compile API views from authoritative persisted state without writes."""

    def __init__(self, repository: SQLiteRuntimeRepository) -> None:
        self.repository = repository

    def list_runs(self, *, cursor: str | None = None, limit: int = 20) -> RunPageResponse:
        page = self.repository.list_runs(
            cursor=decode_run_cursor(cursor) if cursor is not None else None,
            limit=limit,
        )
        return RunPageResponse(
            items=tuple(_summary(item) for item in page.items),
            next_cursor=(
                encode_run_cursor(page.next_cursor) if page.next_cursor is not None else None
            ),
        )

    def get_run(self, run_id: str) -> RunDetailResponse:
        run = self.repository.get_run(run_id)
        task = self.repository.get_task_for_run(run_id)
        overview = RunOverviewRecord(run=run, task_kind=task.kind, task_input=task.input)
        steps = self.repository.get_steps(run_id)
        budget = self.repository.get_budget_status(run_id)
        try:
            delivery_record = self.repository.get_delivery(run_id)
        except RecordNotFound:
            delivery = None
        else:
            delivery = DeliveryResponse(
                disposition=delivery_record.disposition.value,
                artifact_ids=delivery_record.artifact_refs,
                evidence_ids=delivery_record.evidence_refs,
                limitations=delivery_record.limitations,
                unmet_criteria=delivery_record.unmet_criteria,
                recovery_actions=delivery_record.recovery_actions,
            )
        errors = self.repository.get_errors(run_id)
        error = None
        if errors:
            latest = errors[-1].record
            error = ApiErrorResponse(
                code=latest.code,
                message=latest.user_message,
                recovery_action=latest.recovery_action or None,
                retryable=latest.retryable,
            )
        complete_statuses = {
            StepStatus.SUCCEEDED,
            StepStatus.FAILED,
            StepStatus.CANCELLED,
            StepStatus.SKIPPED,
        }
        summary = _summary(overview)
        return RunDetailResponse(
            **summary.model_dump(),
            progress=ProgressResponse(
                completed_steps=sum(step.status in complete_statuses for step in steps),
                total_steps=len(steps),
                active=any(step.status is StepStatus.RUNNING for step in steps),
            ),
            budget=BudgetResponse(
                state=budget.state,
                input_tokens_used=budget.actual.input_tokens,
                output_tokens_used=budget.actual.output_tokens,
                tool_calls_used=budget.actual.tool_calls,
                retrieval_rounds_used=budget.actual.retrieval_rounds,
                input_tokens_limit=budget.limit.input_tokens,
                output_tokens_limit=budget.limit.output_tokens,
                tool_calls_limit=budget.limit.tool_calls,
                retrieval_rounds_limit=budget.limit.retrieval_rounds,
                estimated_cost_limit=budget.estimated_cost_limit,
                cost_enforcement=budget.cost_enforcement,
            ),
            delivery=delivery,
            error=error,
            research_context=_research_context(task.kind, task.input),
        )

    def get_events(
        self,
        run_id: str,
        *,
        after: int = 0,
        limit: int = 100,
    ) -> RunEventPageResponse:
        state = map_run_state(self.repository.get_run(run_id).status)
        records = self.repository.get_run_events(
            run_id,
            after_event_id=after,
            limit=limit,
        )
        items = tuple(_event(record, state) for record in records)
        return RunEventPageResponse(
            items=items,
            next_after=items[-1].cursor if items else after,
        )

    def get_delivery_artifacts(
        self, run_id: str
    ) -> tuple[ArtifactMetadataResponse, ...]:
        return tuple(
            _artifact_metadata(artifact)
            for artifact in self.repository.get_delivery_artifacts(run_id)
        )

    def read_delivery_artifact(
        self, run_id: str, artifact_id: str
    ) -> tuple[ArtifactMetadataResponse, bytes]:
        artifact = next(
            (
                item
                for item in self.repository.get_delivery_artifacts(run_id)
                if item.artifact_id == artifact_id
            ),
            None,
        )
        if artifact is None:
            raise RecordNotFound("Delivery Artifact not found")
        return _artifact_metadata(artifact), self.repository.artifact_store.read_bytes(
            artifact
        )

    def get_evidence(self, run_id: str, evidence_id: str) -> EvidenceResponse:
        delivery = self.repository.get_delivery(run_id)
        if evidence_id not in delivery.evidence_refs:
            raise RecordNotFound("Evidence not found")
        matches: list[EvidenceResponse] = []
        for step in self.repository.get_steps(run_id):
            if step.kind not in {"rank_candidates", "merge_and_rank", "publish_delivery"}:
                continue
            for artifact in self.repository.get_step_artifacts(step.step_id):
                if artifact.schema_version not in {
                    RANK_CHECKPOINT,
                    DURABLE_RESEARCH_EVIDENCE_SCHEMA,
                }:
                    continue
                matches.extend(self._evidence_matches(artifact, evidence_id))
        if not matches:
            raise PersistenceInvariantError(
                "Delivery Evidence is missing from its registered rank checkpoint"
            )
        first = matches[0]
        if any(item != first for item in matches[1:]):
            raise PersistenceInvariantError(
                "Delivery Evidence has conflicting registered definitions"
            )
        return first

    def readiness(self, *, provider_configured: bool) -> ReadinessResponse:
        try:
            database_ready = bool(self.repository.migration_records())
        except Exception:
            database_ready = False
        artifact_root = self.repository.artifact_store.root
        existing_parent = artifact_root
        while not existing_parent.exists() and existing_parent != existing_parent.parent:
            existing_parent = existing_parent.parent
        artifact_ready = existing_parent.is_dir() and os.access(existing_parent, os.W_OK)
        checks = (
            _readiness_check(
                "database",
                database_ready,
                "运行数据库已就绪。",
                "运行数据库不可用。",
                "检查数据目录权限和 schema migration。",
            ),
            _readiness_check(
                "artifact_store",
                artifact_ready,
                "交付文件目录已就绪。",
                "交付文件目录不可写。",
                "检查数据目录权限。",
            ),
            _readiness_check(
                "provider",
                provider_configured,
                "模型 Provider 配置已提供。",
                "模型 Provider 配置不完整。",
                "设置 Provider URL、API Key 和模型名称后重试。",
            ),
        )
        return ReadinessResponse(
            status="ready" if all(item.status == "ready" for item in checks) else "not_ready",
            checks=checks,
        )

    def _evidence_matches(
        self, artifact: ArtifactRef, evidence_id: str
    ) -> list[EvidenceResponse]:
        try:
            payload = json.loads(
                self.repository.artifact_store.read_bytes(artifact).decode("utf-8")
            )
            if not isinstance(payload, dict):
                raise ValueError("rank checkpoint must be an object")
            if payload.get("schema_version") not in {
                RANK_CHECKPOINT,
                DURABLE_RESEARCH_EVIDENCE_SCHEMA,
            }:
                raise ValueError("Evidence checkpoint schema mismatch")
            evidence = payload.get("evidence")
            if not isinstance(evidence, list):
                raise ValueError("Evidence checkpoint evidence must be a list")
            return [
                EvidenceResponse.model_validate(item)
                for item in evidence
                if isinstance(item, dict) and item.get("evidence_id") == evidence_id
            ]
        except (
            ArtifactIntegrityError,
            UnicodeDecodeError,
            json.JSONDecodeError,
            ValidationError,
            ValueError,
        ) as exc:
            raise PersistenceInvariantError(
                "registered Evidence checkpoint is invalid"
            ) from exc


def _summary(record: RunOverviewRecord) -> RunSummaryResponse:
    state = map_run_state(record.run.status)
    query = record.task_input.get("query", record.task_input.get("objective"))
    if record.task_kind == "research_fixture":
        status_messages = _FIXTURE_STATE_MESSAGES
    elif record.task_kind in {
        "verified_paper_research",
        "managed_verified_research",
    }:
        status_messages = _VERIFIED_STATE_MESSAGES
    else:
        status_messages = _STATE_MESSAGES
    return RunSummaryResponse(
        run_id=record.run.run_id,
        task_id=record.run.task_id,
        task_family=record.task_kind,
        query=query if isinstance(query, str) else "",
        state=state,
        status_message=status_messages.get(state, _STATE_MESSAGES[state]),
        is_terminal=record.run.status.is_terminal,
        created_at=record.run.created_at,
        updated_at=record.run.updated_at,
    )


def _research_context(task_kind: str, task_input: dict[str, Any]) -> ResearchRunContextResponse:
    if task_kind == "verified_paper_research":
        mode = "single"
        corpus_scope = "已发布本地论文语料 / LanceDB Hybrid + Rerank"
    elif task_kind == "managed_verified_research":
        mode = "managed"
        corpus_scope = "已发布本地论文语料 / Manager 分解 / LanceDB Hybrid + Rerank"
    elif task_kind == "research_fixture":
        mode = "fixture"
        corpus_scope = "离线 Harness fixture"
    else:
        mode = "discovery"
        corpus_scope = "外部论文发现"
    max_subquestions = task_input.get("max_subquestions")
    return ResearchRunContextResponse(
        mode=mode,
        corpus_scope=corpus_scope,
        max_subquestions=(max_subquestions if isinstance(max_subquestions, int) else None),
        parent_run_id=(task_input.get("parent_run_id") if isinstance(task_input.get("parent_run_id"), str) else None),
        follow_up_question=(task_input.get("follow_up_question") if isinstance(task_input.get("follow_up_question"), str) else None),
    )


def _event(record: RunEventRecord, state: UserRunState) -> RunEventResponse:
    kind, message = _EVENT_MESSAGES.get(
        record.event_type,
        (RunEventKind.PROGRESS, "运行状态已更新。"),
    )
    return RunEventResponse(
        cursor=record.event_id,
        run_id=record.run_id,
        kind=kind,
        state=state,
        message=message,
        created_at=record.created_at,
    )


def _artifact_metadata(artifact: ArtifactRef) -> ArtifactMetadataResponse:
    return ArtifactMetadataResponse(
        artifact_id=artifact.artifact_id,
        media_type=artifact.media_type,
        content_hash=artifact.content_hash,
        schema_version=artifact.schema_version,
    )


def _readiness_check(
    name: Literal["database", "artifact_store", "provider"],
    ready: bool,
    ready_message: str,
    failure_message: str,
    recovery_action: str,
) -> ReadinessCheckResponse:
    return ReadinessCheckResponse(
        name=name,
        status="ready" if ready else "not_ready",
        message=ready_message if ready else failure_message,
        recovery_action=None if ready else recovery_action,
    )


__all__ = [
    "ApiErrorResponse",
    "ArtifactContentResponse",
    "ArtifactMetadataResponse",
    "BudgetResponse",
    "DeliveryResponse",
    "EvidenceResponse",
    "FixtureResearchTaskRequest",
    "FollowUpResearchTaskRequest",
    "ProgressResponse",
    "ProviderConfigResponse",
    "ProviderConfigTestRequest",
    "ProviderConfigTestResponse",
    "ProviderConfigUpdateRequest",
    "ProviderConfigUpdateResponse",
    "ReadinessCheckResponse",
    "ReadinessResponse",
    "ResearchTaskAcceptedResponse",
    "ResearchRunContextResponse",
    "ResearchTaskRequest",
    "RunDetailResponse",
    "RunEventKind",
    "RunEventPageResponse",
    "RunEventResponse",
    "RunPageResponse",
    "RunSummaryResponse",
    "UserRunState",
    "WorkbenchConfigResponse",
    "WorkbenchQueryService",
    "VerifiedResearchTaskRequest",
    "ChatAnswerResponse",
    "ChatHistoryResponse",
    "ChatMessageRecord",
    "ChatMessageRequest",
    "decode_run_cursor",
    "encode_run_cursor",
    "map_exception",
    "map_run_state",
]
