"""Unit and integration tests for Gate D1 Skill and Ecosystem Governance.

Tests cover:
- Local skill creation, SemVer updates, version history snapshots, and rollback
- Input schema editing, compatibility checking (breaking vs compatible), and drift detection
- Skill compute budget configuration and persistence
- Markdown skill parsing (YAML frontmatter + Markdown prompt template) & importing
- MCP server connectivity probing (latency, tool count, health) and schema drift auditing
- OS-level compute sandbox status reporting and enforcement
- REST API governance endpoints
"""

from __future__ import annotations

import json
from pathlib import Path
import sys
from types import SimpleNamespace
from typing import Any

import pytest
from starlette.testclient import TestClient

from conflux_weave.compute_tools import (
    ComputeToolRequest,
    RestrictedComputeSandbox,
    ToolClass,
    ToolPolicy,
    get_sandbox_status,
)
from conflux_weave.mcp.client import MCPClient
from conflux_weave.mcp.manager import MCPServerManager
from conflux_weave.mcp.spec import (
    MCPServerConfig,
    MCPToolInfo,
    MCPTransportType,
)
from conflux_weave.skills.registry import (
    SkillRegistry,
    check_schema_compatibility,
    parse_skill_markdown,
)
from conflux_weave.skills.spec import (
    SkillBudget,
    SkillCategory,
    SkillSpec,
    SkillStatus,
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
        pass
    elif method == "tools/list":
        res = {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "tools": [
                    {
                        "name": "echo_tool",
                        "description": "Echoes back input",
                        "inputSchema": {
                            "type": "object",
                            "properties": {"text": {"type": "string"}},
                            "required": ["text"]
                        }
                    },
                    {
                        "name": "calc_tool",
                        "description": "Calculates sum",
                        "inputSchema": {
                            "type": "object",
                            "properties": {"val": {"type": "number"}},
                            "required": ["val"]
                        }
                    }
                ]
            }
        }
        sys.stdout.write(json.dumps(res) + "\\n")
        sys.stdout.flush()
    elif method == "tools/call":
        res = {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "content": [{"type": "text", "text": "mock_response"}]
            }
        }
        sys.stdout.write(json.dumps(res) + "\\n")
        sys.stdout.flush()
