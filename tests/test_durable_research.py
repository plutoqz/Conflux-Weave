import json
from types import SimpleNamespace

from conflux_weave.api_contracts import WorkbenchQueryService
from conflux_weave.core import BudgetLedger, DeliveryDisposition, RunStatus, StepStatus
from conflux_weave.evidence import EvidenceRef
from conflux_weave.runtime import (
    BudgetAmount,
    DurableResearchExecution,
    DurableResearchRuntime,
    LocalArtifactStore,
    SQLiteRuntimeRepository,
    VerifiedWorkflowExecutorAdapter,
)


T0 = "2026-08-26T12:00:00Z"
T10 = "2026-08-26T12:00:10Z"


class SimulatedProcessExit(BaseException):
    pass


class FixtureExecutor:
    def __init__(self, store, *, crash=False, usage=None, disposition=DeliveryDisposition.COMPLETE):
        self.store = store
        self.crash = crash
        self.calls = 0
        self.usage = usage or BudgetAmount(120, 40, 4, 1)
        self.disposition = disposition

    def __call__(self, task_kind, objective, max_subquestions):
        self.calls += 1
        if self.crash:
            raise SimulatedProcessExit("worker exited during paid research batch")
        report = self.store.put_bytes(
            f"# Result\n\n{objective}\n".encode(),
            media_type="text/markdown; charset=utf-8",
            producer_step_id="fixture-research",
            schema_version="fixture-report.v1",
        )
        manifest = self.store.put_json(
            {
                "task_kind": task_kind,
                "objective": objective,
                "max_subquestions": max_subquestions,
            },
            producer_step_id="fixture-research",
            schema_version="fixture-manifest.v1",
        )
        no_answer = self.disposition is DeliveryDisposition.NO_ANSWER
        return DurableResearchExecution(
            report.artifact_id,
            manifest.artifact_id,
            () if no_answer else ("evidence-fixture-1",),
            () if no_answer else (
                {
                    "evidence_id": "evidence-fixture-1",
                    "source_snapshot_id": "source-fixture-1",
                    "locator": {"page": 1},
                    "quote": "Fixture evidence.",
                    "extraction_method": "fixture",
                },
            ),
            self.usage,
            self.usage.tool_calls,
            self.disposition,
            ("No fixture evidence supports this objective.",) if no_answer else (),
        )


def build_runtime(tmp_path, *, executor=None):
    store = LocalArtifactStore(tmp_path / "artifacts")
    repository = SQLiteRuntimeRepository(
        tmp_path / "db" / "runtime.sqlite3", store, clock=lambda: T0
    )
    executor = executor or FixtureExecutor(store)
    runtime = DurableResearchRuntime(
        repository,
        store,
        executor,
        worker_id="research-worker-fixture",
        lease_seconds=5,
        clock=lambda: T0,
        id_factory=lambda prefix: f"{prefix}-fixture",
        code_revision="fixture-revision",
    )
    return runtime, repository, store, executor


def test_submission_is_idempotent_and_executes_no_provider_call(tmp_path):
    runtime, repository, _, executor = build_runtime(tmp_path)

    first = runtime.submit("Compare agent context strategies")
    duplicate = runtime.submit("Compare agent context strategies")

    assert first.created is True
    assert duplicate.created is False
    assert duplicate.run_id == first.run_id
    assert executor.calls == 0
    assert repository.get_run(first.run_id).status is RunStatus.QUEUED
    assert [step.kind for step in repository.get_steps(first.run_id)] == [
        "execute_research",
        "publish_delivery",
    ]


def test_managed_submission_reserves_plan_and_coverage_audit_calls(tmp_path):
    runtime, repository, _, _ = build_runtime(tmp_path)

    submission = runtime.submit(
        "Compare methods and evaluation",
        task_kind="managed_verified_research",
        max_subquestions=2,
    )

    budget = repository.get_budget_status(submission.run_id)
    assert budget.limit.tool_calls == 14
    assert budget.limit.retrieval_rounds == 2


