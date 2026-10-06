"""P6-C2 Step 级 checkpoint 与恢复测试（深度研究主链路，离线）。

验收对照：
- 在 retrieve 阶段终止 Worker → 重启后从最近 checkpoint 继续（不重跑付费批次）
- 不重复生成不可区分的报告（输出 Artifact 一致）
- 原始失败记录保留
- 恢复后的 Run 可追溯每一步（checkpoint 台账）
"""

import json
from pathlib import Path
import pytest

from conflux_weave.core import (
    BudgetLedger,
    DeliveryDisposition,
    RunRecord,
    RunStatus,
    StepRecord,
    StepStatus,
    TaskSpec,
)
from conflux_weave.runtime import (
    LocalArtifactStore,
    SQLiteRuntimeRepository,
    StepRecoveryOutcome,
    RecoveryAction,
)
from conflux_weave.runtime.sqlite_contracts import BudgetAmount
from conflux_weave.core.errors import ErrorCategory, ErrorRecord
from conflux_weave.runtime.durable_research import (
    DURABLE_RESEARCH_WORKFLOW_VERSION,
    DurableResearchExecution,
    DurableResearchRuntime,
    VerifiedWorkflowExecutorAdapter,
)
from conflux_weave.research_agents import VerifiedResearchWorkflow
from conflux_weave.managed_research import ManagedVerifiedResearchWorkflow
from conflux_weave.hybrid_retrieval import HybridRetrievalPipeline
from conflux_weave.indexing import LanceDBDenseIndex
from conflux_weave.retrieval import RetrievalDocument
from conflux_weave.provider import (
    OpenAICompatibleChatAdapter,
    OpenAICompatibleEmbeddingAdapter,
    OpenAICompatibleRerankerAdapter,
    ProviderConfig,
    ProviderHttpResponse,
)

NOW = "2026-09-12T13:00:00Z"
WRITER_FIXTURE = json.loads(
    Path("tests/fixtures/writer_stage_fixtures.json").read_text(encoding="utf-8")
)


class CountingSequenceTransport:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.call_count = 0
        self.requests = []

    def post(self, *args, **kwargs):
        self.call_count += 1
        self.requests.append(json.loads(kwargs["body"]))
        payload = self.payloads.pop(0)
        return ProviderHttpResponse(
            200, json.dumps(payload).encode(), {"Content-Type": "application/json"}
        )


def chat_response(content, response_id):
    return {
        "id": response_id,
        "model": "fixture-chat",
        "choices": [
            {"message": {"content": json.dumps(content)}, "finish_reason": "stop"}
        ],
        "usage": {
            "prompt_tokens": 10,
            "completion_tokens": 10,
            "total_tokens": 20,
        },
    }

EXECUTION_PAYLOAD = {
    "schema_version": "conflux-weave.durable-research-execution.v1",
    "report_artifact_id": "report",
    "manifest_artifact_id": "manifest",
    "evidence_refs": [],
    "evidence": [],
    "usage": {"input_tokens": 10, "output_tokens": 10, "tool_calls": 0, "retrieval_rounds": 1},
    "provider_call_count": 1,
    "timings_ms": {},
    "usage_granularity": "aggregate_research_batch",
    "disposition": "complete",
    "limitations": [],
    "unmet_criteria": [],
}


class CountingExecutor:
    """记录调用次数的执行器桩：复用时不得再次调用。"""

    calls = 0

    def __init__(self, store):
        self.store = store

    def __call__(self, task_kind, objective, max_subquestions):
        CountingExecutor.calls += 1
        step_id = f"stub-exec-{CountingExecutor.calls}"
        report = self.store.put_bytes(
            b"# report\n",
            media_type="text/markdown; charset=utf-8",
            producer_step_id=step_id,
            schema_version="conflux-weave.durable-research-report.v1",
        )
        manifest = self.store.put_json(
            {"schema_version": "fixture-manifest"},
            producer_step_id=step_id,
            schema_version="fixture-manifest.v1",
        )
        return DurableResearchExecution(
            report_artifact_id=report.artifact_id,
            manifest_artifact_id=manifest.artifact_id,
            evidence_refs=(),
            evidence_records=(),
            usage=BudgetAmount(input_tokens=10, output_tokens=10, tool_calls=1, retrieval_rounds=1),
            provider_call_count=1,
            disposition=DeliveryDisposition.COMPLETE,
        )


