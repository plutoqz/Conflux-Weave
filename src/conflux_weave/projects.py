"""Project container, Git inspector, and safe file tree scanner for Direction A."""

from __future__ import annotations

import ast
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from typing import Any


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


DEFAULT_IGNORE_DIRS = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    "node_modules",
    "dist",
    "build",
    "var",
    ".workbuddy",
    ".zcode",
    "tmp",
}

DEFAULT_IGNORE_EXTS = {
    ".pyc",
    ".pyo",
    ".so",
    ".dll",
    ".exe",
    ".bin",
    ".sqlite3",
    ".lock",
    ".whl",
}


@dataclass
class CommitInfo:
    sha: str
    author: str
    date: str
    subject: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class GitStatusSnapshot:
    is_git: bool
    branch: str
    commit_hash: str
    commit_message: str
    is_dirty: bool
    modified_files: list[str] = field(default_factory=list)
    untracked_files: list[str] = field(default_factory=list)
    staged_files: list[str] = field(default_factory=list)
    recent_commits: list[CommitInfo] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "is_git": self.is_git,
            "branch": self.branch,
            "commit_hash": self.commit_hash,
            "commit_message": self.commit_message,
            "is_dirty": self.is_dirty,
            "modified_files": self.modified_files,
            "untracked_files": self.untracked_files,
            "staged_files": self.staged_files,
            "recent_commits": [c.to_dict() for c in self.recent_commits],
        }


@dataclass
class SemanticBranchDiff:
    current_branch: str
    compare_branch: str
    experiment_intent: str
    changed_areas: list[str] = field(default_factory=list)
    impact_level: str = "minor_tweak"  # major_experiment | minor_tweak | refactor | docs_only | test_suite | ui_enhancement | none
    file_diff_summaries: list[dict[str, Any]] = field(default_factory=list)
    total_additions: int = 0
    total_deletions: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class FileNode:
    name: str
    path: str
    is_dir: bool
    size_bytes: int = 0
    children: list[FileNode] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "path": self.path,
            "is_dir": self.is_dir,
            "size_bytes": self.size_bytes,
            "children": [c.to_dict() for c in self.children],
        }


