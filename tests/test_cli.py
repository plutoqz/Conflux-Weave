import json

from conflux_weave.cli import _print_json, build_parser, main


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


class GbkOutput:
    encoding = "gbk"

    def __init__(self) -> None:
        self.value = ""

    def write(self, value: str) -> int:
        value.encode(self.encoding)
        self.value += value
        return len(value)

    def flush(self) -> None:
        pass


def test_json_output_escapes_characters_missing_from_console_encoding(monkeypatch) -> None:
    output = GbkOutput()
    monkeypatch.setattr("sys.stdout", output)

    _print_json({"description": "Use ⌥ to switch modes"})

    parsed = json.loads(output.value)
    assert parsed == {"description": "Use ⌥ to switch modes"}
    assert "\\u2325" in output.value


def test_serve_accepts_an_isolated_workspace_root(tmp_path) -> None:
    args = build_parser().parse_args(
        ["serve", "--workspace-root", str(tmp_path / "workspace")]
    )

    assert args.workspace_root == tmp_path / "workspace"
