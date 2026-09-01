import json

import pytest

from conflux_weave.provider import (
    OpenAICompatibleChatAdapter,
    ProviderConfig,
    ProviderConfigurationError,
    ProviderHttpResponse,
    ProviderPortError,
)
from conflux_weave.runtime import LocalArtifactStore


class FakeTransport:
    def __init__(self, response: ProviderHttpResponse) -> None:
        self.response = response
        self.calls = []

    def post(self, url, *, headers, body, timeout_seconds):
        self.calls.append((url, headers, body, timeout_seconds))
        return self.response


def response(body: dict, status: int = 200) -> ProviderHttpResponse:
    return ProviderHttpResponse(
        status_code=status,
        body=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )


def valid_response() -> dict:
    return {
        "id": "chatcmpl-fixture",
        "model": "fixture-model",
        "choices": [
            {
                "message": {"role": "assistant", "content": "fixture answer"},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 11,
            "completion_tokens": 5,
            "total_tokens": 16,
        },
    }


def test_provider_adapter_records_request_and_raw_response_without_secret(tmp_path) -> None:
    transport = FakeTransport(response(valid_response()))
    store = LocalArtifactStore(tmp_path)
    result = OpenAICompatibleChatAdapter(
        store,
        ProviderConfig("https://provider.example/v1", "fixture-secret", "fixture-model"),
        transport=transport,
    ).complete(
        system_prompt="system",
        user_prompt="user",
        max_output_tokens=128,
        enable_thinking=False,
    )

    assert result.content == "fixture answer"
    assert result.total_tokens == 16
    request_payload = json.loads(store.read_bytes(result.request_artifact))
    assert request_payload["request"]["model"] == "fixture-model"
    assert request_payload["request"]["enable_thinking"] is False
    assert request_payload["automatic_retry"] is False
    assert b"fixture-secret" not in store.read_bytes(result.request_artifact)
    assert transport.calls[0][1]["Authorization"] == "Bearer fixture-secret"
    assert store.read_bytes(result.response_artifact) == json.dumps(valid_response()).encode()


def test_provider_http_failure_preserves_response_and_fails_closed(tmp_path) -> None:
    transport = FakeTransport(response({"error": "denied"}, status=401))
    adapter = OpenAICompatibleChatAdapter(
        LocalArtifactStore(tmp_path),
        ProviderConfig("https://provider.example/v1", "fixture-secret", "fixture-model"),
        transport=transport,
    )

    with pytest.raises(ProviderPortError) as caught:
        adapter.complete(system_prompt="system", user_prompt="user")

    assert caught.value.code == "provider_http_failed"
    assert caught.value.status_code == 401
    assert caught.value.retryable is False
    assert caught.value.request_artifact_ref
    assert caught.value.response_artifact_ref
    assert len(transport.calls) == 1


def test_invalid_provider_response_is_not_used_as_answer(tmp_path) -> None:
    transport = FakeTransport(response({"id": "missing-contract"}))
    adapter = OpenAICompatibleChatAdapter(
        LocalArtifactStore(tmp_path),
        ProviderConfig("https://provider.example/v1", "fixture-secret", "fixture-model"),
        transport=transport,
    )

    with pytest.raises(ProviderPortError, match="contract invalid") as caught:
        adapter.complete(system_prompt="system", user_prompt="user")

    assert caught.value.code == "provider_response_invalid"
    assert len(transport.calls) == 1


def test_provider_config_loads_ignored_dotenv_without_overriding_environment(
    tmp_path, monkeypatch
) -> None:
    dotenv_path = tmp_path / ".env"
    dotenv_path.write_text(
        "CONFLUX_WEAVE_PROVIDER_BASE_URL=https://provider.example/v1\n"
        "CONFLUX_WEAVE_PROVIDER_API_KEY=dotenv-secret\n"
        "CONFLUX_WEAVE_PROVIDER_MODEL=dotenv-model\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CONFLUX_WEAVE_PROVIDER_MODEL", "environment-model")

    config = ProviderConfig.from_environment(dotenv_path)

    assert config.api_key == "dotenv-secret"
    assert config.model == "environment-model"


def test_provider_config_reads_optional_engine_model(tmp_path, monkeypatch) -> None:
    dotenv_path = tmp_path / ".env"
    dotenv_path.write_text(
        "CONFLUX_WEAVE_PROVIDER_BASE_URL=https://provider.example/v1\n"
        "CONFLUX_WEAVE_PROVIDER_API_KEY=dotenv-secret\n"
        "CONFLUX_WEAVE_PROVIDER_MODEL=dotenv-model\n"
        "CONFLUX_WEAVE_PROVIDER_ENGINE_MODEL=deepseek-v4-flash-0731\n",
        encoding="utf-8",
    )

    config = ProviderConfig.from_environment(dotenv_path)

    assert config.engine_model == "deepseek-v4-flash-0731"
    # 未设置该键时保持 None（引擎回退到 chat 模型）
    monkeypatch.delenv("CONFLUX_WEAVE_PROVIDER_ENGINE_MODEL", raising=False)
    bare_path = tmp_path / "bare.env"
    bare_path.write_text(
        "CONFLUX_WEAVE_PROVIDER_BASE_URL=https://provider.example/v1\n"
        "CONFLUX_WEAVE_PROVIDER_API_KEY=dotenv-secret\n"
        "CONFLUX_WEAVE_PROVIDER_MODEL=dotenv-model\n",
        encoding="utf-8",
    )
    assert ProviderConfig.from_environment(bare_path).engine_model is None


def test_provider_config_rejects_non_https_endpoint(monkeypatch) -> None:
    monkeypatch.setenv("CONFLUX_WEAVE_PROVIDER_BASE_URL", "http://provider.example/v1")
    monkeypatch.setenv("CONFLUX_WEAVE_PROVIDER_API_KEY", "fixture-secret")
    monkeypatch.setenv("CONFLUX_WEAVE_PROVIDER_MODEL", "fixture-model")

    with pytest.raises(ProviderConfigurationError, match="HTTPS"):
        ProviderConfig.from_environment()


class SequencedTransport:
    """Returns queued responses in order; records every request body."""

    def __init__(self, responses) -> None:
        self.responses = iter(responses)
        self.bodies = []

    def post(self, url, *, headers, body, timeout_seconds):
        self.bodies.append(json.loads(body))
        return next(self.responses)


def thinking_reject_response() -> dict:
    # glm-5.3-flash 网关行为：enable_thinking=false 时返回带 error 的无效响应。
    return {
        "error": {"message": "该模型始终思考，不支持关闭思考；请使用 low、high 或 max。"},
        "choices": [],
    }


def test_adapter_retries_once_without_enable_thinking_when_model_rejects_it(tmp_path) -> None:
    transport = SequencedTransport([response(thinking_reject_response()), response(valid_response())])
    store = LocalArtifactStore(tmp_path)
    result = OpenAICompatibleChatAdapter(
        store,
        ProviderConfig("https://provider.example/v1", "fixture-secret", "fixture-model"),
        transport=transport,
    ).complete(
        system_prompt="system",
        user_prompt="user",
        max_output_tokens=128,
        enable_thinking=False,
    )

    assert result.content == "fixture answer"
    assert len(transport.bodies) == 2
    assert transport.bodies[0]["enable_thinking"] is False
    assert "enable_thinking" not in transport.bodies[1]
    assert transport.bodies[1]["reasoning_effort"] == "low"
    first_request = json.loads(store.read_bytes(result.request_artifact))
    assert first_request["attempt"] == 2
    assert first_request["automatic_retry"] is True


def test_adapter_keeps_single_call_when_enable_thinking_accepted(tmp_path) -> None:
    transport = SequencedTransport([response(valid_response())])
    store = LocalArtifactStore(tmp_path)
    result = OpenAICompatibleChatAdapter(
        store,
        ProviderConfig("https://provider.example/v1", "fixture-secret", "fixture-model"),
        transport=transport,
    ).complete(
        system_prompt="system",
        user_prompt="user",
        max_output_tokens=128,
        enable_thinking=False,
    )

    assert result.content == "fixture answer"
    assert len(transport.bodies) == 1
    assert transport.bodies[0]["enable_thinking"] is False


def test_adapter_does_not_retry_for_other_invalid_responses(tmp_path) -> None:
    transport = SequencedTransport([response({"error": {"message": "boom"}, "choices": []})])
    store = LocalArtifactStore(tmp_path)
    with pytest.raises(ProviderPortError) as excinfo:
        OpenAICompatibleChatAdapter(
            store,
            ProviderConfig("https://provider.example/v1", "fixture-secret", "fixture-model"),
            transport=transport,
        ).complete(
            system_prompt="system",
            user_prompt="user",
            max_output_tokens=128,
        )

    assert excinfo.value.code == "provider_response_invalid"
    assert len(transport.bodies) == 1
    assert "enable_thinking" not in transport.bodies[0]


def test_adapter_retries_when_gateway_rejects_thinking_with_http_400(tmp_path) -> None:
    transport = SequencedTransport(
        [
            ProviderHttpResponse(
                status_code=400,
                body=json.dumps(
                    {"error": {"message": "该模型始终思考，不支持关闭思考；请使用 low、high 或 max。", "code": "1210"}}
                ).encode(),
                headers={"Content-Type": "application/json"},
            ),
            response(valid_response()),
        ]
    )
    store = LocalArtifactStore(tmp_path)
    result = OpenAICompatibleChatAdapter(
        store,
        ProviderConfig("https://provider.example/v1", "fixture-secret", "fixture-model"),
        transport=transport,
    ).complete(
        system_prompt="system",
        user_prompt="user",
        max_output_tokens=128,
        enable_thinking=False,
    )

    assert result.content == "fixture answer"
    assert len(transport.bodies) == 2
    assert "enable_thinking" not in transport.bodies[1]


def test_adapter_does_not_auto_retry_server_errors(tmp_path) -> None:
    transport = SequencedTransport(
        [
            ProviderHttpResponse(status_code=429, body=b"rate limited", headers={}),
            response(valid_response()),
        ]
    )
    store = LocalArtifactStore(tmp_path)
    with pytest.raises(ProviderPortError) as excinfo:
        OpenAICompatibleChatAdapter(
            store,
            ProviderConfig("https://provider.example/v1", "fixture-secret", "fixture-model"),
            transport=transport,
        ).complete(
            system_prompt="system",
            user_prompt="user",
            max_output_tokens=128,
            enable_thinking=False,
        )

    assert excinfo.value.code == "provider_http_failed"
    assert len(transport.bodies) == 1
