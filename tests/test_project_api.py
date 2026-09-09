"""Tests for Project & Coding REST API endpoints."""

from __future__ import annotations

from pathlib import Path
import subprocess
import pytest
from starlette.testclient import TestClient

from conflux_weave.runtime import SQLiteRuntimeRepository
from conflux_weave.runtime.artifacts import LocalArtifactStore
from conflux_weave.server import WorkerLoop, create_app


class _PassiveRuntime:
    executor_id = "passive-paper@v1"
    task_kinds = ("paper_discovery",)

    def work_once(self, *, now: str | None = None) -> None:
        return None


def build_test_app(tmp_path: Path):
    store = LocalArtifactStore(tmp_path / "artifacts")
    repository = SQLiteRuntimeRepository(
        tmp_path / "db" / "runtime.sqlite3", store, clock=lambda: "2026-09-09T12:00:00Z"
    )
    runtime = _PassiveRuntime()
    return create_app(
        repository,
        runtime,
        provider_configured=False,
        worker=WorkerLoop(runtime, interval_seconds=10),
    )


def test_project_api_lifecycle(tmp_path: Path) -> None:
    app = build_test_app(tmp_path)
    client = TestClient(app)

    # 1. GET /api/v1/projects (should have at least default project)
    res_list = client.get("/api/v1/projects")
    assert res_list.status_code == 200
    projects = res_list.json()
    assert isinstance(projects, list)
    assert len(projects) >= 1

    # 2. Register a new test project with git
    sample_dir = tmp_path / "test_git_app"
    sample_dir.mkdir()
    subprocess.run(["git", "init"], cwd=sample_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Tester"], cwd=sample_dir, check=True)
    subprocess.run(["git", "config", "user.email", "tester@test.com"], cwd=sample_dir, check=True)
    (sample_dir / "README.md").write_text("# Test Git App\n", encoding="utf-8")
    (sample_dir / "main.py").write_text("print('hello world')\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=sample_dir, check=True)
    subprocess.run(["git", "commit", "-m", "init test app"], cwd=sample_dir, check=True)

    res_reg = client.post(
        "/api/v1/projects",
        json={
            "name": "Test Git App",
            "root_path": str(sample_dir),
            "description": "Integration test project",
        },
    )
    assert res_reg.status_code == 200
    reg_data = res_reg.json()
    project_id = reg_data["project_id"]
    assert reg_data["name"] == "Test Git App"
    assert reg_data["git_status"]["is_git"] is True
    assert "init test app" in reg_data["git_status"]["commit_message"]

    # 3. GET /api/v1/projects/{project_id}
    res_get = client.get(f"/api/v1/projects/{project_id}")
    assert res_get.status_code == 200
    assert res_get.json()["project_id"] == project_id

    # 4. GET /api/v1/projects/{project_id}/tree
    res_tree = client.get(f"/api/v1/projects/{project_id}/tree")
    assert res_tree.status_code == 200
    tree_items = res_tree.json()["items"]
    names = {item["name"] for item in tree_items}
    assert "README.md" in names
    assert "main.py" in names

    # 5. GET /api/v1/projects/{project_id}/file?path=main.py
    res_file = client.get(f"/api/v1/projects/{project_id}/file?path=main.py")
    assert res_file.status_code == 200
    f_data = res_file.json()
    assert "hello world" in f_data["content"]
    assert len(f_data["sha256"]) == 64

    # 6. Path escape rejected (403)
    res_escape = client.get(f"/api/v1/projects/{project_id}/file?path=../../outside.txt")
    assert res_escape.status_code == 403
    assert res_escape.json()["code"] == "path_escape_rejected"

    # 7. POST /api/v1/projects/{project_id}/ask
    res_ask = client.post(
        f"/api/v1/projects/{project_id}/ask",
        json={"question": "项目的核心入口是什么？"},
    )
    assert res_ask.status_code == 200
    ask_data = res_ask.json()
    assert len(ask_data["answer_markdown"]) > 20
    assert ask_data["git_evidence"]["is_git"] is True

    # 8. POST /api/v1/projects/{project_id}/coding/propose
    res_prop = client.post(
        f"/api/v1/projects/{project_id}/coding/propose",
        json={
            "instruction": "添加计算总和函数",
            "target_file": "main.py",
            "custom_replacement": "print('hello world')\ndef add(a, b): return a + b\n",
        },
    )
    assert res_prop.status_code == 200
    prop_data = res_prop.json()
    proposal_id = prop_data["proposal_id"]
    assert prop_data["status"] == "proposed"
    assert "add(a, b)" in prop_data["proposed_content"]
    expected_hash = prop_data["original_hash"]

    # 9. POST /api/v1/projects/{project_id}/coding/apply with conflict (409)
    res_conflict = client.post(
        f"/api/v1/projects/{project_id}/coding/apply",
        json={
            "proposal_id": proposal_id,
            "target_file": "main.py",
            "expected_hash": "wrong_hash_to_trigger_conflict",
            "proposed_content": prop_data["proposed_content"],
        },
    )
    assert res_conflict.status_code == 409
    assert res_conflict.json()["code"] == "version_conflict"

    # 10. POST /api/v1/projects/{project_id}/coding/apply (success)
    res_apply = client.post(
        f"/api/v1/projects/{project_id}/coding/apply",
        json={
            "proposal_id": proposal_id,
            "target_file": "main.py",
            "expected_hash": expected_hash,
            "proposed_content": prop_data["proposed_content"],
        },
    )
    assert res_apply.status_code == 200
    assert res_apply.json()["success"] is True

    # Verify file content modified on disk
    assert "add(a, b)" in (sample_dir / "main.py").read_text(encoding="utf-8")

    # 11. GET /api/v1/projects/{project_id}/walkthrough
    res_walk = client.get(f"/api/v1/projects/{project_id}/walkthrough")
    assert res_walk.status_code == 200
    walk_data = res_walk.json()
    assert walk_data["project_id"] == project_id
    assert len(walk_data["components"]) >= 2
    assert "flowchart TD" in walk_data["mermaid_topology"]
    assert len(walk_data["data_flow_description"]) > 10

    # 12. GET /api/v1/projects/{project_id}/audit
    res_audit = client.get(f"/api/v1/projects/{project_id}/audit")
    assert res_audit.status_code == 200
    audit_data = res_audit.json()
    assert audit_data["project_id"] == project_id
    assert 50 <= audit_data["implementation_score"] <= 100
    assert 50 <= audit_data["health_score"] <= 100
    assert "status_counts" in audit_data
    assert "findings" in audit_data

    # 13. GET /api/v1/projects/{project_id}/git/semantic-diff
    res_diff = client.get(f"/api/v1/projects/{project_id}/git/semantic-diff?compare_branch=main")
    assert res_diff.status_code == 200
    diff_data = res_diff.json()
    assert "experiment_intent" in diff_data
    assert "changed_areas" in diff_data

