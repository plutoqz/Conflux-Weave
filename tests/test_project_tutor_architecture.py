"""Unit tests for architecture motivations reverse engineering."""

from __future__ import annotations

from pathlib import Path
import tempfile

from conflux_weave.project_tutor.architecture_reverse import reverse_engineer_architecture


def test_reverse_engineer_architecture_motivations() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        (root / "pyproject.toml").write_text("[project]\nname='conflux-weave'\n", encoding="utf-8")
        (root / "server.py").write_text("import fastapi\n", encoding="utf-8")

        motivations = reverse_engineer_architecture(root, project_name="Conflux-Weave")
        assert "Conflux-Weave" in motivations.mental_model_summary
        assert "确定性沙箱" in motivations.mental_model_summary or "智能体" in motivations.mental_model_summary
        assert len(motivations.decisions) >= 3

        # Check key decisions
        topics = [d.topic for d in motivations.decisions]
        assert any("持久化" in t for t in topics)
        assert any("沙箱" in t for t in topics)
        assert any("调度" in t or "计算预算" in t for t in topics)

        # Check mermaid code
        assert "```mermaid" in motivations.topology_mermaid
        assert "flowchart TD" in motivations.topology_mermaid
        assert "```mermaid" in motivations.dataflow_sequence_mermaid
        assert "sequenceDiagram" in motivations.dataflow_sequence_mermaid
