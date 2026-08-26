from __future__ import annotations

import json
from pathlib import Path


DATASET = Path("datasets/regression/s15-live-research-v1")


def test_s15_live_matrix_is_frozen_and_balanced() -> None:
    manifest = json.loads((DATASET / "manifest.json").read_text(encoding="utf-8"))
    cases = [
        json.loads(line)
        for line in (DATASET / "cases.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    assert manifest["case_count"] == len(cases) == 8
    assert manifest["live_execution_requires_explicit_flag"] is True
    assert len(manifest["new_sources"]) == 2
    assert {item["corpus_scope"] for item in cases} == {
        "arxiv_metadata",
        "local",
        "new",
        "mixed",
    }
    assert any(not item["expected_answerable"] for item in cases)
    assert {item["mode"] for item in cases} == {"discovery", "single", "managed"}

    for group in ("local-cross", "mixed-cross"):
        paired = [item for item in cases if item["comparison_group"] == group]
        assert len(paired) == 2
        assert {item["mode"] for item in paired} == {"single", "managed"}
        assert len({item["objective"] for item in paired}) == 1
        assert len({item["corpus_scope"] for item in paired}) == 1
