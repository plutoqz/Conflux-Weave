"""Verified-claim report Writer with independent faithfulness audit (spec W1/W2a).

The Writer reorganizes accepted Claims into a readable report document. It may
connect and summarize but never add facts: paragraph-level claim closure, digit
drift check (C5) and an independent audit gate every cited paragraph, and any
failure degrades the delivery back to the v1 atomic-claim report. W2a adds a
Chinese fact-card distillation pass so the Writer reads digested Chinese cards
instead of raw English quotes, decoupling comprehension from expression.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, replace

from conflux_weave.evidence import (
    Citation,
    Claim,
    EvidenceRef,
    ReportBackground,
    ReportDocument,
    ReportParagraph,
    ReportSection,
    unreferenced_claim_ids,
)
from conflux_weave.engine_narrative import EngineNarrative
from conflux_weave.merge import MergePlan
from conflux_weave.provider import OpenAICompatibleChatAdapter
from conflux_weave.runtime import LocalArtifactStore


REPORT_DOCUMENT_SCHEMA = "conflux-weave.research-report-document.v5"
EVIDENCE_CARDS_SCHEMA = "conflux-weave.evidence-cards.v1"
PARAGRAPH_AUDITS_SCHEMA = "conflux-weave.writer-paragraph-audits.v1"
WRITER_PROMPT_VERSION = "writer-zh-cards-v1"

WRITER_MAX_OUTPUT_TOKENS = 8192
WRITER_MAX_ATTEMPTS = 3
AUDIT_MAX_OUTPUT_TOKENS = 2000
DISTILL_MAX_OUTPUT_TOKENS = 4096
WRITER_TEMPERATURE = 0.2
MAX_SECTIONS = 8
MAX_PARAGRAPHS_PER_SECTION = 8
MAX_CLAIMS_PER_PARAGRAPH = 10
MAX_OPEN_QUESTIONS = 8
MAX_BACKGROUND_ITEMS = 6
MAX_BACKGROUND_HEADING_CHARS = 200
MAX_TEXT_CHARS = 4000
MAX_HEADING_CHARS = 200
MAX_QUESTION_CHARS = 500
MAX_CARDS = 16
MAX_CARD_SUMMARY_CHARS = 2000
MAX_CARD_KEY_POINTS = 8
MAX_CARD_KEY_POINT_CHARS = 500
MAX_CARD_TERMS = 12
MAX_CARD_TERM_CHARS = 100
MAX_CARD_SCOPE_CHARS = 500
# 写作素材中每条 Claim 附带的证据引文节选上限（W3.2.1：模型需要看到细节出处
# 才能把具体事实正确归到所引 Claim 上；全量引文太长，节选够定位归属）。
WRITER_CLAIM_EVIDENCE_CHARS = 1500

DISTILL_SYSTEM_PROMPT = (
    "You are a bilingual research distiller. You receive verified Claims and their "
    "Evidence quotes (usually English academic text). For every Evidence quote "
    "produce exactly one structured Chinese fact card. Return exactly this JSON "
    "object shape: "
    '{"cards":[{"evidence_id":"evidence-0001","zh_summary":"中文要点总结",'
    '"zh_key_points":["中文要点"],"terms":[{"en":"term","zh":"术语"}],'
    '"scope_limits":"适用范围与局限"}]} '
    "The root must contain only cards, and there must be exactly one card per "
    "supplied evidence_id. Summarize in your own natural Chinese; never translate "
    "sentence by sentence and never mirror the original word order. Preserve every "
    "qualification, condition, scope and limitation from the original. Copy "
    "numbers and identifiers verbatim. terms lists the English terms that need a "
    "standard Chinese translation with your recommended translation, so the report "
    "uses one consistent Chinese term per concept. scope_limits states what the "
    "evidence does not cover. Cards must not add facts absent from the quote. "
    "Do not output Markdown."
)

WRITER_STYLE_RULES = (
    "Style and formatting rules (mandatory): "
    "State the judgment first, then support it; every section must end with a "
    "conclusion, never stop abruptly. Use clear Chinese subjects; avoid passive-"
    "voice chains and paper-abstract phrasing. Keep paragraphs to three to five "
    "sentences and mix short narrative paragraphs with concise lists; never "
    "alternate text walls with bullet dumps. Attribution must be specific to the "
    "cited claim (which method or paper shows it), never vague phrasing like "
    "“研究表明”. Preserve every condition, scope and uncertainty stated in the "
    "claims; do not drop qualifiers. Terminology: on first mention use the "
    "standard Chinese term from the terminology table followed by the English in "
    "parentheses, 中文（English）. Short Markdown lists are allowed; fenced code "
    "blocks are allowed only for concrete examples that exist in the supplied "
    "material. Markdown tables are forbidden. Do not write citation markers "
    "yourself; the renderer appends them."
)

WRITER_TRANSLATION_ESE_BANS = (
    "Forbidden translation-ese patterns: sentence-by-sentence mirroring of the "
    "source order; abstract-style framing such as “本文提出了一种…用于解决…”; stacked "
    "“的”-chains such as “基于…的…的…”; three or more consecutive passive sentences."
)

WRITER_CARD_INSTRUCTIONS = (
    "Your input contains Chinese evidence cards distilled from the source quotes "
    "plus the authoritative verified Claims. Write from the Chinese cards so the "
    "report reads as native analysis, not translation; the cited paragraphs remain "
    "bound to the Claims. If a card seems to conflict with a Claim, follow the "
    "Claim. Copy numbers verbatim; every digit you write in a cited paragraph must "
    "literally appear in the cited Claim or its source quote."
)

WRITER_QUOTE_INSTRUCTIONS = (
    "Your input contains the verified Claims and their source quotes. Every "
    "factual statement in a cited paragraph must be entailed by the Claims it "
    "cites and their Evidence. Copy numbers verbatim; every digit you write in a "
    "cited paragraph must literally appear in the cited Claim or its source quote."
)

AUDIT_SYSTEM_PROMPT = (
    "You are an independent report faithfulness auditor. You receive the objective, "
    "the verified Claims, their Evidence quotes, closed Citations, and every report "
    "paragraph flattened with the Claim IDs it cites. For every paragraph return "
    "exactly this JSON object shape: "
    '{"audits":[{"section_index":0,"paragraph_index":0,'
    '"verdict":"supported|unsupported","rationale":"why"}]} '
    "The root must contain only audits. Audit the summary as section_index 0, "
    "paragraph_index 0. Judge semantic entailment, not wording: paragraphs are "
    "Chinese paraphrases of the cited Claims and may reorganize, merge, bold, or "
    "re-bullet them. Supported means every factual assertion in the paragraph is "
    "entailed by the union of its cited Claims and their Evidence quotes; a "
    "comparison is supported when both compared items and the compared respect are "
    "present in that union. Unsupported means the paragraph introduces a fact, "
    "entity, number, date, causal claim, or evaluation that is absent from that "
    "union, or drops a load-bearing qualifier in a way that changes the claim. "
    "Cover every paragraph exactly once and provide a non-empty rationale that "
    "quotes the exact offending sentence when unsupported."
)


@dataclass(frozen=True, slots=True)
class EvidenceCard:
    evidence_id: str
    zh_summary: str
    zh_key_points: tuple[str, ...]
    terms: tuple[tuple[str, str], ...]
    scope_limits: str
    claim_ids: tuple[str, ...] = ()  # deterministic, injected from Citations


@dataclass(frozen=True, slots=True)
class DistillOutcome:
    cards: tuple[EvidenceCard, ...]
    status: str  # "ok" | "failed"
    reason: str | None = None
    cards_artifact_id: str | None = None
    request_artifact_id: str | None = None
    response_artifact_id: str | None = None


def distill_evidence_cards(
    store: LocalArtifactStore,
    chat: OpenAICompatibleChatAdapter,
    claims: tuple[Claim, ...],
    evidence: tuple[EvidenceRef, ...],
    citations: tuple[Citation, ...],
    *,
    step_id: str = "s1-research-distill",
) -> DistillOutcome:
    """Distill Chinese fact cards; every failure degrades to quote-fed writing."""

    completion = None
    try:
        completion = chat.complete(
            system_prompt=DISTILL_SYSTEM_PROMPT,
            user_prompt=json.dumps(
                {
                    "claims": [
                        {"claim_id": item.claim_id, "text": item.text} for item in claims
                    ],
                    "evidence": [
                        {"evidence_id": item.evidence_id, "quote": item.quote}
                        for item in evidence
                    ],
                    "citations": [
                        {
                            "display_index": item.display_index,
                            "claim_id": item.claim_id,
                            "evidence_id": item.evidence_id,
                        }
                        for item in citations
                    ],
                },
                ensure_ascii=False,
            ),
            max_output_tokens=DISTILL_MAX_OUTPUT_TOKENS,
            temperature=0.0,
            json_object=True,
            enable_thinking=False,
            producer_step_id=step_id,
        )
        cards = _parse_cards(completion.content, evidence)
        claims_by_evidence: dict[str, list[str]] = {}
        for citation in citations:
            ids = claims_by_evidence.setdefault(citation.evidence_id, [])
            if citation.claim_id not in ids:
                ids.append(citation.claim_id)
        cards = tuple(
            replace(card, claim_ids=tuple(claims_by_evidence.get(card.evidence_id, ())))
            for card in cards
        )
        cards_ref = store.put_json(
            {
                "schema_version": EVIDENCE_CARDS_SCHEMA,
                "cards": [
                    {
                        "evidence_id": card.evidence_id,
                        "zh_summary": card.zh_summary,
                        "zh_key_points": list(card.zh_key_points),
                        "terms": [{"en": en, "zh": zh} for en, zh in card.terms],
                        "scope_limits": card.scope_limits,
                        "claim_ids": list(card.claim_ids),
                    }
                    for card in cards
                ],
            },
            producer_step_id=step_id,
            schema_version=EVIDENCE_CARDS_SCHEMA,
        )
        return DistillOutcome(
            cards=cards,
            status="ok",
            cards_artifact_id=cards_ref.artifact_id,
            request_artifact_id=completion.request_artifact.artifact_id,
            response_artifact_id=completion.response_artifact.artifact_id,
        )
    except Exception as exc:  # noqa: BLE001 - 卡片失败回退引文直读，不阻断交付
        outcome = DistillOutcome(cards=(), status="failed", reason=f"evidence card distillation failed: {exc}")
        if completion is not None:
            outcome = replace(
                outcome,
                request_artifact_id=completion.request_artifact.artifact_id,
                response_artifact_id=completion.response_artifact.artifact_id,
            )
        return outcome


def _parse_cards(content: str, evidence: tuple[EvidenceRef, ...]) -> tuple[EvidenceCard, ...]:
    payload = json.loads(content)
    if not isinstance(payload, dict) or set(payload) != {"cards"} or not isinstance(payload["cards"], list):
        raise ValueError("distiller output must contain only cards")
    known = {item.evidence_id for item in evidence}
    if len(payload["cards"]) != len(known):
        raise ValueError(f"distiller must return exactly one card per evidence ({len(known)})")
    seen = set()
    cards = []
    for index, raw in enumerate(payload["cards"]):
        if not isinstance(raw, dict) or set(raw) != {
            "evidence_id",
            "zh_summary",
            "zh_key_points",
            "terms",
            "scope_limits",
        }:
            raise ValueError(f"card {index} has an invalid schema")
        evidence_id = raw["evidence_id"]
        if evidence_id not in known or evidence_id in seen:
            raise ValueError(f"card {index} must reference each evidence exactly once")
        seen.add(evidence_id)
        zh_summary = raw["zh_summary"]
        if not isinstance(zh_summary, str) or not zh_summary.strip() or len(zh_summary) > MAX_CARD_SUMMARY_CHARS:
            raise ValueError(f"card {index} zh_summary must be a bounded non-empty string")
        key_points = raw["zh_key_points"]
        if not isinstance(key_points, list) or len(key_points) > MAX_CARD_KEY_POINTS:
            raise ValueError(f"card {index} must have at most {MAX_CARD_KEY_POINTS} key points")
        points = []
        for point in key_points:
            if not isinstance(point, str) or not point.strip() or len(point) > MAX_CARD_KEY_POINT_CHARS:
                raise ValueError(f"card {index} key point must be a bounded non-empty string")
            points.append(point.strip())
        raw_terms = raw["terms"]
        if not isinstance(raw_terms, list) or len(raw_terms) > MAX_CARD_TERMS:
            raise ValueError(f"card {index} must have at most {MAX_CARD_TERMS} terms")
        terms = []
        for term in raw_terms:
            if not isinstance(term, dict) or set(term) != {"en", "zh"}:
                raise ValueError(f"card {index} term has an invalid schema")
            en, zh = term["en"], term["zh"]
            if (
                not isinstance(en, str) or not en.strip() or len(en) > MAX_CARD_TERM_CHARS
                or not isinstance(zh, str) or not zh.strip() or len(zh) > MAX_CARD_TERM_CHARS
            ):
                raise ValueError(f"card {index} term must be bounded non-empty strings")
            terms.append((en.strip(), zh.strip()))
        scope = raw["scope_limits"]
        if not isinstance(scope, str) or len(scope) > MAX_CARD_SCOPE_CHARS:
            raise ValueError(f"card {index} scope_limits must be a bounded string")
        cards.append(EvidenceCard(evidence_id, zh_summary.strip(), tuple(points), tuple(terms), scope.strip()))
    return tuple(cards)


def _writer_system_prompt(with_cards: bool) -> str:
    return (
        "You are a research report Writer. You receive an objective, Verifier-accepted "
        "Claims and report material. Compose a readable, rich research report. "
        "Write every text field in the same language as the objective. Quality bar: "
        "answer the objective directly; prefer concrete details and examples from the "
        "supplied material over abstraction. Claim IDs are verbatim identifiers copied from the supplied claim list - never invent or renumber them. Every claim_ids list must be "
        "non-empty, duplicate-free, and reference only supplied Claim IDs. "
        "Layout rules (mandatory): "
        "summary.text is exactly 3-5 Markdown bullet lines; each line starts with "
        "\"- **\" plus a bolded self-contained conclusion, then a colon and one "
        "supporting sentence grounded in the cited claims. "
        "Prefix section headings with Chinese ordinals in order (一、二、三…). "
        "Each section contains at least one Markdown list for enumerations, at least "
        "one bolded key conclusion (**…**), and at least one explicit cross-source "
        "comparison (与 X 相比，Y…) when the material supports it. "
        "Paragraphs: one assertion per sentence, at most 3-5 short sentences, target "
        "at most 250 characters; alternate narrative and lists; never stack text "
        "walls or bullet dumps. "
        "Unverified paragraphs: inside any section you may intersperse model-"
        "knowledge paragraphs that carry general concepts the material does not "
        "cover - definitions, terminology, mental models, common mechanisms, and "
        "clearly illustrative examples such as generic JSON snippets or worked "
        "flows. Mark them with \"claim_ids\": [] and \"unverified\": true; the "
        "renderer prefixes ○ and labels them unverified. They must not state "
        "specific statistics, dates, prices, or benchmark numbers as fact, must not "
        "contradict the cited content, and one paragraph must not mix verified and "
        "unverified statements. "
        "background is the same model-knowledge lane for longer standalone items "
        "(0-6 items, heading + text). Summarize every supplied Claim somewhere in "
        "the cited sections. open_questions lists what the corpus could not answer. "
        "Attribution rules (mandatory): the claim_evidence list shows the Evidence "
        "excerpts behind each Claim - a paragraph may cite multiple Claims, and "
        "before you state any specific fact, entity, model name, number, date, "
        "mechanism or example, locate it in the claim_evidence excerpts and add "
        "that Claim's ID to the paragraph's claim_ids. If a detail appears in no "
        "claim_evidence excerpt, either drop it or move the sentence into an "
        "unverified paragraph (claim_ids: [], unverified: true). Never leave a "
        "cited paragraph with specifics that its cited Claims' excerpts do not "
        "cover - the auditor rejects exactly that and the whole report falls "
        "back. A cross-source comparison is supported only when both compared "
        "items AND the compared respect appear in the cited Claims' excerpts; "
        "otherwise put the comparison in an unverified paragraph or drop it. "
        + (WRITER_CARD_INSTRUCTIONS if with_cards else WRITER_QUOTE_INSTRUCTIONS)
        + " "
        + WRITER_STYLE_RULES
        + " "
        + WRITER_TRANSLATION_ESE_BANS
        + " Return EXACTLY this JSON object shape and nothing else: "
        '{"summary":{"text":"- **结论一**：支撑句。\n- **结论二**：支撑句。","claim_ids":["claim-0001"]},'
        '"sections":[{"heading":"一、小节标题",'
        '"paragraphs":[{"text":"正文段落","claim_ids":["claim-0001"]},'
        '{"text":"通识或示意段落","claim_ids":[],"unverified":true}]}],'
        '"background":[{"heading":"背景标题","text":"背景正文"}],'
        '"open_questions":["开放问题"]} '
        "The root must contain exactly summary, sections, background, and open_questions; "
        "every section is {\"heading\": string, \"paragraphs\": list} and every paragraph "
        "is {\"text\": string, \"claim_ids\": list, \"unverified\": optional bool}; "
        "verified paragraphs cite 1-10 Claim IDs, unverified paragraphs cite none; "
        "no extra keys anywhere."
    )


DIGIT_PATTERN = re.compile(r"\d+(?:\.\d+)?")
LIST_MARKER_PATTERN = re.compile(r"^\s*(?:[-*+]|\d+\.)\s+")
CITATION_MARKER_PATTERN = re.compile(r"\[\d+\]")


def _digit_runs(text: str) -> list[str]:
    lines = []
    for line in text.splitlines():
        while True:
            stripped = LIST_MARKER_PATTERN.sub("", line)
            if stripped == line:
                break
            line = stripped
        lines.append(line)
    cleaned = CITATION_MARKER_PATTERN.sub(" ", "\n".join(lines))
    return DIGIT_PATTERN.findall(cleaned)


def _require_no_digit_drift(
    document: ReportDocument,
    claims: tuple[Claim, ...],
    evidence: tuple[EvidenceRef, ...],
    citations: tuple[Citation, ...],
) -> None:
    """C5: every digit in a cited paragraph must exist in its sources verbatim."""

    claim_by_id = {claim.claim_id: claim for claim in claims}
    evidence_by_id = {item.evidence_id: item for item in evidence}
    evidence_by_claim: dict[str, set[str]] = {}
    for citation in citations:
        evidence_by_claim.setdefault(citation.claim_id, set()).add(citation.evidence_id)
    paragraphs = [(0, 0, document.summary)]
    for section_index, section in enumerate(document.sections, 1):
        for paragraph_index, paragraph in enumerate(section.paragraphs):
            paragraphs.append((section_index, paragraph_index, paragraph))
    for section_index, paragraph_index, paragraph in paragraphs:
        if paragraph.unverified:
            continue
        allowed: set[str] = set()
        for claim_id in paragraph.claim_ids:
            allowed.update(DIGIT_PATTERN.findall(claim_by_id[claim_id].text))
            for evidence_id in evidence_by_claim.get(claim_id, ()):
                allowed.update(DIGIT_PATTERN.findall(evidence_by_id[evidence_id].quote))
        drift = [digit for digit in _digit_runs(paragraph.text) if digit not in allowed]
        if drift:
            raise ValueError(
                f"digit drift in section {section_index} paragraph {paragraph_index}: {drift[:5]}"
            )


@dataclass(frozen=True, slots=True)
class WriterOutcome:
    document: ReportDocument | None
    status: str
    reason: str | None
    warnings: tuple[str, ...]
    document_artifact_id: str | None = None
    audit_artifact_id: str | None = None
    writer_request_artifact_id: str | None = None
    writer_response_artifact_id: str | None = None
    audit_request_artifact_id: str | None = None
    audit_response_artifact_id: str | None = None


def _claim_evidence_excerpts(
    claims: tuple[Claim, ...],
    evidence: tuple[EvidenceRef, ...],
    citations: tuple[Citation, ...],
) -> list[dict]:
    """claim → 其引用证据的引文节选（确定性构造，不依赖模型）。"""

    evidence_by_id = {item.evidence_id: item for item in evidence}
    grouped: dict[str, list[dict]] = {}
    for citation in citations:
        ref = evidence_by_id.get(citation.evidence_id)
        if ref is None or citation.claim_id in grouped and any(
            entry["evidence_id"] == citation.evidence_id
            for entry in grouped[citation.claim_id]
        ):
            continue
        grouped.setdefault(citation.claim_id, []).append(
            {
                "evidence_id": ref.evidence_id,
                "excerpt": ref.quote[:WRITER_CLAIM_EVIDENCE_CHARS],
            }
        )
    return [
        {"claim_id": claim.claim_id, "evidence": grouped.get(claim.claim_id, [])}
        for claim in claims
    ]


def compose_report_document(
    store: LocalArtifactStore,
    chat: OpenAICompatibleChatAdapter,
    objective: str,
    claims: tuple[Claim, ...],
    evidence: tuple[EvidenceRef, ...],
    citations: tuple[Citation, ...],
    *,
    cards: tuple[EvidenceCard, ...] = (),
    writer_step_id: str = "s1-research-write",
    audit_step_id: str = "s1-research-write-audit",
) -> WriterOutcome:
    """Compose the verified-claim report; every failure degrades, none raises."""

    writer_completion = None
    audit_completion = None
    try:
        shared_material = {
            "objective": objective,
            "claims": [
                {"claim_id": item.claim_id, "text": item.text} for item in claims
            ],
            # 始终附上 claim→证据节选：审计要求"段落断言 ⊆ 所引 Claim 及其证据
            # 并集"，模型必须能看到证据细节才能正确归属（W3.2.1）。
            "claim_evidence": _claim_evidence_excerpts(claims, evidence, citations),
            "citations": [
                {
                    "display_index": item.display_index,
                    "claim_id": item.claim_id,
                    "evidence_id": item.evidence_id,
                }
                for item in citations
            ],
        }
        writer_material = (
            {
                **shared_material,
                "cards": [
                    {
                        "evidence_id": card.evidence_id,
                        "zh_summary": card.zh_summary,
                        "zh_key_points": list(card.zh_key_points),
                        "terms": [{"en": en, "zh": zh} for en, zh in card.terms],
                        "scope_limits": card.scope_limits,
                        "claim_ids": list(card.claim_ids),
                    }
                    for card in cards
                ],
            }
            if cards
            else {
                **shared_material,
                "evidence": [
                    {"evidence_id": item.evidence_id, "quote": item.quote}
                    for item in evidence
                ],
            }
        )
        # 一次修复重试统一覆盖三类违规：JSON schema / C5 数字漂移 / 审计拒绝。
        # 只规范化格式与归属，全部质量门照旧把关；重试自身失败按原始违规降级（W2a.2）。
        valid_ids = ", ".join(claim.claim_id for claim in claims)
        document = None
        audit_completion = None
        first_violation = ""
        latest_violation = ""
        for attempt in range(WRITER_MAX_ATTEMPTS):
            try:
                writer_completion = chat.complete(
                    system_prompt=(
                        _writer_system_prompt(with_cards=bool(cards))
                        + (
                            f" Your previous attempt was rejected: {latest_violation} Fix the "
                            "violation and return ONLY the exact JSON object. Check the "
                            "claim_evidence excerpts: if the offending detail appears in some "
                            "Claim's excerpt, add that Claim ID to the paragraph's claim_ids "
                            "(a paragraph may cite multiple Claims); if it appears in none, "
                            "move the sentence into an unverified paragraph (claim_ids: [], "
                            "unverified: true). If a sentence is general knowledge, move it "
                            "into an unverified paragraph; if it asserts a specific fact, "
                            "cite the Claim whose excerpt actually contains it; copy numbers "
                            f"verbatim. The ONLY Claim IDs that exist are: {valid_ids}."
                            if attempt
                            else ""
                        )
                    ),
                    user_prompt=json.dumps(writer_material, ensure_ascii=False),
                    max_output_tokens=WRITER_MAX_OUTPUT_TOKENS,
                    temperature=WRITER_TEMPERATURE,
                    json_object=True,
                    enable_thinking=False,
                    producer_step_id=writer_step_id if attempt == 0 else f"{writer_step_id}-repair",
                )
                document = _parse_writer_payload(writer_completion.content, claims, objective)
                _require_no_digit_drift(document, claims, evidence, citations)
                omitted = unreferenced_claim_ids(document, claims)
                if 2 * len(omitted) > len(claims):
                    raise ValueError(f"writer omitted {len(omitted)} of {len(claims)} verified Claims")
                warnings: tuple[str, ...] = ()
                if omitted:
                    warnings = (
                        f"Writer 未引用 {len(omitted)} 条已验证 Claim，已由确定性代码汇总到「补充发现」。",
                    )
                document = replace(
                    document, unreferenced_claim_ids=omitted, warnings=warnings
                )
                paragraphs = _flatten_paragraphs(document)
                audit_completion = chat.complete(
                    system_prompt=AUDIT_SYSTEM_PROMPT,
                    user_prompt=json.dumps(
                        {
                            "objective": objective,
                            "claims": [
                                {"claim_id": item.claim_id, "text": item.text} for item in claims
                            ],
                            "evidence": [
                                {"evidence_id": item.evidence_id, "quote": item.quote}
                                for item in evidence
                            ],
                            "citations": [
                                {
                                    "display_index": item.display_index,
                                    "claim_id": item.claim_id,
                                    "evidence_id": item.evidence_id,
                                }
                                for item in citations
                            ],
                            "paragraphs": [
                                {
                                    "section_index": section_index,
                                    "paragraph_index": paragraph_index,
                                    "text": paragraph.text,
                                    "claim_ids": list(paragraph.claim_ids),
                                }
                                for section_index, paragraph_index, paragraph in paragraphs
                            ],
                        },
                        ensure_ascii=False,
                    ),
                    max_output_tokens=AUDIT_MAX_OUTPUT_TOKENS,
                    temperature=0.0,
                    json_object=True,
                    enable_thinking=False,
                    producer_step_id=audit_step_id,
                )
                _parse_audit_payload(audit_completion.content, paragraphs)
                break
            except ValueError as violation:
                # 首错为根因：兜底原因保留首次违规；反馈用最新违规；二次错误不掩盖首错。
                latest_violation = str(violation)[:500]
                if not first_violation:
                    first_violation = latest_violation
                if attempt == WRITER_MAX_ATTEMPTS - 1:
                    return _fallback_outcome(
                        store, objective, claims, first_violation,
                        writer_completion=writer_completion,
                        audit_completion=audit_completion,
                    )
                writer_material = {
                    **writer_material,
                    "previous_invalid_output": writer_completion.content[:2000],
                }
            except Exception:
                if latest_violation:
                    return _fallback_outcome(
                        store, objective, claims, first_violation or latest_violation,
                        writer_completion=writer_completion,
                        audit_completion=audit_completion,
                    )
                raise
        document_ref = store.put_json(
            {
                "schema_version": REPORT_DOCUMENT_SCHEMA,
                "writer_prompt_version": WRITER_PROMPT_VERSION,
                "objective": document.objective,
                "summary": asdict(document.summary),
                "sections": [
                    {
                        "heading": section.heading,
                        "paragraphs": [asdict(item) for item in section.paragraphs],
                    }
                    for section in document.sections
                ],
                "background": [asdict(item) for item in document.background],
                "open_questions": list(document.open_questions),
                "unreferenced_claim_ids": list(document.unreferenced_claim_ids),
                "warnings": list(document.warnings),
            },
            producer_step_id=writer_step_id,
            schema_version=REPORT_DOCUMENT_SCHEMA,
        )
        audits_ref = store.put_json(
            {
                "schema_version": PARAGRAPH_AUDITS_SCHEMA,
                "paragraph_count": len(paragraphs),
                "content": audit_completion.content,
            },
            producer_step_id=audit_step_id,
            schema_version=PARAGRAPH_AUDITS_SCHEMA,
        )
        return WriterOutcome(
            document=document,
            status="ok",
            reason=None,
            warnings=warnings,
            document_artifact_id=document_ref.artifact_id,
            audit_artifact_id=audits_ref.artifact_id,
            writer_request_artifact_id=writer_completion.request_artifact.artifact_id,
            writer_response_artifact_id=writer_completion.response_artifact.artifact_id,
            audit_request_artifact_id=audit_completion.request_artifact.artifact_id,
            audit_response_artifact_id=audit_completion.response_artifact.artifact_id,
        )
    except Exception as exc:  # noqa: BLE001 - 兜底交付，绝不中断
        return _fallback_outcome(
            store, objective, claims, f"report writer failed: {exc}",
            writer_completion=writer_completion,
            audit_completion=audit_completion,
        )


def build_deterministic_document(
    objective: str, claims: tuple[Claim, ...]
) -> ReportDocument:
    """Complete report assembled without the model: every accepted Claim verbatim.

    禁止降级（W2a.2）：模型写作未通过校验时的交付底线。逐字使用 Claim 原文，
    引用闭合与 C5 由构造保证，不依赖任何模型输出。
    排版（W3.2.1）：全部 Claim 收进唯一一节（不再按 5 条切片产生同名小节）；
    摘要是每条 Claim 首句的确定性提要，不再与正文逐字重复。
    """
    if not claims:
        raise ValueError("deterministic fallback requires at least one Claim")

    def _digest(text: str) -> str:
        for cut in ("。", "；", "？", "！", ".", ";", "?", "!"):
            position = text.find(cut)
            if position > 0:
                candidate = text[: position + 1].strip()
                if len(candidate) >= 12:
                    return candidate
        return text.strip()

    summary = ReportParagraph(
        text="\n".join(f"- {_digest(claim.text)}" for claim in claims),
        claim_ids=tuple(claim.claim_id for claim in claims),
    )
    sections = [
        ReportSection(
            "一、已验证研究发现",
            tuple(
                ReportParagraph(claim.text, (claim.claim_id,)) for claim in claims
            ),
        )
    ]
    return ReportDocument(objective=objective, summary=summary, sections=tuple(sections))


def _fallback_outcome(
    store: LocalArtifactStore,
    objective: str,
    claims: tuple[Claim, ...],
    reason: str,
    *,
    writer_completion=None,
    audit_completion=None,
) -> WriterOutcome:
    document = build_deterministic_document(objective, claims)
    document_ref = store.put_json(
        {
            "schema_version": REPORT_DOCUMENT_SCHEMA,
            "writer_prompt_version": WRITER_PROMPT_VERSION,
            "assembly": "deterministic-fallback",
            "objective": document.objective,
            "summary": asdict(document.summary),
            "sections": [
                {
                    "heading": section.heading,
                    "paragraphs": [asdict(item) for item in section.paragraphs],
                }
                for section in document.sections
            ],
            "background": [],
            "open_questions": [],
            "unreferenced_claim_ids": [],
            "warnings": [f"模型写作未通过校验（{reason}），已由确定性代码组装完整报告。"],
        },
        producer_step_id="s1-research-write-fallback",
        schema_version=REPORT_DOCUMENT_SCHEMA,
    )
    outcome = WriterOutcome(
        document=document,
        status="fallback",
        reason=reason,
        warnings=(
            f"模型写作未通过校验（{reason}），已由确定性代码组装完整报告。",
        ),
        document_artifact_id=document_ref.artifact_id,
    )
    if writer_completion is not None:
        outcome = replace(
            outcome,
            writer_request_artifact_id=writer_completion.request_artifact.artifact_id,
            writer_response_artifact_id=writer_completion.response_artifact.artifact_id,
        )
    if audit_completion is not None:
        outcome = replace(
            outcome,
            audit_request_artifact_id=audit_completion.request_artifact.artifact_id,
            audit_response_artifact_id=audit_completion.response_artifact.artifact_id,
        )
    return outcome


def _parse_writer_payload(
    content: str, claims: tuple[Claim, ...], objective: str
) -> ReportDocument:
    payload = json.loads(content)
    if not isinstance(payload, dict) or set(payload) != {
        "summary",
        "sections",
        "background",
        "open_questions",
    }:
        raise ValueError(
            "writer output must contain exactly summary, sections, background, and open_questions"
        )
    summary = _parse_paragraph(payload["summary"], claims, "summary")
    raw_sections = payload["sections"]
    if not isinstance(raw_sections, list) or not 1 <= len(raw_sections) <= MAX_SECTIONS:
        raise ValueError(f"writer sections must contain between 1 and {MAX_SECTIONS} items")
    sections = []
    for index, raw_section in enumerate(raw_sections):
        if not isinstance(raw_section, dict) or set(raw_section) != {"heading", "paragraphs"}:
            raise ValueError(f"writer section {index} has an invalid schema")
        heading = raw_section["heading"]
        if not isinstance(heading, str) or not heading.strip() or len(heading) > MAX_HEADING_CHARS:
            raise ValueError(f"writer section {index} heading must be a bounded non-empty string")
        raw_paragraphs = raw_section["paragraphs"]
        if (
            not isinstance(raw_paragraphs, list)
            or not 1 <= len(raw_paragraphs) <= MAX_PARAGRAPHS_PER_SECTION
        ):
            raise ValueError(
                f"writer section {index} must contain between 1 and {MAX_PARAGRAPHS_PER_SECTION} paragraphs"
            )
        paragraphs = tuple(
            _parse_paragraph(raw_paragraph, claims, f"section {index} paragraph {paragraph_index}")
            for paragraph_index, raw_paragraph in enumerate(raw_paragraphs)
        )
        sections.append(ReportSection(heading.strip(), paragraphs))
    raw_questions = payload["open_questions"]
    if not isinstance(raw_questions, list) or len(raw_questions) > MAX_OPEN_QUESTIONS:
        raise ValueError(f"writer open_questions must be a list of at most {MAX_OPEN_QUESTIONS} items")
    questions = []
    for index, question in enumerate(raw_questions):
        if not isinstance(question, str) or not question.strip() or len(question) > MAX_QUESTION_CHARS:
            raise ValueError(f"writer open question {index} must be a bounded non-empty string")
        questions.append(question.strip())
    background = _parse_background(payload["background"])
    return ReportDocument(
        objective=objective,
        summary=summary,
        sections=tuple(sections),
        open_questions=tuple(questions),
        background=background,
    )


def _parse_background(raw: object) -> tuple[ReportBackground, ...]:
    if not isinstance(raw, list) or len(raw) > MAX_BACKGROUND_ITEMS:
        raise ValueError(f"writer background must be a list of at most {MAX_BACKGROUND_ITEMS} items")
    items = []
    for index, raw_item in enumerate(raw):
        if not isinstance(raw_item, dict) or set(raw_item) != {"heading", "text"}:
            raise ValueError(f"writer background item {index} has an invalid schema")
        heading = raw_item["heading"]
        text = raw_item["text"]
        if (
            not isinstance(heading, str)
            or not heading.strip()
            or len(heading) > MAX_BACKGROUND_HEADING_CHARS
        ):
            raise ValueError(f"writer background item {index} heading must be a bounded non-empty string")
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT_CHARS:
            raise ValueError(f"writer background item {index} text must be a bounded non-empty string")
        items.append(ReportBackground(heading.strip(), text.strip()))
    return tuple(items)


def _parse_paragraph(
    raw: object, claims: tuple[Claim, ...], label: str
) -> ReportParagraph:
    if not isinstance(raw, dict) or set(raw) not in (
        {"text", "claim_ids"},
        {"text", "claim_ids", "unverified"},
    ):
        raise ValueError(f"writer {label} has an invalid schema")
    text = raw["text"]
    if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT_CHARS:
        raise ValueError(f"writer {label} text must be a bounded non-empty string")
    unverified = raw.get("unverified", False)
    if not isinstance(unverified, bool):
        raise ValueError(f"writer {label} unverified must be a boolean")
    raw_claim_ids = raw["claim_ids"]
    if unverified:
        if raw_claim_ids:
            raise ValueError(f"writer {label} is unverified and must not cite Claims")
        return ReportParagraph(text.strip(), (), unverified=True)
    if not isinstance(raw_claim_ids, list) or not 1 <= len(raw_claim_ids) <= MAX_CLAIMS_PER_PARAGRAPH:
        raise ValueError(f"writer {label} must cite between 1 and {MAX_CLAIMS_PER_PARAGRAPH} Claims")
    claim_ids = tuple(str(value) for value in raw_claim_ids)
    known = {item.claim_id for item in claims}
    if len(claim_ids) != len(set(claim_ids)):
        raise ValueError(f"writer {label} repeats a Claim reference")
    unknown = [value for value in claim_ids if value not in known]
    if unknown:
        raise ValueError(f"writer {label} references unknown Claims: {', '.join(unknown)}")
    return ReportParagraph(text.strip(), claim_ids)


def _flatten_paragraphs(
    document: ReportDocument,
) -> list[tuple[int, int, ReportParagraph]]:
    """Cited paragraphs only: unverified paragraphs are excluded from the audit."""

    flattened = [(0, 0, document.summary)]
    for section_index, section in enumerate(document.sections, 1):
        for paragraph_index, paragraph in enumerate(section.paragraphs):
            if not paragraph.unverified:
                flattened.append((section_index, paragraph_index, paragraph))
    return flattened


def _parse_audit_payload(
    content: str, paragraphs: list[tuple[int, int, ReportParagraph]]
) -> None:
    payload = json.loads(content)
    if not isinstance(payload, dict) or set(payload) != {"audits"}:
        raise ValueError("audit output must contain only audits")
    raw_audits = payload["audits"]
    if not isinstance(raw_audits, list):
        raise ValueError("audit output must contain only audits")
    expected = {(section_index, paragraph_index) for section_index, paragraph_index, _ in paragraphs}
    seen = set()
    unsupported = []
    for raw in raw_audits:
        if not isinstance(raw, dict) or set(raw) != {
            "section_index",
            "paragraph_index",
            "verdict",
            "rationale",
        }:
            raise ValueError("audit item has an invalid schema")
        section_index = raw["section_index"]
        paragraph_index = raw["paragraph_index"]
        verdict = raw["verdict"]
        rationale = raw["rationale"]
        if not isinstance(section_index, int) or not isinstance(paragraph_index, int):
            raise ValueError("audit indices must be integers")
        if (section_index, paragraph_index) in seen:
            raise ValueError("audit must cover every paragraph exactly once")
        seen.add((section_index, paragraph_index))
        if verdict not in {"supported", "unsupported"}:
            raise ValueError("audit verdict must be supported or unsupported")
        if not isinstance(rationale, str) or not rationale.strip():
            raise ValueError("audit rationale must be a non-empty string")
        if verdict == "unsupported":
            unsupported.append((section_index, paragraph_index, rationale.strip()))
    if seen != expected:
        raise ValueError("audit must cover every paragraph exactly once")
    if unsupported:
        section_index, paragraph_index, rationale = unsupported[0]
        raise ValueError(
            "audit rejected paragraph "
            f"section {section_index} paragraph {paragraph_index}: {rationale}"
        )


# ---------------------------------------------------------------------------
# W3.5 融合 Writer：引擎报告为叙事骨架，本地核验证据按段落融入。
# ---------------------------------------------------------------------------

FUSED_MAX_ATTEMPTS = 3
FUSED_MAX_PARAGRAPHS_PER_SECTION = 16
FUSED_MAX_OPEN_QUESTIONS = 8
FUSED_WEB_SOURCE_CONTENT_CHARS = 2000

FUSED_SYSTEM_PROMPT = (
    "You are a research report Writer performing evidence fusion. You receive an "
    "objective, the aggregation engine's report outline (sections with indexed "
    "paragraphs, narrative synthesized from web sources), a merge plan (which "
    "web sources back each paragraph; which verified Claims support, qualify, "
    "contradict or extend it), the verified Claims with their Evidence excerpts, "
    "and optional Chinese evidence cards. Rewrite EVERY engine paragraph as one "
    "polished Chinese research-report paragraph: keep the engine paragraph's "
    "facts and their order, weave its assigned Claims into the same flowing "
    "text where the relation says (a contradicting Claim must surface as an "
    "explicit conflicting finding with both sides), and never mention the "
    "mechanics (no phrases like 本地语料补充说 or 网络来源认为). Keep section "
    "headings EXACTLY as supplied - same count, same order, no extra sections. "
    "Every output paragraph must carry web_source_ids (the paragraph's "
    "attributed web source ids from the plan, copied verbatim) and claim_ids "
    "(the Claim IDs it actually uses, verbatim from the supplied list). A "
    "paragraph must have at least one of the two; if the plan gave it no web "
    "sources and it uses no Claim, mark it {\"unverified\": true} with both "
    "lists empty - unverified paragraphs must not state specific statistics, "
    "dates, prices, or benchmark numbers as fact. Do not add facts absent from "
    "the engine paragraph, its attributed web sources, the Claims, or their "
    "Evidence. Copy numbers verbatim; every digit you write must literally "
    "appear in one of those. Do not write citation markers yourself; the "
    "renderer appends them. open_questions lists what neither the engine "
    "narrative nor the Claims answer. "
    "Style rules (mandatory): state the judgment first, then support it; keep "
    "paragraphs to three to six sentences and mix short narrative paragraphs "
    "with concise lists; preserve every condition, scope and uncertainty; on "
    "first mention use the standard Chinese term followed by English in "
    "parentheses, 中文（English）; short Markdown lists are allowed; Markdown "
    "tables are forbidden; a paragraph must not mix verified and unverified "
    "statements. "
    + WRITER_TRANSLATION_ESE_BANS
    + " Return EXACTLY this JSON object shape and nothing else: "
    '{"sections":[{"heading":"一、小节标题","paragraphs":[{"text":"融合后的段落",'
    '"claim_ids":["claim-0001"],"web_source_ids":["web-0001"]}]}],'
    '"open_questions":["开放问题"]} '
    "The root must contain exactly sections and open_questions; every section is "
    '{"heading": string, "paragraphs": list}; every paragraph is {"text": string, '
    '"claim_ids": list, "web_source_ids": list, "unverified": optional bool}; '
    "no extra keys anywhere."
)

FUSED_AUDIT_SYSTEM_PROMPT = (
    "You are an independent report faithfulness auditor for a fused research "
    "report. You receive the objective, the verified Claims with their Evidence "
    "quotes, closed Citations, the web source contents, and every report "
    "paragraph flattened with the Claim IDs and web source IDs it cites. For "
    "every paragraph return exactly this JSON object shape: "
    '{"audits":[{"section_index":0,"paragraph_index":0,'
    '"verdict":"supported|unsupported","rationale":"why"}]} '
    "The root must contain only audits. Judge semantic entailment, not wording: "
    "supported means every factual assertion in the paragraph is entailed by "
    "the union of its cited Claims (plus their Evidence quotes) and the "
    "contents of its cited web sources. Unsupported means the paragraph "
    "introduces a fact, entity, number, date, causal claim, or evaluation "
    "absent from that union, or drops a load-bearing qualifier in a way that "
    "changes the claim. Cover every paragraph exactly once and provide a "
    "non-empty rationale that quotes the exact offending sentence when "
    "unsupported."
)


def _parse_fused_paragraph(
    raw: object,
    claims: tuple[Claim, ...],
    web_source_ids: tuple[str, ...],
    label: str,
) -> ReportParagraph:
    if not isinstance(raw, dict) or set(raw) not in (
        {"text", "claim_ids", "web_source_ids"},
        {"text", "claim_ids", "web_source_ids", "unverified"},
    ):
        raise ValueError(f"writer {label} has an invalid schema")
    text = raw["text"]
    if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT_CHARS:
        raise ValueError(f"writer {label} text must be a bounded non-empty string")
    unverified = raw.get("unverified", False)
    if not isinstance(unverified, bool):
        raise ValueError(f"writer {label} unverified must be a boolean")
    raw_claim_ids = raw["claim_ids"]
    raw_web_ids = raw["web_source_ids"]
    if not isinstance(raw_claim_ids, list) or not isinstance(raw_web_ids, list):
        raise ValueError(f"writer {label} claim_ids and web_source_ids must be lists")
    claim_ids = tuple(str(value) for value in raw_claim_ids)
    source_ids = tuple(str(value) for value in raw_web_ids)
    known_claims = {item.claim_id for item in claims}
    known_sources = set(web_source_ids)
    if unverified:
        if claim_ids or source_ids:
            raise ValueError(f"writer {label} is unverified and must not cite Claims or web sources")
        return ReportParagraph(text.strip(), (), unverified=True)
    if len(claim_ids) > MAX_CLAIMS_PER_PARAGRAPH or len(source_ids) > MAX_CLAIMS_PER_PARAGRAPH:
        raise ValueError(f"writer {label} exceeds citation limits")
    if not claim_ids and not source_ids:
        raise ValueError(f"writer {label} must cite Claims or web sources")
    if len(claim_ids) != len(set(claim_ids)) or len(source_ids) != len(set(source_ids)):
        raise ValueError(f"writer {label} repeats a citation reference")
    unknown_claims = [value for value in claim_ids if value not in known_claims]
    if unknown_claims:
        raise ValueError(f"writer {label} references unknown Claims: {', '.join(unknown_claims)}")
    unknown_sources = [value for value in source_ids if value not in known_sources]
    if unknown_sources:
        raise ValueError(f"writer {label} references unknown web sources: {', '.join(unknown_sources)}")
    return ReportParagraph(text.strip(), claim_ids, web_source_ids=source_ids)


def _parse_fused_writer_payload(
    content: str,
    claims: tuple[Claim, ...],
    narrative: EngineNarrative,
    web_source_ids: tuple[str, ...],
) -> tuple[tuple[ReportSection, ...], tuple[str, ...]]:
    """解析融合输出；节标题必须与引擎骨架逐字 1:1。返回 (sections, questions)。"""
    payload = json.loads(content)
    if not isinstance(payload, dict) or set(payload) != {"sections", "open_questions"}:
        raise ValueError("fused writer output must contain exactly sections and open_questions")
    raw_sections = payload["sections"]
    expected_headings = [section.heading for section in narrative.sections]
    if not isinstance(raw_sections, list) or len(raw_sections) != len(expected_headings):
        raise ValueError(
            "fused writer must keep the engine section structure: "
            f"expected {len(expected_headings)} sections"
        )
    sections = []
    for index, raw_section in enumerate(raw_sections):
        if not isinstance(raw_section, dict) or set(raw_section) != {"heading", "paragraphs"}:
            raise ValueError(f"writer section {index} has an invalid schema")
        heading = raw_section["heading"]
        if not isinstance(heading, str) or heading.strip() != expected_headings[index]:
            raise ValueError(
                "fused writer must keep engine headings verbatim and in order: "
                f"expected {expected_headings[index]!r}, got {heading!r}"
            )
        raw_paragraphs = raw_section["paragraphs"]
        if (
            not isinstance(raw_paragraphs, list)
            or not 1 <= len(raw_paragraphs) <= FUSED_MAX_PARAGRAPHS_PER_SECTION
        ):
            raise ValueError(
                f"writer section {index} must contain between 1 and {FUSED_MAX_PARAGRAPHS_PER_SECTION} paragraphs"
            )
        paragraphs = tuple(
            _parse_fused_paragraph(
                raw_paragraph, claims, web_source_ids, f"section {index} paragraph {paragraph_index}"
            )
            for paragraph_index, raw_paragraph in enumerate(raw_paragraphs)
        )
        sections.append(ReportSection(heading.strip(), paragraphs))
    raw_questions = payload["open_questions"]
    if not isinstance(raw_questions, list) or len(raw_questions) > FUSED_MAX_OPEN_QUESTIONS:
        raise ValueError(f"fused open_questions must be a list of at most {FUSED_MAX_OPEN_QUESTIONS} items")
    questions = []
    for index, question in enumerate(raw_questions):
        if not isinstance(question, str) or not question.strip() or len(question) > MAX_QUESTION_CHARS:
            raise ValueError(f"fused open question {index} must be a bounded non-empty string")
        questions.append(question.strip())
    return tuple(sections), tuple(questions)


def _require_no_fused_digit_drift(
    document: ReportDocument,
    claims: tuple[Claim, ...],
    evidence: tuple[EvidenceRef, ...],
    citations: tuple[Citation, ...],
    web_content: dict[str, str],
) -> None:
    """C5 扩展（W3.5）：融合段落的每个数字必须出现在所引 Claim、其证据或所引
    网络来源正文里——引擎叙事同样不许写出无出处的数字。"""
    claim_by_id = {claim.claim_id: claim for claim in claims}
    evidence_by_id = {item.evidence_id: item for item in evidence}
    evidence_by_claim: dict[str, set[str]] = {}
    for citation in citations:
        evidence_by_claim.setdefault(citation.claim_id, set()).add(citation.evidence_id)
    for section_index, section in enumerate(document.sections, 1):
        for paragraph_index, paragraph in enumerate(section.paragraphs):
            if paragraph.unverified:
                continue
            allowed: set[str] = set()
            for claim_id in paragraph.claim_ids:
                allowed.update(DIGIT_PATTERN.findall(claim_by_id[claim_id].text))
                for evidence_id in evidence_by_claim.get(claim_id, ()):
                    allowed.update(DIGIT_PATTERN.findall(evidence_by_id[evidence_id].quote))
            for source_id in paragraph.web_source_ids:
                allowed.update(DIGIT_PATTERN.findall(web_content.get(source_id, "")))
            drift = [digit for digit in _digit_runs(paragraph.text) if digit not in allowed]
            if drift:
                raise ValueError(
                    f"digit drift in section {section_index} paragraph {paragraph_index}: {drift[:5]}"
                )


def compose_fused_report_document(
    store: LocalArtifactStore,
    chat: OpenAICompatibleChatAdapter,
    objective: str,
    narrative: EngineNarrative,
    claims: tuple[Claim, ...],
    evidence: tuple[EvidenceRef, ...],
    citations: tuple[Citation, ...],
    *,
    plan: MergePlan,
    web_content: dict[str, str],
    cards: tuple[EvidenceCard, ...] = (),
    writer_step_id: str = "w34-fused-write",
    audit_step_id: str = "w34-fused-write-audit",
) -> WriterOutcome:
    """融合成文：引擎段落逐段改写并融入本地核验证据；失败降级，绝不中断。"""
    writer_completion = None
    audit_completion = None
    try:
        web_source_ids = tuple(web_content.keys())
        material = {
            "objective": objective,
            "engine_outline": [
                {
                    "heading": section.heading,
                    "paragraphs": [
                        {"paragraph_index": paragraph_index, "text": text}
                        for paragraph_index, text in enumerate(section.paragraphs)
                    ],
                }
                for section in narrative.sections
            ],
            "merge_plan": plan.payload(),
            "claims": [{"claim_id": item.claim_id, "text": item.text} for item in claims],
            "claim_evidence": _claim_evidence_excerpts(claims, evidence, citations),
            "citations": [
                {"display_index": item.display_index, "claim_id": item.claim_id, "evidence_id": item.evidence_id}
                for item in citations
            ],
            "web_sources": [
                {"source_id": source_id, "content": content[:FUSED_WEB_SOURCE_CONTENT_CHARS]}
                for source_id, content in web_content.items()
            ],
        }
        if cards:
            material["cards"] = [
                {
                    "evidence_id": card.evidence_id,
                    "zh_summary": card.zh_summary,
                    "zh_key_points": list(card.zh_key_points),
                    "terms": [{"en": en, "zh": zh} for en, zh in card.terms],
                    "scope_limits": card.scope_limits,
                    "claim_ids": list(card.claim_ids),
                }
                for card in cards
            ]
        assigned_claims = {
            entry.claim_id
            for item in plan.assignments
            for entry in item.claims
        }
        sections: tuple[ReportSection, ...] = ()
        open_questions: tuple[str, ...] = ()
        first_violation = ""
        latest_violation = ""
        paragraphs: list = []
        for attempt in range(FUSED_MAX_ATTEMPTS):
            try:
                writer_completion = chat.complete(
                    system_prompt=(
                        FUSED_SYSTEM_PROMPT
                        + (
                            f" Your previous attempt was rejected: {latest_violation} Fix the "
                            "violation and return ONLY the exact JSON object. Keep the engine "
                            "section headings verbatim; every paragraph needs web_source_ids "
                            "or claim_ids; every assigned Claim must be used somewhere."
                            if attempt
                            else ""
                        )
                    ),
                    user_prompt=json.dumps(material, ensure_ascii=False),
                    max_output_tokens=WRITER_MAX_OUTPUT_TOKENS,
                    temperature=WRITER_TEMPERATURE,
                    json_object=True,
                    enable_thinking=False,
                    producer_step_id=writer_step_id if attempt == 0 else f"{writer_step_id}-repair",
                )
                sections, open_questions = _parse_fused_writer_payload(
                    writer_completion.content, claims, narrative, web_source_ids
                )
                draft = ReportDocument(
                    objective=objective,
                    summary=ReportParagraph(plan.thesis, tuple(claim.claim_id for claim in claims)),
                    sections=sections,
                    open_questions=open_questions,
                )
                _require_no_fused_digit_drift(draft, claims, evidence, citations, web_content)
                referenced = {
                    claim_id
                    for section in draft.sections
                    for paragraph in section.paragraphs
                    for claim_id in paragraph.claim_ids
                }
                omitted = sorted(assigned_claims - referenced)
                if omitted:
                    raise ValueError(f"fused writer omitted assigned Claims: {', '.join(omitted)}")
                paragraphs = _flatten_paragraphs(draft)
                audit_completion = chat.complete(
                    system_prompt=FUSED_AUDIT_SYSTEM_PROMPT,
                    user_prompt=json.dumps(
                        {
                            "objective": objective,
                            "claims": [{"claim_id": item.claim_id, "text": item.text} for item in claims],
                            "evidence": [
                                {"evidence_id": item.evidence_id, "quote": item.quote} for item in evidence
                            ],
                            "citations": [
                                {"display_index": item.display_index, "claim_id": item.claim_id, "evidence_id": item.evidence_id}
                                for item in citations
                            ],
                            "web_sources": [
                                {"source_id": source_id, "content": content[:FUSED_WEB_SOURCE_CONTENT_CHARS]}
                                for source_id, content in web_content.items()
                            ],
                            "paragraphs": [
                                {
                                    "section_index": section_index,
                                    "paragraph_index": paragraph_index,
                                    "text": paragraph.text,
                                    "claim_ids": list(paragraph.claim_ids),
                                    "web_source_ids": list(paragraph.web_source_ids),
                                }
                                for section_index, paragraph_index, paragraph in paragraphs
                            ],
                        },
                        ensure_ascii=False,
                    ),
                    max_output_tokens=AUDIT_MAX_OUTPUT_TOKENS,
                    temperature=0.0,
                    json_object=True,
                    enable_thinking=False,
                    producer_step_id=audit_step_id,
                )
                _parse_audit_payload(audit_completion.content, paragraphs)
                break
            except ValueError as violation:
                latest_violation = str(violation)[:500]
                if not first_violation:
                    first_violation = latest_violation
                if attempt == FUSED_MAX_ATTEMPTS - 1:
                    return _fused_fallback_outcome(
                        store, objective, narrative, plan, claims, first_violation,
                        writer_completion=writer_completion,
                    )
            except Exception:
                if latest_violation:
                    return _fused_fallback_outcome(
                        store, objective, narrative, plan, claims, first_violation or latest_violation,
                        writer_completion=writer_completion,
                    )
                raise
        document = ReportDocument(
            objective=objective,
            summary=ReportParagraph(plan.thesis, tuple(claim.claim_id for claim in claims)),
            sections=sections,
            open_questions=open_questions,
        )
        document_ref = store.put_json(
            {
                "schema_version": REPORT_DOCUMENT_SCHEMA,
                "writer_prompt_version": "fused-engine-skeleton-v1",
                "assembly": "engine-fused",
                "objective": document.objective,
                "summary": asdict(document.summary),
                "sections": [
                    {
                        "heading": section.heading,
                        "paragraphs": [asdict(item) for item in section.paragraphs],
                    }
                    for section in document.sections
                ],
                "open_questions": list(document.open_questions),
                "warnings": [],
            },
            producer_step_id=writer_step_id,
            schema_version=REPORT_DOCUMENT_SCHEMA,
        )
        audits_ref = store.put_json(
            {
                "schema_version": PARAGRAPH_AUDITS_SCHEMA,
                "paragraph_count": len(paragraphs),
                "content": audit_completion.content,
            },
            producer_step_id=audit_step_id,
            schema_version=PARAGRAPH_AUDITS_SCHEMA,
        )
        return WriterOutcome(
            document=document,
            status="ok",
            reason=None,
            warnings=(),
            document_artifact_id=document_ref.artifact_id,
            audit_artifact_id=audits_ref.artifact_id,
            writer_request_artifact_id=writer_completion.request_artifact.artifact_id,
            writer_response_artifact_id=writer_completion.response_artifact.artifact_id,
            audit_request_artifact_id=audit_completion.request_artifact.artifact_id,
            audit_response_artifact_id=audit_completion.response_artifact.artifact_id,
        )
    except Exception as exc:  # noqa: BLE001 - 兜底交付，绝不中断
        return _fused_fallback_outcome(
            store, objective, narrative, plan, claims, f"fused writer failed: {exc}",
            writer_completion=writer_completion,
        )


def _fused_fallback_outcome(
    store: LocalArtifactStore,
    objective: str,
    narrative: EngineNarrative,
    plan: MergePlan,
    claims: tuple[Claim, ...],
    reason: str,
    *,
    writer_completion=None,
) -> WriterOutcome:
    """确定性融合组装（W3.5 降级第二级）：引擎段落原文 + 匹配 Claim 逐字嵌入。"""
    document = build_deterministic_fused_document(objective, narrative, plan, claims)
    document_ref = store.put_json(
        {
            "schema_version": REPORT_DOCUMENT_SCHEMA,
            "writer_prompt_version": "fused-engine-skeleton-v1",
            "assembly": "deterministic-fused-fallback",
            "objective": document.objective,
            "summary": asdict(document.summary),
            "sections": [
                {
                    "heading": section.heading,
                    "paragraphs": [asdict(item) for item in section.paragraphs],
                }
                for section in document.sections
            ],
            "open_questions": list(document.open_questions),
            "warnings": [f"模型融合写作未通过校验（{reason}），已按引擎段落原文+本地核验结论逐字组装。"],
        },
        producer_step_id="w34-fused-write-fallback",
        schema_version=REPORT_DOCUMENT_SCHEMA,
    )
    outcome = WriterOutcome(
        document=document,
        status="fallback",
        reason=reason,
        warnings=(f"模型融合写作未通过校验（{reason}），已按引擎段落原文+本地核验结论逐字组装。",),
        document_artifact_id=document_ref.artifact_id,
    )
    if writer_completion is not None:
        outcome = replace(
            outcome,
            writer_request_artifact_id=writer_completion.request_artifact.artifact_id,
            writer_response_artifact_id=writer_completion.response_artifact.artifact_id,
        )
    return outcome


def build_deterministic_fused_document(
    objective: str,
    narrative: EngineNarrative,
    plan: MergePlan,
    claims: tuple[Claim, ...],
) -> ReportDocument:
    """无模型的融合组装：引擎段落原文落位，匹配 Claim 逐字嵌入其段落之后。

    引用闭合由构造保证：引擎段落带 plan 归属的网络来源，Claim 段落自带
    claim_id；无归属且无 Claim 的引擎段落按未核验车道（○）呈现。
    """
    claim_by_id = {claim.claim_id: claim for claim in claims}
    sections = []
    for section_index, section in enumerate(narrative.sections):
        paragraphs: list[ReportParagraph] = []
        for paragraph_index, text in enumerate(section.paragraphs):
            web_ids = plan.web_sources_for_paragraph(section_index, paragraph_index)
            if web_ids:
                paragraphs.append(ReportParagraph(text, (), web_source_ids=web_ids))
            else:
                paragraphs.append(ReportParagraph(text, (), unverified=True))
            for entry in plan.claims_for_paragraph(section_index, paragraph_index):
                claim = claim_by_id[entry.claim_id]
                paragraphs.append(ReportParagraph(claim.text, (claim.claim_id,)))
        sections.append(ReportSection(section.heading, tuple(paragraphs)))
    return ReportDocument(
        objective=objective,
        summary=ReportParagraph(plan.thesis, tuple(claim.claim_id for claim in claims)),
        sections=tuple(sections),
    )
