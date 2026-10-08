"""Unit tests for Gate 1.3: Reflection auditing and evidence gap remedy loop."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock

from conflux_weave.long_horizon_research import (
    ContradictionFinding,
    DiscoveredSource,
    EvidenceGapFinding,
    InformationGapAuditor,
    ReflectionAuditReport,
    SearchGraphCrawler,
    build_deterministic_outline,
)


def test_deterministic_reflection_audit_detects_gaps_and_contradictions():
    outline = build_deterministic_outline("大模型量化技术")
    # Limited sources mentioning only basic terms and conflicting speedups
    sources = [
        DiscoveredSource(
            source_id="src-1",
            url="https://arxiv.org/abs/2401.0001",
            title="Quantization Study 1",
            snippet="Overview of INT4",
            full_text="Our INT4 quantization achieves 4.2x speedup on NVIDIA H100 with minimal loss.",
            authority_score=0.9,
            hop_depth=0,
        ),
        DiscoveredSource(
            source_id="src-2",
            url="https://arxiv.org/abs/2401.0002",
            title="Quantization Study 2",
            snippet="Production evaluation",
            full_text="In serving environments, real latency is only 1.3x faster due to dequantization overhead.",
            authority_score=0.9,
            hop_depth=0,
        ),
    ]

    auditor = InformationGapAuditor(chat_adapter=None)
    gaps, contras = auditor.audit_and_plan_remedy(outline, sources)

    # Must detect gaps for unrepresented chapters/probes
    assert len(gaps) > 0
    for g in gaps:
        assert isinstance(g, EvidenceGapFinding)
        assert len(g.remedy_query) > 0
        assert g.chapter_id.startswith("ch-")

    # Must detect speedup discrepancy (4.2x vs 1.3x)
    assert len(contras) > 0
    contra = contras[0]
    assert isinstance(contra, ContradictionFinding)
    assert "加速比" in contra.topic or "差异" in contra.topic
    assert len(contra.arbitration_guideline) > 0


def test_reflection_loop_executes_remedy_crawls():
    outline = build_deterministic_outline("深度强化学习长程推理")
    sources = [
        DiscoveredSource(
            source_id="src-init",
            url="https://arxiv.org/abs/2401.1111",
            title="Initial Paper",
            snippet="RL for reasoning",
            full_text="PPO versus GRPO comparison notes.",
            authority_score=0.85,
            hop_depth=0,
        )
    ]

    crawled_queries: list[str] = []

    async def mock_search(query: str, limit: int = 4):
        crawled_queries.append(query)
        return [
            {
                "url": f"https://arxiv.org/abs/2402.{abs(hash(query)) % 1000}",
                "title": f"Remedy paper for {query}",
                "snippet": "Remedy evidence content",
            }
        ]

    async def mock_fetcher(url: str):
        return ("Remedy Paper", "Detailed experimental data and benchmark proofs.")

    crawler = SearchGraphCrawler(
        max_sources=20,
        max_depth=1,
        concurrency=2,
        fetcher=mock_fetcher,
        search_engine=mock_search,
    )

    auditor = InformationGapAuditor(chat_adapter=None)

    async def run_loop():
        enriched_sources, report = await auditor.execute_reflection_loop(
            outline, sources, crawler, max_remedy_queries=3
        )
        assert len(enriched_sources) > 1
        assert isinstance(report, ReflectionAuditReport)
        assert len(report.executed_remedy_queries) > 0
        assert report.audit_passed is True

        # Check serialization roundtrip
        data = report.to_dict()
        restored = ReflectionAuditReport.from_dict(data)
        assert restored.audit_id == report.audit_id
        assert len(restored.gap_findings) == len(report.gap_findings)

    asyncio.run(run_loop())
    assert len(crawled_queries) > 0


def test_llm_reflection_audit_with_mock():
    mock_payload = {
        "gap_findings": [
            {
                "chapter_id": "ch-3",
                "probe_id": "p-3-1",
                "aspect_description": "缺少针对混合精度架构的直接评测",
                "severity": "high",
                "remedy_query": "混合精度架构 benchmark ablation",
            }
        ],
        "contradiction_findings": [
            {
                "topic": "吞吐量差异",
                "claim_a": "A报告提升3倍",
                "source_a_url": "https://url1",
                "claim_b": "B报告无提升",
                "source_b_url": "https://url2",
                "arbitration_guideline": "结合硬件平台说明分歧原因",
            }
        ],
        "audit_passed": False,
    }

    mock_adapter = MagicMock()
    mock_adapter.chat.return_value = json.dumps(mock_payload)

    auditor = InformationGapAuditor(chat_adapter=mock_adapter)
    outline = build_deterministic_outline("测试主题")
    sources = [
        DiscoveredSource(
            source_id="src-0",
            url="https://url1",
            title="Paper 1",
            snippet="snippet",
            full_text="full text",
            authority_score=0.9,
            hop_depth=0,
        )
    ]

    gaps, contras = auditor.audit_and_plan_remedy(outline, sources)
    assert len(gaps) == 1
    assert gaps[0].probe_id == "p-3-1"
    assert gaps[0].remedy_query == "混合精度架构 benchmark ablation"
    assert len(contras) == 1
    assert contras[0].topic == "吞吐量差异"