"""


def test_skill_creation_version_history_and_rollback(tmp_path: Path) -> None:
    """Test local skill creation, SemVer updates, immutable version records, and version rollback."""
    db_file = tmp_path / "skills.sqlite3"
    registry = SkillRegistry(db_path=db_file)

    skill_v1 = SkillSpec(
        skill_id="custom_summarizer",
        version="1.0.0",
        name="Custom Summarizer",
        description="Initial version of text summarizer",
        category=SkillCategory.RESEARCH,
        author="Dev team",
        input_schema={
            "type": "object",
            "required": ["text"],
            "properties": {"text": {"type": "string"}},
        },
        required_tools=("local_file_read",),
        prompt_template="Summarize the following: {{text}}",
        rules=("Be concise",),
        default_budget=SkillBudget(max_tokens=1000, max_steps=5, estimated_time_seconds=15),
        is_builtin=False,
    )

    # 1. Register version 1.0.0
    registry.register_skill(skill_v1, change_summary="Initial release")
    versions = registry.list_versions("custom_summarizer")
    assert len(versions) == 1
    assert versions[0].version == "1.0.0"
    assert versions[0].change_summary == "Initial release"

    # 2. Register version 1.1.0 (with new rule and prompt)
    skill_v2 = SkillSpec(
        skill_id="custom_summarizer",
        version="1.1.0",
        name="Custom Summarizer v2",
        description="Enhanced summarizer",
        category=SkillCategory.RESEARCH,
        author="Dev team",
        input_schema={
            "type": "object",
            "required": ["text"],
            "properties": {"text": {"type": "string"}},
        },
        required_tools=("local_file_read",),
        prompt_template="Provide a structured summary: {{text}}",
        rules=("Be concise", "Include bullet points"),
        default_budget=SkillBudget(max_tokens=1500, max_steps=6, estimated_time_seconds=20),
        is_builtin=False,
    )
    registry.register_skill(skill_v2, change_summary="Added structured bullet points")

    versions = registry.list_versions("custom_summarizer")
    assert len(versions) == 2
    assert [v.version for v in versions] == ["1.1.0", "1.0.0"]

    active = registry.get_skill("custom_summarizer")
    assert active is not None
    assert active.version == "1.1.0"
    assert "bullet points" in active.rules[1]

    # 3. Rollback to 1.0.0
    rolled_back = registry.rollback_skill("custom_summarizer", "1.0.0")
    assert rolled_back.version == "1.0.0"
    assert rolled_back.prompt_template == "Summarize the following: {{text}}"
    assert len(rolled_back.rules) == 1

    # Verify active skill from registry is restored to 1.0.0
    active = registry.get_skill("custom_summarizer")
    assert active is not None
    assert active.version == "1.0.0"

    # Rollback to non-existent version raises ValueError
    with pytest.raises(ValueError):
        registry.rollback_skill("custom_summarizer", "9.9.9")


def test_schema_compatibility_and_drift_detection(tmp_path: Path) -> None:
    """Test schema compatibility checking and update_skill_schema with automated SemVer bumping."""
    registry = SkillRegistry(db_path=tmp_path / "skills.sqlite3")

    old_schema = {
        "type": "object",
        "required": ["query"],
        "properties": {
            "query": {"type": "string"},
            "limit": {"type": "integer"},
        },
    }

    # 1. Compatible change: adding optional property
    compat_new_schema = {
        "type": "object",
        "required": ["query"],
        "properties": {
            "query": {"type": "string"},
            "limit": {"type": "integer"},
            "sort_by": {"type": "string"},
        },
    }
    report_compat = check_schema_compatibility(old_schema, compat_new_schema)
    assert report_compat.compatible is True
    assert len(report_compat.breaking_changes) == 0

    # 2. Breaking change: adding required property
    breaking_new_schema = {
        "type": "object",
        "required": ["query", "api_key"],
        "properties": {
            "query": {"type": "string"},
            "limit": {"type": "integer"},
            "api_key": {"type": "string"},
        },
    }
    report_breaking = check_schema_compatibility(old_schema, breaking_new_schema)
    assert report_breaking.compatible is False
    assert any("api_key" in b for b in report_breaking.breaking_changes)

    # 3. Breaking change: property type altered
    type_change_schema = {
        "type": "object",
        "required": ["query"],
        "properties": {
            "query": {"type": "integer"},  # altered from string to integer
            "limit": {"type": "integer"},
        },
    }
    report_type = check_schema_compatibility(old_schema, type_change_schema)
    assert report_type.compatible is False
    assert any("query" in b and "类型变更" in b for b in report_type.breaking_changes)

    # 4. Warning: property removed
    removed_schema = {
        "type": "object",
        "required": ["query"],
        "properties": {
            "query": {"type": "string"},
        },
    }
    report_removed = check_schema_compatibility(old_schema, removed_schema)
    assert report_removed.compatible is True
    assert any("limit" in w for w in report_removed.warnings)

    # 5. update_skill_schema: auto-bump patch for compatible schema update
    skill = SkillSpec(
        skill_id="test_skill",
        version="1.0.0",
        name="Test",
        description="Test",
        category=SkillCategory.RESEARCH,
        author="Dev",
        input_schema=old_schema,
        required_tools=(),
        prompt_template="Test prompt",
    )
    registry.register_skill(skill)

    updated_compat, rep1 = registry.update_skill_schema("test_skill", compat_new_schema)
    assert rep1.compatible is True
    assert updated_compat.version == "1.0.1"  # auto patch bump

    # 6. update_skill_schema: auto-bump minor for breaking schema update
    updated_breaking, rep2 = registry.update_skill_schema("test_skill", breaking_new_schema)
    assert rep2.compatible is False
    assert updated_breaking.version == "1.1.0"  # auto minor bump


def test_skill_budget_management(tmp_path: Path) -> None:
    """Test updating default compute budget for a skill and database persistence."""
    db_file = tmp_path / "skills.sqlite3"
    registry = SkillRegistry(db_path=db_file)

    skill = SkillSpec(
        skill_id="budget_skill",
        version="1.0.0",
        name="Budgeted Skill",
        description="Skill with initial budget",
        category=SkillCategory.UTILITY,
        author="Ops",
        input_schema={"type": "object"},
        required_tools=(),
        prompt_template="Test prompt",
        default_budget=SkillBudget(max_tokens=2000, max_steps=10, estimated_time_seconds=30),
    )
    registry.register_skill(skill)

    new_budget = SkillBudget(max_tokens=5000, max_steps=20, estimated_time_seconds=60)
    updated = registry.update_skill_budget("budget_skill", new_budget, change_summary="Increased quota for long jobs")

    assert updated.default_budget.max_tokens == 5000
    assert updated.default_budget.max_steps == 20
    assert updated.default_budget.estimated_time_seconds == 60

    # Verify persistence via new registry instance on same database
    registry2 = SkillRegistry(db_path=db_file)
    reloaded = registry2.get_skill("budget_skill")
    assert reloaded is not None
    assert reloaded.default_budget.max_tokens == 5000
    assert reloaded.default_budget.max_steps == 20


def test_markdown_skill_parser_and_import(tmp_path: Path) -> None:
    """Test parsing YAML frontmatter + Markdown body and importing local skill."""
    markdown_content = """---
