"""Local Provider configuration persistence for the Workbench Settings view.

Reads and writes the ``CONFLUX_WEAVE_PROVIDER_*`` keys of the local dotenv file.
The API key is write-only: it is never returned to callers, logged, or stored in
the database. The runtime reads Provider configuration once at process start, so
writes take effect after ``conflux-weave serve`` restarts.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values
from urllib.parse import urlparse

PROVIDER_BASE_URL_KEY = "CONFLUX_WEAVE_PROVIDER_BASE_URL"
PROVIDER_API_KEY_KEY = "CONFLUX_WEAVE_PROVIDER_API_KEY"
PROVIDER_MODEL_KEY = "CONFLUX_WEAVE_PROVIDER_MODEL"
PROVIDER_EMBEDDING_MODEL_KEY = "CONFLUX_WEAVE_PROVIDER_EMBEDDING_MODEL"
PROVIDER_RERANKER_MODEL_KEY = "CONFLUX_WEAVE_PROVIDER_RERANKER_MODEL"
PROVIDER_ENGINE_MODEL_KEY = "CONFLUX_WEAVE_PROVIDER_ENGINE_MODEL"

_PROVIDER_KEYS = (
    PROVIDER_BASE_URL_KEY,
    PROVIDER_API_KEY_KEY,
    PROVIDER_MODEL_KEY,
    PROVIDER_EMBEDDING_MODEL_KEY,
    PROVIDER_RERANKER_MODEL_KEY,
    PROVIDER_ENGINE_MODEL_KEY,
)


class ConfigValidationError(ValueError):
    """Raised when submitted Provider configuration is invalid."""


@dataclass(frozen=True, slots=True)
class ProviderConfigView:
    """Sanitized Provider configuration; the API key is never included."""

    base_url: str
    model: str
    embedding_model: str
    reranker_model: str
    engine_model: str
    api_key_configured: bool
    api_key_hint: str | None

    @classmethod
    def from_env(cls, dotenv_path: Path | None) -> ProviderConfigView:
        values = _read_values(dotenv_path)
        api_key = values[PROVIDER_API_KEY_KEY]
        return _view_from_values(values, api_key)


def _view_from_dotenv(dotenv_path: Path | None) -> ProviderConfigView:
    """Build the view from the dotenv file itself, ignoring process env vars."""

    dotenv_config = dotenv_values(dotenv_path) if dotenv_path else {}
    values: dict[str, str] = {}
    for key in _PROVIDER_KEYS:
        value = dotenv_config.get(key)
        values[key] = value.strip() if isinstance(value, str) else ""
    api_key = values[PROVIDER_API_KEY_KEY]
    return _view_from_values(values, api_key)


def _view_from_values(values: dict[str, str], api_key: str) -> ProviderConfigView:
    return ProviderConfigView(
        base_url=values[PROVIDER_BASE_URL_KEY],
        model=values[PROVIDER_MODEL_KEY],
        embedding_model=values[PROVIDER_EMBEDDING_MODEL_KEY],
        reranker_model=values[PROVIDER_RERANKER_MODEL_KEY],
        engine_model=values[PROVIDER_ENGINE_MODEL_KEY],
        api_key_configured=bool(api_key),
        api_key_hint=_mask_key(api_key),
    )


def _read_values(dotenv_path: Path | None) -> dict[str, str]:
    import os

    dotenv_config = dotenv_values(dotenv_path) if dotenv_path else {}
    values: dict[str, str] = {}
    for key in _PROVIDER_KEYS:
        value = os.environ.get(key)
        if value is None:
            value = dotenv_config.get(key)
        values[key] = value.strip() if isinstance(value, str) else ""
    return values


def _mask_key(api_key: str) -> str | None:
    if not api_key:
        return None
    return f"…{api_key[-4:]}" if len(api_key) > 4 else "已设置"


def validate_provider_update(
    *,
    base_url: str,
    model: str,
) -> None:
    parsed = urlparse(base_url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ConfigValidationError(
            "服务地址必须是可访问的 HTTPS URL。"
        )
    if not model.strip():
        raise ConfigValidationError("Chat 模型名称不能为空。")


def update_provider(
    dotenv_path: Path,
    *,
    base_url: str,
    api_key: str | None,
    model: str,
    embedding_model: str | None,
    reranker_model: str | None,
    engine_model: str | None = None,
) -> ProviderConfigView:
    """Persist Provider keys into the dotenv file, preserving unrelated lines.

    Environment variables take precedence for the running process; a written
    dotenv value is shadowed by a stale exported variable until restart with a
    clean environment. Existing comments and ordering are kept.
    """

    validate_provider_update(base_url=base_url, model=model)
    updates = {
        PROVIDER_BASE_URL_KEY: base_url.strip(),
        PROVIDER_MODEL_KEY: model.strip(),
        PROVIDER_EMBEDDING_MODEL_KEY: (embedding_model or "").strip(),
        PROVIDER_RERANKER_MODEL_KEY: (reranker_model or "").strip(),
        PROVIDER_ENGINE_MODEL_KEY: (engine_model or "").strip(),
    }
    if api_key is not None and api_key.strip():
        updates[PROVIDER_API_KEY_KEY] = api_key.strip()

    parent = dotenv_path.parent
    if str(parent):
        parent.mkdir(parents=True, exist_ok=True)

    lines: list[str] = []
    seen: set[str] = set()
    if dotenv_path.exists():
        for raw in dotenv_path.read_text(encoding="utf-8").splitlines():
            stripped = raw.strip()
            if stripped.startswith("#") or "=" not in raw:
                lines.append(raw)
                continue
            key = raw.split("=", 1)[0].strip()
            if key in updates:
                lines.append(f"{key}={updates[key]}")
                seen.add(key)
            else:
                lines.append(raw)
    for key in _PROVIDER_KEYS:
        if key in updates and key not in seen:
            lines.append(f"{key}={updates[key]}")
            seen.add(key)

    tmp_path = dotenv_path.with_name(dotenv_path.name + ".tmp")
    tmp_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    tmp_path.replace(dotenv_path)
    return _view_from_dotenv(dotenv_path)


__all__ = [
    "ConfigValidationError",
    "ProviderConfigView",
    "update_provider",
    "validate_provider_update",
]
