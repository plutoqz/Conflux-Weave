import json
from pathlib import Path
from types import SimpleNamespace

from conflux_weave.api_contracts import DeepResearchTaskRequest
from conflux_weave.core import BudgetLedger, DeliveryDisposition
from conflux_weave.deep_research import (
    DeepLocalChunk,
    DeepResearchResult,
    DeepResearchWorkflow,
    DeepSource,
    GPTResearcherBridge,
)
from conflux_weave.provider import (
    OpenAICompatibleChatAdapter,
    ProviderConfig,
    ProviderHttpResponse,
)
from conflux_weave.runtime import LocalArtifactStore

WRITER_FIXTURE = json.loads(Path("tests/fixtures/writer_stage_fixtures.json").read_text(encoding="utf-8"))
# 内容须达到 MIN_EVIDENCE_CONTENT_CHARS（200 字符）才会进入证据台账。
CONTENT_A = "Agents use skills to act on the world. " * 8
CONTENT_B = "Memory writes are deduplicated by content hash. " * 8
SOURCES = (
    DeepSource("https://a.example/skill", "Source A", CONTENT_A),
    DeepSource("https://b.example/memory", "Source B", CONTENT_B),
)


class SequenceTransport:
    def __init__(self, payloads):
        self.payloads = iter(payloads)
        self.requests = []

    def post(self, *args, **kwargs):
        self.requests.append(json.loads(kwargs["body"]))
        payload = next(self.payloads)
        return ProviderHttpResponse(200, json.dumps(payload).encode(), {"Content-Type": "application/json"})


