"""Tech Stack Tutor and Progressive Codebase Walkthrough Guide."""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any

from conflux_weave.project_tutor.spec import (
    CodeWalkthroughStage,
    DependencyKnowledgeCard,
)


KNOWLEDGE_BASE_CARDS: dict[str, dict[str, str]] = {
    "fastapi": {
        "category": "网络通信与服务边界",
        "role_in_project": "构建单进程统一 ASGI API 网关，提供全部 RESTful 接口与 SSE 长连接事件流。",
        "underlying_mechanism": "基于 Starlette 异步事件循环与 Pydantic 强类型声明式验证，底层依赖 uvloop/asyncio 达成极低调度开销。",
        "why_needed": "在单一进程边界内统一交付全部后台服务能力，消除多微服务进程协同的运维与网络复杂性。",
    },
    "pymupdf": {
        "category": "文档分析与多模态视觉处理",
        "role_in_project": "执行高精度 PDF 解析、图表/表格物理坐标定位与多模态切片像素渲染。",
        "underlying_mechanism": "封装高性能 C++ MuPDF 渲染引擎，以直接内存映射（mmap）与矢量光栅化机制提供远超纯 Python 库的处理吞吐。",
        "why_needed": "论文自适应精读依赖原生图表物理切片与正文原地嵌入，必须具备亚毫秒级的高保真图像提取能力。",
    },
    "fitz": {
        "category": "文档分析与多模态视觉处理",
        "role_in_project": "PyMuPDF 核心模块，直接操作 PDF 页面、文本块（Blocks）与内嵌图像（XRef）。",
        "underlying_mechanism": "基于 MuPDF C 库的高速光栅化与拓扑流提取。",
        "why_needed": "快速定位页面中关键 Figure 与 Table 坐标并生成独立 PNG 资产。",
    },
    "lancedb": {
        "category": "嵌入式多模态向量存储",
        "role_in_project": "存储图文嵌入向量与元数据，支持跨模态密集召回与混合检索。",
        "underlying_mechanism": "基于 Apache Arrow 列式内存格式与磁盘原生的 Lance 存储引擎，无需常驻守护进程即可执行高效向量最近邻检索。",
        "why_needed": "满足个人工作台本地优先（Local-First）原则，彻底摆脱对外置 Milvus/Qdrant 数据库集群的容器依赖。",
    },
    "pydantic": {
        "category": "数据契约与强类型校验",
        "role_in_project": "定义 API 数据输入输出模型、参数反序列化与运行时完整性保护。",
        "underlying_mechanism": "核心层采用 Rust (pydantic-core) 编译，提供纳秒级参数校验与类型转换。",
        "why_needed": "保障学术智能体复杂结构化输入输出的确定性，防止因字段漂移导致下游逻辑异常崩溃。",
    },
    "sqlite3": {
        "category": "嵌入式关系存储与状态持久化",
        "role_in_project": "记录 Task、Run、Step、Delivery 等耐用状态机数据与审计日志。",
        "underlying_mechanism": "单文件无服务器轻量级数据库，启用预写日志模式（WAL, Write-Ahead Logging）实现无锁高并发读取与故障自愈。",
        "why_needed": "确保系统具备细粒度断点续跑能力，单机零配置即开即用。",
    },
    "uv": {
        "category": "现代工程构建与环境治理",
        "role_in_project": "管理 Python 项目依赖解析、虚拟环境隔离与锁定文件构建（uv.lock）。",
        "underlying_mechanism": "纯 Rust 实现的高性能包管理工具，多线程并行下载解压与全局缓存硬链接复用。",
        "why_needed": "秒级完成冷启动环境构建与测试执行，彻底消除传统 pip/poetry 的网络阻塞与依赖漂移隐患。",
    },
    "starlette": {
        "category": "轻量级异步 ASGI 核心框架",
        "role_in_project": "提供基础 HTTP/WebSocket 请求处理、中间件流水线与路由基座。",
        "underlying_mechanism": "极简纯异步设计，具备极低内存占用与高并发吞吐。",
        "why_needed": "作为 FastAPI 的底层执行内核，提供稳健的高性能事件循环抽象。",
    },
    "pytest": {
        "category": "自动化测试与质量工程保障",
        "role_in_project": "驱动全栈单元测试、集成测试与长程端到端状态机验收。",
        "underlying_mechanism": "强大的 Fixture 依赖注入与断言重写机制，支持多进程并行与参数化覆盖。",
        "why_needed": "确保系统各模块契约自洽，守住重构与迭代的底线质量门禁。",
    },
    "anyio": {
        "category": "结构化并发与跨异步运行时兼容层",
        "role_in_project": "提供统一的异步任务组（TaskGroups）、信号量与容量限制超时控制。",
        "underlying_mechanism": "抹平 asyncio 与 trio 运行差异，严格遵循结构化并发哲学避免孤儿协程泄漏。",
        "why_needed": "保障长程高并发作业中的超时熔断与取消级联传播。",
    },
    "react": {
        "category": "用户交互与声明式 UI 构建",
        "role_in_project": "构建学术工作台响应式单页面前端（Workbench SPA）。",
        "underlying_mechanism": "虚拟 DOM 与声明式 Hook 状态流，高效协调用户输入与异步流式研报渲染。",
        "why_needed": "交付所见即所得的多模态研报交互、图表下钻与实时代码体检报告。",
    },
    "vite": {
        "category": "现代前端极速构建工具链",
        "role_in_project": "驱动前端开发服务器即时热更新（HMR）与生产静态资源打包。",
        "underlying_mechanism": "开发阶段利用原生 ESM 按需编译，生产构建基于 Rollup 高度优化。",
        "why_needed": "实现秒级前端冷启动与亚秒级热更新，极大压降交互调试周期。",
    },
}


