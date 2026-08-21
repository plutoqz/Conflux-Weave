import json

import pytest

from conflux_weave.runtime import LocalArtifactStore
from conflux_weave.search import (
    GitHubRepositorySearchAdapter,
    HttpResponse,
    SearchPortError,
)


def repository_item(
    full_name: str = "acme/pi-agent", *, score: float = 42.0
) -> dict[str, object]:
    owner, name = full_name.split("/", 1)
    return {
        "full_name": full_name,
        "name": name,
        "owner": {"login": owner},
        "html_url": f"https://github.com/{full_name}",
        "description": "A coding agent fixture",
        "default_branch": "main",
        "stargazers_count": 120,
        "archived": False,
        "fork": False,
        "score": score,
        "updated_at": "2026-08-20T00:00:00Z",
    }


class RecordedTransport:
    def __init__(self, response: HttpResponse) -> None:
        self.response = response
        self.calls: list[tuple[str, dict[str, str], float]] = []

    def get(self, url, *, headers, timeout_seconds):
        self.calls.append((url, dict(headers), timeout_seconds))
        return self.response


def adapter_for(tmp_path, payload, *, status=200, token=None, headers=None):
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    transport = RecordedTransport(
        HttpResponse(
            status_code=status,
            body=body,
            headers=headers
            or {
                "Content-Type": "application/json",
                "X-RateLimit-Remaining": "9",
                "X-RateLimit-Reset": "1780000000",
            },
        )
    )
    store = LocalArtifactStore(tmp_path / "artifacts")
    adapter = GitHubRepositorySearchAdapter(
        store,
        transport=transport,
        token=token,
        acquired_at="2026-08-21T00:00:00Z",
    )
    return adapter, store, transport, body


def test_search_preserves_raw_response_and_does_not_persist_token(tmp_path) -> None:
    adapter, store, transport, body = adapter_for(
        tmp_path,
        {"total_count": 1, "items": [repository_item()]},
        token="fixture-secret-token",
    )

    result = adapter.search("pi coding agent", limit=3)

    assert store.read_bytes(result.response_artifact) == body
    assert [candidate.full_name for candidate in result.candidates] == [
        "acme/pi-agent"
    ]
    assert result.rate_limit_remaining == 9
    _, request_headers, _ = transport.calls[0]
    assert request_headers["Authorization"] == "Bearer fixture-secret-token"
    manifest = store.read_bytes(result.manifest_artifact)
    assert b"fixture-secret-token" not in body
    assert b"fixture-secret-token" not in manifest
    assert json.loads(manifest)["request"]["auth_mode"] == "token"


def test_explicit_candidate_registration_creates_one_source_snapshot(tmp_path) -> None:
    adapter, store, _, _ = adapter_for(
        tmp_path,
        {
            "total_count": 2,
            "items": [
                repository_item("acme/pi-agent", score=10),
                repository_item("other/pi-agent", score=9),
            ],
        },
    )
    result = adapter.search("pi coding agent")

    registered = adapter.register(result, full_name="ACME/pi-agent")

    assert registered.candidate.full_name == "acme/pi-agent"
    assert registered.source_snapshot.canonical_uri == "https://github.com/acme/pi-agent"
    assert registered.source_snapshot.artifact_ref == registered.source_artifact.artifact_id
    source = json.loads(store.read_bytes(registered.source_artifact))
    assert source["official_status"] == "selected_candidate_not_independently_verified"
    assert source["discovery_manifest_ref"] == result.manifest_artifact.artifact_id


def test_invalid_candidates_are_rejected_with_reasons(tmp_path) -> None:
    invalid = repository_item()
    invalid["html_url"] = "https://example.invalid/not-github"
    adapter, store, _, _ = adapter_for(
        tmp_path,
        {"total_count": 2, "items": [invalid, repository_item("valid/repo")]},
    )

    result = adapter.search("repository")

    assert [candidate.full_name for candidate in result.candidates] == ["valid/repo"]
    assert result.rejected_items == (
        "item[0]: html_url is not the canonical GitHub repository URL",
    )
    manifest = json.loads(store.read_bytes(result.manifest_artifact))
    assert manifest["rejected_items"] == list(result.rejected_items)


def test_rate_limit_failure_retains_raw_response(tmp_path) -> None:
    adapter, store, _, body = adapter_for(
        tmp_path,
        {"message": "API rate limit exceeded"},
        status=403,
    )

    with pytest.raises(SearchPortError) as captured:
        adapter.search("pi coding agent")

    error = captured.value
    assert error.code == "github_rate_limited"
    assert error.retryable is True
    assert error.status_code == 403
    digest = error.artifact_ref.removeprefix("artifact-sha256-")
    assert store.path_for_digest(digest).read_bytes() == body


class FailingTransport:
    def get(self, url, *, headers, timeout_seconds):
        raise SearchPortError(
            code="github_network_failed",
            message="offline fixture",
            retryable=True,
        )


def test_network_failure_creates_diagnostic_artifact_without_fallback(tmp_path) -> None:
    store = LocalArtifactStore(tmp_path / "artifacts")
    adapter = GitHubRepositorySearchAdapter(
        store,
        transport=FailingTransport(),
        acquired_at="2026-08-21T00:00:00Z",
    )

    with pytest.raises(SearchPortError) as captured:
        adapter.search("pi coding agent")

    error = captured.value
    assert error.code == "github_network_failed"
    assert error.retryable is True
    assert error.artifact_ref is not None
    digest = error.artifact_ref.removeprefix("artifact-sha256-")
    failure = json.loads(store.path_for_digest(digest).read_bytes())
    assert failure["code"] == "github_network_failed"
    assert failure["message"] == "offline fixture"


def test_registration_requires_an_exact_discovered_owner_and_repo(tmp_path) -> None:
    adapter, _, _, _ = adapter_for(
        tmp_path,
        {"total_count": 1, "items": [repository_item()]},
    )
    result = adapter.search("pi coding agent")

    with pytest.raises(SearchPortError, match="exactly one owner/repo") as captured:
        adapter.register(result, full_name="unknown/pi-agent")

    assert captured.value.code == "github_candidate_not_selected"


def test_fetch_readme_creates_content_addressed_source_snapshot(tmp_path) -> None:
    adapter, store, _, _ = adapter_for(
        tmp_path,
        {"total_count": 1, "items": [repository_item()]},
    )
    candidate = adapter.search("pi coding agent", limit=1).candidates[0]
    readme = b"# Pi Agent Harness\n\nRepository documentation.\n"
    adapter.transport = RecordedTransport(
        HttpResponse(200, readme, {"Content-Type": "text/markdown; charset=utf-8"})
    )

    result = adapter.fetch_readme(candidate)

    assert store.read_bytes(result.readme_artifact) == readme
    assert result.source_snapshot.source_type == "github_repository_readme"
    assert result.source_snapshot.canonical_uri.endswith("/main/README.md")
    snapshot = json.loads(store.read_bytes(result.snapshot_artifact))
    assert snapshot["artifact_ref"] == result.readme_artifact.artifact_id


def test_fetch_readme_failure_preserves_raw_response(tmp_path) -> None:
    adapter, _, _, _ = adapter_for(
        tmp_path,
        {"total_count": 1, "items": [repository_item()]},
    )
    candidate = adapter.search("pi coding agent", limit=1).candidates[0]
    adapter.transport = RecordedTransport(HttpResponse(404, b"missing", {}))

    with pytest.raises(SearchPortError) as captured:
        adapter.fetch_readme(candidate)

    assert captured.value.code == "github_readme_failed"
    assert captured.value.artifact_ref