@dataclass
class Project:
    project_id: str
    name: str
    root_path: str
    description: str = ""
    created_at: str = field(default_factory=_utc_now)
    updated_at: str = field(default_factory=_utc_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Project:
        return cls(
            project_id=str(data["project_id"]),
            name=str(data["name"]),
            root_path=str(data["root_path"]),
            description=str(data.get("description", "")),
            created_at=str(data.get("created_at", _utc_now())),
            updated_at=str(data.get("updated_at", _utc_now())),
        )


class GitInspector:
    """Safe read-only inspector for project Git state."""

    @staticmethod
    def is_git_repository(root_path: Path | str) -> bool:
        root_path = Path(root_path)
        git_dir = root_path / ".git"
        return git_dir.exists()

    @classmethod
    def get_status(cls, root_path: Path | str) -> GitStatusSnapshot:
        root_path = Path(root_path)
        if not cls.is_git_repository(root_path):
            return GitStatusSnapshot(
                is_git=False,
                branch="",
                commit_hash="",
                commit_message="",
                is_dirty=False,
            )

        branch = "unknown"
        commit_hash = ""
        commit_message = ""
        modified_files: list[str] = []
        untracked_files: list[str] = []
        staged_files: list[str] = []
        recent_commits: list[CommitInfo] = []

        try:
            # 1. Branch
            res_branch = subprocess.run(
                ["git", "branch", "--show-current"],
                cwd=root_path,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=5,
                check=False,
            )
            if res_branch.returncode == 0:
                branch = res_branch.stdout.strip() or "HEAD (detached)"

            # 2. Latest Commit & Message
            res_head = subprocess.run(
                ["git", "-c", "core.quotepath=false", "log", "-1", "--pretty=format:%H%x09%s"],
                cwd=root_path,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=5,
                check=False,
            )
            if res_head.returncode == 0 and res_head.stdout.strip():
                parts = res_head.stdout.strip().split("\t", 1)
                commit_hash = parts[0]
                commit_message = parts[1] if len(parts) > 1 else ""

            # 3. Status porcelain
            res_status = subprocess.run(
                ["git", "-c", "core.quotepath=false", "status", "--porcelain=v1"],
                cwd=root_path,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=5,
                check=False,
            )
            if res_status.returncode == 0:
                for line in res_status.stdout.splitlines():
                    if len(line) < 3:
                        continue
                    index_status = line[0]
                    work_status = line[1]
                    file_name = line[3:].strip().strip('"')
                    if index_status in {"M", "A", "D", "R"}:
                        staged_files.append(file_name)
                    if work_status == "M":
                        modified_files.append(file_name)
                    elif index_status == "?" and work_status == "?":
                        untracked_files.append(file_name)

            # 4. Recent commits
            res_log = subprocess.run(
                ["git", "-c", "core.quotepath=false", "log", "-n", "5", "--pretty=format:%H%x09%an%x09%cI%x09%s"],
                cwd=root_path,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=5,
                check=False,
            )
            if res_log.returncode == 0:
                for line in res_log.stdout.splitlines():
                    parts = line.split("\t")
                    if len(parts) >= 4:
                        recent_commits.append(
                            CommitInfo(
                                sha=parts[0],
                                author=parts[1],
                                date=parts[2],
                                subject=parts[3],
                            )
                        )
        except Exception:
            # Fallback gracefully
            pass

        is_dirty = bool(modified_files or untracked_files or staged_files)
        return GitStatusSnapshot(
            is_git=True,
            branch=branch,
            commit_hash=commit_hash,
            commit_message=commit_message,
            is_dirty=is_dirty,
            modified_files=modified_files,
            untracked_files=untracked_files,
            staged_files=staged_files,
            recent_commits=recent_commits,
        )

    @classmethod
    def get_diff(cls, root_path: Path | str, file_path: str | None = None) -> str:
        root_path = Path(root_path)
        if not cls.is_git_repository(root_path):
            return ""
        cmd = ["git", "-c", "core.quotepath=false", "diff", "HEAD"]
        if file_path:
            cmd.extend(["--", file_path])
        try:
            res = subprocess.run(
                cmd,
                cwd=root_path,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
                check=False,
            )
            if res.returncode == 0:
                return res.stdout
            # If HEAD diff fails (e.g. initial commit), try git diff
            res2 = subprocess.run(
                ["git", "-c", "core.quotepath=false", "diff"] + (["--", file_path] if file_path else []),
                cwd=root_path,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
                check=False,
            )
            return res2.stdout if res2.returncode == 0 else ""
        except Exception:
            return ""

    @classmethod
    def get_semantic_diff(cls, root_path: Path | str, compare_branch: str = "main") -> SemanticBranchDiff:
        root_path = Path(root_path)
        if not cls.is_git_repository(root_path):
            return SemanticBranchDiff(
                current_branch="",
                compare_branch=compare_branch,
                experiment_intent="目标路径非 Git 仓库，无版本对比数据。",
                changed_areas=[],
                impact_level="none",
                file_diff_summaries=[],
            )

        status = cls.get_status(root_path)
        cur_branch = status.branch

        diff_spec = f"{compare_branch}...HEAD"
        commit_spec = f"{compare_branch}..HEAD"

        file_diffs: list[dict[str, Any]] = []
        total_add = 0
        total_del = 0
        commit_msgs: list[str] = []

        try:
            res_log = subprocess.run(
                ["git", "log", commit_spec, "--pretty=format:%s"],
                cwd=root_path,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=5,
                check=False,
            )
            if res_log.returncode == 0 and res_log.stdout.strip():
                commit_msgs = [line.strip() for line in res_log.stdout.splitlines() if line.strip()]

            res_num = subprocess.run(
                ["git", "-c", "core.quotepath=false", "diff", "--numstat", diff_spec],
                cwd=root_path,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
                check=False,
            )
            num_output = res_num.stdout if res_num.returncode == 0 else ""
            if not num_output.strip():
                res_head = subprocess.run(
                    ["git", "-c", "core.quotepath=false", "diff", "--numstat", "HEAD~1"],
                    cwd=root_path,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=5,
                    check=False,
                )
                if res_head.returncode == 0 and res_head.stdout.strip():
                    num_output = res_head.stdout

            for line in num_output.splitlines():
                parts = line.split("\t")
                if len(parts) >= 3:
                    adds = int(parts[0]) if parts[0].isdigit() else 0
                    dels = int(parts[1]) if parts[1].isdigit() else 0
                    fname = parts[2].strip().strip('"')
                    total_add += adds
                    total_del += dels
                    file_diffs.append({
                        "file": fname,
                        "status": "committed_diff",
                        "category": cls._categorize_file(fname),
                        "additions": adds,
                        "deletions": dels,
                    })

            if status.is_dirty:
                for mf in status.modified_files:
                    if not any(f["file"] == mf for f in file_diffs):
                        file_diffs.append({
                            "file": mf,
                            "status": "worktree_modified",
                            "category": cls._categorize_file(mf),
                            "additions": 0,
                            "deletions": 0,
                        })
                for uf in status.untracked_files:
                    if not any(f["file"] == uf for f in file_diffs):
                        file_diffs.append({
                            "file": uf,
                            "status": "untracked",
                            "category": cls._categorize_file(uf),
                            "additions": 0,
                            "deletions": 0,
                        })
        except Exception:
            pass

        changed_areas = sorted({f["category"] for f in file_diffs})

        area_names = {
            "core_algorithms": "核心算法与智能体",
            "api_service": "API服务与契约",
            "storage_data": "数据存储与持久化",
            "workbench_ui": "前端工作台与交互",
            "test_verification": "自动化测试套件",
            "documentation_config": "文档与工程配置",
            "general_code": "通用代码与工具",
        }
        area_labels = [area_names.get(a, a) for a in changed_areas]

        if "core_algorithms" in changed_areas and (total_add + total_del >= 80 or len(commit_msgs) >= 3):
            impact = "major_experiment"
        elif set(changed_areas) <= {"documentation_config"}:
            impact = "docs_only"
        elif set(changed_areas) <= {"workbench_ui"}:
            impact = "ui_enhancement"
        elif set(changed_areas) <= {"test_verification"}:
            impact = "test_suite"
        elif total_add + total_del > 100:
            impact = "refactor"
        elif file_diffs:
            impact = "minor_tweak"
        else:
            impact = "none"

        if not file_diffs:
            intent = f"分支 `{cur_branch}` 与 `{compare_branch}` 处于代码同步状态，未发现差异变更。"
        else:
            recent_subjects = f"；近期提交涉及：{'、'.join(commit_msgs[:3])}" if commit_msgs else ""
            dirty_notice = f"（当前工作区有 {len(status.modified_files) + len(status.untracked_files)} 个未提交修改）" if status.is_dirty else ""
            intent = (
                f"实验意图分析：当前分支主要变动聚焦在【{'、'.join(area_labels)}】领域。"
                f"共涉及 {len(file_diffs)} 个变更项（累计新增 {total_add} 行，删除 {total_del} 行）"
                f"{dirty_notice}{recent_subjects}。"
            )

        return SemanticBranchDiff(
            current_branch=cur_branch,
            compare_branch=compare_branch,
            experiment_intent=intent,
            changed_areas=changed_areas,
            impact_level=impact,
            file_diff_summaries=file_diffs,
            total_additions=total_add,
            total_deletions=total_del,
        )

    @staticmethod
    def _categorize_file(file_path: str) -> str:
        p_lower = file_path.lower().replace("\\", "/")
        if any(k in p_lower for k in ["retrieval", "harness", "agent", "model", "engine", "rag", "fusion", "chat", "provider"]):
            return "core_algorithms"
        if any(k in p_lower for k in ["server", "api_contracts", "router", "endpoint"]):
            return "api_service"
        if any(k in p_lower for k in ["sqlite", "lancedb", "store", "database", "models.py", "repository"]):
            return "storage_data"
        if any(k in p_lower for k in ["workbench", "static", "templates", ".html", ".js", ".css"]):
            return "workbench_ui"
        if any(k in p_lower for k in ["tests/", "test_", "conftest"]):
            return "test_verification"
        if any(k in p_lower for k in ["docs/", ".md", "pyproject.toml", ".toml", ".yaml", ".yml", ".json"]):
            return "documentation_config"
        return "general_code"


class ProjectScanner:
    """Safe tree traversal and bounded file reader."""

    @staticmethod
    def resolve_safe_path(root_path: Path, rel_path: str) -> Path:
        """Resolve rel_path against root_path and ensure no path escape."""
        if not rel_path or rel_path.strip() in {"", "."}:
            return root_path.resolve()

        # Sanitize slashes
        clean_rel = rel_path.replace("\\", "/").strip("/")
        if ".." in clean_rel.split("/"):
            raise PermissionError("Path escape rejected: contains parent directory reference.")

        resolved_root = root_path.resolve()
        target = (resolved_root / clean_rel).resolve()
        if not target.is_relative_to(resolved_root):
            raise PermissionError("Path escape rejected: resolved path is outside project root.")
        return target

    @classmethod
    def scan_tree(
        cls,
        root_path: Path,
        max_depth: int = 4,
        max_files: int = 500,
        ignore_dirs: set[str] | None = None,
        ignore_exts: set[str] | None = None,
    ) -> list[FileNode]:
        """Scan root_path and return hierarchical FileNode list."""
        if not root_path.is_dir():
            return []

        ignored_d = ignore_dirs or DEFAULT_IGNORE_DIRS
        ignored_e = ignore_exts or DEFAULT_IGNORE_EXTS
        file_count = 0

        def _traverse(current_dir: Path, current_depth: int) -> list[FileNode]:
            nonlocal file_count
            if current_depth > max_depth or file_count >= max_files:
                return []

            nodes: list[FileNode] = []
            try:
                entries = sorted(current_dir.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
            except (PermissionError, OSError):
                return []

            for entry in entries:
                if file_count >= max_files:
                    break
                name = entry.name
                if entry.is_dir():
                    if name in ignored_d or name.startswith("."):
                        continue
                    children = _traverse(entry, current_depth + 1)
                    rel_p = str(entry.relative_to(root_path)).replace("\\", "/")
                    nodes.append(
                        FileNode(
                            name=name,
                            path=rel_p,
                            is_dir=True,
                            children=children,
                        )
                    )
                elif entry.is_file():
                    if name.startswith(".") and name not in {".gitignore", ".env.example"}:
                        continue
                    ext = entry.suffix.lower()
                    if ext in ignored_e:
                        continue
                    file_count += 1
                    rel_p = str(entry.relative_to(root_path)).replace("\\", "/")
                    try:
                        sz = entry.stat().st_size
                    except OSError:
                        sz = 0
                    nodes.append(
                        FileNode(
                            name=name,
                            path=rel_p,
                            is_dir=False,
                            size_bytes=sz,
                        )
                    )
            return nodes

        return _traverse(root_path.resolve(), 1)

    @classmethod
    def read_file_safe(
        cls,
        root_path: Path,
        rel_path: str,
        max_size_bytes: int = 1_048_576,  # 1 MB
    ) -> tuple[str, str, int]:
        """Read safe file content. Returns (content, sha256, size_bytes)."""
        target = cls.resolve_safe_path(root_path, rel_path)
        if not target.is_file():
            raise FileNotFoundError(f"File not found: {rel_path}")

        sz = target.stat().st_size
        if sz > max_size_bytes:
            raise ValueError(f"File size {sz} bytes exceeds safety limit of {max_size_bytes} bytes.")

        raw_bytes = target.read_bytes()
        file_sha256 = hashlib.sha256(raw_bytes).hexdigest()
        try:
            text_content = raw_bytes.decode("utf-8")
        except UnicodeDecodeError:
            text_content = raw_bytes.decode("utf-8", errors="replace")
        text_content = text_content.replace("\r\n", "\n")

        return text_content, file_sha256, sz

    @classmethod
    def extract_ast_summary(cls, root_path: Path, rel_path: str) -> dict[str, Any]:
        """Parse Python file using AST and extract classes, methods, stubs, mocks, and TODOs."""
        target = cls.resolve_safe_path(root_path, rel_path)
        if not target.is_file() or target.suffix.lower() != ".py":
            return {"file_path": rel_path, "classes": [], "functions": [], "todos": [], "imports": []}

        content, _, sz = cls.read_file_safe(root_path, rel_path)
        try:
            tree = ast.parse(content, filename=rel_path)
        except SyntaxError:
            return {
                "file_path": rel_path,
                "syntax_error": True,
                "classes": [],
                "functions": [],
                "todos": [],
                "imports": [],
            }

        def _is_stub(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
            body = list(node.body)
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
                body = body[1:]
            if not body:
                return True
            if len(body) == 1:
                stmt = body[0]
                if isinstance(stmt, ast.Pass):
                    return True
                if isinstance(stmt, ast.Raise):
                    if isinstance(stmt.exc, ast.Name) and stmt.exc.id == "NotImplementedError":
                        return True
                    if isinstance(stmt.exc, ast.Call) and isinstance(stmt.exc.func, ast.Name) and stmt.exc.func.id == "NotImplementedError":
                        return True
                if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant) and stmt.value.value is ...:
                    return True
            return False

        def _is_mock(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
            if "mock" in node.name.lower():
                return True
            doc = ast.get_docstring(node) or ""
            if "mock" in doc.lower() or "fake" in doc.lower():
                return True
            for stmt in node.body:
                if isinstance(stmt, ast.Return) and stmt.value is not None:
                    if isinstance(stmt.value, ast.Constant) and isinstance(stmt.value.value, str) and "mock" in str(stmt.value.value).lower():
                        return True
            return False

        classes_info = []
        functions_info = []
        imports_info = []

        for node in tree.body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        imports_info.append(alias.name)
                else:
                    mod = node.module or ""
                    for alias in node.names:
                        imports_info.append(f"{mod}.{alias.name}")
            elif isinstance(node, ast.ClassDef):
                methods = []
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        methods.append({
                            "name": item.name,
                            "line": item.lineno,
                            "is_stub": _is_stub(item),
                            "is_mock": _is_mock(item),
                            "docstring": (ast.get_docstring(item) or "")[:120],
                        })
                classes_info.append({
                    "name": node.name,
                    "line": node.lineno,
                    "methods": methods,
                    "method_count": len(methods),
                    "docstring": (ast.get_docstring(node) or "")[:120],
                })
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                functions_info.append({
                    "name": node.name,
                    "line": node.lineno,
                    "is_stub": _is_stub(node),
                    "is_mock": _is_mock(node),
                    "docstring": (ast.get_docstring(node) or "")[:120],
                })

        todos = []
        for idx, line in enumerate(content.splitlines(), start=1):
            if "# TODO" in line or "# FIXME" in line:
                todos.append({"line": idx, "text": line.strip()})

        return {
            "file_path": rel_path,
            "line_count": len(content.splitlines()),
            "classes": classes_info,
            "functions": functions_info,
            "todos": todos,
            "imports": imports_info,
        }


class ProjectStore:
    """Registry and storage for Conflux-Weave registered projects."""

    def __init__(self, registry_file: Path, default_workspace: Path | None = None) -> None:
        self.registry_file = registry_file
        self.default_workspace = default_workspace or Path.cwd()
        self._ensure_initialized()

    def _ensure_initialized(self) -> None:
        if not self.registry_file.exists():
            self.registry_file.parent.mkdir(parents=True, exist_ok=True)
            # Register default project (current workspace)
            default_proj = Project(
                project_id="proj-conflux-weave",
                name="Conflux-Weave",
                root_path=str(self.default_workspace.resolve()),
                description="Conflux-Weave 主工程代码库与工作区",
            )
            self._save([default_proj])

    def _load(self) -> list[Project]:
        if not self.registry_file.exists():
            return []
        try:
            data = json.loads(self.registry_file.read_text(encoding="utf-8"))
            return [Project.from_dict(item) for item in data]
        except Exception:
            return []

    def _save(self, projects: list[Project]) -> None:
        data = [p.to_dict() for p in projects]
        self.registry_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def list_projects(self) -> list[Project]:
        return self._load()

    def get_project(self, project_id: str) -> Project | None:
        for p in self._load():
            if p.project_id == project_id:
                return p
        return None

    def register(self, name: str, root_path: str, description: str = "") -> Project:
        resolved = Path(root_path).resolve()
        if not resolved.is_dir():
            raise ValueError(f"Project root path does not exist or is not a directory: {root_path}")

        projects = self._load()
        # Check if already registered
        for p in projects:
            if Path(p.root_path).resolve() == resolved:
                return p

        # Generate unique project id
        h = hashlib.sha256(str(resolved).encode("utf-8")).hexdigest()[:8]
        slug = re.sub(r"[^a-zA-Z0-9_-]", "-", name.lower()).strip("-") or "project"
        project_id = f"proj-{slug}-{h}"

        new_proj = Project(
            project_id=project_id,
            name=name.strip(),
            root_path=str(resolved),
            description=description.strip(),
        )
        projects.append(new_proj)
        self._save(projects)
        return new_proj

    def delete_project(self, project_id: str) -> bool:
        projects = self._load()
        initial_len = len(projects)
        projects = [p for p in projects if p.project_id != project_id]
        if len(projects) < initial_len:
            self._save(projects)
            return True
        return False
