"""Manager-planned complex research over verified subquestion executions."""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass

from conflux_weave.core import BudgetLedger, DeliveryDisposition
from conflux_weave.evidence import (
    AnswerBlock,
    Citation,
    Claim,
    EvidenceRef,
    EvidenceSupportStatus,
    SourceTrustLevel,
    render_evidence_report,
    require_closed_citations,
)
from conflux_weave.harness import AgentProfile
from conflux_weave.provider import OpenAICompatibleChatAdapter
from conflux_weave.research_agents import ResearchExecution, ResearchPlan, VerifiedResearchWorkflow
from conflux_weave.runtime import LocalArtifactStore


@dataclass(frozen=True, slots=True)
class ManagedResearchExecution:
    plan: ResearchPlan
    subruns: tuple[ResearchExecution, ...]
    report_artifact_id: str
    manifest_artifact_id: str
    claim_count: int
    evidence_count: int
    citation_count: int
    disposition: DeliveryDisposition = DeliveryDisposition.COMPLETE
    limitations: tuple[str, ...] = ()
    unmet_criteria: tuple[str, ...] = ()
    coverage_assessments: tuple["CoverageAssessment", ...] = ()


@dataclass(frozen=True, slots=True)
class CoverageRequirement:
    coverage_id: str
    objective_quote: str


@dataclass(frozen=True, slots=True)
class PlannedSubquestion:
    question: str
    coverage_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CoverageAssessment:
    coverage_id: str
    status: str
    claim_ids: tuple[str, ...]
    rationale: str