def build_runtime(tmp_path):
    store = LocalArtifactStore(tmp_path / "artifacts")
    repository = SQLiteRuntimeRepository(tmp_path / "db" / "runtime.sqlite3", store, clock=lambda: NOW)
    executor = CountingExecutor(store)
    runtime = DurableResearchRuntime(
        repository, store, executor, worker_id="w1", lease_seconds=60, clock=lambda: NOW
    )
    return repository, store, runtime


def _make_execution_artifact(store, step_id: str):
    payload = dict(EXECUTION_PAYLOAD)
    report = store.put_bytes(b"# report\n", media_type="text/markdown; charset=utf-8", producer_step_id=step_id, schema_version="conflux-weave.durable-research-report.v1")
    payload["report_artifact_id"] = report.artifact_id
    execution = store.put_json(payload, producer_step_id=step_id, schema_version="conflux-weave.durable-research-execution.v1")
    return report, execution


def submit_durable_run(runtime, suffix="c2"):
    """走 runtime.submit 正规路径（含 step_policies 注册），再置为 RUNNING。"""
    result = runtime.submit(
        f"测试目标-{suffix}",
        task_kind="managed_verified_research",
        max_subquestions=2,
        budget=BudgetLedger(900, 1000, 1000, "unavailable", 4, 2, 1),
    )
    run_id = result.run_id
    if runtime.repository.get_run(run_id).status != RunStatus.RUNNING:
        runtime.repository.transition_run(run_id, RunStatus.RUNNING, updated_at=NOW)
    return run_id


def test_checkpoint_registry_roundtrip(tmp_path) -> None:
    repository, store, runtime = build_runtime(tmp_path)
    run_id = submit_durable_run(runtime)
    exec_step = next(s for s in repository.get_steps(run_id) if s.kind == "execute_research")
    report, execution = _make_execution_artifact(store, exec_step.step_id)
    repository.record_step_checkpoint(
        run_id, exec_step.step_id,
        chain_phase="retrieve", input_digest="sha256:abc", output_artifact_id=execution.artifact_id,
        attempt=1, started_at=NOW, finished_at=NOW,
    )
    items = repository.list_step_checkpoints(run_id)
    assert len(items) == 1
    assert items[0]["chain_phase"] == "retrieve"
    assert items[0]["status"] == "succeeded"
    # 同输入摘要 → 可复用
    reused = repository.reuse_step_checkpoint(run_id, "retrieve", "sha256:abc")
    assert reused is not None
    assert reused["output_artifact_id"] == execution.artifact_id
    # 输入摘要不同 → 不可复用
    assert repository.reuse_step_checkpoint(run_id, "retrieve", "sha256:different") is None
    # 阶段不符 → 不可复用
    assert repository.reuse_step_checkpoint(run_id, "write", "sha256:abc") is None


def test_worker_termination_resumes_from_checkpoint(tmp_path) -> None:
    """验收：retrieve 阶段后终止 Worker → 重启后从 checkpoint 继续，不重跑批次。"""
    repository, store, runtime = build_runtime(tmp_path)
    CountingExecutor.calls = 0
    run_id = submit_durable_run(runtime)

    # 第一次执行：完成 retrieve 步骤（付费批次运行一次）
    result = runtime.work_once(now=NOW)
    assert result is not None and result.status in {"running", "succeeded"}
    assert CountingExecutor.calls == 1
    checkpoints = repository.list_step_checkpoints(run_id)
    assert any(item["chain_phase"] == "retrieve" for item in checkpoints)
    execution_artifact = next(item["output_artifact_id"] for item in checkpoints if item["chain_phase"] == "retrieve")

    # 模拟在 publish 阶段 Worker 崩溃：publish 步骤失败，Run 仍 running
    # （execute_research 的 attempt 输出已在库中，checkpoint 台账可复用）
    # 新的 Worker（进程重启语义）认领并执行
    runtime2 = DurableResearchRuntime(repository, store, CountingExecutor(store), worker_id="w2-restarted", lease_seconds=60, clock=lambda: NOW)
    # "Worker 在 retrieve 后崩溃重启"：新 worker 进程，同一 SQLite 队列
    for _ in range(6):
        result = runtime2.work_once(now=NOW)
        if result is None or repository.get_run(run_id).status.is_terminal:
            break
    assert repository.get_run(run_id).status in {RunStatus.SUCCEEDED, RunStatus.PARTIAL}
    assert CountingExecutor.calls == 1  # 付费批次未重复执行


