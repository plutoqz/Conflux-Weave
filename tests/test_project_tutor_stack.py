"""Unit tests for tech stack tutor and progressive code walkthrough guide."""

from __future__ import annotations

from pathlib import Path
import tempfile

from conflux_weave.project_tutor.stack_tutor import (
    build_progressive_walkthrough_stages,
    scan_project_dependencies,
)


def test_scan_project_dependencies() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        (root / "pyproject.toml").write_text(
            """
            [project]
            dependencies = [
                "fastapi>=0.115",
                "pymupdf>=1.24",
                "lancedb>=0.13",
                "pydantic>=2.9",
            ]
            """,
            encoding="utf-8",
        )

        cards = scan_project_dependencies(root)
        pkg_names = {c.package_name for c in cards}
        assert "fastapi" in pkg_names
        assert "pymupdf" in pkg_names
        assert "lancedb" in pkg_names
        assert "pydantic" in pkg_names

        # Check detail fields
        fastapi_card = next(c for c in cards if c.package_name == "fastapi")
        assert "ASGI" in fastapi_card.role_in_project
        assert "Starlette" in fastapi_card.underlying_mechanism


def test_build_progressive_walkthrough_stages() -> None:
    stages = build_progressive_walkthrough_stages(Path("."), project_name="Conflux-Weave")
    assert len(stages) == 4
    assert stages[0].stage_index == 1
    assert "数据基石" in stages[0].stage_name
    assert stages[1].stage_index == 2
    assert "执行回路" in stages[1].stage_name or "沙箱" in stages[1].stage_name
    assert stages[2].stage_index == 3
    assert "核心学术引擎" in stages[2].stage_name
    assert stages[3].stage_index == 4
    assert "外部边界" in stages[3].stage_name or "治理" in stages[3].stage_name
