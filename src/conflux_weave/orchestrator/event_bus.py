"""Asynchronous Agent Event Bus with SQLite persistence and live pub/sub (P5.4)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sqlite3
from typing import Any
import uuid

from conflux_weave.orchestrator.spec import AgentEvent, _utc_now


class AsyncAgentEventBus:
    """Event bus recording cross-Agent interaction signals and broadcasting live events."""

    def __init__(self, db_path: Path | str | None = None) -> None:
        self._db_path = Path(db_path) if db_path is not None else None
        self._subscribers: dict[str, set[asyncio.Queue[AgentEvent]]] = {}
        self._memory_events: list[AgentEvent] = []

    def _ensure_tables(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_events (
                event_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                agent_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                causation_event_id TEXT,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_agent_events_run
            ON agent_events(run_id, created_at)
            """
        )

    def _connect(self) -> sqlite3.Connection | None:
        if self._db_path is None:
            return None
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        self._ensure_tables(conn)
        return conn

    def publish(
        self,
        run_id: str,
        agent_id: str,
        event_type: str,
        payload: dict[str, Any] | None = None,
        causation_event_id: str | None = None,
    ) -> AgentEvent:
        """Publish a new agent event, persist to SQLite, and notify subscribers."""
        event = AgentEvent(
            event_id=f"evt-{uuid.uuid4().hex[:12]}",
            run_id=run_id,
            agent_id=agent_id,
            event_type=event_type,
            payload=payload or {},
            causation_event_id=causation_event_id,
            created_at=_utc_now(),
        )

        # 1. In-memory append
        self._memory_events.append(event)

        # 2. SQLite persistence
        conn = self._connect()
        if conn is not None:
            with conn:
                conn.execute(
                    """
                    INSERT INTO agent_events (
                        event_id, run_id, agent_id, event_type,
                        causation_event_id, payload_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event.event_id,
                        event.run_id,
                        event.agent_id,
                        event.event_type,
                        event.causation_event_id,
                        json.dumps(event.payload, ensure_ascii=False),
                        event.created_at,
                    ),
                )

        # 3. Broadcast to in-memory live subscribers
        subs = self._subscribers.get(run_id, set())
        for q in list(subs):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass

        return event

    def list_events(self, run_id: str, event_type: str | None = None) -> list[AgentEvent]:
        """Fetch chronological events for a given run_id."""
        conn = self._connect()
        if conn is not None:
            query = "SELECT * FROM agent_events WHERE run_id = ?"
            params: list[Any] = [run_id]
            if event_type is not None:
                query += " AND event_type = ?"
                params.append(event_type)
            query += " ORDER BY created_at ASC"

            rows = conn.execute(query, params).fetchall()
            return [
                AgentEvent(
                    event_id=row["event_id"],
                    run_id=row["run_id"],
                    agent_id=row["agent_id"],
                    event_type=row["event_type"],
                    payload=json.loads(row["payload_json"]) if row["payload_json"] else {},
                    causation_event_id=row["causation_event_id"],
                    created_at=row["created_at"],
                )
                for row in rows
            ]

        # Memory fallback
        events = [e for e in self._memory_events if e.run_id == run_id]
        if event_type is not None:
            events = [e for e in events if e.event_type == event_type]
        return sorted(events, key=lambda e: e.created_at)

    def subscribe(self, run_id: str, max_queue_size: int = 100) -> asyncio.Queue[AgentEvent]:
        """Subscribe to live events for a specific run."""
        if run_id not in self._subscribers:
            self._subscribers[run_id] = set()
        queue: asyncio.Queue[AgentEvent] = asyncio.Queue(maxsize=max_queue_size)
        self._subscribers[run_id].add(queue)
        return queue

    def unsubscribe(self, run_id: str, queue: asyncio.Queue[AgentEvent]) -> None:
        """Remove an active subscription queue."""
        if run_id in self._subscribers:
            self._subscribers[run_id].discard(queue)
            if not self._subscribers[run_id]:
                self._subscribers.pop(run_id, None)
