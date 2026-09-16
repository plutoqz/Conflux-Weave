"""Project dissection engine and codebase learning tutor for cloned and vibe-coding projects."""

from __future__ import annotations

import ast
import json
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class CodeSymbol:
    name: str
    kind: str  # "class", "function", "interface", "schema"
    file_path: str
    line: int
    summary: str


@dataclass(frozen=True)
class RoadmapStep:
    step_number: int
    title: str
    description: str
    target_files: tuple[str, ...]
    reading_focus: str
    estimated_minutes: int


@dataclass
class ProjectDissectionReport:
    project_id: str
    project_name: str
    project_type: str
    framework: str
    primary_language: str
    total_files: int
    total_lines: int
    mental_model: str
    design_philosophy: str
    onboarding_roadmap: list[dict[str, Any]]
    lexicon: list[dict[str, Any]]
    vibe_coding_tips: list[str]
    architecture_overview: list[str]
    entrypoints: list[str]
    ecosystem: dict[str, Any] = field(default_factory=dict)
    mission: dict[str, Any] = field(default_factory=dict)
    architectural_topology: dict[str, Any] = field(default_factory=dict)
    progressive_reading_roadmap: list[dict[str, Any]] = field(default_factory=list)
    vibecoding_hygiene_audit: dict[str, Any] = field(default_factory=dict)
    suggested_exploration_questions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "project_name": self.project_name,
            "project_type": self.project_type,
            "framework": self.framework,
            "primary_language": self.primary_language,
            "total_files": self.total_files,
            "total_lines": self.total_lines,
            "mental_model": self.mental_model,
            "design_philosophy": self.design_philosophy,
            "onboarding_roadmap": self.onboarding_roadmap,
            "lexicon": self.lexicon,
            "vibe_coding_tips": self.vibe_coding_tips,
            "architecture_overview": self.architecture_overview,
            "entrypoints": self.entrypoints,
            "ecosystem": self.ecosystem,
            "mission": self.mission,
            "architectural_topology": self.architectural_topology,
            "progressive_reading_roadmap": self.progressive_reading_roadmap,
            "vibecoding_hygiene_audit": self.vibecoding_hygiene_audit,
            "suggested_exploration_questions": self.suggested_exploration_questions,
        }


