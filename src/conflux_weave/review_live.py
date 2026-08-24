"""W1.5 live workflow for a cited Chinese reading note of a local PDF review."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
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
from conflux_weave.documents import DocumentSegment, LocalDocumentImporter
from conflux_weave.evidence import (
    AnswerBlock,
    ArtifactRef,
    Citation,
    Claim,
    EvidenceRef,
    EvidenceSupportStatus,
    SourceTrustLevel,
    render_evidence_report,
    require_closed_citations,
)
from conflux_weave.live_research import LiveResearchValidationError
from conflux_weave.provider import OpenAICompatibleChatAdapter
from conflux_weave.runtime.artifacts import LocalArtifactStore


REVIEW_WORKFLOW_VERSION = "fixed-review-reading-note-live-v1"
REVIEW_SCHEMA_VERSION = "conflux-weave.review-reading-note-live.v1"
REVIEW_PROMPT_VERSION = "review-reading-note-zh-v1"
MAX_OUTPUT_TOKENS = 2048
MAX_CONTEXT_CHARS = 42_000
MAX_EVIDENCE_CHARS = 2_300
SELECTED_PAGES = (1, 4, 7, 9, 10, 19, 23, 25, 27, 33, 39, 40, 48, 50, 67, 71)


SYSTEM_PROMPT = """你是证据约束的中文论文阅读助手。只能使用用户消息中的 PDF Evidence，不得用参数化知识补充论文外事实。
输出一个 JSON object，只能包含：title、executive_summary、key_points、terms、omitted_or_underdeveloped、implications、limitations。
格式：
{"title":"中文标题","executive_summary":{"text":"紧凑摘要","evidence_ids":["pdf-page-01"]},"key_points":[{"text":"观点","evidence_ids":["pdf-page-01"]}],"terms":[{"term":"术语","explanation":"通俗解释","evidence_ids":["pdf-page-01"]}],"omitted_or_underdeveloped":[{"text":"文章略过或证据不足的点","evidence_ids":["pdf-page-01"]}],"implications":[{"text":"对研究工程的启发","evidence_ids":["pdf-page-01"]}],"limitations":["阅读覆盖限制"]}
executive_summary 和每个 key_points、terms、omitted_or_underdeveloped、implications 项都必须引用一个或多个给定 Evidence ID。不要声称未出现在 Evidence 中的论文事实；不要编造页码或参考文献。"""


@dataclass(frozen=True, slots=True)
class ReviewNoteExecution:
    task: TaskSpec
    run_history: tuple[RunRecord, ...]
    step_history: tuple[StepRecord, ...]
    delivery: DeliveryRecord
    report_artifact: ArtifactRef
    manifest_artifact: ArtifactRef
    source_artifact: ArtifactRef
    claims: tuple[Claim, ...]
    evidence: tuple[EvidenceRef, ...]
    citations: tuple[Citation, ...]
    provider_model: str
    provider_response_id: str
    input_tokens: int
    output_tokens: int

    @property
    def final_run(self) -> RunRecord:
        return self.run_history[-1]


class FixedReviewReadingNoteWorkflow:
    def __init__(
        self,
        artifact_store: LocalArtifactStore,
        chat_adapter: OpenAICompatibleChatAdapter,
        *,
        clock: Callable[[], str] | None = None,
        id_factory: Callable[[str], str] | None = None,
        code_revision: str = "unknown",
    ) -> None:
        self.artifact_store = artifact_store
        self.chat_adapter = chat_adapter
        self.clock = clock or _utc_now
        self.id_factory = id_factory or _new_id
        self.code_revision = code_revision

    def execute(self, document_path: Path, query: str) -> ReviewNoteExecution:
        normalized_query = query.strip()
        if not normalized_query:
            raise ValueError("query must not be empty")
        if not document_path.is_file():
            raise FileNotFoundError(f"document not found: {document_path}")
        task_id = self.id_factory("task")
        run_id = self.id_factory("run")
        step_id = self.id_factory("step")
        created_at = self.clock()
        budget = BudgetLedger(
            wall_clock_seconds=180,
            input_tokens=12_000,
            output_tokens=MAX_OUTPUT_TOKENS,
            estimated_cost="provider-price-not-frozen",
            tool_calls=1,
            retrieval_rounds=1,
            concurrency=1,
        )
        importer = LocalDocumentImporter(self.artifact_store, acquired_at=created_at)
        imported = importer.import_path(document_path, producer_step_id=step_id)
        selected_segments = _select_segments(imported.segments)
        evidence = _build_evidence(imported.document_id, imported.source_snapshot.source_id, selected_segments)
        context = _build_context(normalized_query, imported, evidence)
        config_artifact = self.artifact_store.put_json(
            {
                "schema_version": REVIEW_SCHEMA_VERSION,
                "workflow_version": REVIEW_WORKFLOW_VERSION,
                "prompt_version": REVIEW_PROMPT_VERSION,
                "code_revision": self.code_revision,
                "query": normalized_query,
                "source_path_recorded": imported.source_snapshot.canonical_uri,
                "source_artifact_ref": imported.source_artifact.artifact_id,
                "selected_pages": list(SELECTED_PAGES),
                "selected_evidence_count": len(evidence),
                "context_chars": len(context),
                "parameters": {
                    "temperature": 0.0,
                    "max_output_tokens": MAX_OUTPUT_TOKENS,
                    "response_format": "json_object",
                    "enable_thinking": False,
                },
                "budget_limits": {
                    "wall_clock_seconds": budget.wall_clock_seconds,
                    "input_tokens": budget.input_tokens,
                    "output_tokens": budget.output_tokens,
                    "tool_calls": budget.tool_calls,
                    "retrieval_rounds": budget.retrieval_rounds,
                    "concurrency": budget.concurrency,
                    "cost": budget.estimated_cost,
                },
                "automatic_retry": False,
                "fallback": False,
                "secret_recorded": False,
            },
            producer_step_id=step_id,
            schema_version=REVIEW_SCHEMA_VERSION,
        )
        task = TaskSpec(
            task_id=task_id,
            kind="review_reading_note",
            input={"query": normalized_query, "document_id": imported.document_id},
            requested_policy=REVIEW_WORKFLOW_VERSION,
            idempotency_key=_idempotency_key(imported.document_id, normalized_query),
        )
        runs = [
            RunRecord(
                run_id=run_id,
                task_id=task_id,
                status=RunStatus.ACCEPTED,
                workflow_version=REVIEW_WORKFLOW_VERSION,
                config_snapshot_ref=config_artifact.artifact_id,
                budget=budget,
                created_at=created_at,
                updated_at=created_at,
            )
        ]
        self._transition(runs, RunStatus.QUEUED)
        self._transition(runs, RunStatus.RUNNING)
        steps = [
            StepRecord(step_id, run_id, "review_reading_note_live", 1, StepStatus.PENDING),
            StepRecord(step_id, run_id, "review_reading_note_live", 1, StepStatus.RUNNING),
        ]
        completion = self.chat_adapter.complete(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=context,
            max_output_tokens=MAX_OUTPUT_TOKENS,
            temperature=0.0,
            json_object=True,
            enable_thinking=False,
            producer_step_id=step_id,
        )
        if completion.output_tokens > MAX_OUTPUT_TOKENS:
            raise LiveResearchValidationError(
                f"Provider-reported output usage exceeded frozen budget: {completion.output_tokens}/{MAX_OUTPUT_TOKENS}",
                code="budget_exhausted",
                request_artifact_ref=completion.request_artifact.artifact_id,
                response_artifact_ref=completion.response_artifact.artifact_id,
                recovery_action="检查 Provider token 语义后重新冻结预算并创建新 Run。",
            )
        note = _parse_note(completion.content, evidence, step_id)
        report, claims, citations = _render_report(
            normalized_query, imported, note, evidence
        )
        report_artifact = self.artifact_store.put_bytes(
            report.encode("utf-8"),
            media_type="text/markdown; charset=utf-8",
            producer_step_id=step_id,
            schema_version="conflux-weave.review-reading-note-report.v1",
        )
        limitations = tuple(note["limitations"]) + (
            f"本次一次性上下文仅覆盖选定页：{', '.join(map(str, SELECTED_PAGES))}；未选页未被模型直接阅读。",
        )
        unmet = ("尚未对全文 71 页逐页进行模型级综合，也未核验论文引用的外部原文。",)
        manifest_artifact = self.artifact_store.put_json(
            {
                "schema_version": REVIEW_SCHEMA_VERSION,
                "run_id": run_id,
                "status": RunStatus.PARTIAL.value,
                "query": normalized_query,
                "document_id": imported.document_id,
                "source_artifact_ref": imported.source_artifact.artifact_id,
                "source_snapshot_artifact_ref": imported.snapshot_artifact.artifact_id,
                "segments_artifact_ref": imported.segments_artifact.artifact_id,
                "config_artifact_ref": config_artifact.artifact_id,
                "selected_pages": list(SELECTED_PAGES),
                "selected_evidence_count": len(evidence),
                "provider_request_artifact_ref": completion.request_artifact.artifact_id,
                "provider_response_artifact_ref": completion.response_artifact.artifact_id,
                "provider_response_id": completion.response_id,
                "provider_model_requested": self.chat_adapter.config.model,
                "provider_model_returned": completion.model,
                "usage": {
                    "input_tokens": completion.input_tokens,
                    "output_tokens": completion.output_tokens,
                    "total_tokens": completion.total_tokens,
                    "finish_reason": completion.finish_reason,
                },
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
            schema_version=REVIEW_SCHEMA_VERSION,
        )
        steps.append(
            StepRecord(
                step_id,
                run_id,
                "review_reading_note_live",
                1,
                StepStatus.SUCCEEDED,
                output_refs=(report_artifact.artifact_id, manifest_artifact.artifact_id),
            )
        )
        self._transition(runs, RunStatus.PARTIAL)
        delivery = DeliveryRecord(
            run_id=run_id,
            disposition=DeliveryDisposition.PARTIAL,
            artifact_refs=(report_artifact.artifact_id, manifest_artifact.artifact_id),
            evidence_refs=tuple(item.evidence_id for item in evidence),
            limitations=limitations,
            unmet_criteria=unmet,
            recovery_actions=("补充逐页综合或指定未覆盖章节后创建新 Run。",),
        )
        return ReviewNoteExecution(
            task=task,
            run_history=tuple(runs),
            step_history=tuple(steps),
            delivery=delivery,
            report_artifact=report_artifact,
            manifest_artifact=manifest_artifact,
            source_artifact=imported.source_artifact,
            claims=claims,
            evidence=evidence,
            citations=citations,
            provider_model=completion.model,
            provider_response_id=completion.response_id,
            input_tokens=completion.input_tokens,
            output_tokens=completion.output_tokens,
        )

    def _transition(self, runs: list[RunRecord], target: RunStatus) -> None:
        current = runs[-1]
        require_transition(current.status, target)
        runs.append(
            RunRecord(
                run_id=current.run_id,
                task_id=current.task_id,
                status=target,
                workflow_version=current.workflow_version,
                config_snapshot_ref=current.config_snapshot_ref,
                budget=current.budget,
                created_at=current.created_at,
                updated_at=self.clock(),
            )
        )


def _select_segments(segments: tuple[DocumentSegment, ...]) -> tuple[DocumentSegment, ...]:
    selected = tuple(item for item in segments if item.locator.get("page") in SELECTED_PAGES)
    if not selected:
        raise LiveResearchValidationError("selected PDF pages produced no text")
    return selected


def _build_evidence(
    document_id: str,
    source_snapshot_id: str,
    segments: tuple[DocumentSegment, ...],
) -> tuple[EvidenceRef, ...]:
    return tuple(
        EvidenceRef(
            evidence_id=f"pdf-page-{int(segment.locator['page']):02d}",
            source_snapshot_id=source_snapshot_id,
            locator={
                **segment.locator,
                "start_char": 0,
                "end_char": min(len(segment.text), MAX_EVIDENCE_CHARS),
            },
            quote=segment.text[:MAX_EVIDENCE_CHARS],
            extraction_method="pypdf-page-prefix-v1",
        )
        for segment in segments
    )


def _build_context(query: str, imported, evidence: tuple[EvidenceRef, ...]) -> str:
    blocks = [
        f"阅读任务：{query}",
        f"文档：{imported.document_id}",
        "以下是按章节覆盖策略选取的 PDF 页级 Evidence。每个 Evidence ID 对应原始 PDF 页码。",
    ]
    for item in evidence:
        blocks.extend((f"\nEvidence ID: {item.evidence_id}", item.quote))
    context = "\n".join(blocks)
    if len(context) > MAX_CONTEXT_CHARS:
        raise LiveResearchValidationError(
            f"bounded Evidence context exceeds character budget: {len(context)}/{MAX_CONTEXT_CHARS}",
            code="context_budget_exhausted",
        )
    return context


def _parse_note(
    content: str, evidence: tuple[EvidenceRef, ...], step_id: str
) -> dict[str, object]:
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        raise LiveResearchValidationError(f"model reading note is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise LiveResearchValidationError("model reading note root must be an object")
    if not isinstance(payload.get("title"), str) or not payload["title"].strip():
        raise LiveResearchValidationError("title must be a non-empty string")
    allowed = {item.evidence_id for item in evidence}
    summary = payload.get("executive_summary")
    if not isinstance(summary, dict) or not isinstance(summary.get("text"), str):
        raise LiveResearchValidationError("executive_summary must be an object with text")
    _require_known_evidence_ids(summary, allowed, "executive_summary")
    for key in ("key_points", "terms", "omitted_or_underdeveloped", "implications"):
        items = payload.get(key)
        if not isinstance(items, list) or not items:
            raise LiveResearchValidationError(f"{key} must be a non-empty list")
        for item in items:
            if not isinstance(item, dict) or not isinstance(item.get("text") or item.get("term"), str):
                raise LiveResearchValidationError(f"invalid {key} item")
            _require_known_evidence_ids(item, allowed, f"{key} item")
    limitations = payload.get("limitations")
    if not isinstance(limitations, list) or not limitations or not all(isinstance(i, str) and i.strip() for i in limitations):
        raise LiveResearchValidationError("limitations must be a non-empty list")
    return payload


def _require_known_evidence_ids(
    item: dict[str, object], allowed: set[str], label: str
) -> None:
    ids = item.get("evidence_ids")
    if (
        not isinstance(ids, list)
        or not ids
        or not all(isinstance(value, str) and value in allowed for value in ids)
    ):
        raise LiveResearchValidationError(f"{label} references unknown evidence")


def _render_report(
    query: str,
    imported,
    note: dict[str, object],
    evidence: tuple[EvidenceRef, ...],
) -> tuple[str, tuple[Claim, ...], tuple[Citation, ...]]:
    claims: list[Claim] = []
    citations: list[Citation] = []
    blocks: list[AnswerBlock] = []
    summary = note["executive_summary"]
    summary_claim_id = "review-claim-0001"
    claims.append(
        Claim(
            summary_claim_id,
            str(summary["text"]),
            "摘要",
            "primary",
            "step-review-reading-note",
        )
    )
    index = 1
    for evidence_id in summary["evidence_ids"]:
        citations.append(
            Citation(
                f"review-citation-{index:04d}",
                summary_claim_id,
                evidence_id,
                index,
            )
        )
        index += 1
    blocks.append(
        AnswerBlock(
            "执行摘要",
            str(summary["text"]),
            EvidenceSupportStatus.PARTIAL_SUPPORT,
            (summary_claim_id,),
        )
    )
    section_map = (
        ("key_points", "核心观点", "观点"),
        ("terms", "专业名词解释", "术语"),
        ("omitted_or_underdeveloped", "文章略过或展开不足的点", "缺口"),
        ("implications", "对研究工程的启发", "启发"),
    )
    for key, heading, kind in section_map:
        section_claim_ids: list[str] = []
        section_lines: list[str] = []
        for item_number, item in enumerate(note[key], start=1):
            text = item.get("text") or f"{item.get('term')}: {item.get('explanation')}"
            claim_id = f"review-claim-{len(claims) + 1:04d}"
            claims.append(Claim(claim_id, str(text), kind, "primary", "step-review-reading-note"))
            section_claim_ids.append(claim_id)
            section_lines.append(f"- {text}")
            for evidence_id in item["evidence_ids"]:
                citation = Citation(
                    f"review-citation-{index:04d}", claim_id, evidence_id, index
                )
                citations.append(citation)
                index += 1
        blocks.append(
            AnswerBlock(
                heading,
                "\n".join(section_lines),
                EvidenceSupportStatus.PARTIAL_SUPPORT,
                tuple(section_claim_ids),
            )
        )
    closed_claims = tuple(claims)
    closed_evidence = tuple(evidence)
    closed_citations = tuple(citations)
    require_closed_citations(closed_claims, closed_evidence, closed_citations)
    report = render_evidence_report(
        title=str(note["title"]),
        intro_lines=(
            f"> 阅读任务：{query}",
            f"> SourceSnapshot：`{imported.source_snapshot.source_id}`",
            f"> 原始 PDF Artifact：`{imported.source_artifact.artifact_id}`",
        ),
        blocks=tuple(blocks),
        claims=closed_claims,
        evidence=closed_evidence,
        citations=closed_citations,
        evidence_trust={
            item.evidence_id: SourceTrustLevel.GENERAL_SOURCE
            for item in closed_evidence
        },
        limitations=tuple(str(item) for item in note["limitations"]),
    )
    return report, closed_claims, closed_citations


def _idempotency_key(document_id: str, query: str) -> str:
    return "sha256:" + hashlib.sha256(
        f"{REVIEW_WORKFLOW_VERSION}\0{document_id}\0{query}".encode("utf-8")
    ).hexdigest()


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex}"


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
