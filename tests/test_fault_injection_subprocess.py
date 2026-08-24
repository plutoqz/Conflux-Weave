import json
import os
from pathlib import Path
import subprocess
import sys
import time

from conflux_weave.core import RunStatus
from conflux_weave.runtime import (
    DurablePaperDiscoveryRuntime,
    LocalArtifactStore,
    RecordNotFound,
    SQLiteRuntimeRepository,
)
from conflux_weave.paper_discovery import ArxivHttpResponse, ArxivSearchAdapter
from conflux_weave.provider import (
    OpenAICompatibleChatAdapter,
    ProviderConfig,
    ProviderHttpResponse,
)


T0 = "2026-08-24T12:00:00Z"
T10 = "2026-08-24T12:00:10Z"
WORKER_SCRIPT = Path(__file__).parent / "fixtures" / "w3_fault_worker.py"
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
        append_call(self.call_log, "provider")
        response = {
            "id": "chatcmpl-subprocess", "model": "fixture-model",
            "choices": [{"message": {"role": "assistant", "content": json.dumps({
                "claims": [{
                    "text": "Context Management（2026）研究长时 Agent 的上下文管理。",
                    "evidence_ids": ["arxiv-paper-01"],
                }]
            }, ensure_ascii=False)}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 120, "completion_tokens": 40, "total_tokens": 160},
        }
        return ProviderHttpResponse(
            200, json.dumps(response, ensure_ascii=False).encode(),
            {"Content-Type": "application/json"},
        )


def build_runtime(root: Path, now: str):
    store = LocalArtifactStore(root / "artifacts")
    repository = SQLiteRuntimeRepository(
        root / "runtime.sqlite3", store, clock=lambda: now
    )
    runtime = DurablePaperDiscoveryRuntime(
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
        worker_id=f"parent-{now}",
        lease_seconds=10,
        clock=lambda: now,
        id_factory=lambda prefix: f"{prefix}-subprocess",
        code_revision="subprocess-fixture",
    )
    return runtime, repository, store


def submit(runtime):
    return runtime.submit(
        "查找 Agent 上下文管理论文",
        search_query="agent context management",
        max_results=1,
    )


def calls(root: Path) -> list[str]:
    path = root / "calls.log"
    return path.read_text(encoding="ascii").splitlines() if path.exists() else []


def kill_child_at(root: Path, fault: str, *, now: str = T0) -> None:
    signal = root / f"{fault}.signal"
    process = subprocess.Popen(
        [
            sys.executable,
            str(WORKER_SCRIPT),
            "--database", str(root / "runtime.sqlite3"),
            "--artifacts", str(root / "artifacts"),
            "--call-log", str(root / "calls.log"),
            "--signal", str(signal),
            "--fault", fault,
            "--now", now,
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


def finish(runtime, count: int, *, now: str) -> None:
    for _ in range(count):
        runtime.work_once(now=now)


def test_real_kill_before_external_call_recovers_and_preserves_citations(tmp_path):
    runtime, _, _ = build_runtime(tmp_path, T0)
    submission = submit(runtime)
    kill_child_at(tmp_path, "before_search_external_call")
    assert calls(tmp_path) == []

    recovered, repository, store = build_runtime(tmp_path, T10)
    finish(recovered, 5, now=T10)

    assert calls(tmp_path) == ["arxiv", "provider"]
    assert repository.get_run(submission.run_id).status is RunStatus.PARTIAL
    search_step, rank_step, _, validation_step, _ = repository.get_steps(
        submission.run_id
    )
    assert [
        attempt.status for attempt in repository.get_attempts(search_step.step_id)
    ] == ["fenced", "succeeded"]
    search = json.loads(store.read_bytes(repository.get_step_artifacts(search_step.step_id)[-1]))
    rank = json.loads(store.read_bytes(repository.get_step_artifacts(rank_step.step_id)[0]))
    validation = json.loads(
        store.read_bytes(repository.get_step_artifacts(validation_step.step_id)[0])
    )
    evidence_sources = {
        item["evidence_id"]: item["source_snapshot_id"] for item in rank["evidence"]
    }
    assert all(
        evidence_sources[item["evidence_id"]] == search["snapshot"]["source_id"]
        for item in validation["citations"]
    )


def test_real_kill_after_search_commit_reuses_response(tmp_path):
    runtime, _, _ = build_runtime(tmp_path, T0)
    submission = submit(runtime)
    kill_child_at(tmp_path, "search_response_committed")
    assert calls(tmp_path) == ["arxiv"]

    recovered, repository, _ = build_runtime(tmp_path, T10)
    finish(recovered, 4, now=T10)

    assert calls(tmp_path) == ["arxiv", "provider"]
    assert repository.get_run(submission.run_id).status is RunStatus.PARTIAL


def test_real_kill_during_provider_call_never_auto_replays(tmp_path):
    runtime, repository, _ = build_runtime(tmp_path, T0)
    submission = submit(runtime)
    finish(runtime, 2, now=T0)
    kill_child_at(tmp_path, "provider_outcome_unknown")
    assert calls(tmp_path) == ["arxiv", "provider"]

    recovered, reopened, _ = build_runtime(tmp_path, T10)
    assert recovered.work_once(now=T10) is None

    assert reopened.get_run(submission.run_id).status is RunStatus.WAITING_FOR_USER
    assert calls(tmp_path) == ["arxiv", "provider"]
    assert repository.get_run(submission.run_id).run_id == submission.run_id


def test_real_kill_after_provider_commit_reuses_response(tmp_path):
    runtime, _, _ = build_runtime(tmp_path, T0)
    submission = submit(runtime)
    finish(runtime, 2, now=T0)
    kill_child_at(tmp_path, "provider_response_committed")
    assert calls(tmp_path) == ["arxiv", "provider"]

    recovered, repository, _ = build_runtime(tmp_path, T10)
    finish(recovered, 2, now=T10)

    assert calls(tmp_path) == ["arxiv", "provider"]
    assert repository.get_run(submission.run_id).status is RunStatus.PARTIAL


def test_real_kill_after_artifact_rename_does_not_publish_early(tmp_path):
    runtime, repository, _ = build_runtime(tmp_path, T0)
    submission = submit(runtime)
    finish(runtime, 4, now=T0)
    artifact_count_before = len(tuple((tmp_path / "artifacts").rglob("*")))
    kill_child_at(tmp_path, "publish_artifacts_written")

    assert repository.get_run(submission.run_id).status is RunStatus.RUNNING
    assert len(tuple((tmp_path / "artifacts").rglob("*"))) > artifact_count_before
    try:
        repository.get_delivery(submission.run_id)
    except RecordNotFound:
        pass
    else:
        raise AssertionError("Delivery became visible before publication transaction")

    recovered, reopened, _ = build_runtime(tmp_path, T10)
    recovered.work_once(now=T10)

    assert reopened.get_run(submission.run_id).status is RunStatus.PARTIAL
    assert reopened.get_delivery(submission.run_id).artifact_refs
    assert calls(tmp_path) == ["arxiv", "provider"]
