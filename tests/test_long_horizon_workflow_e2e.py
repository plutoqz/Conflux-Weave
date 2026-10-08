"""Unit tests for Gate 1.4: End-to-end long-horizon survey workflow and checkpoint recovery."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock

from conflux_weave.long_horizon_research import (
    ComprehensiveSurveyReport,
    DiscoveredSource,
    InformationGapAuditor,
    LongHorizonSurveyWorkflow,
    OutlinePlanner,
    SearchGraphCrawler,
    SurveyReportSection,
    SurveySynthesizer,
    build_deterministic_outline,
)


class MockRuntimeRepository:
    """Mock repository capturing step checkpoints in memory."""

    def __init__(self) -> None:
        self.checkpoints: dict[tuple[str, str], dict] = {}

    def get_step_checkpoint(self, run_id: str, step_id: str) -> dict | None:
        return self.checkpoints.get((run_id, step_id))

    def record_step_checkpoint(
        self,
        run_id: str,
        step_id: str,
        chain_phase: str,
        input_digest: str,
        output_payload: str,
        resumable: bool = True,
    ) -> None:
        self.checkpoints[(run_id, step_id)] = {
            "run_id": run_id,
            "step_id": step_id,
            "chain_phase": chain_phase,
            "input_digest": input_digest,
            "output_payload": output_payload,
            "resumable": resumable,
        }


def test_long_horizon_survey_workflow_end_to_end():
    progress_records: list[tuple[str, float, str]] = []

    def on_progress(stage: str, pct: float, msg: str) -> None:
        progress_records.append((stage, pct, msg))

    # Fast stub crawler
    async def fast_search(q: str, limit: int = 2):
        return [
            {
                "url": f"https://arxiv.org/abs/2401.{abs(hash(q)) % 1000}",
                "title": f"Paper for {q[:20]}",
                "snippet": "High authority technical content.",
            }
        ]

    async def fast_fetcher(u: str):
        return ("Paper Title", "Evaluation benchmarks and ablation analysis.")

    crawler = SearchGraphCrawler(
        max_sources=15,
        max_depth=1,
        concurrency=4,
        fetcher=fast_fetcher,
        search_engine=fast_search,
    )

    repo = MockRuntimeRepository()
    wf = LongHorizonSurveyWorkflow(
        planner=OutlinePlanner(chat_adapter=None),
        crawler=crawler,
        auditor=InformationGapAuditor(chat_adapter=None),
        synthesizer=SurveySynthesizer(chat_adapter=None),
        repository=repo,
    )

    async def run_e2e():
        report = await wf.execute(
            objective="多模态大模型长程视觉推理架构综述",
            run_id="run-test-e2e-1",
            progress_callback=on_progress,
        )
        return report

    report = asyncio.run(run_e2e())

    assert isinstance(report, ComprehensiveSurveyReport)
    assert report.objective == "多模态大模型长程视觉推理架构综述"
    assert len(report.sections) == 6
    assert report.total_word_count > 3000
    assert len(report.executive_summary) >= 3
    assert len(report.citations_ledger) > 0

    # Verify progress callback fired all stages
    stages = [r[0] for r in progress_records]
    assert "outline_planning" in stages
    assert "recursive_search" in stages
    assert "reflection_audit" in stages
    assert "synthesis" in stages
    assert "completed" in stages

    # Verify step checkpoints were recorded in repository
    assert ("run-test-e2e-1", "phase_1_outline") in repo.checkpoints
    for ch_idx in range(1, 7):
        assert ("run-test-e2e-1", f"phase_4_chapter_ch-{ch_idx}") in repo.checkpoints


def test_long_horizon_survey_workflow_checkpoint_recovery():
    """Verify that an interrupted run reuses prior chapter checkpoints without resynthesizing."""
    repo = MockRuntimeRepository()

    # Pre-populate checkpoint for chapter 1 and chapter 2
    mock_sec1 = SurveyReportSection(
        chapter_id="ch-1",
        chapter_index=1,
        title="一、研究背景与核心概念",
        content_markdown="[已从断点恢复的第1章正文内容]",
        word_count=500,
        cited_source_urls=("https://arxiv.org/abs/1",),
    )
    mock_sec2 = SurveyReportSection(
        chapter_id="ch-2",
        chapter_index=2,
        title="二、理论基石与技术演化沿革",
        content_markdown="[已从断点恢复的第2章正文内容]",
        word_count=500,
        cited_source_urls=("https://arxiv.org/abs/2",),
    )

    outline = build_deterministic_outline("大模型断点恢复综述")
    import hashlib

    sec1_digest = hashlib.sha256(f"ch-1::{outline.chapters[0].core_thesis}::1".encode("utf-8")).hexdigest()
    sec2_digest = hashlib.sha256(f"ch-2::{outline.chapters[1].core_thesis}::1".encode("utf-8")).hexdigest()

    repo.record_step_checkpoint(
        run_id="run-resume-1",
        step_id="phase_4_chapter_ch-1",
        chain_phase="write",
        input_digest=sec1_digest,
        output_payload=json.dumps(mock_sec1.to_dict(), ensure_ascii=False),
    )
    repo.record_step_checkpoint(
        run_id="run-resume-1",
        step_id="phase_4_chapter_ch-2",
        chain_phase="write",
        input_digest=sec2_digest,
        output_payload=json.dumps(mock_sec2.to_dict(), ensure_ascii=False),
    )

    # Mock synthesizer to spy on calls
    spy_synthesizer = SurveySynthesizer(chat_adapter=None)
    original_synth = spy_synthesizer.synthesize_chapter
    synthesized_chapters: list[str] = []

    def tracking_synth(ch, out, src, prior, contra):
        synthesized_chapters.append(ch.chapter_id)
        return original_synth(ch, out, src, prior, contra)

    spy_synthesizer.synthesize_chapter = tracking_synth

    # Minimal crawler
    async def dummy_search(q, limit=1):
        return [{"url": "https://arxiv.org/abs/1", "title": "T", "snippet": "S"}]

    crawler = SearchGraphCrawler(
        max_sources=1,
        max_depth=0,
        fetcher=lambda u: asyncio.sleep(0, result=("T", "C")),
        search_engine=dummy_search,
    )

    wf = LongHorizonSurveyWorkflow(
        planner=OutlinePlanner(chat_adapter=None),
        crawler=crawler,
        auditor=InformationGapAuditor(chat_adapter=None),
        synthesizer=spy_synthesizer,
        repository=repo,
    )

    async def run_resumed():
        return await wf.execute(
            objective="大模型断点恢复综述",
            run_id="run-resume-1",
        )

    report = asyncio.run(run_resumed())

    # Chapters 1 and 2 were recovered directly from checkpoints and NEVER invoked tracking_synth!
    assert "ch-1" not in synthesized_chapters
    assert "ch-2" not in synthesized_chapters
    # Chapters 3, 4, 5, 6 were synthesized freshly
    assert "ch-3" in synthesized_chapters
    assert "ch-4" in synthesized_chapters

    # Ensure final report includes the recovered chapter content
    assert "[已从断点恢复的第1章正文内容]" in report.sections[0].content_markdown
    assert "[已从断点恢复的第2章正文内容]" in report.sections[1].content_markdown
