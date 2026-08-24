"""SQLite Attempt, Lease, heartbeat, fencing, and claim lifecycle."""

from __future__ import annotations

import sqlite3
from typing import Sequence

from conflux_weave.core import RunRecord, RunStatus, StepRecord, StepStatus
from conflux_weave.evidence import ArtifactRef
from conflux_weave.runtime.sqlite_contracts import (
    AttemptEffectRecord,
    AttemptRecord,
    LeaseClaim,
    LeaseConflict,
    PersistenceInvariantError,
    RecordNotFound,
    SideEffectClass,
)
from conflux_weave.runtime.sqlite_base import (
    _run_from_row,
    _step_from_row,
    _attempt_from_row,
    _attempt_effect_from_row,
    _canonical_json,
    _validate_worker_request,
    _normalize_timestamp,
    _add_seconds,
    _transaction,
)


class LeaseRepositoryMixin:
    def claim_next_step(
        self,
        worker_id: str,
        *,
        lease_seconds: int,
        now: str | None = None,
    ) -> LeaseClaim | None:
        _validate_worker_request(worker_id, lease_seconds)
        claimed_at = _normalize_timestamp(now or self.clock())
        expires_at = _add_seconds(claimed_at, lease_seconds)
        with self._connect() as connection:
            with _transaction(connection):
                cancelling = connection.execute(
                    """
                        SELECT r.run_id, s.step_id, a.attempt_id
                        FROM runs r
                        JOIN steps s ON s.run_id = r.run_id
                        JOIN attempts a ON a.step_id = s.step_id
                        JOIN leases l ON l.attempt_id = a.attempt_id
                        WHERE r.status = 'cancelling' AND a.status = 'running'
                          AND l.released_at IS NULL AND l.expires_at <= ?
                        LIMIT 1
                        """,
                    (claimed_at,),
                ).fetchone()
                if cancelling is not None:
                    connection.execute(
                        """
                            UPDATE attempts SET status = 'fenced', finished_at = ?
                            WHERE attempt_id = ? AND status = 'running'
                            """,
                        (claimed_at, cancelling["attempt_id"]),
                    )
                    connection.execute(
                        """
                            UPDATE leases SET released_at = ?
                            WHERE attempt_id = ? AND released_at IS NULL
                            """,
                        (claimed_at, cancelling["attempt_id"]),
                    )
                    connection.execute(
                        """
                            UPDATE steps SET status = 'cancelled'
                            WHERE run_id = ? AND status IN (
                                'pending', 'running', 'waiting_for_user'
                            )
                            """,
                        (cancelling["run_id"],),
                    )
                    connection.execute(
                        """
                            UPDATE runs SET status = 'cancelled', updated_at = ?
                            WHERE run_id = ? AND status = 'cancelling'
                            """,
                        (claimed_at, cancelling["run_id"]),
                    )
                    self._append_run_event(
                        connection,
                        run_id=str(cancelling["run_id"]),
                        step_id=str(cancelling["step_id"]),
                        attempt_id=str(cancelling["attempt_id"]),
                        event_type="cancelled_after_lease_expiry",
                        detail={},
                        created_at=claimed_at,
                    )
                active_lease = connection.execute(
                    """
                        SELECT 1 FROM leases
                        WHERE released_at IS NULL AND expires_at > ?
                        LIMIT 1
                        """,
                    (claimed_at,),
                ).fetchone()
                if active_lease is not None:
                    return None
                row = connection.execute(
                    """
                        SELECT s.*, r.status AS run_status
                        FROM steps s
                        JOIN runs r ON r.run_id = s.run_id
                        WHERE r.status IN ('queued', 'running')
                          AND NOT EXISTS (
                              SELECT 1
                              FROM steps prior
                              WHERE prior.run_id = s.run_id
                                AND prior.ordinal < s.ordinal
                                AND prior.status NOT IN ('succeeded', 'skipped')
                          )
                          AND (
                              (
                                  s.status = 'pending'
                                  AND NOT EXISTS (
                                      SELECT 1 FROM attempts a
                                      WHERE a.step_id = s.step_id
                                        AND a.status = 'running'
                                  )
                              )
                              OR (
                                  s.status = 'running'
                                  AND EXISTS (
                                      SELECT 1
                                      FROM attempts a
                                      JOIN leases l ON l.attempt_id = a.attempt_id
                                      WHERE a.step_id = s.step_id
                                        AND a.attempt_number = s.attempt
                                        AND a.status = 'running'
                                        AND l.released_at IS NULL
                                        AND l.expires_at <= ?
                                  )
                              )
                          )
                        ORDER BY r.created_at, r.run_id, s.ordinal
                        LIMIT 1
                        """,
                    (claimed_at,),
                ).fetchone()
                if row is None:
                    return None

                attempt_number = int(row["attempt"])
                if row["status"] == StepStatus.PENDING.value:
                    previous_number = connection.execute(
                        "SELECT MAX(attempt_number) FROM attempts WHERE step_id = ?",
                        (row["step_id"],),
                    ).fetchone()[0]
                    if previous_number is not None:
                        attempt_number = int(previous_number) + 1
                if row["status"] == StepStatus.RUNNING.value:
                    previous = connection.execute(
                        """
                            SELECT
                                a.attempt_id, a.worker_id, a.fencing_token,
                                COALESCE(sp.side_effect, 'none') AS side_effect,
                                COALESCE(ae.effect_state, 'not_started') AS effect_state
                            FROM attempts a
                            JOIN leases l ON l.attempt_id = a.attempt_id
                            LEFT JOIN step_policies sp ON sp.step_id = a.step_id
                            LEFT JOIN attempt_effects ae ON ae.attempt_id = a.attempt_id
                            WHERE a.step_id = ? AND a.attempt_number = ?
                              AND a.status = 'running' AND l.released_at IS NULL
                              AND l.expires_at <= ?
                            """,
                        (row["step_id"], attempt_number, claimed_at),
                    ).fetchone()
                    if previous is None:
                        raise LeaseConflict("expired Step lease changed during claim")
                    connection.execute(
                        """
                            UPDATE attempts
                            SET status = 'fenced', finished_at = ?
                            WHERE attempt_id = ? AND status = 'running'
                            """,
                        (claimed_at, previous["attempt_id"]),
                    )
                    connection.execute(
                        """
                            UPDATE leases SET released_at = ?
                            WHERE attempt_id = ? AND released_at IS NULL
                            """,
                        (claimed_at, previous["attempt_id"]),
                    )
                    if (
                        previous["side_effect"]
                        == SideEffectClass.PAID_EXTERNAL_UNKNOWN.value
                        and previous["effect_state"] == "request_started"
                    ):
                        connection.execute(
                            """
                                UPDATE steps SET status = 'waiting_for_user'
                                WHERE step_id = ? AND status = 'running'
                                """,
                            (row["step_id"],),
                        )
                        connection.execute(
                            """
                                UPDATE runs SET status = 'waiting_for_user', updated_at = ?
                                WHERE run_id = ? AND status = 'running'
                                """,
                            (claimed_at, row["run_id"]),
                        )
                        self._append_run_event(
                            connection,
                            run_id=str(row["run_id"]),
                            step_id=str(row["step_id"]),
                            attempt_id=str(previous["attempt_id"]),
                            event_type="recovery_decision_required",
                            detail={
                                "reason": "provider_outcome_unknown",
                                "automatic_replay": False,
                            },
                            created_at=claimed_at,
                        )
                        return None
                    self._append_run_event(
                        connection,
                        run_id=str(row["run_id"]),
                        step_id=str(row["step_id"]),
                        attempt_id=str(previous["attempt_id"]),
                        event_type="attempt_fenced",
                        detail={
                            "previous_worker_id": str(previous["worker_id"]),
                            "previous_fencing_token": int(previous["fencing_token"]),
                            "reason": "lease_expired",
                        },
                        created_at=claimed_at,
                    )
                    attempt_number += 1

                fencing_token = int(
                    connection.execute(
                        "SELECT COALESCE(MAX(fencing_token), 0) + 1 FROM attempts"
                    ).fetchone()[0]
                )
                attempt_id = f"{row['step_id']}:attempt:{attempt_number}"
                connection.execute(
                    """
                        INSERT INTO attempts(
                            attempt_id, step_id, attempt_number, worker_id,
                            fencing_token, status, started_at, finished_at, error_ref
                        ) VALUES (?, ?, ?, ?, ?, 'running', ?, NULL, NULL)
                        """,
                    (
                        attempt_id,
                        row["step_id"],
                        attempt_number,
                        worker_id,
                        fencing_token,
                        claimed_at,
                    ),
                )
                policy = connection.execute(
                    "SELECT side_effect FROM step_policies WHERE step_id = ?",
                    (row["step_id"],),
                ).fetchone()
                side_effect = (
                    str(policy["side_effect"])
                    if policy is not None
                    else SideEffectClass.NONE.value
                )
                connection.execute(
                    """
                        INSERT INTO attempt_effects(
                            attempt_id, side_effect, effect_state,
                            intent_artifact_ref, request_artifact_ref,
                            response_artifact_ref, external_response_id, updated_at
                        ) VALUES (?, ?, 'not_started', NULL, NULL, NULL, NULL, ?)
                        """,
                    (attempt_id, side_effect, claimed_at),
                )
                connection.execute(
                    """
                        INSERT INTO leases(
                            attempt_id, worker_id, fencing_token, acquired_at,
                            heartbeat_at, expires_at, released_at
                        ) VALUES (?, ?, ?, ?, ?, ?, NULL)
                        """,
                    (
                        attempt_id,
                        worker_id,
                        fencing_token,
                        claimed_at,
                        claimed_at,
                        expires_at,
                    ),
                )
                connection.execute(
                    """
                        UPDATE steps
                        SET status = 'running', attempt = ?
                        WHERE step_id = ?
                        """,
                    (attempt_number, row["step_id"]),
                )
                if row["run_status"] == RunStatus.QUEUED.value:
                    connection.execute(
                        """
                            UPDATE runs SET status = 'running', updated_at = ?
                            WHERE run_id = ? AND status = 'queued'
                            """,
                        (claimed_at, row["run_id"]),
                    )
                self._append_run_event(
                    connection,
                    run_id=str(row["run_id"]),
                    step_id=str(row["step_id"]),
                    attempt_id=attempt_id,
                    event_type="step_claimed",
                    detail={
                        "attempt_number": attempt_number,
                        "fencing_token": fencing_token,
                        "worker_id": worker_id,
                    },
                    created_at=claimed_at,
                )
        return LeaseClaim(
            attempt_id=attempt_id,
            step_id=str(row["step_id"]),
            run_id=str(row["run_id"]),
            worker_id=worker_id,
            attempt_number=attempt_number,
            fencing_token=fencing_token,
            acquired_at=claimed_at,
            heartbeat_at=claimed_at,
            expires_at=expires_at,
        )

    def heartbeat_attempt(
        self,
        claim: LeaseClaim,
        *,
        lease_seconds: int,
        now: str | None = None,
    ) -> LeaseClaim:
        _validate_worker_request(claim.worker_id, lease_seconds)
        heartbeat_at = _normalize_timestamp(now or self.clock())
        expires_at = _add_seconds(heartbeat_at, lease_seconds)
        with self._connect() as connection:
            with _transaction(connection):
                self._require_active_attempt(connection, claim, heartbeat_at)
                connection.execute(
                    """
                        UPDATE leases
                        SET heartbeat_at = ?, expires_at = ?
                        WHERE attempt_id = ? AND fencing_token = ?
                          AND worker_id = ? AND released_at IS NULL
                          AND heartbeat_at <= ?
                        """,
                    (
                        heartbeat_at,
                        expires_at,
                        claim.attempt_id,
                        claim.fencing_token,
                        claim.worker_id,
                        heartbeat_at,
                    ),
                )
        return LeaseClaim(
            attempt_id=claim.attempt_id,
            step_id=claim.step_id,
            run_id=claim.run_id,
            worker_id=claim.worker_id,
            attempt_number=claim.attempt_number,
            fencing_token=claim.fencing_token,
            acquired_at=claim.acquired_at,
            heartbeat_at=heartbeat_at,
            expires_at=expires_at,
        )

    def complete_attempt(
        self,
        claim: LeaseClaim,
        artifacts: Sequence[ArtifactRef] = (),
        *,
        now: str | None = None,
    ) -> StepRecord:
        completed_at = _normalize_timestamp(now or self.clock())
        artifact_ids = tuple(artifact.artifact_id for artifact in artifacts)
        if len(artifact_ids) != len(set(artifact_ids)):
            raise PersistenceInvariantError("Step output Artifact ids must be unique")
        for artifact in artifacts:
            if artifact.producer_step_id != claim.step_id:
                raise PersistenceInvariantError(
                    "Step output Artifact producer does not match claimed Step"
                )
            self._validate_artifact(artifact)

        with self._connect() as connection:
            with _transaction(connection):
                self._require_active_attempt(connection, claim, completed_at)
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
                    event_type="step_succeeded",
                    detail={"artifact_refs": artifact_ids},
                    created_at=completed_at,
                )
                row = connection.execute(
                    "SELECT * FROM steps WHERE step_id = ?", (claim.step_id,)
                ).fetchone()
        return _step_from_row(row)

    def fail_attempt(
        self,
        claim: LeaseClaim,
        error_ref: str,
        *,
        now: str | None = None,
    ) -> StepRecord:
        if not error_ref.strip():
            raise PersistenceInvariantError("failed Attempt requires an error_ref")
        failed_at = _normalize_timestamp(now or self.clock())
        with self._connect() as connection:
            with _transaction(connection):
                self._require_active_attempt(connection, claim, failed_at)
                self._close_active_reservation(
                    connection, claim, "attempt_failed_before_usage_report", failed_at
                )
                changed = connection.execute(
                    """
                        UPDATE steps
                        SET status = 'failed', error_ref = ?
                        WHERE step_id = ? AND status = 'running' AND attempt = ?
                        """,
                    (error_ref, claim.step_id, claim.attempt_number),
                ).rowcount
                if changed != 1:
                    raise LeaseConflict("claimed Step is no longer current")
                self._finish_attempt(
                    connection,
                    claim,
                    status="failed",
                    finished_at=failed_at,
                    error_ref=error_ref,
                )
                self._append_run_event(
                    connection,
                    run_id=claim.run_id,
                    step_id=claim.step_id,
                    attempt_id=claim.attempt_id,
                    event_type="step_failed",
                    detail={"error_ref": error_ref},
                    created_at=failed_at,
                )
                row = connection.execute(
                    "SELECT * FROM steps WHERE step_id = ?", (claim.step_id,)
                ).fetchone()
        return _step_from_row(row)

    def get_attempts(self, step_id: str) -> tuple[AttemptRecord, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                    SELECT * FROM attempts
                    WHERE step_id = ? ORDER BY attempt_number
                    """,
                (step_id,),
            ).fetchall()
        return tuple(_attempt_from_row(row) for row in rows)

    def get_attempt_effect(self, attempt_id: str) -> AttemptEffectRecord:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM attempt_effects WHERE attempt_id = ?",
                (attempt_id,),
            ).fetchone()
        if row is None:
            raise RecordNotFound("Attempt effect not found")
        return _attempt_effect_from_row(row)

    def cancel_claim(self, claim: LeaseClaim, *, now: str | None = None) -> RunRecord:
        cancelled_at = _normalize_timestamp(now or self.clock())
        with self._connect() as connection:
            with _transaction(connection):
                self._require_active_attempt(connection, claim, cancelled_at)
                self._close_active_reservation(
                    connection,
                    claim,
                    "attempt_cancelled_before_usage_report",
                    cancelled_at,
                )
                connection.execute(
                    """
                        UPDATE attempts SET status = 'fenced', finished_at = ?
                        WHERE attempt_id = ? AND status = 'running'
                        """,
                    (cancelled_at, claim.attempt_id),
                )
                connection.execute(
                    """
                        UPDATE leases SET released_at = ?
                        WHERE attempt_id = ? AND released_at IS NULL
                        """,
                    (cancelled_at, claim.attempt_id),
                )
                connection.execute(
                    """
                        UPDATE steps SET status = 'cancelled'
                        WHERE step_id = ? AND status = 'running' AND attempt = ?
                        """,
                    (claim.step_id, claim.attempt_number),
                )
                connection.execute(
                    """
                        UPDATE steps SET status = 'cancelled'
                        WHERE run_id = ? AND status IN ('pending', 'waiting_for_user')
                        """,
                    (claim.run_id,),
                )
                connection.execute(
                    """
                        UPDATE runs SET status = 'cancelled', updated_at = ?
                        WHERE run_id = ? AND status IN ('running', 'cancelling')
                        """,
                    (cancelled_at, claim.run_id),
                )
                self._append_run_event(
                    connection,
                    run_id=claim.run_id,
                    step_id=claim.step_id,
                    attempt_id=claim.attempt_id,
                    event_type="attempt_cancelled",
                    detail={},
                    created_at=cancelled_at,
                )
                row = connection.execute(
                    "SELECT * FROM runs WHERE run_id = ?", (claim.run_id,)
                ).fetchone()
        return _run_from_row(row)

    def _require_active_attempt(
        self,
        connection: sqlite3.Connection,
        claim: LeaseClaim,
        at: str,
    ) -> None:
        row = connection.execute(
            """
                SELECT
                    a.step_id, a.attempt_number, a.worker_id, a.fencing_token,
                    a.status, l.heartbeat_at, l.expires_at, l.released_at
                FROM attempts a
                JOIN leases l ON l.attempt_id = a.attempt_id
                WHERE a.attempt_id = ?
                """,
            (claim.attempt_id,),
        ).fetchone()
        if row is None:
            raise LeaseConflict("Attempt lease does not exist")
        if (
            row["step_id"] != claim.step_id
            or int(row["attempt_number"]) != claim.attempt_number
            or row["worker_id"] != claim.worker_id
            or int(row["fencing_token"]) != claim.fencing_token
            or row["status"] != "running"
            or row["released_at"] is not None
            or row["heartbeat_at"] > at
            or row["expires_at"] <= at
        ):
            raise LeaseConflict("Attempt is expired, released, or fenced")
        current = connection.execute(
            "SELECT status, attempt FROM steps WHERE step_id = ?",
            (claim.step_id,),
        ).fetchone()
        if (
            current is None
            or current["status"] != StepStatus.RUNNING.value
            or int(current["attempt"]) != claim.attempt_number
        ):
            raise LeaseConflict("Attempt no longer owns the current Step")

    @staticmethod
    def _finish_attempt(
        connection: sqlite3.Connection,
        claim: LeaseClaim,
        *,
        status: str,
        finished_at: str,
        error_ref: str | None,
    ) -> None:
        changed = connection.execute(
            """
                UPDATE attempts
                SET status = ?, finished_at = ?, error_ref = ?
                WHERE attempt_id = ? AND status = 'running'
                  AND fencing_token = ? AND worker_id = ?
                """,
            (
                status,
                finished_at,
                error_ref,
                claim.attempt_id,
                claim.fencing_token,
                claim.worker_id,
            ),
        ).rowcount
        if changed != 1:
            raise LeaseConflict("Attempt was fenced during completion")
        connection.execute(
            """
                UPDATE leases SET released_at = ?
                WHERE attempt_id = ? AND released_at IS NULL
                """,
            (finished_at, claim.attempt_id),
        )

    @staticmethod
    def _append_run_event(
        connection: sqlite3.Connection,
        *,
        run_id: str,
        step_id: str | None,
        attempt_id: str | None,
        event_type: str,
        detail: object,
        created_at: str,
    ) -> None:
        connection.execute(
            """
                INSERT INTO run_events(
                    run_id, step_id, attempt_id, event_type, detail_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
            (
                run_id,
                step_id,
                attempt_id,
                event_type,
                _canonical_json(detail),
                created_at,
            ),
        )
