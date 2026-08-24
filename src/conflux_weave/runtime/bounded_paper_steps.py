"""Static Step handlers for the W4 bounded arXiv planning strategy."""

from __future__ import annotations

from dataclasses import asdict
import json

from conflux_weave.core import (
    DeliveryDisposition,
    DeliveryRecord,
    ErrorCategory,
    ErrorRecord,
    RunStatus,
)
from conflux_weave.evidence import (
    AnswerBlock,
    Citation,
    Claim,
    EvidenceRef,
    EvidenceSupportStatus,
    SourceSnapshot,
    SourceTrustLevel,
    render_evidence_report,
)
from conflux_weave.paper_discovery import MAX_SELECTED, _paper_evidence
from conflux_weave.planning import (
    ActionType,
    ContextBuilder,
    PlanBudget,
    PlanningContractError,
    PlanValidator,
    ToolGateway,
    ToolResult,
    parse_plan_proposal,
)
from conflux_weave.provider import ChatCompletionResult
from conflux_weave.retrieval import BM25Retriever, RetrievalDocument
from conflux_weave.runtime.durable_paper_shared import (
    RANK_CHECKPOINT,
    SEARCH_CHECKPOINT,
    SYNTHESIS_CHECKPOINT,
    VALIDATION_CHECKPOINT,
    _paper_from_json,
)
from conflux_weave.runtime.sqlite import BudgetAmount, LeaseClaim


CONTEXT_CHECKPOINT = "conflux-weave.w4.context-checkpoint.v1"
PLAN_CHECKPOINT = "conflux-weave.w4.plan-checkpoint.v1"
PLAN_VALIDATION_CHECKPOINT = "conflux-weave.w4.plan-validation-checkpoint.v1"
SKIPPED_SEARCH_CHECKPOINT = "conflux-weave.w4.search-skipped.v1"
PLANNER_OUTPUT_TOKENS = 768
PLANNER_PROMPT_VERSION = "bounded-arxiv-planner-prompt-v1"
PLANNER_SYSTEM_PROMPT = """You are a bounded arXiv search planner. Return one JSON object only.
Use only search_arxiv. Produce one or two distinct tool_call actions followed by one finish action.
Preserve every constraint by id. Never relax constraints, add tools, retry, or invent evidence."""


