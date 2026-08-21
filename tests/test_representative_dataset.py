import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

import pytest


DATASET_BASE = Path(__file__).parents[1] / "datasets" / "regression"
DATASET_ROOTS = {
    DATASET_BASE / "personal-research-v1": "awaiting_user_review",
    DATASET_BASE / "personal-research-v1.0.0": "frozen",
}
CASE_ID_PATTERN = re.compile(r"^CW-PR-[0-9]{3}$")
ADMISSION_FIELDS = {
    "Feature",
    "Risk",
    "Question",
    "Metric",
    "Decision",
    "ActionOnFail",
    "StopCondition",
}


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_cases(dataset_root: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in (dataset_root / "cases.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
        if line.strip()
    ]


def normalized_sha256(path: Path) -> str:
    normalized = path.read_text(encoding="utf-8").replace("\r\n", "\n").replace(
        "\r", "\n"
    )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


@pytest.mark.parametrize(("dataset_root", "expected_status"), DATASET_ROOTS.items())
def test_manifest_matches_case_inventory_and_coverage(
    dataset_root: Path, expected_status: str
) -> None:
    manifest = load_json(dataset_root / "manifest.json")
    cases = load_cases(dataset_root)
    case_ids = [case["case_id"] for case in cases]

    assert manifest["status"] == expected_status
    assert manifest["case_count"] == len(cases) == 12
    assert manifest["case_ids"] == case_ids
    assert len(case_ids) == len(set(case_ids))
    assert all(CASE_ID_PATTERN.fullmatch(case_id) for case_id in case_ids)
    assert manifest["coverage"]["origin"] == dict(
        Counter(case["origin"] for case in cases)
    )
    assert manifest["coverage"]["task_family"] == dict(
        Counter(case["task_family"] for case in cases)
    )
    assert manifest["coverage"]["expected_outcome"] == dict(
        Counter(case["expected_outcome"] for case in cases)
    )


@pytest.mark.parametrize(("dataset_root", "expected_status"), DATASET_ROOTS.items())
def test_cases_satisfy_versioned_contract_shape(
    dataset_root: Path, expected_status: str
) -> None:
    schema = load_json(dataset_root / "schema.json")
    required = set(schema["required"])
    allowed = set(schema["properties"])

    for case in load_cases(dataset_root):
        assert set(case) == allowed
        assert required <= set(case)
        assert case["annotation_status"] == expected_status
        assert case["origin"] in {"user_seeded", "derived_boundary"}
        assert case["task_family"] in {
            "paper_discovery",
            "evidence_question_answering",
        }
        assert case["expected_outcome"] in {
            "complete",
            "partial",
            "waiting_for_user",
            "no_answer",
        }
        assert case["input"]["query_zh"]
        assert isinstance(case["input"]["attachments"], list)
        assert case["deliverable"]["must_include"]
        assert case["required_evidence"]
        assert case["acceptable_degradation"]
        assert len(case["completion_criteria"]) >= 2
        assert case["source_policy"]["priority"]


@pytest.mark.parametrize("dataset_root", DATASET_ROOTS)
def test_lineage_covers_each_case_once(dataset_root: Path) -> None:
    manifest = load_json(dataset_root / "manifest.json")
    lineage_ids = [
        case_id
        for source in manifest["source_lineage"]
        for case_id in source["case_ids"]
    ]
    assert lineage_ids == manifest["case_ids"]
    assert len(lineage_ids) == len(set(lineage_ids))


@pytest.mark.parametrize(("dataset_root", "expected_status"), DATASET_ROOTS.items())
def test_manifest_disables_live_execution_and_has_admission_contract(
    dataset_root: Path, expected_status: str
) -> None:
    manifest = load_json(dataset_root / "manifest.json")
    assert set(manifest["evaluation_admission"]) == ADMISSION_FIELDS
    assert not any(manifest["execution_authorization"].values())
    assert manifest["review"]["required"] is True
    if expected_status == "frozen":
        assert manifest["review"] == {
            "required": True,
            "reviewed_by": "user",
            "reviewed_at": "2026-08-21",
            "decision": "approved_with_four_scope_decisions",
        }
    else:
        assert manifest["review"] == {
            "required": True,
            "reviewed_by": None,
            "reviewed_at": None,
            "decision": None,
        }


@pytest.mark.parametrize("dataset_root", DATASET_ROOTS)
def test_manifest_hashes_are_lf_normalized_and_current(dataset_root: Path) -> None:
    manifest = load_json(dataset_root / "manifest.json")
    expected_files = {"README.md", "cases.jsonl", "schema.json"}
    assert set(manifest["file_hashes"]) == expected_files
    for filename, expected_hash in manifest["file_hashes"].items():
        assert normalized_sha256(dataset_root / filename) == expected_hash


def test_frozen_version_records_user_scope_decisions() -> None:
    frozen_root = DATASET_BASE / "personal-research-v1.0.0"
    cases = {case["case_id"]: case for case in load_cases(frozen_root)}
    manifest = load_json(frozen_root / "manifest.json")

    assert manifest["version"] == "1.0.0"
    assert manifest["status"] == "frozen"
    assert cases["CW-PR-001"]["input"]["scope"]["excludes"] == [
        "仅遥感影像理解或分类"
    ]
    assert cases["CW-PR-002"]["allowed_confirmation"] == []
    assert "系统通过网络检索定位" in cases["CW-PR-002"]["required_evidence"][0]
    assert cases["CW-PR-005"]["input"]["preprints"] == "accepted"
    assert cases["CW-PR-006"]["input"]["output_language"] == "zh-CN"
    assert cases["CW-PR-008"]["input"]["supplementary_inputs"] == (
        "optional_not_guaranteed"
    )
    assert cases["CW-PR-009"]["input"]["preprints"] == "accepted"
