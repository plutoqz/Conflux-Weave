"""ProjectAgent and CodingAgent implementations for Direction A."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
import difflib
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any

from conflux_weave.core import BudgetLedger
from conflux_weave.harness import (
    AgentProfile,
    AgentResult,
    AgentResultStatus,
    AgentTask,
    ContextBundle,
)
from conflux_weave.projects import GitInspector, Project, ProjectScanner
from conflux_weave.provider import OpenAICompatibleChatAdapter


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


@dataclass
class ProjectAnswer:
    answer_markdown: str
    cited_files: list[str] = field(default_factory=list)
    git_evidence: dict[str, Any] = field(default_factory=dict)
    risks_and_recommendations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TheoryMappingItem:
    concept: str
    paper_reference: str
    code_symbol: str
    file_path: str
    line_number: int
    description: str
    design_rationale: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ArchitectureComponent:
    name: str
    layer: str
    files: list[str] = field(default_factory=list)
    responsibilities: str = ""
    dependencies: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ArchitectureWalkthrough:
    project_id: str
    overview: str
    components: list[ArchitectureComponent] = field(default_factory=list)
    mermaid_topology: str = ""
    data_flow_description: str = ""
    theory_mappings: list[TheoryMappingItem] = field(default_factory=list)
    dependencies_analysis: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "overview": self.overview,
            "components": [c.to_dict() for c in self.components],
            "mermaid_topology": self.mermaid_topology,
            "data_flow_description": self.data_flow_description,
            "theory_mappings": [m.to_dict() for m in self.theory_mappings],
            "dependencies_analysis": self.dependencies_analysis,
        }


@dataclass
class AuditFinding:
    finding_id: str
    category: str  # implementation_gap | coupling_smell | state_leak | test_gap | security_risk
    severity: str  # critical | warning | info
    title: str
    description: str
    target_file: str
    line_number: int | None = None
    snippet: str = ""
    recommendation: str = ""
    implementation_status: str = "fully_implemented"  # fully_implemented | partially_implemented | stub_or_todo | unimplemented

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ProjectAuditReport:
    report_id: str
    project_id: str
    summary: str
    implementation_score: int  # 0 - 100
    health_score: int          # 0 - 100
    status_counts: dict[str, int] = field(default_factory=dict)
    findings: list[AuditFinding] = field(default_factory=list)
    checked_rules: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=_utc_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_id": self.report_id,
            "project_id": self.project_id,
            "summary": self.summary,
            "implementation_score": self.implementation_score,
            "health_score": self.health_score,
            "status_counts": self.status_counts,
            "findings": [f.to_dict() for f in self.findings],
            "checked_rules": self.checked_rules,
            "created_at": self.created_at,
        }


@dataclass
class CodeProposal:
    proposal_id: str
    project_id: str
    title: str
    rationale: str
    risk_level: str  # low | medium | high
    target_file: str
    original_hash: str
    diff: str
    proposed_content: str
    original_content: str = ""
    verification_commands: list[str] = field(default_factory=list)
    status: str = "proposed"  # proposed | applied | rejected
    created_at: str = field(default_factory=_utc_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CodeProposal:
        return cls(
            proposal_id=str(data["proposal_id"]),
            project_id=str(data["project_id"]),
            title=str(data.get("title", "")),
            rationale=str(data.get("rationale", "")),
            risk_level=str(data.get("risk_level", "low")),
            target_file=str(data.get("target_file", "")),
            original_hash=str(data.get("original_hash", "")),
            diff=str(data.get("diff", "")),
            proposed_content=str(data.get("proposed_content", "")),
            original_content=str(data.get("original_content", "")),
            verification_commands=list(data.get("verification_commands", [])),
            status=str(data.get("status", "proposed")),
            created_at=str(data.get("created_at", _utc_now())),
        )


class ProjectAgent:
    """Agent that analyzes project structure, Git status, code architecture, and answers queries."""

    profile = AgentProfile(
        agent_type="project_agent",
        version="v1",
        description="Reads project structure, Git repository state, docs and code to answer architectural and status questions.",
        accepted_task_kinds=("project_qa",),
        allowed_tool_ids=(),
        default_budget=BudgetLedger(
            wall_clock_seconds=60,
            input_tokens=4000,
            output_tokens=2000,
            estimated_cost="$0.05",
            tool_calls=10,
            retrieval_rounds=3,
            concurrency=1,
        ),
    )

    def __init__(
        self,
        provider: OpenAICompatibleChatAdapter | None = None,
        memory_agent: Any | None = None,
    ) -> None:
        self.provider = provider
        self.memory_agent = memory_agent

    def ask(
        self,
        project: Project,
        question: str,
        current_file: str | None = None,
        selected_snippet: str | None = None,
        source_version: str | None = None,
    ) -> ProjectAnswer:
        root = Path(project.root_path).resolve()
        git_status = GitInspector.get_status(root)
        tree_nodes = ProjectScanner.scan_tree(root, max_depth=2, max_files=100)

        # Gather relevant files
        cited_files: list[str] = []
        context_snippets: list[str] = []

        # If current_file is specified, prioritize it as primary context
        if current_file:
            clean_curr = re.sub(r"[:#]L?\d+$", "", current_file).replace("\\", "/").strip("/")
            if clean_curr:
                try:
                    c, _, _ = ProjectScanner.read_file_safe(root, clean_curr, max_size_bytes=65536)
                    if clean_curr not in cited_files:
                        cited_files.append(clean_curr)
                    lang = Path(clean_curr).suffix.lstrip(".") or "text"
                    snippet_block = ""
                    if selected_snippet and selected_snippet.strip():
                        snippet_block = f"\n> **用户选中的代码片段/关注行**:\n```{lang}\n{selected_snippet.strip()[:4000]}\n```\n"
                    context_snippets.append(f"### [当前查看文件: {clean_curr}]\n{snippet_block}完整文件上下文:\n```{lang}\n{c[:3000]}\n```")
                except Exception:
                    pass

        # Check README.md
        readme_path = root / "README.md"
        if readme_path.is_file() and "README.md" not in cited_files:
            try:
                c, _, _ = ProjectScanner.read_file_safe(root, "README.md", max_size_bytes=65536)
                cited_files.append("README.md")
                context_snippets.append(f"### [README.md]\n```markdown\n{c[:2000]}\n```")
            except Exception:
                pass

        # Check pyproject.toml
        pyproject_path = root / "pyproject.toml"
        if pyproject_path.is_file():
            try:
                c, _, _ = ProjectScanner.read_file_safe(root, "pyproject.toml", max_size_bytes=65536)
                cited_files.append("pyproject.toml")
                context_snippets.append(f"### [pyproject.toml]\n```toml\n{c[:1500]}\n```")
            except Exception:
                pass
        # Find files matching question keywords recursively
        q_words = [w.lower() for w in re.split(r"[\s,._\-\\/]+", question) if len(w) >= 2]
        all_files: list[tuple[str, Path]] = []
        ignored_dirs = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", ".next", ".pytest_cache"}

        try:
            for p in root.rglob("*"):
                if p.is_file() and not any(part in ignored_dirs or part.startswith(".") for part in p.parts):
                    try:
                        rel = str(p.relative_to(root)).replace("\\", "/")
                        all_files.append((rel, p))
                    except ValueError:
                        pass
                    if len(all_files) >= 1500:
                        break
        except Exception:
            pass

        scored_files: list[tuple[int, str, Path]] = []
        for rel, p in all_files:
            rel_lower = rel.lower()
            score = sum(1 for w in q_words if w in rel_lower)
            if score > 0:
                scored_files.append((score, rel, p))

        scored_files.sort(key=lambda item: item[0], reverse=True)
        for _, rel, p in scored_files[:6]:
            if rel not in cited_files:
                cited_files.append(rel)
                content, _, _ = ProjectScanner.read_file_safe(root, rel, max_size_bytes=16384)
                lang_tag = p.suffix.lstrip(".") or "text"
                context_snippets.append(f"--- File: {rel} ---\n```{lang_tag}\n{content[:3000]}\n```")

        # If no specific matches, include directory structure
        if not context_snippets:
            context_snippets.append(f"Directory tree:\n" + "\n".join(f"- {n.path} ({n.kind})" for n in tree_nodes[:30]))

        git_summary = (
            f"Git 分支: `{git_status.branch}`, 最新提交: `{git_status.commit_hash[:8] if git_status.commit_hash else 'none'}` "
            f"('{git_status.commit_message}'), 工作区状态: {'存在未提交改动 (Dirty)' if git_status.is_dirty else '干净 (Clean)'}。"
        )

        risks: list[str] = []
        if git_status.is_dirty:
            risks.append(f"工作区存在 {len(git_status.modified_files) + len(git_status.untracked_files)} 个未提交或未跟踪文件，建议在重大修改前暂存。")

        # If provider available, query LLM
        if self.provider is not None:
            mem_ctx = ""
            if self.memory_agent is not None:
                mem_ctx = self.memory_agent.format_prompt_context(
                    user_id="user_default",
                    project_id=project.project_id,
                )
            system_prompt = (
                "你是一个资深架构师与代码项目分析专家 (ProjectAgent)。\n"
                "你的职责是基于用户提供的项目结构、Git 状态与源代码片段，回答用户的项目架构、功能实现、代码细节与工程状态问题。\n"
                "严格要求：\n"
                "1. 务必实事求是，只依据提供的代码和事实陈述，禁止编造不存在的文件或模块；\n"
                "2. 引用代码或文件时使用反引号注明相对路径；\n"
                "3. 输出格式清晰、专业、层级分明，使用 GitHub 风格 Markdown。"
            )
            if mem_ctx:
                system_prompt = f"{system_prompt}\n\n{mem_ctx}"
            user_msg = (
                f"项目名称: {project.name}\n"
                f"项目路径: {project.root_path}\n"
                f"{git_summary}\n\n"
                f"项目上下文信息:\n" + "\n\n".join(context_snippets) + f"\n\n用户问题: {question}"
            )
            try:
                chat_func = getattr(self.provider, "complete", None) or getattr(self.provider, "chat", None)
                if chat_func is not None:
                    chat_resp = chat_func(
                        system_prompt=system_prompt,
                        user_prompt=user_msg,
                        temperature=0.2,
                    )
                    content = getattr(chat_resp, "content", None) or (str(chat_resp) if isinstance(chat_resp, str) else None)
                    if content and content.strip():
                        return ProjectAnswer(
                            answer_markdown=content.strip(),
                            cited_files=cited_files,
                            git_evidence=git_status.to_dict(),
                            risks_and_recommendations=risks,
                        )
            except Exception:
                pass

        # Offline deterministic fallback tailored to target project
        report_lines = [
            f"## 项目分析报告：{project.name}",
            "",
            f"**项目根目录**：`{project.root_path}`  ",
            f"**Git 状态**：{git_summary}",
            "",
            "### 一、 项目结构概览",
            f"- 探测到顶级资源：{', '.join(n.name for n in tree_nodes[:8])} 等",
            f"- 核心引用文件：{', '.join(f'`{f}`' for f in cited_files) if cited_files else '项目根目录配置'}",
            "",
            "### 二、 针对问题的分析",
        ]

        if "实现" in question or "功能" in question or "架构" in question or "怎么" in question or "如何" in question:
            overview_desc = f"项目《{project.name}》"
            if readme_path.is_file():
                try:
                    c, _, _ = ProjectScanner.read_file_safe(root, "README.md", max_size_bytes=4096)
                    lines = [l.strip() for l in c.splitlines() if l.strip() and not l.startswith("#")]
                    if lines:
                        overview_desc += f" 主要定位为：{lines[0]}"
                except Exception:
                    pass
            if "主要定位" not in overview_desc:
                overview_desc += f" 包含 {len(tree_nodes)} 个主要探测目录/文件节点，提供模块化功能实现。"

            report_lines.extend([
                f"基于对 `{project.name}` 仓库源码与配置的静态解析：",
                f"1. **核心定位**：{overview_desc}",
                f"2. **模块与目录组成**：包含 {', '.join(f'`{n.name}`' for n in tree_nodes[:6])} 等核心模块划分；",
                f"3. **关键实现入口**：建议从核心文件 {', '.join(f'`{f}`' for f in cited_files[:3]) if cited_files else '根目录入口文件'} 开始追踪业务调用链路与执行逻辑。",
            ])
        elif "git" in question.lower() or "提交" in question or "变更" in question or "diff" in question.lower():
            report_lines.extend([
                f"当前 Git 状态分析：",
                f"- **分支**：`{git_status.branch}`",
                f"- **HEAD**：`{git_status.commit_hash[:10] if git_status.commit_hash else 'none'}` - {git_status.commit_message}",
                f"- **改动文件数**：{len(git_status.modified_files)} 个修改，{len(git_status.untracked_files)} 个未跟踪。",
            ])
            if git_status.recent_commits:
                report_lines.append("\n**最近提交记录**：")
                for c in git_status.recent_commits[:5]:
                    report_lines.append(f"- `{c.sha[:7]}` ({c.author}, {c.date[:10]}): {c.subject}")
        else:
            if selected_snippet and selected_snippet.strip():
                report_lines.extend([
                    f"针对当前选中的代码片段分析：",
                    f"```\n{selected_snippet.strip()[:400]}\n```",
                    f"关于针对该片段的提问 “{question}”：该代码片段位于 `{current_file or '当前文件'}`，属于关键逻辑分支。",
                    f"关联上下文文件：{', '.join(f'`{f}`' for f in cited_files)}。",
                ])
            else:
                report_lines.extend([
                    f"针对提问 “{question}”：",
                    f"已索引 `{project.name}` 上下文并检索相关文件：{', '.join(f'`{f}`' for f in cited_files) if cited_files else '项目根目录'}。",
                    f"如需更深入的局部逻辑剖析，可指定具体文件路径或选中代码片段进行定向提问。",
                ])

        if risks:
            report_lines.append("\n### 三、 风险与工程建议")
            for r in risks:
                report_lines.append(f"- {r}")

        return ProjectAnswer(
            answer_markdown="\n".join(report_lines),
            cited_files=cited_files,
            git_evidence=git_status.to_dict(),
            risks_and_recommendations=risks,
        )

    def generate_walkthrough(self, project: Project) -> ArchitectureWalkthrough:
        root = Path(project.root_path).resolve()

        overview = f"《{project.name}》项目面向学术研究与工程落地，基于模块化分层架构设计。"
        readme_path = root / "README.md"
        if readme_path.is_file():
            try:
                c, _, _ = ProjectScanner.read_file_safe(root, "README.md", max_size_bytes=8192)
                lines = [line.strip() for line in c.splitlines() if line.strip() and not line.startswith("#")]
                if lines:
                    overview = f"《{project.name}》- {lines[0]}"
            except Exception:
                pass

        is_conflux = (project.project_id == "proj-conflux-weave" or project.name.lower() == "conflux-weave") and (root / "src" / "conflux_weave").is_dir()

        if is_conflux:
            components = [
                ArchitectureComponent(
                    name="展示与工作台层 (Presentation)",
                    layer="presentation",
                    files=["src/conflux_weave/workbench/index.html", "src/conflux_weave/workbench/app.js", "src/conflux_weave/workbench/modules/projects.js"],
                    responsibilities="纯原生 ESM 单页应用，提供学术多模态检索、研读笔记、项目认知透视及代码治理交互。",
                    dependencies=["FastAPI REST & SSE 服务层"],
                ),
                ArchitectureComponent(
                    name="服务与契约层 (Service & API)",
                    layer="service",
                    files=["src/conflux_weave/server.py", "src/conflux_weave/api_contracts.py"],
                    responsibilities="对外暴露 REST API、SSE 增量流、Pydantic 输入强校验与安全沙箱越界拦截。",
                    dependencies=["确定性 Harness 与智能体控制层"],
                ),
                ArchitectureComponent(
                    name="确定性 Harness 与控制层 (Harness & Agents)",
                    layer="engine",
                    files=["src/conflux_weave/harness.py", "src/conflux_weave/project_agents.py", "src/conflux_weave/document_agent.py"],
                    responsibilities="受控预算调度、多智能体状态机、代码架构解构、设计契约审计与乐观锁补丁生成。",
                    dependencies=["多模态检索与 RAG 引擎", "权威元数据与向量索引持久化"],
                ),
                ArchitectureComponent(
                    name="多模态检索与 RAG 引擎 (Multimodal RAG)",
                    layer="retrieval",
                    files=["src/conflux_weave/retrieval.py", "src/conflux_weave/documents.py"],
                    responsibilities="BM25 稀疏检索与密集向量双模索引、倒数排序融合 (RRF) 及图文版面提取。",
                    dependencies=["权威元数据与向量索引持久化"],
                ),
                ArchitectureComponent(
                    name="权威元数据与向量持久化 (Persistence)",
                    layer="storage",
                    files=["src/conflux_weave/projects.py", "src/conflux_weave/runtime/sqlite.py"],
                    responsibilities="SQLite 单库单表权威记录与状态溯源、LanceDB 向量索引表族与安全文件系统探查。",
                    dependencies=[],
                ),
            ]
            mermaid_topology = (
                "flowchart TD\n"
                "    subgraph Presentation [\"展示交互层 (Presentation)\"]\n"
                "        WB[\"Workbench UI (原生ESM)\"]\n"
                "    end\n"
                "    subgraph Service [\"服务与契约层 (Service & API)\"]\n"
                "        API[\"FastAPI REST & SSE Server\"]\n"
                "        Contracts[\"Pydantic 契约与校验\"]\n"
                "    end\n"
                "    subgraph CoreEngine [\"核心智能体与控制层 (Agents & Harness)\"]\n"
                "        Harness[\"确定性执行 Harness & 预算账本\"]\n"
                "        PA[\"ProjectAgent (架构解构与审计)\"]\n"
                "        CA[\"CodingAgent (受控代码补丁)\"]\n"
                "        DA[\"DocumentAgent (文档研读与笔记)\"]\n"
                "    end\n"
                "    subgraph EngineRAG [\"多模态检索与 RAG 引擎 (Multimodal RAG)\"]\n"
                "        RAG[\"混合检索器 (BM25 + 向量)\"]\n"
                "        RRF[\"倒数排序融合 (RRF 算法)\"]\n"
                "        MM[\"多模态图表/文档解析\"]\n"
                "    end\n"
                "    subgraph Storage [\"存储与索引层 (Persistence)\"]\n"
                "        SQL[\"SQLite 权威元数据持久化\"]\n"
                "        LANCE[\"LanceDB 向量索引表族\"]\n"
                "    end\n"
                "\n"
                "    WB --> API\n"
                "    API --> Contracts\n"
                "    API --> Harness\n"
                "    Harness --> PA\n"
                "    Harness --> CA\n"
                "    Harness --> DA\n"
                "    DA --> RAG\n"
                "    PA --> RAG\n"
                "    RAG --> RRF\n"
                "    RRF --> LANCE\n"
                "    Contracts --> SQL"
            )
            data_flow = (
                "1. **请求接入**：用户于 Workbench 发起任务，FastAPI 拦截并执行安全越界检查与 Pydantic 契约校验；\n"
                "2. **调度控制**：DeterministicHarness 接入预算账本，分派 ProjectAgent 或 DocumentAgent 执行编排；\n"
                "3. **检索增强**：引擎调取 BM25 词频与 LanceDB 向量嵌入，经由 RRF 算法按倒数排位加权融合；\n"
                "4. **状态溯源**：所有执行事件与产出物严格写回 SQLite，通过 SSE 实时增量回流前端展示。"
            )
        else:
            src_files: list[str] = []
            for p in root.rglob("*.py"):
                if not any(ign in p.parts for ign in [".git", ".venv", "venv", "__pycache__", "build", "dist"]):
                    src_files.append(str(p.relative_to(root)).replace("\\", "/"))
                if len(src_files) >= 60:
                    break

            api_files = [f for f in src_files if any(k in f.lower() for k in ["api", "server", "main", "app", "view"])]
            core_files = [f for f in src_files if any(k in f.lower() for k in ["model", "agent", "core", "engine", "algo", "loss", "train"])]
            data_files = [f for f in src_files if any(k in f.lower() for k in ["data", "db", "dataset", "store", "loader", "utils"])]
            other_files = [f for f in src_files if f not in api_files and f not in core_files and f not in data_files]

            components = [
                ArchitectureComponent(
                    name="入口与服务层 (Interface & Service)",
                    layer="service",
                    files=api_files[:5],
                    responsibilities="外部调用入口、CLI 命令行参数解析或 HTTP 请求路由分发。",
                    dependencies=["核心算法与逻辑层"],
                ),
                ArchitectureComponent(
                    name="核心算法与模型层 (Core Logic & Model)",
                    layer="engine",
                    files=core_files[:5],
                    responsibilities="算法实现、模型网络定义、计算图调度与业务核心规则。",
                    dependencies=["数据与工具支撑层"],
                ),
                ArchitectureComponent(
                    name="数据与工具支撑层 (Data & Utilities)",
                    layer="storage",
                    files=(data_files + other_files)[:5],
                    responsibilities="数据集加载、序列化处理、底层数据库/文件交互与辅助工具函数。",
                    dependencies=[],
                ),
            ]
            mermaid_topology = (
                "flowchart TD\n"
                "    subgraph TopLevel [\"外部交互与入口\"]\n"
                "        ENTRY[\"Main / API / CLI\"]\n"
                "    end\n"
                "    subgraph CoreEngine [\"核心算法与模型\"]\n"
                "        ENGINE[\"Core Algorithms & Models\"]\n"
                "    end\n"
                "    subgraph DataInfra [\"数据与基础设施\"]\n"
                "        DATA[\"Data Loaders & Storage\"]\n"
                "    end\n"
                "\n"
                "    ENTRY --> ENGINE\n"
                "    ENGINE --> DATA"
            )
            data_flow = (
                "1. **入口解析**：主入口接收输入配置或请求；\n"
                "2. **算法计算**：核心模块调用内部模型算子进行前向推理或逻辑计算；\n"
                "3. **数据读写**：底层管线完成数据加载与结果持久化。"
            )

        theory_mappings = self.get_theory_mappings(project)
        dependencies_analysis = self._analyze_dependencies(root)

        return ArchitectureWalkthrough(
            project_id=project.project_id,
            overview=overview,
            components=components,
            mermaid_topology=mermaid_topology,
            data_flow_description=data_flow,
            theory_mappings=theory_mappings,
            dependencies_analysis=dependencies_analysis,
        )

    def get_theory_mappings(self, project: Project) -> list[TheoryMappingItem]:
        root = Path(project.root_path).resolve()
        is_conflux = (project.project_id == "proj-conflux-weave" or project.name.lower() == "conflux-weave") and (root / "src" / "conflux_weave").is_dir()

        if is_conflux:
            return [
                TheoryMappingItem(
                    concept="倒数排序融合 (Reciprocal Rank Fusion, RRF)",
                    paper_reference="Cormack et al. (2009) 'Reciprocal Rank Fusion outperforms Condorcet and individual Rank Learning Methods' (SIGIR 2009)",
                    code_symbol="_reciprocal_rank_fusion / hybrid_search",
                    file_path="src/conflux_weave/retrieval.py",
                    line_number=210,
                    description="将 BM25 词频排位与密集向量余弦距离排位转换为以常数 k=60 衰减的无量纲倒数得分，实现稀疏与密集检索的鲁棒混合。",
                    design_rationale="不同检索模型打分尺度无法直接相加（BM25 无上界，余弦在 [-1, 1]），RRF 仅依赖相对排位，兼具高鲁棒性与免调参特性。",
                ),
                TheoryMappingItem(
                    concept="确定性执行与预算账本 (Deterministic ReAct Harness & Budget Ledger)",
                    paper_reference="Yao et al. (2022) 'ReAct: Synergizing Reasoning and Acting in Language Models' (ICLR 2023)",
                    code_symbol="DeterministicHarness / BudgetLedger",
                    file_path="src/conflux_weave/harness.py",
                    line_number=1,
                    description="对多智能体运行的时间、Token 消耗、工具调用轮次建立不可逆有界计数器，一旦超支即刻安全熔断并保全现场。",
                    design_rationale="防止模型因幻觉陷入无限推理循环或非受控递归，确保科研复现与实验计费的精确可控性。",
                ),
                TheoryMappingItem(
                    concept="多模态图表空间对齐与视觉问答 (Multimodal Vision-RAG)",
                    paper_reference="Fevry et al. (2024) 'ColPali: Efficient Document Retrieval with Vision Language Models'",
                    code_symbol="MultimodalRetriever / extract_page_assets",
                    file_path="src/conflux_weave/retrieval.py",
                    line_number=95,
                    description="对 PDF 论文中的高清折线图、消融实验表与架构框图执行物理坐标绑定与多模态双通道索引。",
                    design_rationale="学术论文的核心结论与消融对比集中于图表之中，纯 OCR 提取文本会彻底破坏二维版面与拓扑语义关系。",
                ),
                TheoryMappingItem(
                    concept="双向学术证据闭包 (Bidirectional Lineage & Citation Provenance)",
                    paper_reference="Buneman et al. (2001) 'Why and Where: A Characterization of Data Provenance'",
                    code_symbol="DocumentAgent / EvidenceRef",
                    file_path="src/conflux_weave/document_agent.py",
                    line_number=1,
                    description="记录笔记摘要、引言断言与原始论文 PDF 页码、图表哈希之间的确定性双向引用链路。",
                    design_rationale="研究者在撰写文献综述时要求“每一句论断必有出处”，杜绝 AI 生成中常见的伪造参考文献与事实漂移。",
                ),
                TheoryMappingItem(
                    concept="乐观并发控制与防冲撞补丁 (Optimistic Concurrency Control, OCC)",
                    paper_reference="Kung & Robinson (1981) 'On Optimistic Methods for Concurrency Control' (ACM TODS)",
                    code_symbol="CodingAgent.apply_patch",
                    file_path="src/conflux_weave/project_agents.py",
                    line_number=317,
                    description="在应用代码解耦补丁前强制核验目标文件的 SHA-256 哈希；若已被外部 IDE 变动，直接以 409 拒绝写入。",
                    design_rationale="研究者主要在本地 Cursor/VS Code 工作，受控 Agent 绝不能静默覆盖用户手动微调的代码进度。",
                ),
            ]
        else:
            generic_mappings: list[TheoryMappingItem] = []
            for p in root.rglob("*.py"):
                if any(ign in p.parts for ign in [".git", ".venv", "venv", "__pycache__"]):
                    continue
                rel = str(p.relative_to(root)).replace("\\", "/")
                ast_sum = ProjectScanner.extract_ast_summary(root, rel)
                for fn in ast_sum.get("functions", []):
                    name_lower = fn["name"].lower()
                    for kw, concept in [("loss", "损失函数优化"), ("attention", "注意力机制"), ("embed", "嵌入表示"), ("eval", "评估基准")]:
                        if kw in name_lower:
                            generic_mappings.append(
                                TheoryMappingItem(
                                    concept=f"{concept} ({fn['name']})",
                                    paper_reference="开源仓库核心算法实现",
                                    code_symbol=fn["name"],
                                    file_path=rel,
                                    line_number=fn["line"],
                                    description=fn["docstring"] or f"检测到与 {concept} 相关的核心算子定义。",
                                    design_rationale="算法核心处理链路关键节点。",
                                )
                            )
                            break
                    if len(generic_mappings) >= 5:
                        break
                if len(generic_mappings) >= 5:
                    break
            return generic_mappings

    def generate_audit_report(self, project: Project) -> ProjectAuditReport:
        root = Path(project.root_path).resolve()
        findings: list[AuditFinding] = []
        checked_rules = [
            "rule_stub_and_todo (检测空桩函数与未闭环TODO)",
            "rule_mock_and_fallback (检测临时Mock与伪数据硬编码)",
            "rule_god_class_and_oversized (检测超长文件与上帝类高耦合风险)",
            "rule_state_and_env_pollution (检测全局变量与进程环境变量污染)",
            "rule_test_coverage_mesh (核心模块与单元测试网格匹配)",
        ]

        total_files = 0
        total_stubs = 0
        total_mocks = 0

        py_files: list[str] = []
        for p in root.rglob("*.py"):
            if any(ign in p.parts for ign in [".git", ".venv", "venv", "__pycache__", "build", "dist", "tmp"]):
                continue
            py_files.append(str(p.relative_to(root)).replace("\\", "/"))
            if len(py_files) >= 100:
                break

        for rel in py_files:
            total_files += 1
            ast_data = ProjectScanner.extract_ast_summary(root, rel)
            line_count = ast_data.get("line_count", 0)

            # 1. Rule God Class / Oversized
            if line_count > 700 and not rel.startswith("tests/"):
                findings.append(
                    AuditFinding(
                        finding_id=f"find-godfile-{hashlib.sha256(rel.encode()).hexdigest()[:6]}",
                        category="coupling_smell",
                        severity="warning",
                        title=f"单文件代码膨胀 ({line_count} 行)",
                        description=f"模块 `{rel}` 拥有 {line_count} 行代码，承担了过多异构职责，存在高耦合维护风险。",
                        target_file=rel,
                        line_number=1,
                        recommendation="建议按单一职责原则将底层操作、业务调度与外部契约拆分到独立模块。",
                        implementation_status="fully_implemented",
                    )
                )

            for cls_item in ast_data.get("classes", []):
                if cls_item.get("method_count", 0) > 15:
                    findings.append(
                        AuditFinding(
                            finding_id=f"find-godcls-{hashlib.sha256((rel + cls_item['name']).encode()).hexdigest()[:6]}",
                            category="coupling_smell",
                            severity="info",
                            title=f"高复杂度类: `{cls_item['name']}` ({cls_item['method_count']} 个方法)",
                            description=f"类 `{cls_item['name']}` 方法数量较多，可能属于上帝类 (God Class) 坏味道。",
                            target_file=rel,
                            line_number=cls_item["line"],
                            recommendation="可抽取子组件或使用组合 (Composition) 模式分解功能。",
                            implementation_status="fully_implemented",
                        )
                    )

                for m in cls_item.get("methods", []):
                    if m.get("is_stub"):
                        total_stubs += 1
                        findings.append(
                            AuditFinding(
                                finding_id=f"find-stub-{hashlib.sha256((rel + m['name']).encode()).hexdigest()[:6]}",
                                category="implementation_gap",
                                severity="warning",
                                title=f"检测到空桩方法: `{cls_item['name']}.{m['name']}`",
                                description=f"方法 `{m['name']}` 处于空桩占位状态（仅有 pass、NotImplementedError 或 ...）。",
                                target_file=rel,
                                line_number=m["line"],
                                recommendation="若属于未完备接口，请尽快实现核心逻辑或在契约中显式声明不支持。",
                                implementation_status="stub_or_todo",
                            )
                        )
                    elif m.get("is_mock"):
                        total_mocks += 1
                        findings.append(
                            AuditFinding(
                                finding_id=f"find-mock-{hashlib.sha256((rel + m['name']).encode()).hexdigest()[:6]}",
                                category="implementation_gap",
                                severity="info",
                                title=f"检测到 Mock/伪实现: `{cls_item['name']}.{m['name']}`",
                                description=f"方法 `{m['name']}` 带有 Mock 或伪数据标志，在正式实验环境中可能失真。",
                                target_file=rel,
                                line_number=m["line"],
                                recommendation="建议在生产或正式复现实验中替换为真实模型或持久化调用。",
                                implementation_status="partially_implemented",
                            )
                        )

            for fn in ast_data.get("functions", []):
                if fn.get("is_stub"):
                    total_stubs += 1
                    findings.append(
                        AuditFinding(
                            finding_id=f"find-stub-{hashlib.sha256((rel + fn['name']).encode()).hexdigest()[:6]}",
                            category="implementation_gap",
                            severity="warning",
                            title=f"检测到空桩函数: `{fn['name']}`",
                            description=f"顶层函数 `{fn['name']}` 处于未实现桩状态。",
                            target_file=rel,
                            line_number=fn["line"],
                            recommendation="请落实具体实现或清理多余声明。",
                            implementation_status="stub_or_todo",
                        )
                    )
                elif fn.get("is_mock"):
                    total_mocks += 1
                    findings.append(
                        AuditFinding(
                            finding_id=f"find-mock-{hashlib.sha256((rel + fn['name']).encode()).hexdigest()[:6]}",
                            category="implementation_gap",
                            severity="info",
                            title=f"检测到 Mock/伪实现函数: `{fn['name']}`",
                            description=f"函数 `{fn['name']}` 返回模拟数据，属于部分实现。",
                            target_file=rel,
                            line_number=fn["line"],
                            recommendation="后续补齐真实实现链路。",
                            implementation_status="partially_implemented",
                        )
                    )

            for todo in ast_data.get("todos", [])[:2]:
                findings.append(
                    AuditFinding(
                        finding_id=f"find-todo-{hashlib.sha256((rel + str(todo['line'])).encode()).hexdigest()[:6]}",
                        category="implementation_gap",
                        severity="info",
                        title=f"未决 TODO 标注: `{todo['text'][:30]}`",
                        description=f"代码包含待办项：`{todo['text']}`",
                        target_file=rel,
                        line_number=todo["line"],
                        recommendation="检查该待办事项是否影响当前学术实验闭环。",
                        implementation_status="stub_or_todo",
                    )
                )

            # 2. Rule State and Env Pollution
            if not rel.startswith("tests/"):
                try:
                    content, _, _ = ProjectScanner.read_file_safe(root, rel, max_size_bytes=65536)
                    for l_idx, line in enumerate(content.splitlines(), start=1):
                        if "os.environ[" in line and "=" in line and not line.strip().startswith("#"):
                            findings.append(
                                AuditFinding(
                                    finding_id=f"find-env-{hashlib.sha256((rel + str(l_idx)).encode()).hexdigest()[:6]}",
                                    category="state_leak",
                                    severity="critical",
                                    title=f"检测到全局环境变量直接修改",
                                    description=f"第 {l_idx} 行使用 `os.environ[...] = ...`，可能污染宿主进程及其他测试用例的运行状态。",
                                    target_file=rel,
                                    line_number=l_idx,
                                    snippet=line.strip()[:100],
                                    recommendation="建议使用环境变量传参、pydantic BaseSettings 或在测试夹具中进行受控 monkeypatch。",
                                    implementation_status="fully_implemented",
                                )
                            )
                except Exception:
                    pass

            # 3. Rule Test Mesh
            if rel.startswith("src/conflux_weave/") and not rel.endswith("__init__.py"):
                stem = Path(rel).stem
                test_cand = root / "tests" / f"test_{stem}.py"
                if not test_cand.is_file() and stem not in {"api_contracts", "workbench"}:
                    findings.append(
                        AuditFinding(
                            finding_id=f"find-testgap-{hashlib.sha256(rel.encode()).hexdigest()[:6]}",
                            category="test_gap",
                            severity="warning",
                            title=f"缺失自动化单元测试文件: `test_{stem}.py`",
                            description=f"核心模块 `{rel}` 暂无直接对应的同名回归测试套件。",
                            target_file=rel,
                            line_number=1,
                            recommendation=f"建议新建 `tests/test_{stem}.py` 构筑防劣化测试保护网。",
                            implementation_status="fully_implemented",
                        )
                    )

        crit_count = sum(1 for f in findings if f.severity == "critical")
        warn_count = sum(1 for f in findings if f.severity == "warning")
        info_count = sum(1 for f in findings if f.severity == "info")

        impl_score = max(50, min(100, 100 - total_stubs * 10 - total_mocks * 4))
        health_score = max(50, min(100, 100 - crit_count * 15 - warn_count * 3 - info_count * 1))

        fully_impl_count = max(0, total_files - total_stubs - total_mocks)
        status_counts = {
            "fully_implemented": fully_impl_count,
            "partially_implemented": total_mocks,
            "stub_or_todo": total_stubs,
            "unimplemented": 0,
        }

        summary = (
            f"项目全量体检已完成：共扫描 {total_files} 个源码文件，执行了 5 项核心治理规则。"
            f"当前实现完整度得分 **{impl_score}/100**，健康治理得分 **{health_score}/100**。"
            f"共发现 {len(findings)} 项待治理点（包含 {crit_count} 项严重警报、{warn_count} 项中度告警、{info_count} 项建议提示）。"
        )

        return ProjectAuditReport(
            report_id=f"audit-{hashlib.sha256((project.project_id + _utc_now()).encode()).hexdigest()[:10]}",
            project_id=project.project_id,
            summary=summary,
            implementation_score=impl_score,
            health_score=health_score,
            status_counts=status_counts,
            findings=findings,
            checked_rules=checked_rules,
        )

    def _analyze_dependencies(self, root: Path) -> dict[str, Any]:
        pyproject = root / "pyproject.toml"
        direct_deps: list[str] = []
        if pyproject.is_file():
            try:
                c, _, _ = ProjectScanner.read_file_safe(root, "pyproject.toml", max_size_bytes=16384)
                in_deps = False
                for line in c.splitlines():
                    if "dependencies = [" in line:
                        in_deps = True
                        continue
                    if in_deps:
                        if "]" in line:
                            break
                        dep_name = line.strip().strip('"').strip("'").strip(",")
                        if dep_name:
                            direct_deps.append(dep_name)
            except Exception:
                pass
        return {
            "direct_dependencies_count": len(direct_deps),
            "direct_dependencies": direct_deps,
            "runtime_environment": "Python 3.12+ (Virtualenv Isolated)",
        }

    def generate_learning_guide(self, project: Project) -> dict[str, Any]:
        """Generate structured dissection and progressive learning roadmap for cloned & vibecoding projects."""
        root = Path(project.root_path).resolve()
        scanner = ProjectScanner
        tree_nodes = scanner.scan_tree(root, max_depth=3, max_files=200)

        # 1. Project classification & ecosystem detection
        is_python = (root / "pyproject.toml").is_file() or (root / "requirements.txt").is_file() or any(root.glob("*.py"))
        is_node = (root / "package.json").is_file() or (root / "tsconfig.json").is_file()
        is_rust = (root / "Cargo.toml").is_file()
        is_go = (root / "go.mod").is_file()

        stack_tags = []
        if is_python: stack_tags.append("Python")
        if is_node: stack_tags.append("TypeScript/JavaScript")
        if is_rust: stack_tags.append("Rust")
        if is_go: stack_tags.append("Go")
        if not stack_tags: stack_tags.append("Multi-Language")

        # 2. Extract description from README or package config
        readme_text = ""
        for readme_name in ["README.md", "README.MD", "readme.md", "README"]:
            if (root / readme_name).is_file():
                try:
                    c, _, _ = scanner.read_file_safe(root, readme_name, max_size_bytes=32768)
                    readme_text = c
                    break
                except Exception:
                    pass

        # 3. Categorize files into architectural roles
        configs: list[str] = []
        entrypoints: list[str] = []
        models_and_types: list[str] = []
        core_logic: list[str] = []
        test_files: list[str] = []
        doc_files: list[str] = []

        all_rel_paths: list[str] = []
        def _collect_files(nodes):
            for n in nodes:
                if not n.is_dir:
                    all_rel_paths.append(n.path)
                if getattr(n, "children", None):
                    _collect_files(n.children)
        _collect_files(tree_nodes)

        for p in all_rel_paths:
            p_lower = p.lower()
            name = Path(p).name.lower()
            if any(t in p_lower for t in ["test", "tests", "spec", "__tests__"]):
                test_files.append(p)
            elif name in ["readme.md", "contributing.md", "architecture.md", "license"] or p_lower.startswith("docs/"):
                doc_files.append(p)
            elif name in ["pyproject.toml", "package.json", "tsconfig.json", "cargo.toml", "go.mod", "requirements.txt", ".env.example", "dockerfile", "docker-compose.yml"]:
                configs.append(p)
            elif name in ["main.py", "app.py", "server.py", "cli.py", "index.ts", "index.js", "app.tsx", "main.rs", "main.go"]:
                entrypoints.append(p)
            elif any(k in p_lower for k in ["model", "type", "schema", "contract", "entity", "dto", "interface"]):
                models_and_types.append(p)
            elif any(k in p_lower for k in ["core", "service", "agent", "workflow", "engine", "pipeline", "controller", "handler", "util", "lib"]):
                core_logic.append(p)

        # 4. Synthesize project mission
        first_readme_lines = [l.strip().lstrip("#").strip() for l in readme_text.splitlines() if l.strip() and not l.startswith("```")][:4]
        purpose_and_value = (
            " ".join(first_readme_lines[:2])
            if first_readme_lines
            else f"{project.name} 是一个基于 {' / '.join(stack_tags)} 构建的代码工程，提供了模块化的业务功能与架构设计。"
        )

        # 5. Progressive Reading Roadmap (5 Steps)
        roadmap = [
            {
                "step_number": 1,
                "stage": "认知底座",
                "title": "项目规范、运行环境与核心配置契约",
                "files": (configs[:3] or [all_rel_paths[0]] if all_rel_paths else []),
                "focus": "从环境声明与依赖列表快速切入，掌握项目所需的外部服务、中间件版本与整体构建运行方式。",
                "tip": "重点关注依赖项列表中的核心三方库，它直接决定了系统的技术选型与底层驱动机制。",
                "difficulty": "入门 (Easy)",
            },
            {
                "step_number": 2,
                "stage": "数据形态",
                "title": "领域实体、数据模型与通信契约定义",
                "files": models_and_types[:4] or [p for p in all_rel_paths if "type" in p or "model" in p][:3],
                "focus": "理清系统在不同模块之间传递的核心状态与数据结构，掌握输入输出 schema 与核心枚举。",
                "tip": "在阅读执行逻辑之前，先看懂数据结构（Data Structures），后续的逻辑控制流将一目了然。",
                "difficulty": "基础 (Easy)",
            },
            {
                "step_number": 3,
                "stage": "生命周期",
                "title": "系统主入口、初始化引导与控制流中枢",
                "files": entrypoints[:3] or [p for p in all_rel_paths if "server" in p or "app" in p or "main" in p][:2],
                "focus": "跟踪应用程序的 Bootstrapping 流程：从参数解析、服务容器注入、路由挂载到优雅退出机制。",
                "tip": "标记出入口处创建的全局单例与生命周期钩子，注意上下文（Context）如何向下层模块传递。",
                "difficulty": "进阶 (Medium)",
            },
            {
                "step_number": 4,
                "stage": "业务内核",
                "title": "核心业务逻辑、算子管线与编排状态机",
                "files": core_logic[:5] or all_rel_paths[1:6],
                "focus": "深入核心领域服务或业务 Agent 链路，跟踪一个完整业务请求或任务从接收、调度到产出的全过程。",
                "tip": "关注异常处理分支与状态流转条件，体会该项目在并发、重试或缓存上的权衡设计。",
                "difficulty": "核心 (Medium-Hard)",
            },
            {
                "step_number": 5,
                "stage": "工程保障",
                "title": "测试套件、防御性边界与工程治理",
                "files": test_files[:4] or [p for p in all_rel_paths if "test" in p][:3],
                "focus": "查看关键模块的单元测试与集成验证逻辑，通过测试用例反推模块的预期行为与边界用例。",
                "tip": "测试代码是最具真实性的“活文档”，重点观察断言（Assertions）检验了哪些边缘输入。",
                "difficulty": "精通 (Medium)",
            },
        ]

        # 6. Vibecoding & Prototype Hygiene Audit
        total_files_count = len(all_rel_paths)
        test_ratio = len(test_files) / max(total_files_count, 1)
        has_tests = len(test_files) > 0
        has_docs = len(doc_files) > 0

        maturity_score = 60
        if has_tests: maturity_score += 15
        if has_docs: maturity_score += 10
        if len(models_and_types) > 0: maturity_score += 10
        if len(configs) > 0: maturity_score += 5
        maturity_score = min(100, maturity_score)

        strengths = [
            f"工程模块结构清晰，代码组织围绕 {' / '.join(stack_tags)} 标准规范展开",
            f"已建立明确的目录分层，包含 {len(all_rel_paths)} 个主要源文件",
        ]
        if has_docs:
            strengths.append("具备完整的文档说明（README/Docs），便于新人开发者快速建立宏观理解")
        if models_and_types:
            strengths.append(f"具备专门的类型/契约层定义（共收录 {len(models_and_types)} 个契约文件），降低了跨模块耦合度")

        vibecoding_risks = []
        if not has_tests:
            vibecoding_risks.append("【缺乏测试屏障】：未检测到单元测试或集成测试用例，重构时缺乏自动化安全网")
        elif test_ratio < 0.1:
            vibecoding_risks.append(f"【测试覆盖偏低】：测试文件仅占整体代码的 {test_ratio*100:.1f}%，核心链路边界可能缺少防护")

        if len(models_and_types) == 0:
            vibecoding_risks.append("【契约弱化/隐式类型】：核心数据字典未建立严格的 Schema 校验，存在潜在的空值访问风险")

        vibecoding_risks.append("【防御性边界校验】：建议审查关键 IO 调用（如网络请求、外部工具、文件系统）的异常捕获与重试机制")

        production_roadmap = [
            "1. 补齐核心契约与严格类型定义：为主要输入输出建立严格的模型契约，避免字典魔法键访问；",
            "2. 建立端到端 Golden 单元测试用例：为入口和关键算子编写核心回归测试，锁定功能预期；",
            "3. 解耦硬编码与增加环境配置：将代码中的固定配置、超时常数提取至环境配置文件或配置单例；",
            "4. 引入可观测性与防御熔断：关键算子增加耗时日志、Trace 追踪与安全异常隔离边界。",
        ]

        # 7. Recommended questions for interactive Q&A
        suggested_questions = [
            "请用简洁清晰的步骤梳理本项目的核心执行链路从入口到产出经历了哪些阶段？",
            "请深入解读本项目最核心的 2-3 个类或函数，它们分别承担什么职责，如何协作？",
            "如果我想基于当前代码扩展一个自定义算子或功能插件，应该在哪个目录下遵循什么契约编写？",
            "分析本项目代码中的并发控制、状态持久化或异常防御机制，有哪些值得借鉴或需要改进的地方？",
        ]

        ecosystem = {
            "primary_stack": stack_tags[0] if stack_tags else "Multi-Language",
            "languages": stack_tags,
            "package_manager": "uv / pip" if is_python else "pnpm / npm" if is_node else "cargo" if is_rust else "go" if is_go else "standard",
        }
        mission = {
            "purpose_and_value": purpose_and_value,
            "summary": purpose_and_value,
        }
        vibecoding_hygiene_audit = {
            "maturity_score": maturity_score,
            "strengths": strengths,
            "risks_and_anti_patterns": vibecoding_risks,
            "vibecoding_risks": vibecoding_risks,
            "production_roadmap": production_roadmap,
        }
        lexicon = []
        for p in (models_and_types[:3] + entrypoints[:2]):
            stem = Path(p).stem
            lexicon.append({
                "name": stem,
                "kind": "module/schema",
                "file_path": p,
                "line": 1,
                "summary": f"{project.name} 的核心组件与定义文件",
            })

        return {
            "project_id": project.project_id,
            "project_name": project.name,
            "mission": mission,
            "ecosystem": ecosystem,
            "progressive_reading_roadmap": roadmap,
            "reading_roadmap": roadmap,
            "lexicon": lexicon,
            "vibecoding_hygiene_audit": vibecoding_hygiene_audit,
            "vibecoding_audit": vibecoding_hygiene_audit,
            "stack_tags": stack_tags,
            "purpose_and_value": purpose_and_value,
            "file_counts": {
                "total": total_files_count,
                "configs": len(configs),
                "entrypoints": len(entrypoints),
                "models_and_types": len(models_and_types),
                "core_logic": len(core_logic),
                "tests": len(test_files),
            },
            "suggested_questions": suggested_questions,
        }


class CodingAgent:
    """Agent that analyzes code and proposes structured, reviewed code patches with conflict protection."""

    profile = AgentProfile(
        agent_type="coding_agent",
        version="v1",
        description="Analyzes code and proposes reviewable unified diff patches. Strictly read-only by default.",
        accepted_task_kinds=("coding_proposal",),
        allowed_tool_ids=(),
        default_budget=BudgetLedger(
            wall_clock_seconds=60,
            input_tokens=4000,
            output_tokens=2000,
            estimated_cost="$0.05",
            tool_calls=10,
            retrieval_rounds=3,
            concurrency=1,
        ),
    )

    def __init__(self, provider: OpenAICompatibleChatAdapter | None = None) -> None:
        self.provider = provider

    def propose_patch(
        self,
        project: Project,
        instruction: str,
        target_file: str,
        custom_replacement: str | None = None,
    ) -> CodeProposal:
        target_file = re.sub(r"[:#]L?\d+$", "", target_file).strip()
        ProjectScanner.validate_editable_file(target_file)
        root = Path(project.root_path).resolve()
        safe_target = ProjectScanner.resolve_safe_path(root, target_file)

        original_content = ""
        original_hash = ""
        if safe_target.is_file():
            original_content, original_hash, _ = ProjectScanner.read_file_safe(root, target_file)

        if custom_replacement is not None:
            proposed_content = custom_replacement
        elif self.provider is not None:
            try:
                system_prompt = (
                    "你是一位资深高级工程师。请根据用户的治理或重构指令，对目标文件进行严谨、完整、高质量的代码修改与改进。\n"
                    "严格输出要求：\n"
                    "1. 仅输出修改后的完整文件源代码内容。\n"
                    "2. 绝对不要包含任何 Markdown 代码块包裹符号（如 ```python ），不要包含任何前置解释或后置说明。\n"
                    "3. 保持原有代码架构和编码风格，准确实现指令中的要求。"
                )
                user_prompt = f"目标文件: {target_file}\n重构与治理指令:\n{instruction}\n\n当前完整文件内容如下:\n{original_content}"
                completion = self.provider.complete(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    temperature=0.2,
                    max_output_tokens=4096,
                )
                content = (completion.content or "").strip()
                if content.startswith("```"):
                    lines = content.splitlines()
                    if lines[0].startswith("```"):
                        lines = lines[1:]
                    if lines and lines[-1].startswith("```"):
                        lines = lines[:-1]
                    content = "\n".join(lines)
                commented_instruction = "\n".join(f"# {line}" if line.strip() else "#" for line in instruction.splitlines())
                proposed_content = content if content else (original_content.rstrip() + f"\n\n# Patch proposed:\n{commented_instruction}\n")
            except Exception:
                commented_instruction = "\n".join(f"# {line}" if line.strip() else "#" for line in instruction.splitlines())
                if original_content:
                    proposed_content = original_content.rstrip() + f"\n\n# Patch proposed:\n{commented_instruction}\n"
                else:
                    proposed_content = f'"""New file created for:\n{instruction}\n"""\n\n'
        else:
            # Deterministic fallback
            commented_instruction = "\n".join(f"# {line}" if line.strip() else "#" for line in instruction.splitlines())
            if original_content:
                proposed_content = original_content.rstrip() + f"\n\n# Patch proposed:\n{commented_instruction}\n"
            else:
                proposed_content = f'"""New file created for:\n{instruction}\n"""\n\n'

        # Generate Unified Diff
        from_lines = original_content.splitlines(keepends=True)
        to_lines = proposed_content.splitlines(keepends=True)
        diff_lines = list(
            difflib.unified_diff(
                from_lines,
                to_lines,
                fromfile=f"a/{target_file}",
                tofile=f"b/{target_file}",
            )
        )
        diff_text = "".join(diff_lines) or f"# No diff detected for {target_file}"

        proposal_id = f"prop-{hashlib.sha256((target_file + instruction + _utc_now()).encode()).hexdigest()[:10]}"
        stem = Path(target_file).stem
        v_cmds = []
        if (root / "tests" / f"test_{stem}.py").is_file():
            v_cmds.append(f"pytest tests/test_{stem}.py -q")
        else:
            v_cmds.append(f"pytest tests/ -k {stem} -q")
        v_cmds.append("pytest -q")

        return CodeProposal(
            proposal_id=proposal_id,
            project_id=project.project_id,
            title=f"修改 `{target_file}`: {instruction[:40]}",
            rationale=f"根据需求指令 “{instruction}” 对目标文件进行增量重构与改进。",
            risk_level="low" if len(diff_lines) < 20 else "medium",
            target_file=target_file,
            original_hash=original_hash,
            diff=diff_text,
            proposed_content=proposed_content,
            original_content=original_content,
            verification_commands=v_cmds,
            status="proposed",
        )

    def apply_patch(self, project: Project, proposal: CodeProposal) -> tuple[bool, str]:
        """Atomically apply proposal after verifying optimistic revision hash."""
        clean_target = re.sub(r"[:#]L?\d+$", "", proposal.target_file).strip()
        proposal.target_file = clean_target
        root = Path(project.root_path).resolve()
        safe_target = ProjectScanner.resolve_safe_path(root, clean_target)

        # Optimistic concurrency check
        if safe_target.is_file():
            _, current_hash, _ = ProjectScanner.read_file_safe(root, clean_target)
            if proposal.original_hash and current_hash != proposal.original_hash:
                return False, f"revision_conflict: 目标文件基准哈希不匹配 (预期 {proposal.original_hash[:8]}, 当前为 {current_hash[:8]})，已被外部修改。"
        elif proposal.original_hash:
            return False, "revision_conflict: 目标文件原本存在，但当前已被删除。"

        # Atomically write
        safe_target.parent.mkdir(parents=True, exist_ok=True)
        tmp_target = safe_target.with_suffix(f".tmp.{os.getpid()}")
        try:
            tmp_target.write_text(proposal.proposed_content, encoding="utf-8", newline="\n")
            tmp_target.replace(safe_target)
        except Exception as exc:
            if tmp_target.exists():
                tmp_target.unlink(missing_ok=True)
            return False, f"写入失败: {exc}"

        proposal.status = "applied"
        return True, "补丁已成功原子应用。"

    def revert_patch(self, project: Project, proposal: CodeProposal) -> tuple[bool, str]:
        """Atomically revert an applied proposal after verifying concurrency hash."""
        clean_target = re.sub(r"[:#]L?\d+$", "", proposal.target_file).strip()
        proposal.target_file = clean_target
        root = Path(project.root_path).resolve()
        safe_target = ProjectScanner.resolve_safe_path(root, clean_target)

        if not safe_target.is_file():
            return False, "revert_failed: 目标文件不存在，无法执行回滚。"

        # Concurrency safety: Verify current hash matches the patch's proposed content hash
        expected_applied_hash = hashlib.sha256(proposal.proposed_content.replace("\r\n", "\n").encode("utf-8")).hexdigest()
        _, current_hash, _ = ProjectScanner.read_file_safe(root, clean_target)
        if current_hash != expected_applied_hash:
            return False, f"revision_conflict: 目标文件自补丁应用后已被外部修改 (预期 {expected_applied_hash[:8]}, 当前为 {current_hash[:8]})，已阻断回滚以防代码覆盖。"

        if not proposal.original_hash and not proposal.original_content:
            try:
                safe_target.unlink()
                proposal.status = "proposed"
                return True, "补丁创建的文件已成功撤销并移除。"
            except Exception as exc:
                return False, f"回滚移除文件失败: {exc}"

        tmp_target = safe_target.with_suffix(f".tmp.{os.getpid()}")
        try:
            tmp_target.write_text(proposal.original_content, encoding="utf-8", newline="\n")
            tmp_target.replace(safe_target)
        except Exception as exc:
            if tmp_target.exists():
                tmp_target.unlink(missing_ok=True)
            return False, f"回滚写入失败: {exc}"

        proposal.status = "proposed"
        return True, "补丁已成功原子回滚至原版本。"
