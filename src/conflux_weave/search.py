"""GitHub repository discovery and explicit source registration for W1.3."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Mapping, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from conflux_weave.evidence import ArtifactRef, SourceSnapshot
from conflux_weave.runtime import LocalArtifactStore


GITHUB_API = "https://api.github.com"
GITHUB_API_VERSION = "2022-11-28"
SEARCH_SCHEMA_VERSION = "conflux-weave.github-repository-search.v1"


@dataclass(frozen=True, slots=True)
class HttpResponse:
    status_code: int
    body: bytes
    headers: Mapping[str, str]


class HttpTransport(Protocol):
    def get(
        self, url: str, *, headers: Mapping[str, str], timeout_seconds: float
    ) -> HttpResponse: ...


class UrllibTransport:
    def get(
        self, url: str, *, headers: Mapping[str, str], timeout_seconds: float
    ) -> HttpResponse:
        request = Request(url, headers=dict(headers), method="GET")
        try:
            with urlopen(request, timeout=timeout_seconds) as response:
                return HttpResponse(
                    status_code=response.status,
                    body=response.read(),
                    headers=dict(response.headers.items()),
                )
        except HTTPError as exc:
            return HttpResponse(
                status_code=exc.code,
                body=exc.read(),
                headers=dict(exc.headers.items()) if exc.headers else {},
            )
        except (URLError, TimeoutError, OSError) as exc:
            raise SearchPortError(
                code="github_network_failed",
                message=f"GitHub request failed: {exc}",
                retryable=True,
            ) from exc


class SearchPortError(RuntimeError):
    def __init__(
        self,
        *,
        code: str,
        message: str,
        retryable: bool,
        status_code: int | None = None,
        artifact_ref: str | None = None,
        recovery_action: str = "",
    ) -> None:
        self.code = code
        self.retryable = retryable
        self.status_code = status_code
        self.artifact_ref = artifact_ref
        self.recovery_action = recovery_action
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class RepositoryCandidate:
    full_name: str
    owner: str
    name: str
    html_url: str
    description: str | None
    default_branch: str
    stars: int
    archived: bool
    fork: bool
    github_score: float
    updated_at: str


@dataclass(frozen=True, slots=True)
class RepositorySearchResult:
    query: str
    acquired_at: str
    candidates: tuple[RepositoryCandidate, ...]
    response_artifact: ArtifactRef
    manifest_artifact: ArtifactRef
    rejected_items: tuple[str, ...]
    rate_limit_remaining: int | None


@dataclass(frozen=True, slots=True)
class RegisteredRepository:
    candidate: RepositoryCandidate
    source_snapshot: SourceSnapshot
    source_artifact: ArtifactRef
    snapshot_artifact: ArtifactRef
    discovery_manifest_ref: str


@dataclass(frozen=True, slots=True)
class RepositoryReadmeResult:
    repository: RepositoryCandidate
    source_snapshot: SourceSnapshot
    readme_artifact: ArtifactRef
    snapshot_artifact: ArtifactRef


class GitHubRepositorySearchAdapter:
    def __init__(
        self,
        artifact_store: LocalArtifactStore,
        *,
        transport: HttpTransport | None = None,
        token: str | None = None,
        acquired_at: str | None = None,
        timeout_seconds: float = 15.0,
    ) -> None:
        self.artifact_store = artifact_store
        self.transport = transport or UrllibTransport()
        self.token = token
        self.acquired_at = acquired_at or _utc_now()
        self.timeout_seconds = timeout_seconds

    @classmethod
    def from_environment(
        cls, artifact_store: LocalArtifactStore
    ) -> GitHubRepositorySearchAdapter:
        return cls(artifact_store, token=os.environ.get("GITHUB_TOKEN"))

    def search(self, query: str, *, limit: int = 5) -> RepositorySearchResult:
        normalized_query = query.strip()
        if not normalized_query:
            raise ValueError("search query must not be empty")
        if not 1 <= limit <= 10:
            raise ValueError("limit must be between 1 and 10")

        request_url = f"{GITHUB_API}/search/repositories?{urlencode({'q': normalized_query, 'per_page': limit})}"
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": GITHUB_API_VERSION,
            "User-Agent": "Conflux-Weave/0.0.1",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        try:
            response = self.transport.get(
                request_url,
                headers=headers,
                timeout_seconds=self.timeout_seconds,
            )
        except SearchPortError as exc:
            failure_artifact = self._store_failure(
                query=normalized_query,
                code=exc.code,
                message=str(exc),
                retryable=exc.retryable,
                status_code=exc.status_code,
            )
            exc.artifact_ref = failure_artifact.artifact_id
            exc.recovery_action = "检查网络连接后显式重试；不要自动切换来源。"
            raise

        response_artifact = self.artifact_store.put_bytes(
            response.body,
            media_type=response.headers.get("Content-Type", "application/json"),
            producer_step_id="step-github-search",
            schema_version="github-api.search-repositories.response",
        )
        if response.status_code != 200:
            code = (
                "github_rate_limited"
                if response.status_code in {403, 429}
                else "github_http_failed"
            )
            raise SearchPortError(
                code=code,
                message=f"GitHub search returned HTTP {response.status_code}",
                retryable=response.status_code >= 500 or response.status_code in {403, 429},
                status_code=response.status_code,
                artifact_ref=response_artifact.artifact_id,
                recovery_action="检查 GitHub rate limit 或服务状态后显式重试。",
            )

        payload = self._decode_response(response.body, response_artifact)
        raw_items = payload.get("items")
        if not isinstance(raw_items, list):
            raise self._invalid_response(
                "GitHub response field 'items' must be a list", response_artifact
            )

        candidates: list[RepositoryCandidate] = []
        rejected_items: list[str] = []
        for index, item in enumerate(raw_items):
            try:
                candidates.append(_normalize_candidate(item))
            except (KeyError, TypeError, ValueError) as exc:
                rejected_items.append(f"item[{index}]: {exc}")

        rate_limit_remaining = _parse_optional_int(
            _header(response.headers, "X-RateLimit-Remaining")
        )
        manifest = {
            "schema_version": SEARCH_SCHEMA_VERSION,
            "query": normalized_query,
            "acquired_at": self.acquired_at,
            "request": {
                "endpoint": "/search/repositories",
                "limit": limit,
                "auth_mode": "token" if self.token else "anonymous",
            },
            "response_artifact_ref": response_artifact.artifact_id,
            "http_status": response.status_code,
            "rate_limit": {
                "remaining": rate_limit_remaining,
                "reset": _header(response.headers, "X-RateLimit-Reset"),
            },
            "candidate_count": len(candidates),
            "rejected_items": rejected_items,
            "identity_boundary": "GitHub search rank is discovery evidence, not proof that a repository is official.",
        }
        manifest_artifact = self.artifact_store.put_json(
            manifest,
            producer_step_id="step-github-search",
            schema_version=SEARCH_SCHEMA_VERSION,
        )
        return RepositorySearchResult(
            query=normalized_query,
            acquired_at=self.acquired_at,
            candidates=tuple(candidates),
            response_artifact=response_artifact,
            manifest_artifact=manifest_artifact,
            rejected_items=tuple(rejected_items),
            rate_limit_remaining=rate_limit_remaining,
        )

    def register(
        self, result: RepositorySearchResult, *, full_name: str
    ) -> RegisteredRepository:
        matches = [
            candidate
            for candidate in result.candidates
            if candidate.full_name.casefold() == full_name.strip().casefold()
        ]
        if len(matches) != 1:
            raise SearchPortError(
                code="github_candidate_not_selected",
                message=f"candidate must match exactly one owner/repo: {full_name}",
                retryable=False,
                artifact_ref=result.manifest_artifact.artifact_id,
                recovery_action="从 discovery candidates 中选择一个规范 owner/repo。",
            )
        candidate = matches[0]
        source_payload = {
            "schema_version": "conflux-weave.github-repository-source.v1",
            "full_name": candidate.full_name,
            "owner": candidate.owner,
            "name": candidate.name,
            "html_url": candidate.html_url,
            "description": candidate.description,
            "default_branch": candidate.default_branch,
            "stars": candidate.stars,
            "archived": candidate.archived,
            "fork": candidate.fork,
            "updated_at": candidate.updated_at,
            "discovery_manifest_ref": result.manifest_artifact.artifact_id,
            "official_status": "selected_candidate_not_independently_verified",
        }
        source_artifact = self.artifact_store.put_json(
            source_payload,
            producer_step_id="step-github-register-source",
            schema_version="conflux-weave.github-repository-source.v1",
        )
        digest = source_artifact.content_hash.removeprefix("sha256:")
        source_id = (
            "github-repository-"
            + candidate.full_name.casefold().replace("/", "-")
            + f"-{digest[:12]}"
        )
        snapshot = SourceSnapshot(
            source_id=source_id,
            source_type="github_repository_metadata",
            canonical_uri=candidate.html_url,
            acquired_at=result.acquired_at,
            content_hash=source_artifact.content_hash,
            artifact_ref=source_artifact.artifact_id,
        )
        snapshot_artifact = self.artifact_store.put_json(
            {
                "schema_version": "conflux-weave.source-snapshot.v1",
                "source_id": snapshot.source_id,
                "source_type": snapshot.source_type,
                "canonical_uri": snapshot.canonical_uri,
                "acquired_at": snapshot.acquired_at,
                "content_hash": snapshot.content_hash,
                "artifact_ref": snapshot.artifact_ref,
                "discovery_manifest_ref": result.manifest_artifact.artifact_id,
            },
            producer_step_id="step-github-register-source",
            schema_version="conflux-weave.source-snapshot.v1",
        )
        return RegisteredRepository(
            candidate=candidate,
            source_snapshot=snapshot,
            source_artifact=source_artifact,
            snapshot_artifact=snapshot_artifact,
            discovery_manifest_ref=result.manifest_artifact.artifact_id,
        )

    def fetch_readme(
        self, candidate: RepositoryCandidate
    ) -> RepositoryReadmeResult:
        request_url = f"{GITHUB_API}/repos/{candidate.full_name}/readme"
        headers = {
            "Accept": "application/vnd.github.raw+json",
            "X-GitHub-Api-Version": GITHUB_API_VERSION,
            "User-Agent": "Conflux-Weave/0.0.1",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        try:
            response = self.transport.get(
                request_url,
                headers=headers,
                timeout_seconds=self.timeout_seconds,
            )
        except SearchPortError as exc:
            failure_artifact = self._store_failure(
                query=candidate.full_name,
                code=exc.code,
                message=str(exc),
                retryable=exc.retryable,
                status_code=exc.status_code,
            )
            exc.artifact_ref = failure_artifact.artifact_id
            exc.recovery_action = "检查 GitHub README 访问条件后显式创建新 Run。"
            raise

        readme_artifact = self.artifact_store.put_bytes(
            response.body,
            media_type=response.headers.get("Content-Type", "text/markdown"),
            producer_step_id="step-github-readme",
            schema_version="github-api.repository-readme.response",
        )
        if response.status_code != 200:
            raise SearchPortError(
                code="github_readme_failed",
                message=f"GitHub README returned HTTP {response.status_code}",
                retryable=response.status_code >= 500 or response.status_code in {403, 429},
                status_code=response.status_code,
                artifact_ref=readme_artifact.artifact_id,
                recovery_action="检查仓库 README 和 GitHub 服务状态后显式创建新 Run。",
            )
        try:
            response.body.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SearchPortError(
                code="github_readme_invalid",
                message=f"GitHub README is not UTF-8: {exc}",
                retryable=False,
                artifact_ref=readme_artifact.artifact_id,
                recovery_action="保留原始响应并检查 README 编码，不要从无效文本生成回答。",
            ) from exc

        digest = readme_artifact.content_hash.removeprefix("sha256:")
        snapshot = SourceSnapshot(
            source_id=(
                "github-readme-"
                + candidate.full_name.casefold().replace("/", "-")
                + f"-{digest[:12]}"
            ),
            source_type="github_repository_readme",
            canonical_uri=(
                f"https://github.com/{candidate.full_name}/blob/"
                f"{candidate.default_branch}/README.md"
            ),
            acquired_at=self.acquired_at,
            content_hash=readme_artifact.content_hash,
            artifact_ref=readme_artifact.artifact_id,
        )
        snapshot_artifact = self.artifact_store.put_json(
            {
                "schema_version": "conflux-weave.source-snapshot.v1",
                "source_id": snapshot.source_id,
                "source_type": snapshot.source_type,
                "canonical_uri": snapshot.canonical_uri,
                "acquired_at": snapshot.acquired_at,
                "content_hash": snapshot.content_hash,
                "artifact_ref": snapshot.artifact_ref,
                "repository": candidate.full_name,
            },
            producer_step_id="step-github-readme",
            schema_version="conflux-weave.source-snapshot.v1",
        )
        return RepositoryReadmeResult(
            repository=candidate,
            source_snapshot=snapshot,
            readme_artifact=readme_artifact,
            snapshot_artifact=snapshot_artifact,
        )

    def _decode_response(
        self, body: bytes, response_artifact: ArtifactRef
    ) -> dict[str, Any]:
        try:
            payload = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise self._invalid_response(
                f"GitHub response is not valid JSON: {exc}", response_artifact
            ) from exc
        if not isinstance(payload, dict):
            raise self._invalid_response(
                "GitHub response root must be an object", response_artifact
            )
        return payload

    @staticmethod
    def _invalid_response(
        message: str, response_artifact: ArtifactRef
    ) -> SearchPortError:
        return SearchPortError(
            code="github_response_invalid",
            message=message,
            retryable=False,
            artifact_ref=response_artifact.artifact_id,
            recovery_action="保留原始响应并检查 GitHub API 合同，不要从无效字段生成候选。",
        )

    def _store_failure(
        self,
        *,
        query: str,
        code: str,
        message: str,
        retryable: bool,
        status_code: int | None,
    ) -> ArtifactRef:
        return self.artifact_store.put_json(
            {
                "schema_version": "conflux-weave.github-search-failure.v1",
                "query": query,
                "acquired_at": self.acquired_at,
                "code": code,
                "message": message,
                "retryable": retryable,
                "status_code": status_code,
            },
            producer_step_id="step-github-search",
            schema_version="conflux-weave.github-search-failure.v1",
        )


def _normalize_candidate(item: Any) -> RepositoryCandidate:
    if not isinstance(item, dict):
        raise TypeError("candidate must be an object")
    full_name = _required_string(item, "full_name")
    name = _required_string(item, "name")
    html_url = _required_string(item, "html_url")
    owner_payload = item["owner"]
    if not isinstance(owner_payload, dict):
        raise TypeError("owner must be an object")
    owner = _required_string(owner_payload, "login")
    if full_name.casefold() != f"{owner}/{name}".casefold():
        raise ValueError("full_name does not match owner/name")
    expected_url = f"https://github.com/{full_name}"
    if html_url.rstrip("/").casefold() != expected_url.casefold():
        raise ValueError("html_url is not the canonical GitHub repository URL")
    description = item.get("description")
    if description is not None and not isinstance(description, str):
        raise TypeError("description must be a string or null")
    return RepositoryCandidate(
        full_name=full_name,
        owner=owner,
        name=name,
        html_url=expected_url,
        description=description,
        default_branch=_required_string(item, "default_branch"),
        stars=_required_int(item, "stargazers_count"),
        archived=_required_bool(item, "archived"),
        fork=_required_bool(item, "fork"),
        github_score=float(item.get("score", 0.0)),
        updated_at=_required_string(item, "updated_at"),
    )


def _required_string(item: Mapping[str, Any], key: str) -> str:
    value = item[key]
    if not isinstance(value, str) or not value.strip():
        raise TypeError(f"{key} must be a non-empty string")
    return value


def _required_int(item: Mapping[str, Any], key: str) -> int:
    value = item[key]
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{key} must be an integer")
    return value


def _required_bool(item: Mapping[str, Any], key: str) -> bool:
    value = item[key]
    if not isinstance(value, bool):
        raise TypeError(f"{key} must be a boolean")
    return value


def _header(headers: Mapping[str, str], name: str) -> str | None:
    for key, value in headers.items():
        if key.casefold() == name.casefold():
            return value
    return None


def _parse_optional_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
