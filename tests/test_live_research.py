import json

import pytest

from conflux_weave.core import DeliveryDisposition, RunStatus
from conflux_weave.live_research import (
    FixedRepositoryIdentityWorkflow,
    LiveResearchValidationError,
)
from conflux_weave.provider import (
    OpenAICompatibleChatAdapter,
    ProviderConfig,
    ProviderHttpResponse,
)
from conflux_weave.runtime import LocalArtifactStore
from conflux_weave.search import (
    GitHubRepositorySearchAdapter,
    HttpResponse,
)


def repository_item() -> dict[str, object]:
    return {
        "full_name": "example/pi",
        "name": "pi",
        "owner": {"login": "example"},
        "html_url": "https://github.com/example/pi",
        "description": "Pi coding agent",
        "default_branch": "main",
        "stargazers_count": 100,
        "archived": False,
        "fork": False,
        "score": 10.0,
        "updated_at": "2026-08-20T00:00:00Z",
    }


class GitHubTransport:
    def __init__(self) -> None:
        self.urls = []

    def get(self, url, *, headers, timeout_seconds):
        self.urls.append(url)
        if url.endswith("/readme"):
            return HttpResponse(
                200,
                b"# Pi Agent Harness\n\n* coding-agent: Interactive coding agent CLI\n",
                {"Content-Type": "text/markdown"},
            )
        return HttpResponse(
            200,
            json.dumps({"total_count": 1, "items": [repository_item()]}).encode(),
            {"Content-Type": "application/json", "X-RateLimit-Remaining": "8"},
        )


class ProviderTransport:
    def __init__(self, model_content: dict) -> None:
        self.model_content = model_content
        self.calls = 0

    def post(self, url, *, headers, body, timeout_seconds):
        self.calls += 1
        response = {
            "id": "chatcmpl-live-fixture",
            "model": "fixture-model",
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": json.dumps(self.model_content, ensure_ascii=False),
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 40,
                "total_tokens": 140,
            },
        }
        return ProviderHttpResponse(
            200,
            json.dumps(response, ensure_ascii=False).encode(),
            {"Content-Type": "application/json"},
        )


def stable_id(prefix: str) -> str:
    return f"{prefix}-fixed"


def fixed_clock() -> str:
    return "2026-08-21T00:00:00Z"


def build_workflow(tmp_path, model_content):
    store = LocalArtifactStore(tmp_path / "artifacts")
    github_transport = GitHubTransport()
    search = GitHubRepositorySearchAdapter(
        store,
        transport=github_transport,
        acquired_at=fixed_clock(),
    )
    provider_transport = ProviderTransport(model_content)
    provider = OpenAICompatibleChatAdapter(
        store,
        ProviderConfig("https://provider.example/v1", "fixture-secret", "fixture-model"),
        transport=provider_transport,
    )
    workflow = FixedRepositoryIdentityWorkflow(
        store,
        search,
        provider,
        clock=fixed_clock,
        id_factory=stable_id,
        code_revision="fixture-revision",
    )
    return workflow, store, provider_transport, github_transport


def test_live_repository_workflow_compiles_closed_cited_partial_delivery(tmp_path) -> None:
    workflow, store, provider_transport, github_transport = build_workflow(
        tmp_path,
        {
            "claims": [
                {
                    "text": "该仓库将项目自述为 Pi Agent Harness。",
                    "evidence_ids": ["github-repository-readme"],
                },
                {
                    "text": "规范仓库路径是 example/pi。",
                    "evidence_ids": ["github-repository-metadata"],
                },
            ],
            "limitations": ["证据仅包含 GitHub 元数据和仓库 README。"],
        },
    )

    result = workflow.execute("pi coding agent")

    assert result.final_run.status is RunStatus.PARTIAL
    assert result.delivery.disposition is DeliveryDisposition.PARTIAL
    assert result.selected_repository.full_name == "example/pi"
    assert len(result.claims) == 2
    assert len(result.citations) == 2
    assert provider_transport.calls == 1
    assert "q=pi+coding+agent" in github_transport.urls[0]
    report = store.read_bytes(result.report_artifact).decode()
    assert "### ◐ ? 证据约束结论" in report
    assert "该仓库将项目自述为 Pi Agent Harness。 [1]" not in report
    assert "## Evidence 汇总" in report
    manifest = store.read_bytes(result.manifest_artifact)
    assert b"fixture-secret" not in manifest
    assert json.loads(manifest)["usage"]["total_tokens"] == 140


def test_live_repository_workflow_rejects_unknown_model_evidence(tmp_path) -> None:
    workflow, _, provider_transport, _ = build_workflow(
        tmp_path,
        {
            "claims": [
                {"text": "unsupported", "evidence_ids": ["model-memory"]}
            ],
            "limitations": [],
        },
    )

    with pytest.raises(LiveResearchValidationError, match="unknown evidence"):
        workflow.execute("pi coding agent")

    assert provider_transport.calls == 1


def test_live_repository_workflow_extracts_ascii_entity_terms_from_chinese_question(
    tmp_path,
) -> None:
    workflow, _, _, github_transport = build_workflow(
        tmp_path,
        {
            "claims": [
                {"text": "Pi self-identifies in README.", "evidence_ids": ["github-repository-readme"]}
            ],
            "limitations": [],
        },
    )

    workflow.execute("定位 pi coding agent 的规范名称、维护者、官方仓库 URL 和公开实现入口")

    assert "q=pi+coding+agent" in github_transport.urls[0]


def test_live_repository_workflow_rejects_provider_usage_over_frozen_budget(
    tmp_path,
) -> None:
    workflow, _, provider_transport, _ = build_workflow(
        tmp_path,
        {
            "claims": [
                {"text": "Pi self-identifies in README.", "evidence_ids": ["github-repository-readme"]}
            ],
            "limitations": [],
        },
    )
    original_post = provider_transport.post

    def over_budget_post(*args, **kwargs):
        response = original_post(*args, **kwargs)
        payload = json.loads(response.body)
        payload["usage"]["completion_tokens"] = 2049
        payload["usage"]["total_tokens"] = 2149
        return ProviderHttpResponse(
            response.status_code,
            json.dumps(payload).encode(),
            response.headers,
        )

    provider_transport.post = over_budget_post

    with pytest.raises(LiveResearchValidationError) as captured:
        workflow.execute("pi coding agent")

    assert captured.value.code == "budget_exhausted"
    assert captured.value.request_artifact_ref
    assert captured.value.response_artifact_ref


def test_live_repository_workflow_rejects_official_wording_without_independent_proof(
    tmp_path,
) -> None:
    workflow, _, _, _ = build_workflow(
        tmp_path,
        {
            "claims": [
                {"text": "官方仓库 URL 是 https://github.com/example/pi。", "evidence_ids": ["github-repository-metadata"]}
            ],
            "limitations": [],
        },
    )

    with pytest.raises(LiveResearchValidationError, match="official repository"):
        workflow.execute("pi coding agent")
