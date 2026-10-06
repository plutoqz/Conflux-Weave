"""Tests for multi-provider failover, circuit breaker, rate limiting, and route auditing."""

import json
import time
from typing import Mapping

import pytest

from conflux_weave.provider import (
    ProviderHttpResponse,
    ProviderHttpTransport,
    ProviderPortError,
)
from conflux_weave.provider_pool import (
    CircuitBreaker,
    CircuitState,
    ProviderEndpoint,
    ProviderFailoverExhaustedError,
    ProviderPoolRouter,
    RouteStepAudit,
)
from conflux_weave.runtime.artifacts import LocalArtifactStore


class MockTransport(ProviderHttpTransport):
    def __init__(self, responses: dict[str, list[ProviderHttpResponse | Exception]]) -> None:
        self.responses = responses
        self.call_history: list[dict] = []

    def post(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        body: bytes,
        timeout_seconds: float,
    ) -> ProviderHttpResponse:
        self.call_history.append({"url": url, "body": body})
        queue = self.responses.get(url, [])
        if not queue:
            # Default success response
            mock_body = json.dumps({
                "id": "resp-default",
                "model": "mock-model",
                "choices": [{"message": {"content": "Hello world"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            }).encode("utf-8")
            return ProviderHttpResponse(status_code=200, body=mock_body, headers={"Content-Type": "application/json"})

        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture
def artifact_store(tmp_path):
    return LocalArtifactStore(tmp_path / "artifacts")


def test_circuit_breaker_transitions():
    cb = CircuitBreaker(failure_threshold=2, recovery_timeout_seconds=5.0)
    assert cb.state == CircuitState.CLOSED
    assert cb.can_attempt(now=100.0)

    # 1st failure: still closed
    cb.record_failure("error 1", now=100.0)
    assert cb.state == CircuitState.CLOSED
    assert cb.failure_count == 1

    # 2nd failure: trips open
    cb.record_failure("error 2", now=101.0)
    assert cb.state == CircuitState.OPEN
    assert cb.can_attempt(now=102.0) is False

    # After recovery timeout: transitions to HALF_OPEN
    assert cb.can_attempt(now=107.0) is True
    assert cb.state == CircuitState.HALF_OPEN

    # Half open success restores to CLOSED
    cb.record_success(now=107.5)
    assert cb.state == CircuitState.CLOSED
    assert cb.failure_count == 0


def test_failover_cascades_to_backup_on_error(artifact_store):
    primary_url = "https://primary.ai/chat/completions"
    backup_url = "https://backup.ai/chat/completions"

    primary_error = ProviderHttpResponse(
        status_code=502,
        body=b'{"error": "Bad Gateway"}',
        headers={"Content-Type": "application/json"},
    )
    backup_success = ProviderHttpResponse(
        status_code=200,
        body=json.dumps({
            "id": "resp-backup-1",
            "model": "deepseek-v3",
            "choices": [{"message": {"content": '{"analysis": "recovered"}'}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 50, "completion_tokens": 20, "total_tokens": 70},
        }).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )

    mock_transport = MockTransport({
        primary_url: [primary_error],
        backup_url: [backup_success],
    })

    router = ProviderPoolRouter(artifact_store, transport=mock_transport)
    router.register_endpoint(
        ProviderEndpoint(
            endpoint_id="primary",
            name="Qwen-Primary",
            base_url="https://primary.ai",
            api_key="pk-1",
            model="qwen-plus",
            priority=0,
        )
    )
    router.register_endpoint(
        ProviderEndpoint(
            endpoint_id="backup",
            name="DeepSeek-Backup",
            base_url="https://backup.ai",
            api_key="pk-2",
            model="deepseek-v3",
            priority=1,
        )
    )

    result, decision = router.complete_chat(
        system_prompt="Analyze",
        user_prompt="Topic X",
        json_object=True,
    )

    assert result.content == '{"analysis": "recovered"}'
    assert decision.selected_endpoint_id == "backup"
    assert decision.selected_model == "deepseek-v3"
    assert decision.final_status == "success"
    assert len(decision.fallback_chain) == 2
    assert decision.fallback_chain[0].endpoint_id == "primary"
    assert decision.fallback_chain[0].status == "http_error"
    assert decision.fallback_chain[0].status_code == 502
    assert decision.fallback_chain[1].endpoint_id == "backup"
    assert decision.fallback_chain[1].status == "success"
    assert "Failover succeeded" in decision.resilience_reason


def test_circuit_tripped_skips_primary_directly(artifact_store):
    primary_url = "https://primary.ai/chat/completions"
    backup_url = "https://backup.ai/chat/completions"

    mock_transport = MockTransport({})
    router = ProviderPoolRouter(artifact_store, transport=mock_transport)

    ep_primary = ProviderEndpoint(
        endpoint_id="primary",
        name="Qwen-Primary",
        base_url="https://primary.ai",
        api_key="pk-1",
        model="qwen-plus",
        priority=0,
    )
    # Force trip circuit
    ep_primary.circuit_breaker.state = CircuitState.OPEN
    ep_primary.circuit_breaker.last_failure_time = time.time()  # recent

    ep_backup = ProviderEndpoint(
        endpoint_id="backup",
        name="DeepSeek-Backup",
        base_url="https://backup.ai",
        api_key="pk-2",
        model="deepseek-v3",
        priority=1,
    )

    router.register_endpoint(ep_primary)
    router.register_endpoint(ep_backup)

    result, decision = router.complete_chat(
        system_prompt="Test",
        user_prompt="Hello",
    )

    # Primary should be skipped with circuit_open without making an HTTP request to primary
    assert decision.selected_endpoint_id == "backup"
    assert decision.fallback_chain[0].status == "circuit_open"
    assert decision.fallback_chain[1].status == "success"


def test_all_endpoints_exhausted_raises_error(artifact_store):
    primary_url = "https://primary.ai/chat/completions"
    backup_url = "https://backup.ai/chat/completions"

    primary_error = ProviderHttpResponse(
        status_code=500,
        body=b'{"error": "Internal"}',
        headers={"Content-Type": "application/json"},
    )
    backup_error = ProviderHttpResponse(
        status_code=503,
        body=b'{"error": "Service Unavailable"}',
        headers={"Content-Type": "application/json"},
    )

    mock_transport = MockTransport({
        primary_url: [primary_error],
        backup_url: [backup_error],
    })

    router = ProviderPoolRouter(artifact_store, transport=mock_transport)
    router.register_endpoint(
        ProviderEndpoint("ep1", "Primary", "https://primary.ai", "k1", "m1", priority=0)
    )
    router.register_endpoint(
        ProviderEndpoint("ep2", "Backup", "https://backup.ai", "k2", "m2", priority=1)
    )

    with pytest.raises(ProviderFailoverExhaustedError) as exc_info:
        router.complete_chat(system_prompt="Test", user_prompt="Hello")

    assert len(exc_info.value.fallback_chain) == 2
    assert exc_info.value.fallback_chain[0].status_code == 500
    assert exc_info.value.fallback_chain[1].status_code == 503


def test_health_status_and_circuit_reset(artifact_store):
    router = ProviderPoolRouter(artifact_store)
    ep = ProviderEndpoint("ep1", "Primary", "https://primary.ai", "k1", "m1")
    ep.circuit_breaker.state = CircuitState.OPEN
    ep.circuit_breaker.last_failure_time = time.time()
    ep.circuit_breaker.failure_count = 5
    router.register_endpoint(ep)

    health = router.get_health_status()
    assert len(health) == 1
    assert health[0]["circuit_state"] == "OPEN"
    assert health[0]["failure_count"] == 5

    # Reset circuit
    assert router.reset_circuit("ep1") is True
    health_after = router.get_health_status()
    assert health_after[0]["circuit_state"] == "CLOSED"
    assert health_after[0]["failure_count"] == 0


def test_rate_limiting_skips_saturated_endpoint(artifact_store):
    ep1 = ProviderEndpoint("ep1", "Primary", "https://primary.ai", "k1", "m1", priority=0, rate_limit_rpm=1)
    ep2 = ProviderEndpoint("ep2", "Backup", "https://backup.ai", "k2", "m2", priority=1)

    mock_transport = MockTransport({})
    router = ProviderPoolRouter(artifact_store, transport=mock_transport)
    router.register_endpoint(ep1)
    router.register_endpoint(ep2)

    now = time.time()
    ep1.record_request(now=now)  # Uses up 1/1 quota
    assert ep1.is_rate_limited(now=now) is True

    result, decision = router.complete_chat(system_prompt="Test", user_prompt="Hello")
    assert decision.selected_endpoint_id == "ep2"
    assert decision.fallback_chain[0].status == "rate_limited"
    assert decision.fallback_chain[1].status == "success"


def test_thinking_toggle_incompatibility_self_heals(artifact_store):
    endpoint_url = "https://thinking-model.ai/chat/completions"

    # Attempt 1 returns 400 rejecting enable_thinking
    err_400 = ProviderHttpResponse(
        status_code=400,
        body=b'{"error": {"code": 1210, "message": "enable_thinking is not supported"}}',
        headers={"Content-Type": "application/json"},
    )
    # Attempt 2 succeeds with reasoning_effort=low
    ok_200 = ProviderHttpResponse(
        status_code=200,
        body=json.dumps({
            "id": "resp-healed",
            "model": "glm-flash",
            "choices": [{"message": {"content": "Healed content"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 15, "completion_tokens": 8, "total_tokens": 23},
        }).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )

    mock_transport = MockTransport({endpoint_url: [err_400, ok_200]})
    router = ProviderPoolRouter(artifact_store, transport=mock_transport)
    ep = ProviderEndpoint("glm", "GLM", "https://thinking-model.ai", "k1", "glm-flash", priority=0)
    router.register_endpoint(ep)

    result, decision = router.complete_chat(
        system_prompt="Analyze",
        user_prompt="Task",
        enable_thinking=False,
    )

    assert result.content == "Healed content"
    assert ep.supports_thinking_toggle is False  # Automatically marked as unsupported
    assert decision.resilience_reason == "Self-healed capability incompatibility on endpoint"


def test_provider_health_and_repair_api_endpoints(tmp_path):
    from types import SimpleNamespace
    from starlette.testclient import TestClient
    from conflux_weave.server import create_app
    from conflux_weave.runtime.sqlite import SQLiteRuntimeRepository
    from conflux_weave.runtime.artifacts import LocalArtifactStore

    store = LocalArtifactStore(tmp_path / "artifacts")
    repo = SQLiteRuntimeRepository(tmp_path / "runtime.sqlite3", store)
    app = create_app(repo, SimpleNamespace(), enable_worker=False)
    client = TestClient(app)

    # 1. Test GET /api/v1/provider/health
    resp = client.get("/api/v1/provider/health")
    assert resp.status_code == 200
    data = resp.json()
    assert "endpoints" in data
    assert "timestamp" in data

    # 2. Test POST /api/v1/provider/{endpoint_id}/reset-circuit
    reset_resp = client.post("/api/v1/provider/non-existent/reset-circuit")
    assert reset_resp.status_code == 200
    assert reset_resp.json()["reset"] is False

    # 3. Test POST /api/v1/provider/structured/repair (successful extraction & schema repair)
    raw_markdown = "<think>reasoning...</think>\n```json\n{\"title\": \"AI Safety\", \"items\": [1, 2, ], \"status\": True}\n```"
    repair_resp = client.post("/api/v1/provider/structured/repair", json={
        "raw_text": raw_markdown,
        "schema": {
            "type": "object",
            "required": ["title", "count"],
            "properties": {
                "title": {"type": "string"},
                "count": {"type": "integer", "default": 0},
            }
        },
        "fill_defaults": True
    })
    assert repair_resp.status_code == 200
    repair_data = repair_resp.json()
    assert repair_data["success"] is True
    assert repair_data["parsed_data"]["title"] == "AI Safety"
    assert repair_data["parsed_data"]["count"] == 0  # schema default
    assert repair_data["parsed_data"]["status"] is True  # python literal repaired
    assert len(repair_data["warnings"]) >= 1

    # 4. Test POST /api/v1/provider/structured/repair (malformed text failure handling)
    bad_resp = client.post("/api/v1/provider/structured/repair", json={
        "raw_text": "Plain string with no json anywhere",
    })
    assert bad_resp.status_code == 200
    assert bad_resp.json()["success"] is False
    assert bad_resp.json()["error"] is not None
