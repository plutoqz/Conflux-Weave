"""Unit tests for Gate 1.1: Long-horizon survey outline and hypothesis planning."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from conflux_weave.long_horizon_research import (
    OutlinePlanner,
    ResearchOutlineTree,
    ResearchProbe,
    SurveyChapterPlan,
    build_deterministic_outline,
)


def test_deterministic_outline_structure():
    tree = build_deterministic_outline("大模型长上下文与显存优化技术综述")
    assert isinstance(tree, ResearchOutlineTree)
    assert tree.objective == "大模型长上下文与显存优化技术综述"
    assert len(tree.chapters) == 6
    assert tree.total_probes_count() >= 20

    # Ensure all chapters have title, core thesis, and probes
    for ch in tree.chapters:
        assert ch.chapter_id.startswith("ch-")
        assert len(ch.title) > 0
        assert len(ch.core_thesis) > 0
        assert len(ch.probes) >= 3
        for p in ch.probes:
            assert isinstance(p, ResearchProbe)
            assert len(p.target_aspect) > 0
            assert len(p.query_intent) > 0

    # Check DAG validity
    assert tree.validate_acyclic() is True
    ordered = tree.topological_chapters()
    assert len(ordered) == 6
    assert ordered[0].chapter_id == "ch-1"

    # Check serialization roundtrip
    data = tree.to_dict()
    roundtrip = ResearchOutlineTree.from_dict(data)
    assert roundtrip.outline_id == tree.outline_id
    assert len(roundtrip.chapters) == len(tree.chapters)
    assert roundtrip.total_probes_count() == tree.total_probes_count()


def test_outline_planner_with_mock_llm():
    mock_payload = {
        "background_summary": "测试背景概述",
        "target_word_count_range": [9000, 16000],
        "chapters": [
            {
                "chapter_id": "ch-1",
                "chapter_index": 1,
                "title": "一、引言与研究动机",
                "core_thesis": "立论1",
                "depends_on": [],
                "probes": [
                    {
                        "probe_id": "p-1-1",
                        "target_aspect": "切面1",
                        "query_intent": "查询1",
                        "expected_data_type": "theoretical_foundation",
                    },
                    {
                        "probe_id": "p-1-2",
                        "target_aspect": "切面2",
                        "query_intent": "查询2",
                        "expected_data_type": "historical_milestone",
                    },
                    {
                        "probe_id": "p-1-3",
                        "target_aspect": "切面3",
                        "query_intent": "查询3",
                        "expected_data_type": "architectural_tradeoff",
                    },
                ],
            },
            {
                "chapter_id": "ch-2",
                "chapter_index": 2,
                "title": "二、前置技术脉络",
                "core_thesis": "立论2",
                "depends_on": ["ch-1"],
                "probes": [
                    {
                        "probe_id": "p-2-1",
                        "target_aspect": "切面2-1",
                        "query_intent": "查询2-1",
                    },
                    {
                        "probe_id": "p-2-2",
                        "target_aspect": "切面2-2",
                        "query_intent": "查询2-2",
                    },
                    {
                        "probe_id": "p-2-3",
                        "target_aspect": "切面2-3",
                        "query_intent": "查询2-3",
                    },
                ],
            },
            {
                "chapter_id": "ch-3",
                "chapter_index": 3,
                "title": "三、核心机制分类",
                "core_thesis": "立论3",
                "depends_on": ["ch-2"],
                "probes": [
                    {
                        "probe_id": "p-3-1",
                        "target_aspect": "切面3-1",
                        "query_intent": "查询3-1",
                    },
                    {
                        "probe_id": "p-3-2",
                        "target_aspect": "切面3-2",
                        "query_intent": "查询3-2",
                    },
                    {
                        "probe_id": "p-3-3",
                        "target_aspect": "切面3-3",
                        "query_intent": "查询3-3",
                    },
                ],
            },
            {
                "chapter_id": "ch-4",
                "chapter_index": 4,
                "title": "四、实验对比",
                "core_thesis": "立论4",
                "depends_on": ["ch-3"],
                "probes": [
                    {
                        "probe_id": "p-4-1",
                        "target_aspect": "切面4-1",
                        "query_intent": "查询4-1",
                    },
                    {
                        "probe_id": "p-4-2",
                        "target_aspect": "切面4-2",
                        "query_intent": "查询4-2",
                    },
                    {
                        "probe_id": "p-4-3",
                        "target_aspect": "切面4-3",
                        "query_intent": "查询4-3",
                    },
                ],
            },
            {
                "chapter_id": "ch-5",
                "chapter_index": 5,
                "title": "五、挑战与总结",
                "core_thesis": "立论5",
                "depends_on": ["ch-4"],
                "probes": [
                    {
                        "probe_id": "p-5-1",
                        "target_aspect": "切面5-1",
                        "query_intent": "查询5-1",
                    },
                    {
                        "probe_id": "p-5-2",
                        "target_aspect": "切面5-2",
                        "query_intent": "查询5-2",
                    },
                    {
                        "probe_id": "p-5-3",
                        "target_aspect": "切面5-3",
                        "query_intent": "查询5-3",
                    },
                ],
            },
        ],
    }

    # Simulate reasoning model with <think> tag and markdown fences
    llm_output = (
        "<think>Planning 5 comprehensive chapters for the user survey...</think>\n"
        "```json\n"
        + json.dumps(mock_payload, ensure_ascii=False)
        + "\n```"
    )

    mock_adapter = MagicMock()
    mock_adapter.chat.return_value = llm_output

    planner = OutlinePlanner(chat_adapter=mock_adapter)
    tree = planner.plan_outline("端侧轻量化模型综述", min_chapters=5)

    assert tree.objective == "端侧轻量化模型综述"
    assert tree.background_summary == "测试背景概述"
    assert len(tree.chapters) == 5
    assert tree.total_probes_count() == 15
    assert tree.validate_acyclic() is True


def test_outline_planner_with_malformed_llm_falls_back():
    mock_adapter = MagicMock()
    mock_adapter.chat.return_value = "抱歉，我不能完成此任务。这不是有效的 JSON。"

    planner = OutlinePlanner(chat_adapter=mock_adapter)
    tree = planner.plan_outline("端侧轻量化模型综述")

    # Should fall back gracefully to deterministic generator
    assert isinstance(tree, ResearchOutlineTree)
    assert len(tree.chapters) == 6
    assert tree.total_probes_count() >= 20
    assert tree.validate_acyclic() is True


def test_outline_planner_sanitizes_dependencies_and_prevents_cycles():
    # LLM tries to create an impossible cycle or forward dependencies
    cycle_payload = {
        "chapters": [
            {
                "chapter_id": "ch-1",
                "title": "第1章",
                "depends_on": ["ch-2"],  # forward dependency to ch-2
                "probes": [{"target_aspect": "a", "query_intent": "q"}],
            },
            {
                "chapter_id": "ch-2",
                "title": "第2章",
                "depends_on": ["ch-1"],
                "probes": [{"target_aspect": "b", "query_intent": "q"}],
            },
            {
                "chapter_id": "ch-3",
                "title": "第3章",
                "depends_on": ["ch-2"],
                "probes": [{"target_aspect": "c", "query_intent": "q"}],
            },
            {
                "chapter_id": "ch-4",
                "title": "第4章",
                "depends_on": ["ch-3"],
                "probes": [{"target_aspect": "d", "query_intent": "q"}],
            },
            {
                "chapter_id": "ch-5",
                "title": "第5章",
                "depends_on": ["ch-4"],
                "probes": [{"target_aspect": "e", "query_intent": "q"}],
            },
        ]
    }
    mock_adapter = MagicMock()
    mock_adapter.chat.return_value = json.dumps(cycle_payload)

    planner = OutlinePlanner(chat_adapter=mock_adapter)
    # The sanitizer only allows depends_on to reference already-seen chapters, breaking forward loops
    tree = planner.plan_outline("测试循环依赖课题", min_chapters=5)
    assert tree.validate_acyclic() is True


def test_outline_planner_empty_objective_raises():
    planner = OutlinePlanner()
    with pytest.raises(ValueError, match="Objective cannot be empty"):
        planner.plan_outline("   ")
