"""Tests for P11 acceptance fixes: overview stats, multimodal fallback, asset resolution, and project dissection."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from starlette.testclient import TestClient

from conflux_weave.project_dissection import (
    ProjectDissector,
    answer_project_learning_question,
)


def test_project_dissection_engine():
    """Verify ProjectDissector extracts mental models, roadmaps, and symbol lexicons."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        # Create a sample project structure
        (tmp_path / "src").mkdir()
        (tmp_path / "README.md").write_text("# Test Project\nA high-performance scientific workflow engine.", encoding="utf-8")
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "test-proj"\ndependencies = ["fastapi"]', encoding="utf-8")
        (tmp_path / "src" / "main.py").write_text(
            'class WorkflowEngine:\n    """Main workflow engine coordinator."""\n    pass\n\ndef start_service():\n    pass\n',
            encoding="utf-8",
        )
        (tmp_path / "src" / "models.py").write_text(
            'class WorkflowState:\n    """Execution state schema."""\n    pass\n',
            encoding="utf-8",
        )

        dissector = ProjectDissector(tmp_path, project_id="proj-test-1", project_name="Test Project")
        report = dissector.dissect()

        assert report.project_name == "Test Project"
        assert report.primary_language == "Python"
        assert "FastAPI" in report.framework
        assert len(report.entrypoints) >= 1
        assert len(report.onboarding_roadmap) >= 2
        assert len(report.progressive_reading_roadmap) >= 2
        assert len(report.lexicon) >= 1
        assert report.vibecoding_hygiene_audit["maturity_score"] > 0
        assert len(report.vibe_coding_tips) >= 3

        report_dict = report.to_dict()
        assert "ecosystem" in report_dict
        assert "mission" in report_dict
        assert "architectural_topology" in report_dict
        assert "progressive_reading_roadmap" in report_dict
        assert "vibecoding_hygiene_audit" in report_dict
        assert "suggested_exploration_questions" in report_dict

        # Test answering question
        ans = answer_project_learning_question(report, "核心执行流是什么？")
        assert "项目解析指引" in ans or "WorkflowEngine" in ans


def test_multimodal_caption_fallback_on_dimension_conflict():
    """Verify search_images_by_text does not return empty tuple when vector dimension conflicts."""
    from conflux_weave.multimodal_retrieval import MultimodalRetrievalPipeline, MultimodalRetrievalHit

    mock_text_pipeline = MagicMock()
    mock_image_index = MagicMock()
    mock_image_index.vector_dimensions.return_value = 1024
    
    # Simulate search_caption_text returning a valid hit
    sample_hit = MultimodalRetrievalHit(
        asset_id="asset-test-01",
        score=0.9,
        rank=1,
        modality="image",
        source_snapshot_id="snap-01",
        document_id="doc-01",
        page=2,
        bbox=None,
        coordinate_space="pdf_page_points_top_left",
        parent_chunk_ids=(),
        caption="Architecture Overview Diagram",
        artifact_ref="artifact-sha256-test",
        thumbnail_artifact_ref=None,
        embedding_model="jina-clip-v2",
        index_version="v1",
        locator={"page": 2},
    )
    mock_image_index.search_caption_text.return_value = [sample_hit]
    mock_image_index.table = MagicMock()

    mock_embedder = MagicMock()
    mock_embedder.dimensions = 128  # Conflict with 1024!

    mock_text_pipeline.documents = ()
    mock_text_pipeline.document_by_id = {}
    pipeline = MultimodalRetrievalPipeline(
        text_pipeline=mock_text_pipeline,
        image_index=mock_image_index,
        image_embedding=mock_embedder,
    )

    # Dimensional conflict is detected
    conflict = pipeline._image_dimension_conflict()
    assert conflict is not None
    assert "image_dimension_mismatch" in conflict

    # With our fix, caption hits MUST still be retrieved!
    hits = pipeline.search_images_by_text("Architecture Overview", top_k=5)
    assert len(hits) == 1
    assert hits[0].asset_id == "asset-test-01"
    assert hits[0].caption == "Architecture Overview Diagram"


def test_overview_stats_endpoint(tmp_path):
    """Verify GET /api/v1/overview/stats returns runs and corpus counts."""
    from conflux_weave.runtime import LocalArtifactStore, SQLiteRuntimeRepository
    from conflux_weave.server import create_app
    from starlette.testclient import TestClient

    store = LocalArtifactStore(tmp_path / "artifacts")
    repo = SQLiteRuntimeRepository(tmp_path / "db" / "repo.sqlite3", store)
    orchestrator = MagicMock()
    app = create_app(repo, orchestrator, enable_worker=False)

    client = TestClient(app)
    res = client.get("/api/v1/overview/stats")
    assert res.status_code == 200
    data = res.json()
    assert "runs" in data
    assert "total" in data["runs"]
    assert "success_rate" in data["runs"]
    assert "corpus" in data
    assert "total_documents" in data["corpus"]


