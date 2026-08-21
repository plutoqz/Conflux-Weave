"""Minimal OpenAI-compatible chat Provider adapter for W1.5 live runs."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from dotenv import load_dotenv

from conflux_weave.evidence import ArtifactRef
from conflux_weave.runtime.artifacts import LocalArtifactStore


PROVIDER_SCHEMA_VERSION = "conflux-weave.openai-chat-completion.v1"
DEFAULT_TIMEOUT_SECONDS = 60.0


@dataclass(frozen=True, slots=True)
class ProviderConfig:
    base_url: str
    api_key: str
    model: str
    provider_name: str = "openai-compatible"

    @classmethod
    def from_environment(cls, dotenv_path: Path | None = None) -> ProviderConfig:
        load_dotenv(dotenv_path=dotenv_path, override=False)
        base_url = os.environ.get("CONFLUX_WEAVE_PROVIDER_BASE_URL", "").strip()
        api_key = os.environ.get("CONFLUX_WEAVE_PROVIDER_API_KEY", "").strip()
        model = os.environ.get("CONFLUX_WEAVE_PROVIDER_MODEL", "").strip()
        missing = [
            name
            for name, value in (
                ("CONFLUX_WEAVE_PROVIDER_BASE_URL", base_url),
                ("CONFLUX_WEAVE_PROVIDER_API_KEY", api_key),
                ("CONFLUX_WEAVE_PROVIDER_MODEL", model),
            )
            if not value
        ]
        if missing:
            raise ProviderConfigurationError(
                "missing Provider configuration: " + ", ".join(missing)
            )
        parsed = urlparse(base_url)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ProviderConfigurationError(
                "CONFLUX_WEAVE_PROVIDER_BASE_URL must be an HTTPS URL"
            )
        return cls(base_url=base_url.rstrip("/"), api_key=api_key, model=model)


@dataclass(frozen=True, slots=True)
class ProviderHttpResponse:
    status_code: int
    body: bytes
    headers: Mapping[str, str]


class ProviderHttpTransport(Protocol):
    def post(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        body: bytes,
        timeout_seconds: float,
    ) -> ProviderHttpResponse: ...


class UrllibProviderTransport:
    def post(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        body: bytes,
        timeout_seconds: float,
    ) -> ProviderHttpResponse:
        request = Request(url, data=body, headers=dict(headers), method="POST")
        try:
            with urlopen(request, timeout=timeout_seconds) as response:
                return ProviderHttpResponse(
                    status_code=response.status,
                    body=response.read(),
                    headers=dict(response.headers.items()),
                )
        except HTTPError as exc:
            return ProviderHttpResponse(
                status_code=exc.code,
                body=exc.read(),
                headers=dict(exc.headers.items()) if exc.headers else {},
            )
        except (URLError, TimeoutError, OSError) as exc:
            raise ProviderPortError(
                code="provider_network_failed",
                message=f"Provider request failed: {exc}",
                retryable=True,
                recovery_action="检查 Provider 网络和服务状态后显式创建新 Run。",
            ) from exc


class ProviderConfigurationError(ValueError):
    """Raised before a live request when local Provider configuration is invalid."""


class ProviderPortError(RuntimeError):
    def __init__(
        self,
        *,
        code: str,
        message: str,
        retryable: bool,
        status_code: int | None = None,
        request_artifact_ref: str | None = None,
        response_artifact_ref: str | None = None,
        recovery_action: str,
    ) -> None:
        self.code = code
        self.retryable = retryable
        self.status_code = status_code
        self.request_artifact_ref = request_artifact_ref
        self.response_artifact_ref = response_artifact_ref
        self.recovery_action = recovery_action
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class ChatCompletionResult:
    response_id: str
    model: str
    content: str
    finish_reason: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    request_artifact: ArtifactRef
    response_artifact: ArtifactRef


class OpenAICompatibleChatAdapter:
    def __init__(
        self,
        artifact_store: LocalArtifactStore,
        config: ProviderConfig,
        *,
        transport: ProviderHttpTransport | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self.artifact_store = artifact_store
        self.config = config
        self.transport = transport or UrllibProviderTransport()
        self.timeout_seconds = timeout_seconds

    def complete(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        max_output_tokens: int = 2048,
        temperature: float = 0.0,
        json_object: bool = False,
        enable_thinking: bool | None = None,
        producer_step_id: str = "step-provider-chat",
    ) -> ChatCompletionResult:
        if not system_prompt.strip() or not user_prompt.strip():
            raise ValueError("system_prompt and user_prompt must not be empty")
        if not 1 <= max_output_tokens <= 4096:
            raise ValueError("max_output_tokens must be between 1 and 4096")
        if not 0.0 <= temperature <= 2.0:
            raise ValueError("temperature must be between 0 and 2")

        payload = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature,
            "max_tokens": max_output_tokens,
            "stream": False,
        }
        if json_object:
            payload["response_format"] = {"type": "json_object"}
        if enable_thinking is not None:
            payload["enable_thinking"] = enable_thinking
        request_artifact = self.artifact_store.put_json(
            {
                "schema_version": PROVIDER_SCHEMA_VERSION,
                "provider": self.config.provider_name,
                "endpoint": "/chat/completions",
                "request": payload,
                "secret_recorded": False,
                "attempt": 1,
                "automatic_retry": False,
            },
            producer_step_id=producer_step_id,
            schema_version=PROVIDER_SCHEMA_VERSION,
        )
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        try:
            response = self.transport.post(
                self.config.base_url + "/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.config.api_key}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "User-Agent": "Conflux-Weave/0.0.1",
                },
                body=body,
                timeout_seconds=self.timeout_seconds,
            )
        except ProviderPortError as exc:
            exc.request_artifact_ref = request_artifact.artifact_id
            raise

        response_artifact = self.artifact_store.put_bytes(
            response.body,
            media_type=_header(response.headers, "Content-Type") or "application/json",
            producer_step_id=producer_step_id,
            schema_version="openai-compatible.chat-completions.response",
        )
        if response.status_code != 200:
            raise ProviderPortError(
                code="provider_http_failed",
                message=f"Provider returned HTTP {response.status_code}",
                retryable=response.status_code >= 500 or response.status_code == 429,
                status_code=response.status_code,
                request_artifact_ref=request_artifact.artifact_id,
                response_artifact_ref=response_artifact.artifact_id,
                recovery_action="检查原始响应、模型权限和余额后显式创建新 Run。",
            )

        parsed = _decode_object(response.body, request_artifact, response_artifact)
        try:
            response_id = _required_string(parsed, "id")
            response_model = _required_string(parsed, "model")
            choices = parsed["choices"]
            if not isinstance(choices, list) or len(choices) != 1:
                raise TypeError("choices must contain exactly one item")
            choice = choices[0]
            if not isinstance(choice, dict):
                raise TypeError("choice must be an object")
            message = choice["message"]
            if not isinstance(message, dict):
                raise TypeError("message must be an object")
            content = _required_string(message, "content")
            finish_reason = _required_string(choice, "finish_reason")
            usage = parsed["usage"]
            if not isinstance(usage, dict):
                raise TypeError("usage must be an object")
            input_tokens = _required_nonnegative_int(usage, "prompt_tokens")
            output_tokens = _required_nonnegative_int(usage, "completion_tokens")
            total_tokens = _required_nonnegative_int(usage, "total_tokens")
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderPortError(
                code="provider_response_invalid",
                message=f"Provider response contract invalid: {exc}",
                retryable=False,
                status_code=response.status_code,
                request_artifact_ref=request_artifact.artifact_id,
                response_artifact_ref=response_artifact.artifact_id,
                recovery_action="保留原始响应并检查 Provider 兼容合同，不要从无效响应生成回答。",
            ) from exc
        return ChatCompletionResult(
            response_id=response_id,
            model=response_model,
            content=content,
            finish_reason=finish_reason,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            request_artifact=request_artifact,
            response_artifact=response_artifact,
        )


def _decode_object(
    body: bytes, request_artifact: ArtifactRef, response_artifact: ArtifactRef
) -> dict[str, Any]:
    try:
        parsed = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProviderPortError(
            code="provider_response_invalid",
            message=f"Provider response is not valid JSON: {exc}",
            retryable=False,
            request_artifact_ref=request_artifact.artifact_id,
            response_artifact_ref=response_artifact.artifact_id,
            recovery_action="保留原始响应并检查 Provider 兼容合同。",
        ) from exc
    if not isinstance(parsed, dict):
        raise ProviderPortError(
            code="provider_response_invalid",
            message="Provider response root must be an object",
            retryable=False,
            request_artifact_ref=request_artifact.artifact_id,
            response_artifact_ref=response_artifact.artifact_id,
            recovery_action="保留原始响应并检查 Provider 兼容合同。",
        )
    return parsed


def _required_string(value: Mapping[str, Any], key: str) -> str:
    item = value[key]
    if not isinstance(item, str) or not item.strip():
        raise TypeError(f"{key} must be a non-empty string")
    return item


def _required_nonnegative_int(value: Mapping[str, Any], key: str) -> int:
    item = value[key]
    if not isinstance(item, int) or isinstance(item, bool) or item < 0:
        raise TypeError(f"{key} must be a non-negative integer")
    return item


def _header(headers: Mapping[str, str], name: str) -> str | None:
    for key, value in headers.items():
        if key.casefold() == name.casefold():
            return value
    return None
