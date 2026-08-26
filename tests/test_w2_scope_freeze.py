import json
from pathlib import Path


ROOT = Path(__file__).parents[1]
SCOPE_PATH = (
    ROOT / "docs" / "plans" / "deprecated" / "v0.2" / "W2-scope-freeze.json"
)


def load_scope() -> dict:
    return json.loads(SCOPE_PATH.read_text(encoding="utf-8"))


def test_w2_scope_preserves_freeze_time_statuses() -> None:
    scope = load_scope()

    assert scope["status"] == "scope_frozen"
    assert scope["milestone"]["current_status"] == "scope_frozen"
    assert scope["milestone"]["implementation_authorized_by_this_freeze"] is False
    assert {
        point["id"]: point["status_at_freeze"]
        for point in scope["acceptance_points"]
    } == {
        "W2.0": "scope_frozen",
        "W2.1": "pending",
        "W2.2": "pending",
        "W2.3": "pending",
        "W2.4": "pending",
        "W2.5": "pending",
    }
    assert scope["next_acceptance_point_at_freeze"] == (
        "W2.1 检索基线和单一策略选择"
    )


def test_w2_scope_does_not_authorize_live_or_paid_execution() -> None:
    authorization = load_scope()["authorization"]

    assert authorization["implementation"] == "not_authorized_by_this_acceptance_point"
    assert authorization["public_network_search"] == (
        "explicit_live_run_authorization_required"
    )
    assert authorization["model_provider_calls"] == (
        "provider_model_budget_and_live_run_authorization_required"
    )
    assert authorization["paid_services"] == "not_authorized"
