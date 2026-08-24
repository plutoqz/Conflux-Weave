import json

import pytest

from conflux_weave.core import DeliveryDisposition, RunStatus
from conflux_weave.live_research import LiveResearchValidationError
from conflux_weave.paper_discovery import (
    ArxivHttpResponse,
    ArxivSearchAdapter,
    FixedPaperDiscoveryWorkflow,
    PaperSearchError,
)
from conflux_weave.provider import (
    OpenAICompatibleChatAdapter,
    ProviderConfig,
    ProviderHttpResponse,
)
from conflux_weave.runtime import LocalArtifactStore


ATOM_FIXTURE = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/2608.00001v1</id>
    <updated>2026-08-01T00:00:00Z</updated>
    <published>2026-08-01T00:00:00Z</published>
    <title>Unrelated Vision Benchmark</title>
    <summary>A benchmark for image segmentation.</summary>
    <author><name>A. Author</name></author>
    <link href="http://arxiv.org/abs/2608.00001v1" rel="alternate" />
    <link title="pdf" href="http://arxiv.org/pdf/2608.00001v1" rel="related" />
    <category term="cs.CV" />
  </entry>
  <entry>
    <id>http://arxiv.org/abs/2608.00002v1</id>
    <updated>2026-08-02T00:00:00Z</updated>
    <published>2026-08-02T00:00:00Z</published>
    <title>Context Management for Language Model Agents</title>
    <summary>Methods for managing context in long-running LLM agents.</summary>
    <author><name>B. Author</name></author>
    <link href="http://arxiv.org/abs/2608.00002v1" rel="alternate" />
    <link title="pdf" href="http://arxiv.org/pdf/2608.00002v1" rel="related" />
    <category term="cs.AI" />
  </entry>
