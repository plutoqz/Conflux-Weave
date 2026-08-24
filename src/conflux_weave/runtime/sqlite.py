"""SQLite authority for the bounded W3 runtime."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
import hashlib
import json
from pathlib import Path
import sqlite3
from enum import StrEnum
from typing import Callable, Iterator, Mapping, Sequence

from conflux_weave.core import (
    BudgetLedger,
    DeliveryDisposition,
    DeliveryRecord,
    ErrorCategory,
    ErrorRecord,
    RunRecord,
    RunStatus,
    StepRecord,
    StepStatus,
    TaskSpec,
    require_transition,
)
from conflux_weave.evidence import ArtifactRef
from conflux_weave.runtime.artifacts import LocalArtifactStore


class PersistenceError(RuntimeError):
    """Base error for stable runtime persistence failures."""


class MigrationChecksumError(PersistenceError):
    """Raised when an applied migration no longer matches its source."""


class IdempotencyConflict(PersistenceError):
    """Raised when one idempotency key is reused for different task input."""


class PersistenceInvariantError(PersistenceError):
    """Raised when a write would violate a runtime persistence invariant."""


class ArtifactMetadataConflict(PersistenceInvariantError):
    """Raised when one content address is registered with conflicting storage."""


class RecordNotFound(PersistenceError):
    """Raised when a requested authoritative record does not exist."""


class LeaseConflict(PersistenceInvariantError):
    """Raised when an Attempt no longer owns the active Step lease."""


class RecoveryDecisionRequired(PersistenceInvariantError):
    """Raised when recovery requires an explicit decision about unknown effects."""


@dataclass(frozen=True, slots=True)
class BudgetAmount:
    input_tokens: int = 0
    output_tokens: int = 0
    tool_calls: int = 0
    retrieval_rounds: int = 0


@dataclass(frozen=True, slots=True)
class BudgetStatus:
    run_id: str
    state: str
    limit: BudgetAmount
    actual: BudgetAmount
    reserved: BudgetAmount
    wall_clock_seconds: int
    concurrency: int
    estimated_cost_limit: str
    cost_enforcement: str


@dataclass(frozen=True, slots=True)
class BudgetEntryRecord:
    entry_id: int
    run_id: str
    step_id: str
    attempt_id: str
    reservation_id: str
    entry_kind: str
    amount: BudgetAmount
    source: str
    created_at: str


@dataclass(frozen=True, slots=True)
class StoredErrorRecord:
    error_id: str
    run_id: str
    step_id: str | None
    attempt_id: str | None
    record: ErrorRecord
    created_at: str


class SideEffectClass(StrEnum):
    NONE = "none"
    REPLAYABLE_EXTERNAL_READ = "replayable_external_read"
    PAID_EXTERNAL_UNKNOWN = "paid_external_unknown"
    IDEMPOTENT_LOCAL_WRITE = "idempotent_local_write"


class RecoveryDecision(StrEnum):
    RETRY_UNKNOWN_EXTERNAL = "retry_unknown_external"
    FAIL_UNKNOWN_EXTERNAL = "fail_unknown_external"


_BUDGET_DIMENSIONS = (
    "input_tokens",
    "output_tokens",
    "tool_calls",
    "retrieval_rounds",
)


@dataclass(frozen=True, slots=True)
class StepPolicy:
    side_effect: SideEffectClass
    recovery_rule: str


@dataclass(frozen=True, slots=True)
class SubmissionResult:
    task_id: str
    run_id: str
    created: bool


@dataclass(frozen=True, slots=True)
class MigrationRecord:
    version: int
    name: str
    checksum: str
    applied_at: str


@dataclass(frozen=True, slots=True)
class LeaseClaim:
    attempt_id: str
    step_id: str
    run_id: str
    worker_id: str
    attempt_number: int
    fencing_token: int
    acquired_at: str
    heartbeat_at: str
    expires_at: str


@dataclass(frozen=True, slots=True)
class AttemptRecord:
    attempt_id: str
    step_id: str
    worker_id: str
    attempt_number: int
    fencing_token: int
    status: str
    started_at: str
    finished_at: str | None
    error_ref: str | None


@dataclass(frozen=True, slots=True)
class AttemptEffectRecord:
    attempt_id: str
    side_effect: SideEffectClass
    effect_state: str
    intent_artifact_ref: str | None
    request_artifact_ref: str | None
    response_artifact_ref: str | None
    external_response_id: str | None
    updated_at: str


@dataclass(frozen=True, slots=True)
class _Migration:
    version: int
    name: str
    statements: tuple[str, ...]

    @property
    def checksum(self) -> str:
        payload = "\n-- statement --\n".join(self.statements).encode("utf-8")
        return "sha256:" + hashlib.sha256(payload).hexdigest()


_MIGRATIONS = (
    _Migration(
        version=1,
        name="w3_runtime_authority",
        statements=(
            """
            CREATE TABLE tasks (
                task_id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                input_json TEXT NOT NULL,
                requested_policy TEXT NOT NULL,
                idempotency_key TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE runs (
                run_id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE RESTRICT,
                status TEXT NOT NULL CHECK (status IN (
                    'accepted', 'queued', 'running', 'waiting_for_user',
                    'cancelling', 'succeeded', 'partial', 'failed',
                    'cancelled', 'expired'
                )),
                workflow_version TEXT NOT NULL,
                config_snapshot_ref TEXT NOT NULL,
                budget_json TEXT NOT NULL,
                predecessor_run_id TEXT REFERENCES runs(run_id) ON DELETE RESTRICT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
            """
            CREATE INDEX runs_task_created_idx
            ON runs(task_id, created_at, run_id)
            """,
            """
            CREATE TABLE steps (
                step_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE RESTRICT,
                ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
                kind TEXT NOT NULL,
                attempt INTEGER NOT NULL CHECK (attempt >= 1),
                status TEXT NOT NULL CHECK (status IN (
                    'pending', 'running', 'waiting_for_user', 'succeeded',
                    'failed', 'cancelled', 'skipped'
                )),
                input_refs_json TEXT NOT NULL,
                output_refs_json TEXT NOT NULL,
                error_ref TEXT,
                UNIQUE (run_id, ordinal)
            )
            """,
            """
            CREATE INDEX steps_run_idx ON steps(run_id, step_id)
            """,
            """
            CREATE TABLE artifacts (
                artifact_id TEXT PRIMARY KEY,
                content_hash TEXT NOT NULL UNIQUE,
                storage_uri TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE artifact_registrations (
                registration_id INTEGER PRIMARY KEY AUTOINCREMENT,
                artifact_id TEXT NOT NULL REFERENCES artifacts(artifact_id) ON DELETE RESTRICT,
                media_type TEXT NOT NULL,
                producer_step_id TEXT NOT NULL,
                schema_version TEXT NOT NULL,
                UNIQUE (artifact_id, media_type, producer_step_id, schema_version)
            )
            """,
            """
            CREATE TABLE deliveries (
                run_id TEXT PRIMARY KEY REFERENCES runs(run_id) ON DELETE RESTRICT,
                disposition TEXT NOT NULL CHECK (disposition IN (
                    'complete', 'partial', 'no_answer'
                )),
                evidence_refs_json TEXT NOT NULL,
                limitations_json TEXT NOT NULL,
                unmet_criteria_json TEXT NOT NULL,
                recovery_actions_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE delivery_artifacts (
                run_id TEXT NOT NULL REFERENCES deliveries(run_id) ON DELETE RESTRICT,
                ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
                registration_id INTEGER NOT NULL
                    REFERENCES artifact_registrations(registration_id) ON DELETE RESTRICT,
                PRIMARY KEY (run_id, ordinal),
                UNIQUE (run_id, registration_id)
            )
            """,
        ),
    ),
    _Migration(
        version=2,
        name="w3_worker_leases",
        statements=(
            """
            CREATE TABLE attempts (
                attempt_id TEXT PRIMARY KEY,
                step_id TEXT NOT NULL REFERENCES steps(step_id) ON DELETE RESTRICT,
                attempt_number INTEGER NOT NULL CHECK (attempt_number >= 1),
                worker_id TEXT NOT NULL,
                fencing_token INTEGER NOT NULL UNIQUE CHECK (fencing_token >= 1),
                status TEXT NOT NULL CHECK (status IN (
                    'running', 'succeeded', 'failed', 'fenced'
                )),
                started_at TEXT NOT NULL,
                finished_at TEXT,
                error_ref TEXT,
                UNIQUE (step_id, attempt_number)
            )
            """,
            """
            CREATE INDEX attempts_step_idx
            ON attempts(step_id, attempt_number)
            """,
            """
            CREATE TABLE leases (
                attempt_id TEXT PRIMARY KEY
                    REFERENCES attempts(attempt_id) ON DELETE RESTRICT,
                worker_id TEXT NOT NULL,
                fencing_token INTEGER NOT NULL UNIQUE,
                acquired_at TEXT NOT NULL,
                heartbeat_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                released_at TEXT
            )
            """,
            """
            CREATE INDEX leases_expiry_idx
            ON leases(released_at, expires_at)
            """,
            """
            CREATE TABLE attempt_artifacts (
                attempt_id TEXT NOT NULL
                    REFERENCES attempts(attempt_id) ON DELETE RESTRICT,
                registration_id INTEGER NOT NULL
                    REFERENCES artifact_registrations(registration_id) ON DELETE RESTRICT,
                ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
                PRIMARY KEY (attempt_id, ordinal),
                UNIQUE (attempt_id, registration_id)
            )
            """,
            """
            CREATE TABLE run_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE RESTRICT,
                step_id TEXT REFERENCES steps(step_id) ON DELETE RESTRICT,
                attempt_id TEXT REFERENCES attempts(attempt_id) ON DELETE RESTRICT,
                event_type TEXT NOT NULL,
                detail_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """,
            """
            CREATE INDEX run_events_run_idx
            ON run_events(run_id, event_id)
            """,
        ),
    ),
    _Migration(
        version=3,
        name="w3_workflow_checkpoints",
        statements=(
            """
            CREATE TABLE step_policies (
                step_id TEXT PRIMARY KEY REFERENCES steps(step_id) ON DELETE RESTRICT,
                side_effect TEXT NOT NULL CHECK (side_effect IN (
                    'none', 'replayable_external_read',
                    'paid_external_unknown', 'idempotent_local_write'
                )),
                recovery_rule TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE attempt_effects (
                attempt_id TEXT PRIMARY KEY
                    REFERENCES attempts(attempt_id) ON DELETE RESTRICT,
                side_effect TEXT NOT NULL CHECK (side_effect IN (
                    'none', 'replayable_external_read',
                    'paid_external_unknown', 'idempotent_local_write'
                )),
                effect_state TEXT NOT NULL CHECK (effect_state IN (
                    'not_started', 'request_started', 'response_committed'
                )),
                intent_artifact_ref TEXT,
                request_artifact_ref TEXT,
                response_artifact_ref TEXT,
                external_response_id TEXT,
                updated_at TEXT NOT NULL
            )
            """,
        ),
    ),
    _Migration(
        version=4,
        name="w3_budget_diagnostics",
        statements=(
            """
            CREATE TABLE budget_limits (
                run_id TEXT PRIMARY KEY REFERENCES runs(run_id) ON DELETE RESTRICT,
                wall_clock_seconds INTEGER NOT NULL CHECK (wall_clock_seconds > 0),
                input_tokens INTEGER NOT NULL CHECK (input_tokens >= 0),
                output_tokens INTEGER NOT NULL CHECK (output_tokens >= 0),
                tool_calls INTEGER NOT NULL CHECK (tool_calls >= 0),
                retrieval_rounds INTEGER NOT NULL CHECK (retrieval_rounds >= 0),
                concurrency INTEGER NOT NULL CHECK (concurrency > 0),
                estimated_cost_limit TEXT NOT NULL,
                cost_enforcement TEXT NOT NULL CHECK (cost_enforcement IN ('available', 'unavailable')),
                state TEXT NOT NULL CHECK (state IN ('active', 'stopped')),
                created_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE budget_reservations (
                reservation_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE RESTRICT,
                step_id TEXT NOT NULL REFERENCES steps(step_id) ON DELETE RESTRICT,
                attempt_id TEXT NOT NULL UNIQUE REFERENCES attempts(attempt_id) ON DELETE RESTRICT,
                input_tokens INTEGER NOT NULL CHECK (input_tokens >= 0),
                output_tokens INTEGER NOT NULL CHECK (output_tokens >= 0),
                tool_calls INTEGER NOT NULL CHECK (tool_calls >= 0),
                retrieval_rounds INTEGER NOT NULL CHECK (retrieval_rounds >= 0),
                status TEXT NOT NULL CHECK (status IN ('active', 'settled', 'released')),
                created_at TEXT NOT NULL,
                closed_at TEXT
            )
            """,
            """
            CREATE INDEX budget_reservations_run_idx
            ON budget_reservations(run_id, status)
            """,
            """
            CREATE TABLE budget_entries (
                entry_id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE RESTRICT,
                step_id TEXT NOT NULL REFERENCES steps(step_id) ON DELETE RESTRICT,
                attempt_id TEXT NOT NULL REFERENCES attempts(attempt_id) ON DELETE RESTRICT,
                reservation_id TEXT NOT NULL REFERENCES budget_reservations(reservation_id) ON DELETE RESTRICT,
                entry_kind TEXT NOT NULL CHECK (entry_kind IN ('reservation', 'actual', 'release', 'unknown_actual')),
                input_tokens INTEGER NOT NULL CHECK (input_tokens >= 0),
                output_tokens INTEGER NOT NULL CHECK (output_tokens >= 0),
                tool_calls INTEGER NOT NULL CHECK (tool_calls >= 0),
                retrieval_rounds INTEGER NOT NULL CHECK (retrieval_rounds >= 0),
                source TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """,
            """
            CREATE INDEX budget_entries_run_idx ON budget_entries(run_id, entry_id)
            """,
            """
            CREATE TABLE errors (
                error_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE RESTRICT,
                step_id TEXT REFERENCES steps(step_id) ON DELETE RESTRICT,
                attempt_id TEXT REFERENCES attempts(attempt_id) ON DELETE RESTRICT,
                code TEXT NOT NULL,
                category TEXT NOT NULL,
                stage TEXT NOT NULL,
                retryable INTEGER NOT NULL CHECK (retryable IN (0, 1)),
                user_message TEXT NOT NULL,
                technical_detail_ref TEXT NOT NULL REFERENCES artifacts(artifact_id) ON DELETE RESTRICT,
                recovery_action TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """,
            """
            CREATE INDEX errors_run_idx ON errors(run_id, created_at, error_id)
            """,
            """
            CREATE TABLE error_artifacts (
                error_id TEXT NOT NULL REFERENCES errors(error_id) ON DELETE RESTRICT,
                ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
                artifact_id TEXT NOT NULL REFERENCES artifacts(artifact_id) ON DELETE RESTRICT,
                PRIMARY KEY (error_id, ordinal),
                UNIQUE (error_id, artifact_id)
            )
            """,
            """
            INSERT INTO budget_limits(
                run_id, wall_clock_seconds, input_tokens, output_tokens,
                tool_calls, retrieval_rounds, concurrency,
                estimated_cost_limit, cost_enforcement, state, created_at
            )
            SELECT run_id,
                   json_extract(budget_json, '$.wall_clock_seconds'),
                   json_extract(budget_json, '$.input_tokens'),
                   json_extract(budget_json, '$.output_tokens'),
                   json_extract(budget_json, '$.tool_calls'),
                   json_extract(budget_json, '$.retrieval_rounds'),
                   json_extract(budget_json, '$.concurrency'),
                   json_extract(budget_json, '$.estimated_cost'),
                   'unavailable', 'active', created_at
            FROM runs
            """,
        ),
    ),
)


