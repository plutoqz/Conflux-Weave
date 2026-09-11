"""Adapter to bridge MCP tools to Harness ToolSpec contracts (P5.2)."""

from __future__ import annotations

from typing import Any

from conflux_weave.harness.contracts import (
    HARNESS_SCHEMA_VERSION,
    ToolSideEffect,
    ToolSpec,
)
from conflux_weave.mcp.spec import MCPToolInfo


def mcp_tool_to_harness_spec(
    server_id: str,
    tool: MCPToolInfo,
    timeout_seconds: float = 30.0,
) -> ToolSpec:
    """Convert an external MCPToolInfo into a framework-independent Harness ToolSpec."""
    tool_id = f"mcp.{server_id}.{tool.name}"
    description = tool.description or f"External MCP tool '{tool.name}' from server '{server_id}'"
    input_schema = dict(tool.input_schema) if tool.input_schema else {"type": "object"}
    output_schema = {
        "type": "object",
        "properties": {
            "content": {"type": "array"},
            "is_error": {"type": "boolean"},
            "raw_text": {"type": "string"},
        },
    }

    return ToolSpec(
        tool_id=tool_id,
        version="1.0.0",
        description=description,
        input_schema=input_schema,
        output_schema=output_schema,
        side_effect_class=ToolSideEffect.REPLAYABLE_EXTERNAL_READ,
        required_permissions=(f"mcp:{server_id}",),
        timeout_seconds=max(timeout_seconds, 1.0),
        schema_version=HARNESS_SCHEMA_VERSION,
    )
