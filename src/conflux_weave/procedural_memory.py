"""Procedural memory extraction and eligibility governance (P7-B3).

Enforces strict quality, intent, and HITL gates:
1. Run must be succeeded (status == 'succeeded' and delivery complete);
2. User must have explicitly marked it useful (user_marked_useful is True or feedback > 0);
3. Key Artifact and Evidence must be complete;
4. Extraction stages candidates as 'pending' for Human-in-the-Loop (HITL) approval;
5. Retains provenance with source_type='procedural_run' and source_id=run_id.

Strictly prohibits automatically turning all historical runs into long-term memory.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Any

from conflux_weave.core import DeliveryDisposition, RunStatus
from conflux_weave.runtime.artifacts import LocalArtifactStore
from conflux_weave.runtime.memory_store import (
    CandidateStatus,
    HierarchicalMemoryStore,
    MemoryCandidate,
    MemoryCategory,
    MemoryScope,
)
from conflux_weave.runtime.sqlite import SQLiteRuntimeRepository


class ProceduralIneligibleError(ValueError):
    """Raised when a run fails one or more eligibility criteria for procedural memory extraction."""

    def __init__(self, check_result: ProceduralEligibilityCheckResult) -> None:
        self.check_result = check_result
        super().__init__(f"Run {check_result.run_id} is ineligible for procedural extraction: {check_result.reason}")


@dataclass(frozen=True, slots=True)
class ProceduralEligibilityCheckResult:
    """Detailed audit result of eligibility gates."""

    eligible: bool
    reason: str
    missing_criteria: tuple[str, ...]
    run_id: str
    run_status: str | None = None
    delivery_disposition: str | None = None
    has_report_artifact: bool = False
    evidence_count: int = 0
    user_marked_useful: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "eligible": self.eligible,
            "reason": self.reason,
            "missing_criteria": list(self.missing_criteria),
            "run_id": self.run_id,
            "run_status": self.run_status,
            "delivery_disposition": self.delivery_disposition,
            "has_report_artifact": self.has_report_artifact,
            "evidence_count": self.evidence_count,
            "user_marked_useful": self.user_marked_useful,
        }


def check_procedural_eligibility(
    run_id: str,
    repository: SQLiteRuntimeRepository,
    artifact_store: LocalArtifactStore,
    *,
    user_marked_useful: bool = False,
) -> ProceduralEligibilityCheckResult:
    """Verify that a research run satisfies all mandatory prerequisites for procedural memory extraction."""
    missing: list[str] = []

    # 1. Run existence and terminal succeeded status
    try:
        run = repository.get_run(run_id)
        run_status = run.status.value if hasattr(run.status, "value") else str(run.status)
    except Exception:
        return ProceduralEligibilityCheckResult(
            eligible=False,
            reason=f"未找到运行记录: {run_id}",
            missing_criteria=("run_exists",),
            run_id=run_id,
        )

    if run.status != RunStatus.SUCCEEDED:
        missing.append("run_status_succeeded")

    # 2. User explicit intent (feedback)
    if not user_marked_useful:
        missing.append("user_explicit_feedback")

    # 3. Delivery disposition
    try:
        delivery = repository.get_delivery(run_id)
    except Exception:
        delivery = None

    delivery_disp = None
    if delivery is None:
        missing.append("delivery_record_exists")
    else:
        delivery_disp = (
            delivery.disposition.value
            if hasattr(delivery.disposition, "value")
            else str(delivery.disposition)
        )
        if delivery.disposition != DeliveryDisposition.COMPLETE:
            missing.append("delivery_disposition_complete")

    # 4. Report artifact existence and readability
    has_report = False
    if delivery and delivery.artifact_refs:
        for aid in delivery.artifact_refs:
            try:
                raw_bytes = artifact_store.read_bytes(aid)
                if raw_bytes and len(raw_bytes) > 0:
                    has_report = True
                    break
            except Exception:
                pass

    if not has_report:
        missing.append("valid_report_artifact")

    # 5. Evidence cards count
    evidence_count = len(delivery.evidence_refs) if delivery else 0
    if evidence_count == 0:
        missing.append("complete_evidence_cards")

    eligible = len(missing) == 0
    if eligible:
        reason = "通过全部程序性记忆门禁：运行成功、用户标记有用、产物与证据卡完备"
    else:
        criteria_names = {
            "run_status_succeeded": "Run 状态必须为 succeeded",
            "user_explicit_feedback": "必须经用户明确标记有用 (user_marked_useful=True)",
            "delivery_record_exists": "缺少 Delivery 交付记录",
            "delivery_disposition_complete": "交付状态必须为 complete (完整回答)",
            "valid_report_artifact": "关键报告 Artifact 缺失或损坏",
            "complete_evidence_cards": "必须包含至少 1 张支撑证据卡",
        }
        reason = "；".join(criteria_names.get(c, c) for c in missing)

    return ProceduralEligibilityCheckResult(
        eligible=eligible,
        reason=reason,
        missing_criteria=tuple(missing),
        run_id=run_id,
        run_status=run_status,
        delivery_disposition=delivery_disp,
        has_report_artifact=has_report,
        evidence_count=evidence_count,
        user_marked_useful=user_marked_useful,
    )


class ProceduralMemoryExtractor:
    """Extracts procedural candidates from eligible research runs into staging."""

    def __init__(
        self,
        repository: SQLiteRuntimeRepository,
        artifact_store: LocalArtifactStore,
        memory_store: HierarchicalMemoryStore,
        chat_adapter=None,
    ) -> None:
        self.repository = repository
        self.artifact_store = artifact_store
        self.memory_store = memory_store
        self.chat = chat_adapter

    def extract_candidates_from_run(
        self,
        run_id: str,
        *,
        user_id: str = "user_default",
        project_id: str | None = None,
        user_marked_useful: bool = False,
        category: str | MemoryCategory = MemoryCategory.DECISION,
    ) -> tuple[ProceduralEligibilityCheckResult, list[MemoryCandidate]]:
        """Verify eligibility and extract procedural memory candidates into pending staging."""
        check = check_procedural_eligibility(
            run_id,
            self.repository,
            self.artifact_store,
            user_marked_useful=user_marked_useful,
        )
        if not check.eligible:
            return check, []

        try:
            task = self.repository.get_task_for_run(run_id)
            task_input = task.input if hasattr(task, "input") else getattr(task, "input_json", {})
            query = task_input.get("query", "").strip() or "深度研究任务"
        except Exception:
            query = "深度研究任务"

        try:
            delivery = self.repository.get_delivery(run_id)
        except Exception:
            delivery = None
        evidence_count = len(delivery.evidence_refs) if delivery else 0

        # Determine target scope & ID
        scope = MemoryScope.PROJECT if project_id else MemoryScope.USER
        target_id = project_id if project_id else user_id

        # Generate procedural rules
        statements: list[str] = []

        # Heuristic rule 1: Core verification & evidence cross-checking heuristic
        short_query = query[:40] if len(query) <= 40 else query[:37] + "..."
        stmt1 = f"研究方法约定：针对【{short_query}】类问题，优先依据本地权威语料与证据卡比对验证核心论断，并核实数据一致性"
        statements.append(stmt1)

        # Heuristic rule 2: Retrieval granularity and evidence density
        if evidence_count >= 2:
            stmt2 = f"检索规约：在【{short_query}】调研中，保持多轮证据卡交叉引用，至少获取 {evidence_count} 处独立来源支撑"
            statements.append(stmt2)

        candidates: list[MemoryCandidate] = []
        for stmt in statements:
            # Conflict & deduplication check
            cat_enum = category if isinstance(category, MemoryCategory) else MemoryCategory(category)
            dup_id, conflict_id = self.memory_store.find_conflicts_and_duplicates(
                scope, target_id, cat_enum, stmt
            )
            if dup_id:
                # Already exists as active memory, skip creating duplicate
                continue

            cand = self.memory_store.create_candidate(
                scope=scope,
                target_id=target_id,
                category=cat_enum,
                statement=stmt,
                confidence=0.90,
                source_type="procedural_run",
                source_id=run_id,
                conflict_with_memory_id=conflict_id,
            )
            candidates.append(cand)

        return check, candidates
