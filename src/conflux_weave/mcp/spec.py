"""Data specifications for MCP client and server gateway (P5.2)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class MCPTransportType(StrEnum):
    STDIO = "stdio"
    SSE = "sse"


@dataclass(frozen=True, slots=True)
class MCPToolInfo:
    name: str
    description: str
    input_schema: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MCPToolInfo:
        return cls(
            name=str(data.get("name", "")),
            description=str(data.get("description", "")),
            input_schema=dict(data.get("inputSchema") or data.get("input_schema") or {"type": "object"}),
        )


@dataclass(frozen=True, slots=True)
class MCPServerConfig:
    server_id: str
    name: str
    transport_type: MCPTransportType
    command: str | None = None
    args: tuple[str, ...] = ()
    url: str | None = None
    env_vars: dict[str, str] = field(default_factory=dict)
    enabled: bool = True
    timeout_seconds: float = 30.0
    tools_cache: tuple[MCPToolInfo, ...] = ()
    last_connected_at: str | None = None
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["transport_type"] = self.transport_type.value
        result["tools_cache"] = [t.to_dict() for t in self.tools_cache]
        return result


@dataclass(frozen=True, slots=True)
class MCPToolCallRequest:
    server_id: str
    tool_name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class MCPToolCallResult:
    tool_name: str
    is_error: bool
    content: tuple[dict[str, Any], ...] = ()
    raw_text: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
