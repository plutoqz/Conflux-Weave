"""Unit and integration tests for Skill system (P5.1)."""

from __future__ import annotations

import asyncio
from pathlib import Path
import sqlite3

import pytest
from starlette.testclient import TestClient

from conflux_weave.skills.builtin import (
    BUILTIN_SKILLS,
    CODE_ARCHITECTURE_AUDIT,
    LATEX_PAPER_POLISHER,
    LITERATURE_COMPARATIVE_SURVEY,
)
from conflux_weave.skills.spec import (
    SkillBudget,
    SkillCategory,
    SkillExecutionRequest,
    SkillSpec,
    SkillStatus,
)
from conflux_weave.skills.registry import SkillRegistry
from conflux_weave.skills.runner import SkillRunner
from types import SimpleNamespace


def test_builtin_skills_are_complete_and_valid() -> None:
    assert len(BUILTIN_SKILLS) == 3
    skill_ids = {s.skill_id for s in BUILTIN_SKILLS}
    assert skill_ids == {
        "literature_comparative_survey",
        "code_architecture_audit",
        "latex_paper_polisher",
    }

    assert LITERATURE_COMPARATIVE_SURVEY.category == SkillCategory.RESEARCH
    assert "paper_ids" in LITERATURE_COMPARATIVE_SURVEY.input_schema["required"]
    assert "rag_hybrid_search" in LITERATURE_COMPARATIVE_SURVEY.required_tools

    assert CODE_ARCHITECTURE_AUDIT.category == SkillCategory.GOVERNANCE
    assert "project_id" in CODE_ARCHITECTURE_AUDIT.input_schema["required"]
    assert "project_walkthrough" in CODE_ARCHITECTURE_AUDIT.required_tools

    assert LATEX_PAPER_POLISHER.category == SkillCategory.WRITING
    assert "latex_content" in LATEX_PAPER_POLISHER.input_schema["required"]


def test_skill_registry_crud_and_sqlite_persistence(tmp_path: Path) -> None:
    from conflux_weave.runtime.sqlite import SQLiteRuntimeRepository
    from conflux_weave.runtime import LocalArtifactStore

    db_file = tmp_path / "test_skills.sqlite3"
    store = LocalArtifactStore(tmp_path / "artifacts")
    repo = SQLiteRuntimeRepository(db_file, store)

    registry = SkillRegistry(db_file)

    # 1. Verify builtins are auto-populated
    active_skills = registry.list_skills(status="active")
    assert len(active_skills) == 3
    research_skills = registry.list_skills(category=SkillCategory.RESEARCH)
    assert len(research_skills) == 1
    assert research_skills[0].skill_id == "literature_comparative_survey"

    # 2. Register a custom skill
    custom_skill = SkillSpec(
        skill_id="custom_dataset_evaluator",
        version="0.1.0",
        name="数据集自动化评估",
        description="自动化评估数据集分布与偏差",
        category=SkillCategory.UTILITY,
        author="ResearchTeam",
        input_schema={"type": "object", "required": ["dataset_path"]},
        required_tools=(),
        prompt_template="评估数据集：{dataset_path}",
        default_budget=SkillBudget(max_tokens=4000),
        is_builtin=False,
    )
    registered = registry.register_skill(custom_skill)
    assert registered.skill_id == "custom_dataset_evaluator"

    fetched = registry.get_skill("custom_dataset_evaluator")
    assert fetched is not None
    assert fetched.author == "ResearchTeam"
    assert fetched.is_builtin is False

    # 3. Disable skill
    assert registry.disable_skill("custom_dataset_evaluator") is True
    active_now = registry.list_skills(status="active")
    assert len(active_now) == 3
    disabled_skills = registry.list_skills(status="disabled")
    assert any(s.skill_id == "custom_dataset_evaluator" for s in disabled_skills)


def test_skill_input_validation() -> None:
    registry = SkillRegistry()

    # Valid inputs
    ok, err = registry.validate_inputs(
        "literature_comparative_survey",
        {"paper_ids": ["2606.08702", "2606.10209"], "focus_dimensions": ["模型结构"]},
    )
    assert ok is True
    assert err is None

    # Missing required field
    ok, err = registry.validate_inputs("literature_comparative_survey", {})
    assert ok is False
    assert "缺少必要参数: paper_ids" in str(err)

    # Empty required list
    ok, err = registry.validate_inputs("literature_comparative_survey", {"paper_ids": []})
    assert ok is False
    assert "不能为空" in str(err)

    # Invalid type
    ok, err = registry.validate_inputs(
        "literature_comparative_survey", {"paper_ids": "not_a_list"}
    )
    assert ok is False
    assert "必须为数组格式" in str(err)


