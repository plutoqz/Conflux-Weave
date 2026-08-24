"""Checkpointed Step implementations for durable paper discovery."""

from __future__ import annotations

from dataclasses import asdict

from conflux_weave.core import (
    DeliveryDisposition,
    DeliveryRecord,
    ErrorCategory,
    ErrorRecord,
    RunStatus,
)
from conflux_weave.evidence import (
    AnswerBlock,
    ArtifactRef,
    Citation,
    Claim,
    EvidenceRef,
    EvidenceSupportStatus,
    SourceSnapshot,
    SourceTrustLevel,
    render_evidence_report,
)
from conflux_weave.paper_discovery import (
    MAX_OUTPUT_TOKENS,
    MAX_SELECTED,
    PROMPT_VERSION,
    SCHEMA_VERSION,
    SYSTEM_PROMPT,
    _paper_evidence,
    _paper_prompt,
    _parse_claims,
)
from conflux_weave.retrieval import BM25Retriever, RetrievalDocument
from conflux_weave.runtime.durable_paper_shared import (
    FINAL_LIMITATION,
    RANK_CHECKPOINT,
    SEARCH_CHECKPOINT,
    SYNTHESIS_CHECKPOINT,
    UNMET_CRITERION,
    VALIDATION_CHECKPOINT,
    _paper_from_json,
)
from conflux_weave.runtime.sqlite import BudgetAmount, LeaseClaim


