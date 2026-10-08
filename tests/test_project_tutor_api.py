"""REST API endpoint and end-to-end workflow tests for Project Dissection V2 & Tutor."""

from __future__ import annotations

from pathlib import Path
import tempfile
from types import SimpleNamespace

from starlette.testclient import TestClient

from conflux_weave.project_tutor.workflow import run_project_dissection_v2
from conflux_weave.runtime import LocalArtifactStore
from conflux_weave.runtime.sqlite import SQLiteRuntimeRepository
from conflux_weave.server import create_app


def test_project_dissection_v2_workflow_e2e(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path / "artifacts")
    project_root = tmp_path / "sample_project"
    project_root.mkdir()
    (project_root / "pyproject.toml").write_text("[project]\nname='sample-pkg'\ndependencies=['fastapi']\n", encoding="utf-8")
    (project_root / "app.py").write_text("def run():\n    return 'running'\n", encoding="utf-8")

    report = run_project_dissection_v2(
        root_path=project_root,
        project_name="Sample Project",
        artifact_store=store,
    )

    assert report.project_name == "Sample Project"
    assert report.motivations is not None
    assert len(report.dependency_cards) >= 1
    assert len(report.walkthrough_stages) == 4
    assert report.hygiene_report.hygiene_score > 50
    assert "## 一、架构设计动机逆向工程" in report.full_markdown
    assert "## 二、系统分层拓扑与核心数据流图谱" in report.full_markdown
    assert "## 三、技术栈通识教学与核心依赖原理剖析" in report.full_markdown
    assert "## 四、“自顶向下”保姆级源码精读路线导航" in report.full_markdown
    assert "## 五、“AI 编程恶习与官僚防御过当”专项深度体检诊断书" in report.full_markdown


def test_project_tutor_api_endpoints(tmp_path: Path) -> None:
    db_file = tmp_path / "tutor_api.sqlite3"
    store = LocalArtifactStore(tmp_path / "artifacts")
    repository = SQLiteRuntimeRepository(db_file, store)
    orchestrator = SimpleNamespace()
    app = create_app(repository, orchestrator)
    client = TestClient(app)

    project_root = tmp_path / "demo_project"
    project_root.mkdir()
    (project_root / "main.py").write_text("import fastapi\ndef start():\n    pass\n", encoding="utf-8")

    # 1. Test /api/v1/projects/tutor/dissect endpoint
    resp_dissect = client.post(
        "/api/v1/projects/tutor/dissect",
        json={
            "project_path": str(project_root),
            "project_name": "Demo Project",
        },
    )
    assert resp_dissect.status_code == 200, resp_dissect.text
    data_dissect = resp_dissect.json()
    assert data_dissect["project_name"] == "Demo Project"
    assert len(data_dissect["decisions"]) >= 3
    assert "flowchart TD" in data_dissect["topology_mermaid"]
    assert len(data_dissect["walkthrough_stages"]) == 4
    assert len(data_dissect["full_markdown"]) > 1000

    # 2. Test /api/v1/projects/tutor/hygiene-audit endpoint
    resp_audit = client.post(
        "/api/v1/projects/tutor/hygiene-audit",
        json={
            "project_path": str(project_root),
            "max_files": 50,
        },
    )
    assert resp_audit.status_code == 200, resp_audit.text
    data_audit = resp_audit.json()
    assert "hygiene_score" in data_audit
    assert "findings_by_category" in data_audit
    assert "executive_verdict" in data_audit
