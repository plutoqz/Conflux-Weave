import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from conflux_weave.core import RunStatus
from conflux_weave.paper_discovery import ArxivHttpResponse, ArxivSearchAdapter
from conflux_weave.planning import BOUNDED_STRATEGY_ID, PLAN_SCHEMA_VERSION
from conflux_weave.provider import (
    OpenAICompatibleChatAdapter,
    ProviderConfig,
    ProviderHttpResponse,
)
from conflux_weave.runtime import (
    BoundedPaperStrategyRuntime,
    LocalArtifactStore,
    RecordNotFound,
    SQLiteRuntimeRepository,
)


T0 = "2026-08-24T12:00:00Z"
T11 = "2026-08-24T12:00:11Z"
WORKER_SCRIPT = Path(__file__).parent / "fixtures" / "w4_fault_worker.py"
ATOM = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"><entry>
<id>http://arxiv.org/abs/2608.00002v1</id>
<updated>2026-08-02T00:00:00Z</updated><published>2026-08-02T00:00:00Z</published>
<title>Context Management for Language Model Agents</title>
<summary>Methods for managing context in long-running LLM agents.</summary>
<author><name>B. Author</name></author>
<link href="http://arxiv.org/abs/2608.00002v1" rel="alternate" />
<category term="cs.AI" /></entry></feed>"""


def append_call(path: Path, name: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="ascii") as handle:
        handle.write(name + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def planner_content(request: dict) -> str:
    prompt = request["messages"][1]["content"]
    context = json.loads(
        prompt.split("ContextBundle:\n", 1)[1].split("\nRequired root keys:", 1)[0]
    )
    refs = [item["constraint_id"] for item in context["constraints"]]
    return json.dumps(
        {
            "schema_version": PLAN_SCHEMA_VERSION,
            "strategy_id": BOUNDED_STRATEGY_ID,
            "strategy_version": "bounded-arxiv-prompt-v1",
            "context_sha256": context["content_sha256"],
            "objective": "Find relevant Agent context-management papers.",
            "actions": [
                {
                    "action_id": "search-1",
                    "action_type": "tool_call",
                    "tool_id": "search_arxiv",
                    "arguments": {
                        "query": "all:agent AND all:context",
                        "max_results": 15,
                    },
                    "expected_evidence": ["arXiv metadata and abstracts"],
                    "constraint_refs": refs,
                },
                {
                    "action_id": "finish-1",
                    "action_type": "finish",
                    "tool_id": None,
                    "arguments": {},
                    "expected_evidence": [],
                    "constraint_refs": [],
                },
            ],
            "stop_reason": "Stop after the validated bounded search.",
        }
    )


class ArxivTransport:
    def __init__(self, call_log: Path):
        self.call_log = call_log

    def get(self, url, *, headers, timeout_seconds):
        append_call(self.call_log, "arxiv")
        return ArxivHttpResponse(200, ATOM, {"Content-Type": "application/atom+xml"})


class ProviderTransport:
    def __init__(self, call_log: Path):
        self.call_log = call_log

    def post(self, url, *, headers, body, timeout_seconds):
        request = json.loads(body)
        planner = request["max_tokens"] == 768
        name = "planner" if planner else "synthesis"
        append_call(self.call_log, name)
        content = (
            planner_content(request)
            if planner
            else json.dumps(
                {
                    "claims": [
                        {
                            "text": "该论文研究长时 Agent 的上下文管理。",
                            "evidence_ids": ["arxiv-paper-01"],
                        }
                    ]
                },
                ensure_ascii=False,
            )
        )
        response = {
            "id": f"chatcmpl-{name}-parent",
            "model": "fixture-model",
            "choices": [
                {
                    "message": {"role": "assistant", "content": content},
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


def build_runtime(root: Path, now: str):
    store = LocalArtifactStore(root / "artifacts")
    repository = SQLiteRuntimeRepository(
        root / "runtime.sqlite3", store, clock=lambda: now
    )
    runtime = BoundedPaperStrategyRuntime(
        repository,
        store,
        ArxivSearchAdapter(
            store, transport=ArxivTransport(root / "calls.log"), acquired_at=T0
        ),
        OpenAICompatibleChatAdapter(
            store,
            ProviderConfig(
                "https://provider.example/v1", "fixture-secret", "fixture-model"
            ),
            transport=ProviderTransport(root / "calls.log"),
        ),
        worker_id=f"w4-parent-{now}",
        lease_seconds=10,
        clock=lambda: now,
        id_factory=lambda prefix: f"{prefix}-w4-subprocess",
        code_revision="w4-subprocess-fixture",
    )
    return runtime, repository, store


def submit(runtime):
    return runtime.submit(
        "Find Agent context management papers",
        inclusion_constraints=("Agent context management",),
        exclusion_constraints=("vision-only work",),
        hard_constraints=("arXiv metadata and abstracts only",),
    )


def calls(root: Path) -> list[str]:
    path = root / "calls.log"
    return path.read_text(encoding="ascii").splitlines() if path.exists() else []


def advance(runtime, count: int, *, now: str) -> None:
    for _ in range(count):
        assert runtime.work_once(now=now) is not None


def finish(runtime, repository, run_id: str, *, now: str) -> None:
    for _ in range(12):
        if repository.get_run(run_id).status.is_terminal:
            return
        result = runtime.work_once(now=now)
        if result is None:
            return
    raise AssertionError("bounded Run did not reach a terminal or waiting state")


def kill_child_at(root: Path, fault: str, *, now: str = T0) -> None:
    signal = root / f"{fault}.signal"
    process = subprocess.Popen(
        [
            sys.executable,
            str(WORKER_SCRIPT),
            "--database",
            str(root / "runtime.sqlite3"),
            "--artifacts",
            str(root / "artifacts"),
            "--call-log",
            str(root / "calls.log"),
            "--signal",
            str(signal),
            "--fault",
            fault,
            "--now",
            now,
        ],
        cwd=Path(__file__).parents[1],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    deadline = time.monotonic() + 20
    try:
        while not signal.exists():
            if process.poll() is not None:
                stdout, stderr = process.communicate()
                raise AssertionError(
                    f"child exited before {fault}: stdout={stdout!r} stderr={stderr!r}"
                )
            if time.monotonic() >= deadline:
                raise AssertionError(f"child did not reach fault point {fault}")
            time.sleep(0.05)
        process.kill()
        process.wait(timeout=10)
        assert process.returncode != 0
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)


def assert_closed_delivery(repository, store, run_id: str) -> None:
    steps = repository.get_steps(run_id)
    rank_step = next(item for item in steps if item.kind == "merge_and_rank")
    validation_step = next(item for item in steps if item.kind == "validate_delivery")
    rank = json.loads(store.read_bytes(repository.get_step_artifacts(rank_step.step_id)[0]))
    validation = json.loads(
        store.read_bytes(repository.get_step_artifacts(validation_step.step_id)[0])
    )
    evidence_ids = {item["evidence_id"] for item in rank["evidence"]}
    assert evidence_ids == set(repository.get_delivery(run_id).evidence_refs)
    assert {item["evidence_id"] for item in validation["citations"]} <= evidence_ids
    for artifact in repository.get_delivery_artifacts(run_id):
        store.read_bytes(artifact)


@pytest.mark.parametrize(
    ("fault", "prepare_steps", "calls_at_kill"),
    [
        ("planner_response_committed", 1, ["planner"]),
        ("search_slot_1_response_committed", 3, ["planner", "arxiv"]),
        ("provider_response_committed", 6, ["planner", "arxiv", "synthesis"]),
        ("publish_artifacts_written", 8, ["planner", "arxiv", "synthesis"]),
    ],
)
def test_real_kill_after_commit_reuses_effect_and_closes_delivery(
    tmp_path, fault, prepare_steps, calls_at_kill
):
    runtime, repository, _ = build_runtime(tmp_path, T0)
    submission = submit(runtime)
    advance(runtime, prepare_steps, now=T0)

    kill_child_at(tmp_path, fault)
    assert calls(tmp_path) == calls_at_kill
    if fault == "publish_artifacts_written":
        with pytest.raises(RecordNotFound):
            repository.get_delivery(submission.run_id)

    recovered, reopened, store = build_runtime(tmp_path, T11)
    finish(recovered, reopened, submission.run_id, now=T11)

    assert reopened.get_run(submission.run_id).status is RunStatus.PARTIAL
    assert calls(tmp_path) == ["planner", "arxiv", "synthesis"]
    assert_closed_delivery(reopened, store, submission.run_id)


@pytest.mark.parametrize(
    ("fault", "prepare_steps", "expected_calls"),
    [
        ("planner_outcome_unknown", 1, ["planner"]),
        ("synthesis_outcome_unknown", 6, ["planner", "arxiv", "synthesis"]),
    ],
)
def test_real_kill_during_paid_call_never_auto_replays(
    tmp_path, fault, prepare_steps, expected_calls
):
    runtime, _, _ = build_runtime(tmp_path, T0)
    submission = submit(runtime)
    advance(runtime, prepare_steps, now=T0)

    kill_child_at(tmp_path, fault)
    recovered, repository, _ = build_runtime(tmp_path, T11)

    assert recovered.work_once(now=T11) is None
    assert repository.get_run(submission.run_id).status is RunStatus.WAITING_FOR_USER
    assert calls(tmp_path) == expected_calls


def test_real_kill_during_replayable_search_fences_and_replays_once(tmp_path):
    runtime, _, _ = build_runtime(tmp_path, T0)
    submission = submit(runtime)
    advance(runtime, 3, now=T0)

    kill_child_at(tmp_path, "search_outcome_unknown")
    recovered, repository, store = build_runtime(tmp_path, T11)
    finish(recovered, repository, submission.run_id, now=T11)

    search_step = next(
        item
        for item in repository.get_steps(submission.run_id)
        if item.kind == "search_slot_1"
    )
    assert [
        item.status for item in repository.get_attempts(search_step.step_id)
    ] == ["fenced", "succeeded"]
    assert calls(tmp_path) == ["planner", "arxiv", "arxiv", "synthesis"]
    assert repository.get_run(submission.run_id).status is RunStatus.PARTIAL
    assert_closed_delivery(repository, store, submission.run_id)
