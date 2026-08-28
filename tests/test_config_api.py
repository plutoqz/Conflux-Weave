import asyncio
import json
from pathlib import Path

from conflux_weave.api_contracts import ProviderConfigUpdateRequest
from conflux_weave.runtime import LocalArtifactStore, SQLiteRuntimeRepository
from conflux_weave.server import create_app


class PassiveRuntime:
    executor_id = "passive-config@v1"
    task_kinds = ("paper_discovery",)

    def work_once(self, *, now: str | None = None) -> None:
        return None

    def submit(self, *args, **kwargs):
        raise AssertionError("submission is not used by this fixture")

    def request_cancel(self, *args, **kwargs):
        raise AssertionError("cancellation is not used by this fixture")

    def resume(self, *args, **kwargs):
        raise AssertionError("resume is not used by this fixture")


def route(app, path: str):
    return next(item.endpoint for item in app.routes if item.path == path)


def build_config_app(tmp_path: Path, dotenv_content: str = ""):
    store = LocalArtifactStore(tmp_path / "artifacts")
    repository = SQLiteRuntimeRepository(tmp_path / "db" / "runtime.sqlite3", store)
    dotenv_path = tmp_path / "settings.env"
    if dotenv_content:
        dotenv_path.write_text(dotenv_content, encoding="utf-8")
    app = create_app(
        repository,
        PassiveRuntime(),
        provider_configured=False,
        dotenv_path=dotenv_path,
        config_paths={"database": str(tmp_path / "db" / "runtime.sqlite3")},
    )
    return app, dotenv_path


DOTENV_SEEDED = (
    "# local provider config\n"
    "CONFLUX_WEAVE_PROVIDER_BASE_URL=https://provider.example/v1\n"
    "CONFLUX_WEAVE_PROVIDER_API_KEY=sk-existing-9f8e\n"
    "CONFLUX_WEAVE_PROVIDER_MODEL=qwen3.7-flash\n"
    "OTHER_SETTING=keep-me\n"
)


def test_get_config_returns_sanitized_provider_and_paths(tmp_path) -> None:
    app, dotenv_path = build_config_app(tmp_path, DOTENV_SEEDED)
    response = asyncio.run(route(app, "/api/v1/config")())

    assert response.provider.base_url == "https://provider.example/v1"
    assert response.provider.model == "qwen3.7-flash"
    assert response.provider.api_key_configured is True
    assert response.provider.api_key_hint == "…9f8e"
    assert response.provider_active is False
    assert response.paths["database"].endswith("runtime.sqlite3")
    payload = json.dumps(response.model_dump(mode="json"), ensure_ascii=False)
    assert "sk-existing-9f8e" not in payload


def test_put_provider_config_writes_env_and_preserves_other_lines(tmp_path) -> None:
    app, dotenv_path = build_config_app(tmp_path, DOTENV_SEEDED)
    request = ProviderConfigUpdateRequest(
        base_url="https://new.example/v1",
        model="qwen-next",
        embedding_model="text-embedding-v4",
        api_key="sk-new-key-abcd",
    )
    response = asyncio.run(route(app, "/api/v1/config/provider")(request))

    assert response.requires_restart is True
    assert response.provider.base_url == "https://new.example/v1"
    assert response.provider.api_key_hint == "…abcd"
    written = dotenv_path.read_text(encoding="utf-8")
    assert written.startswith("# local provider config\n")
    assert "OTHER_SETTING=keep-me\n" in written
    assert "CONFLUX_WEAVE_PROVIDER_BASE_URL=https://new.example/v1\n" in written
    assert "CONFLUX_WEAVE_PROVIDER_API_KEY=sk-new-key-abcd\n" in written
    assert "CONFLUX_WEAVE_PROVIDER_MODEL=qwen-next\n" in written
    assert "CONFLUX_WEAVE_PROVIDER_EMBEDDING_MODEL=text-embedding-v4\n" in written
    payload = json.dumps(response.model_dump(mode="json"), ensure_ascii=False)
    assert "sk-new-key-abcd" not in payload


