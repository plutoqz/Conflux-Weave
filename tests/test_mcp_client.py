"""Unit and integration tests for MCP Client, Adapter, Manager and API (P5.2)."""

from __future__ import annotations

import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from conflux_weave.harness.contracts import ToolSideEffect
from conflux_weave.mcp.adapter import mcp_tool_to_harness_spec
from conflux_weave.mcp.client import MCPClient
from conflux_weave.mcp.manager import MCPServerManager
from conflux_weave.mcp.spec import (
    MCPServerConfig,
    MCPToolCallRequest,
    MCPToolInfo,
    MCPTransportType,
)


MOCK_MCP_SERVER_SCRIPT = """
import sys
import json

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        req = json.loads(line)
    except Exception:
        continue
    
    req_id = req.get("id")
    method = req.get("method")
    
    if method == "initialize":
        res = {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "mock-mcp-server", "version": "1.0.0"}
            }
        }
        sys.stdout.write(json.dumps(res) + "\\n")
        sys.stdout.flush()
    elif method == "notifications/initialized":
        # Notification, no response
        pass
    elif method == "tools/list":
        res = {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "tools": [
                    {
                        "name": "mock_echo",
                        "description": "Mock echo tool for testing",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "message": {"type": "string"}
                            },
                            "required": ["message"]
                        }
                    },
                    {
                        "name": "mock_calc",
                        "description": "Mock calculator tool",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "a": {"type": "number"},
                                "b": {"type": "number"}
                            },
                            "required": ["a", "b"]
                        }
                    }
                ]
            }
        }
        sys.stdout.write(json.dumps(res) + "\\n")
        sys.stdout.flush()
    elif method == "tools/call":
        params = req.get("params", {})
        name = params.get("name")
        args = params.get("arguments", {})
        if name == "mock_echo":
            msg = args.get("message", "")
            res = {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": f"Echo: {msg}"}],
                    "isError": False
                }
            }
        elif name == "mock_calc":
            a = args.get("a", 0)
            b = args.get("b", 0)
            res = {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": f"Result: {a + b}"}],
                    "isError": False
                }
            }
        else:
            res = {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": f"Unknown tool: {name}"}
            }
        sys.stdout.write(json.dumps(res) + "\\n")
        sys.stdout.flush()
"""


