"""REST API endpoint tests for Deep Research V2 Long-Horizon Survey."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from starlette.testclient import TestClient

from conflux_weave.runtime import LocalArtifactStore
from conflux_weave.runtime.sqlite import SQLiteRuntimeRepository
from conflux_weave.server import create_app


def test_survey_plan_outline_and_execute_endpoints(tmp_path: Path):
    db_file = tmp_path / "survey_api.sqlite3"
    store = LocalArtifactStore(tmp_path / "artifacts")
    repository = SQLiteRuntimeRepository(db_file, store)
    orchestrator = SimpleNamespace()
    app = create_app(repository, orchestrator)
    client = TestClient(app)

    # 1. Test plan-outline endpoint
    resp_outline = client.post(
        "/api/v1/research/survey/plan-outline",
        json={
            "objective": "高效长序列大模型稀疏注意力架构综述",
            "background_context": "重点探讨 FlashAttention 与 Block-Sparse 演化",
            "max_sources": 50,
        },
    )
    assert resp_outline.status_code == 200, resp_outline.text
    data_outline = resp_outline.json()
    assert data_outline["objective"] == "高效长序列大模型稀疏注意力架构综述"
    assert len(data_outline["chapters"]) >= 5
    assert data_outline["total_probes_count"] >= 15
    assert data_outline["target_word_count_range"] == [8000, 15000]

    # 2. Test execute endpoint (with fast fallback crawler)
    resp_exec = client.post(
        "/api/v1/research/survey/execute",
        json={
            "objective": "端侧模型轻量化微调机制综述",
            "max_sources": 10,
        },
    )
    assert resp_exec.status_code == 200, resp_exec.text
    data_exec = resp_exec.json()
    assert data_exec["objective"] == "端侧模型轻量化微调机制综述"
    assert len(data_exec["sections"]) >= 5
    assert data_exec["total_word_count"] > 2000
    assert len(data_exec["executive_summary"]) >= 3
    assert "timeline" in data_exec["evolution_timeline_mermaid"]
    assert "| 技术流派" in data_exec["comparison_matrix_markdown"]
    assert len(data_exec["full_markdown"]) > 2000
    assert "## 核心执行要点" in data_exec["full_markdown"]
