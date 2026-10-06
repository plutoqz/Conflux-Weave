"""Research Task Organization (P7-A4): Tags, Favorites, Recent Visits, Project Grouping, Note Linking, Templates, and Continuation."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
import json
from pathlib import Path
import re
from typing import Any, Literal
from uuid import uuid4

from conflux_weave.core import DeliveryDisposition
from conflux_weave.runtime.sqlite_contracts import RecordNotFound


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# 1. Run Organization Record & Store
# ---------------------------------------------------------------------------


@dataclass
class RunOrganizationRecord:
    """Metadata and organizational attributes for a research Run."""

    run_id: str
    is_favorite: bool = False
    tags: list[str] = field(default_factory=list)
    last_accessed_at: str | None = None
    project_id: str | None = None
    note_ids: list[str] = field(default_factory=list)
    updated_at: str = field(default_factory=_utc_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RunOrganizationRecord:
        return cls(
            run_id=str(data["run_id"]),
            is_favorite=bool(data.get("is_favorite", False)),
            tags=[str(t).strip() for t in (data.get("tags") or []) if str(t).strip()],
            last_accessed_at=data.get("last_accessed_at"),
            project_id=data.get("project_id"),
            note_ids=[str(n).strip() for n in (data.get("note_ids") or []) if str(n).strip()],
            updated_at=str(data.get("updated_at", _utc_now())),
        )


class RunOrganizationStore:
    """Persistent registry for Run organization metadata (tags, favorites, recent visits, project, notes)."""

    def __init__(self, registry_file: Path | None = None) -> None:
        self.registry_file = registry_file
        self._memory_cache: dict[str, RunOrganizationRecord] = {}
        if self.registry_file is not None:
            self._ensure_initialized()

    def _ensure_initialized(self) -> None:
        if self.registry_file is None:
            return
        if not self.registry_file.exists():
            self.registry_file.parent.mkdir(parents=True, exist_ok=True)
            self._save({})

    def _load(self) -> dict[str, RunOrganizationRecord]:
        if self.registry_file is None:
            return self._memory_cache
        if not self.registry_file.exists():
            return {}
        try:
            data = json.loads(self.registry_file.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return {k: RunOrganizationRecord.from_dict(v) for k, v in data.items()}
            return {}
        except (OSError, ValueError):
            return {}

    def _save(self, records: dict[str, RunOrganizationRecord]) -> None:
        if self.registry_file is None:
            self._memory_cache = records
            return
        self.registry_file.parent.mkdir(parents=True, exist_ok=True)
        data = {k: v.to_dict() for k, v in records.items()}
        tmp_file = self.registry_file.with_name(f"{self.registry_file.name}.tmp")
        tmp_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_file.replace(self.registry_file)

    def get(self, run_id: str) -> RunOrganizationRecord:
        records = self._load()
        if run_id in records:
            return records[run_id]
        return RunOrganizationRecord(run_id=run_id)

    def get_or_create(self, run_id: str) -> RunOrganizationRecord:
        records = self._load()
        if run_id not in records:
            rec = RunOrganizationRecord(run_id=run_id)
            records[run_id] = rec
            self._save(records)
            return rec
        return records[run_id]

    def set_favorite(self, run_id: str, is_favorite: bool) -> RunOrganizationRecord:
        records = self._load()
        rec = records.get(run_id) or RunOrganizationRecord(run_id=run_id)
        rec.is_favorite = is_favorite
        rec.updated_at = _utc_now()
        records[run_id] = rec
        self._save(records)
        return rec

    def toggle_favorite(self, run_id: str) -> RunOrganizationRecord:
        rec = self.get(run_id)
        return self.set_favorite(run_id, not rec.is_favorite)

    def set_tags(self, run_id: str, tags: list[str]) -> RunOrganizationRecord:
        records = self._load()
        rec = records.get(run_id) or RunOrganizationRecord(run_id=run_id)
        # Deduplicate while preserving order
        seen = set()
        clean_tags = []
        for t in tags:
            s = str(t).strip()
            if s and s not in seen:
                seen.add(s)
                clean_tags.append(s)
        rec.tags = clean_tags
        rec.updated_at = _utc_now()
        records[run_id] = rec
        self._save(records)
        return rec

    def add_tag(self, run_id: str, tag: str) -> RunOrganizationRecord:
        s = str(tag).strip()
        if not s:
            return self.get(run_id)
        rec = self.get(run_id)
        current = list(rec.tags)
        if s not in current:
            current.append(s)
        return self.set_tags(run_id, current)

    def remove_tag(self, run_id: str, tag: str) -> RunOrganizationRecord:
        s = str(tag).strip()
        rec = self.get(run_id)
        current = [t for t in rec.tags if t != s]
        return self.set_tags(run_id, current)

    def touch(self, run_id: str, timestamp: str | None = None) -> RunOrganizationRecord:
        records = self._load()
        rec = records.get(run_id) or RunOrganizationRecord(run_id=run_id)
        rec.last_accessed_at = timestamp or _utc_now()
        rec.updated_at = _utc_now()
        records[run_id] = rec
        self._save(records)
        return rec

    def set_project(self, run_id: str, project_id: str | None) -> RunOrganizationRecord:
        records = self._load()
        rec = records.get(run_id) or RunOrganizationRecord(run_id=run_id)
        rec.project_id = str(project_id).strip() if project_id else None
        rec.updated_at = _utc_now()
        records[run_id] = rec
        self._save(records)
        return rec

    def link_note(self, run_id: str, note_id: str) -> RunOrganizationRecord:
        n = str(note_id).strip()
        if not n:
            return self.get(run_id)
        records = self._load()
        rec = records.get(run_id) or RunOrganizationRecord(run_id=run_id)
        if n not in rec.note_ids:
            rec.note_ids.append(n)
            rec.updated_at = _utc_now()
            records[run_id] = rec
            self._save(records)
        return rec

    def unlink_note(self, run_id: str, note_id: str) -> RunOrganizationRecord:
        n = str(note_id).strip()
        records = self._load()
        rec = records.get(run_id) or RunOrganizationRecord(run_id=run_id)
        if n in rec.note_ids:
            rec.note_ids = [nid for nid in rec.note_ids if nid != n]
            rec.updated_at = _utc_now()
            records[run_id] = rec
            self._save(records)
        return rec

    def list_runs_by_project(self) -> dict[str, list[str]]:
        """Group run_ids by project_id. Unassigned runs are grouped under '__unassigned__'."""
        records = self._load()
        result: dict[str, list[str]] = {}
        for run_id, rec in records.items():
            key = rec.project_id or "__unassigned__"
            result.setdefault(key, []).append(run_id)
        return result

    def list_runs_for_project(self, project_id: str) -> list[str]:
        records = self._load()
        p = str(project_id).strip()
        return [run_id for run_id, rec in records.items() if rec.project_id == p]

    def list_runs_for_note(self, note_id: str) -> list[str]:
        records = self._load()
        n = str(note_id).strip()
        return [run_id for run_id, rec in records.items() if n in rec.note_ids]

    def list_favorites(self) -> list[str]:
        records = self._load()
        return [run_id for run_id, rec in records.items() if rec.is_favorite]

    def list_by_tag(self, tag: str) -> list[str]:
        records = self._load()
        t = str(tag).strip()
        return [run_id for run_id, rec in records.items() if t in rec.tags]

    def list_recent(self, limit: int = 20) -> list[RunOrganizationRecord]:
        records = self._load()
        visited = [r for r in records.values() if r.last_accessed_at]
        visited.sort(key=lambda r: str(r.last_accessed_at), reverse=True)
        return visited[:limit]


# ---------------------------------------------------------------------------
# 2. Research Task Templates
# ---------------------------------------------------------------------------


@dataclass
class ResearchTaskTemplate:
    """Preconfigured research task template with parameter placeholders and suggested budgets."""

    template_id: str
    name: str
    description: str
    task_family: str  # verified_paper_research | managed_verified_research | deep_research
    category: str  # architecture | literature | benchmark | engineering | custom
    default_query: str
    tags: list[str] = field(default_factory=list)
    suggested_budgets: dict[str, Any] = field(default_factory=dict)
    variables_schema: list[dict[str, Any]] = field(default_factory=list)
    is_builtin: bool = True
    created_at: str = field(default_factory=_utc_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ResearchTaskTemplate:
        return cls(
            template_id=str(data["template_id"]),
            name=str(data["name"]),
            description=str(data.get("description", "")),
            task_family=str(data.get("task_family", "verified_paper_research")),
            category=str(data.get("category", "custom")),
            default_query=str(data.get("default_query", "")),
            tags=list(data.get("tags") or []),
            suggested_budgets=dict(data.get("suggested_budgets") or {}),
            variables_schema=list(data.get("variables_schema") or []),
            is_builtin=bool(data.get("is_builtin", False)),
            created_at=str(data.get("created_at", _utc_now())),
        )

    def render_query(self, variables: dict[str, str]) -> str:
        """Render query by substituting `{variable_name}` placeholders."""
        query = self.default_query
        for k, v in variables.items():
            placeholder = "{" + k.strip() + "}"
            query = query.replace(placeholder, str(v).strip())
        return query


_BUILTIN_TEMPLATES = [
    ResearchTaskTemplate(
        template_id="tpl-architecture-survey",
        name="深度架构调研与可行性评估",
        description="针对特定系统架构、分层解耦或关键协议进行多智能体分解调研与代码落地可行性分析。",
        task_family="managed_verified_research",
        category="architecture",
        default_query="调研评估 {technology} 的核心设计模式、性能开销与落地可行性边界",
        tags=["architecture", "survey", "feasibility"],
        suggested_budgets={"max_tool_calls": 30, "max_retrieval_rounds": 8, "max_output_tokens": 16000},
        variables_schema=[
            {
                "name": "technology",
                "label": "架构技术/协议",
                "placeholder": "如：LanceDB 混合向量索引 / Rust 扩展模块",
                "required": True,
            }
        ],
        is_builtin=True,
    ),
    ResearchTaskTemplate(
        template_id="tpl-literature-matrix",
        name="学术文献综述与对比矩阵",
        description="对目标学术领域的代表性论文进行系统性检索、事实断言提取与关键维度对比矩阵构建。",
        task_family="verified_paper_research",
        category="literature",
        default_query="对近期 {topic} 的前沿文献进行综述对比，提取方法差异与实验基准表现",
        tags=["literature", "review", "comparison-matrix"],
        suggested_budgets={"max_tool_calls": 20, "max_retrieval_rounds": 6, "max_output_tokens": 12000},
        variables_schema=[
            {
                "name": "topic",
                "label": "学术研究方向",
                "placeholder": "如：多智能体代码自我验证 / 长文本注意力机制",
                "required": True,
            }
        ],
        is_builtin=True,
    ),
    ResearchTaskTemplate(
        template_id="tpl-multimodal-benchmark",
        name="跨模态基准复现与指标分析",
        description="聚焦多模态图表、视觉锚点与跨模态混合检索基准测试指标的深度事实核验与消融比对。",
        task_family="verified_paper_research",
        category="benchmark",
        default_query="分析 {method} 在标准多模态评测集上的指标表现、消融实验与图表证据",
        tags=["multimodal", "benchmark", "ablation"],
        suggested_budgets={"max_tool_calls": 25, "max_retrieval_rounds": 6, "max_output_tokens": 14000},
        variables_schema=[
            {
                "name": "method",
                "label": "评测方法或模型",
                "placeholder": "如：MinerU 多模态解析 / ColPali 视觉向量检索",
                "required": True,
            }
        ],
        is_builtin=True,
    ),
    ResearchTaskTemplate(
        template_id="tpl-engineering-decision",
        name="工程落地决策与风险论证",
        description="结合工程现状与外部成熟实践，论证关键技术选型的收益、潜在风险与迁移防御成本。",
        task_family="deep_research",
        category="engineering",
        default_query="针对 {solution} 在生产环境落地的优缺点、故障模式与兜底防御策略进行论证",
        tags=["engineering", "decision", "risk-assessment"],
        suggested_budgets={"max_tool_calls": 25, "max_retrieval_rounds": 5, "max_output_tokens": 10000},
        variables_schema=[
            {
                "name": "solution",
                "label": "技术方案/重构目标",
                "placeholder": "如：SQLite Outbox 事务解耦 / Durable Step Checkpoint",
                "required": True,
            }
        ],
        is_builtin=True,
    ),
]


class ResearchTaskTemplateStore:
    """Storage and registry for research task templates (builtin + custom)."""

    def __init__(self, registry_file: Path | None = None) -> None:
        self.registry_file = registry_file
        self._custom_cache: dict[str, ResearchTaskTemplate] = {}
        if self.registry_file is not None:
            self._ensure_initialized()

    def _ensure_initialized(self) -> None:
        if self.registry_file is None:
            return
        if not self.registry_file.exists():
            self.registry_file.parent.mkdir(parents=True, exist_ok=True)
            self._save({})

    def _load_custom(self) -> dict[str, ResearchTaskTemplate]:
        if self.registry_file is None:
            return self._custom_cache
        if not self.registry_file.exists():
            return {}
        try:
            data = json.loads(self.registry_file.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return {k: ResearchTaskTemplate.from_dict(v) for k, v in data.items()}
            return {}
        except (OSError, ValueError):
            return {}

    def _save(self, records: dict[str, ResearchTaskTemplate]) -> None:
        if self.registry_file is None:
            self._custom_cache = records
            return
        self.registry_file.parent.mkdir(parents=True, exist_ok=True)
        data = {k: v.to_dict() for k, v in records.items()}
        tmp_file = self.registry_file.with_name(f"{self.registry_file.name}.tmp")
        tmp_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_file.replace(self.registry_file)

    def list_templates(self, category: str | None = None) -> list[ResearchTaskTemplate]:
        all_templates = list(_BUILTIN_TEMPLATES)
        custom = self._load_custom()
        all_templates.extend(custom.values())
        if category and category != "all":
            all_templates = [t for t in all_templates if t.category == category]
        return all_templates

    def get_template(self, template_id: str) -> ResearchTaskTemplate | None:
        for t in _BUILTIN_TEMPLATES:
            if t.template_id == template_id:
                return t
        custom = self._load_custom()
        return custom.get(template_id)

    def create_custom_template(
        self,
        name: str,
        description: str,
        task_family: str = "verified_paper_research",
        category: str = "custom",
        default_query: str = "",
        tags: list[str] | None = None,
        suggested_budgets: dict[str, Any] | None = None,
        variables_schema: list[dict[str, Any]] | None = None,
    ) -> ResearchTaskTemplate:
        name_clean = str(name).strip()
        if not name_clean:
            raise ValueError("模板名称不能为空。")
        slug = re.sub(r"[^a-zA-Z0-9_-]", "-", name_clean.lower()).strip("-") or "template"
        template_id = f"tpl-{slug}-{uuid4().hex[:6]}"
        template = ResearchTaskTemplate(
            template_id=template_id,
            name=name_clean,
            description=str(description).strip(),
            task_family=str(task_family).strip(),
            category=str(category).strip() or "custom",
            default_query=str(default_query).strip(),
            tags=[str(t).strip() for t in (tags or []) if str(t).strip()],
            suggested_budgets=dict(suggested_budgets or {}),
            variables_schema=list(variables_schema or []),
            is_builtin=False,
        )
        custom = self._load_custom()
        custom[template_id] = template
        self._save(custom)
        return template

    def delete_custom_template(self, template_id: str) -> bool:
        if any(t.template_id == template_id for t in _BUILTIN_TEMPLATES):
            raise ValueError("内置模板不允许删除。")
        custom = self._load_custom()
        if template_id in custom:
            del custom[template_id]
            self._save(custom)
            return True
        return False


# ---------------------------------------------------------------------------
# 3. "继续上次研究"入口与推导引擎 (Continue Previous Research)
# ---------------------------------------------------------------------------


@dataclass
class ContinuationProposal:
    """Proposal for continuing research based on a completed or partial predecessor Run."""

    predecessor_run_id: str
    parent_query: str
    recommended_task_family: str
    suggested_query: str
    inherited_project_id: str | None
    inherited_tags: list[str]
    inherited_note_ids: list[str]
    unresolved_limitations: list[str]
    unmet_criteria: list[str]
    context_summary: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def derive_continuation_proposal(
    run_id: str,
    repository: Any,
    org_store: RunOrganizationStore,
    custom_focus: str | None = None,
) -> ContinuationProposal:
    """Analyze predecessor Run, extracting unresolved questions, limitations, and org metadata to form a continuation plan."""
    run = repository.get_run(run_id)
    task = repository.get_task_for_run(run_id)
    org = org_store.get(run_id)

    parent_query = str(task.input.get("query") or task.input.get("objective") or "")
    task_kind = task.kind

    # Extract delivery details if available
    limitations: list[str] = []
    unmet_criteria: list[str] = []
    disposition = "unknown"
    try:
        delivery = repository.get_delivery(run_id)
        limitations = list(delivery.limitations or [])
        unmet_criteria = list(delivery.unmet_criteria or [])
        disposition = delivery.disposition.value if hasattr(delivery.disposition, "value") else str(delivery.disposition)
    except RecordNotFound:
        pass

    # Determine recommended continuation query
    custom_clean = str(custom_focus).strip() if custom_focus else ""
    if custom_clean:
        suggested_query = custom_clean
    elif limitations:
        top_limitation = limitations[0]
        suggested_query = f"承接前序研究，深入攻坚未覆盖局限性：{top_limitation}"
    elif unmet_criteria:
        top_unmet = unmet_criteria[0]
        suggested_query = f"承接前序研究，继续探索未达标指标：{top_unmet}"
    else:
        suggested_query = f"继续深入调研「{parent_query}」的核心问题与后续工程扩展方向"

    # Inherit tags and ensure 'continued' tag is included
    inherited_tags = list(org.tags)
    if "continued" not in inherited_tags:
        inherited_tags.append("continued")

    # Generate context summary
    disp_zh = {
        "complete": "完整回答",
        "partial": "部分回答（存在未覆盖项）",
        "no_answer": "未找到确定性答案",
    }.get(disposition, disposition)

    summary_parts = [
        f"前序研究任务 ID: {run_id}（终态交付状态: {disp_zh}）。",
        f"原核心目标: {parent_query}。",
    ]
    if limitations:
        summary_parts.append(f"前序报告指出的已知局限: {'；'.join(limitations)}。")
    if unmet_criteria:
        summary_parts.append(f"前序未满足的验收标准: {'；'.join(unmet_criteria)}。")
    summary_parts.append("本续接任务将继承前序工程归属与标签脉络，专注未尽研究领域的突破。")
    context_summary = "\n".join(summary_parts)

    return ContinuationProposal(
        predecessor_run_id=run_id,
        parent_query=parent_query,
        recommended_task_family=task_kind,
        suggested_query=suggested_query,
        inherited_project_id=org.project_id,
        inherited_tags=inherited_tags,
        inherited_note_ids=list(org.note_ids),
        unresolved_limitations=limitations,
        unmet_criteria=unmet_criteria,
        context_summary=context_summary,
    )