def test_put_provider_config_keeps_existing_key_when_blank(tmp_path) -> None:
    app, dotenv_path = build_config_app(tmp_path, DOTENV_SEEDED)
    request = ProviderConfigUpdateRequest(base_url="https://new.example/v1", model="m")
    asyncio.run(route(app, "/api/v1/config/provider")(request))

    written = dotenv_path.read_text(encoding="utf-8")
    assert "CONFLUX_WEAVE_PROVIDER_API_KEY=sk-existing-9f8e\n" in written


def test_put_provider_config_rejects_non_https_base_url(tmp_path) -> None:
    app, dotenv_path = build_config_app(tmp_path, DOTENV_SEEDED)
    request = ProviderConfigUpdateRequest(base_url="http://insecure.example", model="m")
    response = asyncio.run(route(app, "/api/v1/config/provider")(request))

    assert response.status_code == 422
    body = json.loads(response.body)
    assert body["code"] == "invalid_request"


def test_config_store_rejects_blank_model(tmp_path) -> None:
    from conflux_weave.config_store import ConfigValidationError, validate_provider_update

    try:
        validate_provider_update(base_url="https://ok.example/v1", model="  ")
    except ConfigValidationError:
        pass
    else:
        raise AssertionError("blank model must be rejected")

    try:
        validate_provider_update(base_url="http://insecure.example", model="m")
    except ConfigValidationError:
        pass
    else:
        raise AssertionError("non-https base_url must be rejected")


def test_provider_test_reports_missing_fields_without_network(tmp_path) -> None:
    app, _ = build_config_app(tmp_path, "")
    request = route(app, "/api/v1/config/provider/test")
    response = asyncio.run(request(__import__("conflux_weave.api_contracts", fromlist=["ProviderConfigTestRequest"]).ProviderConfigTestRequest(base_url="https://only.example")))

    assert response.ok is False
    assert "API Key" in response.message
    assert "Chat 模型" in response.message


def test_provider_test_uses_injected_adapter(tmp_path, monkeypatch) -> None:
    import conflux_weave.provider as provider_module
    from conflux_weave.api_contracts import ProviderConfigTestRequest

    calls: dict[str, object] = {}

    class FakeAdapter:
        def __init__(self, store, config, *, timeout_seconds: float = 20.0) -> None:
            calls["base_url"] = config.base_url
            calls["model"] = config.model

        def complete(self, **kwargs) -> object:
            calls["prompt"] = kwargs["user_prompt"]
            return object()

    monkeypatch.setattr(provider_module, "OpenAICompatibleChatAdapter", FakeAdapter)
    app, _ = build_config_app(tmp_path, DOTENV_SEEDED)
    response = asyncio.run(
        route(app, "/api/v1/config/provider/test")(ProviderConfigTestRequest())
    )

    assert response.ok is True
    assert response.latency_ms is not None
    assert calls["base_url"] == "https://provider.example/v1"
    assert calls["model"] == "qwen3.7-flash"


def test_provider_test_reports_adapter_failure_message(tmp_path, monkeypatch) -> None:
    import conflux_weave.provider as provider_module
    from conflux_weave.api_contracts import ProviderConfigTestRequest

    class FailingAdapter:
        def __init__(self, store, config, *, timeout_seconds: float = 20.0) -> None:
            pass

        def complete(self, **kwargs) -> object:
            raise RuntimeError("Provider returned HTTP 401")

    monkeypatch.setattr(provider_module, "OpenAICompatibleChatAdapter", FailingAdapter)
    app, _ = build_config_app(tmp_path, DOTENV_SEEDED)
    response = asyncio.run(
        route(app, "/api/v1/config/provider/test")(ProviderConfigTestRequest())
    )

    assert response.ok is False
    assert "HTTP 401" in response.message
