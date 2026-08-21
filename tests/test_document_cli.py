import json

from conflux_weave.cli import main


def test_document_cli_reports_waiting_for_user_for_missing_document(tmp_path, capsys) -> None:
    exit_code = main(
        [
            "import-document",
            str(tmp_path / "missing.md"),
            "--artifact-root",
            str(tmp_path / "artifacts"),
        ]
    )
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert output == {
        "status": "waiting_for_user",
        "reason": "document_missing",
        "message": f"document not found: {tmp_path / 'missing.md'}",
    }


def test_document_cli_emits_report_artifact(tmp_path, capsys) -> None:
    source = tmp_path / "notes.md"
    source.write_text("# 结论\n\n这是可定位的内容。\n", encoding="utf-8")
    artifact_root = tmp_path / "artifacts"

    exit_code = main(
        [
            "import-document",
            str(source),
            "--artifact-root",
            str(artifact_root),
            "--title",
            "测试报告",
        ]
    )
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert output["status"] == "succeeded"
    assert output["segment_count"] == 1
    assert output["evidence_count"] == output["citation_count"] == 1
    report_digest = output["report_artifact_id"].removeprefix("artifact-sha256-")
    report_path = artifact_root / report_digest[:2] / report_digest
    assert report_path.is_file()
    assert "测试报告" in report_path.read_text(encoding="utf-8")
