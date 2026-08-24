"""SQLite budget ledger, external-call settlement, and error persistence."""

from __future__ import annotations

from dataclasses import asdict
import sqlite3
from typing import Sequence

from conflux_weave.core import (
    ErrorCategory,
    ErrorRecord,
    RunRecord,
    RunStatus,
    StepRecord,
)
from conflux_weave.evidence import ArtifactRef
from conflux_weave.runtime.sqlite_contracts import (
    BudgetAmount,
    BudgetEntryRecord,
    BudgetStatus,
    LeaseClaim,
    LeaseConflict,
    PersistenceInvariantError,
    RecordNotFound,
    SideEffectClass,
    StoredErrorRecord,
    _BUDGET_DIMENSIONS,
)
from conflux_weave.runtime.sqlite_base import (
    _run_from_row,
    _step_from_row,
    _canonical_json,
    _validate_budget_amount,
    _normalize_timestamp,
    _add_seconds,
    _transaction,
)


class BudgetErrorRepositoryMixin:
    def get_budget_status(self, run_id: str) -> BudgetStatus:
        with self._connect() as connection:
            limit = connection.execute(
                "SELECT * FROM budget_limits WHERE run_id = ?", (run_id,)
            ).fetchone()
            if limit is None:
                raise RecordNotFound("BudgetLimit not found")
            actual = self._budget_totals(connection, run_id, "actual")
            reserved = self._active_reservation_totals(connection, run_id)
        return BudgetStatus(
            run_id=run_id,
            state=str(limit["state"]),
            limit=BudgetAmount(
                **{dimension: int(limit[dimension]) for dimension in _BUDGET_DIMENSIONS}
            ),
            actual=BudgetAmount(**actual),
            reserved=BudgetAmount(**reserved),
            wall_clock_seconds=int(limit["wall_clock_seconds"]),
            concurrency=int(limit["concurrency"]),
            estimated_cost_limit=str(limit["estimated_cost_limit"]),
            cost_enforcement=str(limit["cost_enforcement"]),
        )

    def get_budget_entries(self, run_id: str) -> tuple[BudgetEntryRecord, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM budget_entries WHERE run_id = ? ORDER BY entry_id",
                (run_id,),
            ).fetchall()
        return tuple(
            BudgetEntryRecord(
                entry_id=int(row["entry_id"]),
                run_id=str(row["run_id"]),
                step_id=str(row["step_id"]),
                attempt_id=str(row["attempt_id"]),
                reservation_id=str(row["reservation_id"]),
                entry_kind=str(row["entry_kind"]),
                amount=BudgetAmount(
                    **{
                        dimension: int(row[dimension])
                        for dimension in _BUDGET_DIMENSIONS
                    }
                ),
                source=str(row["source"]),
                created_at=str(row["created_at"]),
            )
            for row in rows
        )

    def get_errors(self, run_id: str) -> tuple[StoredErrorRecord, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM errors WHERE run_id = ? ORDER BY created_at, error_id",
                (run_id,),
            ).fetchall()
            records = []
            for row in rows:
                affected = tuple(
                    str(item["artifact_id"])
                    for item in connection.execute(
                        "SELECT artifact_id FROM error_artifacts WHERE error_id = ? ORDER BY ordinal",
                        (row["error_id"],),
                    ).fetchall()
                )
                records.append(
                    StoredErrorRecord(
                        error_id=str(row["error_id"]),
                        run_id=str(row["run_id"]),
                        step_id=row["step_id"],
                        attempt_id=row["attempt_id"],
                        record=ErrorRecord(
                            code=str(row["code"]),
                            category=ErrorCategory(row["category"]),
                            stage=str(row["stage"]),
                            retryable=bool(row["retryable"]),
                            user_message=str(row["user_message"]),
                            technical_detail_ref=str(row["technical_detail_ref"]),
                            affected_artifact_refs=affected,
                            recovery_action=str(row["recovery_action"]),
                        ),
                        created_at=str(row["created_at"]),
                    )
                )
        return tuple(records)

    def record_error(
        self,
        claim: LeaseClaim,
        error: ErrorRecord,
        artifacts: Sequence[ArtifactRef],
        *,
        now: str | None = None,
    ) -> StoredErrorRecord:
        created_at = _normalize_timestamp(now or self.clock())
        for artifact in artifacts:
            self._validate_artifact(artifact)
        with self._connect() as connection:
            with _transaction(connection):
                for artifact in artifacts:
                    self._register_artifact(connection, artifact)
                error_id = f"error:{claim.attempt_id}:{error.code}"
                self._persist_error(
                    connection,
                    error_id=error_id,
                    run_id=claim.run_id,
                    step_id=claim.step_id,
                    attempt_id=claim.attempt_id,
                    error=error,
                    created_at=created_at,
                )
        return next(
            item for item in self.get_errors(claim.run_id) if item.error_id == error_id
        )

    def authorize_external_call(
        self,
        claim: LeaseClaim,
        intent_artifact: ArtifactRef,
        reservation: BudgetAmount,
        denial_detail: ArtifactRef,
        denial_error: ErrorRecord,
        *,
        now: str | None = None,
    ) -> bool:
        started_at = _normalize_timestamp(now or self.clock())
        if intent_artifact.producer_step_id != claim.step_id:
            raise PersistenceInvariantError(
                "external call intent producer does not match claimed Step"
            )
        self._validate_artifact(intent_artifact)
        self._validate_artifact(denial_detail)
        _validate_budget_amount(reservation)
        with self._connect() as connection:
            with _transaction(connection):
                self._require_active_attempt(connection, claim, started_at)
                run_status = connection.execute(
                    "SELECT status FROM runs WHERE run_id = ?", (claim.run_id,)
                ).fetchone()
                if (
                    run_status is None
                    or run_status["status"] != RunStatus.RUNNING.value
                ):
                    raise LeaseConflict("Run no longer allows an external call")
                effect = connection.execute(
                    "SELECT * FROM attempt_effects WHERE attempt_id = ?",
                    (claim.attempt_id,),
                ).fetchone()
                if effect is None or effect["effect_state"] != "not_started":
                    raise PersistenceInvariantError(
                        "external call intent was already recorded"
                    )
                if effect["side_effect"] not in {
                    SideEffectClass.REPLAYABLE_EXTERNAL_READ.value,
                    SideEffectClass.PAID_EXTERNAL_UNKNOWN.value,
                }:
                    raise PersistenceInvariantError(
                        "Step policy does not permit an external call"
                    )
                limit = connection.execute(
                    "SELECT * FROM budget_limits WHERE run_id = ?",
                    (claim.run_id,),
                ).fetchone()
                if limit is None:
                    raise PersistenceInvariantError("Run has no BudgetLimit snapshot")
                self._release_stale_reservations(connection, claim.run_id, started_at)
                actual = self._budget_totals(connection, claim.run_id, "actual")
                active = self._active_reservation_totals(connection, claim.run_id)
                deadline = _add_seconds(
                    str(limit["created_at"]), int(limit["wall_clock_seconds"])
                )
                sufficient = (
                    limit["state"] == "active"
                    and started_at <= deadline
                    and all(
                        actual[dimension]
                        + active[dimension]
                        + getattr(reservation, dimension)
                        <= int(limit[dimension])
                        for dimension in _BUDGET_DIMENSIONS
                    )
                )
                self._register_artifact(connection, intent_artifact)
                if not sufficient:
                    self._register_artifact(connection, denial_detail)
                    self._persist_error(
                        connection,
                        error_id=f"error-budget-denied:{claim.attempt_id}",
                        run_id=claim.run_id,
                        step_id=claim.step_id,
                        attempt_id=claim.attempt_id,
                        error=denial_error,
                        created_at=started_at,
                    )
                    connection.execute(
                        "UPDATE budget_limits SET state = 'stopped' WHERE run_id = ?",
                        (claim.run_id,),
                    )
                    connection.execute(
                        """
                            UPDATE steps SET status = 'failed', error_ref = ?
                            WHERE step_id = ? AND status = 'running' AND attempt = ?
                            """,
                        (
                            denial_detail.artifact_id,
                            claim.step_id,
                            claim.attempt_number,
                        ),
                    )
                    connection.execute(
                        "UPDATE steps SET status = 'skipped' WHERE run_id = ? AND status = 'pending'",
                        (claim.run_id,),
                    )
                    self._finish_attempt(
                        connection,
                        claim,
                        status="failed",
                        finished_at=started_at,
                        error_ref=denial_detail.artifact_id,
                    )
                    connection.execute(
                        "UPDATE runs SET status = 'failed', updated_at = ? WHERE run_id = ?",
                        (started_at, claim.run_id),
                    )
                    self._append_run_event(
                        connection,
                        run_id=claim.run_id,
                        step_id=claim.step_id,
                        attempt_id=claim.attempt_id,
                        event_type="budget_reservation_denied",
                        detail={
                            "reservation": asdict(reservation),
                            "deadline": deadline,
                            "deadline_exceeded": started_at > deadline,
                        },
                        created_at=started_at,
                    )
                    return False
                reservation_id = f"budget-reservation:{claim.attempt_id}"
                connection.execute(
                    """
                        INSERT INTO budget_reservations(
                            reservation_id, run_id, step_id, attempt_id,
                            input_tokens, output_tokens, tool_calls, retrieval_rounds,
                            status, created_at, closed_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, NULL)
                        """,
                    (
                        reservation_id,
                        claim.run_id,
                        claim.step_id,
                        claim.attempt_id,
                        reservation.input_tokens,
                        reservation.output_tokens,
                        reservation.tool_calls,
                        reservation.retrieval_rounds,
                        started_at,
                    ),
                )
                self._insert_budget_entry(
                    connection,
                    claim,
                    reservation_id,
                    "reservation",
                    reservation,
                    "pre_call_worst_case",
                    started_at,
                )
                connection.execute(
                    """
                        UPDATE attempt_effects
                        SET effect_state = 'request_started', intent_artifact_ref = ?,
                            updated_at = ?
                        WHERE attempt_id = ? AND effect_state = 'not_started'
                        """,
                    (intent_artifact.artifact_id, started_at, claim.attempt_id),
                )
                self._append_run_event(
                    connection,
                    run_id=claim.run_id,
                    step_id=claim.step_id,
                    attempt_id=claim.attempt_id,
                    event_type="external_call_started",
                    detail={"side_effect": str(effect["side_effect"])},
                    created_at=started_at,
                )
        return True

    def complete_external_attempt(
        self,
        claim: LeaseClaim,
        artifacts: Sequence[ArtifactRef],
        *,
        request_artifact_ref: str,
        response_artifact_ref: str,
        external_response_id: str | None = None,
        actual_usage: BudgetAmount = BudgetAmount(),
        overage_detail: ArtifactRef | None = None,
        overage_error: ErrorRecord | None = None,
        now: str | None = None,
    ) -> StepRecord:
        completed_at = _normalize_timestamp(now or self.clock())
        artifact_ids = tuple(artifact.artifact_id for artifact in artifacts)
        if (
            request_artifact_ref not in artifact_ids
            or response_artifact_ref not in artifact_ids
        ):
            raise PersistenceInvariantError(
                "committed external response requires request and response Artifacts"
            )
        if len(artifact_ids) != len(set(artifact_ids)):
            raise PersistenceInvariantError("Step output Artifact ids must be unique")
        for artifact in artifacts:
            if artifact.producer_step_id != claim.step_id:
                raise PersistenceInvariantError(
                    "Step output Artifact producer does not match claimed Step"
                )
            self._validate_artifact(artifact)
        _validate_budget_amount(actual_usage)
        if (overage_detail is None) != (overage_error is None):
            raise PersistenceInvariantError(
                "budget overage detail and ErrorRecord are paired"
            )
        if overage_detail is not None:
            self._validate_artifact(overage_detail)
        with self._connect() as connection:
            with _transaction(connection):
                self._require_active_attempt(connection, claim, completed_at)
                effect = connection.execute(
                    "SELECT effect_state FROM attempt_effects WHERE attempt_id = ?",
                    (claim.attempt_id,),
                ).fetchone()
                if effect is None or effect["effect_state"] != "request_started":
                    raise PersistenceInvariantError(
                        "external response cannot commit before call intent"
                    )
                reservation = connection.execute(
                    "SELECT * FROM budget_reservations WHERE attempt_id = ? AND status = 'active'",
                    (claim.attempt_id,),
                ).fetchone()
                if reservation is None:
                    raise PersistenceInvariantError(
                        "external response has no active reservation"
                    )
                reservation_id = str(reservation["reservation_id"])
                reserved = BudgetAmount(
                    **{
                        dimension: int(reservation[dimension])
                        for dimension in _BUDGET_DIMENSIONS
                    }
                )
                self._insert_budget_entry(
                    connection,
                    claim,
                    reservation_id,
                    "actual",
                    actual_usage,
                    "provider_or_tool_reported",
                    completed_at,
                )
                released = BudgetAmount(
                    **{
                        dimension: max(
                            0,
                            getattr(reserved, dimension)
                            - getattr(actual_usage, dimension),
                        )
                        for dimension in _BUDGET_DIMENSIONS
                    }
                )
                self._insert_budget_entry(
                    connection,
                    claim,
                    reservation_id,
                    "release",
                    released,
                    "reservation_reconciliation",
                    completed_at,
                )
                connection.execute(
                    "UPDATE budget_reservations SET status = 'settled', closed_at = ? WHERE reservation_id = ?",
                    (completed_at, reservation_id),
                )
                registrations = [
                    self._register_artifact(connection, artifact)
                    for artifact in artifacts
                ]
                for ordinal, registration_id in enumerate(registrations):
                    connection.execute(
                        """
                            INSERT INTO attempt_artifacts(
                                attempt_id, registration_id, ordinal
                            ) VALUES (?, ?, ?)
                            """,
                        (claim.attempt_id, registration_id, ordinal),
                    )
                connection.execute(
                    """
                        UPDATE attempt_effects
                        SET effect_state = 'response_committed',
                            request_artifact_ref = ?, response_artifact_ref = ?,
                            external_response_id = ?, updated_at = ?
                        WHERE attempt_id = ? AND effect_state = 'request_started'
                        """,
                    (
                        request_artifact_ref,
                        response_artifact_ref,
                        external_response_id,
                        completed_at,
                        claim.attempt_id,
                    ),
                )
                changed = connection.execute(
                    """
                        UPDATE steps
                        SET status = 'succeeded', output_refs_json = ?, error_ref = NULL
                        WHERE step_id = ? AND status = 'running' AND attempt = ?
                        """,
                    (
                        _canonical_json(artifact_ids),
                        claim.step_id,
                        claim.attempt_number,
                    ),
                ).rowcount
                if changed != 1:
                    raise LeaseConflict("claimed Step is no longer current")
                self._finish_attempt(
                    connection,
                    claim,
                    status="succeeded",
                    finished_at=completed_at,
                    error_ref=None,
                )
                self._append_run_event(
                    connection,
                    run_id=claim.run_id,
                    step_id=claim.step_id,
                    attempt_id=claim.attempt_id,
                    event_type="external_response_committed",
                    detail={
                        "request_artifact_ref": request_artifact_ref,
                        "response_artifact_ref": response_artifact_ref,
                        "external_response_id": external_response_id,
                    },
                    created_at=completed_at,
                )
                limit = connection.execute(
                    "SELECT * FROM budget_limits WHERE run_id = ?", (claim.run_id,)
                ).fetchone()
                totals = self._budget_totals(connection, claim.run_id, "actual")
                over_limit = any(
                    totals[dimension] > int(limit[dimension])
                    for dimension in _BUDGET_DIMENSIONS
                )
                if over_limit:
                    if overage_detail is None or overage_error is None:
                        raise PersistenceInvariantError(
                            "actual Budget overage requires structured diagnostics"
                        )
                    self._register_artifact(connection, overage_detail)
                    self._persist_error(
                        connection,
                        error_id=f"error-budget-overage:{claim.attempt_id}",
                        run_id=claim.run_id,
                        step_id=claim.step_id,
                        attempt_id=claim.attempt_id,
                        error=overage_error,
                        created_at=completed_at,
                    )
                    connection.execute(
                        "UPDATE budget_limits SET state = 'stopped' WHERE run_id = ?",
                        (claim.run_id,),
                    )
                    connection.execute(
                        "UPDATE steps SET status = 'skipped' WHERE run_id = ? AND status = 'pending'",
                        (claim.run_id,),
                    )
                    connection.execute(
                        "UPDATE runs SET status = 'failed', updated_at = ? WHERE run_id = ?",
                        (completed_at, claim.run_id),
                    )
                    self._append_run_event(
                        connection,
                        run_id=claim.run_id,
                        step_id=claim.step_id,
                        attempt_id=claim.attempt_id,
                        event_type="budget_actual_exceeded",
                        detail={"actual": totals},
                        created_at=completed_at,
                    )
                row = connection.execute(
                    "SELECT * FROM steps WHERE step_id = ?", (claim.step_id,)
                ).fetchone()
        return _step_from_row(row)

    def _budget_totals(
        self, connection: sqlite3.Connection, run_id: str, entry_kind: str
    ) -> dict[str, int]:
        row = connection.execute(
            """
                SELECT COALESCE(SUM(input_tokens), 0) AS input_tokens,
                       COALESCE(SUM(output_tokens), 0) AS output_tokens,
                       COALESCE(SUM(tool_calls), 0) AS tool_calls,
                       COALESCE(SUM(retrieval_rounds), 0) AS retrieval_rounds
                FROM budget_entries WHERE run_id = ? AND entry_kind = ?
                """,
            (run_id, entry_kind),
        ).fetchone()
        return {dimension: int(row[dimension]) for dimension in _BUDGET_DIMENSIONS}

    def _active_reservation_totals(
        self, connection: sqlite3.Connection, run_id: str
    ) -> dict[str, int]:
        row = connection.execute(
            """
                SELECT COALESCE(SUM(input_tokens), 0) AS input_tokens,
                       COALESCE(SUM(output_tokens), 0) AS output_tokens,
                       COALESCE(SUM(tool_calls), 0) AS tool_calls,
                       COALESCE(SUM(retrieval_rounds), 0) AS retrieval_rounds
                FROM budget_reservations WHERE run_id = ? AND status = 'active'
                """,
            (run_id,),
        ).fetchone()
        return {dimension: int(row[dimension]) for dimension in _BUDGET_DIMENSIONS}

    def _insert_budget_entry(
        self,
        connection: sqlite3.Connection,
        claim: LeaseClaim,
        reservation_id: str,
        entry_kind: str,
        amount: BudgetAmount,
        source: str,
        created_at: str,
    ) -> None:
        connection.execute(
            """
                INSERT INTO budget_entries(
                    run_id, step_id, attempt_id, reservation_id, entry_kind,
                    input_tokens, output_tokens, tool_calls, retrieval_rounds,
                    source, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
            (
                claim.run_id,
                claim.step_id,
                claim.attempt_id,
                reservation_id,
                entry_kind,
                amount.input_tokens,
                amount.output_tokens,
                amount.tool_calls,
                amount.retrieval_rounds,
                source,
                created_at,
            ),
        )

    def _release_stale_reservations(
        self, connection: sqlite3.Connection, run_id: str, now: str
    ) -> None:
        rows = connection.execute(
            """
                SELECT br.*, l.expires_at, l.released_at, a.status AS attempt_status
                FROM budget_reservations br
                JOIN attempts a ON a.attempt_id = br.attempt_id
                JOIN leases l ON l.attempt_id = br.attempt_id
                WHERE br.run_id = ? AND br.status = 'active'
                  AND (a.status != 'running' OR l.released_at IS NOT NULL OR l.expires_at <= ?)
                """,
            (run_id, now),
        ).fetchall()
        for row in rows:
            self._close_reservation_row(
                connection, row, "external_outcome_not_reported", now
            )

    def _close_active_reservation(
        self, connection: sqlite3.Connection, claim: LeaseClaim, source: str, now: str
    ) -> None:
        row = connection.execute(
            "SELECT * FROM budget_reservations WHERE attempt_id = ? AND status = 'active'",
            (claim.attempt_id,),
        ).fetchone()
        if row is not None:
            self._close_reservation_row(connection, row, source, now)

    def _close_reservation_row(
        self, connection: sqlite3.Connection, row: sqlite3.Row, source: str, now: str
    ) -> None:
        values = tuple(int(row[dimension]) for dimension in _BUDGET_DIMENSIONS)
        connection.execute(
            """
                INSERT INTO budget_entries(
                    run_id, step_id, attempt_id, reservation_id, entry_kind,
                    input_tokens, output_tokens, tool_calls, retrieval_rounds,
                    source, created_at
                ) VALUES (?, ?, ?, ?, 'unknown_actual', 0, 0, 0, 0, ?, ?)
                """,
            (
                row["run_id"],
                row["step_id"],
                row["attempt_id"],
                row["reservation_id"],
                source,
                now,
            ),
        )
        connection.execute(
            """
                INSERT INTO budget_entries(
                    run_id, step_id, attempt_id, reservation_id, entry_kind,
                    input_tokens, output_tokens, tool_calls, retrieval_rounds,
                    source, created_at
                ) VALUES (?, ?, ?, ?, 'release', ?, ?, ?, ?, ?, ?)
                """,
            (
                row["run_id"],
                row["step_id"],
                row["attempt_id"],
                row["reservation_id"],
                *values,
                source,
                now,
            ),
        )
        connection.execute(
            "UPDATE budget_reservations SET status = 'released', closed_at = ? WHERE reservation_id = ?",
            (now, row["reservation_id"]),
        )

    def _persist_error(
        self,
        connection: sqlite3.Connection,
        *,
        error_id: str,
        run_id: str,
        step_id: str | None,
        attempt_id: str | None,
        error: ErrorRecord,
        created_at: str,
    ) -> None:
        if not error.recovery_action.strip():
            raise PersistenceInvariantError(
                "structured Error requires a recovery action"
            )
        if (
            connection.execute(
                "SELECT 1 FROM artifacts WHERE artifact_id = ?",
                (error.technical_detail_ref,),
            ).fetchone()
            is None
        ):
            raise PersistenceInvariantError(
                "Error technical detail Artifact is not registered"
            )
        for artifact_id in error.affected_artifact_refs:
            if (
                connection.execute(
                    "SELECT 1 FROM artifacts WHERE artifact_id = ?", (artifact_id,)
                ).fetchone()
                is None
            ):
                raise PersistenceInvariantError(
                    "affected Error Artifact is not registered"
                )
        connection.execute(
            """
                INSERT INTO errors(
                    error_id, run_id, step_id, attempt_id, code, category, stage,
                    retryable, user_message, technical_detail_ref, recovery_action,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
            (
                error_id,
                run_id,
                step_id,
                attempt_id,
                error.code,
                error.category.value,
                error.stage,
                int(error.retryable),
                error.user_message,
                error.technical_detail_ref,
                error.recovery_action,
                created_at,
            ),
        )
        for ordinal, artifact_id in enumerate(error.affected_artifact_refs):
            connection.execute(
                "INSERT INTO error_artifacts(error_id, ordinal, artifact_id) VALUES (?, ?, ?)",
                (error_id, ordinal, artifact_id),
            )

    def block_unknown_external_outcome(
        self,
        claim: LeaseClaim,
        detail_artifact: ArtifactRef,
        *,
        now: str | None = None,
    ) -> RunRecord:
        blocked_at = _normalize_timestamp(now or self.clock())
        if detail_artifact.producer_step_id != claim.step_id:
            raise PersistenceInvariantError(
                "unknown-outcome detail producer does not match claimed Step"
            )
        self._validate_artifact(detail_artifact)
        with self._connect() as connection:
            with _transaction(connection):
                self._require_active_attempt(connection, claim, blocked_at)
                effect = connection.execute(
                    "SELECT * FROM attempt_effects WHERE attempt_id = ?",
                    (claim.attempt_id,),
                ).fetchone()
                if (
                    effect is None
                    or effect["side_effect"]
                    != SideEffectClass.PAID_EXTERNAL_UNKNOWN.value
                    or effect["effect_state"] != "request_started"
                ):
                    raise PersistenceInvariantError(
                        "Attempt has no unknown paid external outcome"
                    )
                self._close_active_reservation(
                    connection, claim, "provider_outcome_unknown", blocked_at
                )
                self._register_artifact(connection, detail_artifact)
                connection.execute(
                    """
                        UPDATE attempts
                        SET status = 'fenced', finished_at = ?, error_ref = ?
                        WHERE attempt_id = ? AND status = 'running'
                        """,
                    (blocked_at, detail_artifact.artifact_id, claim.attempt_id),
                )
                connection.execute(
                    """
                        UPDATE leases SET released_at = ?
                        WHERE attempt_id = ? AND released_at IS NULL
                        """,
                    (blocked_at, claim.attempt_id),
                )
                connection.execute(
                    """
                        UPDATE steps SET status = 'waiting_for_user', error_ref = ?
                        WHERE step_id = ? AND status = 'running' AND attempt = ?
                        """,
                    (
                        detail_artifact.artifact_id,
                        claim.step_id,
                        claim.attempt_number,
                    ),
                )
                connection.execute(
                    """
                        UPDATE runs SET status = 'waiting_for_user', updated_at = ?
                        WHERE run_id = ? AND status = 'running'
                        """,
                    (blocked_at, claim.run_id),
                )
                self._append_run_event(
                    connection,
                    run_id=claim.run_id,
                    step_id=claim.step_id,
                    attempt_id=claim.attempt_id,
                    event_type="recovery_decision_required",
                    detail={
                        "reason": "provider_outcome_unknown",
                        "detail_artifact_ref": detail_artifact.artifact_id,
                        "automatic_replay": False,
                    },
                    created_at=blocked_at,
                )
                row = connection.execute(
                    "SELECT * FROM runs WHERE run_id = ?", (claim.run_id,)
                ).fetchone()
        return _run_from_row(row)
