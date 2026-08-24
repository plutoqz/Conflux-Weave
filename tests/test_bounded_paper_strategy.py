import json

import pytest

from conflux_weave.cli import main
from conflux_weave.core import BudgetLedger, RunStatus, StepStatus
from conflux_weave.paper_discovery import ArxivHttpResponse, ArxivSearchAdapter
from conflux_weave.planning import BOUNDED_STRATEGY_ID, PLAN_SCHEMA_VERSION
from conflux_weave.provider import (
    OpenAICompatibleChatAdapter,
    ProviderConfig,
    ProviderHttpResponse,
)
from conflux_weave.runtime import (
    BOUNDED_WORKFLOW_VERSION,
    BoundedPaperStrategyRuntime,
    DurablePaperDiscoveryRuntime,
    LocalArtifactStore,
    SQLiteRuntimeRepository,
)


ATOM_FIXTURE = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/2608.00002v1</id>
    <updated>2026-08-02T00:00:00Z</updated>
    <published>2026-08-02T00:00:00Z</published>
    <title>Context Management for Language Model Agents</title>
    <summary>Methods for managing context and memory in long-running LLM agents.</summary>
    <author><name>B. Author</name></author>
    <link href="http://arxiv.org/abs/2608.00002v1" rel="alternate" />
    <category term="cs.AI" />
  </entry>
