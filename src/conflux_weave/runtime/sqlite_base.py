"""SQLite connection, migration, validation, and row-mapping primitives."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
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
)
from conflux_weave.evidence import ArtifactRef
from conflux_weave.runtime.artifacts import LocalArtifactStore
from conflux_weave.runtime.sqlite_contracts import (
    AttemptEffectRecord,
    AttemptRecord,
    BudgetAmount,
    MigrationChecksumError,
    MigrationRecord,
    PersistenceInvariantError,
    SideEffectClass,
    _BUDGET_DIMENSIONS,
)
from conflux_weave.runtime.sqlite_migrations import _MIGRATIONS


class _SQLiteRepositoryBase:
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
                        + ", ".join(
                            str(version) for version in sorted(unknown_versions)
                        )
                    )

                for migration in _MIGRATIONS:
                    existing = applied.get(migration.version)
                    if existing is not None:
                        if (
                            existing["name"] != migration.name
                            or existing["checksum"] != migration.checksum
                        ):
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
