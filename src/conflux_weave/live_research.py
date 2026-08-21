"""W1.5 fixed live workflow for evidence-bound repository identity research."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
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
    ArtifactRef,
    Citation,
    Claim,
    EvidenceRef,
    require_closed_citations,
)
from conflux_weave.provider import OpenAICompatibleChatAdapter
from conflux_weave.runtime.artifacts import LocalArtifactStore
from conflux_weave.search import GitHubRepositorySearchAdapter, RepositoryCandidate


LIVE_WORKFLOW_VERSION = "fixed-repository-identity-live-v1"
LIVE_SCHEMA_VERSION = "conflux-weave.repository-identity-live.v1"
PROMPT_VERSION = "repository-identity-claims-zh-v1"
MAX_README_CHARS = 8_000
MAX_OUTPUT_TOKENS = 2_048


SYSTEM_PROMPT = """你是证据约束的研究助手。你只能使用用户消息中的 Evidence，不得使用参数化知识补充外部事实。
输出一个 JSON object，且只能包含 claims 和 limitations：
{"claims":[{"text":"中文声明","evidence_ids":["evidence id"]}],"limitations":["中文限制"]}
每条声明必须是可由所列 Evidence 直接支持的原子事实。不要输出 Markdown，不要输出代码围栏，不要把 GitHub 搜索排名表述为官方性证明。"""


class LiveResearchValidationError(ValueError):
    """Raised when a live model response cannot form a closed cited delivery."""


@dataclass(frozen=True, slots=True)
class RepositoryIdentityExecution:
    task: TaskSpec
    run_history: tuple[RunRecord, ...]
    step_history: tuple[StepRecord, ...]
    delivery: DeliveryRecord
    report_artifact: ArtifactRef
    manifest_artifact: ArtifactRef
    claims: tuple[Claim, ...]
    evidence: tuple[EvidenceRef, ...]
    citations: tuple[Citation, ...]
    selected_repository: RepositoryCandidate
    provider_model: str
    provider_response_id: str
    input_tokens: int
    output_tokens: int

    @property
    def final_run(self) -> RunRecord:
        return self.run_history[-1]


class FixedRepositoryIdentityWorkflow:
    def __init__(
        self,
        artifact_store: LocalArtifactStore,
        search_adapter: GitHubRepositorySearchAdapter,
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

    def execute(self, query: str) -> RepositoryIdentityExecution:
        normalized_query = query.strip()
        if not normalized_query:
            raise ValueError("query must not be empty")
        task_id = self.id_factory("task")
        run_id = self.id_factory("run")
        step_id = self.id_factory("step")
        created_at = self.clock()
        budget = BudgetLedger(
            wall_clock_seconds=120,
            input_tokens=12_000,
            output_tokens=MAX_OUTPUT_TOKENS,
            estimated_cost="provider-price-not-frozen",
            tool_calls=3,
            retrieval_rounds=1,
            concurrency=1,
        )
        config_artifact = self.artifact_store.put_json(
            {
                "schema_version": LIVE_SCHEMA_VERSION,
                "workflow_version": LIVE_WORKFLOW_VERSION,
                "prompt_version": PROMPT_VERSION,
                "code_revision": self.code_revision,
                "query": normalized_query,
                "provider": self.chat_adapter.config.provider_name,
                "model": self.chat_adapter.config.model,
                "parameters": {
                    "temperature": 0.0,
                    "max_output_tokens": MAX_OUTPUT_TOKENS,
                    "response_format": "json_object",
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
                "selection_policy": "github_search_rank_1_then_readme_self_identification",
                "automatic_retry": False,
                "fallback": False,
                "secret_recorded": False,
            },
            producer_step_id=step_id,
            schema_version=LIVE_SCHEMA_VERSION,
        )
        task = TaskSpec(
            task_id=task_id,
            kind="repository_identity_research",
            input={"query": normalized_query},
            requested_policy=LIVE_WORKFLOW_VERSION,
            idempotency_key=_idempotency_key(normalized_query),
        )
        runs = [
            RunRecord(
                run_id=run_id,
                task_id=task_id,
                status=RunStatus.ACCEPTED,
                workflow_version=LIVE_WORKFLOW_VERSION,
                config_snapshot_ref=config_artifact.artifact_id,
                budget=budget,
                created_at=created_at,
                updated_at=created_at,
            )
        ]
        self._transition(runs, RunStatus.QUEUED)
        self._transition(runs, RunStatus.RUNNING)
        steps = [
            StepRecord(step_id, run_id, "repository_identity_live", 1, StepStatus.PENDING),
            StepRecord(step_id, run_id, "repository_identity_live", 1, StepStatus.RUNNING),
        ]

        search_result = self.search_adapter.search(normalized_query, limit=10)
        if not search_result.candidates:
            raise LiveResearchValidationError("GitHub search returned no valid candidates")
        selected = search_result.candidates[0]
        registered = self.search_adapter.register(
            search_result, full_name=selected.full_name
        )
        readme = self.search_adapter.fetch_readme(selected)
        readme_text = self.artifact_store.read_bytes(readme.readme_artifact).decode("utf-8")
        evidence = _build_evidence(selected, registered.source_snapshot.source_id, readme_text, readme.source_snapshot.source_id)
        user_prompt = _build_user_prompt(normalized_query, evidence)
        completion = self.chat_adapter.complete(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=user_prompt,
            max_output_tokens=MAX_OUTPUT_TOKENS,
            temperature=0.0,
            json_object=True,
            producer_step_id=step_id,
        )
        claims, citations, model_limitations = _parse_model_claims(
            completion.content, evidence, step_id
        )
        report = _render_report(
            normalized_query,
            selected,
            claims,
            evidence,
            citations,
            model_limitations,
        )
        report_artifact = self.artifact_store.put_bytes(
            report.encode("utf-8"),
            media_type="text/markdown; charset=utf-8",
            producer_step_id=step_id,
            schema_version="conflux-weave.repository-identity-report.v1",
        )
        limitations = tuple(model_limitations) + (
            "仓库由 GitHub 搜索首位候选自动定位，并由仓库 README 自述交叉核对；未完成独立组织所有权认证。",
        )
        unmet = ("尚未通过独立来源验证仓库维护组织与项目官方关系。",)
        manifest_artifact = self.artifact_store.put_json(
            {
                "schema_version": LIVE_SCHEMA_VERSION,
                "run_id": run_id,
                "status": RunStatus.PARTIAL.value,
                "query": normalized_query,
                "selected_repository": selected.full_name,
                "selection_rank": 1,
                "official_status": "readme_self_identified_not_independently_verified",
                "config_artifact_ref": config_artifact.artifact_id,
                "search_response_artifact_ref": search_result.response_artifact.artifact_id,
                "search_manifest_artifact_ref": search_result.manifest_artifact.artifact_id,
                "repository_source_artifact_ref": registered.source_artifact.artifact_id,
                "repository_snapshot_artifact_ref": registered.snapshot_artifact.artifact_id,
                "readme_artifact_ref": readme.readme_artifact.artifact_id,
                "readme_snapshot_artifact_ref": readme.snapshot_artifact.artifact_id,
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
            schema_version=LIVE_SCHEMA_VERSION,
        )
        steps.append(
            StepRecord(
                step_id,
                run_id,
                "repository_identity_live",
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
            recovery_actions=("使用独立项目网站或维护者声明补证后创建新 Run。",),
        )
        return RepositoryIdentityExecution(
            task=task,
            run_history=tuple(runs),
            step_history=tuple(steps),
            delivery=delivery,
            report_artifact=report_artifact,
            manifest_artifact=manifest_artifact,
            claims=claims,
            evidence=evidence,
            citations=citations,
            selected_repository=selected,
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


def _build_evidence(
    candidate: RepositoryCandidate,
    metadata_snapshot_id: str,
    readme_text: str,
    readme_snapshot_id: str,
) -> tuple[EvidenceRef, ...]:
    metadata_quote = json.dumps(
        {
            "full_name": candidate.full_name,
            "owner": candidate.owner,
            "html_url": candidate.html_url,
            "description": candidate.description,
            "default_branch": candidate.default_branch,
            "stars": candidate.stars,
            "archived": candidate.archived,
            "fork": candidate.fork,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return (
        EvidenceRef(
            evidence_id="github-repository-metadata",
            source_snapshot_id=metadata_snapshot_id,
            locator={"type": "github_repository_metadata", "repository": candidate.full_name},
            quote=metadata_quote,
            extraction_method="github-api-normalization-v1",
        ),
        EvidenceRef(
            evidence_id="github-repository-readme",
            source_snapshot_id=readme_snapshot_id,
            locator={"type": "github_readme_prefix", "start_char": 0, "end_char": min(len(readme_text), MAX_README_CHARS)},
            quote=readme_text[:MAX_README_CHARS],
            extraction_method="utf8-prefix-v1",
        ),
    )


def _build_user_prompt(query: str, evidence: tuple[EvidenceRef, ...]) -> str:
    blocks = [f"研究问题：{query}", "请提取项目名称、维护者/组织、规范仓库 URL、实现入口和证据限制。"]
    for item in evidence:
        blocks.extend((f"\nEvidence ID: {item.evidence_id}", item.quote))
    return "\n".join(blocks)


def _parse_model_claims(
    content: str, evidence: tuple[EvidenceRef, ...], step_id: str
) -> tuple[tuple[Claim, ...], tuple[Citation, ...], tuple[str, ...]]:
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        raise LiveResearchValidationError(
            f"model content is not valid JSON: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise LiveResearchValidationError("model content root must be an object")
    raw_claims = payload.get("claims")
    raw_limitations = payload.get("limitations")
    if not isinstance(raw_claims, list) or not 1 <= len(raw_claims) <= 8:
        raise LiveResearchValidationError("claims must contain between 1 and 8 items")
    if not isinstance(raw_limitations, list) or not all(
        isinstance(item, str) and item.strip() for item in raw_limitations
    ):
        raise LiveResearchValidationError("limitations must be a list of non-empty strings")
    allowed_evidence = {item.evidence_id for item in evidence}
    claims: list[Claim] = []
    citations: list[Citation] = []
    display_index = 1
    for claim_index, raw_claim in enumerate(raw_claims, start=1):
        if not isinstance(raw_claim, dict):
            raise LiveResearchValidationError("each claim must be an object")
        text = raw_claim.get("text")
        evidence_ids = raw_claim.get("evidence_ids")
        if not isinstance(text, str) or not text.strip():
            raise LiveResearchValidationError("claim text must be non-empty")
        if not isinstance(evidence_ids, list) or not evidence_ids:
            raise LiveResearchValidationError("each claim requires evidence_ids")
        if not all(isinstance(item, str) and item in allowed_evidence for item in evidence_ids):
            raise LiveResearchValidationError("claim references unknown evidence")
        if len(evidence_ids) != len(set(evidence_ids)):
            raise LiveResearchValidationError("claim contains duplicate evidence_ids")
        claim_id = f"live-claim-{claim_index:04d}"
        claims.append(Claim(claim_id, text.strip(), "direct", "primary", step_id))
        for evidence_id in evidence_ids:
            citations.append(
                Citation(
                    f"live-citation-{display_index:04d}",
                    claim_id,
                    evidence_id,
                    display_index,
                )
            )
            display_index += 1
    closed_claims = tuple(claims)
    closed_citations = tuple(citations)
    require_closed_citations(closed_claims, evidence, closed_citations)
    return closed_claims, closed_citations, tuple(item.strip() for item in raw_limitations)


def _render_report(
    query: str,
    selected: RepositoryCandidate,
    claims: tuple[Claim, ...],
    evidence: tuple[EvidenceRef, ...],
    citations: tuple[Citation, ...],
    model_limitations: tuple[str, ...],
) -> str:
    by_claim: dict[str, list[Citation]] = {}
    for citation in citations:
        by_claim.setdefault(citation.claim_id, []).append(citation)
    lines = [
        "# 仓库身份核验",
        "",
        f"> 查询：{query}",
        f"> 自动选择：[{selected.full_name}]({selected.html_url})（GitHub 搜索第 1 位）",
        "",
        "## 证据约束结论",
        "",
    ]
    for claim in claims:
        markers = "".join(f"[{item.display_index}]" for item in by_claim[claim.claim_id])
        lines.append(f"- {claim.text} {markers}")
    lines.extend(("", "## 限制", ""))
    for limitation in model_limitations:
        lines.append(f"- {limitation}")
    lines.append("- 搜索排名只用于发现；仓库 README 的自述不能单独证明维护组织的独立官方身份。")
    lines.extend(("", "## 引用", ""))
    evidence_by_id = {item.evidence_id: item for item in evidence}
    for citation in citations:
        item = evidence_by_id[citation.evidence_id]
        locator = json.dumps(item.locator, ensure_ascii=False, sort_keys=True)
        lines.append(
            f"[{citation.display_index}] `{item.evidence_id}`，SourceSnapshot "
            f"`{item.source_snapshot_id}`，locator `{locator}`。"
        )
    return "\n".join(lines).rstrip() + "\n"


def _idempotency_key(query: str) -> str:
    payload = f"{LIVE_WORKFLOW_VERSION}\0{query}".encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex}"


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
