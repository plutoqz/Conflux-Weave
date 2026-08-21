import json

import pytest

from conflux_weave.cli import main


@pytest.mark.parametrize(
    ("scenario", "exit_code", "run_status", "disposition", "error_code"),
    [
        ("missing_input", 0, "waiting_for_user", None, None),
        ("no_answer", 0, "succeeded", "no_answer", None),
        ("source_partial", 0, "partial", "partial", None),
        ("source_failure", 1, "failed", None, "source_unavailable"),
        ("budget_failure", 1, "failed", None, "budget_exhausted"),
    ],
)
def test_outcome_cli_exposes_distinct_exit_and_delivery_semantics(
    tmp_path,
    capsys,
    scenario,
    exit_code,
    run_status,
    disposition,
    error_code,
) -> None:
    actual_exit = main(
        [
            "validate-outcome",
            "--query",
            "验证结果语义",
            "--scenario",
            scenario,
            "--artifact-root",
            str(tmp_path / scenario),
        ]
    )
    output = json.loads(capsys.readouterr().out)

    assert actual_exit == exit_code
    assert output["run_status"] == run_status
    assert output["delivery_disposition"] == disposition
    assert (output["error"] or {}).get("code") == error_code
    assert output["validation_only"] is True
    assert output["network_called"] is False
    assert output["provider_called"] is False
