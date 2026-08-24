"""SQLite Task/Run submission, transition, cancellation, and resume lifecycle."""

from __future__ import annotations

from dataclasses import asdict
import json
import sqlite3
from typing import Mapping, Sequence

from conflux_weave.core import (
    RunRecord,
    RunStatus,
    StepRecord,
    StepStatus,
    TaskSpec,
    require_transition,
)
from conflux_weave.evidence import ArtifactRef
from conflux_weave.runtime.sqlite_contracts import (
    IdempotencyConflict,
    LeaseConflict,
    PersistenceInvariantError,
    RecordNotFound,
    RecoveryDecision,
    RecoveryDecisionRequired,
    SideEffectClass,
    StepPolicy,
    SubmissionResult,
)
from conflux_weave.runtime.sqlite_base import (
    _validate_submission,
    _run_from_row,
    _step_from_row,
    _canonical_json,
    _normalize_timestamp,
    _transaction,
)


class TaskRunRepositoryMixin:
    def submit_task(
        self,
        task: TaskSpec,
        run: RunRecord,
        steps: Sequence[StepRecord],
        *,
        step_policies: Mapping[str, StepPolicy] | None = None,
        submission_artifacts: Sequence[ArtifactRef] = (),
    ) -> SubmissionResult:
        _validate_submission(task, run, steps)
        policies = dict(step_policies or {})
        unknown_policy_steps = set(policies) - {step.step_id for step in steps}
        if unknown_policy_steps:
            raise PersistenceInvariantError(
                "Step policies reference unknown Steps: "
                + ", ".join(sorted(unknown_policy_steps))
            )
        for artifact in submission_artifacts:
            self._validate_artifact(artifact)
        task_input = _canonical_json(task.input)
        with self._connect() as connection:
            with _transaction(connection):
                existing = connection.execute(
                    """
                        SELECT task_id, kind, input_json, requested_policy
                        FROM tasks
                        WHERE idempotency_key = ?
                        """,
                    (task.idempotency_key,),
                ).fetchone()
                if existing is not None:
                    if (
                        existing["kind"] != task.kind
                        or existing["input_json"] != task_input
                        or existing["requested_policy"] != task.requested_policy
                    ):
                        raise IdempotencyConflict(
                            "idempotency key is already bound to different task input"
                        )
                    existing_run = connection.execute(
                        """
                            SELECT run_id
                            FROM runs
                            WHERE task_id = ?
                            ORDER BY created_at, run_id
                            LIMIT 1
                            """,
                        (existing["task_id"],),
                    ).fetchone()
                    if existing_run is None:
                        raise PersistenceInvariantError(
                            "idempotent task exists without an initial Run"
                        )
                    return SubmissionResult(
                        task_id=str(existing["task_id"]),
                        run_id=str(existing_run["run_id"]),
                        created=False,
                    )

                try:
                    connection.execute(
                        """
                            INSERT INTO tasks(
                                task_id, kind, input_json, requested_policy,
                                idempotency_key, created_at
                            ) VALUES (?, ?, ?, ?, ?, ?)
                            """,
                        (
                            task.task_id,
                            task.kind,
                            task_input,
                            task.requested_policy,
                            task.idempotency_key,
                            run.created_at,
                        ),
                    )
                    connection.execute(
                        """
                            INSERT INTO runs(
                                run_id, task_id, status, workflow_version,
                                config_snapshot_ref, budget_json, predecessor_run_id,
                                created_at, updated_at
                            ) VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?)
                            """,
                        (
                            run.run_id,
                            run.task_id,
                            run.status.value,
                            run.workflow_version,
                            run.config_snapshot_ref,
                            _canonical_json(asdict(run.budget)),
                            run.created_at,
                            run.updated_at,
                        ),
                    )
                    connection.execute(
                        """
                            INSERT INTO budget_limits(
                                run_id, wall_clock_seconds, input_tokens,
                                output_tokens, tool_calls, retrieval_rounds,
                                concurrency, estimated_cost_limit,
                                cost_enforcement, state, created_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'unavailable', 'active', ?)
                            """,
                        (
                            run.run_id,
                            run.budget.wall_clock_seconds,
                            run.budget.input_tokens,
                            run.budget.output_tokens,
                            run.budget.tool_calls,
                            run.budget.retrieval_rounds,
                            run.budget.concurrency,
                            run.budget.estimated_cost,
                            run.created_at,
                        ),
                    )
                    for ordinal, step in enumerate(steps):
                        connection.execute(
                            """
                                INSERT INTO steps(
                                    step_id, run_id, ordinal, kind, attempt, status,
                                    input_refs_json, output_refs_json, error_ref
                                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                                """,
                            (
                                step.step_id,
                                step.run_id,
                                ordinal,
                                step.kind,
                                step.attempt,
                                step.status.value,
                                _canonical_json(step.input_refs),
                                _canonical_json(step.output_refs),
                                step.error_ref,
                            ),
                        )
                        policy = policies.get(step.step_id)
                        if policy is not None:
                            connection.execute(
                                """
                                    INSERT INTO step_policies(
                                        step_id, side_effect, recovery_rule
                                    ) VALUES (?, ?, ?)
                                    """,
                                (
                                    step.step_id,
                                    policy.side_effect.value,
                                    policy.recovery_rule,
                                ),
                            )
                    for artifact in submission_artifacts:
                        self._register_artifact(connection, artifact)
                except sqlite3.IntegrityError as exc:
                    raise PersistenceInvariantError(
                        f"task submission violates persistence constraints: {exc}"
                    ) from exc
        return SubmissionResult(task.task_id, run.run_id, created=True)

    def get_task_by_idempotency_key(self, idempotency_key: str) -> TaskSpec:
        with self._connect() as connection:
            row = connection.execute(
                """
                    SELECT task_id, kind, input_json, requested_policy, idempotency_key
                    FROM tasks
                    WHERE idempotency_key = ?
                    """,
                (idempotency_key,),
            ).fetchone()
        if row is None:
            raise RecordNotFound("Task not found")
        return TaskSpec(
            task_id=str(row["task_id"]),
            kind=str(row["kind"]),
            input=json.loads(row["input_json"]),
            requested_policy=str(row["requested_policy"]),
            idempotency_key=str(row["idempotency_key"]),
        )

    def get_task_for_run(self, run_id: str) -> TaskSpec:
        with self._connect() as connection:
            row = connection.execute(
                """
                    SELECT t.task_id, t.kind, t.input_json, t.requested_policy,
                           t.idempotency_key
                    FROM runs r JOIN tasks t ON t.task_id = r.task_id
                    WHERE r.run_id = ?
                    """,
                (run_id,),
            ).fetchone()
        if row is None:
            raise RecordNotFound("Task for Run not found")
        return TaskSpec(
            task_id=str(row["task_id"]),
            kind=str(row["kind"]),
            input=json.loads(row["input_json"]),
            requested_policy=str(row["requested_policy"]),
            idempotency_key=str(row["idempotency_key"]),
        )

    def get_run(self, run_id: str) -> RunRecord:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        if row is None:
            raise RecordNotFound("Run not found")
        return _run_from_row(row)

    def get_steps(self, run_id: str) -> tuple[StepRecord, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM steps WHERE run_id = ? ORDER BY ordinal", (run_id,)
            ).fetchall()
        return tuple(_step_from_row(row) for row in rows)

    def request_cancel(self, run_id: str, *, now: str | None = None) -> RunRecord:
        requested_at = _normalize_timestamp(now or self.clock())
        with self._connect() as connection:
            with _transaction(connection):
                row = connection.execute(
                    "SELECT * FROM runs WHERE run_id = ?", (run_id,)
                ).fetchone()
                if row is None:
                    raise RecordNotFound("Run not found")
                current = RunStatus(row["status"])
                if current.is_terminal:
                    return _run_from_row(row)
                active = connection.execute(
                    """
                        SELECT 1
                        FROM attempts a
                        JOIN steps s ON s.step_id = a.step_id
                        JOIN leases l ON l.attempt_id = a.attempt_id
                        WHERE s.run_id = ? AND a.status = 'running'
                          AND l.released_at IS NULL
                        LIMIT 1
                        """,
                    (run_id,),
                ).fetchone()
                target = (
                    RunStatus.CANCELLING if active is not None else RunStatus.CANCELLED
                )
                connection.execute(
                    "UPDATE runs SET status = ?, updated_at = ? WHERE run_id = ?",
                    (target.value, requested_at, run_id),
                )
                if target is RunStatus.CANCELLED:
                    connection.execute(
                        """
                            UPDATE steps SET status = 'cancelled'
                            WHERE run_id = ? AND status IN ('pending', 'waiting_for_user')
                            """,
                        (run_id,),
                    )
                self._append_run_event(
                    connection,
                    run_id=run_id,
                    step_id=None,
                    attempt_id=None,
                    event_type="cancel_requested",
                    detail={"in_flight": active is not None},
                    created_at=requested_at,
                )
                updated = connection.execute(
                    "SELECT * FROM runs WHERE run_id = ?", (run_id,)
                ).fetchone()
        return _run_from_row(updated)

    def finalize_cancellation(
        self, run_id: str, *, now: str | None = None
    ) -> RunRecord:
        cancelled_at = _normalize_timestamp(now or self.clock())
        with self._connect() as connection:
            with _transaction(connection):
                row = connection.execute(
                    "SELECT * FROM runs WHERE run_id = ?", (run_id,)
                ).fetchone()
                if row is None:
                    raise RecordNotFound("Run not found")
                if row["status"] != RunStatus.CANCELLING.value:
                    return _run_from_row(row)
                active = connection.execute(
                    """
                        SELECT 1 FROM attempts a
                        JOIN steps s ON s.step_id = a.step_id
                        JOIN leases l ON l.attempt_id = a.attempt_id
                        WHERE s.run_id = ? AND a.status = 'running'
                          AND l.released_at IS NULL
                        LIMIT 1
                        """,
                    (run_id,),
                ).fetchone()
                if active is not None:
                    raise LeaseConflict("Run still has an in-flight Attempt")
                connection.execute(
                    """
                        UPDATE steps SET status = 'cancelled'
                        WHERE run_id = ? AND status IN ('pending', 'waiting_for_user')
                        """,
                    (run_id,),
                )
                connection.execute(
                    """
                        UPDATE runs SET status = 'cancelled', updated_at = ?
                        WHERE run_id = ? AND status = 'cancelling'
                        """,
                    (cancelled_at, run_id),
                )
                updated = connection.execute(
                    "SELECT * FROM runs WHERE run_id = ?", (run_id,)
                ).fetchone()
        return _run_from_row(updated)

    def resume_run(
        self,
        run_id: str,
        decision: RecoveryDecision | None = None,
        *,
        now: str | None = None,
    ) -> RunRecord:
        decided_at = _normalize_timestamp(now or self.clock())
        with self._connect() as connection:
            with _transaction(connection):
                run = connection.execute(
                    "SELECT * FROM runs WHERE run_id = ?", (run_id,)
                ).fetchone()
                if run is None:
                    raise RecordNotFound("Run not found")
                if run["status"] != RunStatus.WAITING_FOR_USER.value:
                    raise PersistenceInvariantError(
                        "resume only applies to a waiting_for_user Run"
                    )
                step = connection.execute(
                    """
                        SELECT s.step_id, a.attempt_id, ae.effect_state, ae.side_effect
                        FROM steps s
                        JOIN attempts a ON a.step_id = s.step_id
                        JOIN attempt_effects ae ON ae.attempt_id = a.attempt_id
                        WHERE s.run_id = ? AND s.status = 'waiting_for_user'
                        ORDER BY a.attempt_number DESC LIMIT 1
                        """,
                    (run_id,),
                ).fetchone()
                if (
                    step is None
                    or step["side_effect"]
                    != SideEffectClass.PAID_EXTERNAL_UNKNOWN.value
                    or step["effect_state"] != "request_started"
                ):
                    raise PersistenceInvariantError(
                        "Run has no resumable unknown Provider outcome"
                    )
                if decision is None:
                    raise RecoveryDecisionRequired(
                        "unknown Provider outcome requires retry or fail decision"
                    )
                if decision is RecoveryDecision.RETRY_UNKNOWN_EXTERNAL:
                    step_status = StepStatus.PENDING.value
                    run_status = RunStatus.QUEUED.value
                    error_ref = None
                elif decision is RecoveryDecision.FAIL_UNKNOWN_EXTERNAL:
                    step_status = StepStatus.FAILED.value
                    run_status = RunStatus.FAILED.value
                    error_ref = connection.execute(
                        "SELECT error_ref FROM steps WHERE step_id = ?",
                        (step["step_id"],),
                    ).fetchone()["error_ref"]
                else:
                    raise PersistenceInvariantError("unsupported recovery decision")
                connection.execute(
                    "UPDATE steps SET status = ?, error_ref = ? WHERE step_id = ?",
                    (step_status, error_ref, step["step_id"]),
                )
                connection.execute(
                    "UPDATE runs SET status = ?, updated_at = ? WHERE run_id = ?",
                    (run_status, decided_at, run_id),
                )
                self._append_run_event(
                    connection,
                    run_id=run_id,
                    step_id=str(step["step_id"]),
                    attempt_id=str(step["attempt_id"]),
                    event_type="recovery_decided",
                    detail={"decision": decision.value, "automatic_replay": False},
                    created_at=decided_at,
                )
                updated = connection.execute(
                    "SELECT * FROM runs WHERE run_id = ?", (run_id,)
                ).fetchone()
        return _run_from_row(updated)

    def is_cancel_requested(self, run_id: str) -> bool:
        return self.get_run(run_id).status in {
            RunStatus.CANCELLING,
            RunStatus.CANCELLED,
        }

    def transition_run(
        self,
        run_id: str,
        target: RunStatus,
        *,
        updated_at: str | None = None,
    ) -> RunRecord:
        if target in {RunStatus.SUCCEEDED, RunStatus.PARTIAL}:
            raise PersistenceInvariantError(
                "successful terminal states require atomic Delivery publication"
            )
        timestamp = updated_at or self.clock()
        with self._connect() as connection:
            with _transaction(connection):
                row = connection.execute(
                    "SELECT * FROM runs WHERE run_id = ?", (run_id,)
                ).fetchone()
                if row is None:
                    raise RecordNotFound("Run not found")
                current = RunStatus(row["status"])
                require_transition(current, target)
                changed = connection.execute(
                    """
                        UPDATE runs
                        SET status = ?, updated_at = ?
                        WHERE run_id = ? AND status = ?
                        """,
                    (target.value, timestamp, run_id, current.value),
                ).rowcount
                if changed != 1:
                    raise PersistenceInvariantError(
                        "Run status changed during transition"
                    )
                updated = connection.execute(
                    "SELECT * FROM runs WHERE run_id = ?", (run_id,)
                ).fetchone()
        return _run_from_row(updated)
