"""Tests for P7-B4: 记忆评估基准体系与小型真实评估集.

Covers:
1. 7 key evaluation dimensions:
   - PREFERENCE (用户偏好)
   - PROJECT_CONTEXT (项目技术约定)
   - SYNONYMS (同义表达与泛化)
   - CONFLICT (冲突检测)
   - EXPIRED (生命周期到期过滤)
   - UNRELATED_ZERO_INJECTION (无关查询零强行注入)
   - CROSS_PROJECT_ISOLATION (跨项目隔离零泄漏)
2. Quantitative quality metrics:
   - Recall@1, Recall@3, Recall@5
   - MRR (Mean Reciprocal Rank)
   - Unrelated Injection Rate (Target: 0.0%)
   - Cross-Project Leak Rate (Target: 0.0%)
   - Expired Recall Rate (Target: 0.0%)
   - Conflict Detection Rate (Target: 1.0)
   - Injected Tokens (Budget <= 400)
   - Latency (mean, P95)
3. Markdown table rendering
4. FastAPI REST endpoint: POST /api/v1/memories/evaluate
"""

from __future__ import annotations

from pathlib import Path
import pytest
from starlette.testclient import TestClient

from conflux_weave.runtime.memory_store import HierarchicalMemoryStore
from conflux_weave.memory_recall import MemoryRecallService
from conflux_weave.memory_agent import MemoryAgent
from conflux_weave.memory_evaluation import (
    MemoryBenchmarkDimension,
    MemoryBenchmarkRunner,
    create_frozen_benchmark_fixtures,
)
from conflux_weave.server import create_app


def test_frozen_benchmark_dataset_structure():
    """验证冻结基准测试集涵盖全部 7 个核心评估维度。"""
    memories, cases = create_frozen_benchmark_fixtures()
    assert len(memories) >= 8
    assert len(cases) >= 11

    dimensions = {c.dimension for c in cases}
    assert dimensions == {
        MemoryBenchmarkDimension.PREFERENCE,
        MemoryBenchmarkDimension.PROJECT_CONTEXT,
        MemoryBenchmarkDimension.SYNONYMS,
        MemoryBenchmarkDimension.CONFLICT,
        MemoryBenchmarkDimension.EXPIRED,
        MemoryBenchmarkDimension.UNRELATED_ZERO_INJECTION,
        MemoryBenchmarkDimension.CROSS_PROJECT_ISOLATION,
    }


def test_memory_benchmark_offline_execution(tmp_path: Path):
    """验证离线确定性（词法+recency+规则）环境下的基准测试执行与指标达标。"""
    store = HierarchicalMemoryStore(tmp_path / "memory.sqlite3")
    recall_service = MemoryRecallService(store, embedder=None)
    agent = MemoryAgent(store, recall_service=recall_service)

    runner = MemoryBenchmarkRunner(store, recall_service=recall_service, agent=agent)
    report = runner.run_benchmark()

    # 验证门禁全绿通过
    assert report.passed is True
    m = report.metrics

    # 1. 核心安全性指标：必须绝对为 0
    assert m.unrelated_injection_rate == 0.0, "无关查询不得注入任何记忆"
    assert m.cross_project_leak_rate == 0.0, "项目间记忆不得发生跨域泄漏"
    assert m.expired_recall_rate == 0.0, "已过期记忆不得被召回"

    # 2. 冲突检测覆盖率必须为 100%
    assert m.conflict_detection_rate == 1.0, "与活跃记忆冲突的候选必须被 100% 识别"

    # 3. 召回效果指标
    assert m.recall_at_1 >= 0.50
    assert m.recall_at_3 >= 0.80
    assert m.recall_at_5 >= 0.80
    assert m.mrr >= 0.70

    # 4. Prompt 注入预算保护 (<= 400 tokens / 1200 字符)
    assert m.estimated_injected_tokens <= 400.0

    # 5. 延迟合理
    assert m.mean_latency_ms >= 0.0
    assert m.p95_latency_ms >= 0.0

    # 6. 表格渲染测试
    md = report.format_markdown_table()
    assert "### 记忆基准评估报告" in md
    assert "PASS ✅" in md
    assert "Recall@3" in md
    assert "无关注入率" in md
    assert "跨项目泄漏率" in md


def test_memory_evaluate_rest_api(tmp_path: Path):
    """验证 POST /api/v1/memories/evaluate REST 端点契约。"""
    from conflux_weave.runtime.artifacts import LocalArtifactStore
    from conflux_weave.runtime.sqlite import SQLiteRuntimeRepository
    from conflux_weave.harness.orchestration import CompositeOrchestrator, UnavailableTaskRuntime

    store = LocalArtifactStore(tmp_path / "artifacts")
    repository = SQLiteRuntimeRepository(tmp_path / "runtime.sqlite3", store)
    dummy_runtime = UnavailableTaskRuntime(
        repository,
        executor_id="dummy",
        task_kinds=("paper_discovery",),
        message="dummy",
    )
    orchestrator = CompositeOrchestrator(repository, (dummy_runtime,))

    app = create_app(
        repository,
        orchestrator,
        enable_worker=False,
    )
    client = TestClient(app)

    resp = client.post("/api/v1/memories/evaluate")
    assert resp.status_code == 200
    data = resp.json()

    assert data["passed"] is True
    assert data["total_cases"] >= 11
    assert data["passed_cases"] == data["total_cases"]
    assert data["unrelated_injection_rate"] == 0.0
    assert data["cross_project_leak_rate"] == 0.0
    assert data["expired_recall_rate"] == 0.0
    assert data["conflict_detection_rate"] == 1.0
    assert data["recall_at_3"] >= 0.80
    assert data["mrr"] >= 0.70
    assert len(data["case_results"]) == data["total_cases"]
