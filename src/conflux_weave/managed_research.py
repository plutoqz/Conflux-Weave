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


class ManagedVerifiedResearchWorkflow:
    def __init__(self, store: LocalArtifactStore, worker: VerifiedResearchWorkflow, manager_chat: OpenAICompatibleChatAdapter) -> None:
        self.store, self.worker, self.manager_chat = store, worker, manager_chat
        self.manager_profile = AgentProfile(
            "research_manager",
            "v1",
            "Plan bounded complex research without generating factual conclusions",
            ("managed_verified_research",),
            (),
            BudgetLedger(60, 8000, 1000, "provider-price-not-frozen", 1, 0, 1),
        )

    def execute(self, objective: str, *, max_subquestions: int = 4) -> ManagedResearchExecution:
        if not objective.strip():
            raise ValueError("objective must not be empty")
        if not 2 <= max_subquestions <= 4:
            raise ValueError("max_subquestions must be between 2 and 4")
        completion = self.manager_chat.complete(
            system_prompt=(
                "You are a research Manager. Return JSON with subquestions only. "
                "Create 2 to the supplied maximum distinct evidence-seeking subquestions. "
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
        payload = json.loads(completion.content)
        questions = payload.get("subquestions")
        if not isinstance(questions, list) or not 2 <= len(questions) <= max_subquestions:
            raise ValueError("Manager must return 2 to max_subquestions items")
        normalized = tuple(str(item).strip() for item in questions)
        if any(not item for item in normalized) or len(set(normalized)) != len(normalized):
            raise ValueError("Manager subquestions must be non-empty and unique")
        self._require_scope_preserved(objective, normalized)
        plan = ResearchPlan(
            objective.strip(),
            normalized,
            1,
            1,
            "every subquestion has at least one Verifier-accepted evidence-supported claim",
        )
        plan_ref = self.store.put_json(asdict(plan), producer_step_id="s1-manager-plan", schema_version="conflux-weave.research-plan.v1")
        subruns = tuple(self.worker.execute(question) for question in normalized)
        claims, evidence, citations, blocks = self._aggregate(normalized, subruns)
        require_closed_citations(claims, evidence, citations)
        answered_subquestions = sum(bool(item.claims) for item in subruns)
        if answered_subquestions == len(subruns):
            disposition = DeliveryDisposition.COMPLETE
            unmet_criteria = ()
        elif answered_subquestions == 0:
            disposition = DeliveryDisposition.NO_ANSWER
            unmet_criteria = ()
        else:
            disposition = DeliveryDisposition.PARTIAL
            unmet_criteria = (
                f"{len(subruns) - answered_subquestions} of {len(subruns)} planned subquestions produced no evidence-supported Claim.",
            )
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
            "manager_request_artifact": completion.request_artifact.artifact_id,
            "manager_response_artifact": completion.response_artifact.artifact_id,
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
        return ManagedResearchExecution(plan, subruns, report_ref.artifact_id, manifest_ref.artifact_id, len(claims), len(evidence), len(citations), disposition, limitations, unmet_criteria)

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
