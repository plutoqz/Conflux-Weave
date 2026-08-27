"""Bounded real ResearchAgent -> Verifier -> one repair workflow for S1.4."""
from __future__ import annotations

import json
import hashlib
from dataclasses import asdict, dataclass
from datetime import UTC, datetime

from conflux_weave.core import BudgetLedger, DeliveryDisposition
from conflux_weave.evidence import (
    AnswerBlock, AssessmentVerdict, Citation, Claim, ClaimAssessment,
    EvidenceRef, EvidenceRelation, EvidenceSupportStatus, SourceTrustLevel,
    render_evidence_report, require_closed_citations,
)
from conflux_weave.harness import (
    AgentProfile, AgentResult, AgentResultStatus, AgentTask, ContextBundle,
    MessageEnvelope, MessageType, ToolResult, ToolResultStatus, contract_to_dict,
)
from conflux_weave.hybrid_retrieval import HybridRetrievalPipeline, HybridRetrievalRun
from conflux_weave.provider import OpenAICompatibleChatAdapter
from conflux_weave.runtime import LocalArtifactStore

RESEARCH_TOOL_ID = "hybrid_paper_retrieval"


@dataclass(frozen=True, slots=True)
class ResearchPlan:
    objective: str
    subquestions: tuple[str, ...]
    max_retrieval_rounds: int
    max_repair_rounds: int
    stop_condition: str


@dataclass(frozen=True, slots=True)
class CoverageReport:
    evidence_count: int
    candidate_claim_count: int
    accepted_claim_count: int
    rejected_claim_count: int
    repair_rounds: int
    stop_reason: str


@dataclass(frozen=True, slots=True)
class ResearchExecution:
    report_artifact_id: str
    manifest_artifact_id: str
    claims: tuple[Claim, ...]
    evidence: tuple[EvidenceRef, ...]
    citations: tuple[Citation, ...]
    assessments: tuple[ClaimAssessment, ...]
    coverage: CoverageReport
    disposition: DeliveryDisposition = DeliveryDisposition.COMPLETE
    limitations: tuple[str, ...] = ()
    unmet_criteria: tuple[str, ...] = ()


def _parse_verifier_assessments(
    content: str,
    claims: tuple[Claim, ...],
    evidence: tuple[EvidenceRef, ...],
    response_artifact_id: str,
    store: LocalArtifactStore,
    producer_step_id: str,
):
    payload = json.loads(content)
    if not isinstance(payload, dict) or set(payload) != {"assessments"}:
        raise ValueError("Verifier output must contain only assessments")
    raw_assessments = payload["assessments"]
    if not isinstance(raw_assessments, list):
        raise ValueError("Verifier assessments must be a list")

    known_claims = {item.claim_id for item in claims}
    known_evidence = {item.evidence_id for item in evidence}
    assessments = []
    normalization_warnings = []
    required_fields = {
        "claim_id",
        "evidence_ids",
        "relation",
        "verdict",
        "rationale",
    }
    for item in raw_assessments:
        if not isinstance(item, dict) or set(item) != required_fields:
            raise ValueError("Verifier assessment has an invalid schema")
        claim_id = item["claim_id"]
        if claim_id not in known_claims:
            raise ValueError("Verifier returned an unknown Claim ID")
        raw_evidence_ids = item["evidence_ids"]
        if not isinstance(raw_evidence_ids, list) or not raw_evidence_ids:
            raise ValueError("Verifier assessment must cite Evidence")
        if any(not isinstance(value, str) for value in raw_evidence_ids):
            raise ValueError("Verifier Evidence IDs must be strings")
        evidence_ids = tuple(
            dict.fromkeys(
                value for value in raw_evidence_ids if value in known_evidence
            )
        )
        if not evidence_ids:
            raise ValueError("Verifier assessment has no known Evidence ID")
        unknown_evidence_ids = tuple(
            dict.fromkeys(
                value for value in raw_evidence_ids if value not in known_evidence
            )
        )
        if unknown_evidence_ids:
            normalization_warnings.append(
                {
                    "claim_id": claim_id,
                    "ignored_unknown_evidence_ids": list(unknown_evidence_ids),
                }
            )
        rationale = item["rationale"]
        if not isinstance(rationale, str) or not rationale.strip():
            raise ValueError("Verifier assessment must include a rationale")
        assessments.append(
            ClaimAssessment(
                claim_id,
                evidence_ids,
                EvidenceRelation(item["relation"]),
                AssessmentVerdict(item["verdict"]),
                rationale.strip(),
                response_artifact_id,
            )
        )
    assessed_claims = [item.claim_id for item in assessments]
    if len(assessed_claims) != len(known_claims) or set(assessed_claims) != known_claims:
        raise ValueError("Verifier must assess every Claim exactly once")
    assessment_ref = store.put_json(
        {
            "assessments": [asdict(item) for item in assessments],
            "normalization_warnings": normalization_warnings,
        },
        producer_step_id=producer_step_id,
        schema_version="conflux-weave.claim-assessments.v1",
    )
    return tuple(assessments), assessment_ref


