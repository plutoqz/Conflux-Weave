import pytest

from conflux_weave.evidence import (
    AnswerBlock,
    Citation,
    Claim,
    EvidenceRef,
    EvidenceSupportStatus,
    SourceTrustLevel,
    render_evidence_report,
)


def fixture_contracts():
    claim = Claim("claim-1", "已支持事实。", "direct", "primary", "step-1")
    evidence = EvidenceRef(
        "evidence-1",
        "source-1",
        {"type": "text_range", "start_char": 0, "end_char": 6},
        "已支持事实。",
        "fixture-v1",
    )
    citation = Citation("citation-1", "claim-1", "evidence-1", 1)
    return claim, evidence, citation


def test_report_uses_block_marker_and_keeps_citation_out_of_body() -> None:
    claim, evidence, citation = fixture_contracts()

    report = render_evidence_report(
        title="结果",
        intro_lines=("> 查询：fixture",),
        blocks=(
            AnswerBlock(
                "主要结论",
                claim.text,
                EvidenceSupportStatus.CITED,
                (claim.claim_id,),
            ),
            AnswerBlock(
                "背景说明",
                "这是帮助理解的一般背景。",
                EvidenceSupportStatus.UNCITED_CONTEXT,
            ),
        ),
        claims=(claim,),
        evidence=(evidence,),
        citations=(citation,),
        evidence_trust={"evidence-1": SourceTrustLevel.AUTHORITATIVE},
    )

    assert "### ● A 主要结论" in report
    assert "### ○ 背景说明" in report
    assert "已支持事实。 [1]" not in report
    assert report.count("[1]") == 1
    assert "## Evidence 汇总" in report


def test_citation_and_source_trust_are_independent() -> None:
    claim, evidence, citation = fixture_contracts()

    report = render_evidence_report(
        title="结果",
        intro_lines=(),
        blocks=(AnswerBlock("结论", claim.text, EvidenceSupportStatus.CITED, (claim.claim_id,)),),
        claims=(claim,),
        evidence=(evidence,),
        citations=(citation,),
        evidence_trust={"evidence-1": SourceTrustLevel.UNVERIFIED_SOURCE},
    )

    assert "### ● ? 结论" in report
    assert "来源 ?" in report


def test_uncited_context_cannot_register_fact_claims() -> None:
    with pytest.raises(ValueError, match="cannot register fact claims"):
        AnswerBlock(
            "背景",
            "context",
            EvidenceSupportStatus.UNCITED_CONTEXT,
            ("claim-1",),
        )


def test_report_requires_trust_for_every_evidence() -> None:
    claim, evidence, citation = fixture_contracts()
    with pytest.raises(ValueError, match="classify every"):
        render_evidence_report(
            title="结果",
            intro_lines=(),
            blocks=(AnswerBlock("结论", claim.text, EvidenceSupportStatus.CITED, (claim.claim_id,)),),
            claims=(claim,),
            evidence=(evidence,),
            citations=(citation,),
            evidence_trust={},
        )
