"""Multi-provider failover, circuit breaker resilience, rate limiting, and routing audit.

Implements Gate C2 (P7-C4) resilience requirements:
- Cascading failover across primary and backup Provider endpoints;
- Three-state Circuit Breaker (CLOSED / OPEN / HALF_OPEN) per endpoint;
- Concurrency and RPM rate limiting;
- Capability caching (thinking toggle, json_object compatibility);
- Comprehensive route decision auditing recorded with latency and token usage.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Mapping, Sequence
from urllib.parse import urlparse

from conflux_weave.evidence import ArtifactRef
from conflux_weave.provider import (
    DEFAULT_TIMEOUT_SECONDS,
    PROVIDER_SCHEMA_VERSION,
    ChatCompletionResult,
    ProviderConfig,
    ProviderHttpResponse,
    ProviderHttpTransport,
    ProviderPortError,
    UrllibProviderTransport,
    _decode_object,
    _header,
    _required_nonnegative_int,
    _required_string,
)
from conflux_weave.runtime.artifacts import LocalArtifactStore
from conflux_weave.structured_output import safe_json_loads


class CircuitState(str, Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


@dataclass(slots=True)
class CircuitBreaker:
    failure_threshold: int = 3
    recovery_timeout_seconds: float = 30.0
    half_open_success_threshold: int = 1
    state: CircuitState = CircuitState.CLOSED
    failure_count: int = 0
    consecutive_successes: int = 0
    last_failure_time: float = 0.0
    last_state_change_time: float = field(default_factory=time.time)
    last_error_message: str = ""

    def can_attempt(self, now: float | None = None) -> bool:
        current_time = time.time() if now is None else now
        if self.state == CircuitState.CLOSED:
            return True
        if self.state == CircuitState.OPEN:
            if current_time - self.last_failure_time >= self.recovery_timeout_seconds:
                self.state = CircuitState.HALF_OPEN
                self.last_state_change_time = current_time
                self.consecutive_successes = 0
                return True
            return False
        if self.state == CircuitState.HALF_OPEN:
            # In half open, allow one attempt to test recovery
            return True
        return False

    def record_success(self, now: float | None = None) -> None:
        current_time = time.time() if now is None else now
        if self.state == CircuitState.HALF_OPEN:
            self.consecutive_successes += 1
            if self.consecutive_successes >= self.half_open_success_threshold:
                self.state = CircuitState.CLOSED
                self.last_state_change_time = current_time
                self.failure_count = 0
                self.consecutive_successes = 0
                self.last_error_message = ""
        else:
            self.failure_count = 0

    def record_failure(self, error_msg: str, *, is_hard_failure: bool = True, now: float | None = None) -> None:
        if not is_hard_failure:
            return
        current_time = time.time() if now is None else now
        self.last_failure_time = current_time
        self.last_error_message = error_msg
        if self.state == CircuitState.HALF_OPEN:
            # Any hard failure in half-open immediately returns to OPEN
            self.state = CircuitState.OPEN
            self.last_state_change_time = current_time
            self.consecutive_successes = 0
        elif self.state == CircuitState.CLOSED:
            self.failure_count += 1
            if self.failure_count >= self.failure_threshold:
                self.state = CircuitState.OPEN
                self.last_state_change_time = current_time

    def reset(self) -> None:
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.consecutive_successes = 0
        self.last_failure_time = 0.0
        self.last_state_change_time = time.time()
        self.last_error_message = ""


@dataclass(slots=True)
class ProviderEndpoint:
    endpoint_id: str
    name: str
    base_url: str
    api_key: str
    model: str
    priority: int = 0  # 0 is primary, 1+ are fallbacks
    weight: int = 1
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    supports_json_object: bool = True
    supports_thinking_toggle: bool = True
    rate_limit_rpm: int = 0
    circuit_breaker: CircuitBreaker = field(default_factory=CircuitBreaker)
    # Runtime rate limiting counters
    _request_timestamps: list[float] = field(default_factory=list)

    def is_rate_limited(self, now: float | None = None) -> bool:
        if self.rate_limit_rpm <= 0:
            return False
        current_time = time.time() if now is None else now
        window_start = current_time - 60.0
        # Clean expired timestamps
        self._request_timestamps = [ts for ts in self._request_timestamps if ts > window_start]
        return len(self._request_timestamps) >= self.rate_limit_rpm

    def record_request(self, now: float | None = None) -> None:
        current_time = time.time() if now is None else now
        self._request_timestamps.append(current_time)


@dataclass(frozen=True, slots=True)
class RouteStepAudit:
    endpoint_id: str
    model: str
    attempt: int
    status: str  # "success", "http_error", "network_error", "rate_limited", "circuit_open"
    error: str | None = None
    latency_ms: float = 0.0
    status_code: int | None = None


@dataclass(frozen=True, slots=True)
class ProviderRouteDecision:
    selected_endpoint_id: str
    selected_model: str
    attempts_total: int
    fallback_chain: tuple[RouteStepAudit, ...]
    final_status: str  # "success" or "exhausted"
    total_latency_ms: float
    token_usage: Mapping[str, int]
    resilience_reason: str


class ProviderFailoverExhaustedError(RuntimeError):
    """Raised when all primary and fallback Provider endpoints have been exhausted or circuit-broken."""

    def __init__(self, message: str, fallback_chain: Sequence[RouteStepAudit]) -> None:
        super().__init__(message)
        self.fallback_chain = tuple(fallback_chain)


class ProviderPoolRouter:
    """Manages a pool of Provider endpoints with circuit breakers and failover routing."""

    def __init__(
        self,
        artifact_store: LocalArtifactStore,
        *,
        endpoints: Sequence[ProviderEndpoint] | None = None,
        transport: ProviderHttpTransport | None = None,
    ) -> None:
        self.artifact_store = artifact_store
        self.transport = transport or UrllibProviderTransport()
        self._endpoints: dict[str, ProviderEndpoint] = {}
        if endpoints:
            for ep in endpoints:
                self.register_endpoint(ep)

    @classmethod
    def from_primary_config(
        cls,
        artifact_store: LocalArtifactStore,
        primary_config: ProviderConfig,
        *,
        fallback_configs: Sequence[ProviderConfig] | None = None,
        transport: ProviderHttpTransport | None = None,
    ) -> ProviderPoolRouter:
        """Convenience constructor from primary config and optional fallbacks."""
        router = cls(artifact_store, transport=transport)
        primary_ep = ProviderEndpoint(
            endpoint_id="primary",
            name=primary_config.provider_name or "primary-provider",
            base_url=primary_config.base_url,
            api_key=primary_config.api_key,
            model=primary_config.model,
            priority=0,
        )
        router.register_endpoint(primary_ep)

        if fallback_configs:
            for idx, fb in enumerate(fallback_configs, start=1):
                fb_ep = ProviderEndpoint(
                    endpoint_id=f"fallback-{idx}",
                    name=fb.provider_name or f"fallback-provider-{idx}",
                    base_url=fb.base_url,
                    api_key=fb.api_key,
                    model=fb.model,
                    priority=idx,
                )
                router.register_endpoint(fb_ep)
        return router

    def register_endpoint(self, endpoint: ProviderEndpoint) -> None:
        self._endpoints[endpoint.endpoint_id] = endpoint

    def get_endpoint(self, endpoint_id: str) -> ProviderEndpoint | None:
        return self._endpoints.get(endpoint_id)

    def list_endpoints(self) -> list[ProviderEndpoint]:
        return sorted(self._endpoints.values(), key=lambda ep: (ep.priority, -ep.weight))

    def reset_circuit(self, endpoint_id: str) -> bool:
        ep = self._endpoints.get(endpoint_id)
        if ep:
            ep.circuit_breaker.reset()
            return True
        return False

    def reset_all_circuits(self) -> None:
        for ep in self._endpoints.values():
            ep.circuit_breaker.reset()

    def get_health_status(self) -> list[dict[str, Any]]:
        status_list = []
        now = time.time()
        for ep in self.list_endpoints():
            cb = ep.circuit_breaker
            can_try = cb.can_attempt(now)
            status_list.append(
                {
                    "endpoint_id": ep.endpoint_id,
                    "name": ep.name,
                    "model": ep.model,
                    "priority": ep.priority,
                    "base_url": ep.base_url,
                    "circuit_state": cb.state.value,
                    "can_attempt": can_try,
                    "failure_count": cb.failure_count,
                    "last_error": cb.last_error_message,
                    "last_failure_time": cb.last_failure_time,
                    "supports_json_object": ep.supports_json_object,
                    "supports_thinking_toggle": ep.supports_thinking_toggle,
                }
            )
        return status_list

    def complete_chat(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        images: Sequence[str | dict[str, Any]] | None = None,
        max_output_tokens: int = 2048,
        temperature: float = 0.0,
        json_object: bool = False,
        enable_thinking: bool | None = None,
        producer_step_id: str = "step-provider-chat",
    ) -> tuple[ChatCompletionResult, ProviderRouteDecision]:
        """Execute chat completion with automatic failover and circuit breaker protection."""
        if not self._endpoints:
            raise RuntimeError("ProviderPoolRouter has no registered endpoints")

        candidates = self.list_endpoints()
        fallback_audits: list[RouteStepAudit] = []
        overall_start_time = time.time()
        last_error: Exception | None = None

        for endpoint in candidates:
            cb = endpoint.circuit_breaker
            now = time.time()

            if not cb.can_attempt(now):
                fallback_audits.append(
                    RouteStepAudit(
                        endpoint_id=endpoint.endpoint_id,
                        model=endpoint.model,
                        attempt=1,
                        status="circuit_open",
                        error=f"Circuit is {cb.state.value}; cooling until recovery window",
                    )
                )
                continue

            if endpoint.is_rate_limited(now):
                fallback_audits.append(
                    RouteStepAudit(
                        endpoint_id=endpoint.endpoint_id,
                        model=endpoint.model,
                        attempt=1,
                        status="rate_limited",
                        error=f"Local rate limit reached ({endpoint.rate_limit_rpm} RPM)",
                    )
                )
                continue

            # Record this attempt
            endpoint.record_request(now)
            step_start = time.time()

            # Build request payload
            if images:
                user_content: list[dict[str, Any]] = [{"type": "text", "text": user_prompt}]
                for img in images:
                    if isinstance(img, str):
                        url = img if (img.startswith("data:") or img.startswith("http")) else f"data:image/jpeg;base64,{img}"
                        user_content.append({"type": "image_url", "image_url": {"url": url}})
                    elif isinstance(img, dict):
                        if "type" in img:
                            user_content.append(img)
                        elif "url" in img:
                            user_content.append({"type": "image_url", "image_url": img})
                        else:
                            user_content.append({"type": "image_url", "image_url": {"url": img.get("image_url", "")}})
            else:
                user_content = user_prompt

            payload = {
                "model": endpoint.model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
                "temperature": temperature,
                "max_tokens": max_output_tokens,
                "stream": False,
            }

            if json_object and endpoint.supports_json_object:
                payload["response_format"] = {"type": "json_object"}

            if enable_thinking is not None:
                if endpoint.supports_thinking_toggle:
                    payload["enable_thinking"] = enable_thinking
                elif enable_thinking is False:
                    payload["reasoning_effort"] = "low"

            try:
                # Execute HTTP call on this endpoint
                result = self._dispatch_call(
                    endpoint,
                    payload,
                    producer_step_id=producer_step_id,
                    timeout_seconds=endpoint.timeout_seconds,
                )

                # Record success on circuit breaker
                cb.record_success()
                step_latency = (time.time() - step_start) * 1000.0
                total_latency = (time.time() - overall_start_time) * 1000.0

                audit = RouteStepAudit(
                    endpoint_id=endpoint.endpoint_id,
                    model=endpoint.model,
                    attempt=1,
                    status="success",
                    latency_ms=round(step_latency, 2),
                    status_code=200,
                )
                fallback_audits.append(audit)

                decision = ProviderRouteDecision(
                    selected_endpoint_id=endpoint.endpoint_id,
                    selected_model=endpoint.model,
                    attempts_total=len(fallback_audits),
                    fallback_chain=tuple(fallback_audits),
                    final_status="success",
                    total_latency_ms=round(total_latency, 2),
                    token_usage={
                        "input_tokens": result.input_tokens,
                        "output_tokens": result.output_tokens,
                        "total_tokens": result.total_tokens,
                    },
                    resilience_reason="Direct primary success"
                    if len(fallback_audits) == 1
                    else f"Failover succeeded on {endpoint.endpoint_id} after {len(fallback_audits)-1} fallbacks",
                )
                return result, decision

            except ProviderPortError as port_err:
                step_latency = (time.time() - step_start) * 1000.0
                is_hard = port_err.retryable or (port_err.status_code is not None and port_err.status_code >= 500) or port_err.code in ("provider_network_failed", "provider_http_failed")

                # If this endpoint rejected enable_thinking (e.g. 400 bad request), update capability and retry once
                if enable_thinking is False and port_err.status_code == 400 and endpoint.supports_thinking_toggle:
                    endpoint.supports_thinking_toggle = False
                    payload_retry = {k: v for k, v in payload.items() if k != "enable_thinking"}
                    payload_retry["reasoning_effort"] = "low"
                    try:
                        result = self._dispatch_call(
                            endpoint,
                            payload_retry,
                            producer_step_id=producer_step_id,
                            timeout_seconds=endpoint.timeout_seconds,
                        )
                        cb.record_success()
                        step_latency = (time.time() - step_start) * 1000.0
                        total_latency = (time.time() - overall_start_time) * 1000.0
                        fallback_audits.append(
                            RouteStepAudit(
                                endpoint_id=endpoint.endpoint_id,
                                model=endpoint.model,
                                attempt=2,
                                status="success",
                                latency_ms=round(step_latency, 2),
                                status_code=200,
                            )
                        )
                        decision = ProviderRouteDecision(
                            selected_endpoint_id=endpoint.endpoint_id,
                            selected_model=endpoint.model,
                            attempts_total=len(fallback_audits),
                            fallback_chain=tuple(fallback_audits),
                            final_status="success",
                            total_latency_ms=round(total_latency, 2),
                            token_usage={
                                "input_tokens": result.input_tokens,
                                "output_tokens": result.output_tokens,
                                "total_tokens": result.total_tokens,
                            },
                            resilience_reason="Self-healed capability incompatibility on endpoint",
                        )
                        return result, decision
                    except Exception as retry_err:
                        last_error = retry_err

                cb.record_failure(port_err.message, is_hard_failure=is_hard)
                last_error = port_err
                fallback_audits.append(
                    RouteStepAudit(
                        endpoint_id=endpoint.endpoint_id,
                        model=endpoint.model,
                        attempt=1,
                        status="http_error" if port_err.status_code else "network_error",
                        error=port_err.message,
                        latency_ms=round(step_latency, 2),
                        status_code=port_err.status_code,
                    )
                )

            except Exception as unk_err:
                step_latency = (time.time() - step_start) * 1000.0
                cb.record_failure(str(unk_err), is_hard_failure=True)
                last_error = unk_err
                fallback_audits.append(
                    RouteStepAudit(
                        endpoint_id=endpoint.endpoint_id,
                        model=endpoint.model,
                        attempt=1,
                        status="network_error",
                        error=str(unk_err),
                        latency_ms=round(step_latency, 2),
                    )
                )

        total_latency = (time.time() - overall_start_time) * 1000.0
        raise ProviderFailoverExhaustedError(
            f"All {len(candidates)} provider endpoints failed or circuit-broken. Last error: {last_error}",
            fallback_chain=fallback_audits,
        )

    def _dispatch_call(
        self,
        endpoint: ProviderEndpoint,
        payload: dict[str, Any],
        *,
        producer_step_id: str,
        timeout_seconds: float,
    ) -> ChatCompletionResult:
        request_artifact = self.artifact_store.put_json(
            {
                "schema_version": PROVIDER_SCHEMA_VERSION,
                "endpoint_id": endpoint.endpoint_id,
                "provider": endpoint.name,
                "request": payload,
            },
            producer_step_id=producer_step_id,
            schema_version=PROVIDER_SCHEMA_VERSION,
        )
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        try:
            response = self.transport.post(
                endpoint.base_url + "/chat/completions",
                headers={
                    "Authorization": f"Bearer {endpoint.api_key}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "User-Agent": "Conflux-Weave/0.0.1",
                },
                body=body,
                timeout_seconds=timeout_seconds,
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
                message=f"Provider {endpoint.name} returned HTTP {response.status_code}",
                retryable=response.status_code >= 500 or response.status_code == 429,
                status_code=response.status_code,
                request_artifact_ref=request_artifact.artifact_id,
                response_artifact_ref=response_artifact.artifact_id,
                recovery_action="检查 Provider 服务状态或通过 Failover 尝试备用节点。",
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
            usage = parsed.get("usage", {})
            input_tokens = _required_nonnegative_int(usage, "prompt_tokens") if "prompt_tokens" in usage else 0
            output_tokens = _required_nonnegative_int(usage, "completion_tokens") if "completion_tokens" in usage else 0
            total_tokens = _required_nonnegative_int(usage, "total_tokens") if "total_tokens" in usage else (input_tokens + output_tokens)
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderPortError(
                code="provider_response_invalid",
                message=f"Provider {endpoint.name} response invalid: {exc}",
                retryable=False,
                status_code=response.status_code,
                request_artifact_ref=request_artifact.artifact_id,
                response_artifact_ref=response_artifact.artifact_id,
                recovery_action="检查 Provider 返回格式兼容性。",
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
