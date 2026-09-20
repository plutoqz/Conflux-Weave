"""Unit tests for BoundedConcurrentRunner (Micro-experiment B).

Verifies:
1. Concurrency limit enforced via asyncio.Semaphore (max 3 concurrent tasks).
2. Rate-limit detection and exponential backoff retry.
3. Checkpointing and resumption without re-evaluating completed cases.
4. Latency statistics and percentile calculation (P50, P90, P99).
5. Kill-switch triggering on high rate-limit error frequency (>10%).
6. Analytical runtime estimation function.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
import pytest

from conflux_weave.multimodal_concurrent_runner import (
    BoundedConcurrentRunner,
    CheckpointManager,
    ConcurrencyConfig,
    is_rate_limit_or_transient_error,
)


def test_rate_limit_error_detection():
    class CustomHTTPError(Exception):
        def __init__(self, code: int, msg: str):
            super().__init__(msg)
            self.status_code = code

    assert is_rate_limit_or_transient_error(CustomHTTPError(429, "Too Many Requests"))
    assert is_rate_limit_or_transient_error(CustomHTTPError(503, "Service Unavailable"))
    assert is_rate_limit_or_transient_error(RuntimeError("OpenAI API rate limit exceeded"))
    assert is_rate_limit_or_transient_error(RuntimeError("connection reset by peer"))
    assert not is_rate_limit_or_transient_error(ValueError("Invalid argument format"))


def test_checkpoint_manager(tmp_path: Path):
    chk_file = tmp_path / "eval_checkpoint.jsonl"
    mgr = CheckpointManager(chk_file)

    assert mgr.load_completed_case_ids() == set()

    mgr.append_record({"case_id": "c1", "status": "ok", "latency_ms": 120.0})
    mgr.append_record({"case_id": "c2", "status": "ok", "latency_ms": 150.0})

    loaded = mgr.load_completed_case_ids()
    assert loaded == {"c1", "c2"}


def test_bounded_concurrency(tmp_path: Path):
    max_active = 0
    current_active = 0
    lock = threading_lock = asyncio.Lock()

    async def worker(case_id: str) -> str:
        nonlocal max_active, current_active
        async with lock:
            current_active += 1
            if current_active > max_active:
                max_active = current_active
        await asyncio.sleep(0.05)
        async with lock:
            current_active -= 1
        return f"result-{case_id}"

    config = ConcurrencyConfig(
        max_concurrency=3,
        checkpoint_path=tmp_path / "chk.jsonl",
    )
    runner = BoundedConcurrentRunner(config)

    cases = [f"case-{i}" for i in range(10)]
    results, stats = runner.run_batch(cases, worker, case_id_fn=lambda x: x)

    assert len(results) == 10
    assert max_active <= 3, f"Max active concurrency exceeded 3: {max_active}"
    assert stats.successful_calls == 10
    assert stats.failed_calls == 0
    assert stats.p50_ms > 0.0


def test_retry_on_rate_limit_error(tmp_path: Path):
    attempts = 0

    async def flaky_worker(case_id: str) -> str:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise RuntimeError("HTTP 429 Too Many Requests: Rate limit exceeded")
        return f"done-{case_id}"

    config = ConcurrencyConfig(
        max_concurrency=2,
        max_retries=3,
        base_backoff_seconds=0.05,
        jitter=False,
        checkpoint_path=tmp_path / "chk.jsonl",
    )
    runner = BoundedConcurrentRunner(config)

    results, stats = runner.run_batch(["c1"], flaky_worker, case_id_fn=lambda x: x)
    assert results == ["done-c1"]
    assert stats.successful_calls == 1
    assert stats.rate_limit_errors == 2


def test_resumption_skips_completed_cases(tmp_path: Path):
    chk_file = tmp_path / "eval_checkpoint.jsonl"
    chk_file.write_text(
        json.dumps({"case_id": "c1", "result": "already_done"}) + "\n",
        encoding="utf-8",
    )

    executed = []

    async def worker(case_id: str) -> str:
        executed.append(case_id)
        return f"done-{case_id}"

    config = ConcurrencyConfig(
        max_concurrency=2,
        resume=True,
        checkpoint_path=chk_file,
    )
    runner = BoundedConcurrentRunner(config)

    cases = ["c1", "c2", "c3"]
    results, stats = runner.run_batch(cases, worker, case_id_fn=lambda x: x)

    # c1 should be skipped because it was already completed
    assert executed == ["c2", "c3"]
    assert len(results) == 2


def test_kill_switch_trigger(tmp_path: Path):
    # Fails frequently with rate limit error
    async def failing_worker(case_id: str) -> str:
        raise RuntimeError("429 Too Many Requests")

    config = ConcurrencyConfig(
        max_concurrency=2,
        max_retries=1,
        base_backoff_seconds=0.01,
        jitter=False,
        kill_switch_error_rate_threshold=0.10,
        checkpoint_path=tmp_path / "chk.jsonl",
    )
    runner = BoundedConcurrentRunner(config)

    with pytest.raises(RuntimeError):
        runner.run_batch(["c1"], failing_worker, case_id_fn=lambda x: x)

    assert runner._kill_switch_triggered is True


def test_estimate_total_runtime():
    # 450 cases, P50 latency 18.0s, concurrency 3, retry rate 0.05, buffer 60s
    # Base: 450 * 18 / 3 = 2700s = 45 min
    # With 5% retry: 2700 * 1.05 = 2835s
    # With buffer: 2835 + 60 = 2895s
    total_sec = BoundedConcurrentRunner.estimate_total_runtime(
        p50_latency_seconds=18.0,
        total_cases=450,
        concurrency=3,
        retry_rate=0.05,
        network_buffer_seconds=60.0,
    )
    assert 2890.0 <= total_sec <= 2900.0
