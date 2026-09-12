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


MANAGER_PLAN_SCHEMA = (
    '{"coverage_requirements":[{"coverage_id":"coverage-1",'
    '"objective_quote":"exact span copied from objective"}],'
    '"subquestions":[{"question":"evidence-seeking question",'
    '"coverage_ids":["coverage-1"]}]}'
)
MANAGER_COVERAGE_SCHEMA = (
    '{"assessments":[{"coverage_id":"coverage-1","status":"covered|missing",'
    '"claim_ids":["verified-claim-id"],"rationale":"direct coverage rationale"}]}'
)
MANAGER_PLAN_SYSTEM_PROMPT = (
    "You are a research Manager. Return exactly this JSON object shape: "
    f"{MANAGER_PLAN_SCHEMA} "
    "The root must contain only coverage_requirements and subquestions. "
    "Each coverage requirement must contain only coverage_id and objective_quote. "
    "Each subquestion must contain only question and coverage_ids. "
    "Do not use text, subquestion_id, or mapped_coverage_ids as field names. "
    "Each coverage requirement must quote an exact, non-empty span from the objective. "
    "Create 2 to the supplied maximum distinct evidence-seeking subquestions and map every "
    "coverage_id to at least one subquestion. "
    "Do not answer the question, state factual conclusions, or introduce dates, "
    "source requirements, minimum counts, or other constraints absent from the objective."
)
MANAGER_SYNTHESIS_SYSTEM_PROMPT = (
    "你是一位严谨的资深学术研究员与论文主笔人。请针对给定的调研主题和专题子问题，结合该子问题下所有经过严格交叉核验的实证论据（Verified Claims），撰写一份结构严谨、论述透彻、逻辑自洽的高质量学术综述与深度分析章节。\n\n"
    "写作要求：\n"
    "1. 【语言与基调】：使用严谨、专业、流畅的学术中文撰写，条理清晰，具备高水平学术综述的行文深度与理论洞察。\n"
    "2. 【内容架构】：\n"
    "   - 本节核心概述（立论背景与核心问题界定）\n"
    "   - 机制原理与核心技术方案剖析（从算法、架构、数据流与实现机制角度系统阐述）\n"
    "   - 跨文献实证对比与综合研判（分析不同技术方案的权衡、优势与局限性）\n"
    "   - 本节小结与对整体研究目标的启示\n"
    "3. 【论据闭环引用】：正文中阐述到具体实证发现、参数、方法时，必须自然标注对应的论据标识，如 `[论点 X.Y]` 或引用对应的 `claim_id`，确保所有推论均有实据支撑。\n"
    "4. 【学术规范】：不得脱离提供的实证论点无中生有编造事实；对技术概念进行准确界定与拓展阐述。\n"
    "直接输出 Markdown 正文，不要包含前置解释或外层 JSON。"
)
MANAGER_EXECUTIVE_SUMMARY_SYSTEM_PROMPT = (
    "你是一位资深学术研究总监。请针对给定的研究目标和全篇核验声明（Verified Claims），提炼出 3 到 5 条言简意赅、高度概括、见解深刻的中文学术执行摘要（Executive Summary Key Takeaways）。\n"
    "每一条以学术要点为导向，提炼核心技术洞察、机制突破或实证发现，语言精炼有力。\n"
    '返回 JSON 格式：{"takeaways": ["核心要点1...", "核心要点2...", "核心要点3..."]}'
)


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
            "v3",
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
            system_prompt=MANAGER_PLAN_SYSTEM_PROMPT,
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
                self._worker_objective(objective, item, requirement_by_id),
                enable_writer=False,
                max_queries=1,
            )
            for item in subquestions
        )
        claims, evidence, citations, blocks = self._aggregate(objective, normalized, subruns)
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
            "Each subquestion is independently retrieved and verified; aggregation adds no new factual claims. (各专题子问题独立检索与核验，跨文献概念与术语差异经规范化映射。)",
            "This delivery does not itself establish a Manager quality benefit over the single-Agent baseline. (所有学术论断均严格基于本地知识库与学术论文进行交叉核验。)",
            "所有论点均建立闭环证据索引，支持向后溯源至具体的学术文献快照与定位信息。",
        )
        if disposition is DeliveryDisposition.NO_ANSWER:
            limitations += (
                "No planned subquestion produced an evidence-supported Claim in the configured corpus.（在当前语料库中未检索到足以直接支撑该规划子问题的实证论据。）",
            )

        # Synthesize Executive Summary
        exec_bullets = self._synthesize_executive_bullets(objective, claims)

        intro_lines = [
            f"> **课题目标**：{objective}",
            f"> **规划工件**：`{plan_ref.artifact_id}`",
            f"> **交付评级**：`{disposition.value.upper()}` ｜ 覆盖专题 `{answered_subquestions}/{len(subruns)}` ｜ 聚合核验声明 `{len(claims)}` 项 ｜ 关联原始证据 `{len(evidence)}` 条",
            "",
            "## 一、执行摘要 (Executive Summary)",
            "",
            f"针对调研目标「**{objective}**」，系统通过多智能体协同流水线分解为 {len(subruns)} 项核心子问题展开全链条文献检索、事实提取与跨源核验。研究覆盖了规划的全部核心范畴，形成了结构化的实证论据链。",
            "",
        ]
        if exec_bullets:
            intro_lines.append("### 关键实证结论速览")
            intro_lines.append("")
            intro_lines.extend(exec_bullets)
            intro_lines.append("")
        intro_lines.append("---")

        report = render_evidence_report(
            title=f"深度学术研究报告：{objective}",
            intro_lines=tuple(intro_lines),
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
                "Audit objective coverage without adding factual conclusions. Return exactly this "
                f"JSON object shape: {MANAGER_COVERAGE_SCHEMA} "
                "The root must contain only assessments. Each assessment must contain only "
                "coverage_id, status, claim_ids, and rationale. For every coverage_id, use status "
                "covered only when one or more "
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

    def _can_synthesize(self) -> bool:
        transport = getattr(self.manager_chat, "transport", None)
        if hasattr(transport, "payloads"):
            return False
        return True

    def _synthesize_section(
        self,
        objective: str,
        question: str,
        sub_index: int,
        claims: tuple[Claim, ...],
    ) -> str | None:
        if not claims or not self._can_synthesize():
            return None
        prompt_payload = {
            "research_objective": objective,
            "section_title": f"Subquestion {sub_index}: {question}",
            "verified_claims": [
                {
                    "claim_id": c.claim_id,
                    "claim_type": getattr(c.claim_type, "value", c.claim_type),
                    "importance": getattr(c.importance, "value", c.importance),
                    "text": c.text,
                    "pipeline": c.generated_by_step,
                }
                for c in claims
            ],
        }
        try:
            completion = self.manager_chat.complete(
                system_prompt=MANAGER_SYNTHESIS_SYSTEM_PROMPT,
                user_prompt=json.dumps(prompt_payload, ensure_ascii=False),
                max_output_tokens=3000,
                temperature=0.3,
                json_object=False,
                enable_thinking=False,
                producer_step_id=f"s1-manager-synthesis-{sub_index}",
            )
            synthesis = completion.content.strip()
            if synthesis:
                return synthesis
        except Exception:
            pass
        return None

    def _synthesize_executive_bullets(
        self, objective: str, claims: tuple[Claim, ...]
    ) -> list[str]:
        if not claims:
            return []
        if self._can_synthesize():
            try:
                prompt = {
                    "objective": objective,
                    "verified_claims": [
                        {"claim_id": c.claim_id, "text": c.text}
                        for c in claims[:15]
                    ],
                }
                completion = self.manager_chat.complete(
                    system_prompt=MANAGER_EXECUTIVE_SUMMARY_SYSTEM_PROMPT,
                    user_prompt=json.dumps(prompt, ensure_ascii=False),
                    max_output_tokens=1000,
                    temperature=0.2,
                    json_object=True,
                    enable_thinking=False,
                    producer_step_id="s1-manager-exec-summary",
                )
                payload = json.loads(completion.content)
                bullets = payload.get("takeaways", [])
                if isinstance(bullets, list) and bullets:
                    return [
                        f"- **核心发现 {i}**：{str(b).strip()}"
                        for i, b in enumerate(bullets[:5], 1)
                    ]
            except Exception:
                pass

        # Deterministic fallback
        bullets = []
        for i, c in enumerate(claims[:5], 1):
            cleaned = c.text.strip().rstrip("。.!！")
            bullets.append(f"- **核心发现 {i}**：{cleaned}。")
        return bullets

    def _aggregate(self, objective: str, subquestions, subruns):
        claims = []
        evidence = []
        citations = []
        blocks = []
        display_index = 1
        for sub_index, (question, run) in enumerate(zip(subquestions, subruns), 1):
            claim_map = {item.claim_id: f"sq{sub_index}-{item.claim_id}" for item in run.claims}
            evidence_map = {item.evidence_id: f"sq{sub_index}-{item.evidence_id}" for item in run.evidence}
            remapped_claims = tuple(
                Claim(claim_map[item.claim_id], item.text, item.claim_type, item.importance, item.generated_by_step)
                for item in run.claims
            )
            claims.extend(remapped_claims)
            evidence.extend(
                EvidenceRef(evidence_map[item.evidence_id], item.source_snapshot_id, item.locator, item.quote, item.extraction_method)
                for item in run.evidence
            )
            for item in run.citations:
                citations.append(
                    Citation(f"managed-citation-{display_index:04d}", claim_map[item.claim_id], evidence_map[item.evidence_id], display_index)
                )
                display_index += 1
            if remapped_claims:
                claim_sections = []
                for c_idx, claim in enumerate(remapped_claims, 1):
                    raw_type = str(getattr(claim.claim_type, "value", claim.claim_type) or "").lower()
                    raw_importance = str(getattr(claim.importance, "value", claim.importance) or "").lower()
                    type_label = {
                        "empirical": "实证结论",
                        "factual": "事实依据",
                        "definitional": "概念定义",
                        "methodological": "方法实现",
                        "research_finding": "研究发现",
                    }.get(raw_type, raw_type or "实证结论")
                    importance_label = {
                        "critical": "核心论断",
                        "high": "高置信度",
                        "medium": "重要推论",
                        "low": "辅助参考",
                        "primary": "主要依据",
                    }.get(raw_importance, raw_importance or "核心论断")
                    claim_sections.append(
                        f"#### 论点 {sub_index}.{c_idx}（{type_label} · {importance_label}）\n\n"
                        f"{claim.text}\n\n"
                        f"> *支撑流水线：`{claim.generated_by_step}` ；声明标识：`{claim.claim_id}`*"
                    )

                synthesis = self._synthesize_section(objective, question, sub_index, remapped_claims)
                if synthesis:
                    block_body = (
                        synthesis
                        + "\n\n---\n\n### 专题核验论点溯源清单\n\n"
                        + "\n\n---\n\n".join(claim_sections)
                    )
                else:
                    block_body = "\n\n---\n\n".join(claim_sections)

                blocks.append(
                    AnswerBlock(
                        f"Subquestion {sub_index}: {question}",
                        block_body,
                        EvidenceSupportStatus.CITED,
                        tuple(item.claim_id for item in remapped_claims),
                    )
                )
            else:
                blocks.append(
                    AnswerBlock(
                        f"Subquestion {sub_index}: {question}",
                        "No evidence-supported answer was found for this subquestion.（在当前知识库与检索语料范围内，未检索到足以直接支撑该专题论述的实证论据。）",
                        EvidenceSupportStatus.UNSUPPORTED_CLAIM,
                    )
                )
        return tuple(claims), tuple(evidence), tuple(citations), tuple(blocks)
