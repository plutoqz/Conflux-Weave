"""Unit tests for physical visual slice inlining and deep chart/table analysis."""

from __future__ import annotations

from pathlib import Path
import tempfile

from conflux_weave.paper_reading.spec import (
    PhysicalVisualSlice,
    VisualAssetType,
)
from conflux_weave.paper_reading.visual_inliner import (
    classify_visual_asset_type,
    extract_visual_slices_from_pdf,
    generate_deep_visual_analysis,
    inline_visual_slices_into_markdown,
)
from conflux_weave.runtime.artifacts import LocalArtifactStore


def test_classify_visual_asset_type() -> None:
    assert classify_visual_asset_type("Figure 1: The Transformer model architecture.") == VisualAssetType.ARCHITECTURE_DIAGRAM
    assert classify_visual_asset_type("Figure 3: Ablation study on number of attention heads.") == VisualAssetType.ABLATION_CHART
    assert classify_visual_asset_type("Table 2: Comparison of BLEU scores on WMT 2014.", "table") == VisualAssetType.PERFORMANCE_TABLE
    assert classify_visual_asset_type("Table 1: Main benchmark performance comparison.") == VisualAssetType.PERFORMANCE_TABLE
    assert classify_visual_asset_type("Figure 5: Example translation outputs.") == VisualAssetType.GENERIC_FIGURE


def test_generate_deep_visual_analysis() -> None:
    analysis_arch = generate_deep_visual_analysis("Figure 1: System Overview", VisualAssetType.ARCHITECTURE_DIAGRAM, page=3)
    assert "核心架构拓扑与数据流解剖" in analysis_arch
    assert "张量" in analysis_arch or "计算图" in analysis_arch

    analysis_ablation = generate_deep_visual_analysis("Figure 2: Ablation of layers", VisualAssetType.ABLATION_CHART, page=5)
    assert "消融趋势与超参敏感性量化归因" in analysis_ablation

    analysis_table = generate_deep_visual_analysis("Table 1: Benchmark Results", VisualAssetType.PERFORMANCE_TABLE, page=7)
    assert "主实验权威评测与基准性能对比" in analysis_table


def test_inline_visual_slices_into_markdown() -> None:
    markdown_raw = (
        "# 论文研读研报\n\n"
        "## 三、核心架构拓扑与计算流程\n"
        "### 核心系统架构拓扑与关键计算流\n"
        "这里是正文文本，介绍多头注意力网络结构。\n\n"
        "## 四、主实验基准评测与消融归因\n"
        "### 主实验权威评测\n"
        "这里是主实验段落文本。\n\n"
        "### 消融实验与参数敏感性\n"
        "这里是消融分析段落文本。\n"
    )

    slices = (
        PhysicalVisualSlice(
            asset_id="slice_arch_1",
            page=3,
            asset_type=VisualAssetType.ARCHITECTURE_DIAGRAM,
            caption="Figure 1: Transformer Model Architecture",
            image_rel_path="visual_slices/slice_arch_1.png",
            deep_analysis="拓扑流转解剖说明",
        ),
        PhysicalVisualSlice(
            asset_id="slice_tbl_1",
            page=6,
            asset_type=VisualAssetType.PERFORMANCE_TABLE,
            caption="Table 1: Main BLEU Results",
            image_rel_path="visual_slices/slice_tbl_1.png",
            deep_analysis="主实验指标对比剖析",
        ),
        PhysicalVisualSlice(
            asset_id="slice_abl_1",
            page=8,
            asset_type=VisualAssetType.ABLATION_CHART,
            caption="Figure 2: Ablation on Head Count",
            image_rel_path="visual_slices/slice_abl_1.png",
            deep_analysis="消融参数敏感性说明",
        ),
    )

    inlined = inline_visual_slices_into_markdown(markdown_raw, slices, base_asset_url="/api/v1/assets")

    # Verify each slice is cleanly inlined
    assert "![Figure 1: Transformer Model Architecture](/api/v1/assets/slice_arch_1)" in inlined
    assert "![Table 1: Main BLEU Results](/api/v1/assets/slice_tbl_1)" in inlined
    assert "![Figure 2: Ablation on Head Count](/api/v1/assets/slice_abl_1)" in inlined
    assert "> [!NOTE]" in inlined
    assert "图表物理切片深度解析" in inlined


def test_extract_visual_slices_from_nonexistent_pdf() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        store = LocalArtifactStore(tmp_dir)
        slices = extract_visual_slices_from_pdf("nonexistent_path.pdf", store)
        assert slices == ()
