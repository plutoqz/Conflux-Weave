"""Data models and specification contracts for Project Dissection V2 & AI Hygiene Auditor."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class HygieneCategory(str, Enum):
    BUREAUCRATIC_RITUALISM = "bureaucratic_ritualism"              # 官僚防御与仪式感浪费
    MOCK_ILLUSION = "mock_illusion"                                # Mock 假象
    STUB_PLACEHOLDER = "stub_placeholder"                          # 空壳与伪实现
    SILENT_SWALLOWING = "silent_swallowing"                        # 静默吞异常
    PHANTOM_CONFIG = "phantom_config"                              # 幽灵配置与参数幻觉
    ZOMBIE_DEFENSIVENESS = "zombie_defensiveness"                  # 死深防御性废代码
    ARCHITECTURAL_VULNERABILITY = "architectural_vulnerability"    # 真实架构隐患与资源泄露


@dataclass(frozen=True)
class ArchitecturalDecision:
    topic: str
    chosen_solution: str
    rejected_alternatives: tuple[str, ...]
    core_rationale: str
    tradeoffs_accepted: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "topic": self.topic,
            "chosen_solution": self.chosen_solution,
            "rejected_alternatives": list(self.rejected_alternatives),
            "core_rationale": self.core_rationale,
            "tradeoffs_accepted": self.tradeoffs_accepted,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ArchitecturalDecision:
        return cls(
            topic=data["topic"],
            chosen_solution=data["chosen_solution"],
            rejected_alternatives=tuple(data.get("rejected_alternatives", [])),
            core_rationale=data["core_rationale"],
            tradeoffs_accepted=data.get("tradeoffs_accepted", ""),
        )


@dataclass(frozen=True)
class ArchitectureMotivations:
    mental_model_summary: str
    decisions: tuple[ArchitecturalDecision, ...]
    topology_mermaid: str
    dataflow_sequence_mermaid: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "mental_model_summary": self.mental_model_summary,
            "decisions": [d.to_dict() for d in self.decisions],
            "topology_mermaid": self.topology_mermaid,
            "dataflow_sequence_mermaid": self.dataflow_sequence_mermaid,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ArchitectureMotivations:
        return cls(
            mental_model_summary=data["mental_model_summary"],
            decisions=tuple(ArchitecturalDecision.from_dict(d) for d in data.get("decisions", [])),
            topology_mermaid=data.get("topology_mermaid", ""),
            dataflow_sequence_mermaid=data.get("dataflow_sequence_mermaid", ""),
        )


@dataclass(frozen=True)
class DependencyKnowledgeCard:
    package_name: str
    category: str
    role_in_project: str
    underlying_mechanism: str
    why_needed: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "package_name": self.package_name,
            "category": self.category,
            "role_in_project": self.role_in_project,
            "underlying_mechanism": self.underlying_mechanism,
            "why_needed": self.why_needed,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DependencyKnowledgeCard:
        return cls(
            package_name=data["package_name"],
            category=data.get("category", "核心依赖"),
            role_in_project=data.get("role_in_project", ""),
            underlying_mechanism=data.get("underlying_mechanism", ""),
            why_needed=data.get("why_needed", ""),
        )


@dataclass(frozen=True)
class CodeWalkthroughStage:
    stage_index: int
    stage_name: str
    core_theme: str
    key_files: tuple[str, ...]
    reading_tips: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage_index": self.stage_index,
            "stage_name": self.stage_name,
            "core_theme": self.core_theme,
            "key_files": list(self.key_files),
            "reading_tips": self.reading_tips,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CodeWalkthroughStage:
        return cls(
            stage_index=int(data["stage_index"]),
            stage_name=data["stage_name"],
            core_theme=data["core_theme"],
            key_files=tuple(data.get("key_files", [])),
            reading_tips=data.get("reading_tips", ""),
        )


@dataclass(frozen=True)
class HygieneAuditFinding:
    finding_id: str
    category: HygieneCategory
    severity: str  # "critical" | "warning" | "info"
    file_path: str
    line_number: int
    code_snippet: str
    issue_description: str
    harm_analysis: str
    refactor_diff_suggestion: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "finding_id": self.finding_id,
            "category": self.category.value,
            "severity": self.severity,
            "file_path": self.file_path,
            "line_number": self.line_number,
            "code_snippet": self.code_snippet,
            "issue_description": self.issue_description,
            "harm_analysis": self.harm_analysis,
            "refactor_diff_suggestion": self.refactor_diff_suggestion,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HygieneAuditFinding:
        return cls(
            finding_id=data["finding_id"],
            category=HygieneCategory(data["category"]),
            severity=data.get("severity", "warning"),
            file_path=data["file_path"],
            line_number=int(data.get("line_number", 1)),
            code_snippet=data.get("code_snippet", ""),
            issue_description=data.get("issue_description", ""),
            harm_analysis=data.get("harm_analysis", ""),
            refactor_diff_suggestion=data.get("refactor_diff_suggestion", ""),
        )


@dataclass(frozen=True)
class ProjectHygieneReport:
    project_path: str
    hygiene_score: int
    total_findings_count: int
    findings_by_category: dict[str, int]
    findings: tuple[HygieneAuditFinding, ...]
    executive_verdict: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_path": self.project_path,
            "hygiene_score": self.hygiene_score,
            "total_findings_count": self.total_findings_count,
            "findings_by_category": dict(self.findings_by_category),
            "findings": [f.to_dict() for f in self.findings],
            "executive_verdict": self.executive_verdict,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProjectHygieneReport:
        return cls(
            project_path=data["project_path"],
            hygiene_score=int(data["hygiene_score"]),
            total_findings_count=int(data["total_findings_count"]),
            findings_by_category=dict(data.get("findings_by_category", {})),
            findings=tuple(HygieneAuditFinding.from_dict(f) for f in data.get("findings", [])),
            executive_verdict=data.get("executive_verdict", ""),
        )


@dataclass(frozen=True)
class ProjectTutorReport:
    project_id: str
    project_name: str
    root_path: str
    motivations: ArchitectureMotivations
    dependency_cards: tuple[DependencyKnowledgeCard, ...]
    walkthrough_stages: tuple[CodeWalkthroughStage, ...]
    hygiene_report: ProjectHygieneReport
    full_markdown: str
    created_at: str = field(default_factory=_utc_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "project_name": self.project_name,
            "root_path": self.root_path,
            "motivations": self.motivations.to_dict(),
            "dependency_cards": [c.to_dict() for c in self.dependency_cards],
            "walkthrough_stages": [s.to_dict() for s in self.walkthrough_stages],
            "hygiene_report": self.hygiene_report.to_dict(),
            "full_markdown": self.full_markdown,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProjectTutorReport:
        return cls(
            project_id=data["project_id"],
            project_name=data["project_name"],
            root_path=data["root_path"],
            motivations=ArchitectureMotivations.from_dict(data["motivations"]),
            dependency_cards=tuple(DependencyKnowledgeCard.from_dict(c) for c in data.get("dependency_cards", [])),
            walkthrough_stages=tuple(CodeWalkthroughStage.from_dict(s) for s in data.get("walkthrough_stages", [])),
            hygiene_report=ProjectHygieneReport.from_dict(data["hygiene_report"]),
            full_markdown=data.get("full_markdown", ""),
            created_at=data.get("created_at", _utc_now()),
        )
