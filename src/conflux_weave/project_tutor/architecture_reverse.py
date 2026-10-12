"""Reverse Engineer Architecture Motivations and Decision Trade-offs."""

from __future__ import annotations

import ast
import json
import logging
from pathlib import Path
import re
from typing import Any

from conflux_weave.project_tutor.spec import (
    ArchitecturalDecision,
    ArchitectureMotivations,
)
from conflux_weave.provider import OpenAICompatibleChatAdapter

logger = logging.getLogger(__name__)


def reverse_engineer_architecture(
    root_path: Path | str,
    project_name: str = "",
    adapter: OpenAICompatibleChatAdapter | None = None,
) -> ArchitectureMotivations:
    """Analyze codebase to reverse engineer architectural motivations, trade-offs, and data flows."""
    path = Path(root_path).resolve()
    p_name = project_name or path.name

    # 1. Real static inspection of codebase
    py_files = list(path.glob("**/*.py"))[:80]
    imported_modules: set[str] = set()
    has_async_functions = False
    entrypoint_files: list[str] = []

    for f in py_files:
        if any(ignored in f.parts for ignored in (".venv", "venv", "__pycache__", ".git", "var", "dist", "build", "node_modules")):
            continue
        try:
            content = f.read_text(encoding="utf-8", errors="ignore")
            tree = ast.parse(content, filename=str(f))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        imported_modules.add(alias.name.split(".")[0].lower())
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported_modules.add(node.module.split(".")[0].lower())
                elif isinstance(node, ast.AsyncFunctionDef):
                    has_async_functions = True
            if "if __name__ == '__main__':" in content or "@app." in content or "create_app(" in content:
                entrypoint_files.append(f.name)
        except Exception as exc:
            logger.debug("AST parsing skipped for %s: %s", f, exc)

    has_sqlite = "sqlite3" in imported_modules or any(path.glob("**/*.sqlite3")) or any("sqlite" in f.name.lower() for f in py_files)
    has_lancedb = "lancedb" in imported_modules or any("lance" in f.name.lower() for f in py_files)
    has_fastapi = "fastapi" in imported_modules or "starlette" in imported_modules
    has_react = (path / "web").is_dir() or (path / "package.json").is_file()

    # Discover real submodules under src or root
    src_subdirs = []
    src_dir = path / "src"
    search_root = src_dir if src_dir.is_dir() else path
    if src_dir.is_dir():
        pkgs = [
            p for p in src_dir.iterdir()
            if p.is_dir() and not p.name.startswith((".", "_")) and p.name not in ("__pycache__", "tests", "dist", "build", "node_modules", "var")
        ]
        if len(pkgs) == 1 and ((pkgs[0] / "__init__.py").exists() or any(pkgs[0].glob("*.py"))):
            search_root = pkgs[0]

    for p in search_root.iterdir():
        if p.is_dir() and not p.name.startswith((".", "_")) and p.name not in ("__pycache__", "tests", "dist", "build", "node_modules", "var"):
            if any(p.glob("*.py")) or (p / "__init__.py").exists():
                src_subdirs.append(p.name)

    # 2. Synthesize mental model summary dynamically
    has_agent_harness = any("harness" in f.name.lower() or "budget" in f.name.lower() for f in py_files)
    if has_agent_harness or "conflux" in p_name.lower():
        mental_model = (
            f"《{p_name}》采用「以不可变证据为锚、以确定性沙箱为核、以长程异步状态机为骨架」的自主智能体工作台架构。"
            "系统摒弃了粗放式的自由递归调用，将非确定性推理严格约束在声明式工具调用、单进程 ASGI 边界与不可篡改的内容寻址证据存储（CAS）之内。"
        )
    elif has_fastapi and has_react:
        modules_str = "、".join(src_subdirs[:4]) if src_subdirs else "核心业务引擎"
        mental_model = (
            f"《{p_name}》采用现代全栈单体工作台架构：前端以轻量响应式 UI 驱动交互，"
            f"后端基于 FastAPI ASGI 异步事件流驱动领域管道（涵盖 {modules_str}），核心服务间采用面向接口解耦的高内聚模块化设计。"
        )
    else:
        mental_model = (
            f"《{p_name}》采用领域驱动分层架构（Domain-Driven Layered Architecture），"
            "将业务契约、领域核心计算与外部 IO 接口严格隔离，保证业务规则的独立可测性与高内聚。"
        )

    # 3. Derive key architectural decisions dynamically
    decisions: list[ArchitecturalDecision] = []

    # Decision 1: Storage and persistence
    if has_sqlite or has_lancedb:
        storage_tech = "嵌入式 SQLite (WAL 模式)" + (" + LanceDB 多模态向量存储" if has_lancedb else " + Local Artifact Store (CAS 内容寻址)")
        decisions.append(
            ArchitecturalDecision(
                topic="数据基石与状态持久化选型",
                chosen_solution=storage_tech,
                rejected_alternatives=("外部独立分布式数据库集群 (PostgreSQL/MySQL)", "纯内存易失性状态管理"),
                core_rationale="面向个人/本地优先（Local-First）研发环境，消除对复杂外部数据库容器的运维依赖；内容寻址存储保障实验结果与证据不可篡改。",
                tradeoffs_accepted="单机并发写事务存在锁争用上限，需依赖严格的 Worker 租约机制与细粒度行级锁隔离读写。",
            )
        )
    else:
        decisions.append(
            ArchitecturalDecision(
                topic="核心状态流转与持久化选型",
                chosen_solution="轻量化持久化快照与文件系统契约映射",
                rejected_alternatives=("重型分布式状态总线", "无状态纯内存单次请求"),
                core_rationale="兼顾极速启动与执行链路的可重放审计能力，保证在异常中断后可无损热恢复。",
                tradeoffs_accepted="需手动维系状态反序列化版本兼容性与脏数据定期清理策略。",
            )
        )

    # Decision 2: Agent execution harness & boundaries
    if has_agent_harness or "conflux" in p_name.lower():
        decisions.append(
            ArchitecturalDecision(
                topic="智能体执行沙箱回路与副作用隔离",
                chosen_solution="确定性执行沙箱 (Deterministic Harness) + 精细化预算账本 (Budget Ledger)",
                rejected_alternatives=("无约束直接调用 LLM 的黑盒循环", "无预算上限的自由递归智能体框架"),
                core_rationale="学术与工程研究要求输出绝对可信、可验证。通过沙箱拦截未授权副作用，通过预算账本防范死循环与 Token 爆炸。",
                tradeoffs_accepted="引入了显式上下文打包与参数前置/后置校验开销，略微牺牲了无约束智能体的自由发散灵活性。",
            )
        )
    else:
        decisions.append(
            ArchitecturalDecision(
                topic="执行调度回路与并发模型选型",
                chosen_solution="基于 asyncio 异步协程事件循环的高并发非阻塞调度",
                rejected_alternatives=("单线程阻塞式线性执行", "多进程重型 IPC 协同"),
                core_rationale="充分利用现代 I/O 多路复用技术，在单进程边界内实现轻量级协程并发调度与任务流水线编排。",
                tradeoffs_accepted="需严格防范在协程上下文中执行 CPU 密集型同步阻塞调用导致事件循环饥饿。",
            )
        )

    # Decision 3: Long-horizon computing vs latency
    decisions.append(
        ArchitecturalDecision(
            topic="计算预算与调度模型取舍",
            chosen_solution="长程异步 DAG 与细粒度章节级检查点 (Section-level Checkpoint)",
            rejected_alternatives=("低延迟但浅层的单次同步问答", "无断点全局失败重试机制"),
            core_rationale="严肃学术研究与深度代码体检需容忍长程计算深度，章节级断点保障中断后无损继承，彻底消灭计算浪费。",
            tradeoffs_accepted="系统异步状态机复杂度上升，需维系心跳检测与进度持久化事件流。",
        )
    )

    # 4. Generate Dynamic Mermaid Topology based on real components
    engine_nodes = []
    if src_subdirs:
        for idx, sub in enumerate(src_subdirs[:4], 1):
            clean_name = sub.replace("_", " ").title()
            engine_nodes.append(f'        M{idx}["{clean_name} 模块 ({sub})"]:::coreLayer')
    else:
        engine_nodes.append('        CORE["领域核心计算与业务流水线"]:::coreLayer')
    engine_block = "\n".join(engine_nodes)
    engine_targets = " & ".join([f"M{i}" for i in range(1, len(engine_nodes) + 1)]) if src_subdirs else "CORE"

    storage_nodes = []
    if has_sqlite:
        storage_nodes.append('        DB[("SQLite Runtime (WAL Mode)")]:::storeLayer')
    if has_lancedb:
        storage_nodes.append('        VDB[("LanceDB Multimodal Vector Index")]:::storeLayer')
    storage_nodes.append('        CAS[("Artifact Store (CAS Content Addressed)")]:::storeLayer')
    storage_block = "\n".join(storage_nodes)
    storage_targets = " & ".join([("DB" if has_sqlite else "CAS"), ("VDB" if has_lancedb else "CAS")])

    ui_label = "React Workbench / Web UI" if has_react else "CLI Commands / Scripts"
    api_label = "FastAPI ASGI 网关 / Router" if has_fastapi else "Entrypoint Dispatcher"

    topology_mermaid = (
        "```mermaid\n"
        "flowchart TD\n"
        "    classDef uiLayer fill:#1e293b,stroke:#38bdf8,stroke-width:2px,color:#fff;\n"
        "    classDef apiLayer fill:#1e1b4b,stroke:#818cf8,stroke-width:2px,color:#fff;\n"
        "    classDef coreLayer fill:#064e3b,stroke:#34d399,stroke-width:2px,color:#fff;\n"
        "    classDef storeLayer fill:#451a03,stroke:#fb923c,stroke-width:2px,color:#fff;\n\n"
        "    subgraph L1[\"1. 交互呈现层 (Presentation Layer)\"]\n"
        f"        UI[\"{ui_label}\"]:::uiLayer\n"
        "    end\n\n"
        "    subgraph L2[\"2. 通信网关与路由层 (API & Gateway)\"]\n"
        f"        API[\"{api_label}\"]:::apiLayer\n"
        "    end\n\n"
        "    subgraph L3[\"3. 领域核心引擎层 (Domain Core Engines)\"]\n"
        f"{engine_block}\n"
        "    end\n\n"
        "    subgraph L4[\"4. 数据基石与存储层 (Storage & Persistence)\"]\n"
        f"{storage_block}\n"
        "    end\n\n"
        "    UI --> API\n"
        f"    API --> {engine_targets}\n"
        f"    {engine_targets} --> {storage_targets}\n"
        "```"
    )

    # 5. Generate Mermaid Dataflow Sequence
    dataflow_sequence_mermaid = (
        "```mermaid\n"
        "sequenceDiagram\n"
        "    autonumber\n"
        "    actor User as 研究者 / 开发者\n"
        "    participant UI as 交互终端 (UI/CLI)\n"
        "    participant Gateway as 通信网关 (API)\n"
        "    participant Domain as 领域核心引擎\n"
        "    participant Store as 存储持久化层\n\n"
        "    User->>UI: 触发研读 / 架构体检指令\n"
        "    UI->>Gateway: POST 请求提交长程作业\n"
        "    Gateway->>Store: 初始化 Task/Run 状态 (Pending)\n"
        "    Gateway->>Domain: 调度并激活长程工作流执行器\n"
        "    loop 阶段执行与检查点持久化\n"
        "        Domain->>Domain: 执行切面探针分析与参数校验\n"
        "        Domain->>Store: 写入细粒度 Section Checkpoint & Artifacts\n"
        "        Gateway-->>UI: 推送增量执行心跳与结果\n"
        "    end\n"
        "    Domain->>Store: 交付最终研报与体检诊断书\n"
        "    Gateway-->>UI: 标记完成 (Completed 状态)\n"
        "    UI-->>User: 呈现结构化多模态研报与重构建议\n"
        "```"
    )

    # Optional LLM refinement if available
    if adapter is not None:
        try:
            prompt = (
                f"请逆向分析工程《{p_name}》的设计动机，概括核心心智模型（Mental Model）与为什么这样设计的权衡取舍：\n"
                f"工程路径: {str(path)}\n"
                "输出 JSON：{'mental_model_summary': '...'}"
            )
            resp = adapter.chat([{"role": "user", "content": prompt}], temperature=0.1)
            raw = resp.get("content", "").strip()
            if raw.startswith("```"):
                lines = raw.splitlines()
                if lines[0].startswith("```"):
                    lines = lines[1:]
                if lines and lines[-1].startswith("```"):
                    lines = lines[:-1]
                raw = "\n".join(lines).strip()
            parsed = json.loads(raw)
            if parsed.get("mental_model_summary"):
                mental_model = parsed["mental_model_summary"]
        except Exception:
            pass

    return ArchitectureMotivations(
        mental_model_summary=mental_model,
        decisions=tuple(decisions),
        topology_mermaid=topology_mermaid,
        dataflow_sequence_mermaid=dataflow_sequence_mermaid,
    )
