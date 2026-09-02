import json
from pathlib import Path

from conflux_weave.engine_narrative import EngineNarrative, EngineSection, parse_engine_narrative
from conflux_weave.evidence import Citation, Claim, EvidenceRef, render_fused_report
from conflux_weave.merge import MergePlan, plan_merge
from conflux_weave.provider import (
    OpenAICompatibleChatAdapter,
    ProviderConfig,
    ProviderHttpResponse,
)
from conflux_weave.report_writer import build_deterministic_fused_document
from conflux_weave.runtime import LocalArtifactStore


class SequenceTransport:
    def __init__(self, payloads):
        self.payloads = iter(payloads)
        self.requests = []

    def post(self, *args, **kwargs):
        self.requests.append(json.loads(kwargs["body"]))
        payload = next(self.payloads)
        return ProviderHttpResponse(200, json.dumps(payload).encode(), {"Content-Type": "application/json"})


def chat_response(content, response_id):
    return {
        "id": response_id,
        "model": "fixture-chat",
        "choices": [{"message": {"content": content if isinstance(content, str) else json.dumps(content)}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
    }


CLAIMS = (
    Claim("claim-0001", "Agents use skills to act on the world.", "finding", "high", "test"),
    Claim("claim-0002", "Memory writes are deduplicated by content hash.", "finding", "high", "test"),
    Claim("claim-0003", "Local corpus bundles skills into reusable instructions.", "finding", "high", "test"),
)
EVIDENCE = (
    EvidenceRef("evidence-0001", "web-0001", {"type": "web_page", "url": "https://a"}, "Skills let agents act. " * 20, "gpt-researcher-aggregation-v1"),
    EvidenceRef("evidence-0002", "web-0002", {"type": "web_page", "url": "https://b"}, "Memory dedup by hash. " * 20, "gpt-researcher-aggregation-v1"),
    EvidenceRef("evidence-0003", "snap-local-1", {"page": 1}, "Local corpus bundles skills. " * 20, "deep-research-local-corpus-chunk-v1"),
)
CITATIONS = tuple(
    Citation(f"citation-{index:04d}", f"claim-{index:04d}", f"evidence-{index:04d}", index)
    for index in (1, 2, 3)
)
ENGINE_MD = """# 融合报告标题

## 封装形态
技能以文件夹为单位封装程序性知识。
### 形态细节
技能包由指令与资源组成。

## 生效条件
技能只有在知识缺口存在且检索命中时才产生收益。
"""
WEB_META = {
    "web-0001": {"title": "Source A", "url": "https://a.example/skill"},
    "web-0002": {"title": "Source B", "url": "https://b.example/memory"},
}
PLAN_OK = {
    "thesis": "技能机制以封装包与按需加载生效。",
    "assignments": [
        {"section_index": 0, "paragraph_index": 0, "web_source_ids": ["web-0001"],
         "claims": [{"claim_id": "claim-0001", "relation": "supports"},
                    {"claim_id": "claim-0003", "relation": "extends"}]},
        {"section_index": 1, "paragraph_index": 0, "web_source_ids": ["web-0002"],
         "claims": [{"claim_id": "claim-0002", "relation": "qualifies"}]},
    ],
    "research_space": [],
    "dropped": [],
}


def build_service(tmp_path, payloads):
    transport = SequenceTransport(payloads)
    config = ProviderConfig("https://provider.example/v1", "secret", "chat")
    adapter = OpenAICompatibleChatAdapter(
        LocalArtifactStore(tmp_path / "artifacts"), config, transport=transport
    )
    store = LocalArtifactStore(tmp_path / "plans")
    return store, adapter, transport


def narrative():
    return parse_engine_narrative(ENGINE_MD)


def test_parse_engine_narrative_structure_and_fallback():
    parsed = narrative()
    assert parsed.title == "融合报告标题"
    assert [section.heading for section in parsed.sections] == ["一、封装形态", "二、生效条件"]
    # H3 子标题折叠为引导段，位置在内容之前
    assert parsed.sections[0].paragraphs[1] == "**形态细节**"
    assert parse_engine_narrative("# 只有标题\n正文没有小节。") is None


def test_parse_engine_narrative_groups_wrapped_lines_until_a_blank_line():
    parsed = parse_engine_narrative(
        "# 标题\n\n## 小节\n同一段的第一行。\n同一段的第二行。\n\n下一自然段。\n"
    )

    assert parsed is not None
    assert parsed.sections[0].paragraphs == ("同一段的第一行。\n同一段的第二行。", "下一自然段。")


def test_parse_engine_narrative_excludes_trailing_reference_section():
    parsed = parse_engine_narrative(
        "# 标题\n\n## 研究发现\n正文。\n\n## 八、参考文献\nSource A. https://a.example\n"
    )

    assert parsed is not None
    assert [section.heading for section in parsed.sections] == ["一、研究发现"]


def test_parse_engine_narrative_renumbers_mixed_headings_and_reference_sources():
    parsed = parse_engine_narrative(
        "# 标题\n\n## 引言\n引言。\n\n## 一、机制\n机制。\n\n"
        "## 十、参考来源\n1. local-document-sha256-deadbeef.txt\n"
    )

    assert parsed is not None
    assert [section.heading for section in parsed.sections] == ["一、引言", "二、机制"]


def test_plan_merge_ok_assigns_all_claims(tmp_path):
    store, chat, transport = build_service(tmp_path, [chat_response(PLAN_OK, "plan")])

    outcome = plan_merge(
        store, chat, "目标", narrative(), CLAIMS, EVIDENCE, CITATIONS,
        web_source_ids=("web-0001", "web-0002"), web_source_meta=WEB_META,
    )

    assert outcome.status == "ok"
    assert outcome.plan.research_space == () and outcome.plan.dropped == ()
    material = json.loads(transport.requests[0]["messages"][1]["content"])
    # 素材含引擎骨架、带标题的 web 来源与带 origin 的证据节选
    assert material["outline"][0]["heading"] == "一、封装形态"
    assert material["web_sources"][0]["title"] == "Source A"
    origins = {entry["origin"] for item in material["claims"] for entry in item["evidence"]}
    assert origins == {"web", "local"}


def test_plan_merge_rejects_incomplete_partition_then_repairs(tmp_path):
    broken = {**PLAN_OK, "assignments": PLAN_OK["assignments"][:1]}  # claim-0002 未分类
    store, chat, transport = build_service(tmp_path, [chat_response(broken, "bad"), chat_response(PLAN_OK, "good")])

    outcome = plan_merge(
        store, chat, "目标", narrative(), CLAIMS, EVIDENCE, CITATIONS,
        web_source_ids=("web-0001", "web-0002"), web_source_meta=WEB_META,
    )

    assert outcome.status == "ok"
    assert "claim-0002" in transport.requests[1]["messages"][0]["content"]


def test_plan_merge_degrades_after_double_rejection(tmp_path):
    store, chat, _ = build_service(tmp_path, [chat_response("not-json", "b1"), chat_response("still", "b2")])

    outcome = plan_merge(
        store, chat, "目标", narrative(), CLAIMS, EVIDENCE, CITATIONS,
        web_source_ids=("web-0001", "web-0002"), web_source_meta=WEB_META,
    )

    assert outcome.status == "degraded" and outcome.plan is None


def test_deterministic_fused_document_embeds_claims_after_engine_paragraphs(tmp_path):
    store, chat, _ = build_service(tmp_path, [])
    plan = plan_merge(
        store, chat, "目标", narrative(), CLAIMS, EVIDENCE, CITATIONS,
        web_source_ids=("web-0001", "web-0002"), web_source_meta=WEB_META,
    )
    # 直接以 OK 计划构造（规划器已被单独覆盖），这里用解析器重放
    from conflux_weave.merge import _parse_plan

    parsed_plan = _parse_plan(json.dumps(PLAN_OK), CLAIMS, narrative(), ("web-0001", "web-0002"))

    document = build_deterministic_fused_document("目标", narrative(), parsed_plan, CLAIMS)

    assert document.summary.text == PLAN_OK["thesis"]
    first = document.sections[0].paragraphs
    # fallback 也应把原引擎段落和匹配 Claim 合为一个段落，避免逐条 Claim 散落。
    assert first[0].web_source_ids == ("web-0001",)
    assert first[0].claim_ids == ("claim-0001", "claim-0003")
    assert CLAIMS[0].text in first[0].text
    assert CLAIMS[2].text in first[0].text
    # 无归属且无 Claim 的引导段按未核验车道呈现。
    assert first[1].unverified is True


def test_fused_renderer_separates_each_report_paragraph_with_markdown_blank_lines(tmp_path):
    from conflux_weave.merge import _parse_plan

    parsed_plan = _parse_plan(json.dumps(PLAN_OK), CLAIMS, narrative(), ("web-0001", "web-0002"))
    document = build_deterministic_fused_document("目标", narrative(), parsed_plan, CLAIMS)
    report = render_fused_report(
        title="融合报告标题",
        thesis=PLAN_OK["thesis"],
        claims=CLAIMS,
        evidence=EVIDENCE,
        citations=CITATIONS,
        document=document,
        research_space_claims=(),
        web_registry=WEB_META,
        local_registry={"snap-local-1": "本地技能综述"},
    )

    first = document.sections[0].paragraphs[0].text
    assert f"{first} [1][3]\n\n" in report
    assert "### 形态细节" in report
    assert "○ " not in report


def test_fused_renderer_normalizes_engine_links_into_the_single_reference_space():
    from conflux_weave.evidence import ReportDocument, ReportParagraph, ReportSection

    document = ReportDocument(
        objective="目标",
        summary=ReportParagraph("总结。", ("claim-0001",)),
        sections=(
            ReportSection(
                "一、发现",
                (
                    ReportParagraph(
                        "引擎事实（[Source B](https://b.example/memory)）。",
                        (),
                        unverified=True,
                    ),
                ),
            ),
        ),
        open_questions=(),
    )
    report = render_fused_report(
        title="融合报告",
        thesis="总结。",
        claims=CLAIMS,
        evidence=EVIDENCE,
        citations=CITATIONS,
        document=document,
        research_space_claims=(),
        web_registry=WEB_META,
        local_registry={"snap-local-1": "本地技能综述"},
    )

    body, references = report.split("## 来源引用", 1)
    assert "https://b.example/memory" not in body
    assert "引擎事实。 [2]" in body
    assert references.count("[2](https://b.example/memory) Source B[web]") == 1


def test_fused_renderer_deduplicates_same_title_web_variants():
    from conflux_weave.evidence import ReportDocument, ReportParagraph, ReportSection

    document = ReportDocument(
        objective="目标",
        summary=ReportParagraph("总结。", (), web_source_ids=("web-a",)),
        sections=(
            ReportSection(
                "一、发现",
                (ReportParagraph("补充。", (), web_source_ids=("web-b",)),),
            ),
        ),
        open_questions=(),
    )
    report = render_fused_report(
        title="融合报告",
        thesis="总结。",
        claims=(),
        evidence=(),
        citations=(),
        document=document,
        research_space_claims=(),
        web_registry={
            "web-a": {"title": "同一文章", "url": "https://example.test/article.html"},
            "web-b": {"title": "同一文章", "url": "https://example.test/article"},
        },
        local_registry={},
    )

    assert report.count("同一文章[web]") == 1
    assert "补充。 [1]" in report


def test_merge_skips_without_narrative_or_claims(tmp_path):
    store, chat, transport = build_service(tmp_path, [])
    outcome = plan_merge(
        store, chat, "目标", parse_engine_narrative("# 无小节"), CLAIMS, EVIDENCE, CITATIONS,
        web_source_ids=("web-0001",), web_source_meta=WEB_META,
    )
    assert outcome.status == "skipped" and transport.requests == []


def test_engine_parser_drops_truncated_tail_fragment():
    narrative = parse_engine_narrative("# 报告\n\n## 第一节\n完整句子。\n\n（[范桂")

    assert narrative is not None
    assert narrative.sections[0].paragraphs == ("完整句子。",)


def test_engine_parser_normalizes_numeric_section_prefix():
    narrative = parse_engine_narrative("# 报告\n\n## 1. 引言\n正文。")

    assert narrative is not None
    assert narrative.sections[0].heading == "一、引言"


def test_deterministic_fallback_keeps_empty_engine_section_deliverable():
    narrative = EngineNarrative(
        "报告",
        (EngineSection("一、空节", ("（[范桂",)),),
    )
    document = build_deterministic_fused_document("目标", narrative, MergePlan("目标", (), (), ()), ())

    assert document.sections[0].paragraphs
    assert document.sections[0].paragraphs[0].unverified is True
    assert document.sections[0].paragraphs[0].text.endswith("实质性结论。")
