"""Trace construction and failure isolation for durable paper discovery."""

from __future__ import annotations

from conflux_weave.runtime.sqlite import LeaseClaim
from conflux_weave.runtime.telemetry import TraceRecord


class DurablePaperTraceMixin:
    def _emit_trace(
        self,
        claim: LeaseClaim,
        step_kind: str,
        status: str,
        *,
        now: str | None,
    ) -> None:
        if self.trace is None:
            return
        try:
            record = self._trace_record(claim, step_kind, status)
            self.trace.export(record)
        except Exception as exc:
            try:
                self.repository.record_telemetry_drop(
                    claim.run_id,
                    step_id=claim.step_id,
                    attempt_id=claim.attempt_id,
                    span_name=f"conflux_weave.{step_kind}",
                    reason=type(exc).__name__,
                    now=now,
                )
            except Exception:
                pass

    def _trace_record(
        self, claim: LeaseClaim, step_kind: str, status: str
    ) -> TraceRecord:
        task = self.repository.get_task_for_run(claim.run_id)
        run = self.repository.get_run(claim.run_id)
        step = next(
            item
            for item in self.repository.get_steps(claim.run_id)
            if item.step_id == claim.step_id
        )
        budget = self.repository.get_budget_status(claim.run_id)
        span_kind = {
            "search_arxiv": "TOOL",
            "search_slot_1": "TOOL",
            "search_slot_2_or_skip": "TOOL",
            "propose_plan": "LLM",
            "synthesize_claims": "LLM",
        }.get(step_kind, "CHAIN")
        prompt_version = {
            "propose_plan": task.input.get("planner_prompt_version", "none"),
            "synthesize_claims": task.input.get(
                "synthesis_prompt_version",
                task.input.get("prompt_version", "none"),
            ),
        }.get(step_kind, task.input.get("prompt_version", "none"))
        return TraceRecord(
            name=f"conflux_weave.{step_kind}",
            attributes={
                "task_id": task.task_id,
                "run_id": claim.run_id,
                "step_id": claim.step_id,
                "attempt_id": claim.attempt_id,
                "attempt": claim.attempt_number,
                "workflow_version": run.workflow_version,
                "provider_model": str(task.input.get("model", "none")),
                "prompt_version": str(prompt_version),
                "budget_input_tokens_limit": budget.limit.input_tokens,
                "budget_output_tokens_limit": budget.limit.output_tokens,
                "budget_tool_calls_actual": budget.actual.tool_calls,
                "budget_retrieval_rounds_actual": budget.actual.retrieval_rounds,
                "artifact_refs": tuple(step.output_refs),
                "status": status,
                "openinference.span.kind": span_kind,
            },
        )

    def _record_trace_drop(self, record: TraceRecord, reason: str) -> None:
        attributes = record.attributes
        self.repository.record_telemetry_drop(
            str(attributes["run_id"]),
            step_id=str(attributes["step_id"]),
            attempt_id=str(attributes["attempt_id"]),
            span_name=record.name,
            reason=reason,
            now=self.clock(),
        )
