"""Readable evidence delivery without inline citation noise."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Mapping

from conflux_weave.evidence.contracts import (
    Citation,
    Claim,
    EvidenceRef,
    ReportDocument,
    ReportParagraph,
)
from conflux_weave.evidence.validation import (
    require_closed_citations,
    require_closed_report_document,
)


class EvidenceSupportStatus(StrEnum):
    CITED = "cited"
    PARTIAL_SUPPORT = "partial_support"
    UNCITED_CONTEXT = "uncited_context"
    CONFLICTING = "conflicting"
    UNSUPPORTED_CLAIM = "unsupported_claim"


class SourceTrustLevel(StrEnum):
    AUTHORITATIVE = "authoritative"
    CREDIBLE_SECONDARY = "credible_secondary"
    GENERAL_SOURCE = "general_source"
    UNVERIFIED_SOURCE = "unverified_source"


SUPPORT_MARKERS = {
    EvidenceSupportStatus.CITED: "●",
    EvidenceSupportStatus.PARTIAL_SUPPORT: "◐",
    EvidenceSupportStatus.UNCITED_CONTEXT: "○",
    EvidenceSupportStatus.CONFLICTING: "!",
    EvidenceSupportStatus.UNSUPPORTED_CLAIM: "?",
}
TRUST_MARKERS = {
    SourceTrustLevel.AUTHORITATIVE: "A",
    SourceTrustLevel.CREDIBLE_SECONDARY: "C",
    SourceTrustLevel.GENERAL_SOURCE: "G",
    SourceTrustLevel.UNVERIFIED_SOURCE: "?",
}
LANE_MARKERS = {"web": "[web]", "local": "[本地]"}


def origin_lane(item: EvidenceRef) -> str:
    """证据来源车道（W3.4）：web / local / other。

    由 extraction_method 与 locator.type 判定，渲染层来源标记与叙事编排
    的规划素材共用同一判定，保证两处口径一致。
    """
    if "web" in item.extraction_method or item.locator.get("type") == "web_page":
        return "web"
    if "local" in item.extraction_method:
        return "local"
    return "other"


@dataclass(frozen=True, slots=True)
class AnswerBlock:
    heading: str
    body: str
    support_status: EvidenceSupportStatus
    claim_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.heading.strip() or not self.body.strip():
            raise ValueError("answer block heading and body must not be empty")
        if self.support_status in {
            EvidenceSupportStatus.CITED,
            EvidenceSupportStatus.PARTIAL_SUPPORT,
            EvidenceSupportStatus.CONFLICTING,
        } and not self.claim_ids:
            raise ValueError("evidence-backed answer blocks require claim_ids")
        if self.support_status in {
            EvidenceSupportStatus.UNCITED_CONTEXT,
            EvidenceSupportStatus.UNSUPPORTED_CLAIM,
        } and self.claim_ids:
            raise ValueError("uncited or unsupported answer blocks cannot register fact claims")


def render_evidence_report(
    *,
    title: str,
    intro_lines: tuple[str, ...],
    blocks: tuple[AnswerBlock, ...],
    claims: tuple[Claim, ...],
    evidence: tuple[EvidenceRef, ...],
    citations: tuple[Citation, ...],
    evidence_trust: Mapping[str, SourceTrustLevel],
    limitations: tuple[str, ...] = (),
) -> str:
    """Render block-level markers and keep exact citations in one summary."""

    if not title.strip() or not blocks:
        raise ValueError("report title and answer blocks must not be empty")
    require_closed_citations(claims, evidence, citations)
    claim_ids = {claim.claim_id for claim in claims}
    evidence_ids = {item.evidence_id for item in evidence}
    if set(evidence_trust) != evidence_ids:
        raise ValueError("evidence_trust must classify every and only known Evidence")
    registered_claims = {
        claim_id for block in blocks for claim_id in block.claim_ids
    }
    if registered_claims != claim_ids:
        raise ValueError("answer blocks must register every and only known Claim")

    citations_by_claim: dict[str, list[Citation]] = {}
    for citation in citations:
        citations_by_claim.setdefault(citation.claim_id, []).append(citation)

    lines = [f"# {title}", ""]
    lines.extend(intro_lines)
    lines.extend(
        (
            "",
            "> 图例：● 有声明级证据；◐ 部分支持；○ 一般背景；! 证据冲突；? 待核验。",
            "> 来源：A 官方/一手；C 可信二手；G 一般来源；? 来源身份未核验。",
            "",
            "## 回答",
            "",
        )
    )
    for block in blocks:
        trust_levels = {
            evidence_trust[citation.evidence_id]
            for claim_id in block.claim_ids
            for citation in citations_by_claim[claim_id]
        }
        trust_text = "/".join(
            sorted(TRUST_MARKERS[level] for level in trust_levels)
        )
        marker = SUPPORT_MARKERS[block.support_status]
        suffix = f" {trust_text}" if trust_text else ""
        lines.extend((f"### {marker}{suffix} {block.heading}", "", block.body, ""))

    if limitations:
        lines.extend(("## 限制", ""))
        lines.extend(f"- {item}" for item in limitations)
        lines.append("")

    evidence_by_id = {item.evidence_id: item for item in evidence}
    claim_by_id = {claim.claim_id: claim for claim in claims}
    lines.extend(("## Evidence 汇总", ""))
    lines.extend(_evidence_summary_lines(citations, evidence_by_id, claim_by_id, evidence_trust))
    return "\n".join(lines).rstrip() + "\n"


def _evidence_summary_lines(
    citations: tuple[Citation, ...],
    evidence_by_id: dict[str, EvidenceRef],
    claim_by_id: dict[str, Claim],
    evidence_trust: Mapping[str, SourceTrustLevel],
) -> list[str]:
    lines = []
    for citation in citations:
        item = evidence_by_id[citation.evidence_id]
        trust = evidence_trust[item.evidence_id]
        locator = json.dumps(item.locator, ensure_ascii=False, sort_keys=True)
        lines.append(
            f"[{citation.display_index}] `{claim_by_id[citation.claim_id].claim_id}` -> "
            f"`{item.evidence_id}`；来源 {TRUST_MARKERS[trust]}；SourceSnapshot "
            f"`{item.source_snapshot_id}`；locator `{locator}`。"
        )
    return lines


REPORT_LEGEND_LINES = (
    "> 图例：● 有声明级证据；◐ 部分支持；○ 一般背景；! 证据冲突；? 待核验。",
    "> 来源：A 官方/一手；C 可信二手；G 一般来源；? 来源身份未核验。",
)


def render_report_document(
    *,
    title: str,
    document: ReportDocument,
    claims: tuple[Claim, ...],
    evidence: tuple[EvidenceRef, ...],
    citations: tuple[Citation, ...],
    evidence_trust: Mapping[str, SourceTrustLevel],
    limitations: tuple[str, ...] = (),
) -> str:
    """Render a Writer-produced report document with inline citation markers."""

    if not title.strip():
        raise ValueError("report title must not be empty")
    require_closed_citations(claims, evidence, citations)
    require_closed_report_document(document, claims)
    evidence_ids = {item.evidence_id for item in evidence}
    if set(evidence_trust) != evidence_ids:
        raise ValueError("evidence_trust must classify every and only known Evidence")

    display_by_claim: dict[str, list[int]] = {}
    for citation in citations:
        display_by_claim.setdefault(citation.claim_id, []).append(citation.display_index)

    def markers(claim_ids: tuple[str, ...]) -> str:
        indexes = sorted(
            index for claim_id in claim_ids for index in display_by_claim[claim_id]
        )
        return "".join(f"[{index}]" for index in indexes)

    lines = [f"# {title}", ""]
    lines.extend(REPORT_LEGEND_LINES)
    lines.extend(("", "## 回答摘要", ""))
    lines.append(f"{document.summary.text} {markers(document.summary.claim_ids)}".rstrip())
    lines.append("")
    for section in document.sections:
        lines.extend((f"## {section.heading}", ""))
        for paragraph in section.paragraphs:
            if paragraph.unverified:
                lines.append(f"○ {paragraph.text}")
            else:
                lines.append(f"{paragraph.text} {markers(paragraph.claim_ids)}".rstrip())
        lines.append("")
    if document.background:
        lines.extend(("## 背景补充（模型知识 · 未经证据核验）", ""))
        for item in document.background:
            lines.extend((f"### ○ {item.heading}", "", item.text, ""))
    if document.unreferenced_claim_ids:
        claim_by_id = {claim.claim_id: claim for claim in claims}
        lines.extend(("## 补充发现", ""))
        lines.extend(
            f"- {claim_by_id[claim_id].text} {markers((claim_id,))}".rstrip()
            for claim_id in document.unreferenced_claim_ids
        )
        lines.append("")
    if document.open_questions:
        lines.extend(("## 开放问题", ""))
        lines.extend(f"- {question}" for question in document.open_questions)
        lines.append("")
    if limitations:
        lines.extend(("## 限制", ""))
        lines.extend(f"- {item}" for item in limitations)
        lines.append("")
    evidence_by_id = {item.evidence_id: item for item in evidence}
    lines.extend(("## 来源", ""))
    for citation in citations:
        item = evidence_by_id[citation.evidence_id]
        trust = evidence_trust[item.evidence_id]
        locator = json.dumps(item.locator, ensure_ascii=False, sort_keys=True)
        lane_marker = LANE_MARKERS.get(origin_lane(item), "")
        lines.append(
            f"[{citation.display_index}] 来源 {TRUST_MARKERS[trust]}"
            + (f" {lane_marker}" if lane_marker else "")
            + f" · SourceSnapshot `{item.source_snapshot_id}` · 定位 `{locator}`"
        )
    lines.append("")
    claim_by_id = {claim.claim_id: claim for claim in claims}
    lines.extend(("### 审计附录（Evidence 汇总）", ""))
    lines.extend(_evidence_summary_lines(citations, evidence_by_id, claim_by_id, evidence_trust))
    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# W3.5 融合报告渲染：引擎骨架正文 + 紧凑统一来源引用。
# ---------------------------------------------------------------------------

RESEARCH_SPACE_HEADING = "可以进一步探索的问题或者研究空间"


@dataclass(frozen=True, slots=True)
class ReferenceEntry:
    """一条面向用户的紧凑来源：正文只出现 [n]，内部审计结构留在工件里。"""

    key: str  # web: 快照 id；local: "快照id#p页码"
    lane: str  # "web" | "local"
    title: str
    detail: str  # url 或 "第N页"


def evidence_reference_key(item: EvidenceRef) -> str:
    """Evidence → 引用键：网页按 URL 级快照去重，本地按（文档,页）去重。"""
    if origin_lane(item) == "web":
        return item.source_snapshot_id
    page = item.locator.get("page")
    if page is None:
        return item.source_snapshot_id
    return f"{item.source_snapshot_id}#p{page}"


def _local_page_detail(item: EvidenceRef) -> str:
    page = item.locator.get("page")
    if page is not None:
        return f"第{page}页"
    heading = str(item.locator.get("heading", "") or "").strip()
    return heading or "全文"


def render_fused_report(
    *,
    title: str,
    thesis: str,
    claims: tuple[Claim, ...],
    evidence: tuple[EvidenceRef, ...],
    citations: tuple[Citation, ...],
    document: ReportDocument,
    research_space_claims: tuple[Claim, ...],
    web_registry: Mapping[str, Mapping[str, str]],
    local_registry: Mapping[str, str],
    note_lines: tuple[str, ...] = (),
    warning_lines: tuple[str, ...] = (),
) -> str:
    """渲染融合报告：问题与来源说明 → 引擎骨架小节 → 研究空间 → 来源引用。

    来源引用为单一编号空间（网络与本地混合、按首次出现顺序、同源去重），
    每条仅含 标题+[web]/[本地]+链接/页码；SourceSnapshot/locator/哈希等
    内部审计结构不进入用户视图，继续保存在机器工件中。
    """
    if not title.strip():
        raise ValueError("report title must not be empty")
    require_closed_citations(claims, evidence, citations)
    require_closed_report_document(document, claims)
    evidence_by_id = {item.evidence_id: item for item in evidence}
    citations_by_claim: dict[str, list[Citation]] = {}
    for citation in citations:
        citations_by_claim.setdefault(citation.claim_id, []).append(citation)

    def claim_source_keys(claim_id: str) -> list[str]:
        keys = []
        for citation in citations_by_claim.get(claim_id, ()):
            item = evidence_by_id[citation.evidence_id]
            key = evidence_reference_key(item)
            if key not in keys:
                keys.append(key)
        return keys

    def reference_entry(key: str) -> ReferenceEntry:
        if "#" in key or (key not in web_registry and key in local_registry):
            snapshot_id = key.split("#", 1)[0]
            title_local = local_registry.get(snapshot_id, snapshot_id)
            page = key.split("#p", 1)[1] if "#p" in key else ""
            return ReferenceEntry(key, "local", title_local, f"第{page}页" if page else "全文")
        meta = web_registry.get(key, {})
        return ReferenceEntry(key, "web", str(meta.get("title", key)), str(meta.get("url", "")))

    # 编号：摘要 → 正文各段 → 研究空间，按首次出现顺序分配
    ordered_keys: list[str] = []

    def register_keys(keys: list[str] | tuple[str, ...]) -> None:
        for key in keys:
            if key not in ordered_keys:
                ordered_keys.append(key)

    def paragraph_keys(paragraph: ReportParagraph) -> list[str]:
        keys: list[str] = []
        for claim_id in paragraph.claim_ids:
            for key in claim_source_keys(claim_id):
                if key not in keys:
                    keys.append(key)
        for source_id in paragraph.web_source_ids:
            if source_id not in keys:
                keys.append(source_id)
        return keys

    register_keys(claim_source_keys_union(document.summary.claim_ids, citations_by_claim, evidence_by_id))
    for section in document.sections:
        for paragraph in section.paragraphs:
            if not paragraph.unverified:
                register_keys(paragraph_keys(paragraph))
    for claim in research_space_claims:
        register_keys(claim_source_keys(claim.claim_id))
    numbers = {key: index + 1 for index, key in enumerate(ordered_keys)}

    def markers(keys: list[str] | tuple[str, ...]) -> str:
        return "".join(f"[{number}]" for number in sorted(numbers[key] for key in keys))

    lines = [f"# {title}", ""]
    lines.extend(("## 问题与来源说明", ""))
    lines.append(f"总体结论：{thesis} {markers(claim_source_keys_union(document.summary.claim_ids, citations_by_claim, evidence_by_id))}".rstrip())
    lines.append("")
    lines.extend((
        "> [web] 网络来源：聚合引擎综合的网络资料，未经本地核验；",
        "> [本地] 本地语料：经本地 Claim/Verifier 链核验的结论与证据。",
    ))
    for line in note_lines:
        lines.append(f"> {line}")
    for line in warning_lines:
        lines.append(f"> ⚠ {line}")
    lines.append("")
    for section in document.sections:
        lines.extend((f"## {section.heading}", ""))
        for paragraph in section.paragraphs:
            if paragraph.unverified:
                lines.append(f"○ {paragraph.text}")
            else:
                lines.append(f"{paragraph.text} {markers(paragraph_keys(paragraph))}".rstrip())
        lines.append("")
    orphans = [claim for claim in research_space_claims if claim.text.strip()]
    if orphans or document.open_questions:
        lines.extend((f"## {RESEARCH_SPACE_HEADING}", ""))
        for claim in orphans:
            lines.append(f"- {claim.text} {markers(claim_source_keys(claim.claim_id))}".rstrip())
        for question in document.open_questions:
            lines.append(f"- {question}")
        lines.append("")
    lines.extend(("## 来源引用", ""))
    for key in ordered_keys:
        entry = reference_entry(key)
        if entry.lane == "web":
            lines.append(f"[{numbers[key]}] {entry.title}[web], {entry.detail}")
        else:
            lines.append(f"[{numbers[key]}] 《{entry.title}》[本地], {entry.detail}")
    return "\n".join(lines).rstrip() + "\n"


def claim_source_keys_union(
    claim_ids: tuple[str, ...],
    citations_by_claim: Mapping[str, list[Citation]],
    evidence_by_id: Mapping[str, EvidenceRef],
) -> list[str]:
    """一组 Claim 的去重来源键（摘要行等聚合位置使用）。"""
    keys: list[str] = []
    for claim_id in claim_ids:
        for citation in citations_by_claim.get(claim_id, ()):
            item = evidence_by_id[citation.evidence_id]
            key = evidence_reference_key(item)
            if key not in keys:
                keys.append(key)
    return keys
