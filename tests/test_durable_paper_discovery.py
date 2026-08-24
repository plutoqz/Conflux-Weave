import json

import pytest

from conflux_weave.core import RunStatus, StepStatus
from conflux_weave.paper_discovery import (
    ArxivHttpResponse,
    ArxivSearchAdapter,
)
from conflux_weave.provider import (
    OpenAICompatibleChatAdapter,
    ProviderConfig,
    ProviderHttpResponse,
)
from conflux_weave.runtime import (
    DurablePaperDiscoveryRuntime,
    LocalArtifactStore,
    RecoveryDecision,
    RecoveryDecisionRequired,
    SQLiteRuntimeRepository,
    OpenTelemetryTraceExporter,
)


ATOM_FIXTURE = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/2608.00001v1</id>
    <updated>2026-08-01T00:00:00Z</updated>
    <published>2026-08-01T00:00:00Z</published>
    <title>Unrelated Vision Benchmark</title>
    <summary>A benchmark for image segmentation.</summary>
    <author><name>A. Author</name></author>
    <link href="http://arxiv.org/abs/2608.00001v1" rel="alternate" />
    <category term="cs.CV" />
  </entry>
  <entry>
    <id>http://arxiv.org/abs/2608.00002v1</id>
    <updated>2026-08-02T00:00:00Z</updated>
    <published>2026-08-02T00:00:00Z</published>
    <title>Context Management for Language Model Agents</title>
    <summary>Methods for managing context in long-running LLM agents.</summary>
    <author><name>B. Author</name></author>
    <link href="http://arxiv.org/abs/2608.00002v1" rel="alternate" />
    <category term="cs.AI" />
  </entry>