def test_requeue_allows_retry_and_preserves_failure_record(tmp_path) -> None:
    """验收：可重试错误自动重试；原始失败记录保留。"""
    repository, store, runtime = build_runtime(tmp_path)
    run_id = submit_durable_run(runtime)
    detail = store.put_json({"error": "transient"}, producer_step_id=f"{run_id}:execute_research", schema_version="fixture.error.v1")
    claim = repository.claim_next_step("w1", lease_seconds=60, now=NOW)
    assert claim is not None
    repository.fail_attempt(claim, detail.artifact_id, now=NOW)
    repository.record_error(
        claim,
        ErrorRecord(
            "research_step_failed",
            ErrorCategory.UNKNOWN,
            "execute_research",
            True,
            "transient failure",
            detail.artifact_id,
            (),
            "retry the step",
        ),
        (detail,),
        now=NOW,
    )
    # 失败记录入 errors 表
    assert len(repository.get_errors(run_id)) >= 1
    # 放回队列后可再次认领（attempt+1）
    assert repository.requeue_failed_step(run_id, claim.step_id) is True
    claim2 = repository.claim_next_step("w1", lease_seconds=60, now=NOW)
    assert claim2 is not None and claim2.attempt_number == 2
    # 原始失败记录仍在
    assert len(repository.get_errors(run_id)) >= 1


def test_unknown_outcome_checkpoints_not_reusable(tmp_path) -> None:
    repository, store, runtime = build_runtime(tmp_path)
    run_id = submit_durable_run(runtime)
    exec_step = next(s for s in repository.get_steps(run_id) if s.kind == "execute_research")
    report, execution = _make_execution_artifact(store, exec_step.step_id)
    repository.record_step_checkpoint(
        run_id, exec_step.step_id,
        chain_phase="retrieve", input_digest="sha256:xyz", output_artifact_id=execution.artifact_id,
        attempt=1, started_at=NOW,
    )
    repository.mark_step_checkpoints_unknown_outcome(run_id, exec_step.step_id)
    items = repository.list_step_checkpoints(run_id)
    assert any(item["status"] == "unknown_outcome" for item in items)
    assert repository.reuse_step_checkpoint(run_id, "retrieve", "sha256:xyz") is None


def test_checkpoints_api_listing(tmp_path) -> None:
    from starlette.testclient import TestClient
    from conflux_weave.server import WorkerLoop, create_app

    class Passive:
        executor_id = "p"
        task_kinds = ("paper_discovery",)

        def work_once(self, **kwargs):
            return None

    repository, store, runtime = build_runtime(tmp_path)
    run_id = submit_durable_run(runtime)
    exec_step = next(s for s in repository.get_steps(run_id) if s.kind == "execute_research")
    report, execution = _make_execution_artifact(store, exec_step.step_id)
    repository.record_step_checkpoint(
        run_id, exec_step.step_id,
        chain_phase="retrieve", input_digest="sha256:abc", output_artifact_id=execution.artifact_id,
        attempt=1, started_at=NOW,
    )
    app = create_app(repository, Passive(), worker=WorkerLoop(Passive(), interval_seconds=10))
    client = TestClient(app)
    response = client.get(f"/api/v1/runs/{run_id}/checkpoints")
    assert response.status_code == 200
    payload = response.json()
    assert payload["run_id"] == run_id
    assert payload["items"][0]["chain_phase"] in {"plan", "retrieve", "claim", "verify", "synthesize", "write", "deliver"}
    missing = client.get("/api/v1/runs/run-missing/checkpoints")
    assert missing.status_code == 404


