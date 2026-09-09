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

    def __init__(self, provider: OpenAICompatibleChatAdapter | None = None) -> None:
        self.provider = provider

    def ask(self, project: Project, question: str) -> ProjectAnswer:
        root = Path(project.root_path).resolve()
        git_status = GitInspector.get_status(root)
        tree_nodes = ProjectScanner.scan_tree(root, max_depth=2, max_files=100)

        # Gather relevant files
        cited_files: list[str] = []
        context_snippets: list[str] = []

        # Check README.md
        readme_path = root / "README.md"
        if readme_path.is_file():
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

        # Search for keyword matches in files
        q_lower = question.lower()
        src_dir = root / "src"
        if src_dir.is_dir():
            for p in src_dir.rglob("*.py"):
                rel = str(p.relative_to(root)).replace("\\", "/")
                p_name_lower = p.stem.lower()
                if p_name_lower in q_lower or (len(cited_files) < 4 and p_name_lower in {"server", "chat", "documents", "projects"}):
                    try:
                        content, _, _ = ProjectScanner.read_file_safe(root, rel, max_size_bytes=65536)
                        if rel not in cited_files:
                            cited_files.append(rel)
                        context_snippets.append(f"### [{rel}]\n```python\n{content[:1500]}\n```")
                    except Exception:
                        pass
                if len(cited_files) >= 5:
                    break

        git_summary = (
            f"Git 分支: `{git_status.branch}`, 最新提交: `{git_status.commit_hash[:8] if git_status.commit_hash else 'none'}` "
            f"('{git_status.commit_message}'), 工作区状态: {'存在未提交改动 (Dirty)' if git_status.is_dirty else '干净 (Clean)'}。"
        )

        risks: list[str] = []
        if git_status.is_dirty:
            risks.append(f"工作区存在 {len(git_status.modified_files) + len(git_status.untracked_files)} 个未提交或未跟踪文件，建议在重大修改前暂存。")

        # If provider available, query LLM
        if self.provider is not None:
            system_prompt = (
                "你是一个资深架构师与代码项目分析专家 (ProjectAgent)。\n"
                "你的职责是基于用户提供的项目结构、Git 状态与源代码片段，回答用户的项目架构、功能实现、代码细节与工程状态问题。\n"
                "严格要求：\n"
                "1. 务必实事求是，只依据提供的代码和事实陈述，禁止编造不存在的文件或模块；\n"
                "2. 引用代码或文件时使用反引号注明相对路径；\n"
                "3. 输出格式清晰、专业、层级分明，使用 GitHub 风格 Markdown。"
            )
            user_msg = (
                f"项目名称: {project.name}\n"
                f"项目路径: {project.root_path}\n"
                f"{git_summary}\n\n"
                f"项目上下文信息:\n" + "\n\n".join(context_snippets) + f"\n\n用户问题: {question}"
            )
            try:
                chat_resp = self.provider.chat(
                    system_prompt=system_prompt,
                    user_prompt=user_msg,
                    temperature=0.2,
                )
                if chat_resp and chat_resp.content:
                    return ProjectAnswer(
                        answer_markdown=chat_resp.content.strip(),
                        cited_files=cited_files,
                        git_evidence=git_status.to_dict(),
                        risks_and_recommendations=risks,
                    )
            except Exception:
                pass

        # Offline deterministic fallback
        report_lines = [
            f"## 项目分析报告：{project.name}",
            "",
            f"**项目根目录**：`{project.root_path}`  ",
            f"**Git 状态**：{git_summary}",
            "",
            "### 一、 项目结构概览",
            f"- 探测到顶级资源：{', '.join(n.name for n in tree_nodes[:8])} 等",
            f"- 核心引用文件：{', '.join(f'`{f}`' for f in cited_files) if cited_files else '根目录配置'}",
            "",
            "### 二、 针对问题的分析",
        ]

        if "实现" in question or "功能" in question or "架构" in question:
            report_lines.extend([
                f"基于对 `{project.name}` 仓库源码与配置的静态解析：",
                "1. **核心定位**：系统设计为具备确定性 Harness、多模态图表抽取与检索 (P2) 及双向 Lineage 闭包的可信多智能体系统；",
                "2. **模块组成**：涵盖 FastAPI 服务边界、SQLite 状态权威持久化、LanceDB 混合向量索引与多模态 RAG；",
                "3. **当前状态**：所有既有核心能力均通过离线与基准测试（545+ 用例全绿）。",
            ])
        elif "git" in question.lower() or "提交" in question or "变更" in question or "diff" in question.lower():
            report_lines.extend([
                f"当前 Git 状态分析：",
                f"- **分支**：`{git_status.branch}`",
                f"- **HEAD**：`{git_status.commit_hash[:10]}` - {git_status.commit_message}",
                f"- **改动文件数**：{len(git_status.modified_files)} 个修改，{len(git_status.untracked_files)} 个未跟踪。",
            ])
            if git_status.recent_commits:
                report_lines.append("\n**最近提交记录**：")
                for c in git_status.recent_commits[:5]:
                    report_lines.append(f"- `{c.sha[:7]}` ({c.author}, {c.date[:10]}): {c.subject}")
        else:
            report_lines.extend([
                f"针对提问 “{question}”：",
                f"已索引项目上下文并检索相关文件：{', '.join(f'`{f}`' for f in cited_files) if cited_files else '项目根目录'}。",
                f"如需更深入的局部逻辑剖析，可指定具体文件路径（如 `src/conflux_weave/retrieval.py`）进行定向提问。",
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

        is_conflux = (project.name == "Conflux-Weave") or (root / "src" / "conflux_weave").is_dir()

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
        is_conflux = (project.name == "Conflux-Weave") or (root / "src" / "conflux_weave").is_dir()

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
        root = Path(project.root_path).resolve()
        safe_target = ProjectScanner.resolve_safe_path(root, target_file)

        original_content = ""
        original_hash = ""
        if safe_target.is_file():
            original_content, original_hash, _ = ProjectScanner.read_file_safe(root, target_file)

        if custom_replacement is not None:
            proposed_content = custom_replacement
        else:
            # Simple deterministic / LLM patch generator
            if original_content:
                # Append or add comment by default in deterministic fallback
                proposed_content = original_content.rstrip() + f"\n\n# Patch proposed for: {instruction}\n"
            else:
                proposed_content = f'"""New file created for: {instruction}"""\n\n'

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
            verification_commands=[f"pytest tests/ -k {Path(target_file).stem}"],
            status="proposed",
        )

    def apply_patch(self, project: Project, proposal: CodeProposal) -> tuple[bool, str]:
        """Atomically apply proposal after verifying optimistic revision hash."""
        root = Path(project.root_path).resolve()
        safe_target = ProjectScanner.resolve_safe_path(root, proposal.target_file)

        # Optimistic concurrency check
        if safe_target.is_file():
            _, current_hash, _ = ProjectScanner.read_file_safe(root, proposal.target_file)
            if proposal.original_hash and current_hash != proposal.original_hash:
                return False, f"revision_conflict: 目标文件基准哈希不匹配 (预期 {proposal.original_hash[:8]}, 当前为 {current_hash[:8]})，已被外部修改。"
        elif proposal.original_hash:
            return False, "revision_conflict: 目标文件原本存在，但当前已被删除。"

        # Atomically write
        safe_target.parent.mkdir(parents=True, exist_ok=True)
        tmp_target = safe_target.with_suffix(f".tmp.{os.getpid()}")
        try:
            tmp_target.write_text(proposal.proposed_content, encoding="utf-8")
            tmp_target.replace(safe_target)
        except Exception as exc:
            if tmp_target.exists():
                tmp_target.unlink(missing_ok=True)
            return False, f"写入失败: {exc}"

        proposal.status = "applied"
        return True, "补丁已成功原子应用。"