class ProjectDissector:
    """Analyzes unfamiliar or vibe-coded codebases to extract mental models, learning roadmaps, and symbol lexicons."""

    IGNORE_DIRS = {
        ".git",
        ".venv",
        "venv",
        "node_modules",
        "dist",
        "build",
        "__pycache__",
        ".idea",
        ".vscode",
        ".pytest_cache",
        "var",
        "tmp",
    }

    IGNORE_EXTS = {
        ".pyc",
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".ico",
        ".svg",
        ".sqlite3",
        ".db",
        ".lock",
        ".zip",
        ".tar",
        ".gz",
    }

    def __init__(self, root_path: Path | str, project_id: str = "", project_name: str = ""):
        self.root_path = Path(root_path).resolve()
        self.project_id = project_id
        self.project_name = project_name or self.root_path.name

    def dissect(self) -> ProjectDissectionReport:
        if not self.root_path.is_dir():
            raise FileNotFoundError(f"Project directory does not exist: {self.root_path}")

        files = self._collect_files()
        total_files = len(files)
        total_lines = 0

        # Scan extensions and counts
        ext_counts: dict[str, int] = {}
        for f in files:
            ext = f.suffix.lower()
            ext_counts[ext] = ext_counts.get(ext, 0) + 1
            try:
                if f.stat().st_size < 500_000:
                    total_lines += sum(1 for _ in f.open("rb"))
            except Exception:
                pass

        primary_lang = self._detect_primary_language(ext_counts)
        framework, project_type = self._detect_framework()
        entrypoints = self._detect_entrypoints(files)
        symbols = self._extract_key_symbols(files)
        readme_summary = self._extract_readme_summary()

        mental_model, philosophy = self._derive_mental_model(
            primary_lang, framework, project_type, readme_summary, symbols, entrypoints
        )

        roadmap = self._generate_roadmap(entrypoints, symbols, files)
        vibe_tips = self._generate_vibe_coding_tips(framework, files)
        arch_overview = self._generate_architecture_overview(files, symbols)

        ecosystem = {
            "languages": [primary_lang] + [l for l in ("Python", "TypeScript", "JavaScript", "Rust", "Go") if l != primary_lang and ext_counts.get("." + l.lower()[:2])],
            "primary_stack": f"{primary_lang} / {framework}",
            "package_manager": "uv / pip" if primary_lang == "Python" else "pnpm / npm" if "TypeScript" in primary_lang or "JavaScript" in primary_lang else "cargo" if primary_lang == "Rust" else "standard",
            "is_python": primary_lang == "Python",
            "is_node": "JavaScript" in primary_lang or "TypeScript" in primary_lang,
            "is_rust": primary_lang == "Rust",
            "is_go": primary_lang == "Go",
        }

        mission = {
            "purpose_and_value": readme_summary or f"该工程基于 {framework} 构建，主要提供 {project_type} 的系统实现与业务调度能力。",
            "target_audience": "研发工程师、架构师、开源学习者与全栈开发者",
            "core_problem_solved": f"解决了在 {primary_lang} 生态下高内聚业务逻辑组织与模块治理的问题。",
        }

        architectural_topology = {
            "entrypoints": entrypoints,
            "contracts_and_models": [s.file_path for s in symbols if "model" in s.file_path.lower() or "schema" in s.file_path.lower() or "type" in s.file_path.lower()][:4],
            "core_pipelines": [s.file_path for s in symbols if s.kind == "class"][:4],
            "test_suites": [str(f.relative_to(self.root_path)).replace("\\", "/") for f in files if "test" in f.name.lower()][:4],
            "configs": [str(f.relative_to(self.root_path)).replace("\\", "/") for f in files if any(c in f.name.lower() for c in ("config", "settings", ".env", "toml", "json"))][:4],
            "docs": [str(f.relative_to(self.root_path)).replace("\\", "/") for f in files if f.suffix.lower() == ".md"][:4],
        }

        diff_labels = ["基础", "核心", "引擎", "外设"]
        progressive_reading_roadmap = [
            {
                "step_number": s.step_number,
                "stage": f"第 {s.step_number} 阶段",
                "title": s.title,
                "files": list(s.target_files),
                "focus": s.description + " 关注重点：" + s.reading_focus,
                "tip": f"预计耗时 {s.estimated_minutes} 分钟。重点追踪模块接口与生命周期流转。",
                "difficulty": diff_labels[min(s.step_number - 1, len(diff_labels) - 1)],
            }
            for s in roadmap
        ]

        vibecoding_hygiene_audit = {
            "maturity_score": 85 if len(entrypoints) > 0 and len(symbols) > 5 else 65,
            "strengths": [
                f"工程结构清晰，采用 {framework} 标准规范分层",
                f"核心业务抽象明确，抽取出 {len(symbols)} 个关键类与领域导出符号",
                f"包含明确的模块启动入口：{', '.join(entrypoints[:2]) if entrypoints else '根目录模块'}",
            ],
            "risks_and_anti_patterns": vibe_tips,
            "production_roadmap": [
                "完善核心领域函数的参数校验与边界异常处理",
                "为关键业务流水线补充端到端单元测试与集成测试",
                "移除代码中的硬编码配置，统一收敛至环境变量或配置服务",
            ],
        }

        suggested_exploration_questions = [
            f"这个项目的核心执行流是如何从 {entrypoints[0] if entrypoints else '入口'} 开始运转的？",
            f"如果要在项目中新增一个扩展模块，应该在哪个目录下实现并注册？",
            f"项目中核心类 {symbols[0].name if symbols else '核心服务'} 的职责是什么？它是如何被调用的？",
            "针对这个项目，进行 Vibe Coding 或二次开发时有哪些潜在的隐患需要注意？",
        ]

        return ProjectDissectionReport(
            project_id=self.project_id,
            project_name=self.project_name,
            project_type=project_type,
            framework=framework,
            primary_language=primary_lang,
            total_files=total_files,
            total_lines=total_lines,
            mental_model=mental_model,
            design_philosophy=philosophy,
            onboarding_roadmap=[asdict(s) for s in roadmap],
            lexicon=[asdict(s) for s in symbols[:25]],
            vibe_coding_tips=vibe_tips,
            architecture_overview=arch_overview,
            entrypoints=entrypoints,
            ecosystem=ecosystem,
            mission=mission,
            architectural_topology=architectural_topology,
            progressive_reading_roadmap=progressive_reading_roadmap,
            vibecoding_hygiene_audit=vibecoding_hygiene_audit,
            suggested_exploration_questions=suggested_exploration_questions,
        )

    def _collect_files(self) -> list[Path]:
        collected = []
        try:
            for root, dirs, filenames in os.walk(self.root_path):
                dirs[:] = [d for d in dirs if d not in self.IGNORE_DIRS and not d.startswith(".")]
                for fname in filenames:
                    p = Path(root) / fname
                    if p.suffix.lower() not in self.IGNORE_EXTS:
                        collected.append(p)
        except Exception:
            pass
        return collected

    def _detect_primary_language(self, ext_counts: dict[str, int]) -> str:
        lang_map = {
            ".py": "Python",
            ".ts": "TypeScript",
            ".tsx": "TypeScript/React",
            ".js": "JavaScript",
            ".jsx": "JavaScript/React",
            ".rs": "Rust",
            ".go": "Go",
            ".java": "Java",
            ".cpp": "C++",
            ".c": "C",
        }
        best_lang = "Unknown"
        best_count = -1
        for ext, count in ext_counts.items():
            if ext in lang_map and count > best_count:
                best_lang = lang_map[ext]
                best_count = count
        return best_lang

    def _detect_framework(self) -> tuple[str, str]:
        root = self.root_path
        if (root / "package.json").is_file():
            try:
                pj = json.loads((root / "package.json").read_text(encoding="utf-8"))
                deps = {**pj.get("dependencies", {}), **pj.get("devDependencies", {})}
                if "next" in deps:
                    return "Next.js", "Fullstack Web App"
                if "react" in deps and "vite" in deps:
                    return "React + Vite", "Frontend SPA"
                if "react" in deps:
                    return "React", "Frontend App"
                if "express" in deps:
                    return "Express.js", "Node.js Backend"
                return "Node.js", "JavaScript/TypeScript Project"
            except Exception:
                pass

        if (root / "pyproject.toml").is_file():
            content = (root / "pyproject.toml").read_text(encoding="utf-8", errors="ignore")
            if "fastapi" in content.lower():
                return "FastAPI", "Python Web / API Service"
            if "django" in content.lower():
                return "Django", "Python Web Framework"
            if "flask" in content.lower():
                return "Flask", "Python Microservice"
            return "Modern Python", "Python Package / CLI"

        if (root / "requirements.txt").is_file():
            content = (root / "requirements.txt").read_text(encoding="utf-8", errors="ignore")
            if "fastapi" in content.lower():
                return "FastAPI", "Python Web / API Service"
            if "django" in content.lower():
                return "Django", "Python Web Framework"

        if (root / "Cargo.toml").is_file():
            return "Cargo", "Rust Project"
        if (root / "go.mod").is_file():
            return "Go Modules", "Go Project"

        return "Standard", "General Codebase"

    def _detect_entrypoints(self, files: list[Path]) -> list[str]:
        entry_candidates = [
            "main.py",
            "app.py",
            "server.py",
            "cli.py",
            "index.ts",
            "index.tsx",
            "main.ts",
            "main.tsx",
            "src/main.py",
            "src/app.py",
            "src/server.py",
            "src/index.ts",
            "src/index.tsx",
            "web/src/main.tsx",
            "web/src/App.tsx",
        ]
        found = []
        for candidate in entry_candidates:
            p = self.root_path / candidate
            if p.is_file():
                found.append(candidate)
        return found

    def _extract_readme_summary(self) -> str:
        for name in ("README.md", "readme.md", "README", "README.zh-CN.md"):
            p = self.root_path / name
            if p.is_file():
                try:
                    lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()
                    summary_lines = []
                    for line in lines[:30]:
                        if line.strip() and not line.startswith("#"):
                            summary_lines.append(line.strip())
                        if len(summary_lines) >= 3:
                            break
                    return " ".join(summary_lines)
                except Exception:
                    pass
        return ""

    def _extract_key_symbols(self, files: list[Path]) -> list[CodeSymbol]:
        symbols: list[CodeSymbol] = []
        for f in files:
            rel = str(f.relative_to(self.root_path)).replace("\\", "/")
            if f.suffix == ".py":
                try:
                    tree = ast.parse(f.read_text(encoding="utf-8", errors="ignore"))
                    for node in ast.iter_child_nodes(tree):
                        if isinstance(node, ast.ClassDef):
                            doc = ast.get_docstring(node) or ""
                            first_doc = doc.strip().splitlines()[0] if doc.strip() else "核心类定义"
                            symbols.append(
                                CodeSymbol(
                                    name=node.name,
                                    kind="class",
                                    file_path=rel,
                                    line=node.lineno,
                                    summary=first_doc[:120],
                                )
                            )
                        elif isinstance(node, ast.FunctionDef) and not node.name.startswith("_"):
                            doc = ast.get_docstring(node) or ""
                            first_doc = doc.strip().splitlines()[0] if doc.strip() else "顶层关键函数"
                            symbols.append(
                                CodeSymbol(
                                    name=node.name,
                                    kind="function",
                                    file_path=rel,
                                    line=node.lineno,
                                    summary=first_doc[:120],
                                )
                            )
                except Exception:
                    pass
            elif f.suffix in (".ts", ".tsx", ".js", ".jsx"):
                try:
                    text = f.read_text(encoding="utf-8", errors="ignore")
                    for m in re.finditer(r"export\s+(class|interface|type|function|const)\s+([A-Z][A-Za-z0-9_]+)", text):
                        line = text[: m.start()].count("\n") + 1
                        kind = m.group(1)
                        name = m.group(2)
                        symbols.append(
                            CodeSymbol(
                                name=name,
                                kind=kind,
                                file_path=rel,
                                line=line,
                                summary=f"前端/模块导出 {kind}: {name}",
                            )
                        )
                except Exception:
                    pass
            if len(symbols) >= 50:
                break
        return symbols

    def _derive_mental_model(
        self,
        lang: str,
        framework: str,
        project_type: str,
        readme: str,
        symbols: list[CodeSymbol],
        entrypoints: list[str],
    ) -> tuple[str, str]:
        core_classes = [s.name for s in symbols if s.kind == "class"][:4]
        class_str = "、".join(core_classes) if core_classes else "核心控制器与业务服务"

        mental_model = (
            f"本项目基于 {lang} 与 {framework} 构建（{project_type}）。"
            f"其核心架构以「{class_str}」为主轴组织，实现了端到端的数据流转与领域抽象。"
            + (f" 项目主要定位：{readme}。" if readme else "")
        )

        philosophy = (
            f"系统遵循模块化解耦与职责隔离原则：入口模块 ({', '.join(entrypoints[:2]) or '根目录启动点'}) "
            f"负责环境配置装配与生命周期初始化；核心领域层提供高内聚的状态处理与业务能力；"
            f"外部契约层通过统一的 API/数据总线向外暴露能力。"
        )
        return mental_model, philosophy

    def _generate_roadmap(
        self, entrypoints: list[str], symbols: list[CodeSymbol], files: list[Path]
    ) -> list[RoadmapStep]:
        roadmap = []
        ep_files = tuple(entrypoints[:2]) if entrypoints else ("README.md",)
        roadmap.append(
            RoadmapStep(
                step_number=1,
                title="1. 启动入口与环境配置",
                description="掌握项目是如何被初始化的，环境变量与依赖装配的流转链条。",
                target_files=ep_files,
                reading_focus="定位启动函数、CLI 参数解析、中间件注册以及依赖注入流程。",
                estimated_minutes=2,
            )
        )

        model_files = []
        for s in symbols:
            if "model" in s.file_path.lower() or "schema" in s.file_path.lower() or "type" in s.file_path.lower():
                if s.file_path not in model_files:
                    model_files.append(s.file_path)
        if not model_files and symbols:
            model_files = [symbols[0].file_path]
        roadmap.append(
            RoadmapStep(
                step_number=2,
                title="2. 核心数据模型与类型契约",
                description="摸清系统操作的核心实体定义，理解输入/输出数据结构与持久化表结构。",
                target_files=tuple(model_files[:3]),
                reading_focus="关注关键数据字段、验证逻辑（Pydantic/TypeScript 接口）以及生命周期状态。",
                estimated_minutes=3,
            )
        )

        core_files = []
        for s in symbols:
            if s.kind == "class" and s.file_path not in model_files and s.file_path not in ep_files:
                if s.file_path not in core_files:
                    core_files.append(s.file_path)
        roadmap.append(
            RoadmapStep(
                step_number=3,
                title="3. 领域核心引擎与业务调度",
                description="剖析系统最关键的算法、状态机或流水线执行逻辑。",
                target_files=tuple(core_files[:3]),
                reading_focus="追踪核心处理函数的调用链、错误恢复、异步分支以及外部服务交互点。",
                estimated_minutes=3,
            )
        )

        storage_files = []
        for f in files:
            rel = str(f.relative_to(self.root_path)).replace("\\", "/")
            if any(k in rel.lower() for k in ("store", "db", "repo", "client", "api")):
                if rel not in ep_files and rel not in core_files and rel not in model_files:
                    storage_files.append(rel)
        roadmap.append(
            RoadmapStep(
                step_number=4,
                title="4. 存储层、适配器与外设交互",
                description="理解数据是如何落盘与调用的，外部 API 或微服务交互边界。",
                target_files=tuple(storage_files[:3]) if storage_files else ("src/",),
                reading_focus="检查数据库连接池、事务边界、异常重试机制以及缓存策略。",
                estimated_minutes=2,
            )
        )

        return roadmap

    def _generate_vibe_coding_tips(self, framework: str, files: list[Path]) -> list[str]:
        tips = [
            "【边界防御】：针对 AI 辅助生成 (Vibe Coding) 的模块，重点核查边界条件、空值分支与类型转换异常。",
            "【状态流转】：注意检查是否存在跨请求共享的全局可变对象或隐式单例，防止并发状态污染。",
            "【错误隐藏】：排查是否有过宽的 `except Exception: pass` 静默捕获，确保异常具有明确的可追溯日志。",
            "【硬编码检查】：扫描是否存在遗留的本地硬编码路径、测试 API Key 或未参数化的超时配置。",
        ]
        if "FastAPI" in framework or "Python" in framework:
            tips.append("【异步安全】：检查在 async 路由内部是否存在耗时的阻塞性同步 I/O 调用，应使用 `asyncio.to_thread` 隔离。")
        elif "React" in framework or "Vite" in framework:
            tips.append("【前端重渲染】：审查 `useEffect` 与自定义 Hook 的依赖项数组，防止无限循环拉取或内存泄漏。")
        return tips

    def _generate_architecture_overview(self, files: list[Path], symbols: list[CodeSymbol]) -> list[str]:
        dirs = set()
        for f in files:
            rel = f.relative_to(self.root_path)
            parts = rel.parts
            if len(parts) > 1:
                dirs.add(parts[0])

        items = []
        for d in sorted(dirs):
            items.append(f"📁 `/{d}`: 包含针对 {d} 业务切面的具体实现与资源定义。")
        return items


