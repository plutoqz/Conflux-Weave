import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).parents[1]
SCOPE_PATH = (
    ROOT / "docs" / "plans" / "deprecated" / "v0.2" / "W1-scope-freeze.json"
)


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def normalized_sha256(path: Path) -> str:
    content = path.read_text(encoding="utf-8").replace("\r\n", "\n").replace(
        "\r", "\n"
    )
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def test_w1_scope_is_bound_to_frozen_dataset() -> None:
    scope = load_json(SCOPE_PATH)
    dataset = scope["data_contract"]["frozen_dataset"]
    manifest_path = ROOT / dataset["manifest_path"]

    assert scope["status"] == "scope_frozen"
    assert dataset["version"] == "1.0.0"
    assert normalized_sha256(manifest_path) == dataset["manifest_sha256_lf"]


def test_validation_cases_reference_existing_frozen_cases() -> None:
    scope = load_json(SCOPE_PATH)
    dataset = scope["data_contract"]["frozen_dataset"]
    cases_path = (ROOT / dataset["manifest_path"]).parent / "cases.jsonl"
    frozen_case_ids = {
        json.loads(line)["case_id"]
        for line in cases_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }

    referenced_ids = {
        case_id
        for validation in scope["validation_cases"]
        for case_id in validation["maps_to_case_ids"]
    }
    assert referenced_ids == {
        "CW-PR-002",
        "CW-PR-006",
        "CW-PR-010",
        "CW-PR-011",
        "CW-PR-012",
    }
    assert referenced_ids <= frozen_case_ids


def test_scope_records_acceptance_state_at_freeze_without_becoming_status() -> None:
    scope = load_json(SCOPE_PATH)
    points = scope["acceptance_points"]

    assert [point["id"] for point in points] == [
        "W1.0",
        "W1.1",
        "W1.2",
        "W1.3",
        "W1.4",
        "W1.5",
    ]
    assert points[0]["status_at_freeze"] == "validated_offline"
    assert all(point["status_at_freeze"] == "pending" for point in points[1:])
    assert scope["next_acceptance_point_at_freeze"] == "W1.1"


def test_scope_freeze_does_not_authorize_implementation_or_live_calls() -> None:
    scope = load_json(SCOPE_PATH)
    authorization = scope["authorization"]

    assert scope["milestone"]["implementation_authorized_by_this_freeze"] is False
    assert authorization["implementation"] == "not_authorized_by_this_acceptance_point"
    assert authorization["public_network_search"] == (
        "explicit_live_run_authorization_required"
    )
    assert authorization["model_provider_calls"] == (
        "provider_model_budget_and_live_run_authorization_required"
    )
    assert authorization["paid_services"] == "not_authorized"


def test_w1_golden_path_has_no_optional_framework_dependency() -> None:
    scope = load_json(SCOPE_PATH)
    serialized_scope = json.dumps(scope["in_scope"], ensure_ascii=False)
    serialized_out_of_scope = json.dumps(scope["out_of_scope"], ensure_ascii=False)

    for framework in ("LangGraph", "Phoenix", "Ragas", "DeepEval"):
        assert framework not in serialized_scope
        assert framework in serialized_out_of_scope


def test_live_tasks_cover_both_ingress_paths_and_user_use() -> None:
    scope = load_json(SCOPE_PATH)
    live_tasks = scope["live_tasks"]

    assert len(live_tasks) == 2
    assert {task["validation_id"] for task in live_tasks} == {
        "W1-LOCAL-SUCCESS",
        "W1-REPOSITORY-IDENTITY",
    }
    assert all(task["required_input"] for task in live_tasks)
    assert all(task["use_decision"] for task in live_tasks)


def test_w1_admits_one_source_identity_without_losing_discovery_lineage() -> None:
    approach = load_json(SCOPE_PATH)["selected_approach"]

    assert approach["registered_source_identity_limit_per_run"] == 1
    assert approach["discovery_responses_are_artifacts_not_evidence"] is True