def test_cancel_before_worker_execution_makes_zero_executor_calls(tmp_path):
    runtime, repository, _, executor = build_runtime(tmp_path)
    submission = runtime.submit("Cancel this research")

    cancelled = runtime.request_cancel(submission.run_id)

    assert cancelled.status is RunStatus.CANCELLED
    assert runtime.work_once() is None
    assert executor.calls == 0
    assert all(
        step.status is StepStatus.CANCELLED
        for step in repository.get_steps(submission.run_id)
    )


def test_successful_delivery_and_budget_survive_repository_reopen(tmp_path):
    runtime, repository, store, executor = build_runtime(tmp_path)
    submission = runtime.submit("Durably research citation verification")

    assert runtime.work_once().step_kind == "execute_research"
    assert runtime.work_once().step_kind == "publish_delivery"

    assert executor.calls == 1
    assert repository.get_run(submission.run_id).status is RunStatus.SUCCEEDED
    budget = repository.get_budget_status(submission.run_id)
    assert budget.actual == BudgetAmount(120, 40, 4, 1)
    actual_entries = [
        entry
        for entry in repository.get_budget_entries(submission.run_id)
        if entry.entry_kind == "actual"
    ]
    assert len(actual_entries) == 1
    assert actual_entries[0].amount == BudgetAmount(120, 40, 4, 1)

    reopened = SQLiteRuntimeRepository(repository.database_path, store)
    assert reopened.get_run(submission.run_id).status is RunStatus.SUCCEEDED
    delivery = reopened.get_delivery(submission.run_id)
    assert delivery.evidence_refs == ("evidence-fixture-1",)
    report, manifest, evidence = reopened.get_delivery_artifacts(submission.run_id)
    assert store.read_bytes(report).startswith(b"# Result")
    assert json.loads(store.read_bytes(manifest))["objective"] == (
        "Durably research citation verification"
    )
    assert json.loads(store.read_bytes(evidence))["evidence"][0]["evidence_id"] == (
        "evidence-fixture-1"
    )
    projected = WorkbenchQueryService(reopened).get_evidence(
        submission.run_id, "evidence-fixture-1"
    )
    assert projected.source_snapshot_id == "source-fixture-1"
    assert projected.locator == {"page": 1}


def test_no_answer_is_published_as_successful_delivery(tmp_path):
    store = LocalArtifactStore(tmp_path / "artifacts")
    executor = FixtureExecutor(store, disposition=DeliveryDisposition.NO_ANSWER)
    repository = SQLiteRuntimeRepository(
        tmp_path / "db" / "runtime.sqlite3", store, clock=lambda: T0
    )
    runtime = DurableResearchRuntime(
        repository,
        store,
        executor,
        worker_id="research-worker-fixture",
        clock=lambda: T0,
    )
    submission = runtime.submit("An objective outside the configured corpus")

    assert runtime.work_once().step_kind == "execute_research"
    assert runtime.work_once().step_kind == "publish_delivery"

    assert repository.get_run(submission.run_id).status is RunStatus.SUCCEEDED
    delivery = repository.get_delivery(submission.run_id)
    assert delivery.disposition is DeliveryDisposition.NO_ANSWER
    assert delivery.evidence_refs == ()
    assert "No fixture evidence" in delivery.limitations[0]
    assert repository.get_errors(submission.run_id) == ()


def test_unknown_paid_batch_outcome_is_not_automatically_replayed(tmp_path):
    runtime, repository, store, executor = build_runtime(tmp_path)
    executor.crash = True
    submission = runtime.submit("Do not replay an unknown paid batch")

    try:
        runtime.work_once(now=T0)
    except SimulatedProcessExit:
        pass
    else:
        raise AssertionError("simulated process exit was not raised")

    restarted = DurableResearchRuntime(
        SQLiteRuntimeRepository(repository.database_path, store),
        store,
        executor,
        worker_id="research-worker-restarted",
        lease_seconds=5,
        clock=lambda: T10,
    )
    assert restarted.work_once(now=T10) is None
    assert executor.calls == 1
    assert repository.get_run(submission.run_id).status is RunStatus.WAITING_FOR_USER
    assert repository.get_steps(submission.run_id)[0].status is StepStatus.WAITING_FOR_USER


