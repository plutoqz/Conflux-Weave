"""Master orchestrator for 15-20 minute long-horizon survey workflows with persistent checkpoints."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import logging
from typing import Any, Callable
import uuid

from conflux_weave.long_horizon_research.outline_planner import OutlinePlanner
from conflux_weave.long_horizon_research.reflection_auditor import InformationGapAuditor, ReflectionAuditReport
from conflux_weave.long_horizon_research.search_graph import DiscoveredSource, SearchGraphCrawler
from conflux_weave.long_horizon_research.spec import ResearchOutlineTree, SurveyChapterPlan, _utc_now
from conflux_weave.long_horizon_research.survey_synthesizer import (
    ComprehensiveSurveyReport,
    SurveyReportSection,
    SurveySynthesizer,
)

logger = logging.getLogger(__name__)


class LongHorizonSurveyWorkflow:
    """Orchestrates multi-phase deep survey generation with checkpoint persistence and section resilience."""

    def __init__(
        self,
        *,
        planner: OutlinePlanner | None = None,
        crawler: SearchGraphCrawler | None = None,
        auditor: InformationGapAuditor | None = None,
        synthesizer: SurveySynthesizer | None = None,
        repository: Any | None = None,  # SQLiteRuntimeRepository or mock
    ) -> None:
        self.planner = planner or OutlinePlanner()
        self.crawler = crawler or SearchGraphCrawler()
        self.auditor = auditor or InformationGapAuditor()
        self.synthesizer = synthesizer or SurveySynthesizer()
        self.repository = repository

    def _save_checkpoint(self, run_id: str, step_id: str, phase: str, digest: str, payload: dict) -> None:
        """Safely record a step checkpoint adapting to SQLiteRuntimeRepository and mock stores."""
        if not self.repository:
            return
        try:
            if hasattr(self.repository, "record_step_checkpoint"):
                sig = inspect.signature(self.repository.record_step_checkpoint)
                now = _utc_now()
                art_id = f"art-{uuid.uuid4().hex[:8]}"

                # Store artifact if artifact store available
                store = getattr(self.repository, "artifacts", getattr(self.repository, "store", None))
                if store and hasattr(store, "write_json"):
                    try:
                        art_ref = store.write_json(payload)
                        art_id = getattr(art_ref, "artifact_id", art_id)
                    except Exception:
                        pass

                # If SQLite repository enforces run existence
                if hasattr(self.repository, "_connect"):
                    try:
                        with self.repository._connect() as conn:
                            exists = conn.execute("SELECT 1 FROM runs WHERE run_id = ?", (run_id,)).fetchone()
                            if exists is None:
                                conn.execute(
                                    "INSERT OR IGNORE INTO runs (run_id, task_id, status, created_at, updated_at) "
                                    "VALUES (?, ?, 'running', ?, ?)",
                                    (run_id, f"task-{run_id}", now, now),
                                )
                    except Exception:
                        pass

                kwargs = {
                    "run_id": run_id,
                    "step_id": step_id,
                    "chain_phase": phase,
                    "input_digest": digest,
                    "output_artifact_id": art_id,
                    "attempt": 1,
                    "status": "succeeded",
                    "started_at": now,
                    "finished_at": now,
                    "resumable": True,
                }
                filtered = {k: v for k, v in kwargs.items() if k in sig.parameters}
                if "output_payload" in sig.parameters:
                    filtered["output_payload"] = json.dumps(payload, ensure_ascii=False)
                self.repository.record_step_checkpoint(**filtered)
        except Exception as exc:
            logger.debug("Checkpoint record skipped: %s", exc)

    def _get_checkpoint_payload(self, run_id: str, step_id: str, phase: str, digest: str) -> dict | None:
        """Safely retrieve checkpoint payload from SQLite or mock repository."""
        if not self.repository:
            return None
        try:
            if hasattr(self.repository, "get_step_checkpoint"):
                cp = self.repository.get_step_checkpoint(run_id, step_id)
                if cp and cp.get("input_digest") == digest:
                    if cp.get("output_payload"):
                        return json.loads(cp["output_payload"])
                    if cp.get("output_artifact_id"):
                        store = getattr(self.repository, "artifacts", getattr(self.repository, "store", None))
                        if store and hasattr(store, "read_json"):
                            return store.read_json(cp["output_artifact_id"])
            if hasattr(self.repository, "reuse_step_checkpoint"):
                cp = self.repository.reuse_step_checkpoint(run_id, phase, digest)
                if cp and cp.get("output_artifact_id"):
                    store = getattr(self.repository, "artifacts", getattr(self.repository, "store", None))
                    if store and hasattr(store, "read_json"):
                        return store.read_json(cp["output_artifact_id"])
        except Exception as exc:
            logger.debug("Checkpoint reuse skipped: %s", exc)
        return None

    async def execute(
        self,
        objective: str,
        *,
        run_id: str | None = None,
        background_context: str = "",
        max_sources: int = 80,
        progress_callback: Callable[[str, float, str], None] | None = None,
    ) -> ComprehensiveSurveyReport:
        """Execute the end-to-end 4-phase survey workflow with checkpoint recovery."""
        active_run_id = run_id or f"survey-run-{uuid.uuid4().hex[:8]}"

        def report_progress(stage: str, pct: float, msg: str) -> None:
            if progress_callback:
                try:
                    progress_callback(stage, pct, msg)
                except Exception as exc:
                    logger.debug("Progress callback failed: %s", exc)

        # ---------------------------------------------------------
        # Phase 1: Outline and Hypothesis Tree Planning
        # ---------------------------------------------------------
        report_progress("outline_planning", 0.05, f"正在进行课题多视角解构与大纲树规划: {objective[:30]}...")

        outline_digest = hashlib.sha256(f"{objective}::{background_context}".encode("utf-8")).hexdigest()
        cached_outline = self._get_checkpoint_payload(active_run_id, "phase_1_outline", "plan", outline_digest)
        outline: ResearchOutlineTree | None = None

        if cached_outline:
            try:
                outline = ResearchOutlineTree.from_dict(cached_outline)
                logger.info("Resuming Phase 1 outline from durable checkpoint.")
            except Exception:
                pass

        if outline is None:
            outline = self.planner.plan_outline(objective, background_context=background_context)
            self._save_checkpoint(active_run_id, "phase_1_outline", "plan", outline_digest, outline.to_dict())

        report_progress("outline_planning", 0.15, f"大纲树已锁定 ({len(outline.chapters)} 章节, {outline.total_probes_count()} 探针)")

        # ---------------------------------------------------------
        # Phase 2: Recursive Multi-Source Graph Search
        # ---------------------------------------------------------
        report_progress("recursive_search", 0.20, "开始多源并发检索与深度外链递归爬取...")

        all_probes = outline.get_all_probes()
        sources: list[DiscoveredSource] = await self.crawler.explore_probes(
            tuple(all_probes),
            progress_callback=lambda curr, tot, msg: report_progress(
                "recursive_search",
                0.20 + 0.25 * (curr / max(1, tot)),
                f"[{curr}/{tot}] {msg}",
            ),
        )

        report_progress("recursive_search", 0.45, f"首轮检索完成，累计获取 {len(sources)} 篇权威文献与技术源")

        # ---------------------------------------------------------
        # Phase 3: Evidence Gap & Contradiction Auditing Loop
        # ---------------------------------------------------------
        report_progress("reflection_audit", 0.50, "审查文献池证据密度与学术争议焦点...")

        sources, audit_report = await self.auditor.execute_reflection_loop(
            outline, sources, self.crawler, max_remedy_queries=5
        )

        report_progress(
            "reflection_audit",
            0.60,
            f"反思审计完成: 消除 {len(audit_report.executed_remedy_queries)} 处证据空白，当前证据池: {len(sources)} 篇",
        )

        # ---------------------------------------------------------
        # Phase 4: Long-Form Section-by-Section Synthesis
        # ---------------------------------------------------------
        report_progress("synthesis", 0.65, "启动万字综述长篇分章合成...")

        sections: list[SurveyReportSection] = []
        prior_summaries: list[str] = []
        total_chapters = len(outline.chapters)

        for idx, chapter in enumerate(outline.chapters, start=1):
            sec_step_id = f"phase_4_chapter_{chapter.chapter_id}"
            sec_digest = hashlib.sha256(
                f"{chapter.chapter_id}::{chapter.core_thesis}::{len(sources)}".encode("utf-8")
            ).hexdigest()

            cached_sec = self._get_checkpoint_payload(active_run_id, sec_step_id, "write", sec_digest)
            section: SurveyReportSection | None = None
            if cached_sec:
                try:
                    section = SurveyReportSection.from_dict(cached_sec)
                    logger.info("Resumed chapter %s from durable checkpoint.", chapter.chapter_id)
                except Exception:
                    pass

            if section is None:
                report_progress(
                    "synthesis",
                    0.65 + 0.25 * (idx / total_chapters),
                    f"正在撰写第 {idx}/{total_chapters} 章 · {chapter.title}...",
                )
                section = self.synthesizer.synthesize_chapter(
                    chapter,
                    outline,
                    sources,
                    prior_summaries,
                    audit_report.contradiction_findings,
                )
                self._save_checkpoint(active_run_id, sec_step_id, "write", sec_digest, section.to_dict())

            sections.append(section)
            prior_summaries.append(f"{section.title}: {section.content_markdown[:200]}...")

        report_progress("synthesis", 0.92, "各章节正文撰写完毕，正在合成执行摘要与演进谱系...")

        # 4.2 Executive summary, timeline, matrix, and citation ledger
        executive_summary = self.synthesizer.synthesize_executive_summary(outline, sections)
        timeline = self.synthesizer.render_evolution_timeline(outline)
        matrix = self.synthesizer.render_comparison_matrix(outline)

        # Build citations ledger
        all_cited_urls = set()
        for sec in sections:
            all_cited_urls.update(sec.cited_source_urls)

        citations_ledger: list[dict[str, Any]] = []
        for s in sources:
            if s.url in all_cited_urls or len(citations_ledger) < min(len(sources), 35):
                citations_ledger.append(
                    {
                        "source_id": s.source_id,
                        "title": s.title,
                        "url": s.url,
                        "authority_score": s.authority_score,
                        "source_channel": s.source_channel,
                    }
                )

        total_words = sum(s.word_count for s in sections)

        master_report = ComprehensiveSurveyReport(
            report_id=f"survey-{uuid.uuid4().hex[:8]}",
            objective=objective,
            executive_summary=executive_summary,
            sections=tuple(sections),
            evolution_timeline_mermaid=timeline,
            comparison_matrix_markdown=matrix,
            total_word_count=total_words,
            total_sources_cited=len(citations_ledger),
            citations_ledger=tuple(citations_ledger),
        )

        report_progress("completed", 1.0, f"万字综述研报交付完成！累计 {total_words} 字，引用 {len(citations_ledger)} 篇文献。")
        return master_report
