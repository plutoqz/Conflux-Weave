"""Model Context Protocol (MCP) gateway package for Conflux-Weave (P5.2/P5.3)."""

from __future__ import annotations

from conflux_weave.mcp.adapter import mcp_tool_to_harness_spec
from conflux_weave.mcp.client import MCPClient
from conflux_weave.mcp.manager import MCPServerManager
from conflux_weave.mcp.server import MCPServerCore, MCPSSEManager, run_stdio_server
from conflux_weave.mcp.spec import (
    MCPServerConfig,
    MCPToolCallRequest,
    MCPToolCallResult,
    MCPToolInfo,
    MCPTransportType,
)

__all__ = [
    "MCPClient",
    "MCPServerConfig",
    "MCPServerCore",
    "MCPServerManager",
    "MCPSSEManager",
    "MCPToolCallRequest",
    "MCPToolCallResult",
    "MCPToolInfo",
    "MCPTransportType",
    "mcp_tool_to_harness_spec",
    "run_stdio_server",
]