name: Vulnerability Scanner
id: vuln_scanner
version: 1.2.0
description: Scans source code for potential security flaws and secrets
category: governance
author: Security Team
tools:
  - compute_code_exec
  - file_grep
budget:
  max_tokens: 4000
  max_steps: 12
  estimated_time_seconds: 50
inputs:
  type: object
  required:
    - target_path
  properties:
    target_path:
      type: string
      description: Relative path to audit
rules:
  - Redact secret tokens
  - Prioritize remote code execution vulnerabilities
---

# Vulnerability Scanner Prompt Template

You are an automated security auditor. Inspect the repository at `{{target_path}}`.
Search for unescaped queries, secret tokens, and insecure deserialization.
"""

    spec = parse_skill_markdown(markdown_content)
    assert spec.skill_id == "vuln_scanner"
    assert spec.name == "Vulnerability Scanner"
    assert spec.version == "1.2.0"
    assert spec.category == SkillCategory.GOVERNANCE
    assert spec.author == "Security Team"
    assert spec.required_tools == ("compute_code_exec", "file_grep")
    assert spec.default_budget.max_tokens == 4000
    assert spec.default_budget.max_steps == 12
    assert "target_path" in spec.input_schema["required"]
    assert len(spec.rules) == 2
    assert "You are an automated security auditor" in spec.prompt_template

    # Import into registry
    registry = SkillRegistry(db_path=tmp_path / "skills.sqlite3")
    registered = registry.import_skill_from_markdown(markdown_content)
    assert registered.skill_id == "vuln_scanner"

    # Validate inputs against schema
    valid, err = registry.validate_inputs("vuln_scanner", {"target_path": "src/auth.py"})
    assert valid is True
    assert err is None

    invalid, err = registry.validate_inputs("vuln_scanner", {})
    assert invalid is False
    assert "缺少必要参数: target_path" in (err or "")


def test_mcp_server_probing_and_drift_auditing(tmp_path: Path) -> None:
    """Test MCP server probing (health, latency, tool count) and schema drift auditing."""
    server_script = tmp_path / "mock_mcp_server.py"
    server_script.write_text(MOCK_MCP_SERVER_SCRIPT, encoding="utf-8")

    db_path = tmp_path / "mcp.sqlite3"
    manager = MCPServerManager(db_path=db_path)

    config = MCPServerConfig(
        server_id="mock_srv_1",
        name="Mock MCP Server",
        transport_type=MCPTransportType.STDIO,
        command=sys.executable,
        args=(str(server_script),),
        timeout_seconds=5.0,
    )
    manager.register_server(config)

    # 1. Probe server before tool sync (cache empty, live probe returns 2 tools)
    probe_res = manager.probe_server("mock_srv_1")
    assert probe_res["status"] == "healthy"
    assert probe_res["latency_ms"] >= 0.0
    assert probe_res["tool_count"] == 2
    assert probe_res["error"] is None

    # Probe non-existent server returns unhealthy
    bad_probe = manager.probe_server("non_existent_server")
    assert bad_probe["status"] == "unhealthy"
    assert "not found" in bad_probe["error"]

    # 2. Sync tools to cache
    tools = manager.sync_server_tools("mock_srv_1")
    assert len(tools) == 2

    # Check drift: cache matches live -> no drift
    drift1 = manager.check_schema_drift("mock_srv_1")
    assert drift1["has_drift"] is False
    assert drift1["added_tools"] == []
    assert drift1["removed_tools"] == []
    assert drift1["modified_tools"] == []

    # 3. Simulate cache drift: manually update cached tool schema
    server = manager.get_server("mock_srv_1")
    assert server is not None
    # Change cached echo_tool inputSchema so it differs from live
    tampered_tools = (
        MCPToolInfo(
            name="echo_tool",
            description="Echo tool",
            input_schema={"type": "object", "properties": {"old_field": {"type": "string"}}},
        ),
        server.tools_cache[1],
    )
    tampered_server = MCPServerConfig(
        server_id=server.server_id,
        name=server.name,
        transport_type=server.transport_type,
        command=server.command,
        args=server.args,
        timeout_seconds=server.timeout_seconds,
        tools_cache=tampered_tools,
    )
    manager.register_server(tampered_server)

    # Now check drift -> modified_tools should report echo_tool!
    drift2 = manager.check_schema_drift("mock_srv_1")
    assert drift2["has_drift"] is True
    assert "echo_tool" in drift2["modified_tools"]


def test_compute_sandbox_os_status_and_protection(tmp_path: Path) -> None:
    """Test OS-level compute sandbox security status and execution guardrails."""
    status = get_sandbox_status()
    assert status["platform"] == sys.platform
    if sys.platform == "win32":
        assert status["os_sandbox_mechanism"] == "windows_job_object"
    else:
        assert status["os_sandbox_mechanism"] == "posix_rlimit"
    assert status["os_isolation_enforced"] is True
    assert status["network_blocked"] is True
    assert status["max_timeout_seconds"] > 0
    assert status["max_memory_bytes"] > 0

    # Execute inside RestrictedComputeSandbox
    from conflux_weave.runtime import LocalArtifactStore
    store = LocalArtifactStore(tmp_path / "artifacts")
    sandbox = RestrictedComputeSandbox(store, workspace_root=tmp_path / "sandbox")

    # 1. Blocked network library import inside sandbox
    network_script = """
