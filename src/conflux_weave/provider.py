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

from dotenv import dotenv_values

from conflux_weave.evidence import ArtifactRef
from conflux_weave.runtime.artifacts import LocalArtifactStore


PROVIDER_SCHEMA_VERSION = "conflux-weave.openai-chat-completion.v1"
EMBEDDING_SCHEMA_VERSION = "conflux-weave.embedding.v1"
RERANK_SCHEMA_VERSION = "conflux-weave.rerank.v1"
# 思考型模型（glm-5.3-flash 等）会先输出大量 reasoning 再给正文，研究链路的
# 大提示词调用普遍超过 60s；240s 覆盖 8k 输出预算的最慢合法调用。
DEFAULT_TIMEOUT_SECONDS = 240.0


@dataclass(frozen=True, slots=True)
class ProviderConfig:
    base_url: str
    api_key: str
    model: str
    provider_name: str = "openai-compatible"
    embedding_model: str | None = None
    reranker_model: str | None = None

    @classmethod
    def from_environment(cls, dotenv_path: Path | None = None) -> ProviderConfig:
        dotenv_config = dotenv_values(dotenv_path) if dotenv_path else {}

        def read_setting(name: str) -> str:
            value = os.environ.get(name)
            if value is None:
                value = dotenv_config.get(name)
            return value.strip() if isinstance(value, str) else ""

        base_url = read_setting("CONFLUX_WEAVE_PROVIDER_BASE_URL")
        api_key = read_setting("CONFLUX_WEAVE_PROVIDER_API_KEY")
        model = read_setting("CONFLUX_WEAVE_PROVIDER_MODEL")
        embedding_model = read_setting("CONFLUX_WEAVE_PROVIDER_EMBEDDING_MODEL") or None
        reranker_model = read_setting("CONFLUX_WEAVE_PROVIDER_RERANKER_MODEL") or None
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
        return cls(base_url=base_url.rstrip("/"), api_key=api_key, model=model,
                   embedding_model=embedding_model, reranker_model=reranker_model)


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
        # 上限护栏只防荒谬值；写作阶段（W3.2.1）10 条 Claim 的富文本报告需要
        # 8k 输出预算，4096 会把 JSON 截断导致整篇降级。
        if not 1 <= max_output_tokens <= 16384:
            raise ValueError("max_output_tokens must be between 1 and 16384")
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

        def _attempt(attempt_payload: dict, *, attempt: int, automatic_retry: bool) -> ChatCompletionResult:
            request_artifact = self.artifact_store.put_json(
                {
                    "schema_version": PROVIDER_SCHEMA_VERSION,
                    "provider": self.config.provider_name,
                    "endpoint": "/chat/completions",
                    "request": attempt_payload,
                    "secret_recorded": False,
                    "attempt": attempt,
                    "automatic_retry": automatic_retry,
                },
                producer_step_id=producer_step_id,
                schema_version=PROVIDER_SCHEMA_VERSION,
            )
            body = json.dumps(attempt_payload, ensure_ascii=False).encode("utf-8")
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

        try:
            return _attempt(payload, attempt=1, automatic_retry=False)
        except ProviderPortError as exc:
            # 一次确定性能力重试（W3.2.1）：部分思考型模型（如 glm-5.3-flash）不支持
            # 关闭思考，网关会以 HTTP 400（code 1210）或无效响应体拒绝
            # enable_thinking=false。此时去掉该参数重试一次；成功路径不受影响，
            # 二次失败按原错误 fail-closed，5xx/429 不自动重试（避免重复计费调用）。
            # 注意：首次失败调用的用量不进入任何 manifest 引用图（成本少记），
            # 换取对异构模型家族的兼容；该权衡已记录。
            rejected_contract = exc.code == "provider_response_invalid" or (
                exc.code == "provider_http_failed" and exc.status_code == 400
            )
            if enable_thinking is False and rejected_contract:
                # 该模型家族拒绝 enable_thinking 但支持 reasoning_effort 控制思考
                # 强度（网关契约：low/high/max）。low 把 reasoning 压到很小，避免
                # 思考吃满输出预算导致正文为空。
                retry_payload = {
                    key: value
                    for key, value in payload.items()
                    if key != "enable_thinking"
                }
                retry_payload["reasoning_effort"] = "low"
                return _attempt(retry_payload, attempt=2, automatic_retry=True)
            raise


@dataclass(frozen=True, slots=True)
class EmbeddingResult:
    model: str
    vectors: tuple[tuple[float, ...], ...]
    input_tokens: int | None
    request_artifact: ArtifactRef
    response_artifact: ArtifactRef