def test_mcp_adapter_converts_to_harness_spec() -> None:
    tool_info = MCPToolInfo(
        name="web_search",
        description="Search the web for literature",
        input_schema={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    )

    spec = mcp_tool_to_harness_spec("brave", tool_info, timeout_seconds=45.0)

    assert spec.tool_id == "mcp.brave.web_search"
    assert spec.version == "1.0.0"
    assert "Search the web" in spec.description
    assert spec.side_effect_class == ToolSideEffect.REPLAYABLE_EXTERNAL_READ
    assert spec.required_permissions == ("mcp:brave",)
    assert spec.timeout_seconds == 45.0
    assert "content" in spec.output_schema["properties"]


def test_mcp_client_stdio_communication(tmp_path: Path) -> None:
    server_script = tmp_path / "mock_server.py"
    server_script.write_text(MOCK_MCP_SERVER_SCRIPT, encoding="utf-8")

    config = MCPServerConfig(
        server_id="test_stdio",
        name="Test Stdio MCP Server",
        transport_type=MCPTransportType.STDIO,
        command=sys.executable,
        args=(str(server_script),),
        timeout_seconds=10.0,
    )

    client = MCPClient(config)

    # 1. list_tools
    tools = client.list_tools()
    assert len(tools) == 2
    tool_names = {t.name for t in tools}
    assert tool_names == {"mock_echo", "mock_calc"}

    # 2. call_tool mock_echo
    res_echo = client.call_tool("mock_echo", {"message": "hello world"})
    assert not res_echo.is_error
    assert "Echo: hello world" in res_echo.raw_text

    # 3. call_tool mock_calc
    res_calc = client.call_tool("mock_calc", {"a": 40, "b": 2})
    assert not res_calc.is_error
    assert "Result: 42" in res_calc.raw_text

    # 4. call_tool unknown
    res_unknown = client.call_tool("non_existent", {})
    assert res_unknown.is_error
    assert "Unknown tool" in res_unknown.raw_text

    # 5. disabled server
    disabled_config = MCPServerConfig(
        server_id="test_disabled",
        name="Disabled Server",
        transport_type=MCPTransportType.STDIO,
        command=sys.executable,
        args=(str(server_script),),
        enabled=False,
    )
    disabled_client = MCPClient(disabled_config)
    assert disabled_client.list_tools() == ()
    call_disabled = disabled_client.call_tool("mock_echo", {"message": "hi"})
    assert call_disabled.is_error
    assert "已被禁用" in call_disabled.raw_text


def test_mcp_server_manager_sqlite_crud_and_sync(tmp_path: Path) -> None:
    from conflux_weave.runtime.sqlite import SQLiteRuntimeRepository
    from conflux_weave.runtime import LocalArtifactStore

    db_file = tmp_path / "test_mcp.sqlite3"
    store = LocalArtifactStore(tmp_path / "artifacts")
    repo = SQLiteRuntimeRepository(db_file, store)

    server_script = tmp_path / "mock_server.py"
    server_script.write_text(MOCK_MCP_SERVER_SCRIPT, encoding="utf-8")

    manager = MCPServerManager(db_file)

    config = MCPServerConfig(
        server_id="server_alpha",
        name="Alpha MCP Server",
        transport_type=MCPTransportType.STDIO,
        command=sys.executable,
        args=(str(server_script),),
        enabled=True,
    )

    # 1. register
    manager.register_server(config)
    retrieved = manager.get_server("server_alpha")
    assert retrieved is not None
    assert retrieved.name == "Alpha MCP Server"
    assert retrieved.enabled is True

    # 2. sync tools
    tools = manager.sync_server_tools("server_alpha")
    assert len(tools) == 2
    updated = manager.get_server("server_alpha")
    assert updated is not None
    assert len(updated.tools_cache) == 2
    assert updated.last_connected_at is not None

    # 3. get_all_harness_tools
    harness_tools = manager.get_all_harness_tools()
    assert len(harness_tools) == 2
    assert harness_tools[0].tool_id.startswith("mcp.server_alpha.")

    # 4. manager call_tool
    call_res = manager.call_tool("server_alpha", "mock_echo", {"message": "via manager"})
    assert not call_res.is_error
    assert "Echo: via manager" in call_res.raw_text

    # 5. delete
    deleted = manager.delete_server("server_alpha")
    assert deleted is True
    assert manager.get_server("server_alpha") is None


def test_mcp_rest_api_integration(tmp_path: Path) -> None:
    from conflux_weave.runtime.sqlite import SQLiteRuntimeRepository
    from conflux_weave.runtime import LocalArtifactStore
    from conflux_weave.server import create_app

    db_file = tmp_path / "api_test.sqlite3"
    store = LocalArtifactStore(tmp_path / "artifacts")
    repository = SQLiteRuntimeRepository(db_file, store)
    orchestrator = SimpleNamespace()
    app = create_app(repository, orchestrator)
    client = TestClient(app)

    server_script = tmp_path / "mock_server.py"
    server_script.write_text(MOCK_MCP_SERVER_SCRIPT, encoding="utf-8")

    # 1. GET /api/v1/mcp/servers (Empty)
    r1 = client.get("/api/v1/mcp/servers")
    assert r1.status_code == 200
    assert r1.json()["total"] == 0

    # 2. POST /api/v1/mcp/servers (Register)
    r2 = client.post(
        "/api/v1/mcp/servers",
        json={
            "server_id": "test_mcp_srv",
            "name": "Test MCP REST Server",
            "transport_type": "stdio",
            "command": sys.executable,
            "args": [str(server_script)],
            "enabled": True,
            "timeout_seconds": 15.0,
        },
    )
    assert r2.status_code == 200
    server_data = r2.json()
    assert server_data["server_id"] == "test_mcp_srv"
    assert server_data["name"] == "Test MCP REST Server"

    # 3. GET /api/v1/mcp/servers/{server_id}
    r3 = client.get("/api/v1/mcp/servers/test_mcp_srv")
    assert r3.status_code == 200
    assert r3.json()["server_id"] == "test_mcp_srv"

    # 4. POST /api/v1/mcp/servers/{server_id}/sync
    r4 = client.post("/api/v1/mcp/servers/test_mcp_srv/sync")
    assert r4.status_code == 200
    synced = r4.json()
    assert len(synced["tools_cache"]) == 2
    assert synced["last_connected_at"] is not None

    # 5. POST /api/v1/mcp/servers/{server_id}/tools/{tool_name}/call
    r5 = client.post(
        "/api/v1/mcp/servers/test_mcp_srv/tools/mock_calc/call",
        json={"arguments": {"a": 15, "b": 27}},
    )
    assert r5.status_code == 200
    call_data = r5.json()
    assert call_data["tool_name"] == "mock_calc"
    assert call_data["is_error"] is False
    assert "Result: 42" in call_data["raw_text"]

    # 6. DELETE /api/v1/mcp/servers/{server_id}
    r6 = client.delete("/api/v1/mcp/servers/test_mcp_srv")
    assert r6.status_code == 200
    assert r6.json()["ok"] is True

    # 7. GET 404 after deletion
    r7 = client.get("/api/v1/mcp/servers/test_mcp_srv")
    assert r7.status_code == 404