def chat_response(content, response_id):
    return {
        "id": response_id,
        "model": "fixture-chat",
        "choices": [{"message": {"content": content if isinstance(content, str) else json.dumps(content)}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
    }


class FakeBridge:
    def __init__(self, sources=SOURCES, local_chunks=(), report_markdown="# raw engine report"):
        self.sources = sources
        self.local_chunks = local_chunks
        self.report_markdown = report_markdown
        self.calls = []

    def execute(self, objective, *, on_progress=None):
        self.calls.append(objective)
        return DeepResearchResult(
            sources=self.sources,
            context="context",
            report_markdown=self.report_markdown,
            planned_queries=(),
            costs_usd=0.05,
            token_usage={"input_tokens": 1200, "output_tokens": 400, "calls": 6},
            report_source="hybrid" if self.local_chunks else "web",
            local_chunks=self.local_chunks,
        )


def build_workflow(tmp_path, chat_payloads, bridge=None):
    store = LocalArtifactStore(tmp_path / "artifacts")
    config = ProviderConfig("https://provider.example/v1", "secret", "chat")
    chat = OpenAICompatibleChatAdapter(store, config, transport=SequenceTransport(chat_payloads))
    workflow = DeepResearchWorkflow(store, chat, bridge or FakeBridge(), code_revision="test")
    return workflow, store


DRAFT = {"claims": [
    {"text": "Agents use skills to act on the world.", "evidence_ids": ["evidence-0001"]},
    {"text": "Memory writes are deduplicated by content hash.", "evidence_ids": ["evidence-0002"]},
]}
VERIFY = {"assessments": [
    {"claim_id": "claim-0001", "evidence_ids": ["evidence-0001"], "relation": "supports", "verdict": "accepted", "rationale": "Direct."},
    {"claim_id": "claim-0002", "evidence_ids": ["evidence-0002"], "relation": "supports", "verdict": "accepted", "rationale": "Direct."},
]}
WRITER = {"summary": {"text": "技能让智能体执行动作；记忆写入按哈希去重。", "claim_ids": ["claim-0001"]}, "sections": [{"heading": "一、发现", "paragraphs": [{"text": "技能用于动作执行，记忆按内容哈希去重。", "claim_ids": ["claim-0001", "claim-0002"]}]}], "background": [], "open_questions": []}
AUDIT = {"audits": [
    {"section_index": 0, "paragraph_index": 0, "verdict": "supported", "rationale": "Summary restates claims."},
    {"section_index": 1, "paragraph_index": 0, "verdict": "supported", "rationale": "Paragraph restates both claims."},
]}


def read_artifact(store, artifact_id):
    return store.path_for_digest(artifact_id.removeprefix("artifact-sha256-")).read_text(encoding="utf-8")


def test_deep_workflow_produces_snapshots_ledger_and_v2_report(tmp_path):
    workflow, store = build_workflow(tmp_path, [
        chat_response(DRAFT, "draft"),
        chat_response(VERIFY, "verify"),
        chat_response({"cards": []}, "distill"),
        chat_response(WRITER, "writer"),
        chat_response(AUDIT, "audit"),
    ])

    result = workflow.execute("How do agents use skills and memory?")

    assert result.disposition is DeliveryDisposition.COMPLETE
    assert len(result.claims) == 2 and len(result.citations) == 2
    assert result.usage["tool_calls"] == len(result.evidence) + 1 == 3
    manifest = json.loads(read_artifact(store, result.manifest_artifact_id))
    assert manifest["report_contract"] == "v2"
    assert len(manifest["sources"]) == 2
    assert manifest["sources"][0]["url"] == "https://a.example/skill"
    assert manifest["sources"][0]["content_hash"].startswith("sha256-")
    report = read_artifact(store, result.report_artifact_id)
    assert "## 回答摘要" in report and "https://a.example/skill" in report
    assert "## 附录：聚合引擎综合视图（未经本地核验）" in report
    assert "raw engine report" in report
    assert "未经本地核验" in report
    manifest_engine_view = json.loads(read_artifact(store, result.manifest_artifact_id))["engine_view"]
    assert manifest_engine_view == "appended-unverified"
    raw = read_artifact(store, manifest["raw_report_artifact"])
    assert "raw engine report" in raw


def test_deep_workflow_delivers_unverified_report_when_verification_rejects(tmp_path):
    rejected = {"assessments": [
        {"claim_id": f"claim-{index:04d}", "evidence_ids": [f"evidence-{index:04d}"], "relation": "insufficient", "verdict": "rejected", "rationale": "Not supported."}
        for index in (1, 2)
    ]}
    workflow, store = build_workflow(tmp_path, [
        chat_response(DRAFT, "draft"),
        chat_response(rejected, "verify"),
        chat_response(DRAFT, "repair"),
        chat_response(rejected, "verify-2"),
    ])

    result = workflow.execute("How do agents use skills and memory?")

    assert result.disposition is DeliveryDisposition.PARTIAL
    assert result.claims == () and result.evidence == ()
    assert result.unmet_criteria and "Verifier" in result.unmet_criteria[0]
    manifest = json.loads(read_artifact(store, result.manifest_artifact_id))
    assert manifest["report_contract"] == "v2-unverified"
    assert manifest["verification"]["verified_claim_count"] == 0
    assert manifest["provider_call_count"] == 3  # 2 快照 + 1 引擎批次
    report = read_artifact(store, result.report_artifact_id)
    assert "# 深度研究（未通过本地核验）" in report
    assert "https://a.example/skill" in report
    assert "未经本地核验" in report
    raw = read_artifact(store, manifest["raw_report_artifact"])
    assert "raw engine report" in raw


def test_deep_workflow_delivers_unverified_report_when_draft_returns_nothing(tmp_path):
    workflow, store = build_workflow(tmp_path, [chat_response({"claims": []}, "draft")])

    result = workflow.execute("How do agents use skills and memory?")

    assert result.disposition is DeliveryDisposition.PARTIAL
    assert result.unmet_criteria and "起草" in result.unmet_criteria[0]
    manifest = json.loads(read_artifact(store, result.manifest_artifact_id))
    assert manifest["report_contract"] == "v2-unverified"
    report = read_artifact(store, result.report_artifact_id)
    assert "## 来源清单" in report and "Source A" in report


def test_deep_workflow_draft_schema_retry_recovers(tmp_path):
    workflow, store = build_workflow(tmp_path, [
        chat_response("not-json", "draft-bad"),
        chat_response(DRAFT, "draft"),
        chat_response(VERIFY, "verify"),
        chat_response({"cards": []}, "distill"),
        chat_response(WRITER, "writer"),
        chat_response(AUDIT, "audit"),
    ])

    result = workflow.execute("How do agents use skills and memory?")

    assert result.disposition is DeliveryDisposition.COMPLETE
    assert len(workflow.chat.transport.requests) >= 2
    assert "violated the contract" in workflow.chat.transport.requests[1]["messages"][0]["content"]


def test_deep_workflow_title_only_sources_stay_out_of_evidence(tmp_path):
    sources = (
        DeepSource("https://a.example/skill", "Source A", CONTENT_A),
        DeepSource("https://b.example/empty", "Source B", ""),
    )
    draft = {"claims": [{"text": "Agents use skills.", "evidence_ids": ["evidence-0001"]}]}
    verify = {"assessments": [
        {"claim_id": "claim-0001", "evidence_ids": ["evidence-0001"], "relation": "supports", "verdict": "accepted", "rationale": "Direct."},
    ]}
    workflow, store = build_workflow(tmp_path, [
        chat_response(draft, "draft"),
        chat_response(verify, "verify"),
        chat_response({"cards": []}, "distill"),
        chat_response(WRITER, "writer"),
        chat_response(AUDIT, "audit"),
    ], bridge=FakeBridge(sources=sources))

    result = workflow.execute("How do agents use skills?")

    assert result.disposition is DeliveryDisposition.COMPLETE
    assert len(result.evidence) == 1
    manifest = json.loads(read_artifact(store, result.manifest_artifact_id))
    assert len(manifest["sources"]) == 2
    assert manifest["sources"][0]["title_only"] is False
    assert manifest["sources"][1]["title_only"] is True
    # 2 快照 + 1 引擎批次；仅标题来源计入快照边界调用，但不作为证据。
    assert result.usage["tool_calls"] == 3


def test_deep_workflow_local_chunks_enter_evidence_ledger(tmp_path):
    local_chunks = (DeepLocalChunk("snap-local-1", "doc-9", {"page": 1}, "Local corpus: skills bundle instructions. " * 8),)
    workflow, store = build_workflow(tmp_path, [
        chat_response(DRAFT, "draft"),
        chat_response(VERIFY, "verify"),
        chat_response({"cards": []}, "distill"),
        chat_response(WRITER, "writer"),
        chat_response(AUDIT, "audit"),
    ], bridge=FakeBridge(sources=SOURCES[:1], local_chunks=local_chunks))

    result = workflow.execute("How do agents use skills?")

    assert result.disposition is DeliveryDisposition.COMPLETE
    assert tuple(item.evidence_id for item in result.evidence) == ("evidence-0001", "evidence-0002")
    assert result.evidence[1].source_snapshot_id == "snap-local-1"
    assert result.evidence[1].extraction_method == "deep-research-local-corpus-chunk-v1"
    manifest = json.loads(read_artifact(store, result.manifest_artifact_id))
    assert manifest["coverage"]["local_evidence_count"] == 1
    # 1 快照 + 1 本地检索 + 1 引擎批次。
    assert result.usage["tool_calls"] == 3


def test_deep_workflow_keeps_no_answer_when_no_material_at_all(tmp_path):
    workflow, store = build_workflow(tmp_path, [], bridge=FakeBridge(sources=(), report_markdown=""))

    result = workflow.execute("How do agents use skills?")

    assert result.disposition is DeliveryDisposition.NO_ANSWER
    assert result.claims == () and result.evidence == ()
    manifest = json.loads(read_artifact(store, result.manifest_artifact_id))
    assert manifest["report_contract"] == "no_answer"
    assert result.usage["tool_calls"] == result.provider_call_count == 1


def test_durable_validation_accepts_partial_unverified_execution(tmp_path):
    from conflux_weave.runtime.durable_research import DurableResearchExecution, DurableResearchRuntime
    from conflux_weave.runtime.sqlite import BudgetAmount

    store = LocalArtifactStore(tmp_path / "artifacts")
    report = store.put_bytes(b"# unverified", media_type="text/markdown; charset=utf-8", producer_step_id="t", schema_version="r")
    manifest = store.put_json({}, producer_step_id="t", schema_version="m")
    execution = DurableResearchExecution(
        report_artifact_id=report.artifact_id,
        manifest_artifact_id=manifest.artifact_id,
        evidence_refs=(),
        evidence_records=(),
        usage=BudgetAmount(input_tokens=10, output_tokens=10, tool_calls=1, retrieval_rounds=1),
        provider_call_count=1,
        disposition=DeliveryDisposition.PARTIAL,
        limitations=("unverified view",),
        unmet_criteria=("no verified claims",),
    )
    # _validate_execution 只读 artifact_store 与 _artifact_digest；用最小替身避免拉起整个运行时。
    stub = SimpleNamespace(artifact_store=store, _artifact_digest=DurableResearchRuntime._artifact_digest)
    DurableResearchRuntime._validate_execution(stub, execution)


def test_token_tracker_registers_on_sync_and_async_callback_lists():
    import os

    # litellm 在 import 时会 load_dotenv（把仓库 .env 灌进 os.environ），
    # 快照必须先于 import，测完恢复，避免污染后续测试。
    snapshot = os.environ.copy()

    import litellm

    from conflux_weave.deep_research import _TokenTracker

    tracker = _TokenTracker()
    try:
        assert tracker._record in litellm.success_callback
        assert tracker._record in litellm._async_success_callback
        response = SimpleNamespace(usage=SimpleNamespace(prompt_tokens=11, completion_tokens=7))
        tracker._record({}, response, None, None)
        counts = tracker.consume()
        assert counts == {"input_tokens": 11, "output_tokens": 7, "calls": 1}
        assert tracker._record not in litellm.success_callback
        assert tracker._record not in litellm._async_success_callback
    finally:
        tracker.consume()
        os.environ.clear()
        os.environ.update(snapshot)


def test_bridge_usage_probe_accumulates_engine_tokens():
    from gpt_researcher.llm_provider.generic.base import GenericLLMProvider

    from conflux_weave.deep_research import GPTResearcherBridge

    engine_usage = {"input_tokens": 0, "output_tokens": 0, "calls": 0}
    original = GPTResearcherBridge._install_usage_probe(engine_usage)
    try:
        provider = GenericLLMProvider(None)
        provider._capture_response_metadata(SimpleNamespace(
            usage_metadata={"input_tokens": 5, "output_tokens": 2, "total_tokens": 7},
            response_metadata=None,
        ))
        assert engine_usage == {"input_tokens": 5, "output_tokens": 2, "calls": 1}
    finally:
        GPTResearcherBridge._uninstall_usage_probe(original)
    assert GenericLLMProvider._capture_response_metadata is original


def test_executor_adapter_dispatches_deep_research(tmp_path):
    from conflux_weave.runtime.durable_research import (
        DEEP_RESEARCH_TASK,
        VerifiedWorkflowExecutorAdapter,
    )

    workflow, store = build_workflow(tmp_path, [
        chat_response(DRAFT, "draft"),
        chat_response(VERIFY, "verify"),
        chat_response({"cards": []}, "distill"),
        chat_response(WRITER, "writer"),
        chat_response(AUDIT, "audit"),
    ])
    adapter = VerifiedWorkflowExecutorAdapter(store, verified_workflow=None, deep_workflow=workflow)

    execution = adapter(DEEP_RESEARCH_TASK, "How do agents use skills and memory?", 4)

    assert execution.usage.tool_calls == execution.provider_call_count == 3
    assert execution.evidence_refs == ("evidence-0001", "evidence-0002")


def test_deep_research_endpoint_submits_expected_kind(tmp_path):
    from types import SimpleNamespace

    from conflux_weave.api_contracts import DeepResearchTaskRequest
    from conflux_weave.server import create_app

    captured = {}

    class StubOrchestrator:
        def submit(self, submission):
            captured["submission"] = submission
            return SimpleNamespace(task_id="task-1", run_id="run-1", created=True)

    app = create_app(SimpleNamespace(), StubOrchestrator())
    submit = next(item.endpoint for item in app.routes if item.path == "/api/v1/tasks/deep-research")

    # 端点为 async def：显式驱动；内部 get_run 未知 run 走错误响应，但提交已被捕获。
    import asyncio

    asyncio.run(submit(DeepResearchTaskRequest(objective="研究问题")))

    assert captured["submission"].task_kind == "deep_research"
    assert captured["submission"].input["objective"] == "研究问题"
