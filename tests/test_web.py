import json

import pytest

from conflux_weave.runtime import LocalArtifactStore
from conflux_weave.web import WebFetchAdapter, WebFetchError, WebHttpResponse


class FixtureTransport:
    def __init__(self, response: WebHttpResponse) -> None:
        self.response = response
        self.calls = []

    def get(self, url, *, headers, timeout_seconds):
        self.calls.append((url, dict(headers), timeout_seconds))
        return self.response


def adapter(tmp_path, response: WebHttpResponse) -> tuple[WebFetchAdapter, LocalArtifactStore, FixtureTransport]:
    store = LocalArtifactStore(tmp_path / "artifacts")
    transport = FixtureTransport(response)
    return (
        WebFetchAdapter(
            store,
            transport=transport,
            acquired_at="2026-08-24T00:00:00Z",
        ),
        store,
        transport,
    )


def test_fetch_preserves_raw_source_and_creates_stable_text_locators(tmp_path) -> None:
    body = b"<html><head><style>ignore</style></head><body><h1>Title</h1><p>First claim.</p><p>Second claim.</p></body></html>"
    fetcher, store, transport = adapter(
        tmp_path,
        WebHttpResponse(200, body, {"Content-Type": "text/html; charset=utf-8"}),
    )

    result = fetcher.fetch("https://example.test/page")

    assert transport.calls[0][0] == "https://example.test/page"
    assert store.read_bytes(result.source_artifact) == body
    assert result.media_type == "text/html"
    assert [segment.text for segment in result.segments] == [
        "Title",
        "First claim.",
        "Second claim.",
    ]
    first = result.segments[0].locator
    assert first["type"] == "text_range"
    assert first["start_char"] == 0
    assert first["text_sha256"]
    snapshot = json.loads(store.read_bytes(result.snapshot_artifact))
    assert snapshot["artifact_ref"] == result.source_artifact.artifact_id
    segments = json.loads(store.read_bytes(result.segments_artifact))
    assert segments["segments"][1]["text"] == "First claim."


def test_fetch_plain_text_normalizes_and_keeps_source_identity(tmp_path) -> None:
    fetcher, _, _ = adapter(
        tmp_path,
        WebHttpResponse(200, b"A\r\n\r\n B  text\n", {"Content-Type": "text/plain"}),
    )

    result = fetcher.fetch("https://example.test/plain")

    assert result.source_snapshot.source_type == "web_document"
    assert result.segments[0].text == "A"
    assert result.segments[1].text == "B text"


def test_fetch_http_failure_preserves_raw_response_reference(tmp_path) -> None:
    fetcher, store, _ = adapter(tmp_path, WebHttpResponse(404, b"missing", {}))

    with pytest.raises(WebFetchError) as captured:
        fetcher.fetch("https://example.test/missing")

    error = captured.value
    assert error.code == "web_http_failed"
    assert error.artifact_ref is not None
    digest = error.artifact_ref.removeprefix("artifact-sha256-")
    assert store.path_for_digest(digest).read_bytes() == b"missing"


def test_fetch_rejects_invalid_url_before_transport(tmp_path) -> None:
    fetcher, _, transport = adapter(tmp_path, WebHttpResponse(200, b"ok", {}))

    with pytest.raises(ValueError, match="http"):
        fetcher.fetch("file:///tmp/local")
    assert transport.calls == []


def test_fetch_rejects_oversized_response_but_keeps_source_artifact(tmp_path) -> None:
    fetcher, _, _ = adapter(tmp_path, WebHttpResponse(200, b"0123456789", {}))
    fetcher.max_bytes = 5

    with pytest.raises(WebFetchError, match="max_bytes") as captured:
        fetcher.fetch("https://example.test/large")

    assert captured.value.artifact_ref is not None