</feed>
"""
T0 = "2026-08-24T12:00:00Z"
T31 = "2026-08-24T12:00:31Z"


class ArxivTransport:
    def __init__(self):
        self.calls = 0

    def get(self, url, *, headers, timeout_seconds):
        self.calls += 1
        return ArxivHttpResponse(
            200, ATOM_FIXTURE, {"Content-Type": "application/atom+xml"}
        )


class PlannerProviderTransport:
    def __init__(self, *, search_count=1, invalid=False, crash_planner=False):
        self.search_count = search_count
        self.invalid = invalid
        self.crash_planner = crash_planner
        self.calls = 0

    def post(self, url, *, headers, body, timeout_seconds):
        self.calls += 1
        request = json.loads(body)
        if request["max_tokens"] == 768:
            if self.crash_planner:
                raise SimulatedProcessExit("worker exited after Planner request")
            content = self._plan(request)
        else:
            content = json.dumps(
                {
                    "claims": [
                        {
                            "text": "该论文研究长时 Agent 的上下文与记忆管理。",
                            "evidence_ids": ["arxiv-paper-01"],
                        }
                    ]
                },
                ensure_ascii=False,
            )
        response = {
            "id": f"chatcmpl-bounded-{self.calls}",
            "model": "fixture-model",
            "choices": [
                {
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "total_tokens": 150,
            },
        }
        return ProviderHttpResponse(
            200,
            json.dumps(response, ensure_ascii=False).encode(),
            {"Content-Type": "application/json"},
        )

    def _plan(self, request):
        user_prompt = request["messages"][1]["content"]
        context = json.loads(
            user_prompt.split("ContextBundle:\n", 1)[1].split("\nRequired root keys:", 1)[0]
        )
        constraint_refs = [item["constraint_id"] for item in context["constraints"]]
        actions = []
        for index in range(self.search_count):
            actions.append(
                {
                    "action_id": f"search-{index + 1}",
                    "action_type": "tool_call",
                    "tool_id": "shell" if self.invalid else "search_arxiv",
                    "arguments": {
                        "query": (
                            "all:agent AND all:context"
                            if index == 0
                            else "all:agent AND all:memory"
                        ),
                        "max_results": 15,
                    },
                    "expected_evidence": ["arXiv metadata and abstracts"],
                    "constraint_refs": constraint_refs,
                }
            )
        actions.append(
            {
                "action_id": "finish-1",
                "action_type": "finish",
                "tool_id": None,
                "arguments": {},
                "expected_evidence": [],
                "constraint_refs": [],
            }
        )
        return json.dumps(
            {
                "schema_version": PLAN_SCHEMA_VERSION,
                "strategy_id": BOUNDED_STRATEGY_ID,
                "strategy_version": "bounded-arxiv-prompt-v1",
                "context_sha256": context["content_sha256"],
                "objective": "Find relevant Agent context-management papers.",
                "actions": actions,
                "stop_reason": "Stop after the validated bounded searches.",
            }
        )


class SimulatedProcessExit(BaseException):
    pass


def make_runtime(tmp_path, *, search_count=1, invalid=False, crash_planner=False):
    store = LocalArtifactStore(tmp_path / "artifacts")
    repository = SQLiteRuntimeRepository(
        tmp_path / "runtime.sqlite3", store, clock=lambda: T0
    )
    search_transport = ArxivTransport()
    provider_transport = PlannerProviderTransport(
        search_count=search_count,
        invalid=invalid,
        crash_planner=crash_planner,
    )
    config = ProviderConfig(
        "https://provider.example/v1", "secret", "fixture-model", "fixture"
    )
    runtime = BoundedPaperStrategyRuntime(
        repository,
        store,
        ArxivSearchAdapter(store, transport=search_transport),
        OpenAICompatibleChatAdapter(store, config, transport=provider_transport),
        clock=lambda: T0,
        id_factory=lambda prefix: f"{prefix}-bounded",
        code_revision="fixture-revision",
    )
    return runtime, repository, search_transport, provider_transport


def submit(runtime, *, budget=None):
    return runtime.submit(
        "Find papers about context management for long-running Agents",
        inclusion_constraints=("Agent context management",),
        exclusion_constraints=("vision-only work",),
        hard_constraints=("arXiv metadata and abstracts only",),
        budget=budget,
    )


def work_to_terminal(runtime, repository, run_id):
    results = []
    while not repository.get_run(run_id).status.is_terminal:
        result = runtime.work_once(now=T0)
        assert result is not None
        results.append(result)
    return results


@pytest.mark.parametrize(
    ("search_count", "expected_search_calls", "second_status", "tool_calls", "retrieval_rounds"),
    [
        (1, 1, StepStatus.SKIPPED, 3, 1),
        (2, 2, StepStatus.SUCCEEDED, 4, 2),
    ],
)
def test_static_nine_step_strategy_executes_one_or_two_searches(
    tmp_path,
    search_count,
    expected_search_calls,
    second_status,
    tool_calls,
    retrieval_rounds,
):
    runtime, repository, search, provider = make_runtime(
        tmp_path, search_count=search_count
    )
    result = submit(runtime)

    work_results = work_to_terminal(runtime, repository, result.run_id)

    run = repository.get_run(result.run_id)
    steps = repository.get_steps(result.run_id)
    budget = repository.get_budget_status(result.run_id)
    assert run.status is RunStatus.PARTIAL
    assert run.workflow_version == BOUNDED_WORKFLOW_VERSION
    assert len(work_results) == 9
    assert [step.kind for step in steps] == [
        "build_context",
        "propose_plan",
        "validate_plan",
        "search_slot_1",
        "search_slot_2_or_skip",
        "merge_and_rank",
        "synthesize_claims",
        "validate_delivery",
        "publish_delivery",
    ]
    assert steps[4].status is second_status
    assert search.calls == expected_search_calls
    assert provider.calls == 2
    assert budget.actual.tool_calls == tool_calls
    assert budget.actual.retrieval_rounds == retrieval_rounds
    assert all(len(repository.get_attempts(step.step_id)) == 1 for step in steps)


def test_invalid_plan_fails_before_any_arxiv_call(tmp_path):
    runtime, repository, search, provider = make_runtime(tmp_path, invalid=True)
    result = submit(runtime)

    work_to_terminal(runtime, repository, result.run_id)

    assert repository.get_run(result.run_id).status is RunStatus.FAILED
    assert provider.calls == 1
    assert search.calls == 0
    assert repository.get_steps(result.run_id)[2].status is StepStatus.FAILED
    assert repository.get_errors(result.run_id)[0].record.code == "plan_contract_rejected"


def test_unknown_planner_outcome_is_not_automatically_replayed(tmp_path):
    runtime, repository, search, provider = make_runtime(
        tmp_path, crash_planner=True
    )
    result = submit(runtime)
    runtime.work_once(now=T0)

    with pytest.raises(SimulatedProcessExit):
        runtime.work_once(now=T0)

    assert runtime.work_once(now=T31) is None
    assert repository.get_run(result.run_id).status is RunStatus.WAITING_FOR_USER
    assert provider.calls == 1
    assert search.calls == 0


def test_budget_denial_starts_no_planner_or_search_call(tmp_path):
    runtime, repository, search, provider = make_runtime(tmp_path)
    budget = BudgetLedger(300, 40_000, 3_072, "not-frozen", 0, 2, 1)
    result = submit(runtime, budget=budget)

    work_to_terminal(runtime, repository, result.run_id)

    assert repository.get_run(result.run_id).status is RunStatus.FAILED
    assert provider.calls == 0
    assert search.calls == 0
    assert repository.get_errors(result.run_id)[0].record.code == "budget_reservation_denied"


@pytest.mark.parametrize(
    "fault_point", ["planner_response_committed", "search_slot_1_response_committed"]
)
def test_committed_external_response_is_reused_after_process_exit(tmp_path, fault_point):
    runtime, repository, search, provider = make_runtime(tmp_path)
    result = submit(runtime)
    raised = False

    def fault(point):
        nonlocal raised
        if point == fault_point and not raised:
            raised = True
            raise SimulatedProcessExit(point)

    runtime.fault_hook = fault
    with pytest.raises(SimulatedProcessExit):
        while True:
            runtime.work_once(now=T0)
    calls_after_commit = (search.calls, provider.calls)
    runtime.fault_hook = None

    work_to_terminal(runtime, repository, result.run_id)

    assert repository.get_run(result.run_id).status is RunStatus.PARTIAL
    if fault_point == "planner_response_committed":
        assert calls_after_commit == (0, 1)
    else:
        assert calls_after_commit == (1, 1)
    assert search.calls == 1
    assert provider.calls == 2


def test_bounded_cli_submit_is_disabled_after_w4_5_rejection(tmp_path, capsys):
    exit_code = main(
        [
            "durable-paper",
            "submit",
            "--strategy",
            "bounded",
            "--query",
            "Find Agent context management papers",
            "--include",
            "Agent context management",
            "--database",
            str(tmp_path / "cli.sqlite3"),
            "--artifact-root",
            str(tmp_path / "cli-artifacts"),
        ]
    )
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 2
    assert output["status"] == "rejected"
    assert output["error_code"] == "strategy_rejected"
    assert output["decision"] == "reject"
    assert output["network_called"] is False
    assert output["provider_called"] is False
    assert not (tmp_path / "cli.sqlite3").exists()


def test_fixed_and_bounded_workers_claim_only_their_workflow(tmp_path):
    bounded, repository, _, _ = make_runtime(tmp_path)
    fixed = DurablePaperDiscoveryRuntime(
        repository,
        bounded.artifact_store,
        bounded.search_adapter,
        bounded.chat_adapter,
        clock=lambda: T0,
        id_factory=lambda prefix: f"{prefix}-fixed",
    )
    fixed_result = fixed.submit(
        "fixed query", search_query="all:fixed", max_results=15
    )
    bounded_result = submit(bounded)

    bounded_work = bounded.work_once(now=T0)
    fixed_work = fixed.work_once(now=T0)

    assert bounded_work.run_id == bounded_result.run_id
    assert bounded_work.step_kind == "build_context"
    assert fixed_work.run_id == fixed_result.run_id
    assert fixed_work.step_kind == "search_arxiv"