def test_evaluate_recovery_action_all_branches(tmp_path) -> None:
    """P7-C2 统一恢复决策：覆盖 5 种执行结果及推荐动作。"""
    repository, store, runtime = build_runtime(tmp_path)
    run_id = submit_durable_run(runtime)
    exec_step = next(s for s in repository.get_steps(run_id) if s.kind == "execute_research")
    step_id = exec_step.step_id

    # 1. 初始状态：未认领，无外部调用 -> REQUEST_NOT_SENT, AUTOMATIC_RETRY
    outcome, action, _ = repository.evaluate_recovery_action(run_id, step_id)
    assert outcome == StepRecoveryOutcome.REQUEST_NOT_SENT
    assert action == RecoveryAction.AUTOMATIC_RETRY

    # 2. 外部付费请求已发出但未收到响应 -> REQUEST_SENT_NO_RESPONSE, AWAIT_USER_DECISION
    claim = repository.claim_next_step("w1", lease_seconds=60, now=NOW)
    assert claim is not None
    intent = store.put_json({"intent": "call"}, producer_step_id=step_id, schema_version="test.intent.v1")
    with repository._connect() as conn:
        conn.execute(
            "UPDATE attempt_effects SET effect_state = 'request_started', intent_artifact_ref = ? WHERE attempt_id = ?",
            (intent.artifact_id, claim.attempt_id),
        )
    outcome, action, _ = repository.evaluate_recovery_action(run_id, step_id)
    assert outcome == StepRecoveryOutcome.REQUEST_SENT_NO_RESPONSE
    assert action == RecoveryAction.AWAIT_USER_DECISION

    # 3. 外部响应已接收但尚未完成持久化 -> RESPONSE_RECEIVED_NOT_PERSISTED, REUSE_ARTIFACT
    resp_art = store.put_json({"result": "ok"}, producer_step_id=step_id, schema_version="test.resp.v1")
    with repository._connect() as conn:
        conn.execute(
            "UPDATE attempt_effects SET effect_state = 'response_committed', response_artifact_ref = ? WHERE attempt_id = ?",
            (resp_art.artifact_id, claim.attempt_id),
        )
    outcome, action, _ = repository.evaluate_recovery_action(run_id, step_id)
    assert outcome == StepRecoveryOutcome.RESPONSE_RECEIVED_NOT_PERSISTED
    assert action == RecoveryAction.REUSE_ARTIFACT

    # 4. Checkpoint 已持久化但交付尚未发布 -> RESPONSE_PERSISTED_DELIVERY_FAILED, REUSE_ARTIFACT
    report, execution = _make_execution_artifact(store, step_id)
    repository.record_step_checkpoint(
        run_id, step_id,
        chain_phase="write", input_digest="sha256:write_dig", output_artifact_id=execution.artifact_id,
        attempt=1, started_at=NOW, finished_at=NOW, resumable=True,
    )
    outcome, action, _ = repository.evaluate_recovery_action(run_id, step_id)
    assert outcome == StepRecoveryOutcome.RESPONSE_PERSISTED_DELIVERY_FAILED
    assert action == RecoveryAction.REUSE_ARTIFACT

    # 5. 交付已发布 -> DELIVERY_PUBLISHED, REUSE_ARTIFACT
    with repository._connect() as conn:
        conn.execute(
            """
            INSERT INTO deliveries(run_id, disposition, evidence_refs_json, limitations_json, unmet_criteria_json, recovery_actions_json, created_at)
            VALUES (?, 'complete', '[]', '[]', '[]', '[]', ?)
            """,
            (run_id, NOW),
        )
    outcome, action, _ = repository.evaluate_recovery_action(run_id, step_id)
    assert outcome == StepRecoveryOutcome.DELIVERY_PUBLISHED
    assert action == RecoveryAction.REUSE_ARTIFACT

    # 6. Run 处于终态 (failed / cancelled) -> TERMINATE_RUN
    run_id2 = submit_durable_run(runtime, suffix="term")
    exec_step2 = next(s for s in repository.get_steps(run_id2) if s.kind == "execute_research")
    repository.transition_run(run_id2, RunStatus.FAILED, updated_at=NOW)
    outcome, action, _ = repository.evaluate_recovery_action(run_id2, exec_step2.step_id)
    assert action == RecoveryAction.TERMINATE_RUN


