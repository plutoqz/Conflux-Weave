import json
from pathlib import Path


ROOT = Path(__file__).parents[1]
SCOPE_PATH = (
    ROOT / "docs" / "plans" / "deprecated" / "v0.2" / "W5-scope-freeze.json"
)
ACCEPTANCE_PATH = (
    ROOT
    / "docs"
    / "plans"
    / "deprecated"
    / "v0.2"
    / "W5.0-转段与范围冻结.json"
)


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_w5_scope_closes_w4_negatively_without_promoting_bounded_strategy() -> None:
    scope = load_json(SCOPE_PATH)
    transition = scope["w4_transition"]

    assert scope["status"] == "scope_frozen"
    assert scope["verification_base_revision"] == (
        "af69a33d3513a0fadd145d08b810ba4e1b4a5de4"
    )
    assert scope["verification_base_tree"] == (
        "5cf39f1a00635d3503d701650b8df54394f6bba8"
    )
    assert transition["final_disposition"] == "closed_negative_fixed_default"
    assert transition["candidate_decision"] == "reject"
    assert transition["default_strategy"] == "fixed-arxiv-v1"
    assert transition["bounded_new_submission"] == "disabled"
    assert transition["live_runs_created_in_w4_6"] == 0


def test_w5_scope_freezes_one_service_and_one_fixed_golden_path() -> None:
    scope = load_json(SCOPE_PATH)
    architecture = scope["selected_architecture"]

    assert architecture["decision"] == "single_fastapi_with_packaged_static_workbench"
    assert architecture["service_count"] == 1
    assert architecture["worker_count"] == 1
    assert architecture["default_strategy"] == "fixed-arxiv-v1"
    assert architecture["default_port"] == 8000
    assert scope["golden_path"]["task_family"] == "paper_discovery"
    assert scope["golden_path"]["second_durable_vertical_slice_in_w5"] is False
    assert scope["runtime_layout"]["data_root"] == "var"
    assert scope["offline_smoke_contract"]["network"] is False


def test_w5_scope_preserves_acceptance_and_authorization_gates() -> None:
    scope = load_json(SCOPE_PATH)
    statuses = {
        point["id"]: point["status_at_freeze"]
        for point in scope["acceptance_points"]
    }

    assert statuses == {
        "W5.0": "validated_offline",
        "W5.1": "pending",
        "W5.2": "pending",
        "W5.3": "pending",
        "W5.4": "pending",
        "W5.5": "pending",
        "W5.6": "pending_separate_live_authorization",
        "W5.7": "pending_separate_live_authorization",
    }
    assert scope["milestone"]["implementation_authorized_by_this_freeze"] is False
    authorization = scope["authorization"]
    assert authorization["implementation"] == (
        "not_authorized_by_this_acceptance_point"
    )
    assert authorization["dependency_changes"] == (
        "not_authorized_by_this_acceptance_point"
    )
    assert authorization["browser_execution"] == (
        "not_authorized_by_this_acceptance_point"
    )
    assert authorization["paid_services"] == "not_authorized"


def test_w5_0_acceptance_retains_failed_preflight_and_zero_call_boundary() -> None:
    acceptance = load_json(ACCEPTANCE_PATH)
    pytest_runs = [
        item for item in acceptance["commands"] if "pytest" in item["command"]
    ]

    assert acceptance["status"] == "validated_offline"
    assert pytest_runs[0]["result"] == "102 passed, 127 setup errors"
    assert pytest_runs[1]["result"].startswith("229 passed")
    assert acceptance["acceptance"]["decision"] == "pass"
    assert acceptance["execution_evidence"] == {
        "product_files_modified_in_w5_0": 0,
        "dependencies_added": 0,
        "fastapi_installed": False,
        "uvicorn_installed": False,
        "browser_runs": 0,
        "public_network_calls": 0,
        "real_provider_calls": 0,
        "paid_calls": 0,
        "live_runs_created": 0,
        "automatic_retry": False,
        "fallback": False,
    }