def test_browse_folder_endpoint_safe(tmp_path, monkeypatch):
    """Verify POST /api/v1/projects/browse-folder returns gracefully without 500."""
    from conflux_weave.runtime import LocalArtifactStore, SQLiteRuntimeRepository
    from conflux_weave.server import create_app
    from starlette.testclient import TestClient
    import subprocess

    store = LocalArtifactStore(tmp_path / "artifacts")
    repo = SQLiteRuntimeRepository(tmp_path / "db" / "repo.sqlite3", store)
    orchestrator = MagicMock()
    app = create_app(repo, orchestrator, enable_worker=False)

    mock_res = MagicMock()
    mock_res.stdout = str(tmp_path)
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: mock_res)

    client = TestClient(app)
    res = client.post("/api/v1/projects/browse-folder?initial_dir=" + str(tmp_path))
    assert res.status_code == 200
    data = res.json()
    assert data["path"] == str(tmp_path.resolve())


def test_project_learning_guide_endpoints(tmp_path):
    """Verify GET /api/v1/projects/{id}/learning-guide and POST ask-learning."""
    from conflux_weave.runtime import LocalArtifactStore, SQLiteRuntimeRepository
    from conflux_weave.server import create_app
    from starlette.testclient import TestClient

    store = LocalArtifactStore(tmp_path / "artifacts")
    repo = SQLiteRuntimeRepository(tmp_path / "db" / "repo.sqlite3", store)
    orchestrator = MagicMock()
    app = create_app(repo, orchestrator, enable_worker=False)

    client = TestClient(app)
    proj_dir = tmp_path / "sample_proj"
    proj_dir.mkdir()
    (proj_dir / "README.md").write_text("# Cloned Project\nLearning sample", encoding="utf-8")
    (proj_dir / "main.py").write_text("class MyService:\n    pass\n", encoding="utf-8")

    reg_res = client.post("/api/v1/projects", json={
        "name": "Sample Cloned Project",
        "root_path": str(proj_dir),
        "description": "A sample project for learning",
    })
    assert reg_res.status_code == 200
    proj_id = reg_res.json()["project_id"]

    guide_res = client.get(f"/api/v1/projects/{proj_id}/learning-guide")
    assert guide_res.status_code == 200
    guide = guide_res.json()
    assert guide["project_name"] == "Sample Cloned Project"
    assert "ecosystem" in guide
    assert "progressive_reading_roadmap" in guide
    assert len(guide["progressive_reading_roadmap"]) >= 1

    ask_res = client.post(f"/api/v1/projects/{proj_id}/ask-learning", json={
        "question": "这个项目核心服务怎么使用？"
    })
    assert ask_res.status_code == 200
    ans_data = ask_res.json()
    assert "answer" in ans_data
    assert "项目解析指引" in ans_data["answer"] or "MyService" in ans_data["answer"]


def test_lancedb_asset_resolution_and_content_fallback(tmp_path):
    """Verify LanceDB asset resolution and thumbnail fallback to original image."""
    from conflux_weave.runtime import LocalArtifactStore, SQLiteRuntimeRepository
    from conflux_weave.server import create_app
    from starlette.testclient import TestClient

    store = LocalArtifactStore(tmp_path / "artifacts")
    # Put a test image file into artifact store
    art_ref = store.put_bytes(
        b"\x89PNG\r\n\x1a\nfakeimagecontent",
        media_type="image/png",
        schema_version="conflux-weave.test.v1",
        producer_step_id="test",
    )

    repo = SQLiteRuntimeRepository(tmp_path / "db" / "repo.sqlite3", store)
    orchestrator = MagicMock()
    mock_pipeline = MagicMock()
    mock_idx = MagicMock()
    mock_tbl = MagicMock()

    mock_row = {
        "asset_id": "asset-test-01",
        "document_id": "doc-01",
        "source_snapshot_id": "snap-01",
        "page": 1,
        "caption": "Sample Figure",
        "artifact_ref": art_ref.artifact_id,
        "thumbnail_artifact_ref": "",  # Empty thumbnail!
        "locator_json": '{"page": 1}',
    }
    mock_search = MagicMock()
    mock_search.where.return_value.limit.return_value.to_list.return_value = [mock_row]
    mock_search.limit.return_value.to_list.return_value = [mock_row]
    mock_tbl.search.return_value = mock_search
    mock_idx.table = mock_tbl
    mock_pipeline.image_index = mock_idx

    app = create_app(repo, orchestrator, retrieval_pipeline=mock_pipeline, enable_worker=False)
    client = TestClient(app)

    # 1. Detail endpoint
    detail_res = client.get("/api/v1/library/assets/asset-test-01")
    assert detail_res.status_code == 200
    detail = detail_res.json()
    assert detail["asset_id"] == "asset-test-01"
    assert detail["caption"] == "Sample Figure"
    assert detail["content_url"] == "/api/v1/library/assets/asset-test-01/content"

    # 2. Content endpoint with thumbnail fallback
    thumb_res = client.get("/api/v1/library/assets/asset-test-01/content?variant=thumbnail")
    assert thumb_res.status_code == 200
    assert thumb_res.content == b"\x89PNG\r\n\x1a\nfakeimagecontent"


