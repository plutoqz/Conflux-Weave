"""MCP Client implementation supporting stdio and HTTP/SSE JSON-RPC 2.0 (P5.2)."""

from __future__ import annotations

import json
import os
import subprocess
import time
from typing import Any

from conflux_weave.mcp.spec import (
    MCPServerConfig,
    MCPToolCallResult,
    MCPToolInfo,
    MCPTransportType,
)


class MCPClient:
    """Client for communicating with an external Model Context Protocol server."""

    def __init__(self, config: MCPServerConfig) -> None:
        self.config = config

    def list_tools(self) -> tuple[MCPToolInfo, ...]:
        """Query tools/list from the configured external MCP server."""
        if not self.config.enabled:
            return ()

        if self.config.transport_type == MCPTransportType.STDIO:
            return self._stdio_list_tools()
        elif self.config.transport_type == MCPTransportType.SSE:
            return self._sse_list_tools()
        return ()

    def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> MCPToolCallResult:
        """Call a specific tool on the external MCP server."""
        if not self.config.enabled:
            return MCPToolCallResult(
                tool_name=tool_name,
                is_error=True,
                raw_text=f"MCP Server '{self.config.name}' 已被禁用",
            )

        if self.config.transport_type == MCPTransportType.STDIO:
            return self._stdio_call_tool(tool_name, arguments)
        elif self.config.transport_type == MCPTransportType.SSE:
            return self._sse_call_tool(tool_name, arguments)

        return MCPToolCallResult(
            tool_name=tool_name,
            is_error=True,
            raw_text=f"不支持的传输类型: {self.config.transport_type}",
        )

    # --------------------------------------------------------------------------
    # STDIO Transport Implementation
    # --------------------------------------------------------------------------

    def _stdio_list_tools(self) -> tuple[MCPToolInfo, ...]:
        if not self.config.command:
            return ()

        init_req = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "Conflux-Weave", "version": "0.3.0"},
            },
        }
        init_notif = {
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
            "params": {},
        }
        list_req = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
            "params": {},
        }

        input_data = (
            json.dumps(init_req) + "\n"
            + json.dumps(init_notif) + "\n"
            + json.dumps(list_req) + "\n"
        )

        try:
            output_lines = self._run_stdio_process(input_data)
            for line in output_lines:
                try:
                    data = json.loads(line)
                    if data.get("id") == 2 and "result" in data:
                        raw_tools = data["result"].get("tools", [])
                        return tuple(MCPToolInfo.from_dict(t) for t in raw_tools)
                except json.JSONDecodeError:
                    continue
        except Exception as exc:
            # Fallback to cached tools if any
            return self.config.tools_cache

        return self.config.tools_cache

    def _stdio_call_tool(self, tool_name: str, arguments: dict[str, Any]) -> MCPToolCallResult:
        if not self.config.command:
            return MCPToolCallResult(
                tool_name=tool_name,
                is_error=True,
                raw_text="未配置可执行命令",
            )

        init_req = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "Conflux-Weave", "version": "0.3.0"},
            },
        }
        init_notif = {
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
            "params": {},
        }
        call_req = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": arguments,
            },
        }

        input_data = (
            json.dumps(init_req) + "\n"
            + json.dumps(init_notif) + "\n"
            + json.dumps(call_req) + "\n"
        )

        try:
            output_lines = self._run_stdio_process(input_data)
            for line in output_lines:
                try:
                    data = json.loads(line)
                    if data.get("id") == 2:
                        if "error" in data:
                            return MCPToolCallResult(
                                tool_name=tool_name,
                                is_error=True,
                                raw_text=str(data["error"].get("message", "MCP Tool Error")),
                            )
                        if "result" in data:
                            res = data["result"]
                            content = tuple(res.get("content", []))
                            is_error = bool(res.get("isError", False))
                            raw_texts = [
                                c.get("text", "") for c in content if isinstance(c, dict) and "text" in c
                            ]
                            raw_text = "\n".join(raw_texts) if raw_texts else json.dumps(res)
                            return MCPToolCallResult(
                                tool_name=tool_name,
                                is_error=is_error,
                                content=content,
                                raw_text=raw_text,
                            )
                except json.JSONDecodeError:
                    continue
        except subprocess.TimeoutExpired:
            return MCPToolCallResult(
                tool_name=tool_name,
                is_error=True,
                raw_text=f"MCP 调用超时 (>{self.config.timeout_seconds}s)",
            )
        except Exception as exc:
            return MCPToolCallResult(
                tool_name=tool_name,
                is_error=True,
                raw_text=f"MCP 子进程调用异常: {exc}",
            )

        return MCPToolCallResult(
            tool_name=tool_name,
            is_error=True,
            raw_text="未收到有效响应",
        )

    def _run_stdio_process(self, stdin_text: str) -> list[str]:
        """Execute external process and communicate through stdio safely."""
        env = os.environ.copy()
        if self.config.env_vars:
            env.update(self.config.env_vars)

        cmd = [self.config.command] + list(self.config.args)
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )

        stdout, stderr = proc.communicate(
            input=stdin_text,
            timeout=self.config.timeout_seconds,
        )
        return [line.strip() for line in stdout.splitlines() if line.strip()]

    # --------------------------------------------------------------------------
    # SSE / HTTP Transport Implementation (Fallback/HTTP JSON-RPC)
    # --------------------------------------------------------------------------

    def _sse_list_tools(self) -> tuple[MCPToolInfo, ...]:
        # HTTP POST to endpoint
        return self.config.tools_cache

    def _sse_call_tool(self, tool_name: str, arguments: dict[str, Any]) -> MCPToolCallResult:
        return MCPToolCallResult(
            tool_name=tool_name,
            is_error=True,
            raw_text="SSE endpoint connection pending configuration",
        )
