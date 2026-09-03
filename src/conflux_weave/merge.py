"""Paragraph-level evidence merge planner (W3.5 模式 C 融合交付).

聚合引擎报告是叙事骨架（engine_narrative 解析），本地核验证据按段落融入：
规划器一次调用完成两件事——(1) 把每个引擎段落归属到它依据的网络来源；
(2) 把全部已接受 Claim 分配到段落并标注关系，或归入"研究空间"，或显式
丢弃。引擎内容仍是素材而非引用权威：每个事实都落到本地快照台账里的来源，
融合段落继续过段落审计。

契约（冻结）：conflux-weave.merge-plan.v1
  {thesis: 非空 ≤500 字（一句总体结论）,
   assignments: [{section_index, paragraph_index,
                  web_source_ids: [已知快照 id, 去重],
                  claims: [{claim_id, relation}]}],
   research_space: [claim_id],   # 无段落对应但有独立价值的本地结论
   dropped: [claim_id]}          # 与目标无关，不进正文（manifest 留痕）
  relation ∈ {supports, qualifies, contradicts, extends}；
  三处并集 = 全部已接受 Claim 且两两不交（覆盖率由构造校验）。
校验违规 → 带反馈重试一次 → 仍违规 plan=None（显式降级）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from conflux_weave.engine_narrative import EngineNarrative
from conflux_weave.evidence import Citation, Claim, EvidenceRef, origin_lane
from conflux_weave.provider import OpenAICompatibleChatAdapter
from conflux_weave.runtime import LocalArtifactStore


MERGE_SCHEMA = "conflux-weave.merge-plan.v1"
MERGE_RELATIONS = ("supports", "qualifies", "contradicts", "extends")
MERGE_MAX_ASSIGNMENTS = 144  # 12 节 × 12 段的上界
MERGE_THESIS_CHARS = 500
MERGE_NOTE_CHARS = 0  # 预留：关系备注不进 v1 契约
# 规划素材预算：段落与证据节选截断，保证规划调用规模可控。
MERGE_PARAGRAPH_CHARS = 800
MERGE_EVIDENCE_CHARS = 800
MERGE_URL_CHARS = 200
MERGE_MAX_OUTPUT_TOKENS = 4096
MERGE_TEMPERATURE = 0.2
MERGE_MAX_ATTEMPTS = 2

MERGE_SYSTEM_PROMPT = (
    "You are an evidence-merge planner for a research report. You receive an "
    "objective, the aggregation engine's report outline (sections with indexed "
    "paragraphs, narrative synthesized from web sources), the engine's web "
    "source list, and Verifier-accepted Claims with their Evidence excerpts "
    "(origin \"web\" or \"local\"). Produce the merge plan. Return exactly this "
    "JSON object shape and nothing else: "
    '{"thesis":"一句总体结论，直接回答目标","assignments":[{"section_index":0,'
    '"paragraph_index":0,"web_source_ids":["web-0001"],'
    '"claims":[{"claim_id":"claim-0001","relation":"supports"}]}],'
    '"research_space":["claim-0002"],"dropped":[]} '
    "Rules: write thesis in the same language as the objective. "
    "web_source_ids: attribute every paragraph to the web sources its content "
    "draws from (subset of the supplied source ids; omit the key's entries as "
    "empty list when unclear). claims: assign each Claim to the ONE paragraph "
    "it supports, qualifies (adds conditions/scope), contradicts, or extends "
    "(adds what the paragraph does not cover); place local-origin Claims by "
    "their content and web-origin Claims where they corroborate the paragraph; "
    "a paragraph may take several Claims. Claims with independent value that "
    "fits no paragraph go to research_space; Claims irrelevant to the "
    "objective go to dropped. Every supplied Claim must appear in exactly one "
    "of assignments/research_space/dropped, never twice. section_index and "
    "paragraph_index must match the supplied outline indices. No extra keys "
    "anywhere."
)


@dataclass(frozen=True, slots=True)
class ClaimAssignment:
    claim_id: str
    relation: str


@dataclass(frozen=True, slots=True)
class ParagraphAssignment:
    section_index: int
    paragraph_index: int
    web_source_ids: tuple[str, ...]
    claims: tuple[ClaimAssignment, ...]


@dataclass(frozen=True, slots=True)
class MergePlan:
    thesis: str
    assignments: tuple[ParagraphAssignment, ...]
    research_space: tuple[str, ...]
    dropped: tuple[str, ...]

    def payload(self) -> dict:
        return {
            "thesis": self.thesis,
            "assignments": [
                {
                    "section_index": item.section_index,
                    "paragraph_index": item.paragraph_index,
                    "web_source_ids": list(item.web_source_ids),
                    "claims": [
                        {"claim_id": entry.claim_id, "relation": entry.relation}
                        for entry in item.claims
                    ],
                }
                for item in self.assignments
            ],
            "research_space": list(self.research_space),
            "dropped": list(self.dropped),
        }

    def claims_for_paragraph(self, section_index: int, paragraph_index: int) -> tuple[ClaimAssignment, ...]:
        for item in self.assignments:
            if item.section_index == section_index and item.paragraph_index == paragraph_index:
                return item.claims
        return ()

    def web_sources_for_paragraph(self, section_index: int, paragraph_index: int) -> tuple[str, ...]:
        for item in self.assignments:
            if item.section_index == section_index and item.paragraph_index == paragraph_index:
                return item.web_source_ids
        return ()


@dataclass(frozen=True, slots=True)
class MergeOutcome:
    plan: MergePlan | None
    status: str  # "ok" | "degraded" | "skipped"
    reason: str | None = None
    plan_artifact_id: str | None = None
    request_artifact_id: str | None = None
    response_artifact_id: str | None = None
    normalization_warnings: tuple[str, ...] = ()


def _normalize_duplicate_claim_assignments(content: str) -> tuple[str, tuple[str, ...]] | None:
    """Remove only repeated Claim occurrences, leaving all other schema errors strict."""
    try:
        payload = json.loads(content)
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("assignments"), list):
        return None
    seen: set[str] = set()
    removed: list[str] = []
    for assignment in payload["assignments"]:
        if not isinstance(assignment, dict) or not isinstance(assignment.get("claims"), list):
            continue
        kept = []
        for entry in assignment["claims"]:
            claim_id = entry.get("claim_id") if isinstance(entry, dict) else None
            normalizable = (
                isinstance(entry, dict)
                and set(entry) == {"claim_id", "relation"}
                and entry.get("relation") in MERGE_RELATIONS
            )
            if normalizable and claim_id in seen:
                removed.append(str(claim_id))
                continue
            if claim_id is not None:
                seen.add(claim_id)
            kept.append(entry)
        assignment["claims"] = kept
    for label in ("research_space", "dropped"):
        values = payload.get(label)
        if not isinstance(values, list):
            continue
        kept = []
        for claim_id in values:
            if claim_id in seen:
                removed.append(str(claim_id))
                continue
            seen.add(claim_id)
            kept.append(claim_id)
        payload[label] = kept
    if not removed:
        return None
    return json.dumps(payload, ensure_ascii=False), tuple(dict.fromkeys(removed))


def _claim_evidence_input(
    claims: tuple[Claim, ...],
    evidence: tuple[EvidenceRef, ...],
    citations: tuple[Citation, ...],
) -> list[dict]:
    evidence_by_id = {item.evidence_id: item for item in evidence}
    grouped: dict[str, list[dict]] = {}
    for citation in citations:
        ref = evidence_by_id.get(citation.evidence_id)
        if ref is None:
            continue
        entries = grouped.setdefault(citation.claim_id, [])
        if any(entry["evidence_id"] == ref.evidence_id for entry in entries):
            continue
        entries.append(
            {
                "evidence_id": ref.evidence_id,
                "origin": origin_lane(ref),
                "excerpt": ref.quote[:MERGE_EVIDENCE_CHARS],
            }
        )
    return [
        {"claim_id": claim.claim_id, "text": claim.text, "evidence": grouped.get(claim.claim_id, [])}
        for claim in claims
    ]


def _parse_plan(
    content: str,
    claims: tuple[Claim, ...],
    narrative: EngineNarrative,
    web_source_ids: tuple[str, ...],
) -> MergePlan:
    payload = json.loads(content)
    if not isinstance(payload, dict) or set(payload) != {"thesis", "assignments", "research_space", "dropped"}:
        raise ValueError("merge plan must contain exactly thesis, assignments, research_space, and dropped")
    thesis = payload["thesis"]
    if not isinstance(thesis, str) or not thesis.strip() or len(thesis.strip()) > MERGE_THESIS_CHARS:
        raise ValueError("merge thesis must be a bounded non-empty string")
    known_claims = {claim.claim_id for claim in claims}
    known_sources = set(web_source_ids)
    raw_assignments = payload["assignments"]
    if not isinstance(raw_assignments, list) or len(raw_assignments) > MERGE_MAX_ASSIGNMENTS:
        raise ValueError(f"merge assignments must be a list of at most {MERGE_MAX_ASSIGNMENTS} items")
    assignments: list[ParagraphAssignment] = []
    placed: set[str] = set()
    for index, raw in enumerate(raw_assignments):
        if not isinstance(raw, dict) or set(raw) != {"section_index", "paragraph_index", "web_source_ids", "claims"}:
            raise ValueError(f"merge assignment {index} has an invalid schema")
        section_index = raw["section_index"]
        paragraph_index = raw["paragraph_index"]
        if not isinstance(section_index, int) or not isinstance(paragraph_index, int):
            raise ValueError(f"merge assignment {index} indices must be integers")
        if not 0 <= section_index < len(narrative.sections):
            raise ValueError(f"merge assignment {index} section_index out of range")
        section = narrative.sections[section_index]
        if not 0 <= paragraph_index < len(section.paragraphs):
            raise ValueError(f"merge assignment {index} paragraph_index out of range")
        raw_sources = raw["web_source_ids"]
        if not isinstance(raw_sources, list):
            raise ValueError(f"merge assignment {index} web_source_ids must be a list")
        source_ids = tuple(str(value) for value in raw_sources)
        if len(source_ids) != len(set(source_ids)):
            raise ValueError(f"merge assignment {index} repeats a web source")
        unknown_sources = [value for value in source_ids if value not in known_sources]
        if unknown_sources:
            raise ValueError(f"merge assignment {index} references unknown web sources: {', '.join(unknown_sources)}")
        raw_claim_entries = raw["claims"]
        if not isinstance(raw_claim_entries, list):
            raise ValueError(f"merge assignment {index} claims must be a list")
        entries = []
        for entry_index, raw_entry in enumerate(raw_claim_entries):
            if not isinstance(raw_entry, dict) or set(raw_entry) != {"claim_id", "relation"}:
                raise ValueError(f"merge assignment {index} claim {entry_index} has an invalid schema")
            claim_id = str(raw_entry["claim_id"])
            relation = raw_entry["relation"]
            if claim_id not in known_claims:
                raise ValueError(f"merge assignment {index} references unknown Claim: {claim_id}")
            if claim_id in placed:
                raise ValueError(f"merge assignment {index} re-assigns Claim {claim_id}")
            if relation not in MERGE_RELATIONS:
                raise ValueError(f"merge assignment {index} relation must be one of {MERGE_RELATIONS}")
            placed.add(claim_id)
            entries.append(ClaimAssignment(claim_id, relation))
        assignments.append(ParagraphAssignment(section_index, paragraph_index, source_ids, tuple(entries)))

    def _claim_id_list(raw: object, label: str) -> tuple[str, ...]:
        if not isinstance(raw, list):
            raise ValueError(f"merge {label} must be a list")
        values = tuple(str(item) for item in raw)
        for value in values:
            if value not in known_claims:
                raise ValueError(f"merge {label} references unknown Claim: {value}")
            if value in placed:
                raise ValueError(f"merge {label} re-assigns Claim {value}")
            placed.add(value)
        return values

    research_space = _claim_id_list(payload["research_space"], "research_space")
    dropped = _claim_id_list(payload["dropped"], "dropped")
    missing = sorted(known_claims - placed)
    if missing:
        raise ValueError(f"merge plan leaves Claims unclassified: {', '.join(missing)}")
    return MergePlan(thesis.strip(), tuple(assignments), research_space, dropped)


def plan_merge(
    store: LocalArtifactStore,
    chat: OpenAICompatibleChatAdapter,
    objective: str,
    narrative: EngineNarrative,
    claims: tuple[Claim, ...],
    evidence: tuple[EvidenceRef, ...],
    citations: tuple[Citation, ...],
    *,
    web_source_ids: tuple[str, ...],
    web_source_meta: dict[str, dict] | None = None,
) -> MergeOutcome:
    """规划段落级证据融合；任何失败都显式降级，绝不阻断交付。"""
    if narrative is None or not narrative.sections or not claims:
        return MergeOutcome(
            plan=None,
            status="skipped",
            reason="engine narrative has no sections or no accepted Claims",
        )
    completion = None
    try:
        meta = web_source_meta or {}
        material = {
            "objective": objective,
            "outline": [
                {
                    "section_index": section_index,
                    "heading": section.heading,
                    "paragraphs": [
                        {"paragraph_index": paragraph_index, "text": text[:MERGE_PARAGRAPH_CHARS]}
                        for paragraph_index, text in enumerate(section.paragraphs)
                    ],
                }
                for section_index, section in enumerate(narrative.sections)
            ],
            "web_sources": [
                {
                    "source_id": source_id,
                    "title": str(meta.get(source_id, {}).get("title", source_id)),
                    "url": str(meta.get(source_id, {}).get("url", ""))[:MERGE_URL_CHARS],
                }
                for source_id in web_source_ids
            ],
            "claims": _claim_evidence_input(claims, evidence, citations),
        }
        valid_ids = ", ".join(claim.claim_id for claim in claims)
        plan = None
        violation = ""
        normalization_warnings: tuple[str, ...] = ()
        for attempt in range(MERGE_MAX_ATTEMPTS):
            completion = chat.complete(
                system_prompt=(
                    MERGE_SYSTEM_PROMPT
                    + (
                        f" Your previous plan was rejected: {violation} Return ONLY the exact "
                        f"JSON object. The ONLY Claim IDs that exist are: {valid_ids}. Every Claim "
                        "must appear exactly once across assignments/research_space/dropped, and "
                        "assignment indices must match the supplied outline."
                        if attempt
                        else ""
                    )
                ),
                user_prompt=json.dumps(material, ensure_ascii=False),
                max_output_tokens=MERGE_MAX_OUTPUT_TOKENS,
                temperature=MERGE_TEMPERATURE,
                json_object=True,
                enable_thinking=False,
                producer_step_id="w34-merge-plan" if attempt == 0 else "w34-merge-plan-repair",
            )
            try:
                plan = _parse_plan(completion.content, claims, narrative, web_source_ids)
                break
            except ValueError as error:
                violation = str(error)[:500]
                plan = None
                normalized = _normalize_duplicate_claim_assignments(completion.content)
                if normalized is not None and "re-assigns Claim" in violation:
                    normalized_content, removed = normalized
                    try:
                        plan = _parse_plan(normalized_content, claims, narrative, web_source_ids)
                        normalization_warnings = tuple(
                            f"removed duplicate Claim occurrence during merge normalization: {claim_id}"
                            for claim_id in removed
                        )
                        break
                    except ValueError:
                        plan = None
        if plan is None:
            return MergeOutcome(
                plan=None,
                status="degraded",
                reason=f"merge plan rejected twice: {violation}",
                request_artifact_id=completion.request_artifact.artifact_id,
                response_artifact_id=completion.response_artifact.artifact_id,
                normalization_warnings=normalization_warnings,
            )
        plan_ref = store.put_json(
            {"schema_version": MERGE_SCHEMA, "objective": objective, **plan.payload()},
            producer_step_id="w34-merge-plan",
            schema_version=MERGE_SCHEMA,
        )
        return MergeOutcome(
            plan=plan,
            status="ok",
            plan_artifact_id=plan_ref.artifact_id,
            request_artifact_id=completion.request_artifact.artifact_id,
            response_artifact_id=completion.response_artifact.artifact_id,
            normalization_warnings=normalization_warnings,
        )
    except Exception as exc:  # noqa: BLE001 - 编排失败降级为无融合交付
        return MergeOutcome(
            plan=None,
            status="degraded",
            reason=f"merge planning failed: {exc}",
            request_artifact_id=(
                completion.request_artifact.artifact_id if completion is not None else None
            ),
            response_artifact_id=(
                completion.response_artifact.artifact_id if completion is not None else None
            ),
        )
