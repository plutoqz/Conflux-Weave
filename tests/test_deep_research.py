import json
import sys
import threading
import time
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


class ParallelRoutingTransport(SequenceTransport):
    """Thread-safe fixture transport that routes the two parallel prep calls."""

    supports_concurrent_requests = True

    def __init__(self, payloads):
        super().__init__(payloads)
        self._lock = threading.Lock()
        self._active = 0
        self.max_active = 0

    def post(self, *args, **kwargs):
        body = json.loads(kwargs["body"])
        system = body["messages"][0]["content"]
        if "evidence-merge planner" in system or "bilingual research distiller" in system:
            with self._lock:
                self.requests.append(body)
                self._active += 1
                self.max_active = max(self.max_active, self._active)
            try:
                time.sleep(0.05)
                payload = MERGE if "evidence-merge planner" in system else {"cards": []}
                return ProviderHttpResponse(200, json.dumps(chat_response(payload, "parallel")).encode(), {"Content-Type": "application/json"})
            finally:
                with self._lock:
                    self._active -= 1
        return super().post(*args, **kwargs)


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

    def local_document_title(self, document_id, fallback):
        for chunk in self.local_chunks:
            if chunk.document_id == document_id:
                return chunk.text.splitlines()[0].strip() or fallback
        return fallback


def build_workflow(tmp_path, chat_payloads, bridge=None, transport=None):
    store = LocalArtifactStore(tmp_path / "artifacts")
    config = ProviderConfig("https://provider.example/v1", "secret", "chat")
    chat = OpenAICompatibleChatAdapter(store, config, transport=transport or SequenceTransport(chat_payloads))
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


def test_gpt_researcher_bridge_passes_configured_model_to_all_roles(monkeypatch, tmp_path):
    captured = {}

    class FakeResearcher:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            captured["config"] = json.loads(Path(kwargs["config_path"]).read_text(encoding="utf-8"))

        async def conduct_research(self, on_progress=None):
            return None

        async def write_report(self):
            return "# report"

        def get_research_context(self):
            return "context"

        def get_research_sources(self):
            return []

        def get_costs(self):
            return 0

    monkeypatch.setitem(sys.modules, "gpt_researcher", SimpleNamespace(GPTResearcher=FakeResearcher))
    config = ProviderConfig("https://provider.example/v1", "secret", "glm-5.3-flash")

    result = GPTResearcherBridge(config).execute("test objective")

    assert result.report_markdown == "# report"
    assert captured["config"]["FAST_LLM"] == "openai:glm-5.3-flash"
    assert captured["config"]["SMART_LLM"] == "openai:glm-5.3-flash"
    assert captured["config"]["STRATEGIC_LLM"] == "openai:glm-5.3-flash"


def test_gpt_researcher_bridge_uses_engine_model_for_llm_roles(monkeypatch, tmp_path):
    captured = {}

    class FakeResearcher:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            captured["config"] = json.loads(Path(kwargs["config_path"]).read_text(encoding="utf-8"))

        async def conduct_research(self, on_progress=None):
            return None

        async def write_report(self):
            return "# report"

        def get_research_context(self):
            return "context"

        def get_research_sources(self):
            return []

        def get_costs(self):
            return 0

    monkeypatch.setitem(sys.modules, "gpt_researcher", SimpleNamespace(GPTResearcher=FakeResearcher))
    config = ProviderConfig(
        "https://provider.example/v1", "secret", "glm-5.3-flash",
        engine_model="deepseek-v4-flash-0731",
    )

    GPTResearcherBridge(config).execute("test objective")

    # 三个引擎角色走 engine_model；embedding 保持在 provider 模型上
    assert captured["config"]["FAST_LLM"] == "openai:deepseek-v4-flash-0731"
    assert captured["config"]["SMART_LLM"] == "openai:deepseek-v4-flash-0731"
    assert captured["config"]["STRATEGIC_LLM"] == "openai:deepseek-v4-flash-0731"


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


