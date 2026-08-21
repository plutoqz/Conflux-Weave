import json

from conflux_weave import cli
from conflux_weave.cli import build_parser, main
from conflux_weave.provider import ProviderPortError


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


def test_review_cli_exposes_provider_failure_without_traceback(
    tmp_path, monkeypatch, capsys
) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "CONFLUX_WEAVE_PROVIDER_BASE_URL=https://provider.example/v1\n"
        "CONFLUX_WEAVE_PROVIDER_API_KEY=fixture-secret\n"
        "CONFLUX_WEAVE_PROVIDER_MODEL=fixture-model\n",
        encoding="utf-8",
    )
    document = tmp_path / "review.pdf"
    document.write_bytes(b"fixture")

    class FailingWorkflow:
        def __init__(self, *args, **kwargs):
            pass

        def execute(self, path, query):
            raise ProviderPortError(
                code="provider_network_failed",
                message="offline fixture",
                retryable=True,
                request_artifact_ref="artifact-request",
                recovery_action="check network",
            )

    monkeypatch.setattr(cli, "FixedReviewReadingNoteWorkflow", FailingWorkflow)

    exit_code = main(
        [
            "review-document",
            str(document),
            "--query",
            "review",
            "--dotenv",
            str(dotenv),
            "--artifact-root",
            str(tmp_path / "artifacts"),
        ]
    )
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert output["error_code"] == "provider_network_failed"
    assert output["request_artifact_ref"] == "artifact-request"
    assert output["network_called"] is True
    assert output["provider_called"] is True
