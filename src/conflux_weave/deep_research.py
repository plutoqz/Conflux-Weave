"""Deep research engine (W3.2 模式 C): GPT Researcher as discovery/aggregation
engine behind the evidence bridge.

GPT Researcher NEVER becomes a citation authority: its sources enter the local
SourceSnapshot -> Evidence ledger, claims are drafted and verified by the local
chain, and the delivery is produced by the local Writer v2 with deterministic
fallback. Its own markdown report is archived as a secondary view artifact.

交付语义（W3.2.1）：无本地核验结论 ≠ 无答案。起草/核验链没有产出可交付结论时，
以 PARTIAL 交付明确标注"未经本地核验"的引擎综合视图 + 来源引用清单；仅当引擎
与本地语料都没有产出任何可引用材料时才交付 NO_ANSWER。
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

from conflux_weave.core import DeliveryDisposition
from conflux_weave.documents import document_title_from_segments
from conflux_weave.engine_narrative import parse_engine_narrative
from conflux_weave.evidence import (
    AssessmentVerdict,
    Citation,
    Claim,
    EvidenceRef,
    EvidenceRelation,
    SourceTrustLevel,
    origin_lane,
    render_fused_report,
    render_report_document,
    require_closed_citations,
)
from conflux_weave.merge import plan_merge
from conflux_weave.provider import OpenAICompatibleChatAdapter, ProviderConfig
from conflux_weave.report_writer import (
    WriterOutcome,
    build_deterministic_document,
    compose_fused_report_document,
    compose_report_document,
    distill_evidence_cards,
)
from conflux_weave.research_agents import VerifiedResearchWorkflow
from conflux_weave.runtime import LocalArtifactStore


DEEP_RESEARCH_SCHEMA = "conflux-weave.deep-research-manifest.v1"
DEEP_REPORT_SCHEMA = "conflux-weave.deep-research-report.v3"
SNAPSHOT_SCHEMA = "conflux-weave.source-snapshot.v1"
EVIDENCE_CHARS = 6000
LOCAL_EVIDENCE_CHARS = 3600
MAX_SOURCES = 10
MAX_LOCAL_DOCUMENTS = 8
# 短于该长度的正文视作"仅标题"记录：只进快照/来源清单，不进证据台账。
MIN_EVIDENCE_CONTENT_CHARS = 200


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class DeepSource:
    url: str
    title: str
    content: str


@dataclass(frozen=True, slots=True)
class DeepLocalChunk:
    """本地语料检索命中：既写 DOC_PATH 供引擎聚合，也直接进入本地证据链。"""

    snapshot_id: str
    document_id: str
    locator: dict
    text: str


@dataclass(frozen=True, slots=True)
class DeepResearchResult:
    sources: tuple[DeepSource, ...]
    context: str
    report_markdown: str
    planned_queries: tuple[str, ...]
    costs_usd: float
    token_usage: dict[str, int] = field(default_factory=dict)
    report_source: str = "web"
    local_chunks: tuple[DeepLocalChunk, ...] = ()


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
        # 引擎专用模型（W3.6）：FAST/SMART/STRATEGIC 三角色走 engine_model，
        # 缺省回退 chat 模型；embedding 保持 provider 模型，引擎内嵌向量
        # 检索不受引擎 LLM 切换影响。
        chat_model = self._provider_config.model
        embedding_model = f"openai/{chat_model}"
        # GPT Researcher expects LLM configuration values as provider:model;
        # keep the slash form only for its OpenAI-compatible embedding env var.
        engine_llm = f"openai:{self._provider_config.engine_model or chat_model}"
        retriever = self._retriever or ("tavily" if os.environ.get("TAVILY_API_KEY") else "duckduckgo")
        env_patches = {
            "OPENAI_API_KEY": self._provider_config.api_key,
            "OPENAI_BASE_URL": self._provider_config.base_url,
            "EMBEDDING_PROVIDER": "openai",
            "OPENAI_EMBEDDING_MODEL": embedding_model,
            # Also set the supported environment keys so a process-level
            # override cannot replace the system-selected model.
            "FAST_LLM": engine_llm,
            "SMART_LLM": engine_llm,
            "STRATEGIC_LLM": engine_llm,
        }
        saved = {key: os.environ.get(key) for key in env_patches}
        os.environ.update(env_patches)
        temp_dir = None
        tracker = _TokenTracker()
        engine_usage = {"input_tokens": 0, "output_tokens": 0, "calls": 0}
        original_capture = self._install_usage_probe(engine_usage)
        original_tavily_init = self._install_tavily_adapter()
        try:
            local_chunks = self._local_chunks(objective)
            config_payload = {
                "SMART_LLM": engine_llm,
                "FAST_LLM": engine_llm,
                "STRATEGIC_LLM": engine_llm,
                "RETRIEVER": retriever,
                "MAX_SEARCH_RESULTS_PER_QUERY": 4,
                # 引擎报告随交付附录呈现，中文目标必须产出中文叙事（W3.2.1）。
                "LANGUAGE": "Chinese",
            }
            if local_chunks:
                temp_dir = Path(tempfile.mkdtemp(prefix="cw-deep-docs-"))
                for chunk in local_chunks:
                    (temp_dir / _local_filename(chunk)).write_text(chunk.text, encoding="utf-8")
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
            sources = self._collect_sources(researcher)
            counts = tracker.consume()
            return DeepResearchResult(
                sources=sources,
                context=context,
                report_markdown=report,
                # 引擎不暴露其规划 sub-queries；该字段保留为空（仅观测用途）。
                planned_queries=(),
                costs_usd=float(researcher.get_costs() or 0.0),
                token_usage={
                    "input_tokens": counts["input_tokens"] + engine_usage["input_tokens"],
                    "output_tokens": counts["output_tokens"] + engine_usage["output_tokens"],
                    "calls": counts["calls"] + engine_usage["calls"],
                },
                report_source=report_source,
                local_chunks=tuple(local_chunks),
            )
        finally:
            # 异常路径也要回收回调与探针，避免泄漏进全局状态。
            tracker.consume()
            self._uninstall_usage_probe(original_capture)
            self._uninstall_tavily_adapter(original_tavily_init)
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
            if temp_dir is not None:
                shutil.rmtree(temp_dir, ignore_errors=True)

    @staticmethod
    def _install_usage_probe(engine_usage: dict[str, int]):
        """挂到引擎 provider 的响应元数据钩子上累计真实 token。

        引擎 LLM 经 langchain_openai 直连（不经 litellm），litellm 回调收不到；
        GenericLLMProvider 每次响应都调用 _capture_response_metadata，这是唯一
        能拿到 usage 的边界。进程级补丁仅覆盖本次引擎运行、结束即恢复；引擎
        串行执行（durable 单 worker）时安全；任何缺失/失败都静默降级为 0 计数。
        """
        try:
            from gpt_researcher.llm_provider.generic import base as engine_base

            original = engine_base.GenericLLMProvider._capture_response_metadata

            def patched(provider_self, message, *args, **kwargs):
                try:
                    usage = getattr(message, "usage_metadata", None)
                    if usage is not None:
                        if hasattr(usage, "model_dump"):
                            usage = usage.model_dump()
                        engine_usage["input_tokens"] += int(usage.get("input_tokens", 0) or 0)
                        engine_usage["output_tokens"] += int(usage.get("output_tokens", 0) or 0)
                        engine_usage["calls"] += 1
                except Exception:  # noqa: BLE001 - 记账失败不阻断
                    pass
                return original(provider_self, message, *args, **kwargs)

            engine_base.GenericLLMProvider._capture_response_metadata = patched
            return original
        except Exception:  # noqa: BLE001 - 引擎不可导入时无从记账
            return None

    @staticmethod
    def _install_tavily_adapter():
        """Tavily 端点/鉴权适配（W3.6）。

        官方新版 API 要求 ``Authorization: Bearer`` 头鉴权，而 gpt-researcher
        内置检索器用旧式 body api_key 且把端点硬编码为 api.tavily.com；网关
        代理平台（key 前缀非 tvly-）的兼容端点需经 TAVILY_BASE_URL 覆盖。
        补丁仅覆盖本次引擎运行，结束即恢复；失败静默降级为不补。
        """
        try:
            from gpt_researcher.retrievers.tavily import tavily_search as module
        except Exception:  # noqa: BLE001 - 引擎不可导入时无检索器可补
            return None
        original_init = module.TavilySearch.__init__

        def patched_init(self, query, headers=None, topic="general", query_domains=None):
            original_init(self, query, headers=headers, topic=topic, query_domains=query_domains)
            base_url = os.environ.get("TAVILY_BASE_URL", "").strip()
            if base_url:
                self.base_url = base_url
            if self.api_key:
                self.headers["Authorization"] = f"Bearer {self.api_key}"

        module.TavilySearch.__init__ = patched_init
        return original_init

    @staticmethod
    def _uninstall_tavily_adapter(original) -> None:
        if original is None:
            return
        try:
            from gpt_researcher.retrievers.tavily import tavily_search as module

            module.TavilySearch.__init__ = original
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def _uninstall_usage_probe(original) -> None:
        if original is None:
            return
        try:
            from gpt_researcher.llm_provider.generic import base as engine_base

            engine_base.GenericLLMProvider._capture_response_metadata = original
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def _collect_sources(researcher) -> tuple[DeepSource, ...]:
        """引擎来源 → DeepSource：按 URL 去重、保留内容最完整的一条。

        gpt-researcher 抓取结果的正文字段是 raw_content（搜索期占位条目只有
        url，无 title/content）；不去重会让同一页面以"空占位 + 抓取成功"两条
        进入台账。
        """
        merged: dict[str, DeepSource] = {}
        order: list[str] = []
        for item in researcher.get_research_sources():
            url = str(item.get("url", "")).strip()
            title = str(item.get("title") or "").strip() or url or "未命名来源"
            content = str(item.get("raw_content") or item.get("content") or "")
            existing = merged.get(url)
            if existing is None:
                order.append(url)
                merged[url] = DeepSource(url=url, title=title, content=content)
            elif len(content) > len(existing.content):
                merged[url] = DeepSource(url=url, title=title or existing.title, content=content)
        return tuple(merged[url] for url in order)

    def _local_chunks(self, objective: str) -> list[DeepLocalChunk]:
        """本地语料 Top-N 命中：写入 DOC_PATH 供引擎聚合，同时直接进证据链。"""
        if self._retrieval is None:
            return []
        run = self._retrieval.search(objective)
        chunks = []
        for hit in run.final.hits[: self._max_local_documents]:
            document = self._retrieval.document_by_id[hit.document_id]
            chunks.append(
                DeepLocalChunk(
                    snapshot_id=hit.source_snapshot_id or "",
                    document_id=hit.document_id,
                    locator=hit.locator or {},
                    text=document.text,
                )
            )
        return chunks

    def local_document_title(self, document_id: str, fallback: str) -> str:
        """本地来源标题（W3.5 紧凑引用）：基于检索索引分段表的首页标题。"""
        if self._retrieval is None:
            return fallback
        return document_title_from_segments(self._retrieval.document_by_id, document_id, fallback)


def _local_filename(chunk: DeepLocalChunk) -> str:
    return f"local-{(chunk.snapshot_id or chunk.document_id).replace(':', '-')[:40]}.txt"


def _web_content_map(evidence: tuple[EvidenceRef, ...]) -> dict[str, str]:
    """网络快照 → 最长正文（融合 Writer 的数字漂移与审计基准之一）。"""
    content: dict[str, str] = {}
    for item in evidence:
        if origin_lane(item) != "web":
            continue
        existing = content.get(item.source_snapshot_id, "")
        if len(item.quote) > len(existing):
            content[item.source_snapshot_id] = item.quote
    return content


class _TokenTracker:
    """litellm callback accumulator (best-effort; silent on any mismatch)."""

    def __init__(self) -> None:
        self.input_tokens = 0
        self.output_tokens = 0
        self.calls = 0
        # 引擎全部走 litellm.acompletion：异步成功回调读 _async_success_callback，
        # 只注册同步列表会恒为 0，因此两处都挂。
        for callback_list in self._callback_lists():
            callback_list.append(self._record)

    @staticmethod
    def _callback_lists() -> list[list]:
        try:
            import litellm

            lists = [litellm.success_callback]
            async_list = getattr(litellm, "_async_success_callback", None)
            if async_list is not None and async_list is not litellm.success_callback:
                lists.append(async_list)
            return lists
        except Exception:  # noqa: BLE001
            return []

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
        for callback_list in self._callback_lists():
            try:
                if self._record in callback_list:
                    callback_list.remove(self._record)
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
        sources = list(result.sources)

        # 来源分级：带正文的来源进证据台账（截到 max_sources）；仅标题来源只留
        # 快照与来源清单，供未核验交付引用，不作为 Claim 证据。
        substantive_sources = [
            item for item in sources if len(item.content.strip()) >= MIN_EVIDENCE_CONTENT_CHARS
        ][:max_sources]
        title_only_sources = [
            item for item in sources if len(item.content.strip()) < MIN_EVIDENCE_CONTENT_CHARS
        ]

        snapshot_records = []
        web_evidence = []
        for source in substantive_sources + title_only_sources:
            index = len(snapshot_records) + 1
            source_id = f"web-{index:04d}"
            content = source.content.strip()
            title_only = not content
            content_artifact = self.store.put_bytes(
                (content or source.title).encode("utf-8"),
                media_type="text/markdown; charset=utf-8",
                producer_step_id=step_id,
                schema_version="conflux-weave.web-source-content.v1",
            )
            content_hash = f"sha256-{content_artifact.content_hash}"
            acquired_at = _utc_now()
            snapshot_artifact = self.store.put_json(
                {
                    "schema_version": SNAPSHOT_SCHEMA,
                    "source_id": source_id,
                    "source_type": "web_page",
                    "canonical_uri": source.url,
                    "acquired_at": acquired_at,
                    "content_hash": content_hash,
                    "artifact_ref": content_artifact.artifact_id,
                    "title": source.title,
                    "acquisition_boundary": (
                        "content delivered by the aggregation engine; page not re-crawled"
                        if not title_only
                        else "title-only record; the aggregation engine did not deliver page content"
                    ),
                },
                producer_step_id=step_id,
                schema_version=SNAPSHOT_SCHEMA,
            )
            snapshot_records.append(
                {
                    "source_id": source_id,
                    "url": source.url,
                    "title": source.title,
                    "content_hash": content_hash,
                    "snapshot_artifact": snapshot_artifact.artifact_id,
                    "acquired_at": acquired_at,
                    "title_only": title_only,
                }
            )
            if not title_only:
                web_evidence.append(
                    EvidenceRef(
                        f"evidence-{len(web_evidence) + 1:04d}",
                        source_id,
                        {"type": "web_page", "url": source.url, "title": source.title},
                        content[:EVIDENCE_CHARS],
                        "gpt-researcher-aggregation-v1",
                    )
                )

        # 本地语料命中直接进入证据链（真实 source_snapshot_id，provenance 完整），
        # "结合本地语料" 由核验链而非仅引擎聚合兑现。
        local_chunks = result.local_chunks
        local_evidence = tuple(
            EvidenceRef(
                f"evidence-{len(web_evidence) + offset:04d}",
                chunk.snapshot_id,
                dict(chunk.locator),
                chunk.text[:LOCAL_EVIDENCE_CHARS],
                "deep-research-local-corpus-chunk-v1",
            )
            for offset, chunk in enumerate(local_chunks, 1)
        )
        evidence = tuple(web_evidence) + local_evidence

        local_call_count = 1 if local_chunks else 0
        provider_call_count = len(snapshot_records) + local_call_count + 1
        usage = {
            "input_tokens": int(result.token_usage.get("input_tokens", 0)),
            "output_tokens": int(result.token_usage.get("output_tokens", 0)),
            "tool_calls": provider_call_count,
            "retrieval_rounds": 1 + local_call_count,
        }

        if not evidence and not snapshot_records and not result.report_markdown.strip():
            return self._no_answer(result, usage, provider_call_count, "引擎未返回可用来源，且本地语料无检索命中")

        disposition = DeliveryDisposition.COMPLETE
        unmet: tuple[str, ...] = ()
        claims, _draft_refs = self._draft_with_retry(objective, evidence)
        if not claims:
            return self._unverified_delivery(
                objective, snapshot_records, result, "起草未产出任何候选结论", usage, provider_call_count
            )
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
            return self._unverified_delivery(
                objective, snapshot_records, result, "核验后无通过 Verifier 的结论", usage, provider_call_count
            )
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

        # W3.5 融合交付：引擎报告解析为叙事骨架，本地核验证据按段落融入。
        # 引擎无可解析结构或融合规划失败时，回退 legacy claim 组装路径。
        engine_body = result.report_markdown.strip()
        narrative = parse_engine_narrative(engine_body) if engine_body else None
        web_meta = {
            item["source_id"]: {"title": item["title"], "url": item["url"]}
            for item in snapshot_records
            if not item["title_only"]
        }
        local_registry = {
            chunk.snapshot_id: self.bridge.local_document_title(chunk.document_id, chunk.snapshot_id)
            for chunk in local_chunks
        }
        limitations = (
            "GPT Researcher 仅用于来源发现与聚合；引用权威为本地 Claim/Evidence 验证链。",
            "来源快照内容为聚合引擎交付正文，未重新爬取页面。",
        )
        merge = None
        if narrative is not None:
            merge = plan_merge(
                self.store,
                self.chat,
                objective,
                narrative,
                accepted_claims,
                accepted_evidence,
                citations,
                web_source_ids=tuple(dict.fromkeys(
                    ref.source_snapshot_id for ref in accepted_evidence if origin_lane(ref) == "web"
                )),
                web_source_meta=web_meta,
            )
        fused = merge is not None and merge.status == "ok"
        if fused:
            web_content = _web_content_map(accepted_evidence)
            distill = distill_evidence_cards(self.store, self.chat, accepted_claims, accepted_evidence, citations)
            writer: WriterOutcome = compose_fused_report_document(
                self.store,
                self.chat,
                objective,
                narrative,
                accepted_claims,
                accepted_evidence,
                citations,
                plan=merge.plan,
                web_content=web_content,
                cards=distill.cards if distill.status == "ok" else (),
            )
            document = writer.document
            if document is None:
                document = build_deterministic_document(objective, accepted_claims)
            if writer.status == "fallback":
                limitations += ("报告正文为确定性融合组装：引擎段落原文+本地核验结论逐字嵌入，未润色。",)
            note_lines = [f"正文骨架继承聚合引擎报告（{len(narrative.sections)} 节），本地核验结论按段落融入。"]
            if merge.plan.dropped:
                note_lines.append(
                    f"另有 {len(merge.plan.dropped)} 条与目标无直接关系的核验结论未写入正文，完整清单见运行工件。"
                )
            research_space = tuple(
                claim for claim in accepted_claims if claim.claim_id in set(merge.plan.research_space)
            )
            report = render_fused_report(
                title=narrative.title or objective,
                thesis=merge.plan.thesis,
                claims=accepted_claims,
                evidence=accepted_evidence,
                citations=citations,
                document=document,
                research_space_claims=research_space,
                web_registry=web_meta,
                local_registry=local_registry,
                note_lines=tuple(note_lines),
                warning_lines=writer.warnings,
            )
            delivery_shape = "engine-fused"
            engine_view = "narrative-skeleton"
        else:
            if merge is not None and merge.status == "degraded":
                limitations += (f"证据融合规划未产出可用方案（{merge.reason}），报告按本地核验结论组装交付。",)
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
            if engine_body:
                # 引擎综合视图并入交付：与未核验交付同一分层模式——核验
                # claim 链是骨架，附录内容显式标注"未经本地核验"。
                report += (
                    "\n\n---\n\n"
                    "## 附录：聚合引擎综合视图（未经本地核验）\n\n"
                    "> 以下内容由 GPT Researcher 生成，未经过本地 Claim/Verifier 链核验；"
                    "结论请以上文「已验证研究发现」为准，来源出处见上方来源清单与快照。\n\n"
                    + engine_body
                    + "\n"
                )
            delivery_shape = "flat"
            engine_view = "appended-unverified" if engine_body else "omitted"
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
                "delivery_shape": delivery_shape,
                "merge": {
                    "status": merge.status if merge else "skipped",
                    "reason": merge.reason if merge else "engine narrative has no sections",
                    "plan_artifact": merge.plan_artifact_id if merge else None,
                    "assignment_count": len(merge.plan.assignments) if merge and merge.plan else 0,
                    "research_space_count": len(merge.plan.research_space) if merge and merge.plan else 0,
                    "dropped_count": len(merge.plan.dropped) if merge and merge.plan else 0,
                },
                "engine_view": engine_view,
                "coverage": {
                    "accepted_claim_count": len(accepted_claims),
                    "candidate_claim_count": len(claims),
                    "evidence_count": len(accepted_evidence),
                    "web_evidence_count": len(web_evidence),
                    "local_evidence_count": len(local_evidence),
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

    def _draft_with_retry(self, objective: str, evidence: tuple[EvidenceRef, ...]):
        """起草一次；输出契约违规时带反馈重试一次（与 Verifier 的 schema 修复同型）。

        两次都失败由调用方落入未核验交付，而不是把整个 Run 打成 failed。
        """
        try:
            return self._verified._draft(objective, evidence, repair=False)
        except ValueError as error:
            return self._verified._draft(
                objective,
                evidence,
                repair=False,
                fix_note=(
                    f"Your previous output violated the contract: {error}. "
                    "Return ONLY the exact JSON object {claims:[{text,evidence_ids}]}; "
                    "every evidence_id must be copied verbatim from the supplied evidence list; "
                    "keep at most 10 claims."
                ),
            )

    def _unverified_delivery(
        self,
        objective: str,
        snapshot_records: list[dict],
        result: DeepResearchResult,
        reason: str,
        usage: dict[str, int],
        provider_call_count: int,
    ) -> DeepResearchExecution:
        """无本地核验结论 ≠ 无答案：PARTIAL 交付未核验综合视图 + 来源引用清单。

        报告三段式：边界说明 → 来源清单（编号/标题/URL/快照/hash/获取时间）→
        引擎原始报告正文，全部显式标注"未经本地核验"。不走 Claim/Citation 闭合
        （那要求 Verifier 通过），证据台账保持为空（不得作为支撑引用发布）。
        """
        web_records = [item for item in snapshot_records if not item["title_only"]]
        title_only_records = [item for item in snapshot_records if item["title_only"]]
        local_used = bool(result.local_chunks)
        limitations = (
            "GPT Researcher 仅用于来源发现与聚合；引用权威为本地 Claim/Evidence 验证链。",
            f"深度研究未产生可验证的核验结论：{reason}。以下内容来自聚合引擎，未经本地核验，不得作为已核验结论引用。",
        )
        unmet = (f"本地核验未产生任何通过 Verifier 的结论：{reason}",)
        lines = [
            "# 深度研究（未通过本地核验）",
            "",
            f"> 目标：{objective}",
            "> 本地核验链没有产出可交付的核验结论；以下内容由聚合引擎生成，**未经本地核验**。",
            "> 请通过下方来源清单自行复核；本报告不应作为已核验结论引用。",
            "",
            "## 核验状态",
            "",
            f"- 本地核验结论：0 条（{reason}）",
            f"- 引擎来源：{len(snapshot_records)} 条（带正文 {len(web_records)} 条、仅标题 {len(title_only_records)} 条）",
            f"- 本地语料：{'已进入聚合（其内容同样未经本地核验）' if local_used else '未参与本次运行'}",
            "",
            "## 来源清单",
            "",
        ]
        for index, item in enumerate(snapshot_records, 1):
            label = f"[{item['title']}]({item['url']})" if item["url"] else item["title"]
            note = "" if not item["title_only"] else "（仅标题，未获取正文）"
            lines.append(f"{index}. {label}{note}")
            lines.append(f"   - 快照 `{item['snapshot_artifact']}` · {item['content_hash']} · 获取于 {item['acquired_at']}")
        if not snapshot_records:
            lines.append("_聚合引擎未返回任何来源。_")
        lines.extend(["", "## 聚合引擎原始报告（第二视图，未经本地核验）", ""])
        engine_body = result.report_markdown.strip()
        lines.append(engine_body if engine_body else "_聚合引擎未产出报告正文。_")
        report = "\n".join(lines) + "\n"
        step_id = "w32-deep-research"
        report_ref = self.store.put_bytes(
            report.encode("utf-8"),
            media_type="text/markdown; charset=utf-8",
            producer_step_id=step_id,
            schema_version=DEEP_REPORT_SCHEMA,
        )
        raw_ref = self.store.put_bytes(
            ("# GPT Researcher 原始报告（第二视图，非交付）\n\n" + result.report_markdown).encode("utf-8"),
            media_type="text/markdown; charset=utf-8",
            producer_step_id=step_id,
            schema_version="conflux-weave.gpt-researcher-raw-report.v1",
        )
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
                "disposition": DeliveryDisposition.PARTIAL.value,
                "verification": {"verified_claim_count": 0, "reason": reason},
                "sources": snapshot_records,
                "raw_report_artifact": raw_ref.artifact_id,
                "report_artifact": report_ref.artifact_id,
                "report_contract": "v2-unverified",
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
            claims=(),
            evidence=(),
            citations=(),
            usage=usage,
            provider_call_count=provider_call_count,
            costs_usd=result.costs_usd,
            limitations=limitations,
            unmet_criteria=unmet,
            disposition=DeliveryDisposition.PARTIAL,
        )

    def _no_answer(
        self,
        result: DeepResearchResult,
        usage: dict[str, int],
        provider_call_count: int,
        reason: str,
    ) -> DeepResearchExecution:
        """引擎与本地语料均无可引用材料时的诚实空交付（保留 NO_ANSWER 语义）。"""
        limitations = (
            "GPT Researcher 仅用于来源发现与聚合；引用权威为本地 Claim/Evidence 验证链。",
            f"深度研究未产出任何可引用材料：{reason}。",
        )
        lines = [
            "# 深度研究（无可引用材料）",
            "",
            "> 本次深度研究没有从聚合引擎与本地语料获得任何可引用的来源或正文。",
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
        manifest_ref = self.store.put_json(
            {
                "schema_version": DEEP_RESEARCH_SCHEMA,
                "engine": {"name": "gpt-researcher", "costs_usd": result.costs_usd},
                "disposition": DeliveryDisposition.NO_ANSWER.value,
                "sources": [],
                "report_artifact": report_ref.artifact_id,
                "report_contract": "no_answer",
                "usage": usage,
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
            usage=usage,
            provider_call_count=provider_call_count,
            costs_usd=result.costs_usd,
            limitations=limitations,
            disposition=DeliveryDisposition.NO_ANSWER,
        )
