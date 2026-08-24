import importlib.util
from pathlib import Path

import pytest


RUNNER_PATH = Path(__file__).parent / "fixtures" / "w4_abc_runner.py"
SPEC = importlib.util.spec_from_file_location("w4_abc_runner", RUNNER_PATH)
assert SPEC is not None and SPEC.loader is not None
w4_abc_runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(w4_abc_runner)

build_blind_pack = w4_abc_runner.build_blind_pack
load_frozen_inputs = w4_abc_runner.load_frozen_inputs
run_evaluation = w4_abc_runner.run_evaluation


def test_frozen_inputs_and_deterministic_b_are_exact():
    fixture, cases = load_frozen_inputs()

    assert len(fixture["cases"]) == 3
    assert set(cases) >= {"CW-PR-005", "CW-PR-009", "CW-PR-010"}


def test_offline_abc_runs_are_isolated_and_decision_is_mechanical(tmp_path):
    result = run_evaluation(tmp_path / "final")

    assert len(result["runs"]) == 9
    assert len({item["database"] for item in result["runs"]}) == 9
    assert len({item["artifact_root"] for item in result["runs"]}) == 9
    assert all(item["citation_evidence_closure"] == 1.0 for item in result["runs"])
    assert all(not item["hard_vetoes"] for item in result["runs"])
    assert result["comparison"]["c_beats_b_case_count"] == 1
    assert result["comparison"]["decision"] == "reject"
    assert result["comparison"]["candidate_admitted"] is False

    with pytest.raises(FileExistsError, match="refusing to reuse"):
        run_evaluation(tmp_path / "final")

    blind_pack = build_blind_pack(result)
    assert blind_pack["status"] == "awaiting_human_review"
    assert blind_pack["human_review_completed"] is False
    assert all("strategy_id" not in item for item in blind_pack["items"])
    assert all("accepted_arxiv_ids" not in item for item in blind_pack["items"])
    assert all("near_match_arxiv_ids" not in item for item in blind_pack["items"])
    assert all("direct_themes_covered" not in item for item in blind_pack["items"])