DRAFT3 = {"claims": [
    {"text": "Agents use skills to act on the world.", "evidence_ids": ["evidence-0001"]},
    {"text": "Memory writes are deduplicated by content hash.", "evidence_ids": ["evidence-0002"]},
    {"text": "Local corpus bundles skills into reusable instructions.", "evidence_ids": ["evidence-0003"]},
]}
VERIFY3 = {"assessments": [
    {"claim_id": "claim-0001", "evidence_ids": ["evidence-0001"], "relation": "supports", "verdict": "accepted", "rationale": "Direct."},
    {"claim_id": "claim-0002", "evidence_ids": ["evidence-0002"], "relation": "supports", "verdict": "accepted", "rationale": "Direct."},
    {"claim_id": "claim-0003", "evidence_ids": ["evidence-0003"], "relation": "supports", "verdict": "accepted", "rationale": "Direct."},
]}
DISTILL3 = {"cards": [
    {"evidence_id": f"evidence-{index:04d}", "zh_summary": f"事实{index}",
     "zh_key_points": [f"事实{index}"], "terms": [], "scope_limits": ""}
    for index in range(1, 4)
]}
ENGINE_REPORT = """# 智能体 Skill 机制研究报告

## 封装形态
技能以文件夹为单位封装程序性知识，包含说明与脚本。
### 形态细节
技能包由指令与资源组成。

## 生效条件
技能只有在模型缺少相关知识且检索命中时才产生收益。
"""
MERGE = {"thesis": "技能机制以封装包与按需加载生效，收益取决于知识缺口与检索命中。", "assignments": [
    {"section_index": 0, "paragraph_index": 0, "web_source_ids": ["web-0001"],
     "claims": [{"claim_id": "claim-0001", "relation": "supports"}]},
    {"section_index": 1, "paragraph_index": 0, "web_source_ids": ["web-0002"],
     "claims": [{"claim_id": "claim-0002", "relation": "qualifies"}]},
], "research_space": ["claim-0003"], "dropped": []}
WRITER_FUSED = {"sections": [
    {"heading": "一、封装形态", "paragraphs": [
        {"text": "技能以文件夹为单位封装程序性知识，本地语料同样将技能组织为可复用指令包。",
         "claim_ids": ["claim-0001"], "web_source_ids": ["web-0001"]},
        {"text": "**形态细节**", "claim_ids": [], "web_source_ids": ["web-0001"]},
        {"text": "技能包由指令与资源组成。", "claim_ids": [], "web_source_ids": ["web-0001"]},
    ]},
    {"heading": "二、生效条件", "paragraphs": [
        {"text": "技能只有在模型缺少相关知识且检索命中时才产生收益。",
         "claim_ids": ["claim-0002"], "web_source_ids": ["web-0002"]},
    ]},
], "open_questions": ["多轮真实场景下技能机制的效果缺乏证据。"]}
AUDIT_FUSED = {"audits": [
    {"section_index": 0, "paragraph_index": 0, "verdict": "supported", "rationale": "Thesis restates claims."},
    {"section_index": 1, "paragraph_index": 0, "verdict": "supported", "rationale": "Restates claim and source."},
    {"section_index": 1, "paragraph_index": 1, "verdict": "supported", "rationale": "Subheading from source."},
    {"section_index": 1, "paragraph_index": 2, "verdict": "supported", "rationale": "Restates source."},
    {"section_index": 2, "paragraph_index": 0, "verdict": "supported", "rationale": "Restates claim and source."},
]}


