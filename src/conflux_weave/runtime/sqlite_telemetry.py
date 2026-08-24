"""SQLite telemetry-drop persistence isolated from workflow outcomes."""

from __future__ import annotations

from conflux_weave.runtime.sqlite_contracts import (
    PersistenceInvariantError,
    TelemetryDropRecord,
)
from conflux_weave.runtime.sqlite_base import (
    _normalize_timestamp,
    _transaction,
)


class TelemetryRepositoryMixin:
    def record_telemetry_drop(
        self,
        run_id: str,
        *,
        step_id: str | None,
        attempt_id: str | None,
        span_name: str,
        reason: str,
        now: str | None = None,
    ) -> None:
        if not span_name.strip() or not reason.strip():
            raise PersistenceInvariantError(
                "telemetry drop requires span_name and reason"
            )
        created_at = _normalize_timestamp(now or self.clock())
        with self._connect() as connection:
            with _transaction(connection):
                connection.execute(
                    """
                        INSERT INTO telemetry_drops(
                            run_id, step_id, attempt_id, span_name, reason, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                    (
                        run_id,
                        step_id,
                        attempt_id,
                        span_name,
                        reason,
                        created_at,
                    ),
                )

    def get_telemetry_drops(self, run_id: str) -> tuple[TelemetryDropRecord, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                    SELECT * FROM telemetry_drops
                    WHERE run_id = ? ORDER BY drop_id
                    """,
                (run_id,),
            ).fetchall()
        return tuple(
            TelemetryDropRecord(
                drop_id=int(row["drop_id"]),
                run_id=str(row["run_id"]),
                step_id=row["step_id"],
                attempt_id=row["attempt_id"],
                span_name=str(row["span_name"]),
                reason=str(row["reason"]),
                created_at=str(row["created_at"]),
            )
            for row in rows
        )
