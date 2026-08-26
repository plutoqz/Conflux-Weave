import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).parents[1]
INVENTORY = (
    ROOT
    / "docs"
    / "plans"
    / "deprecated"
    / "v0.2"
    / "W0-legacy-export-inventory.json"
)


def load_inventory() -> dict[str, Any]:
    return json.loads(INVENTORY.read_text(encoding="utf-8"))


def test_inventory_is_bound_to_one_read_only_source_revision() -> None:
    inventory = load_inventory()
    source = inventory["source"]

    assert inventory["status"] == "frozen_read_only_inventory"
    assert source["repository_path"] == r"D:\code\Conflux"
    assert len(source["revision"]) == 40
    assert source["dirty_boundary"]["tracked_changes"] == []
    assert source["dirty_boundary"]["untracked_paths"] == [
        "docs/plans/Conflux-Weave设计文档v0.1.md"
    ]


def test_asset_ids_and_scopes_are_unique() -> None:
    assets = load_inventory()["assets"]
    asset_ids = [asset["asset_id"] for asset in assets]
    scopes = [
        (asset["source_path"], asset.get("path_filter")) for asset in assets
    ]

    assert len(asset_ids) == len(set(asset_ids))
    assert len(scopes) == len(set(scopes))


def test_actions_are_closed_and_direct_import_is_forbidden() -> None:
    inventory = load_inventory()
    allowed_actions = set(inventory["allowed_actions"])

    assert allowed_actions == {
        "export_candidate",
        "defer_export_w2",
        "defer_export_w6",
        "archive_only",
        "test_fixture_only",
        "selective_export_after_review",
        "local_archive_only",
        "reject_and_rebuild",
        "reject",
    }
    for asset in inventory["assets"]:
        assert asset["source_tracking"] in {
            "tracked",
            "untracked_or_local",
            "mixed",
        }
        assert asset["recommended_action"] in allowed_actions
        assert asset["direct_import_allowed"] is False
        assert asset["evidence_boundary"]
        assert asset["reason"]


def test_export_candidates_have_hash_and_revision_lineage() -> None:
    inventory = load_inventory()
    source_revision = inventory["source"]["revision"]
    assert len(source_revision) == 40

    candidates = [
        asset
        for asset in inventory["assets"]
        if asset["recommended_action"]
        in {
            "export_candidate",
            "defer_export_w2",
            "defer_export_w6",
            "selective_export_after_review",
        }
    ]
    assert candidates
    for asset in candidates:
        assert asset["source_tracking"] == "tracked"
        assert asset["source_path"]
        assert asset["hash"]["method"].startswith("sha256-")
        assert len(asset["hash"]["value"]) == 64


def test_runtime_databases_and_indexes_cannot_be_export_candidates() -> None:
    forbidden_actions = {
        "export_candidate",
        "defer_export_w2",
        "defer_export_w6",
        "selective_export_after_review",
    }
    for asset in load_inventory()["assets"]:
        is_runtime_db = asset["asset_id"].startswith("legacy.runtime_db.")
        is_index = asset["asset_id"] == "legacy.chroma.index"
        is_transient_db = asset["asset_id"] == "legacy.transient_test_databases"
        if is_runtime_db or is_index or is_transient_db:
            assert asset["recommended_action"] not in forbidden_actions


def test_constructed_panel_data_is_fixture_only() -> None:
    assets = {
        asset["asset_id"]: asset for asset in load_inventory()["assets"]
    }
    panel = assets["legacy.p4_panel_ab"]

    assert panel["authority"] == "constructed_fixture"
    assert panel["recommended_action"] == "test_fixture_only"
    assert panel["size"]["claims"] == 100
