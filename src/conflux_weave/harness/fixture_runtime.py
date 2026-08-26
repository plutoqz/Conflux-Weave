"""Deterministic end-to-end ResearchAgent fixture for the v0.3 Harness."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
from typing import Any, Callable
from uuid import uuid4

from conflux_weave.core import (
    BudgetLedger,
    DeliveryDisposition,
    DeliveryRecord,
    ErrorCategory,
    ErrorRecord,
    RunRecord,
    RunStatus,
    StepRecord,
    StepStatus,
    TaskSpec,
)
from conflux_weave.evidence import ArtifactRef
from conflux_weave.harness.contracts import (
    AgentProfile,
    AgentResult,
    AgentResultStatus,
    AgentTask,
    ContextBundle,
    MessageEnvelope,
    MessageType,
    TaskSubmission,
    ToolResult,
    ToolResultStatus,
    ToolSideEffect,
    ToolSpec,
    WorkspaceKind,
    WorkspaceRef,
    contract_to_dict,
)
from conflux_weave.harness.workspace import LocalWorkspaceAdapter, WorkspaceAccess
from conflux_weave.runtime.artifacts import LocalArtifactStore
from conflux_weave.runtime.sqlite import (
    BudgetAmount,
    RecoveryDecision,
    SideEffectClass,
    SQLiteRuntimeRepository,
    StepPolicy,
    SubmissionResult,
)
from conflux_weave.runtime.worker import SQLiteStepWorker


FIXTURE_TASK_KIND = "research_fixture"
FIXTURE_WORKFLOW_VERSION = "research-fixture-harness-v1"
FIXTURE_AGENT_ID = "research_fixture@v1"
FIXTURE_TOOL_ID = "fixture_lookup"
FIXTURE_REPORT_SCHEMA = "conflux-weave.research-fixture-report.v1"


@dataclass(frozen=True, slots=True)
class FixtureToolExecution:
    result: ToolResult
    artifacts: tuple[ArtifactRef, ...]


class FixtureToolGateway:
    def __init__(
        self,
        artifact_store: LocalArtifactStore,
        *,
        clock: Callable[[], str],
    ) -> None:
        self.artifact_store = artifact_store
        self.clock = clock
        self.spec = ToolSpec(
            tool_id=FIXTURE_TOOL_ID,
            version="v1",
            description="Return a deterministic offline research fixture",
            input_schema={
                "type": "object",
                "required": ["objective"],
                "properties": {"objective": {"type": "string"}},
            },
            output_schema={
                "type": "object",
                "required": ["summary", "network_calls", "provider_calls"],
            },
            side_effect_class=ToolSideEffect.NONE,
            required_permissions=("workspace:runs:write",),
            timeout_seconds=5,
        )

    def execute(
        self,
        task: AgentTask,
        *,
        objective: str,
    ) -> FixtureToolExecution:
        if FIXTURE_TOOL_ID not in task.allowed_tool_ids:
            raise ValueError("fixture Tool is not allowed for this AgentTask")
        timestamp = self.clock()
        output = self.artifact_store.put_json(
            {
                "schema_version": "conflux-weave.fixture-tool-output.v1",
                "objective": objective,
                "summary": "离线 Harness 已完成确定性工具调用。",
                "network_calls": 0,
                "provider_calls": 0,
                "input_tokens": 0,
                "output_tokens": 0,
            },
            producer_step_id=task.step_id,
            schema_version="conflux-weave.fixture-tool-output.v1",
        )
        result = ToolResult(
            tool_call_id=f"tool-call:{task.agent_task_id}",
            tool_id=FIXTURE_TOOL_ID,
            agent_task_id=task.agent_task_id,
            status=ToolResultStatus.SUCCEEDED,
            output_refs=(output.artifact_id,),
            evidence_refs=(),
            error_ref=None,
            started_at=timestamp,
            finished_at=timestamp,
        )
        result_artifact = self.artifact_store.put_json(
            contract_to_dict(result),
            producer_step_id=task.step_id,
            schema_version=result.schema_version,
        )
        return FixtureToolExecution(result=result, artifacts=(output, result_artifact))


class ResearchFixtureAgent:
    executor_id = FIXTURE_AGENT_ID

    def __init__(
        self,
        artifact_store: LocalArtifactStore,
        tool_gateway: FixtureToolGateway,
    ) -> None:
        self.artifact_store = artifact_store
        self.tool_gateway = tool_gateway
        self.profile = AgentProfile(
            agent_type="research_fixture",
            version="v1",
            description="Deterministic offline ResearchAgent fixture",
            accepted_task_kinds=(FIXTURE_TASK_KIND,),
            allowed_tool_ids=(FIXTURE_TOOL_ID,),
            default_budget=BudgetLedger(30, 0, 0, "0", 1, 0, 1),
        )

    def execute(
        self, task: AgentTask, context: ContextBundle
    ) -> tuple[AgentResult, tuple[ArtifactRef, ...]]:
        if task.task_kind not in self.profile.accepted_task_kinds:
            raise ValueError("Research fixture Agent cannot execute this task kind")
        execution = self.tool_gateway.execute(task, objective=context.objective)
        result = AgentResult(
            agent_task_id=task.agent_task_id,
            status=AgentResultStatus.PARTIAL,
            summary="离线 ResearchAgent fixture 已完成；未执行真实检索或模型调用。",
            output_refs=tuple(item.artifact_id for item in execution.artifacts),
            evidence_refs=(),
            stop_reason="fixture_boundary_reached",
        )
        result_artifact = self.artifact_store.put_json(
            contract_to_dict(result),
            producer_step_id=task.step_id,
            schema_version=result.schema_version,
        )
        return result, (*execution.artifacts, result_artifact)


class ResearchFixtureRuntime:
    executor_id = FIXTURE_AGENT_ID
    task_kinds = (FIXTURE_TASK_KIND,)

    def __init__(
        self,
        repository: SQLiteRuntimeRepository,
        artifact_store: LocalArtifactStore,
        workspace: LocalWorkspaceAdapter,
        *,
        clock: Callable[[], str] | None = None,
        id_factory: Callable[[str], str] | None = None,
    ) -> None:
        self.repository = repository
        self.artifact_store = artifact_store
        self.workspace = workspace
        self.clock = clock or repository.clock
        self.id_factory = id_factory or _new_id
        self.worker = SQLiteStepWorker(
            repository,
            "research-fixture-worker",
            workflow_version=FIXTURE_WORKFLOW_VERSION,
        )
        self.agent = ResearchFixtureAgent(
            artifact_store,
            FixtureToolGateway(artifact_store, clock=self.clock),
        )

    def submit(self, submission: TaskSubmission) -> SubmissionResult:
        if submission.task_kind != FIXTURE_TASK_KIND:
            raise ValueError("ResearchFixtureRuntime only accepts research_fixture")
        objective = submission.input.get("objective")
        if not isinstance(objective, str) or not objective.strip():
            raise ValueError("research_fixture requires a non-empty objective")
        objective = objective.strip()
        created_at = self.clock()
        task_id = self.id_factory("task")
        run_id = self.id_factory("run")
        step_id = f"{run_id}:research_fixture"
        budget = self.agent.profile.default_budget
        config = self.artifact_store.put_json(
            {
                "schema_version": "conflux-weave.research-fixture-config.v1",
                "workflow_version": FIXTURE_WORKFLOW_VERSION,
                "agent": FIXTURE_AGENT_ID,
                "tool": FIXTURE_TOOL_ID,
                "validation_only": True,
                "network": False,
                "provider": False,
                "budget": {
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "tool_calls": 1,
                    "retrieval_rounds": 0,
                },
            },
            producer_step_id=step_id,
            schema_version="conflux-weave.research-fixture-config.v1",
        )
        frozen_input = {
            "query": objective,
            "objective": objective,
            "validation_only": True,
            "network_calls": 0,
            "provider_calls": 0,
        }
        key = submission.idempotency_key or _input_key(frozen_input)
        task = TaskSpec(
            task_id=task_id,
            kind=FIXTURE_TASK_KIND,
            input=frozen_input,
            requested_policy=FIXTURE_WORKFLOW_VERSION,
            idempotency_key=key,
        )
        run = RunRecord(
            run_id=run_id,
            task_id=task_id,
            status=RunStatus.ACCEPTED,
            workflow_version=FIXTURE_WORKFLOW_VERSION,
            config_snapshot_ref=config.artifact_id,
            budget=budget,
            created_at=created_at,
            updated_at=created_at,
        )
        step = StepRecord(
            step_id=step_id,
            run_id=run_id,
            kind=FIXTURE_TASK_KIND,
            attempt=1,
            status=StepStatus.PENDING,
            input_refs=(config.artifact_id,),
        )
        result = self.repository.submit_task(
            task,
            run,
            (step,),
            step_policies={
                step_id: StepPolicy(
                    SideEffectClass.NONE,
                    "deterministic replay; no network or Provider side effect",
                )
            },
            submission_artifacts=(config,),
        )
        if result.created:
            self.repository.transition_run(
                result.run_id, RunStatus.QUEUED, updated_at=created_at
            )
        return result

    def work_once(self, *, now: str | None = None) -> str | None:
        claim = self.worker.claim_next(now=now)
        if claim is None:
            return None
        timestamp = now or self.clock()
        if self.repository.is_cancel_requested(claim.run_id):
            self.repository.cancel_claim(claim, now=timestamp)
            return "cancelled"
        try:
            self._execute(claim, timestamp)
        except Exception as exc:
            self._fail(claim, exc, timestamp)
            return "failed"
        return self.repository.get_run(claim.run_id).status.value

    def request_cancel(self, run_id: str, *, now: str | None = None) -> RunRecord:
        return self.repository.request_cancel(run_id, now=now)

    def resume(
        self,
        run_id: str,
        decision: RecoveryDecision | None = None,
        *,
        now: str | None = None,
    ) -> RunRecord:
        return self.repository.resume_run(run_id, decision, now=now)

    def _execute(self, claim: Any, timestamp: str) -> None:
        run = self.repository.get_run(claim.run_id)
        task_spec = self.repository.get_task_for_run(claim.run_id)
        objective = str(task_spec.input["objective"])
        agent_task_id = f"{claim.run_id}:research_fixture"
        context = ContextBundle(
            context_id=f"context:{agent_task_id}",
            agent_task_id=agent_task_id,
            identity=FIXTURE_AGENT_ID,
            objective=objective,
            state_snapshot={
                "run_id": claim.run_id,
                "step_id": claim.step_id,
                "status": "running",
                "validation_only": True,
                "input_tokens": 0,
                "output_tokens": 0,
            },
            input_refs=(run.config_snapshot_ref,),
            evidence_refs=(),
            workspace_refs=(
                WorkspaceRef(
                    uri=f"weave://runs/{claim.run_id}/",
                    kind=WorkspaceKind.DIRECTORY,
                    revision=None,
                    media_type=None,
                    read_only=False,
                ),
            ),
            available_tool_ids=(FIXTURE_TOOL_ID,),
            constraints=(
                "offline fixture only",
                "no network or Provider calls",
                "do not claim research capability",
            ),
            completion_criteria=("publish one traceable fixture result",),
            created_at=run.created_at,
        )
        context_artifact = self.artifact_store.put_json(
            contract_to_dict(context),
            producer_step_id=claim.step_id,
            schema_version=context.schema_version,
        )
        agent_task = AgentTask(
            agent_task_id=agent_task_id,
            run_id=claim.run_id,
            step_id=claim.step_id,
            task_kind=FIXTURE_TASK_KIND,
            objective=objective,
            completion_criteria=context.completion_criteria,
            context_ref=context_artifact.artifact_id,
            input_refs=context.input_refs,
            allowed_tool_ids=context.available_tool_ids,
            budget=run.budget,
            idempotency_key=f"agent-task:{claim.run_id}",
        )
        task_artifact = self.artifact_store.put_json(
            contract_to_dict(agent_task),
            producer_step_id=claim.step_id,
            schema_version=agent_task.schema_version,
        )
        assigned = self._append_message(
            claim.run_id,
            agent_task_id,
            MessageType.TASK_ASSIGNED,
            task_artifact,
            sender="orchestrator",
            recipient=FIXTURE_AGENT_ID,
            created_at=run.created_at,
        )
        context_message = self._append_message(
            claim.run_id,
            agent_task_id,
            MessageType.STATUS_UPDATE,
            context_artifact,
            sender=FIXTURE_AGENT_ID,
            recipient="orchestrator",
            causation_id=assigned.message.message_id,
            suffix="context",
            created_at=run.created_at,
        )

        existing_result = next(
            (
                item
                for item in self.repository.get_agent_messages(claim.run_id)
                if item.agent_task_id == agent_task_id
                and item.message_type is MessageType.RESULT_SUBMITTED
            ),
            None,
        )
        if existing_result is None:
            result, execution_artifacts = self.agent.execute(agent_task, context)
            tool_output, tool_result_artifact, _ = execution_artifacts
            tool_output_message = self._append_message(
                claim.run_id,
                agent_task_id,
                MessageType.STATUS_UPDATE,
                tool_output,
                sender=FIXTURE_AGENT_ID,
                recipient="orchestrator",
                causation_id=context_message.message.message_id,
                suffix="tool-output",
                created_at=run.created_at,
            )
            tool_result_message = self._append_message(
                claim.run_id,
                agent_task_id,
                MessageType.STATUS_UPDATE,
                tool_result_artifact,
                sender=FIXTURE_AGENT_ID,
                recipient="orchestrator",
                causation_id=tool_output_message.message.message_id,
                suffix="tool-result",
                created_at=run.created_at,
            )
            report_payload = {
                "schema_version": FIXTURE_REPORT_SCHEMA,
                "label": "offline_harness_fixture",
                "answer": (
                    "离线 ResearchAgent Harness 闭环已完成。\n\n"
                    "本次运行生成并持久化了 Context Bundle、AgentTask、"
                    "ToolResult、AgentResult、通信消息和交付 Artifact。\n\n"
                    "边界：未执行真实论文检索、RAG 或模型调用。"
                ),
                "agent_result": contract_to_dict(result),
                "tool_result_ref": tool_result_artifact.artifact_id,
                "network_calls": 0,
                "provider_calls": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "tool_calls": 1,
            }
            workspace_ref = self.workspace.write_bytes_atomic(
                f"weave://runs/{claim.run_id}/artifacts/fixture-result.json",
                _json_bytes(report_payload),
                WorkspaceAccess(
                    agent_instance_id=FIXTURE_AGENT_ID,
                    run_id=claim.run_id,
                ),
                media_type="application/json",
            )
            report = self.workspace.publish_artifact(
                workspace_ref,
                WorkspaceAccess(
                    agent_instance_id=FIXTURE_AGENT_ID,
                    run_id=claim.run_id,
                ),
                producer_step_id=claim.step_id,
                schema_version=FIXTURE_REPORT_SCHEMA,
            )
            result_message = self._append_message(
                claim.run_id,
                agent_task_id,
                MessageType.RESULT_SUBMITTED,
                report,
                sender=FIXTURE_AGENT_ID,
                recipient="orchestrator",
                causation_id=tool_result_message.message.message_id,
                created_at=run.created_at,
            )
            existing_result = result_message.message

        report = self.repository.get_artifact_registrations(
            existing_result.payload_ref
        )[0]
        if self.repository.get_budget_status(claim.run_id).actual.tool_calls == 0:
            self.repository.record_local_attempt_usage(
                claim,
                BudgetAmount(tool_calls=1),
                now=timestamp,
            )
        delivery = DeliveryRecord(
            run_id=claim.run_id,
            disposition=DeliveryDisposition.PARTIAL,
            artifact_refs=(report.artifact_id,),
            limitations=(
                "离线 fixture，仅验证 Harness 机制和可恢复交付。",
                "未调用网络、模型 Provider 或论文来源。",
            ),
            unmet_criteria=("真实论文检索、完整 RAG 和引用验证尚未执行。",),
            recovery_actions=("进入 S1 后使用真实来源和 qwen3.7flash 验证。",),
        )
        self.repository.publish_delivery(
            claim.run_id,
            RunStatus.PARTIAL,
            delivery,
            (report,),
            claim=claim,
            published_at=timestamp,
        )

    def _append_message(
        self,
        run_id: str,
        agent_task_id: str,
        message_type: MessageType,
        payload: ArtifactRef,
        *,
        sender: str,
        recipient: str,
        created_at: str,
        causation_id: str | None = None,
        suffix: str | None = None,
    ) -> Any:
        message_suffix = suffix or message_type.value
        message = MessageEnvelope(
            message_id=f"message:{agent_task_id}:{message_suffix}",
            run_id=run_id,
            agent_task_id=agent_task_id,
            sender=sender,
            recipient=recipient,
            message_type=message_type,
            causation_id=causation_id,
            correlation_id=run_id,
            payload_ref=payload.artifact_id,
            idempotency_key=f"message:{agent_task_id}:{message_suffix}",
            created_at=created_at,
        )
        return self.repository.append_agent_message(message, payload)

    def _fail(self, claim: Any, exc: Exception, timestamp: str) -> None:
        detail = self.artifact_store.put_json(
            {
                "schema_version": "conflux-weave.research-fixture-failure.v1",
                "exception_type": type(exc).__name__,
                "message": str(exc),
                "validation_only": True,
            },
            producer_step_id=claim.step_id,
            schema_version="conflux-weave.research-fixture-failure.v1",
        )
        error = ErrorRecord(
            code="research_fixture_failed",
            category=ErrorCategory.TOOL,
            stage=FIXTURE_TASK_KIND,
            retryable=False,
            user_message="离线 Harness 验证失败。",
            technical_detail_ref=detail.artifact_id,
            recovery_action="查看失败 Artifact，修复机制后创建新的 fixture Run。",
        )
        self.repository.record_error(claim, error, (detail,), now=timestamp)
        try:
            self._append_message(
                claim.run_id,
                f"{claim.run_id}:research_fixture",
                MessageType.FAILURE_REPORTED,
                detail,
                sender=FIXTURE_AGENT_ID,
                recipient="orchestrator",
                created_at=self.repository.get_run(claim.run_id).created_at,
            )
        finally:
            self.worker.fail(claim, detail.artifact_id, now=timestamp)
            self.repository.transition_run(
                claim.run_id, RunStatus.FAILED, updated_at=timestamp
            )


def _input_key(value: dict[str, Any]) -> str:
    return "research-fixture:" + hashlib.sha256(_json_bytes(value)).hexdigest()


def _json_bytes(value: dict[str, Any]) -> bytes:
    import json

    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid4()}"


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


__all__ = [
    "FIXTURE_AGENT_ID",
    "FIXTURE_REPORT_SCHEMA",
    "FIXTURE_TASK_KIND",
    "FIXTURE_TOOL_ID",
    "FIXTURE_WORKFLOW_VERSION",
    "FixtureToolGateway",
    "ResearchFixtureAgent",
    "ResearchFixtureRuntime",
]