def answer_project_learning_question(
    dissection: ProjectDissectionReport,
    question: str,
    chat_adapter: Any | None = None,
    extra_context: str = "",
) -> str:
    """Answers a developer's question about the project using codebase context and LLM."""
    if not question or not question.strip():
        return "请提供关于本项目的具体问题，例如：核心执行流程是什么、如何扩展新功能等。"

    lexicon_preview = ", ".join([str(item.get("name", "")) for item in dissection.lexicon[:8] if isinstance(item, dict)]) if dissection.lexicon else ""
    system_prompt = (
        "你是一个实事求是的代码工程架构助手与全栈工程师 (Codebase Copilot)。\n"
        "【严格要求】：\n"
        "1. 严禁任何虚构客套话或免责开场白（绝对禁止输出“作为资深技术导师”、“基于心智模型推演”、“由于未直接读取源码”等口癖或免责声明）。\n"
        "2. 针对用户问题，直接给出明确、专业、符合实际工程逻辑的解答，说明具体模块职责、接口分工、调用关系与关键实现。\n"
        "3. 输出规范的技术 Markdown 格式，包含清晰的层级标题与标准代码块。\n\n"
        f"工程背景事实：\n"
        f"- 项目名称: {dissection.project_name}\n"
        f"- 核心语言: {dissection.primary_language}\n"
        f"- 技术框架: {dissection.framework} ({dissection.project_type})\n"
        f"- 核心入口: {', '.join(dissection.entrypoints)}\n"
        f"- 核心符号: {lexicon_preview or '无'}\n"
    )

    user_prompt = f"项目工程背景：\n{extra_context}\n\n用户提问：{question.strip()}"

    if chat_adapter is not None:
        chat_func = getattr(chat_adapter, "complete", None) or getattr(chat_adapter, "chat", None)
        if chat_func is not None:
            try:
                res = chat_func(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    max_output_tokens=3000,
                    temperature=0.3,
                )
                content = getattr(res, "content", None) or getattr(res, "text", None) or (str(res) if isinstance(res, str) else None)
                if content and content.strip():
                    return content.strip()
            except Exception:
                pass

    # High-quality fallback deterministic answer
    return (
        f"### 🎓 项目解析指引：{dissection.project_name}\n\n"
        f"关于问题：**{question.strip()}**\n\n"
        f"#### 1. 核心架构契约与心智模型\n"
        f"{dissection.mental_model}\n\n"
        f"#### 2. 关联研读路径与入口\n"
        f"建议首先查阅入口文件 `{', '.join(dissection.entrypoints[:2]) or '核心启动文件'}`，"
        f"追踪以下核心符号的定义与调用：\n"
        + "\n".join(f"- `{s['name']}` ({s['kind']}): `{s['file_path']}:L{s['line']}` — {s['summary']}" for s in dissection.lexicon[:5])
        + "\n\n#### 3. Vibe-Coding 审查要点\n"
        + "\n".join(f"- {tip}" for tip in dissection.vibe_coding_tips[:3])
    )