class OpenAICompatibleEmbeddingAdapter:
    """OpenAI-compatible embeddings adapter with raw request/response evidence."""

    def __init__(self, artifact_store: LocalArtifactStore, config: ProviderConfig, *, transport: ProviderHttpTransport | None = None, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS, model: str | None = None) -> None:
        self.artifact_store, self.config = artifact_store, config
        self.transport = transport or UrllibProviderTransport()
        self.timeout_seconds = timeout_seconds
        self.model = model or config.embedding_model or "text-embedding-v4"

    def embed(self, texts: list[str] | tuple[str, ...], *, producer_step_id: str = "step-provider-embedding") -> EmbeddingResult:
        if not texts or any(not text.strip() for text in texts):
            raise ValueError("texts must contain non-empty values")
        payload = {"model": self.model, "input": list(texts)}
        request = self.artifact_store.put_json({"schema_version": EMBEDDING_SCHEMA_VERSION, "endpoint": "/embeddings", "request": payload, "secret_recorded": False}, producer_step_id=producer_step_id, schema_version=EMBEDDING_SCHEMA_VERSION)
        response = self.transport.post(self.config.base_url + "/embeddings", headers={"Authorization": f"Bearer {self.config.api_key}", "Content-Type": "application/json", "Accept": "application/json"}, body=json.dumps(payload, ensure_ascii=False).encode(), timeout_seconds=self.timeout_seconds)
        response_artifact = self.artifact_store.put_bytes(response.body, media_type="application/json", producer_step_id=producer_step_id, schema_version=EMBEDDING_SCHEMA_VERSION + ".response")
        if response.status_code != 200:
            raise ProviderPortError(code="provider_http_failed", message=f"Provider returned HTTP {response.status_code}", retryable=response.status_code >= 500 or response.status_code == 429, status_code=response.status_code, request_artifact_ref=request.artifact_id, response_artifact_ref=response_artifact.artifact_id, recovery_action="检查 Embedding Provider 原始响应。")
        try:
            parsed = json.loads(response.body)
            data = parsed["data"]
            vectors = tuple(tuple(float(value) for value in item["embedding"]) for item in sorted(data, key=lambda item: item.get("index", 0)))
            if len(vectors) != len(texts) or not vectors or len({len(vector) for vector in vectors}) != 1:
                raise ValueError("embedding count or dimensions invalid")
            usage = parsed.get("usage", {})
            tokens = usage.get("prompt_tokens") if isinstance(usage, dict) else None
            tokens = int(tokens) if isinstance(tokens, int) else None
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ProviderPortError(code="provider_response_invalid", message=f"Embedding response contract invalid: {exc}", retryable=False, request_artifact_ref=request.artifact_id, response_artifact_ref=response_artifact.artifact_id, recovery_action="保留原始响应并检查 Embedding 合同。") from exc
        return EmbeddingResult(self.model, vectors, tokens, request, response_artifact)


@dataclass(frozen=True, slots=True)
class RerankResult:
    model: str
    scores: tuple[float, ...]
    ranked_indices: tuple[int, ...]
    request_artifact: ArtifactRef
    response_artifact: ArtifactRef


class OpenAICompatibleRerankerAdapter:
    def __init__(self, artifact_store: LocalArtifactStore, config: ProviderConfig, *, transport: ProviderHttpTransport | None = None, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS, model: str | None = None) -> None:
        self.artifact_store, self.config = artifact_store, config
        self.transport = transport or UrllibProviderTransport()
        self.timeout_seconds = timeout_seconds
        self.model = model or config.reranker_model or "qwen3-rerank"

    def rerank(self, query: str, documents: list[str] | tuple[str, ...], *, top_n: int | None = None, producer_step_id: str = "step-provider-rerank") -> RerankResult:
        if not query.strip() or not documents or any(not item.strip() for item in documents):
            raise ValueError("query and documents must not be empty")
        payload = {"model": self.model, "query": query, "documents": list(documents)}
        if top_n is not None: payload["top_n"] = top_n
        request = self.artifact_store.put_json({"schema_version": RERANK_SCHEMA_VERSION, "endpoint": "/rerank", "request": payload, "secret_recorded": False}, producer_step_id=producer_step_id, schema_version=RERANK_SCHEMA_VERSION)
        response = self.transport.post(self.config.base_url + "/rerank", headers={"Authorization": f"Bearer {self.config.api_key}", "Content-Type": "application/json", "Accept": "application/json"}, body=json.dumps(payload, ensure_ascii=False).encode(), timeout_seconds=self.timeout_seconds)
        response_artifact = self.artifact_store.put_bytes(response.body, media_type="application/json", producer_step_id=producer_step_id, schema_version=RERANK_SCHEMA_VERSION + ".response")
        if response.status_code != 200:
            raise ProviderPortError(code="provider_http_failed", message=f"Provider returned HTTP {response.status_code}", retryable=response.status_code >= 500 or response.status_code == 429, status_code=response.status_code, request_artifact_ref=request.artifact_id, response_artifact_ref=response_artifact.artifact_id, recovery_action="检查 Reranker Provider 原始响应。")
        try:
            data = json.loads(response.body)["results"]
            pairs = [(int(item["index"]), float(item["relevance_score"])) for item in data]
            pairs.sort(key=lambda item: (-item[1], item[0]))
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ProviderPortError(code="provider_response_invalid", message=f"Rerank response contract invalid: {exc}", retryable=False, request_artifact_ref=request.artifact_id, response_artifact_ref=response_artifact.artifact_id, recovery_action="保留原始响应并检查 Rerank 合同。") from exc
        return RerankResult(self.model, tuple(score for _, score in pairs), tuple(index for index, _ in pairs), request, response_artifact)


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
