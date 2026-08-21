from conflux_weave.evidence import (
    AssessmentVerdict,
    ClaimAssessment,
    EvidenceRelation,
)


def test_contradiction_is_not_an_accepted_support_relation() -> None:
    assessment = ClaimAssessment(
        claim_id="claim-1",
        evidence_ids=("evidence-1",),
        relation=EvidenceRelation.CONTRADICTS,
        verdict=AssessmentVerdict.REJECTED,
        rationale="The source reports the opposite result.",
        evaluator_ref="deterministic-rule-v1",
    )
    assert assessment.relation is not EvidenceRelation.SUPPORTS
    assert assessment.verdict is AssessmentVerdict.REJECTED


def test_insufficient_evidence_remains_uncertain() -> None:
    assessment = ClaimAssessment(
        claim_id="claim-2",
        evidence_ids=(),
        relation=EvidenceRelation.INSUFFICIENT,
        verdict=AssessmentVerdict.UNCERTAIN,
        rationale="No qualifying primary source was available.",
        evaluator_ref="evidence-gate-v1",
    )
    assert assessment.verdict is AssessmentVerdict.UNCERTAIN
