"""MCP Server Manager for persistence, lifecycle and tool aggregation (P5.2)."""

from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
import sqlite3
from typing import Any

from conflux_weave.harness.contracts import ToolSpec
from conflux_weave.mcp.adapter import mcp_tool_to_harness_spec
from conflux_weave.mcp.client import MCPClient
from conflux_weave.mcp.spec import (
    MCPServerConfig,
    MCPToolCallResult,
    MCPToolInfo,
    MCPTransportType,
)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class MCPServerManager:
    """Manages registered external MCP servers, dynamic tool sync, and Harness tool aggregation."""

    def __init__(self, db_path: Path | str | None = None) -> None:
        self._db_path = Path(db_path) if db_path is not None else None
        self._memory_cache: dict[str, MCPServerConfig] = {}

    def _ensure_tables(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS mcp_servers (
                server_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                transport_type TEXT NOT NULL CHECK (transport_type IN ('stdio', 'sse')),
                command TEXT,
                args TEXT,
                url TEXT,
                env_vars TEXT,
                enabled INTEGER NOT NULL DEFAULT 1,
                tools_cache TEXT,
                last_connected_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_mcp_servers_enabled
            ON mcp_servers(enabled)
            """
        )

    def _connect(self) -> sqlite3.Connection | None:
        if self._db_path is None:
            return None
        connection = sqlite3.connect(self._db_path)
        connection.row_factory = sqlite3.Row
        self._ensure_tables(connection)
        return connection

    def list_servers(self, enabled_only: bool = False) -> list[MCPServerConfig]:
        """List all configured external MCP servers."""
        conn = self._connect()
        if conn is not None:
            query = "SELECT * FROM mcp_servers"
            params: list[Any] = []
            if enabled_only:
                query += " WHERE enabled = 1"
            query += " ORDER BY created_at DESC"
            rows = conn.execute(query, params).fetchall()
            return [self._row_to_config(r) for r in rows]

        results = list(self._memory_cache.values())
        if enabled_only:
            results = [s for s in results if s.enabled]
        return sorted(results, key=lambda s: s.created_at, reverse=True)

    def get_server(self, server_id: str) -> MCPServerConfig | None:
        """Fetch server configuration by server_id."""
        conn = self._connect()
        if conn is not None:
            row = conn.execute(
                "SELECT * FROM mcp_servers WHERE server_id = ?", (server_id,)
            ).fetchone()
            if row is not None:
                return self._row_to_config(row)

        return self._memory_cache.get(server_id)

    def register_server(self, config: MCPServerConfig) -> MCPServerConfig:
        """Register or update an external MCP server."""
        now = _utc_now()
        saved = MCPServerConfig(
            server_id=config.server_id,
            name=config.name,
            transport_type=config.transport_type,
            command=config.command,
            args=config.args,
            url=config.url,
            env_vars=config.env_vars,
            enabled=config.enabled,
            timeout_seconds=config.timeout_seconds,
            tools_cache=config.tools_cache,
            last_connected_at=config.last_connected_at,
            created_at=config.created_at or now,
            updated_at=now,
        )
        self._memory_cache[saved.server_id] = saved

        conn = self._connect()
        if conn is not None:
            with conn:
                conn.execute(
                    """
                    INSERT INTO mcp_servers (
                        server_id, name, transport_type, command, args, url,
                        env_vars, enabled, tools_cache, last_connected_at,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(server_id) DO UPDATE SET
                        name = excluded.name,
                        transport_type = excluded.transport_type,
                        command = excluded.command,
                        args = excluded.args,
                        url = excluded.url,
                        env_vars = excluded.env_vars,
                        enabled = excluded.enabled,
                        tools_cache = excluded.tools_cache,
                        last_connected_at = excluded.last_connected_at,
                        updated_at = excluded.updated_at
                    """,
                    (
                        saved.server_id,
                        saved.name,
                        saved.transport_type.value,
                        saved.command,
                        json.dumps(list(saved.args), ensure_ascii=False),
                        saved.url,
                        json.dumps(saved.env_vars, ensure_ascii=False),
                        1 if saved.enabled else 0,
                        json.dumps([t.to_dict() for t in saved.tools_cache], ensure_ascii=False),
                        saved.last_connected_at,
                        saved.created_at,
                        saved.updated_at,
                    ),
                )
        return saved

    def delete_server(self, server_id: str) -> bool:
        """Delete an MCP server registration."""
        existed = server_id in self._memory_cache
        self._memory_cache.pop(server_id, None)

        conn = self._connect()
        if conn is not None:
            with conn:
                cursor = conn.execute(
                    "DELETE FROM mcp_servers WHERE server_id = ?", (server_id,)
                )
                return cursor.rowcount > 0
        return existed

    def sync_server_tools(self, server_id: str) -> tuple[MCPToolInfo, ...]:
        """Query tools/list from server, update cache and timestamp."""
        server = self.get_server(server_id)
        if server is None:
            return ()

        client = MCPClient(server)
        tools = client.list_tools()
        now = _utc_now()

        updated = MCPServerConfig(
            server_id=server.server_id,
            name=server.name,
            transport_type=server.transport_type,
            command=server.command,
            args=server.args,
            url=server.url,
            env_vars=server.env_vars,
            enabled=server.enabled,
            timeout_seconds=server.timeout_seconds,
            tools_cache=tools,
            last_connected_at=now,
            created_at=server.created_at,
            updated_at=now,
        )
        self.register_server(updated)
        return tools

    def call_tool(self, server_id: str, tool_name: str, arguments: dict[str, Any]) -> MCPToolCallResult:
        """Invoke a specific tool on a registered MCP server."""
        server = self.get_server(server_id)
        if server is None:
            return MCPToolCallResult(
                tool_name=tool_name,
                is_error=True,
                raw_text=f"未找到指定的 MCP Server: {server_id}",
            )

        client = MCPClient(server)
        return client.call_tool(tool_name, arguments)

    def get_all_harness_tools(self) -> list[ToolSpec]:
        """Convert all cached tools from all enabled MCP servers into Harness ToolSpecs."""
        servers = self.list_servers(enabled_only=True)
        harness_tools: list[ToolSpec] = []
        for server in servers:
            for tool in server.tools_cache:
                harness_tools.append(
                    mcp_tool_to_harness_spec(
                        server_id=server.server_id,
                        tool=tool,
                        timeout_seconds=server.timeout_seconds,
                    )
                )
        return harness_tools

    @staticmethod
    def _row_to_config(row: sqlite3.Row) -> MCPServerConfig:
        raw_tools = json.loads(row["tools_cache"]) if row["tools_cache"] else []
        tools = tuple(MCPToolInfo.from_dict(t) for t in raw_tools)
        return MCPServerConfig(
            server_id=row["server_id"],
            name=row["name"],
            transport_type=MCPTransportType(row["transport_type"]),
            command=row["command"],
            args=tuple(json.loads(row["args"])) if row["args"] else (),
            url=row["url"],
            env_vars=json.loads(row["env_vars"]) if row["env_vars"] else {},
            enabled=bool(row["enabled"]),
            tools_cache=tools,
            last_connected_at=row["last_connected_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