def test_deep_workflow_fuses_local_evidence_into_engine_skeleton(tmp_path):
    local_chunks = (DeepLocalChunk(
        "snap-local-1", "doc-9", {"page": 1},
        "SkillCenter 相关工作综述\nLocal corpus: skills bundle instructions. " * 4,
    ),)
    workflow, store = build_workflow(tmp_path, [
        chat_response(DRAFT3, "draft"),
        chat_response(VERIFY3, "verify"),
        chat_response(MERGE, "merge"),
        chat_response(DISTILL3, "distill"),
        chat_response(WRITER_FUSED, "writer"),
        chat_response(AUDIT_FUSED, "audit"),
    ], bridge=FakeBridge(sources=SOURCES, local_chunks=local_chunks, report_markdown=ENGINE_REPORT))

    result = workflow.execute("How do agents use skills and memory?")

    assert result.disposition is DeliveryDisposition.COMPLETE
    manifest = json.loads(read_artifact(store, result.manifest_artifact_id))
    assert manifest["delivery_shape"] == "engine-fused"
    assert manifest["merge"]["status"] == "ok"
    assert manifest["merge"]["research_space_count"] == 1
    report = read_artifact(store, result.report_artifact_id)
    # 引擎骨架即正文主线，不再是附录
    assert "## 一、封装形态" in report and "## 二、生效条件" in report
    assert "## 附录" not in report
    # 问题与来源说明：总体结论 + 车道图例
    assert "总体结论：技能机制以封装包与按需加载生效" in report
    assert "[web] 网络来源" in report and "[本地] 本地语料" in report
    # 无段落对应的本地结论进入研究空间
    assert "## 可以进一步探索的问题或者研究空间" in report
    assert "Local corpus bundles skills into reusable instructions." in report
    # 紧凑来源引用：标题+车道+链接/页码，无快照 id/JSON 定位
    assert "[1](https://a.example/skill) Source A[web]" in report
    assert "《SkillCenter 相关工作综述》[本地], 第1页" in report
    assert "SourceSnapshot" not in report and "document-sha256" not in report
    draft_material = json.loads(workflow.chat.transport.requests[0]["messages"][1]["content"])
    assert draft_material["evidence"][0]["evidence_id"] == "evidence-0003"
    assert draft_material["evidence"][0]["origin"] == "local"
    audit_request = next(
        request for request in workflow.chat.transport.requests
        if "faithfulness auditor for a fused research report" in request["messages"][0]["content"]
    )
    audit_material = json.loads(audit_request["messages"][1]["content"])
    assert audit_material["engine_outline"][0]["heading"] == "一、封装形态"
    assert audit_material["merge_plan"]["thesis"] == MERGE["thesis"]


def test_deep_workflow_parallelizes_merge_and_distill_when_transport_allows(tmp_path):
    local_chunks = (DeepLocalChunk(
        "snap-local-1", "doc-9", {"page": 1},
        "SkillCenter 相关工作综述\nLocal corpus: skills bundle instructions. " * 4,
    ),)
    transport = ParallelRoutingTransport([
        chat_response(DRAFT3, "draft"),
        chat_response(VERIFY3, "verify"),
        chat_response(WRITER_FUSED, "writer"),
        chat_response(AUDIT_FUSED, "audit"),
    ])
    workflow, store = build_workflow(
        tmp_path,
        [],
        bridge=FakeBridge(sources=SOURCES, local_chunks=local_chunks, report_markdown=ENGINE_REPORT),
        transport=transport,
    )

    result = workflow.execute("How do agents use skills and memory?")

    manifest = json.loads(read_artifact(store, result.manifest_artifact_id))
    assert result.disposition is DeliveryDisposition.COMPLETE
    assert manifest["parallel_mode"] == "parallel"
    assert manifest["timings_ms"]["distill"] >= 0
    assert manifest["timings_ms"]["merge_distill_wall"] >= 0
    assert transport.max_active >= 2
    assert manifest["delivery_shape"] == "engine-fused"


WRITER3 = {"summary": {"text": "- **技能决定执行，记忆决定状态**：分工明确。", "claim_ids": ["claim-0001", "claim-0002"]}, "sections": [
    {"heading": "一、技能与记忆的分工", "paragraphs": [{"text": "技能用于动作执行，记忆按内容哈希去重。", "claim_ids": ["claim-0001", "claim-0002"]}]},
    {"heading": "二、本地语料中的技能封装", "paragraphs": [{"text": "本地语料把技能封装为可复用指令。", "claim_ids": ["claim-0003"]}]},
], "background": [], "open_questions": ["技能与记忆的性能开销缺少量化数据。"]}
AUDIT3 = {"audits": [
    {"section_index": 0, "paragraph_index": 0, "verdict": "supported", "rationale": "Summary restates claims."},
    {"section_index": 1, "paragraph_index": 0, "verdict": "supported", "rationale": "Paragraph restates claims."},
    {"section_index": 2, "paragraph_index": 0, "verdict": "supported", "rationale": "Paragraph restates claim."},
]}


