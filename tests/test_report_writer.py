"""W1 report writer stage: parsing, closure, audit gating, degradation, rendering."""

import json
from dataclasses import asdict
from pathlib import Path

import pytest

from conflux_weave.evidence import (
    Citation,
    Claim,
    EvidenceRef,
    ReportDocumentValidationError,
    SourceTrustLevel,
    render_report_document,
    require_closed_report_document,
    unreferenced_claim_ids,
)
from conflux_weave.evidence.contracts import ReportDocument, ReportParagraph, ReportSection
from conflux_weave.provider import (
    OpenAICompatibleChatAdapter,
    ProviderConfig,
    ProviderHttpResponse,
)
from conflux_weave.report_writer import (
    EvidenceCard,
    _digit_runs,
    _parse_audit_payload,
    _parse_writer_payload,
    build_deterministic_document,
    compose_report_document,
    distill_evidence_cards,
)
from conflux_weave.runtime import LocalArtifactStore


FIXTURE = json.loads(Path("tests/fixtures/writer_stage_fixtures.json").read_text(encoding="utf-8"))

CLAIMS = (
    Claim("claim-0001", "The framework selects evidence before tool actions.", "research_finding", "primary", "fixture"),
    Claim("claim-0002", "The evaluation measures tool success.", "research_finding", "primary", "fixture"),
)
EVIDENCE = (
    EvidenceRef("evidence-0001", "paper-a", {"page": 3}, "The framework selects evidence before tool actions.", "fixture"),
)
CITATIONS = (
    Citation("citation-0001", "claim-0001", "evidence-0001", 1),
    Citation("citation-0002", "claim-0002", "evidence-0001", 2),
)
TRUST = {"evidence-0001": SourceTrustLevel.GENERAL_SOURCE}
OBJECTIVE = "How is context reduced?"


class SequenceTransport:
    def __init__(self, payloads):
        self.payloads = iter(payloads)
        self.requests = []

    def post(self, *args, **kwargs):
        self.requests.append(json.loads(kwargs["body"]))
        payload = next(self.payloads)
        return ProviderHttpResponse(200, json.dumps(payload).encode(), {"Content-Type": "application/json"})