import urllib.request
urllib.request.urlopen('https://example.com')
"""
    req = ComputeToolRequest(script=network_script)
    policy = ToolPolicy(allowed_tools=("compute_code_exec",))
    res = sandbox.execute(req, policy=policy, run_id="run-sandbox-1")
    assert res.status == "failed"
    assert "PermissionError" in (res.error or "") or "blocked in Conflux-Weave compute sandbox" in (res.error or "")

    # 2. Blocked write outside designated output dir
    outside_write_script = """
with open('../escaped.txt', 'w') as f:
    f.write('pwned')
"""
    req_write = ComputeToolRequest(script=outside_write_script)
    res_write = sandbox.execute(req_write, policy=policy, run_id="run-sandbox-2")
    assert res_write.status == "failed"
    assert "PermissionError" in (res_write.error or "")


def test_governance_rest_api_endpoints(tmp_path: Path) -> None:
    """Test REST API governance endpoints for Skills, MCP Probing/Drifting, and Sandbox Status."""
    from conflux_weave.runtime.sqlite import SQLiteRuntimeRepository
    from conflux_weave.runtime import LocalArtifactStore
    from conflux_weave.server import create_app

    server_script = tmp_path / "mock_server.py"
    server_script.write_text(MOCK_MCP_SERVER_SCRIPT, encoding="utf-8")

    db_file = tmp_path / "api_gov.sqlite3"
    store = LocalArtifactStore(tmp_path / "artifacts")
    repository = SQLiteRuntimeRepository(db_file, store)
    orchestrator = SimpleNamespace()
    app = create_app(repository, orchestrator)
    client = TestClient(app)

    # 1. GET /api/v1/tools/compute/sandbox-status
    sb_resp = client.get("/api/v1/tools/compute/sandbox-status")
    assert sb_resp.status_code == 200
    sb_data = sb_resp.json()
    assert sb_data["os_isolation_enforced"] is True
    assert sb_data["network_blocked"] is True

    # 2. POST /api/v1/skills/import-markdown
    md_content = """---