def test_reported_budget_overage_records_error_and_fails_run(tmp_path):
    runtime, repository, _, executor = build_runtime(tmp_path)
    executor.usage = BudgetAmount(120, 40, 4, 1)
    budget = BudgetLedger(900, 100, 30, "unavailable", 3, 1, 1)
    submission = runtime.submit("Exceed the frozen budget", budget=budget)

    result = runtime.work_once()

    assert result.status == "failed"
    assert repository.get_run(submission.run_id).status is RunStatus.FAILED
    errors = repository.get_errors(submission.run_id)
    assert [item.record.code for item in errors] == [
        "research_budget_actual_exceeded"
    ]
    assert repository.get_steps(submission.run_id)[1].status is StepStatus.SKIPPED


def test_verified_workflow_adapter_collects_traceable_provider_usage(tmp_path):
    store = LocalArtifactStore(tmp_path / "artifacts")
    request_refs = tuple(
        store.put_json(
            {"endpoint": endpoint, "request": request},
            producer_step_id="fixture-request",
            schema_version="fixture-request.v1",
        )
        for endpoint, request in (
            ("/chat/completions", {"messages": []}),
            ("/embeddings", {"input": ["query"]}),
            ("/rerank", {"query": "query", "documents": ["text"]}),
        )
    )
    chat_response = store.put_json(
        {
            "id": "chat-fixture",
            "choices": [{"message": {"content": "{}"}}],
            "usage": {"prompt_tokens": 20, "completion_tokens": 5},
        },
        producer_step_id="fixture-chat",
        schema_version="fixture-chat-response.v1",
    )
    embedding_response = store.put_json(
        {
            "data": [{"index": 0, "embedding": [1.0, 0.0]}],
            "usage": {"prompt_tokens": 3},
        },
        producer_step_id="fixture-embedding",
        schema_version="fixture-embedding-response.v1",
    )
    rerank_response = store.put_json(
        {"results": [{"index": 0, "relevance_score": 0.9}]},
        producer_step_id="fixture-rerank",
        schema_version="fixture-rerank-response.v1",
    )
    report = store.put_bytes(
        b"# Verified\n",
        media_type="text/markdown",
        producer_step_id="fixture-deliver",
        schema_version="fixture-report.v1",
    )
    retrieval = store.put_json(
        {
            "embedding_request": request_refs[1].artifact_id,
            "embedding_response": embedding_response.artifact_id,
            "rerank_request": request_refs[2].artifact_id,
            "rerank_response": rerank_response.artifact_id,
        },
        producer_step_id="fixture-retrieval",
        schema_version="fixture-retrieval.v1",
    )
    manifest = store.put_json(
        {
            "report_artifact": report.artifact_id,
            "retrieval_artifact": retrieval.artifact_id,
            "model_artifacts": [
                request_refs[0].artifact_id,
                chat_response.artifact_id,
            ],
        },
        producer_step_id="fixture-deliver",
        schema_version="fixture-manifest.v1",
    )
    result = SimpleNamespace(
        report_artifact_id=report.artifact_id,
        manifest_artifact_id=manifest.artifact_id,
        evidence=(
            EvidenceRef(
                "evidence-1",
                "source-1",
                {"page": 1},
                "Fixture quote.",
                "fixture",
            ),
        ),
    )
    workflow = SimpleNamespace(execute=lambda objective: result)

    execution = VerifiedWorkflowExecutorAdapter(store, workflow)(
        "verified_paper_research", "objective", 4
    )

    assert execution.evidence_refs == ("evidence-1",)
    assert execution.provider_call_count == 3
    assert execution.usage == BudgetAmount(23, 5, 3, 1)

    no_answer_result = SimpleNamespace(
        report_artifact_id=report.artifact_id,
        manifest_artifact_id=manifest.artifact_id,
        evidence=(),
        disposition=DeliveryDisposition.NO_ANSWER,
        limitations=("No supported Claim in the configured corpus.",),
        unmet_criteria=(),
    )
    no_answer_execution = VerifiedWorkflowExecutorAdapter(
        store, SimpleNamespace(execute=lambda objective: no_answer_result)
    )("verified_paper_research", "objective", 4)

    assert no_answer_execution.disposition is DeliveryDisposition.NO_ANSWER
    assert no_answer_execution.evidence_refs == ()
    assert no_answer_execution.limitations == (
        "No supported Claim in the configured corpus.",
    )
