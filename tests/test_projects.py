"""Tests for Project container, GitInspector, ProjectScanner, and ProjectStore."""

from __future__ import annotations

from pathlib import Path
import subprocess
import pytest

from conflux_weave.projects import (
    CommitInfo,
    FileNode,
    GitInspector,
    GitStatusSnapshot,
    Project,
    ProjectScanner,
    ProjectStore,
)


def test_git_inspector_detects_git_and_snapshot(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    # 1. Non-git directory
    status_non_git = GitInspector.get_status(repo)
    assert not status_non_git.is_git
    assert status_non_git.branch == ""

    # 2. Initialize git repository
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Tester"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)

    # Initial untracked file
    sample = repo / "hello.py"
    sample.write_text("print('hello')", encoding="utf-8")

    status_dirty = GitInspector.get_status(repo)
    assert status_dirty.is_git
    assert "hello.py" in status_dirty.untracked_files or "hello.py" in status_dirty.modified_files
    assert status_dirty.is_dirty

    # Commit
    subprocess.run(["git", "add", "hello.py"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=repo, check=True)

    status_clean = GitInspector.get_status(repo)
    assert status_clean.is_git
    assert not status_clean.is_dirty
    assert len(status_clean.commit_hash) >= 8
    assert "Initial commit" in status_clean.commit_message
    assert len(status_clean.recent_commits) >= 1
    assert status_clean.recent_commits[0].subject == "Initial commit"


def test_project_scanner_path_safety(tmp_path: Path) -> None:
    root = tmp_path / "project_root"
    root.mkdir()
    sub = root / "subdir"
    sub.mkdir()
    file_a = sub / "a.txt"
    file_a.write_text("content a", encoding="utf-8")

    # Valid relative path
    p = ProjectScanner.resolve_safe_path(root, "subdir/a.txt")
    assert p == file_a.resolve()

    # Empty / dot path returns root
    p_root = ProjectScanner.resolve_safe_path(root, "")
    assert p_root == root.resolve()

    # Parent directory escape rejected
    with pytest.raises(PermissionError, match="Path escape rejected"):
        ProjectScanner.resolve_safe_path(root, "../outside.txt")

    with pytest.raises(PermissionError, match="Path escape rejected"):
        ProjectScanner.resolve_safe_path(root, "subdir/../../secret.txt")


def test_project_scanner_read_file_safe(tmp_path: Path) -> None:
    root = tmp_path / "app"
    root.mkdir()
    f = root / "code.py"
    f.write_text("def run(): pass\n", encoding="utf-8")

    content, sha, sz = ProjectScanner.read_file_safe(root, "code.py")
    assert content == "def run(): pass\n"
    assert len(sha) == 64
    assert sz == f.stat().st_size

    # File not found
    with pytest.raises(FileNotFoundError):
        ProjectScanner.read_file_safe(root, "missing.py")

    # File exceeding limit
    big = root / "big.txt"
    big.write_text("A" * 1000, encoding="utf-8")
    with pytest.raises(ValueError, match="exceeds safety limit"):
        ProjectScanner.read_file_safe(root, "big.txt", max_size_bytes=500)


def test_project_scanner_tree(tmp_path: Path) -> None:
    root = tmp_path / "tree_proj"
    root.mkdir()
    (root / "src").mkdir()
    (root / "src" / "main.py").write_text("main", encoding="utf-8")
    (root / "README.md").write_text("# Readme", encoding="utf-8")

    # Ignored directory and files
    (root / ".git").mkdir()
    (root / ".git" / "config").write_text("git config", encoding="utf-8")
    (root / "__pycache__").mkdir()
    (root / "__pycache__" / "main.cpython-312.pyc").write_text("pyc", encoding="utf-8")

    nodes = ProjectScanner.scan_tree(root, max_depth=3)
    names = {n.name for n in nodes}
    assert "src" in names
    assert "README.md" in names
    assert ".git" not in names
    assert "__pycache__" not in names

    src_node = next(n for n in nodes if n.name == "src")
    assert src_node.is_dir
    assert any(c.name == "main.py" for c in src_node.children)


def test_project_store_lifecycle(tmp_path: Path) -> None:
    registry_file = tmp_path / "projects.json"
    default_ws = tmp_path / "default_ws"
    default_ws.mkdir()

    store = ProjectStore(registry_file, default_workspace=default_ws)
    projects = store.list_projects()
    assert len(projects) == 1
    assert projects[0].project_id == "proj-conflux-weave"

    # Register new project
    other_proj_dir = tmp_path / "other_app"
    other_proj_dir.mkdir()
    new_p = store.register("Other App", str(other_proj_dir), description="Testing registration")
    assert new_p.name == "Other App"
    assert new_p.root_path == str(other_proj_dir.resolve())

    # Get project
    fetched = store.get_project(new_p.project_id)
    assert fetched is not None
    assert fetched.name == "Other App"

    # Registering same directory returns existing
    again = store.register("Other App Duplicate", str(other_proj_dir))
    assert again.project_id == new_p.project_id

    # Non-existent directory raises
    with pytest.raises(ValueError, match="does not exist"):
        store.register("Bad Dir", str(tmp_path / "non_existent_folder"))

    # Delete project
    deleted = store.delete_project(new_p.project_id)
    assert deleted
    assert store.get_project(new_p.project_id) is None


def test_git_semantic_diff(tmp_path: Path) -> None:
    repo_dir = tmp_path / "diff_repo"
    repo_dir.mkdir()
    subprocess.run(["git", "init"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "DiffTester"], cwd=repo_dir, check=True)
    subprocess.run(["git", "config", "user.email", "diff@example.com"], cwd=repo_dir, check=True)

    # Initial commit
    (repo_dir / "README.md").write_text("# Repo\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo_dir, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo_dir, check=True)

    # Add core algorithm file
    (repo_dir / "retrieval_algo.py").write_text("def rrf_rank(): pass\n", encoding="utf-8")
    diff = GitInspector.get_semantic_diff(repo_dir, compare_branch="main")
    assert diff.current_branch != ""
    assert "实验意图分析" in diff.experiment_intent
    assert "core_algorithms" in diff.changed_areas

    # Non-git directory
    non_git_diff = GitInspector.get_semantic_diff(tmp_path / "not_git")
    assert non_git_diff.impact_level == "none"


def test_project_scanner_ast_summary(tmp_path: Path) -> None:
    root = tmp_path / "ast_test"
    root.mkdir()
    code_file = root / "sample.py"
    code_file.write_text(
        '"""Sample module."""\n'
        'import os\n'
        'from pathlib import Path\n'
        '\n'
        '# TODO: finish this\n'
        'class Worker:\n'
        '    """Worker class."""\n'
        '    def work(self):\n'
        '        pass\n'
        '    def mock_run(self):\n'
        '        return "mock_value"\n'
        '\n'
        'def top_func():\n'
        '    return 42\n',
        encoding="utf-8",
    )

    ast_summary = ProjectScanner.extract_ast_summary(root, "sample.py")
    assert ast_summary["line_count"] > 10
    assert len(ast_summary["classes"]) == 1
    assert ast_summary["classes"][0]["name"] == "Worker"
    assert len(ast_summary["classes"][0]["methods"]) == 2
    assert ast_summary["classes"][0]["methods"][0]["is_stub"] is True
    assert ast_summary["classes"][0]["methods"][1]["is_mock"] is True
    assert len(ast_summary["functions"]) == 1
    assert ast_summary["functions"][0]["name"] == "top_func"
    assert len(ast_summary["todos"]) == 1
    assert "TODO" in ast_summary["todos"][0]["text"]


def test_multi_folder_project_scanning(tmp_path: Path) -> None:
    backend_dir = tmp_path / "backend"
    backend_dir.mkdir()
    (backend_dir / "server.py").write_text("print('hello backend')", encoding="utf-8")

    frontend_dir = tmp_path / "frontend"
    frontend_dir.mkdir()
    (frontend_dir / "app.tsx").write_text("console.log('hello frontend');", encoding="utf-8")

    store = ProjectStore(tmp_path / "multi_proj.json", default_workspace=tmp_path)
    proj = store.register(
        name="Fullstack App",
        root_path=str(backend_dir),
        root_paths=[str(backend_dir), str(frontend_dir)],
        description="Fullstack multi-folder test",
    )
    assert len(proj.root_paths) == 2

    # Scan tree
    tree = ProjectScanner.scan_project_tree(proj)
    assert len(tree) == 2
    root_names = [node.name for node in tree]
    assert "backend" in root_names
    assert "frontend" in root_names

    # Read safe file from multi-root
    backend_file_node = next(n for n in tree if "backend" in n.name)
    assert len(backend_file_node.children) == 1
    backend_rel_path = backend_file_node.children[0].path
    content, sha, sz = ProjectScanner.read_file_safe(proj, backend_rel_path)
    assert "hello backend" in content

    frontend_file_node = next(n for n in tree if "frontend" in n.name)
    assert len(frontend_file_node.children) == 1
    frontend_rel_path = frontend_file_node.children[0].path
    content2, sha2, sz2 = ProjectScanner.read_file_safe(proj, frontend_rel_path)
    assert "hello frontend" in content2


