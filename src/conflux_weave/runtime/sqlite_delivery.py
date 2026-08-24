"""SQLite Artifact registration and atomic Delivery publication."""

from __future__ import annotations

import json
import sqlite3
from typing import Sequence

from conflux_weave.core import (
    DeliveryDisposition,
    DeliveryRecord,
    RunRecord,
    RunStatus,
    require_transition,
)
from conflux_weave.evidence import ArtifactRef
from conflux_weave.runtime.sqlite_contracts import (
    ArtifactMetadataConflict,
    LeaseClaim,
    LeaseConflict,
    PersistenceInvariantError,
    RecordNotFound,
)
from conflux_weave.runtime.sqlite_base import (
    _validate_publication,
    _run_from_row,
    _artifact_from_row,
    _canonical_json,
    _normalize_timestamp,
    _transaction,
)


class DeliveryArtifactRepositoryMixin:
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