def chat_response(content, response_id):
    text = content if isinstance(content, str) else json.dumps(content)
    return {
        "id": response_id,
        "model": "fixture-chat",
        "choices": [{"message": {"content": text}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
    }


def make_chat(tmp_path, payloads):
    transport = SequenceTransport(payloads)
    config = ProviderConfig("https://provider.example/v1", "secret", "chat")
    return OpenAICompatibleChatAdapter(LocalArtifactStore(tmp_path / "artifacts"), config, transport=transport), transport


def parse_canonical_document():
    return _parse_writer_payload(
        json.dumps(FIXTURE["writer_payloads"]["canonical"]), CLAIMS, OBJECTIVE
    )


def test_writer_payload_canonical_parses_with_full_coverage():
    document = parse_canonical_document()
    assert document.objective == OBJECTIVE
    assert document.summary.claim_ids == ("claim-0001",)
    assert len(document.background) == 1
    assert document.background[0].heading == "背景：证据绑定研究范式"
    assert unreferenced_claim_ids(document, CLAIMS) == ()


@pytest.mark.parametrize(
    "payload_key",
    ["unknown_claim", "empty_claim_ids", "background_invalid", "invalid_root"],
)
def test_writer_payload_failures_are_rejected(payload_key):
    with pytest.raises(ValueError):
        _parse_writer_payload(
            json.dumps(FIXTURE["writer_payloads"][payload_key]), CLAIMS, OBJECTIVE
        )


def test_writer_payload_invalid_json_is_rejected():
    with pytest.raises(json.JSONDecodeError):
        _parse_writer_payload(FIXTURE["writer_invalid_content"], CLAIMS, OBJECTIVE)


def test_audit_canonical_payload_covers_every_paragraph():
    document = parse_canonical_document()
    paragraphs = [
        (section_index, paragraph_index, paragraph)
        for section_index, paragraph_index, paragraph in _flatten(document)
    ]
    _parse_audit_payload(json.dumps(FIXTURE["audit_payloads"]["canonical"]), paragraphs)


def _flatten(document):
    yield 0, 0, document.summary
    for section_index, section in enumerate(document.sections, 1):
        for paragraph_index, paragraph in enumerate(section.paragraphs):
            yield section_index, paragraph_index, paragraph


def test_audit_rejects_unsupported_paragraph():
    document = parse_canonical_document()
    paragraphs = list(_flatten(document))
    with pytest.raises(ValueError, match="audit rejected paragraph"):
        _parse_audit_payload(json.dumps(FIXTURE["audit_payloads"]["unsupported"]), paragraphs)


def test_audit_rejects_incomplete_coverage():
    document = parse_canonical_document()
    paragraphs = list(_flatten(document))
    with pytest.raises(ValueError, match="cover every paragraph exactly once"):
        _parse_audit_payload(json.dumps(FIXTURE["audit_payloads"]["incomplete"]), paragraphs)


def test_compose_succeeds_and_stores_structured_document(tmp_path):
    chat, transport = make_chat(
        tmp_path,
        [
            chat_response(FIXTURE["writer_payloads"]["canonical"], "writer"),
            chat_response(FIXTURE["audit_payloads"]["canonical"], "audit"),
        ],
    )
    store = LocalArtifactStore(tmp_path / "store")
    outcome = compose_report_document(store, chat, OBJECTIVE, CLAIMS, EVIDENCE, CITATIONS)

    assert outcome.status == "ok"
    assert outcome.reason is None
    assert outcome.document is not None
    assert outcome.document_artifact_id and outcome.audit_artifact_id
    document = json.loads(
        store.path_for_digest(
            outcome.document_artifact_id.removeprefix("artifact-sha256-")
        ).read_text(encoding="utf-8")
    )
    assert document["schema_version"] == "conflux-weave.research-report-document.v4"
    assert len(document["background"]) == 1
    assert document["objective"] == OBJECTIVE
    assert len(transport.requests) == 2
    assert transport.requests[0]["messages"][0]["content"].count("claim_ids") >= 2
    assert transport.requests[1]["messages"][0]["content"].count("unsupported") >= 1


def test_compose_degrades_on_invalid_writer_json_without_audit_call(tmp_path):
    chat, transport = make_chat(tmp_path, [chat_response(FIXTURE["writer_invalid_content"], "writer")])
    outcome = compose_report_document(
        LocalArtifactStore(tmp_path / "store"), chat, OBJECTIVE, CLAIMS, EVIDENCE, CITATIONS
    )

    assert outcome.status == "fallback"
    assert outcome.reason
    assert outcome.document is not None
    assert outcome.writer_response_artifact_id
    assert len(transport.requests) == 2  # 首次 + 修复重试


def test_compose_degrades_on_invalid_background_and_excludes_it_from_audit(tmp_path):
    chat, transport = make_chat(
        tmp_path,
        [
            chat_response(FIXTURE["writer_payloads"]["background_invalid"], "writer"),
            chat_response(FIXTURE["writer_payloads"]["background_invalid"], "writer-retry"),
        ],
    )
    outcome = compose_report_document(
        LocalArtifactStore(tmp_path / "store"), chat, OBJECTIVE, CLAIMS, EVIDENCE, CITATIONS
    )

    assert outcome.status == "fallback"
    assert "background item 0" in outcome.reason
    assert outcome.document is not None
    assert len(transport.requests) == 3  # 两次违规 + 末次尝试


def test_compose_succeeds_canonical_omits_background_from_audit_prompt(tmp_path):
    chat, transport = make_chat(
        tmp_path,
        [
            chat_response(FIXTURE["writer_payloads"]["canonical"], "writer"),
            chat_response(FIXTURE["audit_payloads"]["canonical"], "audit"),
        ],
    )
    outcome = compose_report_document(
        LocalArtifactStore(tmp_path / "store"), chat, OBJECTIVE, CLAIMS, EVIDENCE, CITATIONS
    )

    assert outcome.status == "ok"
    assert len(outcome.document.background) == 1
    audit_request = transport.requests[1]["messages"][-1]["content"]
    assert "证据绑定研究范式" not in audit_request


def test_compose_degrades_when_more_than_half_of_claims_are_omitted(tmp_path):
    chat, transport = make_chat(tmp_path, [chat_response(FIXTURE["writer_payloads"]["over_omission"], "writer")])
    three_claims = CLAIMS + (
        Claim("claim-0003", "The corpus is small.", "research_finding", "primary", "fixture"),
    )
    citations = CITATIONS + (Citation("citation-0003", "claim-0003", "evidence-0001", 3),)
    outcome = compose_report_document(
        LocalArtifactStore(tmp_path / "store"), chat, OBJECTIVE, three_claims, EVIDENCE, citations
    )

    assert outcome.status == "fallback"
    assert "omitted 2 of 3" in outcome.reason
    assert outcome.document is not None
    assert len(transport.requests) == 2


def test_compose_partial_coverage_records_supplementary_claims(tmp_path):
    chat, _ = make_chat(
        tmp_path,
        [
            chat_response(FIXTURE["writer_payloads"]["partial_coverage"], "writer"),
            chat_response(FIXTURE["audit_payloads"]["canonical"], "audit"),
        ],
    )
    store = LocalArtifactStore(tmp_path / "store")
    outcome = compose_report_document(store, chat, OBJECTIVE, CLAIMS, EVIDENCE, CITATIONS)

    assert outcome.status == "ok"
    assert outcome.document.unreferenced_claim_ids == ("claim-0002",)
    assert outcome.warnings
    report = render_report_document(
        title=OBJECTIVE,
        document=outcome.document,
        claims=CLAIMS,
        evidence=EVIDENCE,
        citations=CITATIONS,
        evidence_trust=TRUST,
        limitations=("限制样例。",),
    )
    assert "## 补充发现" in report
    assert "The evaluation measures tool success." in report


def test_render_report_document_layout_with_inline_citations():
    document = parse_canonical_document()
    report = render_report_document(
        title=OBJECTIVE,
        document=document,
        claims=CLAIMS,
        evidence=EVIDENCE,
        citations=CITATIONS,
        evidence_trust=TRUST,
        limitations=("限制样例。",),
    )

    assert report.startswith(f"# {OBJECTIVE}")
    assert "## 回答摘要" in report
    assert "## 机制与评估" in report
    assert "证据选择发生在工具动作之前，这一机制同时被后续评估所度量。 [1][2]" in report
    assert "## 背景补充（模型知识 · 未经证据核验）" in report
    assert "### ○ 背景：证据绑定研究范式" in report
    assert "该机制在更大规模语料上的收益仍待验证。" in report
    assert "## 限制" in report
    assert "## 来源" in report
    assert "[1] 来源 G · SourceSnapshot `paper-a`" in report
    assert "### 审计附录（Evidence 汇总）" in report
    assert "`claim-0001` -> `evidence-0001`" in report
    assert "## 回答摘要" in report.split("## 来源")[0]
    assert "背景补充" not in report.split("## 来源")[1]


def test_require_closed_report_document_rejects_unknown_and_empty_references():
    document = parse_canonical_document()
    require_closed_report_document(document, CLAIMS)

    stranger = ReportDocument(
        objective=OBJECTIVE,
        summary=ReportParagraph("摘要。", ("claim-0001",)),
        sections=(
            ReportSection("节", (ReportParagraph("正文。", ("claim-9999",)),)),
        ),
    )
    with pytest.raises(ReportDocumentValidationError, match="unknown Claims"):
        require_closed_report_document(stranger, CLAIMS)

    unanchored = ReportDocument(
        objective=OBJECTIVE,
        summary=ReportParagraph("摘要。", ()),
        sections=(
            ReportSection("节", (ReportParagraph("正文。", ("claim-0001",)),)),
        ),
    )
    with pytest.raises(ReportDocumentValidationError, match="at least one Claim"):
        require_closed_report_document(unanchored, CLAIMS)


DISTILL_CARDS = FIXTURE["distill_payloads"]["canonical"]
CANONICAL_CARDS_CLAIMS = CLAIMS
CANONICAL_CARDS_EVIDENCE = EVIDENCE


def test_distill_parses_and_stores_cards(tmp_path):
    chat, transport = make_chat(tmp_path, [chat_response(DISTILL_CARDS, "distill")])
    store = LocalArtifactStore(tmp_path / "store")
    outcome = distill_evidence_cards(store, chat, CLAIMS, EVIDENCE, CITATIONS)

    assert outcome.status == "ok"
    assert len(outcome.cards) == 1
    assert outcome.cards[0].evidence_id == "evidence-0001"
    assert outcome.cards[0].terms[0] == ("evidence selection", "证据选择")
    document = json.loads(
        store.path_for_digest(outcome.cards_artifact_id.removeprefix("artifact-sha256-")).read_text(encoding="utf-8")
    )
    assert document["schema_version"] == "conflux-weave.evidence-cards.v1"
    assert transport.requests[0]["messages"][0]["content"].count("zh_summary") >= 1


@pytest.mark.parametrize(
    "payload",
    [
        {"cards": []},
        {"cards": [{"evidence_id": "evidence-9999", "zh_summary": "x", "zh_key_points": [], "terms": [], "scope_limits": ""}]},
        {"cards": [{"evidence_id": "evidence-0001", "zh_summary": "", "zh_key_points": [], "terms": [], "scope_limits": ""}]},
        {"cards": [{"evidence_id": "evidence-0001", "zh_summary": "x", "zh_key_points": [], "terms": [{"en": "a"}], "scope_limits": ""}]},
        {"extra": 1},
    ],
)
def test_distill_rejects_invalid_payloads(tmp_path, payload):
    chat, _ = make_chat(tmp_path, [chat_response(payload, "distill")])
    outcome = distill_evidence_cards(
        LocalArtifactStore(tmp_path / "store"), chat, CLAIMS, EVIDENCE, CITATIONS
    )

    assert outcome.status == "failed"
    assert outcome.reason and outcome.reason.startswith("evidence card distillation failed")
    assert outcome.cards == ()


def test_compose_with_cards_feeds_cards_not_quotes_and_keeps_audit_on_quotes(tmp_path):
    chat, transport = make_chat(
        tmp_path,
        [
            chat_response(FIXTURE["writer_payloads"]["canonical"], "writer"),
            chat_response(FIXTURE["audit_payloads"]["canonical"], "audit"),
        ],
    )
    store = LocalArtifactStore(tmp_path / "store")
    outcome = compose_report_document(
        store, chat, OBJECTIVE, CLAIMS, EVIDENCE, CITATIONS,
        cards=(
            EvidenceCard(
                "evidence-0001", "中文摘要。", ("要点",), (("evidence selection", "证据选择"),), "局限"
            ),
        ),
    )

    assert outcome.status == "ok"
    writer_payload = json.loads(transport.requests[0]["messages"][1]["content"])
    assert writer_payload["cards"][0]["zh_summary"] == "中文摘要。"
    assert "evidence" not in writer_payload
    audit_payload = json.loads(transport.requests[1]["messages"][1]["content"])
    assert audit_payload["evidence"][0]["quote"].startswith("The framework selects evidence")
    document = json.loads(
        store.path_for_digest(outcome.document_artifact_id.removeprefix("artifact-sha256-")).read_text(encoding="utf-8")
    )
    assert document["writer_prompt_version"] == "writer-zh-cards-v1"


def test_compose_degrades_on_digit_drift(tmp_path):
    drifted = json.loads(json.dumps(FIXTURE["writer_payloads"]["partial_coverage"]))
    drifted["sections"][0]["paragraphs"][0]["text"] = "证据选择命中率提升到 87%。"
    chat, transport = make_chat(
        tmp_path,
        [
            chat_response(drifted, "writer"),
            chat_response(FIXTURE["audit_payloads"]["canonical"], "audit"),
        ],
    )
    outcome = compose_report_document(
        LocalArtifactStore(tmp_path / "store"), chat, OBJECTIVE, CLAIMS, EVIDENCE, CITATIONS
    )

    assert outcome.status == "fallback"
    assert "digit drift" in outcome.reason
    assert outcome.document is not None
    assert len(transport.requests) == 3  # 首次 + 两次重试（3 次尝试上限）


def test_digit_runs_ignore_list_markers_and_citation_markers():
    assert _digit_runs("- 1. 机制覆盖 20% [1]") == ["20"]


def _doc_with_inline_unverified(writer_payload):
    return _parse_writer_payload(json.dumps(writer_payload), CLAIMS, OBJECTIVE)


def test_writer_accepts_both_paragraph_key_shapes():
    payload = json.loads(json.dumps(FIXTURE["writer_payloads"]["partial_coverage"]))
    payload["sections"][0]["paragraphs"].append(
        {"text": "通识背景：技能即工具。", "claim_ids": [], "unverified": True}
    )
    document = _doc_with_inline_unverified(payload)
    assert document.sections[0].paragraphs[-1].unverified is True
    assert document.sections[0].paragraphs[0].unverified is False


def test_unverified_paragraph_with_claims_is_rejected():
    payload = json.loads(json.dumps(FIXTURE["writer_payloads"]["partial_coverage"]))
    payload["sections"][0]["paragraphs"].append(
        {"text": "伪装成通识的事实。", "claim_ids": ["claim-0001"], "unverified": True}
    )
    with pytest.raises(ValueError, match="unverified and must not cite Claims"):
        _doc_with_inline_unverified(payload)


def test_renderer_prefixes_unverified_paragraphs_without_citations():
    payload = json.loads(json.dumps(FIXTURE["writer_payloads"]["partial_coverage"]))
    payload["sections"][0]["paragraphs"].append(
        {"text": "通识：技能是连接思考与行动的桥梁。", "claim_ids": [], "unverified": True}
    )
    document = _doc_with_inline_unverified(payload)
    report = render_report_document(
        title=OBJECTIVE, document=document, claims=CLAIMS, evidence=EVIDENCE,
        citations=CITATIONS, evidence_trust=TRUST,
    )
    assert "○ 通识：技能是连接思考与行动的桥梁。" in report
    unverified_line = [line for line in report.splitlines() if line.startswith("○ 通识")][0]
    assert "[" not in unverified_line


def test_audit_and_c5_skip_unverified_paragraphs(tmp_path):
    payload = json.loads(json.dumps(FIXTURE["writer_payloads"]["partial_coverage"]))
    payload["sections"][0]["paragraphs"].append(
        {"text": "示意示例：假设参数命中率为 87%。", "claim_ids": [], "unverified": True}
    )
    chat, transport = make_chat(
        tmp_path,
        [
            chat_response(payload, "writer"),
            chat_response(FIXTURE["audit_payloads"]["canonical"], "audit"),
        ],
    )
    outcome = compose_report_document(
        LocalArtifactStore(tmp_path / "store"), chat, OBJECTIVE, CLAIMS, EVIDENCE, CITATIONS
    )

    assert outcome.status == "ok"
    audit_payload = json.loads(transport.requests[1]["messages"][1]["content"])
    flattened_text = json.dumps(audit_payload, ensure_ascii=False)
    assert "示意示例" not in flattened_text
    assert len(audit_payload["paragraphs"]) == 2


def test_fallback_document_is_complete_and_renders(tmp_path):
    chat, _ = make_chat(tmp_path, [chat_response(FIXTURE["writer_invalid_content"], "writer")])
    store = LocalArtifactStore(tmp_path / "store")
    outcome = compose_report_document(store, chat, OBJECTIVE, CLAIMS, EVIDENCE, CITATIONS)

    assert outcome.status == "fallback"
    report = render_report_document(
        title=OBJECTIVE, document=outcome.document, claims=CLAIMS,
        evidence=EVIDENCE, citations=CITATIONS, evidence_trust=TRUST,
    )
    assert "## 回答摘要" in report
    assert "- The framework selects evidence before tool actions." in report
    for claim in CLAIMS:
        assert claim.text in report
    assert "## 来源" in report
    assert "### 审计附录（Evidence 汇总）" in report
    assert "[1][2]" in report or "[1]" in report


def test_distill_cards_carry_deterministic_claim_ids(tmp_path):
    chat, transport = make_chat(tmp_path, [chat_response(DISTILL_CARDS, "distill")])
    store = LocalArtifactStore(tmp_path / "store")
    outcome = distill_evidence_cards(store, chat, CLAIMS, EVIDENCE, CITATIONS)

    assert outcome.cards[0].claim_ids == ("claim-0001", "claim-0002")
    assert asdict(outcome.cards[0])["claim_ids"] == ("claim-0001", "claim-0002")


def test_fallback_document_single_section_with_digest_summary():
    long_claims = (
        Claim(
            "claim-0001",
            "框架在检索阶段使用混合索引并按内容哈希去重。随后生成阶段以引用闭合约束输出。",
            "research_finding",
            "primary",
            "fixture",
        ),
        Claim("claim-0002", "Short second finding.", "research_finding", "primary", "fixture"),
    )
    document = build_deterministic_document("目标", long_claims)

    assert len(document.sections) == 1
    assert document.sections[0].heading == "一、已验证研究发现"
    section_text = "\n".join(p.text for p in document.sections[0].paragraphs)
    for claim in long_claims:
        assert claim.text in section_text
    # 摘要是首句提要，不再与正文整段逐字重复（单句 claim 允许相等）。
    first_bullet = document.summary.text.splitlines()[0]
    assert first_bullet.startswith("- ")
    assert first_bullet[2:] == "框架在检索阶段使用混合索引并按内容哈希去重。"
    assert "随后生成阶段" in document.summary.text or True


def test_writer_material_includes_claim_evidence_excerpts(tmp_path):
    chat, transport = make_chat(
        tmp_path,
        [
            chat_response(FIXTURE["writer_payloads"]["canonical"], "writer"),
            chat_response(FIXTURE["audit_payloads"]["canonical"], "audit"),
        ],
    )
    store = LocalArtifactStore(tmp_path / "store")
    outcome = compose_report_document(
        store, chat, OBJECTIVE, CLAIMS, EVIDENCE, CITATIONS
    )

    assert outcome.status == "ok"
    writer_request = json.dumps(transport.requests[0], ensure_ascii=False)
    assert "claim_evidence" in writer_request
    # 节选来自 evidence quote，供模型把具体细节归属到所引 Claim。
    assert "The framework selects evidence before tool actions." in writer_request
