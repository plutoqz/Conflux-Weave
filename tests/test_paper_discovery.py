import json
from pathlib import Path

import pytest

from conflux_weave.core import DeliveryDisposition, RunStatus
from conflux_weave.live_research import LiveResearchValidationError
from conflux_weave.paper_discovery import (
    ArxivResponseCache,
    ArxivHttpResponse,
    ArxivSearchAdapter,
    FixedPaperDiscoveryWorkflow,
    PaperSearchError,
    SourceAccessPolicy,
    SourceRequestGovernor,
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
FAILURE_FIXTURE = Path("tests/fixtures/s16c_contract_failures.json")


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


class SequenceArxivTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def get(self, url, *, headers, timeout_seconds):
        response = self.responses[self.calls]
        self.calls += 1
        return response


class FakeTimer:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class ProviderTransport:
    def __init__(self, content):
        self.contents = list(content) if isinstance(content, list) else [content]
        self.calls = 0
        self.requests = []

    def post(self, url, *, headers, body, timeout_seconds):
        self.calls += 1
        self.requests.append(json.loads(body))
        content = self.contents[self.calls - 1]
        response = {
            "id": f"chatcmpl-paper-fixture-{self.calls}",
            "model": "fixture-model",
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": json.dumps(content, ensure_ascii=False),
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


def build_workflow(tmp_path, content, verification=None):
    store = LocalArtifactStore(tmp_path / "artifacts")
    arxiv_transport = ArxivTransport()
    search = ArxivSearchAdapter(
        store, transport=arxiv_transport, acquired_at=fixed_clock()
    )
    if verification is None:
        raw_claims = content.get("claims", []) if isinstance(content, dict) else []
        verification = {
            "assessments": [
                {
                    "claim_id": f"paper-claim-{index:04d}",
                    "evidence_ids": item.get("evidence_ids", []),
                    "relation": "supports",
                    "verdict": "accepted",
                    "rationale": "The title and abstract directly support this bounded relevance Claim.",
                }
                for index, item in enumerate(raw_claims, 1)
            ]
        }
    provider_transport = ProviderTransport([content, verification])
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
    assert result.final_run.budget.tool_calls == 3
    assert [paper.arxiv_id for paper in result.selected_papers] == ["2608.00002v1"]
    assert provider_transport.calls == 2
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
    manifest_payload = json.loads(manifest)
    assert manifest_payload["usage"]["total_tokens"] == 320
    assert manifest_payload["candidate_claim_count"] == 1
    assert manifest_payload["rejected_claim_count"] == 0


def test_paper_workflow_filters_overstated_claim_after_independent_review(tmp_path):
    candidate = {
        "claims": [
            {
                "text": "Context Management studies long-running LLM agent context.",
                "evidence_ids": ["arxiv-paper-01"],
            },
            {
                "text": "Context Management proves lower production failure rates.",
                "evidence_ids": ["arxiv-paper-01"],
            },
        ]
    }
    verification = {
        "assessments": [
            {
                "claim_id": "paper-claim-0001",
                "evidence_ids": ["arxiv-paper-01"],
                "relation": "supports",
                "verdict": "accepted",
                "rationale": "The abstract directly describes context management for agents.",
            },
            {
                "claim_id": "paper-claim-0002",
                "evidence_ids": ["arxiv-paper-01"],
                "relation": "insufficient",
                "verdict": "rejected",
                "rationale": "The abstract contains no production failure-rate result.",
            },
        ]
    }
    workflow, store, _, provider_transport = build_workflow(
        tmp_path, candidate, verification
    )

    result = workflow.execute(
        "查找 Agent 上下文管理论文",
        search_query="agent context management",
        max_results=2,
    )

    assert provider_transport.calls == 2
    assert [item.text for item in result.claims] == [candidate["claims"][0]["text"]]
    assert len(result.citations) == len(result.evidence) == 1
    report = store.read_bytes(result.report_artifact).decode()
    assert "lower production failure rates" not in report
    assert "移除了 1 条" in report
    manifest = json.loads(store.read_bytes(result.manifest_artifact))
    assert manifest["candidate_claim_count"] == 2
    assert manifest["claim_count"] == 1
    assert manifest["rejected_claim_count"] == 1
    assert manifest["claim_assessment_artifact_ref"]


def test_paper_workflow_delivers_zero_claims_when_all_candidates_are_rejected(tmp_path):
    candidate = {
        "claims": [
            {
                "text": "Context Management proves lower production failure rates.",
                "evidence_ids": ["arxiv-paper-01"],
            }
        ]
    }
    verification = {
        "assessments": [
            {
                "claim_id": "paper-claim-0001",
                "evidence_ids": ["arxiv-paper-01"],
                "relation": "insufficient",
                "verdict": "rejected",
                "rationale": "The abstract contains no production result.",
            }
        ]
    }
    workflow, store, _, _ = build_workflow(tmp_path, candidate, verification)

    result = workflow.execute(
        "查找 Agent 上下文管理论文",
        search_query="agent context management",
        max_results=2,
    )

    assert result.claims == result.citations == result.evidence == ()
    assert result.delivery.evidence_refs == ()
    report = store.read_bytes(result.report_artifact).decode()
    assert "没有模型生成的相关性 Claim 通过" in report
    assert "lower production failure rates" not in report


def test_paper_workflow_fails_closed_when_verifier_omits_a_claim(tmp_path):
    candidate = {
        "claims": [
            {
                "text": "Context Management studies agent context.",
                "evidence_ids": ["arxiv-paper-01"],
            }
        ]
    }
    workflow, store, _, provider_transport = build_workflow(
        tmp_path, candidate, {"assessments": []}
    )

    with pytest.raises(LiveResearchValidationError) as captured:
        workflow.execute("papers", search_query="agent context", max_results=2)

    assert captured.value.code == "paper_claim_verification_invalid"
    assert provider_transport.calls == 2
    assert captured.value.request_artifact_ref
    assert captured.value.response_artifact_ref
    failure = json.loads(
        store.path_for_digest(
            captured.value.artifact_ref.removeprefix("artifact-sha256-")
        ).read_bytes()
    )
    assert failure["verification_response_artifact_ref"] == (
        captured.value.response_artifact_ref
    )


def test_paper_workflow_replays_s16c_wrong_verifier_root_and_freezes_prompt_schema(
    tmp_path,
):
    fixture = json.loads(FAILURE_FIXTURE.read_text(encoding="utf-8"))
    candidate = {
        "claims": [
            {
                "text": "Context Management studies agent context.",
                "evidence_ids": ["arxiv-paper-01"],
            }
        ]
    }
    workflow, _, _, provider_transport = build_workflow(
        tmp_path, candidate, fixture["discovery_verifier_response"]
    )

    with pytest.raises(
        LiveResearchValidationError,
        match="verification output must contain only assessments",
    ) as captured:
        workflow.execute("papers", search_query="agent context", max_results=2)

    assert captured.value.code == "paper_claim_verification_invalid"
    prompt = provider_transport.requests[1]["messages"][0]["content"]
    assert '{"assessments"' in prompt
    assert '"evidence_ids"' in prompt
    assert '"rationale"' in prompt
    assert "Do not return claims" in prompt


def test_paper_workflow_rejects_assessment_evidence_rebinding(tmp_path):
    candidate = {
        "claims": [
            {
                "text": "Context Management studies agent context.",
                "evidence_ids": ["arxiv-paper-01"],
            }
        ]
    }
    verification = {
        "assessments": [
            {
                "claim_id": "paper-claim-0001",
                "evidence_ids": ["arxiv-paper-02"],
                "relation": "supports",
                "verdict": "accepted",
                "rationale": "Attempted evidence rebinding.",
            }
        ]
    }
    workflow, _, _, _ = build_workflow(tmp_path, candidate, verification)

    with pytest.raises(LiveResearchValidationError, match="original Evidence binding"):
        workflow.execute("papers", search_query="agent context", max_results=2)


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


def test_arxiv_read_only_retry_honors_retry_after_and_retains_attempts(tmp_path):
    store = LocalArtifactStore(tmp_path / "artifacts")
    timer = FakeTimer()
    transport = SequenceArxivTransport(
        [
            ArxivHttpResponse(429, b"limited", {"Retry-After": "4"}),
            ArxivHttpResponse(503, b"unavailable", {}),
            ArxivHttpResponse(200, ATOM_FIXTURE, {"Content-Type": "application/atom+xml"}),
        ]
    )
    policy = SourceAccessPolicy(
        min_interval_seconds=3,
        max_attempts=3,
        retry_base_seconds=2,
        max_retry_wait_seconds=20,
        cache_ttl_seconds=0,
    )
    adapter = ArxivSearchAdapter(
        store,
        transport=transport,
        acquired_at=fixed_clock(),
        policy=policy,
        governor=SourceRequestGovernor(
            policy.min_interval_seconds,
            monotonic=timer.monotonic,
            sleep=timer.sleep,
        ),
        sleep=timer.sleep,
    )

    result = adapter.search("agent recovery", max_results=2)

    assert transport.calls == 3
    assert timer.sleeps == [4.0, 4]
    assert result.attempt_count == 3
    assert result.retry_delays == (4.0, 4)
    manifest = json.loads(store.read_bytes(result.manifest_artifact))
    assert manifest["automatic_retry"] is True
    assert manifest["retry_scope"] == "read_only_source_get"
    assert len(manifest["attempt_artifact_refs"]) == 3


def test_arxiv_retry_exhaustion_retains_failure_manifest(tmp_path):
    store = LocalArtifactStore(tmp_path / "artifacts")
    timer = FakeTimer()
    transport = SequenceArxivTransport(
        [ArxivHttpResponse(429, f"limited-{index}".encode(), {}) for index in range(3)]
    )
    policy = SourceAccessPolicy(
        min_interval_seconds=0,
        max_attempts=3,
        retry_base_seconds=1,
        max_retry_wait_seconds=10,
        cache_ttl_seconds=0,
    )
    adapter = ArxivSearchAdapter(
        store,
        transport=transport,
        policy=policy,
        governor=SourceRequestGovernor(0, monotonic=timer.monotonic, sleep=timer.sleep),
        sleep=timer.sleep,
    )

    with pytest.raises(PaperSearchError) as captured:
        adapter.search("rate limited")

    error = captured.value
    assert transport.calls == 3
    assert error.status_code == 429
    assert error.retry_delays == (1, 2)
    assert len(error.attempt_artifact_refs) == 3
    digest = error.artifact_ref.removeprefix("artifact-sha256-")
    failure = json.loads(store.path_for_digest(digest).read_bytes())
    assert failure["automatic_retry"] is True
    assert failure["retry_scope"] == "read_only_source_get"
    assert len(failure["attempt_artifact_refs"]) == 3


def test_arxiv_success_cache_reuses_snapshot_without_network(tmp_path):
    store = LocalArtifactStore(tmp_path / "artifacts")
    transport = ArxivTransport()
    cache = ArxivResponseCache(
        tmp_path / "source-cache", ttl_seconds=60, wall_time=lambda: 10
    )
    policy = SourceAccessPolicy(
        min_interval_seconds=0,
        max_attempts=1,
        retry_base_seconds=0,
        max_retry_wait_seconds=0,
        cache_ttl_seconds=60,
    )
    adapter = ArxivSearchAdapter(
        store,
        transport=transport,
        acquired_at=fixed_clock(),
        policy=policy,
        governor=SourceRequestGovernor(0),
        cache=cache,
    )

    first = adapter.search("cached agent", max_results=2)
    second = adapter.search("cached agent", max_results=2)

    assert len(transport.urls) == 1
    assert first.cache_hit is False
    assert second.cache_hit is True
    assert second.attempt_count == 0
    assert first.snapshot.content_hash == second.snapshot.content_hash
    assert first.snapshot.acquired_at == second.snapshot.acquired_at


def test_arxiv_invalid_xml_does_not_poison_success_cache(tmp_path):
    store = LocalArtifactStore(tmp_path / "artifacts")
    transport = SequenceArxivTransport(
        [
            ArxivHttpResponse(200, b"<feed>", {"Content-Type": "application/xml"}),
            ArxivHttpResponse(200, ATOM_FIXTURE, {"Content-Type": "application/xml"}),
        ]
    )
    cache = ArxivResponseCache(tmp_path / "cache", ttl_seconds=60)
    policy = SourceAccessPolicy(
        min_interval_seconds=0,
        max_attempts=1,
        retry_base_seconds=0,
        max_retry_wait_seconds=0,
        cache_ttl_seconds=60,
    )
    adapter = ArxivSearchAdapter(
        store,
        transport=transport,
        policy=policy,
        governor=SourceRequestGovernor(0),
        cache=cache,
    )

    with pytest.raises(PaperSearchError):
        adapter.search("valid after malformed")
    result = adapter.search("valid after malformed")

    assert transport.calls == 2
    assert result.cache_hit is False


def test_arxiv_governor_spaces_distinct_queries(tmp_path):
    store = LocalArtifactStore(tmp_path / "artifacts")
    timer = FakeTimer()
    policy = SourceAccessPolicy(
        min_interval_seconds=3,
        max_attempts=1,
        retry_base_seconds=0,
        max_retry_wait_seconds=0,
        cache_ttl_seconds=0,
    )
    governor = SourceRequestGovernor(
        3, monotonic=timer.monotonic, sleep=timer.sleep
    )
    adapter = ArxivSearchAdapter(
        store,
        transport=ArxivTransport(),
        policy=policy,
        governor=governor,
        sleep=timer.sleep,
    )

    adapter.search("first", max_results=2)
    adapter.search("second", max_results=2)

    assert timer.sleeps == [3.0]


def test_arxiv_does_not_start_retry_after_cancellation(tmp_path):
    store = LocalArtifactStore(tmp_path / "artifacts")
    retry_allowed = True

    class CancellingTransport:
        calls = 0

        def get(self, url, *, headers, timeout_seconds):
            nonlocal retry_allowed
            self.calls += 1
            retry_allowed = False
            return ArxivHttpResponse(429, b"limited", {"Retry-After": "3"})

    transport = CancellingTransport()
    policy = SourceAccessPolicy(
        min_interval_seconds=0,
        max_attempts=3,
        retry_base_seconds=1,
        max_retry_wait_seconds=10,
        cache_ttl_seconds=0,
    )
    adapter = ArxivSearchAdapter(
        store,
        transport=transport,
        policy=policy,
        governor=SourceRequestGovernor(0),
    )

    with pytest.raises(PaperSearchError):
        adapter.search("cancelled", retry_allowed=lambda: retry_allowed)

    assert transport.calls == 1