def scan_project_dependencies(
    root_path: Path | str,
    project_name: str = "",
) -> tuple[DependencyKnowledgeCard, ...]:
    """Scan dependency definitions and map to rich pedagogical knowledge cards."""
    path = Path(root_path).resolve()
    cards: list[DependencyKnowledgeCard] = []
    seen: set[str] = set()

    found_packages: set[str] = set()

    # 1. pyproject.toml parsing with tomllib
    pyproject_file = path / "pyproject.toml"
    if pyproject_file.is_file():
        raw_text = pyproject_file.read_text(encoding="utf-8", errors="ignore")
        pseudo_pkgs = {
            "src", "tests", "test", "dist", "build", "var", "tmp", "project",
            "hatchling", "flit_core", "setuptools", "wheel",
            path.name.lower(), (project_name or "").lower(),
        }
        try:
            import tomllib
            toml_data = tomllib.loads(raw_text)
            proj_name = toml_data.get("project", {}).get("name", "").lower()
            if proj_name:
                pseudo_pkgs.add(proj_name)
                pseudo_pkgs.add(proj_name.replace("-", "_"))
                pseudo_pkgs.add(proj_name.replace("_", "-"))

            deps: list[str] = []
            proj_deps = toml_data.get("project", {}).get("dependencies", [])
            if isinstance(proj_deps, list):
                deps.extend(proj_deps)
            opt_deps = toml_data.get("project", {}).get("optional-dependencies", {})
            if isinstance(opt_deps, dict):
                for group in opt_deps.values():
                    if isinstance(group, list):
                        deps.extend(group)
            dep_groups = toml_data.get("dependency-groups", {})
            if isinstance(dep_groups, dict):
                for group in dep_groups.values():
                    if isinstance(group, list):
                        deps.extend(group)
            poetry_deps = toml_data.get("tool", {}).get("poetry", {}).get("dependencies", {})
            if isinstance(poetry_deps, dict):
                deps.extend(poetry_deps.keys())

            for dep in deps:
                if isinstance(dep, str):
                    pkg_cand = re.split(r"[><=~^!;\s\[]", dep)[0].strip().lower()
                    if pkg_cand and pkg_cand not in pseudo_pkgs and len(pkg_cand) >= 2:
                        found_packages.add(pkg_cand)
        except Exception:
            # Fallback to scanning known cards
            pass

        for pkg in KNOWLEDGE_BASE_CARDS:
            if pkg in raw_text.lower() and pkg not in pseudo_pkgs:
                found_packages.add(pkg)

    # 2. requirements.txt / uv.lock / package.json
    for name in ("requirements.txt", "uv.lock", "package.json"):
        f = path / name
        if f.is_file():
            text = f.read_text(encoding="utf-8", errors="ignore").lower()
            for pkg in KNOWLEDGE_BASE_CARDS:
                if pkg in text:
                    found_packages.add(pkg)

    # 3. If no files, add sensible defaults based on codebase
    if not found_packages:
        found_packages = {"fastapi", "sqlite3", "pydantic", "uv"}

    # Curate cards: known packages + dynamically synthesized cards
    for pkg in sorted(found_packages):
        if pkg in seen:
            continue
        seen.add(pkg)
        if pkg in KNOWLEDGE_BASE_CARDS:
            info = KNOWLEDGE_BASE_CARDS[pkg]
            cards.append(
                DependencyKnowledgeCard(
                    package_name=pkg,
                    category=info["category"],
                    role_in_project=info["role_in_project"],
                    underlying_mechanism=info["underlying_mechanism"],
                    why_needed=info["why_needed"],
                )
            )
        elif len(cards) < 15:
            cards.append(
                DependencyKnowledgeCard(
                    package_name=pkg,
                    category="第三方核心工具与计算依赖",
                    role_in_project=f"作为工程关键依赖组件 `{pkg}` 引入，协同支撑核心业务流水线。",
                    underlying_mechanism="通过标准化模块生态引入，提供封装完备的算法与系统调用能力。",
                    why_needed=f"避免重复造轮子，保障系统在处理专用领域计算时的稳定性与健壮度。",
                )
            )

    return tuple(cards)