</feed>
"""
T0 = "2026-08-24T12:00:00Z"
T5 = "2026-08-24T12:00:05Z"
T10 = "2026-08-24T12:00:10Z"
T11 = "2026-08-24T12:00:11Z"


class SimulatedProcessExit(BaseException):
    pass


class ArxivTransport:
    def __init__(self, *, crash_once: bool = False):
        self.crash_once = crash_once
        self.calls = 0

    def get(self, url, *, headers, timeout_seconds):
        self.calls += 1
        if self.crash_once and self.calls == 1:
            raise SimulatedProcessExit("worker exited during arXiv call")
        return ArxivHttpResponse(
            200, ATOM_FIXTURE, {"Content-Type": "application/atom+xml"}
        )


class ProviderTransport:
    def __init__(self, *, crash_once: bool = False, on_call=None):
        self.crash_once = crash_once
        self.on_call = on_call
        self.calls = 0

    def post(self, url, *, headers, body, timeout_seconds):
        self.calls += 1
        if self.on_call is not None:
            self.on_call()
        if self.crash_once and self.calls == 1:
            raise SimulatedProcessExit("worker exited after Provider request")
        response = {
            "id": f"chatcmpl-durable-{self.calls}",
            "model": "fixture-model",
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": json.dumps(
                            {
                                "claims": [
                                    {
                                        "text": "Context Management（2026）研究长时 Agent 的上下文管理。",
                                        "evidence_ids": ["arxiv-paper-01"],
                                    }
                                ]
                            },
                            ensure_ascii=False,
                        ),
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 120,
                "completion_tokens": 40,
                "total_tokens": 160,
            },
        }
        return ProviderHttpResponse(
            200,
            json.dumps(response, ensure_ascii=False).encode(),
            {"Content-Type": "application/json"},
        )


def build_runtime(
    tmp_path,
    *,
    arxiv_transport=None,
    provider_transport=None,
    trace_exporter=None,
):
    store = LocalArtifactStore(tmp_path / "artifacts")
    repository = SQLiteRuntimeRepository(
        tmp_path / "db" / "conflux-weave.sqlite3",
        store,
        clock=lambda: T0,
    )
    arxiv_transport = arxiv_transport or ArxivTransport()
    provider_transport = provider_transport or ProviderTransport()
    search = ArxivSearchAdapter(
        store, transport=arxiv_transport, acquired_at=T0
    )
    provider = OpenAICompatibleChatAdapter(
        store,
        ProviderConfig(
            "https://provider.example/v1", "fixture-secret", "fixture-model"
        ),
        transport=provider_transport,
    )
    runtime = DurablePaperDiscoveryRuntime(
        repository,
        store,
        search,
        provider,
        worker_id="worker-fixture",
        lease_seconds=10,
        clock=lambda: T0,
        id_factory=lambda prefix: f"{prefix}-durable",
        code_revision="fixture-revision",
        trace_exporter=trace_exporter,
    )
    return runtime, repository, store, arxiv_transport, provider_transport


class RecordingTraceExporter:
    def __init__(self):
        self.records = []

    def export(self, record):
        self.records.append(record)


class TimeoutTraceExporter:
    def export(self, record):
        raise TimeoutError("fixture exporter timeout with secret-fixture")


def submit(runtime):
    return runtime.submit(
        "查找 Agent 上下文管理论文",
        search_query="agent context management",
        max_results=2,
    )


def test_five_step_checkpoints_resume_without_repeating_committed_calls(tmp_path):
    runtime, repository, store, arxiv, provider = build_runtime(tmp_path)
    submission = submit(runtime)
    duplicate = submit(runtime)
    assert duplicate.created is False
    assert duplicate.run_id == submission.run_id

    observed = []
    for _ in range(5):
        restarted = DurablePaperDiscoveryRuntime(
            SQLiteRuntimeRepository(repository.database_path, store),
            store,
            runtime.search_adapter,
            runtime.chat_adapter,
            worker_id="worker-restarted",
            lease_seconds=10,
            clock=lambda: T0,
        )
        observed.append(restarted.work_once(now=T0).step_kind)

    assert observed == [
        "search_arxiv",
        "rank_candidates",
        "synthesize_claims",
        "validate_delivery",
        "publish_delivery",
    ]
    assert arxiv.calls == 1
    assert provider.calls == 1
    assert repository.get_run(submission.run_id).status is RunStatus.PARTIAL
    assert all(
        step.status is StepStatus.SUCCEEDED
        for step in repository.get_steps(submission.run_id)
    )
    assert repository.get_delivery(submission.run_id).artifact_refs
    manifest_ref = repository.get_delivery(submission.run_id).artifact_refs[1]
    manifest = json.loads(
        store.path_for_digest(
            manifest_ref.removeprefix("artifact-sha256-")
        ).read_bytes()
    )
    assert manifest["search_response_artifact_ref"]
    assert manifest["provider_request_artifact_ref"]
    assert manifest["provider_response_artifact_ref"]
    assert b"fixture-secret" not in json.dumps(manifest).encode()


def test_idempotency_key_freezes_code_prompt_provider_model_and_parameters(tmp_path):
    runtime, repository, store, _, _ = build_runtime(tmp_path)
    first = submit(runtime)
    changed = DurablePaperDiscoveryRuntime(
        repository,
        store,
        runtime.search_adapter,
        runtime.chat_adapter,
        worker_id="worker-changed-code",
        lease_seconds=10,
        clock=lambda: T0,
        id_factory=lambda prefix: f"{prefix}-changed",
        code_revision="different-revision",
    )

    second = submit(changed)

    assert second.created is True
    assert second.task_id != first.task_id
    assert second.run_id != first.run_id


def test_interrupted_replayable_search_is_reclaimed_once(tmp_path):
    arxiv = ArxivTransport(crash_once=True)
    runtime, repository, store, _, provider = build_runtime(
        tmp_path, arxiv_transport=arxiv
    )
    submission = submit(runtime)

    with pytest.raises(SimulatedProcessExit):
        runtime.work_once(now=T0)

    restarted = DurablePaperDiscoveryRuntime(
        SQLiteRuntimeRepository(repository.database_path, store),
        store,
        runtime.search_adapter,
        runtime.chat_adapter,
        worker_id="worker-after-search-crash",
        lease_seconds=10,
        clock=lambda: T10,
    )
    assert restarted.work_once(now=T10).step_kind == "search_arxiv"
    for _ in range(4):
        restarted.work_once(now=T10)

    assert arxiv.calls == 2
    assert provider.calls == 1
    attempts = repository.get_attempts(
        next(
            step.step_id
            for step in repository.get_steps(submission.run_id)
            if step.kind == "search_arxiv"
        )
    )
    assert [attempt.status for attempt in attempts] == ["fenced", "succeeded"]


def test_unknown_provider_outcome_never_replays_without_explicit_decision(tmp_path):
    provider = ProviderTransport(crash_once=True)
    runtime, repository, _, arxiv, _ = build_runtime(
        tmp_path, provider_transport=provider
    )
    submission = submit(runtime)
    runtime.work_once(now=T0)
    runtime.work_once(now=T0)

    with pytest.raises(SimulatedProcessExit):
        runtime.work_once(now=T0)
    assert provider.calls == 1

    assert runtime.work_once(now=T10) is None
    assert repository.get_run(submission.run_id).status is RunStatus.WAITING_FOR_USER
    assert provider.calls == 1
    with pytest.raises(RecoveryDecisionRequired):
        runtime.resume(submission.run_id, now=T11)
    assert provider.calls == 1

    resumed = runtime.resume(
        submission.run_id,
        RecoveryDecision.RETRY_UNKNOWN_EXTERNAL,
        now=T11,
    )
    assert resumed.status is RunStatus.QUEUED
    assert runtime.work_once(now=T11).step_kind == "synthesize_claims"
    runtime.work_once(now=T11)
    runtime.work_once(now=T11)

    assert provider.calls == 2
    assert arxiv.calls == 1
    assert repository.get_run(submission.run_id).status is RunStatus.PARTIAL


def test_explicit_fail_decision_closes_unknown_provider_run(tmp_path):
    provider = ProviderTransport(crash_once=True)
    runtime, repository, _, _, _ = build_runtime(
        tmp_path, provider_transport=provider
    )
    submission = submit(runtime)
    runtime.work_once(now=T0)
    runtime.work_once(now=T0)
    with pytest.raises(SimulatedProcessExit):
        runtime.work_once(now=T0)
    runtime.work_once(now=T10)

    failed = runtime.resume(
        submission.run_id,
        RecoveryDecision.FAIL_UNKNOWN_EXTERNAL,
        now=T11,
    )

    assert failed.status is RunStatus.FAILED
    assert provider.calls == 1
    assert runtime.work_once(now=T11) is None


def test_cancelled_in_flight_provider_commits_response_but_starts_no_next_step(
    tmp_path,
):
    holder = {}
    provider = ProviderTransport(on_call=lambda: holder["cancel"]())
    runtime, repository, _, arxiv, _ = build_runtime(
        tmp_path, provider_transport=provider
    )
    submission = submit(runtime)
    holder["cancel"] = lambda: runtime.request_cancel(submission.run_id, now=T5)
    runtime.work_once(now=T0)
    runtime.work_once(now=T0)

    result = runtime.work_once(now=T5)

    assert result.status == "cancelled"
    assert repository.get_run(submission.run_id).status is RunStatus.CANCELLED
    steps = {step.kind: step.status for step in repository.get_steps(submission.run_id)}
    assert steps["synthesize_claims"] is StepStatus.SUCCEEDED
    assert steps["validate_delivery"] is StepStatus.CANCELLED
    assert steps["publish_delivery"] is StepStatus.CANCELLED
    assert provider.calls == 1
    assert arxiv.calls == 1
    assert runtime.work_once(now=T10) is None


def test_cancelled_queued_run_starts_no_external_call(tmp_path):
    runtime, repository, _, arxiv, provider = build_runtime(tmp_path)
    submission = submit(runtime)

    cancelled = runtime.request_cancel(submission.run_id, now=T0)

    assert cancelled.status is RunStatus.CANCELLED
    assert runtime.work_once(now=T0) is None
    assert arxiv.calls == provider.calls == 0
    assert all(
        step.status is StepStatus.CANCELLED
        for step in repository.get_steps(submission.run_id)
    )


def test_trace_records_required_links_without_owning_delivery(tmp_path):
    exporter = RecordingTraceExporter()
    runtime, repository, _, arxiv, provider = build_runtime(
        tmp_path, trace_exporter=exporter
    )
    submission = submit(runtime)

    for _ in range(5):
        runtime.work_once(now=T0)

    assert repository.get_run(submission.run_id).status is RunStatus.PARTIAL
    assert arxiv.calls == provider.calls == 1
    assert len(exporter.records) == 5
    required = {
        "task_id", "run_id", "step_id", "attempt_id", "workflow_version",
        "provider_model", "artifact_refs", "openinference.span.kind",
    }
    assert all(required <= set(record.attributes) for record in exporter.records)
    serialized = json.dumps(
        [dict(record.attributes) for record in exporter.records], ensure_ascii=False
    )
    assert "fixture-secret" not in serialized


@pytest.mark.parametrize(
    ("exporter", "reason"),
    [
        (TimeoutTraceExporter(), "TimeoutError"),
        (
            OpenTelemetryTraceExporter(
                module_loader=lambda name: (_ for _ in ()).throw(
                    ModuleNotFoundError(name)
                )
            ),
            "TraceDependencyUnavailable",
        ),
    ],
)
def test_trace_failure_does_not_change_deterministic_delivery(
    tmp_path, exporter, reason
):
    baseline_exporter = RecordingTraceExporter()
    baseline, baseline_repository, _, _, _ = build_runtime(
        tmp_path / "baseline", trace_exporter=baseline_exporter
    )
    baseline_submission = submit(baseline)
    failing, failing_repository, failing_store, arxiv, provider = build_runtime(
        tmp_path / "failing", trace_exporter=exporter
    )
    failing_submission = submit(failing)

    for _ in range(5):
        baseline.work_once(now=T0)
        failing.work_once(now=T0)

    assert baseline_repository.get_delivery(
        baseline_submission.run_id
    ).artifact_refs == failing_repository.get_delivery(
        failing_submission.run_id
    ).artifact_refs
    assert failing_repository.get_run(failing_submission.run_id).status is RunStatus.PARTIAL
    assert arxiv.calls == provider.calls == 1
    reopened = SQLiteRuntimeRepository(
        failing_repository.database_path, failing_store
    )
    drops = reopened.get_telemetry_drops(failing_submission.run_id)
    assert len(drops) == 5
    assert {drop.reason for drop in drops} == {reason}
    assert all("secret-fixture" not in drop.reason for drop in drops)
