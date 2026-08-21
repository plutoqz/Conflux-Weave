import json

from conflux_weave.cli import build_parser, main


def test_live_repository_parser_defaults_to_ignored_dotenv() -> None:
    args = build_parser().parse_args(
        ["research-repository", "--query", "pi coding agent"]
    )

    assert str(args.dotenv) == ".env"
    assert args.query == "pi coding agent"


def test_live_repository_cli_fails_before_network_without_provider_config(
    tmp_path, monkeypatch, capsys
) -> None:
    for name in (
        "CONFLUX_WEAVE_PROVIDER_BASE_URL",
        "CONFLUX_WEAVE_PROVIDER_API_KEY",
        "CONFLUX_WEAVE_PROVIDER_MODEL",
    ):
        monkeypatch.delenv(name, raising=False)

    exit_code = main(
        [
            "research-repository",
            "--query",
            "pi coding agent",
            "--dotenv",
            str(tmp_path / "missing.env"),
            "--artifact-root",
            str(tmp_path / "artifacts"),
        ]
    )
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 2
    assert output["error_code"] == "provider_configuration_invalid"
    assert output["network_called"] is False
    assert output["provider_called"] is False
