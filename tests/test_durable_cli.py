import json
from pathlib import Path
from types import SimpleNamespace

from conflux_weave.cli import build_parser, main
from conflux_weave.cli_commands import durable_paper


def _dotenv(path: Path) -> Path:
    path.write_text(
        "CONFLUX_WEAVE_PROVIDER_BASE_URL=https://provider.example/v1\n"
        "CONFLUX_WEAVE_PROVIDER_API_KEY=fixture-secret\n"
        "CONFLUX_WEAVE_PROVIDER_MODEL=fixture-model\n",
        encoding="utf-8",
    )
    return path


def test_durable_parser_has_isolated_persistence_defaults() -> None:
    args = build_parser().parse_args(
        [
            "durable-paper",
            "submit",
            "--query",
            "paper query",
            "--search-query",
            "all:paper",
        ]
    )

    assert args.durable_command == "submit"
    assert args.database == Path("var/db/conflux-weave.sqlite3")
    assert args.artifact_root == Path("var/artifacts/sha256")
    assert args.max_results == 15


def test_durable_submit_persists_without_external_call(tmp_path, capsys) -> None:
    database = tmp_path / "runtime.sqlite3"
    artifacts = tmp_path / "artifacts"
    exit_code = main(
        [
            "durable-paper",
            "submit",
            "--query",
            "paper query",
            "--search-query",
            "all:paper",
            "--dotenv",
            str(_dotenv(tmp_path / ".env")),
            "--database",
            str(database),
            "--artifact-root",
            str(artifacts),
        ]
    )
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert output["status"] == "queued"
    assert output["created"] is True
    assert output["network_called"] is False
    assert output["provider_called"] is False
    assert database.is_file()

    exit_code = main(
        [
            "durable-paper",
            "status",
            "--run-id",
            output["run_id"],
            "--database",
            str(database),
            "--artifact-root",
            str(artifacts),
        ]
    )
    status = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert status["run"]["status"] == "queued"
    assert [item["kind"] for item in status["steps"]] == [
        "search_arxiv",
        "rank_candidates",
        "synthesize_claims",
        "validate_delivery",
        "publish_delivery",
    ]
    assert status["budget"]["limit"]["tool_calls"] == 2
    assert status["budget_entries"] == []
    assert status["errors"] == []
    assert status["delivery"] is None


def test_durable_status_does_not_require_provider_config(
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
            "durable-paper",
            "status",
            "--run-id",
            "missing-run",
            "--database",
            str(tmp_path / "runtime.sqlite3"),
            "--artifact-root",
            str(tmp_path / "artifacts"),
        ]
    )
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 2
    assert output["status"] == "not_found"


def test_durable_worker_executes_only_one_step(tmp_path, monkeypatch, capsys) -> None:
    calls = []

    class FakeRuntime:
        def __init__(self, *args, **kwargs):
            pass

        def work_once(self):
            calls.append("work_once")
            return SimpleNamespace(
                status="running", run_id="run-fixture", step_kind="search_arxiv"
            )

    monkeypatch.setattr(durable_paper, "DurablePaperDiscoveryRuntime", FakeRuntime)
    exit_code = main(
        [
            "durable-paper",
            "worker",
            "--once",
            "--dotenv",
            str(_dotenv(tmp_path / ".env")),
            "--database",
            str(tmp_path / "runtime.sqlite3"),
            "--artifact-root",
            str(tmp_path / "artifacts"),
        ]
    )
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert calls == ["work_once"]
    assert output["step_kind"] == "search_arxiv"
    assert output["automatic_retry"] is False
    assert output["fallback"] is False
