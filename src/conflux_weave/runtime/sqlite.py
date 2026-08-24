"""SQLite authority for the bounded W3 runtime."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Callable, Iterator, Sequence

from conflux_weave.core import (
    BudgetLedger,
    DeliveryDisposition,
    DeliveryRecord,
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
    ) -> SubmissionResult:
        _validate_submission(task, run, steps)
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
        published_at: str | None = None,
    ) -> RunRecord:
        timestamp = published_at or self.clock()
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


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


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
