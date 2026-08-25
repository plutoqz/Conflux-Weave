"""Deterministic, no-network W5 installation and Workbench smoke path."""

from __future__ import annotations

import argparse
import importlib.resources
import json
import tempfile
from pathlib import Path
from typing import Any

from conflux_weave.api_contracts import ResearchTaskRequest, WorkbenchQueryService
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
from conflux_weave.runtime import LocalArtifactStore, SQLiteRuntimeRepository
from conflux_weave.runtime.durable_paper_shared import RANK_CHECKPOINT


SMOKE_SCHEMA = "conflux-weave.offline-smoke.v1"
SMOKE_LABEL = "offline_smoke"
NOW = "2026-08-25T12:00:00Z"


class _SmokeRuntime:
    def work_once(self, *, now: str | None = None) -> None:
        return None

    def submit(self, *args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("offline smoke does not submit a second task")

    def request_cancel(self, *args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("offline smoke does not mutate its completed Run")

    def resume(self, *args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("offline smoke does not resume its completed Run")


def fixture_payload() -> dict[str, Any]:
    resource = importlib.resources.files("conflux_weave").joinpath(
        "offline_smoke_fixture.json"
    )
    payload = json.loads(resource.read_text(encoding="utf-8"))
    if payload.get("schema_version") != SMOKE_SCHEMA or payload.get("label") != SMOKE_LABEL:
        raise RuntimeError("offline smoke fixture schema or label is invalid")
    return payload


def run_smoke(data_root: Path) -> dict[str, Any]:
    payload = fixture_payload()
    data_root.mkdir(parents=True, exist_ok=True)
    store = LocalArtifactStore(data_root / "artifacts" / "sha256")
    repository = SQLiteRuntimeRepository(
        data_root / "db" / "conflux-weave.sqlite3", store, clock=lambda: NOW
    )
    task_data = payload["task"]
    task = TaskSpec(
        task_id="task-offline-smoke",
        kind="paper_discovery",
        input={
            "query": task_data["query"],
            "topics": task_data["topics"],
            "max_results": task_data["max_results"],
        },
        requested_policy="offline-smoke-v1",
        idempotency_key="offline-smoke-v1",
    )
    run = RunRecord(
        run_id="run-offline-smoke",
        task_id=task.task_id,
        status=RunStatus.ACCEPTED,
        workflow_version="offline-smoke-v1",
        config_snapshot_ref="offline-smoke-fixture",
        budget=BudgetLedger(60, 500, 200, "unavailable", 2, 2, 1),
        created_at=NOW,
        updated_at=NOW,
    )
    steps = (
        StepRecord(
            step_id="step-offline-smoke-rank",
            run_id=run.run_id,
            kind="rank_candidates",
            attempt=1,
            status=StepStatus.PENDING,
        ),
        StepRecord(
            step_id="step-offline-smoke-publish",
            run_id=run.run_id,
            kind="publish_delivery",
            attempt=1,
            status=StepStatus.PENDING,
        ),
    )
    repository.submit_task(task, run, steps)
    repository.transition_run(run.run_id, RunStatus.QUEUED, updated_at=NOW)
    rank_claim = repository.claim_next_step("offline-smoke-worker", lease_seconds=60, now=NOW)
    if rank_claim is None:
        raise RuntimeError("offline smoke rank step was not claimable")
    checkpoint = store.put_json(
        {"schema_version": RANK_CHECKPOINT, "evidence": payload["evidence"]},
        producer_step_id=rank_claim.step_id,
        schema_version=RANK_CHECKPOINT,
    )
    repository.complete_attempt(rank_claim, (checkpoint,), now=NOW)
    publish_claim = repository.claim_next_step(
        "offline-smoke-worker", lease_seconds=60, now=NOW
    )
    if publish_claim is None:
        raise RuntimeError("offline smoke publish step was not claimable")
    report = store.put_json(
        {
            "schema_version": "conflux-weave.offline-smoke-delivery.v1",
            "label": SMOKE_LABEL,
            "answer": payload["answer"],
            "citations": payload["citations"],
        },
        producer_step_id=publish_claim.step_id,
        schema_version="conflux-weave.offline-smoke-delivery.v1",
    )
    delivery = DeliveryRecord(
        run_id=run.run_id,
        disposition=DeliveryDisposition.PARTIAL,
        artifact_refs=(report.artifact_id,),
        evidence_refs=tuple(item["evidence_id"] for item in payload["evidence"]),
        limitations=tuple(payload["limitations"]),
        unmet_criteria=tuple(payload["unmet_criteria"]),
        recovery_actions=tuple(payload["recovery_actions"]),
    )
    repository.publish_delivery(
        run.run_id,
        RunStatus.PARTIAL,
        delivery,
        (report,),
        claim=publish_claim,
        published_at=NOW,
    )

    service = WorkbenchQueryService(repository)
    request = ResearchTaskRequest(**task_data)
    detail = service.get_run(run.run_id)
    metadata, content = service.read_delivery_artifact(run.run_id, report.artifact_id)
    evidence = tuple(service.get_evidence(run.run_id, item["evidence_id"]) for item in payload["evidence"])
    workbench_root = importlib.resources.files("conflux_weave").joinpath("workbench")
    assets = tuple(path.name for path in workbench_root.iterdir())
    content_payload = json.loads(content)
    if content_payload["label"] != SMOKE_LABEL:
        raise RuntimeError("offline smoke delivery label mismatch")
    if len(payload["citations"]) != len(evidence):
        raise RuntimeError("offline smoke citation/evidence closure mismatch")
    return {
        "label": SMOKE_LABEL,
        "schema_version": SMOKE_SCHEMA,
        "network_calls": 0,
        "provider_calls": 0,
        "paid_calls": 0,
        "live_runs_created": 0,
        "task_request_valid": request.query == task_data["query"],
        "run_id": run.run_id,
        "run_state": detail.state.value,
        "delivery_disposition": detail.delivery.disposition if detail.delivery else None,
        "answer_contains_citation": "[1]" in content_payload["answer"],
        "citation_count": len(payload["citations"]),
        "evidence_count": len(evidence),
        "artifact_media_type": metadata.media_type,
        "workbench_assets": sorted(assets),
        "data_root": str(data_root),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="conflux-weave offline-smoke")
    parser.add_argument(
        "--data-root",
        type=Path,
        default=None,
        help="isolated output root; defaults to a temporary directory",
    )
    args = parser.parse_args(argv)
    if args.data_root is None:
        with tempfile.TemporaryDirectory(prefix="conflux-weave-offline-smoke-") as root:
            result = run_smoke(Path(root))
    else:
        result = run_smoke(args.data_root)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


__all__ = ["fixture_payload", "main", "run_smoke"]
