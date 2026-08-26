import hashlib
import json
from pathlib import Path

import conflux_weave.core as core_contracts
import conflux_weave.evidence as evidence_contracts


ROOT = Path(__file__).parents[1]
FROZEN_DATASET = ROOT / "datasets" / "regression" / "personal-research-v1.0.0"
CONTRACT_MAP = (
    ROOT
    / "docs"
    / "plans"
    / "deprecated"
    / "v0.2"
    / "W0-case-contract-map.json"
)


def normalized_sha256(path: Path) -> str:
    content = path.read_text(encoding="utf-8").replace("\r\n", "\n").replace(
        "\r", "\n"
    )
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def test_contract_map_is_bound_to_frozen_manifest() -> None:
    contract_map = json.loads(CONTRACT_MAP.read_text(encoding="utf-8"))
    manifest_path = ROOT / contract_map["dataset"]["manifest_path"]
    assert contract_map["status"] == "frozen"
    assert contract_map["dataset"]["version"] == "1.0.0"
    assert normalized_sha256(manifest_path) == contract_map["dataset"][
        "manifest_sha256_lf"
    ]


def test_each_frozen_case_has_one_contract_mapping() -> None:
    contract_map = json.loads(CONTRACT_MAP.read_text(encoding="utf-8"))
    cases = [
        json.loads(line)
        for line in (FROZEN_DATASET / "cases.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
    ]
    mappings = {item["case_id"]: item for item in contract_map["case_mappings"]}
    assert set(mappings) == {case["case_id"] for case in cases}
    assert len(mappings) == len(contract_map["case_mappings"])

    for case in cases:
        mapping = mappings[case["case_id"]]
        expected = case["expected_outcome"]
        if expected == "complete":
            assert (mapping["run_outcome"], mapping["delivery_disposition"]) == (
                "succeeded",
                "complete",
            )
        elif expected == "no_answer":
            assert (mapping["run_outcome"], mapping["delivery_disposition"]) == (
                "succeeded",
                "no_answer",
            )
        elif expected == "partial":
            assert (mapping["run_outcome"], mapping["delivery_disposition"]) == (
                "partial",
                "partial",
            )
        else:
            assert expected == "waiting_for_user"
            assert (mapping["run_outcome"], mapping["delivery_disposition"]) == (
                "waiting_for_user",
                None,
            )


def test_declared_stable_contracts_are_exported() -> None:
    contract_map = json.loads(CONTRACT_MAP.read_text(encoding="utf-8"))
    exported = set(core_contracts.__all__) | set(evidence_contracts.__all__)
    assert set(contract_map["stable_contracts"]) <= exported


def test_confirmation_policy_matches_case_contract() -> None:
    contract_map = json.loads(CONTRACT_MAP.read_text(encoding="utf-8"))
    mappings = {item["case_id"]: item for item in contract_map["case_mappings"]}
    cases = [
        json.loads(line)
        for line in (FROZEN_DATASET / "cases.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
    ]
    for case in cases:
        policy = mappings[case["case_id"]]["user_input_policy"]
        if case["expected_outcome"] == "waiting_for_user":
            assert policy == "required"
        elif case["allowed_confirmation"]:
            assert policy == "conditional"
        else:
            assert policy == "not_expected"