def build_progressive_walkthrough_stages(
    root_path: Path | str,
    project_name: str = "",
) -> tuple[CodeWalkthroughStage, ...]:
    """Generate top-down progressive code walkthrough guide dynamically locating real project files."""
    path = Path(root_path).resolve()

    def _find_rel_paths(patterns: tuple[str, ...], max_count: int = 4) -> tuple[str, ...]:
        matched: list[str] = []
        ignored_names = {
            ".venv", "venv", "__pycache__", ".git", "node_modules",
            "var", "tmp", ".tmp", "dist", "build", "acceptance",
            ".pytest_cache", ".coverage", "egg-info", "tests", "test",
        }
        for p in patterns:
            for f in path.glob(p):
                if any(x.lower() in ignored_names for x in f.parts):
                    continue
                try:
                    rel = str(f.relative_to(path)).replace("\\", "/")
                except ValueError:
                    continue
                rel_lower = rel.lower()
                if any(bad in rel_lower for bad in ("var/", "acceptance/", "tmp/", "tests/", "dist/", "build/")):
                    continue
                if rel not in matched:
                    matched.append(rel)
                if len(matched) >= max_count:
                    return tuple(matched)
        return tuple(matched)

    # 1. Data contracts files
    stage1_files = _find_rel_paths(
        ("src/**/spec*.py", "src/**/contract*.py", "src/**/model*.py", "src/**/evidence*.py")
    ) or ("src/conflux_weave/api_contracts.py", "src/conflux_weave/evidence.py")

    # 2. Execution harness / core files
    stage2_files = _find_rel_paths(
        ("src/**/harness*.py", "src/**/core*.py", "src/**/runtime*.py", "src/**/orchestrat*.py")
    ) or ("src/conflux_weave/harness/orchestration.py", "src/conflux_weave/runtime/sqlite_task_runs.py")

    # 3. Domain engines
    stage3_files = _find_rel_paths(
        ("src/**/workflow*.py", "src/**/classifier*.py", "src/**/visual_inliner*.py", "src/**/synthesizer*.py")
    ) or ("src/conflux_weave/long_horizon_research/workflow.py", "src/conflux_weave/paper_reading/classifier.py")

    # 4. Entrypoint / Server / Boundaries
    stage4_files = _find_rel_paths(
        ("src/**/server.py", "src/**/main.py", "src/**/app.py", "src/**/provider*.py")
    ) or ("src/conflux_weave/server.py", "src/conflux_weave/provider.py")

    return (
        CodeWalkthroughStage(
            stage_index=1,
            stage_name="第一阶段：数据基石与不可变契约 (Data Foundations & Contracts)",
            core_theme="理解系统的实体定义、状态机契约与不可变证据存储，先看数据结构，不急于探究执行算法。",
            key_files=stage1_files,
            reading_tips="重点关注核心 Dataclass/Model 的字段定义与其不可变性约束（slots=True, frozen=True），体会系统如何通过类型系统拦截无效状态。",
        ),
        CodeWalkthroughStage(
            stage_index=2,
            stage_name="第二阶段：执行回路与确定性沙箱 (Execution Harness & Sandbox)",
            core_theme="探索系统如何将非确定性大模型推理，约束在具有资源配额与副作用拦截的安全回路内。",
            key_files=stage2_files,
            reading_tips="仔细研读配额与状态机控制机制，体会执行沙箱的隔离注入与白盒化可审计性落地。",
        ),
        CodeWalkthroughStage(
            stage_index=3,
            stage_name="第三阶段：领域核心学术引擎 (Domain Core Engines)",
            core_theme="解剖万字深度研报综述生成、论文范式自适应精读以及图表物理切片原地嵌入的核心算法流程。",
            key_files=stage3_files,
            reading_tips="追踪长程递归探索图的多源检索与证据自省补捞环，观察系统如何在复杂任务预算下维系逻辑闭环。",
        ),
        CodeWalkthroughStage(
            stage_index=4,
            stage_name="第四阶段：系统外部边界、熔断容灾与工程治理 (Boundaries, Failover & Governance)",
            core_theme="理解单进程 ASGI 边界的请求路由、多提供商故障转移（Circuit Breaker）与 Skill 沙箱安全计算。",
            key_files=stage4_files,
            reading_tips="观察网络与网关路由状态机流转，以及模型结构化输出在遇到格式损坏时的自动化自愈与修复机制。",
        ),
    )
