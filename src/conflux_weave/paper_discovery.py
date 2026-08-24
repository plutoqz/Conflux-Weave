"""Bounded arXiv paper discovery workflow for W2.5 live acceptance."""

from __future__ import annotations

import hashlib
import json
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Mapping, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from uuid import uuid4

from conflux_weave.core import (
    BudgetLedger,
    DeliveryDisposition,
    DeliveryRecord,
    RunRecord,
    RunStatus,
    StepRecord,
    StepStatus,
    TaskSpec,
    require_transition,
)
from conflux_weave.evidence import (
    AnswerBlock,
    ArtifactRef,
    Citation,
    Claim,
    EvidenceRef,
    EvidenceSupportStatus,
    SourceSnapshot,
    SourceTrustLevel,
    render_evidence_report,
    require_closed_citations,
)
from conflux_weave.live_research import LiveResearchValidationError
from conflux_weave.provider import OpenAICompatibleChatAdapter
from conflux_weave.retrieval import BM25Retriever, RetrievalDocument
from conflux_weave.runtime.artifacts import LocalArtifactStore


ARXIV_API = "https://export.arxiv.org/api/query"
ATOM = "{http://www.w3.org/2005/Atom}"
ARXIV = "{http://arxiv.org/schemas/atom}"
WORKFLOW_VERSION = "fixed-arxiv-paper-discovery-live-v1"
SCHEMA_VERSION = "conflux-weave.arxiv-paper-discovery-live.v1"
PROMPT_VERSION = "arxiv-relevance-claims-zh-v3"
MAX_OUTPUT_TOKENS = 2_048
MAX_SUMMARY_CHARS = 1_600
MAX_SELECTED = 8
UNVERIFIED_PUBLICATION_STATUS_TERMS = (
    "同行评审",
    "期刊出版",
    "正式发表",
    "peer review",
    "peer-reviewed",
)


SYSTEM_PROMPT = """你是证据约束的论文发现助手。只能使用用户消息中的 arXiv Evidence。
输出 JSON object，且只能包含 claims：
{"claims":[{"text":"论文身份和与查询的直接关系","evidence_ids":["Evidence ID"]}]}
每条 claim 必须只对应一篇论文的 Evidence，说明标题、年份和相关性，不得补充 Evidence 中没有的实验结论或来源质量判断。不要输出 limitations、Markdown 或代码围栏。"""


@dataclass(frozen=True, slots=True)
class ArxivHttpResponse:
    status_code: int
    body: bytes
    headers: Mapping[str, str]


class ArxivTransport(Protocol):
    def get(
        self, url: str, *, headers: Mapping[str, str], timeout_seconds: float
    ) -> ArxivHttpResponse: ...


class UrllibArxivTransport:
    def get(
        self, url: str, *, headers: Mapping[str, str], timeout_seconds: float
    ) -> ArxivHttpResponse:
        request = Request(url, headers=dict(headers), method="GET")
        try:
            with urlopen(request, timeout=timeout_seconds) as response:
                return ArxivHttpResponse(
                    response.status, response.read(), dict(response.headers.items())
                )
        except HTTPError as exc:
            return ArxivHttpResponse(
                exc.code,
                exc.read(),
                dict(exc.headers.items()) if exc.headers else {},
            )
        except (URLError, TimeoutError, OSError) as exc:
            raise PaperSearchError(
                code="arxiv_network_failed",
                message=f"arXiv request failed: {exc}",
                retryable=True,
                recovery_action="检查 arXiv 网络状态后显式创建新 Run。",
            ) from exc


class PaperSearchError(RuntimeError):
    def __init__(
        self,
        *,
        code: str,
        message: str,
        retryable: bool,
        status_code: int | None = None,
        artifact_ref: str | None = None,
        recovery_action: str = "",
    ) -> None:
        self.code = code
        self.retryable = retryable
        self.status_code = status_code
        self.artifact_ref = artifact_ref
        self.recovery_action = recovery_action
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class ArxivPaper:
    arxiv_id: str
    title: str
    summary: str
    authors: tuple[str, ...]
    published: str
    updated: str
    entry_url: str
    pdf_url: str | None
    categories: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ArxivSearchResult:
    search_query: str
    acquired_at: str
    papers: tuple[ArxivPaper, ...]
    response_artifact: ArtifactRef
    snapshot: SourceSnapshot
    snapshot_artifact: ArtifactRef
    manifest_artifact: ArtifactRef


