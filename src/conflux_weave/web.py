"""Minimal web fetch and stable text locator support for W2.2."""

from __future__ import annotations

import hashlib
import html
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Mapping, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from conflux_weave.evidence import ArtifactRef, SourceSnapshot
from conflux_weave.runtime import LocalArtifactStore


WEB_FETCH_SCHEMA_VERSION = "conflux-weave.web-fetch.v1"
WEB_SEGMENTS_SCHEMA_VERSION = "conflux-weave.web-segments.v1"


@dataclass(frozen=True, slots=True)
class WebHttpResponse:
    status_code: int
    body: bytes
    headers: Mapping[str, str]


class WebFetchTransport(Protocol):
    def get(
        self, url: str, *, headers: Mapping[str, str], timeout_seconds: float
    ) -> WebHttpResponse: ...


class UrllibWebFetchTransport:
    def get(
        self, url: str, *, headers: Mapping[str, str], timeout_seconds: float
    ) -> WebHttpResponse:
        request = Request(url, headers=dict(headers), method="GET")
        try:
            with urlopen(request, timeout=timeout_seconds) as response:
                return WebHttpResponse(
                    status_code=response.status,
                    body=response.read(),
                    headers=dict(response.headers.items()),
                )
        except HTTPError as exc:
            return WebHttpResponse(
                status_code=exc.code,
                body=exc.read(),
                headers=dict(exc.headers.items()) if exc.headers else {},
            )
        except (URLError, TimeoutError, OSError) as exc:
            raise WebFetchError(
                code="web_network_failed",
                message=f"web request failed: {exc}",
                retryable=True,
                recovery_action="检查来源访问条件后显式创建新 Run。",
            ) from exc


class WebFetchError(RuntimeError):
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
class WebTextSegment:
    segment_id: str
    ordinal: int
    text: str
    locator: dict[str, object]


@dataclass(frozen=True, slots=True)
class WebFetchResult:
    url: str
    acquired_at: str
    media_type: str
    source_snapshot: SourceSnapshot
    source_artifact: ArtifactRef
    snapshot_artifact: ArtifactRef
    normalized_text_artifact: ArtifactRef
    segments_artifact: ArtifactRef
    segments: tuple[WebTextSegment, ...]