</feed>
"""


class ArxivTransport:
    def __init__(self, response=None, error=None):
        self.response = response or ArxivHttpResponse(
            200, ATOM_FIXTURE, {"Content-Type": "application/atom+xml"}
        )
        self.error = error
        self.urls = []

    def get(self, url, *, headers, timeout_seconds):
        self.urls.append(url)
        if self.error:
            raise self.error
        return self.response


class ProviderTransport:
    def __init__(self, content):
        self.content = content
        self.calls = 0
        self.requests = []

    def post(self, url, *, headers, body, timeout_seconds):
        self.calls += 1
        self.requests.append(json.loads(body))
        response = {
            "id": "chatcmpl-paper-fixture",
            "model": "fixture-model",
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": json.dumps(self.content, ensure_ascii=False),
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 120,
                "completion_tokens": 40,
                "total_tokens": 160,
            },
        }
        return ProviderHttpResponse(
            200,
            json.dumps(response, ensure_ascii=False).encode(),
            {"Content-Type": "application/json"},
        )


def fixed_clock():
    return "2026-08-24T00:00:00Z"


def stable_id(prefix):
    return f"{prefix}-fixed"


def build_workflow(tmp_path, content):
    store = LocalArtifactStore(tmp_path / "artifacts")
    arxiv_transport = ArxivTransport()
    search = ArxivSearchAdapter(
        store, transport=arxiv_transport, acquired_at=fixed_clock()
    )
    provider_transport = ProviderTransport(content)
    provider = OpenAICompatibleChatAdapter(
        store,
        ProviderConfig(
            "https://provider.example/v1", "fixture-secret", "fixture-model"
        ),
        transport=provider_transport,
    )
    workflow = FixedPaperDiscoveryWorkflow(
        store,
        search,
        provider,
        clock=fixed_clock,
        id_factory=stable_id,
        code_revision="fixture-revision",
    )
    return workflow, store, arxiv_transport, provider_transport


def test_arxiv_search_parses_feed_and_retains_raw_response(tmp_path):
    store = LocalArtifactStore(tmp_path / "artifacts")
    result = ArxivSearchAdapter(
        store, transport=ArxivTransport(), acquired_at=fixed_clock()
    ).search("all:agent", max_results=2)

    assert [paper.arxiv_id for paper in result.papers] == [
        "2608.00001v1",
        "2608.00002v1",
    ]
    assert result.papers[1].pdf_url == "http://arxiv.org/pdf/2608.00002v1"
    assert store.read_bytes(result.response_artifact) == ATOM_FIXTURE
    manifest = json.loads(store.read_bytes(result.manifest_artifact))
    assert manifest["paper_count"] == 2
    assert "max_results=2" in manifest["request_url"]


def test_paper_workflow_reranks_and_compiles_readable_closed_delivery(tmp_path):
    workflow, store, _, provider_transport = build_workflow(
        tmp_path,
        {
            "claims": [
                {
                    "text": "《Context Management for Language Model Agents》（2026）直接研究长时 LLM Agent 的上下文管理。",
                    "evidence_ids": ["arxiv-paper-01"],
                }
            ]
        },
    )

    result = workflow.execute(
        "查找 Agent 上下文管理论文",
        search_query="agent context management",
        max_results=2,
    )

    assert result.final_run.status is RunStatus.PARTIAL
    assert result.delivery.disposition is DeliveryDisposition.PARTIAL
    assert [paper.arxiv_id for paper in result.selected_papers] == ["2608.00002v1"]
    assert provider_transport.calls == 1
    user_prompt = provider_transport.requests[0]["messages"][1]["content"]
    assert "Evidence 边界和其他限制由工作流确定性生成" in user_prompt
    assert "不要输出 limitations" in user_prompt
    assert "保留预印本边界" not in user_prompt
    assert len(result.claims) == len(result.citations) == 1
    report = store.read_bytes(result.report_artifact).decode()
    assert "### ◐ G 候选论文及相关性" in report
    assert "（2026）直接研究" in report
    assert "（2026）直接研究" in report.split("## Evidence 汇总")[0]
    assert "[1]" not in report.split("## Evidence 汇总")[0]
    assert "## Evidence 汇总" in report
    manifest = store.read_bytes(result.manifest_artifact)
    assert b"fixture-secret" not in manifest
    assert json.loads(manifest)["usage"]["total_tokens"] == 160


def test_paper_workflow_rejects_unknown_evidence_and_keeps_provider_response(tmp_path):
    workflow, store, _, _ = build_workflow(
        tmp_path,
        {
            "claims": [
                {"text": "unsupported", "evidence_ids": ["model-memory"]}
            ]
        },
    )

    with pytest.raises(LiveResearchValidationError) as captured:
        workflow.execute("papers", search_query="agent context", max_results=2)

    assert captured.value.request_artifact_ref
    assert captured.value.response_artifact_ref
    assert captured.value.artifact_ref
    digest = captured.value.response_artifact_ref.removeprefix("artifact-sha256-")
    assert store.path_for_digest(digest).is_file()
    failure_digest = captured.value.artifact_ref.removeprefix("artifact-sha256-")
    failure = json.loads(store.path_for_digest(failure_digest).read_bytes())
    assert failure["status"] == "failed"
    assert failure["error_code"] == "live_output_invalid"
    assert failure["provider_response_artifact_ref"] == captured.value.response_artifact_ref


def test_paper_workflow_rejects_unverified_peer_review_status(tmp_path):
    workflow, _, _, provider_transport = build_workflow(
        tmp_path,
        {
            "claims": [
                {
                    "text": "这些论文尚未经历正式同行评审。",
                    "evidence_ids": ["arxiv-paper-01"],
                }
            ]
        },
    )

    with pytest.raises(LiveResearchValidationError) as captured:
        workflow.execute("papers", search_query="agent context", max_results=2)

    assert captured.value.code == "paper_publication_status_unsupported"
    assert provider_transport.calls == 1
    assert captured.value.response_artifact_ref
    assert captured.value.artifact_ref


def test_paper_workflow_rejects_model_generated_limitations(tmp_path):
    workflow, _, _, provider_transport = build_workflow(
        tmp_path,
        {
            "claims": [
                {
                    "text": "Context Management directly studies agent context.",
                    "evidence_ids": ["arxiv-paper-01"],
                }
            ],
            "limitations": ["Only abstracts were searched."],
        },
    )

    with pytest.raises(LiveResearchValidationError) as captured:
        workflow.execute("papers", search_query="agent context", max_results=2)

    assert captured.value.code == "paper_output_schema_invalid"
    assert provider_transport.calls == 1
    assert captured.value.artifact_ref


def test_arxiv_http_and_xml_failures_retain_raw_artifact(tmp_path):
    store = LocalArtifactStore(tmp_path / "artifacts")
    http_adapter = ArxivSearchAdapter(
        store,
        transport=ArxivTransport(
            ArxivHttpResponse(503, b"unavailable", {"Content-Type": "text/plain"})
        ),
        acquired_at=fixed_clock(),
    )
    with pytest.raises(PaperSearchError) as http_failure:
        http_adapter.search("agent")
    assert http_failure.value.artifact_ref

    xml_adapter = ArxivSearchAdapter(
        store,
        transport=ArxivTransport(
            ArxivHttpResponse(200, b"<feed>", {"Content-Type": "application/xml"})
        ),
        acquired_at=fixed_clock(),
    )
    with pytest.raises(PaperSearchError) as xml_failure:
        xml_adapter.search("agent")
    assert xml_failure.value.artifact_ref