def test_deep_workflow_merge_degrades_to_flat_report(tmp_path):
    local_chunks = (DeepLocalChunk(
        "snap-local-1", "doc-9", {"page": 1},
        "SkillCenter 相关工作综述\nLocal corpus: skills bundle instructions. " * 4,
    ),)
    workflow, store = build_workflow(tmp_path, [
        chat_response(DRAFT3, "draft"),
        chat_response(VERIFY3, "verify"),
        chat_response("not-json", "merge-bad"),
        chat_response("still-bad", "merge-bad2"),
        chat_response({"cards": []}, "distill"),
        chat_response(WRITER3, "writer"),
        chat_response(AUDIT3, "audit"),
    ], bridge=FakeBridge(sources=SOURCES, local_chunks=local_chunks, report_markdown=ENGINE_REPORT))

    result = workflow.execute("How do agents use skills and memory?")

    assert result.disposition is DeliveryDisposition.COMPLETE
    manifest = json.loads(read_artifact(store, result.manifest_artifact_id))
    assert manifest["delivery_shape"] == "flat"
    assert manifest["merge"]["status"] == "degraded"
    assert len(workflow.chat.transport.requests) == 8  # 规划最多 2 次 + 失败批次重试 + 起草核验写作审计
    report = read_artifact(store, result.report_artifact_id)
    assert "## 附录：聚合引擎综合视图（未经本地核验）" in report
    assert any("证据融合规划" in item for item in result.limitations)


def test_deep_workflow_fused_writer_falls_back_to_deterministic_fusion(tmp_path):
    local_chunks = (DeepLocalChunk(
        "snap-local-1", "doc-9", {"page": 1},
        "SkillCenter 相关工作综述\nLocal corpus: skills bundle instructions. " * 4,
    ),)
    wrong = {"sections": [
        {"heading": "一、错误标题", "paragraphs": [
            {"text": "内容。", "claim_ids": ["claim-0001"], "web_source_ids": ["web-0001"]},
        ]},
    ], "open_questions": []}
    workflow, store = build_workflow(tmp_path, [
        chat_response(DRAFT3, "draft"),
        chat_response(VERIFY3, "verify"),
        chat_response(MERGE, "merge"),
        chat_response({"cards": []}, "distill"),
        chat_response(wrong, "writer-1"),
        chat_response(wrong, "writer-2"),
        chat_response(wrong, "writer-3"),
    ], bridge=FakeBridge(sources=SOURCES, local_chunks=local_chunks, report_markdown=ENGINE_REPORT))

    result = workflow.execute("How do agents use skills and memory?")

    assert result.disposition is DeliveryDisposition.COMPLETE
    manifest = json.loads(read_artifact(store, result.manifest_artifact_id))
    assert manifest["delivery_shape"] == "engine-fused"
    assert manifest["writer_status"] == "fallback"
    report = read_artifact(store, result.report_artifact_id)
    # 确定性融合组装：引擎段落原文保留，匹配 Claim 逐字嵌入
    assert "## 一、封装形态" in report and "## 二、生效条件" in report
    assert "技能以文件夹为单位封装程序性知识，包含说明与脚本。" in report
    assert "Agents use skills to act on the world." in report
    assert "Memory writes are deduplicated by content hash." in report
    assert "核验结果" in report
    assert "模型融合写作未通过校验" not in report
    assert any("确定性融合组装" in item for item in result.limitations)


def test_tavily_adapter_overrides_endpoint_and_adds_bearer(monkeypatch):
    import os

    from gpt_researcher.retrievers.tavily.tavily_search import TavilySearch

    monkeypatch.setenv("TAVILY_BASE_URL", "https://gateway.example/tavily/search")
    original = GPTResearcherBridge._install_tavily_adapter()
    try:
        searcher = TavilySearch("query")
        assert searcher.base_url == "https://gateway.example/tavily/search"
        assert searcher.headers["Authorization"].startswith("Bearer ")
        # 卸载后恢复硬编码端点
        GPTResearcherBridge._uninstall_tavily_adapter(original)
        restored = TavilySearch("query")
        assert restored.base_url == "https://api.tavily.com/search"
    finally:
        GPTResearcherBridge._uninstall_tavily_adapter(original)