name: API Governance Skill
id: api_gov_skill
version: 1.0.0
description: Governance skill via markdown
category: governance
author: QA Team
tools:
  - compute_code_exec
budget:
  max_tokens: 2500
  max_steps: 8
  estimated_time_seconds: 30
inputs:
  type: object
  required:
    - param1
  properties:
    param1:
      type: string
rules:
  - Check security
---

Audit param: {{param1}}
"""
    import_resp = client.post("/api/v1/skills/import-markdown", json={"content": md_content})
    assert import_resp.status_code == 200
    skill_data = import_resp.json()
    assert skill_data["skill_id"] == "api_gov_skill"
    assert skill_data["version"] == "1.0.0"

    # 3. GET /api/v1/skills/{skill_id}/versions
    versions_resp = client.get("/api/v1/skills/api_gov_skill/versions")
    assert versions_resp.status_code == 200
    versions_data = versions_resp.json()
    assert versions_data["total"] >= 1
    assert versions_data["items"][0]["version"] == "1.0.0"

    # 4. PUT /api/v1/skills/{skill_id}/budget
    budget_resp = client.put(
        "/api/v1/skills/api_gov_skill/budget",
        json={
            "budget": {
                "max_tokens": 4000,
                "max_steps": 15,
                "estimated_time_seconds": 45,
            },
            "change_summary": "Expanded budget via API",
        },
    )
    assert budget_resp.status_code == 200
    assert budget_resp.json()["default_budget"]["max_tokens"] == 4000

    # 5. PUT /api/v1/skills/{skill_id}/schema
    new_schema = {
        "type": "object",
        "required": ["param1", "param2"],  # breaking change: param2 added to required
        "properties": {
            "param1": {"type": "string"},
            "param2": {"type": "string"},
        },
    }
    schema_resp = client.put(
        "/api/v1/skills/api_gov_skill/schema",
        json={
            "new_schema": new_schema,
            "change_summary": "Added required param2",
        },
    )
    assert schema_resp.status_code == 200
    schema_data = schema_resp.json()
    assert schema_data["compatible"] is False
    assert len(schema_data["breaking_changes"]) > 0
    assert schema_data["skill"]["version"] == "1.1.0"  # auto minor bump on breaking

    # 6. POST /api/v1/skills/{skill_id}/rollback
    rollback_resp = client.post(
        "/api/v1/skills/api_gov_skill/rollback",
        json={"target_version": "1.0.0"},
    )
    assert rollback_resp.status_code == 200
    assert rollback_resp.json()["version"] == "1.0.0"

    # 7. Register an MCP server via POST /api/v1/mcp/servers
    mcp_resp = client.post(
        "/api/v1/mcp/servers",
        json={
            "server_id": "api_mock_mcp",
            "name": "API Mock MCP",
            "transport_type": "stdio",
            "command": sys.executable,
            "args": [str(server_script)],
            "timeout_seconds": 5.0,
        },
    )
    assert mcp_resp.status_code == 200

    # 8. POST /api/v1/mcp/servers/{server_id}/probe
    probe_resp = client.post("/api/v1/mcp/servers/api_mock_mcp/probe")
    assert probe_resp.status_code == 200
    probe_data = probe_resp.json()
    assert probe_data["status"] == "healthy"
    assert probe_data["tool_count"] == 2

    # 9. Sync MCP tools and check GET /api/v1/mcp/servers/{server_id}/drift
    client.post("/api/v1/mcp/servers/api_mock_mcp/sync")
    drift_resp = client.get("/api/v1/mcp/servers/api_mock_mcp/drift")
    assert drift_resp.status_code == 200
    drift_data = drift_resp.json()
    assert drift_data["has_drift"] is False