class DurablePaperStepMixin:
    def _execute_search_arxiv(self, claim: LeaseClaim, *, now: str | None) -> None:
        task = self.repository.get_task_for_run(claim.run_id)
        intent = self._intent_artifact(
            claim,
            "arxiv_search",
            {
                "search_query": task.input["search_query"],
                "max_results": task.input["max_results"],
            },
        )
        self._fault("before_search_external_call")
        if not self._authorize_external_call(
            claim,
            intent,
            BudgetAmount(tool_calls=1, retrieval_rounds=1),
            stage="search_arxiv",
            now=now,
        ):
            return
        result = self.search_adapter.search(
            str(task.input["search_query"]),
            max_results=int(task.input["max_results"]),
            producer_step_id=claim.step_id,
        )
        checkpoint = self.artifact_store.put_json(
            {
                "schema_version": SEARCH_CHECKPOINT,
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
        self._fault("search_response_committed")

    def _execute_rank_candidates(self, claim: LeaseClaim, *, now: str | None) -> None:
        task = self.repository.get_task_for_run(claim.run_id)
        search = self._checkpoint(claim.run_id, "search_arxiv", SEARCH_CHECKPOINT)
        papers = tuple(_paper_from_json(item) for item in search["papers"])
        if not papers:
            raise ValueError("arXiv checkpoint has no candidates")
        retriever = BM25Retriever(
            RetrievalDocument(paper.arxiv_id, paper.title + " " + paper.summary)
            for paper in papers
        )
        hits = retriever.search(
            str(task.input["search_query"]), top_k=min(MAX_SELECTED, len(papers))
        ).hits
        by_id = {paper.arxiv_id: paper for paper in papers}
        selected = tuple(by_id[hit.document_id] for hit in hits)
        if not selected:
            raise ValueError("BM25 produced no positive-score candidates")
        snapshot = SourceSnapshot(**search["snapshot"])
        evidence = _paper_evidence(selected, snapshot.source_id)
        checkpoint = self.artifact_store.put_json(
            {
                "schema_version": RANK_CHECKPOINT,
                "selected_papers": [asdict(paper) for paper in selected],
                "evidence": [asdict(item) for item in evidence],
            },
            producer_step_id=claim.step_id,
            schema_version=RANK_CHECKPOINT,
        )
        self.worker.complete(claim, (checkpoint,), now=now)

    def _execute_synthesize_claims(self, claim: LeaseClaim, *, now: str | None) -> None:
        task = self.repository.get_task_for_run(claim.run_id)
        rank = self._checkpoint(claim.run_id, "rank_candidates", RANK_CHECKPOINT)
        evidence = tuple(EvidenceRef(**item) for item in rank["evidence"])
        intent = self._intent_artifact(
            claim,
            "provider_chat_completion",
            {
                "prompt_version": PROMPT_VERSION,
                "model": self.chat_adapter.config.model,
                "evidence_ids": [item.evidence_id for item in evidence],
            },
        )
        if not self._authorize_external_call(
            claim,
            intent,
            BudgetAmount(output_tokens=MAX_OUTPUT_TOKENS, tool_calls=1),
            stage="synthesize_claims",
            now=now,
        ):
            return
        completion = self.chat_adapter.complete(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=_paper_prompt(str(task.input["query"]), evidence),
            max_output_tokens=MAX_OUTPUT_TOKENS,
            temperature=0.0,
            json_object=True,
            enable_thinking=False,
            producer_step_id=claim.step_id,
        )
        checkpoint = self.artifact_store.put_json(
            {
                "schema_version": SYNTHESIS_CHECKPOINT,
                "response_id": completion.response_id,
                "model": completion.model,
                "content": completion.content,
                "finish_reason": completion.finish_reason,
                "input_tokens": completion.input_tokens,
                "output_tokens": completion.output_tokens,
                "total_tokens": completion.total_tokens,
                "request_artifact_ref": completion.request_artifact.artifact_id,
                "response_artifact_ref": completion.response_artifact.artifact_id,
            },
            producer_step_id=claim.step_id,
            schema_version=SYNTHESIS_CHECKPOINT,
        )
        actual_usage = BudgetAmount(
            input_tokens=completion.input_tokens,
            output_tokens=completion.output_tokens,
            tool_calls=1,
        )
        budget = self.repository.get_budget_status(claim.run_id)
        will_exceed = any(
            getattr(budget.actual, dimension) + getattr(actual_usage, dimension)
            > getattr(budget.limit, dimension)
            for dimension in (
                "input_tokens",
                "output_tokens",
                "tool_calls",
                "retrieval_rounds",
            )
        )
        if will_exceed:
            overage_detail, overage_error = self._budget_error(
                claim,
                code="budget_actual_exceeded",
                stage="synthesize_claims",
                message=(
                    "Provider reported usage exceeded the frozen Run budget; "
                    "no subsequent external call is allowed."
                ),
                affected=(
                    intent.artifact_id,
                    completion.request_artifact.artifact_id,
                    completion.response_artifact.artifact_id,
                    checkpoint.artifact_id,
                ),
                recovery_action="Inspect usage and partial Artifacts; create a new Run with an explicitly frozen budget.",
            )
        else:
            overage_detail = overage_error = None
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
            actual_usage=actual_usage,
            overage_detail=overage_detail,
            overage_error=overage_error,
            now=now,
        )
        self._fault("provider_response_committed")

    def _authorize_external_call(
        self,
        claim: LeaseClaim,
        intent: ArtifactRef,
        reservation: BudgetAmount,
        *,
        stage: str,
        now: str | None,
    ) -> bool:
        detail, error = self._budget_error(
            claim,
            code="budget_reservation_denied",
            stage=stage,
            message="The frozen Run budget cannot cover this external call; the call was not started.",
            affected=(
                self.repository.get_run(claim.run_id).config_snapshot_ref,
                intent.artifact_id,
            ),
            recovery_action="Inspect the budget ledger; create a new Run with an explicitly frozen budget if the task should continue.",
        )
        return self.repository.authorize_external_call(
            claim, intent, reservation, detail, error, now=now
        )

    def _budget_error(
        self,
        claim: LeaseClaim,
        *,
        code: str,
        stage: str,
        message: str,
        affected: tuple[str, ...],
        recovery_action: str,
    ) -> tuple[ArtifactRef, ErrorRecord]:
        detail = self.artifact_store.put_json(
            {
                "schema_version": "conflux-weave.w3.budget-error.v1",
                "run_id": claim.run_id,
                "step_id": claim.step_id,
                "attempt_id": claim.attempt_id,
                "code": code,
                "stage": stage,
                "automatic_retry": False,
                "automatic_budget_expansion": False,
                "cost_enforcement": "unavailable",
            },
            producer_step_id=claim.step_id,
            schema_version="conflux-weave.w3.budget-error.v1",
        )
        return detail, ErrorRecord(
            code=code,
            category=ErrorCategory.BUDGET,
            stage=stage,
            retryable=False,
            user_message=message,
            technical_detail_ref=detail.artifact_id,
            affected_artifact_refs=affected,
            recovery_action=recovery_action,
        )

    def _execute_validate_delivery(self, claim: LeaseClaim, *, now: str | None) -> None:
        rank = self._checkpoint(claim.run_id, "rank_candidates", RANK_CHECKPOINT)
        synthesis = self._checkpoint(
            claim.run_id, "synthesize_claims", SYNTHESIS_CHECKPOINT
        )
        evidence = tuple(EvidenceRef(**item) for item in rank["evidence"])
        claims, citations, model_limitations = _parse_claims(
            str(synthesis["content"]), evidence, claim.step_id
        )
        checkpoint = self.artifact_store.put_json(
            {
                "schema_version": VALIDATION_CHECKPOINT,
                "claims": [asdict(item) for item in claims],
                "citations": [asdict(item) for item in citations],
                "limitations": [*model_limitations, FINAL_LIMITATION],
                "unmet_criteria": [UNMET_CRITERION],
            },
            producer_step_id=claim.step_id,
            schema_version=VALIDATION_CHECKPOINT,
        )
        self.worker.complete(claim, (checkpoint,), now=now)

    def _execute_publish_delivery(self, claim: LeaseClaim, *, now: str | None) -> None:
        task = self.repository.get_task_for_run(claim.run_id)
        run = self.repository.get_run(claim.run_id)
        search = self._checkpoint(claim.run_id, "search_arxiv", SEARCH_CHECKPOINT)
        rank = self._checkpoint(claim.run_id, "rank_candidates", RANK_CHECKPOINT)
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
        report = render_evidence_report(
            title="arXiv 论文发现",
            intro_lines=(
                f"> 研究问题：{task.input['query']}",
                f"> arXiv 检索式：`{task.input['search_query']}`",
            ),
            blocks=(
                AnswerBlock(
                    "候选论文及相关性",
                    "\n".join(f"- {claim_item.text}" for claim_item in claims),
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
        manifest = self.artifact_store.put_json(
            {
                "schema_version": SCHEMA_VERSION,
                "run_id": claim.run_id,
                "status": RunStatus.PARTIAL.value,
                "query": task.input["query"],
                "search_query": task.input["search_query"],
                "config_artifact_ref": run.config_snapshot_ref,
                "search_response_artifact_ref": search["response_artifact_ref"],
                "search_snapshot_artifact_ref": search["snapshot_artifact_ref"],
                "search_manifest_artifact_ref": search["manifest_artifact_ref"],
                "selected_arxiv_ids": [
                    item["arxiv_id"] for item in rank["selected_papers"]
                ],
                "provider_request_artifact_ref": synthesis["request_artifact_ref"],
                "provider_response_artifact_ref": synthesis["response_artifact_ref"],
                "provider_response_id": synthesis["response_id"],
                "usage": {
                    "input_tokens": synthesis["input_tokens"],
                    "output_tokens": synthesis["output_tokens"],
                    "total_tokens": synthesis["total_tokens"],
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
            schema_version=SCHEMA_VERSION,
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
