"""End-to-End Orchestrator and Comprehensive Markdown Report Synthesizer for Project Tutor V2."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from conflux_weave.project_tutor.architecture_reverse import reverse_engineer_architecture
from conflux_weave.project_tutor.hygiene_auditor import audit_project_hygiene
from conflux_weave.project_tutor.spec import (
    ArchitectureMotivations,
    ProjectHygieneReport,
    ProjectTutorReport,
)
from conflux_weave.project_tutor.stack_tutor import (
    build_progressive_walkthrough_stages,
    scan_project_dependencies,
)
from conflux_weave.provider import OpenAICompatibleChatAdapter
from conflux_weave.runtime.artifacts import LocalArtifactStore


def render_project_tutor_markdown(
    project_name: str,
    root_path: str,
    motivations: ArchitectureMotivations,
    dependency_cards: list[Any] | tuple[Any, ...],
    walkthrough_stages: list[Any] | tuple[Any, ...],
    hygiene_report: ProjectHygieneReport,
) -> str:
    """Render comprehensive, professional academic & engineering Markdown dissection report."""
    lines = [
        f"# 《{project_name}》项目深度解剖与实战导师研报 (Project Dissection V2)\n",
        f"- **工程根目录**：`{root_path}`\n",
        f"- **代码卫生评级**：{hygiene_report.executive_verdict}\n",
        f"- **卫生健康评分**：`{hygiene_report.hygiene_score} / 100`\n",
        "---\n",
        "## 一、架构设计动机逆向工程 (The 'Why' Behind the Code)\n",
        f"### 1.1 顶层心智模型 (Mental Model)\n",
        f"{motivations.mental_model_summary}\n\n",
        "### 1.2 关键架构决策与权衡矩阵 (Decision Trade-offs)\n",
    ]

    for d in motivations.decisions:
        rej_str = "、".join(d.rejected_alternatives)
        lines.extend([
            f"#### ▸ 决策切面：{d.topic}\n",
            f"- **采纳方案**：`{d.chosen_solution}`\n",
            f"- **否决替代方案**：{rej_str}\n",
            f"- **选型底层动机 (Why)**：{d.core_rationale}\n",
            f"- **接受的工程代价 (Trade-offs)**：{d.tradeoffs_accepted}\n\n",
        ])

    lines.extend([
        "## 二、系统分层拓扑与核心数据流图谱\n",
        "### 2.1 模块分层拓扑拓扑架构图 (Module Topology)\n",
        f"{motivations.topology_mermaid}\n\n",
        "### 2.2 端到端核心业务执行时序图 (Dataflow Sequence)\n",
        f"{motivations.dataflow_sequence_mermaid}\n\n",
        "## 三、技术栈通识教学与核心依赖原理剖析\n",
        "> **面向研读者的技术栈通识解析**：解剖项目三方依赖在系统中的精准职责与底层物理机制：\n\n",
    ])

    for card in dependency_cards:
        lines.extend([
            f"### 依赖组件：`{card.package_name}` ({card.category})\n",
            f"- **在工程中的职责**：{card.role_in_project}\n",
            f"- **底层核心机制**：{card.underlying_mechanism}\n",
            f"- **为什么必须引入它**：{card.why_needed}\n\n",
        ])

    lines.extend([
        "## 四、“自顶向下”保姆级源码精读路线导航\n",
        "> **科学渐进精读路线**：遵循「数据基石 -> 确定性沙箱 -> 核心算法 -> 外部边界」的认知脉络展开精读：\n\n",
    ])

    for s in walkthrough_stages:
        files_str = "\n".join(f"  - `{f}`" for f in s.key_files)
        lines.extend([
            f"### {s.stage_name}\n",
            f"- **核心学习主题**：{s.core_theme}\n",
            f"- **重点导航文件**：\n{files_str}\n",
            f"- **导师精读指引**：{s.reading_tips}\n\n",
        ])

    lines.extend([
        "## 五、“AI 编程恶习与官僚防御过当”专项深度体检诊断书\n",
        f"> **体检裁决结论**：{hygiene_report.executive_verdict}（累计发现问题点: {hygiene_report.total_findings_count} 项）\n\n",
        "### 5.1 七大维度体检统计分布\n",
        "| 体检维度 | 命中项数 | 风险等级 | 说明 |\n",
        "| :--- | :--- | :--- | :--- |\n",
        f"| **1. 官僚防御与仪式感浪费** | {hygiene_report.findings_by_category.get('bureaucratic_ritualism', 0)} | 低危/流程 | 机械哈希校验与死板基线清单 |\n",
        f"| **2. Mock 假象与虚假繁荣** | {hygiene_report.findings_by_category.get('mock_illusion', 0)} | 中危/测试 | 滥用全量 Mock 隔离真实环境 |\n",
        f"| **3. 空壳桩代码与伪实现** | {hygiene_report.findings_by_category.get('stub_placeholder', 0)} | 中危/逻辑 | 仅含 pass 或假数据的未完成函数 |\n",
        f"| **4. 静默吞异常与故障遮蔽** | {hygiene_report.findings_by_category.get('silent_swallowing', 0)} | 高危/稳定性 | 裸 except 导致隐蔽系统崩溃 |\n",
        f"| **5. 幽灵配置与参数幻觉** | {hygiene_report.findings_by_category.get('phantom_config', 0)} | 低危/认知 | 声明了但代码中从未调用的形参配置 |\n",
        f"| **6. 死深防御性废代码** | {hygiene_report.findings_by_category.get('zombie_defensiveness', 0)} | 中危/冗余 | 多重重复判空与无意义僵尸保护分支 |\n",
        f"| **7. 真实架构与资源隐患** | {hygiene_report.findings_by_category.get('architectural_vulnerability', 0)} | 高危/安全 | 密钥硬编码、连接未释放与并发竞态 |\n\n",
        "### 5.2 靶向问题清单与重构 Diff 方案\n",
    ])

    if hygiene_report.findings:
        for idx, f in enumerate(hygiene_report.findings[:15]):
            lines.extend([
                f"#### [{f.severity.upper()}] 问题 #{idx+1}：{f.issue_description}\n",
                f"- **位置**：`{f.file_path}:{f.line_number}`\n",
                f"- **所属类别**：`{f.category.value}`\n",
                f"- **危害剖析**：{f.harm_analysis}\n",
                f"- **现场代码摘录**：\n```python\n{f.code_snippet}\n```\n",
                f"- **精准重构 Diff 建议**：\n```diff\n{f.refactor_diff_suggestion}\n```\n\n",
            ])
    else:
        lines.append("*(代码库卫生状况极佳，未检出典型 AI 编程恶习与官僚防御缺陷)*\n\n")

    return "\n".join(lines)


def run_project_dissection_v2(
    root_path: Path | str,
    project_id: str = "",
    project_name: str = "",
    artifact_store: LocalArtifactStore | None = None,
    adapter: OpenAICompatibleChatAdapter | None = None,
) -> ProjectTutorReport:
    """Run complete Project Dissection V2 pipeline."""
    path = Path(root_path).resolve()
    p_id = project_id or path.name.lower()
    p_name = project_name or path.name

    # Step 1: Architecture motivations reverse engineering
    motivations = reverse_engineer_architecture(path, project_name=p_name, adapter=adapter)

    # Step 2: Stack tutor and progressive walkthrough
    cards = scan_project_dependencies(path)
    stages = build_progressive_walkthrough_stages(path, project_name=p_name)

    # Step 3: Targeted AI hygiene audit
    hygiene = audit_project_hygiene(path)

    # Step 4: Render full markdown report
    full_md = render_project_tutor_markdown(
        project_name=p_name,
        root_path=str(path),
        motivations=motivations,
        dependency_cards=cards,
        walkthrough_stages=stages,
        hygiene_report=hygiene,
    )

    report_obj = ProjectTutorReport(
        project_id=p_id,
        project_name=p_name,
        root_path=str(path),
        motivations=motivations,
        dependency_cards=cards,
        walkthrough_stages=stages,
        hygiene_report=hygiene,
        full_markdown=full_md,
    )

    # Persist artifacts if store is provided
    if artifact_store is not None:
        try:
            artifact_store.write_artifact(
                f"project_dissection_v2/{p_id}_report.json",
                json.dumps(report_obj.to_dict(), ensure_ascii=False, indent=2),
            )
            artifact_store.write_artifact(
                f"project_dissection_v2/{p_id}_report.md",
                full_md,
            )
        except Exception:
            pass

    return report_obj
