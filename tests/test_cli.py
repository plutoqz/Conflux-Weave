import json

from conflux_weave.cli import main


def test_cli_runs_deterministic_workflow_and_prints_evidence_boundary(
    tmp_path, capsys
) -> None:
    exit_code = main(
        [
            "validate-workflow",
            "--query",
            "验证 CLI",
            "--artifact-root",
            str(tmp_path),
        ]
    )
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert output["run_status"] == "succeeded"
    assert output["step_status"] == "succeeded"
    assert output["validation_only"] is True
    assert "no source" in output["evidence_boundary"]
    assert output["error"] is None
    digest = output["artifact_id"].removeprefix("artifact-sha256-")
    assert (tmp_path / digest[:2] / digest).is_file()
