import hashlib
import json
from pathlib import Path

from conflux_weave.offline_smoke import fixture_payload, run_smoke


ROOT = Path(__file__).parents[1]
DATASET = ROOT / "datasets" / "smoke" / "w5-offline-smoke-v1.0.0"


def test_fixture_is_versioned_and_has_offline_boundary() -> None:
    payload = fixture_payload()
    assert payload["schema_version"] == "conflux-weave.offline-smoke.v1"
    assert payload["label"] == "offline_smoke"
    assert len(payload["evidence"]) == len(payload["citations"]) == 2


def test_smoke_dataset_manifest_hashes_are_complete() -> None:
    manifest = json.loads((DATASET / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["case_count"] == 1
    assert manifest["network"] is False
    assert manifest["provider"] is False
    for name in ("README.md", "cases.jsonl", "schema.json"):
        digest = hashlib.sha256((DATASET / name).read_bytes()).hexdigest()
        assert manifest["files"][name] == f"sha256:{digest}"


def test_offline_smoke_closes_run_delivery_citation_and_workbench(tmp_path) -> None:
    result = run_smoke(tmp_path / "smoke-root")

    assert result["label"] == "offline_smoke"
    assert result["network_calls"] == 0
    assert result["provider_calls"] == 0
    assert result["paid_calls"] == 0
    assert result["live_runs_created"] == 0
    assert result["task_request_valid"] is True
    assert result["run_state"] == "partial"
    assert result["delivery_disposition"] == "partial"
    assert result["answer_contains_citation"] is True
    assert result["citation_count"] == result["evidence_count"] == 2
    assert result["artifact_media_type"] == "application/json"
    assert result["workbench_assets"] == [
        "THIRD_PARTY_NOTICES.md",
        "app.js",
        "index.html",
        "modules/",
        "styles.css",
    ]


def test_offline_smoke_output_is_json_serializable(tmp_path, capsys) -> None:
    from conflux_weave.offline_smoke import main

    assert main(["--data-root", str(tmp_path / "smoke-root")]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["label"] == "offline_smoke"