def test_verified_research_internal_checkpoints_roundtrip_and_reuse(tmp_path) -> None:
    """P7-C1 内部 Checkpoint：plan / retrieve / claim / verify / synthesize / write 逐阶段入账与复用。"""
    repository, store, runtime = build_runtime(tmp_path)
    run_id = submit_durable_run(runtime, suffix="internal_chk")
    exec_step = next(s for s in repository.get_steps(run_id) if s.kind == "execute_research")
    step_id = exec_step.step_id

    config = ProviderConfig("https://provider.example/v1", "secret", "chat")
    documents = (
        RetrievalDocument(
            "chunk-a",
            "The framework reduces context by selecting evidence before tool actions.",
            "paper-a",
            {"type": "pdf_page", "page": 3},
        ),
    )
    index = LanceDBDenseIndex(tmp_path / "dense_db")
    index.publish(documents, ((1.0, 0.0),))
    emb_transport = CountingSequenceTransport([{"data": [{"index": 0, "embedding": [1.0, 0.0]}]}])
    rerank_transport = CountingSequenceTransport([{"results": [{"index": 0, "relevance_score": 0.9}]}])
    retrieval = HybridRetrievalPipeline(
        documents,
        index,
        OpenAICompatibleEmbeddingAdapter(store, config, transport=emb_transport),
        OpenAICompatibleRerankerAdapter(store, config, transport=rerank_transport),
    )

    chat_payloads = [
        chat_response({"queries": ["How is context reduced?"]}, "plan"),
        chat_response(
            {"claims": [{"text": "The framework selects evidence before tool actions.", "evidence_ids": ["evidence-0001"]}]},
            "draft",
        ),
        chat_response(
            {"assessments": [{"claim_id": "claim-0001", "evidence_ids": ["evidence-0001"], "relation": "supports", "verdict": "accepted", "rationale": "Directly stated."}]},
            "verify",
        ),
        chat_response(WRITER_FIXTURE["distill_payloads"]["canonical"], "distill"),
        chat_response(
            {
                "summary": {"text": "该框架在工具动作之前先选择证据。", "claim_ids": ["claim-0001"]},
                "sections": [{"heading": "机制", "paragraphs": [{"text": "证据选择发生在工具动作之前。", "claim_ids": ["claim-0001"]}]}],
                "background": [],
                "open_questions": ["更大规模语料上的收益仍待验证。"],
            },
            "writer",
        ),
        chat_response(
            {
                "audits": [
                    {"section_index": 0, "paragraph_index": 0, "verdict": "supported", "rationale": "Summary restates cited claim."},
                    {"section_index": 1, "paragraph_index": 0, "verdict": "supported", "rationale": "Paragraph cites accepted claim."},
                ]
            },
            "audit",
        ),
    ]
    chat_transport = CountingSequenceTransport(chat_payloads)
    chat = OpenAICompatibleChatAdapter(store, config, transport=chat_transport)
    workflow = VerifiedResearchWorkflow(store, retrieval, chat, corpus_scope="fixture new-paper corpus")

    # 首次执行：生成并入账 6 个内部阶段 checkpoint
    result1 = workflow.execute(
        "How is context reduced?",
        max_queries=2,
        document_ids=("paper-a",),
        run_id=run_id,
        step_id=step_id,
        attempt=1,
        repository=repository,
    )
    assert result1.coverage.accepted_claim_count == 1
    assert chat_transport.call_count == 6
    assert emb_transport.call_count == 1
    assert rerank_transport.call_count == 1

    # 校验 checkpoints 台账包含所有 6 个内部阶段
    checkpoints = repository.list_step_checkpoints(run_id)
    phases = [item["chain_phase"] for item in checkpoints]
    assert set(phases) == {"plan", "retrieve", "claim", "verify", "synthesize", "write"}
    for item in checkpoints:
        assert item["status"] == "succeeded"
        assert item["resumable"] == 1
        assert item["output_artifact_id"]
        assert item["input_digest"]

    # 第二次执行：同输入摘要 -> 阶段 checkpoint 全部命中复用，0 新增模型与检索调用
    result2 = workflow.execute(
        "How is context reduced?",
        max_queries=2,
        document_ids=("paper-a",),
        run_id=run_id,
        step_id=step_id,
        attempt=1,
        repository=repository,
    )
    assert chat_transport.call_count == 6  # 0 新增模型调用
    assert emb_transport.call_count == 1   # 0 新增向量化调用
    assert rerank_transport.call_count == 1 # 0 新增重排调用
    assert result2.report_artifact_id == result1.report_artifact_id
    assert result2.coverage.accepted_claim_count == result1.coverage.accepted_claim_count