class BoundedPaperStepMixin:
    def _context(self, run_id: str):
        task = self.repository.get_task_for_run(run_id)
        run = self.repository.get_run(run_id)
        context = ContextBuilder().build(
            task_summary=str(task.input["task_summary"]),
            research_question=str(task.input["query"]),
            inclusion_constraints=tuple(task.input["inclusion_constraints"]),
            exclusion_constraints=tuple(task.input["exclusion_constraints"]),
            hard_constraints=tuple(task.input["hard_constraints"]),
            budget_remaining=PlanBudget(tool_calls=2, retrieval_rounds=2),
            input_refs=(run.config_snapshot_ref,),
            max_search_actions=2,
        )
        checkpoint = self._checkpoint(run_id, "build_context", CONTEXT_CHECKPOINT)
        if checkpoint["content_sha256"] != context.content_sha256:
            raise ValueError("persisted ContextBundle hash does not match frozen Run input")
        return context

    def _proposal(self, run_id: str):
        checkpoint = self._checkpoint(run_id, "propose_plan", PLAN_CHECKPOINT)
        return parse_plan_proposal(str(checkpoint["content"]))

    def _execute_build_context(self, claim: LeaseClaim, *, now: str | None) -> None:
        task = self.repository.get_task_for_run(claim.run_id)
        run = self.repository.get_run(claim.run_id)
        context = ContextBuilder().build(
            task_summary=str(task.input["task_summary"]),
            research_question=str(task.input["query"]),
            inclusion_constraints=tuple(task.input["inclusion_constraints"]),
            exclusion_constraints=tuple(task.input["exclusion_constraints"]),
            hard_constraints=tuple(task.input["hard_constraints"]),
            budget_remaining=PlanBudget(tool_calls=2, retrieval_rounds=2),
            input_refs=(run.config_snapshot_ref,),
            max_search_actions=2,
        )
        checkpoint = self.artifact_store.put_json(
            {
                "schema_version": CONTEXT_CHECKPOINT,
                "context": asdict(context),
                "content_sha256": context.content_sha256,
            },
            producer_step_id=claim.step_id,
            schema_version=CONTEXT_CHECKPOINT,
        )
        self.worker.complete(claim, (checkpoint,), now=now)

    def _execute_propose_plan(self, claim: LeaseClaim, *, now: str | None) -> None:
        context = self._context(claim.run_id)
        intent = self._intent_artifact(
            claim,
            "provider_planner_completion",
            {
                "prompt_version": PLANNER_PROMPT_VERSION,
                "model": self.chat_adapter.config.model,
                "context_sha256": context.content_sha256,
            },
        )
        if not self._authorize_external_call(
            claim,
            intent,
            BudgetAmount(output_tokens=PLANNER_OUTPUT_TOKENS, tool_calls=1),
            stage="propose_plan",
            now=now,
        ):
            return
        completion = self.chat_adapter.complete(
            system_prompt=PLANNER_SYSTEM_PROMPT,
            user_prompt=self._planner_prompt(context),
            max_output_tokens=PLANNER_OUTPUT_TOKENS,
            temperature=0.0,
            json_object=True,
            enable_thinking=False,
            producer_step_id=claim.step_id,
        )
        checkpoint = self.artifact_store.put_json(
            {
                "schema_version": PLAN_CHECKPOINT,
                "prompt_version": PLANNER_PROMPT_VERSION,
                "context_sha256": context.content_sha256,
                "content": completion.content,
                "response_id": completion.response_id,
                "model": completion.model,
                "input_tokens": completion.input_tokens,
                "output_tokens": completion.output_tokens,
                "total_tokens": completion.total_tokens,
                "request_artifact_ref": completion.request_artifact.artifact_id,
                "response_artifact_ref": completion.response_artifact.artifact_id,
            },
            producer_step_id=claim.step_id,
            schema_version=PLAN_CHECKPOINT,
        )
        self._complete_provider_call(claim, intent, completion, checkpoint, now=now)
        self._fault("planner_response_committed")

    def _execute_validate_plan(self, claim: LeaseClaim, *, now: str | None) -> None:
        context = self._context(claim.run_id)
        try:
            proposal = self._proposal(claim.run_id)
        except PlanningContractError as exc:
            self._reject_plan(
                claim,
                ({"code": exc.code, "path": exc.path, "message": str(exc)},),
                now=now,
            )
            return
        result = PlanValidator().validate(context, proposal)
        if not result.accepted:
            self._reject_plan(
                claim,
                tuple(asdict(item) for item in result.violations),
                proposal_sha256=proposal.proposal_sha256,
                now=now,
            )
            return
        validated = result.require_validated()
        checkpoint = self.artifact_store.put_json(
            {
                "schema_version": PLAN_VALIDATION_CHECKPOINT,
                "accepted": True,
                "context_sha256": validated.context_sha256,
                "proposal_sha256": proposal.proposal_sha256,
                "validation_sha256": validated.validation_sha256,
                "search_action_ids": list(validated.search_action_ids),
                "required_budget": asdict(validated.required_budget),
            },
            producer_step_id=claim.step_id,
            schema_version=PLAN_VALIDATION_CHECKPOINT,
        )
        self.worker.complete(claim, (checkpoint,), now=now)

    def _reject_plan(
        self,
        claim: LeaseClaim,
        violations: tuple[dict, ...],
        *,
        proposal_sha256: str | None = None,
        now: str | None,
    ) -> None:
        detail = self.artifact_store.put_json(
            {
                "schema_version": "conflux-weave.w4.plan-rejection.v1",
                "run_id": claim.run_id,
                "step_id": claim.step_id,
                "proposal_sha256": proposal_sha256,
                "violations": list(violations),
                "tool_calls_started_after_rejection": 0,
                "automatic_retry": False,
                "fallback": False,
            },
            producer_step_id=claim.step_id,
            schema_version="conflux-weave.w4.plan-rejection.v1",
        )
        error = ErrorRecord(
            code="plan_contract_rejected",
            category=ErrorCategory.STRATEGY,
            stage="validate_plan",
            retryable=False,
            user_message="The Planner proposal violated the frozen bounded strategy contract.",
            technical_detail_ref=detail.artifact_id,
            affected_artifact_refs=(
                self.repository.get_run(claim.run_id).config_snapshot_ref,
            ),
            recovery_action="Inspect the preserved Plan response and violations; create a new Run after correcting the Planner contract.",
        )
        self.repository.record_error(claim, error, (detail,), now=now)
        self.worker.fail(claim, detail.artifact_id, now=now)
        self.repository.transition_run(
            claim.run_id, RunStatus.FAILED, updated_at=now or self.clock()
        )

    def _execute_search_slot_1(self, claim: LeaseClaim, *, now: str | None) -> None:
        self._execute_search_slot(claim, 0, now=now)

    def _execute_search_slot_2_or_skip(
        self, claim: LeaseClaim, *, now: str | None
    ) -> None:
        validation = self._checkpoint(
            claim.run_id, "validate_plan", PLAN_VALIDATION_CHECKPOINT
        )
        if len(validation["search_action_ids"]) < 2:
            checkpoint = self.artifact_store.put_json(
                {
                    "schema_version": SKIPPED_SEARCH_CHECKPOINT,
                    "reason": "validated_plan_has_one_search_action",
                    "tool_called": False,
                    "automatic_fallback": False,
                },
                producer_step_id=claim.step_id,
                schema_version=SKIPPED_SEARCH_CHECKPOINT,
            )
            self.worker.skip(claim, (checkpoint,), now=now)
            return
        self._execute_search_slot(claim, 1, now=now)

    def _execute_search_slot(
        self, claim: LeaseClaim, slot_index: int, *, now: str | None
    ) -> None:
        context = self._context(claim.run_id)
        proposal = self._proposal(claim.run_id)
        validation = self._checkpoint(
            claim.run_id, "validate_plan", PLAN_VALIDATION_CHECKPOINT
        )
        action_id = str(validation["search_action_ids"][slot_index])
        action = next(item for item in proposal.actions if item.action_id == action_id)
        assert action.arguments is not None
        intent = self._intent_artifact(
            claim,
            "arxiv_search",
            {
                "action_id": action_id,
                "search_query": action.arguments.query,
                "max_results": action.arguments.max_results,
            },
        )
        if not self._authorize_external_call(
            claim,
            intent,
            BudgetAmount(tool_calls=1, retrieval_rounds=1),
            stage=claim.step_id.rsplit(":", 1)[-1],
            now=now,
        ):
            return
        captured = {}

        def search_handler(bound_action_id, arguments):
            result = self.search_adapter.search(
                arguments.query,
                max_results=arguments.max_results,
                producer_step_id=claim.step_id,
            )
            captured["result"] = result
            return ToolResult(
                "search_arxiv",
                bound_action_id,
                (
                    result.response_artifact.artifact_id,
                    result.snapshot_artifact.artifact_id,
                    result.manifest_artifact.artifact_id,
                ),
            )

        ToolGateway({"search_arxiv": search_handler}).dispatch(
            context, proposal, action_id
        )
        result = captured["result"]
        checkpoint = self.artifact_store.put_json(
            {
                "schema_version": SEARCH_CHECKPOINT,
                "action_id": action_id,
                "search_query": action.arguments.query,
                "papers": [asdict(paper) for paper in result.papers],
                "snapshot": asdict(result.snapshot),
                "response_artifact_ref": result.response_artifact.artifact_id,
                "snapshot_artifact_ref": result.snapshot_artifact.artifact_id,
                "manifest_artifact_ref": result.manifest_artifact.artifact_id,
            },
            producer_step_id=claim.step_id,
            schema_version=SEARCH_CHECKPOINT,
        )
        self.repository.complete_external_attempt(
            claim,
            (
                intent,
                result.response_artifact,
                result.snapshot_artifact,
                result.manifest_artifact,
                checkpoint,
            ),
            request_artifact_ref=intent.artifact_id,
            response_artifact_ref=result.response_artifact.artifact_id,
            actual_usage=BudgetAmount(tool_calls=1, retrieval_rounds=1),
            now=now,
        )
        self._fault(f"search_slot_{slot_index + 1}_response_committed")

    def _execute_merge_and_rank(self, claim: LeaseClaim, *, now: str | None) -> None:
        task = self.repository.get_task_for_run(claim.run_id)
        search_steps = [
            item
            for item in self.repository.get_steps(claim.run_id)
            if item.kind in {"search_slot_1", "search_slot_2_or_skip"}
            and item.status.value == "succeeded"
        ]
        searches = [
            self._checkpoint(claim.run_id, step.kind, SEARCH_CHECKPOINT)
            for step in search_steps
        ]
        papers_by_id = {}
        source_by_id = {}
        for search in searches:
            source_id = SourceSnapshot(**search["snapshot"]).source_id
            for item in search["papers"]:
                paper = _paper_from_json(item)
                papers_by_id.setdefault(paper.arxiv_id, paper)
                source_by_id.setdefault(paper.arxiv_id, source_id)
        if not papers_by_id:
            raise ValueError("validated searches returned no arXiv candidates")
        proposal = self._proposal(claim.run_id)
        ranking_query = " ".join(
            action.arguments.query
            for action in proposal.actions
            if action.action_type is ActionType.TOOL_CALL and action.arguments is not None
        )
        retriever = BM25Retriever(
            RetrievalDocument(paper.arxiv_id, paper.title + " " + paper.summary)
            for paper in papers_by_id.values()
        )
        hits = retriever.search(
            ranking_query or str(task.input["query"]),
            top_k=min(MAX_SELECTED, len(papers_by_id)),
        ).hits
        selected = tuple(papers_by_id[hit.document_id] for hit in hits)
        if not selected:
            raise ValueError("BM25 produced no positive-score candidates")
        evidence = []
        for index, paper in enumerate(selected, start=1):
            item = _paper_evidence((paper,), source_by_id[paper.arxiv_id])[0]
            evidence.append(
                EvidenceRef(
                    f"arxiv-paper-{index:02d}",
                    item.source_snapshot_id,
                    item.locator,
                    item.quote,
                    item.extraction_method,
                )
            )
        checkpoint = self.artifact_store.put_json(
            {
                "schema_version": RANK_CHECKPOINT,
                "search_queries": [item["search_query"] for item in searches],
                "deduplicated_candidate_count": len(papers_by_id),
                "selected_papers": [asdict(paper) for paper in selected],
                "evidence": [asdict(item) for item in evidence],
            },
            producer_step_id=claim.step_id,
            schema_version=RANK_CHECKPOINT,
        )
        self.worker.complete(claim, (checkpoint,), now=now)

    def _execute_publish_delivery(self, claim: LeaseClaim, *, now: str | None) -> None:
        task = self.repository.get_task_for_run(claim.run_id)
        run = self.repository.get_run(claim.run_id)
        context = self._checkpoint(claim.run_id, "build_context", CONTEXT_CHECKPOINT)
        plan = self._checkpoint(claim.run_id, "propose_plan", PLAN_CHECKPOINT)
        plan_validation = self._checkpoint(
            claim.run_id, "validate_plan", PLAN_VALIDATION_CHECKPOINT
        )
        rank = self._checkpoint(claim.run_id, "merge_and_rank", RANK_CHECKPOINT)
        synthesis = self._checkpoint(
            claim.run_id, "synthesize_claims", SYNTHESIS_CHECKPOINT
        )
        validation = self._checkpoint(
            claim.run_id, "validate_delivery", VALIDATION_CHECKPOINT
        )
        evidence = tuple(EvidenceRef(**item) for item in rank["evidence"])
        claims = tuple(Claim(**item) for item in validation["claims"])
        citations = tuple(Citation(**item) for item in validation["citations"])
        limitations = tuple(str(item) for item in validation["limitations"])
        unmet = tuple(str(item) for item in validation["unmet_criteria"])
        query_lines = tuple(
            f"> 计划检索式 {index}：`{query}`"
            for index, query in enumerate(rank["search_queries"], start=1)
        )
        report = render_evidence_report(
            title="arXiv 论文发现",
            intro_lines=(f"> 研究问题：{task.input['query']}", *query_lines),
            blocks=(
                AnswerBlock(
                    "候选论文及相关性",
                    "\n".join(f"- {item.text}" for item in claims),
                    EvidenceSupportStatus.PARTIAL_SUPPORT,
                    tuple(item.claim_id for item in claims),
                ),
            ),
            claims=claims,
            evidence=evidence,
            citations=citations,
            evidence_trust={
                item.evidence_id: SourceTrustLevel.GENERAL_SOURCE for item in evidence
            },
            limitations=limitations,
        )
        report_artifact = self.artifact_store.put_bytes(
            report.encode("utf-8"),
            media_type="text/markdown; charset=utf-8",
            producer_step_id=claim.step_id,
            schema_version="conflux-weave.paper-discovery-report.v1",
        )
        search_lineage = []
        for kind in ("search_slot_1", "search_slot_2_or_skip"):
            step = next(
                item for item in self.repository.get_steps(claim.run_id) if item.kind == kind
            )
            entry = {"step_kind": kind, "status": step.status.value}
            if step.status.value == "succeeded":
                search = self._checkpoint(claim.run_id, kind, SEARCH_CHECKPOINT)
                entry.update(
                    {
                        "action_id": search["action_id"],
                        "search_query": search["search_query"],
                        "response_artifact_ref": search["response_artifact_ref"],
                        "snapshot_artifact_ref": search["snapshot_artifact_ref"],
                        "manifest_artifact_ref": search["manifest_artifact_ref"],
                    }
                )
            else:
                skipped = self._checkpoint(
                    claim.run_id, kind, SKIPPED_SEARCH_CHECKPOINT
                )
                entry["reason"] = skipped["reason"]
            search_lineage.append(entry)
        manifest = self.artifact_store.put_json(
            {
                "schema_version": "conflux-weave.w4.bounded-paper-delivery.v1",
                "run_id": claim.run_id,
                "status": RunStatus.PARTIAL.value,
                "strategy_id": "bounded-arxiv-planner-v1",
                "query": task.input["query"],
                "config_artifact_ref": run.config_snapshot_ref,
                "context_sha256": context["content_sha256"],
                "planner_request_artifact_ref": plan["request_artifact_ref"],
                "planner_response_artifact_ref": plan["response_artifact_ref"],
                "planner_response_id": plan["response_id"],
                "plan_validation_sha256": plan_validation["validation_sha256"],
                "searches": search_lineage,
                "deduplicated_candidate_count": rank["deduplicated_candidate_count"],
                "selected_arxiv_ids": [
                    item["arxiv_id"] for item in rank["selected_papers"]
                ],
                "synthesis_request_artifact_ref": synthesis["request_artifact_ref"],
                "synthesis_response_artifact_ref": synthesis["response_artifact_ref"],
                "synthesis_response_id": synthesis["response_id"],
                "usage": {
                    "input_tokens": plan["input_tokens"] + synthesis["input_tokens"],
                    "output_tokens": plan["output_tokens"] + synthesis["output_tokens"],
                    "total_tokens": plan["total_tokens"] + synthesis["total_tokens"],
                },
                "report_artifact_ref": report_artifact.artifact_id,
                "claim_count": len(claims),
                "evidence_count": len(evidence),
                "citation_count": len(citations),
                "limitations": list(limitations),
                "unmet_criteria": list(unmet),
                "automatic_retry": False,
                "fallback": False,
                "secret_recorded": False,
            },
            producer_step_id=claim.step_id,
            schema_version="conflux-weave.w4.bounded-paper-delivery.v1",
        )
        delivery = DeliveryRecord(
            run_id=claim.run_id,
            disposition=DeliveryDisposition.PARTIAL,
            artifact_refs=(report_artifact.artifact_id, manifest.artifact_id),
            evidence_refs=tuple(item.evidence_id for item in evidence),
            limitations=limitations,
            unmet_criteria=unmet,
            recovery_actions=(
                "如需完整综述，授权出版社/Crossref 检索和全文核验后创建新 Run。",
            ),
        )
        self._fault("publish_artifacts_written")
        self.repository.publish_delivery(
            claim.run_id,
            RunStatus.PARTIAL,
            delivery,
            (report_artifact, manifest),
            claim=claim,
            published_at=now,
        )

    def _planner_prompt(self, context) -> str:
        payload = asdict(context)
        payload["allowed_actions"] = [item.value for item in context.allowed_actions]
        payload["constraints"] = [
            {**asdict(item), "kind": item.kind.value} for item in context.constraints
        ]
        return (
            "ContextBundle:\n"
            + json.dumps(payload, ensure_ascii=False, sort_keys=True)
            + "\nRequired root keys: schema_version, strategy_id, strategy_version, "
            "context_sha256, objective, actions, stop_reason. "
            "Each action requires action_id, action_type, tool_id, arguments, "
            "expected_evidence, constraint_refs. Finish uses null tool_id and empty arguments."
        )

    def _complete_provider_call(
        self,
        claim: LeaseClaim,
        intent,
        completion: ChatCompletionResult,
        checkpoint,
        *,
        now: str | None,
    ) -> None:
        actual = BudgetAmount(
            input_tokens=completion.input_tokens,
            output_tokens=completion.output_tokens,
            tool_calls=1,
        )
        budget = self.repository.get_budget_status(claim.run_id)
        exceeded = any(
            getattr(budget.actual, name) + getattr(actual, name)
            > getattr(budget.limit, name)
            for name in ("input_tokens", "output_tokens", "tool_calls", "retrieval_rounds")
        )
        if exceeded:
            detail, error = self._budget_error(
                claim,
                code="budget_actual_exceeded",
                stage="propose_plan",
                message="Planner usage exceeded the frozen Run budget; no search is allowed.",
                affected=(
                    intent.artifact_id,
                    completion.request_artifact.artifact_id,
                    completion.response_artifact.artifact_id,
                    checkpoint.artifact_id,
                ),
                recovery_action="Inspect Planner usage and create a new explicitly budgeted Run.",
            )
        else:
            detail = error = None
        self.repository.complete_external_attempt(
            claim,
            (
                intent,
                completion.request_artifact,
                completion.response_artifact,
                checkpoint,
            ),
            request_artifact_ref=completion.request_artifact.artifact_id,
            response_artifact_ref=completion.response_artifact.artifact_id,
            external_response_id=completion.response_id,
            actual_usage=actual,
            overage_detail=detail,
            overage_error=error,
            now=now,
        )
