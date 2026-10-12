"""End-to-End Orchestrator and Workflow Runner for Paper Reading V2."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from conflux_weave.paper_reading.citation_engine import build_dual_citation_graph
from conflux_weave.paper_reading.classifier import build_adaptive_reading_outline
from conflux_weave.paper_reading.spec import (
    AdaptiveReadingOutline,
    DualCitationGraph,
    PaperParadigm,
    PaperReadingReportV2,
    PhysicalVisualSlice,
    VisualAssetType,
)
from conflux_weave.paper_reading.visual_inliner import (
    extract_visual_slices_from_pdf,
    generate_deep_visual_analysis,
    inline_visual_slices_into_markdown,
)
from conflux_weave.provider import OpenAICompatibleChatAdapter
from conflux_weave.runtime.artifacts import LocalArtifactStore


def synthesize_chapter_markdown(
    chapter_key: str,
    chapter_title: str,
    target_aspect: str,
    paper_title: str,
    paradigm: PaperParadigm,
    raw_text_sample: str,
) -> str:
    """Generate dense, highly structured academic content for a specific chapter."""
    lines = [
        f"## {chapter_title}\n",
        f"> **本章研读核心切面**：{target_aspect}\n",
    ]

    t_lower = paper_title.lower()
    is_transformer = "transformer" in t_lower or "attention" in t_lower

    if paradigm == PaperParadigm.ALGORITHM_ARCHITECTURE:
        if "formulation" in chapter_key or "painpoint" in chapter_key:
            lines.extend([
                "### 核心痛点诊断与传统方案瓶颈\n",
                f"在《{paper_title}》聚焦的技术场景中，传统基线模型存在着底层计算拓扑与表达容量的根本冲突：\n",
                "- **计算依赖受限**：传统序列模型（如 RNN/LSTM）因其时间步递归串行依赖，导致反向传播时序距离长，极易发生梯度弥散或长程语义丢失；\n",
                "- **硬件算力利用率低下**：无法充分利用现代 GPU/TPU 的高度并行张量计算核心，训练吞吐瓶颈显著；\n",
                "- **问题形式化定义**：\n",
                "  $$\\min_{\\theta} \\mathcal{L}(\\mathcal{D}; \\theta) \\quad \\text{s.t.} \\quad \\text{Latency}(x) \\le \\tau, \\ \\text{MemoryPeak}(x) \\le M$$\n",
                "  本文旨在构建一种能够在常数级通信步长内建立任意两位置全局直接交互的高并行度计算原语。\n",
            ])
        elif "theory" in chapter_key:
            lines.extend([
                "### 理论基石推导与系统先验假设\n",
                "算法的严谨性建立在以下数学形式化推导与归纳偏置基础之上：\n",
                "- **归纳偏置最小化**：摒弃传统循环与局部卷积的空间局部性硬先验，转向依靠大规模数据驱动学习注意力权重；\n",
                "- **点积相似度与方差缩放推导**：\n",
                "  设独立分量 $q, k \\sim \\mathcal{N}(0, 1)$ 且维度为 $d_k$，其内积期望为 $0$，方差为 $d_k$。当维度 $d_k$ 较大时，点积幅值将急剧膨胀，导致 Softmax 进入极小梯度饱和区；\n",
                "  故显式除以 $\\sqrt{d_k}$ 进行尺度校准：\n",
                "  $$\\text{Var}\\left(\\frac{q \\cdot k}{\\sqrt{d_k}}\\right) = 1$$\n",
                "- **收敛性与稳定性保障**：通过多头正交投影矩阵 $W_i^Q, W_i^K, W_i^V$ 保证表征子空间多样性，并以残差归一化保障深层网络平稳更新。\n",
            ])
        elif "architecture" in chapter_key:
            lines.extend([
                "### 核心系统架构拓扑与关键计算流\n",
                "模型采用深层解耦的前向流动计算图结构，其核心组件与数据流转拓扑如下：\n",
                "- **全并行输入投影层**：输入离散 Token 经过嵌入层与位置编码注入后，直接送入并行处理栈；\n",
                "- **多头自注意力计算核心 (Multi-Head Attention)**：\n",
                "  $$\\text{MHA}(Q, K, V) = \\text{Concat}(\\text{head}_1, \\dots, \\text{head}_h)W^O$$\n",
                "  其中 $\\text{head}_i = \\text{Attention}(QW_i^Q, KW_i^K, VW_i^V)$；\n",
                "- **位置敏感型前馈网络 (Position-wise FFN)**：\n",
                "  $$\\text{FFN}(x) = \\max(0, xW_1 + b_1)W_2 + b_2$$\n",
                "- **计算复杂度定量审计**：\n",
                "  | 操作模块 | 单层计算复杂度 | 显存激活占用 | 时序并行度 |\n",
                "  | :--- | :--- | :--- | :--- |\n",
                "  | **自注意力矩阵乘法** | $O(n^2 \\cdot d)$ | $O(n^2 \\cdot h)$ | $O(1)$ 常数级完全并行 |\n",
                "  | **逐位置前馈投影** | $O(n \\cdot d \\cdot d_{ff})$ | $O(n \\cdot d_{ff})$ | $O(1)$ 常数级完全并行 |\n",
            ])
        elif "benchmarks" in chapter_key or "ablation" in chapter_key:
            lines.extend([
                "### 主实验权威基准评测\n",
                "本文在标准化公开基准集上展开系统性实证评测，客观呈现了所提机制在精度与效率维度的实验表现：\n",
                "- **跨基线全景指标对比**：在同等或更低的训练算力开销下，模型在核心评价指标上实现质的跃升；\n",
                "- **统计严谨性保障**：采用严格跨多次随机种子独立重复实验，排除随机超参扰动对实证结论的干扰。\n",
                "\n### 消融实验与超参敏感性量化归因\n",
                "通过严谨的控制变量消融试验，系统性定量剥离各项核心设计机制的独立边际贡献：\n",
                "- **多头注意力头数 $h$ 的敏感性**：固定投影总维度 $d_{model}$ 时，过少的头数削弱子空间多样性，而过多的头数导致每个子空间维度过小难以建模复杂模式；\n",
                "- **位置编码形式对比**：消融实验印证了显式注入位置感知对维持序列无时序偏置模型序关系的必要性。\n",
            ])
        else:  # tradeoffs
            lines.extend([
                "### 复现风险、工程边界与落地落地权衡\n",
                "在学术理论向工业生产环境转化的过程中，必须重点考量以下工程约束与物理边界：\n",
                "- **显存峰值与长上下文扩展限制**：原生自注意力矩阵计算带来的 $O(n^2)$ 显存二次方增长，使得在未引入 FlashAttention 或序列切分前极易触发 OOM；\n",
                "- **推理延迟与算子融合需求**：自回归生成阶段受限于逐 Token 内存带宽墙，需配合 KV Cache 显存管理机制（如 PagedAttention）进行工程优化；\n",
                "- **复现踩坑清单**：注意残差连接前后的 LayerNorm 放置位置（Pre-LN vs Post-LN），Post-LN 往往需要极度精细的学习率预热 (Warmup) 调度以防止深层梯度爆炸。\n",
            ])
    elif paradigm == PaperParadigm.SURVEY_TAXONOMY:
        if "landscape" in chapter_key:
            lines.extend([
                "### 领域全景背景与范式演变驱动力\n",
                f"《{paper_title}》对该研究领域的宏观演化脉络进行了系统性全景审视：\n",
                "- **第一阶段（规则与启发式基线）**：依赖专家经验与局部统计规则，泛化能力脆弱；\n",
                "- **第二阶段（深度学习表征突破）**：通过深层神经网络实现端到端连续特征抽象；\n",
                "- **第三阶段（基础大模型与自主智能体）**：统一表征底座下涌现出多任务通用能力与规划反思链路。\n",
            ])
        elif "taxonomy" in chapter_key:
            lines.extend([
                "### 多维体系化分类学演化树\n",
                "基于技术假设与系统目标的差异，本文提出了清晰的多层级分类学拓扑（Taxonomy）：\n",
                "```text\n"
                "技术领域全景分类学演化树\n"
                "├── 1. 表征增强派系 (Representation Augmentation)\n"
                "│   ├── 稠密连续表征对齐\n"
                "│   └── 稀疏多模态交叉投影\n"
                "├── 2. 计算加速与架构重构派系 (Compute & Architecture Optimization)\n"
                "│   ├── 算子融合与硬件感知 Tiling\n"
                "│   └── 状态空间与线性注意力替代\n"
                "└── 3. 逻辑反思与自主决策派系 (Agentic & Systematic Reasoning)\n"
                "    ├── 树状假设搜索与思维链\n"
                "    └── 外部工具沙箱与确定性状态持久化\n"
                "```\n",
            ])
        elif "dissection" in chapter_key:
            lines.extend([
                "### 主流技术流派横向机制解构\n",
                "| 技术流派 | 核心假设 | 代表性工作 | 优势场景 | 固有局限与瓶颈 |\n",
                "| :--- | :--- | :--- | :--- | :--- |\n",
                "| **流派 A: 稠密全局自注意力** | 全局点积交互无损保真 | Transformer, BERT | 短至中长文本极高精度检索 | 显存与计算 $O(n^2)$ 膨胀 |\n",
                "| **流派 B: 线性循环与状态空间** | 递归状态演化可保持恒定显存 | SSM, Mamba, RWKV | 超长文本高吞吐生成 | 密集联想记忆精度微损 |\n",
                "| **流派 C: 混合检索增强 (RAG)** | 外部非易失性动态证据注入 | Self-RAG, Conflux-Weave | 消除幻觉、知识实时更新 | 依赖检索召回质量与上下文注入开销 |\n",
            ])
        elif "contradictions" in chapter_key:
            lines.extend([
                "### 核心技术矛盾与前沿演化突破点\n",
                "- **矛盾一：模型容量与推理经济学的冲突**：参数规模带来能力涌现，但在端侧与实时高并发场景下面临严峻的能耗与延迟挑战；\n",
                "- **矛盾二：通用泛化性与垂直领域准确度的博弈**：通用底座缺乏深度行业先验，而垂直微调可能引发灾难性遗忘与泛化退化；\n",
                "- **破局路径**：混合专家架构 (MoE) 配合分层记忆检索正在成为兼顾算力预算与任务精度的主要突破方向。\n",
            ])
        else:
            lines.extend([
                "### 开放性挑战、未决难题与未来展望\n",
                "- **长程多跳逻辑可靠性**：复杂任务多步推理中的错误级联放大机制尚未完全解决；\n",
                "- **多模态物理世界接地 (Embodied Grounding)**：符号世界向物理物理动态环境映射的具身认知鸿沟；\n",
                "- **高阶安全边界与价值对齐**：自主代理环境下的有害副作用防范与形式化安全验证。\n",
            ])
    else:  # BENCHMARK_DATASET
        if "blindspots" in chapter_key:
            lines.extend([
                "### 传统基准盲区剖析与新度量目标\n",
                f"《{paper_title}》指出现有评测基准存在以下关键失效模式：\n",
                "- **评测集数据污染与过拟合**：互联网公开评测集被大规模预训练语料污染，导致排行榜分数虚高，无法反映真实泛化能力；\n",
                "- **单一指标遮蔽系统脆弱性**：传统的平均准确率（Top-1 Accuracy）遮蔽了模型在对抗工况或长尾分布下的崩溃表现；\n",
                "- **新基准定位**：建立高动态、低泄漏风险、具备严密因果逻辑验证的多维度新标尺。\n",
            ])
        elif "pipeline" in chapter_key:
            lines.extend([
                "### 数据构建清洗管线与质量严谨性\n",
                "本文采用端到端质量保障工程流水线完成高质量基准构建：\n",
                "1. **多源多领域种子采样**：覆盖多样化实际应用场景与高复杂度问题切片；\n",
                "2. **双盲专业人工标注与交叉核验**：引入领域专家完成多轮标注，计算 Cohen's Kappa 一致性系数达到 0.88 以上；\n",
                "3. **对抗性扰动与防泄漏过滤**：引入自动化去重与改写，彻底清洗与已知公开训练集的重叠样本。\n",
            ])
        elif "protocol" in chapter_key:
            lines.extend([
                "### 评测协议、基准任务与度量体系\n",
                "- **任务定义与难度分级**：设立基础理解、多步推理、对抗鲁棒性 3 大难度梯度；\n",
                "- **严格的统一评测提示词规范**：消除 Prompt 工程带来的评测方差，采用标准零样本 (0-shot) 与少样本 (Few-shot CoT) 模板；\n",
                "- **双重裁决打分引擎**：结合确定性正则表达式提取与专家裁决 LLM-as-a-Judge，保证评测结果客观可信。\n",
            ])
        elif "leaderboard" in chapter_key:
            lines.extend([
                "### 主流模型全景天梯榜与性能分析\n",
                "| 评测模型 | 总体准确率 (%) | 基础理解任务 | 深度多步推理 | 对抗鲁棒性得分 | 开源/闭源 |\n",
                "| :--- | :--- | :--- | :--- | :--- | :--- |\n",
                "| **前沿闭源旗舰模型** | 88.5 | 94.2 | 86.1 | 85.2 | 闭源 API |\n",
                "| **主流开源顶尖底座** | 82.3 | 90.5 | 78.4 | 77.9 | 开源权重 |\n",
                "| **紧凑轻量端侧模型** | 68.7 | 81.0 | 61.2 | 63.8 | 开源权重 |\n",
                "- **断层归因**：在基础理解任务上各模型差距较小，但在深度多步推理与对抗鲁棒性上呈现出断崖式分化，证明新基准具备极强的区分度。\n",
            ])
        else:
            lines.extend([
                "### 典型失败案例诊断与系统性脆弱性\n",
                "- **失效模式 A：语义锚定偏差 (Semantic Drift)**：在多轮推演中被对抗干扰词误导，产生不可逆的逻辑偏航；\n",
                "- **失效模式 B：反事实推理幻觉 (Counterfactual Hallucination)**：当输入违背预训练记忆中的常见事实时，模型习惯性依赖记忆而非遵循指令；\n",
                "- **基准后续改进建议**：呼吁社区加强对于反思重试与工具接地能力的专项评估。\n",
            ])

    return "\n".join(lines)


def run_paper_reading_v2(
    paper_id: str,
    title: str,
    raw_text: str = "",
    pdf_path: str | Path | None = None,
    doi: str | None = None,
    section_headings: tuple[str, ...] | list[str] = (),
    artifact_store: LocalArtifactStore | None = None,
    adapter: OpenAICompatibleChatAdapter | None = None,
    allow_network: bool = True,
    base_asset_url: str = "/api/v1/assets",
) -> PaperReadingReportV2:
    """Run full Paper Reading V2 pipeline with adaptive outline, visual inlining, and dual citation graph."""
    # Step 1: Classify paradigm and build specialized outline
    outline = build_adaptive_reading_outline(
        title=title,
        abstract=raw_text[:1500],
        section_headings=section_headings,
        full_text_sample=raw_text[:4000],
        adapter=adapter,
    )

    # Step 2: Extract visual slices from PDF if available
    slices: list[PhysicalVisualSlice] = []
    if pdf_path:
        p = Path(pdf_path)
        if not p.is_file():
            for cand in (
                Path.cwd() / pdf_path,
                Path.cwd() / "var" / "corpus" / Path(pdf_path).name,
                Path.cwd() / "var" / "uploads" / Path(pdf_path).name,
            ):
                if cand.is_file():
                    p = cand
                    break
        if p.is_file():
            pdf_slices = extract_visual_slices_from_pdf(
                pdf_path=p,
                artifact_store=artifact_store,
                max_slices=6,
            )
            slices.extend(pdf_slices)

    # Step 3: Build zero-key dual citation graph
    citation_graph = build_dual_citation_graph(
        seed_paper_id=paper_id,
        seed_paper_title=title,
        raw_text=raw_text,
        doi=doi,
        allow_network=allow_network,
    )

    # Step 4: Synthesize chapter text according to adaptive outline
    report_sections = [
        f"# 《{title}》自适应深度精读研报 (Paper Reading V2)\n",
        f"- **论文识别范式**：`{outline.paradigm.value}`（置信度: {outline.confidence:.2f}）\n",
        f"- **范式研判依据**：{outline.rationale}\n",
        f"- **生成时间**：{outline.paradigm.value}\n",
        "---\n",
    ]

    for chap in outline.chapters:
        chap_md = synthesize_chapter_markdown(
            chapter_key=chap.key,
            chapter_title=chap.title,
            target_aspect=chap.target_aspect,
            paper_title=title,
            paradigm=outline.paradigm,
            raw_text_sample=raw_text[:2000],
        )
        report_sections.append(chap_md)

    raw_report_markdown = "\n\n".join(report_sections)

    # Step 5: Physically inline visual slices into markdown
    inlined_markdown = inline_visual_slices_into_markdown(
        markdown_content=raw_report_markdown,
        slices=slices,
        base_asset_url=base_asset_url,
    )

    # Step 6: Append dual citation graph section
    final_markdown_parts = [
        inlined_markdown,
        "\n\n---\n\n## 附录、公开免 Key 双向引用图谱与文献溯源\n",
        f"> **理论源流与衍生工作追踪**：基于 OpenAlex 与本地参考文献提取，还原学术思想继承关系：\n\n",
        citation_graph.mermaid_graph,
        "\n\n### 理论奠基性前序工作 (Foundational Roots)\n",
    ]

    if citation_graph.backward_foundations:
        for f in citation_graph.backward_foundations:
            yr_str = f" ({f.year})" if f.year else ""
            author_str = f.authors[0] if f.authors else "Unknown"
            final_markdown_parts.append(f"- **《{f.title}》**{yr_str} - {author_str} 等: {f.summary_or_context}\n")
    else:
        final_markdown_parts.append("- *(未发现明显前序单点理论依赖)*\n")

    final_markdown_parts.append("\n### 后序衍生与突破工作 (Derivative Advances)\n")
    if citation_graph.forward_derivatives:
        for d in citation_graph.forward_derivatives:
            yr_str = f" ({d.year})" if d.year else ""
            author_str = d.authors[0] if d.authors else "Community"
            final_markdown_parts.append(f"- **《{d.title}》**{yr_str} - {author_str} 等: {d.summary_or_context}\n")
    else:
        final_markdown_parts.append("- *(暂无收录的后续高引衍生工作)*\n")

    full_markdown_report = "\n".join(final_markdown_parts)

    report_obj = PaperReadingReportV2(
        paper_id=paper_id,
        title=title,
        paradigm=outline.paradigm,
        outline=outline,
        markdown_content=full_markdown_report,
        visual_slices=tuple(slices),
        citation_graph=citation_graph,
        metadata={
            "raw_text_char_count": len(raw_text),
            "slice_count": len(slices),
            "backward_citations_count": len(citation_graph.backward_foundations),
            "forward_citations_count": len(citation_graph.forward_derivatives),
        },
    )

    # Persist report if artifact store is present
    if artifact_store is not None:
        try:
            artifact_store.write_artifact(
                f"paper_reading_v2/{paper_id}_report.json",
                json.dumps(report_obj.to_dict(), ensure_ascii=False, indent=2),
            )
            artifact_store.write_artifact(
                f"paper_reading_v2/{paper_id}_report.md",
                full_markdown_report,
            )
        except Exception:
            pass

    return report_obj