class SQLiteRuntimeRepository:
    def __init__(
        self,
        database_path: Path,
        artifact_store: LocalArtifactStore,
        *,
        clock: Callable[[], str] | None = None,
        busy_timeout_seconds: float = 5.0,
    ) -> None:
        self.database_path = database_path
        self.artifact_store = artifact_store
        self.clock = clock or _utc_now
        self.busy_timeout_seconds = busy_timeout_seconds
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def initialize(self) -> None:
        with self._connect() as connection:
            with _transaction(connection):
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS schema_migrations (
                        version INTEGER PRIMARY KEY,
                        name TEXT NOT NULL,
                        checksum TEXT NOT NULL,
                        applied_at TEXT NOT NULL
                    )
                    """
                )
                applied = {
                    int(row["version"]): row
                    for row in connection.execute(
                        "SELECT version, name, checksum, applied_at FROM schema_migrations"
                    )
                }
                known_versions = {migration.version for migration in _MIGRATIONS}
                unknown_versions = set(applied) - known_versions
                if unknown_versions:
                    raise PersistenceInvariantError(
                        "database contains unsupported migration versions: "
                        + ", ".join(str(version) for version in sorted(unknown_versions))
                    )

                for migration in _MIGRATIONS:
                    existing = applied.get(migration.version)
                    if existing is not None:
                        if existing["name"] != migration.name or existing[
                            "checksum"
                        ] != migration.checksum:
                            raise MigrationChecksumError(
                                f"migration {migration.version} checksum mismatch"
                            )
                        continue
                    for statement in migration.statements:
                        connection.execute(statement)
                    connection.execute(
                        """
                        INSERT INTO schema_migrations(version, name, checksum, applied_at)
                        VALUES (?, ?, ?, ?)
                        """,
                        (
                            migration.version,
                            migration.name,
                            migration.checksum,
                            self.clock(),
                        ),
                    )
                connection.execute(f"PRAGMA user_version = {_MIGRATIONS[-1].version}")

    def migration_records(self) -> tuple[MigrationRecord, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT version, name, checksum, applied_at
                FROM schema_migrations
                ORDER BY version
                """
            ).fetchall()
        return tuple(
            MigrationRecord(
                version=int(row["version"]),
                name=str(row["name"]),
                checksum=str(row["checksum"]),
                applied_at=str(row["applied_at"]),
            )
            for row in rows
        )

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
                            "previous_fencing_token": int(
                                previous["fencing_token"]
                            ),
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
            limit=BudgetAmount(**{
                dimension: int(limit[dimension]) for dimension in _BUDGET_DIMENSIONS
            }),
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
                entry_id=int(row["entry_id"]), run_id=str(row["run_id"]),
                step_id=str(row["step_id"]), attempt_id=str(row["attempt_id"]),
                reservation_id=str(row["reservation_id"]),
                entry_kind=str(row["entry_kind"]),
                amount=BudgetAmount(**{
                    dimension: int(row[dimension]) for dimension in _BUDGET_DIMENSIONS
                }),
                source=str(row["source"]), created_at=str(row["created_at"]),
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
                        error_id=str(row["error_id"]), run_id=str(row["run_id"]),
                        step_id=row["step_id"], attempt_id=row["attempt_id"],
                        record=ErrorRecord(
                            code=str(row["code"]), category=ErrorCategory(row["category"]),
                            stage=str(row["stage"]), retryable=bool(row["retryable"]),
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

    def get_step_artifacts(self, step_id: str) -> tuple[ArtifactRef, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    a.artifact_id, a.content_hash, a.storage_uri,
                    ar.media_type, ar.producer_step_id, ar.schema_version
                FROM attempts at
                JOIN attempt_artifacts aa ON aa.attempt_id = at.attempt_id
                JOIN artifact_registrations ar
                  ON ar.registration_id = aa.registration_id
                JOIN artifacts a ON a.artifact_id = ar.artifact_id
                WHERE at.step_id = ? AND at.status = 'succeeded'
                ORDER BY at.attempt_number DESC, aa.ordinal
                """,
                (step_id,),
            ).fetchall()
        return tuple(_artifact_from_row(row) for row in rows)

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
                if run_status is None or run_status["status"] != RunStatus.RUNNING.value:
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
                        (denial_detail.artifact_id, claim.step_id, claim.attempt_number),
                    )
                    connection.execute(
                        "UPDATE steps SET status = 'skipped' WHERE run_id = ? AND status = 'pending'",
                        (claim.run_id,),
                    )
                    self._finish_attempt(
                        connection, claim, status="failed", finished_at=started_at,
                        error_ref=denial_detail.artifact_id,
                    )
                    connection.execute(
                        "UPDATE runs SET status = 'failed', updated_at = ? WHERE run_id = ?",
                        (started_at, claim.run_id),
                    )
                    self._append_run_event(
                        connection, run_id=claim.run_id, step_id=claim.step_id,
                        attempt_id=claim.attempt_id, event_type="budget_reservation_denied",
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
                        reservation_id, claim.run_id, claim.step_id, claim.attempt_id,
                        reservation.input_tokens, reservation.output_tokens,
                        reservation.tool_calls, reservation.retrieval_rounds, started_at,
                    ),
                )
                self._insert_budget_entry(
                    connection, claim, reservation_id, "reservation", reservation,
                    "pre_call_worst_case", started_at,
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
        if request_artifact_ref not in artifact_ids or response_artifact_ref not in artifact_ids:
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
            raise PersistenceInvariantError("budget overage detail and ErrorRecord are paired")
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
                    raise PersistenceInvariantError("external response has no active reservation")
                reservation_id = str(reservation["reservation_id"])
                reserved = BudgetAmount(**{
                    dimension: int(reservation[dimension]) for dimension in _BUDGET_DIMENSIONS
                })
                self._insert_budget_entry(
                    connection, claim, reservation_id, "actual", actual_usage,
                    "provider_or_tool_reported", completed_at,
                )
                released = BudgetAmount(**{
                    dimension: max(0, getattr(reserved, dimension) - getattr(actual_usage, dimension))
                    for dimension in _BUDGET_DIMENSIONS
                })
                self._insert_budget_entry(
                    connection, claim, reservation_id, "release", released,
                    "reservation_reconciliation", completed_at,
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
                        run_id=claim.run_id, step_id=claim.step_id,
                        attempt_id=claim.attempt_id, error=overage_error,
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
                        connection, run_id=claim.run_id, step_id=claim.step_id,
                        attempt_id=claim.attempt_id, event_type="budget_actual_exceeded",
                        detail={"actual": totals}, created_at=completed_at,
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
        self, connection: sqlite3.Connection, claim: LeaseClaim,
        reservation_id: str, entry_kind: str, amount: BudgetAmount,
        source: str, created_at: str,
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
                claim.run_id, claim.step_id, claim.attempt_id, reservation_id,
                entry_kind, amount.input_tokens, amount.output_tokens,
                amount.tool_calls, amount.retrieval_rounds, source, created_at,
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
                row["run_id"], row["step_id"], row["attempt_id"],
                row["reservation_id"], source, now,
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
                row["run_id"], row["step_id"], row["attempt_id"],
                row["reservation_id"], *values, source, now,
            ),
        )
        connection.execute(
            "UPDATE budget_reservations SET status = 'released', closed_at = ? WHERE reservation_id = ?",
            (now, row["reservation_id"]),
        )

    def _persist_error(
        self, connection: sqlite3.Connection, *, error_id: str, run_id: str,
        step_id: str | None, attempt_id: str | None, error: ErrorRecord,
        created_at: str,
    ) -> None:
        if not error.recovery_action.strip():
            raise PersistenceInvariantError("structured Error requires a recovery action")
        if connection.execute(
            "SELECT 1 FROM artifacts WHERE artifact_id = ?", (error.technical_detail_ref,)
        ).fetchone() is None:
            raise PersistenceInvariantError("Error technical detail Artifact is not registered")
        for artifact_id in error.affected_artifact_refs:
            if connection.execute(
                "SELECT 1 FROM artifacts WHERE artifact_id = ?", (artifact_id,)
            ).fetchone() is None:
                raise PersistenceInvariantError("affected Error Artifact is not registered")
        connection.execute(
            """
            INSERT INTO errors(
                error_id, run_id, step_id, attempt_id, code, category, stage,
                retryable, user_message, technical_detail_ref, recovery_action,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                error_id, run_id, step_id, attempt_id, error.code,
                error.category.value, error.stage, int(error.retryable),
                error.user_message, error.technical_detail_ref,
                error.recovery_action, created_at,
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

    def cancel_claim(
        self, claim: LeaseClaim, *, now: str | None = None
    ) -> RunRecord:
        cancelled_at = _normalize_timestamp(now or self.clock())
        with self._connect() as connection:
            with _transaction(connection):
                self._require_active_attempt(connection, claim, cancelled_at)
                self._close_active_reservation(
                    connection, claim, "attempt_cancelled_before_usage_report", cancelled_at
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
                target = RunStatus.CANCELLING if active is not None else RunStatus.CANCELLED
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

    def publish_delivery(
        self,
        run_id: str,
        target: RunStatus,
        delivery: DeliveryRecord,
        artifacts: Sequence[ArtifactRef],
        *,
        claim: LeaseClaim | None = None,
        published_at: str | None = None,
    ) -> RunRecord:
        timestamp = _normalize_timestamp(published_at or self.clock())
        _validate_publication(run_id, target, delivery, artifacts)
        for artifact in artifacts:
            self._validate_artifact(artifact)

        with self._connect() as connection:
            with _transaction(connection):
                row = connection.execute(
                    "SELECT * FROM runs WHERE run_id = ?", (run_id,)
                ).fetchone()
                if row is None:
                    raise RecordNotFound("Run not found")
                current = RunStatus(row["status"])
                require_transition(current, target)
                managed_attempts = int(
                    connection.execute(
                        """
                        SELECT COUNT(*)
                        FROM attempts a
                        JOIN steps s ON s.step_id = a.step_id
                        WHERE s.run_id = ?
                        """,
                        (run_id,),
                    ).fetchone()[0]
                )
                if managed_attempts:
                    if claim is None or claim.run_id != run_id:
                        raise LeaseConflict(
                            "lease-managed Delivery publication requires the current claim"
                        )
                    self._require_active_attempt(connection, claim, timestamp)

                registrations = [
                    self._register_artifact(connection, artifact)
                    for artifact in artifacts
                ]
                try:
                    connection.execute(
                        """
                        INSERT INTO deliveries(
                            run_id, disposition, evidence_refs_json,
                            limitations_json, unmet_criteria_json,
                            recovery_actions_json, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            run_id,
                            delivery.disposition.value,
                            _canonical_json(delivery.evidence_refs),
                            _canonical_json(delivery.limitations),
                            _canonical_json(delivery.unmet_criteria),
                            _canonical_json(delivery.recovery_actions),
                            timestamp,
                        ),
                    )
                    for ordinal, registration_id in enumerate(registrations):
                        connection.execute(
                            """
                            INSERT INTO delivery_artifacts(
                                run_id, ordinal, registration_id
                            ) VALUES (?, ?, ?)
                            """,
                            (run_id, ordinal, registration_id),
                        )
                except sqlite3.IntegrityError as exc:
                    raise PersistenceInvariantError(
                        f"Delivery publication violates persistence constraints: {exc}"
                    ) from exc

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
                        "Run status changed during Delivery publication"
                    )
                if claim is not None:
                    step_changed = connection.execute(
                        """
                        UPDATE steps
                        SET status = 'succeeded', output_refs_json = ?, error_ref = NULL
                        WHERE step_id = ? AND status = 'running' AND attempt = ?
                        """,
                        (
                            _canonical_json(
                                tuple(artifact.artifact_id for artifact in artifacts)
                            ),
                            claim.step_id,
                            claim.attempt_number,
                        ),
                    ).rowcount
                    if step_changed != 1:
                        raise LeaseConflict("claimed publish Step is no longer current")
                    for ordinal, registration_id in enumerate(registrations):
                        connection.execute(
                            """
                            INSERT INTO attempt_artifacts(
                                attempt_id, registration_id, ordinal
                            ) VALUES (?, ?, ?)
                            """,
                            (claim.attempt_id, registration_id, ordinal),
                        )
                    self._finish_attempt(
                        connection,
                        claim,
                        status="succeeded",
                        finished_at=timestamp,
                        error_ref=None,
                    )
                    self._append_run_event(
                        connection,
                        run_id=run_id,
                        step_id=claim.step_id,
                        attempt_id=claim.attempt_id,
                        event_type="delivery_published",
                        detail={
                            "artifact_refs": tuple(
                                artifact.artifact_id for artifact in artifacts
                            ),
                            "run_status": target.value,
                        },
                        created_at=timestamp,
                    )
                updated = connection.execute(
                    "SELECT * FROM runs WHERE run_id = ?", (run_id,)
                ).fetchone()
        return _run_from_row(updated)

    def get_delivery(self, run_id: str) -> DeliveryRecord:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM deliveries WHERE run_id = ?", (run_id,)
            ).fetchone()
            if row is None:
                raise RecordNotFound("Delivery not found")
            artifact_rows = connection.execute(
                """
                SELECT a.artifact_id
                FROM delivery_artifacts da
                JOIN artifact_registrations ar
                  ON ar.registration_id = da.registration_id
                JOIN artifacts a ON a.artifact_id = ar.artifact_id
                WHERE da.run_id = ?
                ORDER BY da.ordinal
                """,
                (run_id,),
            ).fetchall()
        return DeliveryRecord(
            run_id=run_id,
            disposition=DeliveryDisposition(row["disposition"]),
            artifact_refs=tuple(str(item["artifact_id"]) for item in artifact_rows),
            evidence_refs=tuple(json.loads(row["evidence_refs_json"])),
            limitations=tuple(json.loads(row["limitations_json"])),
            unmet_criteria=tuple(json.loads(row["unmet_criteria_json"])),
            recovery_actions=tuple(json.loads(row["recovery_actions_json"])),
        )

    def get_delivery_artifacts(self, run_id: str) -> tuple[ArtifactRef, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    a.artifact_id,
                    a.content_hash,
                    a.storage_uri,
                    ar.media_type,
                    ar.producer_step_id,
                    ar.schema_version
                FROM delivery_artifacts da
                JOIN artifact_registrations ar
                  ON ar.registration_id = da.registration_id
                JOIN artifacts a ON a.artifact_id = ar.artifact_id
                WHERE da.run_id = ?
                ORDER BY da.ordinal
                """,
                (run_id,),
            ).fetchall()
        if not rows:
            raise RecordNotFound("Delivery artifacts not found")
        return tuple(
            ArtifactRef(
                artifact_id=str(row["artifact_id"]),
                media_type=str(row["media_type"]),
                content_hash=str(row["content_hash"]),
                storage_uri=str(row["storage_uri"]),
                producer_step_id=str(row["producer_step_id"]),
                schema_version=str(row["schema_version"]),
            )
            for row in rows
        )

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

    def _register_artifact(
        self, connection: sqlite3.Connection, artifact: ArtifactRef
    ) -> int:
        existing = connection.execute(
            "SELECT content_hash, storage_uri FROM artifacts WHERE artifact_id = ?",
            (artifact.artifact_id,),
        ).fetchone()
        if existing is None:
            connection.execute(
                """
                INSERT INTO artifacts(artifact_id, content_hash, storage_uri)
                VALUES (?, ?, ?)
                """,
                (artifact.artifact_id, artifact.content_hash, artifact.storage_uri),
            )
        elif (
            existing["content_hash"] != artifact.content_hash
            or existing["storage_uri"] != artifact.storage_uri
        ):
            raise ArtifactMetadataConflict(
                f"conflicting metadata for {artifact.artifact_id}"
            )

        connection.execute(
            """
            INSERT OR IGNORE INTO artifact_registrations(
                artifact_id, media_type, producer_step_id, schema_version
            ) VALUES (?, ?, ?, ?)
            """,
            (
                artifact.artifact_id,
                artifact.media_type,
                artifact.producer_step_id,
                artifact.schema_version,
            ),
        )
        registration = connection.execute(
            """
            SELECT registration_id
            FROM artifact_registrations
            WHERE artifact_id = ? AND media_type = ?
              AND producer_step_id = ? AND schema_version = ?
            """,
            (
                artifact.artifact_id,
                artifact.media_type,
                artifact.producer_step_id,
                artifact.schema_version,
            ),
        ).fetchone()
        if registration is None:
            raise PersistenceInvariantError("Artifact registration was not persisted")
        return int(registration["registration_id"])

    def _validate_artifact(self, artifact: ArtifactRef) -> None:
        algorithm, separator, digest = artifact.content_hash.partition(":")
        if separator != ":" or algorithm != "sha256":
            raise PersistenceInvariantError("Artifact content hash must use sha256")
        if artifact.artifact_id != f"artifact-sha256-{digest}":
            raise PersistenceInvariantError(
                "Artifact id does not match its content hash"
            )
        expected_uri = self.artifact_store.path_for_digest(digest).resolve().as_uri()
        if artifact.storage_uri != expected_uri:
            raise PersistenceInvariantError(
                "Artifact storage URI is outside the configured content store"
            )
        self.artifact_store.read_bytes(artifact)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.database_path,
            timeout=self.busy_timeout_seconds,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(
            f"PRAGMA busy_timeout = {int(self.busy_timeout_seconds * 1000)}"
        )
        connection.execute("PRAGMA journal_mode = WAL")
        return connection


def _validate_submission(
    task: TaskSpec, run: RunRecord, steps: Sequence[StepRecord]
) -> None:
    if not task.idempotency_key.strip():
        raise PersistenceInvariantError("Task idempotency key must not be empty")
    if run.task_id != task.task_id:
        raise PersistenceInvariantError("Run task_id does not match Task")
    if run.status is not RunStatus.ACCEPTED:
        raise PersistenceInvariantError("new Run must start in accepted state")
    if not steps:
        raise PersistenceInvariantError("new Run requires at least one Step")
    step_ids = [step.step_id for step in steps]
    if len(step_ids) != len(set(step_ids)):
        raise PersistenceInvariantError("Step ids must be unique")
    for step in steps:
        if step.run_id != run.run_id:
            raise PersistenceInvariantError("Step run_id does not match Run")
        if step.status is not StepStatus.PENDING or step.attempt != 1:
            raise PersistenceInvariantError(
                "new Step must start pending with attempt 1"
            )


def _validate_publication(
    run_id: str,
    target: RunStatus,
    delivery: DeliveryRecord,
    artifacts: Sequence[ArtifactRef],
) -> None:
    expected_target = {
        DeliveryDisposition.COMPLETE: RunStatus.SUCCEEDED,
        DeliveryDisposition.PARTIAL: RunStatus.PARTIAL,
        DeliveryDisposition.NO_ANSWER: RunStatus.SUCCEEDED,
    }[delivery.disposition]
    if target is not expected_target:
        raise PersistenceInvariantError(
            "Run terminal status does not match Delivery disposition"
        )
    if delivery.run_id != run_id:
        raise PersistenceInvariantError("Delivery run_id does not match Run")
    artifact_ids = tuple(artifact.artifact_id for artifact in artifacts)
    if len(artifact_ids) != len(set(artifact_ids)):
        raise PersistenceInvariantError("Delivery Artifact ids must be unique")
    if delivery.artifact_refs != artifact_ids:
        raise PersistenceInvariantError(
            "Delivery Artifact refs must match the published Artifact order"
        )


def _run_from_row(row: sqlite3.Row) -> RunRecord:
    return RunRecord(
        run_id=str(row["run_id"]),
        task_id=str(row["task_id"]),
        status=RunStatus(row["status"]),
        workflow_version=str(row["workflow_version"]),
        config_snapshot_ref=str(row["config_snapshot_ref"]),
        budget=BudgetLedger(**json.loads(row["budget_json"])),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _step_from_row(row: sqlite3.Row) -> StepRecord:
    return StepRecord(
        step_id=str(row["step_id"]),
        run_id=str(row["run_id"]),
        kind=str(row["kind"]),
        attempt=int(row["attempt"]),
        status=StepStatus(row["status"]),
        input_refs=tuple(json.loads(row["input_refs_json"])),
        output_refs=tuple(json.loads(row["output_refs_json"])),
        error_ref=row["error_ref"],
    )


def _attempt_from_row(row: sqlite3.Row) -> AttemptRecord:
    return AttemptRecord(
        attempt_id=str(row["attempt_id"]),
        step_id=str(row["step_id"]),
        worker_id=str(row["worker_id"]),
        attempt_number=int(row["attempt_number"]),
        fencing_token=int(row["fencing_token"]),
        status=str(row["status"]),
        started_at=str(row["started_at"]),
        finished_at=row["finished_at"],
        error_ref=row["error_ref"],
    )


def _attempt_effect_from_row(row: sqlite3.Row) -> AttemptEffectRecord:
    return AttemptEffectRecord(
        attempt_id=str(row["attempt_id"]),
        side_effect=SideEffectClass(row["side_effect"]),
        effect_state=str(row["effect_state"]),
        intent_artifact_ref=row["intent_artifact_ref"],
        request_artifact_ref=row["request_artifact_ref"],
        response_artifact_ref=row["response_artifact_ref"],
        external_response_id=row["external_response_id"],
        updated_at=str(row["updated_at"]),
    )


def _artifact_from_row(row: sqlite3.Row) -> ArtifactRef:
    return ArtifactRef(
        artifact_id=str(row["artifact_id"]),
        media_type=str(row["media_type"]),
        content_hash=str(row["content_hash"]),
        storage_uri=str(row["storage_uri"]),
        producer_step_id=str(row["producer_step_id"]),
        schema_version=str(row["schema_version"]),
    )


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _validate_budget_amount(amount: BudgetAmount) -> None:
    if any(getattr(amount, dimension) < 0 for dimension in _BUDGET_DIMENSIONS):
        raise PersistenceInvariantError("Budget amounts must be non-negative")


def _validate_worker_request(worker_id: str, lease_seconds: int) -> None:
    if not worker_id.strip():
        raise PersistenceInvariantError("worker_id must not be empty")
    if lease_seconds <= 0:
        raise PersistenceInvariantError("lease_seconds must be positive")


def _normalize_timestamp(value: str) -> str:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise PersistenceInvariantError("lease timestamps must use UTC")
    return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _add_seconds(value: str, seconds: int) -> str:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return (parsed + timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z")


@contextmanager
def _transaction(connection: sqlite3.Connection) -> Iterator[None]:
    connection.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        connection.rollback()
        raise
    else:
        connection.commit()


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