class ManagedVerifiedResearchWorkflow:
    def __init__(self, store: LocalArtifactStore, worker: VerifiedResearchWorkflow, manager_chat: OpenAICompatibleChatAdapter) -> None:
        self.store, self.worker, self.manager_chat = store, worker, manager_chat
        self.manager_profile = AgentProfile(
            "research_manager",
            "v2",
            "Plan and audit bounded objective coverage without generating factual conclusions",
            ("managed_verified_research",),
            (),
            BudgetLedger(120, 16000, 2400, "provider-price-not-frozen", 2, 0, 1),
        )

    def execute(self, objective: str, *, max_subquestions: int = 4) -> ManagedResearchExecution:
        if not objective.strip():
            raise ValueError("objective must not be empty")
        if not 2 <= max_subquestions <= 4:
            raise ValueError("max_subquestions must be between 2 and 4")
        plan_completion = self.manager_chat.complete(
            system_prompt=(
                "You are a research Manager. Return JSON with coverage_requirements and subquestions only. "
                "Each coverage requirement must quote an exact, non-empty span from the objective. "
                "Create 2 to the supplied maximum distinct evidence-seeking subquestions and map every "
                "coverage_id to at least one subquestion. "
                "Do not answer the question, state factual conclusions, or introduce dates, "
                "source requirements, minimum counts, or other constraints absent from the objective."
            ),
            user_prompt=json.dumps({"objective": objective, "max_subquestions": max_subquestions}, ensure_ascii=False),
            max_output_tokens=800,
            temperature=0,
            json_object=True,
            enable_thinking=False,
            producer_step_id="s1-manager-plan",
        )
        requirements, subquestions = self._parse_plan(
            objective, plan_completion.content, max_subquestions
        )
        normalized = tuple(item.question for item in subquestions)
        self._require_scope_preserved(objective, normalized)
        plan = ResearchPlan(
            objective.strip(),
            normalized,
            1,
            1,
            "every subquestion has at least one Verifier-accepted evidence-supported claim",
        )
        plan_ref = self.store.put_json(
            {
                **asdict(plan),
                "coverage_requirements": [asdict(item) for item in requirements],
                "subquestion_assignments": [asdict(item) for item in subquestions],
            },
            producer_step_id="s1-manager-plan",
            schema_version="conflux-weave.research-plan.v2",
        )
        requirement_by_id = {item.coverage_id: item for item in requirements}
        subruns = tuple(
            self.worker.execute(
                self._worker_objective(objective, item, requirement_by_id)
            )
            for item in subquestions
        )
        claims, evidence, citations, blocks = self._aggregate(normalized, subruns)
        require_closed_citations(claims, evidence, citations)
        answered_subquestions = sum(bool(item.claims) for item in subruns)
        coverage_completion = None
        coverage_assessment_ref = None
        if claims:
            (
                coverage_completion,
                coverage_assessments,
                coverage_assessment_ref,
            ) = self._assess_coverage(objective, requirements, claims)
        else:
            coverage_assessments = tuple(
                CoverageAssessment(
                    item.coverage_id,
                    "missing",
                    (),
                    "No Verifier-accepted Claim was available for coverage assessment.",
                )
                for item in requirements
            )
        missing_requirements = tuple(
            requirement_by_id[item.coverage_id].objective_quote
            for item in coverage_assessments
            if item.status == "missing"
        )
        if answered_subquestions == len(subruns) and not missing_requirements:
            disposition = DeliveryDisposition.COMPLETE
            unmet_criteria = ()
        elif answered_subquestions == 0:
            disposition = DeliveryDisposition.NO_ANSWER
            unmet_criteria = ()
        else:
            disposition = DeliveryDisposition.PARTIAL
            unmet = []
            if answered_subquestions != len(subruns):
                unmet.append(
                    f"{len(subruns) - answered_subquestions} of {len(subruns)} planned subquestions produced no evidence-supported Claim."
                )
            unmet.extend(
                f"Objective coverage was not demonstrated for: {item}"
                for item in missing_requirements
            )
            unmet_criteria = tuple(unmet)
        limitations = (
            "Each subquestion is independently retrieved and verified; aggregation adds no new factual claims.",
            "This delivery does not itself establish a Manager quality benefit over the single-Agent baseline.",
        )
        if disposition is DeliveryDisposition.NO_ANSWER:
            limitations += (
                "No planned subquestion produced an evidence-supported Claim in the configured corpus.",
            )
        report = render_evidence_report(
            title="Managed verified paper research",
            intro_lines=(f"> Objective: {objective}", f"> Manager Plan: `{plan_ref.artifact_id}`"),
            blocks=blocks,
            claims=claims,
            evidence=evidence,
            citations=citations,
            evidence_trust={item.evidence_id: SourceTrustLevel.GENERAL_SOURCE for item in evidence},
            limitations=limitations,
        )
        report_ref = self.store.put_bytes(report.encode("utf-8"), media_type="text/markdown; charset=utf-8", producer_step_id="s1-manager-deliver", schema_version="conflux-weave.managed-research-report.v1")
        manifest = {
            "schema_version": "conflux-weave.managed-research-manifest.v1",
            "objective": objective,
            "disposition": disposition.value,
            "manager_profile": asdict(self.manager_profile),
            "manager_plan_artifact": plan_ref.artifact_id,
            "manager_request_artifact": plan_completion.request_artifact.artifact_id,
            "manager_response_artifact": plan_completion.response_artifact.artifact_id,
            "coverage_requirements": [asdict(item) for item in requirements],
            "coverage_assessments": [asdict(item) for item in coverage_assessments],
            "coverage_request_artifact": (
                coverage_completion.request_artifact.artifact_id
                if coverage_completion is not None
                else None
            ),
            "coverage_response_artifact": (
                coverage_completion.response_artifact.artifact_id
                if coverage_completion is not None
                else None
            ),
            "coverage_assessment_artifact": (
                coverage_assessment_ref.artifact_id
                if coverage_assessment_ref is not None
                else None
            ),
            "subrun_manifest_artifacts": [item.manifest_artifact_id for item in subruns],
            "subrun_report_artifacts": [item.report_artifact_id for item in subruns],
            "report_artifact": report_ref.artifact_id,
            "claim_count": len(claims),
            "evidence_count": len(evidence),
            "citation_count": len(citations),
            "citation_closure": 1.0,
            "stop_reason": (
                "all_subquestions_verified"
                if disposition is DeliveryDisposition.COMPLETE
                else "no_supported_claim"
                if disposition is DeliveryDisposition.NO_ANSWER
                else "partial_subquestion_coverage"
            ),
            "limitations": list(limitations),
            "unmet_criteria": list(unmet_criteria),
        }
        manifest_ref = self.store.put_json(manifest, producer_step_id="s1-manager-deliver", schema_version=manifest["schema_version"])
        return ManagedResearchExecution(plan, subruns, report_ref.artifact_id, manifest_ref.artifact_id, len(claims), len(evidence), len(citations), disposition, limitations, unmet_criteria, coverage_assessments)

    @classmethod
    def _parse_plan(cls, objective: str, content: str, max_subquestions: int):
        payload = json.loads(content)
        if not isinstance(payload, dict) or set(payload) != {
            "coverage_requirements",
            "subquestions",
        }:
            raise ValueError(
                "Manager plan must contain only coverage_requirements and subquestions"
            )
        raw_requirements = payload["coverage_requirements"]
        if not isinstance(raw_requirements, list) or not 1 <= len(raw_requirements) <= 8:
            raise ValueError("Manager must return between 1 and 8 coverage requirements")
        requirements = []
        for item in raw_requirements:
            if not isinstance(item, dict) or set(item) != {
                "coverage_id",
                "objective_quote",
            }:
                raise ValueError("each coverage requirement has an invalid schema")
            coverage_id = str(item["coverage_id"]).strip()
            objective_quote = str(item["objective_quote"]).strip()
            if not coverage_id or not objective_quote:
                raise ValueError("coverage requirements must not be blank")
            if cls._normalize_text(objective_quote) not in cls._normalize_text(objective):
                raise ValueError("coverage requirement must quote the original objective")
            requirements.append(CoverageRequirement(coverage_id, objective_quote))
        known = {item.coverage_id for item in requirements}
        if len(known) != len(requirements):
            raise ValueError("coverage requirement ids must be unique")

        raw_subquestions = payload["subquestions"]
        if not isinstance(raw_subquestions, list) or not 2 <= len(raw_subquestions) <= max_subquestions:
            raise ValueError("Manager must return 2 to max_subquestions items")
        subquestions = []
        assigned = set()
        for item in raw_subquestions:
            if not isinstance(item, dict) or set(item) != {"question", "coverage_ids"}:
                raise ValueError("each Manager subquestion has an invalid schema")
            question = str(item["question"]).strip()
            coverage_ids = item["coverage_ids"]
            if not question or not isinstance(coverage_ids, list) or not coverage_ids:
                raise ValueError("Manager subquestions require coverage assignments")
            normalized_ids = tuple(str(value).strip() for value in coverage_ids)
            if any(not value for value in normalized_ids) or len(set(normalized_ids)) != len(normalized_ids):
                raise ValueError("subquestion coverage ids must be non-empty and unique")
            if not set(normalized_ids) <= known:
                raise ValueError("subquestion references an unknown coverage id")
            assigned.update(normalized_ids)
            subquestions.append(PlannedSubquestion(question, normalized_ids))
        if len({item.question for item in subquestions}) != len(subquestions):
            raise ValueError("Manager subquestions must be non-empty and unique")
        if assigned != known:
            raise ValueError("every coverage requirement must be assigned to a subquestion")
        return tuple(requirements), tuple(subquestions)

    def _assess_coverage(self, objective, requirements, claims):
        completion = self.manager_chat.complete(
            system_prompt=(
                "Audit objective coverage without adding factual conclusions. Return JSON with "
                "assessments only. For every coverage_id, use status covered only when one or more "
                "supplied verified claim_ids directly address the quoted objective requirement; "
                "otherwise use missing and an empty claim_ids list."
            ),
            user_prompt=json.dumps(
                {
                    "objective": objective,
                    "coverage_requirements": [asdict(item) for item in requirements],
                    "verified_claims": [
                        {"claim_id": item.claim_id, "text": item.text} for item in claims
                    ],
                },
                ensure_ascii=False,
            ),
            max_output_tokens=1600,
            temperature=0,
            json_object=True,
            enable_thinking=False,
            producer_step_id="s1-manager-coverage",
        )
        payload = json.loads(completion.content)
        if not isinstance(payload, dict) or set(payload) != {"assessments"}:
            raise ValueError("coverage audit must contain only assessments")
        raw = payload["assessments"]
        if not isinstance(raw, list):
            raise ValueError("coverage assessments must be a list")
        known_coverage = {item.coverage_id for item in requirements}
        known_claims = {item.claim_id for item in claims}
        assessments = []
        for item in raw:
            if not isinstance(item, dict) or set(item) != {
                "coverage_id",
                "status",
                "claim_ids",
                "rationale",
            }:
                raise ValueError("coverage assessment has an invalid schema")
            coverage_id = str(item["coverage_id"])
            status = str(item["status"])
            claim_ids = tuple(str(value) for value in item["claim_ids"])
            rationale = str(item["rationale"]).strip()
            if coverage_id not in known_coverage or status not in {"covered", "missing"}:
                raise ValueError("coverage assessment references an unknown value")
            if not rationale or any(value not in known_claims for value in claim_ids):
                raise ValueError("coverage assessment references an unknown Claim")
            if (status == "covered") != bool(claim_ids):
                raise ValueError("covered requirements need Claim ids; missing requirements do not")
            assessments.append(CoverageAssessment(coverage_id, status, claim_ids, rationale))
        if {item.coverage_id for item in assessments} != known_coverage or len(assessments) != len(known_coverage):
            raise ValueError("coverage audit must assess every requirement exactly once")
        assessment_ref = self.store.put_json(
            {"assessments": [asdict(item) for item in assessments]},
            producer_step_id="s1-manager-coverage",
            schema_version="conflux-weave.objective-coverage-assessments.v1",
        )
        return completion, tuple(assessments), assessment_ref

    @staticmethod
    def _worker_objective(objective, subquestion, requirement_by_id):
        obligations = [
            requirement_by_id[item].objective_quote for item in subquestion.coverage_ids
        ]
        return (
            f"Subquestion: {subquestion.question}\n"
            f"Original objective: {objective}\n"
            "Assigned coverage obligations: " + " | ".join(obligations)
        )

    @staticmethod
    def _normalize_text(value: str) -> str:
        return " ".join(value.casefold().split())

    @staticmethod
    def _require_scope_preserved(objective: str, questions: tuple[str, ...]) -> None:
        objective_years = set(re.findall(r"\b(?:19|20)\d{2}\b", objective))
        introduced_years = set(re.findall(r"\b(?:19|20)\d{2}\b", " ".join(questions))) - objective_years
        if introduced_years:
            raise ValueError(
                "Manager introduced an unauthorized time constraint: "
                + ", ".join(sorted(introduced_years))
            )

    @staticmethod
    def _aggregate(subquestions, subruns):
        claims = []
        evidence = []
        citations = []
        blocks = []
        display_index = 1
        for sub_index, (question, run) in enumerate(zip(subquestions, subruns), 1):
            claim_map = {item.claim_id: f"sq{sub_index}-{item.claim_id}" for item in run.claims}
            evidence_map = {item.evidence_id: f"sq{sub_index}-{item.evidence_id}" for item in run.evidence}
            remapped_claims = tuple(Claim(claim_map[item.claim_id], item.text, item.claim_type, item.importance, item.generated_by_step) for item in run.claims)
            claims.extend(remapped_claims)
            evidence.extend(EvidenceRef(evidence_map[item.evidence_id], item.source_snapshot_id, item.locator, item.quote, item.extraction_method) for item in run.evidence)
            for item in run.citations:
                citations.append(Citation(f"managed-citation-{display_index:04d}", claim_map[item.claim_id], evidence_map[item.evidence_id], display_index))
                display_index += 1
            if remapped_claims:
                blocks.append(AnswerBlock(f"Subquestion {sub_index}: {question}", "\n\n".join(item.text for item in remapped_claims), EvidenceSupportStatus.CITED, tuple(item.claim_id for item in remapped_claims)))
            else:
                blocks.append(AnswerBlock(f"Subquestion {sub_index}: {question}", "No evidence-supported answer was found for this subquestion.", EvidenceSupportStatus.UNSUPPORTED_CLAIM))
        return tuple(claims), tuple(evidence), tuple(citations), tuple(blocks)