class ArxivSearchAdapter:
    def __init__(
        self,
        artifact_store: LocalArtifactStore,
        *,
        transport: ArxivTransport | None = None,
        acquired_at: str | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        self.artifact_store = artifact_store
        self.transport = transport or UrllibArxivTransport()
        self.acquired_at = acquired_at or _utc_now()
        self.timeout_seconds = timeout_seconds

    def search(
        self,
        search_query: str,
        *,
        max_results: int = 15,
        producer_step_id: str = "step-arxiv-search",
    ) -> ArxivSearchResult:
        normalized = search_query.strip()
        if not normalized:
            raise ValueError("search_query must not be empty")
        if not 1 <= max_results <= 25:
            raise ValueError("max_results must be between 1 and 25")
        url = ARXIV_API + "?" + urlencode(
            {
                "search_query": normalized,
                "start": 0,
                "max_results": max_results,
                "sortBy": "submittedDate",
                "sortOrder": "descending",
            }
        )
        try:
            response = self.transport.get(
                url,
                headers={"Accept": "application/atom+xml", "User-Agent": "Conflux-Weave/0.0.1"},
                timeout_seconds=self.timeout_seconds,
            )
        except PaperSearchError as exc:
            failure = self.artifact_store.put_json(
                {
                    "schema_version": SCHEMA_VERSION,
                    "search_query": normalized,
                    "acquired_at": self.acquired_at,
                    "code": exc.code,
                    "message": str(exc),
                    "retryable": exc.retryable,
                },
                producer_step_id=producer_step_id,
                schema_version=SCHEMA_VERSION,
            )
            exc.artifact_ref = failure.artifact_id
            raise
        response_artifact = self.artifact_store.put_bytes(
            response.body,
            media_type=_header(response.headers, "Content-Type") or "application/atom+xml",
            producer_step_id=producer_step_id,
            schema_version="arxiv.atom-response.v1",
        )
        if response.status_code != 200:
            raise PaperSearchError(
                code="arxiv_http_failed",
                message=f"arXiv returned HTTP {response.status_code}",
                retryable=response.status_code >= 500 or response.status_code == 429,
                status_code=response.status_code,
                artifact_ref=response_artifact.artifact_id,
                recovery_action="检查 arXiv 服务状态后显式创建新 Run。",
            )
        papers = _parse_atom(response.body, response_artifact)
        snapshot = SourceSnapshot(
            source_id="arxiv-feed-" + response_artifact.content_hash.removeprefix("sha256:")[:16],
            source_type="arxiv_atom_search",
            canonical_uri=url,
            acquired_at=self.acquired_at,
            content_hash=response_artifact.content_hash,
            artifact_ref=response_artifact.artifact_id,
        )
        snapshot_artifact = self.artifact_store.put_json(
            {
                "schema_version": "conflux-weave.source-snapshot.v1",
                "source_id": snapshot.source_id,
                "source_type": snapshot.source_type,
                "canonical_uri": snapshot.canonical_uri,
                "acquired_at": snapshot.acquired_at,
                "content_hash": snapshot.content_hash,
                "artifact_ref": snapshot.artifact_ref,
            },
            producer_step_id=producer_step_id,
            schema_version="conflux-weave.source-snapshot.v1",
        )
        manifest_artifact = self.artifact_store.put_json(
            {
                "schema_version": "conflux-weave.arxiv-search.v1",
                "search_query": normalized,
                "acquired_at": self.acquired_at,
                "request_url": url,
                "response_artifact_ref": response_artifact.artifact_id,
                "snapshot_artifact_ref": snapshot_artifact.artifact_id,
                "paper_count": len(papers),
                "selection_boundary": "arXiv search candidates are discovery results, not proof of relevance, peer review or experimental quality.",
            },
            producer_step_id=producer_step_id,
            schema_version="conflux-weave.arxiv-search.v1",
        )
        return ArxivSearchResult(
            normalized,
            self.acquired_at,
            papers,
            response_artifact,
            snapshot,
            snapshot_artifact,
            manifest_artifact,
        )


@dataclass(frozen=True, slots=True)
class PaperDiscoveryExecution:
    task: TaskSpec
    run_history: tuple[RunRecord, ...]
    step_history: tuple[StepRecord, ...]
    delivery: DeliveryRecord
    report_artifact: ArtifactRef
    manifest_artifact: ArtifactRef
    claims: tuple[Claim, ...]
    evidence: tuple[EvidenceRef, ...]
    citations: tuple[Citation, ...]
    selected_papers: tuple[ArxivPaper, ...]
    provider_model: str
    provider_response_id: str
    input_tokens: int
    output_tokens: int

    @property
    def final_run(self) -> RunRecord:
        return self.run_history[-1]


class FixedPaperDiscoveryWorkflow:
    def __init__(
        self,
        artifact_store: LocalArtifactStore,
        search_adapter: ArxivSearchAdapter,
        chat_adapter: OpenAICompatibleChatAdapter,
        *,
        clock: Callable[[], str] | None = None,
        id_factory: Callable[[str], str] | None = None,
        code_revision: str = "unknown",
    ) -> None:
        self.artifact_store = artifact_store
        self.search_adapter = search_adapter
        self.chat_adapter = chat_adapter
        self.clock = clock or _utc_now
        self.id_factory = id_factory or _new_id
        self.code_revision = code_revision

    def execute(
        self, query: str, *, search_query: str, max_results: int = 15
    ) -> PaperDiscoveryExecution:
        normalized = query.strip()
        if not normalized:
            raise ValueError("query must not be empty")
        task_id, run_id, step_id = (
            self.id_factory("task"),
            self.id_factory("run"),
            self.id_factory("step"),
        )
        created_at = self.clock()
        budget = BudgetLedger(180, 20_000, MAX_OUTPUT_TOKENS, "provider-price-not-frozen", 2, 1, 1)
        config_artifact = self.artifact_store.put_json(
            {
                "schema_version": SCHEMA_VERSION,
                "workflow_version": WORKFLOW_VERSION,
                "prompt_version": PROMPT_VERSION,
                "code_revision": self.code_revision,
                "query": normalized,
                "search_query": search_query,
                "max_results": max_results,
                "selected_limit": MAX_SELECTED,
                "provider": self.chat_adapter.config.provider_name,
                "model": self.chat_adapter.config.model,
                "parameters": {"temperature": 0.0, "max_output_tokens": MAX_OUTPUT_TOKENS, "enable_thinking": False},
                "automatic_retry": False,
                "fallback": False,
                "secret_recorded": False,
            },
            producer_step_id=step_id,
            schema_version=SCHEMA_VERSION,
        )
        task = TaskSpec(
            task_id,
            "paper_discovery",
            {"query": normalized, "search_query": search_query},
            WORKFLOW_VERSION,
            _idempotency_key(normalized, search_query),
        )
        runs = [RunRecord(run_id, task_id, RunStatus.ACCEPTED, WORKFLOW_VERSION, config_artifact.artifact_id, budget, created_at, created_at)]
        self._transition(runs, RunStatus.QUEUED)
        self._transition(runs, RunStatus.RUNNING)
        steps = [
            StepRecord(step_id, run_id, "paper_discovery_live", 1, StepStatus.PENDING),
            StepRecord(step_id, run_id, "paper_discovery_live", 1, StepStatus.RUNNING),
        ]
        search = self.search_adapter.search(search_query, max_results=max_results)
        if not search.papers:
            raise LiveResearchValidationError(
                "arXiv returned no paper candidates",
                code="paper_candidates_missing",
                artifact_ref=search.manifest_artifact.artifact_id,
            )
        retriever = BM25Retriever(
            RetrievalDocument(paper.arxiv_id, paper.title + " " + paper.summary)
            for paper in search.papers
        )
        hits = retriever.search(search_query, top_k=min(MAX_SELECTED, len(search.papers))).hits
        paper_by_id = {paper.arxiv_id: paper for paper in search.papers}
        selected = tuple(paper_by_id[hit.document_id] for hit in hits)
        if not selected:
            raise LiveResearchValidationError(
                "BM25 produced no positive-score arXiv candidates",
                code="paper_retrieval_empty",
                artifact_ref=search.manifest_artifact.artifact_id,
            )
        evidence = _paper_evidence(selected, search.snapshot.source_id)
        completion = self.chat_adapter.complete(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=_paper_prompt(normalized, evidence),
            max_output_tokens=MAX_OUTPUT_TOKENS,
            temperature=0.0,
            json_object=True,
            enable_thinking=False,
            producer_step_id=step_id,
        )
        if completion.output_tokens > MAX_OUTPUT_TOKENS:
            raise LiveResearchValidationError(
                f"Provider output usage exceeded budget: {completion.output_tokens}/{MAX_OUTPUT_TOKENS}",
                code="budget_exhausted",
                request_artifact_ref=completion.request_artifact.artifact_id,
                response_artifact_ref=completion.response_artifact.artifact_id,
            )
        try:
            claims, citations, model_limitations = _parse_claims(completion.content, evidence, step_id)
        except LiveResearchValidationError as exc:
            exc.request_artifact_ref = completion.request_artifact.artifact_id
            exc.response_artifact_ref = completion.response_artifact.artifact_id
            failure_manifest = self.artifact_store.put_json(
                {
                    "schema_version": SCHEMA_VERSION,
                    "run_id": run_id,
                    "status": RunStatus.FAILED.value,
                    "error_code": exc.code,
                    "message": str(exc),
                    "config_artifact_ref": config_artifact.artifact_id,
                    "search_response_artifact_ref": search.response_artifact.artifact_id,
                    "search_snapshot_artifact_ref": search.snapshot_artifact.artifact_id,
                    "search_manifest_artifact_ref": search.manifest_artifact.artifact_id,
                    "provider_request_artifact_ref": completion.request_artifact.artifact_id,
                    "provider_response_artifact_ref": completion.response_artifact.artifact_id,
                    "provider_response_id": completion.response_id,
                    "usage": {
                        "input_tokens": completion.input_tokens,
                        "output_tokens": completion.output_tokens,
                        "total_tokens": completion.total_tokens,
                    },
                    "automatic_retry": False,
                    "fallback": False,
                    "secret_recorded": False,
                },
                producer_step_id=step_id,
                schema_version=SCHEMA_VERSION,
            )
            exc.artifact_ref = failure_manifest.artifact_id
            raise
        report = render_evidence_report(
            title="arXiv 论文发现",
            intro_lines=(f"> 研究问题：{normalized}", f"> arXiv 检索式：`{search_query}`"),
            blocks=(AnswerBlock("候选论文及相关性", "\n".join(f"- {claim.text}" for claim in claims), EvidenceSupportStatus.PARTIAL_SUPPORT, tuple(claim.claim_id for claim in claims)),),
            claims=claims,
            evidence=evidence,
            citations=citations,
            evidence_trust={item.evidence_id: SourceTrustLevel.GENERAL_SOURCE for item in evidence},
            limitations=model_limitations + ("仅检索 arXiv 元数据和摘要；未核验正式发表版本、全文实验或跨数据库召回。",),
        )
        report_artifact = self.artifact_store.put_bytes(
            report.encode(),
            media_type="text/markdown; charset=utf-8",
            producer_step_id=step_id,
            schema_version="conflux-weave.paper-discovery-report.v1",
        )
        limitations = model_limitations + ("仅检索 arXiv 元数据和摘要；未核验正式发表版本、全文实验或跨数据库召回。",)
        unmet = ("尚未覆盖出版社、Crossref、会议页面和全文实验核验。",)
        manifest_artifact = self.artifact_store.put_json(
            {
                "schema_version": SCHEMA_VERSION,
                "run_id": run_id,
                "status": RunStatus.PARTIAL.value,
                "query": normalized,
                "search_query": search_query,
                "config_artifact_ref": config_artifact.artifact_id,
                "search_response_artifact_ref": search.response_artifact.artifact_id,
                "search_snapshot_artifact_ref": search.snapshot_artifact.artifact_id,
                "search_manifest_artifact_ref": search.manifest_artifact.artifact_id,
                "selected_arxiv_ids": [paper.arxiv_id for paper in selected],
                "provider_request_artifact_ref": completion.request_artifact.artifact_id,
                "provider_response_artifact_ref": completion.response_artifact.artifact_id,
                "provider_model_requested": self.chat_adapter.config.model,
                "provider_model_returned": completion.model,
                "provider_response_id": completion.response_id,
                "usage": {"input_tokens": completion.input_tokens, "output_tokens": completion.output_tokens, "total_tokens": completion.total_tokens},
                "report_artifact_ref": report_artifact.artifact_id,
                "claim_count": len(claims),
                "evidence_count": len(evidence),
                "citation_count": len(citations),
                "limitations": list(limitations),
                "unmet_criteria": list(unmet),
                "automatic_retry": False,
                "fallback": False,
                "secret_recorded": False,
            },
            producer_step_id=step_id,
            schema_version=SCHEMA_VERSION,
        )
        steps.append(StepRecord(step_id, run_id, "paper_discovery_live", 1, StepStatus.SUCCEEDED, output_refs=(report_artifact.artifact_id, manifest_artifact.artifact_id)))
        self._transition(runs, RunStatus.PARTIAL)
        delivery = DeliveryRecord(
            run_id,
            DeliveryDisposition.PARTIAL,
            (report_artifact.artifact_id, manifest_artifact.artifact_id),
            tuple(item.evidence_id for item in evidence),
            limitations,
            unmet,
            ("如需完整综述，授权出版社/Crossref 检索和全文核验后创建新 Run。",),
        )
        return PaperDiscoveryExecution(task, tuple(runs), tuple(steps), delivery, report_artifact, manifest_artifact, claims, evidence, citations, selected, completion.model, completion.response_id, completion.input_tokens, completion.output_tokens)

    def _transition(self, runs: list[RunRecord], target: RunStatus) -> None:
        current = runs[-1]
        require_transition(current.status, target)
        runs.append(RunRecord(current.run_id, current.task_id, target, current.workflow_version, current.config_snapshot_ref, current.budget, current.created_at, self.clock()))


def _parse_atom(body: bytes, artifact: ArtifactRef) -> tuple[ArxivPaper, ...]:
    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        raise PaperSearchError(code="arxiv_response_invalid", message=f"invalid Atom XML: {exc}", retryable=False, artifact_ref=artifact.artifact_id) from exc
    papers: list[ArxivPaper] = []
    for entry in root.findall(f"{ATOM}entry"):
        entry_url = _required_text(entry, f"{ATOM}id")
        arxiv_id = entry_url.rstrip("/").rsplit("/", 1)[-1]
        links = {link.attrib.get("title") or link.attrib.get("rel"): link.attrib.get("href") for link in entry.findall(f"{ATOM}link")}
        papers.append(
            ArxivPaper(
                arxiv_id,
                _clean(_required_text(entry, f"{ATOM}title")),
                _clean(_required_text(entry, f"{ATOM}summary")),
                tuple(_required_text(author, f"{ATOM}name") for author in entry.findall(f"{ATOM}author")),
                _required_text(entry, f"{ATOM}published"),
                _required_text(entry, f"{ATOM}updated"),
                entry_url,
                links.get("pdf"),
                tuple(category.attrib["term"] for category in entry.findall(f"{ATOM}category") if category.attrib.get("term")),
            )
        )
    return tuple(papers)


def _paper_evidence(papers: tuple[ArxivPaper, ...], snapshot_id: str) -> tuple[EvidenceRef, ...]:
    result = []
    for index, paper in enumerate(papers, start=1):
        quote = json.dumps({"arxiv_id": paper.arxiv_id, "title": paper.title, "authors": paper.authors, "published": paper.published, "updated": paper.updated, "entry_url": paper.entry_url, "pdf_url": paper.pdf_url, "categories": paper.categories, "summary": paper.summary[:MAX_SUMMARY_CHARS]}, ensure_ascii=False, sort_keys=True)
        result.append(EvidenceRef(f"arxiv-paper-{index:02d}", snapshot_id, {"type": "atom_entry", "arxiv_id": paper.arxiv_id, "entry_index": index, "text_sha256": hashlib.sha256(quote.encode()).hexdigest()}, quote, "arxiv-atom-normalization-v1"))
    return tuple(result)


def _paper_prompt(query: str, evidence: tuple[EvidenceRef, ...]) -> str:
    blocks = [
        f"研究问题：{query}",
        "为每篇候选生成一条紧凑相关性说明。Evidence 边界和其他限制由工作流确定性生成；不要输出 limitations。",
    ]
    for item in evidence:
        blocks.extend((f"\nEvidence ID: {item.evidence_id}", item.quote))
    return "\n".join(blocks)


def _parse_claims(content: str, evidence: tuple[EvidenceRef, ...], step_id: str) -> tuple[tuple[Claim, ...], tuple[Citation, ...], tuple[str, ...]]:
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        raise LiveResearchValidationError(f"model content is not valid JSON: {exc}") from exc
    if any(term.casefold() in content.casefold() for term in UNVERIFIED_PUBLICATION_STATUS_TERMS):
        raise LiveResearchValidationError(
            "arXiv metadata cannot establish publication or peer-review status",
            code="paper_publication_status_unsupported",
        )
    if not isinstance(payload, dict) or set(payload) != {"claims"}:
        raise LiveResearchValidationError(
            "model output must contain only claims",
            code="paper_output_schema_invalid",
        )
    raw_claims = payload.get("claims")
    if not isinstance(raw_claims, list) or not 1 <= len(raw_claims) <= MAX_SELECTED:
        raise LiveResearchValidationError("claims must contain between 1 and 8 items")
    allowed = {item.evidence_id for item in evidence}
    claims, citations = [], []
    for index, item in enumerate(raw_claims, start=1):
        if not isinstance(item, dict) or not isinstance(item.get("text"), str) or not item["text"].strip():
            raise LiveResearchValidationError("each claim requires non-empty text")
        evidence_ids = item.get("evidence_ids")
        if not isinstance(evidence_ids, list) or len(evidence_ids) != 1 or evidence_ids[0] not in allowed:
            raise LiveResearchValidationError("each paper claim requires exactly one known Evidence")
        claim_id = f"paper-claim-{index:04d}"
        claims.append(Claim(claim_id, item["text"].strip(), "paper_relevance", "primary", step_id))
        citations.append(Citation(f"paper-citation-{index:04d}", claim_id, evidence_ids[0], index))
    closed_claims, closed_citations = tuple(claims), tuple(citations)
    require_closed_citations(closed_claims, evidence, closed_citations)
    return closed_claims, closed_citations, ()


def _required_text(element: ET.Element, path: str) -> str:
    value = element.findtext(path)
    if not value or not value.strip():
        raise PaperSearchError(code="arxiv_response_invalid", message=f"missing Atom field: {path}", retryable=False)
    return value.strip()


def _clean(value: str) -> str:
    return " ".join(value.split())


def _header(headers: Mapping[str, str], name: str) -> str | None:
    return next((value for key, value in headers.items() if key.casefold() == name.casefold()), None)


def _idempotency_key(query: str, search_query: str) -> str:
    return "sha256:" + hashlib.sha256(f"{WORKFLOW_VERSION}\0{query}\0{search_query}".encode()).hexdigest()


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex}"


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
