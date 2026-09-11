"""Unit and integration tests for MCP Server exposing academic capabilities (P5.3)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from conflux_weave.mcp.server import MCPServerCore, MCPSSEManager, run_stdio_server
from conflux_weave.runtime.memory_store import (
    HierarchicalMemoryStore,
    MemoryCategory,
    MemoryItem,
    MemoryScope,
    MemoryStatus,
)


def test_mcp_server_core_protocol_handshake() -> None:
    core = MCPServerCore()

    # 1. initialize
    init_req = {
        "jsonrpc": "2.0",
        "id": "req-1",
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "test-client", "version": "1.0"},
        },
    }
    init_res = core.handle_jsonrpc(init_req)
    assert init_res is not None
    assert init_res["jsonrpc"] == "2.0"
    assert init_res["id"] == "req-1"
    assert init_res["result"]["protocolVersion"] == "2024-11-05"
    assert init_res["result"]["serverInfo"]["name"] == "conflux-weave"

    # 2. notifications/initialized
    notif_req = {
        "jsonrpc": "2.0",
        "method": "notifications/initialized",
        "params": {},
    }
    notif_res = core.handle_jsonrpc(notif_req)
    assert notif_res is None

    # 3. ping
    ping_res = core.handle_jsonrpc({"jsonrpc": "2.0", "id": "ping-1", "method": "ping"})
    assert ping_res == {"jsonrpc": "2.0", "id": "ping-1", "result": {}}

    # 4. method not found
    err_res = core.handle_jsonrpc({"jsonrpc": "2.0", "id": "err-1", "method": "unknown_method"})
    assert err_res is not None
    assert err_res["error"]["code"] == -32601


def test_mcp_server_core_tools_list_and_execution(tmp_path: Path) -> None:
    db_file = tmp_path / "memory.sqlite3"
    memory_store = HierarchicalMemoryStore(db_file)
    memory_store.create_memory(
        scope=MemoryScope.USER,
        target_id="global",
        category=MemoryCategory.PREFERENCE,
        statement="始终输出中文学术技术术语",
        confidence=0.98,
    )

    core = MCPServerCore(memory_store=memory_store, project_root=tmp_path)

    # 1. tools/list
    list_req = {"jsonrpc": "2.0", "id": "list-1", "method": "tools/list", "params": {}}
    list_res = core.handle_jsonrpc(list_req)
    assert list_res is not None
    tools = list_res["result"]["tools"]
    tool_names = {t["name"] for t in tools}
    assert tool_names == {
        "conflux_search_papers",
        "conflux_get_paper_evidence",
        "conflux_project_walkthrough",
        "conflux_get_user_preferences",
    }

    # 2. tools/call conflux_search_papers
    search_req = {
        "jsonrpc": "2.0",
        "id": "call-1",
        "method": "tools/call",
        "params": {
            "name": "conflux_search_papers",
            "arguments": {"query": "Reciprocal Rank Fusion", "top_k": 3},
        },
    }
    search_res = core.handle_jsonrpc(search_req)
    assert search_res is not None
    assert search_res["result"]["isError"] is False
    content_text = search_res["result"]["content"][0]["text"]
    assert "2606.08702" in content_text or "学术文献检索结果" in content_text

    # 3. tools/call conflux_get_paper_evidence
    evidence_req = {
        "jsonrpc": "2.0",
        "id": "call-2",
        "method": "tools/call",
        "params": {
            "name": "conflux_get_paper_evidence",
            "arguments": {"paper_id": "2606.08702", "section_or_page": "methodology"},
        },
    }
    evidence_res = core.handle_jsonrpc(evidence_req)
    assert evidence_res is not None
    assert evidence_res["result"]["isError"] is False
    assert "verified_authoritative" in evidence_res["result"]["content"][0]["text"]

    # 4. tools/call conflux_project_walkthrough
    walk_req = {
        "jsonrpc": "2.0",
        "id": "call-3",
        "method": "tools/call",
        "params": {
            "name": "conflux_project_walkthrough",
            "arguments": {"project_id": "Conflux-Weave"},
        },
    }
    walk_res = core.handle_jsonrpc(walk_req)
    assert walk_res is not None
    assert "核心分层拓扑" in walk_res["result"]["content"][0]["text"]
    assert "学术理论源码映射" in walk_res["result"]["content"][0]["text"]

    # 5. tools/call conflux_get_user_preferences
    pref_req = {
        "jsonrpc": "2.0",
        "id": "call-4",
        "method": "tools/call",
        "params": {
            "name": "conflux_get_user_preferences",
            "arguments": {"scope": "user"},
        },
    }
    pref_res = core.handle_jsonrpc(pref_req)
    assert pref_res is not None
    assert "始终输出中文学术技术术语" in pref_res["result"]["content"][0]["text"]

    # 6. tools/call unknown tool
    unknown_req = {
        "jsonrpc": "2.0",
        "id": "call-5",
        "method": "tools/call",
        "params": {"name": "not_exists", "arguments": {}},
    }
    unknown_res = core.handle_jsonrpc(unknown_req)
    assert unknown_res is not None
    assert unknown_res["result"]["isError"] is True


def test_mcp_server_stdio_process_execution() -> None:
    """Test running python -m conflux_weave.mcp.server as an external stdio process."""
    cmd = [sys.executable, "-m", "conflux_weave.mcp.server"]

    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    init_req = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "test-stdio", "version": "1.0"},
        },
    }
    list_req = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}

    input_data = json.dumps(init_req) + "\n" + json.dumps(list_req) + "\n"
    stdout, stderr = proc.communicate(input=input_data, timeout=10.0)

    lines = [line.strip() for line in stdout.splitlines() if line.strip()]
    assert len(lines) == 2

    res1 = json.loads(lines[0])
    assert res1["id"] == 1
    assert res1["result"]["serverInfo"]["name"] == "conflux-weave"

    res2 = json.loads(lines[1])
    assert res2["id"] == 2
    assert len(res2["result"]["tools"]) == 4


def test_mcp_server_fastapi_endpoints(tmp_path: Path) -> None:
    from conflux_weave.runtime.sqlite import SQLiteRuntimeRepository
    from conflux_weave.runtime import LocalArtifactStore
    from conflux_weave.server import create_app

    db_file = tmp_path / "server_test.sqlite3"
    store = LocalArtifactStore(tmp_path / "artifacts")
    repository = SQLiteRuntimeRepository(db_file, store)
    orchestrator = SimpleNamespace()
    app = create_app(repository, orchestrator)
    client = TestClient(app)

    # 1. POST /api/v1/mcp/rpc (initialize)
    rpc_init = client.post(
        "/api/v1/mcp/rpc",
        json={
            "jsonrpc": "2.0",
            "id": 101,
            "method": "initialize",
            "params": {"protocolVersion": "2024-11-05"},
        },
    )
    assert rpc_init.status_code == 200
    init_data = rpc_init.json()
    assert init_data["id"] == 101
    assert init_data["result"]["protocolVersion"] == "2024-11-05"

    # 2. POST /api/v1/mcp/rpc (tools/list)
    rpc_list = client.post(
        "/api/v1/mcp/rpc",
        json={"jsonrpc": "2.0", "id": 102, "method": "tools/list", "params": {}},
    )
    assert rpc_list.status_code == 200
    list_data = rpc_list.json()
    assert len(list_data["result"]["tools"]) == 4

    # 3. POST /api/v1/mcp/rpc (tools/call)
    rpc_call = client.post(
        "/api/v1/mcp/rpc",
        json={
            "jsonrpc": "2.0",
            "id": 103,
            "method": "tools/call",
            "params": {
                "name": "conflux_project_walkthrough",
                "arguments": {},
            },
        },
    )
    assert rpc_call.status_code == 200
    call_data = rpc_call.json()
    assert "核心分层拓扑" in call_data["result"]["content"][0]["text"]

    # 4. POST /api/v1/mcp/messages with invalid session
    bad_msg = client.post(
        "/api/v1/mcp/messages?session_id=invalid-session-id",
        json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
    )
    assert bad_msg.status_code == 404
    assert bad_msg.json()["code"] == "session_not_found"

    # 5. GET /api/v1/mcp/sse?max_events=1 (verify stream header and initial endpoint event)
    sse_res = client.get("/api/v1/mcp/sse?max_events=1")
    assert sse_res.status_code == 200
    assert "text/event-stream" in sse_res.headers["content-type"]
    assert "event: endpoint" in sse_res.text
    assert "/api/v1/mcp/messages?session_id=" in sse_res.text

    # 6. Test full bidirectional SSE + messages flow
    session_id, queue = app.state.mcp_sse_manager.create_session()
    post_res = client.post(
        f"/api/v1/mcp/messages?session_id={session_id}",
        json={"jsonrpc": "2.0", "id": 200, "method": "ping"},
    )
    assert post_res.status_code == 202
    assert not queue.empty()
    queued_msg = queue.get_nowait()
    assert queued_msg["id"] == 200
    assert queued_msg["result"] == {}
    app.state.mcp_sse_manager.remove_session(session_id)