class WebFetchAdapter:
    def __init__(
        self,
        artifact_store: LocalArtifactStore,
        *,
        transport: WebFetchTransport | None = None,
        acquired_at: str,
        timeout_seconds: float = 15.0,
        max_bytes: int = 2_000_000,
    ) -> None:
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        self.artifact_store = artifact_store
        self.transport = transport or UrllibWebFetchTransport()
        self.acquired_at = acquired_at
        self.timeout_seconds = timeout_seconds
        self.max_bytes = max_bytes

    def fetch(self, url: str) -> WebFetchResult:
        normalized_url = url.strip()
        if not normalized_url.startswith(("http://", "https://")):
            raise ValueError("url must use http:// or https://")
        headers = {
            "Accept": "text/html, text/plain, application/xhtml+xml",
            "User-Agent": "Conflux-Weave/0.0.1",
        }
        try:
            response = self.transport.get(
                normalized_url,
                headers=headers,
                timeout_seconds=self.timeout_seconds,
            )
        except WebFetchError as exc:
            failure = self._failure_artifact(normalized_url, exc)
            exc.artifact_ref = failure.artifact_id
            raise

        source_artifact = self.artifact_store.put_bytes(
            response.body,
            media_type=response.headers.get("Content-Type", "application/octet-stream"),
            producer_step_id="step-web-fetch",
            schema_version="web.raw-response.v1",
        )
        if len(response.body) > self.max_bytes:
            error = WebFetchError(
                code="web_response_too_large",
                message=f"web response exceeds max_bytes={self.max_bytes}",
                retryable=False,
                status_code=response.status_code,
                artifact_ref=source_artifact.artifact_id,
                recovery_action="选择更小的来源或先取得经过授权的本地快照。",
            )
            raise error
        if response.status_code < 200 or response.status_code >= 300:
            raise WebFetchError(
                code="web_http_failed",
                message=f"web request returned HTTP {response.status_code}",
                retryable=response.status_code >= 500 or response.status_code in {408, 429},
                status_code=response.status_code,
                artifact_ref=source_artifact.artifact_id,
                recovery_action="检查来源 HTTP 状态后显式创建新 Run。",
            )

        media_type = _media_type(response.headers.get("Content-Type", "text/plain"))
        try:
            raw_text = response.body.decode(_charset(response.headers.get("Content-Type", "")))
        except (LookupError, UnicodeDecodeError) as exc:
            raise WebFetchError(
                code="web_response_invalid_encoding",
                message=f"web response is not decodable UTF-8-compatible text: {exc}",
                retryable=False,
                status_code=response.status_code,
                artifact_ref=source_artifact.artifact_id,
                recovery_action="保留原始响应并改用可解码来源，不要生成 Evidence。",
            ) from exc

        normalized_text = _normalize_text(raw_text, media_type)
        if not normalized_text:
            raise WebFetchError(
                code="web_response_empty_text",
                message="web response contains no extractable text",
                retryable=False,
                status_code=response.status_code,
                artifact_ref=source_artifact.artifact_id,
                recovery_action="选择包含可解析正文的来源，不要从空文本生成 Evidence。",
            )
        normalized_artifact = self.artifact_store.put_bytes(
            normalized_text.encode("utf-8"),
            media_type="text/plain; charset=utf-8",
            producer_step_id="step-web-fetch",
            schema_version="web.normalized-text.v1",
        )
        segments = _segment_text(normalized_text, normalized_artifact.content_hash)
        segments_payload = {
            "schema_version": WEB_SEGMENTS_SCHEMA_VERSION,
            "url": normalized_url,
            "normalized_text_artifact_ref": normalized_artifact.artifact_id,
            "segments": [
                {
                    "segment_id": segment.segment_id,
                    "ordinal": segment.ordinal,
                    "text": segment.text,
                    "locator": segment.locator,
                }
                for segment in segments
            ],
        }
        segments_artifact = self.artifact_store.put_json(
            segments_payload,
            producer_step_id="step-web-fetch",
            schema_version=WEB_SEGMENTS_SCHEMA_VERSION,
        )
        snapshot = SourceSnapshot(
            source_id="web-" + hashlib.sha256(normalized_url.encode("utf-8")).hexdigest()[:20],
            source_type="web_document",
            canonical_uri=normalized_url,
            acquired_at=self.acquired_at,
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
                "normalized_text_artifact_ref": normalized_artifact.artifact_id,
                "segments_artifact_ref": segments_artifact.artifact_id,
            },
            producer_step_id="step-web-fetch",
            schema_version="conflux-weave.source-snapshot.v1",
        )
        return WebFetchResult(
            url=normalized_url,
            acquired_at=self.acquired_at,
            media_type=media_type,
            source_snapshot=snapshot,
            source_artifact=source_artifact,
            snapshot_artifact=snapshot_artifact,
            normalized_text_artifact=normalized_artifact,
            segments_artifact=segments_artifact,
            segments=segments,
        )

    def _failure_artifact(self, url: str, error: WebFetchError) -> ArtifactRef:
        return self.artifact_store.put_json(
            {
                "schema_version": WEB_FETCH_SCHEMA_VERSION,
                "url": url,
                "acquired_at": self.acquired_at,
                "code": error.code,
                "message": str(error),
                "retryable": error.retryable,
                "status_code": error.status_code,
            },
            producer_step_id="step-web-fetch",
            schema_version=WEB_FETCH_SCHEMA_VERSION,
        )


def _media_type(content_type: str) -> str:
    return content_type.split(";", 1)[0].strip().lower() or "text/plain"


def _charset(content_type: str) -> str:
    match = re.search(r"charset=\s*([^;]+)", content_type, re.IGNORECASE)
    return match.group(1).strip(" \"'") if match else "utf-8"


def _normalize_text(text: str, media_type: str) -> str:
    if media_type in {"text/html", "application/xhtml+xml"}:
        text = _HtmlTextExtractor().extract(text)
    text = html.unescape(text).replace("\r\n", "\n").replace("\r", "\n")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _segment_text(text: str, content_hash: str) -> tuple[WebTextSegment, ...]:
    segments: list[WebTextSegment] = []
    cursor = 0
    for block in re.split(r"\n\s*\n", text):
        block = block.strip()
        if not block:
            continue
        start = text.find(block, cursor)
        end = start + len(block)
        ordinal = len(segments) + 1
        segment_hash = hashlib.sha256(block.encode("utf-8")).hexdigest()
        segments.append(
            WebTextSegment(
                segment_id=f"web-segment-{content_hash.removeprefix('sha256:')[:12]}-{ordinal:04d}",
                ordinal=ordinal,
                text=block,
                locator={
                    "type": "text_range",
                    "start_char": start,
                    "end_char": end,
                    "text_sha256": segment_hash,
                },
            )
        )
        cursor = end
    return tuple(segments)


class _HtmlTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._ignored_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag.lower() in {"script", "style", "noscript", "template"}:
            self._ignored_depth += 1
        elif not self._ignored_depth and tag.lower() in {"p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6"}:
            self.parts.append("\n\n")
        elif not self._ignored_depth and tag.lower() == "br":
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "noscript", "template"} and self._ignored_depth:
            self._ignored_depth -= 1
        elif not self._ignored_depth and tag.lower() in {"p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6"}:
            self.parts.append("\n\n")

    def handle_data(self, data: str) -> None:
        if not self._ignored_depth:
            self.parts.append(data)

    def extract(self, text: str) -> str:
        self.feed(text)
        self.close()
        return "".join(self.parts)