def test_skill_runner_execution_and_offline_mode() -> None:
    registry = SkillRegistry()
    runner = SkillRunner(registry, provider=None)

    # 1. Execute literature_comparative_survey
    req1 = SkillExecutionRequest(
        skill_id="literature_comparative_survey",
        inputs={"paper_ids": ["paper_alpha", "paper_beta"], "focus_dimensions": ["创新点", "准确率"]},
    )
    res1 = runner.execute_skill(req1)
    assert res1.status == "completed"
    assert "paper_alpha" in res1.content
    assert "结构化横向对比矩阵" in res1.content
    assert res1.structured_data["category"] == "research"
    assert res1.tokens_consumed > 0

    # 2. Execute code_architecture_audit
    req2 = SkillExecutionRequest(
        skill_id="code_architecture_audit",
        inputs={"project_id": "Conflux-Weave", "severity_threshold": "high"},
    )
    res2 = runner.execute_skill(req2)
    assert res2.status == "completed"
    assert "架构治理与契约审计体检报告" in res2.content
    assert "Conflux-Weave" in res2.content

    # 3. Execute latex_paper_polisher
    req3 = SkillExecutionRequest(
        skill_id="latex_paper_polisher",
        inputs={"latex_content": r"\begin{equation} y = Wx + b \end{equation}", "target_conference": "ICLR"},
    )
    res3 = runner.execute_skill(req3)
    assert res3.status == "completed"
    assert "ICLR" in res3.content
    assert "数学公式符号一致性" in res3.content

    # 4. Unknown skill
    req_unknown = SkillExecutionRequest(skill_id="non_existent", inputs={})
    res_unknown = runner.execute_skill(req_unknown)
    assert res_unknown.status == "failed"
    assert "未找到指定的 Skill" in str(res_unknown.error)

    # 5. Validation failure
    req_invalid = SkillExecutionRequest(skill_id="code_architecture_audit", inputs={})
    res_invalid = runner.execute_skill(req_invalid)
    assert res_invalid.status == "failed"
    assert "缺少必要参数" in str(res_invalid.error)


def test_skills_rest_api(tmp_path: Path) -> None:
    from conflux_weave.runtime.sqlite import SQLiteRuntimeRepository
    from conflux_weave.runtime import LocalArtifactStore
    from conflux_weave.server import create_app

    store = LocalArtifactStore(tmp_path / "artifacts")
    repository = SQLiteRuntimeRepository(tmp_path / "api_test.sqlite3", store)
    orchestrator = SimpleNamespace()
    app = create_app(repository, orchestrator)
    client = TestClient(app)

    # 1. GET /api/v1/skills
    resp = client.get("/api/v1/skills")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] >= 3
    skill_ids = [s["skill_id"] for s in data["items"]]
    assert "literature_comparative_survey" in skill_ids

    # 2. GET /api/v1/skills with category filter
    resp_filtered = client.get("/api/v1/skills?category=research")
    assert resp_filtered.status_code == 200
    cat_data = resp_filtered.json()
    assert all(s["category"] == "research" for s in cat_data["items"])

    # 3. GET /api/v1/skills/{skill_id}
    detail_resp = client.get("/api/v1/skills/literature_comparative_survey")
    assert detail_resp.status_code == 200
    detail = detail_resp.json()
    assert detail["skill_id"] == "literature_comparative_survey"
    assert "paper_ids" in detail["input_schema"]["required"]
    assert len(detail["rules"]) > 0

    # 4. GET 404 for unknown skill
    not_found = client.get("/api/v1/skills/unknown_skill_xyz")
    assert not_found.status_code == 404

    # 5. POST /api/v1/skills/{skill_id}/execute (Success)
    exec_resp = client.post(
        "/api/v1/skills/literature_comparative_survey/execute",
        json={
            "inputs": {
                "paper_ids": ["2606.08702", "2606.10209"],
                "focus_dimensions": ["模型结构", "召回率"],
            }
        },
    )
    assert exec_resp.status_code == 200
    exec_data = exec_resp.json()
    assert exec_data["status"] == "completed"
    assert "对比分析综述" in exec_data["content"]
    assert exec_data["tokens_consumed"] > 0

    # 6. POST /api/v1/skills/{skill_id}/execute (Validation Failure 400)
    fail_resp = client.post(
        "/api/v1/skills/literature_comparative_survey/execute",
        json={"inputs": {}},
    )
    assert fail_resp.status_code == 400
    fail_data = fail_resp.json()
    assert fail_data["code"] == "skill_execution_failed"
