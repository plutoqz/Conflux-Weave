"""Tests for ProjectAgent and CodingAgent."""

from __future__ import annotations

from pathlib import Path
import subprocess
import pytest

from conflux_weave.project_agents import CodeProposal, CodingAgent, ProjectAgent
from conflux_weave.projects import Project


def _init_sample_git_project(tmp_path: Path) -> Project:
    proj_dir = tmp_path / "sample_project"
    proj_dir.mkdir()

    subprocess.run(["git", "init"], cwd=proj_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Tester"], cwd=proj_dir, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=proj_dir, check=True)

    (proj_dir / "README.md").write_text("# Demo Project\nThis is a sample project for testing.\n", encoding="utf-8")
    (proj_dir / "pyproject.toml").write_text('[project]\nname = "demo"\nversion = "0.1.0"\n', encoding="utf-8")
    (proj_dir / "src").mkdir()
    (proj_dir / "src" / "server.py").write_text('"""Main server module."""\ndef run(): pass\n', encoding="utf-8")

    subprocess.run(["git", "add", "."], cwd=proj_dir, check=True)
    subprocess.run(["git", "commit", "-m", "Initial commit for demo"], cwd=proj_dir, check=True)

    return Project(
        project_id="proj-demo-123",
        name="Demo Project",
        root_path=str(proj_dir.resolve()),
        description="Demo project for testing ProjectAgent",
    )


def test_project_agent_ask_offline_fallback(tmp_path: Path) -> None:
    project = _init_sample_git_project(tmp_path)
    agent = ProjectAgent(provider=None)

    # 1. Ask about architecture
    ans = agent.ask(project, "请问项目当前架构是什么？有哪些模块？")
    assert "README.md" in ans.cited_files or "pyproject.toml" in ans.cited_files
    assert ans.git_evidence["is_git"] is True
    assert "Demo Project" in ans.answer_markdown
    assert len(ans.answer_markdown) > 50

    # 2. Ask about Git status
    ans_git = agent.ask(project, "查看当前的 git 提交记录和分支")
    assert ans_git.git_evidence["is_git"] is True
    assert "Initial commit for demo" in ans_git.answer_markdown


def test_coding_agent_propose_and_apply_lifecycle(tmp_path: Path) -> None:
    project = _init_sample_git_project(tmp_path)
    coding_agent = CodingAgent(provider=None)

    # 1. Propose patch for existing file
    proposal = coding_agent.propose_patch(
        project,
        instruction="优化 server 启动流程",
        target_file="src/server.py",
        custom_replacement='"""Main server module."""\ndef run():\n    print("Server started successfully")\n',
    )
    assert proposal.status == "proposed"
    assert proposal.target_file == "src/server.py"
    assert len(proposal.original_hash) == 64
    assert "Server started successfully" in proposal.diff
    assert proposal.proposed_content != ""

    # 2. Apply patch
    success, msg = coding_agent.apply_patch(project, proposal)
    assert success is True
    assert proposal.status == "applied"
    assert "原子应用" in msg

    # Verify file updated on disk
    updated_file = Path(project.root_path) / "src" / "server.py"
    assert 'print("Server started successfully")' in updated_file.read_text(encoding="utf-8")


def test_coding_agent_revision_conflict_rejection(tmp_path: Path) -> None:
    project = _init_sample_git_project(tmp_path)
    coding_agent = CodingAgent(provider=None)

    # Propose patch
    proposal = coding_agent.propose_patch(
        project,
        instruction="增加健康检查",
        target_file="src/server.py",
        custom_replacement="def health(): return 'ok'\n",
    )

    # External actor modifies file before patch is applied
    target = Path(project.root_path) / "src" / "server.py"
    target.write_text("def conflicting_change(): pass\n", encoding="utf-8")

    # Apply patch should fail with revision conflict
    success, msg = coding_agent.apply_patch(project, proposal)
    assert success is False
    assert "revision_conflict" in msg
    assert proposal.status == "proposed"


def test_coding_agent_path_escape_rejected(tmp_path: Path) -> None:
    project = _init_sample_git_project(tmp_path)
    coding_agent = CodingAgent(provider=None)

    with pytest.raises(PermissionError, match="Path escape rejected"):
        coding_agent.propose_patch(
            project,
            instruction="破坏性外逃补丁",
            target_file="../../outside.py",
        )


def test_project_agent_generate_walkthrough(tmp_path: Path) -> None:
    project = _init_sample_git_project(tmp_path)
    agent = ProjectAgent(provider=None)

    walkthrough = agent.generate_walkthrough(project)
    assert walkthrough.project_id == project.project_id
    assert "Demo Project" in walkthrough.overview
    assert len(walkthrough.components) >= 2
    assert "flowchart TD" in walkthrough.mermaid_topology
    assert "-->" in walkthrough.mermaid_topology
    assert len(walkthrough.data_flow_description) > 20
    assert isinstance(walkthrough.theory_mappings, list)
    assert "direct_dependencies_count" in walkthrough.dependencies_analysis

    # Also test to_dict serialization
    w_dict = walkthrough.to_dict()
    assert w_dict["project_id"] == project.project_id
    assert len(w_dict["components"]) >= 2


def test_project_agent_generate_audit_report(tmp_path: Path) -> None:
    project = _init_sample_git_project(tmp_path)
    proj_dir = Path(project.root_path)

    # Add files that trigger specific audit rules
    # 1. Stub function
    stub_file = proj_dir / "src" / "stub_module.py"
    stub_file.write_text(
        '"""Module with stub."""\n'
        'def unfinished_feature():\n'
        '    """TODO: implement feature."""\n'
        '    pass\n',
        encoding="utf-8",
    )

    # 2. Mock function
    mock_file = proj_dir / "src" / "mock_service.py"
    mock_file.write_text(
        '"""Module with mock data."""\n'
        'def mock_remote_call():\n'
        '    return {"status": "mock_success"}\n',
        encoding="utf-8",
    )

    # 3. State leak
    leak_file = proj_dir / "src" / "leak_module.py"
    leak_file.write_text(
        'import os\n'
        'os.environ["POLLUTED_VAR"] = "unsafe_global_state"\n',
        encoding="utf-8",
    )

    agent = ProjectAgent(provider=None)
    report = agent.generate_audit_report(project)

    assert report.project_id == project.project_id
    assert 50 <= report.implementation_score <= 100
    assert 50 <= report.health_score <= 100
    assert "stub_or_todo" in report.status_counts
    assert "partially_implemented" in report.status_counts
    assert report.status_counts["stub_or_todo"] >= 1
    assert report.status_counts["partially_implemented"] >= 1

    # Check findings categories
    categories = {f.category for f in report.findings}
    assert "implementation_gap" in categories
    assert "state_leak" in categories

    # Check finding statuses
    statuses = {f.implementation_status for f in report.findings}
    assert "stub_or_todo" in statuses
    assert "partially_implemented" in statuses

    # Check serialization
    r_dict = report.to_dict()
    assert r_dict["report_id"] == report.report_id
    assert len(r_dict["findings"]) >= 3

