"""SQLite persistence for idempotent Agent communication envelopes."""

from __future__ import annotations

import sqlite3

from conflux_weave.evidence import ArtifactRef
from conflux_weave.harness.contracts import MessageEnvelope, MessageType
from conflux_weave.runtime.sqlite_base import _transaction
from conflux_weave.runtime.sqlite_contracts import (
    AgentMessageAppendResult,
    IdempotencyConflict,
    PersistenceInvariantError,
    RecordNotFound,
)


class AgentMessageRepositoryMixin:
    def append_agent_message(
        self,
        message: MessageEnvelope,
        payload: ArtifactRef,
    ) -> AgentMessageAppendResult:
        if message.payload_ref != payload.artifact_id:
            raise PersistenceInvariantError(
                "Agent Message payload_ref does not match payload Artifact"
            )
        self._validate_artifact(payload)
        with self._connect() as connection:
            with _transaction(connection):
                run = connection.execute(
                    "SELECT 1 FROM runs WHERE run_id = ?", (message.run_id,)
                ).fetchone()
                if run is None:
                    raise RecordNotFound("Run not found")
                existing = connection.execute(
                    "SELECT * FROM agent_messages WHERE idempotency_key = ?",
                    (message.idempotency_key,),
                ).fetchone()
                if existing is not None:
                    stored = _message_from_row(existing)
                    if _semantic_message(stored) != _semantic_message(message):
                        raise IdempotencyConflict(
                            "Agent Message idempotency key was reused for different content"
                        )
                    return AgentMessageAppendResult(message=stored, created=False)

                self._register_artifact(connection, payload)
                try:
                    connection.execute(
                        """
                        INSERT INTO agent_messages(
                            message_id, run_id, agent_task_id, sender, recipient,
                            message_type, causation_id, correlation_id, payload_ref,
                            idempotency_key, created_at, schema_version
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            message.message_id,
                            message.run_id,
                            message.agent_task_id,
                            message.sender,
                            message.recipient,
                            message.message_type.value,
                            message.causation_id,
                            message.correlation_id,
                            message.payload_ref,
                            message.idempotency_key,
                            message.created_at,
                            message.schema_version,
                        ),
                    )
                except sqlite3.IntegrityError as exc:
                    raise PersistenceInvariantError(
                        f"Agent Message violates persistence constraints: {exc}"
                    ) from exc
                self._append_run_event(
                    connection,
                    run_id=message.run_id,
                    step_id=None,
                    attempt_id=None,
                    event_type=f"agent_{message.message_type.value}",
                    detail={
                        "message_id": message.message_id,
                        "agent_task_id": message.agent_task_id,
                        "sender": message.sender,
                        "recipient": message.recipient,
                        "payload_ref": message.payload_ref,
                    },
                    created_at=message.created_at,
                )
        return AgentMessageAppendResult(message=message, created=True)

    def get_agent_message(self, message_id: str) -> MessageEnvelope:
        if not message_id.strip():
            raise ValueError("message_id must not be empty")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM agent_messages WHERE message_id = ?", (message_id,)
            ).fetchone()
        if row is None:
            raise RecordNotFound("Agent Message not found")
        return _message_from_row(row)

    def get_agent_messages(
        self,
        run_id: str,
        *,
        recipient: str | None = None,
        after_message_id: str | None = None,
        limit: int = 100,
    ) -> tuple[MessageEnvelope, ...]:
        if not 1 <= limit <= 500:
            raise ValueError("Agent Message page limit must be between 1 and 500")
        with self._connect() as connection:
            run = connection.execute(
                "SELECT 1 FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if run is None:
                raise RecordNotFound("Run not found")
            after_sequence: int | None = None
            if after_message_id is not None:
                cursor = connection.execute(
                    "SELECT run_id, message_sequence FROM agent_messages WHERE message_id = ?",
                    (after_message_id,),
                ).fetchone()
                if cursor is None or cursor["run_id"] != run_id:
                    raise RecordNotFound("Agent Message cursor not found for Run")
                after_sequence = int(cursor["message_sequence"])

            filters = ["run_id = ?"]
            parameters: list[object] = [run_id]
            if recipient is not None:
                filters.append("recipient = ?")
                parameters.append(recipient)
            if after_sequence is not None:
                filters.append("message_sequence > ?")
                parameters.append(after_sequence)
            parameters.append(limit)
            rows = connection.execute(
                f"""
                SELECT * FROM agent_messages
                WHERE {' AND '.join(filters)}
                ORDER BY message_sequence
                LIMIT ?
                """,
                parameters,
            ).fetchall()
        return tuple(_message_from_row(row) for row in rows)


def _message_from_row(row: sqlite3.Row) -> MessageEnvelope:
    return MessageEnvelope(
        message_id=str(row["message_id"]),
        run_id=str(row["run_id"]),
        agent_task_id=str(row["agent_task_id"]),
        sender=str(row["sender"]),
        recipient=str(row["recipient"]),
        message_type=MessageType(row["message_type"]),
        causation_id=row["causation_id"],
        correlation_id=str(row["correlation_id"]),
        payload_ref=str(row["payload_ref"]),
        idempotency_key=str(row["idempotency_key"]),
        created_at=str(row["created_at"]),
        schema_version=str(row["schema_version"]),
    )


def _semantic_message(message: MessageEnvelope) -> tuple[object, ...]:
    return (
        message.run_id,
        message.agent_task_id,
        message.sender,
        message.recipient,
        message.message_type,
        message.causation_id,
        message.correlation_id,
        message.payload_ref,
        message.schema_version,
    )