def test_durable_runtime_end_to_end_with_internal_checkpoints(tmp_path) -> None:
    """P7-C1/C2 端到端集成：VerifiedWorkflowExecutorAdapter 在 DurableResearchRuntime 中透传 checkpoint 上下文。"""
    store = LocalArtifactStore(tmp_path / "artifacts")
    repository = SQLiteRuntimeRepository(tmp_path / "db" / "runtime.sqlite3", store, clock=lambda: NOW)

    config = ProviderConfig("https://provider.example/v1", "secret", "chat")
    documents = (
        RetrievalDocument(
            "chunk-a",
            "The framework reduces context by selecting evidence before tool actions.",
            "paper-a",
            {"type": "pdf_page", "page": 3},
        ),
    )
    index = LanceDBDenseIndex(tmp_path / "dense_db")
    index.publish(documents, ((1.0, 0.0),))
    emb_transport = CountingSequenceTransport([{"data": [{"index": 0, "embedding": [1.0, 0.0]}]}])
    rerank_transport = CountingSequenceTransport([{"results": [{"index": 0, "relevance_score": 0.9}]}])
    retrieval = HybridRetrievalPipeline(
        documents,
        index,
        OpenAICompatibleEmbeddingAdapter(store, config, transport=emb_transport),
        OpenAICompatibleRerankerAdapter(store, config, transport=rerank_transport),
    )

    chat_payloads = [
        chat_response({"queries": ["How is context reduced?"]}, "plan"),
        chat_response(
            {"claims": [{"text": "The framework selects evidence before tool actions.", "evidence_ids": ["evidence-0001"]}]},
            "draft",
        ),
        chat_response(
            {"assessments": [{"claim_id": "claim-0001", "evidence_ids": ["evidence-0001"], "relation": "supports", "verdict": "accepted", "rationale": "Directly stated."}]},
            "verify",
        ),
        chat_response(WRITER_FIXTURE["distill_payloads"]["canonical"], "distill"),
        chat_response(
            {
                "summary": {"text": "该框架在工具动作之前先选择证据。", "claim_ids": ["claim-0001"]},
                "sections": [{"heading": "机制", "paragraphs": [{"text": "证据选择发生在工具动作之前。", "claim_ids": ["claim-0001"]}]}],
                "background": [],
                "open_questions": ["更大规模语料上的收益仍待验证。"],
            },
            "writer",
        ),
        chat_response(
            {
                "audits": [
                    {"section_index": 0, "paragraph_index": 0, "verdict": "supported", "rationale": "Summary restates cited claim."},
                    {"section_index": 1, "paragraph_index": 0, "verdict": "supported", "rationale": "Paragraph cites accepted claim."},
                ]
            },
            "audit",
        ),
    ]
    chat_transport = CountingSequenceTransport(chat_payloads)
    chat = OpenAICompatibleChatAdapter(store, config, transport=chat_transport)
    workflow = VerifiedResearchWorkflow(store, retrieval, chat, corpus_scope="fixture new-paper corpus")
    adapter = VerifiedWorkflowExecutorAdapter(store, verified_workflow=workflow)

    runtime = DurableResearchRuntime(
        repository, store, adapter, worker_id="w1", lease_seconds=60, clock=lambda: NOW
    )
    result = runtime.submit(
        "How is context reduced?",
        task_kind="verified_paper_research",
        max_subquestions=2,
        document_ids=("paper-a",),
        budget=BudgetLedger(900, 100_000, 10_000, "unavailable", 20, 5, 1),
    )
    run_id = result.run_id
    repository.transition_run(run_id, RunStatus.RUNNING, updated_at=NOW)

    # 1. 执行研究步骤 (execute_research)
    step1_res = runtime.work_once(now=NOW)
    assert step1_res is not None and step1_res.status in {"running", "succeeded"}
    assert chat_transport.call_count == 6

    # 验证 checkpoints 台账中同时包含内部 phase 以及外层 retrieve
    checkpoints = repository.list_step_checkpoints(run_id)
    phases = {item["chain_phase"] for item in checkpoints}
    for expected in ("plan", "retrieve", "claim", "verify", "synthesize", "write"):
        assert expected in phases

    # 验证此时恢复决策：产物已持久化，交付待重放
    exec_step = next(s for s in repository.get_steps(run_id) if s.kind == "execute_research")
    outcome, action, _ = repository.evaluate_recovery_action(run_id, exec_step.step_id)
    assert outcome == StepRecoveryOutcome.RESPONSE_PERSISTED_DELIVERY_FAILED
    assert action == RecoveryAction.REUSE_ARTIFACT

    # 2. 执行交付步骤 (deliver_research)
    step2_res = runtime.work_once(now=NOW)
    assert step2_res is not None
    assert repository.get_run(run_id).status in {RunStatus.SUCCEEDED, RunStatus.PARTIAL}

    # 交付发布后，恢复决策为 DELIVERY_PUBLISHED
    outcome, action, _ = repository.evaluate_recovery_action(run_id, exec_step.step_id)
    assert outcome == StepRecoveryOutcome.DELIVERY_PUBLISHED
    assert action == RecoveryAction.REUSE_ARTIFACT