class VerifiedResearchWorkflow:
    def __init__(self, store: LocalArtifactStore, retrieval: HybridRetrievalPipeline, chat: OpenAICompatibleChatAdapter, *, corpus_scope: str = "configured paper corpus") -> None:
        if not corpus_scope.strip():
            raise ValueError("corpus_scope must not be empty")
        self.store, self.retrieval, self.chat = store, retrieval, chat
        self.corpus_scope = corpus_scope.strip()
        self.research_profile = AgentProfile("research_agent", "v1", "Evidence-bound paper ResearchAgent", ("verified_paper_research",), (RESEARCH_TOOL_ID,), BudgetLedger(180, 30000, 5000, "provider-price-not-frozen", 3, 1, 1))
        self.verifier_profile = AgentProfile("verifier", "v1", "Independent Claim/Evidence verifier", ("verify_research_claims",), (), BudgetLedger(120, 30000, 4000, "provider-price-not-frozen", 2, 0, 1))

    def execute(self, objective: str) -> ResearchExecution:
        if not objective.strip(): raise ValueError("objective must not be empty")
        plan = ResearchPlan(objective.strip(), (objective.strip(),), 1, 1, "all delivered claims accepted by Verifier")
        plan_ref = self.store.put_json(asdict(plan), producer_step_id="s1-research-plan", schema_version="conflux-weave.research-plan.v1")
        run = self.retrieval.search(objective)
        evidence = self._evidence(run)
        retrieval_ref = self.store.put_json(self._retrieval_payload(run), producer_step_id="s1-research-retrieval", schema_version="conflux-weave.retrieval-tool-result.v1")
        claims, research_refs = self._draft(objective, evidence, repair=False)
        if not claims:
            return self._no_answer(
                objective,
                plan_ref.artifact_id,
                retrieval_ref,
                evidence,
                research_refs,
                candidate_claim_count=0,
                repair_rounds=0,
            )
        assessments, verify_refs = self._verify(claims, evidence, round_number=0)
        repair_rounds = 0
        if any(item.verdict is not AssessmentVerdict.ACCEPTED for item in assessments):
            repair_rounds = 1
            claims, repair_refs = self._draft(objective, evidence, repair=True, prior_claims=claims, assessments=assessments)
            assessments, second_verify_refs = self._verify(claims, evidence, round_number=1)
            research_refs += repair_refs; verify_refs += second_verify_refs
        accepted_ids = {item.claim_id for item in assessments if item.verdict is AssessmentVerdict.ACCEPTED and item.relation is EvidenceRelation.SUPPORTS}
        accepted_claims = tuple(claim for claim in claims if claim.claim_id in accepted_ids)
        accepted_assessments = tuple(item for item in assessments if item.claim_id in accepted_ids)
        if not accepted_claims:
            return self._no_answer(
                objective,
                plan_ref.artifact_id,
                retrieval_ref,
                evidence,
                research_refs + verify_refs,
                candidate_claim_count=len(claims),
                repair_rounds=repair_rounds,
            )
        allowed_evidence = {evidence_id for item in accepted_assessments for evidence_id in item.evidence_ids}
        accepted_evidence = tuple(item for item in evidence if item.evidence_id in allowed_evidence)
        citations = tuple(Citation(f"citation-{index:04d}", claim.claim_id, evidence_id, index) for index, (claim, evidence_id) in enumerate(((claim, evidence_id) for claim in accepted_claims for evidence_id in next(item.evidence_ids for item in accepted_assessments if item.claim_id == claim.claim_id)), 1))
        require_closed_citations(accepted_claims, accepted_evidence, citations)
        limitations = self._limitations()
        report = render_evidence_report(title="Verified paper research", intro_lines=(f"> Objective: {objective}", f"> Plan Artifact: `{plan_ref.artifact_id}`", f"> Retrieval Artifact: `{retrieval_ref.artifact_id}`"), blocks=tuple(AnswerBlock(f"Claim {index}", claim.text, EvidenceSupportStatus.CITED, (claim.claim_id,)) for index, claim in enumerate(accepted_claims, 1)), claims=accepted_claims, evidence=accepted_evidence, citations=citations, evidence_trust={item.evidence_id: SourceTrustLevel.GENERAL_SOURCE for item in accepted_evidence}, limitations=limitations)
        report_ref = self.store.put_bytes(report.encode("utf-8"), media_type="text/markdown; charset=utf-8", producer_step_id="s1-research-deliver", schema_version="conflux-weave.verified-research-report.v1")
        coverage = CoverageReport(len(accepted_evidence), len(claims), len(accepted_claims), len(claims) - len(accepted_claims), repair_rounds, "verified_delivery" if accepted_claims else "no_supported_claim")
        harness_refs = self._harness_trace(objective, plan_ref.artifact_id, retrieval_ref, report_ref, accepted_evidence, coverage)
        manifest = {"schema_version": "conflux-weave.verified-research-manifest.v1", "objective": objective, "corpus_scope": self.corpus_scope, "disposition": DeliveryDisposition.COMPLETE.value, "profiles": {"research": asdict(self.research_profile), "verifier": asdict(self.verifier_profile)}, "plan_artifact": plan_ref.artifact_id, "retrieval_artifact": retrieval_ref.artifact_id, "model_artifacts": research_refs + verify_refs, "harness_artifacts": harness_refs, "report_artifact": report_ref.artifact_id, "coverage": asdict(coverage), "citation_closure": 1.0, "repair_rounds": repair_rounds, "limitations": list(limitations)}
        manifest_ref = self.store.put_json(manifest, producer_step_id="s1-research-deliver", schema_version=manifest["schema_version"])
        return ResearchExecution(report_ref.artifact_id, manifest_ref.artifact_id, accepted_claims, accepted_evidence, citations, assessments, coverage, DeliveryDisposition.COMPLETE, limitations)

    def _no_answer(self, objective, plan_ref, retrieval_ref, retrieved_evidence, model_refs, *, candidate_claim_count, repair_rounds):
        limitations = self._limitations() + (
            "No evidence-supported Claim was found in the retrieved chunks for this objective.",
        )
        coverage = CoverageReport(len(retrieved_evidence), candidate_claim_count, 0, candidate_claim_count, repair_rounds, "no_supported_claim")
        report = render_evidence_report(
            title="Verified paper research",
            intro_lines=(
                f"> Objective: {objective}",
                f"> Plan Artifact: `{plan_ref}`",
                f"> Retrieval Artifact: `{retrieval_ref.artifact_id}`",
            ),
            blocks=(
                AnswerBlock(
                    "No evidence-supported answer",
                    "The configured corpus did not yield a Claim that could be supported by the retrieved page-level chunks.",
                    EvidenceSupportStatus.UNSUPPORTED_CLAIM,
                ),
            ),
            claims=(),
            evidence=(),
            citations=(),
            evidence_trust={},
            limitations=limitations,
        )
        report_ref = self.store.put_bytes(report.encode("utf-8"), media_type="text/markdown; charset=utf-8", producer_step_id="s1-research-deliver", schema_version="conflux-weave.verified-research-report.v1")
        harness_refs = self._harness_no_answer_trace(objective, plan_ref, retrieval_ref, report_ref, coverage)
        manifest = {
            "schema_version": "conflux-weave.verified-research-manifest.v1",
            "objective": objective,
            "corpus_scope": self.corpus_scope,
            "disposition": DeliveryDisposition.NO_ANSWER.value,
            "profiles": {"research": asdict(self.research_profile), "verifier": asdict(self.verifier_profile)},
            "plan_artifact": plan_ref,
            "retrieval_artifact": retrieval_ref.artifact_id,
            "model_artifacts": model_refs,
            "harness_artifacts": harness_refs,
            "report_artifact": report_ref.artifact_id,
            "coverage": asdict(coverage),
            "citation_closure": 1.0,
            "repair_rounds": repair_rounds,
            "limitations": list(limitations),
        }
        manifest_ref = self.store.put_json(manifest, producer_step_id="s1-research-deliver", schema_version=manifest["schema_version"])
        return ResearchExecution(report_ref.artifact_id, manifest_ref.artifact_id, (), (), (), (), coverage, DeliveryDisposition.NO_ANSWER, limitations)

    def _limitations(self) -> tuple[str, ...]:
        return (
            f"Evidence is limited to retrieved page-level PDF chunks from: {self.corpus_scope}.",
            "AcademyHunter metadata is not used as Claim evidence.",
        )

    def _evidence(self, run: HybridRetrievalRun) -> tuple[EvidenceRef, ...]:
        evidence = []
        for index, hit in enumerate(run.final.hits[:8], 1):
            document = self.retrieval.document_by_id[hit.document_id]
            evidence.append(EvidenceRef(f"evidence-{index:04d}", hit.source_snapshot_id or "", hit.locator or {}, document.text[:2400], "hybrid-lancedb-rerank-page-chunk-v1"))
        return tuple(evidence)

    def _draft(self, objective: str, evidence: tuple[EvidenceRef, ...], *, repair: bool, prior_claims=(), assessments=()):
        context = {"objective": objective, "evidence": [{"evidence_id": item.evidence_id, "quote": item.quote} for item in evidence]}
        if repair: context.update({"prior_claims": [asdict(item) for item in prior_claims], "assessments": [asdict(item) for item in assessments]})
        completion = self.chat.complete(system_prompt="Return JSON {claims:[{text,evidence_ids}]}. Every claim must be directly entailed by cited evidence. Do not use model knowledge. Keep at most 5 claims." if not repair else "Repair the claims once. Return JSON {claims:[{text,evidence_ids}]}; remove or narrow every rejected/uncertain claim using only supplied evidence.", user_prompt=json.dumps(context, ensure_ascii=False), max_output_tokens=1800, temperature=0, json_object=True, enable_thinking=False, producer_step_id="s1-research-repair" if repair else "s1-research-draft")
        payload = json.loads(completion.content); allowed = {item.evidence_id for item in evidence}; claims=[]
        for index, item in enumerate(payload.get("claims", []), 1):
            ids=item.get("evidence_ids"); text=item.get("text")
            if not isinstance(text,str) or not text.strip() or not isinstance(ids,list) or not ids or any(value not in allowed for value in ids): raise ValueError("ResearchAgent returned invalid Claim/Evidence mapping")
            claims.append(Claim(f"claim-{index:04d}", text.strip(), "research_finding", "primary", "s1-research-repair" if repair else "s1-research-draft"))
        mapping_ref=self.store.put_json({"claims":[{"claim_id":claim.claim_id,"evidence_ids":payload["claims"][i]["evidence_ids"]} for i,claim in enumerate(claims)]},producer_step_id="s1-research-repair" if repair else "s1-research-draft",schema_version="conflux-weave.claim-evidence-map.v1")
        return tuple(claims), [completion.request_artifact.artifact_id, completion.response_artifact.artifact_id, mapping_ref.artifact_id]

    def _verify(self, claims: tuple[Claim, ...], evidence: tuple[EvidenceRef, ...], *, round_number: int):
        prompt={"claims":[asdict(item) for item in claims],"evidence":[asdict(item) for item in evidence],"instruction":"Return assessments for every claim with claim_id,evidence_ids,relation supports|contradicts|context|insufficient,verdict accepted|rejected|uncertain,rationale."}
        completion=self.chat.complete(system_prompt="Act as an independent evidence verifier. Accept only claims directly supported by the quoted evidence. Return JSON {assessments:[...]}. Every evidence_id must exactly match an evidence_id supplied by the user.",user_prompt=json.dumps(prompt,ensure_ascii=False),max_output_tokens=1800,temperature=0,json_object=True,enable_thinking=False,producer_step_id=f"s1-verifier-{round_number}")
        assessments, assessment_ref = _parse_verifier_assessments(
            completion.content,
            claims,
            evidence,
            completion.response_artifact.artifact_id,
            self.store,
            f"s1-verifier-{round_number}",
        )
        return tuple(assessments), [completion.request_artifact.artifact_id,completion.response_artifact.artifact_id,assessment_ref.artifact_id]

    @staticmethod
    def _retrieval_payload(run: HybridRetrievalRun):
        def rows(result): return [{"chunk_id":hit.document_id,"score":hit.score,"rank":hit.rank,"source_snapshot_id":hit.source_snapshot_id,"locator":hit.locator} for hit in result.hits]
        return {"query":run.query,"rerank_status":run.rerank_status,"bm25":rows(run.bm25),"dense":rows(run.dense),"hybrid":rows(run.hybrid),"final":rows(run.final),"embedding_request":run.embedding_request_artifact,"embedding_response":run.embedding_response_artifact,"rerank_request":run.rerank_request_artifact,"rerank_response":run.rerank_response_artifact}

    def _harness_trace(self, objective, plan_ref, retrieval_ref, report_ref, evidence, coverage):
        digest = hashlib.sha256(objective.encode()).hexdigest()[:16]; run_id=f"research-run-{digest}"; created_at=datetime.now(UTC).isoformat().replace("+00:00","Z"); refs=[]
        research_task_id=f"{run_id}:research"; research_context=ContextBundle(f"context:{research_task_id}",research_task_id,"research_agent@v1",objective,{"run_id":run_id,"phase":"research","retrieval_rounds":1},(plan_ref,),(),(),(RESEARCH_TOOL_ID,),("use retrieved Evidence only","do not bypass citation verification"),("submit evidence-bound candidate claims",),created_at)
        research_context_ref=self.store.put_json(contract_to_dict(research_context),producer_step_id="s1-harness-research",schema_version=research_context.schema_version); refs.append(research_context_ref.artifact_id)
        research_task=AgentTask(research_task_id,run_id,"s1-research","verified_paper_research",objective,research_context.completion_criteria,research_context_ref.artifact_id,(plan_ref,),(RESEARCH_TOOL_ID,),self.research_profile.default_budget,f"agent-task:{research_task_id}")
        research_task_ref=self.store.put_json(contract_to_dict(research_task),producer_step_id="s1-harness-research",schema_version=research_task.schema_version); refs.append(research_task_ref.artifact_id)
        tool=ToolResult(f"tool-call:{research_task_id}",RESEARCH_TOOL_ID,research_task_id,ToolResultStatus.SUCCEEDED,(retrieval_ref.artifact_id,),tuple(item.evidence_id for item in evidence),None,created_at,created_at)
        tool_ref=self.store.put_json(contract_to_dict(tool),producer_step_id="s1-harness-research",schema_version=tool.schema_version); refs.append(tool_ref.artifact_id)
        research_result=AgentResult(research_task_id,AgentResultStatus.COMPLETED,"ResearchAgent submitted evidence-bound claims.",(retrieval_ref.artifact_id,),tuple(item.evidence_id for item in evidence))
        research_result_ref=self.store.put_json(contract_to_dict(research_result),producer_step_id="s1-harness-research",schema_version=research_result.schema_version); refs.append(research_result_ref.artifact_id)
        verifier_task_id=f"{run_id}:verifier"; verifier_context=ContextBundle(f"context:{verifier_task_id}",verifier_task_id,"verifier@v1","Verify every candidate Claim against quoted Evidence.",{"run_id":run_id,"phase":"verify","repair_rounds":coverage.repair_rounds},(research_result_ref.artifact_id,),tuple(item.evidence_id for item in evidence),(),(),("independent verification","accept direct support only"),("assess every claim exactly once",),created_at)
        verifier_context_ref=self.store.put_json(contract_to_dict(verifier_context),producer_step_id="s1-harness-verifier",schema_version=verifier_context.schema_version); refs.append(verifier_context_ref.artifact_id)
        verifier_task=AgentTask(verifier_task_id,run_id,"s1-verify","verify_research_claims",verifier_context.objective,verifier_context.completion_criteria,verifier_context_ref.artifact_id,(research_result_ref.artifact_id,),(),self.verifier_profile.default_budget,f"agent-task:{verifier_task_id}")
        verifier_task_ref=self.store.put_json(contract_to_dict(verifier_task),producer_step_id="s1-harness-verifier",schema_version=verifier_task.schema_version); refs.append(verifier_task_ref.artifact_id)
        verifier_result=AgentResult(verifier_task_id,AgentResultStatus.COMPLETED,"Verifier completed claim-level assessment and bounded repair.",(report_ref.artifact_id,),tuple(item.evidence_id for item in evidence))
        verifier_result_ref=self.store.put_json(contract_to_dict(verifier_result),producer_step_id="s1-harness-verifier",schema_version=verifier_result.schema_version); refs.append(verifier_result_ref.artifact_id)
        messages=(MessageEnvelope(f"message:{research_task_id}:assigned",run_id,research_task_id,"orchestrator","research_agent@v1",MessageType.TASK_ASSIGNED,None,run_id,research_task_ref.artifact_id,f"message:{research_task_id}:assigned",created_at),MessageEnvelope(f"message:{research_task_id}:result",run_id,research_task_id,"research_agent@v1","verifier@v1",MessageType.RESULT_SUBMITTED,f"message:{research_task_id}:assigned",run_id,research_result_ref.artifact_id,f"message:{research_task_id}:result",created_at),MessageEnvelope(f"message:{verifier_task_id}:result",run_id,verifier_task_id,"verifier@v1","orchestrator",MessageType.RESULT_SUBMITTED,f"message:{research_task_id}:result",run_id,verifier_result_ref.artifact_id,f"message:{verifier_task_id}:result",created_at))
        for message in messages:
            ref=self.store.put_json(contract_to_dict(message),producer_step_id="s1-harness-messages",schema_version=message.schema_version); refs.append(ref.artifact_id)
        return refs

    def _harness_no_answer_trace(self, objective, plan_ref, retrieval_ref, report_ref, coverage):
        digest = hashlib.sha256(objective.encode()).hexdigest()[:16]
        run_id = f"research-run-{digest}"
        created_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        research_task_id = f"{run_id}:research"
        context = ContextBundle(f"context:{research_task_id}", research_task_id, "research_agent@v1", objective, {"run_id": run_id, "phase": "research", "retrieval_rounds": 1}, (plan_ref,), (), (), (RESEARCH_TOOL_ID,), ("use retrieved Evidence only", "return no claims when Evidence does not support an answer"), ("submit supported claims or an explicit no-answer result",), created_at)
        context_ref = self.store.put_json(contract_to_dict(context), producer_step_id="s1-harness-research", schema_version=context.schema_version)
        task = AgentTask(research_task_id, run_id, "s1-research", "verified_paper_research", objective, context.completion_criteria, context_ref.artifact_id, (plan_ref,), (RESEARCH_TOOL_ID,), self.research_profile.default_budget, f"agent-task:{research_task_id}")
        task_ref = self.store.put_json(contract_to_dict(task), producer_step_id="s1-harness-research", schema_version=task.schema_version)
        tool = ToolResult(f"tool-call:{research_task_id}", RESEARCH_TOOL_ID, research_task_id, ToolResultStatus.SUCCEEDED, (retrieval_ref.artifact_id,), (), None, created_at, created_at)
        tool_ref = self.store.put_json(contract_to_dict(tool), producer_step_id="s1-harness-research", schema_version=tool.schema_version)
        result = AgentResult(research_task_id, AgentResultStatus.COMPLETED, "ResearchAgent returned no evidence-supported claims.", (retrieval_ref.artifact_id, report_ref.artifact_id), ())
        result_ref = self.store.put_json(contract_to_dict(result), producer_step_id="s1-harness-research", schema_version=result.schema_version)
        message = MessageEnvelope(f"message:{research_task_id}:result", run_id, research_task_id, "research_agent@v1", "orchestrator", MessageType.RESULT_SUBMITTED, None, run_id, result_ref.artifact_id, f"message:{research_task_id}:result", created_at)
        message_ref = self.store.put_json(contract_to_dict(message), producer_step_id="s1-harness-messages", schema_version=message.schema_version)
        coverage_ref = self.store.put_json(asdict(coverage), producer_step_id="s1-harness-research", schema_version="conflux-weave.coverage-report.v1")
        return [context_ref.artifact_id, task_ref.artifact_id, tool_ref.artifact_id, result_ref.artifact_id, message_ref.artifact_id, coverage_ref.artifact_id]
