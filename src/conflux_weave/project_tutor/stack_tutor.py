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
}


def scan_project_dependencies(root_path: Path | str) -> tuple[DependencyKnowledgeCard, ...]:
    """Scan dependency definitions and map to rich pedagogical knowledge cards."""
    path = Path(root_path).resolve()
    cards: list[DependencyKnowledgeCard] = []
    seen: set[str] = set()

    found_packages: set[str] = set()

    # 1. pyproject.toml
    pyproject_file = path / "pyproject.toml"
    if pyproject_file.is_file():
        text = pyproject_file.read_text(encoding="utf-8", errors="ignore").lower()
        for pkg in KNOWLEDGE_BASE_CARDS:
            if pkg in text:
                found_packages.add(pkg)

    # 2. requirements or uv.lock or package.json
    for name in ("requirements.txt", "uv.lock", "package.json"):
        f = path / name
        if f.is_file():
            text = f.read_text(encoding="utf-8", errors="ignore").lower()
            for pkg in KNOWLEDGE_BASE_CARDS:
                if pkg in text:
                    found_packages.add(pkg)

    # If no files, add sensible defaults based on codebase
    if not found_packages:
        found_packages = {"fastapi", "sqlite3", "pydantic", "uv"}

    for pkg in sorted(found_packages):
        info = KNOWLEDGE_BASE_CARDS.get(pkg)
        if info and pkg not in seen:
            seen.add(pkg)
            cards.append(
                DependencyKnowledgeCard(
                    package_name=pkg,
                    category=info["category"],
                    role_in_project=info["role_in_project"],
                    underlying_mechanism=info["underlying_mechanism"],
                    why_needed=info["why_needed"],
                )
            )

    return tuple(cards)


def build_progressive_walkthrough_stages(
    root_path: Path | str,
    project_name: str = "",
) -> tuple[CodeWalkthroughStage, ...]:
    """Generate top-down progressive code walkthrough guide from foundations to resilience."""
    path = Path(root_path).resolve()

    return (
        CodeWalkthroughStage(
            stage_index=1,
            stage_name="第一阶段：数据基石与不可变契约 (Data Foundations & Contracts)",
            core_theme="理解系统的实体定义、状态机契约与不可变证据存储，先看数据结构，不急于探究执行算法。",
            key_files=(
                "src/conflux_weave/api_contracts.py",
                "src/conflux_weave/evidence.py",
                "src/conflux_weave/runtime/sqlite.py",
                "src/conflux_weave/paper_reading/spec.py",
            ),
            reading_tips="重点关注各个核心 Dataclass 的字段定义与其不可变性约束（slots=True, frozen=True），体会系统如何通过类型系统拦截无效状态。",
        ),
        CodeWalkthroughStage(
            stage_index=2,
            stage_name="第二阶段：执行回路与确定性沙箱 (Execution Harness & Sandbox)",
            core_theme="探索系统如何将非确定性大模型推理，约束在具有资源配额与副作用拦截的安全回路内。",
            key_files=(
                "src/conflux_weave/harness.py",
                "src/conflux_weave/core.py",
                "src/conflux_weave/orchestrator/",
                "src/conflux_weave/runtime/artifacts.py",
            ),
            reading_tips="仔细研读 BudgetLedger 预算账本的计量扣减机制与 ContextBundle 的隔离注入，体会白盒化执行与可审计性的工程落地。",
        ),
        CodeWalkthroughStage(
            stage_index=3,
            stage_name="第三阶段：领域核心学术引擎 (Domain Core Engines)",
            core_theme="解剖万字深度研报综述生成、论文范式自适应精读以及图表物理切片原地嵌入的核心算法流程。",
            key_files=(
                "src/conflux_weave/long_horizon_research/workflow.py",
                "src/conflux_weave/paper_reading/classifier.py",
                "src/conflux_weave/paper_reading/visual_inliner.py",
                "src/conflux_weave/paper_reading/citation_engine.py",
            ),
            reading_tips="重点追踪长程递归搜索图的多源爬取（arXiv/OpenAlex）与证据自省补捞环（Reviewer Loop），观察系统如何在长达 15~20 分钟预算下保持逻辑自洽。",
        ),
        CodeWalkthroughStage(
            stage_index=4,
            stage_name="第四阶段：系统外部边界、熔断容灾与工程治理 (Boundaries, Failover & Governance)",
            core_theme="理解单进程 ASGI 边界的请求路由、多提供商故障转移（Circuit Breaker）与 Skill 沙箱安全计算。",
            key_files=(
                "src/conflux_weave/server.py",
                "src/conflux_weave/provider_pool.py",
                "src/conflux_weave/structured_output.py",
                "src/conflux_weave/compute_tools.py",
            ),
            reading_tips="观察熔断器（CLOSED -> OPEN -> HALF_OPEN）的状态机流转，以及模型结构化输出在遇到格式损坏时的自动化自愈与修复机制。",
        ),
    )