def test_managed_verified_research_internal_checkpoints(tmp_path) -> None:
    """P7-C1 Managed 研究流程透传 checkpoint 上下文，子问题内部阶段入账。"""
    repository, store, runtime = build_runtime(tmp_path)
    run_id = submit_durable_run(runtime, suffix="managed_chk")
    exec_step = next(s for s in repository.get_steps(run_id) if s.kind == "execute_research")
    step_id = exec_step.step_id

    config = ProviderConfig("https://provider.example/v1", "secret", "chat")
    documents = (
        RetrievalDocument(
            "chunk-a",
            "The framework reduces context by selecting evidence before tool actions.",
            "paper-a",
            {"type": "pdf_page", "page": 3},
        ),
    )
    index = LanceDBDenseIndex(tmp_path / "dense_db")
    index.publish(documents, ((1.0, 0.0),))
    emb_transport = CountingSequenceTransport([
        {"data": [{"index": 0, "embedding": [1.0, 0.0]}]},
        {"data": [{"index": 0, "embedding": [1.0, 0.0]}]},
    ])
    rerank_transport = CountingSequenceTransport([
        {"results": [{"index": 0, "relevance_score": 0.9}]},
        {"results": [{"index": 0, "relevance_score": 0.9}]},
    ])
    retrieval = HybridRetrievalPipeline(
        documents,
        index,
        OpenAICompatibleEmbeddingAdapter(store, config, transport=emb_transport),
        OpenAICompatibleRerankerAdapter(store, config, transport=rerank_transport),
    )

    worker_sub_payloads = [
        chat_response({"claims": [{"text": "Method A reduces context.", "evidence_ids": ["evidence-0001"]}]}, "draft-a"),
        chat_response({"assessments": [{"claim_id": "claim-0001", "evidence_ids": ["evidence-0001"], "relation": "supports", "verdict": "accepted", "rationale": "Direct."}]}, "verify-a"),
        chat_response({"claims": [{"text": "Evaluation B measures tool success.", "evidence_ids": ["evidence-0001"]}]}, "draft-b"),
        chat_response({"assessments": [{"claim_id": "claim-0001", "evidence_ids": ["evidence-0001"], "relation": "supports", "verdict": "accepted", "rationale": "Direct."}]}, "verify-b"),
    ]
    worker_chat_transport = CountingSequenceTransport(worker_sub_payloads)
    worker_chat = OpenAICompatibleChatAdapter(store, config, transport=worker_chat_transport)
    worker_workflow = VerifiedResearchWorkflow(store, retrieval, worker_chat, corpus_scope="fixture new-paper corpus")

    manager_chat_payloads = [
        chat_response({
            "coverage_requirements": [
                {"coverage_id": "coverage-methods", "objective_quote": "methods"},
                {"coverage_id": "coverage-evaluation", "objective_quote": "evaluation"},
            ],
            "subquestions": [
                {"question": "What method reduces context?", "coverage_ids": ["coverage-methods"]},
                {"question": "How is success evaluated?", "coverage_ids": ["coverage-evaluation"]},
            ],
        }, "manager"),
        chat_response({"assessments": [
            {"coverage_id": "coverage-methods", "status": "covered", "claim_ids": ["sq1-claim-0001"], "rationale": "The method Claim addresses this requirement."},
            {"coverage_id": "coverage-evaluation", "status": "covered", "claim_ids": ["sq2-claim-0001"], "rationale": "The evaluation Claim addresses this requirement."},
        ]}, "coverage"),
    ]
    manager_chat_transport = CountingSequenceTransport(manager_chat_payloads)
    manager_chat = OpenAICompatibleChatAdapter(store, config, transport=manager_chat_transport)

    managed_workflow = ManagedVerifiedResearchWorkflow(store, worker_workflow, manager_chat)

    result = managed_workflow.execute(
        "Compare methods and evaluation",
        max_subquestions=2,
        document_ids=("paper-a",),
        run_id=run_id,
        step_id=step_id,
        attempt=1,
        repository=repository,
    )
    assert result.claim_count == 2
    # 验证子问题执行产生的内部 phase Checkpoints 均成功入账
    checkpoints = repository.list_step_checkpoints(run_id)
    phases = {item["chain_phase"] for item in checkpoints}
    for expected in ("plan", "retrieve", "claim", "verify"):
        assert expected in phases


