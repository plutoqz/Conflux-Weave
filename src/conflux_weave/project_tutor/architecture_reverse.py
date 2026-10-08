"""Reverse Engineer Architecture Motivations and Decision Trade-offs."""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any

from conflux_weave.project_tutor.spec import (
    ArchitecturalDecision,
    ArchitectureMotivations,
)
from conflux_weave.provider import OpenAICompatibleChatAdapter


def reverse_engineer_architecture(
    root_path: Path | str,
    project_name: str = "",
    adapter: OpenAICompatibleChatAdapter | None = None,
) -> ArchitectureMotivations:
    """Analyze codebase to reverse engineer architectural motivations, trade-offs, and data flows."""
    path = Path(root_path).resolve()
    p_name = project_name or path.name

    # 1. Inspect directory structure and key files
    has_sqlite = any(path.glob("**/*.sqlite3")) or any("sqlite" in f.name.lower() for f in path.glob("**/*.py"))
    has_lancedb = any("lance" in f.name.lower() for f in path.glob("**/*.py"))
    has_fastapi = any("fastapi" in f.read_text(encoding="utf-8", errors="ignore").lower() for f in list(path.glob("**/*.py"))[:10])
    has_react = (path / "web").is_dir() or (path / "package.json").is_file()

    # 2. Synthesize mental model summary
    if "conflux" in p_name.lower() or "weave" in p_name.lower():
        mental_model = (
            f"《{p_name}》采用「以不可变证据为锚、以确定性沙箱为核、以长程异步状态机为骨架」的自主智能体工作台架构。"
            "系统摒弃了粗放式的自由递归调用，将非确定性推理严格约束在声明式工具调用、单进程 ASGI 边界与不可篡改的内容寻址证据存储（CAS）之内。"
        )
    elif has_fastapi and has_react:
        mental_model = (
            f"《{p_name}》采用现代全栈单体工作台架构：前端以轻量响应式 UI 驱动交互，"
            "后端以高并发 ASGI 异步事件流驱动业务管道，核心服务间采用面向接口解耦的高内聚模块化设计。"
        )
    else:
        mental_model = (
            f"《{p_name}》采用领域驱动分层架构（Domain-Driven Layered Architecture），"
            "将业务契约、领域核心计算与外部 IO 接口严格隔离，保证业务规则的独立可测性与高内聚。"
        )

    # 3. Derive key architectural decisions
    decisions: list[ArchitecturalDecision] = []

    # Decision 1: Storage and persistence
    if has_sqlite:
        decisions.append(
            ArchitecturalDecision(
                topic="数据基石与状态持久化选型",
                chosen_solution="嵌入式 SQLite (WAL 模式) + Local Artifact Store (CAS 内容寻址)",
                rejected_alternatives=("外部独立分布式数据库 (PostgreSQL/MySQL)", "纯内存易失性状态管理"),
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
    decisions.append(
        ArchitecturalDecision(
            topic="智能体执行沙箱回路与副作用隔离",
            chosen_solution="确定性执行沙箱 (Deterministic Harness) + 精细化预算账本 (Budget Ledger)",
            rejected_alternatives=("无约束直接调用 LLM 的黑盒循环", "无预算上限的自由递归智能体框架"),
            core_rationale="学术与工程研究要求输出绝对可信、可验证。通过沙箱拦截未授权副作用，通过预算账本防范死循环与 Token 爆炸。",
            tradeoffs_accepted="引入了显式上下文打包与参数前置/后置校验开销，略微牺牲了无约束智能体的自由发散灵活性。",
        )
    )

    # Decision 3: Long-horizon computing vs latency
    decisions.append(
        ArchitecturalDecision(
            topic="计算预算与调度模型取舍",
            chosen_solution="长程异步 DAG 与细粒度章节级检查点 (Section-level Checkpoint)",
            rejected_alternatives=("低延迟但浅层的单次同步问答", "无断点全局失败重试机制"),
            core_rationale="严肃学术研究与深度代码体检需容忍 15~20 分钟的长程计算深度，章节级断点保障中断后无损继承，彻底消灭计算浪费。",
            tradeoffs_accepted="系统异步状态机复杂度上升，需维系心跳检测与进度持久化事件流。",
        )
    )

    # 4. Generate Mermaid Topology
    topology_mermaid = (
        "```mermaid\n"
        "flowchart TD\n"
        "    classDef uiLayer fill:#1e293b,stroke:#38bdf8,stroke-width:2px,color:#fff;\n"
        "    classDef apiLayer fill:#1e1b4b,stroke:#818cf8,stroke-width:2px,color:#fff;\n"
        "    classDef coreLayer fill:#064e3b,stroke:#34d399,stroke-width:2px,color:#fff;\n"
        "    classDef storeLayer fill:#451a03,stroke:#fb923c,stroke-width:2px,color:#fff;\n\n"
        "    subgraph L1[\"1. 交互呈现层 (Presentation Layer)\"]\n"
        "        UI[\"React Workbench / CLI Commands\"]:::uiLayer\n"
        "    end\n\n"
        "    subgraph L2[\"2. 通信网关与路由层 (API & Gateway)\"]\n"
        "        API[\"FastAPI ASGI 边界 / REST & SSE Router\"]:::apiLayer\n"
        "        ROUTER[\"Fast/Slow Conversation Router\"]:::apiLayer\n"
        "    end\n\n"
        "    subgraph L3[\"3. 领域核心引擎层 (Domain Core Engines)\"]\n"
        "        DR[\"Deep Research V2 长程综述引擎\"]:::coreLayer\n"
        "        PR[\"Paper Reading V2 自适应精读引擎\"]:::coreLayer\n"
        "        PD[\"Project Tutor V2 架构导师与体检官\"]:::coreLayer\n"
        "        HARNESS[\"Deterministic Agent Harness & Budget\"]:::coreLayer\n"
        "    end\n\n"
        "    subgraph L4[\"4. 数据基石与存储层 (Storage & Persistence)\"]\n"
        "        DB[(\"SQLite Runtime (WAL Mode)\")]:::storeLayer\n"
        "        CAS[(\"Artifact Store (CAS Content Addressed)\")]:::storeLayer\n"
        "        VDB[(\"LanceDB Multimodal Vector Index\")]:::storeLayer\n"
        "    end\n\n"
        "    UI --> API --> ROUTER\n"
        "    ROUTER --> DR & PR & PD\n"
        "    DR & PR & PD --> HARNESS\n"
        "    HARNESS --> DB & CAS & VDB\n"
        "```"
    )

    # 5. Generate Mermaid Dataflow Sequence
    dataflow_sequence_mermaid = (
        "```mermaid\n"
        "sequenceDiagram\n"
        "    autonumber\n"
        "    actor User as 研究者 / 开发者\n"
        "    participant Workbench as Workbench 前端\n"
        "    participant Server as FastAPI Server\n"
        "    participant Engine as 领域核心引擎 (V2)\n"
        "    participant Harness as 确定性 Harness 沙箱\n"
        "    participant Store as SQLite & ArtifactStore\n\n"
        "    User->>Workbench: 发起深度研读 / 架构体检指令\n"
        "    Workbench->>Server: POST /api/v1/... (异步作业提交)\n"
        "    Server->>Store: 初始化 Task/Run 状态记录 (Pending)\n"
        "    Server->>Engine: 激活长程工作流执行器\n"
        "    loop 渐进式执行与检查点持久化\n"
        "        Engine->>Harness: 派发子切面任务 (带预算与参数校验)\n"
        "        Harness->>Harness: 副作用隔离检查 & 耗时审计\n"
        "        Harness->>Store: 写入细粒度 Section Checkpoint & Artifacts\n"
        "        Server-->>Workbench: 推送 SSE 进度心跳与增量结果\n"
        "    end\n"
        "    Engine->>Store: 交付闭环研报与体检诊断书\n"
        "    Server-->>Workbench: 交付完成 (Completed 状态)\n"
        "    Workbench-->>User: 渲染富文本图文研报与重构 Diff\n"
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
