"""Single-worker facade for the bounded W3 SQLite runtime."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from conflux_weave.core import StepRecord
from conflux_weave.evidence import ArtifactRef
from conflux_weave.runtime.sqlite import LeaseClaim, SQLiteRuntimeRepository


@dataclass(frozen=True, slots=True)
class SQLiteStepWorker:
    repository: SQLiteRuntimeRepository
    worker_id: str
    lease_seconds: int = 30
    workflow_version: str | None = None

    def claim_next(self, *, now: str | None = None) -> LeaseClaim | None:
        return self.repository.claim_next_step(
            self.worker_id,
            lease_seconds=self.lease_seconds,
            workflow_version=self.workflow_version,
            now=now,
        )

    def heartbeat(self, claim: LeaseClaim, *, now: str | None = None) -> LeaseClaim:
        self._require_owner(claim)
        return self.repository.heartbeat_attempt(
            claim,
            lease_seconds=self.lease_seconds,
            now=now,
        )

    def complete(
        self,
        claim: LeaseClaim,
        artifacts: Sequence[ArtifactRef] = (),
        *,
        now: str | None = None,
    ) -> StepRecord:
        self._require_owner(claim)
        return self.repository.complete_attempt(claim, artifacts, now=now)

    def fail(
        self,
        claim: LeaseClaim,
        error_ref: str,
        *,
        now: str | None = None,
    ) -> StepRecord:
        self._require_owner(claim)
        return self.repository.fail_attempt(claim, error_ref, now=now)

    def skip(
        self,
        claim: LeaseClaim,
        artifacts: Sequence[ArtifactRef] = (),
        *,
        now: str | None = None,
    ) -> StepRecord:
        self._require_owner(claim)
        return self.repository.skip_attempt(claim, artifacts, now=now)

    def _require_owner(self, claim: LeaseClaim) -> None:
        if claim.worker_id != self.worker_id:
            raise ValueError("LeaseClaim belongs to a different Worker")
