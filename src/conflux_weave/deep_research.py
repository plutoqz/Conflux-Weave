"""Deep research engine (W3.2 模式 C): GPT Researcher as discovery/aggregation
engine behind the evidence bridge.

GPT Researcher NEVER becomes a citation authority: its sources enter the local
SourceSnapshot -> Evidence ledger, claims are drafted and verified by the local
chain, and the delivery is produced by the local Writer v2 with deterministic
fallback. Its own markdown report is archived as a secondary view artifact.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

from conflux_weave.core import DeliveryDisposition
from conflux_weave.evidence import (
    AssessmentVerdict,
    Citation,
    Claim,
    EvidenceRef,
    EvidenceRelation,
    SourceTrustLevel,
    render_report_document,
    require_closed_citations,
)
from conflux_weave.provider import OpenAICompatibleChatAdapter, ProviderConfig
from conflux_weave.report_writer import (
    WriterOutcome,
    build_deterministic_document,
    compose_report_document,
    distill_evidence_cards,
)
from conflux_weave.research_agents import VerifiedResearchWorkflow
from conflux_weave.runtime import LocalArtifactStore


DEEP_RESEARCH_SCHEMA = "conflux-weave.deep-research-manifest.v1"
DEEP_REPORT_SCHEMA = "conflux-weave.deep-research-report.v2"
SNAPSHOT_SCHEMA = "conflux-weave.source-snapshot.v1"
EVIDENCE_CHARS = 6000
MAX_SOURCES = 10
MAX_LOCAL_DOCUMENTS = 8


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class DeepSource:
    url: str
    title: str
    content: str


@dataclass(frozen=True, slots=True)
class DeepResearchResult:
    sources: tuple[DeepSource, ...]
    context: str
    report_markdown: str
    planned_queries: tuple[str, ...]
    costs_usd: float
    token_usage: dict[str, int] = field(default_factory=dict)
    report_source: str = "web"


@dataclass(frozen=True, slots=True)
class DeepResearchExecution:
    report_artifact_id: str
    manifest_artifact_id: str
    claims: tuple[Claim, ...]
    evidence: tuple[EvidenceRef, ...]
    citations: tuple[Citation, ...]
    usage: dict[str, int]
    provider_call_count: int
    costs_usd: float
    limitations: tuple[str, ...] = ()
    unmet_criteria: tuple[str, ...] = ()
    disposition: DeliveryDisposition = DeliveryDisposition.COMPLETE


class GPTResearcherBridge:
    """In-process GPT Researcher runner with provider env mapping."""

    def __init__(
        self,
        provider_config: ProviderConfig,
        retrieval=None,
        *,
        retriever: str | None = None,
        max_local_documents: int = MAX_LOCAL_DOCUMENTS,
    ) -> None:
        self._provider_config = provider_config
        self._retrieval = retrieval
        self._retriever = retriever
        self._max_local_documents = max_local_documents

    def execute(self, objective: str, *, on_progress: Callable[[str], None] | None = None) -> DeepResearchResult:
        try:
            from gpt_researcher import GPTResearcher
        except ImportError as exc:  # pragma: no cover - optional engine
            raise RuntimeError(f"GPT Researcher is not installed: {exc}") from exc

        progress = on_progress or (lambda message: None)
        model = f"openai/{self._provider_config.model}"
        retriever = self._retriever or ("tavily" if os.environ.get("TAVILY_API_KEY") else "duckduckgo")
        env_patches = {
            "OPENAI_API_KEY": self._provider_config.api_key,
            "OPENAI_BASE_URL": self._provider_config.base_url,
            "EMBEDDING_PROVIDER": "openai",
            "OPENAI_EMBEDDING_MODEL": model,
        }
        saved = {key: os.environ.get(key) for key in env_patches}
        os.environ.update(env_patches)
        temp_dir = None
        try:
            local_chunks = self._local_chunks(objective)
            config_payload = {
                "SMART_LLM_MODEL": model,
                "FAST_LLM_MODEL": model,
                "STRATEGIC_LLM_MODEL": model,
                "RETRIEVER": retriever,
                "MAX_SEARCH_RESULTS_PER_QUERY": 4,
            }
            if local_chunks:
                temp_dir = Path(_utc_now().replace(":", "").replace("-", ""))  # 占位，真实目录在下方创建
                import tempfile

                temp_dir = Path(tempfile.mkdtemp(prefix="cw-deep-docs-"))
                for filename, text in local_chunks:
                    (temp_dir / filename).write_text(text, encoding="utf-8")
                config_payload["DOC_PATH"] = str(temp_dir)
            config_file = Path(tempfile.mkstemp(prefix="cw-gptr-config-", suffix=".json")[1])
            config_file.write_text(json.dumps(config_payload), encoding="utf-8")

            progress(f"启动深度研究引擎（本地文档 {len(local_chunks)}，检索器 {retriever}）")
            report_source = "hybrid" if local_chunks else "web"
            researcher = GPTResearcher(
                query=objective,
                report_type="research_report",
                report_source=report_source,
                config_path=str(config_file),
            )

            async def run() -> tuple[str, str]:
                await researcher.conduct_research(on_progress=lambda log: progress(str(log)))
                report = await researcher.write_report()
                return report, researcher.get_research_context()

            report, context = asyncio.run(run())
            sources = [
                DeepSource(
                    url=str(item.get("url", "")),
                    title=str(item.get("title") or item.get("url") or "未命名来源"),
                    content=str(item.get("content", "")),
                )
                for item in researcher.get_research_sources()
            ]
            return DeepResearchResult(
                sources=tuple(sources),
                context=context,
                report_markdown=report,
                planned_queries=tuple(researcher.get_source_urls()[:0]) or (),
                costs_usd=float(researcher.get_costs() or 0.0),
                token_usage=self._token_usage(),
                report_source=report_source,
            )
        finally:
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
            if temp_dir is not None:
                import shutil

                shutil.rmtree(temp_dir, ignore_errors=True)

    def _token_usage(self) -> dict[str, int]:
        """Best-effort token accounting via the litellm success callback hook."""

        try:
            import litellm

            tracker = _TokenTracker()
            counts = tracker.consume()
            return counts
        except Exception:  # noqa: BLE001 - 记账失败不阻断
            return {"input_tokens": 0, "output_tokens": 0, "calls": 0}

    def _local_chunks(self, objective: str) -> list[tuple[str, str]]:
        """本地语料 Top-N chunk → (文件名, 正文)，经 DOC_PATH 进入 hybrid 检索。"""
        if self._retrieval is None:
            return []
        run = self._retrieval.search(objective)
        chunks = []
        for hit in run.final.hits[: self._max_local_documents]:
            chunk = self._retrieval.document_by_id[hit.document_id]
            snapshot = (hit.source_snapshot_id or hit.document_id).replace(":", "-")[:40]
            chunks.append((f"local-{snapshot}.txt", chunk.text))
        return chunks


class _TokenTracker:
    """litellm callback accumulator (best-effort; silent on any mismatch)."""

    def __init__(self) -> None:
        self.input_tokens = 0
        self.output_tokens = 0
        self.calls = 0
        try:
            import litellm

            litellm.success_callback.append(self._record)
        except Exception:  # noqa: BLE001
            pass

    def _record(self, kwargs, response, start_time, end_time):  # noqa: ANN001
        try:
            usage = getattr(response, "usage", None)
            if usage is not None:
                self.input_tokens += int(getattr(usage, "prompt_tokens", 0) or 0)
                self.output_tokens += int(getattr(usage, "completion_tokens", 0) or 0)
            self.calls += 1
        except Exception:  # noqa: BLE001
            pass

    def consume(self) -> dict[str, int]:
        try:
            import litellm

            if self._record in litellm.success_callback:
                litellm.success_callback.remove(self._record)
        except Exception:  # noqa: BLE001
            pass
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "calls": self.calls,
        }


class DeepResearchWorkflow:
    """发现聚合（GPT Researcher）→ 快照台账 → 本地 claim 链路 → Writer v2。"""

    def __init__(
        self,
        store: LocalArtifactStore,
        chat: OpenAICompatibleChatAdapter,
        bridge: GPTResearcherBridge,
        *,
        code_revision: str = "unknown",
    ) -> None:
        self.store = store
        self.chat = chat
        self.bridge = bridge
        self.code_revision = code_revision
        self._verified = VerifiedResearchWorkflow(store, None, chat, corpus_scope="web+local hybrid (GPT Researcher)")

    def execute(self, objective: str, *, max_sources: int = MAX_SOURCES) -> DeepResearchExecution:
        normalized = objective.strip()
        if not normalized:
            raise ValueError("objective must not be empty")
        step_id = "w32-deep-research"
        result = self.bridge.execute(normalized)
        sources = list(result.sources)[: max_sources]

        snapshot_records = []
        evidence = []
        for index, source in enumerate(sources, 1):
            content = source.content.strip() or source.title
            content_artifact = self.store.put_bytes(
                content.encode("utf-8"),
                media_type="text/markdown; charset=utf-8",
                producer_step_id=step_id,
                schema_version="conflux-weave.web-source-content.v1",
            )
            content_hash = f"sha256-{content_artifact.content_hash}"
            snapshot_artifact = self.store.put_json(
                {
                    "schema_version": SNAPSHOT_SCHEMA,
                    "source_id": f"web-{index:04d}",
                    "source_type": "web_page",
                    "canonical_uri": source.url,
                    "acquired_at": _utc_now(),
                    "content_hash": content_hash,
                    "artifact_ref": content_artifact.artifact_id,
                    "title": source.title,
                    "acquisition_boundary": "content delivered by the aggregation engine; page not re-crawled",
                },
                producer_step_id=step_id,
                schema_version=SNAPSHOT_SCHEMA,
            )
            snapshot_records.append(
                {
                    "source_id": f"web-{index:04d}",
                    "url": source.url,
                    "title": source.title,
                    "content_hash": content_hash,
                    "snapshot_artifact": snapshot_artifact.artifact_id,
                }
            )
            evidence.append(
                EvidenceRef(
                    f"evidence-{index:04d}",
                    f"web-{index:04d}",
                    {"type": "web_page", "url": source.url, "title": source.title},
                    content[:EVIDENCE_CHARS],
                    "gpt-researcher-aggregation-v1",
                )
            )
        if not evidence:
            raise ValueError("deep research engine returned no usable sources")

        claims, _draft_refs = self._verified._draft(objective, evidence, repair=False)
        disposition = DeliveryDisposition.COMPLETE
        unmet: tuple[str, ...] = ()
        if not claims:
            return self._no_answer(evidence, snapshot_records, result, "no candidate claims")
        assessments, _verify_refs = self._verified._verify(claims, evidence, round_number=0)
        repair_rounds = 0
        if any(item.verdict is not AssessmentVerdict.ACCEPTED for item in assessments):
            repair_rounds = 1
            claims, _repair_refs = self._verified._draft(
                objective, evidence, repair=True, prior_claims=claims, assessments=assessments
            )
            assessments, _reverify_refs = self._verified._verify(claims, evidence, round_number=1)
        accepted_ids = {
            item.claim_id
            for item in assessments
            if item.verdict is AssessmentVerdict.ACCEPTED and item.relation is EvidenceRelation.SUPPORTS
        }
        accepted_claims = tuple(claim for claim in claims if claim.claim_id in accepted_ids)
        if not accepted_claims:
            return self._no_answer(evidence, snapshot_records, result, "no accepted claims after verification")
        accepted_assessments = tuple(item for item in assessments if item.claim_id in accepted_ids)
        allowed_evidence = {
            evidence_id for item in accepted_assessments for evidence_id in item.evidence_ids
        }
        accepted_evidence = tuple(item for item in evidence if item.evidence_id in allowed_evidence)
        citations = tuple(
            Citation(f"citation-{index:04d}", claim.claim_id, evidence_id, index)
            for index, (claim, evidence_id) in enumerate(
                (
                    (claim, evidence_id)
                    for claim in accepted_claims
                    for evidence_id in next(
                        item.evidence_ids for item in accepted_assessments if item.claim_id == claim.claim_id
                    )
                ),
                1,
            )
        )
        require_closed_citations(accepted_claims, accepted_evidence, citations)

        limitations = (
            "GPT Researcher 仅用于来源发现与聚合；引用权威为本地 Claim/Evidence 验证链。",
            "来源快照内容为聚合引擎交付正文，未重新爬取页面。",
        )
        distill = distill_evidence_cards(self.store, self.chat, accepted_claims, accepted_evidence, citations)
        writer: WriterOutcome = compose_report_document(
            self.store,
            self.chat,
            objective,
            accepted_claims,
            accepted_evidence,
            citations,
            cards=distill.cards if distill.status == "ok" else (),
        )
        if writer.status == "fallback":
            limitations += ("报告正文为确定性组装的已验证 Claim 原文（模型写作未通过校验轮次），已完整覆盖全部核验结论。",)
        document = writer.document if writer.document is not None else build_deterministic_document(objective, accepted_claims)
        report = render_report_document(
            title=objective,
            document=document,
            claims=accepted_claims,
            evidence=accepted_evidence,
            citations=citations,
            evidence_trust={item.evidence_id: SourceTrustLevel.GENERAL_SOURCE for item in accepted_evidence},
            limitations=limitations,
        )
        report_ref = self.store.put_bytes(
            report.encode("utf-8"),
            media_type="text/markdown; charset=utf-8",
            producer_step_id=step_id,
            schema_version=DEEP_REPORT_SCHEMA,
        )
        secondary_ref = self.store.put_bytes(
            ("# GPT Researcher 原始报告（第二视图，非交付）\n\n" + result.report_markdown).encode("utf-8"),
            media_type="text/markdown; charset=utf-8",
            producer_step_id=step_id,
            schema_version="conflux-weave.gpt-researcher-raw-report.v1",
        )
        provider_call_count = len(evidence) + 1
        usage = {
            "input_tokens": int(result.token_usage.get("input_tokens", 0)),
            "output_tokens": int(result.token_usage.get("output_tokens", 0)),
            "tool_calls": provider_call_count,
            "retrieval_rounds": 1,
        }
        manifest_ref = self.store.put_json(
            {
                "schema_version": DEEP_RESEARCH_SCHEMA,
                "objective": objective,
                "engine": {
                    "name": "gpt-researcher",
                    "report_source": result.report_source,
                    "costs_usd": result.costs_usd,
                },
                "code_revision": self.code_revision,
                "disposition": disposition.value,
                "sources": snapshot_records,
                "raw_report_artifact": secondary_ref.artifact_id,
                "report_artifact": report_ref.artifact_id,
                "report_contract": "v2",
                "writer_status": writer.status,
                "writer_degrade_reason": writer.reason if writer.status == "fallback" else None,
                "distill_status": distill.status,
                "coverage": {
                    "accepted_claim_count": len(accepted_claims),
                    "candidate_claim_count": len(claims),
                    "evidence_count": len(accepted_evidence),
                    "repair_rounds": repair_rounds,
                },
                "usage": usage,
                "provider_call_count": provider_call_count,
                "limitations": list(limitations),
                "unmet_criteria": list(unmet),
                "budget_semantics": "batch-opaque: internal engine calls are not individually checkpointed",
            },
            producer_step_id=step_id,
            schema_version=DEEP_RESEARCH_SCHEMA,
        )
        return DeepResearchExecution(
            report_artifact_id=report_ref.artifact_id,
            manifest_artifact_id=manifest_ref.artifact_id,
            claims=accepted_claims,
            evidence=accepted_evidence,
            citations=citations,
            usage=usage,
            provider_call_count=provider_call_count,
            costs_usd=result.costs_usd,
            limitations=limitations,
            unmet_criteria=unmet,
            disposition=disposition,
        )

    def _no_answer(self, evidence, snapshot_records, result, reason: str) -> DeepResearchExecution:
        limitations = (
            "GPT Researcher 仅用于来源发现与聚合；引用权威为本地 Claim/Evidence 验证链。",
            f"深度研究未产生可验证的核验结论：{reason}。",
        )
        # NO_ANSWER 交付：说明证据边界（与单 Agent no-answer 同语义）
        lines = [
            "# 深度研究（无核验结论）",
            "",
            "> 本次深度研究聚合了网络与本地来源，但未产生可通过 Verifier 的核验结论。",
            "",
            "## 限制",
            "",
        ]
        lines.extend(f"- {item}" for item in limitations)
        report = "\n".join(lines) + "\n"
        report_ref = self.store.put_bytes(
            report.encode("utf-8"),
            media_type="text/markdown; charset=utf-8",
            producer_step_id="w32-deep-research",
            schema_version=DEEP_REPORT_SCHEMA,
        )
        provider_call_count = len(evidence) + 1
        manifest_ref = self.store.put_json(
            {
                "schema_version": DEEP_RESEARCH_SCHEMA,
                "engine": {"name": "gpt-researcher", "costs_usd": result.costs_usd},
                "disposition": DeliveryDisposition.NO_ANSWER.value,
                "sources": snapshot_records,
                "report_artifact": report_ref.artifact_id,
                "report_contract": "no_answer",
                "usage": {
                    "input_tokens": int(result.token_usage.get("input_tokens", 0)),
                    "output_tokens": int(result.token_usage.get("output_tokens", 0)),
                    "tool_calls": provider_call_count,
                    "retrieval_rounds": 1,
                },
                "provider_call_count": provider_call_count,
                "limitations": list(limitations),
                "budget_semantics": "batch-opaque: internal engine calls are not individually checkpointed",
            },
            producer_step_id="w32-deep-research",
            schema_version=DEEP_RESEARCH_SCHEMA,
        )
        return DeepResearchExecution(
            report_artifact_id=report_ref.artifact_id,
            manifest_artifact_id=manifest_ref.artifact_id,
            claims=(),
            evidence=(),
            citations=(),
            usage={
                "input_tokens": int(result.token_usage.get("input_tokens", 0)),
                "output_tokens": int(result.token_usage.get("output_tokens", 0)),
                "tool_calls": provider_call_count,
                "retrieval_rounds": 1,
            },
            provider_call_count=provider_call_count,
            costs_usd=result.costs_usd,
            limitations=limitations,
            disposition=DeliveryDisposition.NO_ANSWER,
        )
