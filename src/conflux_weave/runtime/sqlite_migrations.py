"""Checksum-protected SQLite schema migrations."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib


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
    _Migration(
        version=5,
        name="w3_trace_diagnostics",
        statements=(
            """
            CREATE TABLE telemetry_drops (
                drop_id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE RESTRICT,
                step_id TEXT REFERENCES steps(step_id) ON DELETE RESTRICT,
                attempt_id TEXT REFERENCES attempts(attempt_id) ON DELETE RESTRICT,
                span_name TEXT NOT NULL,
                reason TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """,
            """
            CREATE INDEX telemetry_drops_run_idx
            ON telemetry_drops(run_id, drop_id)
            """,
        ),
    ),
)
