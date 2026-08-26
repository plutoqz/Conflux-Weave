from __future__ import annotations

import json
from pathlib import Path
import subprocess


DATASET = Path("datasets/regression/s15-live-research-v1")
S16_DATASET = Path("datasets/regression/s16-post-remediation-live-v1")


def test_s15_live_matrix_is_frozen_and_balanced() -> None:
    manifest = json.loads((DATASET / "manifest.json").read_text(encoding="utf-8"))
    cases = [
        json.loads(line)
        for line in (DATASET / "cases.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    assert manifest["case_count"] == len(cases) == 8
    assert manifest["live_execution_requires_explicit_flag"] is True
    assert len(manifest["new_sources"]) == 2
    assert {item["corpus_scope"] for item in cases} == {
        "arxiv_metadata",
        "local",
        "new",
        "mixed",
    }
    assert any(not item["expected_answerable"] for item in cases)
    assert {item["mode"] for item in cases} == {"discovery", "single", "managed"}

    for group in ("local-cross", "mixed-cross"):
        paired = [item for item in cases if item["comparison_group"] == group]
        assert len(paired) == 2
        assert {item["mode"] for item in paired} == {"single", "managed"}
        assert len({item["objective"] for item in paired}) == 1
        assert len({item["corpus_scope"] for item in paired}) == 1


def test_s16_live_protocol_uses_new_namespace_and_frozen_corpora() -> None:
    manifest = json.loads((S16_DATASET / "manifest.json").read_text(encoding="utf-8"))
    cases = [
        json.loads(line)
        for line in (S16_DATASET / "cases.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    assert manifest["case_count"] == len(cases) == 8
    assert manifest["source_matrix"] == "s15-live-research-v1"
    assert all(item["case_id"].startswith("s16c-") for item in cases)
    assert set(manifest["corpora"]) == {"local", "new", "mixed"}
    assert all(len(item["manifest_sha256"]) == 64 for item in manifest["corpora"].values())
    assert manifest["acceptance"]["manager_quality_gain_required"] is False
    assert manifest["acceptance"]["provider_failures_are_not_retried_automatically"] is True

    for group in ("local-cross", "mixed-cross"):
        paired = [item for item in cases if item["comparison_group"] == group]
        assert len(paired) == 2
        assert {item["mode"] for item in paired} == {"single", "managed"}
        assert len({item["objective"] for item in paired}) == 1
        managed = next(item for item in paired if item["mode"] == "managed")
        assert all(
            quote in managed["objective"]
            for quote in managed["required_coverage_quotes"]
        )

    no_answer = next(item for item in cases if not item["expected_answerable"])
    assert no_answer["case_id"] == "s16c-no-answer"


def test_s16_runner_defaults_are_isolated_from_s15() -> None:
    wrapper = Path("scripts/run_s16_live_matrix.py").read_text(encoding="utf-8")
    assert "s16c-matrix.sqlite3" in wrapper
    assert "s16c-matrix-summary.json" in wrapper
    assert '"s16c"' in wrapper
    assert "conflux-weave.s16c-live-matrix-summary.v1" in wrapper

    preflight = Path("scripts/run_s16_preflight.py").read_text(encoding="utf-8")
    assert "--execute-live is required" in preflight
    assert "max_attempts=1" in preflight
    assert '"arxiv"' in preflight


def test_s16_runner_accepts_a_frozen_discovery_failure() -> None:
    completed = subprocess.run(
        ["uv", "run", "python", "scripts/run_s16_live_matrix.py", "--help"],
        check=True,
        capture_output=True,
        text=True,
    )

    assert "--discovery-failure-artifact" in completed.stdout
    runner = Path("scripts/run_s15_live_matrix.py").read_text(encoding="utf-8")
    assert '"mechanical_acceptance": "failed_execution"' in runner
    assert 'else "failed_execution"' in runner
