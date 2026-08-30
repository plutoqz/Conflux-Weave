import json
from pathlib import Path
from types import SimpleNamespace

from conflux_weave.api_contracts import DeepResearchTaskRequest
from conflux_weave.core import BudgetLedger, DeliveryDisposition
from conflux_weave.deep_research import (
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
SOURCES = (
    DeepSource("https://a.example/skill", "Source A", "Agents use skills to act on the world."),
    DeepSource("https://b.example/memory", "Source B", "Memory writes are deduplicated by content hash."),
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
    def __init__(self, sources=SOURCES):
        self.sources = sources
        self.calls = []

    def execute(self, objective, *, on_progress=None):
        self.calls.append(objective)
        return DeepResearchResult(
            sources=self.sources,
            context="context",
            report_markdown="# raw engine report",
            planned_queries=("q1", "q2"),
            costs_usd=0.05,
            token_usage={"input_tokens": 1200, "output_tokens": 400, "calls": 6},
            report_source="hybrid",
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
    manifest = json.loads(store.path_for_digest(result.manifest_artifact_id.removeprefix("artifact-sha256-")).read_text(encoding="utf-8"))
    assert manifest["report_contract"] == "v2"
    assert len(manifest["sources"]) == 2
    assert manifest["sources"][0]["url"] == "https://a.example/skill"
    assert manifest["sources"][0]["content_hash"].startswith("sha256-")
    report = store.path_for_digest(result.report_artifact_id.removeprefix("artifact-sha256-")).read_text(encoding="utf-8")
    assert "## 回答摘要" in report and "https://a.example/skill" in report
    raw = store.path_for_digest(manifest["raw_report_artifact"].removeprefix("artifact-sha256-")).read_text(encoding="utf-8")
    assert "raw engine report" in raw


def test_deep_workflow_returns_no_answer_when_nothing_verified(tmp_path):
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

    assert result.disposition is DeliveryDisposition.NO_ANSWER
    assert result.claims == ()
    manifest = json.loads(store.path_for_digest(result.manifest_artifact_id.removeprefix("artifact-sha256-")).read_text(encoding="utf-8"))
    assert manifest["report_contract"] == "no_answer"
    assert manifest["provider_call_count"] == 3  # 2 快照 + 1 引擎批次


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
