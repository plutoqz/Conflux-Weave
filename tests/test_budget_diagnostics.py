import json
import sqlite3

from conflux_weave.core import BudgetLedger, RunStatus, StepStatus
from conflux_weave.runtime import DurablePaperDiscoveryRuntime, LocalArtifactStore, SQLiteRuntimeRepository
from conflux_weave.paper_discovery import ArxivHttpResponse, ArxivSearchAdapter
from conflux_weave.provider import OpenAICompatibleChatAdapter, ProviderConfig, ProviderHttpResponse


T0 = "2026-08-24T12:00:00Z"
T181 = "2026-08-24T12:03:01Z"
ATOM = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"><entry>
<id>http://arxiv.org/abs/2608.00002v1</id>
<updated>2026-08-02T00:00:00Z</updated><published>2026-08-02T00:00:00Z</published>
<title>Context Management for Language Model Agents</title>
<summary>Methods for managing context in long-running LLM agents.</summary>
<author><name>B. Author</name></author>
<link href="http://arxiv.org/abs/2608.00002v1" rel="alternate" />
<category term="cs.AI" /></entry></feed>"""


class CountingArxivTransport:
    def __init__(self):
        self.calls = 0

    def get(self, url, *, headers, timeout_seconds):
        self.calls += 1
        return ArxivHttpResponse(200, ATOM, {"Content-Type": "application/atom+xml"})


class CountingProviderTransport:
    def __init__(self, *, output_tokens=40, on_call=None):
        self.output_tokens = output_tokens
        self.on_call = on_call
        self.calls = 0

    def post(self, url, *, headers, body, timeout_seconds):
        self.calls += 1
        if self.on_call:
            self.on_call()
        response = {
            "id": f"chatcmpl-budget-{self.calls}", "model": "fixture-model",
            "choices": [{"message": {"role": "assistant", "content": json.dumps({
                "claims": [{"text": "Context Management（2026）研究长时 Agent 的上下文管理。", "evidence_ids": ["arxiv-paper-01"]}]
            }, ensure_ascii=False)}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 120, "completion_tokens": self.output_tokens, "total_tokens": 120 + self.output_tokens},
        }
        return ProviderHttpResponse(200, json.dumps(response, ensure_ascii=False).encode(), {"Content-Type": "application/json"})


def build_runtime(tmp_path, *, provider=None):
    store = LocalArtifactStore(tmp_path / "artifacts")
    repository = SQLiteRuntimeRepository(tmp_path / "db" / "runtime.sqlite3", store, clock=lambda: T0)
    arxiv = CountingArxivTransport()
    provider = provider or CountingProviderTransport()
    runtime = DurablePaperDiscoveryRuntime(
        repository, store,
        ArxivSearchAdapter(store, transport=arxiv, acquired_at=T0),
        OpenAICompatibleChatAdapter(
            store, ProviderConfig("https://provider.example/v1", "secret", "fixture-model"), transport=provider
        ),
        worker_id="budget-worker", clock=lambda: T0,
        id_factory=lambda prefix: f"{prefix}-budget", code_revision="budget-fixture",
    )
    return runtime, repository, store, arxiv, provider


def submit(runtime, budget):
    return runtime.submit(
        "查找 Agent 上下文管理论文", search_query="agent context management",
        max_results=1, budget=budget,
    )


def test_insufficient_search_reservation_starts_zero_external_calls(tmp_path):
    runtime, repository, store, arxiv, provider = build_runtime(tmp_path)
    result = submit(runtime, BudgetLedger(180, 20_000, 2_048, "unavailable", 0, 0, 1))

    work = runtime.work_once(now=T0)

    assert work.status == "failed"
    assert arxiv.calls == provider.calls == 0
    assert repository.get_run(result.run_id).status is RunStatus.FAILED
    error = repository.get_errors(result.run_id)[0].record
    assert error.code == "budget_reservation_denied"
    assert error.recovery_action
    assert store.path_for_digest(
        error.technical_detail_ref.removeprefix("artifact-sha256-")
    ).read_bytes()
    assert len(error.affected_artifact_refs) == 2


def test_v4_migration_backfills_existing_run_budget_snapshot(tmp_path):
    runtime, repository, store, _, _ = build_runtime(tmp_path)
    result = submit(runtime, BudgetLedger(180, 20_000, 2_048, "unavailable", 2, 1, 1))
    with sqlite3.connect(repository.database_path) as connection:
        for table in (
            "agent_messages", "telemetry_drops", "error_artifacts", "errors", "budget_entries",
            "budget_reservations", "budget_limits",
        ):
            connection.execute(f"DROP TABLE {table}")
        connection.execute("DELETE FROM schema_migrations WHERE version >= 4")
        connection.execute("PRAGMA user_version = 3")

    reopened = SQLiteRuntimeRepository(repository.database_path, store)
    status = reopened.get_budget_status(result.run_id)

    assert status.wall_clock_seconds == 180
    assert status.concurrency == 1
    assert status.limit.output_tokens == 2_048
    assert status.estimated_cost_limit == "unavailable"
    assert reopened.migration_records()[-1].version == 6


def test_expired_wall_clock_budget_starts_zero_external_calls(tmp_path):
    runtime, repository, _, arxiv, provider = build_runtime(tmp_path)
    result = submit(
        runtime, BudgetLedger(180, 20_000, 2_048, "unavailable", 2, 1, 1)
    )

    work = runtime.work_once(now=T181)

    assert work.status == "failed"
    assert arxiv.calls == provider.calls == 0
    assert repository.get_budget_status(result.run_id).state == "stopped"
    assert repository.get_errors(result.run_id)[0].record.code == "budget_reservation_denied"


def test_insufficient_provider_output_reservation_starts_zero_provider_calls(tmp_path):
    runtime, repository, _, arxiv, provider = build_runtime(tmp_path)
    result = submit(runtime, BudgetLedger(180, 20_000, 100, "unavailable", 2, 1, 1))
    runtime.work_once(now=T0)
    runtime.work_once(now=T0)

    work = runtime.work_once(now=T0)

    assert work.status == "failed"
    assert arxiv.calls == 1 and provider.calls == 0
    assert repository.get_errors(result.run_id)[0].record.stage == "synthesize_claims"


def test_reservation_actual_and_release_ledger_survives_reopen(tmp_path):
    runtime, repository, store, _, _ = build_runtime(tmp_path)
    result = submit(runtime, BudgetLedger(180, 20_000, 2_048, "unavailable", 2, 1, 1))
    for _ in range(3):
        runtime.work_once(now=T0)

    reopened = SQLiteRuntimeRepository(repository.database_path, store)
    entries = reopened.get_budget_entries(result.run_id)
    status = reopened.get_budget_status(result.run_id)

    assert [entry.entry_kind for entry in entries] == [
        "reservation", "actual", "release", "reservation", "actual", "release"
    ]
    assert status.actual.tool_calls == 2
    assert status.actual.retrieval_rounds == 1
    assert status.actual.input_tokens == 120
    assert status.actual.output_tokens == 40
    assert status.reserved.tool_calls == 0
    assert status.cost_enforcement == "unavailable"


def test_actual_usage_over_limit_stops_run_and_preserves_error_lineage(tmp_path):
    provider = CountingProviderTransport(output_tokens=3_000)
    runtime, repository, store, arxiv, _ = build_runtime(tmp_path, provider=provider)
    result = submit(runtime, BudgetLedger(180, 20_000, 2_048, "unavailable", 2, 1, 1))
    for _ in range(3):
        runtime.work_once(now=T0)

    reopened = SQLiteRuntimeRepository(repository.database_path, store)
    error = reopened.get_errors(result.run_id)[0].record
    steps = {step.kind: step.status for step in reopened.get_steps(result.run_id)}

    assert arxiv.calls == 1 and provider.calls == 1
    assert reopened.get_run(result.run_id).status is RunStatus.FAILED
    assert reopened.get_budget_status(result.run_id).state == "stopped"
    assert reopened.get_budget_status(result.run_id).actual.output_tokens == 3_000
    assert steps["validate_delivery"] is StepStatus.SKIPPED
    assert steps["publish_delivery"] is StepStatus.SKIPPED
    assert error.code == "budget_actual_exceeded"
    assert store.path_for_digest(
        error.technical_detail_ref.removeprefix("artifact-sha256-")
    ).read_bytes()
    assert len(error.affected_artifact_refs) == 4
    assert runtime.work_once(now=T0) is None


def test_in_flight_cancellation_closes_reservation(tmp_path):
    holder = {}
    provider = CountingProviderTransport(on_call=lambda: holder["cancel"]())
    runtime, repository, _, _, _ = build_runtime(tmp_path, provider=provider)
    result = submit(runtime, BudgetLedger(180, 20_000, 2_048, "unavailable", 2, 1, 1))
    holder["cancel"] = lambda: runtime.request_cancel(result.run_id, now=T0)
    runtime.work_once(now=T0)
    runtime.work_once(now=T0)
    runtime.work_once(now=T0)

    assert repository.get_run(result.run_id).status is RunStatus.CANCELLED
    assert repository.get_budget_status(result.run_id).reserved.tool_calls == 0
    assert repository.get_budget_entries(result.run_id)[-1].entry_kind == "release"
