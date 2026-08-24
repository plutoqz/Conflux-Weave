import json
from pathlib import Path


ROOT = Path(__file__).parents[1]
SCOPE_PATH = ROOT / "docs" / "plans" / "current" / "W3-scope-freeze.json"


def load_scope() -> dict:
    return json.loads(SCOPE_PATH.read_text(encoding="utf-8"))


def test_w3_scope_is_bound_to_pushed_w2_baseline_and_one_consumer() -> None:
    scope = load_scope()

    assert scope["status"] == "scope_frozen"
    assert scope["verification_base_revision"] == (
        "e9137f39d3a55254dced02c086b3844f250720fd"
    )
    assert scope["milestone"]["current_status"] == "scope_frozen"
    assert scope["milestone"]["implementation_authorized_by_this_freeze"] is False
    assert scope["stage_claim"]["durable_consumer"] == "discover-papers"
    assert scope["workflow_contract"]["automatic_retry"] is False
    assert scope["workflow_contract"]["fallback"] is False
    assert scope["workflow_contract"]["unknown_provider_outcome"] == (
        "waiting_for_user_or_structured_failure"
    )


def test_w3_scope_preserves_pending_execution_and_authorization_gates() -> None:
    scope = load_scope()
    statuses = {
        point["id"]: point["status_at_freeze"]
        for point in scope["acceptance_points"]
    }

    assert statuses == {
        "W3.0": "scope_frozen",
        "W3.1": "pending",
        "W3.2": "pending",
        "W3.3": "pending",
        "W3.4": "pending",
        "W3.5": "pending",
        "W3.6": "pending",
    }
    authorization = scope["authorization"]
    assert authorization["implementation"] == "not_authorized_by_this_acceptance_point"
    assert authorization["public_network_search"] == (
        "explicit_live_run_authorization_required"
    )
    assert authorization["model_provider_calls"] == (
        "provider_model_budget_and_live_run_authorization_required"
    )
    assert authorization["paid_services"] == "not_authorized"
    assert authorization["deepeval"] == "not_admitted_in_W3"
    assert scope["next_acceptance_point_at_freeze"].startswith("W3.1 ")
